# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Picking in screen space: a port of the original's picking helpers (hair-modeler.clean.html:5179-5238), pure
functions over a `project(p) -> (x, y, z) | None` callback, so they run without a viewport (tests use a fixed
projection); the cursor's ray in the model's space (model_ray) and the wall under it (pick_wall: the ray against
the walls' grids and the head, the nearest wins, as pickObjects over pickables(), 5658). `z` is the normalised depth of toScreen (5179): > 1 means beyond the far plane, skipped like the
original; None means behind the camera. Distances are in region pixels, radii already scaled by the UI scale.

The Blender projector reproduces toScreen with the region's perspective matrix: x, y in region pixels (y up, the
same frame as event.mouse_region_x/y), z = clip z / w."""
import math
from math import hypot

from mathutils import Matrix, Vector

from ..core.js import clamp
from . import errorlog
from .coords import to_blender


def blender_xray(context):
    """Blender's own X-ray in the 3D View being drawn / clicked (Alt+Z): solid shading's show_xray, wireframe's
    show_xray_wireframe (3.8c). The overlay then draws without depth test; anchors are picked behind the head anyway
    (pickAnchor, 5190, never tests depth), and a Shift slide no longer counts the head as hiding a curve."""
    sp = getattr(context, 'space_data', None)
    if sp is None or sp.type != 'VIEW_3D':
        return False
    sh = sp.shading
    # as Blender's SHADING_XRAY_ENABLED: on, and see-through (an alpha of 1 hides as without X-ray)
    if sh.type == 'WIREFRAME':
        return bool(sh.show_xray_wireframe) and sh.xray_alpha_wireframe < 1.0
    return bool(sh.show_xray) and sh.xray_alpha < 1.0 if sh.type == 'SOLID' else False


def pick_anchor(anchors, project, x, y, radius=10):
    # port of pickAnchor (5190): the nearest anchor within `radius` px
    best = None; bd = radius
    for a in anchors:
        s = project(a.pos)
        if s is None or s[2] > 1:
            continue
        d = hypot(s[0] - x, s[1] - y)
        if d < bd:
            bd = d; best = a
    return best


def pick_curve(curves, project, x, y, radius=7):
    # port of pickCurve (5196): the nearest sample segment of any curve within `radius` px.
    # Returns {curve, i, t, point, seg} or None. Callers pass only the visible curves (c.obj.visible).
    best = None; bd = radius
    for c in curves:
        sp = screen_samples(c, project)
        for i in range(len(sp) - 1):
            a, b = sp[i], sp[i + 1]
            if a is None or b is None or a[2] > 1 or b[2] > 1:
                continue
            vx = b[0] - a[0]; vy = b[1] - a[1]; l2 = vx * vx + vy * vy
            t = clamp(((x - a[0]) * vx + (y - a[1]) * vy) / l2, 0, 1) if l2 > 0 else 0
            d = hypot(a[0] + vx * t - x, a[1] + vy * t - y)
            if d < bd:
                bd = d; best = {'curve': c, 'i': i, 't': t}
    if best:
        s0 = best['curve'].samples[best['i']]; s1 = best['curve'].samples[best['i'] + 1]
        best['point'] = s0.p.clone().lerp(s1.p, best['t']); best['seg'] = s0.seg
    return best


_screen = {}                                                        # curve id -> (its samples list, the projector's key, the samples projected)


def screen_samples(c, project):
    """[project(s.p) for s in c.samples], kept while the curve keeps its samples list and the view its projector
    (region_projector's key): the same numbers (lot 4.7b item 5 — Shift + drag projected every curve's samples on
    every move, ~60 000 projections)"""
    key = getattr(project, 'key', None)
    if key is None:
        return [project(s.p) for s in c.samples]
    e = _screen.get(c.id)
    if e is not None and e[0] is c.samples and e[1] == key:
        return e[2]
    sp = [project(s.p) for s in c.samples]
    _screen[c.id] = (c.samples, key, sp, None)
    return sp


def screen_box(c, project):
    """(x0, y0, x1, y1): the box of c's projected samples nearest_on_screen can use (in front: z <= 1), or None —
    no point of its segments lies outside it"""
    sp = screen_samples(c, project)
    e = _screen.get(c.id)
    if e is not None and e[2] is sp and e[3] is not None:
        return e[3][0]
    ok = [s for s in sp if s is not None and s[2] <= 1]
    box = (min(s[0] for s in ok), min(s[1] for s in ok), max(s[0] for s in ok), max(s[1] for s in ok)) if ok else None
    if e is not None and e[2] is sp:
        _screen[c.id] = (e[0], e[1], sp, (box,))
    return box


SNAP_PRUNE = 30                                                     # snapDragToCurve: only the curves within 30 px of the nearest are checked for occlusion (5730)


def nearest_on_screen(c, project, x, y):
    # port of nearestOnScreen (5227): the closest point of curve c to (x, y) on screen: {d, point} or None
    best = None; bd = float('inf')
    sp = screen_samples(c, project)
    for i in range(len(sp) - 1):
        a, b = sp[i], sp[i + 1]
        if a is None or b is None or a[2] > 1 or b[2] > 1:
            continue
        vx = b[0] - a[0]; vy = b[1] - a[1]; l2 = vx * vx + vy * vy
        t = clamp(((x - a[0]) * vx + (y - a[1]) * vy) / l2, 0, 1) if l2 > 0 else 0
        d = hypot(a[0] + vx * t - x, a[1] + vy * t - y)
        if d < bd:
            bd = d; best = {'d': d, 'point': c.samples[i].p.clone().lerp(c.samples[i + 1].p, t)}
    return best


def region_projector(region, rv3d, matrix_world):
    """toScreen (5179) for a 3D View region: model space (the original's, under the root's matrix) -> region pixels."""
    M = rv3d.perspective_matrix @ matrix_world
    w, h = region.width, region.height

    def project(p):
        x, y, z = to_blender(p)
        v = M @ Vector((x, y, z, 1.0))
        if v.w <= 0.0:
            return None
        return ((v.x / v.w + 1.0) * 0.5 * w, (v.y / v.w + 1.0) * 0.5 * h, v.z / v.w)
    project.key = (tuple(tuple(r) for r in M), w, h)                # (screen_samples: the same view, the same pixels)
    return project


def ui_scale(context):
    s = context.preferences.system.ui_scale
    return s if s and s > 0 else 1.0


def line_factor(ui, line):
    """Pure: the factor of a thickness or a point size for the UI scale `ui` and preferences.system.ui_line_width.
    `line` already holds the scale, in whole steps — measured on 5.1.2: max(1, dpi // 64 + {Thin: -1, Default: 0,
    Thick: 1}), dpi = 72 x ui (Default: 1 up to 1.75, 2 from 1.8, 3 at 3.0) — so the two are never multiplied: the
    scale once, then the step of the Line Width preference over what Default gives at that scale. At Default this is
    `ui` itself; at 100 % Thin draws as Default, as Blender's own lines do."""
    auto = max(1, int(72.0 * ui + 0.5) // 64)
    return ui * max(1.0, line) / auto


def line_scale(context):
    """G11: what every thickness and point size drawn over the viewport is multiplied by (the sizes in the code are at
    100 %): it grows with the UI scale and answers to Preferences > Interface > Line Width. Positions, radii that
    measure something (the brush, a pick radius, the profile frames) and text stay on ui_scale."""
    return line_factor(ui_scale(context), context.preferences.system.ui_line_width)


def model_matrix(root):
    """root local space -> world: what the projector and the drawing multiply the axis-swapped model by."""
    return root.matrix_world if root is not None else Matrix.Identity(4)


# ---------------- the cursor's ray and the camera in the model's space (3.2) ----------------

def model_ray(root, region, rv3d, coord):
    """The cursor's ray (rayAt, 5176) in the model's space: region pixel -> world -> the root's local space ->
    the original's axes. Works for perspective and orthographic views."""
    from bpy_extras import view3d_utils
    from ..core.raycast import Ray
    from ..core.vec3 import V3
    from .coords import from_blender
    o = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    d = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    inv = root.matrix_world.inverted()
    o = inv @ o
    d = (inv.to_3x3() @ d).normalized()
    return Ray(V3(*from_blender(o.x, o.y, o.z)), V3(*from_blender(d.x, d.y, d.z)))


def model_cam(root, rv3d):
    """camDir (4246) and the orbit pivot (the view's location, the original's `pivot`) in the model's space."""
    from ..core.vec3 import V3
    from .coords import from_blender
    inv = root.matrix_world.inverted()
    d = (inv.to_3x3() @ (rv3d.view_rotation @ Vector((0.0, 0.0, -1.0)))).normalized()
    piv = inv @ rv3d.view_location
    return V3(*from_blender(d.x, d.y, d.z)), V3(*from_blender(piv.x, piv.y, piv.z))


def head_visible(root):
    """the head object is shown in the viewport (isVisible(S.head) for the picking and the drawing)"""
    from . import session
    ob = session.head_object(root)
    return ob is None or not (ob.hide_viewport or ob.hide_get())


# ---------------- phase 3.5: the wall under the cursor ----------------

def wall_meshes(root, P):
    """A triangle mesh (the core's TriMesh, with its bounding box and sphere) per wall, built from the live
    preview's grids (fresh: live.grids rebuilds what changed) and cached by the grid's revision. The grids exist
    whether the "<group> live" objects are shown or not, so a click finds a wall with the preview off too (1a of
    the manual test of 15/10/2026, an addition of this port: the original cannot pick a hidden surface)."""
    from ..core.raycast import DoubleSide, TriMesh
    from . import live, session
    g, _changed = live.grids(root, P)
    st = session.state(root, P)
    rev = st.get('grid_rev') or {}
    cache = st.setdefault('pick_meshes', {})
    out = {}
    for sid, d in g.items():
        if d is None or not d.faces:
            continue
        c = cache.get(sid)
        if c is None or c[0] != rev.get(sid):
            idx = []
            for f in d.faces:
                for k in range(1, len(f) - 1):
                    idx.extend((f[0], f[k], f[k + 1]))             # the fan of buildMeshObj (2923)
            c = (rev.get(sid), TriMesh(d.pos, idx, DoubleSide))      # NURBS walls are seen from both sides
            cache[sid] = c
        out[sid] = c[1]
    for sid in [k for k in cache if k not in g]:
        del cache[sid]
    return out


def pick_wall(P, ray, meshes, shown=None, head=True):
    """port of pickObjects(x, y, pickables()) (5658-5668) for the walls: the nearest hit among the head's front
    faces (when `head`) and every shown wall (`shown(sf)`, default: isVisible) wins, so a wall behind the head is
    not picked and the click starts a box instead, as in the original. `ray` is in the model's space, `meshes`
    wall_meshes(). Returns (wall id, distance) or None (nothing, or the head first)."""
    from ..core import edit
    from ..core.raycast import Raycaster
    best = None
    rc = Raycaster()
    rc.set(ray.origin, ray.direction)
    for sf in P.surfaces():
        m = meshes.get(sf.id)
        if m is None or not (shown(sf) if shown is not None else edit.isVisible(P, sf)):
            continue
        hits = rc.intersectObject(m)
        if hits and (best is None or hits[0]['distance'] < best[1]):
            best = (sf.id, hits[0]['distance'])
    if best is not None and head:
        h = edit.pickHead(P, ray)
        if h is not None and h['distance'] < best[1]:
            return None
    return best


def wall_under(context, root, x, y, head=True):
    """The wall under the region pixel (x, y): the cursor's ray in the model's space against the walls' grids and
    the head (pick_wall; a wall item is selected by the click, 5849). None without a hit, with the head in front,
    without a region, or whenever the grids cannot be built: picking never raises."""
    from ..core import edit
    from . import session
    region = context.region; rv3d = context.region_data
    if region is None or rv3d is None:
        return None
    try:
        P = session.get_project(root)
        ray = model_ray(root, region, rv3d, (x, y))
        meshes = wall_meshes(root, P)
        hit = pick_wall(P, ray, meshes, head=head and P.head is not None and P.head.visible and head_visible(root) and edit.head_trimesh(P) is not None)
    except Exception as e:
        errorlog.record('pick: wall under the cursor')
        print('HairMesh wall picking:', repr(e))
        return None
    return hit[0] if hit is not None else None


# ---------------- lot 4.2 ∥ 4.5, item 1 (ours, the owner's request): the converted mesh under the cursor ----------------

_conv_tris = {}                                                     # data token -> (lo, hi, v0, e1, e2): a converted mesh's triangles
CONV_TRIS_KEEP = 64                                                 # tokens kept (a rebuilt or baked mesh gets a new one)


def tri_arrays(pos, faces):
    """(lo, hi, v0, e1, e2): the fan of `faces` over `pos` ((x, y, z) rows) as numpy arrays — corner, two edges, the
    bounding box — for ray_tris. A triangle with an index out of range or a corner that is not finite (a degenerate
    grid) is left out, so it never takes the box (and every other triangle's click) with it."""
    import numpy as np
    pos = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    idx = []
    for f in faces:
        for k in range(1, len(f) - 1):
            idx.append((f[0], f[k], f[k + 1]))
    t = np.array(idx, dtype=np.int64).reshape(-1, 3)
    if len(t):
        t = t[np.all((t >= 0) & (t < len(pos)), axis=1)]
    if len(t):
        t = t[np.all(np.isfinite(pos[t]), axis=(1, 2))]
    v0 = pos[t[:, 0]]; e1 = pos[t[:, 1]] - v0; e2 = pos[t[:, 2]] - v0
    if not len(t):
        return (np.zeros(3), np.zeros(3), v0, e1, e2)
    used = pos[np.unique(t)]
    return (used.min(axis=0), used.max(axis=0), v0, e1, e2)


def conv_tris(m):
    """The triangles of a converted mesh item's data (the fan of its faces, in the model's space) as tri_arrays,
    cached by the data's token: a rebuilt or baked mesh gets a new token, the same data after an undo keeps its own,
    so a click costs one vectorised ray test, never a rebuild. Picking only (no bit of the model comes from it): the
    original cannot click a converted mesh in its Select tool."""
    c = _conv_tris.get(m.token) if m.token else None
    if c is not None:
        return c
    c = tri_arrays([(p.x, p.y, p.z) for p in m.data.pos], m.data.faces)
    if m.token:
        _conv_tris[m.token] = c
        while len(_conv_tris) > CONV_TRIS_KEEP:
            del _conv_tris[next(iter(_conv_tris))]
    return c


def ray_tris(o, d, tris, front=False, mask=None):
    """The nearest hit of the ray o + t d (`d` a unit vector: t is the distance) on tri_arrays `tris`: (t, triangle
    index), or None. Both sides (a hair sheet is seen from both), or with `front` only the faces turned to the ray (a
    three.js FrontSide mesh: the head); `mask` (booleans, one per triangle) leaves the others out. A slab test on
    the box, then Möller–Trumbore for every triangle at once."""
    import numpy as np
    lo, hi, v0, e1, e2 = tris
    if not len(v0):
        return None
    o = np.asarray(o, dtype=np.float64); d = np.asarray(d, dtype=np.float64)
    with np.errstate(divide='ignore', invalid='ignore'):
        inv = 1.0 / d
        t0 = (lo - o) * inv; t1 = (hi - o) * inv
        tn = np.nanmax(np.minimum(t0, t1)); tf = np.nanmin(np.maximum(t0, t1))
    if not tf >= max(tn, 0.0):
        return None
    pv = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, pv)
    ok = det > 1e-18 if front else np.abs(det) > 1e-18
    if mask is not None:
        ok &= mask
    if not ok.any():
        return None
    with np.errstate(divide='ignore', invalid='ignore'):
        inv_det = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = o - v0
        u = np.einsum('ij,ij->i', tv, pv) * inv_det
        qv = np.cross(tv, e1)
        v = (qv @ d) * inv_det
        t = np.einsum('ij,ij->i', e2, qv) * inv_det
    hit = ok & (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > 0.0)
    if not hit.any():
        return None
    i = int(np.flatnonzero(hit)[np.argmin(t[hit])])
    return float(t[i]), i


