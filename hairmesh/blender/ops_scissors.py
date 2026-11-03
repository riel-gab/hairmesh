# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Phase 3.8a inside Blender: the Scissors sub-tool (the original's ✂, hair-modeler.clean.html:5085-5486, 5779-5805,
6011-6024, 7935, 8052-8062, 8974-8997), its two modes (Scene.hairmesh.cut_mode) and their pies.

  Curve           hairmesh.scissors (LMB): a line dragged in the viewport; on the release, the selected curves it
                  crosses away from their ends go to the pie HAIRMESH_MT_cut_pie — Split into two curves / Split,
                  delete the upper part / Split, delete the lower part (hairmesh.cut_curves); a click (5 px) picks a
                  curve (Shift adds), as RMB does (ops_select.insert_anchor). One undo step per cut.
  Wall   hairmesh.wall_cut (LMB / RMB): a modal session on one wall — its curves, the anchors placed on it,
                  the drag of an anchor, RMB on the cut inserts one, a double click deletes one; Backspace takes the
                  last one off while placing, Esc cancels, Enter (or the pie's Cut, HAIRMESH_MT_wall_cut_pie)
                  splits the wall in two along a new curve: one undo step, nothing before it.

The data part is core/scissors.py (bit for bit with the original: tests/test_scissors_parity.py). Every operator has
a form that needs no viewport, for the tests and the stress test: hairmesh.cut_curves with the stroke in region
pixels and the view's perspective matrix (or the hits themselves), hairmesh.wall_cut_apply with the cut as JSON."""
import json
from math import hypot

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, FloatVectorProperty, IntProperty, IntVectorProperty, StringProperty
from mathutils import Matrix, Vector

from ..core import curves as CM, groups as G, scissors as SC
from ..core.raycast import Raycaster
from ..core.selection import Selection
from . import clicks, draw as drawmod, live, meshes, modal_keys, owner, pick, select, session
from .coords import to_blender
from .ops_draw import NAV_EVENTS
from .ops_select import PICK_CURVE_PX, View
from .props import CUT_MODES

CLICK_PX = 5                                                        # a stroke this short is a click: it picks a curve (6012)
MODES = (('SPLIT', 'Split into Two Curves', 'Each curve crossed is split in two at the crossing (the shape kept)', 'SCULPTMODE_HLT', 0),
         ('UP', 'Split, Delete the Upper Part', 'Split, then delete the part whose free end is higher', 'TRIA_UP', 1),
         ('DOWN', 'Split, Delete the Lower Part', 'Split, then delete the part whose free end is lower', 'TRIA_DOWN', 2))
PICK_FIRST = 'Pick the curve first: RMB on it (or select it before), then draw the cut across it'
NO_CROSS = "The cut doesn't cross the selected curve"
NO_WALL = 'Click the wall to cut'
HELP_CURVE = '\n'.join(('Click or RMB on a curve: pick it (Shift + click adds more; or select curves before).',
                        'Drag a line across it with LMB: the pie opens.',
                        'The pie: split it in two, or split it and delete the upper / lower part (the part whose free end is higher / lower).',
                        'The shape is kept; the walls of the cut curves are deleted (make new ones from the pieces).'))
HELP_SURFACE = '\n'.join(('Click the wall to cut.',
                          "Click one of its curves where the cut starts (on an anchor, or a new anchor goes in keeping the curve's shape).",
                          'Click on the wall: add anchors.',
                          'Click its other curve: end the cut line.',
                          'Drag an anchor: move it. RMB on the cut: insert one. Double click: delete one.',
                          'Enter (or Shift + RMB > Cut): split the wall in two along a new curve.',
                          'Backspace: remove the last anchor while placing. Esc: cancel.'))

pending = None                                                      # a stroke's hits waiting for the pie: {'root', 'hits': [(curve id, seg, u)], 'title'}
CURVE_KEYS = ((('LMB Drag',), 'Cut the Selected Curves'), (('RMB',), 'Pick a Curve'), (('Shift',), 'Add'), (('Esc',), 'Cancel'))
WALL_KEYS = ((('LMB',), 'Place the Cut'), (('Enter',), 'Cut'), (('Esc',), 'Cancel'))
DEBUG_EVENTS = None                                                 # (a list: the events the wall cut's modal receives, for the simulations)
cutting = None                                                      # the wall being cut: {'root': root name, 'P': model, 'cut': SurfCut}
LEAD_GONE = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE',
             'MOUSESMARTZOOM', 'WINDOW_DEACTIVATE'}                   # events after which the dot of the first click goes (3.8f): navigation, the focus lost


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def matrix_projector(persp, size, matrix_world):
    """toScreen for a given view: `persp` the 4x4 perspective matrix (world -> clip, rv3d.perspective_matrix, 16 floats
    row by row), `size` the region's (width, height) — the projector of pick.region_projector without a region"""
    M = Matrix([persp[0:4], persp[4:8], persp[8:12], persp[12:16]]) @ matrix_world
    w, h = size

    def project(p):
        x, y, z = to_blender(p)
        v = M @ Vector((x, y, z, 1.0))
        if v.w <= 0.0:
            return None
        return ((v.x / v.w + 1.0) * 0.5 * w, (v.y / v.w + 1.0) * 0.5 * h, v.z / v.w)
    return project


