# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Scissors > Split Ends inside Blender (1.0.2: splitEndsOn 7554, splitEndsHover 7614, splitEnds 7629, the click
7593-7596, the spread's Ctrl + RMB drag 7562-7565 / 7729-7737). The data part is core/scissors.py (splitPlan,
splitEndsHover, splitEnds: tests/test_splitends_parity.py); the keys are core/gestures.SplitSession.

  hairmesh.split_ends          the click (LMB on a curve, the tool keymap): the curve splits from there to its tip —
                               one undo step; a click on nothing says so. Its non-modal form takes the point, the view's
                               perspective matrix and the region's size (as hairmesh.cut_curves), for the tests and the
                               stress test.
  hairmesh.split_ends_session  while the mode is on, a session over the viewport (as the anchor brush's): the preview
                               of both branches under the cursor, the Spread's circle, [ ] / F / Ctrl + RMB drag for the
                               Spread. It starts with the mode (set_cut_mode, the sub-tool keys) or, when something else
                               brought the mode back (an undo, a file), from the viewport's next draw; it ends by itself
                               when the mode, the sub-tool, the tool or Object Mode is left. The clicks pass through it
                               to the keymap, so the split is hairmesh.split_ends either way.

The Spread is session state, as the original's S.splitR (never in the file): WindowManager.hairmesh_brush.split_spread."""
import time
import traceback

import bpy
from bpy.props import FloatVectorProperty, IntProperty, IntVectorProperty
from mathutils import Matrix, Vector

from ..core import anchorbrush as AB, curves as CM, gestures as GS, groups as G, scissors as SC
from ..core.raycast import Ray
from ..core.selection import Selection
from ..core.vec3 import V3
from . import draw as drawmod, errorlog, live, owner, pick, pointer, select, session, state
from .coords import from_blender
from .ops_scissors import matrix_projector
from .ops_select import PICK_CURVE_PX

STATE = {'op': None, 'armed': None, 'off': False}                   # the running session (the draw handler reads it); off: an
                                                                    # exception ended it (or its start), no new one until the mode is chosen again
OFF_HINT = 'the Split Ends preview is off until the mode is chosen again (Curve or Wall, then Split Ends)'
TIMES = {'hover': []}                                               # the last hovers' cost, seconds (tools/sim_u2.py reads it)
DRAW_HANDLER = 'split_ends'                                         # (blender/state.py)
PREVIEW = (1.0, 0x8a / 255, 0x3d / 255, 0.9)                        # splitViz: 0xff8a3d, opacity 0.9, over everything (depthTest false)
PREVIEW_PX = 2.0                                                    # (at 100 %: pick.line_scale)
DASH = (6.0, 4.0)                                                   # the new branch dashed: the two branches apart without their colour (G11)
CIRCLE = (1.0, 1.0, 1.0, 0.75)
CIRCLE_PX = 1.5
SHADOW = (0.0, 0.0, 0.0, 0.55)
SPLIT_KEYS = ((('LMB',), 'Split the End Here'), (('RMB',), 'Pick a Curve'), (('[', ']'), 'Spread'), (('F',), 'Spread'), (('Ctrl', 'RMB Drag'), 'Spread'),
              (('Esc',), 'Exit'))
RADIAL_KEYS = ((('Move',), 'Set the Spread'), (('Ctrl',), 'Snap'), (('Shift',), 'Precision'), (('LMB', 'Enter'), 'Keep It'), (('RMB', 'Esc'), 'Put It Back'))
DRAG_KEYS = ((('Move',), 'Set the Spread'), (('RMB',), 'Release: Keep It'), (('Esc',), 'Put It Back'))
MODIFIER_KEYS = {'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY'}


def spread(context=None):
    return (context or bpy.context).window_manager.hairmesh_brush.split_spread


def mode_on(context):
    hm = context.scene.hairmesh
    return hm.subtool == 'SCISSORS' and hm.cut_mode == 'SPLIT'


# ---------------- the views ----------------

def region_view(context, root):
    """(the BrushView splitPlan reads, the projector) of a 3D View region: the model's space, the region's pixels"""
    region, rv3d = context.region, context.region_data
    project = pick.region_projector(region, rv3d, root.matrix_world)
    cam_dir, _piv = pick.model_cam(root, rv3d)
    c = root.matrix_world.inverted() @ rv3d.view_matrix.inverted().translation
    cam_pos = V3(*from_blender(c.x, c.y, c.z))                      # (as the brush's view; splitPlan reads only the direction)
    return AB.BrushView(project, lambda m: pick.model_ray(root, region, rv3d, m), cam_dir, cam_pos, region.height), project


def matrix_view(root, persp, size):
    """region_view for a given view: `persp` the 4x4 perspective matrix (world -> clip, 16 floats row by row), `size`
    the region's (width, height) — the non-modal form's (the rays through the clip space's near and far planes)"""
    project = matrix_projector(persp, size, root.matrix_world)
    inv = (Matrix([persp[0:4], persp[4:8], persp[8:12], persp[12:16]]) @ root.matrix_world).inverted()
    w, h = size

    def at(x, y, z):
        v = inv @ Vector((x / w * 2.0 - 1.0, y / h * 2.0 - 1.0, z, 1.0))
        return v.xyz / v.w

    def ray(m):
        a = at(m[0], m[1], -1.0); d = (at(m[0], m[1], 1.0) - a).normalized()
        return Ray(V3(*from_blender(a.x, a.y, a.z)), V3(*from_blender(d.x, d.y, d.z)))
    centre = ray((w / 2, h / 2))
    return AB.BrushView(project, ray, centre.direction, centre.origin, h), project


def shown_curves(P, hm):
    """the curves the click picks among (hairCurves() of 7594, as View.curves: the shown ones)"""
    sel = select.load(hm)
    return G.visibleCurves(G.shownCurves(P, sel.items, sel.anchors, None, None, drawmod.snap_curve)[0], sel.items)   # (a service curve only while selected, 16484)


# ---------------- the split ----------------

def apply_split(context, root, P, m, view, project, curves, r, ui, report):
    """the click at region pixel m (7593-7596): pickCurve (7 px) and splitEnds with the Spread `r` (px at 100 %);
    saved, the locks it changed rebuilt (S.surfDirty: the curves and the walls it names), the two branches selected —
    {'FINISHED'}; a click on nothing, or a refusal of the core, says so — {'CANCELLED'}"""
    CM.rebuild_dirty(P)                                             # (the samples a frame would have made: pickCurve and splitPlan read them)
    ch = pick.pick_curve(curves, project, m[0], m[1], PICK_CURVE_PX * ui)
    if ch is None:
        report({'INFO'}, SC.SPLIT_MISS)
        return {'CANCELLED'}
    res = SC.splitEnds(P, ch, (m[0], m[1]), view, r * ui, P.S.scale, ui)
    if 'curve' not in res:
        report({'INFO'}, res['toast'])
        return {'CANCELLED'}
    sel = Selection(list(res['sel']), [])                           # S.asel = [], S.sel = {c, nb} (6695)
    select.store(context.scene.hairmesh, sel)
    from . import tool
    tool.show_selection(context)                                    # (the branches' sheet in sight under the Scissors page)
    G.panelsRendered(P, sel.items[-1])                              # onSelChange (7672)
    session.save_project(root, P)
    live.touch(root, [h.id for h in res['curves']] + [i for s in res['walls'] for i in (s.a, s.b)])
    live.update(context, root)                                      # (a pending conversion follows: regenConversion, 7671)
    select.redraw(context)
    report({'INFO'}, res['toast'])
    refresh(context)                                                # (the session's preview: the curve under the cursor changed)
    return {'FINISHED'}


class HAIRMESH_OT_split_ends(bpy.types.Operator):
    """Split the end of the curve under the cursor: from there to its tip it becomes two branches, a new curve sharing its trunk; the ends move apart by the Spread. A wall on the other side goes over to the new branch"""
    bl_idname = 'hairmesh.split_ends'
    bl_label = 'Split Ends'
    bl_options = {'REGISTER', 'UNDO'}

    point: FloatVectorProperty(name='Point', description='Non-modal form: the click in region pixels (x, y)', size=2, options={'HIDDEN', 'SKIP_SAVE'})
    view_matrix: FloatVectorProperty(name='View', description="Non-modal form: the view's perspective matrix (world to clip, row by row) the click was made in", size=16, options={'HIDDEN', 'SKIP_SAVE'})
    region_size: IntVectorProperty(name='Region', description='Non-modal form: the width and height of the region the click was made in', size=2, default=(1, 1), options={'HIDDEN', 'SKIP_SAVE'})
    spread: IntProperty(name='Spread', description='Non-modal form: the Spread in pixels (0: the one of the Scissors page)', min=0, max=GS.SPREAD_MAX, default=0, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        if not mode_on(context) or event.alt or event.oskey:
            return {'PASS_THROUGH'}                                 # (another sub-tool or mode; Alt: Blender's, the original's navigation)
        if context.region is None or context.region_data is None:
            return {'PASS_THROUGH'}
        root = session.find_root(context)
        P = session.get_project(root)
        view, project = region_view(context, root)
        return apply_split(context, root, P, (event.mouse_region_x, event.mouse_region_y), view, project, shown_curves(P, context.scene.hairmesh),
                           spread(context), pick.ui_scale(context), self.report)

    def execute(self, context):
        root = session.find_root(context)
        P = session.get_project(root)
        if any(self.view_matrix):
            view, project = matrix_view(root, list(self.view_matrix), tuple(self.region_size))
            ui = 1.0
        elif context.region is not None and context.region_data is not None:
            view, project = region_view(context, root)
            ui = pick.ui_scale(context)
        else:
            self.report({'INFO'}, 'Click a curve in the 3D View')
            return {'CANCELLED'}
        return apply_split(context, root, P, tuple(self.point), view, project, shown_curves(P, context.scene.hairmesh),
                           self.spread or spread(context), ui, self.report)


# ---------------- the session ----------------

def _still_on(op, context):
    from . import brush, tool
    if STATE.get('op') is not op or not mode_on(context):
        return False
    if context.mode != 'OBJECT' or not tool.is_active(context) or not owner.visible(context):
        return False                                                # (Tab, another tool, the add-on filtered out)
    if context.area is None or context.area.type != 'VIEW_3D':
        return False
    if brush.STATE.get('op') is not None:
        return False                                                # (a brush of the Modifiers tab took the viewport: its own clicks)
    root = session.find_root(context)
    return root is not None and root.name == op.root_name           # (another project: _arm starts one on it)


def _release_area(area):
    if any(a == area for s in bpy.data.screens for a in s.areas):
        live.header(area, None); area.tag_redraw()
    else:
        from . import modal_keys
        modal_keys.clear(bpy.context)


class HAIRMESH_OT_split_ends_session(bpy.types.Operator):
    """Show the preview of both branches under the cursor and the Spread's circle while Scissors > Split Ends is on; [ ] or F change the Spread, Ctrl + right drag too"""
    bl_idname = 'hairmesh.split_ends_session'
    bl_label = 'Split Ends Session'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None and context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        if bpy.app.background or not mode_on(context):
            return {'CANCELLED'}
        cur = STATE.get('op')
        root = session.find_root(context)
        if cur is not None or root is None:
            return {'CANCELLED'}
        self.root_name = root.name
        self.ui = pick.ui_scale(context)
        self.g = GS.SplitSession(spread(context))
        self.mouse = None                                           # the cursor (region pixels), None off the viewport
        self.centre = None                                          # where F / Ctrl + RMB began (the circle stays there)
        self.lines = None                                           # the preview: two lists of region points (the curve's own, the new branch)
        self.what = None                                            # the hover's text: the curve's name, a refusal, or None
        self.area = context.area
        self.popup = context.region_popup
        self.titled = False                                         # the header shows the session's text (only while the pointer is over the viewport)
        self.inside = False                                         # the last event over the viewport itself: pointer.follow
        STATE['op'] = self
        pointer.claim('split')
        view = context.region if context.region is not None and context.region.type == 'WINDOW' else next(r for r in context.area.regions if r.type == 'WINDOW')
        with context.temp_override(region=view):
            context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}                                    # (the text and the keys come with the first move over the viewport)

    def untitle(self, context):
        """Blender's header and status bar back (the pointer left the viewport: the header's mode buttons in reach)"""
        live.header(context.area or self.area, None)
        self.titled = False

    def header(self, context):
        self.titled = True
        a = self.g.adjust
        if a is not None:
            live.header(context.area or self.area, f'HairMesh Split Ends (Spread: {self.g.r} px)', RADIAL_KEYS if a['what'] == 'radial' else DRAG_KEYS)
            return
        what = f': {self.what}' if self.what else ''
        live.header(context.area or self.area, f'HairMesh Split Ends{what} (Spread: {self.g.r} px)', SPLIT_KEYS)

    def over_popup(self, context, event):
        from .brush import popup_rect
        if self.popup is None:
            return False
        r = popup_rect(context, self.popup)
        if r is None:
            self.popup = None
            return False
        x, y, w, h = r
        return x <= event.mouse_x < x + w and y <= event.mouse_y < y + h

    def modal(self, context, event):
        try:
            ret = self._modal(context, event)
            if STATE.get('op') is self:
                pointer.follow('split', context, event, self.inside, 2 * self.g.r, self.g.adjust is not None, 'PASS_THROUGH' in ret)
            return ret
        except Exception:
            STATE['off'] = True                                     # (one entry, not a new session and a new entry at every move: _arm waits for the mode)
            errorlog.record(f'split ends: the session; {OFF_HINT}')
            traceback.print_exc()
            self._end(context)
            return {'CANCELLED'}

    def _modal(self, context, event):
        from .brush import over_ui
        if not _still_on(self, context):
            return self._end(context) | {'PASS_THROUGH'}            # (the release of the click that ended it is the pop-up's: brush._modal, lot 1.2c)
        if not pointer.shows('split'):
            return {'PASS_THROUGH'}                                 # (a modal with a circle of its own runs above: this one waits)
        self.area = context.area
        t, v = event.type, event.value
        inside = self.inside = not (over_ui(context, event) or self.over_popup(context, event))
        r0 = self.g.r
        act = self.g.key(t, v, event.mouse_region_x / self.ui, event.ctrl, event.alt, event.shift, event.oskey, inside)
        if act == 'hover':
            self.mouse = (event.mouse_region_x, event.mouse_region_y)
            self.hover(context)
            return {'PASS_THROUGH'}                                 # (the edges' hover, Blender's own moves)
        if act == 'away':
            if self.mouse is not None or self.lines is not None or self.titled:
                self.mouse = None; self.lines = None; self.what = None
                self.untitle(context)                               # (the header's own buttons back under the pointer: the Scissors modes)
                context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if act in ('radial', 'drag'):
            self.centre = (event.mouse_region_x, event.mouse_region_y)
            self.header(context); context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if act in ('size', 'keep', 'restore'):
            if self.g.adjust is None:
                self.centre = None
            if self.g.r != r0 or act != 'size':
                self._set(context)
            return {'RUNNING_MODAL'}
        if act == 'eat':
            return {'RUNNING_MODAL'}
        if act == 'split':
            self.lines = None                                       # (the click goes on to hairmesh.split_ends; its end calls refresh)
        elif v == 'PRESS' and t not in GS.NAV and t not in MODIFIER_KEYS and (self.mouse is not None or self.lines is not None):
            self.mouse = None; self.lines = None                    # (a pie or a menu may open, and Blender gives the modal no event until it closes: no circle left under it)
            context.area.tag_redraw()
        return {'PASS_THROUGH'}                                     # (the click, RMB, Shift + RMB's pie, Esc back to Select, navigation, every other key)

    def _set(self, context):
        """the Spread of the keys into the field (its update calls spread_changed: the header, the preview)"""
        bp = context.window_manager.hairmesh_brush
        if bp.split_spread != self.g.r:
            bp.split_spread = self.g.r
        else:
            spread_changed(context)

    def hover(self, context):
        """splitEndsHover (7614): the preview and the text for the cursor where it is now"""
        t0 = time.perf_counter()
        root = bpy.data.objects.get(self.root_name)
        if root is None or self.mouse is None or context.region is None or context.region_data is None:
            return
        P = session.get_project(root)
        CM.rebuild_dirty(P)
        view, project = region_view(context, root)
        x, y = self.mouse
        ch = pick.pick_curve(shown_curves(P, context.scene.hairmesh), project, x, y, PICK_CURVE_PX * self.ui)
        lines, hud = SC.splitEndsHover(P, ch, (x, y), view, self.g.r * self.ui, P.S.scale, self.ui)
        if lines is None:
            self.lines = None; self.what = hud or None
        else:
            self.lines = [[p for p in (project(q) for q in line) if p is not None] for line in lines]
            self.what = ch['curve'].name if ch is not None else None
        self.header(context)
        context.area.tag_redraw()
        TIMES['hover'].append(time.perf_counter() - t0)
        del TIMES['hover'][:-400]

    def _end(self, context):
        if STATE.get('op') is self:
            STATE['op'] = None
        if STATE.get('op') is None:
            pointer.release('split')                                # (a session started meanwhile owns the pointer)
        try:
            _release_area(self.area)
        except Exception:
            pass
        try:
            select.redraw(context)
        except Exception:
            pass
        return {'FINISHED'}

    def cancel(self, context):
        self._end(context)                                          # (a file loaded, Blender quitting)


def spread_changed(context):
    """the Spread field changed (the page's field, the session's keys): the session's numbers, header and preview follow"""
    op = STATE.get('op')
    if op is None:
        return
    if op.g.adjust is None:
        op.g.r = spread(context)
    area = op.area
    try:
        with context.temp_override(area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
            if op.mouse is not None:
                op.hover(bpy.context)                               # (off the viewport: no text in the header, nothing to redo)
    except Exception:                                               # (the area closed: the session ends at its next event)
        pass
    if area is not None:
        try:
            area.tag_redraw()
        except Exception:
            pass


def refresh(context):
    """after a split: the preview under the cursor again (the curve changed under it)"""
    op = STATE.get('op')
    if op is not None and op.mouse is not None:
        spread_changed(context)


def stop(context=None):
    op = STATE.get('op')
    STATE['op'] = None
    pointer.release('split')
    if op is not None:
        try:
            _release_area(op.area)
        except Exception:
            pass
    if context is not None:
        select.redraw(context)


def follow(context):
    """the mode or the sub-tool was just chosen: the session on with Split Ends, here and not at the next draw (a button
    of the D: the session has to know that pop-up), off with anything else"""
    op = STATE.get('op')
    STATE['off'] = False                                            # (the mode chosen again: a session that failed may start again)
    if not mode_on(context):
        if op is not None:
            stop(context)
        return
    from . import brush, tool
    if op is None and not bpy.app.background and brush.STATE.get('op') is None and tool.is_active(context) and context.area is not None \
            and context.area.type == 'VIEW_3D' and session.find_root(context) is not None:
        bpy.ops.hairmesh.split_ends_session('INVOKE_DEFAULT')


def _arm(ctx):
    """the mode is Split Ends with no session on this view (an undo brought it back, a file opened with it, the tool or
    Object Mode came back, the header's own buttons): one starts after this draw (a draw cannot start a modal, a timer can)"""
    from . import brush, tool
    if not mode_on(ctx) or STATE.get('armed') is not None or STATE.get('off') or ctx.window is None or ctx.mode != 'OBJECT':
        return
    if brush.STATE.get('op') is not None or not tool.is_active(ctx) or not owner.visible(ctx) or session.find_root(ctx) is None:
        return
    STATE['armed'] = (ctx.window.as_pointer(), ctx.area.as_pointer())
    bpy.app.timers.register(_start_armed, first_interval=0.0)


def _start_armed():
    armed = STATE.pop('armed', None)
    STATE['armed'] = None
    if armed is None or STATE.get('op') is not None:
        return None
    try:
        for w in bpy.context.window_manager.windows:
            if w.as_pointer() != armed[0]:
                continue
            for a in w.screen.areas:
                if a.as_pointer() == armed[1] and a.type == 'VIEW_3D':
                    r = next(x for x in a.regions if x.type == 'WINDOW')
                    with bpy.context.temp_override(window=w, area=a, region=r):
                        bpy.ops.hairmesh.split_ends_session('INVOKE_DEFAULT')
    except Exception:                                               # (a timer must never raise, and no redraw tries again)
        STATE['off'] = True
        errorlog.record(f'timer: split ends armed; {OFF_HINT}')
        traceback.print_exc()
    return None


# ---------------- drawing ----------------

def _draw():
    ctx = bpy.context
    op = STATE.get('op')
    try:
        if op is None and ctx.area is not None:
            _arm(ctx)
        if op is None or ctx.area is None or ctx.area != op.area or not drawmod.overlays_on(ctx) or not pointer.shows('split'):
            return                                                  # (shows: one circle on the screen, the modal's on top)
        import gpu
        lw = pick.line_scale(ctx)
        gpu.state.blend_set('ALPHA')
        if op.lines and op.g.adjust is None:
            own, new = op.lines
            if len(own) > 1:
                drawmod.polyline2d(ctx.region, [(p[0], p[1]) for p in own], PREVIEW, PREVIEW_PX * lw)
            dashes = []
            for a, b in zip(new, new[1:]):
                dashes += drawmod.dash_segments(a[0], a[1], b[0], b[1], DASH[0] * op.ui, DASH[1] * op.ui)
            if dashes:
                drawmod.polyline2d(ctx.region, dashes, PREVIEW, PREVIEW_PX * lw, 'LINES')
        c = op.centre if op.g.adjust is not None else op.mouse
        if c is not None:
            from .brush import circle_points
            r = op.g.r * op.ui
            drawmod.polyline2d(ctx.region, [(x + lw, y - lw) for x, y in circle_points(c[0], c[1], r)], SHADOW, CIRCLE_PX * lw)
            drawmod.polyline2d(ctx.region, circle_points(c[0], c[1], r), CIRCLE, CIRCLE_PX * lw)
            a = op.g.adjust
            if a is not None and a['what'] == 'radial':
                drawmod.polyline2d(ctx.region, circle_points(c[0], c[1], a['r0'] * op.ui), (1.0, 1.0, 1.0, 0.5), max(1.0, CIRCLE_PX * lw / 2))   # (Blender's thin ring: where the value started)
        gpu.state.blend_set('NONE')
    except Exception:
        errorlog.record('split ends: drawing')


CLASSES = (HAIRMESH_OT_split_ends, HAIRMESH_OT_split_ends_session)


@bpy.app.handlers.persistent
def _on_load(_dummy):
    STATE['op'] = None; STATE['armed'] = None; STATE['off'] = False
    pointer.release('split')


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.load_post.append(_on_load)
    state.add(DRAW_HANDLER, bpy.types.SpaceView3D, _draw)


def drop_pending():
    """the start a draw left pending (_arm's timer), dropped: never into an unregistered class"""
    STATE['armed'] = None
    if bpy.app.timers.is_registered(_start_armed):
        bpy.app.timers.unregister(_start_armed)


def unregister():
    STATE['op'] = None
    pointer.release('split')
    drop_pending()
    state.remove(DRAW_HANDLER)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