def wall_soup(root, P):
    """Every wall's grid as one tri_arrays plus the wall id of each triangle, for the click (4.2 ∥ 4.5, item 1):
    kept while the model and the grids stay as they were (P.gen, P.rev, live.revision), so a click does not pay the
    freshness check of every row table (~20 ms on the example, as the Scissors' _cut_mesh, 3.8f) nor a pure-Python
    ray test over 306 walls (~35 ms); after an edit the first click rebuilds it (live.grids, then the arrays)."""
    import numpy as np
    from . import live, session
    st = session.state(root, P)
    c = st.get('wall_soup')
    if c is not None and c[0] == (P.gen, P.rev, live.revision(root)):
        return c[1]
    g, _changed = live.grids(root, P)
    rev = st.get('grid_rev') or {}
    parts = st.setdefault('wall_soup_parts', {})                    # wall id -> (grid revision, tri_arrays): only the changed walls again
    for sid in [k for k in parts if k not in g]:
        del parts[sid]
    v0 = []; e1 = []; e2 = []; lo = []; hi = []; ids = []
    for sid, d in g.items():
        if d is None or not d.faces:
            parts.pop(sid, None)
            continue
        c = parts.get(sid)
        if c is None or c[0] != rev.get(sid):
            c = (rev.get(sid), tri_arrays([(p.x, p.y, p.z) for p in d.pos], d.faces))
            parts[sid] = c
        t = c[1]
        if not len(t[2]):
            continue
        lo.append(t[0]); hi.append(t[1]); v0.append(t[2]); e1.append(t[3]); e2.append(t[4])
        ids.append(np.full(len(t[2]), sid, dtype=np.int64))
    if v0:
        soup = ((np.min(lo, axis=0), np.max(hi, axis=0), np.concatenate(v0), np.concatenate(e1), np.concatenate(e2)), np.concatenate(ids))
    else:
        z = np.zeros((0, 3))
        soup = ((np.zeros(3), np.zeros(3), z, z, z), np.zeros(0, dtype=np.int64))
    st['wall_soup'] = ((P.gen, P.rev, live.revision(root)), soup)
    return soup


