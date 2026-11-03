# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Edge selection on converted meshes (the original's tool 'edge', V; lot 4.9): the polygon edges of a mesh item, edge
loops, shortest paths, chains and Path -> curve. Pure Python, bit for bit with the original (tests/test_edges_parity.py).
Line numbers refer to reference/hair-modeler.clean.html, 1.0.1. Imported meshes (imesh) and Mesh -> curves are phase 5.

Works from the mesh's own data, as the original (the index it draws with, buildMeshObj 2923). The triangles are ported
too and not only the polygons: they decide what counts as one polygon (edgeTopo 3626: a fan diagonal that is a real edge
of another face splits a polygon, two fans sharing a diagonal join two), and a polygon is what loopNext reads.

The vertex positions come from a Float32Array (geo.position, buildMeshObj 2917; edgeWorld writes a Float32Array again,
3652): the distances of edgePath and the anchors of Path -> curve are on values rounded to float32 (edgeWorld below)."""
from array import array
from dataclasses import dataclass, field
from math import sqrt

from .fit import fitPolyline
from .js import js_hypot
from .vec3 import V3

CLICK_MS = 400                                                      # edgeClick: two clicks on one edge within this are a double click (3820)
NO_PATH = 'No path between these edges (different parts of the mesh)'
NO_EDGES = 'Select edges first (Edges tool, V)'
NOTHING = 'Nothing to convert'


class Topo:
    """edgeTopo's T: n, ne, ea, eb (edge ends), E (key -> edge), eFaces, vE (vertex -> edges), fEdges, triFace, nf, idx"""
    __slots__ = ('n', 'ne', 'ea', 'eb', 'E', 'eFaces', 'vE', 'fEdges', 'triFace', 'nf', 'idx')


def triangles(data):
    # the fan of buildMeshObj (hair-modeler.clean.html:2923)
    idx = []
    for f in data.faces:
        f0 = f[0]
        for k in range(1, len(f) - 1):
            idx += (f0, f[k], f[k + 1])
    return idx


def edgeTopo(data):
    """port of edgeTopo (hair-modeler.clean.html:3617) for a mesh item's data; kept on the data (EDGE_TOPO, a WeakMap on
    the same key: the faces never change while the data lives)"""
    T = data.topo
    if T is not None:
        return T
    n = len(data.pos); idx = triangles(data); nt = len(idx) // 3

    def EK(a, b):
        return a * n + b if a < b else b * n + a
    E = {}; ea = []; eb = []
    for f in data.faces:
        L = len(f)
        for k in range(L):
            a = f[k]; b = f[(k + 1) % L]
            if a == b:
                continue
            q = EK(a, b)
            if q not in E:
                E[q] = len(ea); ea.append(a); eb.append(b)
    # triangles -> polygons: triangles sharing an edge that isn't a polygon edge are one polygon
    up = list(range(nt))

    def find(i):
        while up[i] != i:
            up[i] = up[up[i]]; i = up[i]
        return i
    inner = {}
    for t in range(nt):
        for k in range(3):
            a = idx[t * 3 + k]; b = idx[t * 3 + (k + 1) % 3]
            if a == b:
                continue
            q = EK(a, b)
            if q in E:
                continue
            if q in inner:
                x = find(t); y = find(inner[q])
                if x != y:
                    up[x] = y
            else:
                inner[q] = t
    fid = {}; triFace = [0] * nt
    for t in range(nt):
        r = find(t)
        if r not in fid:
            fid[r] = len(fid)
        triFace[t] = fid[r]
    ne = len(ea); eFaces = [[] for _ in range(ne)]; vE = [[] for _ in range(n)]; fEdges = [[] for _ in range(len(fid))]
    for t in range(nt):
        for k in range(3):
            e = E.get(EK(idx[t * 3 + k], idx[t * 3 + (k + 1) % 3]))
            if e is None:
                continue
            f = triFace[t]
            if f not in eFaces[e]:
                eFaces[e].append(f); fEdges[f].append(e)
    for e in range(ne):
        vE[ea[e]].append(e); vE[eb[e]].append(e)
    T = Topo()
    T.n = n; T.ne = ne; T.ea = ea; T.eb = eb; T.E = E; T.eFaces = eFaces; T.vE = vE; T.fEdges = fEdges; T.triFace = triFace; T.nf = len(fid); T.idx = idx
    data.topo = T
    return T


