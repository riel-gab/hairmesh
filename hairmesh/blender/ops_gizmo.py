# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The anchor gizmo and Duplicate (3.8b; D11 (a) of docs/fase-3-plano-ciclo-nativo.md).

The original's gizmo (4404-4708) is three TransformControls on one proxy, one per tool: Move (E: arrows), Rotate (R:
rings), Scale (T: boxes), with W for world / local axes and X / Y / Z to lock an axis. Here:

  HAIRMESH_GGT_anchors   the GizmoGroup of the HairMesh tool (bl_widget): Blender's own arrows, dials and boxes on the
                         selected anchors, the pivot on the LAST one selected (gizmoTargets 4567), the axes of the
                         world or of the curve's frame (localFrame 4546); since 3.8d also on selected items (curves,
                         walls, groups: the anchors inside them, the pivot at the center of their box, 4568-4588; the
                         Local axes the last item's, 4548; their own rotations turned on release, rotateOwnAxes 4551;
                         since 4.0 the converted meshes inside follow, baked on release, 4646: MeshFollow).
                         Shown only with the tool active, in Select, with something selected and a gizmo mode on.
  hairmesh.gizmo_drag    what a handle runs (target_set_operator): the modal drag. The proxy follows the mouse as
                         TransformControls makes it (an axis line, the view plane, a ring, scaleFromScreen 4614), and the
                         model changes through the core (edit.gizmoMatrix / gizmoStep / gizmoSlide: bit for bit with the
                         original). One undo step on release; Esc or the right button go back to where it started, as
                         the direct drag of 3.3. The live walls follow with the same debounce (live.schedule). Moving one
                         anchor ends in the weld pie (checkWeld, 4657). The non-modal form (execute) takes the mode,
                         the axes, the locks, Ctrl / Shift and the proxy's change (delta / axis + angle / scale), as the
                         harness's `gizmo` op, and an optional pivot: tests and the stress test.
  hairmesh.gizmo_move / gizmo_rotate / gizmo_scale   the modes (the original's E / R / T; pressed again: off)
  hairmesh.gizmo_space / gizmo_lock                  W (world / local) and X / Y / Z of the original, as buttons
  hairmesh.duplicate     Shift+D: duplicateSelection (9904), one undo step, the copy's anchors selected (no drag:
                         the original starts none)