def front_view(root, P, size=1000, curves=None):
    """A front orthographic view (Blender's Numpad 1: x right, z up) framing the curves (all, or `curves`), as the
    16 floats of a perspective matrix for a size x size region — the non-modal form's view for the tests, the
    stress test and the benchmark"""
    pts = [root.matrix_world @ Vector(to_blender(s.p)) for c in (curves if curves is not None else P.curves()) for s in c.samples]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    c = (lo + hi) / 2
    h = max(hi.x - lo.x, hi.z - lo.z) * 0.55 or 1.0
    d = (hi.y - lo.y) or 1.0
    M = [1 / h, 0, 0, -c.x / h,
         0, 0, 1 / h, -c.z / h,
         0, 0.5 / d, 0, -0.5 * c.y / d,
         0, 0, 0, 1]
    return M


def shown_selected(P, sel):
    """selectedCurves() as scissorHits gets them: the selected curves the viewport shows (c.obj.visible)"""
    shown = {c.id for c in G.shownCurves(P, sel.items, sel.anchors)[0]}
    return [c for c in select.curves_of(P, sel) if c.id in shown]


# ---------------- the curve mode ----------------

def cut_pie_items():
    """the slots of the cut pie (6018) for `pending` (pure): [W, E, S, N, NW]"""
    p = pending
    if p is None:
        return [('label', 'Nothing to cut', 'INFO')]
    return [*(('op', 'hairmesh.cut_curves', label, icon, {'mode': key}) for key, label, _d, icon, _n in MODES),
            ('box', [('label', p['title'], 'SCULPTMODE_HLT')]), ('op', 'hairmesh.cut_dismiss', 'Cancel', 'X', {})]   # (the title in a box: a bare label sits faint over the pie's centre)


class HAIRMESH_MT_cut_pie(bpy.types.Menu):
    """Choose what the Scissors' stroke does: split the curves it crossed, or split and delete the upper / lower part"""
    bl_idname = 'HAIRMESH_MT_cut_pie'
    bl_label = 'HairMesh Cut'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        from .tool import draw_item
        pie = self.layout.menu_pie()
        for item in cut_pie_items():
            draw_item(pie, context.scene.hairmesh, item)


class HAIRMESH_OT_cut_dismiss(bpy.types.Operator):
    """Cancel: nothing is cut"""
    bl_idname = 'hairmesh.cut_dismiss'
    bl_label = 'Cancel the Cut'
    bl_options = {'REGISTER'}

    def execute(self, context):
        global pending
        pending = None
        return {'FINISHED'}


