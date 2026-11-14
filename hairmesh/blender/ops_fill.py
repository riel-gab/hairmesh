# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Fill the area under the pointer inside Blender (lot 1.1f): the pie's "Detect Fill Outline Here" (core/fillat.py,
the original's startFillAt, hair-modeler.clean.html:8273-8296).

  hairmesh.pie                 Shift+RMB: the context pie (HAIRMESH_MT_pie), the place it opened kept (PIE_AT) — the
                               pie's items know only where the pointer is when they are clicked
  hairmesh.fill_detect         the pie item: the outline at the place the pie opened, drawn in the original's colour,
                               and the confirmation pie (W Cancel, E Fill the Highlighted Outline, N its title); a
                               watcher clears the outline whatever closes that pie (onClose, 8287 / 8295). The form
                               without a viewport: point + view_matrix + region_size (+ confirm, the fill at once)
  hairmesh.fill_outline        the confirmation: the plan as found while the model is the same project instance with
                               the same gen and rev, else found again from the numbers PENDING keeps (undo-safe: no
                               RNA, no object of an older instance), fillat.fillFound, one undo step; a pending
                               conversion follows
  hairmesh.fill_outline_cancel Cancel

The item is in the pie only where it opened over a 3D View (paneAt, 9343): E of the pie with nothing selected (when
that direction is free), the Walls / Lock menu of the curve and wall pies, the anchor pie's submenu."""
import weakref

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatVectorProperty, IntVectorProperty
from mathutils import Vector

from ..core import fillat as FA, fillwatch as FW
from ..core.selection import Selection
from . import errorlog, live, owner, pick, select, session, slow, state, wording
from .coords import to_blender_xyz
from .ops_scissors import matrix_projector

DRAW = 'fill_outline'
COLOR = (0x5f / 255, 0xe0 / 255, 0xff / 255, 1.0)                   # fillViz (9210): 0x5fe0ff, 4 px, over everything (depthTest false)
WIDTH = 4.0
ICON = 'PIVOT_CURSOR'                                               # (G8: the place the pie opened, where the outline is looked for)
FILL_ICON = 'MESH_PLANE'                                            # (the fill, as Fill Hole)
PIE = 'HAIRMESH_MT_fill_pie'
AGAIN = 'The outline is not there any more: open the pie over the area again'

PIE_AT = None       # where the Shift+RMB pie opened: {'region': the region's pointer (an int), 'x', 'y' (region pixels, y up)}
PENDING = None      # the outline shown: plain values only (see show)
_batch: list = [None, None]   # (token, batch) of the outline being drawn
_found: list = [None, None]   # (token, what startFillAt found): the plan itself, kept for the confirmation while the model is the same
_token = [0]


def _region_at(context, ptr):
    """(window, area, region) of the 3D View region with this pointer, looked up again among the windows (never kept:
    a region may be freed meanwhile — a workspace switched, an area joined)"""
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for r in area.regions:
                if r.type == 'WINDOW' and r.as_pointer() == ptr:
                    return win, area, r
    return None


def pie_point(context):
    """(window, area, region, x, y) where the pie opened, over a 3D View; None otherwise"""
    at = PIE_AT
    if not at:
        return None
    w = _region_at(context, at['region'])
    return None if w is None else (*w, at['x'], at['y'])


def detect_item(context):
    """the pie item (8273: only when the pie is opened over the viewport — G4: gone otherwise)"""
    if pie_point(context) is None or session.find_root(context) is None:
        return None
    return ('op', 'hairmesh.fill_detect', wording.core(FA.ITEM_LABEL), ICON, {})


def view_of(area, region):
    """the view as numbers: the perspective matrix (world -> clip, 16 floats row by row) and the region's size — of
    the region itself (region.data, as context.region_data): in a quad view the space's region_3d is another quadrant's.
    (The space's only where the region has none: blender -b, no view drawn.)"""
    rv3d = region.data if region.data is not None else area.spaces.active.region_3d
    M = rv3d.perspective_matrix
    return tuple(v for row in M for v in row), (region.width, region.height)