def edgeWorld(data):
    """port of edgeWorld (3647) for a converted mesh: its matrix is the identity (a drag bakes into the data, 4646), so
    each coordinate is the float32 of the data, x * 1 + y * 0 + z * 0 + 0 (which only turns -0 into 0). A flat list
    [x0, y0, z0, x1, ...] of the float32 values as doubles, kept on the data."""
    W = data.world
    if W is None:
        flat = []
        for p in data.pos:
            flat += (p.x, p.y, p.z)
        W = data.world = [x + 0.0 for x in array('f', flat).tolist()]
    return W


def wv(Wp, i):
    # port of wv (3656)
    return V3(Wp[i * 3], Wp[i * 3 + 1], Wp[i * 3 + 2])


def otherEnd(T, e, v):
    # port of otherEnd (3657)
    return T.eb[e] if T.ea[e] == v else T.ea[e]


def segDist2(mx, my, ax, ay, bx, by):
    # port of segDist2 (3658); clamp(t, 0, 1) as the original's (Math.min(Math.max))
    vx = bx - ax; vy = by - ay; l2 = vx * vx + vy * vy
    t = 0
    if l2 > 0:
        t = ((mx - ax) * vx + (my - ay) * vy) / l2
        t = 0 if t < 0 else 1 if t > 1 else t
    return js_hypot(ax + vx * t - mx, ay + vy * t - my)


def near(T, Wp, cand, project, mx, my, maxD):
    """pickEdge's `near` (3664): the edge of `cand` nearest to (mx, my) on screen, closer than maxD, or None. `project`:
    a screen point (x, y, z) of a V3, or None behind the camera."""
    best = None; bd = maxD
    for e in cand:
        a = project(wv(Wp, T.ea[e])); b = project(wv(Wp, T.eb[e]))
        if a is None or b is None or a[2] > 1 or b[2] > 1:
            continue
        d = segDist2(mx, my, a[0], a[1], b[0], b[1])
        if d < bd:
            bd = d; best = e
    return best


def pickEdge(meshes, hit, project, mx, my, hover, scan=None, radius=8):
    """port of pickEdge (3662) without the ray: `meshes` = [(id, data)] (edgeMeshes), `hit` = (id, triangle) the ray's
    first hit or None. The nearest edge of the polygon hit; near the outline (no polygon under the cursor) any edge within
    `radius` (8 px), not while hovering: too slow on big meshes. (id, T, edge), or None. `scan(data, T, Wp, mx, my, maxD)`: the
    edge of all of them, a faster way to the same one (default: near)."""
    if hit is not None:
        iid, t = hit
        data = next((d for i, d in meshes if i == iid), None)
        if data is not None:
            T = edgeTopo(data)
            if 0 <= t < len(T.triFace):
                e = near(T, edgeWorld(data), T.fEdges[T.triFace[t]], project, mx, my, 1e9)
                if e is not None:
                    return iid, T, e
    if hover:
        return None
    best = None
    for iid, data in meshes:
        T = edgeTopo(data); Wp = edgeWorld(data)
        e = scan(data, T, Wp, mx, my, radius) if scan is not None else near(T, Wp, range(T.ne), project, mx, my, radius)
        if e is not None:
            best = (iid, T, e)
    return best


