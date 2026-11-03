"""The images of the documentation (docs/img/), from a procedural scene: a sphere for a head and a few curves drawn on
it by the add-on's own operators. Runs in a Blender with a window (on the second monitor, without taking the focus):

    python tools/blender_run.py -- --factory-startup --enable-event-simulate --python tools/gen_doc_images.py [-- <out dir>]

The interface stays as it is: no region is hidden and no redraw is forced (hiding the header, the toolbar or the sidebar
right before a capture has crashed Blender 5.1 in its region animation). Each image is cut out of a screenshot of the
whole window: the 3D View area for the viewport, and for a pop-up or a pie the box of the pixels that changed when its
key was pressed. Nothing is saved; the default out dir is docs/img (public/docs/img in the private repository)."""
import json
import math
import os
import sys
import tempfile
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
_pub = os.path.join(ROOT, 'public')
OUT = os.path.abspath(argv[0]) if argv else os.path.join(_pub if os.path.isdir(_pub) else ROOT, 'docs', 'img')
os.makedirs(OUT, exist_ok=True)

import addon_utils                                                  # noqa: E402
import numpy as np                                                  # noqa: E402
from mathutils import Euler, Vector                                 # noqa: E402

bpy.context.preferences.use_preferences_save = False              # nothing of this instance reaches the preferences
bpy.context.preferences.view.show_tooltips = False                 # (a tooltip would cover the pop-ups)
addon_utils.enable('hairmesh', default_set=True)
bpy.context.window_manager.keyconfigs.update()
from hairmesh.blender import keymaps, session                       # noqa: E402

HAIR = (0.47, 0.20, 0.62, 1.0)
SKIN = (0.93, 0.76, 0.66, 1.0)
CURVES = 7                                                          # across the front of the head
log = {'shots': [], 'errors': []}


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def ov():
    win, area, region = view3d()
    return bpy.context.temp_override(window=win, area=area, region=region)


def ev(type_, value, rx, ry):
    win, _area, region = view3d()
    x, y = region.x + int(rx), region.y + int(ry)
    if type_ != 'MOUSEMOVE':
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    win.event_simulate(type=type_, value=value, x=x, y=y)


def press(key, rx, ry):
    ev(key, 'PRESS', rx, ry)
    ev(key, 'RELEASE', rx, ry)


def window_pixels():
    """the whole window, (H, W, 4) floats, row 0 at the bottom"""
    path = os.path.join(tempfile.gettempdir(), f'hairmesh_doc_window_{os.getpid()}.png')   # (never beside the images: a crash would leave it there)
    with ov():
        bpy.ops.screen.screenshot(filepath=path)
    im = bpy.data.images.load(path)
    w, h = im.size
    px = np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(im)
    os.remove(path)
    return px


def save(name, px):
    """a PNG of these pixels (row 0 at the bottom), opaque"""
    h, w = px.shape[:2]
    px = px.copy()
    px[..., 3] = 1.0
    im = bpy.data.images.new(name, w, h, alpha=False)
    im.pixels.foreach_set(px.ravel())
    im.filepath_raw = os.path.join(OUT, name + '.png')
    im.file_format = 'PNG'
    im.save()
    bpy.data.images.remove(im)
    log['shots'].append({'name': name, 'size': [w, h]})


def area_box():
    """(x0, y0, x1, y1) of the 3D View area in window pixels"""
    _win, area, _region = view3d()
    return area.x, area.y, area.x + area.width, area.y + area.height


def shot_area(name):
    x0, y0, x1, y1 = area_box()
    save(name, window_pixels()[y0:y1, x0:x1])


def changed_box(before, after, pad=6):
    """the box of the pixels that differ inside the 3D View area (a pop-up or a pie just opened)"""
    x0, y0, x1, y1 = area_box()
    diff = np.abs(after[y0:y1, x0:x1, :3] - before[y0:y1, x0:x1, :3]).max(axis=2) > 0.03
    # the rows and columns where the pop-up is: many changed pixels (a stray redraw of the viewport changes a few)
    xs = np.nonzero(diff.sum(axis=0) > 12)[0]
    ys = np.nonzero(diff.sum(axis=1) > 12)[0]
    if len(xs) == 0 or len(ys) == 0:
        raise RuntimeError('nothing changed on the screen')
    h, w = after.shape[:2]
    return (max(0, x0 + xs.min() - pad), max(0, y0 + ys.min() - pad),
            min(w, x0 + xs.max() + 1 + pad), min(h, y0 + ys.max() + 1 + pad))


