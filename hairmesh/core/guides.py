# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Guides at run time (hair-modeler.clean.html:1058-1178, 2224-2278): a guide line is stored as hits on curves
(curve id, arc fraction); a NURBS disc is two closed ring curves, and its hits are found every frame by casting the
hair curves' sample segments against the annulus (rebuildDisc). lockGuideRows turns the hits on a lock's curves
into forced loops for buildRowTables. Drawing guides is a tool (phase 4); nothing of that is here."""
import math

import numpy as np

from .fdlibm import atan2 as _atan2, cos as _cos, sin as _sin
from .geom import atF, nearestF, pointOnCurve, resample
from .headobj import float32
from .js import at, clamp, includes, js_max, js_min, js_or, nullish
from .raycast import DoubleSide, Raycaster, TriMesh
from .rowbase import rowPlane
from .trace import br as _br, dec as _dec, dec_near
from .vec3 import V3


def guideOn(P, g):
    # port of guideOn (hair-modeler.clean.html:721): off (⏻) on the guide or a group above it; hiding doesn't matter
    if g.off:
        return False
    p = P.parentOf(g)
    while p:
        if getattr(p, 'guidesOff', False):
            return False
        p = P.parentOf(p)
    return True


def hairCurves(P):
    # port of hairCurves (1024)
    return [c for c in P.curves() if not c.ring]


def guideExcludes(P, g, c):
    # port of guideExcludes (hair-modeler.clean.html:2224)
    ex = g.p.get('excl') if g.p else None
    if not ex or not c:
        return False
    inList = False
    p = P.parentOf(c)
    while p:
        if p.id in ex:
            inList = True; break
        p = P.parentOf(p)
    return (not inList) if g.p.get('exclMode') == 'only' else inList


def guideCurves(P, g):
    """The ids of the hair curves a guide shapes (the locks it touches): a line's hits, a disc's hitPts (the curves
    that pierce it, set by rebuildDisc; None before it was built: every hair curve then). For the live preview's
    partial rebuild when a guide is switched (16/10/2026): the loops of these curves' locks follow the guide."""
    if g.kind == 'disc':
        if g.hitPts is None:
            return [c.id for c in hairCurves(P)]
        return list(g.hitPts)
    return [h['c'] for h in g.hits if h.get('c') in P.items]


def rebuildGuide(P, g):
    # port of rebuildGuide (hair-modeler.clean.html:1133), minus the line geometry
    g.dirty = False
    if g.kind == 'disc':
        rebuildDisc(P, g); return
    pts = [q for q in (pointOnCurve(P.items.get(h['c']), h['t']) for h in g.hits) if q is not None]
    g.samples = [{'p': p} for p in pts]


# ---- NURBS disc: an annulus between an outer and an inner ring (closed curves with 6 anchors each) ----
def discBasis(N):
    # port of discBasis (hair-modeler.clean.html:1068)
    u = V3(1, 0, 0) if abs(N.y) > 0.9 else V3(0, 1, 0)
    u.addScaledVector(N, -u.dot(N)).normalize()
    return [u, V3().crossVectors(N, u)]


def discRings(P, g):
    # port of discRings (1083)
    return [c for c in (P.items.get(i) for i in (g.rings or [])) if c]


def discFrame(P, g):
    # port of discFrame (hair-modeler.clean.html:1084): centre and normal from the ring anchors
    rs = discRings(P, g); all_ = [a.pos for c in rs for a in c.anchors]
    C = V3()
    for p in all_:
        C.add(p)
    C.divideScalar(max(len(all_), 1))
    o = [a.pos for a in rs[0].anchors] if rs else []
    N = V3()
    for i in range(len(o)):
        a = o[i]; b = o[(i + 1) % len(o)]
        N.x += (a.y - b.y) * (a.z + b.z); N.y += (a.z - b.z) * (a.x + b.x); N.z += (a.x - b.x) * (a.y + b.y)
    if N.lengthSq() < 1e-16:
        N.set(0, 1, 0)
    return {'C': C, 'N': N.normalize()}


_discRay = Raycaster()                                              # the module-level `discRay` of the original