_head_tris = {}                                                     # id(TriMesh) -> (the TriMesh, tri_arrays)
_head_bvh = {}                                                      # id(TriMesh) -> (the TriMesh, mathutils BVHTree)


def head_nearest(P):
    """ours (lot 4.7b item 6: Alt in an anchor drag): p -> (the closest point of the head to a model-space point, its
    face's normal) as V3, or None — a BVH of the head's triangles (mathutils), kept with its TriMesh; None without a
    head"""
    from mathutils.bvhtree import BVHTree
    from ..core import edit
    from ..core.vec3 import V3
    tm = edit.head_trimesh(P)
    if tm is None:
        return None
    c = _head_bvh.get(id(tm))
    if c is None or c[0] is not tm:
        _head_bvh.clear()
        idx = tm.index
        c = (tm, BVHTree.FromPolygons([(p.x, p.y, p.z) for p in tm.pos], [idx[i:i + 3] for i in range(0, len(idx) - 2, 3)], all_triangles=True))
        _head_bvh[id(tm)] = c
    bvh = c[1]

    def nearest(p):
        loc, nor, _i, _d = bvh.find_nearest(Vector((p.x, p.y, p.z)))
        if loc is None:
            return None
        return V3(loc.x, loc.y, loc.z), V3(nor.x, nor.y, nor.z)
    return nearest