The gizmo's axes are the MODEL's (the original's x, y, z under the root): with the root unrotated, model x is
Blender's X (red), model y Blender's Z (blue) and model z Blender's -Y (green); the locks and the handles are named by
the Blender axis they show as."""
import math

import bpy
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, FloatProperty, FloatVectorProperty
from mathutils import Matrix, Vector

from ..core import curves as CM, duplicate as DUP, edit, groups as G, weld as W
from ..core import fdlibm
from ..core.hairmesh_io import defaultStep
from ..core.mat4 import applyQuaternion
from ..core.selection import Selection
from ..core.vec3 import V3
from . import draw as drawmod, live, modal_keys, owner, select, session, wording
from .coords import f32, to_blender
from .ops_draw import NAV_EVENTS

MODES = (('NONE', 'Off', 'No gizmo on the selection', 'BLANK1', 0),
         ('TRANSLATE', 'Move', 'Move the selection with arrows', 'EMPTY_ARROWS', 1),
         ('ROTATE', 'Rotate', 'Turn the selection with rings', 'DRIVER_ROTATIONAL_DIFFERENCE', 2),
         ('SCALE', 'Scale', 'Scale the selection with boxes', 'MOD_LENGTH', 3))
SPACES = (('WORLD', 'World', 'The gizmo follows the axes of the world', 'WORLD', 0),
          ('LOCAL', 'Local', "The gizmo follows the axes of the last anchor's curve and its groups (their own rotation from the file)", 'ORIENTATION_LOCAL', 1))
CORE_MODE = {'TRANSLATE': 'translate', 'ROTATE': 'rotate', 'SCALE': 'scale'}
# a Blender axis shown by the gizmo -> the model axis it is (the root unrotated: (x, y, z)_three -> (x, -z, y)_blender)
MODEL_AXIS = {'X': 'x', 'Y': 'z', 'Z': 'y'}
UNIT = {'x': V3(1, 0, 0), 'y': V3(0, 1, 0), 'z': V3(0, 0, 1)}
NEED_ANCHORS = 'The gizmo moves the selection: select anchors (click one, Shift+click adds, or drag a box), curves, walls or a group (the HairMesh Outliner, O)'
NEED_SELECT = 'The gizmo works in the Select sub-tool (key 1)'
HEADER = 'HairMesh Gizmo'
KEYS = ((('LMB Drag',), 'Move the Handle'), (('Ctrl',), 'Uniform Scale'), (('Shift',), '0.1 Steps / Slide Along Curve'), (('RMB', 'Esc'), 'Cancel'))


DRAG = None                                                       # (root name, pos, quat) of the proxy while a drag runs (3.8d): the handles stay on it


def has_anchors(context):
    """anything the gizmo can act on is selected (3.8d: anchors or items)"""
    return select.has_any(context.scene.hairmesh)


def blocked_reason(context):
    """why the gizmo and G / R / S cannot act on the HairMesh selection now, or None (3.8d): nothing selected, no
    project, or what edit.gizmoBlocked says (imported meshes inside: phase 5; nothing that moves)"""
    hm = context.scene.hairmesh
    if not select.has_any(hm):
        return NEED_ANCHORS
    root, P, sel = select.current(context)                          # (an unreadable project text: no project, never an error per redraw)
    if P is None:
        return 'No HairMesh project in this scene'
    if not (sel.items or sel.anchors):
        return NEED_ANCHORS
    return wording.core(edit.gizmoBlocked(select.gizmo_targets(P, sel)))


def blocked_short(why):
    """a short form of blocked_reason for a menu row (a menu widens to its longest row; the tooltips say it whole)"""
    if why == NEED_ANCHORS:
        return 'Nothing selected: click an anchor, a curve or a wall; a group in the Outliner (O)'
    if why.startswith('Imported meshes'):
        return 'Imported meshes cannot be transformed directly: select curves or walls'
    return why.split('. ')[0]


# (x, y, z)_three -> (x, -z, y)_blender as a matrix: a model matrix M shows in Blender as AXES @ M @ AXES⁻¹
AXES = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def blender_matrix(Mx):
    """a Mat4 of the model (three.js, column-major elements) as the Blender matrix it is under the root"""
    e = Mx.e
    return AXES @ Matrix([[e[i], e[i + 4], e[i + 8], e[i + 12]] for i in range(4)]) @ AXES.inverted()


class MeshFollow:
    """4.0 (D4.11): the converted meshes of a drag (st.meshes). While it runs, their objects show the drag's matrix
    (the original: m.obj.matrix = M, 4674) — the data is not touched; on release the matrix is baked into the mesh
    items (edit.bakeMeshes, 4646) and the objects are back at the identity, refilled from the new data by
    live.update (meshes.sync, a new token); a cancel only puts the objects back."""

    def __init__(self, root, st):
        from . import meshes
        obs = meshes.mesh_objects(root) if st.meshes else {}
        self.root = root.name
        self.names = [obs[m.id].name for m in st.meshes if m.id in obs]   # (names, never the objects: an undo may free them)
        self.ptr = {n: bpy.data.objects[n].as_pointer() for n in self.names}   # (a file loaded since: another object of that name is left alone)
        self.baked = []                                            # (what the release baked: commit_release)
        self.start = {n: bpy.data.objects[n].matrix_basis.copy() for n in self.names}   # (identity, unless the user moved the object with Blender's own G)

    def _obs(self):
        return [ob for n in self.names if (ob := bpy.data.objects.get(n)) is not None and ob.as_pointer() == self.ptr[n]]

    def show(self, Mx):
        if self.names:
            B = blender_matrix(Mx)
            for ob in self._obs():
                ob.matrix_basis = self.start[ob.name] @ B

    def reset(self):
        for ob in self._obs():
            ob.matrix_basis = self.start[ob.name]

    def bake(self, P, st, Mx):
        self.baked = edit.bakeMeshes(P, st, Mx) if st.meshes else []
        self.reset()
        return self.baked


def commit_release(context, root, P, baked):
    """the release of a drag (the gizmo's, G / R / S's; modal and not): the text written (one undo step) and the
    preview rebuilt, the meshes it baked kept through that update and then while their walls stay as they are
    (meshes.baking / baked: a pending mesh moved alone stays where it went; Convert never reuses a baked data)"""
    from . import meshes
    session.save_project(root, P)
    if baked:
        meshes.baking(root, P, baked)
    live.update(context, root)
    if baked:
        meshes.baked(root, P)


def set_drag(pos, quat, root=None):
    """the proxy of a running drag (gizmo or G / R / S) of the project `root`: the handles are drawn there, as
    TransformControls draws the proxy it moves (updateGizmo places it only when no drag runs, 4705) — the targets' box
    moves under a turn"""
    global DRAG
    DRAG = (root.name if root is not None else (DRAG[0] if DRAG else None), pos.clone(), quat.clone()) if pos is not None else None


def shown(context):
    """the gizmo shows: the tool active in Select, a mode on, something selected, a project (the GizmoGroup's poll)"""
    from . import tool
    hm = context.scene.hairmesh
    return (hm.gizmo_mode != 'NONE' and hm.subtool == 'SELECT' and has_anchors(context) and owner.visible(context)
            and tool.is_active(context) and session.find_root(context) is not None)


def locks(hm):
    """the locked axes as the core reads them (S.axisLock: model axes)"""
    return {MODEL_AXIS['X']: hm.gizmo_lock_x, MODEL_AXIS['Y']: hm.gizmo_lock_y, MODEL_AXIS['Z']: hm.gizmo_lock_z}


def frame(P, tg, space):
    """(pivot, quaternion): where the proxy sits and its axes (gizmoTargets 4563, localFrame 4546), for the targets tg"""
    q = edit.localFrame(P, [] if tg.items else tg.anchors, tg.items) if space == 'LOCAL' else edit.Quat()
    return tg.pivot.clone(), q


def targets(P, sel, report=None):
    """the gizmo's targets of the selection, or None with the reason reported (edit.gizmoBlocked: imported meshes
    inside it are phase 5; the converted ones move since 4.0)"""
    tg = select.gizmo_targets(P, sel) if P is not None else None
    why = wording.core(edit.gizmoBlocked(tg)) if (P is not None and (sel.items or sel.anchors)) else NEED_ANCHORS
    if why is not None:
        if report is not None:
            report({'INFO'}, why)
        return None
    return tg


def axis_world(root, q, ax):
    """the direction Blender axis `ax` (X / Y / Z) of the frame q takes in the world (unit), and its model direction"""
    d = applyQuaternion(edit.AXIS_LOCAL[ax].clone(), q)
    m = root.matrix_world.to_3x3() @ Vector(to_blender(d))
    return (m.normalized() if m.length > 0 else Vector((0, 0, 1))), d


def pivot_world(root, p):
    return root.matrix_world @ Vector(to_blender(p))


def attach_preview(view, P, anchors):
    """4.4 (the owner's request): while ONE curve end is dragged (Select, G, the gizmo) every crown's connectors show,
    whatever the selection or the groups' modes, and a dot on the connector says where the end would attach — gone
    out of reach and while the weld pie would win"""
    from mathutils import Vector
    from ..core import crowntool as CT
    from ..core.geom import pointOnCurve
    from ..core.weld import nearestT
    from . import pick
    from .ops_select import PICK_CURVE_PX
    gone = G.hiddenCrownCurves(P)
    cand = [c for c in CT.attachCandidates(P, anchors[0]) if edit.isVisible(P, c) and c.id not in gone] if len(anchors) == 1 else []
    drawmod.connectors_on = bool(cand)
    dot = None
    if cand and drawmod.weld_cand is None:
        a = anchors[0]; s = view.project(a.pos)
        ch = pick.pick_curve(cand, view.project, s[0], s[1], view.px(PICK_CURVE_PX)) if s is not None else None
        q = pointOnCurve(ch['curve'], nearestT(ch['curve'], a.pos)) if ch is not None else None
        if q is not None:
            dot = tuple(view.root.matrix_world @ Vector(to_blender(q)))
    drawmod.attach_dot = dot


def attach_drag_end():
    drawmod.connectors_on = False; drawmod.attach_dot = None


def check_attach(view, P, a):
    """checkAttach (7406): an anchor END released near a crown connector (no other anchor near): the Attach pie.
    Shared by the drag of Select (ops_select), the gizmo and G. Returns whether the pie was asked for."""
    from ..core import crowntool as CT
    from . import ops_crown, pick
    from .ops_select import PICK_CURVE_PX
    attach_drag_end()
    cand = [c for c in CT.attachCandidates(P, a) if edit.isVisible(P, c)]   # (4.4, the owner's decision: every crown's connector, whatever the selection and the groups' modes; the eye still hides one, as c.obj.visible 7409)
    s = view.project(a.pos) if cand else None
    ch = pick.pick_curve(cand, view.project, s[0], s[1], view.px(PICK_CURVE_PX)) if s is not None else None
    if ch is None:
        return False
    ops_crown.ask_attach(view.root, a, ch['curve'])
    bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name=ops_crown.HAIRMESH_MT_attach_pie.bl_idname)
    return True


def check_weld(view, P, sel, a):
    """checkWeld (4876) after a drag of one anchor (the gizmo, 4657; G, 3.8c): the weld pie when another anchor is close"""
    from . import ops_weld
    b = W.findWeldCandidate(P, a, view.anchors(sel), view.project)
    if b is None:
        ops_weld.pending = None
        check_attach(view, P, a)                                    # (4878: no weld candidate -> checkAttach, 4.3)
        return
    ops_weld.ask_weld(a, b, P)
    bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name=ops_weld.HAIRMESH_MT_weld_pie.bl_idname)


