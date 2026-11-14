# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Pick of Merge Distance (lot 1.1b; the original's ◎ Pick, hair-modeler.clean.html:11580-11593): a circle at the cursor
in the 3D View; a click sets its diameter, measured in the model's units at the place under the cursor, as the merge
distance of the Conversion tab (the ticked groups, else the project defaults). Ctrl + wheel and Ctrl + RMB drag size
the circle, Esc or RMB cancel; every other event goes on (navigation, the numpad), and so does every event over a
panel, a header, another area or the D that started it. It measures at the cursor as it starts (mergePickOn, 13167),
so Ctrl + wheel before any move already shows the circle. One Pick at a time. The keys are core/mergepick.py; called with a
`distance` the operator sets it without the modal (the tests, the stress)."""
import bpy
from bpy.props import FloatProperty

from ..core import groups as G
from ..core import mergepick as MP
from . import errorlog, live, owner, pick, pointer, select, session, state
from .coords import f32

DRAW = 'merge_pick'


def _region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None) if area is not None else None


def _view3d(context):
    """the 3D View to pick in: the one of the context (the D pop-up opens over it), else the window's first"""
    if context.area is not None and context.area.type == 'VIEW_3D':
        return context.area
    scr = context.window.screen if context.window is not None else None
    return next((a for a in scr.areas if a.type == 'VIEW_3D'), None) if scr is not None else None