def find(P, root, point, persp, size):
    """startFillAt at `point` (region pixels, y up) of the view (persp, size), y turned down for the core; the 3 px of
    its tie scaled by the interface scale (lot 1.2a, decision 13: the original's are CSS pixels)"""
    h = size[1]
    return FA.startFillAt(P, FW.pointer(point, h), FW.y_down(matrix_projector(persp, size, root.matrix_world), h), pick.ui_scale(bpy.context))


def _redraw(context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def show(context, root, P, found, point, persp, size, region_ptr=0):
    """the outline on screen (showFillViz, 9251; the hole's, 8283) until the confirmation pie goes. Returns its token."""
    global PENDING
    mw = root.matrix_world
    o = found['outline']
    world = [tuple(mw @ Vector(to_blender_xyz(o[i], o[i + 1], o[i + 2]))) for i in range(0, len(o) - 2, 3)]
    _token[0] += 1
    PENDING = {'token': _token[0], 'root': root.name, 'pid': weakref.ref(P), 'key': (P.gen, P.rev), 'point': tuple(point), 'persp': tuple(persp),
               'size': tuple(size), 'title': found['title'], 'world': world, 'region': region_ptr}
    _found[0] = _token[0]; _found[1] = found
    if not state.has(DRAW):
        state.add(DRAW, bpy.types.SpaceView3D, _draw, (), 'WINDOW', 'POST_VIEW')
    _redraw(context)
    return _token[0]


def clear(context=None):
    """the outline goes (showFillViz(null))"""
    global PENDING
    PENDING = None
    _batch[0] = _batch[1] = None
    _found[0] = _found[1] = None
    state.remove(DRAW)
    if context is not None:
        _redraw(context)


def mine(token):
    return PENDING is not None and PENDING['token'] == token


def _current(p):
    """the outline still shows the model on screen: the same root and project instance (an undo makes a new one), the
    same gen and rev (an edit since moved or changed the curves)"""
    c = session._cache.get(p['root'])
    return c is not None and p['pid']() is c[2] and (c[2].gen, c[2].rev) == p['key']   # (the instance itself: a dead one's address may be another's)


def _draw():
    p = PENDING
    if p is None or not p['world']:
        return
    ctx = bpy.context
    region = ctx.region
    if region is None or (p['region'] and region.as_pointer() != p['region']) or not _current(p):
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    if _batch[0] != p['token']:
        _batch[0] = p['token']; _batch[1] = batch_for_shader(sh, 'LINES', {'pos': p['world']})
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', WIDTH * pick.line_scale(ctx))
    sh.uniform_bool('lineSmooth', True)
    sh.uniform_float('color', COLOR)
    _batch[1].draw(sh)
    gpu.state.blend_set('NONE')


def fill(context, root, P, found, report):
    """the confirmation's work: fillFound (a hole: fillCycle; a plan: runFillPlan), then the commit — one undo step,
    the preview and a pending conversion inside it"""
    gen = P.gen
    with slow.part('fill the outline'):
        r = FA.fillFound(P, found)
    if P.gen == gen:                                                # (Nothing to fill there / filled already: nothing changed)
        report({'INFO'}, wording.core(r['toast']))
        return {'CANCELLED'}
    hm = context.scene.hairmesh
    sel = select.load(hm).prune(P)
    select.store(hm, Selection(list(r['sel']), []) if r['sel'] is not None else sel)
    session.save_project(root, P)                                   # the original's commit(): one undo step
    live.touch(root, [c.id for c in P.curves()])                    # (S.surfDirty: new walls, edge curves)
    live.update(context, root)                                      # the preview (and a pending conversion) inside the same undo step
    select.redraw(context)
    report({'INFO'}, wording.core(r['toast']))
    return {'FINISHED'}


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


class HAIRMESH_OT_pie(bpy.types.Operator):
    """Open the context pie menu for the selection. The place it opened is kept for Detect Fill Outline Here"""
    bl_idname = 'hairmesh.pie'
    bl_label = 'HairMesh Pie'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def invoke(self, context, event):
        global PIE_AT
        a, r = context.area, context.region
        PIE_AT = ({'region': r.as_pointer(), 'x': event.mouse_region_x, 'y': event.mouse_region_y}
                  if a is not None and a.type == 'VIEW_3D' and r is not None and r.type == 'WINDOW' else None)
        if PENDING is not None:
            clear(context)                                          # (an outline left by a pie that closed without an event since)
        # (popup_menu_pie with the key's own event: wm.call_menu_pie called from here takes the window's event state,
        # and the release of the key closed the pie at once — measured with real events, tools/sim_u3.py)
        context.window_manager.popup_menu_pie(event, _main_pie, title='HairMesh Pie')
        return {'FINISHED'}

    def execute(self, context):
        global PIE_AT
        PIE_AT = None
        bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name='HAIRMESH_MT_pie')
        return {'FINISHED'}