class HAIRMESH_OT_cut_curves(bpy.types.Operator):
    """Cut the curves the Scissors' line crossed: each is split in two at the crossing, the shape kept (curves welded along the cut go with it); or split and delete the upper / lower part. The walls of the cut curves are deleted"""
    bl_idname = 'hairmesh.cut_curves'
    bl_label = 'Cut HairMesh Curves'
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name='How', items=MODES, default='SPLIT')
    stroke: FloatVectorProperty(name='Stroke', description='Non-modal form: the line in region pixels (x0, y0, x1, y1), over the selected curves', size=4, options={'HIDDEN', 'SKIP_SAVE'})
    view_matrix: FloatVectorProperty(name='View', description="Non-modal form: the view's perspective matrix (world to clip, row by row) the stroke was drawn in", size=16, options={'HIDDEN', 'SKIP_SAVE'})
    region_size: IntVectorProperty(name='Region', description='Non-modal form: the width and height of the region the stroke was drawn in', size=2, default=(1, 1), options={'HIDDEN', 'SKIP_SAVE'})
    hits_json: StringProperty(name='Hits', description='Non-modal form: the crossings as [[curve id, segment, u], ...]', default='', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        global pending
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)                                         # (the samples the frame would have made: scissorHits reads them)
        if any(self.view_matrix):
            lst = shown_selected(P, sel)
            if not lst:
                self.report({'INFO'}, PICK_FIRST)
                return {'CANCELLED'}
            hits = SC.scissorHits(P, *self.stroke, lst, matrix_projector(list(self.view_matrix), tuple(self.region_size), root.matrix_world))
        else:
            raw = json.loads(self.hits_json) if self.hits_json else (pending['hits'] if pending is not None and pending['root'] == root.name else [])
            hits = [{'c': P.items[c], 'seg': s, 'u': u} for c, s, u in raw
                    if c in P.items and P.items[c].type == 'curve' and 0 <= s < len(P.items[c].anchors) - 1]   # (a pie left open over an edit: what is gone is dropped)
        pending = None
        if not hits:
            self.report({'INFO'}, NO_CROSS)
            return {'CANCELLED'}
        n, msg, _ids = SC.cutCurves(P, hits, self.mode.lower())
        if not n:
            self.report({'INFO'}, msg)
            session.forget_model(root.name)                         # (an anchor may have gone in before the split refused: the model as its text holds it)
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection([i for i in sel.items if i in P.items], []))   # S.asel = [], S.sel filtered (5124)
        session.save_project(root, P)
        # (no live.touch: live.row_tables sees the pieces' new samples and the walls gone by itself, and rebuilds just
        # that — tests/test_rows_incremental: the cuts through that path equal a rebuild from scratch, bit for bit;
        # touching the pieces spread over every lock welded at their roots: 2.1 s for 20 curves of the example)
        live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class HAIRMESH_OT_scissors(bpy.types.Operator):
    """Drag a line across the selected curves to cut them (a pie asks how); a click picks a curve, Shift adds it"""
    bl_idname = 'hairmesh.scissors'
    bl_label = 'HairMesh Scissors'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        global pending
        hm = context.scene.hairmesh
        if hm.subtool != 'SCISSORS' or hm.cut_mode != 'CURVE' or event.alt or event.oskey:
            return {'PASS_THROUGH'}
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        pending = None
        self.view = v; self.add = event.shift
        self.x0, self.y0 = event.mouse_region_x, event.mouse_region_y
        drawmod.overlay = ('line', self.x0, self.y0, self.x0, self.y0)
        context.window_manager.modal_handler_add(self)
        live.header(context.area, 'HairMesh Scissors (Curve)', CURVE_KEYS)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        drawmod.overlay = None
        live.header(context.area, None)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)                                          # (Blender ends the modal itself: a window closed, a file loaded)

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            drawmod.overlay = ('line', self.x0, self.y0, x, y)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end(context)
            return self._release(context, x, y)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _release(self, context, x, y):
        global pending
        v = self.view; P = v.P; hm = context.scene.hairmesh
        project = pick.region_projector(context.region, context.region_data, v.root.matrix_world)   # (the view as it is now: the stroke may have been navigated through)
        sel = select.load(hm).prune(P)
        if hypot(x - self.x0, y - self.y0) <= v.px(CLICK_PX):      # a click picks a curve (6012)
            ch = pick.pick_curve(v.curves(), project, x, y, v.px(PICK_CURVE_PX))
            if ch is None:
                return {'CANCELLED'}
            sel.select_item(ch['curve'].id, self.add)
            select.store(hm, sel); context.area.tag_redraw()
            return {'FINISHED'}
        sc = shown_selected(P, sel)
        if not sc:
            self.report({'INFO'}, PICK_FIRST)
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        hits = SC.scissorHits(P, self.x0, self.y0, x, y, sc, project)
        if not hits:
            self.report({'INFO'}, NO_CROSS)
            return {'CANCELLED'}
        pending = {'root': v.root.name, 'hits': [(h['c'].id, h['seg'], h['u']) for h in hits], 'title': SC.cutTitle(hits)[2:]}
        bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name=HAIRMESH_MT_cut_pie.bl_idname)
        return {'CANCELLED'}                                        # (nothing changed yet: the pie's item makes the undo step)


