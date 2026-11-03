# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Phase 3.5 inside Blender: walls and locks between curves (the original's curve pie, surfaceMenuItems 6157, and
its surface panel, 8732-8822) as operators, and the menus every action lives in (rule of 14/10/2026: everything
reachable from the pies and the HairMesh Menu; the N panel is a complement).

  hairmesh.create_walls (closed, exact)  "Create Wall" / "Walls between N curves (open)" / "Wall
                                        between the two trunks (facing branches)" / "...exactly these" / "Volume
                                        lock (closed)": createSurfaces over the selected curves, in selection order
  hairmesh.fill_hole                    "Fill the hole between these curves" (fillHole)
  hairmesh.average_curve                "Create average curve" (exactly 2 selected curves)
  hairmesh.segments_to_curves           "Segments -> curves" of the selected walls with width segments
  hairmesh.select_wall (wall, extend)   selects a wall (the buttons of the Curve panel's "Drives:" list and the
                                        Walls menu; a click on a live wall in the viewport does the same, ops_select)
  hairmesh.select_wall_curves (which)   the surface panel's "Select" / "Select both curves"
The surface panel's sliders and checkboxes are properties of Scene.hairmesh (props.py: wall_*), views on the last
selected wall. A wall is an item of the selection like a curve (S.sel); Backspace deletes it (hairmesh.delete_selected).

