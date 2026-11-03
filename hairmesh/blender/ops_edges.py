# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Lot 4.9 inside Blender: the Edges sub-tool (the original's tool 'edge', V; hair-modeler.clean.html:3605-3917 without Mesh
-> curves, 5799-5801, 5882, 6043-6046, 8055). On a converted mesh:

  LMB            an edge (Shift adds, Ctrl + Shift: the shortest path from the last picked one); twice on it: its edge loop
  Ctrl + LMB     takes an edge off (twice: the loop); Ctrl + drag: every selected edge in the box
  Esc            deselects (none selected: back to Select, as ever)
  Shift + RMB    the pie: Path -> Curve, Deselect

The data part is core/edges.py (bit for bit with the original: tests/test_edges_parity.py). The picking works on the mesh
item's data, never on the Blender mesh (whose indices clean_mesh may change): the cursor's ray against the triangles
of pick.conv_tris, the fan of buildMeshObj, whose triangle index edgeTopo knows.

The selection (S.edges) is a JSON in Scene.hairmesh.edge_sel, so Ctrl+Z restores it; it names the mesh's data by its token
and is empty for any other data (current: a reconversion clears it). What is not in the undo: the last click (S.edgeClick,
a double click is the same edge twice within 400 ms as in the original, with the time of the end of the first click, so
a slow click does not eat into it) and the edge under the cursor."""
import json
import time
from math import hypot

import bpy

from ..core import conversion as CONV, edges as ED, edit, groups as G
from ..core.selection import Selection
from . import draw as drawmod, live, owner, pick, select, session, slow
from .ops_draw import NAV_EVENTS
from .ops_select import View

KEYS = ((('Release',), 'Deselect the Edges in the Box'), (('Esc',), 'Cancel'))
CLICK_PX = 4                                                        # a Ctrl + drag this short is a Ctrl + click (6044)
PICK_PX = 8                                                         # near the outline (3660)
NO_EDGES = 'No edges selected: click an edge of a converted mesh (Edges tool)'
NO_MESH = 'No converted mesh yet: convert the hair (D > Conversion), then pick its edges here.'
HELP = '\n'.join(('Click: pick an edge of a converted mesh. Shift + click adds.',
                  'Double click: the edge loop, up to a fork.',
                  'Ctrl + click: take an edge off (double click: the loop). Ctrl + drag: take off a box.',
                  'Ctrl + Shift + click: the shortest path from the last edge.',
                  'Shift + RMB: Path → Curve, Deselect.',
                  'Esc: deselect, then back to Select.'))
HIDDEN = 'The converted meshes are hidden: show them (Baked in D > Conversion) to pick their edges.'

memo = None                                                         # S.edgeClick (ClickMemo): the last click, never in the undo
hover = None                                                        # the edge under the cursor: (mesh id, its token, edge), or None


# ---------------- the selection in the scene ----------------

def current(hm, P):
    """S.edges for model P: empty unless the mesh it names still has that very data (edgeSelMesh 3792, and a mesh
    rebuilt by a reconversion gets another token)"""
    try:
        d = json.loads(hm.edge_sel or '{}')
    except ValueError:
        d = {}
    it = P.items.get(d.get('id'))
    if it is None or it.type != 'mesh' or it.data is None or it.token != d.get('token'):
        return ED.EdgeSel()
    return ED.EdgeSel(it.id, dict.fromkeys(d.get('set') or ()), d.get('last'))


def store(hm, P, E):
    it = P.items.get(E.id)
    hm.edge_sel = json.dumps({'id': E.id, 'token': it.token if it is not None else None, 'set': list(E.edges), 'last': E.last})


def has_edges(context):
    if context.scene.hairmesh.subtool != 'EDGES':
        return False
    root, P, _sel = select.current(context)
    return P is not None and bool(current(context.scene.hairmesh, P).edges)


# ---------------- the picking ----------------

def shown(root, P):
    """edgeMeshes (3615) for the converted meshes: with data, visible, and their object shown in the viewport"""
    from . import meshes
    obs = meshes.mesh_objects(root, clean=False)
    out = []
    for m in CONV.meshes(P):
        ob = obs.get(m.id)
        if m.data is not None and edit.isVisible(P, m) and ob is not None and ob.visible_get():
            out.append(m)
    return out


_arrays = {}                                                        # data token -> the numpy arrays of the outline scan
ARRAYS_KEEP = 64


def _scan_arrays(token, T, Wp):
    import numpy as np
    c = _arrays.get(token)
    if c is None:
        c = (np.array(Wp, dtype=np.float64).reshape(-1, 3), np.array(T.ea, dtype=np.int64), np.array(T.eb, dtype=np.int64))
        _arrays[token] = c
        while len(_arrays) > ARRAYS_KEEP:
            del _arrays[next(iter(_arrays))]
    return c


def scanner(project_matrix, w, h, tokens):
    """core's near over all the edges of a mesh at once (the click near the outline, 3679; picking only, nothing of the
    model comes from it): `project_matrix` model -> clip with the axes of to_blender. The same edge: the first of the
    nearest, closer than maxD (tests/test_blender_c49v.py)."""
    import numpy as np
    M = np.array(project_matrix, dtype=np.float64)

    def scan(data, T, Wp, mx, my, maxD):
        W, ea, eb = _scan_arrays(tokens[id(data)], T, Wp)
        if not len(ea):
            return None
        V = np.stack((W[:, 0], -W[:, 2], W[:, 1], np.ones(len(W))), axis=1) @ M.T
        cw = V[:, 3]
        with np.errstate(divide='ignore', invalid='ignore'):
            sx = (V[:, 0] / cw + 1.0) * 0.5 * w; sy = (V[:, 1] / cw + 1.0) * 0.5 * h; sz = V[:, 2] / cw
            good = (cw > 0.0) & (sz <= 1.0)
            ok = good[ea] & good[eb]
            ax = sx[ea]; ay = sy[ea]; vx = sx[eb] - ax; vy = sy[eb] - ay; l2 = vx * vx + vy * vy
            t = np.where(l2 > 0, np.clip(((mx - ax) * vx + (my - ay) * vy) / l2, 0.0, 1.0), 0.0)
            d = np.hypot(ax + vx * t - mx, ay + vy * t - my)
        d[~ok | np.isnan(d)] = np.inf
        i = int(np.argmin(d))
        return i if d[i] < maxD else None
    return scan


def pick_at(context, v, x, y, hovering):
    """pickEdge (3662) in the 3D View `v`: (mesh id, T, edge) or None"""
    P = v.P
    ms = shown(v.root, P)
    if not ms:
        return None
    ray = pick.model_ray(v.root, context.region, context.region_data, (x, y))
    o = (ray.origin.x, ray.origin.y, ray.origin.z); d = (ray.direction.x, ray.direction.y, ray.direction.z)
    best = None
    for m in ms:
        tris = pick.conv_tris(m)
        h = pick.ray_tris(o, d, tris)
        if h is not None and (best is None or h[0] < best[0]):
            best = (h[0], m, h[1], len(tris[2]))
    hit = None
    if best is not None and best[3] * 3 == len(ED.edgeTopo(best[1].data).idx):   # (a triangle left out of tri_arrays — a non-finite corner — and the indices no longer agree: no polygon)
        hit = (best[1].id, best[2])
    scan = None
    if not hovering:
        scan = scanner(v.rv3d.perspective_matrix @ v.root.matrix_world, context.region.width, context.region.height, {id(m.data): m.token for m in ms})
    return ED.pickEdge([(m.id, m.data) for m in ms], hit, v.project, x, y, hovering, scan, v.px(PICK_PX))


# ---------------- the click ----------------

def press(op, context, v, x, y, ctrl, shift, double=False):
    """edgeClick (3817): the selection stored, the mesh selected (3832); FINISHED, one undo step, when something changed.
    double: Blender paired the two clicks by the events' own clock, so the undo step and the redraw of the first one
    cannot make them two clicks by ours (S.edgeClick's 400 ms stay for what Blender did not pair)"""
    global memo
    hm = context.scene.hairmesh; P = v.P
    E = current(hm, P)
    before = hm.edge_sel
    with slow.part('edge picked'):
        r = pick_at(context, v, x, y, False)
    was = (E.id, list(E.edges), E.last)
    got = ED.edgeClick(E, memo, r, ctrl, shift, memo.t if double and memo is not None else time.perf_counter() * 1000.0, ED.edgeWorld(P.items[r[0]].data) if r is not None else None)
    memo = got['memo']
    if got['toast'] or got['hud']:
        op.report({'INFO'}, got['toast'] or got['hud'])
    changed = (E.id, list(E.edges), E.last) != was
    if changed:
        store(hm, P, E)
    sel = select.load(hm).prune(P)
    if got['select'] is not None and (len(sel.items) != 1 or sel.items[0] != got['select'] or sel.anchors):
        select.store(hm, Selection([got['select']], []))
        G.panelsRendered(P, got['select'])
        changed = True
    if memo is not None:
        memo.t = time.perf_counter() * 1000.0                       # (from the end of this click: its own work is not the user's delay)
    select.redraw(context)
    return {'FINISHED'} if changed or hm.edge_sel != before else {'CANCELLED'}


class HAIRMESH_OT_edge_click(bpy.types.Operator):
    """Pick an edge of a converted mesh (twice: its edge loop). Shift adds, Ctrl takes off, Ctrl + drag takes off a box, Ctrl + Shift adds the shortest path from the last picked edge"""
    bl_idname = 'hairmesh.edge_click'
    bl_label = 'HairMesh Pick Edge'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        if context.scene.hairmesh.subtool != 'EDGES' or event.alt or event.oskey:
            return {'PASS_THROUGH'}
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        x, y = event.mouse_region_x, event.mouse_region_y
        double = event.value == 'DOUBLE_CLICK'
        if event.ctrl and not event.shift:                          # (5800: a click deselects an edge, a drag every edge in the box)
            self.view = v; self.x0 = x; self.y0 = y; self.double = double
            drawmod.overlay = ('box', x, y, x, y)
            context.window_manager.modal_handler_add(self)
            live.header(context.area, 'HairMesh Edges', KEYS)
            return {'RUNNING_MODAL'}
        got = press(self, context, v, x, y, event.ctrl, event.shift, double)
        return {'INTERFACE'} if double and 'CANCELLED' in got else got   # (ours even when it changes nothing, and no undo step: keymaps.DOUBLE_PRESS)

    def _end(self, context):
        drawmod.overlay = None
        live.header(context.area, None)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)                                          # (the window closed, a file loaded: the box goes)

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            drawmod.overlay = ('box', self.x0, self.y0, x, y)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end(context)
            v = View.of(context) or self.view                       # (the wheel passes through: the view of the release)
            if hypot(x - self.x0, y - self.y0) <= v.px(CLICK_PX):
                return press(self, context, v, x, y, True, False, self.double)   # (6044)
            return self._box(context, v, min(x, self.x0), min(y, self.y0), max(x, self.x0), max(y, self.y0))
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _box(self, context, v, x0, y0, x1, y1):
        hm = context.scene.hairmesh; P = v.P
        E = current(hm, P)
        if not E.edges:
            return {'CANCELLED'}
        data = P.items[E.id].data
        n = ED.edgeBoxDeselect(E, ED.edgeTopo(data), ED.edgeWorld(data), v.project, x0, y0, x1, y1)
        if not n:
            return {'CANCELLED'}
        store(hm, P, E)
        select.redraw(context)
        self.report({'INFO'}, f'{n} edge{"" if n == 1 else "s"} deselected')
        return {'FINISHED'}