# Disc: rebuild the ring surface, then find where the hair curves pierce it (first crossing from the root).
# Those hits make it a guide line for every lock it cuts; the cut across each wall is highlighted green.
def rebuildDisc(P, g):
    # port of rebuildDisc (hair-modeler.clean.html:1140)
    S = P.S
    rs = discRings(P, g)
    if len(rs) < 2 or any(len(c.samples) < 4 for c in rs):
        return
    NR = 96
    A = resample([s.p for s in rs[0].samples], NR + 1); B = resample([s.p for s in rs[1].samples], NR + 1)
    pos = []; idx = []
    for k in range(NR + 1):
        pos.append(A[k]); pos.append(B[k])
    for k in range(NR):
        a = 2 * k; idx.extend((a, a + 2, a + 1, a + 1, a + 2, a + 3))
    # Float32BufferAttribute: the geometry keeps float32 positions; the raycast reads them back as doubles
    mesh = TriMesh([V3(float32(p.x), float32(p.y), float32(p.z)) for p in pos], idx, DoubleSide)
    mesh.computeBoundingSphere(); mesh.computeBoundingBox()
    fr = discFrame(P, g); C = fr['C']; N = fr['N']; g.center = C; g.nrm = N
    box = mesh.boundingBox.clone().expandByScalar(S.scale * 1e-3); hits = []; hp = {}
    for c in hairCurves(P):
        s = c.samples
        if len(s) < 2 or not c.arc:
            continue
        # (speed, ours, 16/10/2026: the original's box test of every segment — "max(a, b) < min of the box or
        # min(a, b) > max of the box" on each axis — run on arrays of the samples (the very same comparisons on the
        # very same doubles, so the same segments pass), the ray cast then only on the segments that pass, in the
        # original's order, the first hit ending the curve as before; the arrays are cached per samples list. The
        # disc of the example cost 0.18 s at every rebuild of the hair: ~10 ms now.)
        bc = c.box_cache
        if bc is None or bc[0] is not s:
            arr = np.array([(q.p.x, q.p.y, q.p.z) for q in s], dtype=np.float64)
            bc = c.box_cache = (s, arr, np.maximum(arr[:-1], arr[1:]), np.minimum(arr[:-1], arr[1:]))
        mx = bc[2]; mn = bc[3]
        skip = ((mx[:, 0] < box.min.x) | (mn[:, 0] > box.max.x) | (mx[:, 1] < box.min.y) | (mn[:, 1] > box.max.y)
                | (mx[:, 2] < box.min.z) | (mn[:, 2] > box.max.z))
        for i in np.flatnonzero(~skip).tolist():
            a = s[i].p; b = s[i + 1].p
            # (speed, ours: the ray stops at b, so only the triangles whose box touches the segment's box, widened a
            # hair for the rounding of origin + dir * t, can be hit: TriMesh.triangles_touching)
            eps = 1e-9 * (1.0 + max(abs(a.x), abs(a.y), abs(a.z), abs(b.x), abs(b.y), abs(b.z)))
            seg_box = ((min(a.x, b.x) - eps, min(a.y, b.y) - eps, min(a.z, b.z) - eps), (max(a.x, b.x) + eps, max(a.y, b.y) + eps, max(a.z, b.z) + eps))
            d = b.clone().sub(a); ln = d.length()
            if dec_near('rebuildDisc.segLen', ln, 1e-12) < 1e-12:
                continue
            _discRay.set(a, d.divideScalar(ln)); _discRay.far = ln
            found = _discRay.intersectObject(mesh, seg_box)
            if found:
                h = found[0]; L = c.arc
                hits.append({'c': c.id, 't': (L[i] + h['distance']) / js_or(L[len(L) - 1], 1)})
                hp[c.id] = h['point'].clone()
                _br('rebuildDisc.hit')
                break
    u, v = discBasis(N)

    def ang(cid):
        d = hp[cid].clone().sub(C)
        return _atan2(d.dot(v), d.dot(u))
    angles = {h['c']: ang(h['c']) for h in hits}
    hits.sort(key=lambda h: angles[h['c']])
    for k in range(1, len(hits)):                                   # a 1-ulp difference in atan2 could swap two hits
        _dec('rebuildDisc.angleGap', angles[hits[k]['c']] - angles[hits[k - 1]['c']], 0)
    g.hits = hits; g.hitPts = hp
    # cut lines: across every wall whose two curves both pierce the disc
    g.segCount = sum(1 for sf in P.surfaces() if sf.a in hp and sf.b in hp)
    g.samples = []


