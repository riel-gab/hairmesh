# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Small geometric helpers of the original used across the surface / topology / conversion code."""
import math

from .js import clamp, js_pow, js_sign
from .vec3 import V3, Vec3


def resample(pts, n):
    # port of resample (hair-modeler.clean.html:1413): n points evenly spaced along the polyline's arc length
    L = [0]
    for i in range(1, len(pts)):
        L.append(L[i - 1] + pts[i].distanceTo(pts[i - 1]))
    tot = L[len(L) - 1]; out = []; j = 0
    for i in range(n):
        d = tot * i / (n - 1)
        while j < len(pts) - 2 and L[j + 1] < d:
            j += 1
        sl = L[j + 1] - L[j]; t = clamp((d - L[j]) / sl, 0, 1) if sl > 0 else 0
        out.append(pts[j].clone().lerp(pts[j + 1], t))
    return out


def polyLen(P):
    # port of polyLen (hair-modeler.clean.html:2186)
    l = 0
    for i in range(1, len(P)):
        l += P[i].distanceTo(P[i - 1])
    return l


def atF(C, f):
    # port of atF (hair-modeler.clean.html:1599): point at fractional index f of polyline C
    # (scalar form of `clamp` and `lerp`: the same operations, no temporaries; see nearestF)
    i = math.floor(f); n2 = len(C) - 2
    i = 0 if i <= 0 else n2 if i >= n2 else i                       # clamp(i, 0, N - 2)
    t = f - i
    t = 0 if t <= 0 else 1 if t >= 1 else t                         # clamp(t, 0, 1)
    a = C[i]; b = C[i + 1]
    return Vec3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t)   # a.clone().lerp(b, t)


def atF3(C, f):
    """atF as a tuple (x, y, z), the same arithmetic, for hot loops that only read the components."""
    i = math.floor(f); n2 = len(C) - 2
    i = 0 if i <= 0 else n2 if i >= n2 else i
    t = f - i
    t = 0 if t <= 0 else 1 if t >= 1 else t
    a = C[i]; b = C[i + 1]
    return a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t


def ease(s):
    # port of ease (hair-modeler.clean.html:5894): smoothstep
    return s * s * (3 - 2 * s)


def zoneWeight(rad, blend, x):
    # port of zoneWeight (hair-modeler.clean.html:5731): guide weight at distance x, 1 inside, fading over the outer `blend` share
    if rad <= 0 or x >= rad:
        return 0
    x0 = rad * (1 - clamp(blend, 0, 1))
    if x <= x0:
        return 1
    t = (x - x0) / max(rad - x0, 1e-9)
    return 1 - t * t * (3 - 2 * t)


def pointOnCurve(c, t):
    # port of pointOnCurve (hair-modeler.clean.html:1126): point at arc fraction t of a rebuilt curve
    if not c or not c.arc or len(c.samples) < 2:
        return None
    L = c.arc; d = clamp(t, 0, 1) * L[len(L) - 1]
    i = 0
    while i < len(L) - 2 and L[i + 1] < d:
        i += 1
    s = L[i + 1] - L[i]
    return c.samples[i].p.clone().lerp(c.samples[i + 1].p, clamp((d - L[i]) / s, 0, 1) if s > 0 else 0)


CHUNK = 16                                                          # segments per stretch of a polyline index


def polyIndex(P):
    """Boxes of CHUNK-segment stretches of polyline P, for nearestF: a list of (i0, i1, minx, miny, minz, maxx,
    maxy, maxz, mag), the stretch covering segments i0 .. i1 - 1 (points i0 .. i1); mag = the largest coordinate
    magnitude, which sets the absolute part of the skip margin. Pure function of the points: build it once per
    polyline that is queried many times (the resampled curves and spine of a lock)."""
    n = len(P) - 1; out = []
    for i0 in range(0, n, CHUNK):
        i1 = min(i0 + CHUNK, n)
        minx = miny = minz = math.inf; maxx = maxy = maxz = -math.inf
        for k in range(i0, i1 + 1):
            q = P[k]; x = q.x; y = q.y; z = q.z
            if x < minx: minx = x
            if x > maxx: maxx = x
            if y < miny: miny = y
            if y > maxy: maxy = y
            if z < minz: minz = z
            if z > maxz: maxz = z
        mag = max(abs(minx), abs(maxx), abs(miny), abs(maxy), abs(minz), abs(maxz))
        out.append((i0, i1, minx, miny, minz, maxx, maxy, maxz, mag))
    return out