Every operator has a form that needs no viewport (the selection), ends with session.save_project (one undo step) and
live.update (the preview rebuilt inside the same undo step)."""
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from ..core import curves as CM, effects as E, walls as WL
from ..core.selection import Selection
from ..core.surfaces import hasTwin, wallCrownRoot
from . import live, owner, select, session, wording


def _finish(context, root, P, sel=None, report=None, msg=None, full=False):
    if sel is not None:
        select.store(context.scene.hairmesh, sel)
    session.save_project(root, P)                                   # the original's commit(): one undo step
    if full:
        live.invalidate_rows(root)                                  # S.surfDirty: the row tables are built again
    live.update(context, root)                                      # the preview inside the operator's own undo step
    select.redraw(context)
    if report is not None and msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


class HAIRMESH_OT_create_walls(bpy.types.Operator):
    """Create walls between the selected curves, in selection order (two trunks: between their facing branches); closed = also last to first (a volume lock); exact = between exactly these curves"""
    bl_idname = 'hairmesh.create_walls'
    bl_label = 'Create HairMesh Walls'
    bl_options = {'REGISTER', 'UNDO'}

    closed: BoolProperty(name='Closed', description='Also a wall from the last curve back to the first: a volume lock (3+ curves)', default=False)
    exact: BoolProperty(name='Exactly These Curves', description='Between the selected curves themselves, even when two trunks would resolve to their facing branches', default=False)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        cs = select.curves_of(P, sel)
        if len(cs) < 2:
            self.report({'INFO'}, 'Select two or more curves first')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        made, msg = WL.createSurfaces(P, cs, self.closed, self.exact)
        if not made:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        return _finish(context, root, P, Selection([s.id for s in made], []), self.report, msg or f'{len(made)} wall{"s" if len(made) > 1 else ""} created')


class HAIRMESH_OT_fill_hole(bpy.types.Operator):
    """Fill the hole bounded by the selected curves (welded at its corners): the shortest closed loop through them is split at two corners into two junction curves and a wall between them fills it (Bulge 0)"""
    bl_idname = 'hairmesh.fill_hole'
    bl_label = 'Fill HairMesh Hole'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        cs = select.curves_of(P, sel)
        CM.rebuild_dirty(P)
        s, corners, msg = WL.fillHole(P, cs)
        if s is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        return _finish(context, root, P, Selection([s.id], []), self.report,
                       f'Hole filled: {corners} corner{"" if corners == 1 else "s"}, a wall between two curves along its edges (Bulge 0 — set it in the surface)')


class HAIRMESH_OT_average_curve(bpy.types.Operator):
    """Add a curve halfway between the two selected curves (as many anchors as the denser one); ends they share stay shared"""
    bl_idname = 'hairmesh.average_curve'
    bl_label = 'Create HairMesh Average Curve'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        cs = select.curves_of(P, sel)
        if len(cs) != 2:
            self.report({'INFO'}, 'Select exactly two curves first')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        c = WL.averageCurve(P, cs[0], cs[1])
        if c is None:
            self.report({'INFO'}, 'The curves have no samples yet')
            return {'CANCELLED'}
        return _finish(context, root, P, Selection([c.id], []), self.report, 'Average curve created')


class HAIRMESH_OT_segments_to_curves(bpy.types.Operator):
    """Turn every width segment line of the selected walls (with its bulge) into a curve; each wall is replaced by flat one-segment walls between them (a second surface becomes a wall of its own)"""
    bl_idname = 'hairmesh.segments_to_curves'
    bl_label = 'HairMesh Segments to Curves'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        walls = select.walls_of(P, sel)
        CM.rebuild_dirty(P)
        mc, ms, msg = WL.segmentsToCurves(P, walls)
        msg = wording.core(msg)
        if msg:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        return _finish(context, root, P, Selection([c.id for c in mc], []), self.report,   # (the locks changed shape: those not in the cache are rebuilt by themselves)
                       f'{len(mc)} curve{"" if len(mc) == 1 else "s"} and {len(ms)} wall{"" if len(ms) == 1 else "s"} made from the segments')


class HAIRMESH_OT_select_wall(bpy.types.Operator):
    """Select this wall (its settings come up in the Wall panel and the HairMesh Menu); Shift adds it to the selection"""
    bl_idname = 'hairmesh.select_wall'
    bl_label = 'Select HairMesh Wall'
    bl_options = {'REGISTER', 'UNDO'}

    wall: IntProperty(name='Wall', description='The wall item id', default=-1)
    extend: BoolProperty(name='Extend', description='Add to the selection (toggle)', default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def invoke(self, context, event):
        self.extend = self.extend or event.shift
        return self.execute(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        it = P.items.get(self.wall)
        if it is None or it.type != 'surface':
            self.report({'ERROR'}, f'No wall {self.wall} in the project')
            return {'CANCELLED'}
        sel.select_item(it.id, self.extend)
        select.store(context.scene.hairmesh, sel); select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_select_wall_curves(bpy.types.Operator):
    """Select the driver curves of the last selected wall"""
    bl_idname = 'hairmesh.select_wall_curves'
    bl_label = 'Select HairMesh Wall Curves'
    bl_options = {'REGISTER', 'UNDO'}

    which: EnumProperty(name='Which', items=(('A', 'Curve A', 'The first driver curve'), ('B', 'Curve B', 'The second driver curve'), ('BOTH', 'Both', 'Both driver curves')), default='BOTH')

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        sf = select.last_wall(P, sel)
        if sf is None:
            self.report({'INFO'}, 'Select a wall first')
            return {'CANCELLED'}
        ids = [sf.a] if self.which == 'A' else [sf.b] if self.which == 'B' else [sf.a, sf.b]
        ids = [i for i in ids if i in P.items]                      # (deleted)
        if not ids:
            self.report({'INFO'}, 'The curve is gone')
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection(ids, [])); select.redraw(context)
        return {'FINISHED'}


# ---------------- the menus (pure descriptions, drawn by tool.draw_item) ----------------
CREATE = {'walls': ('hairmesh.create_walls', 'MOD_LATTICE', {'closed': False, 'exact': False}),
          'exact': ('hairmesh.create_walls', 'MOD_LATTICE', {'closed': False, 'exact': True}),
          'closed': ('hairmesh.create_walls', 'MESH_CYLINDER', {'closed': True, 'exact': True}),
          'fill': ('hairmesh.fill_hole', 'MESH_PLANE', {}),
          'average': ('hairmesh.average_curve', 'CURVE_DATA', {}),
          'merge': ('hairmesh.merge_coincident', 'AUTOMERGE_ON', {})}
SHORT = {'walls': 'Wall', 'exact': 'Exactly These', 'closed': 'Volume Lock', 'fill': 'Fill Hole', 'average': 'Average', 'merge': 'Merge'}   # (U6: the long texts are the original's, surfaceMenuItems 6157)


def curve_wall_items(P, cs, short=False):
    """surfaceMenuItems (6157) for 2+ selected curves as items; `short` = the texts of the pop-up's rows."""
    if len(cs) < 2:
        return []
    out = []
    for key, label in ((k, wording.core(t)) for k, t in WL.surfaceMenuChoices(P, cs)):
        idname, icon, props = CREATE[key]
        out.append(('op', idname, SHORT[key] if short else label, icon, dict(props)))
    return out