# best-fit plane of points around their centre c: n — the axis of least spread (Jacobi eigen-solve of the 3×3
# covariance); flat — the points lie (almost) on a line, the plane is undefined
def planeFit(pts, c):
    # port of planeFit (hair-modeler.clean.html:2231)
    A = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    for p in pts:
        d = [p.x - c.x, p.y - c.y, p.z - c.z]
        for i in range(3):
            for j in range(3):
                A[i][j] += d[i] * d[j]
    V = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for sweep in range(20):
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if _dec('planeFit.offDiag', abs(A[p][q]), 1e-14) < 1e-14:
                continue
            th = 0.5 * _atan2(2 * A[p][q], A[q][q] - A[p][p]); cs = _cos(th); sn = _sin(th)
            for k in range(3):
                a = A[k][p]; b = A[k][q]; A[k][p] = cs * a - sn * b; A[k][q] = sn * a + cs * b
            for k in range(3):
                a = A[p][k]; b = A[q][k]; A[p][k] = cs * a - sn * b; A[q][k] = sn * a + cs * b
            for k in range(3):
                a = V[k][p]; b = V[k][q]; V[k][p] = cs * a - sn * b; V[k][q] = sn * a + cs * b
    ev = [A[i][i] for i in range(3)]; order = sorted([0, 1, 2], key=lambda i: ev[i])
    k = order[0]; n = V3(V[0][k], V[1][k], V[2][k]).normalize()
    flat = _dec('planeFit.flat', ev[order[1]], 0.02 * ev[order[2]]) < 0.02 * ev[order[2]]
    return {'n': n, 'flat': flat}


# Guide lines crossing a lock → forced loops. Curves the guide crosses get the loop exactly at the hit;
# the other curves of the lock are cut by a plane through the hits (normal ⟂ the guide, close to the lock's).
def lockGuideRows(P, CP, L):
    # port of lockGuideRows (hair-modeler.clean.html:2246); L = the lock descriptor (cs, Ps, sp), CP = its conv
    out = []
    for gd in P.guides():
        if not guideOn(P, gd):
            continue                                                # switched off (⏻)
        fixed = {}; pts = []
        for h in gd.hits:
            ci = next((k for k, c in enumerate(L.cs) if c.id == h['c']), -1)
            if ci < 0:
                continue
            if guideExcludes(P, gd, L.cs[ci]):
                continue
            p = pointOnCurve(L.cs[ci], h['t'])
            if p is None:
                continue
            fixed[ci] = nearestF(L.Ps[ci], p); pts.append(p)
        if not fixed:
            continue
        f = 0
        for v in fixed.values():
            f += v
        f = f / len(fixed)
        base = rowPlane(CP, L.sp, f)
        if not base:
            continue
        c = V3()
        for p in pts:
            c.add(p)
        c.divideScalar(len(pts))
        n = base['n'].clone(); fit = None
        if gd.kind == 'disc' and gd.nrm:
            _br('lockGuideRows.discNormal')
            n = gd.nrm.clone()                                      # a disc cuts the lock along its own plane
        elif len(pts) >= 3 and not (fit := planeFit(pts, c))['flat']:
            _br('lockGuideRows.planeFit')
            n = fit['n'].negate() if fit['n'].dot(base['n']) < 0 else fit['n']
        elif len(pts) >= 2:                                         # hits (almost) in a row: a flat lock
            _br('lockGuideRows.chord')
            u = pts[len(pts) - 1].clone().sub(pts[0])
            if u.lengthSq() > 1e-12:
                u.normalize(); n.addScaledVector(u, -n.dot(u))
                if n.lengthSq() < 1e-8:
                    n = base['n'].clone()
        out.append({'f': f, 'plane': {'p': c, 'n': n.normalize()}, 'fixed': fixed, 'dens': gd.p.get('density'), 'rad': gd.p.get('radius') * P.S.headH,
                    'blend': nullish(gd.p.get('blend'), 1), 'local': gd.p.get('local') is not False, 'prio': js_or(gd.p.get('priority'), 0), 'gid': gd.id})
    return out