def loopNext(T, e, v):
    # port of loopNext (3684): the next edge of an edge loop at vertex v: across a vertex with 4 edges (inside) or along
    # the border through a vertex with 3 edges; anything else (a pole, a fork, a corner) ends the loop
    es = T.vE[v]; ef = T.eFaces[e]
    inside = len(es) == 4 and all(len(T.eFaces[x]) == 2 for x in es)
    border = len(es) == 3 and len(ef) == 1 and sum(1 for x in es if len(T.eFaces[x]) == 1) == 2
    if not inside and not border:
        return -1
    c = [x for x in es if x != e and not any(f in ef for f in T.eFaces[x])]
    return c[0] if len(c) == 1 else -1


def edgeLoop(T, e0):
    """port of edgeLoop (3693): the whole edge loop through e0: {edges (in order), verts (in order), closed}"""
    out = [e0]; seen = {e0}; closed = False
    head = []                                                       # (the edges unshifted: out = reversed(head) + the rest)
    for dir_ in (0, 1):
        e = e0; v = T.eb[e0] if dir_ else T.ea[e0]
        while True:
            nx = loopNext(T, e, v)
            if nx < 0:
                break
            if nx in seen:
                closed = True
                break
            seen.add(nx)
            if dir_:
                out.append(nx)
            else:
                head.append(nx)
            v = otherEnd(T, nx, v); e = nx
        if closed:
            break
    out = head[::-1] + out
    if len(out) == 1:
        v = T.ea[out[0]]
    else:
        v = T.eb[out[0]] if T.ea[out[0]] == T.ea[out[1]] or T.ea[out[0]] == T.eb[out[1]] else T.ea[out[0]]
    verts = [v]
    for e in out:
        v = otherEnd(T, e, v); verts.append(v)
    if closed:
        verts.pop()
    return {'edges': out, 'verts': verts, 'closed': closed}


def edgePath(T, Wp, from_, to):
    """port of edgePath (3711), its heap as it is (ties matter): the shortest path along the edges from edge `from_` to
    edge `to`: its edges, `to` first and included; None across separate parts"""
    inf = float('inf')
    dist = [inf] * T.n; prevE = [-1] * T.n; heap = []

    def push(d, v):
        heap.append((d, v)); i = len(heap) - 1
        while i > 0:
            p = (i - 1) >> 1
            if heap[p][0] <= heap[i][0]:
                break
            heap[p], heap[i] = heap[i], heap[p]; i = p

    def pop():
        top = heap[0]; last = heap.pop()
        if heap:
            heap[0] = last; i = 0; L = len(heap)
            while True:
                l = 2 * i + 1; r = l + 1; k = i
                if l < L and heap[l][0] < heap[k][0]:
                    k = l
                if r < L and heap[r][0] < heap[k][0]:
                    k = r
                if k == i:
                    break
                heap[k], heap[i] = heap[i], heap[k]; i = k
        return top
    for v in (T.ea[from_], T.eb[from_]):
        dist[v] = 0; push(0, v)
    goal = {T.ea[to], T.eb[to]}; end = -1
    while heap:
        d, v = pop()
        if d > dist[v]:
            continue
        if v in goal:
            end = v
            break
        ax = Wp[v * 3]; ay = Wp[v * 3 + 1]; az = Wp[v * 3 + 2]
        for e in T.vE[v]:
            u = otherEnd(T, e, v)
            dx = ax - Wp[u * 3]; dy = ay - Wp[u * 3 + 1]; dz = az - Wp[u * 3 + 2]   # (distanceTo: sqrt(dx*dx + dy*dy + dz*dz))
            nd = d + sqrt(dx * dx + dy * dy + dz * dz)
            if nd < dist[u]:
                dist[u] = nd; prevE[u] = e; push(nd, u)
    if end < 0:
        return None
    out = [to]; v = end
    while prevE[v] >= 0:
        e = prevE[v]; out.append(e); v = otherEnd(T, e, v)
    return out