def head_tris(P):
    """the head's triangles (edit.head_trimesh, Float32 positions, front faces) as tri_arrays, kept with the TriMesh"""
    from ..core import edit
    tm = edit.head_trimesh(P)
    if tm is None:
        return None
    c = _head_tris.get(id(tm))
    if c is None or c[0] is not tm:
        _head_tris.clear()
        idx = tm.index
        c = (tm, tri_arrays([(p.x, p.y, p.z) for p in tm.pos], [idx[i:i + 3] for i in range(0, len(idx) - 2, 3)]))
        _head_tris[id(tm)] = c
    return c[1]


def ray_in_object(root, ob, ray):
    """the root's model-space ray in the model space of an object's own data (the object shows a drag's matrix:
    its data is still where the model keeps it)"""
    from ..core.raycast import Ray
    from ..core.vec3 import V3
    from .coords import from_blender
    M = ob.matrix_world.inverted() @ root.matrix_world
    o = M @ Vector(to_blender(ray.origin)); d = (M.to_3x3() @ Vector(to_blender(ray.direction))).normalized()
    return Ray(V3(*from_blender(o.x, o.y, o.z)), V3(*from_blender(d.x, d.y, d.z)))


_conv_box = {}                                                      # data token -> (lo, hi): a converted mesh's box in the model's space


def conv_box(m):
    """the box of a converted mesh item's data (the model's geometry, as the click and the outline use it), cached
    by the data's token: the cheap test before conv_tris turns a mesh into arrays"""
    import numpy as np
    c = _conv_box.get(m.token) if m.token else None
    if c is None:
        pos = np.array([(p.x, p.y, p.z) for p in m.data.pos], dtype=np.float64).reshape(-1, 3)
        pos = pos[np.all(np.isfinite(pos), axis=1)]
        c = (pos.min(axis=0), pos.max(axis=0)) if len(pos) else None
        if m.token:
            _conv_box[m.token] = c
            while len(_conv_box) > 4 * CONV_TRIS_KEEP:
                del _conv_box[next(iter(_conv_box))]
    return c


def _ray_box(r, box):
    """does the model-space ray `r` meet the box (lo, hi), a little widened"""
    import numpy as np
    if box is None:
        return False
    lo, hi = box; pad = float((hi - lo).max()) * 1e-6 + 1e-9
    o = np.array((r.origin.x, r.origin.y, r.origin.z), dtype=np.float64); d = np.array((r.direction.x, r.direction.y, r.direction.z), dtype=np.float64)
    with np.errstate(divide='ignore', invalid='ignore'):
        inv = 1.0 / d
        t0 = (lo - pad - o) * inv; t1 = (hi + pad - o) * inv
        tn = np.nanmax(np.minimum(t0, t1)); tf = np.nanmin(np.maximum(t0, t1))
    return bool(tf >= max(tn, 0.0))


_crown_tris = {}                                                    # crown id -> (preview key, tri_arrays)


def crown_hits(root, P, o, d):
    """4.3: the shown crowns' surfaces (live.crown_previews) the ray o + t d meets: (crown id, t) of the nearest, or
    None"""
    from ..core import edit
    from . import live
    if not P.crowns():
        return None
    best = None
    prev = live.crown_previews(root, P)
    for kid in [i for i in _crown_tris if i not in prev]:
        del _crown_tris[kid]                                        # (crowns gone: their triangles too)
    for kid, (key, data, _info) in prev.items():
        k = P.items.get(kid)
        if data is None or not data.faces or k is None or not edit.isVisible(P, k):
            continue
        c = _crown_tris.get(kid)
        if c is None or c[0] != key:
            c = (key, tri_arrays([(p.x, p.y, p.z) for p in data.pos], data.faces)); _crown_tris[kid] = c
        h = ray_tris(o, d, c[1])
        if h is not None and (best is None or h[0] < best[1]):
            best = (kid, h[0])
    return best


_topo_tris = {}                                                     # item id -> (key, triangle arrays) of a Cap's / Fill's preview