class HAIRMESH_OT_fill_detect(bpy.types.Operator):
    """Find the outline of the area where the pie was opened (rays from that place to the curves around it, or a hole in the walls) and highlight it; a second pie fills it with a wall or cancels"""
    bl_idname = 'hairmesh.fill_detect'
    bl_label = 'Detect Fill Outline Here'
    bl_options = {'REGISTER', 'UNDO'}

    point: FloatVectorProperty(name='Point', description='Non-modal form: the place in region pixels (x, y up)', size=2, options={'HIDDEN', 'SKIP_SAVE'})
    view_matrix: FloatVectorProperty(name='View', description="Non-modal form: the view's perspective matrix (world to clip, row by row)", size=16, options={'HIDDEN', 'SKIP_SAVE'})
    region_size: IntVectorProperty(name='Region', description='Non-modal form: the width and height of the region', size=2, default=(0, 0), options={'HIDDEN', 'SKIP_SAVE'})
    confirm: BoolProperty(name='Fill', description='Non-modal form: fill the outline at once instead of highlighting it', default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, _sel = select.current(context)
        if not all(self.region_size):
            self.report({'WARNING'}, 'Open the pie over the viewport to detect the outline there')
            return {'CANCELLED'}
        persp, size, point = tuple(self.view_matrix), tuple(self.region_size), tuple(self.point)
        with slow.part('detect the fill outline'):
            found = find(P, root, point, persp, size)
        if found['toast']:
            self.report({'INFO'}, wording.core(found['toast']))
            return {'CANCELLED'}
        if self.confirm:
            clear(context)
            return fill(context, root, P, found, self.report)
        show(context, root, P, found, point, persp, size)
        return {'CANCELLED'}                                        # (the outline shown, the model unchanged: no undo step — Fill makes it)

    def invoke(self, context, event):
        if all(self.region_size):
            return self.execute(context)
        at = pie_point(context)
        if at is None:
            self.report({'INFO'}, wording.core(FA.OPEN_OVER))
            return {'CANCELLED'}
        win, area, region, x, y = at
        root, P, _sel = select.current(context)
        persp, size = view_of(area, region)
        with slow.part('detect the fill outline'):
            found = find(P, root, (x, y), persp, size)
        if found['toast']:
            self.report({'INFO'}, wording.core(found['toast']))
            return {'CANCELLED'}
        self.token = show(context, root, P, found, (x, y), persp, size, region.as_pointer())
        context.window_manager.modal_handler_add(self)              # (before the pie: the pie's handler goes in front of it and takes every event while it is open)
        with context.temp_override(window=win, area=area, region=region):
            context.window_manager.popup_menu_pie(event, _fill_pie, title=found['title'], icon=ICON)   # (the core's title on the pie, 8286 / 8294)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            if FW.watch(event.type) == 'wait':
                return {'PASS_THROUGH'}
            if mine(self.token):                                    # (closed without Fill or Cancel: Esc, a click outside)
                clear(context)
            return {'CANCELLED', 'PASS_THROUGH'}                    # (nothing changed: no undo step)
        except Exception:
            errorlog.record('modal: fill outline watcher')
            clear(context)
            return {'CANCELLED'}

    def cancel(self, context):                                      # (Blender ends the modal: File > New / Open, the window closed)
        clear(context)


class HAIRMESH_OT_fill_outline(bpy.types.Operator):
    """Fill the highlighted outline with a wall: between the two curves around it (the facing branches of a Y; a zone of an X on its edge curves), or a hole along its rim"""
    bl_idname = 'hairmesh.fill_outline'
    bl_label = 'Fill the Highlighted Outline'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        p = PENDING
        kept = _found[1] if p is not None and _found[0] == p['token'] and _current(p) else None
        clear(context)
        root = bpy.data.objects.get(p['root']) if p is not None else None
        if p is None or root is None or not session.is_root(root):
            self.report({'WARNING'}, 'Detect the fill outline first: open the pie over the area to fill')
            return {'CANCELLED'}
        P = session.get_project(root)
        if kept is not None and p['pid']() is not P:                # (the plan's curves are another instance's: never fill with them)
            self.report({'INFO'}, AGAIN)
            return {'CANCELLED'}
        found = kept                                                # (the same project instance, gen and rev: the plan as found — its curves are this instance's)
        if found is None:
            with slow.part('detect the fill outline again'):
                found = find(P, root, p['point'], p['persp'], p['size'])   # (the model changed since: the plan of now, from the numbers kept)
        if found['toast'] or found['title'] != p['title']:
            self.report({'INFO'}, AGAIN)
            return {'CANCELLED'}
        return fill(context, root, P, found, self.report)


class HAIRMESH_OT_fill_outline_cancel(bpy.types.Operator):
    """Leave the area as it is: the highlighted outline goes"""
    bl_idname = 'hairmesh.fill_outline_cancel'
    bl_label = 'Cancel'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        clear(context)
        return {'FINISHED'}


def _main_pie(menu, context):
    from .tool import HAIRMESH_MT_pie
    HAIRMESH_MT_pie.draw(menu, context)


def _fill_pie(menu, context):
    HAIRMESH_MT_fill_pie.draw(menu, context)


def fill_pie_items():
    """the confirmation pie (8286-8287 / 8294-8295), G5: W Cancel, E Fill the Highlighted Outline; its title (the
    core's: the curves or the hole) is the pie's own, at the top"""
    if PENDING is None:
        return [('label', 'No outline detected', 'INFO')]
    return [('op', 'hairmesh.fill_outline_cancel', FA.CANCEL_LABEL, 'X', {}), ('op', 'hairmesh.fill_outline', wording.core(FA.CONFIRM_LABEL), FILL_ICON, {})]


class HAIRMESH_MT_fill_pie(bpy.types.Menu):
    """Fill the highlighted outline with a wall, or cancel"""
    bl_idname = PIE
    bl_label = 'HairMesh Fill'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        from .tool import draw_item
        pie = self.layout.menu_pie()
        hm = context.scene.hairmesh
        for item in fill_pie_items():
            draw_item(pie, hm, item)


CLASSES = (HAIRMESH_OT_pie, HAIRMESH_OT_fill_detect, HAIRMESH_OT_fill_outline, HAIRMESH_OT_fill_outline_cancel, HAIRMESH_MT_fill_pie)


@persistent
def _reset(*_a):
    """an undo, a redo, a file loaded: the outline goes (restore, 1358), and the place of the last pie"""
    global PIE_AT
    PIE_AT = None
    clear()


_HANDLERS = ('undo_post', 'redo_post', 'load_post')


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for h in _HANDLERS:
        getattr(bpy.app.handlers, h).append(_reset)


def unregister():
    global PIE_AT
    PIE_AT = None
    clear()
    for h in _HANDLERS:
        lst = getattr(bpy.app.handlers, h)
        if _reset in lst:
            lst.remove(_reset)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