def nearestF(P, p, idx=None):
    # port of nearestF (hair-modeler.clean.html:2209): fractional index of the point of polyline P nearest to p
    #
    # The original's Vector3 chain, written out on scalars (the hottest function of the row tables: ~150 000 calls
    # of 160 segments on the example). Every expression keeps the exact IEEE operations and their order:
    #   ab = P[i+1].clone().sub(P[i])                 -> abx = bx - ax ...
    #   l2 = ab.lengthSq()                            -> abx*abx + aby*aby + abz*abz
    #   s = clamp(p.clone().sub(P[i]).dot(ab) / l2)   -> ((px-ax)*abx + (py-ay)*aby + (pz-az)*abz) / l2, then
    #       Math.max(0, Math.min(1, s)): 0 for s <= 0 (-0 included), 1 for s >= 1, NaN stays NaN
    #   d = P[i].clone().addScaledVector(ab, s).distanceToSquared(p) -> dx = (ax + abx*s) - px; dx*dx + dy*dy + dz*dz
    #
    # With idx (polyIndex(P)) the stretches are visited nearest box first and the scan stops at the first stretch
    # whose box lies farther than the best distance found: no segment there can have a strictly smaller d (its
    # computed d is at least the true box distance up to a few ulps, and the margin below covers those). Since the
    # visiting order changes, the minimum is taken on (d, i): an equal d goes to the smaller index, which is what the
    # original's ascending scan with `d < bd` ends up with. The result is bit-identical either way.
    best = 0; bd = math.inf
    px = p.x; py = p.y; pz = p.z
    if idx is None:
        a = P[0]; ax = a.x; ay = a.y; az = a.z
        for i in range(len(P) - 1):
            b = P[i + 1]; bx = b.x; by = b.y; bz = b.z
            abx = bx - ax; aby = by - ay; abz = bz - az
            l2 = abx * abx + aby * aby + abz * abz
            if l2 > 0:
                s = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / l2
                s = 0 if s <= 0 else 1 if s >= 1 else s
            else:
                s = 0
            dx = (ax + abx * s) - px; dy = (ay + aby * s) - py; dz = (az + abz * s) - pz
            d = dx * dx + dy * dy + dz * dz
            if d < bd:
                bd = d; best = i + s
            ax = bx; ay = by; az = bz
        return best
    order = []
    for ch in idx:
        dx = ch[2] - px if px < ch[2] else px - ch[5] if px > ch[5] else 0   # distance from p to the box, per axis
        dy = ch[3] - py if py < ch[3] else py - ch[6] if py > ch[6] else 0
        dz = ch[4] - pz if pz < ch[4] else pz - ch[7] if pz > ch[7] else 0
        order.append((dx * dx + dy * dy + dz * dz, ch))
    order.sort(key=_boxd)                                            # (stable: equal box distances keep the index order)
    bi = -1
    for boxd2, ch in order:
        lim = math.sqrt(bd) * (1 + 1e-6) + 1e-12 * (1 + ch[8])       # the best distance plus the rounding margin
        if boxd2 > lim * lim:
            break                                                   # (sorted: every stretch after this one is farther)
        i0 = ch[0]; a = P[i0]; ax = a.x; ay = a.y; az = a.z
        for i in range(i0, ch[1]):
            b = P[i + 1]; bx = b.x; by = b.y; bz = b.z
            abx = bx - ax; aby = by - ay; abz = bz - az
            l2 = abx * abx + aby * aby + abz * abz
            if l2 > 0:
                s = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / l2
                s = 0 if s <= 0 else 1 if s >= 1 else s
            else:
                s = 0
            dx = (ax + abx * s) - px; dy = (ay + aby * s) - py; dz = (az + abz * s) - pz
            d = dx * dx + dy * dy + dz * dz
            if d < bd or (d == bd and i < bi):
                bd = d; best = i + s; bi = i
            ax = bx; ay = by; az = bz
    return best


def _boxd(t):
    return t[0]