def topo_hits(root, P, o, d):
    """4.4: the shown Caps' and Fill holes' previews (live.topo_previews) the ray o + t d meets: (item id, t) of the
    nearest, or None (pickables 5661 holds their objects)"""
    from ..core import edit
    from .live import shown_previews, topo_shown
    if not (P.caps() or P.of_type('skin') or P.tubes()):
        return None
    prev = shown_previews(root, P)                                  # (4.6: a Profile modifier's surface too)
    for kid in [i for i in _topo_tris if i not in prev]:
        del _topo_tris[kid]
    best = None
    for kid, (key, data, _info) in prev.items():
        k = P.items.get(kid)
        if data is None or not data['faces'] or k is None or not edit.isVisible(P, k):
            continue
        if not topo_shown(P, k):
            continue                                                # (a cap over hidden walls: not drawn, not picked)
        c = _topo_tris.get(kid)
        if c is None or c[0] != key:
            c = (key, tri_arrays([(p.x, p.y, p.z) for p in data['pos']], data['faces'])); _topo_tris[kid] = c
        h = ray_tris(o, d, c[1])
        if h is not None and (best is None or h[0] < best[1]):
            best = (kid, h[0])
    return best


def mesh_hits(context, root, P, coord, ray):
    """The converted meshes (pending or confirmed) the root's model ray `ray` meets (`coord`, the region pixel, is
    not read): (mesh item id, distance along `ray`) of the nearest one shown — the item's eye and its groups' (isVisible) and the object
    itself (hidden by Blender's H, or its collection off: not clickable) —, or None. Each mesh is hit in its own
    object's space (the object shows a drag's matrix while G / R / S run) and the distance measured back in the
    root's model space."""
    from ..core import conversion as CONV
    from ..core import edit
    from . import meshes as M
    from .coords import from_blender
    obs = None
    best = None
    rinv = root.matrix_world.inverted()
    for m in CONV.meshes(P):
        if m.data is None or not m.data.faces or not edit.isVisible(P, m):
            continue
        if obs is None:
            obs = M.mesh_objects(root, clean=False)
        ob = obs.get(m.id)
        if ob is None:
            continue
        try:
            if not ob.visible_get():
                continue
        except Exception:
            continue
        r = ray if ob.matrix_world == root.matrix_world else ray_in_object(root, ob, ray)
        if not _ray_box(r, conv_box(m)):                            # (the data's box first: only a mesh the ray meets becomes arrays)
            continue
        h = ray_tris((r.origin.x, r.origin.y, r.origin.z), (r.direction.x, r.direction.y, r.direction.z), conv_tris(m))
        if h is None:
            continue
        t = h[0]
        if r is not ray:                                            # (the hit back in the root's model space)
            q = r.origin.clone().addScaledVector(r.direction, t)
            w = rinv @ (ob.matrix_world @ Vector(to_blender(q)))
            q = from_blender(w.x, w.y, w.z)
            t = hypot(hypot(q[0] - ray.origin.x, q[1] - ray.origin.y), q[2] - ray.origin.z)
        if best is None or t < best[1]:
            best = (m.id, t)
    return best


def item_under(context, root, x, y):
    """Select's click (lot 4.2 ∥ 4.5, item 1): the wall or converted mesh under the region pixel (x, y), or None
    (item_on_ray with the cursor's ray). Never raises."""
    from . import session
    region = context.region; rv3d = context.region_data
    if region is None or rv3d is None:
        return None
    try:
        P = session.get_project(root)
        return item_on_ray(context, root, P, model_ray(root, region, rv3d, (x, y)), (x, y))
    except Exception as e:
        errorlog.record('pick: item under the cursor')
        print('HairMesh picking:', repr(e))
        return None


def nearest_hits(context, root, P, ray, coord=None, head=True):
    """what the model-space ray meets first among pickables() but the guides' discs (5657): the shown walls (wall
    soup), the shown converted meshes and the head's front faces (when shown; `head` False leaves them out) —
    {'wall': (id, t) | None, 'mesh': (id, t) | None, 'head': t | None}, t the distance along `ray`"""
    import numpy as np
    from ..core import edit
    o = (ray.origin.x, ray.origin.y, ray.origin.z); d = (ray.direction.x, ray.direction.y, ray.direction.z)
    tris, ids = wall_soup(root, P)
    out = {'wall': None, 'mesh': None, 'head': None, 'crown': None, 'topo': None}
    if len(ids):
        shown = [sf.id for sf in P.surfaces() if edit.isVisible(P, sf)]
        h = ray_tris(o, d, tris, mask=np.isin(ids, np.array(shown, dtype=np.int64))) if shown else None
        if h is not None:
            out['wall'] = (int(ids[h[1]]), h[0])
    out['mesh'] = mesh_hits(context, root, P, coord, ray)
    out['crown'] = crown_hits(root, P, o, d)
    out['topo'] = topo_hits(root, P, o, d)                          # (4.4: the Caps' and Fills' previews)
    if head and P.head is not None and P.head.visible and head_visible(root):
        ht = head_tris(P)
        h = ray_tris(o, d, ht, front=True) if ht is not None else None
        out['head'] = h[0] if h is not None else None
    return out


def warm_hits(context, root, P):
    """nearest_hits' arrays built ahead, by a ray of its own: the next ray only reads them"""
    from ..core.raycast import Ray
    nearest_hits(context, root, P, Ray())


def item_on_ray(context, root, P, ray, coord=None):
    """the nearest hit among the shown walls (isVisible; wall_soup) and the shown converted meshes (mesh_hits) wins,
    and the head in front stops both, as pickObjects over pickables() (5658, pick_wall). Vectorised over cached
    arrays: a click costs a few ms. `ray` in the model's space (a mesh whose object shows a drag's matrix is hit in
    that object's space: ray_in_object). Returns the item id, or None."""
    h = nearest_hits(context, root, P, ray, coord)
    best = h['wall']
    if h['mesh'] is not None and (best is None or h['mesh'][1] < best[1]):
        best = h['mesh']
    if h['crown'] is not None and (best is None or h['crown'][1] < best[1]):
        best = h['crown']                                           # (4.3: a crown's surface, as pickables() holds its mesh, 5661)
    if h.get('topo') is not None and (best is None or h['topo'][1] < best[1]):
        best = h['topo']                                            # (4.4: a Cap's rings, a Fill's walls to be)
    if best is not None and h['head'] is not None and h['head'] < best[1]:
        return None
    return best[0] if best is not None else None


