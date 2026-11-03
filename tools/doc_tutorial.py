"""The Getting Started page (docs/getting-started.md) as steps that run: the same operators the text tells the reader to
use, in the same order. tools/gen_doc_images.py runs them in a Blender with a window and takes each image of the page
at the step it belongs to, with a .json beside it: the state measured in the scene at that moment (measure()).
tests/test_doc_claims.py checks the claims the text makes (<!-- claims ... --> beside each image) against those files.

Change the page and this list together: a step the text adds, removes or reorders is a step here."""
import json
import math

import bpy

# the head and the two curves of the tutorial: a sphere, and two curves side by side over the front of it, from the
# crown down (the model space of the original: Y up; the front of the head is +z)
CURVE_ANGLES = (75.0, 105.0)
DIRECTIONS = ('W', 'E', 'S', 'N', 'NW', 'NE', 'SW', 'SE')            # Blender's pie order (UILayout.menu_pie)
NUMPAD = {'W': 4, 'E': 6, 'S': 2, 'N': 8, 'NW': 7, 'NE': 9, 'SW': 1, 'SE': 3}   # the number a pie shows and takes per direction
COLOR_WORDS = {'MAT_PREVIEW': 'orange', 'MAT_MESH': 'light'}       # the words the pages use for blender/meshes.py's two materials


def curve_points(angle_deg):
    a = math.radians(angle_deg)
    pts = []
    for i in range(6):
        t = math.radians(12 + 17 * i)                               # down from the top of the head
        r = 1.04
        pts.append([r * math.sin(t) * math.cos(a), r * math.cos(t), r * math.sin(t) * math.sin(a)])
    return pts


# (id, what the reader does, the key the reader presses first or None, the image of the page taken after the step:
# (name, 'area' for the 3D View or 'pie' for the Shift+RMB pie opened there) or None). run() below does each step.
STEPS = (
    ('head', 'Add a head mesh and make it the active object', None, None),
    ('project', 'New Project..., with Head checked', None, None),
    ('pie-new-group', 'With nothing selected, Shift+RMB: the pie with New Group', None, ('gs-pie', 'pie')),
    ('group', 'New Group', None, None),
    ('draw', 'Press 2 and draw two curves side by side', 'TWO', ('gs-draw', 'area')),
    ('select-curves', 'Esc to Select, click one curve, Shift+click the other, Shift+RMB', 'ESC', ('gs-wall-pie', 'pie')),
    ('wall', 'Walls / Lock > Create Wall', None, ('gs-wall', 'area')),
    ('preview', 'D > Conversion > Convert: the pending preview', None, ('gs-preview', 'area')),
    ('confirmed', 'Confirm', None, ('gs-confirmed', 'area')),
    ('live', 'Live: the walls and curves again', None, ('gs-live', 'area')),
    ('baked', 'Baked: the converted meshes again', None, None),     # (the view Confirm leaves: the image is gs-confirmed)
)
# the images of the other pages, after the tutorial, in the Live view: (name, the key that opens it, the pie measured)
PAGE_SHOTS = (
    ('subtool-pie', 'Q', 'subtools'),                               # tools.md: the Q pie, nothing selected
    ('menu', 'D', None),                                            # tools.md: the HairMesh Menu, the two curves selected
    ('outliner', 'O', None),                                        # workflow.md: the HairMesh Outliner
)


def root():
    from hairmesh.blender import session
    return next(o for o in bpy.data.objects if session.is_root(o))


def project():
    from hairmesh.blender import session
    return session.get_project(root())


KEY_SUBTOOLS = {'TWO': 'DRAW', 'ESC': 'SELECT'}                    # the sub-tool each key of STEPS leaves (Esc: back to Select)


def press_headless(key):
    """a key of STEPS without a window (tests/test_blender_doc_images.py): the sub-tool it switches to, by the operator
    of the sub-tool buttons"""
    ok(bpy.ops.hairmesh.set_subtool(subtool=KEY_SUBTOOLS[key]), key)


