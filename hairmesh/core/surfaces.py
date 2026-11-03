# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Walls: one flat (optionally bulged) surface between two curves of a lock, from the lock's row table
(hair-modeler.clean.html:2718-2830, 2861-2865, 8890)."""
import math

from . import model as M
from .crown import connT
from .fdlibm import sin as _sin
from .geom import ease, pointOnCurve, segPos   # noqa: F401 (segPos re-exported: it lives in geom to avoid an import cycle with crown.py)
from .js import at, clamp, includes, js_max, js_or, js_sign, nullish, to_int32, truthy
from .trace import br as _br, dec as _dec
from .vec3 import V3


def wallCrownRoot(P, sf, crowns=None):
    # port of wallCrownRoot (hair-modeler.clean.html:2718): is this wall rooted on a crown's lower connector?
    # (`crowns`: P.crowns() when the caller already has it — a loop over every wall, lot 3.7-estab)
    ca = P.items.get(sf.a); cb = P.items.get(sf.b)
    if not ca or not cb or ca.type != 'curve' or cb.type != 'curve':
        return None
    for k in (P.crowns() if crowns is None else crowns):
        # (exclZone is always null since 10.10: no "Exclude influence" border check)
        low = P.items.get(k.low)
        if not low or not low.arc or len(low.samples) < 2:
            continue

        def end(c):
            for a in (at(c.anchors, 0), at(c.anchors, len(c.anchors) - 1)):
                if a and ((a.att and a.att.c == low.id) or includes(low.anchors, a)):
                    return a
            return None
        ea = end(ca); eb = end(cb)
        if ea and eb and ea is not eb:
            return {'low': low, 'ea': ea, 'eb': eb, 'ua': connT(low, ea), 'ub': connT(low, eb)}
    return None


# One flat wall (optionally slightly bulged) between two curves
def surfaceData(P, sf, T):
    # port of surfaceData (hair-modeler.clean.html:2740)
    S = P.S
    TA = T.get(sf.a); TB = T.get(sf.b)
    if not TA or not TB:
        return None
    A = TA.rows; B = TB.rows; R = len(A); comp = TA.comp
    segs = js_max(1, to_int32(nullish(sf.p.get('segs'), 0))); Mw = segs + 1
    pos = []; nr = []; wd = []; prev = None
    for k in range(R):
        w = B[k].clone().sub(A[k]); wd.append(w.length())
        k0 = max(k - 1, 0); k1 = min(k + 1, R - 1)
        t = A[k1].clone().add(B[k1]).sub(A[k0]).sub(B[k0])
        n = V3().crossVectors(t, w)
        if _dec('surfaceData.nLengthSq', n.lengthSq(), 1e-16) < 1e-16:
            _br('surfaceData.degenerateNormal')
            n = prev.clone() if prev else V3(0, 0, 1)
        n.normalize(); prev = n; nr.append(n)
    # Decide "outward" once, on the widest row, then keep the side continuous along the wall.
    # (Per-row tests flip near the tip where the width collapses and fold the mesh.)
    kr = 0
    for k, w in enumerate(wd):
        if w > wd[kr]:
            kr = k
    midR = A[kr].clone().add(B[kr]).multiplyScalar(0.5)
    out = midR.clone().sub(comp.cent[kr])
    if comp.n <= 2 or _dec('surfaceData.outLengthSq', out.lengthSq(), 1e-8 * S.scale * S.scale) < 1e-8 * S.scale * S.scale:
        _br('surfaceData.outFromCenter')
        out = midR.clone().sub(S.center)
    if _dec('surfaceData.outSide', nr[kr].dot(out), 0) < 0:
        nr[kr].negate()
    for k in range(kr + 1, R):
        if _dec('surfaceData.flipNext', nr[k].dot(nr[k - 1]), 0) < 0:
            nr[k].negate()
    for k in range(kr - 1, -1, -1):
        if _dec('surfaceData.flipPrev', nr[k].dot(nr[k + 1]), 0) < 0:
            nr[k].negate()
    if truthy(sf.p.get('flip')):
        for n in nr:
            n.negate()
    # A wall rooted on a crown's lower connector: its bulge fades out towards the root, and the root row lies ON the
    # connector at the crown's loop positions (no gap between the lock and the crown), blending back to the plain wall
    cr = wallCrownRoot(P, sf)
    fade = lambda k: 1; delta = None
    if cr:
        _br('surfaceData.crownRoot')
        # Bulge fade (p.bfade, share of the lock length, 0.2 by default): over how long the bulge grows from the root
        kRoot = 0 if A[0].distanceTo(cr['ea'].pos) <= A[R - 1].distanceTo(cr['ea'].pos) else R - 1
        Lf = js_max(1, (R - 1) * clamp(nullish(sf.p.get('bfade'), 0.2), 0.02, 1))
        fade = lambda k, kRoot=kRoot, Lf=Lf: ease(clamp(abs(k - kRoot) / Lf, 0, 1))
        if not sf.twin:
            w0 = B[kRoot].clone().sub(A[kRoot])
            delta = []
            for j in range(Mw):
                s = segPos(j / segs, sf.p, segs); p = pointOnCurve(cr['low'], cr['ua'] + (cr['ub'] - cr['ua']) * s)
                delta.append(p.sub(A[kRoot].clone().addScaledVector(w0, s)) if p else V3())
    # A junction wall (Unite locks, p.jfade = share of the length): the bulge fades out towards where the wall closes
    # to zero width (the junction line and the trunk), so it meets the other lock's junction wall without crossing it
    if sf.p.get('jfade') is not None and R > 1:
        _br('surfaceData.jfade')
        mx = js_max(*wd); Lf = js_max(1, (R - 1) * clamp(sf.p['jfade'], 0.02, 1)); dz = [math.inf] * R
        last = -math.inf
        for k in range(R):
            if wd[k] < 0.03 * mx:
                last = k
            dz[k] = k - last
        last = math.inf
        for k in range(R - 1, -1, -1):
            if wd[k] < 0.03 * mx:
                last = k
            dz[k] = min(dz[k], last - k)
        f0 = fade
        fade = lambda k, f0=f0, dz=dz, Lf=Lf: f0(k) * ease(clamp(dz[k] / Lf, 0, 1))
    bulge = nullish(sf.p.get('bulge'), math.nan)
    for k in range(R):
        w = B[k].clone().sub(A[k]); f = fade(k)
        for j in range(Mw):
            s = segPos(j / segs, sf.p, segs)
            q = A[k].clone().addScaledVector(w, s).addScaledVector(nr[k], bulge * f * wd[k] * _sin(math.pi * s))   # Math.sin = fdlibm's (V8)
            if delta:
                q.addScaledVector(delta[j], 1 - f)
            pos.append(q)
    faces = []; vote = 0
    for k in range(R - 1):
        for j in range(segs):
            q = [k * Mw + j, k * Mw + j + 1, (k + 1) * Mw + j + 1, (k + 1) * Mw + j]
            fn = V3().crossVectors(pos[q[2]].clone().sub(pos[q[0]]), pos[q[3]].clone().sub(pos[q[1]]))
            vote += js_sign(fn.dot(nr[k]))
            faces.append(q)
    if _dec('surfaceData.vote', vote, 0) < 0:                       # one consistent outward winding per wall
        for q in faces:
            q.reverse()
    return M.MeshData(pos=pos, faces=faces)


class WeldInfo:
    """`info` of mergeWeld: vsrc = source parts per welded vertex, fsrc = source part per face, vmap = index maps."""
    def __init__(self):
        self.vmap = {}; self.vsrc = {}; self.fsrc = []


def _cell(x, eps):
    # Math.floor(x / eps) as a dict key; eps is never 0 in practice (levelH() * 0.04)
    q = x / eps if eps != 0 else (math.inf if x > 0 else -math.inf if x < 0 else math.nan)
    return math.floor(q) if q == q and q not in (math.inf, -math.inf) else repr(q)


def mergeWeld(datas, eps, info=None):
    # port of mergeWeld (hair-modeler.clean.html:2804): weld vertices closer than eps across parts, drop degenerate faces
    pos = []; faces = []; grid = {}

    def find(v):
        cx = _cell(v.x, eps); cy = _cell(v.y, eps); cz = _cell(v.z, eps)
        if isinstance(cx, int) and isinstance(cy, int) and isinstance(cz, int):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        l = grid.get((cx + dx, cy + dy, cz + dz))
                        if l:
                            for i in l:
                                if pos[i].distanceToSquared(v) <= eps * eps:
                                    return i
        else:
            l = grid.get((cx, cy, cz))
            if l:
                for i in l:
                    if pos[i].distanceToSquared(v) <= eps * eps:
                        return i
        i = len(pos); pos.append(v.clone()); k = (cx, cy, cz)
        grid.setdefault(k, []).append(i)
        return i

    for di, d in enumerate(datas):
        mp = {}                                                     # only vertices used by faces survive
        if info is not None:
            info.vmap[di] = mp

        def mv(i, mp=mp, d=d, di=di):
            if i not in mp:
                j = find(d.pos[i]); mp[i] = j
                if info is not None:
                    info.vsrc.setdefault(j, {})[di] = True
            return mp[i]
        for f in d.faces:
            g0 = [mv(i) for i in f]
            g = [x for k, x in enumerate(g0) if x != g0[(k + len(g0) - 1) % len(g0)]]
            if len(set(g)) >= 3 and len(g) == len(set(g)):          # quad or collapsed -> tri
                faces.append(g)
                if info is not None:
                    info.fsrc.append(di)
    return M.MeshData(pos=pos, faces=faces)


def concatMeshes(parts):
    # port of concatMeshes (hair-modeler.clean.html:8890)
    pos = []; faces = []
    for m in parts:
        o = len(pos); pos.extend(m.pos); faces.extend([i + o for i in f] for f in m.faces)
    return M.MeshData(pos=pos, faces=faces)


# Second surface on the same two curves (p.b2on — available while Bulge ≠ 0): a wall back from b to a with its own
# bulge / segments / side, so two curves make a closed (lens or triangular) lock
def hasTwin(sf):
    # port of hasTwin (hair-modeler.clean.html:2861)
    return truthy(sf.p.get('b2on')) and truthy(sf.p.get('bulge'))


def twinWall(sf):
    # port of twinWall (hair-modeler.clean.html:2862)
    p = sf.p
    return M.Surface(id=sf.id, type='surface', name=sf.name, parent=sf.parent, visible=sf.visible, twin=True, a=sf.b, b=sf.a,
                     p={'segs': js_or(p.get('b2segs'), 1), 'bulge': nullish(p.get('b2bulge'), p.get('bulge')),
                        'flip': nullish(p.get('b2flip'), not truthy(p.get('flip'))), 'bfade': p.get('bfade')})


def wallsOf(surfs):
    # port of wallsOf (hair-modeler.clean.html:2864)
    out = []
    for s in surfs:
        out.append(s)
        if hasTwin(s):
            out.append(twinWall(s))
    return out