TUBE_CURVE_PX = 3                                                   # ours (lot 4.7b, item 3): over its modifier's surface, a curve wins only this close to its line


def tube_over_curve(context, root, P, ch, x, y, project, radius):
    """lot 4.7b, item 3 (ours): a click on a Profile modifier's surface selects the modifier although its source curve
    runs inside it, within pickCurve's 7 px (the original tries pickCurve first, 5675, and a surface thin on the screen
    could only be reached by the Outliner) — the curve still wins within `radius` px (TUBE_CURVE_PX) of its line.
    `ch` is pick_curve's hit; returns the modifier's id when its surface is the item under the pointer, else None."""
    c = ch['curve']
    ids = {t.id for t in P.tubes() if t.src == c.id}
    if not ids or pick_curve([c], project, x, y, radius) is not None:
        return None
    w = item_under(context, root, x, y)
    return w if w in ids else None


def _first_t(context, root, P, ray):
    """how far along the model-space ray the nearest of pickables() but the discs is, or None"""
    h = nearest_hits(context, root, P, ray)
    ts = [t for t in (h['wall'][1] if h['wall'] else None, h['mesh'][1] if h['mesh'] else None, h['crown'][1] if h['crown'] else None,
                      h['topo'][1] if h.get('topo') else None, h['head']) if t is not None]
    return min(ts) if ts else None


def surface_hit(context, root, P, ray):
    """the point the ray meets first among pickables() but the discs (the head, the shown walls, the shown converted
    meshes, the crowns, the Caps' / Fills' / Profile modifiers' previews: tube / cap / skin are pickables, 5661), in
    the model's space, or None: surfacePoint's and pencilAdd's pickObjects (6272, 6306)"""
    t = _first_t(context, root, P, ray)
    return ray.origin.clone().addScaledVector(ray.direction, t) if t is not None else None


def brush_hit(context, root, P, ray, discs=()):
    """4.9: the point the ray meets first among pickables() but the reference objects, with the discs' surfaces of `discs`
    (the shown ones) too: brushCenter3D's pickObjects (6090), in the model's space, or None"""
    t = _first_t(context, root, P, ray)
    o = (ray.origin.x, ray.origin.y, ray.origin.z); d = (ray.direction.x, ray.direction.y, ray.direction.z)
    for g in discs:
        tr = disc_tris(P, g)
        h = ray_tris(o, d, tr) if tr is not None else None
        if h is not None and (t is None or h[0] < t):
            t = h[0]
    return ray.origin.clone().addScaledVector(ray.direction, t) if t is not None else None


def head_hit(P, ray):
    """the head's front faces under the ray: (the point, the face's unit normal) in the model's space, or None; over
    the cached arrays (the Freehand asks it at every move)"""
    import numpy as np
    from ..core.vec3 import V3
    ht = head_tris(P)
    h = ray_tris((ray.origin.x, ray.origin.y, ray.origin.z), (ray.direction.x, ray.direction.y, ray.direction.z), ht, front=True) if ht is not None else None
    if h is None:
        return None
    n = np.cross(ht[3][h[1]], ht[4][h[1]])
    return ray.origin.clone().addScaledVector(ray.direction, h[0]), V3(float(n[0]), float(n[1]), float(n[2])).normalize()


def meshes_hit(context, root, P, ray):
    """surface_hit without the head: the nearest shown wall, converted mesh, crown or preview, as a hit of
    core/freehand.py (no normal), or None"""
    h = nearest_hits(context, root, P, ray, head=False)
    ts = [e[1] for e in (h['wall'], h['mesh'], h['crown'], h.get('topo')) if e]
    return (ray.origin.clone().addScaledVector(ray.direction, min(ts)), None) if ts else None


def surface_point(context, root, P, ray, x, y, curves, radius):
    """port of surfacePoint (6271) in the viewport: what the click hits (surface_hit), else the point of the hair
    curve under it (pickCurve over `curves`, the shown hair curves, `radius` px), else the plane through the orbit
    pivot facing the camera (planeHit). In the model's space; None when the plane is behind."""
    from ..core.guidetool import surfacePointChoice
    from ..core.raycast import Plane
    from ..core.vec3 import V3
    hit = surface_hit(context, root, P, ray)
    ch = pick_curve(curves, region_projector(context.region, context.region_data, root.matrix_world), x, y, radius) if hit is None else None
    plane = None
    if hit is None and ch is None:
        cam, piv = model_cam(root, context.region_data)
        plane = ray.intersectPlane(Plane().setFromNormalAndCoplanarPoint(cam, piv), V3())
    return surfacePointChoice(hit, ch['point'] if ch else None, plane)


_disc_tris = {}                                                     # guide id -> ((the rings' sample lists), tri_arrays)


def disc_tris(P, g):
    """a disc's annulus as tri_arrays (rebuildDisc's surface, 1149: the rings resampled at 97 points, two triangles
    per step), cached while its rings keep their samples"""
    from ..core.geom import resample
    from ..core.guides import discRings
    rs = discRings(P, g)
    if len(rs) < 2 or any(len(c.samples) < 4 for c in rs):
        return None
    key = (rs[0].samples, rs[1].samples)
    c = _disc_tris.get(g.id)
    if c is not None and c[0][0] is key[0] and c[0][1] is key[1]:
        return c[1]
    NR = 96
    A = resample([q.p for q in rs[0].samples], NR + 1); B = resample([q.p for q in rs[1].samples], NR + 1)
    pos = []
    for k in range(NR + 1):
        pos.append((A[k].x, A[k].y, A[k].z)); pos.append((B[k].x, B[k].y, B[k].z))
    faces = []
    for k in range(NR):
        a = 2 * k
        faces.append([a, a + 2, a + 1]); faces.append([a + 1, a + 2, a + 3])
    t = tri_arrays(pos, faces)
    _disc_tris[g.id] = (key, t)
    return t


