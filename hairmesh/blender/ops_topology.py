# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Lot 4.4 (docs/fase-4-plano.md, step 4.4): the D's Modifiers tab (key 6) and its Topology page — Cap, Fill holes and
Density, items of a group that shape the mesh of its locks (the original's modifierPanel 6915: capPanel 7244,
fillPanel 9505, tipPanel 7298) — and the sheets of a selected Cap (capProps 7341), Fill holes (skinProps 9490) and
Density (tipProps 7333). The core is core/topology.py; the previews (D4.5: the cap's fill and bevel rings, the
Fill's walls to be) go into their group's live object (live.topo_previews).

The settings for new items (the original's S.capDefaults, S.skinDefaults, S.tipDefaults) live on the Scene; the
sheets' controls on the WindowManager are views on the selected item, its rebuild and undo step when the slider stops
(deferred.py), like the crown's. The Density brush is step 4.8: nothing here paints."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup

from ..core import curves as CM, groups as G, topology as TP
from ..core.density import TIP_DEFAULTS
from ..core.model import CAP_DEFAULTS
from . import deferred, live, owner, select, session
from .coords import f32, field_same

CAP_MODES = (('QUAD', 'Quads', 'A grid of quads (+1 triangle on an odd loop)', 0),
             ('POINT', 'Collapse to a Point', 'A fan of triangles to the center', 1),
             ('TRIS', 'Fewest Triangles', 'n − 2 triangles, no new vertices', 2))
TOPO_TABS = (('CAP', 'Cap', 'Close the open ends of the locks of a group. Blue is the preview itself, selected or not: selecting it only makes it stronger', 'MESH_CAPSULE', 0),
             ('FILL', 'Fill', 'Fill holes: walls over the gaps between the curves of a group. Teal is the preview itself, selected or not: selecting it only makes it stronger', 'MOD_SKIN', 1),
             ('DENSITY', 'Density', 'Density: more or fewer loops where it is painted, on the locks of a group', 'MOD_DECIM', 2))
CAP_TEXT = '\n'.join(('Close the holes at the ends of the closed locks (a ring of walls: Volume Lock, or two curves with a second surface) in a group and its sub-groups.',
                      'Seen on the walls now, built into the mesh on conversion.',
                      'The eye switches it off.'))                                                     # (7246)
FILL_TEXT = '\n'.join(('Fill Holes: walls over the gaps between the curves of a group that no wall covers.',
                       'Closed outlines of welded curves, and (option) between open edge curves. Not inside closed locks, not the lock ends.',
                       'Preview first, then Convert to Walls on its sheet.',
                       'The eye switches it off.'))                                                    # (9507)
DENSITY_TEXT = '\n'.join(('Density: more (or fewer) loops only where it is painted, on the locks of a group and its sub-groups.',
                          'Add it to a group, set the density, paint with the Density Brush.',
                          'The eye switches it off.'))                                                 # (7300)
NOT_PAINTED = 'Nothing painted yet — it does nothing. Paint with the Density Brush.'   # (7337)
TIP_VIZ = 'The painted mask is shown on the curves: dark — weak, yellow — full.'   # (7304)


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def _last(P, sel, type_):
    """the last selected item of this type (the sheet shows the last selected item, renderProps 8627)"""
    for iid in reversed(sel.items):
        it = P.items.get(iid)
        if it is not None and it.type == type_:
            return it
    return None


def selected_topo(context, type_):
    root, P, sel = select.current(context)
    return root, P, (_last(P, sel, type_) if P is not None else None)


# ---------------- the settings for new items (S.capDefaults, S.skinDefaults, S.tipDefaults) ----------------

class HairMeshTopoDefaults(PropertyGroup):
    """The Topology tab's settings for the items Add makes (the original keeps them for the session: S.capDefaults,
    7219; S.skinDefaults, 9508; S.tipDefaults, 7269), on the Scene; and the tab shown (S.modGroupTab)."""
    topo_tab: EnumProperty(name='Topology', items=TOPO_TABS, default='CAP')
    cap_def_mode: EnumProperty(name='Fill', description='How the cap fills the hole: quads, a point or the fewest triangles', items=CAP_MODES, default='QUAD')
    cap_def_start: BoolProperty(name='Start (Root)', description="Close the start of every lock (the curves' first anchors)", default=True)
    cap_def_end: BoolProperty(name='End (If Open)', description='Close the end too when it is open (a pointed tip needs no cap)', default=True)
    cap_def_size: FloatProperty(name='Size', description="How far the cap is pushed out of the lock's end and in towards its middle — new rings, the lock's own last row stays (0: a flat cap)", default=0.0, min=0.0, soft_max=10000.0)   # (a field: the soft_max only bounds a drag — 100 stopped it short on a big head)
    cap_def_segs: IntProperty(name='Segments', description='Rings across the bevel', default=2, min=1, max=8)
    cap_def_round: FloatProperty(name='Roundness', description='0%: a straight chamfer, 100%: a round arc', default=1.0, min=0.0, max=1.0, subtype='FACTOR')
    skin_def_open: BoolProperty(name='Also Between Open Curves', description='Edge curves of the group (used by at most one wall) get a wall to their nearest neighbor — as when two curves are picked to create a wall', default=False)
    tip_def_dens: FloatProperty(name='Density', description='Loop density where the mask is painted: ×2 twice as many loops, ×0.5 half as many (in between: by the painted strength)', default=1.5, min=0.25, max=4.0)


def cap_defaults(context):
    d = context.scene.hairmesh_topo
    return {'mode': d.cap_def_mode.lower(), 'start': bool(d.cap_def_start), 'end': bool(d.cap_def_end), 'size': f32(d.cap_def_size),
            'segs': int(d.cap_def_segs), 'round': f32(d.cap_def_round)}


def skin_defaults(context):
    return {'open': bool(context.scene.hairmesh_topo.skin_def_open)}


def tip_defaults(context):
    return {**TIP_DEFAULTS, 'dens': f32(context.scene.hairmesh_topo.tip_def_dens)}


# ---------------- the sheets' controls: views on the selected item ----------------

def _cap():
    return selected_topo(bpy.context, 'cap')


def _skin():
    return selected_topo(bpy.context, 'skin')


def _tip():
    return selected_topo(bpy.context, 'tip')


def _setter(get_item, key, conv=lambda v: v, rebuild=True, touch=False):
    def setter(self, v):
        root, P, k = get_item()
        if k is None:
            return
        v = conv(v)
        if field_same(k.p.get(key, (CAP_DEFAULTS if k.type == 'cap' else TIP_DEFAULTS if k.type == 'tip' else {}).get(key)), v):
            return                                                  # (the same value: nothing)
        k.p = {**k.p, key: v}
        if touch:                                                   # (a Density: the locks of its group read it, lockTip)
            g = P.items.get(k.parent)
            if g is not None and g.type == 'group':
                live.touch(root, [c.id for c in G.curvesIn(P, g)])
        deferred.changed(bpy.context, root, rebuild)
    return setter


def _getter(get_item, key, default, conv=lambda v: v):
    def getter(self):
        _r, _P, k = get_item()
        return conv(k.p.get(key, default)) if k is not None else conv(default)
    return getter


_MODE_IDX = {'quad': 0, 'point': 1, 'tris': 2}
_IDX_MODE = {0: 'quad', 1: 'point', 2: 'tris'}


class HairMeshTopoItem(PropertyGroup):
    """The sheet of a selected Cap, Fill holes or Density (capProps 7341, skinProps 9490, tipProps 7333): on the
    WindowManager, views on the model; the rebuild and the undo step when the slider stops or is let go."""
    cap_mode: EnumProperty(name='Fill', description='How the cap fills the hole: quads, a point or the fewest triangles', items=CAP_MODES,
                           get=_getter(_cap, 'mode', 'quad', lambda v: _MODE_IDX.get(v, 0)), set=_setter(_cap, 'mode', lambda v: _IDX_MODE[v]))
    cap_start: BoolProperty(name='Start (Root)', description="Close the start of every lock (the curves' first anchors)",
                            get=_getter(_cap, 'start', True, lambda v: v is not False), set=_setter(_cap, 'start', bool))
    cap_end: BoolProperty(name='End (If Open)', description='Close the end too when it is open (a pointed tip needs no cap)',
                          get=_getter(_cap, 'end', True, lambda v: v is not False), set=_setter(_cap, 'end', bool))
    cap_size: FloatProperty(name='Size', description="How far the cap is pushed out of the lock's end and in towards its middle (0: a flat cap)", min=0.0, soft_max=10000.0,
                            get=_getter(_cap, 'size', 0, float), set=_setter(_cap, 'size', f32))
    cap_segs: IntProperty(name='Segments', description='Rings across the bevel', min=1, max=8,
                          get=_getter(_cap, 'segs', 2, int), set=_setter(_cap, 'segs', int))
    cap_round: FloatProperty(name='Roundness', description='0%: a straight chamfer, 100%: a round arc', min=0.0, max=1.0, subtype='FACTOR',
                             get=_getter(_cap, 'round', 1, float), set=_setter(_cap, 'round', f32))
    skin_open: BoolProperty(name='Also Between Open Curves', description='Edge curves of the group (used by at most one wall) get a wall to their nearest neighbor',
                            get=_getter(_skin, 'open', False, bool), set=_setter(_skin, 'open', bool))
    skin_bulge: FloatProperty(name='Bulge', description='Bulge of the walls Convert makes', min=-1.0, max=1.0, subtype='FACTOR',   # (a fraction: a slider, as the wall's Bulge)
                              get=_getter(_skin, 'bulge', 0, float), set=_setter(_skin, 'bulge', f32, rebuild=False))
    tip_dens: FloatProperty(name='Density', description='Loop density where the mask is painted: ×2 twice as many loops, ×0.5 half as many', min=0.25, max=4.0,
                            get=_getter(_tip, 'dens', 1.5, float), set=_setter(_tip, 'dens', f32, touch=True))


TOPO_ITEM_ATTRS = frozenset(HairMeshTopoItem.__annotations__)
TOPO_DEF_ATTRS = frozenset(HairMeshTopoDefaults.__annotations__)


# ---------------- operators ----------------

def _finish(context, root, P, sel, report, msg, touch=None):
    select.store(context.scene.hairmesh, sel)
    G.panelsRendered(P, sel.items[len(sel.items) - 1] if sel.items else None)   # onSelChange
    CM.rebuild_dirty(P)
    session.save_project(root, P)
    if touch:
        live.touch(root, touch)
    live.update(context, root)
    select.redraw(context)
    if msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def _targets_poll(cls, context, kind=None):
    if not _poll(context):
        return False
    _r, P, sel = select.current(context)
    gs = TP.capTargets(P, sel.items) if P is not None else []
    if not gs:
        cls.poll_message_set(TP.NO_TARGET)
        return False
    if kind == 'skin' and all(any(getattr(P.items.get(i), 'type', None) == 'skin' for i in g.children) for g in gs):
        cls.poll_message_set(TP.FILL_EXISTS)
        return False
    return True


class HAIRMESH_OT_add_caps(bpy.types.Operator):
    """Add a Cap to the groups of the selection (a group, or anything in it) with the settings above: it closes the open ends of their closed locks (a group that has one gets these settings). Blue is the preview itself, selected or not: selecting it only makes it stronger"""
    bl_idname = 'hairmesh.add_caps'
    bl_label = 'Add Cap'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _targets_poll(cls, context)

    def execute(self, context):
        root, P, sel = select.current(context)
        made, msg = TP.addCaps(P, sel.items, cap_defaults(context))
        if made is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        sel.items = [k.id for k in made]; sel.anchors = []
        return _finish(context, root, P, sel, self.report, msg)


class HAIRMESH_OT_add_skin(bpy.types.Operator):
    """Add Fill holes to the groups of the selection (a group, or anything in it): the gaps between their curves that no wall covers show as walls to be; Convert on its sheet makes them walls. Teal is the preview itself, selected or not: selecting it only makes it stronger"""
    bl_idname = 'hairmesh.add_skin'
    bl_label = 'Add Fill Holes'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _targets_poll(cls, context, 'skin')

    def execute(self, context):
        root, P, sel = select.current(context)
        made, msg = TP.addSkin(P, sel.items, skin_defaults(context))
        if made is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        sel.items = [k.id for k in made]; sel.anchors = []
        return _finish(context, root, P, sel, self.report, msg)


class HAIRMESH_OT_add_tips(bpy.types.Operator):
    """Add a Density to the groups of the selection (a group, or anything in it) with the density above (a group that has one gets it, its mask stays): more or fewer loops where its mask is painted"""
    bl_idname = 'hairmesh.add_tips'
    bl_label = 'Add Density'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _targets_poll(cls, context)

    def execute(self, context):
        root, P, sel = select.current(context)
        gs = TP.capTargets(P, sel.items)
        made, msg = TP.addTips(P, sel.items, tip_defaults(context))
        if made is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        touch = [c.id for g in gs for c in G.curvesIn(P, g)]        # (S.surfDirty: the locks of the groups read it, lockTip)
        sel.items = [k.id for k in made]; sel.anchors = []
        res = _finish(context, root, P, sel, self.report, msg, touch=touch)
        start_tip_brush(context)                                    # (7324: S.tipBrush.on = true — 4.8)
        return res


def start_tip_brush(context):
    """the Density brush on (tipBrushFor 7316); outside a 3D View (or with it on already) nothing — its button stays"""
    from . import brush as BRU
    if BRU.running('TIP') is not None or context.area is None or context.area.type != 'VIEW_3D' or bpy.app.background:
        return
    try:
        bpy.ops.hairmesh.brush('INVOKE_DEFAULT', kind='TIP')
    except RuntimeError:
        pass


def _tip_targets(context):
    """the Density items the brush and Clear work on (tipPanel 7304, tipProps 7337): the selected ones, else all"""
    _r, P, sel = select.current(context)
    if P is None:
        return []
    ks = [P.items[i] for i in sel.items if i in P.items and P.items[i].type == 'tip']
    return ks or [it for it in P.items.values() if it.type == 'tip']


class HAIRMESH_OT_clear_tip_mask(bpy.types.Operator):
    """Remove the painted mask: the Density does nothing until painted again"""
    bl_idname = 'hairmesh.clear_tip_mask'
    bl_label = 'Clear Mask'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        if not [k for k in _tip_targets(context) if k.mask]:
            cls.poll_message_set('No mask to clear')                # (7310)
            return False
        return True

    def execute(self, context):
        root, P, sel = select.current(context)
        ks = [k for k in _tip_targets(context) if k.mask]           # (clearTipMask 7309)
        touch = []
        for k in ks:
            k.mask = None
            g = P.items.get(k.parent)
            if g is not None:
                touch += [c.id for c in G.curvesIn(P, g)]
        return _finish(context, root, P, sel, self.report, f'{len(ks)} masks cleared' if len(ks) > 1 else 'Mask cleared', touch=touch)


def tip_brush_rows(context, ks):
    """tipBrushControls (7280): the Density brush on / off and Clear mask(s); with it on, its Strength, Radius,
    Hardness (ours: the original's hardness is the anchor brush's slider)"""
    from . import brush as BRU
    on = BRU.running('TIP') is not None
    rows = [[('op', 'hairmesh.brush', 'Density Brush', 'BRUSH_DATA', {'kind': 'TIP'}, {'depress': on}),
             ('op', 'hairmesh.clear_tip_mask', 'Clear Mask' if len(ks) == 1 else 'Clear All Masks', 'X', {})]]
    if on:
        rows += [[('prop', 'brush_tip_strength', 'Strength')], [('prop', 'brush_radius', 'Radius'), ('prop', 'brush_hardness', 'Hardness')]]
    return rows


class HAIRMESH_OT_convert_skin(bpy.types.Operator):
    """Turn the previewed walls of this Fill holes into walls: a gap between two open curves, a wall between them; a hole, two junction curves along its outline and a wall between them"""
    bl_idname = 'hairmesh.convert_skin'
    bl_label = 'Convert to Walls'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        root, P, k = selected_topo(context, 'skin')
        if k is None:
            cls.poll_message_set('Select a Fill holes (in the Outliner, O, or a click on its walls to be in the viewport)')
            return False
        n = live.topo_found(root, k)
        if n is not None and not n:
            cls.poll_message_set(TP.NOTHING_TO_FILL)
            return False
        return True                                                 # (no preview yet: it acts — and says so when there is nothing)

    def execute(self, context):
        root, P, k = selected_topo(context, 'skin')
        CM.rebuild_dirty(P)
        before = set(P.items)
        made, msg = TP.convertSkin(P, k, live.row_tables(root, P).T)   # (the live tables: the same as a full build)
        if made is None:
            self.report({'INFO'}, msg or TP.NOTHING_TO_FILL)
            return {'CANCELLED'}
        new_curves = [i for i in P.items if i not in before and P.items[i].type == 'curve']
        touch = list(dict.fromkeys([*new_curves, *(c for s in made for c in (s.a, s.b))]))
        _r, _P, sel = select.current(context)
        sel.items = [s.id for s in made]; sel.anchors = []
        return _finish(context, root, P, sel, self.report, msg, touch=touch)


class HAIRMESH_OT_set_topo_tab(bpy.types.Operator):
    """Show this part of the Topology tab"""
    bl_idname = 'hairmesh.set_topo_tab'
    bl_label = 'Topology Tab'
    bl_options = {'INTERNAL'}

    tab: EnumProperty(items=[(k, lab, tip, icon, n) for k, lab, tip, icon, n in TOPO_TABS], options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return next((t[2] for t in TOPO_TABS if t[0] == properties.tab), cls.__doc__)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        context.scene.hairmesh_topo.topo_tab = self.tab
        select.redraw(context)
        return {'FINISHED'}


# ---------------- the D and the N: rows ----------------

def _add_label(what, gs, room=None):
    """the Add button's text (capPanel 7247): the target groups — the group's name only while it fits the row whole"""
    from . import fit
    from .tool import ROOM
    label = f'Add {what} to {len(gs)} Groups' if len(gs) > 1 else f'Add {what} to {gs[0].name}' if gs else f'Add {what}'
    return label if fit.button_need(label, True) <= (room or ROOM) else f'Add {what}'


def modifier_tool_rows(context, room, sheet=False, label=True):
    """The Topology page of the Modifiers tab (modifierPanel 6915: Cap, Fill, Density), each part with its settings for
    new items and its Add button — in a closed block while the selected item's sheet follows (the D within 18 rows); its
    text (CAP_TEXT, FILL_TEXT, DENSITY_TEXT) is the "?" of the D's tab column (item 9 of the lot after 4.8:
    tool.how_to). `label`: the tab's title row (4.6 draws the tabs above instead)."""
    from .tool import block, text, why_item
    d = context.scene.hairmesh_topo
    _r, P, sel = select.current(context)
    gs = TP.capTargets(P, sel.items) if P is not None else []
    rows = [[('label', 'Topology', 'MESH_GRID')]] if label else []
    rows.append([('op', 'hairmesh.set_topo_tab', lab, icon, {'tab': k}, {'depress': d.topo_tab == k}) for k, lab, _t, icon, _n in TOPO_TABS])
    if d.topo_tab == 'CAP':
        what = 'Cap'
        new = [[('prop', 'cap_def_mode', 'Fill')], [('prop', 'cap_def_start', 'Start (Root)'), ('prop', 'cap_def_end', 'End (If Open)')],
               [('prop', 'cap_def_size', 'Size'), ('prop', 'cap_def_segs', 'Segments', {'active': d.cap_def_size > 0}), *([why_item(FLAT_CAP)] if d.cap_def_size <= 0 else [])],
               [('prop', 'cap_def_round', 'Roundness', {'active': d.cap_def_size > 0})]]
        new.append([('op', 'hairmesh.add_caps', _add_label('Cap', gs), 'ADD', {})])
    elif d.topo_tab == 'FILL':
        what = 'Fill Holes'
        new = [[('prop', 'skin_def_open', 'Also Between Open Curves')], [('op', 'hairmesh.add_skin', _add_label('Fill Holes', gs), 'ADD', {})]]
    else:
        what = 'Density'
        new = [[('prop', 'tip_def_dens', 'Density')], [('op', 'hairmesh.add_tips', _add_label('Density', gs), 'ADD', {})]]
        if P is not None and P.of_type('tip') and not sheet:        # (tipPanel 7302-7305: the mask's colours, the brush)
            new += [[text(TIP_VIZ, 'BLANK1')], *tip_brush_rows(context, _tip_targets(context))]
    if sheet:
        rows.append(block(f'topo_new_{d.topo_tab.lower()}', f'New {what}', 'ADD', new, False))
    else:
        rows += new
    return rows


FLAT_CAP = 'Size 0: a flat cap. Segments and Roundness shape the bevel only.'


def cap_rows(context, P, k, room):
    """the sheet of a Cap (capProps 7341): what it closes, its controls (dimmed when it closes nothing, or without a
    bevel for Segments / Roundness), the rule"""
    from .tool import group_label, label_and, text, why_item
    from ..core import effects as EF
    root = session.find_root(context)
    state = EF.capState(k, live.topo_found(root, k) if root is not None else None)
    acts = state == 'ok'; ends = state in ('ok', 'no_ends')
    bevel = acts and EF.capBevelActs(k.p)
    rows = [[('label', k.name, 'MESH_CAPSULE')], *label_and(group_label(P, k), ('op', 'hairmesh.delete_selected', 'Delete Cap', 'X', {'keep_shape': False}), room)]
    why = {'hidden': 'Hidden (its eye in the Outliner, O): it closes nothing until shown.',
           'no_ends': 'Start and End both off: it closes nothing — tick one.',
           'no_lock': 'It closes nothing now: no closed lock in its group (a ring of walls — Volume Lock, or two curves with a second surface).'}.get(state)
    if why:
        rows.append([text(why, 'ERROR')])
    on = {'active': acts}
    bevel_row = [('prop', 'cap_size', 'Size', on), ('prop', 'cap_segs', 'Segments', {'active': bevel})]
    rows += [[('prop', 'cap_mode', 'Fill', on)], [('prop', 'cap_start', 'Start (Root)', {'active': ends}), ('prop', 'cap_end', 'End (If Open)', {'active': ends})],
             bevel_row + ([why_item(FLAT_CAP)] if acts and not bevel else []), [('prop', 'cap_round', 'Roundness', {'active': bevel})]]
    return rows


def skin_rows(context, P, k, room):
    """the sheet of a Fill holes (skinProps 9490): Also Between Open Curves, Bulge, what it found, Convert"""
    from .tool import group_label, label_and, text
    root = session.find_root(context)
    holes, pairs = live.topo_counts(root, k) if root is not None else (None, None)
    found = 'not computed yet' if holes is None else f'{holes} hole(s), {pairs} gap(s) between open curves'
    return [[('label', k.name, 'MOD_SKIN')], *label_and(group_label(P, k), ('op', 'hairmesh.delete_selected', 'Delete Fill Holes', 'X', {'keep_shape': False}), room),
            [('prop', 'skin_open', 'Also Between Open Curves')], [('prop', 'skin_bulge', 'Bulge')],
            [text(f'Found: {found}.', 'BLANK1')],
            [('op', 'hairmesh.convert_skin', 'Convert to Walls', 'OUTLINER_OB_SURFACE', {})]]


def tip_rows(context, P, k, room):
    """the sheet of a Density (tipProps 7333): what it shapes, the density (dimmed while nothing is painted)"""
    from .tool import group_label, label_and, text
    from ..core import effects as EF
    painted = EF.tipActs(k)
    rows = [[('label', k.name, 'MOD_DECIM')], *label_and(group_label(P, k), ('op', 'hairmesh.delete_selected', 'Delete Density', 'X', {'keep_shape': False}), room),
            [('prop', 'tip_dens', 'Density', {'active': painted})]]
    if not painted:
        rows.append([text(NOT_PAINTED if k.visible else 'Hidden (its eye in the Outliner, O): it shapes no loop until shown.', 'ERROR')])
    rows += tip_brush_rows(context, [k])                            # (tipProps 7338)
    return rows


def topo_pie_items(context, P, k):
    """the Shift+RMB pie with a Cap, a Fill holes or a Density the last selected item: Delete, its sliders in the N box,
    the Fill's Convert, the Density's brush and Clear Mask, the HairMesh Menu"""
    from .tool import MENU_OP, gizmo_slot, pie_box
    E = ('empty',)
    from ..core import effects as EF
    east = west = E
    if k.type == 'cap':
        root = session.find_root(context)
        acts = EF.capState(k, live.topo_found(root, k) if root is not None else None) == 'ok'
        bevel = acts and EF.capBevelActs(k.p)
        box = pie_box(('prop', 'cap_size', 'Size', {'active': acts}), ('prop', 'cap_segs', 'Segments', {'active': bevel}), ('prop', 'cap_round', 'Roundness', {'active': bevel}))
        what = 'Delete Cap'
    elif k.type == 'skin':
        box = pie_box(('prop', 'skin_bulge', 'Bulge'))
        east = ('op', 'hairmesh.convert_skin', 'Convert to Walls', 'OUTLINER_OB_SURFACE', {})
        what = 'Delete Fill Holes'
    else:
        box = pie_box(('prop', 'tip_dens', 'Density', {'active': EF.tipActs(k)}))
        east = ('op', 'hairmesh.brush', 'Density Brush', 'BRUSH_DATA', {'kind': 'TIP'})
        west = ('op', 'hairmesh.clear_tip_mask', 'Clear Mask', 'X', {}) if EF.tipActs(k) else E
        what = 'Delete Density'
    return [E, east, ('op', 'hairmesh.delete_selected', what, 'X', {'keep_shape': False}), box, E, MENU_OP, west, gizmo_slot(context, P)]


def topo_sheet(context, P, it, room):
    """the sheet of a Cap / Fill holes / Density, or None for another item"""
    return {'cap': cap_rows, 'skin': skin_rows, 'tip': tip_rows}.get(getattr(it, 'type', None), lambda *_a: None)(context, P, it, room)


CLASSES = (HairMeshTopoDefaults, HairMeshTopoItem, HAIRMESH_OT_add_caps, HAIRMESH_OT_add_skin, HAIRMESH_OT_add_tips, HAIRMESH_OT_clear_tip_mask, HAIRMESH_OT_convert_skin,
           HAIRMESH_OT_set_topo_tab)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.hairmesh_topo = PointerProperty(type=HairMeshTopoDefaults)
    bpy.types.WindowManager.hairmesh_topo = PointerProperty(type=HairMeshTopoItem)


def unregister():
    del bpy.types.WindowManager.hairmesh_topo
    del bpy.types.Scene.hairmesh_topo
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