class HAIRMESH_OT_edge_hover(bpy.types.Operator):
    """Follow the edge under the cursor (Edges tool): only the polygon the ray hits, no scan of the outline"""
    bl_idname = 'hairmesh.edge_hover'
    bl_label = 'HairMesh Edge Under the Cursor'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.scene.hairmesh.subtool == 'EDGES' and owner.visible(context)

    def invoke(self, context, event):
        global hover
        v = View.of(context)
        new = None
        if v is not None:
            r = pick_at(context, v, event.mouse_region_x, event.mouse_region_y, True)
            if r is not None:
                new = (r[0], v.P.items[r[0]].token, r[2])
        if new != hover:
            hover = new
            if context.area is not None:
                context.area.tag_redraw()
        return {'PASS_THROUGH'}


class HAIRMESH_OT_edges_deselect(bpy.types.Operator):
    """Deselect the edges (Esc; with none selected the key goes on: back to Select)"""
    bl_idname = 'hairmesh.edges_deselect'
    bl_label = 'HairMesh Deselect Edges'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not (owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None and has_edges(context)):
            cls.poll_message_set(NO_EDGES)
            return False
        return True

    def execute(self, context):
        hm = context.scene.hairmesh
        _r, P, _s = select.current(context)
        E = current(hm, P)
        ED.clearEdges(E)
        store(hm, P, E)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_edges_to_curve(bpy.types.Operator):
    """Turn every chain of the selected edges into a curve (chains meeting at a vertex share the anchor there). New curves go into the selected group, else into "<mesh> paths\""""
    bl_idname = 'hairmesh.edges_to_curve'
    bl_label = 'HairMesh Path to Curve'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not (owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None and has_edges(context)):
            cls.poll_message_set(NO_EDGES)
            return False
        return True

    def execute(self, context):
        from . import ops_groups
        hm = context.scene.hairmesh
        root, P, sel = select.current(context)
        E = current(hm, P)
        with slow.part('path to curves'):
            made, g, msg = ED.edgesToCurves(P, sel.items, E)
        if g is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        if made:
            sel = Selection([c.id for c in made], [])
            select.store(hm, sel)
            G.panelsRendered(P, sel.items[len(sel.items) - 1])
        session.save_project(root, P)
        ops_groups.sync_collections(context, root, P)
        live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------- the page and the pie ----------------