def run(step, ov, headless=False):
    """does what the reader does at this step; `ov` gives a context override of the 3D View. `headless`: without a
    window (no toolbar to switch the tool in)"""
    from hairmesh.blender import keymaps
    if step == 'head':
        with ov():
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete()
            bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0)
            bpy.ops.object.shade_smooth()
        bpy.context.active_object.name = 'Head'
    elif step == 'project':
        with ov():
            ok(bpy.ops.hairmesh.new_project(name='hair'), 'New Project')
            if not headless:
                ok(bpy.ops.wm.tool_set_by_id(name=keymaps.TOOL_ID), 'the HairMesh tool')
    elif step == 'pie-new-group':
        pass                                                        # (nothing selected: the pie opens as it is)
    elif step == 'group':
        with ov():
            ok(bpy.ops.hairmesh.add_group(), 'New Group')
    elif step == 'draw':
        with ov():
            for a in CURVE_ANGLES:
                ok(bpy.ops.hairmesh.draw_curve(points_json=json.dumps(curve_points(a))), 'Draw')
    elif step == 'select-curves':
        select_curves(ov)
    elif step == 'wall':
        with ov():
            ok(bpy.ops.hairmesh.create_walls(), 'Create Wall')
    elif step == 'preview':
        with ov():
            ok(bpy.ops.hairmesh.convert(), 'Convert')
    elif step == 'confirmed':
        with ov():
            ok(bpy.ops.hairmesh.confirm_conversion(), 'Confirm')
    elif step in ('baked', 'live'):
        with ov():
            ok(view_op(step), step.title())
    else:
        raise ValueError(f'no step {step!r}')


def select_curves(ov):
    """the click on the first curve and the Shift+click on the second: the Select operator's non-modal form"""
    ids = [c.id for c in project().curves()]
    with ov():
        for k, cid in enumerate(ids):
            ok(bpy.ops.hairmesh.select(curve=cid, extend=k > 0), 'Select')


def view_op(view):
    """the Live / Baked button: the operator the tool's own item calls"""
    from hairmesh.blender import tool
    c = tool.convert_items(project())
    it = c[view]
    return getattr(getattr(bpy.ops, it[1].split('.')[0]), it[1].split('.')[1])(**it[4])


def ok(result, what):
    if result != {'FINISHED'}:
        raise RuntimeError(f'{what} did nothing ({sorted(result)})')
    return result


def pie_labels(items):
    """[{dir, numpad, label}] of a pie's 8 items, as the pie draws them"""
    out = []
    for d, it in zip(DIRECTIONS, items):
        out.append({'dir': d, 'numpad': NUMPAD[d], 'label': item_label(it)})
    return out


def item_label(it):
    """the text a pie slot shows (None: an empty direction)"""
    if not it or it[0] == 'empty':
        return None
    if it[0] in ('op', 'menu', 'subtool', 'toggle'):
        return it[2]
    if it[0] in ('box', 'row'):
        return ' / '.join(l for l in (item_label(x) for x in it[1]) if l) or None
    if it[0] == 'prop':
        return it[2] if len(it) > 2 and isinstance(it[2], str) else None
    if it[0] in ('label', 'text'):
        return it[1]
    raise ValueError(f'a pie item of an unknown kind: {it[0]!r}')


def measure(context, pie=None):
    """the state of the scene the claims of the text are checked against"""
    from hairmesh.blender import live as L, meshes as M, select, tool
    from hairmesh.core import conversion as CONV
    P = project()
    hm = context.scene.hairmesh
    sel = select.load(hm).prune(P)
    meshes = CONV.meshes(P)
    words = {getattr(M, k): w for k, w in COLOR_WORDS.items()}
    shown = [ob for ob in bpy.data.objects if ob.type == 'MESH' and ob.get('hairmesh_type') == 'mesh' and ob.visible_get()]
    # the walls on screen: the ones each visible live mesh holds, as stamped on its datablock (Baked empties it)
    walls_shown = [w for ob in bpy.data.objects if ob.get('hairmesh_type') == 'live' and ob.visible_get() and ob.data is not None
                   for w in json.JSONDecoder().raw_decode(ob.data.get(L.SHOWN_KEY) or '[]')[0]]   # (the crowns follow it)
    state = {
        'curves': len(P.curves()),
        'walls': sum(1 for it in P.items.values() if it.type == 'surface'),
        'walls_shown': len(walls_shown),
        'groups': len(list(P.groups())),
        'pending': sum(1 for m in meshes if m.pending),
        'confirmed': sum(1 for m in meshes if not m.pending),
        'meshes_shown': len(shown),
        'color': sorted({words.get(ob.active_material.name if ob.active_material else '', 'other') for ob in shown}),
        'view': CONV.viewOf(P),
        'subtool': hm.subtool,
        'selected_curves': len(select.curves_of(P, sel)),
        'selected_items': len(sel.items),
        'selected_anchors': len(sel.anchors),
    }
    if pie == 'subtools':
        state['pie'] = {'name': 'HAIRMESH_MT_subtools', 'slots': pie_labels(tool.subtool_pie_items(context))}
    elif pie == 'main':
        state['pie'] = {'name': 'HAIRMESH_MT_pie', 'slots': pie_labels(tool.pie_items(context))}
    return state
