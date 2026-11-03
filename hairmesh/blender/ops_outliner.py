# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The HairMesh Outliner inside Blender (U1 / U2 of docs/fase-3-ux-revisao.md, 15/10/2026; the fixes 1a-1d and lots
3 / 4 of 16/10/2026): the tree of the project drawn from core.outliner's rows, as a pop-up panel (O while the tool is
active, keep_open like the HairMesh Menu; the button of the pop-up's top row and of the pies) and as the first panel
of the N tab. The rows are ordinary layout buttons (an add-on makes no editor):
  - the project row on top (1c), like Blender's "Scene Collection": its name selects the root level (nothing
    selected), Move here / Paste on it put the cut or selected items at the root; the head and the groups hang under
    it, one level in;
  - the name, left-aligned, selects (hairmesh.select_item: Shift adds, Ctrl on a group selects its contents — the
    original's double click, 8463); a double click on the name renames (1d: the second click on the same row within
    the double-click time of the preferences, core.outliner.double_click, opens the rename dialog, since a layout
    button never sees Blender's double-click event); the arrow opens / closes a group (UI state kept on the
    WindowManager, outside undo and the file: the original never commits a click on the arrow);
  - the highlights with the colors of the original's rows (lot 3): the selected row pressed, a group holding the
    selection with the blue collection icon (childsel), a related row (the walls of a selected curve, the curves of a
    selected wall) with the orange marker before its name (OL_REL);
  - the switches in fixed columns, the same in every row (1b): the loop step, the mode A / ∿ / ⌀ (8488) with the
    color swatch, the Container tag, ⏻ (only while some row carries one: a guide, or a group holding guides,
    8510-8523), the conversion tick, the drop column (Paste while something is cut, else Move here), the eye
    (toggleHide 8192; the eye of a wall hides that wall alone in the live mesh, 1a);
  - the bar: the name field of the selected item (type to rename, 1d), Reveal (the F of the original), New Group,
    Rename (the dialog; F2 in the viewport and over the N panel), Cut / Paste (a clipboard on the WindowManager),
    Delete (hairmesh.delete_selected: groups too, deleteSelected 8106).
Groups are selected here and nowhere else, as in the original (U1). The types of phases 4 and 5 (guides, discs,
modifiers, extras, crowns, caps, fill holes, density, meshes) are plain rows with their icons and the ⏻ of the
guides, ready for the tools that will make them."""
import json
import time

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from ..core import conversion, edit, groups as G, outliner as OL
from ..core.effects import modeActs
from ..core.selection import Selection
from . import brush as BRU, fit, icons, live, ops_groups, owner, pick, prefs, select, session
from .ops_groups import WITH_WALLS_DESC, sync_collections

OUTLINER_ID = 'HAIRMESH_PT_outliner'
UNITS_X = 20
MAX_UNITS_X = 30                                                    # the pop-up widens up to this for a long name or "Color from <group>" (21/10/2026; part 3 of the lot ux7)
COL = 1.0                                                           # an icon column, in UI units
STEP_COL = 3.0                                                      # the loop step column ("15.5 ≠", "1550 ≠", "0.0155 ≠" in a project in metres)
MODE_COL = 2.0                                                      # the mode button with the colour swatch
MODE_ICONS = {'auto': 'HM:auto', 'show': 'IPO_BEZIER', 'hide': 'CANCEL'}   # a group's curves mode: Auto, Show, Hide (the original's letters A, ∿, ⌀)
LAST_INVOKE = []                                                    # (what, item id) of the last rename_item.invoke: read by the real-event simulations (tools/sim_ux2_shots.py)
_last_click = None                                                  # (item id, time) of the last row click, for the double click


# ---------------- UI state on the WindowManager (outside undo and the file) ----------------

def _wm_ids(wm, key):
    try:
        return [int(i) for i in json.loads(getattr(wm, key) or '[]')]
    except (ValueError, TypeError):
        return []


def _set_wm_ids(wm, key, ids):
    setattr(wm, key, json.dumps(list(ids)))


def _wm_open(wm):
    """the user's own open / closed switches of this session: {group id: open}, on top of the model's `expanded`"""
    try:
        d = json.loads(wm.hairmesh_outliner_open or '{}')
        return {int(k): bool(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _set_wm_open(wm, d):
    wm.hairmesh_outliner_open = json.dumps({str(k): v for k, v in d.items()})


def is_open(context, g):
    """a group is drawn open as the file says (it.expanded, restored by the original's loadProject: the example
    opens with its Bang groups closed, 15 rows instead of 364) unless the user switched it in this session"""
    return _wm_open(context.window_manager).get(g.id, bool(g.expanded))


def set_open(context, gid, on):
    d = _wm_open(context.window_manager); d[gid] = bool(on); _set_wm_open(context.window_manager, d)


def forget_open_state(context, gid):
    """the model decided (a drop opens the target group, moveItems 8578): the session's switch for it goes"""
    if gid is None:
        return
    d = _wm_open(context.window_manager)
    if gid in d:
        del d[gid]; _set_wm_open(context.window_manager, d)


def collapsed(context, P):
    return frozenset(g.id for g in P.groups() if not is_open(context, g))


def clipboard(context):
    return _wm_ids(context.window_manager, 'hairmesh_clipboard')


def project_name(root):
    """the name the project row shows: what the root Empty was given by New Project / the import"""
    return root.get('hairmesh_project') or root.name if root is not None else ''


def rows_of(context, P, root=None):
    """the rows for this context: the selection, the collapsed groups (opened above the selection when the
    preference says so), the filter, the project name"""
    sel = select.load(context.scene.hairmesh).prune(P)
    root = root if root is not None else session.find_root(context)
    return OL.outliner_rows(P, sel.items, sel.anchors, collapsed(context, P), context.window_manager.hairmesh_outliner_filter,
                            project_name=project_name(root), open_selected=prefs.open_on_select(context))


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def _redraw_all(context):
    select.redraw(context)
    for win in context.window_manager.windows:                      # the pop-up and the N panel
        for area in win.screen.areas:
            area.tag_redraw()


# ---------------- operators ----------------

