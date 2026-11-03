# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The tool's state, kept on the Scene (so it is saved with the .blend and takes part in undo): the active sub-tool
and, from step 3.3 on, the selection (ids of the model, never Blender objects)."""
import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from ..core import groups as G
from . import slow
from .coords import f32, field_same

# every sub-tool of the WorkSpaceTool (the original's S.tool: 'cursor' = select, 'draw', 'scissors', 'guide', 'crown';
# its 'modifier' is the Modifiers tab of the D here, tool.TABS, the owner's decision), with its key and the step of
# docs/fase-4-plano.md that makes it work (4.0, D4.1 / D4.2): the enum items, the key, the Q pie slot. Only the READY
# ones exist anywhere (the header row, the Q pie, the D, the N panel, the keymap): nothing is ever shown that does
# nothing. A step that makes one work adds it to READY_STEPS. The numbers are in saved files: they never change.
SUBTOOL_PLAN = (
    # (key, label, description, icon, enum number, step, key type, Q pie slot)
    ('SELECT', 'Select', 'Select: select and move anchors and curves (1); double click a curve: its anchors; Esc comes back here', 'RESTRICT_SELECT_OFF', 0, '3', 'ONE', 0),
    ('DRAW', 'Draw', 'Draw: draw a curve on the head — each click places an anchor, Enter finishes (2)', 'GREASEPENCIL', 1, '3', 'TWO', 1),
    ('SCISSORS', 'Scissors', 'Scissors: drag a line across curves to cut them, or cut a wall in two (3 or K)', 'SCULPTMODE_HLT', 2, '3', 'THREE', 2),
    ('GUIDES', 'Guides', 'Guides: guide lines — drag a line across curves, place a NURBS disc or draw one with the pencil; the loops follow them (4)', 'IPO_LINEAR', 3, '4.2', 'FOUR', 4),
    ('CROWN', 'Crown', 'Crown: place the whorl on the head, the parting, the connector and the crown curves (5)', 'FORCE_VORTEX', 4, '4.3', 'FIVE', 5),
    ('BRUSH', 'Brush', 'Brush: move, smooth, inflate or shrink the anchors under a circle (M)', 'BRUSH_DATA', 7, '4.9m', 'M', None),
    ('EDGES', 'Edges', 'Edges: pick edges of a converted mesh and turn the path into curves (V)', 'EDGESEL', 6, '4.9v', 'V', None),
)
READY_STEPS = frozenset({'3', '4.2', '4.3', '4.9m', '4.9v'})        # (4.2: Guides; 4.3: Crown; 4.9m: the anchor brush; 4.9v: Edges)


def ready_subtools(ready=READY_STEPS):
    """the SUBTOOL_PLAN rows that work (their step is done), in order"""
    return tuple(t for t in SUBTOOL_PLAN if t[5] in ready)


# the EnumProperty items of the sub-tools that work
SUBTOOLS = tuple(t[:5] for t in ready_subtools())
SUBTOOL_NUMBERS = frozenset(t[4] for t in SUBTOOLS)
# the Scissors' two modes (S.cutMode, the tool panel's buttons 8974-8981)
CUT_MODES = (
    ('CURVE', 'Curve', 'Cut curves: drag a line across them', 'CURVE_DATA', 0),
    ('SURFACE', 'Wall', 'Cut a wall in two along a curve placed on it anchor by anchor', 'SURFACE_NSURFACE', 1),
)


# the Guides' three modes (S.guideMode, the tool panel's buttons 9015-9017)
GUIDE_MODES = (
    ('LINE', 'Line', 'Drag a line across the selected curves (none selected: every visible one); the loops pass through the crossings', 'IPO_LINEAR', 0),
    ('DISC', 'NURBS Disc', 'Click on the hair: a ring around the head at that height; the curves passing through it get a guide loop', 'MESH_CIRCLE', 1),
    ('PENCIL', 'Pencil', 'Hold the left button and draw a loop around the hair: it closes by itself back at the start, or press Enter; Esc cancels', 'GREASEPENCIL', 2),
)


def _known_subtool(self, value, is_set):
    """the sub-tool as it is read: a number no item has — 5 in a file saved while Modifiers was a sub-tool — is Select.
    Blender reads such a value as '' and no sub-tool would answer a click. On every read, not in load_post: it covers
    every scene of the file, one appended later, and the add-on enabled with the file open"""
    return value if value in SUBTOOL_NUMBERS else 0


def _redraw(self, context):
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _leave_pencil(self, context):
    """4.2: another sub-tool or Guides mode drops the pencil loop being drawn (4713, 9016); then the redraw"""
    if self.subtool != 'GUIDES' or self.guide_mode != 'PENCIL':
        from . import ops_guides
        if ops_guides.pencil is not None:
            ops_guides.cancel_pencil()
    _redraw(self, context)


def _toggle_live(self, context):
    from . import live
    live.set_visible(context, self.live_preview)


# ---- the sliders of the Anchor and Curve panels (phase 3.3): views on the model, nothing stored on the scene ----
# get: the value of the last selected anchor / curve (what the original's panel shows, 8630, 8641); set: every
# selected anchor / curve (8634, 8701), then the model is saved (one undo step) and the live walls follow.

def _current():
    from . import select
    return select.current(bpy.context)


@slow.timed('a field of the Anchor / Curve / Wall sheet')
def _after_set(root, P, touch=None):
    from . import live, select
    from . import session
    session.save_project(root, P)
    if touch:
        live.touch(root, touch)                                     # S.surfDirty, limited to the locks of these curves (bit-identical: live.touch)
    live.update(bpy.context, root)                                  # the preview follows now, inside the property's own undo step
    select.redraw(bpy.context)


def _after_slide(root, rebuild=True):
    """the end of a slider's setter (lot 4.8b): the model has the value; the preview and the undo step follow when the
    interaction ends (deferred.py), so a drag only moves the number"""
    from . import deferred
    deferred.changed(bpy.context, root, rebuild)


# ---- the surface panel (phase 3.5, 8745-8822): views on the LAST selected wall (renderProps shows the last selected
# item); a slider's float32 goes back to the short decimal the original would store (f32). Width segments are shown
# as the slider of the original (segs - 1: 0 = one quad wide); Clean topology applies to every selected wall.
def _wall(key):
    """(root, P, wall, value of key) for the last selected wall, or (None, None, None, None)"""
    from . import select
    root, P, sel = _current()
    sf = select.last_wall(P, sel) if P is not None else None
    return (root, P, sf, sf.p.get(key)) if sf is not None else (None, None, None, None)


def _wall_prop(key, kind, default=0.0):
    def get(self):
        _root, _P, sf, v = _wall(key)
        if sf is None:
            return False if kind in ('bool', 'b2on', 'clean') else 0
        if kind == 'int1':
            return max(0, int(v or 0) - 1) if v is not None else 0
        if kind in ('bool', 'b2on', 'clean'):
            return bool(v)
        if v is None:
            return float(default(sf.p) if callable(default) else default)
        return float(v)

    def set(self, v):
        from ..core import walls as WL
        from . import select
        root, P, sf, _old = _wall(key)
        if sf is None:
            return
        touch = None
        if kind == 'int1':
            if int(v) + 1 == sf.p.get(key):
                return
            WL.setWallProp(P, sf, key, int(v) + 1)
            return _after_slide(root)
        elif kind == 'bool':
            WL.setWallProp(P, sf, key, bool(v))
        elif kind == 'b2on':
            WL.setSecondSurface(P, sf, bool(v))
        elif kind == 'clean':
            _r, _P, sel = _current()
            walls = select.walls_of(P, sel)
            if sf not in walls:
                walls.append(sf)
            WL.setClean(P, walls, bool(v)); touch = [c for w in walls for c in (w.a, w.b)]   # the loops of these locks go straight across (surfDirty, 8793)
        else:
            from ..core import effects as E
            if field_same(sf.p.get(key), f32(v)):
                return
            acts = key not in ('bfade', 'jfade') or E.fadeActs(P, sf, key)
            was = sf.dirty
            WL.setWallProp(P, sf, key, f32(v))
            if not acts:
                sf.dirty = was                                      # (a dimmed fade: the grid would come out the same — lot ux8, part 2)
            return _after_slide(root, acts)
        _after_set(root, P, touch)
    return get, set


def _get_smooth(self):
    root, P, sel = _current()
    a = P.anchors.get(sel.last_anchor) if P is not None and sel.anchors else None
    return a.smooth if a is not None else 1.0


def _set_smooth(self, v):
    from ..core import edit
    from . import select
    root, P, sel = _current()
    anchors = select.anchors_of(P, sel) if P is not None else []
    if not anchors:
        return
    v = f32(v)                                                      # the slider's float32 as the short decimal the original would store
    if any(a.smooth != v for a in anchors):
        edit.setSmooth(P, anchors, v)
        _after_slide(root)


def _curve_prop(key):
    def get(self):
        root, P, sel = _current()
        if P is None or not sel.items:
            return 0.0
        it = P.items.get(sel.items[len(sel.items) - 1])
        v = getattr(it, key, None) if it is not None and it.type == 'curve' else None
        return float(v) if v is not None else 0.0

    def set(self, v):
        from ..core import curves as CM, edit
        from . import select
        root, P, sel = _current()
        cs = select.curves_of(P, sel) if P is not None else []
        if not cs:
            return
        v = f32(v)
        if all((getattr(c, key, None) or 0.0) == v for c in cs):
            return
        if key == 'asm':
            CM.evenAnchors(P, cs, v)
        else:
            edit.setCurveProp(P, cs, key, v)
        _after_slide(root)
    return get, set


# ---- the selected group (3.6; since 15/10/2026 the last selected item when it is a group, chosen in the HairMesh
# Outliner as in the original, ops_groups.selected_group — its panel is renderProps 8645-8689), and the Conversion panel (9028-9130): the
# sliders over the ticked groups (setConv), the project's x-ray. Every setter saves the model (one undo step), keeps
# the collections in step and rebuilds the preview inside the same step.
GUIDE_MODE_ITEMS = (('auto', 'Auto', 'Show curves and anchors only while working with something inside this group (free curves stay visible with the label)', 'NONE', 0),
                    ('show', 'Show', 'Curves and anchors are always shown', 'NONE', 1),
                    ('hide', 'Hide', 'Curves and anchors are hidden', 'NONE', 2))
SYM_AXIS_ITEMS = (('x', 'X', 'Mirror across the X plane through the head center', 'NONE', 0),
                  ('y', 'Y', 'Mirror across the Y plane through the head center (the vertical axis, Z in Blender)', 'NONE', 1),
                  ('z', 'Z', 'Mirror across the Z plane through the head center (the depth axis, -Y in Blender)', 'NONE', 2))
TIP_MODE_ITEMS = (('point', 'Point', 'All columns meet in one vertex', 'NONE', 0),
                  ('gradual', 'Gradual', 'One column is removed per loop towards the tip (one triangle per loop)', 'NONE', 1))
# the original's slider titles (475-481)
CONV_DESC = {'step': 'Distance between neighboring loops along the lock (0: the default, the head height / 24). Set it from the height of the active object with the button beside. With nothing ticked it is the project loop step: the step of the locks outside every group and of new groups, the one the groups are compared with (≠), and how close welded trunks of two locks must be to share a loop (a tenth of it)',
             'dev': 'How much a loop\'s size may differ from the loop step, by the curvature: a step of 10 and Deviation 50% give loops from 5 to 15 (shorter on bends, longer on straight parts or where loops must line up). Gaps longer than step × (1 + deviation) always get extra loops',
             'straighten': 'Fixes sharp diagonal loops: loop edges are turned square to their walls. A narrow strip between close curves is kept straight across first — the slant left goes into the wide walls. Higher — more places are fixed and more strongly',
             'loopOrient': '0 — loops are horizontal (aligned across locks); 1 — loops are perpendicular to the lock. Steep parts stay aligned across locks either way',
             'tipMode': 'Point: all columns meet in one vertex. Gradual: one column is removed per loop towards the tip (one triangle per loop)',
             'relax': 'Evens out the loop spacing along every curve (guide and shared loops stay in place)',
             'mergeTrunks': 'Where locks run along welded curves (a common trunk), weld their coincident vertices and remove the double walls: facing walls both go, same-facing walls keep one'}


def _group():
    """(root, P, the selected group or None); (None, None, None) without a project"""
    from . import select
    root, P, sel = _current()
    if P is None:
        return None, None, None
    return root, P, select.selected_group(P, sel)


@slow.timed('a field of the Group sheet')
def _after_group(root, P, touch=None):
    from . import live, ops_groups, select, session
    session.save_project(root, P)
    ops_groups.sync_collections(bpy.context, root, P)
    if touch:
        live.touch(root, touch)                                     # S.surfDirty, limited to the locks of these curves
    live.update(bpy.context, root)
    select.redraw(bpy.context)


def _conv_touch(P, g, key, scope):
    """(the curves whose locks a conversion parameter changes, whether the row tables must be built at all): the
    locks that read the group (and, from the Conversion tab, the ticked groups and those inside them, else the
    project defaults; its step is the project loop step too, which the trunk backfill reads, 2726) — effects.convTouch.
    Nothing to rebuild for a value no lock reads (part 2 of the lot ux8: the dimmed sliders of a group without walls
    of its own rebuilt its 59 curves), nor for Merge Shared Trunks (Convert alone reads it)."""
    from ..core import effects as E
    every = lambda: [c.id for c in P.curves()]                      # noqa: E731
    if scope == 'group':
        t = E.convTouch(P, {g.id}, {key})
        return (every() if t is None else t), bool(t is None or t or P.conv)   # (3.7: a pending conversion reads every value — Merge Shared Trunks too: regenConversion, 9110)
    targets = G.convTargets(P)
    project = key == 'step' or not targets                          # setConv (9105): S takes it (the step always)
    t = E.convTouch(P, {x.id for x in targets}, {key}, project=project)
    return (every() if t is None else t), bool(t is None or t or project and key != 'mergeTrunks' or P.conv)


def _get_group_name(self):
    _r, _P, g = _group()
    return g.name if g is not None else ''


def _set_group_name(self, v):
    root, P, g = _group()
    if g is None:
        return
    if G.renameItem(P, g, v):                                       # the outliner's rename (8278)
        G.panelsRendered(P, g.id)
        _after_group(root, P)


def shown_color(P, g):
    """the color a group's swatch shows: its own, or — while a group above covers it (the swatch is locked) — the
    color of that group, the one its curves and walls show (tintOf 754; part 2 of the manual test of the lot ux5:
    the locked swatch showed the child's own color). The child's own color stays stored and comes back when the
    group above loses its color."""
    from . import draw
    if g is None or g.type != 'group':
        return draw.hex_rgb(G.DEFAULT_COLOR)
    cov = G.colorCoveredBy(P, g)
    c = draw.hex_rgb((cov or g).color) if (cov or g).color else None
    return c or draw.hex_rgb(G.DEFAULT_COLOR)


def _get_group_color(self):
    _r, P, g = _group()
    return shown_color(P, g)


def _set_group_color(self, v):
    root, P, g = _group()
    if g is None:
        return
    css = '#%02x%02x%02x' % tuple(max(0, min(255, round(x * 255))) for x in v)
    G.setColor(P, g, css); G.panelsRendered(P, g.id)
    _after_group(root, P)


def _group_flag(what):
    """get / set of the group panel's switches: 'visible', 'container' / 'loose' (labels), 'sym_on', 'ticked'"""
    def get(self):
        _r, P, g = _group()
        if g is None:
            return False
        if what == 'visible':
            return bool(g.visible)
        if what in ('container', 'loose'):
            return G.labelOn(P, g, what)
        if what == 'sym_on':
            return G.symInfo(P, g)[0]
        return g.id in P.settings.convChecked                        # ticked

    def set(self, v):
        root, P, g = _group()
        if g is None:
            return
        if what == 'visible':
            g.visible = bool(v); P.rev += 1
        elif what in ('container', 'loose'):
            G.setLabel(P, g, what, v); G.panelsRendered(P, g.id)    # S.surfDirty (8662) regenerates the conversion: the row tables don't read the labels
        elif what == 'sym_on':
            G.setSym(P, g, on=v); G.panelsRendered(P, g.id)         # symChanged (9850): the live mirror is phase 5; the tables don't read sym
        else:
            G.tickGroup(P, g, bool(v))                                # the conversion list's checkbox (9036)
            t = G.convTargets(P)
            if t:
                G.groupConv(P, t[0])                                 # syncConvUI
        _after_group(root, P)
    return get, set


def _group_enum(what):
    """get / set of the group's enums: 'guides' (GUIDE_MODE_ITEMS), 'sym_axis' (SYM_AXIS_ITEMS)"""
    items = GUIDE_MODE_ITEMS if what == 'guides' else SYM_AXIS_ITEMS

    def get(self):
        _r, P, g = _group()
        if g is None:
            return 0
        v = (g.guides or 'auto') if what == 'guides' else G.symInfo(P, g)[1]
        return next((it[4] for it in items if it[0] == v), 0)

    def set(self, v):
        root, P, g = _group()
        if g is None:
            return
        key = items[v][0]
        if what == 'guides':
            G.setGuidesMode(P, g, key); G.panelsRendered(P, g.id); _after_group(root, P)
        else:
            G.setSym(P, g, axis=key); G.panelsRendered(P, g.id); _after_group(root, P)
    return get, set


SAME_TOL = 1e-6                                                     # a slider's float32 read back through its text field's six decimals (1b of 21/10/2026)


def _conv_prop(key, kind, scope):
    """get / set of a conversion parameter: scope 'group' = the active group's own conv (setGroupConv), 'ticked' =
    the Conversion panel over the ticked groups, else the project defaults (setConv, 9105). kind: 'float' (f32),
    'enum' (TIP_MODE_ITEMS), 'bool'."""
    def get(self):
        root, P, g = _group()
        if P is None or (scope == 'group' and g is None):
            return 0 if kind != 'bool' else False
        v = G.convShown(P, g)[key] if scope == 'group' else G.convValue(P, key)
        if kind == 'enum':
            return next((it[4] for it in TIP_MODE_ITEMS if it[0] == v), 0)
        if kind == 'bool':
            return bool(v)
        return float(v or 0)

    def set(self, v):
        from . import deferred, live
        root, P, g = _group()
        if P is None or (scope == 'group' and g is None):
            return
        val = f32(v) if kind == 'float' else TIP_MODE_ITEMS[v][0] if kind == 'enum' else bool(v)
        if G.convUnchanged(P, key, val, g if scope == 'group' else None, tol=SAME_TOL, echo=field_same):
            return                                                  # 1b of 21/10/2026: the value it shows, confirmed again — nothing to rebuild
        if scope == 'group':
            G.setGroupConv(P, g, key, val)
        else:
            G.setConv(P, key, val)
        touch, build = _conv_touch(P, g, key, scope)
        live.touch(root, touch)                                     # S.surfDirty, limited to the locks of these curves
        deferred.changed(bpy.context, root, rebuild=build)          # regenConversion: rebuilt (when anything reads it) and saved — one undo step — when the slider stops or is let go
    return get, set


def _get_xray(self):
    _r, P, _s = _current()
    return float(P.settings.xray) if P is not None else 0.2


def _set_xray(self, v):
    from . import select, session
    root, P, _s = _current()
    if P is None:
        return
    P.settings.xray = f32(v); P.rev += 1                            # setXray (11318)
    session.save_project(root, P); select.redraw(bpy.context)


def _get_guide_opacity(self):
    _r, P, _s = _current()
    return float(P.settings.guideOpacity) if P is not None and P.settings.guideOpacity is not None else 1.0


def _set_guide_opacity(self, v):
    from . import select, session
    root, P, _s = _current()
    if P is None:
        return
    P.settings.guideOpacity = f32(v); P.rev += 1                    # #opGuides (375, 11321)
    session.save_project(root, P); select.redraw(bpy.context)


# ---- the HairMesh Outliner's colour swatches (U1, 15/10/2026): a colour property needs a PropertyGroup to sit on, so
# the scene keeps one row per group (gid), kept in step by session.save_project / bind (sync_rows); the colour itself
# is a view on the model (get / set by gid), nothing is stored in the row but the id.
def _row_color_get(self):
    from . import select
    _r, P, _s = select.current(bpy.context)
    return shown_color(P, P.items.get(self.gid) if P is not None else None)


def _row_color_set(self, v):
    from . import select
    root, P, sel = select.current(bpy.context)
    g = P.items.get(self.gid) if P is not None else None
    if g is None or g.type != 'group':
        return
    css = '#%02x%02x%02x' % tuple(max(0, min(255, round(x * 255))) for x in v)
    G.setColor(P, g, css)
    sg = select.selected_group(P, sel)
    G.panelsRendered(P, sg.id if sg is not None else None)
    _after_group(root, P)


def _get_item_name(self):
    """the name of the last selected item (what the outliner's bar edits in place, 1d of 16/10/2026)"""
    from . import select
    _r, P, sel = select.current(bpy.context)
    it = select.last_item(P, sel) if P is not None else None
    return it.name if it is not None else ''


def _set_item_name(self, v):
    from . import select
    root, P, sel = select.current(bpy.context)
    it = select.last_item(P, sel) if P is not None else None
    if it is None:
        return
    if G.renameItem(P, it, v):                                      # the outliner's rename (8278): trimmed, empty keeps the name
        if it.type == 'group':
            G.panelsRendered(P, it.id)
        _after_group(root, P)                                       # the collection and the "<group> live" object follow the name


class HairMeshRow(PropertyGroup):
    gid: IntProperty(name='Group', default=-1)
    color: FloatVectorProperty(name='Group Color', default=(0x4a / 255.0, 0x7f / 255.0, 0xd6 / 255.0), description='The color of the curves and meshes of the group. A colored group above it covers it (the row then says "Color from" that group and the swatch, locked, shows that group’s color). Right click: Clear Group Color', subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, get=_row_color_get, set=_row_color_set)


class HairMeshExtraRow(PropertyGroup):
    """a line of the list of the Extras (ops_extra.HAIRMESH_UL_extras): a list needs a collection; nothing but the id"""
    xid: IntProperty(name='Extra', default=-1)


def sync_rows(hm, P):
    """one row per group of the project, in outliner order, and one per Extra (called where the model is saved; cheap)"""
    gids = [g.id for g in P.groups()]
    if [e.gid for e in hm.rows] != gids:
        hm.rows.clear()
        for gid in gids:
            hm.rows.add().gid = gid
    xids = [x.id for x in P.of_type('xmod')]
    if [e.xid for e in hm.xrows] != xids:
        hm.xrows.clear()
        for xid in xids:
            hm.xrows.add().xid = xid


class HairMeshSceneProps(PropertyGroup):
    subtool: EnumProperty(name='Sub-tool', items=SUBTOOLS, default='SELECT', update=_leave_pencil, get_transform=_known_subtool)
    cut_mode: EnumProperty(name='Scissors', items=CUT_MODES, default='CURVE', update=_redraw)   # (switched by hairmesh.set_cut_mode: the NURBS mode may start on the selected wall)
    guide_mode: EnumProperty(name='Guides', items=GUIDE_MODES, default='LINE', update=_leave_pencil)   # (4.2; switched by hairmesh.set_guide_mode: leaving the Pencil drops its loop)
    # ours (the Draw page of the D): how the anchors the Draw sub-tool places are born (ops_draw.born)
    draw_anchors: EnumProperty(name='New Anchors', description='Set how the anchors placed by the Draw sub-tool are born', items=(
        ('SMOOTH', 'Smooth', 'The curve runs round through them (Smoothness 1)', 0), ('SHARP', 'Sharp', 'The curve makes a corner at them (Smoothness 0)', 1)), default='SMOOTH')
    # ours (the Freehand of the Draw page): ops_draw, core/freehand.py
    draw_mode: EnumProperty(name='Mode', description='Set how the Draw sub-tool makes a curve', items=(
        ('ANCHORS', 'Anchors', 'Place the anchors one click at a time', 0), ('FREEHAND', 'Freehand', 'Drag a stroke: it becomes one curve when the button is released', 1)), default='ANCHORS')
    draw_project: EnumProperty(name='Project On', description='Set what a stroke lands on', items=(
        ('SURFACE', 'Surface', 'Land on what is nearest to the camera, the head or a mesh: add a layer over the hair', 0), ('HEAD', 'Head', 'Land on the head alone, through the meshes over it: draw close to the skull', 1)), default='SURFACE')
    draw_distance: FloatProperty(name='Distance from Head', description='Lift the finished curve off the surface it was drawn on, as a share of the model size (0: on the surface). The default is what a click of Anchors lifts', min=0.0, soft_max=10.0, max=100.0, default=1.2, precision=2, subtype='PERCENTAGE')
    draw_simplify: FloatProperty(name='Simplify', description='Follow the stroke with fewer anchors, for a smoother curve (0: the closest fit)', min=0.0, max=1.0, default=0.0, subtype='FACTOR')
    crown_sym: BoolProperty(name='Symmetric', description="New crowns are symmetric across the head's center (X): either side drives the other", default=True)   # (4.3: S.crownSym, 8963)
    rows: CollectionProperty(type=HairMeshRow, name='Outliner Rows')
    xrows: CollectionProperty(type=HairMeshExtraRow, name='Extra Rows')
    item_name: StringProperty(name='Name', description='The name of the selected item (the last one selected: a group, a curve, a wall...): type here to rename it', get=_get_item_name, set=_set_item_name)
    live_preview: BoolProperty(name='Live Preview', description='Show the walls between the curves as meshes (one "<group> live" object per group), rebuilt after every edit', default=True, update=_toggle_live)
    # an addition of this port (docs/features-adicionadas.md): the original always drags on the plane facing the camera
    drag_on_head: BoolProperty(name='Drag on Head', description='Dragged anchors follow the head surface under the cursor, lifted off it as the Draw tool places them (1.2 % of the model size); off the head they move on the plane facing the view. Shift still slides along a curve', default=False)
    # 3.8b: the anchor gizmo (the original's tools E / R / T, W, X / Y / Z: 4404-4708); the axes named as Blender shows them
    gizmo_mode: EnumProperty(name='Gizmo', description='The gizmo on the selected anchors (Select sub-tool)', items=(
        ('NONE', 'Off', 'No gizmo on the selected anchors'), ('TRANSLATE', 'Move', 'Arrows: move the selected anchors'),
        ('ROTATE', 'Rotate', 'Rings: turn the selected anchors about the last one'), ('SCALE', 'Scale', 'Boxes: scale the selected anchors about the last one')), default='NONE', update=_redraw)
    gizmo_space: EnumProperty(name='Gizmo Axes', items=(('WORLD', 'World', 'The axes of the world'), ('LOCAL', 'Local', "The axes of the last anchor's curve and its groups")), default='WORLD', update=_redraw)
    gizmo_lock_x: BoolProperty(name='Lock X', description='The gizmo never moves the anchors along X (its X handles hide)', default=False, update=_redraw)
    gizmo_lock_y: BoolProperty(name='Lock Y', description='The gizmo never moves the anchors along Y (its Y handles hide)', default=False, update=_redraw)
    gizmo_lock_z: BoolProperty(name='Lock Z', description='The gizmo never moves the anchors along Z (its Z handles hide)', default=False, update=_redraw)
    sel_curves: StringProperty(name='Selected curves', description='JSON list of curve ids (the model, not objects)', default='[]', options={'HIDDEN'})
    sel_anchors: StringProperty(name='Selected anchors', description='JSON list of anchor ids', default='[]', options={'HIDDEN'})
    edge_sel: StringProperty(name='Selected edges', description='S.edges of the Edges tool: JSON with the mesh id, its data token, the edges in order and the last picked one', default='', options={'HIDDEN'})
    # the surface panel (3.5): the last selected wall
    wall_flip: BoolProperty(name='Bulge to the Other Side', description='The bulge goes to the other side of the wall', get=_wall_prop('flip', 'bool')[0], set=_wall_prop('flip', 'bool')[1])
    wall_clean: BoolProperty(name='Clean Topology', description='The loops on this wall go straight across it, evenly and parallel — the cleanest topology; where a slant is unavoidable it goes into the neighboring walls of the lock. Applies to all selected walls', get=_wall_prop('clean', 'clean')[0], set=_wall_prop('clean', 'clean')[1])
    wall_b2on: BoolProperty(name='Second Surface', description='A second wall between the same two curves with its own settings — together they make a closed lock (e.g. triangular). Available when Bulge is not 0', get=_wall_prop('b2on', 'b2on')[0], set=_wall_prop('b2on', 'b2on')[1])
    wall_b2flip: BoolProperty(name='Second: Bulge to the Other Side', description='The second surface bulges to the other side', get=_wall_prop('b2flip', 'bool')[0], set=_wall_prop('b2flip', 'bool')[1])
    # the active group (3.6): the group panel of the original (8640-8690) over the active collection's group
    group_name: StringProperty(name='Name', description='The group name (its collection follows the model)', get=_get_group_name, set=_set_group_name)
    group_color: FloatVectorProperty(name='Marker Color', default=(0x4a / 255.0, 0x7f / 255.0, 0xd6 / 255.0), description='The color of the curves and meshes of the group. The topmost colored group above an item covers the colors below it: then the sheet says "Color from" that group and this swatch, locked, shows that group’s color (the group’s own comes back when that one is cleared). Clear Color (or a right click) takes the color away', subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, get=_get_group_color, set=_set_group_color)
    group_visible: BoolProperty(name='Visible', description='Show the group: its curves, anchors and live walls (the eye and the monitor of the collection itself belong to Blender and are not kept in step, since disabling the active collection would make it inactive)', get=_group_flag('visible')[0], set=_group_flag('visible')[1])
    group_guides: EnumProperty(name='Curves & Anchors', description='Auto: curves and anchors are shown only while working with something inside this group', items=GUIDE_MODE_ITEMS, get=_group_enum('guides')[0], set=_group_enum('guides')[1])
    group_container: BoolProperty(name='Container', description='Sub-groups inside are converted each on its own (never merged with each other)', get=_group_flag('container')[0], set=_group_flag('container')[1])
    group_loose: BoolProperty(name='Free Curves Visible', description='Curves joined to no wall and no modifier stay shown while the group is not worked on (Auto mode)', get=_group_flag('loose')[0], set=_group_flag('loose')[1])
    group_sym_on: BoolProperty(name='Symmetry', description='Mirror everything inside this group on Convert, or click Apply Symmetry to make real curves', get=_group_flag('sym_on')[0], set=_group_flag('sym_on')[1])
    group_sym_axis: EnumProperty(name='Axis', description='The mirror axis (the plane goes through the head center)', items=SYM_AXIS_ITEMS, get=_group_enum('sym_axis')[0], set=_group_enum('sym_axis')[1])
    group_ticked: BoolProperty(name='Convert This Group', description='Ticked for conversion: the Conversion sliders edit it and Convert makes its mesh', get=_group_flag('ticked')[0], set=_group_flag('ticked')[1])
    # the project's view settings kept in the file (settings.xray, 11318): the curves and anchors drawn through the head
    settings_xray: FloatProperty(name='X-ray', description='How much the curves and anchors show through the head and the walls (0: hidden behind them)', min=0.0, max=1.0, subtype='FACTOR', get=_get_xray, set=_set_xray)
    settings_guide_opacity: FloatProperty(name='Guide Opacity', description='How much the guide lines show in the viewport', min=0.05, max=1.0, step=5, precision=2, subtype='FACTOR', get=_get_guide_opacity, set=_set_guide_opacity)


class HairMeshSlideProps(PropertyGroup):
    """The sliders of the Anchor, Curve and Wall sheets (lot 4.8b, the owner's decision: what is heavy recalculates only
    when it ends; one step of these cost 30 to 300 ms, docs/fase-4-desempenho.md): on the WindowManager, as the conversion
    sliders, so deferred.py pushes one step with the model and the preview. Views on the model, nothing is stored here."""
    anchor_smooth: FloatProperty(name='Smoothness', description='Smoothness of the selected anchors (0: a corner, 1: a full arc); B + a left drag does the same in the viewport', min=0.0, max=1.0, subtype='FACTOR', get=_get_smooth, set=_set_smooth)
    curve_round: FloatProperty(name='Roundness', description='0: smooth spline (with 3 anchors a parabola); 1: every bend follows the circular arc through its anchors. Applies to all selected curves', min=0.0, max=1.0, subtype='FACTOR', get=_curve_prop('round')[0], set=_curve_prop('round')[1])
    curve_fair: FloatProperty(name='Smoothing', description="Moves the anchors' handles towards the smoothest curve through them (no curvature jumps). Frozen handles and anchors shared with other curves stay. Applies to all selected curves", min=0.0, max=1.0, subtype='FACTOR', get=_curve_prop('fair')[0], set=_curve_prop('fair')[1])
    curve_even: FloatProperty(name='Even Out Anchors', description='Moves the anchors so the curve runs smoothly: takes out the wobble of a wavy line and keeps its overall shape. Curve ends and anchors shared with other curves stay. Always from the positions before, so 0 goes back. Applies to all selected curves', min=0.0, max=1.0, subtype='FACTOR', get=_curve_prop('asm')[0], set=_curve_prop('asm')[1])
    wall_segs: IntProperty(name='Width Segments', description='Extra edges across the wall: 0 = one quad wide', min=0, max=11, get=_wall_prop('segs', 'int1')[0], set=_wall_prop('segs', 'int1')[1])
    wall_seg_off: FloatProperty(name='Segment Offset', description='Shifts the segment lines towards the first curve (−) or the second (+), keeping their proportions', min=-1.0, max=1.0, subtype='FACTOR', get=_wall_prop('segOff', 'float')[0], set=_wall_prop('segOff', 'float')[1])
    wall_seg_spread: FloatProperty(name='Spread from Center', description='Moves the segment lines towards the middle of the wall (−) or towards its edges (+)', min=-1.0, max=1.0, subtype='FACTOR', get=_wall_prop('segSpread', 'float')[0], set=_wall_prop('segSpread', 'float')[1])
    wall_bulge: FloatProperty(name='Bulge', description='How much the wall bulges out of its flat shape (0: flat, no volume; negative: the other way). It only shapes the wall with Width Segments 1 or more (one quad across has no inner edge to move); not 0, it makes the Second Surface available', min=-1.0, max=1.0, subtype='FACTOR', get=_wall_prop('bulge', 'float')[0], set=_wall_prop('bulge', 'float')[1])
    wall_b2segs: IntProperty(name='Second: Width Segments', description='Width segments of the second surface', min=0, max=11, get=_wall_prop('b2segs', 'int1')[0], set=_wall_prop('b2segs', 'int1')[1])
    wall_b2bulge: FloatProperty(name='Second: Bulge', description='Bulge of the second surface', min=-1.0, max=1.0, subtype='FACTOR', get=_wall_prop('b2bulge', 'float', lambda p: p.get('bulge') or 0)[0], set=_wall_prop('b2bulge', 'float')[1])
    wall_bfade: FloatProperty(name='Bulge Fade (Root)', description='Share of the lock length over which the bulge fades in from the crown (shorter — sharper)', min=0.02, max=1.0, subtype='FACTOR', get=_wall_prop('bfade', 'float', 0.2)[0], set=_wall_prop('bfade', 'float')[1])
    wall_jfade: FloatProperty(name='Bulge Fade (Junction)', description='Share of the lock length over which the bulge fades in from the junction (shorter — sharper)', min=0.02, max=1.0, subtype='FACTOR', get=_wall_prop('jfade', 'float', 0.3)[0], set=_wall_prop('jfade', 'float')[1])


SLIDE_ATTRS = frozenset(HairMeshSlideProps.__annotations__)


class HairMeshConvProps(PropertyGroup):
    """The conversion sliders (1a of the manual test of 21/10/2026): on the WindowManager, so Blender pushes no undo
    step of its own when one is dragged — deferred.py rebuilds the locks when the value stops changing or the slider
    is let go, and pushes one step with the model and the preview (a Scene property's step comes right after the
    release, before any rebuild could). Views on the model, nothing is stored here."""
    # the selected group's own parameters (the group sheet, 8665-8689)
    group_step: FloatProperty(name='Loop Step', description=CONV_DESC['step'], min=0.0, soft_max=10000.0, precision=4, get=_conv_prop('step', 'float', 'group')[0], set=_conv_prop('step', 'float', 'group')[1])
    group_dev: FloatProperty(name='Deviation', description=CONV_DESC['dev'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('dev', 'float', 'group')[0], set=_conv_prop('dev', 'float', 'group')[1])
    group_straighten: FloatProperty(name='Fix Sharp Angles', description=CONV_DESC['straighten'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('straighten', 'float', 'group')[0], set=_conv_prop('straighten', 'float', 'group')[1])
    group_loop_orient: FloatProperty(name='Loops Horiz ↔ Perp', description=CONV_DESC['loopOrient'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('loopOrient', 'float', 'group')[0], set=_conv_prop('loopOrient', 'float', 'group')[1])
    group_tip_mode: EnumProperty(name='Tips', description=CONV_DESC['tipMode'], items=TIP_MODE_ITEMS, get=_conv_prop('tipMode', 'enum', 'group')[0], set=_conv_prop('tipMode', 'enum', 'group')[1])
    group_relax: FloatProperty(name='Relax Loop Spacing', description=CONV_DESC['relax'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('relax', 'float', 'group')[0], set=_conv_prop('relax', 'float', 'group')[1])
    group_merge_trunks: BoolProperty(name='Merge Shared Trunks', description=CONV_DESC['mergeTrunks'], get=_conv_prop('mergeTrunks', 'bool', 'group')[0], set=_conv_prop('mergeTrunks', 'bool', 'group')[1])
    # the Conversion panel (9028-9130): the sliders edit the ticked groups (and the groups nested in them), else the project defaults
    conv_step: FloatProperty(name='Loop Step', description=CONV_DESC['step'], min=0.0, soft_max=10000.0, precision=4, get=_conv_prop('step', 'float', 'ticked')[0], set=_conv_prop('step', 'float', 'ticked')[1])
    conv_dev: FloatProperty(name='Deviation', description=CONV_DESC['dev'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('dev', 'float', 'ticked')[0], set=_conv_prop('dev', 'float', 'ticked')[1])
    conv_straighten: FloatProperty(name='Fix Sharp Angles', description=CONV_DESC['straighten'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('straighten', 'float', 'ticked')[0], set=_conv_prop('straighten', 'float', 'ticked')[1])
    conv_loop_orient: FloatProperty(name='Loops Horiz ↔ Perp', description=CONV_DESC['loopOrient'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('loopOrient', 'float', 'ticked')[0], set=_conv_prop('loopOrient', 'float', 'ticked')[1])
    conv_tip_mode: EnumProperty(name='Tips', description=CONV_DESC['tipMode'], items=TIP_MODE_ITEMS, get=_conv_prop('tipMode', 'enum', 'ticked')[0], set=_conv_prop('tipMode', 'enum', 'ticked')[1])
    conv_relax: FloatProperty(name='Relax Loop Spacing', description=CONV_DESC['relax'], min=0.0, max=1.0, subtype='FACTOR', get=_conv_prop('relax', 'float', 'ticked')[0], set=_conv_prop('relax', 'float', 'ticked')[1])
    conv_merge_trunks: BoolProperty(name='Merge Shared Trunks', description=CONV_DESC['mergeTrunks'], get=_conv_prop('mergeTrunks', 'bool', 'ticked')[0], set=_conv_prop('mergeTrunks', 'bool', 'ticked')[1])


CONV_ATTRS = frozenset(HairMeshConvProps.__annotations__)


def prop_name(attr):
    """the name of a property of the menus (an icon-only toggle's text in the list menu)"""
    from .ops_guides import GUIDE_ATTRS, HairMeshGuideProps
    cls = HairMeshConvProps if attr in CONV_ATTRS else HairMeshSlideProps if attr in SLIDE_ATTRS else HairMeshGuideProps if attr in GUIDE_ATTRS else HairMeshSceneProps
    p = cls.bl_rna.properties.get(attr)
    return p.name if p is not None else attr


def data_of(context, attr, hm=None):
    """where a property of the menus lives: the conversion sliders and the guide's panel (4.2) on the WindowManager,
    the rest on the Scene"""
    if attr in CONV_ATTRS:
        return context.window_manager.hairmesh_conv
    if attr in SLIDE_ATTRS:
        return context.window_manager.hairmesh_slide           # (lot 4.8b: the sheets' sliders)
    if attr.startswith('guide_') and attr != 'guide_mode':
        return context.window_manager.hairmesh_guide
    if attr in ('crown_rings', 'crown_whorl'):
        return context.window_manager.hairmesh_crown           # (4.3: the crown's panel)
    from .ops_profile import PROFILE_ATTRS, TUBE_ATTRS
    if attr in PROFILE_ATTRS:
        return context.scene.hairmesh_profile                  # (4.6: the Profile tab)
    if attr in TUBE_ATTRS:
        return context.window_manager.hairmesh_tube            # (4.6: the sheet of a Profile modifier)
    from .ops_extra import XMOD_ATTRS
    if attr in XMOD_ATTRS:
        return context.window_manager.hairmesh_xmod            # (4.7b: the sheet of an Extra)
    from .brush import BRUSH_ATTRS
    if attr in BRUSH_ATTRS:
        return context.window_manager.hairmesh_brush           # (4.7b: the brush's settings)
    from .ops_topology import TOPO_DEF_ATTRS, TOPO_ITEM_ATTRS
    if attr in TOPO_DEF_ATTRS:
        return context.scene.hairmesh_topo                     # (4.4: the Topology tab's settings for new items)
    if attr in TOPO_ITEM_ATTRS:
        return context.window_manager.hairmesh_topo            # (4.4: the sheet of a Cap / Fill holes / Density)
    return hm if hm is not None else context.scene.hairmesh


def register():
    bpy.utils.register_class(HairMeshRow)
    bpy.utils.register_class(HairMeshExtraRow)
    bpy.utils.register_class(HairMeshSceneProps)
    bpy.utils.register_class(HairMeshConvProps)
    bpy.utils.register_class(HairMeshSlideProps)
    bpy.types.Scene.hairmesh = bpy.props.PointerProperty(type=HairMeshSceneProps)
    bpy.types.WindowManager.hairmesh_conv = bpy.props.PointerProperty(type=HairMeshConvProps)
    bpy.types.WindowManager.hairmesh_slide = bpy.props.PointerProperty(type=HairMeshSlideProps)


def unregister():
    del bpy.types.WindowManager.hairmesh_slide
    del bpy.types.WindowManager.hairmesh_conv
    del bpy.types.Scene.hairmesh
    bpy.utils.unregister_class(HairMeshSlideProps)
    bpy.utils.unregister_class(HairMeshConvProps)
    bpy.utils.unregister_class(HairMeshSceneProps)
    bpy.utils.unregister_class(HairMeshExtraRow)
    bpy.utils.unregister_class(HairMeshRow)