def edges_rows(context, room=None):
    """the Edges page (edgePanel 3888, without the Mesh -> curves part): how many edges on which mesh, Path -> Curve,
    Deselect (dimmed, the tooltip says why); with nothing to pick, why (G2)"""
    hm = context.scene.hairmesh
    root, P, _sel = select.current(context)
    if P is None:
        return []
    if not any(m.data is not None for m in CONV.meshes(P)):
        return [[('text', NO_MESH, 'INFO')]]
    E = current(hm, P)
    n = len(E.edges)
    rows = [[('label', f'{n} edge{"" if n == 1 else "s"} on {P.items[E.id].name}' if n else 'No edges selected', 'EDGESEL')]]
    if not shown(root, P):
        rows.append([('text', HIDDEN, 'INFO')])
    rows.append([('op', 'hairmesh.edges_to_curve', 'Path → Curve', 'CURVE_DATA', {}), ('op', 'hairmesh.edges_deselect', 'Deselect', 'X', {})])
    return rows


def edges_pie_items(context, menu):
    """the Shift + RMB pie with edges selected (openEdgePie 3875), or None: the usual pie"""
    if not has_edges(context):
        return None
    e = ('empty',)
    return [('op', 'hairmesh.edges_deselect', 'Deselect', 'X', {}), e, e, ('op', 'hairmesh.edges_to_curve', 'Path → Curve', 'CURVE_DATA', {}), e, menu, e, e]