def edgeChains(T, set_):
    """port of edgeChains (3731): selected edges (any iterable, in order) -> chains of vertices, split where 3+ selected
    edges meet; closed loops stay closed. [{verts, closed}]"""
    edges = dict.fromkeys(set_)
    deg = {}
    for e in edges:
        for v in (T.ea[e], T.eb[e]):
            deg[v] = deg.get(v, 0) + 1
    used = set(); out = []

    def walk(v0, e0):
        vs = [v0]; v = v0; e = e0
        while True:
            used.add(e); v = otherEnd(T, e, v); vs.append(v)
            if deg.get(v) != 2:
                break
            nx = next((x for x in T.vE[v] if x in edges and x not in used), None)
            if nx is None:
                break
            e = nx
        return vs
    for v, d in deg.items():
        if d != 2:
            for e in T.vE[v]:
                if e in edges and e not in used:
                    out.append({'verts': walk(v, e), 'closed': False})
    for e in edges:
        if e not in used:
            vs = walk(T.ea[e], e); vs.pop()
            out.append({'verts': vs, 'closed': True})
    return out


# ---------------- the selection (S.edges, 3611) ----------------

@dataclass(eq=False)
class EdgeSel:
    """S.edges: `id` the mesh item, `edges` the selected edges in order (the insertion order of the original's Set: it
    decides the chains, the anchors and the curves), `last` the last picked edge"""
    id: int | None = None
    edges: dict = field(default_factory=dict)
    last: int | None = None


def setEdgeSel(E, iid, lst, mode):
    # port of setEdgeSel (3793); mode: set | add | remove
    if E.id != iid:
        E.id = iid; E.edges = {}; E.last = None
    if mode == 'set':
        E.edges = dict.fromkeys(lst)
    else:
        for e in lst:
            if mode == 'add':
                E.edges[e] = None
            else:
                E.edges.pop(e, None)


def clearEdges(E):
    # port of clearEdges (3799)
    E.edges = {}; E.last = None


@dataclass(eq=False)
class ClickMemo:
    """S.edgeClick (3820): the last click, to tell a double click (transient: never in the undo)"""
    id: int | None = None
    e: int | None = None
    t: float = 0.0


def edgeClick(E, memo, r, ctrl, shift, now, Wp=None):
    """port of edgeClick (3817) without the pick: `r` = (mesh id, T, edge) or None, `memo` a ClickMemo, or None for no
    click before, `now` in ms, `Wp` edgeWorld (read for a path). Returns {memo, dbl, hud, toast, select}: `select` — the
    mesh selected (S.sel = it, 3832), None when the click hit nothing."""
    out = {'memo': memo, 'dbl': False, 'hud': None, 'toast': None, 'select': None}
    if r is None:
        if not ctrl and not shift and E.edges:
            clearEdges(E)
        return out
    iid, T, e = r
    dbl = memo is not None and memo.id == iid and memo.e == e and now - memo.t < CLICK_MS
    out['memo'] = None if dbl else ClickMemo(iid, e, now); out['dbl'] = dbl
    same = E.id == iid
    if ctrl and shift:                                              # the shortest path from the last picked edge
        if same and E.last is not None and E.last != e and E.edges:
            p = edgePath(T, Wp, E.last, e)
            if p is not None:
                setEdgeSel(E, iid, p, 'add')
            else:
                out['toast'] = NO_PATH
                setEdgeSel(E, iid, [e], 'add')
        else:
            setEdgeSel(E, iid, [e], 'add' if same else 'set')
        E.last = e
    elif ctrl:
        if same:
            setEdgeSel(E, iid, edgeLoop(T, e)['edges'] if dbl else [e], 'remove')
    elif dbl:
        L = edgeLoop(T, e)
        setEdgeSel(E, iid, L['edges'], 'add'); E.last = e
        n = len(L['edges'])
        out['hud'] = f'Edge loop: {n} edge{"" if n == 1 else "s"}{" (closed)" if L["closed"] else ""}'
    else:
        setEdgeSel(E, iid, [e], 'add' if shift and same else 'set'); E.last = e
    out['select'] = iid
    return out