class _Undo:
    """what a cancel needs to put back: where every anchor started and the frozen handles of the moved anchors
    (gizmoStep turns them by increments, so they are kept whole)"""

    def __init__(self, P, anchors):
        self.pos = [(a, a.pos.clone()) for a in anchors]
        ids = {a.id for a in anchors}
        self.ovr = [(o, o.in_.clone() if o.in_ is not None else None, o.out.clone() if o.out is not None else None)
                    for c in P.curves() for aid, o in c.ovr.items() if aid in ids]
        # (4.2c, item 2: the sample lists of the curves through them, put back with them — a curve resampled back to the
        # very list the row tables were built from is no change, so the Esc of a ring dragged rebuilds no lock)
        self.samples = [(c, c.samples, c.sample_key, c.arc) for c in P.curves() if any(a.id in ids for a in c.anchors)]
        from ..core.crowntool import syncSnapshot
        self.P = P; self.sync = syncSnapshot(P)                     # (4.3: what the crown's frame step moves during the drag — the pairs, the attached anchors)

    def restore(self):
        for a, p in self.pos:
            a.pos.copy(p)
        for o, i, u in self.ovr:
            if i is not None:
                o.in_.copy(i)
            if u is not None:
                o.out.copy(u)
        for c, s, k, arc in self.samples:
            c.samples = s; c.sample_key = k; c.arc = arc
        from ..core.crowntool import syncRestore
        syncRestore(self.P, self.sync)
        attach_drag_end()