def disc_under(P, guides, ray):
    """the disc whose annulus the ray meets first among `guides` (the shown ones): pickObjects over the discs'
    surfaces (6029) — the guide, or None"""
    best = None
    o = (ray.origin.x, ray.origin.y, ray.origin.z); d = (ray.direction.x, ray.direction.y, ray.direction.z)
    for g in guides:
        if g.kind != 'disc':
            continue
        t = disc_tris(P, g)
        h = ray_tris(o, d, t) if t is not None else None
        if h is not None and (best is None or h[0] < best[1]):
            best = (g, h[0])
    return best[0] if best is not None else None


class Occluder:
    """The Density brush's occlusion (4.8: paintTipMask 6588-6599, 1.0.1): a point of a lock is painted only where it
    is seen — the first thing pickables() puts on the way from the camera through it is nothing, something behind it,
    or this lock itself (its walls; a cap or a converted mesh of its walls), never another lock, a modifier's or a
    crown's surface, a disc or the head in front of it. Built while the geometry stays (the brush's _warm, before any
    press) and every point answered once per view (by lock, curve and mask index: aim). The walls are culled by their
    bounding spheres first, then their triangles tested (the soup at once costs ~3 ms a ray on the example)."""

    def __init__(self, context, root, P, project):
        import numpy as np
        from ..core import edit
        self.region = context.region; self.rv3d = context.region_data; self.root = root; self.P = P
        self.project = project; self.context = context
        self.eps = 0.002 * P.S.scale
        self.memo = {}; self.own_ids = {}; self.view = None; self.screen = {}
        (lo, hi, v0, e1, e2), ids = wall_soup(root, P)
        self.box = (lo, hi); self.v0, self.e1, self.e2, self.ids = v0, e1, e2, ids
        if len(ids):
            starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])   # (the soup holds each wall's triangles together)
            ends = np.r_[starts[1:], len(ids)]
            c = np.stack([v0, v0 + e1, v0 + e2], axis=1)
            mn = np.minimum.reduceat(c.min(axis=1), starts); mx = np.maximum.reduceat(c.max(axis=1), starts)
            self.centre = (mn + mx) / 2; self.radius = np.linalg.norm(mx - mn, axis=1) / 2 + 1e-9
            wid = ids[starts]
            self.shown = np.array([edit.isVisible(P, P.items[w]) if w in P.items else False for w in wid.tolist()], dtype=bool)
            self.starts, self.ends, self.wid = starts, ends, wid
        else:
            self.starts = None
        self.head = head_tris(P) if P.head is not None and P.head.visible and head_visible(root) else None
        from ..core import groups as G
        from . import select
        sel = select.load(context.scene.hairmesh).prune(P) if getattr(context, 'scene', None) is not None else None
        _cs, act, loose = G.shownCurves(P, sel.items if sel else [], sel.anchors if sel else [])
        self.discs = [g for g in G.shownGuides(P, act, loose) if g.kind == 'disc']   # (it.surf.visible: its eye, S.showGuides, guidesShown, 11967)
        self.surfs = []                                             # (kind, item id, tri_arrays): the crowns' and the Caps' / Fills' / modifiers' previews, once
        if P.crowns():
            from . import live
            for kid, (key, data, _info) in live.crown_previews(root, P).items():
                k = P.items.get(kid)
                if data is not None and data.faces and k is not None and edit.isVisible(P, k):
                    c = _crown_tris.get(kid)
                    if c is None or c[0] != key:
                        c = (key, tri_arrays([(p.x, p.y, p.z) for p in data.pos], data.faces)); _crown_tris[kid] = c
                    self.surfs.append(('crown', kid, c[1]))
        if P.caps() or P.of_type('skin') or P.tubes():
            from .live import shown_previews, topo_shown
            for kid, (key, data, _info) in shown_previews(root, P).items():
                k = P.items.get(kid)
                if data is None or not data['faces'] or k is None or not edit.isVisible(P, k) or not topo_shown(P, k):
                    continue
                c = _topo_tris.get(kid)
                if c is None or c[0] != key:
                    c = (key, tri_arrays([(p.x, p.y, p.z) for p in data['pos']], data['faces'])); _topo_tris[kid] = c
                self.surfs.append(('topo', kid, c[1]))
        from ..core import conversion as CONV
        self.meshes = bool(CONV.meshes(P))                          # (no converted mesh: mesh_hits is skipped per ray)

    def aim(self, context, project):
        """the view of the stroke starting: what was seen from another view is asked again"""
        self.region = context.region; self.rv3d = context.region_data; self.project = project; self.context = context
        view = getattr(project, 'key', None)                        # (the view and the root's matrix: an Empty moved answers anew too)
        if view != self.view or view is None:
            self.memo = {}; self.screen = {}; self.view = view

    def first_hit(self, ray):
        """(t, kind, item id) of the nearest of pickables() on the ray, or None"""
        import numpy as np
        o = (ray.origin.x, ray.origin.y, ray.origin.z); d = (ray.direction.x, ray.direction.y, ray.direction.z)
        best = None
        if self.starts is not None:
            on = np.asarray(o); dn = np.asarray(d)
            w = self.centre - on; tp = w @ dn; d2 = np.einsum('ij,ij->i', w, w) - tp * tp
            cand = np.flatnonzero(self.shown & (d2 <= self.radius * self.radius) & (tp + self.radius >= 0))
            if len(cand):
                sub = np.concatenate([np.arange(self.starts[k], self.ends[k]) for k in cand.tolist()])
                h = ray_tris(o, d, (self.box[0], self.box[1], self.v0[sub], self.e1[sub], self.e2[sub]))
                if h is not None:
                    best = (h[0], 'wall', int(self.ids[sub[h[1]]]))
        h = mesh_hits(self.context, self.root, self.P, None, ray) if self.meshes else None
        if h is not None and (best is None or h[1] < best[0]):
            best = (h[1], 'mesh', h[0])
        for kind, kid, t in self.surfs:
            h = ray_tris(o, d, t)
            if h is not None and (best is None or h[0] < best[0]):
                best = (h[0], kind, kid)
        for g in self.discs:
            t = disc_tris(self.P, g)
            h = ray_tris(o, d, t) if t is not None else None
            if h is not None and (best is None or h[0] < best[0]):
                best = (h[0], 'disc', g.id)
        if self.head is not None:
            h = ray_tris(o, d, self.head, front=True)
            if h is not None and (best is None or h[0] < best[0]):
                best = (h[0], 'head', None)
        return best

    def own(self, L, h):
        """the first hit is this lock itself (6593-6594): one of its walls, or a cap / converted mesh of its walls"""
        ids = self.own_ids.get(L.key)
        if ids is None:
            ids = self.own_ids[L.key] = {c.id for c in L.cs}
        it = self.P.items.get(h[2]) if h[2] is not None else None
        if it is None:
            return False
        if it.type == 'surface':
            return it.a in ids or it.b in ids
        if it.type in ('cap', 'mesh'):
            return any((s := self.P.items.get(sid)) is not None and (s.a in ids or s.b in ids) for sid in (getattr(it, 'srcSurfs', None) or ()))
        return False

    def seen(self, L, c, i, p):
        key = (L.key, c.id, i)
        v = self.memo.get(key)
        if v is None:
            s = self.project(p)
            ray = model_ray(self.root, self.region, self.rv3d, (s[0], s[1]))
            h = self.first_hit(ray)
            v = h is None or h[0] >= ray.origin.distanceTo(p) - self.eps or self.own(L, h)
            self.memo[key] = v
        return v


