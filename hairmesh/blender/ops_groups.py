# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Phase 3.6 inside Blender: groups are collections (D5). The model is the source of truth: a group lives in the
project JSON, and its collection (tagged hairmesh_id, under the root's collection tree) is a regenerable view
kept in step by sync_collections after every group operator, where the converted meshes and the live walls of
the group land. The SELECTED group is part of the selection (S.sel), as the original selects a group in its
outliner (since 15/10/2026, U1 of docs/fase-3-ux-revisao.md; from 3.2 to 3.6 the active collection stood for it):
the Group section of the pies, the HairMesh Menu and the N panel shows the last selected item when it is a group
(renderProps 8645), new curves are drawn into the group of the selection (targetGroup 4729, ops_draw), and the
curves of a group in 'auto' mode show while something inside it is selected (activeGroups, 11905). The active
collection means nothing to the add-on any more.

  hairmesh.add_group (name, parent)      "Create empty group" (the pie, 7919): a group under the selected one (or at the
                                        root), ticked for conversion, selected
  hairmesh.select_group (group, extend)  selects a group (the outliner click of the original: selectItem 7844; -1 clears
                                        the selection: the root level)
  (rename: hairmesh.rename_item of ops_outliner, the one rename of every item — U6, 16/10/2026)
  hairmesh.delete_group                  deleteSelected (8106) on the selected group: it and everything inside; its
                                        parent group is selected afterwards
  hairmesh.move_to_group (group)         moveItems (8568, 'into' / 'root') of the selected curves and walls; the
                                        HAIRMESH_MT_move_to_group submenu lists the root and every group; since
                                        16/10/2026 (U3) the curves and the walls between them go together
                                        (with_walls, groups.itemsToMoveTogether), unless switched off
  hairmesh.match_project_step            "Match project loop step" (matchProjectStep, 1661) of the selected group
  hairmesh.detach_group                  "Detach from neighboring groups" (detachGroup, 3001) of the selected group
  hairmesh.tick_group (group, on)        the Conversion list's checkbox (9036): S.convChecked
  hairmesh.tick_all (on) / tick_top      ours: Tick Every Group / Uncheck All, Tick Top Groups (U7 C)
  hairmesh.clear_group_color (group)     "Reset color" of the group panel (8649; the RMB on the outliner's swatch, 8503:
                                        the swatch's right-click menu here, swatch_menu)
  hairmesh.step_from_object              the reference cube's "Set reference" (toggleReference, 9115-9131) replaced
                                        by the active object's height (D of the plan: setConv('step', height))
The group's own settings are properties of Scene.hairmesh (props.py: group_*, views on the selected group; the
group's Loop Step edits the group alone since U4, 15/10/2026) and the Conversion tab's sliders (conv_*, over the
ticked groups as setConv does). Every operator ends with session.save_project (one undo step), sync_collections
and live.update (inside the same undo step)."""
import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from ..core import convert as CV, edit, effects as E, groups as G
from ..core.names import unique_names
from ..core.selection import Selection
from . import live, naming, owner, select, session


# ---------------- collections <-> groups ----------------

def root_collection(context, root):
    """the collection tree of the root (make_root tags it with hairmesh_root), else the scene collection"""
    return next((c for c in bpy.data.collections if c.get('hairmesh_root') == root.name), None) or context.scene.collection


def collection_of(root_col, gid):
    """the collection tagged with the group id under the root's tree, or None"""
    stack = [root_col]
    while stack:
        col = stack.pop()
        if col.get('hairmesh_id') == gid:
            return col
        stack.extend(col.children)
    return None


def sync_collections(context, root, P):
    """The collection tree follows the model: one collection per group (tagged hairmesh_id), named after it, under
    its parent's collection; the collections of groups the model no longer has go (their objects are relinked to
    the parent collection). Returns {group id: collection}. The order of the children is not enforced (Blender
    appends; the model's order rules the pies and the panels). The group's visibility (g.visible, the eye of the
    original's outliner) is NOT written to the collection's hide flags: disabling the active collection makes
    Blender drop it as the active one (seen on 15/10/2026), so the switch acts on the model alone — the curves,
    anchors and live walls of a hidden group are not drawn, picked or shown (isVisible), as in the original."""
    root_col = root_collection(context, root)
    cols = {None: root_col}
    seen = set()
    names = unique_names(P, naming.held_by_others(root))            # (3.7b: groups that share a name: "X", "X (2)", never Blender's "X.001"; 3.8a: nor a name held elsewhere in the .blend)

    def walk(ids):
        for iid in ids:
            g = P.items.get(iid)
            if g is None or g.type != 'group':
                continue
            parent = cols[g.parent]
            col = collection_of(root_col, g.id)
            if col is None:
                col = bpy.data.collections.new(names[g.id])
                col['hairmesh_id'] = g.id
                parent.children.link(col)
            elif col.name not in parent.children:
                for other in [c for c in bpy.data.collections if col.name in c.children]:
                    other.children.unlink(col)
                parent.children.link(col)
            if col.name != names[g.id] and bpy.data.collections.get(names[g.id]) is None:   # (a name still held: the last pass below, or never when it is not ours)
                col.name = names[g.id]
            cols[g.id] = col; seen.add(g.id)
            walk(g.children)
    walk(P.roots)
    stack = [(root_col, None)]
    while stack:
        col, parent = stack.pop()
        gid = col.get('hairmesh_id')
        if gid is not None and gid not in seen:                     # a group the model no longer has: its objects go up, the collection goes
            target = cols.get(P.items[gid].parent if gid in P.items else None) or parent or root_col
            for ob in list(col.objects):
                if ob.name not in target.objects:
                    target.objects.link(ob)
                col.objects.unlink(ob)
            for child in list(col.children):
                target.children.link(child); col.children.unlink(child)
            bpy.data.collections.remove(col)
            continue
        stack.extend((c, col) for c in col.children)
    for gid, col in cols.items():                                   # a name a collection gone above (or renamed since) still held: taken now
        if gid is not None and col.name != names[gid] and bpy.data.collections.get(names[gid]) is None:   # (held by something not ours: left)
            col.name = names[gid]
    return cols


def counts(P):
    """["N groups", "M curves", "K walls"] as the user counts them, the same in the Outliner's footer and the Project tab:
    neither the Guides group nor a disc's rings"""
    def count(n, word):
        return f'{n} {word}{"" if n == 1 else "s"}'
    return [count(len(G.groupsOf(P)), 'group'), count(sum(not c.ring for c in P.curves()), 'curve'), count(len(P.surfaces()), 'wall')]


def selected_group(context, P):
    """the id of the selected group (the last selected item when it is a group, as the original's outliner selects
    one: renderProps 8645), else None"""
    g = select.selected_group(P, select.load(context.scene.hairmesh).prune(P))
    return g.id if g is not None else None


def select_group_in(context, P, gid):
    """the selection becomes this group alone (None: nothing selected, the root level); the stored selection"""
    sel = Selection([gid], []) if gid is not None else Selection([], [])
    select.store(context.scene.hairmesh, sel)
    return sel


def _finish(context, root, P, sel=None, report=None, msg=None, full=False, touch=None):
    if sel is not None:
        select.store(context.scene.hairmesh, sel)
    session.save_project(root, P)                                   # the original's commit(): one undo step
    sync_collections(context, root, P)
    if full:
        live.invalidate_rows(root)                                  # S.surfDirty where nobody can name the locks
    elif touch:
        live.touch(root, touch)                                     # S.surfDirty, limited to the locks of these curves (16/10/2026)
    live.update(context, root)                                      # the preview inside the operator's own undo step
    select.redraw(context)
    if report is not None and msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def new_group_guides(context):
    """the Curves & anchors mode of a new group: the add-on preference (U2; 'auto' as the original when the add-on
    is registered by hand, as in the tests)"""
    from . import prefs
    p = prefs.get(context)
    return p.new_group_guides if p is not None else 'auto'


def _active(context):
    """(root, P, the selected group item or None)"""
    root = session.find_root(context)
    P = session.get_project(root)
    return root, P, select.selected_group(P, select.load(context.scene.hairmesh).prune(P))


NO_GROUP = 'Select a group first (in the HairMesh Outliner, O)'


class HAIRMESH_OT_add_group(bpy.types.Operator):
    """Create an empty group in the group of the selection (a selected group, else the group of the last selected item, else the root level), ticked for conversion and selected: new curves go into it"""
    bl_idname = 'hairmesh.add_group'
    bl_label = 'New HairMesh Group'
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name='Name', description='The group name ("Group N" when empty)', default='')
    parent: IntProperty(name='Parent', description='The group to create it in (-1: the group of the selection; -2: the root level)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    guides: StringProperty(name='Curves Mode', description="The new group's Curves & anchors mode: 'auto' | 'show' | 'hide'; empty = the add-on preference (Auto, as the original)", default='', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        parent = None if self.parent == -2 else self.parent if self.parent >= 0 else select.target_group(P, sel)   # targetGroup (4729)
        if parent is not None and (parent not in P.items or P.items[parent].type != 'group'):
            self.report({'ERROR'}, f'No group {parent} in the project')
            return {'CANCELLED'}
        n = G.createGroup(P, parent, self.name.strip() or None)
        mode = self.guides or new_group_guides(context)
        if mode != 'auto':                                          # U2: the preference "Show" for new groups (an addition of the port)
            G.setGuidesMode(P, n, mode)
        return _finish(context, root, P, Selection([n.id], []), self.report, f'Group "{n.name}" created')   # the new group is the selection (7919): new curves go into it


class HAIRMESH_OT_select_group(bpy.types.Operator):
    """Select this group: its settings come up in the Selection tab and new curves go into it; -1 clears the selection (the root level)"""
    bl_idname = 'hairmesh.select_group'
    bl_label = 'Select HairMesh Group'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', description='The group id (-1: nothing selected, the root level)', default=-1)
    extend: BoolProperty(name='Extend', description='Shift: add the group to the selection (or take it out)', default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        self.extend = self.extend or event.shift
        return self.execute(context)

    def execute(self, context):
        root = session.find_root(context); P = session.get_project(root)
        gid = self.group if self.group >= 0 else None
        if gid is not None and (gid not in P.items or P.items[gid].type != 'group'):
            self.report({'ERROR'}, f'No group {self.group} in the project')
            return {'CANCELLED'}
        sel = select.load(context.scene.hairmesh).prune(P)
        if gid is None:
            sel.box([], False)                                      # a click in the void: nothing selected
        else:
            sel.select_item(gid, self.extend)                       # selectItem (7844)
        G.panelsRendered(P, select.selected_group(P, sel).id if select.selected_group(P, sel) is not None else None)   # the group's panel fills its conv (8668)
        select.store(context.scene.hairmesh, sel)
        session.save_project(root, P)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_delete_group(bpy.types.Operator):
    """Delete the selected group with everything inside it (curves, walls, sub-groups); its parent group is selected afterwards"""
    bl_idname = 'hairmesh.delete_group'
    bl_label = 'Delete HairMesh Group'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        root, P, g = _active(context)
        if g is None:
            self.report({'INFO'}, NO_GROUP)
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(self, event, title=f'Delete "{g.name}" and the {len(G.curvesIn(P, g))} curves inside it?')

    def execute(self, context):
        root, P, g = _active(context)
        if g is None:
            self.report({'INFO'}, NO_GROUP)
            return {'CANCELLED'}
        name = g.name; parent = g.parent
        edit.deleteItem(P, g.id); G.panelsRendered(P, parent)
        return _finish(context, root, P, Selection([parent] if parent is not None else [], []), self.report, f'Group "{name}" deleted')   # (S.surfDirty: the locks that lost walls are rebuilt by themselves)


WITH_WALLS_DESC = ('Move curves and their connected walls together: a moved curve takes the walls it drives whose other curve '
                   'goes too (or is already in that group), a moved wall takes its two curves. Off: only the selected items move')


class HAIRMESH_OT_move_to_group(bpy.types.Operator):
    """Move the selected curves and walls into this group (-1: the root level), as the outliner's drag does; curves and the walls between them go together"""
    bl_idname = 'hairmesh.move_to_group'
    bl_label = 'Move HairMesh Selection to Group'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', description='The group id (-1: the root level)', default=-1)
    with_walls: BoolProperty(name='Curves and Walls Together', description=WITH_WALLS_DESC, default=True)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        ids = [i for i in sel.items if i in P.items]
        if not ids:
            self.report({'INFO'}, 'Select curves, walls or groups first')
            return {'CANCELLED'}
        gid = self.group if self.group >= 0 else None
        if gid is not None and (gid not in P.items or P.items[gid].type != 'group'):
            self.report({'ERROR'}, f'No group {self.group} in the project')
            return {'CANCELLED'}
        zone = 'into' if gid is not None else 'root'
        if self.with_walls:
            ids = G.itemsToMoveTogether(P, ids, gid, zone)          # U3 (16/10/2026, ours): the walls and curves that belong together
        moved, msg = G.moveItems(P, ids, gid, zone)
        if msg:
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        if not moved:                                               # (the target itself, the head: moveItems drops them)
            self.report({'INFO'}, 'Nothing to move')
            return {'CANCELLED'}
        G.panelsRendered(P, selected_group(context, P))
        from . import ops_outliner
        ops_outliner.forget_open_state(context, gid)                # the model opened the target group (moveItems): the outliner shows it
        where = P.items[gid].name if gid is not None else 'the root level'
        return _finish(context, root, P, None, self.report, f'{len(moved)} item{"s" if len(moved) != 1 else ""} moved to {where}', touch=G.movedCurveIds(P, moved))


def _key_context(context, say):
    """(root, P, sel) for P / Shift+P (4.0, D4.3), or None: a key that does not serve passes on (in a 3D View only
    with the tool active; a menu greys the item and says why)"""
    if not _poll(context):
        return None
    if context.area is not None and context.area.type == 'VIEW_3D':
        from . import tool
        if not tool.is_active(context):
            say('P / Shift+P act with the HairMesh tool active (Shift+Alt+H)')
            return None
    root, P, sel = select.current(context)
    if P is None:
        say('No HairMesh project in this scene')
        return None
    return root, P, sel


def _parent_plan(context, say):
    got = _key_context(context, say)
    if got is None:
        return None
    root, P, sel = got
    ids, target, msg = G.parentSelection(P, sel.items)
    if msg:
        say(msg)
        return None
    return root, P, sel, ids, target


def _unparent_plan(context, say):
    got = _key_context(context, say)
    if got is None:
        return None
    root, P, sel = got
    ids, msg = G.unparentSelection(P, sel.items)
    if msg:
        say(msg)
        return None
    return root, P, sel, ids


def _move(self, context, root, P, ids, gid):
    zone = 'into' if gid is not None else 'root'
    if self.with_walls:
        ids = G.itemsToMoveTogether(P, ids, gid, zone)              # U3 (ours), as Move to Group
    moved, msg = G.moveItems(P, ids, gid, zone)
    if msg or not moved:
        self.report({'WARNING'} if msg else {'INFO'}, msg or 'Nothing to move')
        return {'CANCELLED'}
    G.panelsRendered(P, selected_group(context, P))
    from . import ops_outliner
    ops_outliner.forget_open_state(context, gid)
    where = P.items[gid].name if gid is not None else 'the root level'
    return _finish(context, root, P, None, self.report, f'{len(moved)} item{"s" if len(moved) != 1 else ""} moved to {where}', touch=G.movedCurveIds(P, moved))


class HAIRMESH_OT_parent_to_last(bpy.types.Operator):
    """Put the selected curves, walls and groups into the group selected last (P): one undo step"""
    bl_idname = 'hairmesh.parent_to_last'
    bl_label = 'HairMesh Parent to Last Selected Group'
    bl_options = {'REGISTER', 'UNDO'}

    with_walls: BoolProperty(name='Curves and Walls Together', description=WITH_WALLS_DESC, default=True)

    @classmethod
    def poll(cls, context):
        return _parent_plan(context, cls.poll_message_set) is not None

    def execute(self, context):
        got = _parent_plan(context, lambda m: self.report({'INFO'}, m))
        if got is None:
            return {'CANCELLED'}
        root, P, _sel, ids, target = got
        return _move(self, context, root, P, ids, target)


class HAIRMESH_OT_unparent(bpy.types.Operator):
    """Take the selected curves, walls and groups out of their groups, to the root level (Shift+P): one undo step"""
    bl_idname = 'hairmesh.unparent'
    bl_label = 'HairMesh Unparent to Root'
    bl_options = {'REGISTER', 'UNDO'}

    with_walls: BoolProperty(name='Curves and Walls Together', description=WITH_WALLS_DESC, default=True)

    @classmethod
    def poll(cls, context):
        return _unparent_plan(context, cls.poll_message_set) is not None

    def execute(self, context):
        got = _unparent_plan(context, lambda m: self.report({'INFO'}, m))
        if got is None:
            return {'CANCELLED'}
        root, P, _sel, ids = got
        return _move(self, context, root, P, ids, None)


def match_done(P, g):
    return f'{g.name} and the groups inside it already use the project loop step ({step_text(G.projectStep(P))}): nothing to match'


class HAIRMESH_OT_match_project_step(bpy.types.Operator):
    """Recalculate this group (and the groups inside it) with the project loop step — for locks made by modifiers or brought in from elsewhere"""
    bl_idname = 'hairmesh.match_project_step'
    bl_label = 'Match Project Loop Step'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        try:
            _r, P, g = _active(context)
            if g is not None and not E.matchActs(P, g):
                return match_done(P, g)
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def execute(self, context):
        root, P, g = _active(context)
        if g is None:
            self.report({'INFO'}, NO_GROUP)
            return {'CANCELLED'}
        if not E.matchActs(P, g):                                   # part 1 of the lot ux7: nothing would change (the button is dimmed)
            self.report({'INFO'}, match_done(P, g))
            return {'CANCELLED'}
        st = G.matchProjectStep(P, g); G.panelsRendered(P, g.id)
        return _finish(context, root, P, None, self.report, f'"{g.name}": loop step {st:.3g} (project)', touch=[c.id for c in G.curvesIn(P, g)])


class HAIRMESH_OT_detach_group(bpy.types.Operator):
    """Give the curves of this group welded to curves of other groups anchors of their own there (welds inside the group stay)"""
    bl_idname = 'hairmesh.detach_group'
    bl_label = 'Detach from Neighboring Groups'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        try:
            _r, P, g = _active(context)
            if g is not None and not E.detachActs(P, g):
                return f"{g.name} isn't welded to other groups: nothing to detach"
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def execute(self, context):
        root, P, g = _active(context)
        if g is None:
            self.report({'INFO'}, NO_GROUP)
            return {'CANCELLED'}
        from ..core import curves as CM
        CM.rebuild_dirty(P)
        r = G.detachGroup(P, g)
        if r is None:
            self.report({'INFO'}, f"{g.name} isn't welded to other groups")
            return {'CANCELLED'}
        shared, att = r
        G.panelsRendered(P, g.id)
        root_, P_, sel = select.current(context)
        sel.anchors = []                                            # S.asel = []
        msg = f'{g.name}: {shared} shared anchor{"" if shared == 1 else "s"} detached' + (f", {att} end{'' if att == 1 else 's'} let go of another group's connector" if att else '')
        return _finish(context, root, P, sel, self.report, msg, touch=[c.id for c in G.curvesIn(P, g)])


def _tick_tip(context, gid):
    """the tooltip of a group's checkbox in the Conversion list: what its state means for the meshes (part 2 of
    21/10/2026, U7 A); None: the operator's own"""
    root = session.find_root(context)
    P = session.get_project(root) if root is not None else None
    row = next((r for r in G.convTree(P) if r[0].id == gid), None) if P is not None else None
    if row is None:
        return None
    g, _d, state, via = row
    if state == 'via':                                              # part 2 of 21/10/2026: what the dimmed box means
        kind, unit = where_in(P, g, via)
        if kind == 'merged':
            where = 'merged with the rest of it (shared trunks welded when Merge Shared Trunks is on)'
        elif kind == 'apart':
            where = ('as a piece of its own (a Container above keeps its sub-groups apart)' if container_above(P, g, via) else
                     'its own walls a piece of their own (it is a Container: they and each of its sub-groups come apart)')
        else:
            where = f'merged with the piece of {unit.name} (a Container above keeps its sub-groups apart)'
        return (f'Included through {via.name}, which is ticked: the Conversion sliders edit it and Convert puts it in the mesh of {via.name}, {where}. '
                f'It cannot be left out while {via.name} is ticked. Click to tick it on its own too: it is then converted as a mesh of its own, not merged with {via.name}')
    if state != 'ticked':
        return None
    if g.id not in {x.id for x, _n, _k in (plan_of(P) or [])}:     # U7 A: "· no mesh"
        return (f'{g.name} is ticked but makes no mesh: every wall inside it is in a sub-group ticked on its own (each its own mesh), '
                'or it has none' + (f'. It stays included through {via.name} for the sliders' if via is not None else ''))
    own = (f'Ticked on its own inside {via.name} (also ticked): converted as a mesh of its own, apart from {via.name} (not merged with it). '
           f'Untick: back into the mesh of {via.name} (it stays included)') if via is not None else f'Ticked: Convert makes the mesh "{g.name} mesh". Untick: not converted'
    pieces, carried = CV.meshPieces(P, g)
    if not carried:
        return own
    if pieces == 1:                                                 # U7 A: "· sub-groups merged / apart / N pieces"
        return own + '. Its sub-groups that are not ticked on their own are merged into it (one piece; shared trunks welded when Merge Shared Trunks is on)'
    if g.labels and g.labels.get('container'):
        return own + '. Its sub-groups that are not ticked on their own come into it apart, each a piece of its own (Container)'
    return own + f'. Its walls come out in {pieces} pieces: a Container among its sub-groups keeps the sub-groups inside it apart'


class HAIRMESH_OT_tick_group(bpy.types.Operator):
    """Tick / untick this group for conversion (the Conversion sliders edit the ticked groups; Convert makes their meshes)"""
    bl_idname = 'hairmesh.tick_group'
    bl_label = 'Tick HairMesh Group for Conversion'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', default=-1)
    on: BoolProperty(name='On', description='Tick (else untick); unset: toggle', default=True, options={'SKIP_SAVE'})
    toggle: BoolProperty(name='Toggle', default=True, options={'HIDDEN', 'SKIP_SAVE'})
    only: BoolProperty(name='Only This', description='Tick this group alone (the others are unticked)', default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        if properties.only:
            try:
                P = session.get_project(session.find_root(context)); g = P.items.get(properties.group)
                if g is not None and not E.tickOnlyActs(P, g):
                    return f'{g.name} is already the only group ticked'
            except Exception:                                       # (a tooltip must never raise)
                pass
            return 'Tick this group alone: the others are unticked (the Conversion sliders then edit this group)'
        try:
            return _tick_tip(context, properties.group) or cls.__doc__
        except Exception:                                           # (a tooltip must never raise: an unreadable project text)
            return cls.__doc__

    def execute(self, context):
        root = session.find_root(context); P = session.get_project(root)
        g = P.items.get(self.group)
        if g is None or g.type != 'group':
            self.report({'ERROR'}, f'No group {self.group} in the project')
            return {'CANCELLED'}
        if self.only:
            if not E.tickOnlyActs(P, g):                            # part 1 of the lot ux7: the ★ is dimmed then
                self.report({'INFO'}, f'{g.name} is already the only group ticked')
                return {'CANCELLED'}
            G.tickOnly(P, g)                                        # ours (1c, 21/10/2026)
        else:
            G.tickGroup(P, g, (g.id not in P.settings.convChecked) if self.toggle else self.on)
        t = G.convTargets(P)
        if t:
            G.groupConv(P, t[0])                                    # syncConvUI (9097)
        session.save_project(root, P)
        select.redraw(context)
        return {'FINISHED'}


TICK_ALL_DONE = {True: 'Every group is already ticked', False: 'No group is ticked'}
TICK_TOP_DONE = 'The top groups alone are already ticked'


class HAIRMESH_OT_tick_all(bpy.types.Operator):
    """Tick every group of the Conversion list: a mesh per group, each sub-group apart from its parent (as the example file comes). Untick: none ticked, the sliders then edit the project defaults"""
    bl_idname = 'hairmesh.tick_all'
    bl_label = 'Tick Every HairMesh Group for Conversion'
    bl_options = {'REGISTER', 'UNDO'}

    on: BoolProperty(name='On', description='Tick every group (else untick every group)', default=True)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        try:
            if not E.tickAllActs(session.get_project(session.find_root(context)), properties.on):
                return TICK_ALL_DONE[properties.on]
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__ if properties.on else 'Untick every group of the Conversion list (the sliders then edit the project defaults)'

    def execute(self, context):
        # ours (1c of the manual test of 21/10/2026, docs/features-adicionadas.md): the original ticks one by one
        root = session.find_root(context); P = session.get_project(root)
        if not E.tickAllActs(P, self.on):                           # part 1 of the lot ux7: nothing to change, no undo step
            self.report({'INFO'}, TICK_ALL_DONE[self.on])
            return {'CANCELLED'}
        G.tickAll(P, self.on)
        t = G.convTargets(P)
        if t:
            G.groupConv(P, t[0])                                    # syncConvUI (9097)
        session.save_project(root, P)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_tick_top(bpy.types.Operator):
    """Tick only the top groups (those with no group above them), untick the rest: a mesh per tree, its sub-groups merged into it"""
    bl_idname = 'hairmesh.tick_top'
    bl_label = 'Tick Top HairMesh Groups for Conversion'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        try:
            if not E.tickTopActs(session.get_project(session.find_root(context))):
                return TICK_TOP_DONE
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def execute(self, context):
        # ours (U7 C of docs/fase-3-ux-revisao.md): "convert everything, the sub-groups merged" in one click
        root = session.find_root(context); P = session.get_project(root)
        if not E.tickTopActs(P):                                    # part 1 of the lot ux7
            self.report({'INFO'}, TICK_TOP_DONE)
            return {'CANCELLED'}
        G.tickTop(P)
        t = G.convTargets(P)
        if t:
            G.groupConv(P, t[0])                                    # syncConvUI (9097)
        session.save_project(root, P)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_clear_group_color(bpy.types.Operator):
    """Clear the group's own color: its curves and walls show the color of a colored group above it, else none, and the sub-groups it covered show their own colors again"""
    bl_idname = 'hairmesh.clear_group_color'
    bl_label = 'Clear HairMesh Group Color'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', description='The group id (-1: the selected group)', default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        # port of the group panel's "Reset colour" and of the RMB / double click on the outliner's swatch (8649,
        # 8498-8503): it.color = null
        root, P, sel = select.current(context)
        g = P.items.get(self.group) if self.group >= 0 else select.selected_group(P, sel)
        if g is None or g.type != 'group':
            self.report({'INFO'} if self.group < 0 else {'ERROR'}, NO_GROUP if self.group < 0 else f'No group {self.group} in the project')
            return {'CANCELLED'}
        if not g.color:
            self.report({'INFO'}, f'"{g.name}" has no color of its own')
            return {'CANCELLED'}
        G.setColor(P, g, None); G.panelsRendered(P, selected_group(context, P))
        return _finish(context, root, P, None, self.report, f'"{g.name}": color cleared')


def swatch_menu(self, context):
    """the right-click menu of a group's color swatch (the outliner's row, the group sheet): Clear Group Color, as
    the original clears the color with a right click on its swatch (8503) — Blender's own "Reset to Default Value"
    there would store the default color instead of none"""
    if not owner.visible(context):
        return
    ptr = getattr(context, 'button_pointer', None); prop = getattr(context, 'button_prop', None)
    if ptr is None or prop is None:
        return
    from . import props
    if isinstance(ptr, props.HairMeshRow) and prop.identifier == 'color':
        gid = ptr.gid
    elif isinstance(ptr, props.HairMeshSceneProps) and prop.identifier == 'group_color':
        gid = -1
    else:
        return
    root, P, sel = select.current(context)
    g = (P.items.get(gid) if gid >= 0 else select.selected_group(P, sel)) if P is not None else None
    if g is None or not g.color:
        return
    self.layout.separator()
    self.layout.operator(HAIRMESH_OT_clear_group_color.bl_idname, text='Clear Group Color', icon='X').group = g.id


class HAIRMESH_OT_step_from_object(bpy.types.Operator):
    """Set the loop step from the height of the active object: its height becomes the loop step of the ticked groups, or the default step"""
    bl_idname = 'hairmesh.step_from_object'
    bl_label = 'Loop Step from Object Height'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob is None or session.is_root(ob):
            cls.poll_message_set('Make an object active (Blender\'s Outliner or a click): its height becomes the loop step')
            return False
        return _poll(context)

    def execute(self, context):
        root = session.find_root(context); P = session.get_project(root)
        ob = context.active_object
        h = object_height(root, ob)
        if not h or h <= 0:
            self.report({'ERROR'}, f'"{ob.name}" has no height')
            return {'CANCELLED'}
        from .coords import f32
        h = f32(h)
        t = G.setConv(P, 'step', h)
        return _finish(context, root, P, None, self.report, f'Loop step = {h:.3g}' + (f' ({len(t)} ticked group{"s" if len(t) != 1 else ""})' if t else ' (project default)'),
                       touch=[c.id for c in P.curves()])            # the project loop step: every lock without a step of its own reads it


def object_height(root, ob):
    """the object's bounding-box height along the model's up axis (Blender Z) in the root's local space (the model's
    units): what the reference cube's height is to the original"""
    from mathutils import Vector
    M = root.matrix_world.inverted() @ ob.matrix_world
    zs = [(M @ Vector(c)).z for c in ob.bound_box]
    return (max(zs) - min(zs)) if zs else 0.0


# ---------------- the menus (pure descriptions, drawn by tool.draw_item) ----------------

def group_tree(P):
    """[(group, depth)] in outliner order"""
    out = []

    def walk(ids, depth):
        for iid in ids:
            g = P.items.get(iid)
            if g is not None and g.type == 'group':
                out.append((g, depth)); walk(g.children, depth + 1)
    walk(P.roots, 0)
    return out


def move_to_group_items(P, current=None):
    """the Move to Group submenu: the root level and every group, indented by depth, the current one marked"""
    items = [('op', 'hairmesh.move_to_group', '(root level)' + ('  ●' if current is None else ''), 'OUTLINER_COLLECTION', {'group': -1})]
    for g, depth in group_tree(P):
        items.append(('op', 'hairmesh.move_to_group', '    ' * depth + g.name + ('  ●' if g.id == current else ''), 'OUTLINER_COLLECTION', {'group': g.id}))
    return items


def where_in(P, g, via):
    """U7 A: where the walls lying directly in g go inside the mesh of `via` (groups.convUnit, mergeUnits 9987):
    ('merged', None) with the rest, ('apart', None) a piece of its own, ('with', X) in the piece of the sub-group X"""
    unit = G.convUnit(P, g, via)
    return ('merged', None) if unit is None else ('apart', None) if unit is g else ('with', unit)


def container_above(P, g, via):
    """whether a group from `via` down to g's parent is a Container (else g's "apart" comes from g being one)"""
    p = P.parentOf(g)
    while p is not None:
        if p.labels and p.labels.get('container'):
            return True
        if p is via:
            break
        p = P.parentOf(p)
    return False


def plan_of(P):
    """convert.conversionPlan for a drawing: None instead of an exception when the project is inconsistent (a
    child id with no item) — the Conversion tab must still draw"""
    try:
        return CV.conversionPlan(P)
    except (KeyError, AttributeError) as e:
        print('HairMesh conversion plan:', repr(e))
        return None


def pieces_text(P, g):
    """U7 A: what the group that makes a mesh says about the sub-groups that come with it (convert.meshPieces: the
    units mergeUnits makes) — '' when none comes with walls"""
    try:
        pieces, carried = CV.meshPieces(P, g)
    except (KeyError, AttributeError):
        return ''
    if not carried:
        return ''
    if pieces == 1:
        return '  · sub-groups merged'
    return '  · sub-groups apart' if g.labels and g.labels.get('container') else f'  · {pieces} pieces'


def tick_item(P, g, state='ticked', via=None, inside=0, makes=True):
    """one group's checkbox of the Conversion list (renderConvList, 9030): its name, its wall count and, since U4
    (15/10/2026), its own loop step — "≠" when it differs from the project's. Part 2 of the second manual test of
    21/10/2026 (ours): a group included through a ticked group above (convTree 'via': the sliders edit it, Convert
    puts it in that group's mesh) shows its box ticked and dimmed with "(via <group>)" — it cannot be left out while
    that group is ticked; a ticked group under a ticked one says "(own mesh)", as the original converts it on its
    own (collect, 9133). `inside`: the groups ticked inside it while it is drawn closed (the row says so).
    U7 A (ours), short enough for a row (Blender cuts the end of a long one): the group that makes a mesh says once
    what happens to the sub-groups that come with it (pieces_text: "· sub-groups merged", "· sub-groups apart" for a
    Container, "· N pieces" when a Container further down splits them), an included Container says its sub-groups
    come apart, an included group that a Container above puts into another sub-group's piece says "(via X, with Y)",
    and a ticked group whose collect finds nothing of its own (`makes` False: every wall inside is in a ticked
    sub-group, or there is none) says "· no mesh"."""
    parts, op = tick_parts(P, g, state, via, inside, makes)
    st = G.convShown(P, g)['step']
    text = '  '.join([parts[0], f'{parts[1]} · step {step_text(st)}' + ('  ≠' if G.stepOff(P, g) else ''), *parts[2:]])
    return (op[0], op[1], text, *op[3:])


def step_text(st):
    """a loop step as the lists show it: three significant digits, a whole number from 1000 on (never 1.55e+03:
    the review of part 2 of the lot ux7)"""
    return f'{st:.0f}' if abs(st) >= 1000 else f'{st:.3g}'


def wall_counts(P):
    """{group id: the walls inside it, its sub-groups' too}: one pass over the walls, for a list of groups"""
    out = {}
    for s in P.surfaces():
        g = P.parentOf(s)
        while g is not None:
            out[g.id] = out.get(g.id, 0) + 1
            g = P.parentOf(g)
    return out


def tick_parts(P, g, state='ticked', via=None, inside=0, makes=True, walls=None):
    """tick_item's text as parts — the name, the walls, then what is said about the group (the Conversion tree of
    the HairMesh Menu wraps them by width and shows the step in a column of its own: part 2 of the lot ux7) — and the
    item with an empty text"""
    n = (wall_counts(P) if walls is None else walls).get(g.id, 0)
    parts = [g.name, f'({n} wall{"" if n == 1 else "s"})'] + ([f'· {inside} ticked inside'] if inside else [])
    if state == 'via':
        kind, unit = where_in(P, g, via)
        parts.append(f'(via {via.name}' + (f', with {unit.name}' if kind == 'with' else '') + ')')
        if g.labels and g.labels.get('container') and pieces_text(P, g):
            parts.append('· sub-groups apart')
        return parts, ('op', 'hairmesh.tick_group', '', 'CHECKBOX_HLT', {'group': g.id, 'toggle': True}, {'active': False})
    if state == 'ticked' and not makes:
        parts.append('· no mesh')                                   # (ticked, but every wall inside is in a sub-group ticked on its own, or there is none)
    elif state == 'ticked':
        if via is not None:
            parts.append('(own mesh)')
        if pieces_text(P, g):
            parts.append(pieces_text(P, g).strip())
    return parts, ('op', 'hairmesh.tick_group', '', 'CHECKBOX_HLT' if state == 'ticked' else 'CHECKBOX_DEHLT', {'group': g.id, 'toggle': True})


def tick_items(P):
    """the Conversion list's checkboxes, one per hair group in outliner order (convTree), flat"""
    makes = {g.id for g, _n, _k in (plan_of(P) or [])}
    return [tick_item(P, g, state, via, makes=g.id in makes) for g, _d, state, via in G.convTree(P)] or [('label', 'No groups', 'INFO')]


class HAIRMESH_OT_apply_symmetry(bpy.types.Operator):
    """Turn the mirrored copy into real, editable curves and walls: a group "<name> mirror" right after this one; the group's Symmetry goes off"""
    bl_idname = 'hairmesh.apply_symmetry'
    bl_label = 'Apply Symmetry'
    bl_options = {'REGISTER', 'UNDO'}

    # 4.10 (D4.10): applySymmetry (9858), the group panel's "Apply symmetry" (8689: disabled while the symmetry is off)
    @classmethod
    def poll(cls, context):
        if not (owner.visible(context) and session.find_root(context) is not None):
            return False
        _r, P, sel = select.current(context)
        g = select.selected_group(P, sel) if P is not None else None
        if g is None:
            cls.poll_message_set(NO_GROUP)
            return False
        if not G.symInfo(P, g)[0]:
            cls.poll_message_set("Turn the group's Symmetry on first (the Symmetry block): Apply makes the mirrored half real")
            return False
        return True

    def execute(self, context):
        from ..core import scene as SC, symmetry as SYM
        root, P, sel = select.current(context)
        g = select.selected_group(P, sel)
        SC.settle(P)                                                # (the frame first: a disc's hits, as between two clicks)
        mg, msg = SYM.applySymmetry(P, g)
        if mg is None:
            return {'CANCELLED'}
        session.save_project(root, P)
        live.touch(root, [c.id for c in P.curves()])                # (curves().forEach(c => c.dirty = true), 9898)
        live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


_CLASSES = (HAIRMESH_OT_apply_symmetry, HAIRMESH_OT_add_group, HAIRMESH_OT_select_group, HAIRMESH_OT_delete_group, HAIRMESH_OT_move_to_group, HAIRMESH_OT_parent_to_last, HAIRMESH_OT_unparent,
            HAIRMESH_OT_match_project_step, HAIRMESH_OT_detach_group, HAIRMESH_OT_tick_group, HAIRMESH_OT_tick_all, HAIRMESH_OT_tick_top, HAIRMESH_OT_clear_group_color,
            HAIRMESH_OT_step_from_object)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.UI_MT_button_context_menu.append(swatch_menu)


def unregister():
    bpy.types.UI_MT_button_context_menu.remove(swatch_menu)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
