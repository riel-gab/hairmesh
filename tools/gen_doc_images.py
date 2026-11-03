"""The images of the documentation (docs/img/), each with a .json beside it: the state of the scene when it was taken
(tools/doc_tutorial.py, measure()). The Getting Started images are taken while the steps of that page run
(doc_tutorial.STEPS: the same operators, in the same order), each at the step it belongs to; the images of the other
pages after them. tests/test_doc_claims.py checks what the pages say about each image against its .json.

Runs in a Blender with a window (on the second monitor, without taking the focus):

    python tools/blender_run.py -- --factory-startup --enable-event-simulate --python tools/gen_doc_images.py [-- <out dir>]

The interface stays as it is: no region is hidden and no redraw is forced (hiding the header, the toolbar or the sidebar
right before a capture has crashed Blender 5.1 in its region animation). Each image is cut out of a screenshot of the
whole window: the 3D View area for the viewport, and for a pop-up or a pie the box of the pixels that changed when its
key was pressed. Nothing is saved; the default out dir is docs/img (public/docs/img in the private repository)."""
import json
import math
import os
import shutil
import sys
import tempfile
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
_pub = os.path.join(ROOT, 'public')
OUT = os.path.abspath(argv[0]) if argv else os.path.join(_pub if os.path.isdir(_pub) else ROOT, 'docs', 'img')
os.makedirs(OUT, exist_ok=True)
STAGE = tempfile.mkdtemp(prefix='hairmesh_doc_img_')               # (every image lands here first: OUT only gets a whole run)

import addon_utils                                                  # noqa: E402
import numpy as np                                                  # noqa: E402
from mathutils import Euler, Vector                                 # noqa: E402

bpy.context.preferences.use_preferences_save = False              # nothing of this instance reaches the preferences
bpy.context.preferences.view.show_tooltips = False                 # (a tooltip would cover the pop-ups)
addon_utils.enable('hairmesh', default_set=True)
bpy.context.window_manager.keyconfigs.update()
import doc_tutorial as T                                            # noqa: E402
from hairmesh.core import conversion as CONV                        # noqa: E402

log = {'shots': [], 'errors': []}


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def ov():
    win, area, region = view3d()
    return bpy.context.temp_override(window=win, area=area, region=region)


def ev(type_, value, rx, ry, **mods):
    win, _area, region = view3d()
    x, y = region.x + int(rx), region.y + int(ry)
    if type_ != 'MOUSEMOVE':
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y, **mods)
    win.event_simulate(type=type_, value=value, x=x, y=y, **mods)


def press(key, rx, ry, **mods):
    ev(key, 'PRESS', rx, ry, **mods)
    ev(key, 'RELEASE', rx, ry, **mods)


def window_pixels():
    """the whole window, (H, W, 4) floats, row 0 at the bottom"""
    path = os.path.join(tempfile.gettempdir(), f'hairmesh_doc_window_{os.getpid()}.png')   # (never beside the images: a crash would leave it there)
    try:
        with ov():
            bpy.ops.screen.screenshot(filepath=path)
        im = bpy.data.images.load(path)
        w, h = im.size
        px = np.array(im.pixels[:], dtype=np.float32).reshape(h, w, 4)
        bpy.data.images.remove(im)
    finally:
        if os.path.exists(path):
            os.remove(path)
    return px


def save(name, px, state):
    """a PNG of these pixels (row 0 at the bottom), opaque, and the state measured with it"""
    h, w = px.shape[:2]
    px = px.copy()
    px[..., 3] = 1.0
    im = bpy.data.images.new(name, w, h, alpha=False)
    im.pixels.foreach_set(px.ravel())
    im.filepath_raw = os.path.join(STAGE, name + '.png')
    im.file_format = 'PNG'
    im.save()
    bpy.data.images.remove(im)
    with open(os.path.join(STAGE, name + '.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(state, f, indent=1, sort_keys=True)
        f.write('\n')
    log['shots'].append({'name': name, 'size': [w, h], 'state': state})


def area_box():
    """(x0, y0, x1, y1) of the 3D View area in window pixels"""
    _win, area, _region = view3d()
    return area.x, area.y, area.x + area.width, area.y + area.height


def measure(pie=None):
    with ov():
        return T.measure(bpy.context, pie)


def shot_area(name):
    x0, y0, x1, y1 = area_box()
    save(name, window_pixels()[y0:y1, x0:x1], measure())


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


def shot_popup(name, key, at, pie=None, **mods):
    """the pop-up or pie `key` opens at `at`; its state is measured while it is open (the pie: what it was drawn from)"""
    ev('MOUSEMOVE', 'NOTHING', *at)                                 # (the cursor in place first: its hover is in both screenshots)
    yield 0.6
    before = window_pixels()
    state = measure(pie)
    press(key, *at, **mods)
    yield 1.2
    after = window_pixels()
    bx0, by0, bx1, by1 = changed_box(before, after)
    save(name, after[by0:by1, bx0:bx1], state)
    press('ESC', *at)
    yield 0.6


def look(space, yaw=-12.0, pitch=78.0, dist=3.6):
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
    pie_at = (mid[0] + 220, mid[1])                                 # (beside the head: the pie does not cover the curves)
    press('ESC', *mid)                                              # (the splash)
    yield 0.4
    for step, _what, key, shot in T.STEPS:
        if key is not None:
            press(key, *corner)
            yield 0.4
        T.run(step, ov)
        if step == 'head':
            space.overlay.show_floor = False
            space.overlay.show_axis_x = space.overlay.show_axis_y = False
            space.overlay.show_cursor = False
            look(space)
        area.tag_redraw()                                           # (an operator run from a timer does not redraw the overlay)
        ev('MOUSEMOVE', 'NOTHING', *corner)
        yield 2.0 if step == 'preview' else 0.8
        if shot is None:
            continue
        name, kind = shot
        if kind == 'area':
            shot_area(name)
            yield 0.3
        else:
            yield from shot_popup(name, 'RIGHTMOUSE', pie_at, pie='main', shift=True)

    # the other pages: in the Live view, nothing selected for the Q pie, the two curves for the menu and the outliner
    if CONV.viewOf(T.project()) != 'live':
        with ov():
            T.ok(T.view_op('live'), 'Live')
    for name, key, pie in T.PAGE_SHOTS:
        if name == 'subtool-pie':
            with ov():
                T.ok(bpy.ops.hairmesh.select(deselect_all=True), 'Deselect')
            at = mid
        else:
            T.select_curves(ov)
            at = (mid[0] - 260, mid[1] + 200)
        area.tag_redraw()
        yield 0.4
        yield from shot_popup(name, key, at, pie=pie)


_gen = steps()


def tick():
    try:
        d = next(_gen)
        return d if isinstance(d, (int, float)) else 0.4
    except StopIteration:
        pass
    except Exception:
        log['errors'].append(traceback.format_exc())
    if not log['errors']:
        # the whole run replaces the images it took (a failed one leaves docs/img as it was)
        for n in sorted(os.listdir(STAGE)):
            shutil.move(os.path.join(STAGE, n), os.path.join(OUT, n))
    shutil.rmtree(STAGE, ignore_errors=True)
    print(json.dumps(log, indent=1))
    sys.stdout.flush()
    if log['errors']:
        os._exit(1)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.5)