def pick_curve_rmb(context, event, v):
    """RMB in the Scissors' curve mode (5780): the curve under the cursor is picked (Shift adds). True: handled."""
    ch = pick.pick_curve(v.curves(), v.project, event.mouse_region_x, event.mouse_region_y, v.px(PICK_CURVE_PX))
    if ch is None:
        return False
    hm = context.scene.hairmesh
    sel = select.load(hm).prune(v.P)
    sel.select_item(ch['curve'].id, event.shift)
    select.store(hm, sel); context.area.tag_redraw()
    return True


# ---------------- the NURBS surface mode ----------------

def _world(root, p):
    return tuple(root.matrix_world @ Vector(to_blender(p)))


def view_cut(root, P, cut):
    """drawSurfCut (5344): the cut's line and dots for the draw handler (world space), the HUD text"""
    line, dots, hud = SC.surfCutView(P, cut)
    drawmod.wall_cut = ([_world(root, p) for p in line], [(_world(root, p), kind) for p, kind in dots])
    return hud


def start_cut(context, root, P, wid, report=None):
    """startSurfCut (5334): the cut begins on wall `wid` (it alone and its two curves count from then on), selected"""
    global cutting
    sf = P.items.get(wid)
    if sf is None or sf.type != 'surface':
        return None
    cut, msg = SC.startSurfCut(P, sf, meshes.tables(root, P))
    if cut is None:
        if report:
            report({'INFO'}, msg)
        return None
    cutting = {'root': root.name, 'P': P, 'cut': cut}
    select.store(context.scene.hairmesh, Selection([sf.id], []))
    return cut


def end_cut():
    """cancelSurfCut (5345)"""
    global cutting
    cutting = None
    drawmod.wall_cut = None


def cut_of(context):
    """the cut in progress of this scene's project, or None. A cut whose model is no longer the one in use (an undo
    — from the D's Undo too —, a reload) or whose wall went (deleted from a menu meanwhile) is dropped, as drawSurfCut
    (5352) and restore (1221) drop it."""
    if cutting is None:
        return None
    root = session.find_root(context)
    if root is None or root.name != cutting['root']:
        return None
    try:
        P = session.get_project(root)
    except Exception:
        P = None
    cut = cutting['cut']
    if P is not cutting['P'] or P.items.get(cut.sf.id) is not cut.sf:
        end_cut()
        return None
    return cut


def apply_cut(context, root, P, cut, report, keep=False):
    """Enter (applySurfCut, 5386): the new curve along the cut, the wall split in two; one undo step (the caller's).
    Refused (cutBuild's text), the cut stays to be edited when `keep` (the session: the original only toasts), unless
    its ends already went in (the model then is the text's again, forget_model, and the cut goes)."""
    CM.rebuild_dirty(P)
    c, msg, touched = SC.applySurfCut(P, cut)
    if c is None:
        if touched:
            session.forget_model(root.name)                         # (the ends went in before the cut was refused: the model as its text holds it)
            end_cut()
        elif not keep:
            end_cut()
        report({'INFO'}, msg or 'Nothing to cut')
        return {'CANCELLED'}
    end_cut()
    select.store(context.scene.hairmesh, Selection([c.id], []))
    session.save_project(root, P)
    live.update(context, root)                                      # (no live.touch: as for cut_curves — the new curve, the walls made / gone, the curves resampled)
    select.redraw(context)
    report({'INFO'}, msg)
    return {'FINISHED'}


def _cut_mesh(root, P, cut):
    """the triangle mesh of the wall being cut, kept by the cut while the model and the wall's grid stay as they were
    (P.gen, the grid's revision): pick.wall_meshes checks the freshness of every row table (~20 ms a call on the
    example) and ran at every move of the mouse (3.8f)"""
    st = session.state(root, P)
    ours = cutting is not None and cutting.get('cut') is cut
    key = (P.gen, (st.get('grid_rev') or {}).get(cut.sf.id))
    c = cutting.get('mesh') if ours else None
    if c is not None and c[0] == key:
        return c[1]
    m = pick.wall_meshes(root, P).get(cut.sf.id)
    if ours:
        cutting['mesh'] = ((P.gen, (st.get('grid_rev') or {}).get(cut.sf.id)), m)   # (the key after the refresh)
    return m


def _hit_on(root, P, cut, region, rv3d, x, y):
    """pickObjects(m, [P.sf.mesh]) (5215): where the cursor's ray meets the wall being cut, or None"""
    m = _cut_mesh(root, P, cut)
    if m is None:
        return None
    ray = pick.model_ray(root, region, rv3d, (x, y))
    rc = Raycaster(); rc.set(ray.origin, ray.direction)
    hits = rc.intersectObject(m)
    return hits[0]['point'] if hits else None