def shot_popup(name, key, at):
    ev('MOUSEMOVE', 'NOTHING', *at)                                 # (the cursor in place first: its hover is in both screenshots)
    yield 0.6
    before = window_pixels()
    press(key, *at)
    yield 1.2
    after = window_pixels()
    bx0, by0, bx1, by1 = changed_box(before, after)
    save(name, after[by0:by1, bx0:bx1])
    press('ESC', *at)
    yield 0.6


def root():
    return next(o for o in bpy.data.objects if session.is_root(o))


def curve_points(k):
    """anchors of curve k, in the model space (the original's: Y up), just off a unit sphere: from the crown down over
    the forehead, spread across the front"""
    a = math.radians(35 + 110 * k / (CURVES - 1))                 # around the vertical axis, across the front (+z)
    pts = []
    for i in range(6):
        t = math.radians(12 + 17 * i)                               # down from the top
        r = 1.04
        pts.append([r * math.sin(t) * math.cos(a), r * math.cos(t), r * math.sin(t) * math.sin(a)])
    return pts


def look(space, yaw=20.0, pitch=78.0, dist=4.2):
    rv3d = space.region_3d
    rv3d.view_perspective = 'PERSP'
    rv3d.view_rotation = Euler((math.radians(pitch), 0.0, math.radians(yaw)), 'XYZ').to_quaternion()
    rv3d.view_location = Vector((0.0, 0.0, 0.25))
    rv3d.view_distance = dist


def steps():
    win, area, region = view3d()
    space = area.spaces.active
    mid = (region.width // 2, region.height // 2)
    corner = (region.width - 60, 60)
    press('ESC', *mid)                                              # (the splash)
    yield 0.4
    with ov():
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0)
    head = bpy.context.active_object
    head.name = 'Head'
    head.color = SKIN
    with ov():
        bpy.ops.object.shade_smooth()
        bpy.ops.hairmesh.new_project(name='hair')
        bpy.ops.hairmesh.add_group(name='Bangs')                    # (Convert takes the ticked groups; a new one is ticked)
        bpy.ops.wm.tool_set_by_id(name=keymaps.TOOL_ID)
    space.shading.color_type = 'OBJECT'
    space.overlay.show_floor = False
    space.overlay.show_axis_x = space.overlay.show_axis_y = False
    space.overlay.show_cursor = False
    look(space)
    yield 0.6

    # Draw: the first curves, the sub-tool active
    with ov():
        for k in range(3):
            bpy.ops.hairmesh.draw_curve(points_json=json.dumps(curve_points(k)))
    press('TWO', *corner)
    yield 0.8
    shot_area('draw')
    yield 0.3

    # the rest of the curves, the walls between them
    with ov():
        for k in range(3, CURVES):
            bpy.ops.hairmesh.draw_curve(points_json=json.dumps(curve_points(k)))
    P = session.get_project(root())
    hm = bpy.context.scene.hairmesh
    hm.sel_curves = json.dumps([c.id for c in P.curves()])
    with ov():
        bpy.ops.hairmesh.create_walls()
    hm.sel_curves = '[]'
    press('ONE', *corner)
    yield 1.0
    shot_area('walls')
    yield 0.3

    # the pop-ups and the pie, over the head; two curves selected, so the menu shows their sheet
    hm.sel_curves = json.dumps([c.id for c in P.curves()][2:4])
    area.tag_redraw()
    yield 0.4
    yield from shot_popup('menu', 'D', (mid[0] - 260, mid[1] + 200))
    yield from shot_popup('outliner', 'O', (mid[0] - 260, mid[1] + 200))
    hm.sel_curves = '[]'
    yield from shot_popup('subtool-pie', 'Q', mid)

    # the conversion
    with ov():
        if bpy.ops.hairmesh.convert() != {'FINISHED'}:
            raise RuntimeError('Convert did nothing')
    yield 2.0
    with ov():
        if bpy.ops.hairmesh.confirm_conversion() != {'FINISHED'}:
            raise RuntimeError('Confirm did nothing')
    yield 1.0
    for ob in bpy.data.objects:
        if ob.type == 'MESH' and ob is not head:
            ob.color = HAIR
            ob.data.shade_smooth()
            ob.show_wire = True                                     # (the quad loops)
    with ov():
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box')        # (the tool off: the mesh alone, no curves over it)
    ev('MOUSEMOVE', 'NOTHING', *corner)
    yield 0.8
    shot_area('converted')
    yield 0.3


_gen = steps()


def tick():
    try:
        d = next(_gen)
        return d if isinstance(d, (int, float)) else 0.4
    except StopIteration:
        pass
    except Exception:
        log['errors'].append(traceback.format_exc())
    print(json.dumps(log, indent=1))
    sys.stdout.flush()
    if log['errors']:
        os._exit(1)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.5)