class HAIRMESH_OT_gizmo_drag(bpy.types.Operator):
    """Drag the gizmo of the HairMesh selection — anchors, curves, walls or a group (move along an axis or in the view, turn about an axis or the view, scale along an axis or evenly); Esc or right click cancels"""
    bl_idname = 'hairmesh.gizmo_drag'
    bl_label = 'HairMesh Gizmo'
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name='Mode', items=MODES[1:], default='TRANSLATE', options={'SKIP_SAVE'})
    axis: EnumProperty(name='Axis', description='The handle: an axis of the gizmo (by the Blender axis it shows as), the view, or (non-modal) a vector of the model',
                       items=(('X', 'X', ''), ('Y', 'Y', ''), ('Z', 'Z', ''), ('VIEW', 'View', 'The view plane / ring / uniform scale'), ('VECTOR', 'Vector', 'Non-modal: axis_vector (model space)')), default='X', options={'SKIP_SAVE'})
    # the non-modal form (tests, the stress test): the proxy's change, in the MODEL's space (the original's axes, Y up)
    space: EnumProperty(name='Axes', items=SPACES, default='WORLD', options={'SKIP_SAVE'})
    delta: FloatVectorProperty(name='Delta', description='Move: the proxy moved by this (model space)', size=3, default=(0.0, 0.0, 0.0), options={'SKIP_SAVE'})
    angle: FloatProperty(name='Angle', description='Rotate: radians about the axis', default=0.0, options={'SKIP_SAVE'})
    axis_vector: FloatVectorProperty(name='Axis Vector', description='Rotate about this unit vector (model space; axis VECTOR)', size=3, default=(0.0, 1.0, 0.0), options={'SKIP_SAVE'})
    scale: FloatVectorProperty(name='Scale', description='Scale: the proxy scale along the gizmo axes (model x, y, z)', size=3, default=(1.0, 1.0, 1.0), options={'SKIP_SAVE'})
    lock_model: BoolVectorProperty(name='Lock (Model Axes)', description='Non-modal: the locked axes x, y, z of the model', size=3, default=(False, False, False), options={'SKIP_SAVE'})
    ctrl: BoolProperty(name='Ctrl', description='Scale: uniform (the axis that changed most)', default=False, options={'SKIP_SAVE'})
    shift: BoolProperty(name='Shift', description='Scale: 0.1 steps · Move one anchor: slide along the nearest curve', default=False, options={'SKIP_SAVE'})
    use_pivot: BoolProperty(name='Pivot', description='Non-modal: turn / scale about `pivot` instead of the last selected anchor', default=False, options={'SKIP_SAVE'})
    pivot: FloatVectorProperty(name='Pivot', description='Model space', size=3, default=(0.0, 0.0, 0.0), options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        return blocked_reason(context) or cls.__doc__

    # ---- the drag (a handle of the gizmo) ----
    def invoke(self, context, event):
        from .ops_select import View
        v = View.of(context)
        if v is None:
            return self.execute(context)
        sel = select.load(context.scene.hairmesh).prune(v.P)
        tg = targets(v.P, sel, self.report)
        if tg is None:
            return {'CANCELLED'}
        hm = context.scene.hairmesh
        self.view = v; self.P = v.P; self.sel = sel
        self.st = edit.startGizmo(v.P, tg, CORE_MODE[self.mode], hm.gizmo_space.lower())
        self.undo = _Undo(v.P, tg.anchors)
        self.lk = locks(hm)
        self.m0 = (event.mouse_region_x, event.mouse_region_y)
        self.pos = self.st.pos.clone(); self.quat = self.st.quat.clone(); self.sc = V3(1, 1, 1)
        self.k = MODEL_AXIS.get(self.axis)                          # None: the view handle
        self.dir = applyQuaternion(edit.AXIS_LOCAL[self.axis].clone(), self.st.quat) if self.k else None
        self.cam = v.cam().clone().normalize()
        self.t0 = self._axis_param(event) if self.mode == 'TRANSLATE' and self.k else None
        self.hit0 = edit.planeHit(v.ray(event), self.st.pos, self.cam) if self.mode == 'TRANSLATE' and not self.k else None
        self.turn = 0.0; self.a_last = self._screen_angle(event) if self.mode == 'ROTATE' else None
        self.level_h = defaultStep(v.P)
        self.follow = MeshFollow(v.root, self.st); self.M = None   # (4.0: the converted meshes inside follow the drag)
        set_drag(self.st.pos, self.st.quat, v.root)
        context.window_manager.modal_handler_add(self)
        live.header(context.area, HEADER, KEYS)
        return {'RUNNING_MODAL'}

    def _axis_param(self, event):
        """where the cursor's ray passes closest to the axis line through the pivot: the parameter along the axis
        (TransformControls drags along an axis on a plane holding it; the closest point is the same line)"""
        r = self.view.ray(event); o = r.origin; d = r.direction; a = self.dir; p = self.st.pos
        w = o.clone().sub(p); b = a.dot(d); den = 1 - b * b
        if abs(den) < 1e-9:                                         # the axis faces the camera: no line to follow
            return None
        return (a.dot(w) - b * d.dot(w)) / den

    def _screen_angle(self, event):
        c = self.view.project(self.st.pos)
        if c is None:
            return 0.0
        return math.atan2(event.mouse_region_y - c[1], event.mouse_region_x - c[0])

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:                                           # (an error mid-drag: everything back, no handles left on a stale proxy)
            self.cancel(context)
            try:
                live.header(context.area, None)
            except Exception:
                pass
            raise

    def _modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._move(context, event)
            return {'RUNNING_MODAL'}
        if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._move(context, event)                              # (Ctrl / Shift change the result at once, as keys[] in the original)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return self._finish(context)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._cancel(context)
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _move(self, context, event):
        st = self.st; v = self.view; P = self.P
        if self.mode == 'TRANSLATE':
            if self.k is not None and self.t0 is not None:
                t = self._axis_param(event)
                if t is None:
                    return
                self.pos = st.pos.clone().addScaledVector(self.dir, t - self.t0)
            else:
                h = edit.planeHit(v.ray(event), st.pos, self.cam)
                if h is None or self.hit0 is None:
                    return
                self.pos = st.pos.clone().add(h.sub(self.hit0))
        elif self.mode == 'ROTATE':
            a = self._screen_angle(event)
            d = a - self.a_last
            d = (d + math.pi) % (2 * math.pi) - math.pi             # the shorter way round: the turns add up past 180°
            self.turn += d; self.a_last = a
            if self.k is not None:
                ang = self.turn if self.dir.dot(self.cam) < 0 else -self.turn   # counter-clockwise on screen = about the axis that points at the viewer
                space = 'local' if context.scene.hairmesh.gizmo_space == 'LOCAL' else 'world'
                self.quat = edit.gizmoRotation(st.quat, edit.AXIS_LOCAL[self.axis], ang, space)
            else:
                self.quat = edit.gizmoRotation(st.quat, self.cam.clone().negate(), self.turn, 'world')   # the view ring (axis E: the eye)
        else:
            self.sc = self._scale_from_screen(event)
        self.mods = (event.ctrl, event.shift)
        set_drag(self.pos, st.quat)
        M = edit.gizmoMatrix(st, self.pos, self.quat, self.sc, self.lk, event.ctrl, event.shift)
        edit.gizmoStep(P, st, M)
        self.M = M; self.follow.show(M)                             # (m.obj.matrix = M, 4674)
        one = self.mode == 'TRANSLATE' and len(st.list) == 1 and len(self.sel.anchors) == 1
        slid = None
        if one and event.shift:
            slid = edit.gizmoSlide(P, st, self.level_h)
            drawmod.snap_curve = slid.id if slid is not None else None
        elif st.snap is not None:
            st.snap = None; drawmod.snap_curve = None
        edit.gizmoMoved(P, st)
        cand = None
        if self.mode == 'TRANSLATE' and len(st.list) == 1:          # S.weldCand (4694)
            cand = W.findWeldCandidate(P, st.list[0], v.anchors(self.sel), v.project)
        drawmod.weld_cand = cand.id if cand is not None else None
        live.schedule(v.root.name)                                  # the live walls follow, at most every 0.25 s (D4)
        if self.mode == 'TRANSLATE':
            attach_preview(v, v.P, [a for a, _p in self.undo.pos])  # (4.4: the connectors and the Attach dot; a turn or a scale never attaches)
        context.area.tag_redraw()

    def _scale_from_screen(self, event):
        """port of scaleFromScreen (4614), the screen in the original's convention (y down): the center (uniform):
        exp((dx - dy) / 250); an axis handle: its tip follows the pointer with a lever of at least 60 px"""
        st = self.st; proj = self.view.project
        mx, my = event.mouse_region_x, -event.mouse_region_y
        dx = mx - self.m0[0]; dy = my - (-self.m0[1])
        if self.k is None:
            s = fdlibm.exp((dx - dy) / 250)
            return V3(s, s, s)
        Pc = proj(st.pos)
        sc = V3(1, 1, 1)
        if Pc is None:
            return sc
        px, py = Pc[0], -Pc[1]
        k = self.k
        tip = proj(st.pos.clone().add(applyQuaternion(UNIT[k].clone(), st.quat).multiplyScalar(self.P.S.scale * 0.1)))
        if tip is None:
            return sc
        sx = tip[0] - px; sy = -tip[1] - py; l = math.hypot(sx, sy)
        if l < 1e-3:                                                # the axis points at the camera
            return sc
        sx /= l; sy /= l
        d0 = (self.m0[0] - px) * sx + (-self.m0[1] - py) * sy
        lever = (-1 if d0 < 0 else 1) * max(abs(d0), 60)
        setattr(sc, k, max(0.01, 1 + (dx * sx + dy * sy) / lever))
        return sc

    def _end(self, context):
        drawmod.snap_curve = None; drawmod.weld_cand = None
        attach_drag_end()
        set_drag(None, None)
        live.header(context.area, None)

    def _finish(self, context):
        self._end(context)
        st = self.st; v = self.view
        if not st.moved:
            live.cancel(v.root.name)
            return {'CANCELLED'}
        self._remember(context)
        self.follow.bake(self.P, st, self.M)                        # (4646): the matrix baked into the converted meshes
        if st.mode == 'rotate':
            edit.rotateOwnAxes(self.P, st, self.quat)               # (4653): the items' own axes turn with them
        commit_release(context, v.root, self.P, self.follow.baked)  # commit (4656): one undo step
        context.area.tag_redraw()
        if st.mode == 'translate' and len(st.list) == 1 and len(self.sel.anchors) == 1:
            self._check_weld(context)                               # checkWeld (4657): the pie when another anchor is close
        return {'FINISHED'}

    def _remember(self, context):
        """the drag as the non-modal properties (Blender's Adjust Last Operation panel redoes it through execute)"""
        st = self.st
        self.space = context.scene.hairmesh.gizmo_space
        self.lock_model = (self.lk['x'], self.lk['y'], self.lk['z'])
        self.ctrl, self.shift = getattr(self, 'mods', (False, False))
        if self.mode == 'TRANSLATE':
            self.delta = self.pos.clone().sub(st.pos).toArray()
        elif self.mode == 'ROTATE':
            d = self.quat.clone().multiply(st.quat.clone().invert())   # the turn in world terms: quat = d · q0
            w = max(-1.0, min(1.0, d.w)); s_ = math.sqrt(max(0.0, 1 - w * w))
            self.axis = 'VECTOR'; self.angle = 2 * math.acos(w)
            self.axis_vector = (d.x / s_, d.y / s_, d.z / s_) if s_ > 1e-9 else (0.0, 1.0, 0.0)
        else:
            self.scale = self.sc.toArray()

    def _check_weld(self, context):
        check_weld(self.view, self.P, self.sel, self.st.list[0])

    def _cancel(self, context):
        # Blender's convention, as the direct drag of 3.3 (the original has no cancel: a release always commits)
        self._end(context)
        self.undo.restore(); self.follow.reset()
        CM.markAnchorsDirty(self.P, self.st.list); self.P.rev += 1
        live.update(context, self.view.root, commit=False); context.area.tag_redraw()
        return {'CANCELLED'}

    def cancel(self, context):
        # Blender ends the modal itself (a window closed, a file loaded): the model back in memory only — no area to
        # talk to, no datablock written while the window manager comes down
        modal_keys.clear(context)
        try:
            self.undo.restore()
            CM.markAnchorsDirty(self.P, self.st.list); self.P.rev += 1
            live.cancel(self.view.root.name)                        # (the drag's debounce timer never fires after: no datablock written from a timer)
            self.follow.reset()
        except Exception:
            pass
        drawmod.snap_curve = None; drawmod.weld_cand = None
        attach_drag_end()
        set_drag(None, None)

    # ---- non-modal (tests, scripts, the stress test): the harness's `gizmo` op ----
    def execute(self, context):
        root, P, sel = select.current(context)
        tg = targets(P, sel, self.report)
        if tg is None:
            return {'CANCELLED'}
        mode = CORE_MODE[self.mode]
        st = edit.startGizmo(P, tg, mode, self.space.lower())
        if self.use_pivot:
            st.pos = V3(*(f32(x) for x in self.pivot))
        lk = {'x': self.lock_model[0], 'y': self.lock_model[1], 'z': self.lock_model[2]}
        pos = st.pos.clone(); quat = st.quat.clone(); sc = V3(1, 1, 1)
        if mode == 'translate':
            pos = st.pos.clone().add(V3(*(f32(x) for x in self.delta)))
        elif mode == 'rotate':
            if self.axis == 'VECTOR':
                quat = edit.gizmoRotation(st.quat, V3(*(f32(x) for x in self.axis_vector)), f32(self.angle), 'world')
            else:
                ax = self.axis if self.axis != 'VIEW' else 'Z'
                quat = edit.gizmoRotation(st.quat, edit.AXIS_LOCAL[ax], f32(self.angle), 'local' if self.space == 'LOCAL' else 'world')
        else:
            sc = V3(*(f32(x) for x in self.scale))
        M = edit.gizmoMatrix(st, pos, quat, sc, lk, self.ctrl, self.shift)
        edit.gizmoStep(P, st, M)
        if mode == 'translate' and len(st.list) == 1 and not st.items and self.shift:
            edit.gizmoSlide(P, st, defaultStep(P))
        edit.gizmoMoved(P, st)
        if st.meshes:
            baked = edit.bakeMeshes(P, st, M)                       # the release (4646)
        else:
            baked = []
        if mode == 'rotate':
            edit.rotateOwnAxes(P, st, quat)                         # the release (4653): the items' own axes turn with them
        commit_release(context, root, P, baked)
        select.redraw(context)
        return {'FINISHED'}


def _mode_operator(key, label, letter):
    class Op(bpy.types.Operator):
        bl_idname = f'hairmesh.gizmo_{label.lower()}'
        bl_label = f'HairMesh {label} Gizmo'
        bl_description = (f'Show the {label} gizmo on the selection — anchors, curves, walls or a group; '
                          f'again: no gizmo. Works in the Select sub-tool')
        bl_options = {'REGISTER'}
        mode = key

        @classmethod
        def poll(cls, context):
            return owner.visible(context)

        @classmethod
        def description(cls, context, properties):
            why = blocked_reason(context)
            return f'{cls.bl_description}. Now: {why}' if why else cls.bl_description

        def execute(self, context):
            hm = context.scene.hairmesh
            hm.gizmo_mode = 'NONE' if hm.gizmo_mode == self.mode else self.mode
            if hm.gizmo_mode != 'NONE':
                if hm.subtool != 'SELECT':
                    hm.subtool = 'SELECT'                           # (the original's E / R / T are tools of their own: picking one leaves Draw / Scissors)
                why = blocked_reason(context)
                if why:
                    self.report({'INFO'}, f'{label} gizmo on. ' + why)
            select.redraw(context)
            return {'FINISHED'}
    Op.__name__ = Op.__qualname__ = f'HAIRMESH_OT_gizmo_{label.lower()}'
    return Op


MODE_OPS = (_mode_operator('TRANSLATE', 'Move', 'E'), _mode_operator('ROTATE', 'Rotate', 'R'), _mode_operator('SCALE', 'Scale', 'T'))
MODE_IDS = {op.mode: op.bl_idname for op in MODE_OPS}


class HAIRMESH_OT_gizmo_space(bpy.types.Operator):
    """Switch the gizmo's axes: the world's, or the local frame of the last anchor's curve"""
    bl_idname = 'hairmesh.gizmo_space'
    bl_label = 'HairMesh Gizmo: World / Local Axes'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    @classmethod
    def description(cls, context, properties):
        """(item 8 of the lot after 4.8: the button is an icon — the tooltip says which axes are on)"""
        on = 'Local' if context.scene.hairmesh.gizmo_space == 'LOCAL' else 'World'
        return f'{on} Axes (a click: {"World" if on == "Local" else "Local"}). {cls.__doc__}'

    def execute(self, context):
        hm = context.scene.hairmesh
        hm.gizmo_space = 'LOCAL' if hm.gizmo_space == 'WORLD' else 'WORLD'
        self.report({'INFO'}, f'Gizmo: {"World" if hm.gizmo_space == "WORLD" else "Local"} axes')   # toggleGizmoSpace's toast (4521)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_gizmo_lock(bpy.types.Operator):
    """Lock / unlock an axis of the gizmo: its handles hide and the anchors never move along it"""
    bl_idname = 'hairmesh.gizmo_lock'
    bl_label = 'HairMesh Gizmo: Lock Axis'
    bl_options = {'REGISTER'}

    axis: EnumProperty(name='Axis', items=(('X', 'X', ''), ('Y', 'Y', ''), ('Z', 'Z', '')), default='X')

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        hm = context.scene.hairmesh
        attr = f'gizmo_lock_{self.axis.lower()}'
        setattr(hm, attr, not getattr(hm, attr))
        self.report({'INFO'}, f'Axis {self.axis} {"locked" if getattr(hm, attr) else "unlocked"}')   # setAxisLock's toast (4516)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_duplicate(bpy.types.Operator):
    """Duplicate the selected curves, walls and groups (a wall takes its two curves, a curve its modifier); without items, the curves of the selected anchors. The copy's anchors are selected, in the same place"""
    bl_idname = 'hairmesh.duplicate'
    bl_label = 'Duplicate HairMesh Selection'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        hm = context.scene.hairmesh
        if (hm.sel_anchors or '[]') == '[]' and (hm.sel_curves or '[]') == '[]':
            return 'Nothing to duplicate: select curves, walls or a group (the HairMesh Outliner, O), or anchors'
        return cls.__doc__

    def execute(self, context):
        root, P, sel = select.current(context)
        if P is None:
            self.report({'INFO'}, 'No HairMesh project in this scene')
            return {'CANCELLED'}
        made, items, anchors = DUP.duplicateSelection(P, sel.items, sel.anchors)
        msg = DUP.duplicateMessage(made)
        if made is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection(items, anchors))
        from ..core import groups as G
        G.panelsRendered(P, items[len(items) - 1] if items else None)   # onSelChange -> renderOutliner (9980)
        extra = DUP.surfDirtyCurves(P, made)
        if extra:
            live.touch(root, extra)                                 # (S.surfDirty of a copied cap / fill / density, 9951-9953)
        session.save_project(root, P); live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


