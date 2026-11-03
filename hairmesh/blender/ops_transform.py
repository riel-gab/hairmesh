# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""G / R / S on the HairMesh selection (3.8c; the owner's decision of 24/10/2026 — nothing of the original: its keys are
E / R / T, the gizmo tools; 3.8d, the owner's option (a) of 28/10/2026: any selection, not only anchors).

With the HairMesh tool active, in Select, with anything of the HairMesh selected — anchors, or curves, walls, groups
(the anchors inside them, as the gizmo takes them: gizmoTargets 4563) — G moves, R turns and S scales them in a modal in
Blender's own style (not through the gizmo); with nothing of the HairMesh selected (or the tool off) the operators fail
their poll and G / R / S reach Blender as always (its transform of the active object). A selection that moves nothing
here (a guide line; imported meshes inside it: phase 5) keeps the key and says why in the status bar, so Blender's
objects never move by mistake; the converted meshes inside it move with it (4.0, D4.11: baked on confirm). The modal:

  - follows the mouse from where the key was pressed: G on the plane facing the view (as the direct drag), R the angle
    about the pivot on the screen, S the ratio of the distances to the pivot on the screen;
  - X / Y / Z lock an axis, the same key again its local axis (the frame of the last anchor's curve, the gizmo's Local),
    again none; Shift+X / Y / Z lock the plane of the other two axes;
  - a typed value (digits, point, minus, Backspace) in the viewport header: G in the project's units (the model's, as
    the Loop Step), R in degrees, S a factor;
  - Ctrl: steps (5°, 0.1, a grid step of the view — a power of ten of ~20 px — for G); Shift: fine (a tenth);
  - Enter or LMB confirm (one undo step; G of a single anchor ends in the weld pie when it lands near another, as the
    direct drag and the gizmo), Esc or RMB cancel (everything back, nothing written).