# ---------------- what draw.py draws ----------------

SEL_COLOR = (1.0, 0x9a / 255.0, 0x2e / 255.0, 1.0)                  # edgeSelObj: 0xff9a2e, 3.5 px
HOV_COLOR = (1.0, 0xf0 / 255.0, 0x7a / 255.0, 1.0)                  # edgeHovObj: 0xfff07a, 2.5 px
SEL_PX = 3.5
HOV_PX = 2.5
_sel_batch = (None, None)                                           # (the JSON of the selection, its token, the batch): rebuilt when one changes
_hov_batch = (None, None)


def segments(data, T, edges):
    """(x, y, z) pairs in the root's local space (to_blender of the positions shown), for the 'LINES' batch"""
    Wp = ED.edgeWorld(data)
    out = []
    for e in edges:
        a = T.ea[e] * 3; b = T.eb[e] * 3
        out.append((Wp[a], -Wp[a + 2], Wp[a + 1])); out.append((Wp[b], -Wp[b + 2], Wp[b + 1]))
    return out


def _batch(pts):
    import gpu
    from gpu_extras.batch import batch_for_shader
    return batch_for_shader(gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR'), 'LINES', {'pos': pts})


def overlay(hm, P, root):
    """updateEdgeOverlay (3801), edgeHover (3811): [(batch, color, width)] in the root's local space, the batches kept
    while nothing changes, so a redraw costs no Python per edge; the selected edges thicker than the one under the cursor
    (G11: never by the colour alone)"""
    global _sel_batch, _hov_batch
    out = []
    key = hm.edge_sel
    if _sel_batch[0] != key:
        E = current(hm, P)
        it = P.items.get(E.id)
        pts = segments(it.data, ED.edgeTopo(it.data), E.edges) if E.edges else ()
        _sel_batch = (key, _batch(pts) if pts else None)
    if _sel_batch[1] is not None:
        E = current(hm, P)
        if E.edges and any(m.id == E.id for m in shown(root, P)):   # (updateEdgeOverlay 3802: a hidden mesh shows no lines)
            out.append((_sel_batch[1], SEL_COLOR, SEL_PX))
    if hover is not None:
        it = P.items.get(hover[0])
        if it is not None and it.type == 'mesh' and it.data is not None and it.token == hover[1]:
            if _hov_batch[0] != hover:
                _hov_batch = (hover, _batch(segments(it.data, ED.edgeTopo(it.data), (hover[2],))))
            out.append((_hov_batch[1], HOV_COLOR, HOV_PX))
    return out


CLASSES = (HAIRMESH_OT_edge_click, HAIRMESH_OT_edge_hover, HAIRMESH_OT_edges_deselect, HAIRMESH_OT_edges_to_curve)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    global memo, hover, _sel_batch, _hov_batch
    memo = None; hover = None; _sel_batch = (None, None); _hov_batch = (None, None)
    _arrays.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
