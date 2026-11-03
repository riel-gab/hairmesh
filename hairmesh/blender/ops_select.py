# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The Select sub-tool (phase 3.3): what the original's pointer handlers do with the cursor tool
(hair-modeler.clean.html:5763-6054) as modal operators of the HairMesh tool, plus Backspace, B and C.

  LMB (hairmesh.select)         on an anchor: select it (Shift adds, Ctrl toggles) and drag the selected anchors on
                                the plane facing the camera through it (or, with the "Drag on Head" option of this
                                port, over the head surface under the cursor); Shift while dragging one anchor
                                slides it along the curve under the cursor (snapDragToCurve). On a curve: select it
                                (Shift toggles). Ctrl in the empty: paint the selection of the curves the stroke
                                crosses (a welded trunk goes together), under a blue disc that follows the cursor
                                (ours). Empty: a box over the anchors; a click clears. On a live wall (3.8d, ours: the
                                original selects it on the press and a drag does nothing): nothing yet — past Blender's
                                drag threshold the box starts, as from the empty space; released before, the wall is
                                selected.
  double LMB (hairmesh.select_curve_anchors)  on a curve (3.8d, ours: the original has no double click in Select): the
                                curve selected as an item becomes all its anchors (the last = the end nearest the click);
                                its anchors all selected become the curve again; Shift adds its anchors to the selection.
  RMB (hairmesh.insert_anchor)  on a curve: a new anchor at that point, into every curve welded along the segment,
                                dragged right away; elsewhere the event passes (Blender's context menu).
  B + LMB (hairmesh.smooth_drag) the smoothness of the selected anchors, 200 px = 100 %.
  Backspace (hairmesh.delete_selected)  the selected anchors (with or without keeping the shape), else the items;
  X (hairmesh.delete_ask, after a question) and Delete (hairmesh.delete_now) the same, contextual (4.4).
  C (hairmesh.select_circle)    the Circle Select of this port over anchors and curves.

The geometry is the core's (edit.py); the viewport only supplies rays, the camera direction and the projector, as the
Draw sub-tool does. Every operator has a non-modal form with properties for the tests (blender -b has no viewport):
anchor / curve / deselect_all, delta, curve + sample + t, value, keep_shape. Whoever changes the model ends with
session.save_project (one undo step, the original's commit) and live.update (the preview rebuilt inside the same undo
step; live.schedule, the debounced timer, only follows the moves of a drag)."""
import time
from math import ceil, hypot

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty

from ..core import crowntool as CT, curves as CM, edit, effects, weld as W
from ..core.crown import crownCurves
from ..core.model import Attachment
from ..core.selection import Selection
from ..core.vec3 import V3
from . import draw as drawmod, live, modal_keys, owner, pick, select, session
from .coords import f32
from .ops_draw import NAV_EVENTS, head_visible, model_cam, model_ray

PICK_ANCHOR_PX = 10                                                 # pickAnchor (5668)
PICK_CURVE_PX = 7                                                   # pickCurve (5675)
BOX_MIN_PX = 3                                                      # a marquee no bigger than this is a click (6050)
PAINT_STEP_PX = 4                                                   # paintSel samples the stroke every 4 px (5857)
PAINT_CURSOR_PX = PICK_CURVE_PX                                     # the blue disc of the paint stroke shows the pick radius (an addition of this port)
CIRCLE_STEP_PX = 5                                                  # the wheel changes the Circle Select radius by this
DRAG_HEADER = 'HairMesh: Moving Anchor(s)'
FOLLOW_HEADER = 'HairMesh: Following the Head'
NO_HEAD_HEADER = 'HairMesh: Moving Anchor(s), No Head Shown'
DRAG_KEYS = ((('Shift',), 'Slide Along Curve'), (('Alt',), 'Follow Head Surface'), (('Esc',), 'Cancel'))
NO_HEAD_KEYS = (DRAG_KEYS[0], DRAG_KEYS[2])                         # (Alt has no head to follow)
SLIDE_KEYS = ((('LMB Drag',), 'Slide Along the Connector'), (('Ctrl',), 'Click: Detach'), (('Esc',), 'Cancel'))
SMOOTH_KEYS = ((('B + LMB Drag',), 'Smoothness'), (('B',), 'Release: Confirm'))
CIRCLE_KEYS = ((('LMB',), 'Select'), (('Shift', 'MMB'), 'Deselect'), (('Wheel', '+', '-'), 'Radius'), (('RMB', 'Esc'), 'Exit'))
CURVE_CLICK = None                                                  # the first click of a double click on a curve (3.8d): {'curve', 'before', 'xy', 'root', 't'}
DOUBLE_PX = 6                                                       # the second click this near the first (Blender pairs presses close together)


def drag_threshold(context):
    """Blender's drag threshold for the mouse (Preferences > Input > Drag Threshold, in pixels, scaled with the UI as
    WM_event_drag_threshold does)"""
    try:
        px = context.preferences.inputs.drag_threshold_mouse
    except AttributeError:
        px = 3
    return px * pick.ui_scale(context)


def curve_anchor_ids(c, nearest_last=None):
    """the anchor ids of curve c, once each, the end nearest to `nearest_last` (an anchor) last"""
    ids = list(dict.fromkeys(a.id for a in c.anchors))
    if nearest_last is not None and ids and nearest_last == ids[0]:
        ids.reverse()
    return ids


def double_click_selection(before, c, near, extend):
    """the selection after a double click on curve c (3.8d): `before` = the selection before the first click, `near`
    = the id of the end anchor nearest the click; Shift (`extend`) adds the curve's anchors; else the curve's anchors,
    or, when they were all selected already, the curve itself"""
    ids = curve_anchor_ids(c, near)
    if extend:
        drop = set(ids)
        return Selection([], [*[a for a in before.anchors if a not in drop], *ids])
    if before.anchors and set(ids) <= set(before.anchors):
        return Selection([c.ring or c.id], [])
    return Selection([], ids)


class View:
    """What an operator needs of the 3D View it runs in: the root and its model, the projector (toScreen) and the
    ray / camera conversions in the model's space. View.of(context) is None outside a 3D View region or without a
    project."""

    def __init__(self, context, root):
        self.context = context
        self.root = root
        self.P = session.get_project(root)
        self.region = context.region
        self.rv3d = context.region_data
        self.project = pick.region_projector(self.region, self.rv3d, root.matrix_world)
        self.ui = pick.ui_scale(context)

    @classmethod
    def of(cls, context):
        if context.area is None or context.area.type != 'VIEW_3D' or context.region is None or context.region_data is None:
            return None
        root = session.find_root(context)
        if root is None:
            return None
        try:
            return cls(context, root)
        except Exception:
            return None

    def ray(self, event):
        return model_ray(self.root, self.region, self.rv3d, (event.mouse_region_x, event.mouse_region_y))

    def cam(self):
        return model_cam(self.root, self.rv3d)[0]

    def px(self, n):
        return n * self.ui

    def anchors(self, sel):
        """S.anchorList: the anchors shown and pickable (the anchors of the curves a group's mode hides go with them, 1381)"""
        return edit.anchorList(self.P, sel.items, sel.anchors, shown={c.id for c in self.curves()})

    def curves(self):
        """the curves pickCurve sees: hair curves that are visible and drawn (c.obj.visible, syncVisuals 11952):
        with 2+ anchors, shown by their group's mode (groups.shownCurves; a selected group is part of S.sel since
        15/10/2026, U1; the curve an anchor slides along shows anyway)"""
        from ..core import groups as G
        sel = select.load(self.context.scene.hairmesh)
        return G.shownCurves(self.P, sel.items, sel.anchors, None, None, drawmod.snap_curve)[0]

    def occluder(self):
        """`occluded(point)` for snap_drag_to_curve: the head (as the original raycasts it) hides the point when its
        hit on the ray from the camera through the point's pixel comes first (5729-5733); None without a head"""
        P = self.P
        if P.head is None or not P.head.visible or not head_visible(self.root) or edit.head_trimesh(P) is None:
            return None
        if pick.blender_xray(self.context):                         # Blender's X-ray (3.8c): the head hides nothing
            return None

        def occluded(point):
            s = self.project(point)
            if s is None:
                return False
            ray = model_ray(self.root, self.region, self.rv3d, (s[0], s[1]))
            hit = edit.pickHead(P, ray)
            return hit is not None and hit['distance'] < ray.origin.distanceTo(point) - 0.002 * P.S.scale
        return occluded


def _finish_edit(context, root, P, report=None, msg=None):
    session.save_project(root, P)                                   # the original's commit(): one undo step
    live.update(context, root)                                      # the preview inside the operator's own undo step
    select.redraw(context)
    if report is not None and msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


class _AnchorDragMixin:
    """The anchor drag (startAnchorDrag 5756, the 'anchor' case of pointermove 5912-5922 and pointerup 5996-6000),
    shared by Select (LMB on an anchor) and Insert Anchor (RMB on a curve). While one anchor is dragged the anchor
    it would weld into is found (findWeldCandidate, magenta); on release checkWeld opens the weld pie (ops_weld),
    unless the drag was a slide or a cancel."""

    def _drag_start(self, context, event, anchors, ref):
        v = self.view; P = v.P
        self.drag = edit.startAnchorDrag(anchors, v.ray(event), v.cam(), ref)
        self.starts0 = [a.pos.clone() for a in anchors]             # for Esc (the snap release rewrites drag.starts)
        self.weld_pool = None                                       # (S.anchorList for findWeldCandidate, kept through the drag: _find_weld)
        self.sync0 = CT.syncSnapshot(P)                             # (4.3: what the crown's frame step moves during the drag — the pairs, the attached anchors)
        self.occluded = v.occluder()
        # the "Drag on Head" option (ours): only with a head that is shown, as the Draw tool's placePoint sees it
        self.on_head = bool(context.scene.hairmesh.drag_on_head and P.head is not None and P.head.visible and head_visible(v.root)
                            and edit.head_trimesh(P) is not None)
        self.nearest = None                                         # (lot 4.7b item 6: the head's BVH at the first Alt)
        self.head_ok = P.head is not None and P.head.visible and head_visible(v.root)
        context.window_manager.modal_handler_add(self)
        live.header(context.area, DRAG_HEADER, DRAG_KEYS if self.head_ok else NO_HEAD_KEYS)
        return {'RUNNING_MODAL'}

    def _drag_move(self, context, event, alt=None):
        v = self.view; P = v.P; d = self.drag; ray = v.ray(event)
        alt = event.alt if alt is None else alt
        if alt and getattr(self, 'nearest', None) is None and getattr(self, 'head_ok', False):
            self.nearest = pick.head_nearest(P)
        if event.shift and len(d.list) == 1:
            r = pick.snap_drag_to_curve(P, d.list[0], v.project, event.mouse_region_x, event.mouse_region_y, d.snap, self.occluded)
            if r is not None:
                c, point = r
                if c.id != d.snap:
                    live.header(context.area, f'HairMesh: Sliding Along {c.name}')
                    drawmod.snap_curve = c.id
                edit.dragSnap(P, d, c, point)
                drawmod.weld_cand = None                            # (5912: no weld candidate while sliding — none is looked for)
                self._after_move(context, weld=False)
                return
        if d.snap is not None:                                      # Shift released: free again from here, no jump
            edit.dragSnapRelease(d, ray); drawmod.snap_curve = None
            live.header(context.area, DRAG_HEADER)
        # lot 4.7b item 6 (ours, the owner's request): Alt, read during the drag (an Alt + click is Blender's), slides the
        # anchors over the head, each keeping the distance it had when Alt went down; let go: free again from there
        follow = alt and getattr(self, 'nearest', None) is not None
        if follow and d.follow is None:
            d.follow = edit.surfaceDists(d.list, self.nearest)
            live.header(context.area, FOLLOW_HEADER)
        elif not follow and d.follow is not None:
            edit.dragRebase(d, ray); d.follow = None                # (no jump: the plane drag from where they are)
            live.header(context.area, DRAG_HEADER)
        elif alt and getattr(self, 'nearest', None) is None:
            live.header(context.area, NO_HEAD_HEADER)
        if follow:
            if d.on_head:
                edit.dragRebase(d, ray)                             # ("Drag on Head" gives way while Alt is down)
            edit.dragAnchors(P, d, ray)
            edit.followSurface(P, d.list, d.follow, self.nearest)
            self._after_move(context)
            return
        if self.on_head:
            if edit.dragOnHead(P, d, ray):                          # over the head: the anchors sit on it
                self._after_move(context)
                return
            if d.on_head:                                           # off the head: back to the view plane from where they are, no jump
                edit.dragRebase(d, ray)
        edit.dragAnchors(P, d, ray)
        self._after_move(context)

    def _after_move(self, context, weld=True):
        if weld:
            self._find_weld(context)
        from .ops_gizmo import attach_preview
        attach_preview(self.view, self.view.P, self.drag.list)      # (4.4: every crown's connectors and the Attach dot)
        live.schedule(self.view.root.name)                          # the live walls follow, at most every 0.25 s (D4)
        context.area.tag_redraw()

    def _find_weld(self, context):
        # S.weldCand = drag.list.length === 1 ? findWeldCandidate(drag.list[0]) : null (5921)
        v = self.view; d = self.drag
        cand = None
        if len(d.list) == 1:
            pool = getattr(self, 'weld_pool', None)
            if pool is None or pool[0] != drawmod.snap_curve:       # (no item, selection or group mode changes in a drag: the anchors shown stay, lot 4.7b item 5)
                sel = select.load(context.scene.hairmesh).prune(v.P)
                pool = self.weld_pool = (drawmod.snap_curve, v.anchors(sel))
            cand = W.findWeldCandidate(v.P, d.list[0], pool[1], v.project)
        drawmod.weld_cand = cand.id if cand is not None else None
        return cand

    def _drag_end(self, context, cancel=False):
        d = self.drag; v = self.view; P = v.P
        from .ops_gizmo import attach_drag_end
        attach_drag_end()
        drawmod.snap_curve = None; drawmod.weld_cand = None
        live.header(context.area, None)
        if cancel:
            # Blender's Esc convention (the original has no cancel: a release always commits): back where it started,
            # and an anchor the right button had just inserted goes away again
            if self._drag_restore():
                select.store(context.scene.hairmesh, Selection())
            live.update(context, v.root, commit=False); context.area.tag_redraw()   # back to the preview of the last step, no timer left behind (nothing written: 3.7)
            return {'CANCELLED'}
        if d.moved or d.inserted or getattr(self, 'conn_insert', None) is not None:
            session.save_project(v.root, P); live.update(context, v.root)
        else:
            live.cancel(v.root.name)                                # a drag that moved nothing: no timer may fire after the operator
        context.area.tag_redraw()
        if d.moved and len(d.list) == 1:
            self._check_weld(context)                               # checkWeld (5998): the pie when another anchor is within 14 px
        return {'FINISHED'}

    def _drag_restore(self):
        """the model as the press found it (Esc, an error, Blender ending the modal): the anchors where they started, the
        crown's frame step undone, the press's insertion into a connector and an anchor the right button inserted
        gone. In memory only. Returns whether an inserted anchor went (its selection goes too)."""
        d = self.drag; P = self.view.P
        for a, s in zip(d.list, self.starts0):
            a.pos.copy(s)
        CT.syncRestore(P, self.sync0)
        ins = getattr(self, 'conn_insert', None)
        if ins is not None:                                         # (the insertion into the connector the press made goes too)
            h, a, pos, att, attPos = ins
            h.anchors = [x for x in h.anchors if x is not a]; a.pos.copy(pos); a.att = Attachment(c=att[0], t=att[1]); a.attPos = attPos
            CM.markAnchorsDirty(P, [a, *h.anchors]); P.gen += 1
        gone = False
        if d.inserted:
            a = d.list[0]
            for c in edit.curvesWith(P, a):
                edit.removeAnchorFromCurve(P, c, a, False)
            P.gen += 1; gone = True
        CM.markAnchorsDirty(P, d.list); P.rev += 1
        return gone

    def _check_attach(self, context):
        """checkAttach (7406): an anchor END dragged near a crown connector (and not near another anchor): the Attach pie"""
        from .ops_gizmo import check_attach
        return check_attach(self.view, self.view.P, self.drag.list[0])

    def _check_weld(self, context):
        from . import ops_weld
        v = self.view; a = self.drag.list[0]
        sel = select.load(context.scene.hairmesh).prune(v.P)
        b = W.findWeldCandidate(v.P, a, v.anchors(sel), v.project, )
        if b is None:
            ops_weld.pending = None
            return self._check_attach(context)                      # (checkWeld 4878: no weld candidate -> checkAttach)
        ops_weld.ask_weld(a, b, v.P)
        bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name=ops_weld.HAIRMESH_MT_weld_pie.bl_idname)
        return True