def drives_text(P, cs):
    """the Curve panel's "Drives:" (8730) as a line: the walls these curves drive, named (no Select buttons in the D:
    a click on a wall selects it, the "?" says so), or None"""
    names = [s.name for s in WL.wallsOfCurves(P, cs)]
    return f'Drives: {", ".join(names)}' if names else None


def wall_select_items(P, cs, short=False):
    """the walls driven by the selected curves (the Curve panel's "Drives:", 8730), one Select button each"""
    return [('op', 'hairmesh.select_wall', s.name if short else f'Select {s.name}', 'RESTRICT_SELECT_OFF', {'wall': s.id}) for s in WL.wallsOfCurves(P, cs)]


def walls_menu_items(context):
    """The Walls / Lock submenu (the SW slot of the curve and wall pies): with a wall selected its
    actions; with curves the wall items of the original's pie and the walls they drive; else a hint."""
    root, P, sel = select.current(context)
    if P is None:
        return [('label', 'No HairMesh project', 'INFO')]
    sf = select.last_wall(P, sel)
    if sf is not None and not sel.anchors:
        return wall_actions(context.scene.hairmesh, P, sf, short=False)['menu'] + ([('sep',), UNITE] if unite_offered(P, sel.items) else [])   # (4.10)
    cs = select.curves_of(P, sel)
    items = curve_wall_items(P, cs)
    if len([c for c in cs if not c.ring]) >= 2:
        # 4.2: the original's curve pie with 2+ curves ends with "Guide lines (drag across curves)" (openMainPie,
        # 7928: pickTool('guide')) — the Guides sub-tool in its Line mode, the curves still selected
        items.append(('op', 'hairmesh.set_guide_mode', 'Guide Line (Drag Across the Curves)', 'IPO_LINEAR', {'mode': 'LINE'}))
    if unite_offered(P, sel.items):
        items.append(UNITE)                                         # (4.10: the original's curve pie, 7927)
    walls = wall_select_items(P, cs)
    if items and walls:
        items.append(('sep',))
    items.extend(walls)
    if not items:
        items.append(('label', 'Select two or more curves' if cs else 'Select curves (2+) or click a wall', 'INFO'))
    return items


BULGE_HINT = 'Bulge only shapes the wall with Width Segments ≥ 1'
CLEAN_ICON = 'MOD_WIREFRAME'                                        # (G8: MOD_LATTICE is the wall's, MOD_DECIM the Density's)