def clear_caches():
    """the click's arrays kept across files (a new file, the add-on off): dropped"""
    _conv_tris.clear(); _head_tris.clear(); _conv_box.clear(); _disc_tris.clear(); _screen.clear(); _head_bvh.clear()


def pickable(ob, scene):
    """A live object the viewport can show and evaluate: linked in the scene (an orphan left by an undo has no
    evaluated mesh), shown, with faces. Picking no longer depends on it (wall_under works on the grids); the tests
    and the simulations use it to check that the preview object is sound."""
    try:
        return (ob.name in scene.objects and not ob.hide_viewport and not ob.hide_get() and ob.data is not None
                and len(ob.data.polygons) > 0)
    except Exception:                                               # a stale wrapper (the object went away): not pickable
        return False


# ---------------- phase 3.3: box, circle and the Shift slide ----------------

def anchors_in_box(anchors, project, x0, y0, x1, y1):
    # the marquee released (6050): the anchors whose screen point lies in the box (z < 1: not beyond the far plane)
    out = []
    for a in anchors:
        s = project(a.pos)
        if s is None or not (s[2] < 1):
            continue
        if x0 <= s[0] <= x1 and y0 <= s[1] <= y1:
            out.append(a)
    return out


def anchors_in_circle(anchors, project, x, y, r):
    """The anchors within `r` px of (x, y): the Circle Select of this port."""
    out = []
    for a in anchors:
        s = project(a.pos)
        if s is None or s[2] > 1:
            continue
        if hypot(s[0] - x, s[1] - y) <= r:
            out.append(a)
    return out


def curves_in_circle(curves, project, x, y, r):
    """The curves whose polyline comes within `r` px of (x, y)."""
    out = []
    for c in curves:
        n = nearest_on_screen(c, project, x, y)
        if n is not None and n['d'] <= r:
            out.append(c)
    return out


def snap_drag_to_curve(P, a, project, x, y, snap=None, occluded=None):
    """port of snapDragToCurve (5720) over a projector. Shift while dragging one anchor: it sits on the curve nearest
    to the CURSOR (any group; hidden only by its eye; its own curves don't count) and slides along it. Picked again on
    every move: the cursor brought to another curve takes the anchor over to it (when that one is nearer by more than
    6 px, no flicker between two close curves); among curves almost equally near on screen (within 4 px) the one
    nearer to the camera wins (not a curve behind the head). `snap` is the id of the curve slid along so far
    (drag.snap); `occluded(point)` says whether the head hides a point (it then counts as 25 px further). Returns
    (curve, point) or None when no other curve is on screen."""
    from ..core import edit
    ks = [k for k in edit.hairCurves(P) if not (a in k.anchors or not edit.isVisible(P, k) or len(k.samples) < 2)]
    # (ours, lot 4.7b item 5: the same result) a curve whose box on screen lies more than SNAP_PRUNE px beyond the
    # nearest curve found so far can't matter — it is never checked for occlusion (d > d0 + 30, below) and never
    # within 4 px of the best (occlusion adds at most 25 px to the others) — so the curves are measured from the
    # nearest box out and the far ones skipped (the one slid along always counts); `cands` keeps hairCurves' order
    lb = []
    for k in ks:
        b = screen_box(k, project)
        lb.append(math.inf if b is None else hypot(max(b[0] - x, 0.0, x - b[2]), max(b[1] - y, 0.0, y - b[3])))
    got = {}; dmin = math.inf
    for i in sorted(range(len(ks)), key=lb.__getitem__):
        if lb[i] - 1e-6 > dmin + SNAP_PRUNE and ks[i].id != snap:
            continue
        r = nearest_on_screen(ks[i], project, x, y)
        if r is not None:
            got[i] = r
            if r['d'] < dmin:
                dmin = r['d']
    cands = []; cur = None
    for i, k in enumerate(ks):
        r = got.get(i)
        if r is None:
            continue
        r['c'] = k
        s = project(r['point']); r['z'] = s[2] if s is not None else float('inf')
        cands.append(r)
        if k.id == snap:
            cur = r
    if not cands:
        return None
    # a point hidden behind the head counts as 25 px further (the curve on the far side lines up on screen too)
    if occluded is not None:
        d0 = min(r['d'] for r in cands)
        for r in cands:
            if r['d'] > d0 + 30:
                continue
            if occluded(r['point']):
                r['d'] += 25
    dmin = min(r['d'] for r in cands)
    best = None
    for q in cands:
        if q['d'] <= dmin + 4 and (best is None or q['z'] < best['z']):
            best = q
    if cur is not None and cur['d'] <= best['d'] + 6:
        best = cur
    return best['c'], best['point']
