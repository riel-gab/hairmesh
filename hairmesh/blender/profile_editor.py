# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Step 4.6b (D4.6, approved): the profile editor — a modal operator drawing the original's profile frames
(profileEditor 6824) with gpu over the 3D View: the panel's profile (one frame: the Profile tab's Edit Profile) or a
modifier's three profiles side by side (Start, Middle, End: its sheet's Edit Profiles). LMB drags a point (Shift snaps
to 0.05; glued points move together, Ctrl pulls one apart; a point brought within 8 px of its neighbor glues), RMB
adds a point on the nearest edge (on a modifier: into the profiles after it too, glued into the ones before), a
double click deletes one (from all three; a profile keeps 3), and so do X / Delete (the point under the pointer, else
the one dragged last: not in the original, docs/features-adicionadas.md). Ctrl+Z and Ctrl+Shift+Z step through the
edits made since the editor opened (the Knife's keys, not in the original either: _undo). Enter, Esc or a click outside
the frames ends it.

The double click is paired here (clicks.DoubleClick): Blender turns every DOUBLE_CLICK into a PRESS before a
modal() sees it, so the delete never worked by hand (the owner's 4.4 manual test, step 41). A point moves only once
the pointer leaves Blender's drag threshold (Preferences > Input > Drag Threshold): a click with a hand's tremor
changes and commits nothing, so its release is cheap and the next press still pairs with it.

The rules are core/profedit.py (bit for bit with the original's, the frame in the original's pixels: the UI scale is
taken out). While a point moves, the modifier's surface is drawn here from rebuildTube (the drawing follows the
mouse); its live object, the project text and the undo step follow when the point is let go — one undo step per
edit, as the original's commit on pointerup."""
import time

import bpy
from bpy.props import EnumProperty

from ..core import profedit as PE, tube as TB
from . import clicks, live, owner, pick, select, session
from .coords import to_blender

GAP_PX = 14                                                         # between two frames, in the original's pixels
MARGIN_PX = 48                                                      # from the region's bottom edge
STATE = {'op': None}                                                # the running editor (the draw handler reads it)
HEADER = 'HairMesh Profile Editor'
KEYS = ((('LMB Drag',), 'Move a Point'), (('Shift',), 'Snap'), (('Ctrl',), 'Pull a Glued One Apart'), (('RMB',), 'Add'), (('Double-Click',), 'Delete'), (('X',), 'Delete'),
        (('Ctrl+Z',), 'Undo'), (('Ctrl+Shift+Z',), 'Redo'), (('Enter', 'Esc'), 'Exit'))


def editor_state(ui):
    """what invoke sets besides the target (tests/test_blender_c47.py builds an editor from it)"""
    return {'drag': None,                                           # (frame k, point i, the run moved)
            'preview': None,                                        # (the modifier's surface while a point moves: world lines)
            'hover': None, 'changed': False,
            'press_xy': None, 'moving': False,                      # (the press; a drag once past the threshold)
            'last': None,                                           # (frame k, point i) dragged last: what X / Delete take with no point under the pointer
            'steps': 0, 'undone': 0,                                # (a modifier: its edits here, and those undone, are steps of Blender's history)
            'past': [], 'future': [], 'kept': None,                 # (the panel's profile has no step there: its own history, panel_state's)
            'clicks': clicks.DoubleClick(ui)}


TIMES = []                                                          # the last moves' cost, seconds (tools/sim_c46_shots.py reads it)


def frames_layout(region_w, n, ui):
    """the frames' boxes in region pixels: [(x0, y_top, w, h)], side by side, centered, MARGIN_PX above the bottom"""
    w = PE.FRAME_W * ui; h = PE.FRAME_H * ui; g = GAP_PX * ui
    total = n * w + (n - 1) * g
    x = (region_w - total) / 2
    y_top = MARGIN_PX * ui + h
    return [(x + k * (w + g), y_top, w, h) for k in range(n)]


def region_to_frame(box, mx, my, ui):
    """region pixels (y up) -> the frame's own pixels (y down, the original's scale)"""
    x0, y_top, _w, _h = box
    return (mx - x0) / ui, (y_top - my) / ui


def frame_to_region(box, fx, fy, ui):
    x0, y_top, _w, _h = box
    return x0 + fx * ui, y_top - fy * ui


class HAIRMESH_OT_profile_editor(bpy.types.Operator):
    """Edit the profile in frames over the viewport: drag a point (Shift snaps; glued points move together, Ctrl pulls one apart), right click adds one, a double click or X / Delete deletes one, Ctrl+Z and Ctrl+Shift+Z step through the edits; Enter, Esc or a click outside ends"""
    bl_idname = 'hairmesh.profile_editor'
    bl_label = 'Edit Profile'
    bl_options = {'REGISTER'}                                       # (each edit pushes its own undo step: _commit; nothing edited, nothing pushed)

    target: EnumProperty(name='Target', items=(('TUBE', 'Modifier', "The selected modifier's three profiles", 0),
                                               ('PANEL', 'Panel', "The Profile tab's profile (what Apply sweeps)", 1)), default='TUBE', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not (owner.visible(context) and session.find_root(context) is not None and context.area is not None and context.area.type == 'VIEW_3D'):
            return False
        return True

    # ---- the profiles edited ----
    def profiles(self):
        if self.target == 'PANEL':
            return [self.panel]
        root, P, sel = select.current(bpy.context)
        t = P.items.get(self.tube_id) if P is not None else None
        return TB.ownProfs(t) if t is not None else []

    def tube(self):
        _r, P, _s = select.current(bpy.context)
        return P.items.get(self.tube_id) if P is not None and self.tube_id is not None else None

    def invoke(self, context, event):
        from .ops_profile import panel_profile, selected_tube
        self.root_name = session.find_root(context).name
        self.tube_id = None
        if self.target == 'TUBE':
            _r, _P, t = selected_tube(context)
            if t is None:
                self.report({'INFO'}, 'Select a Profile modifier first')
                return {'CANCELLED'}
            self.tube_id = t.id
        else:
            self.panel = [list(q) for q in panel_profile(context)]
        self.ui = context.preferences.system.ui_scale
        self.is_tube = self.target == 'TUBE'
        for k, v in editor_state(self.ui).items():
            setattr(self, k, v)
        if self.target == 'PANEL':
            self.kept = self.panel_state(context)
        STATE['op'] = self
        if STATE.get('handle') is None:
            STATE['handle'] = bpy.types.SpaceView3D.draw_handler_add(draw_px, (), 'WINDOW', 'POST_PIXEL')
            STATE['handle3d'] = bpy.types.SpaceView3D.draw_handler_add(draw_3d, (), 'WINDOW', 'POST_VIEW')
        context.window_manager.modal_handler_add(self)
        live.header(context.area, HEADER, KEYS)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def boxes(self, context):
        return frames_layout(context.region.width, len(self.profiles()), self.ui)

    def hit(self, context, event):
        """(frame k, x, y in the frame's pixels) under the pointer, or None outside the frames"""
        for k, box in enumerate(self.boxes(context)):
            fx, fy = region_to_frame(box, event.mouse_region_x, event.mouse_region_y, self.ui)
            if 0 <= fx <= PE.FRAME_W and 0 <= fy <= PE.FRAME_H:
                return k, fx, fy
        return None

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            self._end(context)
            raise

    def _modal(self, context, event):
        t, v = event.type, event.value
        if t not in {'LEFTMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self.clicks.other(event)                                # (a right click, a key: no double click across it)
        if t == 'MOUSEMOVE':
            if self.drag is not None:
                if not self.moving:
                    th = clicks.drag_threshold(self.ui)
                    px, py = self.press_xy
                    if abs(event.mouse_region_x - px) <= th and abs(event.mouse_region_y - py) <= th:
                        return {'RUNNING_MODAL'}                    # (a tremor: no drag yet, WM_event_drag_test)
                    self.moving = True; self.clicks.dragged()
                t0 = time.perf_counter()
                k, i, group = self.drag
                box = self.boxes(context)[k]
                fx, fy = region_to_frame(box, event.mouse_region_x, event.mouse_region_y, self.ui)
                PE.move(self.profiles()[k], group, fx, fy, event.shift, event.ctrl, glue=self.target == 'TUBE')
                tb = self.tube()
                if tb is not None:
                    tb.dirty = True
                    self.preview = surface_lines(self.root_name, tb)   # (drawn by draw_3d; the live object follows on the release)
                self.changed = True
                TIMES.append(time.perf_counter() - t0)
                del TIMES[:-200]
            else:
                h = self.hit(context, event)
                self.hover = (h[0], PE.nearest(self.profiles()[h[0]], h[1], h[2])) if h is not None else None
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if t == 'Z' and v == 'PRESS' and event.ctrl and self.drag is None:
            self._undo(context, event.shift)
            return {'RUNNING_MODAL'}
        if t in {'X', 'DEL'} and v == 'PRESS' and self.drag is None:
            at = self.hover if self.hover is not None and self.hover[1] >= 0 else self.last
            if at is None:
                self.report({'INFO'}, 'Point at a point to delete it (or drag one first)')
            else:
                self._delete(context, *at)
            return {'RUNNING_MODAL'}
        if t == 'LEFTMOUSE' and v in {'PRESS', 'DOUBLE_CLICK'}:      # (a modal only ever sees PRESS: clicks.py)
            dbl = self.clicks.press(event)
            h = self.hit(context, event)
            if h is None:
                return self._finish(context)                        # (a click outside the frames: the editor ends)
            k, fx, fy = h; pts = self.profiles()[k]; i = PE.nearest(pts, fx, fy)
            if dbl and i >= 0:
                self._delete(context, k, i)                         # (the dblclick, 6900)
                return {'RUNNING_MODAL'}
            if i >= 0:
                self.drag = (k, i, PE.press(pts, i, event.ctrl, glue=self.target == 'TUBE'))
                self.press_xy = (event.mouse_region_x, event.mouse_region_y); self.moving = False
            return {'RUNNING_MODAL'}
        if t == 'LEFTMOUSE' and v == 'RELEASE':
            self.clicks.release(event)
            if self.drag is not None:
                if self.moving:
                    self.last = self.drag[:2]
                self.drag = None; self.preview = None; self.moving = False
                if self.changed:
                    self._commit(context)
            return {'RUNNING_MODAL'}
        if t == 'RIGHTMOUSE' and v == 'PRESS' and event.shift:
            return {'PASS_THROUGH'}                                 # (Shift + RMB: the HairMesh pie, as the original's window handler opens its main pie, 7968)
        if t == 'RIGHTMOUSE' and v == 'PRESS':
            h = self.hit(context, event)
            if h is None:
                return self._finish(context)
            k, fx, fy = h; Ps = self.profiles()
            bi, s, q = PE.insert_at(Ps[k], fx, fy, event.shift)
            if self.target == 'TUBE':
                PE.insert_three(Ps, k, bi, s, q)
            else:
                Ps[k].insert(bi + 1, q)
            self.last = None; self.hover = None
            self._commit(context)
            return {'RUNNING_MODAL'}
        if t in {'RET', 'NUMPAD_ENTER', 'ESC'} and v == 'PRESS':
            return self._finish(context)
        if t in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}:
            return {'PASS_THROUGH'}                                 # (the view moves; the frames stay)
        return {'RUNNING_MODAL'}

    def _delete(self, context, k, i):
        """the dblclick (6900-6906): point i goes — from the three profiles of a modifier (onDelete), a profile keeps 3"""
        Ps = self.profiles()
        if not PE.can_delete(Ps[k]):
            self.report({'INFO'}, PE.TOO_FEW)
            return
        for A in (Ps if self.target == 'TUBE' else [Ps[k]]):
            A.pop(i)
        self.last = None; self.hover = None
        self._commit(context)

    def panel_state(self, context):
        """the panel's profile with what the Profile tab reads of it (the preset menu's name, the edited points)"""
        h = context.scene.hairmesh_profile
        return [list(q) for q in self.panel], h.get('preset_name'), h.get('edited')

    def _undo(self, context, redo):
        """Ctrl+Z / Ctrl+Shift+Z: the last edit made since the editor opened, never one from before. A modifier's edits are
        steps of Blender's history and are walked there, so leaving the editor leaves that history right; the panel's profile
        has no step of its own and keeps its own list"""
        if self.target == 'PANEL':
            src, dst = (self.future, self.past) if redo else (self.past, self.future)
            done = bool(src)
            if done:
                dst.append(self.kept)
                self.kept = src.pop()
                self.panel = [list(q) for q in self.kept[0]]
                h = context.scene.hairmesh_profile
                for key, v in zip(('preset_name', 'edited'), self.kept[1:]):
                    if v is not None:
                        h[key] = v
                    else:
                        h.pop(key, None)
        else:
            done = (self.undone if redo else self.steps) > 0
            if done:
                (bpy.ops.ed.redo if redo else bpy.ops.ed.undo)()
                self.steps += 1 if redo else -1; self.undone += -1 if redo else 1
        if not done:
            self.report({'INFO'}, f'Nothing to {"redo" if redo else "undo"} in the editor')
            return
        self.last = None; self.hover = None
        context.area.tag_redraw()

    def _commit(self, context):
        """an edit done (the original's onDone -> commit): the project text, the live object, one undo step"""
        self.changed = False
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return
        if self.target == 'PANEL':
            import json
            context.scene.hairmesh_profile['edited'] = json.dumps(self.panel)
            from .ops_profile import EDITED
            context.scene.hairmesh_profile['preset_name'] = EDITED    # (the preset menu says "— edited —", 6943)
            self.past.append(self.kept); self.future.clear()
            self.kept = self.panel_state(context)
            context.area.tag_redraw()
            return
        P = session.get_project(root)
        session.save_project(root, P)
        live.update(context, root)
        bpy.ops.ed.undo_push(message='HairMesh Profile Edit')
        self.steps += 1; self.undone = 0
        context.area.tag_redraw()

    def _end(self, context):
        STATE['op'] = None
        for k in ('handle', 'handle3d'):
            h = STATE.pop(k, None)
            if h is not None:
                bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        try:
            live.header(context.area, None); context.area.tag_redraw()
        except Exception:
            pass

    def _finish(self, context):
        if self.changed:
            self._commit(context)
        self._end(context)
        return {'FINISHED'}

    def cancel(self, context):
        self._end(context)


# ---------------- drawing ----------------

GRID = (0.17, 0.17, 0.17, 1.0); AXIS = (0.29, 0.29, 0.29, 1.0); BG = (0.10, 0.10, 0.10, 0.92); BORDER = (0.23, 0.23, 0.23, 1.0)
FILL = (0.56, 0.47, 0.88, 0.28); LINE = (0.73, 0.64, 1.0, 1.0); PT = (0.89, 0.84, 1.0, 1.0); HOVER = (1.0, 0.82, 0.48, 1.0)
DRAG = (1.0, 0.60, 0.18, 1.0); GLUED = (0.37, 0.82, 1.0, 1.0); CENTRE = (0.79, 0.72, 1.0, 1.0)
LABEL = (0.75, 0.75, 0.75, 1.0)                                     # the labels inside a frame (the original's #6d6d6d was too dim over the viewport: the outside review)
NAMES = ('Start', 'Middle', 'End')
LABEL_UP = '↑ Outwards'; LABEL_ACROSS = '← Across the Lock →'
# the original's widths (ctx.lineWidth, 6835-6849), at 100 %: pick.line_scale, which also sizes the dots
GRID_PX = 1.0; OUTLINE_PX = 1.5; RING_PX = 2.0


def draw_px():
    op = STATE.get('op')
    ctx = bpy.context
    if op is None or ctx.region is None:
        return
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils.geometry import tessellate_polygon
    from .draw import polyline2d
    ui = op.ui
    lw = pick.line_scale(ctx)
    region = ctx.region
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    Ps = op.profiles()
    for k, box in enumerate(frames_layout(ctx.region.width, len(Ps), ui)):
        gpu.state.blend_set('ALPHA')                                # (per frame: blf.draw leaves another blend; the fill of every frame after the first came out bright, premultiplied)
        x0, y_top, w, h = box
        rect = [(x0, y_top - h), (x0 + w, y_top - h), (x0 + w, y_top), (x0, y_top)]
        sh.uniform_float('color', BG); batch_for_shader(sh, 'TRI_FAN', {'pos': rect}).draw(sh)
        polyline2d(region, [*rect, rect[0]], BORDER, GRID_PX * lw, smooth=False)
        for v in (-1, -0.5, 0, 0.5, 1):                             # (the grid every 0.5, the axes brighter: 6834-6838)
            gx, _ = frame_to_region(box, *PE.to_screen([v, 0]), ui); _, gy = frame_to_region(box, *PE.to_screen([0, v]), ui)
            polyline2d(region, [(gx, y_top - h), (gx, y_top), (x0, gy), (x0 + w, gy)], AXIS if v == 0 else GRID, GRID_PX * lw, 'LINES', smooth=False)
        pts = Ps[k]
        scr = [frame_to_region(box, *PE.to_screen(q), ui) for q in pts]
        if len(scr) >= 3:
            try:
                tris = tessellate_polygon([[(p[0], p[1], 0.0) for p in scr]])
                sh.uniform_float('color', FILL)
                batch_for_shader(sh, 'TRIS', {'pos': [scr[i] for t in tris for i in t]}).draw(sh)
            except Exception:
                pass
            polyline2d(region, [*scr, scr[0]], LINE, OUTLINE_PX * lw)
        cx, cy = frame_to_region(box, PE.FRAME_W / 2, PE.FRAME_H / 2, ui)
        _dot(sh, batch_for_shader, cx, cy, 2.5 * lw, CENTRE)        # (the curve passes here, 6840)
        for i, (sx, sy) in enumerate(scr):
            glued = op.is_tube and len(TB.gluedRun(pts, i)) > 1
            if glued:
                polyline2d(region, _ring(sx, sy, 8 * lw), GLUED, RING_PX * lw)
            on_drag = op.drag is not None and op.drag[0] == k and i in op.drag[2]
            on_hover = op.hover is not None and op.hover == (k, i)
            _dot(sh, batch_for_shader, sx, sy, (5.5 if on_drag or on_hover else 4.5) * lw, DRAG if on_drag else HOVER if on_hover else PT)
        blf.size(0, 10 * ui); blf.color(0, *LABEL)
        blf.position(0, x0 + 4 * ui, y_top - 12 * ui, 0); blf.draw(0, LABEL_UP)
        blf.position(0, x0 + w - blf.dimensions(0, LABEL_ACROSS)[0] - 4 * ui, y_top - h + 5 * ui, 0); blf.draw(0, LABEL_ACROSS)
        if op.is_tube:
            blf.size(0, 11 * ui); blf.color(0, 0.81, 0.81, 0.81, 1.0)
            blf.position(0, x0, y_top + 6 * ui, 0); blf.draw(0, NAMES[k])
    gpu.state.blend_set('NONE')


def _dot(sh, bfs, x, y, r, col, n=12):
    import math
    sh.uniform_float('color', col)
    bfs(sh, 'TRI_FAN', {'pos': [(x, y)] + [(x + r * math.cos(2 * math.pi * j / n), y + r * math.sin(2 * math.pi * j / n)) for j in range(n + 1)]}).draw(sh)


def _ring(x, y, r, n=24):
    """pure: a closed loop of n + 1 points around (x, y)"""
    import math
    return [(x + r * math.cos(2 * math.pi * j / n), y + r * math.sin(2 * math.pi * j / n)) for j in range(n + 1)]


def surface_lines(root_name, t):
    """the modifier's surface (rebuildTube: its loop and edge lines) in world space, computed in the modal on a move —
    never in a draw callback (it writes it.dirty, it.cl and maybe rRef); None when it has none"""
    from mathutils import Vector
    root = bpy.data.objects.get(root_name)
    if root is None:
        return None
    from . import slow
    try:
        with slow.part('Profile modifier surface', t.name):           # (4.7b: with curls this was ~150 ms per move — extras' frames now kept, tube._extras_frames)
            m, _w = TB.rebuildTube(session.get_project(root), t)
    except Exception:                                               # (one surface must not take the editor down)
        return None
    if m is None:
        return None
    M = root.matrix_world
    pos = [tuple(M @ Vector(to_blender(q))) for q in m.pos]
    return [pos[i] for a, b in m.loops for i in (a, b)] + [pos[i] for a, b in m.edges for i in (a, b)]


def draw_3d():
    """while a point moves, the modifier's surface the modal computed (its live object follows on the release)"""
    op = STATE.get('op')
    lines = getattr(op, 'preview', None) if op is not None else None
    if not lines:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    ctx = bpy.context
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
    sh.uniform_float('lineWidth', max(1.0, GRID_PX * pick.line_scale(ctx)))
    sh.uniform_bool('lineSmooth', True)
    sh.uniform_float('color', (0.73, 0.64, 1.0, 0.9))
    gpu.state.blend_set('ALPHA')
    batch_for_shader(sh, 'LINES', {'pos': lines}).draw(sh)
    gpu.state.blend_set('NONE')


CLASSES = (HAIRMESH_OT_profile_editor,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    op = STATE.get('op')
    if op is not None:
        STATE['op'] = None
    for k in ('handle', 'handle3d'):
        h = STATE.pop(k, None)
        if h is not None:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