def wall_actions(hm, P, sf, short=False):
    """The actions and settings of one wall (the surface panel, 8732-8822), as items: 'pie' (the 8 slots), 'blocks'
    (the sheet's blocks for the pop-up and the N panel: tool.wall_rows puts the identity rows above them), 'menu'
    (the submenu's list), 'delete' (the item of the identity row). Lot 4.3, item 1: the sheet has no selection
    buttons (its curves are selected from the pie, derived; Deselect All is a click in the empty space or Alt+A)."""
    ca, cb = WL.wallCurves(P, sf)
    na = ca.name if ca else '(deleted)'; nb = cb.name if cb else '(deleted)'
    segs = max(1, int(sf.p.get('segs') or 0)); bulge = sf.p.get('bulge') or 0
    sel_a = ('op', 'hairmesh.select_wall_curves', na if short else f'Select {na}', 'CURVE_DATA', {'which': 'A'})
    sel_b = ('op', 'hairmesh.select_wall_curves', nb if short else f'Select {nb}', 'CURVE_DATA', {'which': 'B'})
    both = ('op', 'hairmesh.select_wall_curves', 'Both' if short else 'Select Both Curves', 'RESTRICT_SELECT_OFF', {'which': 'BOTH'})
    delete = ('op', 'hairmesh.delete_selected', 'Delete' if short else 'Delete Wall', 'X', {'keep_shape': False})
    to_curves = ('op', 'hairmesh.segments_to_curves', 'Segs → Curves' if short else 'Segments to Curves', 'OUTLINER_OB_CURVE', {}) if segs > 1 else None
    label = ('label', sf.name, 'MOD_LATTICE')
    drivers = ('label', f'{na} ↔ {nb}', 'CURVE_DATA')
    # 1e of the second manual test of 21/10/2026: with one quad across (Width Segments 0) the bulge moves no vertex —
    # sin(pi s) at the two edges (surfaceData 2872; sin(pi) = 1.2e-16 in JS, below the last bit of the coordinates) —
    # and Segments -> Curves needs 2+ (3097); it still decides whether a Second Surface may exist (hasTwin 2945, the
    # checkbox 8798) and gives it its bulge when switched on (8801). So: dimmed (Blender's "no effect now", still
    # editable, or a wall made with Bulge 0 could never get a second surface) unless the second surface is on
    idle_bulge = segs == 1 and not sf.p.get('b2on')
    sliders = [('prop', 'wall_bulge', 'Bulge', {'active': False}) if idle_bulge else ('prop', 'wall_bulge', 'Bulge'), ('prop', 'wall_segs', 'Width Segments')]
    if segs > 1:
        sliders.append(('prop', 'wall_seg_off', 'Segment Offset'))
    if segs > 2:
        sliders.append(('prop', 'wall_seg_spread', 'Spread from Center'))
    # part 1 of the lot ux7 (core/effects.fadeActs, surfaceData 2824): the root fade also blends the root row onto the
    # connector, the junction fade only scales the bulge (the second surface does not copy it)
    fade_hint = None
    if wallCrownRoot(P, sf):
        sliders.append(('prop', 'wall_bfade', 'Bulge Fade (Root)', *(() if E.fadeActs(P, sf, 'bfade') else ({'active': False},))))
    if sf.p.get('jfade') is not None:
        j_ok = E.fadeActs(P, sf, 'jfade')
        sliders.append(('prop', 'wall_jfade', 'Bulge Fade (Junction)', *(() if j_ok else ({'active': False},))))
        if not j_ok and not idle_bulge:
            fade_hint = ('Bulge Fade (Junction) only scales the bulge: the Bulge is 0.' if segs >= 2 else
                         'Bulge Fade (Junction) needs Width Segments ≥ 1 (the second surface does not take it).')
    toggles = [('toggle', 'wall_flip', 'Flip' if short else 'Bulge to the Other Side', 'ARROW_LEFTRIGHT'), ('toggle', 'wall_clean', 'Clean' if short else 'Clean Topology', CLEAN_ICON)]
    second = ('toggle', 'wall_b2on', 'Second' if short else 'Second Surface', 'MOD_ARRAY', *(() if bulge else ({'enabled': False},)))   # (the original offers it only with a bulge, 8798)
    twin = []
    if hasTwin(sf):
        twin = [('prop', 'wall_b2bulge', 'Second: Bulge'), ('prop', 'wall_b2segs', 'Second: Segments'), ('toggle', 'wall_b2flip', 'Second: Flip' if short else 'Second: Bulge to the Other Side', 'ARROW_LEFTRIGHT')]
    from .tool import pie_box
    box = pie_box(sliders[0], sliders[1], ('row', [('toggle', 'wall_flip', 'Flip', 'ARROW_LEFTRIGHT'), ('toggle', 'wall_clean', 'Clean', CLEAN_ICON),
                                                   *([('toggle', 'wall_b2on', 'Second Surface', 'MOD_ARRAY')] if bulge else [])]))   # (G4: gone from the pie, dimmed in the sheet)
    pie = [sel_a, sel_b, delete, box, both, None, None, None]        # NE, SW and SE are filled by the caller (the HairMesh Menu, the walls submenu, the gizmo)
    # the sheet: the blocks by subject under the identity rows the caller adds (name, group and the two curves, Move to
    # Group, Delete: tool.wall_rows) — Shape (the sliders two by two, the toggles, segments to curves), Second Surface
    # (its own values, while there is one)
    from .tool import block
    why = ' '.join(t for t in (BULGE_HINT if idle_bulge else None, fade_hint, None if bulge else 'Second Surface needs a Bulge.') if t) or None
    shape = [sliders[i:i + 2] for i in range(0, len(sliders), 2)] + [[*toggles, second]] + ([[to_curves]] if to_curves else [])
    blocks = [block('wall_shape', 'Shape', 'MOD_LATTICE', shape, True, why)] + ([block('wall_second', 'Second Surface', 'MOD_ARRAY', [[t] for t in twin[:2]] + [twin[2:]])] if twin else [])
    menu = [label, drivers, sel_a, sel_b, both, ('sep',), *([to_curves] if to_curves else []), delete]
    return {'pie': pie, 'blocks': blocks, 'menu': menu, 'label': label, 'drivers': drivers, 'delete': delete}