class HAIRMESH_OT_wall_cut(bpy.types.Operator):
    """Click a wall, then put the cut on it like a curve — from one of its curves, over the wall, to its other curve; Enter splits the wall in two along a new curve"""
    bl_idname = 'hairmesh.wall_cut'
    bl_label = 'HairMesh Wall Cut'
    bl_options = {'REGISTER', 'UNDO'}

    wall: IntProperty(name='Wall', description='The wall to cut (-1: the one under the cursor)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        hm = context.scene.hairmesh
        if hm.subtool != 'SCISSORS' or hm.cut_mode != 'SURFACE' or event.alt or event.oskey or (event.type == 'RIGHTMOUSE' and event.shift):
            return {'PASS_THROUGH'}
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        if self.wall < 0 and cut_of(context) is not None and cutting.get('op') is not None:
            # a click the session let pass (over a panel of the sidebar or the toolbar with Region Overlap) that Blender
            # gave to the viewport after all: it is the cut's click, not a new cut
            cutting['op']._click(context, event)
            return {'CANCELLED'}
        if event.type == 'RIGHTMOUSE' and self.wall < 0:
            return {'CANCELLED'}                                    # (no wall yet: RMB does nothing, 5434)
        self.view = v; self.root = v.root; self.P = v.P; self.drag = None
        self.clicks = clicks.DoubleClick()                          # (the double click that deletes a cut's anchor: a modal never sees DOUBLE_CLICK, clicks.py)
        wid = self.wall if self.wall >= 0 else pick.wall_under(context, v.root, event.mouse_region_x, event.mouse_region_y, head=False)   # (pickObjects over the shown walls alone, 5434)
        if wid is None:
            self.report({'INFO'}, NO_WALL)
            return {'CANCELLED'}
        self.cut = start_cut(context, v.root, v.P, wid, self.report)
        if self.cut is None:
            return {'CANCELLED'}
        cutting['op'] = self                                        # (the session: a click that reaches the keymap is handed to it)
        self._show(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _show(self, context):
        hud = view_cut(self.root, self.P, self.cut)
        live.header(context.area, f'HairMesh: {hud}', WALL_KEYS)
        if context.area is not None:
            context.area.tag_redraw()

    def _drop_lead(self, context):
        """ours (3.8f): the dot of the first click goes. Only before the start: after it the dot is not drawn, and a
        redraw then would run surfCutView — which takes ids (tmpAnchor) once the end is in — where the original
        draws nothing"""
        if self.cut.lead is not None and not self.cut.start:
            self.cut.lead = None; self._show(context)

    def _leave(self, context):
        live.header(context.area, None)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        modal_keys.clear(context)
        end_cut()                                                   # (the modal killed: a file loaded, the window closed)

    def _click(self, context, event, project=None):
        """surfCutDown (5429) for a press or a double click in the viewport, and what follows from it"""
        x, y = event.mouse_region_x, event.mouse_region_y
        P = self.P; cut = self.cut; v = self.view
        if project is None:
            project = pick.region_projector(context.region, context.region_data, self.root.matrix_world)
        hit = _hit_on(self.root, P, cut, context.region, context.region_data, x, y)
        other = pick.wall_under(context, self.root, x, y, head=False)
        double = event.type == 'LEFTMOUSE' and self.clicks.press(event)   # (e.detail >= 2: paired here, clicks.py)
        res = SC.surfCutDown(P, cut, (x, y), 0 if event.type == 'LEFTMOUSE' else 2, double, project, hit, other, v.ui)
        if res is None:
            return
        if 'toast' in res:
            self.report({'INFO'}, res['toast'])
        elif 'switch' in res:
            nc = start_cut(context, self.root, P, res['switch'], self.report)
            if nc is not None:
                self.cut = nc; cutting['op'] = self; self._show(context)
        elif 'drag' in res:
            self.drag = res['drag']
            if res.get('redraw'):
                self._show(context)
        else:
            self._show(context)

    def _over_ui(self, context, event):
        """the cursor is not over the viewport's own region: outside it, or over a region overlapping it (the
        toolbar, the N panel, the headers with Region Overlap) — the click is the interface's"""
        r = context.region
        if r is None:
            return True
        mx, my = event.mouse_x, event.mouse_y
        if not (r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height):
            return True
        for o in context.area.regions if context.area is not None else ():
            if o.type in ('TOOLS', 'UI', 'HEADER', 'TOOL_HEADER', 'ASSET_SHELF', 'ASSET_SHELF_HEADER') and o.width > 1 and o.height > 1 \
                    and o.x <= mx < o.x + o.width and o.y <= my < o.y + o.height:
                return True
        return False

    def modal(self, context, event):
        hm = context.scene.hairmesh
        alive = (cutting is not None and cutting['cut'] is self.cut) if event.type in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE') else cut_of(context) is self.cut
        if not alive:                                               # applied or cancelled elsewhere (the pie, the D menu), undone, its wall gone (the model checked at every event but a move: cut_of reads the project's text)
            self._leave(context)
            return {'CANCELLED'}
        if hm.subtool != 'SCISSORS' or hm.cut_mode != 'SURFACE' or context.mode != 'OBJECT':   # another sub-tool or mode (setTool, 4714)
            end_cut(); self._leave(context)
            return {'CANCELLED'}
        t = event.type; val = event.value
        if DEBUG_EVENTS is not None and t not in ('TIMER', 'TIMER_REPORT', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
            DEBUG_EVENTS.append((t, val, event.shift))
        x, y = event.mouse_region_x, event.mouse_region_y
        P = self.P; cut = self.cut; v = self.view
        mouse = t in {'MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE'}
        if mouse and self.drag is None and self._over_ui(context, event):
            self._drop_lead(context)                                # (ours, 3.8f: the dot of the first click goes off the viewport)
            return {'PASS_THROUGH'}                                 # (the buttons of the N panel, the header, the toolbar)
        if t in LEAD_GONE or t.startswith('NDOF') or (t.startswith('NUMPAD_') and val == 'PRESS'):
            self._drop_lead(context)                                # (the view moves, or the window loses the focus: the dot is no longer under the cursor until the next move)
        project = pick.region_projector(context.region, context.region_data, self.root.matrix_world)   # (the view as it is now: navigation passes through)
        if t not in {'LEFTMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self.clicks.other(event)                                # (another button or key: no double click across it)
        if t == 'MOUSEMOVE':
            self.clicks.moved(event)                                # (an anchor dragged: no double click after it)
            hit = _hit_on(self.root, P, cut, context.region, context.region_data, x, y)
            if self.drag is not None:
                if SC.surfCutPtMove(P, cut, self.drag, (x, y), project, hit, v.ui):
                    self._show(context)
            elif SC.surfCutHover(P, cut, (x, y), project, hit, v.ui):
                self._show(context)
            return {'RUNNING_MODAL'}
        if t == 'RIGHTMOUSE' and val == 'PRESS' and event.shift:
            return {'PASS_THROUGH'}                                 # the context pie (the tool's Shift+RMB): while cutting it holds only the cut (tool.pie_items, 7892)
        if t in {'LEFTMOUSE', 'RIGHTMOUSE'} and val in {'PRESS', 'DOUBLE_CLICK'}:
            if event.alt or event.oskey:
                return {'PASS_THROUGH'}
            self._click(context, event, project)
            return {'RUNNING_MODAL'}
        if t == 'LEFTMOUSE' and val == 'RELEASE':
            self.drag = None
            self.clicks.release(event)
            return {'RUNNING_MODAL'}
        if t in {'RET', 'NUMPAD_ENTER'} and val == 'PRESS' and self.drag is None:
            why = not_ready(cut)
            if why:                                                 # (8060 / 5387: Enter before the end, or a cut refused, only says why)
                self.report({'INFO'}, why)
                return {'RUNNING_MODAL'}
            res = apply_cut(context, self.root, P, cut, self.report, keep=True)
            if cutting is not None and cutting['cut'] is self.cut:  # refused, still to be edited
                self._show(context)
                return {'RUNNING_MODAL'}
            self._leave(context)
            return res
        if t == 'BACK_SPACE' and val == 'PRESS':
            if cut.start and not cut.end and self.drag is None:     # placing: the last anchor goes (8061)
                SC.removeLast(cut); self._show(context)
            return {'RUNNING_MODAL'}
        if t == 'ESC' and val == 'PRESS':
            end_cut(); self._leave(context)
            return {'CANCELLED'}
        if t == 'Z' and val == 'PRESS' and (event.ctrl or event.oskey):
            end_cut(); self._leave(context)                         # (the restore of an undo drops the cut too, 1221)
            return {'CANCELLED', 'PASS_THROUGH'}
        if mouse:
            return {'RUNNING_MODAL'}                                # (the releases and the other buttons of the viewport: the cut's)
        return {'PASS_THROUGH'}                                     # every other key is Blender's or the tool's (navigation, 1/2/3, D, O, G / R / S, Ctrl+S, F3…): the next event ends the cut when it changed the sub-tool, the mode or the model


def wall_cut_pie_items(context):
    """openCutPie (7935), pure, as the slots of the context pie (W, E, S, N): Cut — or what is missing, as a line that
    does nothing —, Cancel the cut, Remove the last anchor while placing, the title"""
    cut = cut_of(context)
    if cut is None:
        return [('label', 'No wall is being cut', 'INFO')]
    first = remove = cancel = None
    for kind, label in SC.cutPieItems(cut):
        label = label.replace('✂ ', '').replace('↶ ', '').replace('✖ ', '')
        if kind == 'cut':
            first = ('op', 'hairmesh.wall_cut_apply', label, 'SCULPTMODE_HLT', {})
        elif kind == 'info':
            first = ('label', label, 'INFO')
        elif kind == 'remove':
            remove = ('op', 'hairmesh.wall_cut_remove', label, 'LOOP_BACK', {})
        else:
            cancel = ('op', 'hairmesh.wall_cut_cancel', label, 'X', {})
    return [first, cancel, remove or ('empty',), ('box', [('label', f'Cutting {cut.sf.name}', 'SCULPTMODE_HLT')])]


def not_ready(cut):
    """why Cut would do nothing now (None: ready)"""
    if cut is None:
        return 'No wall is being cut: Scissors > Wall, then click a wall'
    if not cut.start:
        return "Start the cut on one of the wall's curves"
    if not cut.end:
        return "End the cut on the wall's other curve"
    if not cut.build:
        return cut.err or 'The cut must run along the wall — move its ends'
    return None


def _end_of(P, cut, e):
    """an end of the cut from its JSON form: {"side": 0 | 1, "anchor": id} or {"side", "seg", "u"} (a new anchor
    there, keeping the shape)"""
    c = cut.A if e['side'] == 0 else cut.B
    if e.get('anchor') is not None:
        a = P.anchors[e['anchor']]
        return {'side': e['side'], 'c': c, 'a': a, 'pos': a.pos}
    va = SC.virtualAnchor(P, c, e['seg'], e['u'])
    return {'side': e['side'], 'c': c, 'seg': e['seg'], 'u': e['u'], 'v': va, 'pos': va.pos, 'a': None}


class HAIRMESH_OT_wall_cut_apply(bpy.types.Operator):
    """Cut the wall along the cut placed on it: a new curve runs along the cut and the wall becomes two (Enter)"""
    bl_idname = 'hairmesh.wall_cut_apply'
    bl_label = 'Cut the Wall'
    bl_options = {'REGISTER', 'UNDO'}

    spec: StringProperty(name='Cut', description='Non-modal form: {"wall": id, "start": {"side": 0, "anchor": id} or {"side": 0, "seg": k, "u": x}, "pts": [[r, t], ...], "end": {...}} — r, t: wall coordinates', default='', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        why = not_ready(cut_of(context)) if not properties.spec else None
        return f'{cls.__doc__}. Now: {why}' if why else cls.__doc__

    def execute(self, context):
        root, P, _sel = select.current(context)
        if self.spec:
            d = json.loads(self.spec)
            CM.rebuild_dirty(P)
            cut = start_cut(context, root, P, d['wall'], self.report)
            if cut is None:
                return {'CANCELLED'}
            cut.start = _end_of(P, cut, d['start']); cut.end = _end_of(P, cut, d['end'])
            cut.pts = [{'r': r, 't': t} for r, t in d.get('pts') or []]
            return apply_cut(context, root, P, cut, self.report)
        cut = cut_of(context)
        why = not_ready(cut)
        if why:
            self.report({'INFO'}, why)
            return {'CANCELLED'}
        return apply_cut(context, root, cutting['P'], cut, self.report, keep=True)


class HAIRMESH_OT_wall_cut_remove(bpy.types.Operator):
    """Remove the last anchor of the cut being placed (Backspace)"""
    bl_idname = 'hairmesh.wall_cut_remove'
    bl_label = 'Remove the Last Anchor'
    bl_options = {'REGISTER'}

    def execute(self, context):
        cut = cut_of(context)
        if cut is None or not cut.start or cut.end:
            self.report({'INFO'}, 'Only while the cut is being placed (after its start, before its end)')
            return {'CANCELLED'}
        SC.removeLast(cut)
        view_cut(session.find_root(context), cutting['P'], cut)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_wall_cut_cancel(bpy.types.Operator):
    """Cancel the cut (Esc): nothing changes"""
    bl_idname = 'hairmesh.wall_cut_cancel'
    bl_label = 'Cancel the Wall Cut'
    bl_options = {'REGISTER'}

    def execute(self, context):
        end_cut()
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_set_cut_mode(bpy.types.Operator):
    """Set the Scissors mode: cut curves with a line, or cut a wall in two along a curve placed on it (with one wall selected, the cut starts on it)"""
    bl_idname = 'hairmesh.set_cut_mode'
    bl_label = 'Scissors Mode'
    bl_options = {'REGISTER'}

    mode: EnumProperty(name='Mode', items=CUT_MODES, default='CURVE')

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        hm = context.scene.hairmesh
        hm.subtool = 'SCISSORS'
        hm.cut_mode = self.mode
        if self.mode != 'SURFACE':
            end_cut()
        elif cut_of(context) is None:
            # a wall already selected — that one (8980)
            root, P, sel = select.current(context)
            walls = [i for i in sel.items if i in (P.items if P else {}) and P.items[i].type == 'surface'] if P else []
            if len(walls) == 1:
                win = context.window
                area = next((a for a in (win.screen.areas if win else ()) if a.type == 'VIEW_3D'), None)
                region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
                if region is not None:
                    with context.temp_override(window=win, area=area, region=region):
                        bpy.ops.hairmesh.wall_cut('INVOKE_DEFAULT', wall=walls[0])
        select.redraw(context)
        return {'FINISHED'}


def scissors_rows(context):
    """the Scissors' panel (8974-8997) as rows (pure): the two modes; while cutting, what is cut, Cut (dimmed with what
    is missing) and Cancel; the help of the mode (HELP_CURVE / HELP_SURFACE) is the "?" of the D's tab column and of
    the N panel's header (item 9 of the lot after 4.8: tool.how_to)"""
    hm = context.scene.hairmesh
    rows = [[('op', 'hairmesh.set_cut_mode', 'Curve', 'CURVE_DATA', {'mode': 'CURVE'}, {'depress': hm.cut_mode == 'CURVE'}),
             ('op', 'hairmesh.set_cut_mode', 'Wall', 'SURFACE_NSURFACE', {'mode': 'SURFACE'}, {'depress': hm.cut_mode == 'SURFACE'})]]
    if hm.cut_mode == 'CURVE':
        root, P, sel = select.current(context)
        sc = select.curves_of(P, sel) if P is not None else []
        rows.append([('text', f'To cut: {sc[0].name if len(sc) == 1 else f"{len(sc)} curves"}.' if sc else 'No curve picked yet.', 'INFO')])
        return rows
    cut = cut_of(context)
    rows.append([('text', f'Cutting: {cut.sf.name} ({cut.A.name} — {cut.B.name}).' if cut else 'Click the wall to cut.', 'INFO')])
    if cut is not None:
        why = not_ready(cut)
        label = f'Cut ({len(cut.build["mids"]) + 2} anchors)' if not why else 'Cut'
        rows.append([('op', 'hairmesh.wall_cut_apply', label, 'SCULPTMODE_HLT', {}, {'active': why is None}),
                     ('op', 'hairmesh.wall_cut_cancel', 'Cancel', 'X', {})])
        if why:
            rows.append([('text', why, 'BLANK1', {'active': False})])
    return rows


CLASSES = (HAIRMESH_MT_cut_pie, HAIRMESH_OT_cut_dismiss, HAIRMESH_OT_cut_curves, HAIRMESH_OT_scissors, HAIRMESH_OT_wall_cut,
           HAIRMESH_OT_wall_cut_apply, HAIRMESH_OT_wall_cut_remove, HAIRMESH_OT_wall_cut_cancel,
           HAIRMESH_OT_set_cut_mode)


@persistent
def _reset(*_a):
    """an undo, a redo, a file loaded: the stroke waiting for its pie and the cut in progress go (restore 1221)"""
    global pending
    pending = None
    end_cut()


_HANDLERS = ('undo_post', 'redo_post', 'load_post')


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for h in _HANDLERS:
        getattr(bpy.app.handlers, h).append(_reset)


def unregister():
    global pending
    pending = None
    end_cut()
    for h in _HANDLERS:
        lst = getattr(bpy.app.handlers, h)
        if _reset in lst:
            lst.remove(_reset)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