AXIS_COLOR = {'X': (1.0, 0.2, 0.32), 'Y': (0.54, 0.86, 0.0), 'Z': (0.16, 0.56, 1.0)}   # Blender's own axis colours (theme defaults)
VIEW_COLOR = (0.9, 0.9, 0.9)
BLENDER_AXIS = {v: k for k, v in MODEL_AXIS.items()}               # model axis -> the Blender axis it shows as
_IDENTITY = Matrix.Identity(4)
DRAWN = {}                                                        # (mode, axis) -> a copy of the handle's matrix_world as last drawn (the event simulations find the handles on screen)


def _basis(z, origin):
    """a matrix at `origin` whose Z is the unit vector z (an arrow points along its Z; a dial turns about it)"""
    q = Vector((0, 0, 1)).rotation_difference(z)
    m = q.to_matrix().to_4x4()
    m.translation = origin
    return m


class HAIRMESH_GGT_anchors(bpy.types.GizmoGroup):
    """The gizmo of the HairMesh tool (WorkSpaceTool.bl_widget) on the selection (anchors, or items: 3.8d): Move arrows + the view
    ring, Rotate rings about the three axes + the view, Scale boxes + the uniform ring (updateGizmo 4698: one per
    mode). Every handle runs hairmesh.gizmo_drag with its mode and axis."""
    bl_idname = 'HAIRMESH_GGT_anchors'
    bl_label = 'HairMesh Gizmo'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        try:
            return shown(context)
        except Exception:                                           # (a poll never raises: a project text being rewritten)
            return False

    def setup(self, context):
        self.handles = []                                           # (mode, Blender axis or 'VIEW', gizmo)
        for mode in ('TRANSLATE', 'ROTATE', 'SCALE'):
            for ax in ('X', 'Y', 'Z', 'VIEW'):
                if mode == 'ROTATE':
                    gz = self.gizmos.new('GIZMO_GT_dial_3d')
                    gz.draw_options = {'CLIP'} if ax != 'VIEW' else set()
                    gz.scale_basis = 1.0 if ax != 'VIEW' else 1.2
                elif ax == 'VIEW':
                    gz = self.gizmos.new('GIZMO_GT_move_3d')
                    gz.draw_style = 'RING_2D'; gz.draw_options = {'ALIGN_VIEW'}
                    gz.scale_basis = 0.18 if mode == 'TRANSLATE' else 0.28
                else:
                    gz = self.gizmos.new('GIZMO_GT_arrow_3d')
                    gz.draw_style = 'NORMAL' if mode == 'TRANSLATE' else 'BOX'
                    gz.scale_basis = 1.0 if mode == 'TRANSLATE' else 0.8
                col = AXIS_COLOR.get(ax, VIEW_COLOR)
                gz.color = col; gz.alpha = 0.8
                gz.color_highlight = tuple(min(1.0, c + 0.3) for c in col); gz.alpha_highlight = 1.0
                gz.line_width = 2.0
                gz.use_draw_modal = True
                props = gz.target_set_operator(HAIRMESH_OT_gizmo_drag.bl_idname)
                props.mode = mode; props.axis = ax
                self.handles.append((mode, ax, gz))

    def draw_prepare(self, context):
        try:
            self._place(context)
        except Exception:                                           # (never an error per redraw: the handles hide)
            for _m, _a, gz in self.handles:
                gz.hide = True

    def _place(self, context):
        hm = context.scene.hairmesh
        root = session.find_root(context)
        P = session.get_project(root) if root is not None else None
        if DRAG is not None and root is not None and DRAG[0] == root.name:
            p, q = DRAG[1], DRAG[2]                                 # (while a drag of this project runs: the proxy it moves, nothing recomputed)
        else:
            sel = select.load(hm).prune(P) if P is not None else Selection()
            tg = targets(P, sel)
            if tg is None:
                for _m, _a, gz in self.handles:
                    gz.hide = True
                return
            p, q = frame(P, tg, hm.gizmo_space)
        o = pivot_world(root, p)
        view = context.region_data.view_rotation if context.region_data is not None else None
        locked = {'X': hm.gizmo_lock_x, 'Y': hm.gizmo_lock_y, 'Z': hm.gizmo_lock_z}
        for mode, ax, gz in self.handles:
            gz.hide = mode != hm.gizmo_mode or (ax != 'VIEW' and locked[ax])
            if gz.hide:
                continue
            if ax == 'VIEW':
                z = (view @ Vector((0, 0, 1))) if view is not None else Vector((0, 0, 1))
            else:
                z = axis_world(root, q, ax)[0]                     # (AXIS_LOCAL: the green handle points to +Y, the model's -z)
            gz.matrix_basis = _basis(z, o)
            # 3.8c, item 1: a dragged arrow / box keeps the drag as its own offset (the native modal of
            # GIZMO_GT_arrow_3d runs, the operator target notwithstanding, and its basis adds that offset along its Z),
            # while matrix_basis already follows the moved pivot: the handle flew ahead by the drag again. Whatever the
            # final matrix adds is taken back, so the handle stays on the pivot, as Blender's object gizmo.
            if gz.matrix_offset != _IDENTITY:
                gz.matrix_offset = _IDENTITY
            extra = gz.matrix_world.translation - o
            if extra.length > 1e-5 * max(1.0, o.length):               # (above the float32 of matrix_basis: only a real offset)
                m = gz.matrix_basis.copy(); m.translation = o - extra
                gz.matrix_basis = m
            DRAWN[(mode, ax)] = gz.matrix_world.copy()


_CLASSES = (HAIRMESH_OT_gizmo_drag, *MODE_OPS, HAIRMESH_OT_gizmo_space, HAIRMESH_OT_gizmo_lock, HAIRMESH_OT_duplicate, HAIRMESH_GGT_anchors)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
