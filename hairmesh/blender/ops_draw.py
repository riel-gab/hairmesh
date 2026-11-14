# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The Draw sub-tool (phase 3.2): a port of the original's drawing flow (4645-4665, 5323, 5405, 7415) as a modal
operator. Each click places an anchor on the head under the cursor (lifted a little along the normal) or, off the
head, on a plane facing the view through the last anchor; a ghost line follows the cursor; Enter or a right click
finishes, Esc finishes and goes back to Select. The curve is born in the group of the selection (the
original's targetGroup: a selected group, else the group of the last selected item) and selected. A curve with fewer than 2 points is discarded.
The Draw page of the D is ours (the original's Draw has no panel, renderToolPanel 12918): where the next curve goes and
how its anchors are born, and its mode: Anchors, the flow above, or Freehand (ours, core/freehand.py): a modal that lives one
stroke, from the press to the release. It collects points and shows them; the fit, the curve and the walls' preview
come at the release, in the operator's own undo step, the way a drawing ends. Between strokes no modal runs, so
navigation, Ctrl+Z and the pies are Blender's and the tool's.

The geometry is the core's (edit.placePoint over the head kept in the project, with the three.js raycaster port),
so a click here and the same ray in the original land on the same point. The viewport only supplies the ray."""
import json
import time
from types import SimpleNamespace

import bpy
from bpy.props import IntProperty, StringProperty
from mathutils import Vector

from ..core import edit, freehand, lockdraw as LD, rib as RB
from ..core.vec3 import V3
from . import draw as drawmod, live, modal_keys, owner, pick, pointer, select, session, slow
from .coords import f32, to_blender

NAV_EVENTS = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM',
              'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4', 'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9', 'NUMPAD_PERIOD',
              'NUMPAD_MINUS', 'NUMPAD_PLUS', 'NUMPAD_SLASH', 'NUMPAD_ASTERIX', 'HOME', 'NDOF_MOTION'}


KEYS = ((('LMB',), 'Place Anchor'), (('Enter', 'RMB'), 'Finish Curve'), (('Esc',), 'Finish, Back to Select'))
LOCK_KEYS = ((('LMB',), 'Place Point'), (('Enter', 'RMB'), 'Finish Lock'), (('Esc',), 'Finish, Back to Select'), (('[', ']'), 'Width'), (('F',), 'Width'), (('Ctrl', 'RMB Drag'), 'Width'))
LOCK_HELP = '\n'.join(('Click: a point of the lock\'s middle line; each gets the width of the circle there.',
                       '[ ] or F: the width. Ctrl + right drag: the same.',
                       'Anchors of other curves selected before: two start a flat lock between them, as wide as they are apart; '
                       'three or more start a volume branch through them all. That first click only starts it.',
                       'Enter: finish the lock. Esc: finish, back to Select.',
                       'Select the curve: Profile Smoothing, Tip, the brushes, Convert. Select its anchors: the width and rotation there.'))
_running: list = [None]                                             # the drawing modal of Anchors / Lock while it runs (brush.py: its own header waits)


def draw_running():
    return _running[0] is not None
FREEHAND_HELP = '\n'.join(('LMB drag: draw a stroke; each stroke is a curve.',
                           'Project On: Surface lands on what is nearest, Head on the head alone.',
                           'Esc during a stroke: cancel it.',
                           'Select two strokes, then Walls in the pie: a wall between them.',
                           'Esc: back to Select.'))
STROKE_KEYS = ((('LMB',), 'Release: Finish Stroke'), (('Esc',), 'Cancel Stroke'), (('MMB',), 'Navigate'))
TIMES = {'move': [], 'release': []}                                  # seconds, the last ones (tools/sim_c49_freehand_perf.py)

# the cursor's ray, the camera direction and the pivot in the model's space, and the head's visibility, live in
# pick.py since 3.6 (the wall picking needs them too); the names stay importable from here
from .pick import head_visible, model_cam, model_ray  # noqa: E402,F401


def target_group(context, P):
    """targetGroup (6748): the group of the last selected item (a selected group itself), else the root level. Since
    15/10/2026 (U1) the selection holds groups too, chosen in the HairMesh Outliner; the active collection does not
    count any more (it stood for the selected group from 3.2 to 3.6)."""
    return select.target_group(P, select.load(context.scene.hairmesh).prune(P))


def born(context, a):
    """the anchor just placed, as New Anchors asks (ours): Smooth leaves it as the core makes it (newAnchor: smooth 1),
    bit by bit; Sharp writes 0 here at the border. No dirty flag to add: addDrawPointAt marked the curve and nothing
    samples it before this line (the samples' key reads the smoothness anyway: curves.sampleKey)"""
    if a is not None and context.scene.hairmesh.draw_anchors == 'SHARP':
        a.smooth = 0
    return a


class HAIRMESH_OT_draw_curve(bpy.types.Operator):
    """Draw a hair curve: in Anchors each click places an anchor on the head (or on a plane facing the view), Enter or right click finishes, Esc finishes and goes back to Select; in Freehand drag a stroke, the release makes the curve"""
    bl_idname = 'hairmesh.draw_curve'
    bl_label = 'Draw HairMesh Curve'
    bl_options = {'REGISTER', 'UNDO'}

    points_json: StringProperty(name='Points', description='Non-modal form: a JSON list of [x, y, z] points in the model space (tests, scripts)', default='', options={'HIDDEN', 'SKIP_SAVE'})
    stroke_json: StringProperty(name='Stroke', description='Non-modal form of a Freehand stroke: a JSON {"pts": [[x, y, z]...], "normals": [[x, y, z] or null...]} on the surface, in the model space (tests, scripts)', default='', options={'HIDDEN', 'SKIP_SAVE'})
    group: IntProperty(name='Group', description='Non-modal form: the group id the curve goes into (-1: the active collection)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    lock_json: StringProperty(name='Lock', description='Non-modal form of the Lock mode: a JSON {"anchors": [ids selected before], "width": px, "pixel": model units per pixel, "view": [x, y, z] the view direction} (tests, scripts)', default='', options={'HIDDEN', 'SKIP_SAVE'})
    stroke = None                                                   # the Freehand's stroke in course (core/freehand.py); None: Anchors, or no stroke

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    # ---- modal (the viewport) ----
    def invoke(self, context, event):
        hm = context.scene.hairmesh
        if hm.subtool != 'DRAW':
            return {'PASS_THROUGH'}                                 # LMB means select in the other sub-tools
        if context.area is None or context.area.type != 'VIEW_3D' or context.region is None or context.region_data is None:
            return {'PASS_THROUGH'}
        self.root = session.find_root(context)
        self.P = session.get_project(self.root)
        if hm.draw_mode == 'FREEHAND':
            return self._stroke_start(context, event)
        from . import brush
        if brush.over_ui(context, event):
            return {'PASS_THROUGH'}                                 # (the N panel, the toolbar over the view: their own click)
        self.mode = hm.draw_mode
        self.lock_anchors = None
        if self.mode == 'LOCK':                                     # (ribDrawOn: the anchors of other curves selected before, in pick order, 6209)
            self.lock_anchors = select.anchors_of(self.P, select.load(hm).prune(self.P))
            if brush.running('LOCK') is None:
                bpy.ops.hairmesh.brush('INVOKE_DEFAULT', kind='LOCK')   # (the width circle, under this modal: it reads the width keys this one leaves)
        self.ui = pick.ui_scale(context)
        self.state = edit.DrawState()
        self.parent = target_group(context, self.P)
        edit.touchGroup(self.P, self.parent)                        # the target group gets its conv defaults, as its panel would in the app
        context.window_manager.modal_handler_add(self)
        _running[0] = self
        pointer.claim('draw')                                       # (over the width session: the pointer is this modal's while it runs)
        pointer.ask('draw', context, pointer.cursor_for(self._diameter(context)))
        self._header(context)
        self._click(context, event)
        self._ghost(context, event)
        return {'RUNNING_MODAL'}

    def _diameter(self, context):
        """the Lock's circle (its width, px at 100 %); None in Anchors: no circle, Blender's own pointer"""
        if getattr(self, 'mode', None) != 'LOCK':
            return None
        from . import brush
        return brush.props(context).lock_width

    def _pointer(self, context, event, ret):
        """the system pointer after an event (pointer.wanted); a width key goes on to the session below, not to Blender"""
        from . import brush
        op = brush.running('LOCK')
        passed = 'PASS_THROUGH' in ret and LD.lock_key(event.type, event.value, event.shift, event.ctrl, event.alt, event.oskey) is None
        inside = not brush.over_ui(context, event) and not (op is not None and op.over_popup(context, event))   # (the D that armed the width session)
        pointer.follow('draw', context, event, inside, self._diameter(context), op is not None and op.adjust is not None, passed)

    def _header(self, context, area=None):
        """`area`: the width session's own when it asks (a field's update may come with another area or none)"""
        area = area or context.area
        if self.mode == 'LOCK':
            from . import brush
            live.header(area, f'HairMesh Draw Lock ({LD.header(brush.props(context).lock_width)})', LOCK_KEYS)
        else:
            live.header(area, 'HairMesh Draw', KEYS)

    def _ray(self, context, event):
        coord = (event.mouse_region_x, event.mouse_region_y)
        ray = model_ray(self.root, context.region, context.region_data, coord)
        cam, piv = model_cam(self.root, context.region_data)
        return ray, cam, piv

    def _lock(self, context):
        """rib.LockDraw of this click: the anchors selected when the drawing began, the circle's radius (S.ribR in the
        region's pixels) and worldPerPixelAt of this view"""
        from ..core.anchorbrush import worldPerPixelAt
        from . import brush
        cam_dir, cam_pos, tan_half, ortho = brush.view_frame(self.root, context.region_data)
        view = SimpleNamespace(ortho=ortho, H=context.region.height, cam_pos=cam_pos, cam_dir=cam_dir, tan_half=tan_half)
        scale = self.P.S.scale
        return RB.LockDraw(self.lock_anchors or (), LD.half(brush.props(context).lock_width) * self.ui, lambda p: worldPerPixelAt(view, p, scale))

    def _click(self, context, event):
        ray, cam, piv = self._ray(context, event)
        c = self.state.curve
        lock = self._lock(context) if getattr(self, 'mode', None) == 'LOCK' or (c is not None and c.rib) else None
        more = {'lock': lock} if lock is not None else {}           # (a plain curve's click: the call of before)
        a = born(context, edit.addDrawPoint(self.P, self.state, ray, cam, piv, parent=self.parent, head_visible=head_visible(self.root), **more))
        if self.state.msg:
            live.notify(context, self.state.msg, header=True)       # (6223: the toast of a lock started from anchors)
            self.state.msg = None
        c = self.state.curve
        if c is not None and c.rib:
            shape_now(self.P, c, cam)                               # (the ribbon of the lock being drawn, at every click: the original's frame, 14823)
        context.area.tag_redraw()
        return a

    def _ghost(self, context, event):
        c = self.state.curve
        drawmod.ghost = None
        drawmod.drawing = c.id if c is not None else None          # S.drawing: its group's curves show while it is drawn (activeGroups, 11911)
        if c is not None and c.anchors:
            ray, cam, piv = self._ray(context, event)
            p = edit.placePoint(self.P, ray, cam, piv, self.state, head_visible(self.root))
            if p is not None:
                M = self.root.matrix_world
                drawmod.ghost = (tuple(M @ Vector(to_blender(c.anchors[len(c.anchors) - 1].pos))), tuple(M @ Vector(to_blender(p))))
        context.area.tag_redraw()

    def cancel(self, context):
        modal_keys.clear(context)                                   # (Blender ends the modal itself: a window closed, a file loaded)
        pointer.release('draw')
        drawmod.ghost = None; drawmod.drawing = None
        if _running[0] is self:
            _running[0] = None
        if getattr(self, 'stroke', None) is not None:               # (called on the class too: nothing of an instance is sure here)
            drawmod.pencil = None; self.stroke = None

    def modal(self, context, event):
        if self.stroke is not None:
            return self._stroke_modal(context, event)
        try:
            ret = self._modal(context, event)
            if _running[0] is self:
                self._pointer(context, event, ret)
            return ret
        except Exception:                                           # (a modal that raises: everything it set cleared, the traceback logged, cancelled)
            from . import errorlog
            errorlog.record('draw: the drawing modal')
            pointer.release('draw')
            drawmod.ghost = None; drawmod.drawing = None
            if _running[0] is self:
                _running[0] = None
            try:
                self.state.curve = None                             # (the drawing ends here, its curve with it: nothing of it saved)
                session.forget_model(self.root.name)                # (the model as its text holds it: the next save keeps no partial curve nor a lock's root)
            except Exception:
                errorlog.record('draw: the drawing modal, its cleanup')
            live.header(context.area, None) if context.area is not None else modal_keys.clear(context)
            return {'CANCELLED'}

    def _modal(self, context, event):
        hm = context.scene.hairmesh
        if hm.draw_mode != self.mode or hm.subtool != 'DRAW':
            # (another mode: the drawing ends, 11366 — finishDrawing before S.drawMode changes; the event, the release of
            # the click on the Mode, is the pop-up's: brush._modal, lot 1.2c)
            return self._finish(context, back_to_select=False) | {'PASS_THROUGH'}
        if self.mode == 'LOCK':
            from . import brush
            op = brush.running('LOCK')
            if LD.leave_to_width(event.type, event.value, event.shift, event.ctrl, event.alt, op is not None and op.adjust is not None, event.oskey):
                if event.type in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE') and (op is None or op.adjust is None):
                    self._ghost(context, event)

                return {'PASS_THROUGH'}                             # (the width session below reads them: the circle, [ ], F, Ctrl+RMB)
        if event.type == 'MOUSEMOVE':
            self._ghost(context, event)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            from . import brush
            if brush.over_ui(context, event):
                return {'PASS_THROUGH'}                             # (a click on the N panel: its button, never a point under it)
            self._click(context, event)
            self._ghost(context, event)
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._finish(context, back_to_select=False)
        if event.type == 'ESC' and event.value == 'PRESS':
            return self._finish(context, back_to_select=True)     # the original: finishDrawing + the cursor tool (7415)
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}                                 # orbit, pan and zoom keep working while drawing
        return {'RUNNING_MODAL'}

    def _finish(self, context, back_to_select):
        pointer.release('draw')                                     # (the width session below asks again at its next event)
        drawmod.ghost = None; drawmod.drawing = None
        if _running[0] is self:
            _running[0] = None
        if context.area is not None:
            live.header(context.area, None)
        hm = context.scene.hairmesh
        c = edit.finishDrawing(self.P, self.state)
        if back_to_select:
            hm.subtool = 'SELECT'
        else:
            from . import brush
            op = brush.running('LOCK')
            if op is not None:
                op.header(context)                                  # (the width circle goes on: its own header back)
        if context.area is not None:
            context.area.tag_redraw()
        if c is None:
            self.report({'INFO'}, 'A curve needs at least 2 points')
            if getattr(self, 'mode', None) == 'LOCK':
                session.forget_model(self.root.name)                # (a lock's first click may have placed its root: the text holds the state before)
            return {'CANCELLED'}
        return self._done(context, c)

    def _done(self, context, c):
        hm = context.scene.hairmesh
        session.save_project(self.root, self.P)
        hm.sel_curves = json.dumps([c.id]); hm.sel_anchors = '[]'  # the new curve is the selection (4656)
        from . import tool
        tool.show_selection(context)                                # (its sheet in sight under the Draw page: item 14 of lot 1.2c)
        live.update(context, self.root)                              # the preview inside the operator's own undo step
        self.report({'INFO'}, f'HairMesh: {c.name}, {len(c.anchors)} anchors')
        return {'FINISHED'}

    # ---- Freehand: a modal of one stroke ----
    def _stroke_start(self, context, event):
        hm = context.scene.hairmesh
        self.root_name = self.root.name
        self.parent = target_group(context, self.P)
        self.stroke = freehand.Stroke()
        self.world = []                                             # what the overlay shows, in world space
        self.lift = self.P.S.scale * (f32(hm.draw_distance) / 100)
        self.min_px = freehand.MIN_PX * context.preferences.system.ui_scale
        self.hits = stroke_hits(hm, self.root, self.P)
        self._sample(context, event)
        context.window_manager.modal_handler_add(self)
        live.header(context.area, f'HairMesh Freehand (Project On: {"Head" if hm.draw_project == "HEAD" and head_shown(self.root, self.P) else "Surface"}, Distance from Head: {hm.draw_distance:.2f}%, Simplify: {hm.draw_simplify:.2f})', STROKE_KEYS)
        return {'RUNNING_MODAL'}

    def _sample(self, context, event):
        """a point of the stroke, when the filter takes this sample; nothing here touches the model"""
        x, y = event.mouse_region_x, event.mouse_region_y
        if not freehand.farEnough(self.stroke, x, y, self.min_px):
            return
        t0 = time.perf_counter()
        ray, cam, piv = self._ray(context, event)
        p = freehand.strokePoint(self.stroke, ray, cam, piv, self.hits)
        if p is not None:
            self.world.append(tuple(self.root.matrix_world @ Vector(to_blender(p))))
            if len(self.world) > 1:
                drawmod.pencil = self.world
                if context.area is not None:
                    context.area.tag_redraw()
        TIMES['move'].append(time.perf_counter() - t0)
        del TIMES['move'][:-2000]

    def _stroke_on(self, context):
        """the sub-tool, the mode's tool or the root gone (a script, another window): the stroke ends with them"""
        return context.scene.hairmesh.subtool == 'DRAW' and owner.visible(context) and bpy.data.objects.get(self.root_name) is not None

    def _stroke_modal(self, context, event):
        t, v = event.type, event.value
        if not self._stroke_on(context):
            return self._stroke_drop(context)
        if t in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._sample(context, event)
            return {'RUNNING_MODAL'}
        if t == 'LEFTMOUSE' and v in {'RELEASE', 'PRESS'}:          # (a press: the release went elsewhere, the window lost the mouse)
            self._sample(context, event)
            return self._stroke_finish(context)
        if t == 'ESC' and v == 'PRESS':
            return self._stroke_drop(context)
        if t == 'RIGHTMOUSE' and v == 'PRESS' and event.shift:
            self._stroke_drop(context)                              # (a pie takes the mouse: the release would never come here)
            return {'CANCELLED', 'PASS_THROUGH'}
        if t in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _stroke_clear(self, context):
        drawmod.pencil = None
        self.stroke = None
        if context.area is not None:
            live.header(context.area, None)
            context.area.tag_redraw()
        else:
            modal_keys.clear(context)

    def _stroke_drop(self, context):
        self._stroke_clear(context)
        return {'CANCELLED'}

    def _stroke_finish(self, context):
        pts = self.stroke.pts; normals = self.stroke.normals
        self._stroke_clear(context)
        return self._stroke_curve(context, pts, normals)

    def _stroke_curve(self, context, pts, normals):
        if not pts or not freehand.isLine(pts):
            return {'CANCELLED'}                                    # (too short: nothing, no undo step)
        t0 = time.perf_counter()
        hm = context.scene.hairmesh
        with slow.part('the stroke made a curve'):
            edit.touchGroup(self.P, self.parent)
            c = freehand.strokeCurve(self.P, pts, self.parent, f32(hm.draw_simplify), hm.draw_anchors == 'SHARP', normals, self.lift)
            done = self._done(context, c)
        TIMES['release'].append(time.perf_counter() - t0)
        del TIMES['release'][:-50]
        return done

    # ---- non-modal (tests, scripts): the points are given in the model's space ----
    def execute(self, context):
        if not self.points_json and not self.stroke_json:
            self.report({'ERROR'}, 'Draw in the 3D View with the Draw sub-tool, or pass points_json')
            return {'CANCELLED'}
        self.root = session.find_root(context)
        self.P = session.get_project(self.root)
        if self.stroke_json:
            d = json.loads(self.stroke_json)
            self.parent = self.group if self.group >= 0 else target_group(context, self.P)
            self.lift = self.P.S.scale * (f32(context.scene.hairmesh.draw_distance) / 100)
            return self._stroke_curve(context, [V3(*p) for p in d['pts']], [V3(*n) if n is not None else None for n in d['normals']])
        self.state = edit.DrawState()
        parent = self.group if self.group >= 0 else target_group(context, self.P)
        if parent is not None and parent not in self.P.items:
            self.report({'ERROR'}, f'No group {parent} in the project')
            return {'CANCELLED'}
        lock = cam = None
        if self.lock_json:                                          # (the Lock mode: {"anchors": [ids], "width": px, "pixel": model units per px, "view": [x, y, z]})
            d = json.loads(self.lock_json)
            missing = [i for i in d.get('anchors', ()) if i not in self.P.anchors]
            if missing:
                self.report({'ERROR'}, f'No anchor {missing[0]} in the project')
                return {'CANCELLED'}
            px = float(d.get('pixel', 0.001)); cam = V3(*d.get('view', (0, 0, -1)))
            lock = RB.LockDraw([self.P.anchors[i] for i in d.get('anchors', ())], LD.half(float(d.get('width', LD.W_DEFAULT))), lambda _p: px)
        edit.touchGroup(self.P, parent)
        for p in json.loads(self.points_json):
            born(context, edit.addDrawPointAt(self.P, self.state, V3(p[0], p[1], p[2]), parent, lock=lock, cam_dir=cam))
            if self.state.msg:
                self.report({'INFO'}, self.state.msg); self.state.msg = None
        c = edit.finishDrawing(self.P, self.state)
        if c is None:
            self.report({'INFO'}, 'A curve needs at least 2 points')
            if lock is not None:
                session.forget_model(self.root.name)
            return {'CANCELLED'}
        return self._done(context, c)


def shape_now(P, c, cam):
    """the lock being drawn shaped now, in memory (its curve sampled, its ribbon: rebuildRib) — the overlay draws it at
    once, as the original's frame would; the release's update builds the rest"""
    from ..core.curves import rebuild_dirty
    rebuild_dirty(P)
    if RB.rebuildRib(P, c, cam) is not None:                         # (a root that followed its anchors: the next frame)
        rebuild_dirty(P)
        RB.rebuildRib(P, c, cam)


def head_shown(root, P):
    return P.head is not None and P.head.visible and head_visible(root)


def stroke_hits(hm, root, P):
    """what a Freehand stroke lands on (Project On): the head where it shows, and with Surface the meshes too. With no
    head shown the control is dimmed, and a dimmed control changes nothing: the meshes, whatever it says"""
    shown = head_shown(root, P)
    hits = [lambda ray: pick.head_hit(P, ray)] if shown else []
    if hm.draw_project == 'SURFACE' or not shown:
        hits.append(lambda ray: pick.meshes_hit(bpy.context, root, P, ray))
    return hits


def target_menu_items(context):
    """Change Group ▸: the root level and every group, as Move to Group's list, the current one marked. A conscious
    exception to the rule "no selection buttons" (lot 4.3): new curves go to the group of the selection, as in the
    original, there is no destination of its own, so changing it IS selecting that group; the root level clears the
    selection"""
    from .ops_groups import group_tree
    _r, P, _s = select.current(context)
    if P is None:
        return [('label', 'No HairMesh project', 'INFO')]
    tree = group_tree(P)
    if not tree:
        return [('label', 'No groups', 'INFO')]
    cur = target_group(context, P)
    return [('op', 'hairmesh.select', '(root level)' + ('  ●' if cur is None else ''), 'OUTLINER_COLLECTION', {'deselect_all': True}),
            *(('op', 'hairmesh.select', '    ' * depth + g.name + ('  ●' if g.id == cur else ''), 'OUTLINER_COLLECTION', {'item': g.id}) for g, depth in tree)]


class HAIRMESH_MT_draw_target(bpy.types.Menu):
    """Pick the group new curves go into: it becomes the selection, as new curves follow the selection (the root level clears it)"""
    bl_idname = 'HAIRMESH_MT_draw_target'
    bl_label = 'Change Group'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        from .tool import draw_item
        hm = context.scene.hairmesh
        for item in target_menu_items(context):
            draw_item(self.layout, hm, item)


def draw_tool_rows(context, P, room):
    """The Draw page of the D (the Selection tab, before the selection): where the next curve goes, with Change Group
    (dimmed with no group to pick) beside it, and how its anchors are born, as buttons (G6); the mode, and what only Freehand reads when it is on (G4)"""
    from .tool import APART, GAP, align_fields, buttons, cols_fit, dim, enum, field, fitted, item_need, text, why_or_slot
    gid = target_group(context, P)
    g = P.items.get(gid) if gid is not None else None
    # (G6, G13: every name in one column of names, the modes at the width of their texts; the group a label, Change Group
    # apart beside it; G15: a gap between where the curve goes and how it is made)
    where = ('label', g.name if g is not None else '(root level)', 'OUTLINER_COLLECTION')
    change = ('menu', HAIRMESH_MT_draw_target.bl_idname, 'Change Group', 'OUTLINER_COLLECTION', *dim(bool(P.groups())))
    born, mode = enum('draw_anchors'), enum('draw_mode')
    per = max(item_need(context, born) / 2, item_need(context, mode) / 3)   # (one width for every button of the two rows: Sharp ends where Freehand does — the owner's call on the capture)
    rows = [[fitted(context, 'New Curves Go To', where, APART, (item_need(context, change), [change]))], [GAP],
            [fitted(context, 'New Anchors', born, width=2 * per)],
            [fitted(context, 'Mode', mode, width=3 * per)]]
    if context.scene.hairmesh.draw_mode == 'FREEHAND':
        root = session.find_root(context)
        on = root is not None and head_shown(root, P)
        say = None if on else 'Project On needs a head: Project > Set Head.' if P.head is None else 'The head is hidden: its eye in the HairMesh Outliner (O).'
        project = enum('draw_project', '', on)
        rows += [[GAP], [fitted(context, 'Project On', project, (1.0, [why_or_slot(say)]))],
                 [field('Distance from Head', ('prop', 'draw_distance', ''))], [field('Simplify', ('prop', 'draw_simplify', ''))]]
    elif context.scene.hairmesh.draw_mode == 'LOCK':
        rows += [[GAP], [field('Width', ('prop', 'lock_width', ''))],
                 [field('Starts', ('label', lock_start_text(P, select.current(context)[2]), 'NONE'))]]
    rows = align_fields(rows)
    if not cols_fit(context, rows[0][0][1], room):                  # (a long group name: the line as a paragraph, never cut, Change Group under it)
        rows[0:1] = [[text(f'New Curves Go To: {where[1]}', where[2])], [buttons(context, change)]]
    return rows


def lock_start_text(P, sel):
    """how the next lock starts with this selection (addDrawPoint 6209-6226): free; between 2 selected anchors; from 3 or
    more, a volume branch"""
    n = len(dict.fromkeys(a.id for a in select.anchors_of(P, sel))) if sel is not None else 0
    if n >= 3:
        return f'Branch From {n} Anchors'
    if n == 2:
        return 'Between 2 Anchors'
    return 'Free'


def register():
    bpy.utils.register_class(HAIRMESH_OT_draw_curve)
    bpy.utils.register_class(HAIRMESH_MT_draw_target)


def unregister():
    bpy.utils.unregister_class(HAIRMESH_MT_draw_target)
    bpy.utils.unregister_class(HAIRMESH_OT_draw_curve)