The pivot of R and S is the gizmo's: the last selected anchor, or the center of the box of the items' anchors. The model changes through the gizmo's core
(edit.gizmoMatrix / gizmoStep: positions from the start, frozen handles by the increment), so G along X typed 10 is the
gizmo's arrow X moved 10 bit for bit, R about Z 30° its ring Z turned 30°, S along Y 1.5 its box Y at 1.5. The live
walls follow with the debounce of the drag (live.schedule). A non-modal form (axis, local, plane, value, delta, scale,
cancel) serves the tests and the stress test."""
import math

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty
from mathutils import Vector

from ..core import curves as CM, edit, weld as W
from ..core.mat4 import applyQuaternion
from ..core.vec3 import V3
from . import draw as drawmod, live, modal_keys, owner, select, session
from .coords import f32, to_blender
from .ops_draw import NAV_EVENTS
from .ops_gizmo import MeshFollow, _Undo, blocked_reason, check_weld, commit_release, set_drag, targets

MODAL_KEYS = ((('LMB', 'Enter'), 'Confirm'), (('RMB', 'Esc'), 'Cancel'), (('X', 'Y', 'Z'), 'Axis (Twice: Local)'), (('Shift', 'X', 'Y', 'Z'), 'Plane'),
        (('Digits',), 'Type a Value'), (('Ctrl',), 'Snap'), (('Shift',), 'Precision'))
FOLLOW_KEY = (('Alt',), 'Follow Head')                              # (lot 4.7b item 6)

AXES = (('NONE', 'None', 'No constraint: the view plane (G), the view axis (R), every axis (S)'),
        ('X', 'X', ''), ('Y', 'Y', ''), ('Z', 'Z', ''))
LABEL = {'translate': 'Move', 'rotate': 'Rotate', 'scale': 'Scale'}
UNITS = {'translate': '', 'rotate': '°', 'scale': '×'}
KEYS = {'X', 'Y', 'Z'}
DIGITS = {'ZERO': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4', 'FIVE': '5', 'SIX': '6', 'SEVEN': '7', 'EIGHT': '8', 'NINE': '9',
          'NUMPAD_0': '0', 'NUMPAD_1': '1', 'NUMPAD_2': '2', 'NUMPAD_3': '3', 'NUMPAD_4': '4', 'NUMPAD_5': '5', 'NUMPAD_6': '6',
          'NUMPAD_7': '7', 'NUMPAD_8': '8', 'NUMPAD_9': '9', 'PERIOD': '.', 'NUMPAD_PERIOD': '.'}
NEED = ('Select anchors (click one, Shift+click adds, or drag a box), curves, walls or a group first: G / R / S then move, '
        "turn or scale them; with nothing of the HairMesh selected G / R / S stay Blender's")


def in_view(context):
    return context.area is not None and context.area.type == 'VIEW_3D'


def can_run(context, why=None):
    """the poll: anything of the HairMesh selected and a project; inside a 3D View also the tool active in Select — the keys reach
    Blender's own G / R / S otherwise (a keymap item whose poll fails lets the event go on). `why`: the poll's
    poll_message_set, for a greyed button"""
    say = why or (lambda _m: None)
    if not owner.visible(context) or context.mode != 'OBJECT':
        return False
    hm = context.scene.hairmesh
    if session.find_root(context) is None:
        say('No HairMesh project in this scene')
        return False
    if not select.has_any(hm):
        say(NEED)
        return False
    if not (select.current(context)[2]):                            # (ids of items deleted since: Blender's keys, not a message)
        say(NEED)
        return False
    if in_view(context):
        from . import tool
        if not (hm.subtool == 'SELECT' and tool.is_active(context)):
            say('G / R / S move the anchors with the HairMesh tool active (Shift+Alt+H), in Select (key 1)')
            return False
    return True                                                     # (a script, the tests: no key involved)


def proxy(st, mode, axis, value, plane=False):
    """the gizmo proxy after a constrained transform of `value` (ours, 3.8c): G along / R about the Blender axis
    `axis` of the drag's frame (edit.AXIS_LOCAL; the frame's own rotation is st.quat), S along it (or the two other
    axes: `plane`) — what the gizmo's arrow / ring / box give for the same axis and value. Returns (pos, quat, scale)."""
    pos = st.pos.clone(); quat = st.quat.clone(); sc = V3(1, 1, 1)
    if mode == 'translate':
        pos.addScaledVector(applyQuaternion(edit.AXIS_LOCAL[axis].clone(), st.quat), value)
    elif mode == 'rotate':
        quat = edit.gizmoRotation(st.quat, edit.AXIS_LOCAL[axis], value, 'local' if st.space == 'local' else 'world')
    else:
        if abs(value) < 0.01:                                       # (as the gizmo's box, max(0.01, …): never through 0)
            value = -0.01 if value < 0 else 0.01
        for k in ('X', 'Y', 'Z'):
            if (k != axis) == plane:
                setattr(sc, edit.AXIS_SCALE[k], value)
    return pos, quat, sc


def pixel_size(context, root, pivot_world):
    """the model's units per region pixel at the pivot (None when the pivot is behind the view)"""
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    a = view3d_utils.location_3d_to_region_2d(region, rv3d, pivot_world)
    if a is None:
        return None
    p0 = view3d_utils.region_2d_to_location_3d(region, rv3d, a, pivot_world)
    p1 = view3d_utils.region_2d_to_location_3d(region, rv3d, (a[0] + 20, a[1]), pivot_world)
    w = (p1 - p0).length / 20
    s = root.matrix_world.to_scale().x or 1.0
    return w / s if w > 0 else None


def grid_step(ps):
    """a step of the view's grid at the pivot, in the model's units: the power of ten of ~20 px there"""
    return 10 ** math.ceil(math.log10(20 * ps)) if ps else 1.0


def snap(v, step):
    return round(v / step) * step if step > 0 else v