class HAIRMESH_OT_select(_AnchorDragMixin, bpy.types.Operator):
    """Select anchors and curves of the HairMesh project: click an anchor (Shift adds, Ctrl toggles) and drag it; click a curve, a wall or a converted mesh (Shift adds); Ctrl + drag over curves paints their selection; drag in the empty space boxes anchors; a click in the empty space clears"""
    bl_idname = 'hairmesh.select'
    bl_label = 'HairMesh Select'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', description='Non-modal form: the anchor id clicked (with Toggle on a welded anchor of a selected curve: unweld, as Ctrl + click does)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    curve: IntProperty(name='Curve', description='Non-modal form: the curve id clicked', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    wall: IntProperty(name='Wall', description='Non-modal form: the wall (surface item) id clicked', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    item: IntProperty(name='Item', description='Any item by id (a group row of the HairMesh Outliner, a curve, a wall): selectItem of the original', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    extend: BoolProperty(name='Extend', description='Shift: add to the selection (a curve or wall: toggle it)', default=False, options={'SKIP_SAVE'})
    toggle: BoolProperty(name='Toggle', description='Ctrl: toggle the anchor', default=False, options={'SKIP_SAVE'})
    deselect_all: BoolProperty(name='Deselect All', description='Non-modal form: clear the selection (a click in the empty space)', default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    # ---- modal (the viewport) ----
    def invoke(self, context, event):
        global CURVE_CLICK
        if any(self.properties.is_property_set(k) for k in ('anchor', 'curve', 'wall', 'item', 'deselect_all')):
            return self.execute(context)                            # (lot 4.3, item 1: a button, a pie or a menu item that names what to select is the non-modal form — invoked from one, the click's modal started a box at the cursor)
        CURVE_CLICK = None                                          # (any press that is not a click on a curve ends a double click)
        hm = context.scene.hairmesh
        if hm.subtool not in ('SELECT', 'CROWN') or event.alt or event.oskey:
            return {'PASS_THROUGH'}                                 # Draw / Scissors own the click; Alt+LMB is Blender's (emulated 3-button mouse)
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        self.view = v; self.mode = None; self.drag = None; self.from_wall = False; self.guide_hit = None
        x, y = event.mouse_region_x, event.mouse_region_y
        sel = select.load(hm).prune(v.P)
        a = pick.pick_anchor(v.anchors(sel), v.project, x, y, v.px(PICK_ANCHOR_PX))
        if a is None and hm.subtool == 'CROWN' and not event.ctrl:
            return {'PASS_THROUGH'}                                 # (4.3: in Crown an anchor, or Ctrl + drag painting curves (5841), is the Select's; elsewhere hairmesh.crown_tool)
        if a is not None:
            on = CT.slideTarget(v.P, a) if event.ctrl else None
            if on is not None and len(on.samples) > 1:
                # Ctrl + drag on an anchor sitting on a crown connector: it slides along the connector; a Ctrl click
                # detaches it, else as before (5824-5826, 5988-5994)
                self.mode = 'slide'; self.slide = {'a': a, 'poly': [s.p.clone() for s in on.samples], 'moved': False}
                self.slide0 = (CT.syncSnapshot(v.P), a.pos.clone(), (a.att.c, a.att.t) if a.att is not None else None, a.attPos.clone() if a.attPos is not None else None)
                self.sel = sel
                live.hold(v.root.name)                              # (4.4, 2a: the walls follow at the release, as the sliders of 4.2c)
                context.window_manager.modal_handler_add(self)
                live.header(context.area, 'HairMesh: Sliding the Anchor Along the Connector', SLIDE_KEYS)
                return {'RUNNING_MODAL'}
            self.conn_insert = None
            if not event.ctrl and not event.shift and a.att is not None:
                # an old-style attached anchor of a lock curve (not a crown curve): a plain drag makes it an anchor of its
                # connector first — the drag then reshapes the connector instead of tearing the anchor off it (5829-5832;
                # ours: in memory, saved by the drag's release in the same undo step, taken back by its Esc)
                h = v.P.items.get(a.att.c)
                crownC = {r['c'].id for k in v.P.crowns() for r in crownCurves(v.P, k)}
                if h is not None and h.type == 'curve' and h.conn and not any(c.id in crownC for c in edit.curvesWith(v.P, a)):
                    self.conn_insert = (h, a, a.pos.clone(), (a.att.c, a.att.t), a.attPos.clone() if a.attPos is not None else None)
                    CT.insertIntoConnector(v.P, h, a); live.touch(v.root, [c.id for c in edit.curvesWith(v.P, a)])   # (S.surfDirty, 7444)
            if event.ctrl and W.unweldAnchor(v.P, a, sel.items) is not None:   # Ctrl + click on a welded anchor of a selected curve (5826): it comes apart
                _finish_edit(context, v.root, v.P, self.report, f'Anchor unwelded ({a.id})')
                return {'FINISHED'}
            selected = sel.click_anchor(a.id, event.shift, event.ctrl)
            select.store(hm, sel)
            if not selected:
                context.area.tag_redraw()
                return {'FINISHED'}
            self.mode = 'drag'
            return self._drag_start(context, event, select.anchors_of(v.P, sel), a.pos)
        if event.ctrl:
            # Ctrl + drag over curves: select them, or, if the first touched curve was selected, deselect (5839-5841)
            sel.anchors = []; self.sel = sel; self.mode = 'paint'; self.paint_mode = None; self.last = None
            select.store(hm, sel)
            drawmod.overlay = ('paint', x, y, v.px(PAINT_CURSOR_PX))   # the blue disc under the cursor while the stroke paints
            self._paint(context, x, y)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        ch = pick.pick_curve(v.curves(), v.project, x, y, v.px(PICK_CURVE_PX))
        if ch is not None and pick.tube_over_curve(context, v.root, v.P, ch, x, y, v.project, v.px(pick.TUBE_CURVE_PX)) is not None:
            ch = None                                               # (lot 4.7b, item 3: its Profile modifier's surface — item_under below takes it)
        if ch is not None:
            CURVE_CLICK = {'curve': ch['curve'].id, 'before': sel.copy(), 'xy': (x, y), 'root': v.root.name, 't': time.monotonic()}   # (a double click may follow: hairmesh.select_curve_anchors)
            sel.select_item(ch['curve'].ring or ch['curve'].id, event.shift)
            select.store(hm, sel); context.area.tag_redraw()
            return {'FINISHED'}
        from . import ops_guides
        got = ops_guides.click_on_guide(context, v, sel, x, y, event.shift, surface=False)   # (4.2c, item 5, ours: a ring or a guide line, as in Guides)
        w = pick.item_under(context, v.root, x, y) if got is None else None
        self.sel = sel; self.box0 = (x, y); self.add = event.shift
        if got is not None or w is not None:
            # a click on a live wall selects it (pickObjects, 5849; 3.5) — on the release (3.8d, ours: the original selects
            # it on the press and a drag does nothing): past Blender's drag threshold the box starts, as from the empty.
            # A converted mesh in front the same way (4.2 ∥ 4.5, item 1, ours): the add-on's selection, never Blender's
            # (4.2c: a ring or a guide line the same way — selected on the release, a drag from it boxes)
            self.mode = 'wall'; self.wall_hit = w; self.guide_hit = got; self.threshold = drag_threshold(context)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        self.mode = 'box'
        drawmod.overlay = ('box', x, y, x, y)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:                                           # (4.4, the review: an error mid-slide or mid-drag must not keep the root held)
            self.cancel(context)
            raise

    def cancel(self, context):
        # Blender ends the modal itself (a window closed, a file loaded) or an error did: the hold off, the drawing
        # state cleared, no timer left behind — no area to talk to, no datablock written
        from .ops_gizmo import attach_drag_end
        modal_keys.clear(context)
        attach_drag_end(); drawmod.overlay = None; drawmod.snap_curve = None; drawmod.weld_cand = None
        v = getattr(self, 'view', None)
        if v is None:
            return
        live.hold(v.root.name, False); live.cancel(v.root.name)
        try:                                                        # (the model as the press found it: the next operator writes no half drag)
            mode = getattr(self, 'mode', None)
            if mode == 'slide':
                self._slide_restore()
            elif mode == 'drag':
                self._drag_restore()
        except Exception:
            pass
        try:
            live.header(context.area, None)
        except Exception:
            pass

    def _modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            x, y = event.mouse_region_x, event.mouse_region_y
            if self.mode == 'slide':
                if CT.slideStep(self.view.P, self.slide, (x, y), lambda p, f=self.view.project: f(p) or (0.0, 0.0, 2.0)):   # (slideStep 7447 on the connector as the drag began; behind the view: skipped, as z > 1)
                    self.view.P.rev += 1                            # (4.4, 2a: the anchor and its curve drawn at every step, as slideStep 7447)
                    live.schedule(self.view.root.name)              # (its frame step too: live._drag_sync; the root held: the locks at the release)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            if self.mode == 'drag':
                self._drag_move(context, event)
            elif self.mode == 'paint':
                drawmod.overlay = ('paint', x, y, self.view.px(PAINT_CURSOR_PX)); context.area.tag_redraw()
                self._paint(context, x, y)
            elif self.mode == 'wall':
                if abs(x - self.box0[0]) > self.threshold or abs(y - self.box0[1]) > self.threshold:   # (WM_event_drag_test)
                    self.mode = 'box'; self.from_wall = True
                    drawmod.overlay = ('box', self.box0[0], self.box0[1], x, y); context.area.tag_redraw()
            else:
                drawmod.overlay = ('box', self.box0[0], self.box0[1], x, y); context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return self._release(context, event)
        if event.type in {'LEFT_ALT', 'RIGHT_ALT'} and self.mode == 'drag' and event.value in {'PRESS', 'RELEASE'}:
            self._drag_move(context, event, alt=event.value == 'PRESS')   # (lot 4.7b item 6: at once, not at the next move)
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _release(self, context, event):
        hm = context.scene.hairmesh
        if self.mode == 'slide':
            return self._slide_end(context)
        if self.mode == 'drag':
            return self._drag_end(context)
        drawmod.overlay = None
        if self.mode == 'wall':                                     # released before the drag threshold: the wall
            self._pick_hit()
            select.store(hm, self.sel); context.area.tag_redraw()
            return {'FINISHED'}
        if self.mode == 'paint':
            context.area.tag_redraw()
            return {'FINISHED'}
        x0, y0 = self.box0; x1, y1 = event.mouse_region_x, event.mouse_region_y
        bx0, bx1 = min(x0, x1), max(x0, x1); by0, by1 = min(y0, y1), max(y0, y1)
        if self.from_wall and not (bx1 - bx0 > BOX_MIN_PX or by1 - by0 > BOX_MIN_PX):
            self._pick_hit()                                        # (back near the press: a click on the wall, never a cleared selection)
            select.store(hm, self.sel); context.area.tag_redraw()
            return {'FINISHED'}
        if not (bx1 - bx0 > BOX_MIN_PX or by1 - by0 > BOX_MIN_PX) and not self.from_wall:
            from . import ops_guides                                # (4.2c: a click on a disc's band — behind the walls and meshes; a drag from it still boxes)
            disc = ops_guides.click_on_guide(context, self.view, self.sel, x1, y1, self.add, lines=False)
            if disc is not None:
                select.store(hm, disc); context.area.tag_redraw()
                return {'FINISHED'}
        inside = pick.anchors_in_box(self.view.anchors(self.sel), self.view.project, bx0, by0, bx1, by1) if (bx1 - bx0 > BOX_MIN_PX or by1 - by0 > BOX_MIN_PX) else []
        self.sel.box([a.id for a in inside], self.add)
        select.store(hm, self.sel); context.area.tag_redraw()
        return {'FINISHED'}

    def _pick_hit(self):
        """the click of the press: the ring or guide line under it (4.2c), else the wall or mesh"""
        if self.guide_hit is not None:
            self.sel = self.guide_hit
        else:
            self.sel.select_item(self.wall_hit, self.add)

    def _paint(self, context, x, y):
        # port of paintSel (5856): the stroke is sampled every 4 px from the last point; the curves welded along the
        # touched segment (a Y trunk) lie on top of each other, they all go together
        v = self.view; frm = self.last or (x, y)
        n = max(1, ceil(hypot(x - frm[0], y - frm[1]) / PAINT_STEP_PX))
        changed = False
        curves = v.curves()
        for i in range(1, n + 1):
            ch = pick.pick_curve(curves, v.project, frm[0] + (x - frm[0]) * i / n, frm[1] + (y - frm[1]) * i / n, v.px(PICK_CURVE_PX))
            if ch is None:
                continue
            if self.paint_mode is None:
                self.paint_mode = self.sel.paint_mode(ch['curve'].id)
            if self.sel.paint(edit.curvesOnSegment(v.P, ch['curve'], ch['seg']), self.paint_mode):
                changed = True
        self.last = (x, y)
        if changed:
            select.store(context.scene.hairmesh, self.sel); context.area.tag_redraw()

    def _slide_restore(self):
        """the slid anchor and the crown's frame step as the press found them (Esc, an error); in memory only"""
        P = self.view.P; a = self.slide['a']
        snap, pos, att, attPos = self.slide0
        CT.syncRestore(P, snap)
        a.pos.copy(pos); a.att = Attachment(c=att[0], t=att[1]) if att is not None else None; a.attPos = attPos   # (the slid anchor itself: a connector whose crown was deleted is no crown's)
        CM.markAnchorsDirty(P, [a]); P.rev += 1

    def _slide_end(self, context, cancel=False):
        v = self.view; P = v.P; a = self.slide['a']
        live.hold(v.root.name, False)
        live.header(context.area, None)
        if cancel:
            self._slide_restore()
            live.update(context, v.root, commit=False); context.area.tag_redraw()
            return {'CANCELLED'}
        if self.slide['moved']:
            return _finish_edit(context, v.root, P, None, None)     # (5989: commit)
        sel = self.sel                                              # no move: a Ctrl click — detach from the connector, else as before
        before = [c.id for c in edit.curvesWith(P, a)]
        msg = CT.detachFromConnector(P, sel, a)
        if msg is not None:
            select.store(context.scene.hairmesh, sel); live.touch(v.root, before)
            return _finish_edit(context, v.root, P, self.report, msg)
        if W.unweldAnchor(P, a, sel.items) is not None:
            return _finish_edit(context, v.root, P, self.report, f'Anchor unwelded ({a.id})')
        sel.click_anchor(a.id, False, True)                         # (a Ctrl click toggles the anchor)
        select.store(context.scene.hairmesh, sel); context.area.tag_redraw()
        return {'FINISHED'}

    def _cancel(self, context):
        drawmod.overlay = None
        if self.mode == 'slide':
            return self._slide_end(context, cancel=True)
        if self.mode == 'drag':
            return self._drag_end(context, cancel=True)
        context.area.tag_redraw()
        return {'CANCELLED'}

    # ---- non-modal (tests, scripts): a click without a viewport ----
    def execute(self, context):
        root, P, sel = select.current(context)
        hm = context.scene.hairmesh
        if self.anchor >= 0:
            if self.anchor not in P.anchors:
                self.report({'ERROR'}, f'No anchor {self.anchor} in the project')
                return {'CANCELLED'}
            a = P.anchors[self.anchor]
            if self.toggle and W.unweldAnchor(P, a, sel.items) is not None:
                return _finish_edit(context, root, P, self.report, f'Anchor unwelded ({a.id})')
            sel.click_anchor(self.anchor, self.extend, self.toggle)
        elif self.curve >= 0:
            it = P.items.get(self.curve)
            if it is None or it.type != 'curve':
                self.report({'ERROR'}, f'No curve {self.curve} in the project')
                return {'CANCELLED'}
            sel.select_item(it.ring or it.id, self.extend)
        elif self.wall >= 0:
            it = P.items.get(self.wall)
            if it is None or it.type != 'surface':
                self.report({'ERROR'}, f'No wall {self.wall} in the project')
                return {'CANCELLED'}
            sel.select_item(it.id, self.extend)
        elif self.item >= 0:                                        # the outliner's click (selectItem, 7844; Shift adds)
            it = P.items.get(self.item)
            if it is None or it.type == 'head':
                self.report({'ERROR'}, f'No item {self.item} in the project')
                return {'CANCELLED'}
            sel.select_item((it.ring or it.id) if it.type == 'curve' else it.id, self.extend)
        elif self.deselect_all:
            sel.box([], False)
        else:
            self.report({'ERROR'}, 'Click in the 3D View with the Select sub-tool, or pass anchor / curve / wall / item / deselect_all')
            return {'CANCELLED'}
        select.store(hm, sel); select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_deselect_all(bpy.types.Operator):
    """Clear the HairMesh selection (Alt+A with the tool active; with nothing of the HairMesh selected the key is Blender's)"""
    bl_idname = 'hairmesh.deselect_all'
    bl_label = 'HairMesh Deselect All'
    bl_options = {'REGISTER', 'UNDO'}

    # lot 4.3, item 1 (the owner's request): Alt+A contextual like G / R / S — the poll fails with nothing of the
    # HairMesh selected or the tool off, and the key goes on to Blender's (object.select_all DESELECT)
    @classmethod
    def poll(cls, context):
        from . import tool
        if not (owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None):
            return False
        if context.area is not None and context.area.type == 'VIEW_3D' and not tool.is_active(context):
            return False
        if not select.has_any(context.scene.hairmesh):
            cls.poll_message_set('Nothing of the HairMesh is selected')
            return False
        _r, _P, sel = select.current(context)
        if not (sel.items or sel.anchors):                          # (pruned, 4.4: ids of items gone never keep the key from Blender)
            cls.poll_message_set('Nothing of the HairMesh is selected')
            return False
        return True

    def execute(self, context):
        _root, _P, sel = select.current(context)
        sel.box([], False)
        select.store(context.scene.hairmesh, sel); select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_select_curve_anchors(bpy.types.Operator):
    """Double click a curve: select all its anchors (the last one the end nearest the click); its anchors all selected: back to the curve. Shift + double click adds its anchors to the selection"""
    bl_idname = 'hairmesh.select_curve_anchors'
    bl_label = 'HairMesh Curve Anchors (Double Click)'
    bl_options = {'REGISTER', 'UNDO'}

    curve: IntProperty(name='Curve', description='Non-modal form: the curve id (the selection before the double click: the current one)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    near_start: BoolProperty(name='Near Start', description='Non-modal form: the click was nearer the first anchor (it is selected last)', default=False, options={'HIDDEN', 'SKIP_SAVE'})
    extend: BoolProperty(name='Extend', description="Shift: add the curve's anchors to the selection", default=False, options={'HIDDEN', 'SKIP_SAVE'})   # (all hidden: the redo panel would run execute with the selection after the click as the one before)

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        global CURVE_CLICK
        hm = context.scene.hairmesh
        first = CURVE_CLICK; CURVE_CLICK = None
        if hm.subtool != 'SELECT' or event.alt or event.oskey or first is None:
            return {'PASS_THROUGH'}                                 # (not after a click on a curve: Blender sends the press again, hairmesh.select takes it)
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        x, y = event.mouse_region_x, event.mouse_region_y
        late = max(1.0, 2 * context.preferences.inputs.mouse_double_click_time / 1000)
        near = max(v.px(DOUBLE_PX), drag_threshold(context))        # (Blender pairs the presses within its drag threshold)
        if (first['root'] != v.root.name or time.monotonic() - first['t'] > late
                or abs(first['xy'][0] - x) > near or abs(first['xy'][1] - y) > near):
            return {'PASS_THROUGH'}                                 # (another project, a stale click, or not the same spot)
        ch = pick.pick_curve(v.curves(), v.project, x, y, v.px(PICK_CURVE_PX))
        if ch is None or ch['curve'].id != first['curve']:
            return {'PASS_THROUGH'}
        c = ch['curve']; A = c.anchors
        s0 = v.project(A[0].pos); s1 = v.project(A[len(A) - 1].pos)
        near = A[0].id if (s0 is not None and (s1 is None or hypot(s0[0] - x, s0[1] - y) < hypot(s1[0] - x, s1[1] - y))) else A[len(A) - 1].id
        return self._apply(context, first['before'].prune(v.P), c, near, event.shift)

    def _apply(self, context, before, c, near, extend):
        sel = double_click_selection(before, c, near, extend)
        select.store(context.scene.hairmesh, sel); select.redraw(context)
        n = len(sel.anchors)
        self.report({'INFO'}, f'{c.name}: {n} anchor{"s" if n != 1 else ""} selected' if n else f'{c.name} selected')
        return {'FINISHED'}

    def execute(self, context):
        root, P, sel = select.current(context)
        if self.curve < 0:                                          # (Shift+R, Repeat Last: a double click is not repeated)
            self.report({'INFO'}, 'Double click a curve in the viewport (Select): its anchors')
            return {'CANCELLED'}
        c = P.items.get(self.curve) if P is not None else None
        if c is None or c.type != 'curve' or not c.anchors:
            self.report({'ERROR'}, f'No curve {self.curve} in the project')
            return {'CANCELLED'}
        A = c.anchors
        return self._apply(context, sel, c, A[0].id if self.near_start else A[len(A) - 1].id, self.extend)


class HAIRMESH_OT_move_anchors(bpy.types.Operator):
    """Move the selected anchors by a vector in the model's space (the drag without a viewport: tests, scripts)"""
    bl_idname = 'hairmesh.move_anchors'
    bl_label = 'Move HairMesh Anchors'
    bl_options = {'REGISTER', 'UNDO'}

    delta: FloatVectorProperty(name='Delta', description='The move, in the model space (Y up)', size=3, default=(0.0, 0.0, 0.0))

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    def execute(self, context):
        root, P, sel = select.current(context)
        anchors = select.anchors_of(P, sel)
        if not anchors:
            self.report({'INFO'}, 'No anchors selected')
            return {'CANCELLED'}
        edit.moveAnchors(P, anchors, V3(*(f32(d) for d in self.delta)))   # operator floats are float32: the short decimals given
        return _finish_edit(context, root, P)


class HAIRMESH_OT_insert_anchor(_AnchorDragMixin, bpy.types.Operator):
    """Insert an anchor where the right button hits a curve and drag it; curves welded along that segment get it too"""
    bl_idname = 'hairmesh.insert_anchor'
    bl_label = 'Insert HairMesh Anchor'
    bl_options = {'REGISTER', 'UNDO'}

    curve: IntProperty(name='Curve', description='Non-modal form: the curve id', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    sample: IntProperty(name='Sample', description='Non-modal form: the index of the sample the point lies after', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    t: FloatProperty(name='t', description='Non-modal form: the fraction between that sample and the next', default=0.5, min=0.0, max=1.0, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        hm = context.scene.hairmesh
        if hm.subtool == 'SCISSORS' and hm.cut_mode == 'CURVE' and not (event.shift or event.ctrl or event.alt or event.oskey):
            from .ops_scissors import pick_curve_rmb                # the Scissors: RMB picks the curve to cut (5780; Shift+RMB stays the pie)
            v = View.of(context)
            return {'FINISHED'} if v is not None and pick_curve_rmb(context, event, v) else {'PASS_THROUGH'}
        if hm.subtool in ('DRAW', 'SCISSORS') or event.shift or event.ctrl or event.alt or event.oskey:
            return {'PASS_THROUGH'}                                 # no insert in the Draw tool (5783) nor in the Scissors; Shift+RMB is the pie
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        x, y = event.mouse_region_x, event.mouse_region_y
        ch = pick.pick_curve(v.curves(), v.project, x, y, v.px(PICK_CURVE_PX))
        if ch is None:
            return {'PASS_THROUGH'}                                 # (two or more curves selected: the walls pie, step 3.5) Blender's context menu meanwhile
        self.view = v
        a = edit.insertAnchorAt(v.P, ch['curve'], ch['seg'], ch['point'])
        select.store(hm, Selection([], [a.id]))                     # S.asel = [a]; S.sel.clear() (5786)
        res = self._drag_start(context, event, [a], a.pos)
        self.drag.inserted = True
        return res

    def cancel(self, context):
        # Blender ends the modal itself (a window closed, a file loaded): as Select's drag, the model as the press found
        # it, the inserted anchor gone, no timer left behind — no area to talk to, no datablock written
        from .ops_gizmo import attach_drag_end
        modal_keys.clear(context)
        attach_drag_end(); drawmod.snap_curve = None; drawmod.weld_cand = None
        try:
            live.hold(self.view.root.name, False); live.cancel(self.view.root.name)
            self._drag_restore()
        except Exception:
            pass

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self._drag_move(context, event)
            return {'RUNNING_MODAL'}
        if event.type == 'RIGHTMOUSE' and event.value == 'RELEASE':
            return self._drag_end(context)
        if event.type == 'ESC' and event.value == 'PRESS':
            return self._drag_end(context, cancel=True)
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root, P, sel = select.current(context)
        c = P.items.get(self.curve)
        if c is None or c.type != 'curve':
            self.report({'ERROR'}, 'Right-click a curve in the 3D View, or pass curve, sample and t')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        if not (0 <= self.sample < len(c.samples) - 1):
            self.report({'ERROR'}, f'sample must be in 0..{len(c.samples) - 2}')
            return {'CANCELLED'}
        s0 = c.samples[self.sample]; s1 = c.samples[self.sample + 1]
        a = edit.insertAnchorAt(P, c, s0.seg, s0.p.clone().lerp(s1.p, f32(self.t)))
        select.store(context.scene.hairmesh, Selection([], [a.id]))
        return _finish_edit(context, root, P, self.report, f'HairMesh: anchor inserted in {c.name}')


class HAIRMESH_OT_smooth_drag(bpy.types.Operator):
    """Set the smoothness of the selected anchors: hold B and drag with the left button, 200 px = 100 % (the header shows the value); release B to finish"""
    bl_idname = 'hairmesh.smooth_drag'
    bl_label = 'HairMesh Anchor Smoothness'
    bl_options = {'REGISTER', 'UNDO'}

    value: FloatProperty(name='Smoothness', description='Non-modal form: set this smoothness on the selected anchors', default=-1.0, min=-1.0, max=1.0, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    def invoke(self, context, event):
        root, P, sel = select.current(context)
        anchors = select.anchors_of(P, sel)
        if not anchors or context.area is None or context.area.type != 'VIEW_3D':
            self.report({'INFO'}, 'Select anchors first: B + a left drag changes their smoothness')
            return {'CANCELLED'}                                    # (handled: B does not reach Blender's box select while the tool is active)
        self.root, self.P, self.anchors = root, P, anchors
        self.starts = [a.smooth for a in anchors]; self.x0 = None; self.changed = False
        self._hud(context, anchors[len(anchors) - 1].smooth)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _hud(self, context, v):
        live.header(context.area, f'Smoothness: {round(v * 100)}%', SMOOTH_KEYS)

    def cancel(self, context):
        # Blender ends the modal itself (a window closed, a file loaded): the smoothness as it was, no timer left behind
        modal_keys.clear(context)
        try:
            live.cancel(self.root.name)
            edit.smoothDrag(self.P, self.anchors, self.starts, 0)
        except Exception:
            pass

    def modal(self, context, event):
        t = event.type
        if t == 'LEFTMOUSE' and event.value == 'PRESS':
            self.x0 = event.mouse_region_x; self.starts = [a.smooth for a in self.anchors]   # keys.KeyB && S.asel.length (5803)
        elif t == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.x0 = None
            if self.changed:
                live.schedule(self.root.name)                       # (the walls on the release, B still held: the moves change the curves alone)
        elif t == 'MOUSEMOVE' and self.x0 is not None:
            v = edit.smoothDrag(self.P, self.anchors, self.starts, event.mouse_region_x - self.x0)
            self.changed = True; self._hud(context, v)
            context.area.tag_redraw()
        elif (t == 'B' and event.value == 'RELEASE') or (t in {'ESC', 'RIGHTMOUSE', 'RET'} and event.value == 'PRESS'):
            return self._finish(context)
        elif t in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        live.header(context.area, None)
        if not self.changed:
            context.area.tag_redraw()
            return {'CANCELLED'}
        return _finish_edit(context, self.root, self.P)

    def execute(self, context):
        root, P, sel = select.current(context)
        anchors = select.anchors_of(P, sel)
        if not anchors or self.value < 0:
            self.report({'ERROR'}, 'Hold B and drag in the 3D View with anchors selected, or pass value')
            return {'CANCELLED'}
        edit.setSmooth(P, anchors, f32(self.value))
        return _finish_edit(context, root, P)


class HAIRMESH_OT_delete_selected(bpy.types.Operator):
    """Delete the selected anchors (the curves they were in stay selected; curves left with one anchor go too), else the selected curves"""
    bl_idname = 'hairmesh.delete_selected'
    bl_label = 'Delete HairMesh Selection'
    bl_options = {'REGISTER', 'UNDO'}

    keep_shape: BoolProperty(name='Keep Shape', description="The curves keep their shape: the neighbors' handles are frozen to the arc the anchor was on (the pie's \"delete, keep shape\")", default=False)

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    def execute(self, context):
        return delete_selection(self, context, self.keep_shape)


def delete_selection(op, context, keep_shape=False):
    """deleteSelected (8107): the selected anchors (deleteAnchors), else every selected item (deleteItem: curves, walls,
    guides, crowns, groups with everything inside, converted meshes) — Backspace, X (after its question) and Delete"""
    root, P, sel = select.current(context)
    anchors = select.anchors_of(P, sel)
    groups = False
    if anchors:
        touched = edit.deleteAnchors(P, anchors, keep_shape)
        if touched is None:
            op.report({'WARNING'}, "Disc points can't be deleted: use Reset deformations")
            return {'CANCELLED'}
        sel = Selection(touched, [])                                # the edited curves stay selected (4798)
        msg = f'HairMesh: {len(anchors)} anchor{"s" if len(anchors) > 1 else ""} deleted'
    elif sel.items:
        n = 0
        from ..core import groups as G
        tips = [P.items[i] for i in sel.items if getattr(P.items.get(i), 'type', None) == 'tip']
        tip_curves = [c.id for k in tips if getattr(P.items.get(k.parent), 'type', None) == 'group' for c in G.curvesIn(P, P.items[k.parent])]
        for iid in list(sel.items):
            if iid in P.items:
                groups = groups or P.items[iid].type == 'group'
                edit.deleteItem(P, iid); n += 1                     # deleteSelected (8106): every selected item, groups with everything inside
        sel = Selection([], [])
        msg = f'HairMesh: {n} item{"s" if n > 1 else ""} deleted'
        if tip_curves:                                              # (a Density deleted, 769: S.surfDirty — the locks of its group)
            live.touch(root, [i for i in tip_curves if i in P.items])
    else:
        op.report({'INFO'}, 'Nothing selected')
        return {'CANCELLED'}
    select.store(context.scene.hairmesh, sel)
    if groups:                                                      # a group went (the outliner's X, Backspace: U6): its collection goes too, the locks are rebuilt
        from ..core import groups as G
        from . import ops_groups
        G.panelsRendered(P, None)
        session.save_project(root, P)
        ops_groups.sync_collections(context, root, P)               # (S.surfDirty: the locks that lost walls are rebuilt by themselves)
    return _finish_edit(context, root, P, op.report, msg)


def delete_question(P, sel):
    """X's question (4.4): "Delete 3 anchors?", "Delete Curve 12?", "Delete 4 items?" """
    anchors = select.anchors_of(P, sel)
    if anchors:
        return f'Delete {len(anchors)} anchor{"s" if len(anchors) > 1 else ""}?'
    items = [P.items[i] for i in sel.items if i in P.items]
    return f'Delete {items[0].name}?' if len(items) == 1 else f'Delete {len(items)} items?'


class _ContextualDelete:
    """4.4, item 5 (the owner's request): X and Delete contextual like G / R / S and Alt+A — the HairMesh selection with
    the tool active in the 3D View; with nothing of it selected (or the tool off) the poll fails and the key goes on to
    Blender's (object.delete). Backspace stays hairmesh.delete_selected."""
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import tool
        if not (owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None):
            return False
        if context.area is not None and context.area.type == 'VIEW_3D' and not tool.is_active(context):
            return False
        if not select.has_any(context.scene.hairmesh):              # (the cheap test first: no project read)
            cls.poll_message_set('Nothing of the HairMesh is selected')
            return False
        _r, _P, sel = select.current(context)
        if not (sel.items or sel.anchors):                          # (pruned: ids of items gone never keep the key from Blender)
            cls.poll_message_set('Nothing of the HairMesh is selected')
            return False
        return True

    def execute(self, context):
        return delete_selection(self, context)


class HAIRMESH_OT_delete_ask(_ContextualDelete, bpy.types.Operator):
    """Delete the HairMesh selection after a short question (X with the tool active): the selected anchors, else the selected items. With nothing of the HairMesh selected the key is Blender's"""
    bl_idname = 'hairmesh.delete_ask'
    bl_label = 'Delete HairMesh Selection (Ask)'

    def invoke(self, context, event):
        _root, P, sel = select.current(context)
        return context.window_manager.invoke_confirm(self, event, title='HairMesh', message=delete_question(P, sel), confirm_text='Delete', icon='WARNING')


class HAIRMESH_OT_delete_now(_ContextualDelete, bpy.types.Operator):
    """Delete the HairMesh selection at once (Delete with the tool active): the selected anchors, else the selected items. With nothing of the HairMesh selected the key is Blender's"""
    bl_idname = 'hairmesh.delete_now'
    bl_label = 'Delete HairMesh Selection (Direct)'


class HAIRMESH_OT_select_circle(bpy.types.Operator):
    """Select anchors and curves with a circle (C): the circle follows the cursor; a left drag selects what it covers (a stroke starting over anchors paints anchors, one starting over a bare stretch of curve paints curves), Shift + drag or a middle drag deselects, the wheel or + / - changes the radius, Esc or a right click leaves"""
    bl_idname = 'hairmesh.select_circle'
    bl_label = 'Circle Select'
    bl_options = {'REGISTER', 'UNDO'}

    radius: IntProperty(name='Radius', description='In pixels', default=25, min=1, max=500, subtype='PIXEL')

    @classmethod
    def poll(cls, context):
        return owner.visible(context)                               # even without a project: C must not fall into Blender's Circle Select of objects

    def invoke(self, context, event):
        v = View.of(context)
        if v is None:
            self.report({'INFO'}, 'HairMesh Circle Select needs a project and the 3D View')
            return {'CANCELLED'}
        self.view = v; self.sel = select.load(context.scene.hairmesh).prune(v.P)
        self.painting = None; self.deselect = False; self.changed = False
        self._circle(context, event)
        self._hud(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _circle(self, context, event):
        drawmod.overlay = ('circle', event.mouse_region_x, event.mouse_region_y, self.radius * self.view.ui)
        context.area.tag_redraw()

    def _hud(self, context):
        live.header(context.area, f'Circle Select (Radius: {self.radius} px)', CIRCLE_KEYS)

    def cancel(self, context):
        modal_keys.clear(context)                                   # (Blender ends the modal itself: a window closed, a file loaded)
        drawmod.overlay = None

    def modal(self, context, event):
        t = event.type; press = event.value == 'PRESS'
        if t == 'MOUSEMOVE':
            self._circle(context, event)
            if self.painting is not None:
                self._paint(context, event)
        elif t in {'LEFTMOUSE', 'MIDDLEMOUSE'} and press:
            self.deselect = event.shift or t == 'MIDDLEMOUSE'
            v = self.view; x, y = event.mouse_region_x, event.mouse_region_y
            inside = pick.anchors_in_circle(v.anchors(self.sel), v.project, x, y, self.radius * v.ui)
            self.painting = 'ANCHORS' if inside else 'CURVES'      # the stroke's kind is decided where it starts
            self._paint(context, event)
        elif t in {'LEFTMOUSE', 'MIDDLEMOUSE'} and event.value == 'RELEASE':
            self.painting = None
        elif t in {'WHEELUPMOUSE', 'NUMPAD_PLUS', 'EQUAL'} and press:
            self.radius = min(500, self.radius + CIRCLE_STEP_PX); self._circle(context, event); self._hud(context)
        elif t in {'WHEELDOWNMOUSE', 'NUMPAD_MINUS', 'MINUS'} and press:
            self.radius = max(1, self.radius - CIRCLE_STEP_PX); self._circle(context, event); self._hud(context)
        elif t in {'ESC', 'RIGHTMOUSE', 'RET'} and press:
            return self._finish(context)
        return {'RUNNING_MODAL'}

    def _paint(self, context, event):
        v = self.view; x, y = event.mouse_region_x, event.mouse_region_y; r = self.radius * v.ui
        if self.painting == 'ANCHORS':
            ids = [a.id for a in pick.anchors_in_circle(v.anchors(self.sel), v.project, x, y, r)]
            changed = self.sel.circle(anchor_ids=ids, deselect=self.deselect)
        else:
            ids = [c.id for c in pick.curves_in_circle(v.curves(), v.project, x, y, r)]
            changed = self.sel.circle(curve_ids=ids, deselect=self.deselect)
        if changed:
            self.changed = True
            select.store(context.scene.hairmesh, self.sel); context.area.tag_redraw()

    def _finish(self, context):
        drawmod.overlay = None
        live.header(context.area, None); context.area.tag_redraw()
        return {'FINISHED'} if self.changed else {'CANCELLED'}

    def execute(self, context):
        self.report({'INFO'}, 'HairMesh Circle Select runs in the 3D View: press C with the tool active')
        return {'CANCELLED'}


class HAIRMESH_OT_reverse_curves(bpy.types.Operator):
    """Reverse the direction of the selected curves: the root (diamond anchor) becomes the tip; frozen handles, guide hits and attachments stay in place"""
    bl_idname = 'hairmesh.reverse_curves'
    bl_label = 'Reverse HairMesh Curves'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    def execute(self, context):
        root, P, sel = select.current(context)
        cs = edit.reverseCurves(P, select.curves_of(P, sel))
        if not cs:
            self.report({'INFO'}, 'Select curves first')
            return {'CANCELLED'}
        return _finish_edit(context, root, P, self.report, 'Curve direction reversed' if len(cs) == 1 else f'{len(cs)} curves reversed')


RECALC_DONE = 'Nothing to recalculate: Recalculate acts only on a handle frozen by "Delete Anchor, Keep Shape" or on a Smoothness below 1'


def recalc_targets(P, sel):
    """the anchors Recalculate resets: the selected ones, else every anchor of the selected curves"""
    anchors = select.anchors_of(P, sel)
    return anchors or list(dict.fromkeys(a for c in select.curves_of(P, sel) for a in c.anchors))


class HAIRMESH_OT_recalc_handles(bpy.types.Operator):
    """Recalculate the handles of the selected anchors (or of every anchor of the selected curves): frozen handles and smoothness tweaks dropped, back to clean automatic arcs"""
    bl_idname = 'hairmesh.recalc_handles'
    bl_label = 'Recalculate HairMesh Handles'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        try:
            _r, P, sel = select.current(context)
            if P is not None and not effects.recalcActs(P, recalc_targets(P, sel)):
                return RECALC_DONE
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def execute(self, context):
        root, P, sel = select.current(context)
        anchors = select.anchors_of(P, sel)
        if not anchors and not select.curves_of(P, sel):
            self.report({'INFO'}, 'Select anchors or curves first')
            return {'CANCELLED'}
        if not effects.recalcActs(P, recalc_targets(P, sel)):      # part 1 of the lot ux7 (the original says "Handles recalculated" anyway, 4808)
            self.report({'INFO'}, RECALC_DONE)
            return {'CANCELLED'}
        if anchors:
            edit.recalcHandles(P, anchors)
        else:
            edit.recalcCurves(P, select.curves_of(P, sel))
        return _finish_edit(context, root, P, self.report, 'Handles recalculated')


FRAME_NEED = 'F frames the HairMesh selection: select anchors, curves, walls or a group first (with nothing of the HairMesh selected F stays Blender\'s)'


def frame_why(context):
    """why F (4.0, D4.3) does not act here, or None: a project, something of the HairMesh selected and, in a 3D View,
    the tool active (any sub-tool, as the original's F) — the key passes on otherwise (the Fluent pie on the owner's F)"""
    if not owner.visible(context):
        return 'HairMesh is not shown in this workspace'
    if not select.has_any(context.scene.hairmesh):
        return FRAME_NEED
    root, P, sel = select.current(context)
    if P is None:
        return 'No HairMesh project in this scene'
    if not (sel.items or sel.anchors):
        return FRAME_NEED
    if context.area is not None and context.area.type == 'VIEW_3D':
        from . import tool
        if not tool.is_active(context):
            return 'F frames the HairMesh selection with the HairMesh tool active (Shift+Alt+H)'
    return None


def view_tan_half(context, rv3d):
    """tan(fov / 2) of the 3D View along its narrower side: the viewport's sensor is 72 mm (36 mm × the view zoom
    of 2, CAMERA_PARAM_ZOOM_INIT_PERSP) across its wider side, as Blender's own View Selected sees it"""
    lens = getattr(context.space_data, 'lens', 50.0) or 50.0
    region = context.region
    w, h = (region.width, region.height) if region is not None else (1, 1)
    return 36.0 / lens * (min(w, h) / max(w, h, 1))


def frame_view(context, root, c, r):
    """the view on the box of frameBox: the center in the middle, back r / tan(fov / 2) (4398-4400); the view's
    direction stays"""
    from mathutils import Vector
    from .coords import to_blender
    rv3d = context.region_data
    if rv3d is None:
        return False
    s = max(root.matrix_world.to_scale())
    if rv3d.view_perspective == 'CAMERA':                           # (the camera itself never moves: the free view takes over)
        rv3d.view_perspective = 'PERSP'
    rv3d.view_location = root.matrix_world @ Vector(to_blender(c))
    rv3d.view_distance = r * s / max(view_tan_half(context, rv3d), 1e-6)
    return True


class HAIRMESH_OT_frame_selection(bpy.types.Operator):
    """Frame the HairMesh selection (F): the selected anchors, else what is inside the selected curves, walls and groups (their anchors and converted meshes); with nothing of the HairMesh selected F stays Blender's"""
    bl_idname = 'hairmesh.frame_selection'
    bl_label = 'HairMesh Frame Selection'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        why = frame_why(context)
        if why:
            cls.poll_message_set(why)
        return why is None

    def execute(self, context):
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)
        got = edit.frameBox(P, sel.items, select.anchors_of(P, sel))
        if got is None:
            self.report({'INFO'}, 'Nothing to frame')
            return {'CANCELLED'}
        if context.area is None or context.area.type != 'VIEW_3D' or not frame_view(context, root, *got):
            self.report({'INFO'}, 'F frames in a 3D View')
            return {'CANCELLED'}
        context.area.tag_redraw()
        return {'FINISHED'}


_CLASSES = (HAIRMESH_OT_select, HAIRMESH_OT_deselect_all, HAIRMESH_OT_delete_ask, HAIRMESH_OT_delete_now, HAIRMESH_OT_frame_selection, HAIRMESH_OT_select_curve_anchors, HAIRMESH_OT_move_anchors, HAIRMESH_OT_insert_anchor, HAIRMESH_OT_smooth_drag, HAIRMESH_OT_delete_selected,
            HAIRMESH_OT_select_circle, HAIRMESH_OT_reverse_curves, HAIRMESH_OT_recalc_handles)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    drawmod.overlay = None; drawmod.snap_curve = None; drawmod.weld_cand = None