class HAIRMESH_OT_select_item(bpy.types.Operator):
    """Select this item (the outliner's click: Shift adds or takes out; Ctrl on a group selects everything inside it; a double click renames)"""
    bl_idname = 'hairmesh.select_item'
    bl_label = 'Select HairMesh Item'
    bl_options = {'REGISTER', 'UNDO'}

    item: IntProperty(name='Item', description='The item id (a group, a curve, a wall, ...)', default=-1)
    extend: BoolProperty(name='Extend', description='Shift: add to the selection (or take out)', default=False, options={'SKIP_SAVE'})
    contents: BoolProperty(name='Contents', description='Ctrl on a group: select everything inside it', default=False, options={'SKIP_SAVE'})
    rename: BoolProperty(name='Rename', description='A double click: select and open the rename dialog', default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        global _last_click
        self.extend = self.extend or event.shift
        self.contents = self.contents or event.ctrl
        if not self.extend and not self.contents:                   # 1d: the second plain click on the same row within the double-click time
            dbl, _last_click = OL.double_click(self.item, time.monotonic(), _last_click, context.preferences.inputs.mouse_double_click_time / 1000.0)
            self.rename = self.rename or dbl
        else:
            _last_click = None
        return self.execute(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        it = P.items.get(self.item)
        if it is None or it.type == 'head':
            self.report({'ERROR'}, f'No item {self.item} in the project')
            return {'CANCELLED'}
        if it.type == 'group' and self.contents:
            ids = []

            def walk(children):
                for iid in children:
                    x = P.items.get(iid)
                    if x is None or getattr(x, 'ring', None):
                        continue
                    if x.type == 'group':
                        walk(x.children)
                    else:
                        ids.append(iid)
            walk(it.children)                                       # selectContents (8463): not the sub-groups themselves
            sel = Selection(ids, [])
            self.report({'INFO'}, f'Selected {len(ids)} item{"" if len(ids) == 1 else "s"} inside {it.name}')
        else:
            sel.select_item((it.ring or it.id) if it.type == 'curve' else it.id, self.extend)
        g = select.selected_group(P, sel)
        G.panelsRendered(P, g.id if g is not None else None)        # the group's panel fills its conv (8668)
        select.store(context.scene.hairmesh, sel)
        session.save_project(root, P)
        BRU.follow_active(context)                                  # (a line of the list of the Extras, or the Outliner: the running weight brush paints it)
        _redraw_all(context)
        if self.rename and not bpy.app.background:                  # the double click: the rename dialog over the row (startRename, 8270)
            bpy.ops.hairmesh.rename_item('INVOKE_DEFAULT', item=it.id)
        return {'FINISHED'}


def expand_all(context):
    """every group open, or all closed when they are all open already (the same open / closed state as the arrows)"""
    root, P, _sel = select.current(context)
    if P is None:
        return
    gs = P.groups()
    on = not all(is_open(context, g) for g in gs)
    for g in gs:
        set_open(context, g.id, on)
    _redraw_all(context)


class HAIRMESH_OT_outliner_expand(bpy.types.Operator):
    """Open or close this group (Shift + click: every group)"""
    bl_idname = 'hairmesh.outliner_expand'
    bl_label = 'Expand HairMesh Group'
    bl_options = {'REGISTER', 'INTERNAL'}                           # UI state: no undo step

    group: IntProperty(name='Group', default=-1)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def invoke(self, context, event):
        if event.shift:
            expand_all(context)
            return {'FINISHED'}
        return self.execute(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        g = P.items.get(self.group) if P is not None else None
        if g is None or g.type != 'group':
            self.report({'ERROR'}, f'No group {self.group} in the project')
            return {'CANCELLED'}
        set_open(context, g.id, not is_open(context, g))            # the arrow (8473): UI state, never a step of the undo history
        _redraw_all(context)
        return {'FINISHED'}


class HAIRMESH_OT_outliner_reveal(bpy.types.Operator):
    """Open the groups down to the selected item (F)"""
    bl_idname = 'hairmesh.outliner_reveal'
    bl_label = 'Reveal in HairMesh Outliner'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        it, ancestors = OL.reveal_targets(P, sel.items, sel.anchors)
        if it is None:
            self.report({'INFO'}, 'Nothing selected')
            return {'CANCELLED'}
        wm = context.window_manager
        d = _wm_open(wm)
        for a in ancestors:
            d[a] = True                                             # revealInOutliner (8591): p.expanded = true up the tree
        _set_wm_open(wm, d)
        wm.hairmesh_outliner_filter = ''
        _redraw_all(context)
        return {'FINISHED'}


def _touch_extra(root, P, it):
    # touchExtra (8200, 4.4): a Density on / off — the locks of its group
    if it.type == 'tip':
        g = P.items.get(it.parent)
        if g is not None and g.type == 'group':
            live.touch(root, [c.id for c in G.curvesIn(P, g)])


class HAIRMESH_OT_toggle_visible(bpy.types.Operator):
    """Hide or show this item (the eye of the outliner: a hidden group hides its curves, anchors and walls; a hidden wall leaves the live mesh and the picking)"""
    bl_idname = 'hairmesh.toggle_visible'
    bl_label = 'Hide / Show HairMesh Item'
    bl_options = {'REGISTER', 'UNDO'}

    item: IntProperty(name='Item', description='The item id; -1 = the head', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        if self.item < 0:
            if P.head is None:
                self.report({'INFO'}, 'No head')
                return {'CANCELLED'}
            P.head.visible = not P.head.visible; name = P.head.name
        else:
            it = P.items.get(self.item)
            if it is None:
                self.report({'ERROR'}, f'No item {self.item} in the project')
                return {'CANCELLED'}
            it.visible = not it.visible; name = it.name              # toggleHide (8192)
            _touch_extra(root, P, it)
        P.rev += 1
        session.save_project(root, P)
        live.update(context, root)                                  # the live walls of a hidden group, or a hidden wall, go with it (1a)
        BRU.follow_active(context)                                  # (a hidden Extra is not painted)
        _redraw_all(context)
        self.report({'INFO'}, f'{name}: {"hidden" if not (P.head.visible if self.item < 0 else it.visible) else "shown"}')
        return {'FINISHED'}


def _poll_keys(cls, context):
    """H, Shift+H, Alt+H are contextual like Alt+A: the tool on, in Object Mode; a failed poll leaves the key to Blender"""
    from . import tool
    if _poll(context) and context.mode == 'OBJECT' and (context.area is None or context.area.type != 'VIEW_3D' or tool.is_active(context)):
        return True
    cls.poll_message_set('Works with the HairMesh tool active, in Object Mode')
    return False


def _poll_selected(cls, context):
    if not _poll_keys(cls, context):
        return False
    if select.has_any(context.scene.hairmesh):
        _r, _P, sel = select.current(context)
        if sel.items or sel.anchors:                                # (pruned: ids of items gone never keep the key from Blender)
            return True
    cls.poll_message_set('Nothing of the HairMesh is selected')
    return False


def _selected_ids(P, sel):
    """the ids of the selected items; anchors stand for the curves holding them"""
    ids = set(sel.items)
    if sel.anchors:
        aset = set(sel.anchors)
        ids.update(c.id for c in P.curves() if any(a.id in aset for a in c.anchors))
    return ids


def _hidden_by_the_view(P):
    """what Live / Baked keeps hidden, which only its buttons show: a mesh's sources, or the mesh while one of them is on
    screen; pending, what Convert hid"""
    c = P.conv or {}
    out = {*(c.get('hidden') or ()), *(c.get('shown') or ())}
    replaced = set(c.get('replaced') or ())
    ms = [m for m in conversion.meshes(P) if m.id not in replaced]
    owners = {m.parent for m in ms}
    for m in ms:
        src = conversion.meshSources(P, m, owners)
        if m.visible or not any(h.visible for h in src):
            out.update(h.id for h in src)
        else:
            out.add(m.id)
    return out


def _revealable(P):
    """(items, head): what Alt+H shows"""
    view = _hidden_by_the_view(P)
    return [it for it in P.items.values() if not it.visible and it.id not in view], P.head is not None and not P.head.visible


def _unselected(P, keep):
    """what Shift+H hides: the topmost items outside `keep`. A group holding something kept opens up, a kept group keeps
    what it holds; a lock's settings stay (their eye turns them off, and nothing of the kind leaves the screen with it)"""
    up = set()
    for i in keep:
        p = P.parentOf(P.items[i])
        while p is not None:
            up.add(p.id)
            p = P.parentOf(p)

    def in_kept(it):
        while it is not None:
            if it.id in keep:
                return True
            it = P.parentOf(it)
        return False
    return [it for it in P.items.values() if it.visible and it.id not in up and it.type not in ('tip', 'cap', 'skin', 'xmod') and not in_kept(it)
            and (P.parentOf(it) is None or P.parentOf(it).id in up)]


def _shown(context, root, P, sel, say):
    """the end of the three: what was hidden leaves the selection, as in Blender"""
    alive = {a.id for c in P.curves() if edit.isVisible(P, c) for a in c.anchors}
    sel.items = [i for i in sel.items if edit.isVisible(P, P.items[i])]
    sel.anchors = [a for a in sel.anchors if a in alive]
    select.store(context.scene.hairmesh, sel)
    P.rev += 1
    session.save_project(root, P)
    live.update(context, root)
    _redraw_all(context)
    return say


class HAIRMESH_OT_hide_selected(bpy.types.Operator):
    """Hide the selected items, as their eye in the HairMesh Outliner does (anchors: the curves holding them)"""
    bl_idname = 'hairmesh.hide_selected'
    bl_label = 'HairMesh Hide Selected'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_selected(cls, context)

    def execute(self, context):
        root, P, sel = select.current(context)
        gone = G.hiddenCrownCurves(P)
        its = [P.items[i] for i in _selected_ids(P, sel) if edit.isVisible(P, P.items[i]) and i not in gone]   # (what shows: its eye, its groups', its crown's)
        if not its:
            self.report({'INFO'}, 'Nothing to hide: the selection is hidden already')
            return {'CANCELLED'}
        for it in its:
            it.visible = False
            _touch_extra(root, P, it)
        self.report({'INFO'}, _shown(context, root, P, sel, f'Hid {len(its)} item{"" if len(its) == 1 else "s"}'))
        return {'FINISHED'}


class HAIRMESH_OT_hide_unselected(bpy.types.Operator):
    """Hide what is not selected, as the eye in the HairMesh Outliner does; the head stays"""
    bl_idname = 'hairmesh.hide_unselected'
    bl_label = 'HairMesh Hide Unselected'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_selected(cls, context)

    def execute(self, context):
        root, P, sel = select.current(context)
        its = _unselected(P, _selected_ids(P, sel))
        if not its:
            self.report({'INFO'}, 'Nothing else to hide')
            return {'CANCELLED'}
        for it in its:
            it.visible = False
        self.report({'INFO'}, _shown(context, root, P, sel, f'Hid {len(its)} item{"" if len(its) == 1 else "s"}'))
        return {'FINISHED'}


class HAIRMESH_OT_reveal_hidden(bpy.types.Operator):
    """Show everything hidden with the eye of the HairMesh Outliner, H or Shift+H; what Live / Baked hides stays with its buttons"""
    bl_idname = 'hairmesh.reveal_hidden'
    bl_label = 'HairMesh Reveal Hidden'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll_keys(cls, context):
            return False
        _r, P, _s = select.current(context)
        if P is not None and any(_revealable(P)):
            return True
        cls.poll_message_set('Nothing of the HairMesh is hidden')
        return False

    def execute(self, context):
        root, P, sel = select.current(context)
        its, head = _revealable(P)
        for it in its:
            it.visible = True
            _touch_extra(root, P, it)
        if head:
            P.head.visible = True
        n = len(its) + head
        self.report({'INFO'}, _shown(context, root, P, sel, f'Revealed {n} item{"" if n == 1 else "s"}'))
        return {'FINISHED'}


class HAIRMESH_OT_toggle_guide_power(bpy.types.Operator):
    """Switch this guide's effect on the loops on / off (a group: every guide inside it); hiding a guide with the eye does not switch it off"""
    bl_idname = 'hairmesh.toggle_guide_power'
    bl_label = 'HairMesh Guide On / Off'
    bl_options = {'REGISTER', 'UNDO'}

    item: IntProperty(name='Item', description='A guide, or a group holding guides', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        it = P.items.get(self.item)
        if it is None or it.type not in ('guide', 'group'):
            self.report({'ERROR'}, f'No guide or group {self.item} in the project')
            return {'CANCELLED'}
        on = G.setGuidePower(P, it, it.off if it.type == 'guide' else it.guidesOff)   # the ⏻ click (8515-8519)
        session.save_project(root, P)
        live.touch(root, G.guidePowerCurves(P, it))                 # S.surfDirty: the loops of the locks the guide shapes follow it (the others stay: bit-identical)
        live.update(context, root)
        if live.rebuild_report(root):
            self.report({'INFO'}, live.rebuild_report(root))
        _redraw_all(context)
        self.report({'INFO'}, f'{it.name}: guides {"on" if on else "off"}')
        return {'FINISHED'}


MODE_IDLE = ('{name} has no curves of its own: its mode reaches only the curves lying in it (each sub-group has its own). '
             'It is kept for the curves drawn in it later')


class HAIRMESH_OT_cycle_guides_mode(bpy.types.Operator):
    """Switch the group's Curves & anchors mode to the next one: Auto (shown while worked on) → Show → Hide → Auto"""
    bl_idname = 'hairmesh.cycle_guides_mode'
    bl_label = 'HairMesh Group Curves Mode'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        """part 1 of the lot ux7: why the mode of a group with no curves of its own changes nothing"""
        try:
            _r, P, _s = select.current(context)
            g = P.items.get(properties.group)
            if g is not None and g.type == 'group' and not modeActs(P, g):
                return MODE_IDLE.format(name=g.name)
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def execute(self, context):
        root, P, sel = select.current(context)
        g = P.items.get(self.group)
        if g is None or g.type != 'group':
            self.report({'ERROR'}, f'No group {self.group} in the project')
            return {'CANCELLED'}
        if not modeActs(P, g):
            self.report({'INFO'}, MODE_IDLE.format(name=g.name))
        G.setGuidesMode(P, g, OL.next_mode(g.guides or 'auto'))     # the button's click (8488; it is kept for the curves drawn in it later)
        session.save_project(root, P)
        _redraw_all(context)
        return {'FINISHED'}


class HAIRMESH_OT_outliner_cut(bpy.types.Operator):
    """Mark the selected items (curves, walls, groups) to be pasted into another group"""
    bl_idname = 'hairmesh.outliner_cut'
    bl_label = 'HairMesh Cut'
    bl_options = {'REGISTER', 'INTERNAL'}

    item: IntProperty(name='Item', description='One item instead of the selection', default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        ids = [self.item] if self.item >= 0 and self.item in P.items else [i for i in sel.items if i in P.items]
        if not ids:
            self.report({'INFO'}, 'Select something first')
            return {'CANCELLED'}
        _set_wm_ids(context.window_manager, 'hairmesh_clipboard', ids)
        _redraw_all(context)
        self.report({'INFO'}, f'{len(ids)} item{"" if len(ids) == 1 else "s"} cut: paste into a group of the outliner')
        return {'FINISHED'}


class HAIRMESH_OT_outliner_paste(bpy.types.Operator):
    """Move the cut items into this group (-1: the root level); curves and the walls between them go together"""
    bl_idname = 'hairmesh.outliner_paste'
    bl_label = 'HairMesh Paste'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', default=-1)
    with_walls: BoolProperty(name='Curves and Walls Together', description=WITH_WALLS_DESC, default=True)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        ids = [i for i in clipboard(context) if i in P.items]
        if not ids:
            self.report({'INFO'}, 'Nothing cut')
            return {'CANCELLED'}
        gid = self.group if self.group >= 0 else None
        if gid is not None and (gid not in P.items or P.items[gid].type != 'group'):
            self.report({'ERROR'}, f'No group {self.group} in the project')
            return {'CANCELLED'}
        zone = 'into' if gid is not None else 'root'
        if self.with_walls:
            ids = G.itemsToMoveTogether(P, ids, gid, zone)          # U3 (ours): the walls and curves that belong together
        moved, msg = G.moveItems(P, ids, gid, zone)
        if msg:
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        _set_wm_ids(context.window_manager, 'hairmesh_clipboard', [])
        forget_open_state(context, gid)                             # the model opened the target group (moveItems, 8578): the outliner shows it
        g = select.selected_group(P, sel)
        G.panelsRendered(P, g.id if g is not None else None)
        session.save_project(root, P)
        sync_collections(context, root, P)
        live.touch(root, G.movedCurveIds(P, moved))                 # S.surfDirty: the moved curves' locks take the new group's conv (bit-identical)
        live.update(context, root)
        _redraw_all(context)
        self.report({'INFO'}, f'{len(moved)} item{"" if len(moved) == 1 else "s"} moved to {P.items[gid].name if gid is not None else "the root level"}')
        return {'FINISHED'}


class HAIRMESH_OT_rename_item(bpy.types.Operator):
    """Rename the selected item (a group, a curve, a wall...): F2 in the viewport and over the N panel while the tool is active, a double click on its name in the outliner, the pencil of the outliner's bar"""
    bl_idname = 'hairmesh.rename_item'
    bl_label = 'Rename HairMesh Item'
    bl_options = {'REGISTER', 'UNDO'}

    item: IntProperty(name='Item', description='The item id; -1 = the last selected item', default=-1, options={'HIDDEN', 'SKIP_SAVE'})   # (hidden: the dialog shows the name alone)
    name: StringProperty(name='Name', default='')
    tool_only: BoolProperty(name='Tool Only', description='From the 3D View Generic keymap: pass the key through while the HairMesh tool is not active (Blender renames the active object then)', default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def _target(self, context):
        root, P, sel = select.current(context)
        it = P.items.get(self.item) if self.item >= 0 else select.last_item(P, sel)
        return root, P, it

    def invoke(self, context, event):
        if self.tool_only:
            from . import tool
            if not tool.is_active(context):
                return {'PASS_THROUGH'}
        root, P, it = self._target(context)
        if it is None:
            self.report({'INFO'}, 'Select an item first')
            return {'CANCELLED'}
        self.name = it.name
        LAST_INVOKE.append(('dialog', it.id))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        root, P, it = self._target(context)
        if it is None:
            self.report({'INFO'}, 'Select an item first')
            return {'CANCELLED'}
        if G.renameItem(P, it, self.name):                          # the outliner's rename (8270): trimmed, empty keeps
            if it.type == 'group':
                G.panelsRendered(P, it.id)
            session.save_project(root, P)
            sync_collections(context, root, P)
            live.update(context, root)                              # the "<group> live" object follows the name
        _redraw_all(context)
        self.report({'INFO'}, f'Renamed to "{it.name}"')
        return {'FINISHED'}


# ---------------- drawing ----------------
# Part 3 of the lot ux7: every row is cut into fixed widths by splits (fit.split_cols) — the indentation, the name,
# the columns of switches — so the columns start at the same place in every row whatever the name, the "≠", the
# "Color from …", the tags or the depth (a plain row shares its width in proportion to its items' texts: a longer
# name pushed the columns after it, the "A" of a renamed group left its column). The name never ends in "…": the
# pop-up widens for the longest name shown (up to MAX_UNITS_X), and a name still too long goes on in lines under it.

OL_PAD = 0.6                                                        # the pop-up's margins around the rows, in UI units


def _col(side, units, align='RIGHT'):
    """a column of fixed width in the switches block of a row: the block is exactly as wide as its columns (a
    split), so each gets its units"""
    c = side.row(align=True)
    c.ui_units_x = units
    c.alignment = align
    return c


def _blank(side, units=COL):
    c = _col(side, units)
    c.label(text='', icon='BLANK1')
    return c


def name_text(row):
    """the text of a row's name button: the name, "(free)" for a free curve, the crown a connector belongs to"""
    return row.name + ('  (free)' if row.free else '') + (f'  ({row.crown})' if row.crown else '')


def color_from_text(row):
    return f'Color from {row.group["color_from"]}'


def color_from_need(row):
    """the units "Color from <group>" takes with its icon, after the name (a row sized by its texts: fit.py)"""
    return fit.natural_label(color_from_text(row), True)


def name_need(row):
    """the units the name button takes (left-aligned: sized by its text, fit.natural_button)"""
    return fit.natural_button(name_text(row), True)


def lead_units(row):
    """the indentation and the slot (arrow / related marker) before the name: one unit per level"""
    return float(row.depth)


NAME_MIN = 4.0                                                      # the least room a name keeps beside its switches; less, they go on a line of their own
TEXT_MIN = 2.0                                                      # the least width a name wraps in (a very narrow panel)
COMPACT_UNITS = 16.0                                                # a narrower outliner (the N panel's dock at its default width) is compact


def item_side_units(info):
    """the switches of a curve, a wall or another item: Auto (hidden by the group mode), ⏻ when the column exists, eye;
    compact (a narrow dock): no ⏻"""
    return COL + (COL if info.get('guides') and not info.get('compact') else 0.0) + COL


def side_units(row, info):
    if row.kind == 'root':
        return 2 * COL
    if row.kind == 'head':
        return COL
    if row.type == 'group':
        return group_side_units(info)
    return item_side_units(info)


def group_side_units(info):
    """the width of a group row's columns (_group_columns), in UI units; compact (a narrow dock): the mode and the eye
    alone, so that no name is cut — the step, the color, the Container tag, ⏻, the tick and the drop column are in
    the pop-up (O) and in the group's sheet"""
    if info.get('compact'):
        return COL + COL
    return STEP_COL + MODE_COL + COL + (COL if info.get('guides') else 0.0) + COL + COL + COL


def pair_cells(row):
    """the cells of a curve | wall row with the units before their names: the curve indented, the wall its slot"""
    return [(c, lead) for c, lead in ((row.left, float(row.depth)), (row.right, 1.0)) if c is not None]


def paired(row, info, width):
    """whether a curve | wall row is drawn as a pair (each half its name and its switches) or as two rows (compact,
    or a half too narrow for a name)"""
    H = width / 2.0
    return not info.get('compact') and all(H - lead - item_side_units(info) >= NAME_MIN for _c, lead in pair_cells(row))


def row_need(row, info):
    """the units a row needs for its name (and "Color from") on one line"""
    if row.kind == 'pair':
        return 2.0 * max([lead + name_need(c) + item_side_units(info) for c, lead in pair_cells(row)] or [0.0])
    need = lead_units(row) + name_need(row) + side_units(row, info)
    if row.type == 'group' and row.group and row.group.get('color_from'):
        need += color_from_need(row)
    return need


def row_room(units_x):
    return units_x - OL_PAD


SIDEBAR_PAD = 2.6                                                   # the sidebar's panel margins, scroll bar and strip of tabs, in UI units (measured: 280 px give 11.4 units of rows at scale 1)


def units_of_region(width, ui_scale):
    """the UI units a sidebar region gives the rows (pure): its width less the panel's margins, the scroll bar and the
    strip of tabs (the review of part 3 of the lot ux7: the tabs were not counted)"""
    return width / (20.0 * ui_scale) - SIDEBAR_PAD


def popup_units(rows, info):
    """the pop-up's width: UNITS_X, widened up to MAX_UNITS_X so that the longest name shown (with its "Color from")
    fits on one line"""
    need = max((row_need(r, info) for r in rows), default=0.0) + OL_PAD + 0.3   # (a little slack: the widths are measured, Blender rounds to pixels)
    return min(MAX_UNITS_X, max(UNITS_X, need))


def color_from_room(row, info, units_x):
    """the UI units left on a group row for "Color from <group>" between the name and the columns"""
    return row_room(units_x) - lead_units(row) - name_need(row) - side_units(row, info)


def name_lines(row, room, more_room=None):
    """the lines of a row's name: [(text, kind)] — 'name' (the first, on the button, in the name area `room` units
    wide), 'more' (the rest of a name too long for it), 'inline' ("Color from <group>" after the name, when it fits
    there) or 'color' (it under the name). The lines under the first have `more_room` (the row's width after the
    indentation: the switches stay on the first line)"""
    more_room = max(room, more_room or room)
    text = name_text(row)
    if fit.natural_button(text, True) <= room:
        out = [(text, 'name')]
        used = fit.natural_button(text, True)
    else:
        first = fit.wrap(text, max(TEXT_MIN, room - fit.BTN_PAD - fit.BTN_ICON))[0]
        rest = text[len(first):].strip()
        out = [(first, 'name')] + [(ln, 'more') for ln in (fit.wrap(rest, max(TEXT_MIN, more_room - fit.BTN_PAD - fit.BTN_ICON)) if rest else [])]
        used = room
    if row.type == 'group' and row.group and row.group.get('color_from'):
        ct = color_from_text(row)
        if len(out) == 1 and used + color_from_need(row) <= room:
            out.append((ct, 'inline'))
        else:
            out += [(ln, 'color') for ln in fit.wrap(ct, max(TEXT_MIN, more_room - fit.LBL_PAD - fit.LBL_ICON))]
    return out


def single_plan(row, info, lead, width):
    """how a row (or one cell drawn as a row) is laid out at `width`: (its name lines, whether its switches go on a
    line of their own under the name — when the name would keep less than NAME_MIN beside them)"""
    R = side_units(row, info)
    room = width - lead - R
    if room >= NAME_MIN:
        return name_lines(row, room, width - lead), False
    return name_lines(row, width - lead), True


def row_lines(row, info, width):
    """how many lines a row takes at `width` units (its name and "Color from" wrapped, its switches on a line of
    their own in a narrow panel, a curve | wall pair as two rows there)"""
    if row.kind == 'pair':
        if paired(row, info, width):
            H = width / 2.0
            return max(single_lines(c, info, lead, H) for c, lead in pair_cells(row))
        return sum(single_lines(c, info, float(row.depth), width) for c, _lead in pair_cells(row))
    return single_lines(row, info, lead_units(row), width)


def single_lines(row, info, lead, width):
    lines, stacked = single_plan(row, info, lead, width)
    return len(lines) - sum(1 for _t, k in lines if k == 'inline') + (1 if stacked else 0)


def _name_button(layout, row, hm, text=None):
    """the name, left-aligned: the selection button (pressed when selected, embossed when related or holding the
    selection; a group holding the selection carries the blue collection icon: childsel), dimmed when hidden by the
    eye, by the group mode, or pending (a mesh not converted yet)"""
    r = layout.row(align=True)
    r.alignment = 'LEFT'
    r.active = not (row.hidden or row.mode_hidden or row.pending)
    icon = OL.HOLDS_ICON if row.holds and row.type == 'group' else row.icon
    op = r.operator('hairmesh.select_item', text=name_text(row) if text is None else text, icon=icon, emboss=row.selected or row.related or row.holds, depress=row.selected)
    op.item = row.id
    return r


def _slot(layout, row):
    """the column before the name: the arrow of a group, else the related marker (the orange of OL_REL) or nothing"""
    if row.type == 'group':
        op = layout.operator('hairmesh.outliner_expand', text='', icon='DISCLOSURE_TRI_DOWN' if row.expanded else 'DISCLOSURE_TRI_RIGHT', emboss=False)
        op.group = row.id
    elif row.related:
        m = layout.row(); m.active = False                          # dimmed: the pale orange of the original's related rows
        m.label(text='', icon=OL.RELATED_ICON)
    else:
        layout.label(text='', icon='BLANK1')


def _lead(layout, row, depth):
    """the tree indentation: one unit per level, the slot (arrow / marker) being the last of them"""
    for _ in range(max(0, int(depth) - 1)):
        layout.label(text='', icon='BLANK1')
    if depth > 0:
        _slot(layout, row)


def _eye(side, row):
    c = _col(side, COL)
    op = c.operator('hairmesh.toggle_visible', text='', icon='HIDE_ON' if row.hidden else 'HIDE_OFF', emboss=False)
    op.item = row.id if row.kind != 'head' else -1


def _power(side, row, info):
    """the ⏻ column: on a guide, or on a group holding guides; blank on the other rows while the column exists"""
    if not info['guides'] or info.get('compact'):
        return
    on = row.power if row.kind == 'item' and row.type != 'group' else (row.group['guides_power'] if row.group else None)
    if on is None:
        _blank(side)
        return
    c = _col(side, COL)
    # ⏻ (not the ☑ of the conversion tick): on = the filled circle, off = its outline at full strength (4a of the second
    # manual test of 21/10/2026: dimmed, the off state could hardly be seen); a guide's is dimmed only when it is on
    # but a group above switches its guides off (guideOn 722; a group's own switch is never dimmed, as before)
    c.active = not on or row.type != 'guide' or row.power_effective
    op = c.operator('hairmesh.toggle_guide_power', text='', icon='RADIOBUT_ON' if on else 'RADIOBUT_OFF', emboss=False)
    op.item = row.id


def can_drop(P, ids, gid):
    """whether the items can go into the group `gid` (None: the root level): what moveItems (8568) moves — not the head
    nor the target itself, no group inside itself — and at least one of them is not there already"""
    ids = [i for i in ids if i != gid and i in P.items and P.items[i].type != 'head']
    if gid is not None and any(G.isDescendant(P, gid, i) for i in ids):
        return False
    return any(P.items[i].parent != gid for i in ids)


def _drop(side, context, row, sel_items, info):
    """the drop column, only where the items can go: Paste while something is cut, else Move here, else blank"""
    gid = row.id if row.kind != 'root' else None
    cut = clipboard(context)
    if not can_drop(info['project'], cut or sel_items, gid):
        _blank(side)
        return
    c = _col(side, COL)
    op = c.operator('hairmesh.outliner_paste' if cut else 'hairmesh.move_to_group', text='', icon='PASTEDOWN' if cut else 'IMPORT', emboss=False)
    op.group = -1 if gid is None else gid


def _group_columns(side, context, row, hm, sel_items, info):
    """the switches of a group row, in fixed columns: the step, the mode with the color, the Container tag, ⏻,
    the tick, the drop, the eye (the Free curves tag left the row on 16/10/2026: the curves say "(free)" and the
    group's sheet in the HairMesh Menu has the switch)"""
    from .ops_groups import step_text
    g = row.group
    compact = info.get('compact')
    if not compact:
        c = _col(side, STEP_COL)
        if g['step'] is not None:
            c.active = not g['off']
            c.label(text=step_text(g['step']) + (' ≠' if g['off'] else ''))
        else:
            c.label(text='')
    mc = _col(side, COL if compact else MODE_COL, 'EXPAND')
    if not compact:
        mc = mc.split(factor=0.5, align=True)                       # (half and half: an icon in the mode button, whole)
    mb = mc.row(align=True); mb.active = g.get('mode_acts', True)   # part 1 of the lot ux7: dimmed on a group with no curves of its own (the tooltip says why)
    op = mb.operator('hairmesh.cycle_guides_mode', text='', **icons.icon_kw(MODE_ICONS[g['mode']])); op.group = row.id
    if not compact:
        entry = next((e for e in hm.rows if e.gid == row.id), None)
        sw = mc.row(align=True)
        sw.enabled = not g['color_from']                            # locked: a group above covers this colour (tintOf 754; part 3 of the second manual test of 21/10/2026)
        if entry is not None:
            sw.prop(entry, 'color', text='')
        else:
            sw.label(text='', icon='COLOR')
        c = _col(side, COL)
        c.label(text='', icon='BOOKMARKS' if 'container' in g['labels'] else 'BLANK1')
        _power(side, row, info)
        c = _col(side, COL)
        if not g['service']:
            op = c.operator('hairmesh.tick_group', text='', icon='CHECKBOX_HLT' if g['ticked'] else 'CHECKBOX_DEHLT', emboss=False); op.group = row.id; op.toggle = True
        else:
            c.label(text='', icon='BLANK1')
        _drop(side, context, row, sel_items, info)
    _eye(side, row)


def _item_columns(side, row, info):
    """the Auto mode's icon (hidden by the group's Auto mode, U2: the eye is on), ⏻, eye"""
    c = _col(side, COL)
    if row.mode_hidden:
        c.active = False; c.label(text='', **icons.icon_kw(MODE_ICONS['auto']))
    else:
        c.label(text='', icon='BLANK1')
    _power(side, row, info)
    _eye(side, row)


def _draw_single(layout, context, row, hm, sel_items, info, lead, width, right_cell=False):
    """one row (or one cell of a curve | wall pair) on its own physical rows, in fixed widths (fit.split_cols): the
    indentation and the slot, the name button with "Color from" after it when it fits, the switches — on the first
    line, or on a line of their own when the name would keep less than NAME_MIN beside them; the rest of a long name
    and "Color from" that did not fit go on in lines under it"""
    lines, stacked = single_plan(row, info, lead, width)
    R = side_units(row, info)
    inline = len(lines) > 1 and lines[1][1] == 'inline'
    rest = lines[2:] if inline else lines[1:]
    n = 1 + len(rest) + (1 if stacked else 0)
    rows = [layout.row(align=True) for _ in range(n)]
    widths = [lead, width - lead] if stacked else [lead, width - lead - R, R]
    cells = [fit.split_cols(rows[0], widths)] + [fit.split_cols(r, [lead, width - lead]) for r in rows[1:1 + len(rest)]]   # (the lines under the first span the switches' place too)
    lead_lay, name_lay = cells[0][0], cells[0][1]
    if lead_lay is not None:
        if right_cell:
            _slot(lead_lay, row)
        else:
            _lead(lead_lay, row, lead)
    name_lay.alignment = 'LEFT'
    if row.kind == 'root':                                          # the project: its name selects the root level (nothing selected)
        nb = name_lay.row(align=True); nb.alignment = 'LEFT'
        op = nb.operator('hairmesh.select_group', text=lines[0][0], icon=row.icon, emboss=row.selected, depress=row.selected); op.group = -1
    else:
        nb = _name_button(name_lay, row, hm, lines[0][0])
    if inline:                                                      # ours (part 2 of 21/10/2026): why its colour does not show
        c = nb.row(align=True); c.active = False
        c.alignment = 'LEFT'                                        # (sized by its text: a row of the default alignment counts ten units and squeezed the name)
        c.label(text=lines[1][0], icon='LINKED')
    if stacked:
        lw = min(lead, max(0.0, width - R))
        side = fit.split_cols(rows[-1], [lw, width - lw - R, R])[2] if width > R else rows[-1]
    else:
        side = cells[0][2]
    if row.kind == 'root':
        _drop(side, context, row, sel_items, info); _blank(side)    # (no eye on the project)
    elif row.kind == 'head':
        _eye(side, row)
    elif row.type == 'group':
        _group_columns(side, context, row, hm, sel_items, info)
    else:
        _item_columns(side, row, info)
    first_color = True
    for (ln, kind), cell in zip(rest, cells[1:]):
        if cell[0] is not None:
            cell[0].label(text='')
        n2 = cell[1]; n2.alignment = 'LEFT'
        if kind == 'more' and row.kind != 'root':                   # the rest of the name: still the name (it selects, it dims with it)
            r = n2.row(align=True); r.alignment = 'LEFT'
            r.active = not (row.hidden or row.mode_hidden or row.pending)
            op = r.operator('hairmesh.select_item', text=ln, icon='BLANK1', emboss=False); op.item = row.id
        else:
            r = n2.row(align=True); r.alignment = 'LEFT'; r.active = kind == 'more'
            r.label(text=ln, icon='LINKED' if kind == 'color' and first_color else 'BLANK1')
            first_color = first_color and kind != 'color'


def draw_row(layout, context, row, hm, sel_items, info):
    """a row of the outliner at info['row_units'] units, in fixed columns (fit.split_cols): the same columns in every
    row, the name wrapped under itself when it is too long; in a narrow dock the switches may take a line of their
    own and a curve | wall pair two rows"""
    W = info['row_units']
    if row.kind != 'pair':
        _draw_single(layout, context, row, hm, sel_items, info, lead_units(row), W)
        return
    if not paired(row, info, W):
        for cell, _lead in pair_cells(row):
            _draw_single(layout, context, cell, hm, sel_items, info, float(row.depth), W)
        return
    H = W / 2.0
    halves = fit.split_cols(layout.row(align=True), [H, H])
    for k, (cell, lead) in enumerate(((row.left, float(row.depth)), (row.right, 1.0))):
        if cell is None:
            halves[k].label(text='')
            continue
        col = halves[k].column(align=True)                         # (a half holds its lines one under the other; the row is as tall as the taller half)
        _draw_single(col, context, cell, hm, sel_items, info, lead, H, right_cell=k == 1)


def _bar_op(b, idname, icon, ok):
    r = b.row(align=True); r.active = ok
    return r.operator(idname, text='', icon=icon)


def bar_units(popup):
    """the buttons of the bar: expand / collapse all, reveal, new group, cut, paste, delete (and undo / redo in the pop-up)"""
    return 6.0 + (2.5 if popup else 0.0)


def footer_text(P, info):
    """the footer: "N groups · M curves · K walls", then what the rows left out or matched (OL.summary_text after its count)"""
    rest = OL.summary_text(info).split(' · ', 1)[1:]
    return ' · '.join([*ops_groups.counts(P), *rest])


def draw_outliner(layout, context, units_x=None, width=None):
    """the bar (the selected item's name, expand / collapse all, reveal, new group, cut, paste, delete), the rows, the filter and
    the summary. `width`: the units the dock's rows get (the sidebar's, by default; the tests give one)"""
    root, P, sel = select.current(context)
    rows, info = rows_of(context, P, root) if P is not None else ([], {})
    if units_x:
        units_x = popup_units(rows, info) if rows else units_x      # (wider while a name needs it, up to MAX_UNITS_X)
        layout.ui_units_x = units_x
        W = row_room(units_x)
    elif width:
        W = width
    else:                                                           # the dock: the sidebar's width
        region = getattr(context, 'region', None)
        W = units_of_region(region.width, pick.ui_scale(context)) if region is not None and region.width > 0 else row_room(UNITS_X)
    W = max(W, 4.0)
    info['compact'] = W < COMPACT_UNITS                             # (the review of part 3 of the lot ux7: the dock at the sidebar's default width, about 9.5 units)
    hm = context.scene.hairmesh
    it = select.last_item(P, sel) if P is not None else None
    B = bar_units(bool(units_x))
    room = W - B                                                    # the name field's share of the bar
    if it is not None and fit.button_need(it.name, True) + 1.0 > room:
        if fit.button_need(it.name, True) + 1.0 <= W:               # a narrow dock: the field on a row of its own
            layout.prop(hm, 'item_name', text='', icon=OL.item_icon(it))
        else:                                                       # a name its field would cut: said whole (a double click on its row renames)
            for i, ln in enumerate(fit.wrap(it.name, max(TEXT_MIN, W - fit.ICON - fit.LABEL_PAD))):
                layout.label(text=ln, icon=OL.item_icon(it) if i == 0 else 'BLANK1')
        field, b = None, layout.row(align=True)
    elif room >= 1.0:
        field, b = fit.split_cols(layout.row(align=True), [room, B])
    else:
        field, b = None, layout.row(align=True)
    if field is not None:
        if it is not None:                                          # 1d: the name of the selected item, edited in place
            field.prop(hm, 'item_name', text='', icon=OL.item_icon(it))
        elif units_x:                                               # (the pop-up's title; the dock's panel says "Outliner" in its header)
            field.label(text='HairMesh Outliner', icon='OUTLINER')
        else:
            field.label(text='')
    b = b.row(align=True); b.alignment = 'RIGHT'
    if units_x:                                                     # 1a of the second manual test of 21/10/2026: the pop-up swallows Ctrl+Z (over the N panel it works)
        _bar_op(b, 'hairmesh.undo_step', 'LOOP_BACK', bpy.ops.ed.undo.poll()).redo = False
        _bar_op(b, 'hairmesh.undo_step', 'LOOP_FORWARDS', bpy.ops.ed.redo.poll()).redo = True
        b.separator(factor=0.5)
    picked = bool(P is not None and (sel.items or sel.anchors))     # part 1 of the lot ux7: dimmed while there is nothing to act on (they say so when clicked)
    _bar_op(b, 'hairmesh.expand_all', icons.EXPAND_ALL, bool(P is not None and P.groups()))   # (the same button as the Conversion tab's group tree, ours)
    _bar_op(b, 'hairmesh.outliner_reveal', 'ZOOM_SELECTED', picked)
    b.operator('hairmesh.add_group', text='', icon='ADD')
    _bar_op(b, 'hairmesh.outliner_cut', 'UNLINKED', bool(P is not None and sel.items))
    g = select.selected_group(P, sel) if P is not None else None
    op = _bar_op(b, 'hairmesh.outliner_paste', 'PASTEDOWN', bool(clipboard(context))); op.group = g.id if g is not None else -1
    _bar_op(b, 'hairmesh.delete_selected', 'X', picked).keep_shape = False
    if P is None:
        layout.label(text='No HairMesh project', icon='INFO')
        return
    info['units_x'] = units_x or W
    info['row_units'] = W
    info['project'] = P
    col = layout.column(align=True)
    for row in rows:
        draw_row(col, context, row, hm, sel.items, info)
    layout.prop(context.window_manager, 'hairmesh_outliner_filter', text='', icon='VIEWZOOM')
    for ln in fit.wrap(footer_text(P, info), max(TEXT_MIN, W - fit.LABEL_PAD)):   # (the summary under the filter, wrapped: part 3 of the lot ux7)
        f = layout.row(); f.active = False
        f.label(text=ln)


class HAIRMESH_PT_outliner(bpy.types.Panel):
    """Open the HairMesh Outliner (O): the groups, curves and walls of the project"""
    bl_idname = OUTLINER_ID
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'                                       # drawn only as a pop-up, never docked
    bl_label = 'HairMesh Outliner'
    bl_ui_units_x = UNITS_X

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        draw_outliner(self.layout, context, UNITS_X)


class HAIRMESH_PT_outliner_dock(bpy.types.Panel):
    """The same tree at the top of the N tab"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Outliner'
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return session.find_root(context) is not None

    def draw(self, context):
        draw_outliner(self.layout, context)


_CLASSES = (HAIRMESH_OT_hide_selected, HAIRMESH_OT_hide_unselected, HAIRMESH_OT_reveal_hidden, HAIRMESH_OT_select_item, HAIRMESH_OT_outliner_expand, HAIRMESH_OT_outliner_reveal, HAIRMESH_OT_toggle_visible, HAIRMESH_OT_toggle_guide_power,
            HAIRMESH_OT_cycle_guides_mode, HAIRMESH_OT_outliner_cut, HAIRMESH_OT_outliner_paste, HAIRMESH_OT_rename_item, HAIRMESH_PT_outliner, HAIRMESH_PT_outliner_dock)


def register():
    bpy.types.WindowManager.hairmesh_outliner_open = StringProperty(name='Open Groups', description="JSON {group id: open} of the groups the user opened or closed in this session (the file's `expanded` otherwise)", default='{}')
    bpy.types.WindowManager.hairmesh_outliner_filter = StringProperty(name='Filter', description='Only the items whose name or type ("wall", "curve", "density", "free"...) contains this', default='', options={'TEXTEDIT_UPDATE'})
    bpy.types.WindowManager.hairmesh_clipboard = StringProperty(name='Clipboard', description='JSON ids of the cut items', default='[]')
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    for k in ('hairmesh_outliner_open', 'hairmesh_outliner_filter', 'hairmesh_clipboard'):
        if hasattr(bpy.types.WindowManager, k):
            delattr(bpy.types.WindowManager, k)
    LAST_INVOKE.clear()