def polyBox(P, pad, exact=False):
    """Bounding box of polyline P grown by `pad` (a test tolerance): (minx, miny, minz, maxx, maxy, maxz).

    A point outside it is farther than `pad` from every point of P, so a `distance < pad` test on it is false
    without computing nearestF. The box is grown a little more than `pad` (a relative 1e-6 plus 1e-6 absolute) so
    that the floating-point distance (atF, distanceTo: errors of a few ulps) can never fall below `pad` for a point
    the box rejects; the test itself is unchanged for every point the box lets through. exact=True grows it by
    exactly `pad` (min - pad, max + pad): the original's own Box3().setFromPoints(P).expandByScalar(pad)."""
    minx = miny = minz = math.inf; maxx = maxy = maxz = -math.inf
    for q in P:
        x = q.x; y = q.y; z = q.z
        if x < minx: minx = x
        if x > maxx: maxx = x
        if y < miny: miny = y
        if y > maxy: maxy = y
        if z < minz: minz = z
        if z > maxz: maxz = z
    e = pad if exact else pad * (1 + 1e-6) + 1e-6
    return (minx - e, miny - e, minz - e, maxx + e, maxy + e, maxz + e)


def inBox(p, bb):
    return not (p.x < bb[0] or p.y < bb[1] or p.z < bb[2] or p.x > bb[3] or p.y > bb[4] or p.z > bb[5])


def chunkBoxes(idx, pad):
    """The stretch boxes of polyIndex(P), each grown by `pad` as polyBox grows (a relative 1e-6 plus 1e-6 absolute).
    A point outside ALL of them is farther than `pad` from every point of P (every point of a stretch lies in its
    box), so a `atF(P, nearestF(P, p)).distanceTo(p) < pad` test on it is false without computing it — the same
    margin argument as polyBox, for the stretches instead of the whole curve: on a long curve the whole box lets
    most points through, the stretch boxes almost none (lot 4.2c). For a point inside one the test runs unchanged."""
    e = pad * (1 + 1e-6) + 1e-6
    return [(ch[2] - e, ch[3] - e, ch[4] - e, ch[5] + e, ch[6] + e, ch[7] + e) for ch in idx]


def nearBoxes(p, boxes):
    """inBox(p, bb) for some bb of chunkBoxes"""
    px = p.x; py = p.y; pz = p.z
    for bb in boxes:
        if not (px < bb[0] or py < bb[1] or pz < bb[2] or px > bb[3] or py > bb[4] or pz > bb[5]):
            return True
    return False


# Where segment line s (j / segs, 0 … 1 across the wall) goes: p.segSpread (−1 … 1, with 2+ inner lines) pulls the lines
# to the middle (−) or pushes them to the edges (+) symmetrically; then p.segOff (−1 … 1) shifts them all towards curve A
# (−) or B (+) proportionally (a smooth map keeping both edges: the middle goes to 0.5 ± 40%).
def segPos(s, p, segs):
    # port of segPos (hair-modeler.clean.html:2732)
    from .js import js_or
    if segs < 2 or s <= 0 or s >= 1:
        return s
    sp = clamp(js_or(p.get('segSpread'), 0), -1, 1) if segs >= 3 else 0
    off = clamp(js_or(p.get('segOff'), 0), -1, 1)
    if sp:
        u = s * 2 - 1; g = js_pow(2, -sp * 1.5)
        s = (js_sign(u) * js_pow(abs(u), g) + 1) / 2
    if off:
        m = 0.5 + 0.4 * off; k = m / (1 - m)
        s = k * s / (1 + (k - 1) * s)
    return s


def faceNormal(pos, f):
    # port of faceNormal (hair-modeler.clean.html:8472): Newell normal (not normalised)
    n = V3()
    for i in range(len(f)):
        a = pos[f[i]]; b = pos[f[(i + 1) % len(f)]]
        n.x += (a.y - b.y) * (a.z + b.z); n.y += (a.z - b.z) * (a.x + b.x); n.z += (a.x - b.x) * (a.y + b.y)
    return n


def faceCenter(pos, f):
    # port of faceCenter (hair-modeler.clean.html:8477)
    c = V3()
    for i in f:
        c.add(pos[i])
    return c.divideScalar(len(f))


__all__ = ['resample', 'polyLen', 'atF', 'atF3', 'nearestF', 'polyIndex', 'polyBox', 'inBox', 'chunkBoxes', 'nearBoxes', 'ease', 'zoneWeight', 'pointOnCurve', 'faceNormal', 'faceCenter', 'js_sign']