def edgeBoxDeselect(E, T, Wp, project, x0, y0, x1, y1):
    """port of edgeBoxDeselect (3835): every selected edge whose middle is inside the box (in the active view) is
    deselected. Returns how many, 0: nothing changed."""
    out = []
    for e in E.edges:
        s = project(wv(Wp, T.ea[e]).add(wv(Wp, T.eb[e])).multiplyScalar(0.5))
        if s is not None and s[2] < 1 and x0 <= s[0] <= x1 and y0 <= s[1] <= y1:
            out.append(e)
    if out:
        setEdgeSel(E, E.id, out, 'remove')
    return len(out)


# ---------------- Path -> curve ----------------

def edgeCurveGroup(P, sel_items, it, suffix, fresh=False):
    """port of edgeCurveGroup (3847): the group new curves go into: the selected group (not a service one), else a group
    of its own for this mesh (it.pathGroup)"""
    from . import edit
    sg = None
    for iid in reversed(sel_items):
        x = P.items.get(iid)
        if x is not None and x.type == 'group':
            sg = x
            break

    def service(x):
        while x is not None:
            if x.importGroup or x.guidesGroup or x.meshesGroup:
                return True
            x = P.parentOf(x)
        return False
    if not fresh and sg is not None and not service(sg):
        return sg
    key = None if fresh else it.pathGroup
    old = P.items.get(key) if key is not None else None
    if old is not None and old.type == 'group':
        return old
    g = edit.addItem(P, 'group', None, {'name': f'{it.name} {suffix}'})
    if g.id not in P.settings.convChecked:
        P.settings.convChecked.append(g.id)
    if not fresh:
        it.pathGroup = g.id
    return g


def fitAnchor(P, q):
    # port of fitAnchor (3788): an anchor for a fitted point (a sharp turn -> smoothness 0)
    from . import edit
    p, sharp = q
    a = edit.newAnchor(P, p)
    if sharp:
        a.smooth = 0
    return a


def edgesToCurves(P, sel_items, E):
    """port of edgesToCurves (3858): the selected edges -> curves, one per chain; chains meeting at a vertex share the
    anchor there. Returns (the curves made, the group, the toast); (selection: S.sel = the curves, the caller's)."""
    from . import edit
    it = P.items.get(E.id)
    if it is None or it.type != 'mesh' or it.data is None or not E.edges:
        return [], None, NO_EDGES
    T = edgeTopo(it.data); Wp = edgeWorld(it.data); chains = edgeChains(T, E.edges)
    g = edgeCurveGroup(P, sel_items, it, 'paths', False)
    ends = {}; made = []

    def endAnchor(v):
        if v not in ends:
            ends[v] = edit.newAnchor(P, wv(Wp, v))
        return ends[v]
    for ch in chains:
        verts = ch['verts']
        fit = fitPolyline([wv(Wp, v) for v in verts], ch['closed'])
        if len(fit) < 2:
            continue
        c = edit.makeCurve(P, g.id); last = len(fit) - 1
        c.anchors = [fitAnchor(P, q) if ch['closed'] else endAnchor(verts[0]) if k == 0 else endAnchor(verts[len(verts) - 1]) if k == last else fitAnchor(P, q)
                     for k, q in enumerate(fit)]
        if c.anchors[0] is c.anchors[last]:
            c.anchors[last] = edit.newAnchor(P, c.anchors[0].pos)
        if ch['closed']:
            c.closed = True
        c.dirty = True; made.append(c)
    if not made:
        return [], g, NOTHING
    g.expanded = True
    m = len(made); n = len(E.edges)
    return made, g, f'{m} curve{"" if m == 1 else "s"} from {n} edge{"" if n == 1 else "s"} → {g.name}'