class HAIRMESH_OT_unite_locks(bpy.types.Operator):
    """Unite the selected locks: where they run into each other their facing edges become one (a common trunk) and the inner sides are closed by new walls — a Y from the root, an X in the middle"""
    bl_idname = 'hairmesh.unite_locks'
    bl_label = 'Unite Locks (Y / X Junction)'
    bl_options = {'REGISTER', 'UNDO'}

    # 4.10 (D4.10): unionSelectedLocks (3221), the pie's "⊕ Unite locks (Y / X junction)" (7927: two locks or more)
    @classmethod
    def poll(cls, context):
        if not (owner.visible(context) and session.find_root(context) is not None):
            return False
        _r, P, sel = select.current(context)
        if P is None or not unite_offered(P, sel.items):
            cls.poll_message_set('Select walls (or curves) of two locks that run into each other')
            return False
        return True

    def execute(self, context):
        from ..core import scene as SC, union as UN
        from . import slow
        root, P, sel = select.current(context)
        SC.settle(P)
        with slow.part('unite locks'), live.heavy(context, root, 'uniting the locks', live.estimate(root, P)[1]):   # (the row tables of the pairs not welded yet — the live ones, rebuilt where stale: the warning above 0.3 s)
            made, msg, welded = UN.unionSelectedLocks(P, list(sel.items), tables=lambda: live.row_tables(root, P).T)   # (the live tables: no build when they are fresh)
        if made:
            sel = Selection([it.id for it in made if it.type == 'surface'], [])   # (3248: S.sel = the new walls, S.asel = [])
            select.store(context.scene.hairmesh, sel)
        if made or welded:                                          # (edges welded by geometry stay welded: the model changed)
            session.save_project(root, P)
            live.touch(root, [c.id for c in P.curves()])            # (every curve dirty, S.surfDirty: 3247)
            live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'} if made or welded else {'CANCELLED'}


def unite_offered(P, sel_items):
    """canUnite (7927: unionLocks() gives two locks or more), kept per model generation and selection — the menus
    draw it often"""
    from ..core import union as UN
    key = (P.gen, P.rev, tuple(sel_items))
    c = P._memo.get('unite_offered')
    if c is None or c[0] != key:
        c = (key, UN.canUnite(P, list(sel_items))); P._memo['unite_offered'] = c
    return c[1]


UNITE = ('op', 'hairmesh.unite_locks', 'Unite Locks (Y / X Junction)', 'MOD_BOOLEAN', {})
UNITE_S = ('op', 'hairmesh.unite_locks', 'Unite Locks', 'MOD_BOOLEAN', {})


_CLASSES = (HAIRMESH_OT_unite_locks, HAIRMESH_OT_create_walls, HAIRMESH_OT_fill_hole, HAIRMESH_OT_average_curve, HAIRMESH_OT_segments_to_curves,
            HAIRMESH_OT_select_wall, HAIRMESH_OT_select_wall_curves)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