class _Transform:
    """the modal of G / R / S (one class per key, so Preferences > Keymap names them apart)"""
    bl_options = {'REGISTER', 'UNDO'}
    mode = 'translate'

    axis: EnumProperty(name='Axis', items=AXES, default='NONE', options={'SKIP_SAVE'})
    local: BoolProperty(name='Local', description='The local axes (the frame of the last anchor\'s curve and its groups), as the gizmo\'s Local', default=False, options={'SKIP_SAVE'})
    plane: BoolProperty(name='Plane', description='Shift+axis: the two other axes (move on their plane, scale both)', default=False, options={'SKIP_SAVE'})
    value: FloatProperty(name='Value', description='Move: the distance in the project\'s units; Rotate: degrees; Scale: the factor', default=0.0, options={'SKIP_SAVE'})
    delta: FloatVectorProperty(name='Delta', description='Move without an axis: the offset in the model\'s space', size=3, default=(0.0, 0.0, 0.0), options={'SKIP_SAVE'})
    axis_vector: FloatVectorProperty(name='Axis Vector', description='Rotate without an axis: the unit axis in the model\'s space', size=3, default=(0.0, 1.0, 0.0), options={'SKIP_SAVE'})
    scale: FloatVectorProperty(name='Scale', description='Scale without an axis: the factors along the model\'s x, y, z', size=3, default=(1.0, 1.0, 1.0), options={'SKIP_SAVE'})
    cancel_: BoolProperty(name='Cancel', description='Non-modal: apply, then cancel (the modal\'s Esc: everything back, no step)', default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return can_run(context, cls.poll_message_set)

    @classmethod
    def description(cls, context, properties):
        if not select.has_any(context.scene.hairmesh):
            return NEED
        return blocked_reason(context) or cls.__doc__

    # ---- the modal ----
    def invoke(self, context, event):
        from .ops_select import View
        v = View.of(context)
        if v is None:
            return self.execute(context)
        sel = select.load(context.scene.hairmesh).prune(v.P)
        tg = targets(v.P, sel, self.report)                         # (none: the reason in the status bar; the key stays ours)
        if tg is None:
            return {'CANCELLED'}
        self.view = v; self.P = v.P; self.sel = sel
        self.tg = tg; self.anchors = tg.anchors
        self.undo = _Undo(v.P, tg.anchors)
        self.follow = MeshFollow(v.root, tg); self.M = None          # (4.0: the converted meshes inside follow, baked on confirm)
        from . import pick
        from .ops_draw import head_visible
        hd = v.P.head
        self.surface_ok = hd is not None and hd.visible and head_visible(v.root) and not tg.meshes   # (lot 4.7b item 6: Alt follows the head — not with converted meshes, moved whole by the matrix)
        self.surface = None; self.surface_d0 = None                # (the head's BVH at the first Alt)
        from .ops_gizmo import frame
        self.gz_q = frame(v.P, tg, context.scene.hairmesh.gizmo_space)[1]   # (the gizmo's own axes, when it is on: not the constraint's)
        self.cons = None                                             # (axis, local, plane) or None
        self.typed = ''
        self.m0 = Vector((event.mouse_region_x, event.mouse_region_y)); self.virt = self.m0.copy(); self.last = self.m0.copy()
        self.cam = v.cam().clone().normalize()
        self.pivot_world = v.root.matrix_world @ Vector(to_blender(tg.pivot))
        self.ps = pixel_size(context, v.root, self.pivot_world)
        self.step = grid_step(self.ps)
        self._start(context, event)
        context.window_manager.modal_handler_add(self)
        self._header(context)
        return {'RUNNING_MODAL'}

    def _start(self, context, event):
        """a drag from the start (again whenever the constraint changes: from the anchors as they were — marked, so the
        curves, the drawing and the live walls follow even when the next move computes nothing)"""
        self.undo.restore()
        if hasattr(self, 'follow'):
            self.follow.reset()
        CM.markAnchorsDirty(self.P, self.anchors); self.P.rev += 1
        cons = self.cons
        space = 'local' if cons and cons[1] else 'world'
        self.st = edit.startGizmo(self.P, self.tg, self.mode, space)
        self.hit0 = self._hit_cam(self.m0)
        self.hit0p = self._hit_plane(self.m0)
        self.t0 = self._param(self.m0)
        c = self.view.project(self.st.pos)
        self.c = Vector((c[0], c[1])) if c is not None else self.m0.copy()
        if not hasattr(self, 'turn'):                                # (R: the turn so far stays when the axis changes)
            self.turn = 0.0; self.a_last = math.atan2(self.m0.y - self.c.y, self.m0.x - self.c.x)
        self.d0 = max((self.m0 - self.c).length, 1.0)
        self.result = (self.st.pos.clone(), self.st.quat.clone(), V3(1, 1, 1))
        set_drag(self.st.pos, self.gz_q, self.view.root)             # (the gizmo, when on, drawn on the proxy of this drag)

    def _ray(self, m):
        from .ops_draw import model_ray
        v = self.view
        return model_ray(v.root, v.region, v.rv3d, (m.x, m.y))

    def _dir(self, ax):
        return applyQuaternion(edit.AXIS_LOCAL[ax].clone(), self.st.quat)

    def _param(self, m):
        if not self.cons or self.cons[2] or self.mode != 'translate':
            return None
        r = self._ray(m); o = r.origin; d = r.direction; a = self._dir(self.cons[0]); p = self.st.pos
        w = o.clone().sub(p); b = a.dot(d); den = 1 - b * b
        if abs(den) < 1e-9:
            return None
        return (a.dot(w) - b * d.dot(w)) / den

    def _hit_cam(self, m):
        """the cursor on the plane facing the view through the pivot (always defined)"""
        if self.mode != 'translate':
            return None
        return edit.planeHit(self._ray(m), self.st.pos, self.cam)

    def _hit_plane(self, m):
        """the cursor on the constraint plane (Shift+axis) through the pivot; None when the view sees it edge-on (the ray
        nearly in the plane: an intersection far away, or none) — the move then follows the view plane, projected"""
        if self.mode != 'translate' or not (self.cons and self.cons[2]):
            return None
        n = self._dir(self.cons[0]); r = self._ray(m)
        if abs(r.direction.dot(n)) < 0.05:
            return None
        return edit.planeHit(r, self.st.pos, n)

    def _value(self):
        try:
            return float(self.typed) if self.typed not in ('', '-', '.', '-.') else None
        except ValueError:
            return None

    def _compute(self, event):
        st = self.st; ctrl = event.ctrl; cons = self.cons
        typed = self._value()
        axes = [cons[0]] if cons and not cons[2] else [k for k in 'XYZ' if not cons or k != cons[0]]
        if self.mode == 'translate':
            if typed is not None:
                return proxy(st, 'translate', axes[0], typed)
            if cons and not cons[2]:
                t = self._param(self.virt)
                if t is not None and self.t0 is not None:
                    d = t - self.t0
                elif self.ps:
                    # the axis points at the view (no line to follow on the screen): up / down moves along it, up away
                    # from the camera (as the original's axisDepthDrag, 4483)
                    d = (self.virt.y - self.m0.y) * self.ps * (1 if self._dir(cons[0]).dot(self.cam) > 0 else -1)
                else:
                    return None
                if ctrl:
                    d = snap(d, self.step)
                return proxy(st, 'translate', cons[0], d)
            hp = self._hit_plane(self.virt) if self.hit0p is not None else None
            if hp is not None:
                d = hp.sub(self.hit0p)
            else:
                h = self._hit_cam(self.virt)
                if h is None or self.hit0 is None:
                    return None
                d = h.sub(self.hit0)
            if ctrl or cons:
                # the offset in the frame's axes: the excluded axis taken out (a plane), each snapped (Ctrl)
                pos = st.pos.clone()
                for k in axes:
                    u = self._dir(k); x = d.dot(u)
                    pos.addScaledVector(u, snap(x, self.step) if ctrl else x)
                return pos, st.quat.clone(), V3(1, 1, 1)
            return st.pos.clone().add(d), st.quat.clone(), V3(1, 1, 1)
        if self.mode == 'rotate':
            ang = math.radians(typed) if typed is not None else self.turn
            if typed is None and ctrl:
                ang = math.radians(snap(math.degrees(ang), 5.0))
            if cons:
                u = self._dir(cons[0])
                sign = 1 if typed is not None or u.dot(self.cam) < 0 else -1   # (counter-clockwise on the screen = about an axis toward the viewer)
                return proxy(st, 'rotate', cons[0], ang * sign)
            q = edit.gizmoRotation(st.quat, self.cam.clone().negate(), ang, 'world')   # (the view axis, as the gizmo's view ring)
            return st.pos.clone(), q, V3(1, 1, 1)
        f = typed if typed is not None else (self.virt - self.c).length / self.d0
        if typed is None and ctrl:
            f = snap(f, 0.1)
        if abs(f) < 0.01:                                           # never through 0 (a typed "0.5" passes "0"): the frozen handles scale by increments and would never come back
            f = -0.01 if f < 0 else 0.01
        if not cons:
            return st.pos.clone(), st.quat.clone(), V3(f, f, f)
        return proxy(st, 'scale', cons[0], f, plane=cons[2])

    def _apply(self, context, event, alt=None):
        r = self._compute(event)
        if r is None:
            return
        self.result = r
        st = self.st; P = self.P
        set_drag(r[0], self.gz_q, self.view.root)
        self.M = edit.gizmoMatrix(st, *r)
        edit.gizmoStep(P, st, self.M)
        # lot 4.7b item 6 (ours): G with Alt held slides the anchors over the head, each at the distance it had when Alt
        # went down (as Blender's own snapping keys: let go, the free move again)
        alt = event.alt if alt is None else alt
        if alt and self.mode == 'translate' and getattr(self, 'surface', None) is None and getattr(self, 'surface_ok', False):
            from . import pick
            self.surface = pick.head_nearest(P)
        near = getattr(self, 'surface', None) if getattr(self, 'surface_ok', False) else None
        if self.mode == 'translate' and alt and near is not None and st.list:
            if getattr(self, 'surface_d0', None) is None:
                self.surface_d0 = edit.surfaceDists(st.list, near)
            edit.followSurface(P, st.list, self.surface_d0, near)
        else:
            self.surface_d0 = None
        self.follow.show(self.M)
        edit.gizmoMoved(P, st)
        cand = None
        if self.mode == 'translate' and len(st.list) == 1:
            cand = W.findWeldCandidate(P, st.list[0], self.view.anchors(self.sel), self.view.project)
        drawmod.weld_cand = cand.id if cand is not None else None
        live.schedule(self.view.root.name)
        if self.mode == 'translate':
            from .ops_gizmo import attach_preview
            attach_preview(self.view, self.view.P, [a for a, _p in self.undo.pos])   # (4.4: the connectors and the Attach dot; R / S never attach)
        context.area.tag_redraw()
        self._header(context)

    def _header(self, context):
        cons = self.cons
        what = 'Free' if not cons else f'{"Plane Not " if cons[2] else ""}{cons[0]}{" Local" if cons[1] else ""}'
        if self.typed:
            val = f'[{self.typed}{UNITS[self.mode]}]'
        else:
            pos, quat, sc = self.result
            if self.mode == 'translate':
                val = f'{pos.clone().sub(self.st.pos).length():.2f}'
            elif self.mode == 'rotate':
                val = f'{math.degrees(self.turn):.1f}°'
            else:
                val = f'{max((sc.x, sc.y, sc.z), key=lambda x: abs(x - 1)):.3f}×'
        follow = self.mode == 'translate' and getattr(self, 'surface_ok', False)
        live.header(context.area, f'HairMesh {LABEL[self.mode]} {what}: {val}', (*MODAL_KEYS, FOLLOW_KEY) if follow else MODAL_KEYS)

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
        t, v = event.type, event.value
        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            m = Vector((event.mouse_region_x, event.mouse_region_y))
            self.virt += (m - self.last) * (0.1 if event.shift else 1.0)
            self.last = m
            if self.mode == 'rotate':
                a = math.atan2(self.virt.y - self.c.y, self.virt.x - self.c.x)
                d = (a - self.a_last + math.pi) % (2 * math.pi) - math.pi
                self.turn += d; self.a_last = a
            self._apply(context, event)
            return {'RUNNING_MODAL'}
        if t in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._apply(context, event)
            return {'RUNNING_MODAL'}
        if t in {'LEFT_ALT', 'RIGHT_ALT'} and v in {'PRESS', 'RELEASE'}:
            self._apply(context, event, alt=v == 'PRESS')           # (lot 4.7b item 6: Alt follows the head)
            return {'RUNNING_MODAL'}
        if v == 'PRESS' and t in KEYS:
            c = self.cons
            plane = event.shift
            if c and c[0] == t and c[2] == plane:
                self.cons = None if c[1] else (t, True, plane)       # the same key again: local, then none
            else:
                self.cons = (t, False, plane)
            self._start(context, event); self._apply(context, event)
            return {'RUNNING_MODAL'}
        if v == 'PRESS' and (t in DIGITS or t in {'MINUS', 'NUMPAD_MINUS', 'BACK_SPACE'}):
            if t == 'BACK_SPACE':
                self.typed = self.typed[:-1]
            elif t in {'MINUS', 'NUMPAD_MINUS'}:
                self.typed = self.typed[1:] if self.typed.startswith('-') else '-' + self.typed
            elif not (DIGITS[t] == '.' and '.' in self.typed):
                self.typed += DIGITS[t]
            self._apply(context, event)
            return {'RUNNING_MODAL'}
        if (t in {'RET', 'NUMPAD_ENTER'} and v == 'PRESS') or (t == 'LEFTMOUSE' and v == 'PRESS'):
            return self._finish(context)
        if t in {'ESC', 'RIGHTMOUSE'} and v == 'PRESS':
            return self._cancel(context)
        if t in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        live.header(context.area, None)
        set_drag(None, None)
        from .ops_gizmo import attach_drag_end
        attach_drag_end()
        drawmod.snap_curve = None; drawmod.weld_cand = None
        v = self.view
        if not self.st.moved:
            live.cancel(v.root.name)
            return {'CANCELLED'}
        self._remember()
        self.follow.bake(self.P, self.st, self.M)                   # (the gizmo's release, 4646: baked into the converted meshes)
        if self.mode == 'rotate':
            edit.rotateOwnAxes(self.P, self.st, self.result[1])     # (the gizmo's release, 4653: the items' own axes turn with them)
        commit_release(context, v.root, self.P, self.follow.baked)  # one undo step
        context.area.tag_redraw()
        if self.mode == 'translate' and len(self.st.list) == 1 and len(self.sel.anchors) == 1:
            check_weld(self.view, self.P, self.sel, self.st.list[0])
        return {'FINISHED'}

    def _remember(self):
        """the result as the non-modal properties (Adjust Last Operation redoes it through execute)"""
        pos, quat, sc = self.result; st = self.st
        c = self.cons; typed = self._value()
        self.local = bool(c and c[1])                               # (the scale factors are along the drag's frame)
        if c and typed is not None and not (self.mode == 'translate' and c[2]):
            self.axis, self.plane, self.value = c[0], c[2], typed
            return
        self.axis = 'NONE'; self.plane = False
        if self.mode == 'translate':
            self.delta = pos.clone().sub(st.pos).toArray()
        elif self.mode == 'rotate':
            d = quat.clone().multiply(st.quat.clone().invert())
            w = max(-1.0, min(1.0, d.w)); s_ = math.sqrt(max(0.0, 1 - w * w))
            self.value = math.degrees(2 * math.acos(w))
            self.axis_vector = (d.x / s_, d.y / s_, d.z / s_) if s_ > 1e-9 else (0.0, 1.0, 0.0)
        else:
            self.scale = sc.toArray()

    def _cancel(self, context):
        live.header(context.area, None)
        set_drag(None, None)
        drawmod.snap_curve = None; drawmod.weld_cand = None
        self.undo.restore(); self.follow.reset()
        CM.markAnchorsDirty(self.P, self.anchors); self.P.rev += 1
        live.update(context, self.view.root, commit=False); context.area.tag_redraw()
        return {'CANCELLED'}

    def cancel(self, context):
        # Blender ends the modal itself (a window closed, a file loaded): the model back in memory only
        modal_keys.clear(context)
        try:
            self.undo.restore()
            CM.markAnchorsDirty(self.P, self.anchors); self.P.rev += 1
            live.cancel(self.view.root.name)
            self.follow.reset()
        except Exception:
            pass
        drawmod.snap_curve = None; drawmod.weld_cand = None
        from .ops_gizmo import attach_drag_end
        attach_drag_end()
        set_drag(None, None)

    # ---- non-modal (tests, the stress test) ----
    def execute(self, context):
        root, P, sel = select.current(context)
        tg = targets(P, sel, self.report)
        if tg is None:
            return {'CANCELLED'}
        anchors = tg.anchors
        space = 'local' if self.local else 'world'
        st = edit.startGizmo(P, tg, self.mode, space)
        undo = _Undo(P, anchors) if self.cancel_ else None
        if self.axis != 'NONE':
            v = f32(self.value)
            r = proxy(st, self.mode, self.axis, math.radians(v) if self.mode == 'rotate' else v, plane=self.plane)
        elif self.mode == 'translate':
            r = (st.pos.clone().add(V3(*(f32(x) for x in self.delta))), st.quat.clone(), V3(1, 1, 1))
        elif self.mode == 'rotate':
            r = (st.pos.clone(), edit.gizmoRotation(st.quat, V3(*(f32(x) for x in self.axis_vector)), math.radians(f32(self.value)), 'world'), V3(1, 1, 1))
        else:
            r = (st.pos.clone(), st.quat.clone(), V3(*(f32(x) for x in self.scale)))
        M = edit.gizmoMatrix(st, *r)
        edit.gizmoStep(P, st, M)
        edit.gizmoMoved(P, st)
        if undo is not None:                                        # the modal's Esc: everything back, nothing written
            undo.restore()
            CM.markAnchorsDirty(P, anchors); P.rev += 1
            live.update(context, root, commit=False)
            return {'CANCELLED'}
        baked = edit.bakeMeshes(P, st, M) if st.meshes else []    # (the release, 4646)
        if self.mode == 'rotate':
            edit.rotateOwnAxes(P, st, r[1])
        commit_release(context, root, P, baked)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_grab_anchors(_Transform, bpy.types.Operator):
    """Move the HairMesh selection — anchors, or the curves, walls and groups selected (G): follows the mouse on the view plane; X / Y / Z lock an axis (again: local), Shift+X / Y / Z a plane; type a distance; Ctrl: grid steps; Shift: fine; Enter / LMB confirm, Esc / RMB cancel"""
    bl_idname = 'hairmesh.grab_anchors'
    bl_label = 'HairMesh Move Selection'
    mode = 'translate'


class HAIRMESH_OT_rotate_anchors(_Transform, bpy.types.Operator):
    """Turn the HairMesh selection about the last anchor, or the center of the selected items (R): the angle about it on the screen; X / Y / Z lock an axis (again: local); type degrees; Ctrl: 5° steps; Shift: fine; Enter / LMB confirm, Esc / RMB cancel"""
    bl_idname = 'hairmesh.rotate_anchors'
    bl_label = 'HairMesh Rotate Selection'
    mode = 'rotate'


class HAIRMESH_OT_scale_anchors(_Transform, bpy.types.Operator):
    """Scale the HairMesh selection about the last anchor, or the center of the selected items (S): the distance to it on the screen; X / Y / Z one axis (again: local), Shift+X / Y / Z the two others; type a factor; Ctrl: 0.1 steps; Shift: fine; Enter / LMB confirm, Esc / RMB cancel"""
    bl_idname = 'hairmesh.scale_anchors'
    bl_label = 'HairMesh Scale Selection'
    mode = 'scale'


_CLASSES = (HAIRMESH_OT_grab_anchors, HAIRMESH_OT_rotate_anchors, HAIRMESH_OT_scale_anchors)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
