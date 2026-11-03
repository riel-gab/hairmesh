# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Phase 3.8a: the Scissors of the original (hair-modeler.clean.html:5062-5486), the data part. Pure Python, no bpy.

  insertKeepShape                       a new anchor on a segment, the shape kept (de Casteljau; the handles around
                                        it frozen on every curve running along that segment)
  scissorHits / cutCurves               the curve mode: a stroke dragged across the selected curves; each one crossed
                                        away from its ends is split there (split / delete the upper / lower part)
  SurfCut, startSurfCut, wallGrid, gridAt, gridParam, edgeR, endParam, wallPath, edgePick, cutFrame, cutBuild,
  surfCutCurve, surfCutView, applySurfCut, surfCutDown, surfCutPtMove, surfCutHover, cutPieItems
                                        the NURBS surface mode (1.0.1): a cut placed on one wall like a curve, Enter
                                        splits the wall in two along a new curve

A module of its own (like weld.py and walls.py): it needs weld.splitAtAnchor, and weld.py imports edit.py.

The viewport comes in as functions: `screen(p)` is toScreen (5656) — (x, y, z) in pane pixels, z > 1 beyond the far
plane, or None behind the camera (the Blender projector; the original's toScreen gives z > 1 there) — and, for the
wall cut, the point where the cursor's ray hits the wall's own mesh (pickObjects(m, [P.sf.mesh]): `wall_hit`, a
model-space point or None). Pixel thresholds (9 / 10 / 8 px) come scaled by the caller (Blender's UI scale).

`new Anchor(p)` takes an id in the original (UID++) even for the anchors of a preview that is thrown away: the port
does the same (tmpAnchor: the id taken, the anchor not registered in P.anchors), so a parity script gets the same ids
on both sides. Selection, undo (commit), toasts and regenConversion are the caller's."""
import math
from functools import cmp_to_key

from . import model as M
from .curves import bez, curveHandles, curveSegs, fitHandles, markAnchorsDirty, rebuildCurve, sampleCurve
from .edit import addItem, deleteItem, makeCurve, newAnchor
from .js import clamp, index_of, js_hypot, js_max, js_min, js_or, js_round, nullish, to_int32
from .sections import curveSecAt, lockSecs   # noqa: F401 (moved there in 4.1; re-exported)
from .surfaces import surfaceData
from .vec3 import V3
from .weld import distToSeg, splitAtAnchor

INF = math.inf


def tmpAnchor(P, pos):
    # `new Anchor(p)` (hair-modeler.clean.html:713) for an anchor that may never reach a curve: the id is taken
    # (UID++), the anchor is not registered (the original has no registry; the file holds the anchors curves use)
    a = M.Anchor(id=P.uid, pos=pos.clone(), smooth=1); P.uid += 1
    return a


def _lerp(x, y, u):
    return x.clone().lerp(y, u)


# ======================= insertKeepShape (5062) =======================

def insertKeepShape(P, c, seg, u):
    """port of insertKeepShape (hair-modeler.clean.html:5062): a new anchor at Bézier parameter u of segment seg of
    curve c — the shape kept: on every curve running along that segment (a welded trunk) the segment is split by de
    Casteljau and the handles around the new anchor are frozen"""
    p = c.anchors[seg]; q = c.anchors[seg + 1]
    users = []
    for h in P.curves():
        i = index_of(h.anchors, p); j = index_of(h.anchors, q)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            users.append(h)
    a = newAnchor(P, bez(*curveSegs(P, c)[seg], u)); a.smooth = 1
    plans = []
    for h in users:
        i = index_of(h.anchors, p); j = index_of(h.anchors, q); k = min(i, j)
        plans.append((h, k, curveSegs(P, h)[k], u if i < j else 1 - u, curveHandles(P, h, k), curveHandles(P, h, k + 1)))
    for h, k, s, uu, H0, H1 in plans:
        p0, p1, p2, p3 = s
        q0 = _lerp(p0, p1, uu); q1 = _lerp(p1, p2, uu); q2 = _lerp(p2, p3, uu); r0 = _lerp(q0, q1, uu); r1 = _lerp(q1, q2, uu)
        A0 = h.anchors[k]; A1 = h.anchors[k + 1]
        h.anchors.insert(k + 1, a)

        def put(an, side, v, h=h):
            sm = an.smooth
            if sm < 1e-3:
                return
            o = h.ovr.get(an.id)
            if o is None:
                o = M.HandleOverride()
            setattr(o, 'in_' if side == 'in' else 'out', v.clone().sub(an.pos).divideScalar(sm))
            h.ovr[an.id] = o
        if k > 0:
            put(A0, 'in', H0['hin'])
        put(A0, 'out', q0)
        put(a, 'in', r0); put(a, 'out', r1)
        put(A1, 'in', q2)
        if k + 2 < len(h.anchors) - 1:
            put(A1, 'out', H1['hout'])
        h.dirty = True
    P.gen += 1; P.rev += 1
    return a


# ======================= the curve mode (5085-5127) =======================

def scissorHits(P, x0, y0, x1, y1, lst, screen):
    """port of scissorHits (hair-modeler.clean.html:5085): where the stroke (screen, pane coordinates) crosses each of
    the curves — the crossing nearest to the stroke's middle: [{c, seg, u}]. `lst`: the curves to cut (the original's
    selected curves; the caller keeps the shown ones, c.obj.visible)."""
    mx = (x0 + x1) / 2; my = (y0 + y1) / 2; out = []
    for c in lst:
        if c.ring or c.closed or len(c.samples) < 2:
            continue
        sp = [screen(s.p) for s in c.samples]; best = None; bd = INF
        for i in range(len(sp) - 1):
            a = sp[i]; b = sp[i + 1]
            if a is None or b is None or a[2] > 1 or b[2] > 1:
                continue
            rx = x1 - x0; ry = y1 - y0; sx = b[0] - a[0]; sy = b[1] - a[1]; den = rx * sy - ry * sx
            if abs(den) < 1e-9:
                continue
            t = ((a[0] - x0) * sy - (a[1] - y0) * sx) / den; v = ((a[0] - x0) * ry - (a[1] - y0) * rx) / den
            if t < 0 or t > 1 or v < 0 or v > 1:
                continue
            d = js_hypot(x0 + rx * t - mx, y0 + ry * t - my)
            if d < bd:
                bd = d; seg = c.samples[i].seg
                best = {'c': c, 'seg': seg, 'u': clamp((i - seg * 24 + v) / 24, 0, 1)}
        if best:
            out.append(best)
    return out


CUT_MODES = ('split', 'up', 'down')
NOTHING_CUT = 'Nothing was cut (the cut must cross a curve away from its ends)'


def cutCurves(P, hits, mode):
    """port of cutCurves (hair-modeler.clean.html:5102). mode: 'split' — two curves; 'up' / 'down' — split and delete
    the upper / lower part (by the height of its free end). Curves welded along the cut go with the first one; the
    walls of the cut curves are deleted. Returns (curves cut, the toast, the ids of the pieces that still exist)."""
    n = 0; cutIds = set()
    for h in hits:
        c = h['c']; seg = h['seg']; u = h['u']
        if c.id not in P.items or c.id in cutIds:
            continue                                                # (welded along the cut — split with the first one)
        A = c.anchors
        J = A[seg] if u < 0.03 else A[seg + 1] if u > 0.97 else None   # right at an anchor: cut there
        if J is not None and (J is A[0] or J is A[len(A) - 1]):
            continue                                                # (a curve's end — nothing to cut)
        if J is None:
            J = insertKeepShape(P, c, seg, u)
        pairs = splitAtAnchor(P, J, True)
        if not pairs:
            continue
        n += 1
        for pr in pairs:
            cutIds.update(pr)
        # the NURBS surfaces of a cut curve go (both pieces' — the lock is rebuilt by hand from the new curves)
        for s in P.surfaces():
            if s.a in cutIds or s.b in cutIds:
                deleteItem(P, s.id)
        if mode == 'split':
            continue
        for pr in pairs:
            ps = [x for x in (P.items.get(i) for i in pr) if x is not None]
            if len(ps) < 2:
                continue

            def far(x):
                B = x.anchors
                return (B[len(B) - 1] if B[0] is J else B[0]).pos.y
            up = ps[0] if far(ps[0]) >= far(ps[1]) else ps[1]
            down = ps[1] if up is ps[0] else ps[0]
            deleteItem(P, (up if mode == 'up' else down).id)
    if not n:
        return 0, NOTHING_CUT, []
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    ids = [i for i in cutIds if i in P.items]
    if mode == 'split':
        return n, f'Cut: {"the curve is" if n == 1 else f"{n} curves are"} split in two', ids
    return n, f'Cut: the {"upper" if mode == "up" else "lower"} part{"s" if n > 1 else ""} deleted', ids


def cutTitle(hits):
    # the pie's title (6018)
    return f'✂ Cut {hits[0]["c"].name if len(hits) == 1 else f"{len(hits)} curves"}'


# ======================= the NURBS surface mode (5128-5486) =======================

class SurfCut:
    """S.surfCut (startSurfCut, 5334): the wall being cut, its two curves, its grid; the cut's start / placed points
    (wall coordinates {r, t}) / end, the hover while placing, and the last cutBuild (build) or its error (err).
    lead (ours, 3.8f): before the start, edgePick's end under the cursor — where the first click would put it — or None."""

    def __init__(self, sf, A, B, G):
        self.sf = sf; self.A = A; self.B = B; self.G = G
        self.start = None; self.pts = []; self.end = None; self.hover = None; self.lead = None
        self.build = None; self.err = None


class Grid:
    # wallGrid's result
    def __init__(self, R, M_, pos, dr, dt, width):
        self.R = R; self.M = M_; self.pos = pos; self.dr = dr; self.dt = dt; self.width = width

    def at(self, k, j):
        return self.pos[k * self.M + j]


def wallGrid(P, sf, T):
    """port of wallGrid (hair-modeler.clean.html:5146): the wall as a grid of points, R rows × M across (t = 0 — curve
    a, t = M − 1 — curve b); dr / dt — mean spacing. T: the row tables (buildRowTables())."""
    d = surfaceData(P, sf, T)
    if d is None or not d.pos:
        return None
    M_ = js_max(1, to_int32(nullish(sf.p.get('segs'), 0))) + 1
    R = len(d.pos) // M_
    G = Grid(R, M_, d.pos, 0, 0, 0)
    at = G.at
    dr = 0; w = 0
    for k in range(R - 1):
        dr += (at(k, 0).distanceTo(at(k + 1, 0)) + at(k, M_ - 1).distanceTo(at(k + 1, M_ - 1))) / 2
    for k in range(R):
        w += at(k, 0).distanceTo(at(k, M_ - 1))
    dr /= js_max(1, R - 1); w /= R
    G.dr = js_or(dr, 1e-3); G.dt = js_or(w, 1e-3) / (M_ - 1); G.width = js_or(w, 1e-3)
    return G


def _floor(x):
    return math.floor(x) if x == x and x not in (INF, -INF) else x


def gridAt(G, r, t):
    # port of gridAt (hair-modeler.clean.html:5156)
    k = clamp(_floor(r), 0, G.R - 2); j = clamp(_floor(t), 0, G.M - 2); a = clamp(r - k, 0, 1); b = clamp(t - j, 0, 1)
    return G.at(k, j).clone().lerp(G.at(k, j + 1), b).lerp(G.at(k + 1, j).clone().lerp(G.at(k + 1, j + 1), b), a)


# ---- three.js Triangle (math/Triangle.js, r160): getArea, closestPointToPoint, getBarycoord ----

def _triArea(a, b, c):
    v0 = c.clone().sub(b); v1 = a.clone().sub(b)
    return v0.cross(v1).length() * 0.5


def _closestPointToPoint(a, b, c, p):
    # Triangle.closestPointToPoint (Real-Time Collision Detection, 5.1.5)
    vab = b.clone().sub(a); vac = c.clone().sub(a); vap = p.clone().sub(a)
    d1 = vab.dot(vap); d2 = vac.dot(vap)
    if d1 <= 0 and d2 <= 0:
        return a.clone()
    vbp = p.clone().sub(b)
    d3 = vab.dot(vbp); d4 = vac.dot(vbp)
    if d3 >= 0 and d4 <= d3:
        return b.clone()
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return a.clone().addScaledVector(vab, v)
    vcp = p.clone().sub(c)
    d5 = vab.dot(vcp); d6 = vac.dot(vcp)
    if d6 >= 0 and d5 <= d6:
        return c.clone()
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return a.clone().addScaledVector(vac, w)
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        vbc = c.clone().sub(b)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return b.clone().addScaledVector(vbc, w)
    denom = 1 / (va + vb + vc)
    v = vb * denom; w = vc * denom
    return a.clone().addScaledVector(vab, v).addScaledVector(vac, w)


def _barycoord(p, a, b, c):
    # Triangle.getBarycoord: (0, 0, 0) for a degenerate triangle
    v0 = c.clone().sub(a); v1 = b.clone().sub(a); v2 = p.clone().sub(a)
    dot00 = v0.dot(v0); dot01 = v0.dot(v1); dot02 = v0.dot(v2); dot11 = v1.dot(v1); dot12 = v1.dot(v2)
    denom = dot00 * dot11 - dot01 * dot01
    if denom == 0:
        return V3(0, 0, 0)
    invDenom = 1 / denom
    u = (dot11 * dot02 - dot01 * dot12) * invDenom
    v = (dot00 * dot12 - dot01 * dot02) * invDenom
    return V3(1 - u - v, v, u)


def _finite(x):
    return x == x and x not in (INF, -INF)


def gridParam(G, p):
    """port of gridParam (hair-modeler.clean.html:5160): the wall coordinates {r, t} of the point of the wall nearest
    to p (None: no triangle)"""
    best = None; bd = INF
    for k in range(G.R - 1):
        for j in range(G.M - 1):
            C = ((k, j), (k, j + 1), (k + 1, j + 1), (k + 1, j))
            for x, y, z in ((0, 1, 2), (0, 2, 3)):
                ta = G.at(*C[x]); tb = G.at(*C[y]); tc = G.at(*C[z])
                if _triArea(ta, tb, tc) < 1e-12:
                    continue
                q = _closestPointToPoint(ta, tb, tc, p); d = q.distanceToSquared(p)
                if d >= bd:
                    continue
                bc = _barycoord(q, ta, tb, tc)
                if not _finite(bc.x):
                    continue
                bd = d
                best = {'r': bc.x * C[x][0] + bc.y * C[y][0] + bc.z * C[z][0], 't': bc.x * C[x][1] + bc.y * C[y][1] + bc.z * C[z][1]}
    return best


def edgeR(G, j, p):
    # port of edgeR (hair-modeler.clean.html:5177): r of the point of the wall's edge column j (0 or M − 1) nearest to p
    best = 0; bd = INF
    for k in range(G.R - 1):
        a = G.at(k, j); ab = G.at(k + 1, j).clone().sub(a); l2 = ab.lengthSq()
        s = clamp(p.clone().sub(a).dot(ab) / l2, 0, 1) if l2 > 0 else 0
        d = a.clone().addScaledVector(ab, s).distanceToSquared(p)
        if d < bd:
            bd = d; best = k + s
    return best


def endParam(G, E):
    # port of endParam (hair-modeler.clean.html:5185)
    return {'r': edgeR(G, G.M - 1 if E['side'] else 0, E['pos']), 't': G.M - 1 if E['side'] else 0}


def _ceil(x):
    return math.ceil(x) if _finite(x) else x


def wallPath(G, ctl):
    """port of wallPath (hair-modeler.clean.html:5188): a path through wall coordinates (Catmull-Rom in lengths along /
    across): (dense — points {r, t}, idx — where each control point is in it)"""
    X = [[c['r'] * G.dr, c['t'] * G.dt] for c in ctl]; dense = []; idx = []

    def cr(p0, p1, p2, p3, s, i):
        return 0.5 * (2 * p1[i] + (p2[i] - p0[i]) * s + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * s * s + (3 * p1[i] - p0[i] - 3 * p2[i] + p3[i]) * s * s * s)
    for i in range(len(X) - 1):
        p0 = X[max(i - 1, 0)]; p1 = X[i]; p2 = X[i + 1]; p3 = X[min(i + 2, len(X) - 1)]
        n = clamp(_ceil(js_hypot(p2[0] - p1[0], p2[1] - p1[1]) / (0.35 * js_min(G.dr, G.dt))), 4, 400)
        idx.append(len(dense))
        s = 0
        while s < n:
            dense.append({'r': clamp(cr(p0, p1, p2, p3, s / n, 0) / G.dr, 0, G.R - 1), 't': clamp(cr(p0, p1, p2, p3, s / n, 1) / G.dt, 0, G.M - 1)})
            s += 1
    idx.append(len(dense)); dense.append(dict(ctl[len(ctl) - 1]))
    return dense, idx


def curveRev(A, B):
    # port of curveRev (hair-modeler.clean.html:5202)
    a = A.anchors; b = B.anchors; n = len(a) - 1; m = len(b) - 1
    return a[0].pos.distanceTo(b[0].pos) + a[n].pos.distanceTo(b[m].pos) > a[0].pos.distanceTo(b[m].pos) + a[n].pos.distanceTo(b[0].pos)


def virtualAnchor(P, c, seg, u):
    """port of virtualAnchor (hair-modeler.clean.html:5205): an anchor that would go in on curve c at (seg, u) keeping
    the shape — not put in yet (vh: its handles, by the neighbour's id)"""
    p0, p1, p2, p3 = curveSegs(P, c)[seg]
    q0 = _lerp(p0, p1, u); q1 = _lerp(p1, p2, u); q2 = _lerp(p2, p3, u); r0 = _lerp(q0, q1, u); r1 = _lerp(q1, q2, u)
    a = tmpAnchor(P, _lerp(r0, r1, u))
    a.vh = {c.anchors[seg].id: r0, c.anchors[seg + 1].id: r1}
    return a


def handleToward(P, c, a, nb):
    """port of handleToward (hair-modeler.clean.html:5211): the handle of curve c at its anchor a facing its neighbour
    nb (a not put in yet — its own handles), or None"""
    i = index_of(c.anchors, a)
    if i < 0:
        vh = getattr(a, 'vh', None)
        h = vh.get(nb.id) if vh else None
        return h.clone() if h is not None else None
    H = curveHandles(P, c, i)
    return H['hin'] if i > 0 and c.anchors[i - 1] is nb else H['hout']


def edgePick(P, cut, m, maxPx, screen, wall_hit, anchor_px=10):
    """port of edgePick (hair-modeler.clean.html:5213): the wall's curve under the cursor m = (x, y) (within maxPx) —
    an end of the cut: {side (0 — a, 1 — b), c, a, pos} on an anchor (one within anchor_px of the cursor), else
    {side, c, seg, u, v (virtual anchor), pos}. wall_hit: where the cursor is on the wall (a stretch of a curve lying
    elsewhere — another turn of a curl behind the wall — doesn't count)."""
    near = 0.3 * cut.G.width
    best = None
    for side, c in ((0, cut.A), (1, cut.B)):
        sp = [screen(s.p) for s in c.samples]
        for i in range(len(sp) - 1):
            a = sp[i]; b = sp[i + 1]
            if a is None or b is None or a[2] > 1 or b[2] > 1:
                continue
            vx = b[0] - a[0]; vy = b[1] - a[1]; l2 = vx * vx + vy * vy
            t = clamp(((m[0] - a[0]) * vx + (m[1] - a[1]) * vy) / l2, 0, 1) if l2 > 0 else 0
            d = js_hypot(a[0] + vx * t - m[0], a[1] + vy * t - m[1])
            if d >= maxPx or (best is not None and d >= best['d']):
                continue
            if wall_hit is not None and c.samples[i].p.clone().lerp(c.samples[i + 1].p, t).distanceTo(wall_hit) > near:
                continue
            best = {'d': d, 'side': side, 'c': c, 'i': i, 't': t}
    if best is None:
        return None
    side = best['side']; c = best['c']; bi = best['i']; seg = c.samples[bi].seg
    u = clamp((bi - seg * 24 + best['t']) / 24, 0, 1)
    pt = c.samples[bi].p.clone().lerp(c.samples[bi + 1].p, best['t'])
    a = None; ad = anchor_px
    for x in c.anchors:
        if x.pos.distanceTo(pt) > near:
            continue                                                # (one on another turn, there on the screen)
        s = screen(x.pos)
        if s is None or s[2] > 1:
            continue
        d = js_hypot(s[0] - m[0], s[1] - m[1])
        if d < ad:
            ad = d; a = x
    if a is None and u < 0.02:
        a = c.anchors[seg]
    if a is None and u > 0.98:
        a = c.anchors[seg + 1]
    if a is not None:
        return {'side': side, 'c': c, 'a': a, 'pos': a.pos}
    v = virtualAnchor(P, c, seg, u)
    return {'side': side, 'c': c, 'seg': seg, 'u': u, 'v': v, 'pos': v.pos, 'a': None}


def _cmp(v):
    # a comparator's result as Array.prototype.sort reads it (only the sign; NaN as 0)
    return -1 if v < 0 else 1 if v > 0 else 0


def cutLists(cut):
    """port of cutLists (hair-modeler.clean.html:5243): the anchors of A and of B in A's direction, with the ends not
    put in yet among them"""
    A = cut.A; B = cut.B; rev = curveRev(A, B); para = bool(A.para and B.para)
    LA = list(A.anchors); LB = list(B.anchors); ins = []
    for E in (cut.start, cut.end):
        if E and not E.get('a'):
            ins.append((LB if E['side'] else LA, E['seg'], E['u'], E['v']))
    ins.sort(key=cmp_to_key(lambda x, y: _cmp((y[1] - x[1]) or (y[2] - x[2]))))
    for L, sg, _u, a in ins:
        L.insert(sg + 1, a)
    if rev:
        LB.reverse()
    return {'OA': LA, 'OB': LB, 'para': para, 'rev': rev}


def lockOf(P, sf):
    # port of lockOf (hair-modeler.clean.html:5250): the curves of the wall's lock (joined by walls)
    ids = {sf.a: None, sf.b: None}; grew = True
    while grew:
        grew = False
        for s in P.surfaces():
            if (s.a in ids) != (s.b in ids):
                ids.setdefault(s.a, None); ids.setdefault(s.b, None); grew = True
    return [P.items[i] for i in ids if i in P.items]


def _index(L, a):
    for i, x in enumerate(L):
        if x is a:
            return i
    return -1


def cutFrame(cut):
    """port of cutFrame (hair-modeler.clean.html:5257): the cut's frame — {OA, OB, pre (anchors along the first curve up
    to the start), post (from the end along the second curve), para, p / q (where start / end are), swapped (the cut
    was placed from the tip side)} — or an error text"""
    L = cutLists(cut)
    sa = cut.start.get('a') or cut.start.get('v'); ea = cut.end.get('a') or cut.end.get('v'); OA = L['OA']; OB = L['OB']
    if sa is ea:
        return 'Start and end the cut at two different places'

    def where(a):
        i = _index(OA, a)
        if i >= 0:
            return {'L': OA, 'i': i, 's': 0}
        i = _index(OB, a)
        return {'L': OB, 'i': i, 's': 1} if i >= 0 else None
    p = where(sa); q = where(ea); swapped = False
    if not p or not q:
        return "The cut must start and end on the wall's curves"

    def u(w):
        return w['i'] / (len(w['L']) - 1)
    if u(q) < u(p) or (u(q) == u(p) and q['i'] < p['i']):
        p, q = q, p; swapped = True
    bad = 'The cut must run along the wall: from a place nearer the root to one nearer the tip'
    if (p['L'] is q['L'] and q['i'] <= p['i']) or u(q) - u(p) < 1e-6:
        return bad
    pre = p['L'][:p['i'] + 1]; post = q['L'][q['i']:]
    if any(_index(pre, x) >= 0 for x in post):
        return bad
    return {'A': cut.A, 'B': cut.B, 'OA': OA, 'OB': OB, 'pre': pre, 'post': post, 'para': L['para'], 'p': p, 'q': q, 'swapped': swapped}


# ---- cross-sections of a converted modifier's lock: lockSecs 1435 and curveSecAt 1464 live in sections.py (4.1:
# the row tables read them too) ----


def wallSecAt(cut, sec, q):
    # port of wallSecAt (hair-modeler.clean.html:5273): the cross-section at wall coordinates q
    G = cut.G; sa = curveSecAt(cut.A, sec, gridAt(G, q['r'], 0)); sb = curveSecAt(cut.B, sec, gridAt(G, q['r'], G.M - 1))
    return sa + (sb - sa) * q['t'] / (G.M - 1)


# ---- the cut's curve ----

def cutBuild(P, cut):
    """port of cutBuild (hair-modeler.clean.html:5277): the path over the wall and the new curve's anchors in between
    (mids) — {F, mids, mp, d3, at, idx, swapped} — or an error text"""
    F = cutFrame(cut)
    if isinstance(F, str):
        return F
    G = cut.G; ctl = [endParam(G, cut.start), *cut.pts, endParam(G, cut.end)]
    if F['swapped']:
        ctl.reverse()
    dense, idx = wallPath(G, ctl); d3 = [gridAt(G, q['r'], q['t']) for q in dense]
    # a converted lock: the cut must keep going from the root towards the tip
    sec = lockSecs(lockOf(P, cut.sf)) if F['para'] else None
    if sec is not None:
        mx = -INF
        for i in range(0, len(dense), 3):
            s = wallSecAt(cut, sec, dense[i])
            if s < mx - 0.3:
                return 'The cut turns back towards the root — keep it going one way along the lock'
            mx = js_max(mx, s)
    # the placed anchors, plus one in the middle of every stretch where the curve through them (its handles fitted to
    # the path) still leaves the path by more than 1% of the wall's width — until it follows the path
    tol = js_max(0.01 * G.width, 1e-5 * P.S.scale)
    ci = idx[1:len(idx) - 1]; mids = []
    for _pass in range(12):
        mids = [d3[i].clone() for i in ci]
        bd = [0, *ci, len(dense) - 1]; k0 = len(F['pre']) - 1; add = []
        segs = curveSegs(P, surfCutCurve(P, F, [tmpAnchor(P, p) for p in mids], {'d3': d3, 'at': bd}))
        for s in range(len(bd) - 1):
            lo = bd[s]; hi = bd[s + 1]
            if hi - lo < 3:
                continue
            dev = 0
            for w in range(1, 8):
                x = bez(*segs[k0 + s], w / 8); dm = INF
                for i in range(lo, hi):
                    dm = js_min(dm, distToSeg(x, d3[i], d3[i + 1]))
                dev = js_max(dev, dm)
            if dev > tol:
                add.append((lo + hi) >> 1)
        if not add or len(ci) > 200:
            break
        ci = sorted([*ci, *add])
    return {'F': F, 'mids': mids, 'mp': [dense[i] for i in ci], 'd3': d3, 'at': [0, *ci, len(dense) - 1], 'idx': idx, 'swapped': F['swapped']}


def pathHandles(d3, x0, x1, P0, P3):
    """port of pathHandles (hair-modeler.clean.html:5307): handles making the cubic from P0 (path position x0) to P3
    (x1) follow the path d3 between them — {out (at P0), in (at P3)} as offsets"""
    def tan(x):
        i = clamp(js_round(x), 0, len(d3) - 1)
        t = d3[min(i + 1, len(d3) - 1)].clone().sub(d3[max(i - 1, 0)])
        return t.normalize() if t.lengthSq() > 1e-16 else None
    ch = P0.distanceTo(P3); t0 = tan(x0); t1 = tan(x1)
    if t0 is None or t1 is None:
        return {'out': P3.clone().sub(P0).multiplyScalar(1 / 3), 'in': P0.clone().sub(P3).multiplyScalar(1 / 3)}
    pts = [P0]
    i = _floor(x0) + 1
    while i < x1:
        pts.append(d3[i]); i += 1
    pts.append(P3)
    d2 = t1.clone().negate()
    if len(pts) > 2:
        f = fitHandles(pts, P0, P3, t0, d2); a = f['a']; b = f['b']
    else:
        a = ch / 3; b = ch / 3
    return {'out': t0.multiplyScalar(clamp(a, 0.05 * ch, ch)), 'in': d2.multiplyScalar(clamp(b, 0.05 * ch, ch))}


def surfCutCurve(P, F, mids, fit):
    """port of surfCutCurve (hair-modeler.clean.html:5319): the cut's curve, an object not in the scene (the preview,
    then the new curve's anchors and handles). Across the wall every handle is fitted to the path (fit: {d3, at});
    at the two fork anchors the side running along A / B takes the host's own handle."""
    anchors = [*F['pre'], *mids, *F['post']]; ovr = {}; k0 = len(F['pre']) - 1; k1 = k0 + len(mids) + 1

    def host(w):
        return F['A'] if w['L'] is F['OA'] else F['B']

    def put(a, side, v):
        if v is None or not (a.smooth > 0):
            return
        if a.id not in ovr:
            ovr[a.id] = M.HandleOverride(in_=None, out=None)
        setattr(ovr[a.id], 'in_' if side == 'in' else 'out', v.divideScalar(a.smooth))

    def shared(a, nb, h):
        x = handleToward(P, h, a, nb) if nb is not None else None
        return x.sub(a.pos) if x is not None else None
    put(anchors[k0], 'in', shared(anchors[k0], anchors[k0 - 1] if k0 > 0 else None, host(F['p'])))
    put(anchors[k1], 'out', shared(anchors[k1], anchors[k1 + 1] if k1 + 1 < len(anchors) else None, host(F['q'])))
    for s in range(k0, k1):
        a = anchors[s]; b = anchors[s + 1]
        if fit:
            H = pathHandles(fit['d3'], fit['at'][s - k0], fit['at'][s - k0 + 1], a.pos, b.pos)
        else:
            H = {'out': b.pos.clone().sub(a.pos).multiplyScalar(1 / 3), 'in': a.pos.clone().sub(b.pos).multiplyScalar(1 / 3)}
        put(a, 'out', H['out']); put(b, 'in', H['in'])
    return M.Curve(id=-1, type='curve', name='', anchors=anchors, ovr=ovr, closed=False, round=0, fair=0)


def startSurfCut(P, sf, T):
    """port of startSurfCut (hair-modeler.clean.html:5334), the data part: (SurfCut, None) or (None, the toast). The
    selection becomes the wall (S.sel = {sf.id}): the caller's."""
    G = wallGrid(P, sf, T)
    if G is None:
        return None, 'This wall has no surface to cut'
    return SurfCut(sf, P.items.get(sf.a), P.items.get(sf.b), G), None


def surfCutView(P, cut):
    """port of drawSurfCut (hair-modeler.clean.html:5344), the data part: sets cut.build / cut.err and returns
    (line — model-space points of the cut, dots — [(point, kind: 'end' / 'ctl' / 'auto' / 'hover' / 'lead')], the HUD
    text). 'lead' (ours, 3.8f): before the start, where the first click would put it."""
    G = cut.G; dots = []; line = []
    if not cut.start and cut.lead is not None:
        dots.append((cut.lead['pos'], 'lead'))
    if cut.start and cut.end:
        R = cutBuild(P, cut)
        cut.build = None if isinstance(R, str) else R; cut.err = R if isinstance(R, str) else None
        if R and not isinstance(R, str):
            tmp = surfCutCurve(P, R['F'], [tmpAnchor(P, p) for p in R['mids']], R); k0 = len(R['F']['pre']) - 1; k1 = k0 + len(R['mids']) + 1
            line = [s.p for s in sampleCurve(P, tmp) if k0 <= s.seg < k1]; line.append(tmp.anchors[k1].pos.clone())
            for p in R['mids']:
                dots.append((p, 'auto'))
    elif cut.start:
        h = cut.hover
        ctl = [endParam(G, cut.start), *cut.pts, *([endParam(G, h['end']) if h.get('end') else h['pt']] if h else [])]
        if len(ctl) > 1:
            line = [gridAt(G, q['r'], q['t']) for q in wallPath(G, ctl)[0]]
        if h:
            dots.append((h['end']['pos'] if h.get('end') else gridAt(G, h['pt']['r'], h['pt']['t']), 'hover'))
    if cut.start:
        dots.append((cut.start['pos'], 'end'))
    for q in cut.pts:
        dots.append((gridAt(G, q['r'], q['t']), 'ctl'))
    if cut.end:
        dots.append((cut.end['pos'], 'end'))
    if len(line) < 2:
        line = []
    return line, dots, hudText(cut)


def hudText(cut):
    # the HUD of drawSurfCut (5381-5384), with Blender's keys
    if not cut.start:
        return f'Cutting {cut.sf.name}: click one of its curves where the cut starts (on an anchor, or a new one goes in there) · Esc — cancel'
    if not cut.end:
        return 'Click on the wall to add anchors · click its other curve to end the cut · Backspace — remove the last anchor · Esc — cancel'
    if cut.build:
        return f"Drag the anchors (the ends slide along the wall's curves) · RMB on the cut — insert an anchor · double click — delete one · Enter or Shift+RMB — cut ({len(cut.build['mids']) + 2} anchors)"
    return f'{cut.err or "The cut must run along the wall, from nearer the root to nearer the tip"} — move its anchors'


def realizeEnds(P, cut):
    # port of realizeEnds (hair-modeler.clean.html:5379): the ends not put in yet go into their curves now (keeping the shape)
    pend = [E for E in (cut.start, cut.end) if not E.get('a')]
    if not pend:
        return
    pend.sort(key=cmp_to_key(lambda x, y: _cmp((y['seg'] - x['seg']) or (y['u'] - x['u'])) if x['c'] is y['c'] else 0))
    for E in pend:
        E['a'] = insertKeepShape(P, E['c'], E['seg'], E['u'])
    for E in pend:
        E['pos'] = E['a'].pos; E.pop('v', None)
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)                                      # (their samples — for the sections)


def applySurfCut(P, cut):
    """port of applySurfCut (hair-modeler.clean.html:5386): a new curve along the cut and the wall split in two, (A,
    cut) and (cut, B). Returns (the new curve or None, the toast, touched: whether the model changed — a failure after
    the ends went in leaves them in, as in the original; the caller decides what to keep)."""
    if not cut.start or not cut.end:
        return None, None, False
    chk = cutBuild(P, cut)
    if isinstance(chk, str):
        return None, chk, False
    sf = cut.sf; lock = lockOf(P, sf); para = len(lock) > 1 and all(c.para for c in lock)

    def fix():
        # a converted lock: every anchor gets its cross-section number written down first (old files had none)
        sec = lockSecs(lock)
        if sec is not None:
            by = {a.id: a for c in lock for a in c.anchors}
            for aid, s in sec.items():
                by[aid].sec = s
        return sec
    if para:
        fix()
    realizeEnds(P, cut)
    sec = fix() if para else None
    R = cutBuild(P, cut)
    if isinstance(R, str):
        return None, R, True
    F = R['F']; tmp = surfCutCurve(P, F, [tmpAnchor(P, p) for p in R['mids']], R); a0 = F['pre'][len(F['pre']) - 1]; a1 = F['post'][0]
    # the curves running through the fork anchors keep their handles there (frozen — the new curve would otherwise
    # tilt their common tangent), so the stretches along A and B stay exactly as they are
    for a in (a0, a1):
        if a.smooth > 0:
            for h in P.curves():
                i = index_of(h.anchors, a)
                if i <= 0 or i >= len(h.anchors) - 1 or h.ring:
                    continue
                H = curveHandles(P, h, i)
                h.ovr[a.id] = M.HandleOverride(in_=H['hin'].sub(a.pos).divideScalar(a.smooth), out=H['hout'].sub(a.pos).divideScalar(a.smooth))
    if sec is not None:
        # the cut's own anchors: the cross-section where they lie on the wall, kept strictly increasing
        k0 = len(F['pre']) - 1; own = tmp.anchors[k0 + 1:k0 + 1 + len(R['mids'])]; s0 = sec[a0.id]; s1 = sec[a1.id]
        n = len(R['mids']); eps = (s1 - s0) / (4 * (n + 1))
        prev = s0
        for j, a in enumerate(own):
            prev = clamp(wallSecAt(cut, sec, R['mp'][j]), prev + eps, s1 - eps * (n - j))
            a.sec = prev
    c = makeCurve(P, F['A'].parent); c.anchors = tmp.anchors; c.ovr = tmp.ovr
    for a in c.anchors:
        P.anchors.setdefault(a.id, a)                               # (the cut's own anchors: registered now)
    c.para = F['para']
    c.name = f'{F["A"].name} cut'; c.dirty = True
    if c.parent == F['A'].parent:
        arr = P.childrenOf(c.parent); arr.remove(c.id); arr.insert(index_of(arr, F['A'].id) + 1, c.id)
    p = dict(sf.p); par = sf.parent
    deleteItem(P, sf.id)
    for x, y in ((F['A'], c), (c, F['B'])):
        addItem(P, 'surface', par, {'a': x.id, 'b': y.id, 'dirty': True, 'p': dict(p)})
    markAnchorsDirty(P, c.anchors)
    for k in P.curves():
        if any(x is a0 for x in k.anchors) or any(x is a1 for x in k.anchors):
            k.dirty = True
    P.gen += 1; P.rev += 1
    return c, f'Cut: {c.name} ({len(c.anchors)} anchors) along the cut, the wall is split in two', True


def cutDotAt(cut, m, screen, px=10):
    # port of cutDotAt (hair-modeler.clean.html:5422): 0 — start, 1…n — the placed ones, n + 1 — end; −1 — none
    G = cut.G
    dots = [cut.start['pos'] if cut.start else None, *[gridAt(G, q['r'], q['t']) for q in cut.pts], cut.end['pos'] if cut.end else None]
    bk = -1; bd = px
    for k, p in enumerate(dots):
        if p is None:
            continue
        s = screen(p)
        if s is None or s[2] > 1:
            continue
        d = js_hypot(s[0] - m[0], s[1] - m[1])
        if d < bd:
            bd = d; bk = k
    return bk


def surfCutDown(P, cut, m, button, double, screen, wall_hit, other_wall=None, px=1.0):
    """port of surfCutDown (hair-modeler.clean.html:5429) once a wall is being cut: button 0 (LMB) or 2 (RMB), double —
    the second click of a double click (e.detail >= 2), wall_hit — the cursor on cut.sf's own mesh, other_wall — the
    id of another shown wall under the cursor. Returns what the caller does next: {'toast': text}, {'switch': wall id}
    (the cut moves to that wall), {'drag': k} (an anchor of the cut follows the mouse until the release; 'redraw' —
    drawSurfCut ran first), {} (drawSurfCut) or None (nothing)."""
    G = cut.G
    if not cut.start:
        if button != 0:
            return None
        E = edgePick(P, cut, m, 9 * px, screen, wall_hit, 10 * px)
        if E:
            cut.start = E
            return {}
        if other_wall is not None and other_wall != cut.sf.id:
            return {'switch': other_wall}
        return {'toast': "Start the cut on one of the wall's two curves"}
    if not cut.end:
        if button != 0:
            return None
        E = edgePick(P, cut, m, 9 * px, screen, wall_hit, 10 * px)
        if E:
            cut.end = E; R = cutBuild(P, cut)
            if isinstance(R, str):
                cut.end = None
                return {'toast': R}
            cut.hover = None
            return {}
        q = gridParam(G, wall_hit) if wall_hit is not None else None
        if q is not None:                                           # (None: a wall of degenerate triangles only — the original pushed a null)
            cut.pts.append(q)
            return {}
        return {'toast': 'Put the anchors on the wall'}
    k = cutDotAt(cut, m, screen, 10 * px)
    if button == 0 and 0 < k <= len(cut.pts) and double:
        cut.pts.pop(k - 1)
        return {}
    if button == 0 and k >= 0:
        return {'drag': k}
    if button == 2 and cut.build:                                   # RMB on the cut: a new anchor there
        d3 = cut.build['d3']; idx = cut.build['idx']; swapped = cut.build['swapped']; bi = -1; bd = 8 * px
        for i, p in enumerate(d3):
            s = screen(p)
            if s is None or s[2] > 1:
                continue
            d = js_hypot(s[0] - m[0], s[1] - m[1])
            if d < bd:
                bd = d; bi = i
        if bi < 0:
            return None
        s = 0
        while s < len(idx) - 2 and idx[s + 1] <= bi:
            s += 1
        q = gridParam(G, d3[bi])
        cut.pts.insert(len(cut.pts) - s if swapped else s, q)
        return {'drag': len(cut.pts) - s if swapped else s + 1, 'redraw': True}
    return None


def surfCutPtMove(P, cut, k, m, screen, wall_hit, px=1.0):
    """port of surfCutPtMove (hair-modeler.clean.html:5461): dragging anchor k of the cut — one in between over the
    wall; an end along the wall's curves (refused when the cut would no longer run along the wall). True: moved."""
    if k == 0 or k == len(cut.pts) + 1:
        key = 'start' if k == 0 else 'end'
        E = edgePick(P, cut, m, INF, screen, wall_hit, 10 * px); old = getattr(cut, key)
        if not E:
            return False
        setattr(cut, key, E)
        if cut.start and cut.end and isinstance(cutFrame(cut), str):
            setattr(cut, key, old)
            return False
        return True
    q = gridParam(cut.G, wall_hit) if wall_hit is not None else None
    if q is not None:                                               # (None: a wall of degenerate triangles only — the original wrote a null)
        cut.pts[k - 1] = q
        return True
    return False


def surfCutHover(P, cut, m, screen, wall_hit, px=1.0):
    # port of surfCutHover (hair-modeler.clean.html:5479): while placing, the cut follows the cursor; before the start,
    # our leadHover (3.8f). True: drawSurfCut
    if not cut.start:
        return leadHover(P, cut, m, screen, wall_hit, px)
    if cut.end:
        return False
    E = edgePick(P, cut, m, 9 * px, screen, wall_hit, 10 * px)
    q = gridParam(cut.G, wall_hit) if E is None and wall_hit is not None else None
    cut.hover = {'end': E} if E else {'pt': q} if q is not None else None
    return True


def leadHover(P, cut, m, screen, wall_hit, px=1.0):
    """ours (3.8f; the original draws nothing before the first click, surfCutHover 5480 returns): before the start, a
    dot follows the cursor along the nearer of the wall's two curves, where the first click would put the start — the
    same edgePick as surfCutDown's, so the click lands on the dot; out of reach of both curves, none. True: changed.
    The id its virtual anchor took (tmpAnchor, UID++) is given back: the original takes none before the first click,
    so the ids of what the cut makes stay the original's."""
    uid = P.uid
    E = edgePick(P, cut, m, 9 * px, screen, wall_hit, 10 * px)
    P.uid = uid
    old = cut.lead
    cut.lead = E
    if E is None or old is None:
        return E is not old
    return not E['pos'].equals(old['pos'])


def removeLast(cut):
    # Backspace while placing (8061) and the pie's "Remove the last anchor / the start" (7940)
    if cut.pts:
        cut.pts.pop()
    else:
        cut.start = None
        cut.lead = None                                             # (ours: the dot shows again at the next move)


def cutPieItems(cut):
    """port of openCutPie (hair-modeler.clean.html:7935), pure: [('cut', label) | ('info', label), ('remove', label)?,
    ('cancel', label)] — while a wall is being cut the pie holds only the cut"""
    ready = bool(cut.end and cut.build)
    out = [('cut', f'✂ Cut ({len(cut.build["mids"]) + 2} anchors)') if ready else
           ('info', "Start on one of the wall's curves" if not cut.start else "End on the wall's other curve" if not cut.end else cut.err or 'The cut must run along the wall — move its ends')]
    if cut.start and not cut.end:
        out.append(('remove', '↶ Remove the last anchor' if cut.pts else '↶ Remove the start'))
    out.append(('cancel', '✖ Cancel the cut'))
    return out