class HAIRMESH_OT_merge_dist_pick(bpy.types.Operator):
    """Measure the merge distance with a circle in the viewport: click to set its diameter at the place under the cursor"""
    bl_idname = 'hairmesh.merge_dist_pick'
    bl_label = 'Merge Distance Pick'
    bl_options = {'REGISTER', 'UNDO'}

    distance: FloatProperty(name='Distance', description='Set this merge distance without the circle (0: the circle in the viewport)', default=0.0, min=0.0, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if state.has(DRAW):
            cls.poll_message_set('Pick is running: click in the viewport, or press Esc')
            return False
        return owner.visible(context) and session.find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        try:
            from . import tool
            P = session.get_project(session.find_root(context))
            why = tool.merge_dist_why(P) if P is not None else None
            if why:
                return why                                          # (dimmed: its click says this, tool.merge_dist_why)
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    # ---------------- setting it ----------------

    def _set(self, context, d):
        from . import tool
        from .ops_groups import _finish
        root = session.find_root(context); P = session.get_project(root)
        val = f32(d)
        if not val > 0:
            self.report({'INFO'}, 'Merge distance unchanged: nothing measured')
            return {'CANCELLED'}
        if G.convUnchanged(P, 'mergeDist', val):
            self.report({'INFO'}, f'Merge distance unchanged ({val:.4g})')
            return {'CANCELLED'}
        G.setConv(P, 'mergeDist', val)                              # port of the click (7537): setConv('mergeDist', d); commit()
        self.distance = val                                         # (Adjust Last Operation shows what was set, and redoes it)
        why = tool.merge_dist_why(P)
        return _finish(context, root, P, None, self.report, f'Merge distance = {val:.4g}' + (f'. {why}' if why else ''))

    def execute(self, context):
        return self._set(context, self.distance)

    # ---------------- the modal ----------------

    def invoke(self, context, event):
        from . import tool
        P = session.get_project(session.find_root(context))
        why = tool.merge_dist_why(P) if P is not None else None
        if why:                                                     # (dimmed: the click only says why, lot 1.2c, item 6)
            self.report({'INFO'}, why)
            return {'CANCELLED'}
        if self.distance > 0:
            return self.execute(context)
        area = _view3d(context)
        region = _region(area)
        if area is None or region is None:
            self.report({'WARNING'}, 'Open a 3D View to pick the merge distance in')
            return {'CANCELLED'}
        self.area_ptr = area.as_pointer()                           # (never the area itself: it may be freed while the modal runs)
        self.pick = MP.Pick()
        self.ui = pick.ui_scale(context)
        self.mouse = None
        self.value = None
        self.popup = getattr(context, 'region_popup', None)         # (the D that started it: this modal sits above it, and what is over it is the D's, as the brush's over_popup)
        self.inside = False                                         # the last event over the viewport itself: pointer.follow
        if event is not None and self._inside(context, area, region, event.mouse_x, event.mouse_y):   # port of mergePickOn (13167): the circle and its value at the cursor as it is
            self.mouse = (event.mouse_x - region.x, event.mouse_y - region.y)
            self.value = self._measure(context, area, region)
        state.add(DRAW, bpy.types.SpaceView3D, _draw, (self,))
        context.window_manager.modal_handler_add(self)
        pointer.claim('merge')
        if self.mouse is not None:
            pointer.ask('merge', context, pointer.cursor_for(2 * self.pick.r))
        self._say(area)
        return {'RUNNING_MODAL'}

    def _inside(self, context, area, region, mx, my):
        """the cursor over the viewport itself: not over a panel, a header or another area, nor over the D that started
        the Pick (a click there is the D's: a button, a tab)"""
        from . import brush
        if brush.over_ui_at(area, region, mx, my):
            return False
        if self.popup is not None:
            r = brush.popup_rect(context, self.popup)
            if r is None:                                           # (the D closed)
                self.popup = None
            elif r[0] <= mx < r[0] + r[2] and r[1] <= my < r[1] + r[3]:
                return False
        return True

    def _where(self, context):
        """(area, its WINDOW region), looked up again at every event among the window's areas; None once the area is
        gone (joined, closed, another workspace)"""
        scr = context.window.screen if context.window is not None else None
        a = next((a for a in scr.areas if a.as_pointer() == self.area_ptr), None) if scr is not None else None
        r = _region(a)
        return (a, r) if r is not None else None

    def _measure(self, context, area, region):
        """the distance the circle measures now (mergePickWorld), or None with the cursor off the viewport"""
        if self.mouse is None:
            return None
        from . import brush
        root, P, sel = select.current(context)
        project = pick.region_projector(region, area.spaces.active.region_3d, root.matrix_world)
        with context.temp_override(area=area, region=region):
            view = brush._brush_view(bpy.context, root, P, project, sel)
            _cam, pivot = pick.model_cam(root, area.spaces.active.region_3d)
        h = view.hit(self.mouse)
        return MP.mergePickWorld(view, h[0] if h is not None else None, pivot, self.pick.r * self.ui, P.S.scale)

    def _say(self, area):
        v = self.value
        text = f'Merge Distance: {v:.4g} (circle diameter)' if v is not None else 'Merge Distance: move over the viewport'
        live.header(area, text, MP.KEYS)
        area.tag_redraw()

    def _end(self, context):
        pointer.release('merge')
        state.remove(DRAW)
        w = self._where(context) if getattr(self, 'area_ptr', None) is not None else None
        if w is not None:
            live.header(w[0], None)
            w[0].tag_redraw()
        else:
            live.modal_keys.clear(context)                          # (the area is gone: the status bar alone)

    def cancel(self, context):                                      # (Blender ends the modal: File > New / Open, the window closed)
        self._end(context)

    def modal(self, context, event):
        try:
            ret = _step(self, context, event)
            if state.has(DRAW):                                     # (still running: the pointer the event asks for)
                pointer.follow('merge', context, event, self.inside, 2 * self.pick.r, self.pick.drag is not None, 'PASS_THROUGH' in ret)
            return ret
        except Exception:                                           # (the rule of 06/11/2026: everything cleaned, logged, cancelled)
            self._end(context)
            errorlog.record('modal: merge distance pick')
            return {'CANCELLED'}


def _step(op, context, event):
    """one event of the Pick (its modal: the pointer after it); a function of the module, the operator its first argument (tests build it on a SimpleNamespace)"""
    w = op._where(context)
    if w is None:
        op._end(context)
        return {'CANCELLED'}
    area, region = w
    x, y = event.mouse_x - region.x, event.mouse_y - region.y
    inside = op.inside = op._inside(context, area, region, event.mouse_x, event.mouse_y)
    what = op.pick.key(event.type, event.value, x=x, ctrl=event.ctrl, alt=event.alt, inside=inside)
    if what == 'pass':
        return {'PASS_THROUGH'}
    if what == 'cancel':
        op._end(context)
        op.report({'INFO'}, 'Merge distance: Pick cancelled')
        return {'CANCELLED'}
    if what == 'away':                                              # (over a panel, a header or another area: no circle)
        if op.mouse is not None:
            op.mouse = None; op.value = None
            op._say(area)
        return {'PASS_THROUGH'}
    if what == 'move' or (what == 'size' and op.mouse is None and inside):
        op.mouse = (x, y)                                           # (Ctrl + wheel before any move: the circle where the cursor is, 11588)
    if what in ('move', 'size'):
        op.value = op._measure(context, area, region)
        op._say(area)
        return {'RUNNING_MODAL'}
    if what == 'set':
        op.mouse = (x, y)
        d = op._measure(context, area, region)
        op._end(context)
        return op._set(context, d or 0.0)
    return {'RUNNING_MODAL'}


def _draw(op):
    try:
        ctx = bpy.context
        if ctx.area is None or ctx.area.as_pointer() != op.area_ptr or op.mouse is None or not pointer.shows('merge'):
            return
        import gpu
        from .brush import circle_points
        from .draw import polyline2d
        x, y = op.mouse                                             # (a Ctrl + RMB drag keeps the circle where it began: 7730)
        w = 1.5 * pick.line_scale(ctx)
        gpu.state.blend_set('ALPHA')
        polyline2d(ctx.region, circle_points(x, y, op.pick.r * op.ui), (1.0, 1.0, 1.0, 0.85), w)
        gpu.state.blend_set('NONE')
    except ReferenceError:                                          # (the operator is gone: a reload)
        state.remove(DRAW)


CLASSES = (HAIRMESH_OT_merge_dist_pick,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    state.remove(DRAW)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
