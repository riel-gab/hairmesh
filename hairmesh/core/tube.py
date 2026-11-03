# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The Profile modifier (item type 'tube'), the core of step 4.5: a cross-section profile swept along its source curve
(three profiles since 1.0.1: start, middle, end), the automatic radius, the display data of the modifier, and Convert
to curves & walls (curves `para` with their cross-sections `sec`, glued profile points forking in a Y). Pure Python,
no bpy; selection, toasts, undo steps (commit) and display objects are the caller's.

  BUILTIN_PROFILES (6358)       the presets
  ease / tri (6373-6374)        start -> middle -> end, smooth in between
  tubeRadius (6376)             the radius at t, with the tip collapsing
  framesAlong (6385)            parallel-transport frames (THREE.Quaternion.setFromUnitVectors: mat4.Quat)
  sweepFrames (6384)            the frames of NR points evenly along a curve
  tubeProfs / profAt / ownProfs (6407-6416)   the three profiles, the profile at t, the profiles made editable
  tubeRings (6417)              rings[i][j]: profile point j on ring i
  autoTubeRadius (6652)         half the typical wall width (the median), 2 %-20 % of the curve length
  ensureTubeRef (6665)          the automatic radius once for modifiers made before rRef
  rebuildTube (6673)            the data of the modifier's display (no three.js mesh: rings, quads, edge lines)
  modTargets / applyModifier (6699-6703)   the curves Apply works on; Apply (new modifiers, or a new profile)
  convertTube (6758)            Convert to curves & walls, with glued points (Y) and the collapsed tip
  tubeConv / tubeRows (ours, lot 4.7b item 4)   the conversion settings the lock will get (the modifier's own loop
                                step, else the group's) and the loops it will have: what the live preview shows
  samePt / gluedRun (6816-6818) points glued in a profile (a run of neighbours at the very same place)

The Extra modifiers (item type 'xmod', step 4.7: the core; 4.7b: the weight brush's dab):
  MASK_N / XKIND / PAINTED / SIGNED (6431-6434), TWIST_CW (6581)
  extrasOf / activeExtras / curlTurns (6435-6437)   the extras of a modifier, the turns of a curl
  extraRings (6438)             more rings when the lock curls or waves a lot (at most 900)
  maskAt / onlyList / extraWeight (6443-6457)   the painted weights at t, "Affects only", how much of an extra works at t
  four / rootIn (6459-6463)     a value through 4 control values; the root stays where it is
  applyExtras (6464)            noise, curls (smooth the path, shrink on bends), painted thickness and twist
  thinCrowded (6533)            "Thin where crowded" (O(n²) in the rings: the radii precomputed, the distance in scalars)
  newExtra / modTubes / addExtra (6549-6573)   the Extra tab's "+ <kind>" (the modifiers' part; imported meshes: no)
  xmodProps (7039)              what the extra's properties panel writes into the model when it renders
  maskOp (7089, 7097, 7106)     the panel's Clear (every painted extra) / Fill / Invert (the Mask's)
  paintMask (6618)              one dab of the weight brush on the modifiers' Mask / Thickness / Twist weights (4.7b)
  rebuildTube's colours (6689-6703)   the painted weights on the display (TubeMesh.colors)

Every formula keeps the original's operation order (docs/js-para-python.md): transcendentals by core/fdlibm, `**`
never, the zeros of three.js as float +0, `%` as math.fmod (the sign of the dividend, as JS). Line numbers refer to
reference/hair-modeler.clean.html, 1.0.1."""
import math
import re
from dataclasses import dataclass, field

from . import fdlibm
from .edit import addItem, deleteItem, makeCurve, newAnchor
from .brush import brushWeight
from .geom import pointOnCurve, polyLen, resample
from .groups import convTargets, projectStep
from .js import INF, NAN, clamp, index_of, js_hypot, js_max, js_min, js_or, js_pow, js_round, nullish, truthy
from .mat4 import Quat, applyQuaternion
from .vec3 import V3, Vec3
from .walls import makeSurface

TUBE_NR = 96                                                        # rings of the modifier's display (6672)
CONVERT_NR = 200                                                    # rings Convert reads the edges from (6761)
DEG2RAD = math.pi / 180                                             # THREE.MathUtils.DEG2RAD (Math.PI / 180)


def degToRad(degrees):
    # port of THREE.MathUtils.degToRad (three/src/math/MathUtils.js r160): degrees * DEG2RAD
    return degrees * DEG2RAD


def _num(p, k):
    """`p.k` as a JS expression reads it in arithmetic: a missing key is undefined (NaN), null is 0"""
    if k not in p:
        return NAN
    v = p[k]
    return 0 if v is None else v


# port of BUILTIN_PROFILES (hair-modeler.clean.html:6358): profile points in [-1, 1]²: x — across the lock, y — outwards
# from the model's vertical axis
BUILTIN_PROFILES = [
    ['Quad', [[-1, -0.35], [1, -0.35], [1, 0.35], [-1, 0.35]]],
    ['Trapezoid', [[-1, -0.35], [1, -0.35], [0.55, 0.35], [-0.55, 0.35]]],
    ['Triangle', [[-1, -0.35], [1, -0.35], [0, 0.5]]],
    ['Circle', [[fdlibm.cos(k * math.pi / 4 - math.pi / 2), fdlibm.sin(k * math.pi / 4 - math.pi / 2)] for k in range(8)]],
    ['Half circle', [[fdlibm.cos(k * math.pi / 5), fdlibm.sin(k * math.pi / 5) * 0.8 - 0.35] for k in range(6)]],
]


def builtinProfile(name):
    """the built-in part of profileByName (6367): a copy of the preset's points, or None (the user's profiles live in
    the add-on preferences, step 4.6)"""
    b = next((q for q in BUILTIN_PROFILES if q[0] == name), None)
    return [list(q) for q in b[1]] if b is not None else None


# ======================= radius, rotation and frames along the curve (6371-6400) =======================
# radius and rotation: start → middle → end, smooth in between; with the tip collapsing, the last `tipLen` share
# of the length narrows to a point

def ease(s):
    # port of ease (6373): s * s * (3 - 2 * s)
    return s * s * (3 - 2 * s)


def tri(a, b, c, t):
    # port of tri (6374)
    return a + (b - a) * ease(t * 2) if t < 0.5 else b + (c - b) * ease(t * 2 - 1)


def tubeRadius(p, t):
    # port of tubeRadius (hair-modeler.clean.html:6376)
    r = tri(_num(p, 'r0'), _num(p, 'r1'), _num(p, 'r2'), t)
    L = clamp(nullish(p.get('tipLen'), 0.25), 0.01, 1)
    if truthy(p.get('collapse')) and t > 1 - L:
        u = (1 - t) / L
        r *= u * (2 - u)
    return NAN if r != r else r if r > 0 else 0.0                   # Math.max(r, 0): +0 beats -0


@dataclass(eq=False, slots=True)
class Frame:
    """one frame of framesAlong: { p, T, N, B }"""
    p: Vec3
    T: Vec3
    N: Vec3
    B: Vec3


def _perp(v, t):
    # perp (6387): v.clone().addScaledVector(t, -v.dot(t))
    return v.clone().addScaledVector(t, -v.dot(t))


def framesAlong(P, pts):
    """port of framesAlong (hair-modeler.clean.html:6385): frames along the polyline `pts`. The start normal points
    outwards from the model's vertical axis (through S.center: P.S.center), up if the curve runs along that direction;
    then it is carried along the curve without twisting (parallel transport: THREE.Quaternion.setFromUnitVectors of
    one tangent to the next). B = N × T."""
    NR = len(pts)
    T = []
    for i in range(NR):
        t = pts[min(i + 1, NR - 1)].clone().sub(pts[max(i - 1, 0)])
        T.append(t.normalize() if t.lengthSq() > 1e-20 else V3(0.0, -1.0, 0.0))
    ctr = P.S.center
    h = V3(pts[0].x - ctr.x, 0.0, pts[0].z - ctr.z)
    N = _perp(h, T[0])
    if h.lengthSq() < 1e-12 or N.lengthSq() < 0.09 * h.lengthSq():
        N = _perp(V3(0.0, 1.0, 0.0), T[0])
    if N.lengthSq() < 1e-12:
        N = _perp(V3(1.0, 0.0, 0.0), T[0])
    N.normalize()
    out = []
    for i, p in enumerate(pts):
        if i:
            applyQuaternion(N, Quat().setFromUnitVectors(T[i - 1], T[i]))
            N = _perp(N, T[i])
            if N.lengthSq() < 1e-12:
                N = _perp(V3(0.0, 1.0, 0.0), T[i])
            N.normalize()
        out.append(Frame(p, T[i], N.clone(), V3().crossVectors(N, T[i]).normalize()))
    return out


def sweepFrames(P, c, NR):
    # port of sweepFrames (6384): the frames of NR points evenly along the curve's samples
    return framesAlong(P, resample([s.p for s in c.samples], NR))


# ======================= the three profiles (6400-6416) =======================
# The profile modifier has three profiles — start, middle, end (it.profs; older saves and new modifiers: one, it.profile,
# for all three). They always have the same number of points; along the lock each point goes from one profile to the
# next like the radius does (tri). Points glued in a profile (at the same place) are one there: where they come apart
# along the lock, the lock forks (Convert makes a Y of the edge curves there).

def _profsOk(it):
    return it.profs is not None and len(it.profs) == 3 and all(len(Q) == len(it.profs[0]) for Q in it.profs)


def tubeProfs(it):
    # port of tubeProfs (6407)
    return it.profs if _profsOk(it) else [it.profile, it.profile, it.profile]


def profAt(it, t):
    # port of profAt (hair-modeler.clean.html:6408): the profile at t (0 root … 1 tip)
    A, B, C = tubeProfs(it)
    return [[tri(q[0], B[j][0], C[j][0], t), tri(q[1], B[j][1], C[j][1], t)] for j, q in enumerate(A)]


def ownProfs(it):
    # port of ownProfs (6413): the three profiles as their own arrays, ready to edit (it.profile stays the start one)
    if not _profsOk(it):
        it.profs = [[list(q) for q in it.profile] for _k in range(3)]
    it.profile = it.profs[0]
    return it.profs


# ======================= rings (6417) and the Extra modifiers (6430-6600) =======================
# Extra modifiers (item type 'xmod', kind mask / thick / twist / noise / curl) hang on a profile modifier (x.tube).
# Extras painted along the lock with the weight brush: Mask 0…1; Thickness and Twist −1…1 (x.mask, MASK_N weights).

MASK_N = 96                                                         # (6431)
XKIND = {'mask': ['Mask', '◐'], 'thick': ['Thickness', '⬤'], 'twist': ['Twist', '⟳'], 'noise': ['Noise', '≋'],
         'curl': ['Curls', '➰'], 'xform': ['Transform', '✥']}      # (6432)
PAINTED = ['mask', 'thick', 'twist']                                # (6434)
MASK_BRUSH = {'strength': 1, 'intensity': 0, 'twist': 0.5, 'mode': 'add'}   # S.maskBrush's settings (6578): twist −1 ↺ … +1 ↻
# the sign that makes a positive twist clockwise seen from the root along the lock (tubeRings turns the profile from N
# towards B — anticlockwise seen that way) (6581)
TWIST_CW = -1


def SIGNED(k):
    # (6434): Thickness and Twist are painted −1…1
    return k == 'thick' or k == 'twist'


def extrasOf(P, t):
    # port of extrasOf (6435): the Extra modifiers hanging on the profile modifier t (xmods() order)
    return [x for x in P.of_type('xmod') if x.tube == t.id]


def activeExtras(P, t):
    # port of activeExtras (6436)
    return [x for x in extrasOf(P, t) if x.visible is not False]


def curlTurns(x):
    # port of curlTurns (6437): turns over the lock (one value per quarter)
    s = 0
    for k in range(4):
        s = s + nullish(x.p.get('d' + str(k)), 1)
    return s


def extraRings(it, ex, NR):
    # port of extraRings (hair-modeler.clean.html:6438): more rings when the lock curls or waves a lot
    n = NR
    for x in ex:
        if x.kind == 'curl':
            n = js_max(n, curlTurns(x) * 24 + 24)
        if x.kind == 'noise':
            n = js_max(n, js_or(x.p.get('freq'), 1) * 16 + 24)
    return js_min(js_round(n), 900)


def _mv(m, i):
    """m[i] as JS arithmetic reads it: undefined (out of range) is NaN, null is 0"""
    if 0 <= i < len(m):
        v = m[i]
        return 0 if v is None else v
    return NAN


def maskAt(m, t):
    # port of maskAt (6443): the painted weight at t (0 root … 1 tip), linear between the MASK_N weights
    if m is None:                                                   # (!m: an empty array is truthy in JS)
        return 0
    x = clamp(t, 0, 1) * (len(m) - 1); i = min(math.floor(x), len(m) - 2)
    a = _mv(m, i)
    return a + (_mv(m, i + 1) - a) * (x - i)


def onlyList(P, m):
    # port of onlyList (6445): the extras a mask is limited to ("Affects only"; older saves keep one id)
    o = m.p.get('only')
    ids = o if isinstance(o, list) else [o] if o is not None else []
    return [i for i in ids if i in P.items]


def extraWeight(P, x, t, ex):
    """port of extraWeight (hair-modeler.clean.html:6447): how much of extra x works at t — every mask of the same
    modifier that applies to x lowers it (100% mask → nothing)"""
    w = 1
    for m in ex:
        if m.kind != 'mask' or m is x:
            continue
        only = onlyList(P, m)
        applies = not only or ((x.id not in only) if truthy(m.p.get('ignore')) else (x.id in only))
        if applies:
            w *= 1 - maskAt(m.mask, t)
    return w


def four(x, key, t):
    """port of four (6459): a smooth value through 4 control values at the root (k = 0), 1/3, 2/3 and the tip (k = 3)
    of the lock; the coil radius 'r' falls back to the single radius of older saves"""
    p = x.p

    def v(k):
        k = 0 if k < 0 else 3 if k > 3 else k                       # clamp(k, 0, 3) (k is an integer here)
        val = p.get(key + str(k))
        if val is None:                                             # (??)
            return js_or(p.get('radius'), 0) if key == 'r' else 1
        return val
    u = clamp(t, 0, 1) * 3; k = min(math.floor(u), 2); s = ease(u - k)
    vk = v(k)
    return vk + (v(k + 1) - vk) * s


def rootIn(t):
    # port of rootIn (6463): the root stays where it is
    return ease(clamp(t / 0.08, 0, 1))


def _gt0(v):
    """`v > 0` with a value of the file (undefined and null are not)"""
    return v is not None and v > 0


def applyExtras(P, it, ex, F):
    """port of applyExtras (hair-modeler.clean.html:6464): the Extra modifiers on the frames F of the source curve —
    Noise and Curls displace the centre line (Mask limits them), Curls, Thickness and Twist scale / turn the profile.
    Returns (the frames along the displaced line, twist[i] in radians, thick[i] the radius factor)."""
    n = len(F)
    off = [V3() for _f in F]; twist = [0] * n; thick = [1] * n
    for x in ex:
        if x.kind == 'noise':
            p = x.p
            seed = nullish(p.get('seed'), 1) * 12.9898
            # ph(k) (a pure function of k): the JS `%` keeps the dividend's sign, as math.fmod (exact) does
            ph = [math.fmod(fdlibm.sin(seed * (k + 1) * 78.233) * 43758.5453, 2 * math.pi) for k in range(4)]
            amp = js_or(p.get('amp'), 0)
            for i in range(n):
                t = i / (n - 1); w = extraWeight(P, x, t, ex) * rootIn(t) * amp
                if not w or w != w:                                 # (!w: 0, -0, NaN)
                    continue
                a = degToRad(tri(js_or(p.get('rot0'), 0), js_or(p.get('rot1'), 0), js_or(p.get('rot2'), 0), t)); f = F[i]
                ca = fdlibm.cos(a); sa = fdlibm.sin(a)
                U = f.N.clone().multiplyScalar(ca).addScaledVector(f.B, sa)
                Vv = f.B.clone().multiplyScalar(ca).addScaledVector(f.N, -sa)
                q = 2 * math.pi * js_or(p.get('freq'), 1) * t
                # Pure sine (p.pure 0…1): from the irregular mix of waves to one clean sine (the second direction a
                # quarter turn behind — a clean spiral when both spreads are on)
                k1 = clamp(js_or(p.get('pure'), 0), 0, 1)
                sq = fdlibm.sin(q + ph[0])
                s1 = (0.75 * sq + 0.25 * fdlibm.sin(2.13 * q + ph[1])) * (1 - k1) + sq * k1
                s2 = (0.75 * fdlibm.sin(1.07 * q + ph[2]) + 0.25 * fdlibm.sin(2.71 * q + ph[3])) * (1 - k1) + fdlibm.cos(q + ph[0]) * k1
                off[i].addScaledVector(U, w * nullish(p.get('sx'), 1) * s1)
                if not truthy(p.get('plane')):
                    off[i].addScaledVector(Vv, w * nullish(p.get('sy'), 1) * s2)
        if x.kind == 'curl':
            p = x.p; th = degToRad(js_or(p.get('phase'), 0))
            # anti-clipping 1 ("Smooth the path"): the spiral winds around a smoothed path — on tight bends of a curved
            # lock the coils no longer cut into each other (the root stays in place)
            G = F
            sm = clamp(js_or(p.get('smooth'), 0), 0, 1)
            if sm > 0:
                Q = [f.p.clone() for f in F]; h = max(1, js_round(sm * n * 0.12))
                for _pass in range(3):
                    pre = [V3()]
                    for q in Q:
                        pre.append(pre[len(pre) - 1].clone().add(q))
                    Q = [pre[min(n - 1, i + h) + 1].clone().sub(pre[max(0, i - h)]).divideScalar(min(n - 1, i + h) - max(0, i - h) + 1) for i in range(n)]
                G = framesAlong(P, [F[i].p.clone().lerp(q, rootIn(i / (n - 1))) for i, q in enumerate(Q)])
            # anti-clipping 2 ("Shrink on bends"): where the path bends tighter than ~3 coil radii, the radius smoothly
            # goes down
            shr = clamp(js_or(p.get('shrink'), 0), 0, 1); fac = [1] * n
            if shr > 0:
                k = max(1, js_round(n * 0.02))
                for i in range(n):
                    a = max(0, i - k); b = min(n - 1, i + k); ang = fdlibm.acos(clamp(G[a].T.dot(G[b].T), -1, 1))
                    rho = G[a].p.distanceTo(G[b].p) / js_max(ang, 1e-6); R0 = js_max(four(x, 'r', i / (n - 1)), 1e-9)
                    fac[i] = 1 - shr * (1 - clamp((rho - 0.3 * R0) / (R0 * 2.7), 0.05, 1))   # the inner side of a bend is crowded long before rho < R
                pre = [0]; h2 = 3 * k                               # a smooth change, no steps
                for v in fac:
                    pre.append(pre[len(pre) - 1] + v)
                for i in range(n):
                    a = max(0, i - h2); b = min(n - 1, i + h2); fac[i] = (pre[b + 1] - pre[a]) / (b - a + 1)
            follow = truthy(p.get('follow')); spin = degToRad(js_or(p.get('spin'), 0)); tk = nullish(p.get('thick'), 1)
            for i in range(n):
                t = i / (n - 1); w = extraWeight(P, x, t, ex)
                if i:
                    th += 2 * math.pi * four(x, 'd', t) * 4 / (n - 1) * w   # dN turns per quarter of the lock
                f = G[i]; R = four(x, 'r', t) * fac[i] * w * rootIn(t)
                if G is not F:
                    off[i].addScaledVector(G[i].p.clone().sub(F[i].p), w)
                off[i].addScaledVector(f.N, R * fdlibm.cos(th)).addScaledVector(f.B, R * fdlibm.sin(th))
                twist[i] += (th if follow else 0) + spin * w
                thick[i] *= 1 + (tk - 1) * w
        if x.kind == 'thick' and x.mask is not None:                # painted weights: −1 thinner (to a point) … +1 twice as thick
            for i in range(n):
                t = i / (n - 1); thick[i] *= js_max(0.02, 1 + maskAt(x.mask, t) * extraWeight(P, x, t, ex))
        if x.kind == 'twist' and x.mask is not None:                # painted weights: +1 — the profile turned clockwise by the
            a = degToRad(nullish(x.p.get('deg'), 180)) * TWIST_CW   # angle (seen from the root along the lock), −1 — anticlockwise
            for i in range(n):
                t = i / (n - 1); twist[i] += a * maskAt(x.mask, t) * extraWeight(P, x, t, ex)
    Q = [f.p.clone().add(off[i]) for i, f in enumerate(F)]
    for x in ex:
        if x.kind == 'curl' and _gt0(x.p.get('thin')):
            thinCrowded(P, it, x, Q, thick, ex)
    return framesAlong(P, Q), twist, thick


def thinCrowded(P, it, x, Q, thick, ex):
    """port of thinCrowded (hair-modeler.clean.html:6533). Anti-clipping 3 ("Thin where crowded"): where other parts of
    the lock come closer than its thickness (neighbouring coils, tight bends), the cross-section gets thinner so the
    surfaces just touch. Only the thickness changes (thick, in place) — the path and the frames stay, so the lock
    doesn't turn (unlike Shrink on bends, which moves the path).

    O(n²) in the rings (n up to 900; step 2 from 800). Bit for bit faster (docs/fase-4-desempenho.md, 4.7): r(i) —
    tubeRadius × thick, which the loop never changes — is computed once per ring instead of once per pair, the
    positions are read as scalars and the distance (Vector3.distanceTo: sqrt(dx*dx + dy*dy + dz*dz), dx = Pi - Pj) is
    written out; Math.max / Math.min keep their NaN as the original's."""
    n = len(Q); L = [0]
    for i in range(1, n):
        L.append(L[i - 1] + Q[i].distanceTo(Q[i - 1]))
    p = it.p; R = [tubeRadius(p, i / (n - 1)) * thick[i] for i in range(n)]
    step = max(1, n // 400); f = [1] * n; k = clamp(x.p.get('thin'), 0, 1)
    X = [q.x for q in Q]; Y = [q.y for q in Q]; Z = [q.z for q in Q]
    js = range(0, n, step)
    for i in range(n):
        ri = R[i]
        if not (ri > 0):
            continue
        room = INF; Li = L[i]; xi = X[i]; yi = Y[i]; zi = Z[i]
        for j in js:
            rj = R[j]
            if abs(L[j] - Li) < 3 * (ri if ri >= rj else rj):     # the lock's own neighbourhood (Math.max(ri, rj): rj NaN → NaN)
                continue
            dx = xi - X[j]; dy = yi - Y[j]; dz = zi - Z[j]
            s = ri + rj
            v = math.sqrt(dx * dx + dy * dy + dz * dz) * ri / (s if s >= 1e-12 or s != s else 1e-12)   # the gap shared in proportion
            if v != v:
                room = NAN                                          # (Math.min: NaN sticks)
            elif v < room:
                room = v
        if room < ri:
            g = room / ri
            f[i] = 1 - k * (1 - (g if g >= 0.1 else 0.1)) * extraWeight(P, x, i / (n - 1), ex)
    h = max(1, js_round(n * 0.015)); pre = [0]                      # smooth, no steps
    for v in f:
        pre.append(pre[len(pre) - 1] + v)
    for i in range(n):
        a = max(0, i - h); b = min(n - 1, i + h); thick[i] *= js_min(f[i], (pre[b + 1] - pre[a]) / (b - a + 1))


MEMO_EXTRAS = [True]                                                # (tools/sim_c47_shots.py times the profile editor with it off and on)


def _extras_frames(P, it, c, ex, NR):
    """sweepFrames + applyExtras of tubeRings, kept per modifier and ring count while everything they read stays the
    same (ours, lot 4.7b: the profile editor's drag rebuilt them on every move — ~150 ms with curls — though a profile
    reads neither): the curve's samples (the list itself), the modifier's settings, every active extra's kind,
    settings, weights and eye, the scene's centre and scale. The same objects come back; tubeRings only reads them."""
    import json
    key = (json.dumps(it.p, sort_keys=True, default=str), P.S.center.x, P.S.center.y, P.S.center.z, P.S.scale,
           tuple((x.id, x.kind, json.dumps(x.p, sort_keys=True, default=str), tuple(x.mask) if x.mask is not None else None) for x in ex))
    memo = P._memo.setdefault('extras_frames', {})
    e = memo.get((it.id, NR))
    if MEMO_EXTRAS[0] and e is not None and e[0] is c.samples and e[1] == key:
        return e[2]
    F = sweepFrames(P, c, extraRings(it, ex, NR))
    out = applyExtras(P, it, ex, F)
    memo[(it.id, NR)] = (c.samples, key, out)
    if len(memo) > 64:
        memo.pop(next(iter(memo)))
    return out


def tubeRings(P, it, c, NR):
    """port of tubeRings (hair-modeler.clean.html:6417): rings[i][j] — profile point j on ring i (NR rings evenly along
    the arc length), as Vec3. With Extra modifiers (Noise, Curls; Mask limits them) the centre line is displaced and the
    profile twisted / scaled first; the profile is then carried along the displaced line. More rings are used when the
    lock curls or waves a lot (extraRings). it.cl keeps the (displaced) centre line — the mask brush paints along it."""
    p = it.p
    ex = activeExtras(P, it)
    twist = None; thick = None
    if ex:
        F, twist, thick = _extras_frames(P, it, c, ex, NR)
    else:
        F = sweepFrames(P, c, NR)
    it.cl = [f.p for f in F]
    n1 = len(F)
    rot0 = _num(p, 'rot0'); rot1 = _num(p, 'rot1'); rot2 = _num(p, 'rot2')
    out = []
    for i, f in enumerate(F):
        t = i / (n1 - 1)
        r = tubeRadius(p, t) * (thick[i] if thick is not None else 1)
        a = degToRad(tri(rot0, rot1, rot2, t)) + (twist[i] if twist is not None else 0)
        ca = fdlibm.cos(a); sa = fdlibm.sin(a)
        n = f.N.clone().multiplyScalar(ca).addScaledVector(f.B, sa)
        b = f.B.clone().multiplyScalar(ca).addScaledVector(f.N, -sa)
        out.append([f.p.clone().addScaledVector(b, x * r).addScaledVector(n, y * r) for x, y in profAt(it, t)])
    return out


# ======================= adding extras (6549-6573) and their panel (7039) =======================

def newExtra(P, t, kind):
    """port of newExtra (hair-modeler.clean.html:6549): a new extra of `kind` on the modifier t, with the defaults
    around the modifier's radius; named "<Kind> N (<curve>)" and placed after the modifier and its other extras. Marks
    the modifier dirty. Reads autoTubeRadius when the modifier has no rRef (the caller rebuilds dirty curves first)."""
    r = t.p.get('rRef')                                             # (t.p.rRef || autoTubeRadius(c) || S.scale * 0.01)
    if not truthy(r):
        r = autoTubeRadius(P, P.items.get(t.src))
        if not truthy(r):
            r = P.S.scale * 0.01
    if kind == 'mask':
        p = {'only': None, 'ignore': False}
    elif kind == 'thick':
        p = {}
    elif kind == 'twist':
        p = {'deg': 180}
    elif kind == 'noise':
        p = {'amp': r, 'freq': 3, 'sx': 1, 'sy': 1, 'plane': False, 'rot0': 0, 'rot1': 0, 'rot2': 0, 'seed': 1, 'ampRef': r, 'pure': 0}
    else:
        p = {'r0': r * 1.5, 'r1': r * 1.5, 'r2': r * 1.5, 'r3': r * 1.5, 'radRef': r, 'd0': 1.5, 'd1': 1.5, 'd2': 1.5, 'd3': 1.5,
             'thick': 1, 'spin': 0, 'follow': False, 'phase': 0, 'smooth': 0, 'shrink': 0}
    x = addItem(P, 'xmod', t.parent, {'tube': t.id, 'kind': kind, 'p': p, 'mask': [0] * MASK_N if kind in PAINTED else None})
    m = re.fullmatch(r'Modifier [0-9]+ \((.*)\)', t.name)           # (t.name.replace(/^Modifier \d+ \((.*)\)$/, '$1'))
    x.name = f'{XKIND[kind][0]} {P.counters["xmod"]} ({m.group(1) if m else t.name})'
    arr = P.childrenOf(x.parent)
    arr.remove(x.id)                                                # (indexOf + splice: its one place, at the end)
    after = [index_of(arr, i) for i in [t.id] + [e.id for e in extrasOf(P, t) if e is not x] if i in arr]
    arr.insert((max(after) if after else len(arr) - 1) + 1, x.id)
    t.dirty = True
    P.gen += 1
    return x


def modTubes(P, sel_ids):
    # port of modTubes (6562): the modifiers Extra works on — selected modifiers, modifiers of selected curves, of
    # selected extras (each once, in selection order)
    out = []
    for iid in sel_ids:
        it = P.items.get(iid)
        t = None
        if it is None:
            pass
        elif it.type == 'tube':
            t = it
        elif it.type == 'xmod':
            t = P.items.get(it.tube)
        elif it.type == 'curve':
            t = next((y for y in P.tubes() if y.src == it.id), None)
        if t is not None and t not in out:
            out.append(t)
    return out


def addExtra(P, sel_ids, kind):
    """port of addExtra (hair-modeler.clean.html:6564), the data part: "+ <kind>" of the Extra tab on the modifiers of
    the selection (modTubes). Returns (the extras made, the toast). The caller then does what the original does after:
    S.maskBrush on with this kind for a painted one (the brush, 4.7b), S.asel = [], S.sel = them, onSelChange (the
    panel of the last one: xmodProps, groups.panelsRendered) and commit — only when something was made.
    Imported meshes (modMeshes / newMeshExtra, 3473-3550: Mask and Thickness on an imported mesh) are not in the port."""
    ts = modTubes(P, sel_ids)
    if not ts:
        return [], ('Select a modifier (or its curve) or an imported mesh first' if kind in ('mask', 'thick')
                    else 'Select a modifier (or its curve) — apply a profile in the Profile tab first')
    made = [newExtra(P, t, kind) for t in ts]
    return made, ('Mask added — paint it with the mask brush (Ctrl — erase, Shift — smooth)' if kind == 'mask'
                  else 'Thickness added — set the intensity, then paint (Ctrl — inverted, Ctrl+Alt — erase, Shift — smooth)' if kind == 'thick'
                  else 'Twist added — paint it: ↻ clockwise, Ctrl — ↺ anticlockwise, Ctrl+Alt — erase, Shift — smooth' if kind == 'twist'
                  else f'{XKIND[kind][0]} added — its settings are in the properties panel')


def xmodProps(P, x):
    """What the extra's properties panel (xmodProps, hair-modeler.clean.html:7039) writes into the model when it renders
    (renderProps of a selected extra: groups.panelsRendered): Twist's angle 180, Noise's Pure sine 0, Curls' four coil
    radii (older saves: one radius) and the anti-clipping sliders 0, where missing. An imported mesh's extra
    (meshXmodProps) is not in the port. Returns True when it wrote."""
    if x.mesh is not None:
        return False
    p = x.p; wrote = False
    if x.kind == 'twist':
        if p.get('deg') is None:
            p['deg'] = 180; wrote = True
    elif x.kind == 'noise':
        if p.get('pure') is None:
            p['pure'] = 0; wrote = True
    elif x.kind == 'curl':
        ref = js_or(js_or(js_or(p.get('radRef'), p.get('radius')), p.get('r0')), P.S.scale * 0.01)
        for k in range(4):
            if p.get('r' + str(k)) is None:                         # older saves: one radius
                p['r' + str(k)] = nullish(p.get('radius'), ref * 1.5); wrote = True
        for k in ('smooth', 'shrink', 'thin'):
            if p.get(k) is None:
                p[k] = 0; wrote = True
    return wrote


def maskOp(P, x, op):
    """the Clear / Fill / Invert buttons of the extra's panel (7089 the Mask's three, 7097 Thickness's Clear, 7106
    Twist's Clear): every weight to 0 / to 1 / 1 - w — Fill and Invert are the Mask's alone (False elsewhere); the
    modifier dirty. The caller commits."""
    if x.mask is None or (op in ('fill', 'invert') and x.kind != 'mask'):
        return False
    if op == 'clear':
        x.mask[:] = [0] * len(x.mask)
    elif op == 'fill':
        x.mask[:] = [1] * len(x.mask)
    elif op == 'invert':
        x.mask[:] = [1 - (0 if v is None else v) for v in x.mask]   # (1 - null is 1: a NaN weight saved as null)
    else:
        raise ValueError(op)
    t = P.items.get(x.tube)
    if t is not None:
        t.dirty = True
    return True


def paintMask(P, sel_ids, m, op, amt, mb, R, hard, screen, shown=None, only=None):
    """port of paintMask (hair-modeler.clean.html:6618), the profile modifiers' part (an imported mesh's weights,
    paintMeshWeights, are not in the port): one dab of the weight brush at m = (x, y) pixels. op: 'paint' | 'invert'
    (thickness / twist with Ctrl) | 'erase' | 'smooth' (core/brush.weightOp); amt: how much the dab adds in Add mode
    (brush.FIRST_DAB on the press, brush.dabAmount on a move); mb the brush (S.maskBrush: kind, strength, intensity,
    twist, mode 'add' | 'abs'); R, hard: S.brushR, S.brushHard in the pixels of `screen(p)` (toScreen: (x, y, z), or
    None behind the camera); shown(t): the modifier's display shows (t.obj.visible; default its eye). The weights
    are painted along the source curve (it stays put while they change the shape); with modifiers selected (modTubes)
    only they are. `only` (ours, the list of the Extras): the ids of the Extras to paint, the others of the kind left
    as they are; None, every one, as the original. Returns whether a weight changed (the modifiers painted are dirty)."""
    kind = mb['kind']; lo = -1 if SIGNED(kind) else 0
    sel = modTubes(P, sel_ids)
    lst = [x for x in P.of_type('xmod') if x.kind == kind and x.visible is not False and x.mask is not None and (only is None or x.id in only)]
    changed = False
    target = 0 if op == 'erase' else mb['strength'] if kind == 'mask' else (-1 if op == 'invert' else 1) * (mb['twist'] if kind == 'twist' else mb['intensity'])
    if shown is None:
        from .edit import isVisible
        shown = lambda t: isVisible(P, t)                           # noqa: E731
    for x in lst:
        if x.mesh is not None:
            continue                                                # (an imported mesh's extra: not in the port)
        t = P.items.get(x.tube)
        c = P.items.get(t.src) if t is not None else None
        if t is None or c is None or c.arc is None or len(c.samples) < 2 or len(t.profile) < 3 or not shown(t):
            continue                                                # (!t.obj: rebuildTube made no display)
        if sel and t not in sel:
            continue                                                # with modifiers selected only they are painted
        W = x.mask; src = list(W); ch = False
        for k in range(MASK_N):
            s = screen(pointOnCurve(c, k / (MASK_N - 1)))
            if s is None or s[2] > 1:
                continue
            d = js_hypot(s[0] - m[0], s[1] - m[1])
            if d >= R:
                continue
            bw = brushWeight(d, R, hard); old = W[k]; o = 0 if old is None else old
            if op == 'smooth':
                sm = 0; n = 0
                for j in range(k - 3, k + 4):
                    if 0 <= j < MASK_N:
                        sm = sm + (0 if src[j] is None else src[j]); n += 1
                W[k] = o + (sm / n - o) * bw * 0.5
            elif mb['mode'] == 'abs':
                W[k] = o + (target - o) * bw
            elif op == 'erase':
                W[k] = js_max(0, o - bw * amt) if o > 0 else js_min(0, o + bw * amt)
            else:
                W[k] = clamp(o + target * bw * amt, lo, 1)
            if W[k] != old:
                ch = True
        if ch:
            t.dirty = True; changed = True
    return changed


# ======================= the automatic radius and the display (6650-6698) =======================

def autoTubeRadius(P, c):
    """port of autoTubeRadius (hair-modeler.clean.html:6652). Automatic radius: half the typical wall width of the
    locks already in the scene (the median, so a few odd walls don't matter); without walls — 6% of the curve length.
    Always kept between 2% and 20% of the curve length. Reads the samples as they are (the caller rebuilds dirty
    curves first, as the frame loop would have)."""
    length = c.arc[len(c.arc) - 1] if c.arc else polyLen([s.p for s in c.samples])
    ws = []
    for s in P.surfaces():
        A = P.items.get(s.a); B = P.items.get(s.b)
        if A is None or B is None or len(A.samples) < 2 or len(B.samples) < 2:
            continue
        p = A.samples[len(A.samples) >> 1].p
        d = INF
        for q in B.samples:
            x = q.p.distanceToSquared(p)
            if x != x:
                d = NAN
            elif x < d:                                             # Math.min (NaN sticks: x < NaN is false)
                d = x
        ws.append(math.sqrt(d))
    ws.sort()
    r = ws[len(ws) >> 1] * 0.5 if ws else length * 0.06
    return js_or(clamp(r, length * 0.02, length * 0.2), P.S.scale * 0.01)


def ensureTubeRef(P, it):
    """port of ensureTubeRef (6665): the slider range is built around rRef (the automatic radius), so it sits in the
    middle of the sliders; modifiers made before this existed get the automatic radius once. Returns True when it
    wrote it.p (ours: a model change the next operator saves)."""
    if truthy(it.p.get('rRef')):
        return False
    c = P.items.get(it.src)
    if c is None or len(c.samples) < 2:
        return False
    r = autoTubeRadius(P, c)
    it.p.update({'rRef': r, 'r0': r, 'r1': r, 'r2': r})
    return True


@dataclass(eq=False, kw_only=True)
class TubeMesh:
    """The data of the modifier's display (rebuildTube, 6673) without three.js: `rings` (NR × n Vec3, doubles — the
    original's Float32Array rounds them for the GPU, as a Blender mesh does), `pos` (ring i point j at i * n + j),
    `faces` (the quads a, b, d, e between two rings — the original indexes them as the triangles a b d, a d e), `edges`
    (the lines along the lock, a - e: the light edge lines), `loops` (every ls-th ring, ls = round(NR / 16): the
    dark loop lines) and `colors` (4.7: with painted extras, the colour of every ring — [r, g, b] in three.js's linear
    working space, doubles; the original's Float32Array repeats it on the ring's n points and on the edge lines —, else
    None: the plain modifier colour)."""
    rings: list = field(default_factory=list)
    n: int = 0
    pos: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    loops: list = field(default_factory=list)
    colors: list | None = None


def rebuildTube(P, it):
    """port of rebuildTube (hair-modeler.clean.html:6673), the data part: (TubeMesh or None — no display, wrote) where
    `wrote` says ensureTubeRef changed it.p. Clears it.dirty. The painted weights' colours (masks black, thickness
    orange / blue, twist green / pink: 6689-6703) in TubeMesh.colors (tubeColors)."""
    it.dirty = False
    c = P.items.get(it.src)
    if c is None or len(c.samples) < 2 or len(it.profile) < 3:
        return None, False
    wrote = ensureTubeRef(P, it)
    n = len(it.profile)
    rings = tubeRings(P, it, c, TUBE_NR)
    NR = len(rings)
    m = TubeMesh(rings=rings, n=n, pos=[q for R in rings for q in R])
    for i in range(NR - 1):
        for j in range(n):
            j1 = (j + 1) % n; a = i * n + j; b = i * n + j1; d = (i + 1) * n + j1; e = (i + 1) * n + j
            m.faces.append([a, b, d, e]); m.edges.append((a, e))
    ls = max(1, _js_round_pos(NR / 16))
    for i in range(0, NR, ls):
        for j in range(n):
            m.loops.append((i * n + j, i * n + (j + 1) % n))
    m.colors = tubeColors(P, it, NR)
    return m, wrote


def _srgb_to_linear(c):
    # port of SRGBToLinear (three/src/math/ColorManagement.js r160)
    return c * 0.0773993808 if c < 0.04045 else js_pow(c * 0.9478672986 + 0.0521327014, 2.4)


def _hex_color(h):
    """new THREE.Color(0xRRGGBB) in r160: setHex — the sRGB bytes / 255, then to the linear working space
    (ColorManagement.enabled)"""
    return [_srgb_to_linear((h >> 16 & 255) / 255), _srgb_to_linear((h >> 8 & 255) / 255), _srgb_to_linear((h & 255) / 255)]


def _lerp_color(c, d, a):
    # Color.lerp (r160): r += (color.r - r) * alpha
    c[0] += (d[0] - c[0]) * a; c[1] += (d[1] - c[1]) * a; c[2] += (d[2] - c[2]) * a


def tubeColors(P, it, NR):
    """the colours of rebuildTube (hair-modeler.clean.html:6689-6703): painted weights show on the modifier — masks
    black, thickness orange (fatter) / blue (thinner), twist green (clockwise) / pink (anticlockwise). One [r, g, b] per
    ring (three.js's linear working space), or None without painted extras."""
    masks = [x for x in activeExtras(P, it) if x.kind in PAINTED and x.mask is not None]
    if not masks:
        return None
    base = _hex_color(0x8f78e0); fat = [1, 0.55, 0.15]; thin = [0.2, 0.62, 1]   # (new THREE.Color(r, g, b): as given)
    cw = [0.3, 0.9, 0.45]; ccw = [1, 0.35, 0.75]
    mk = [m for m in masks if m.kind == 'mask']; th = [m for m in masks if m.kind == 'thick']; tw = [m for m in masks if m.kind == 'twist']
    out = []
    for i in range(NR):
        t = i / (NR - 1)
        mv = js_max(*[maskAt(m.mask, t) for m in mk]) if mk else 0
        s = 0
        for m in th:
            s = s + maskAt(m.mask, t)
        w = clamp(s, -1, 1)
        s = 0
        for m in tw:
            s = s + maskAt(m.mask, t)
        wt = clamp(s, -1, 1)
        c3 = list(base)
        _lerp_color(c3, fat if w >= 0 else thin, js_min(1, abs(w) * 1.5))
        _lerp_color(c3, cw if wt >= 0 else ccw, js_min(1, abs(wt) * 1.5))
        k = 1 - mv * 0.92
        out.append([c3[0] * k, c3[1] * k, c3[2] * k])
    return out


def _js_round_pos(x):
    # Math.round for x >= 0 (half up)
    f = math.floor(x)
    return f + 1 if x - f >= 0.5 else f


# ======================= Apply (6699-6721) =======================

def modTargets(P, sel_ids):
    # port of modTargets (6700): the curves Apply works on — selected curves, and the source curves of selected
    # modifiers (each once, in selection order; not a disc's ring)
    out = []
    for iid in sel_ids:
        it = P.items.get(iid)
        c = P.items.get(it.src) if it is not None and it.type == 'tube' else it
        if c is not None and c.type == 'curve' and not c.ring and c not in out:
            out.append(c)
    return out


def newTube(P, c, prof):
    """the new modifier of applyModifier (6714-6718): the automatic radius at start, middle and end, no rotation, the
    tip collapsing over its last quarter; named "Modifier N (<curve>)" and placed right after its curve"""
    r = autoTubeRadius(P, c)
    t = addItem(P, 'tube', c.parent, {'src': c.id, 'profile': [list(q) for q in prof], 'dirty': True,
                                      'p': {'r0': r, 'r1': r, 'r2': r, 'rRef': r, 'rot0': 0, 'rot1': 0, 'rot2': 0, 'collapse': True, 'tipLen': 0.25}})
    t.name = f'Modifier {P.counters["tube"]} ({c.name})'
    arr = P.childrenOf(t.parent)
    arr.remove(t.id)                                                # (indexOf + splice: its one place, at the end)
    arr.insert(index_of(arr, c.id) + 1, t.id)
    P.gen += 1
    return t


def applyModifier(P, sel_ids, prof):
    """port of applyModifier (hair-modeler.clean.html:6703), the data part: the profile `prof` (modProfile: the tool
    panel's) on the selected curves. A curve that has a modifier gets the new profile (all three) and keeps its
    settings. Returns (the modifiers, the toast); the caller selects them (S.asel = [], S.sel = them), onSelChange,
    commit."""
    cs = [c for c in modTargets(P, sel_ids) if len(c.samples) >= 2]
    if not cs:
        return [], 'Select a curve in the viewport, then press Apply'
    if len(prof) < 3:
        return [], 'The profile needs at least 3 points'
    made = []
    for c in cs:
        t = next((x for x in P.tubes() if x.src == c.id), None)
        if t is not None:                                           # re-apply: new profile (all three), same settings
            t.profile = [list(q) for q in prof]; t.profs = None; t.dirty = True; made.append(t)
            P.rev += 1
            continue
        made.append(newTube(P, c, prof))
    return made, 'Modifier applied — its settings are in the properties panel' if len(made) == 1 else f'Modifier applied to {len(made)} curves'


# ======================= Convert to curves & walls (6755-6814) =======================

def _js_ceil(x):
    # Math.ceil: NaN and the infinities as they are (math.ceil raises on them)
    return x if x != x or x in (INF, -INF) else math.ceil(x)


def samePt(a, b):
    # port of samePt (6816)
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9


def gluedRun(pts, i):
    """port of gluedRun (hair-modeler.clean.html:6818): the points glued to point i (a run of neighbours at the very
    same place, around the outline), in outline order"""
    n = len(pts); run = [i]
    k = (i + 1) % n
    while k != i and samePt(pts[k], pts[i]):
        run.append(k); k = (k + 1) % n
    k = (i - 1 + n) % n
    while k != i and k not in run and samePt(pts[k], pts[i]):
        run.insert(0, k); k = (k - 1 + n) % n
    return run


def _rootSrc(P):
    # (convertTube 6777) the group whose settings the lock of a curve at the root takes: a ticked one, else any hair group
    return next((x for x in convTargets(P) if x.conv is not None), None) or \
        next((x for x in P.groups() if x.conv is not None and not x.guidesGroup and not x.meshesGroup), None)


def _peekConv(P, c):
    """groupConv of the curve's nearest group (1589) read, never written: its conv with the defaults of S where a key is
    missing (the step: an old density, else the project's default) — convOf without filling the group in"""
    from .hairmesh_io import defaultStep
    from .rowbase import CONV_KEYS, settings_conv
    g = P.parentOf(c)
    while g is not None and g.type != 'group':
        g = P.parentOf(g)
    if g is None:
        return settings_conv(P)
    conv = dict(g.conv or {})
    if conv.get('step') is None:
        conv['step'] = P.S.headH / conv['density'] if truthy(conv.get('density')) else defaultStep(P)
    for k in CONV_KEYS:
        if k not in conv:
            conv[k] = getattr(P.settings, k)
    return conv


def tubeConv(P, it):
    """the conversion settings the lock of this modifier gets on Convert to curves & walls (ours, lot 4.7b item 4; read,
    never written): a curve in a group, that group's (groupConv); a curve at the root, its new group's ({ ...src.conv,
    step: projectStep() }, 6777) — the modifier's own loop step (it.p.step > 0) over either. None without its curve."""
    from .rowbase import CONV_KEYS, settings_conv
    c = P.items.get(it.src)
    if c is None:
        return None
    own = it.p.get('step')
    if c.parent is None:
        src = _rootSrc(P)
        conv = {**settings_conv(P), **(dict(src.conv) if src is not None else {})}
        conv['step'] = projectStep(P)
        for k in CONV_KEYS:
            conv.setdefault(k, getattr(P.settings, k))
    else:
        conv = _peekConv(P, c)
    if truthy(own):
        conv['step'] = own
    return conv


def tubeStep(P, it):
    """the loop step of this modifier's lock after Convert (its own, else the one it inherits); None without its curve"""
    conv = tubeConv(P, it)
    return conv['step'] if conv is not None else None


def _arcFraction(c, p):
    """where p lies along curve c, as a share of its arc (the nearest point of its sample polyline)"""
    S = [s.p for s in c.samples]; best = (INF, 0.0); acc = 0.0; tot = 0.0
    for i in range(len(S) - 1):
        tot += S[i].distanceTo(S[i + 1])
    for i in range(len(S) - 1):
        a = S[i]; b = S[i + 1]; ab = V3().subVectors(b, a); l2 = ab.dot(ab)
        u = clamp(V3().subVectors(p, a).dot(ab) / l2, 0, 1) if l2 > 0 else 0
        d = a.clone().lerp(b, u).distanceTo(p)
        seg = math.sqrt(l2)
        if d < best[0]:
            best = (d, acc + seg * u)
        acc += seg
    return best[1] / tot if tot > 0 else 0.0


def tubeRows(P, it):
    """the loops the lock of this modifier will have (ours, lot 4.7b item 4: the preview showed its 96 rings, the
    converted walls far fewer), as shares of the arc from the root (0) to the tip (1): Convert to curves & walls run on
    a project holding only this modifier, its curve, its active extras and the settings its lock gets (tubeConv), then
    the row tables of that one lock — what Convert makes, loop for loop. Guides, Densities and welds with other locks
    act after Convert only. None without a curve."""
    import copy
    from .rows import buildRowTables
    from .scene import settle
    c = P.items.get(it.src); conv = tubeConv(P, it)
    if c is None or conv is None or len(c.samples) < 2:
        return None
    memo = {}
    c2 = copy.deepcopy(c, memo)
    t2 = copy.deepcopy(it, memo); t2.p = {k: v for k, v in it.p.items() if k != 'step'}
    xs = [copy.deepcopy(x, memo) for x in activeExtras(P, it)]
    Q = type(P)(name='tube preview', uid=max([c2.id, t2.id, *(x.id for x in xs), *(a.id for a in c2.anchors)]) + 1, counters=dict(P.counters))
    Q.S = copy.deepcopy(P.S); Q.settings = copy.deepcopy(P.settings)
    g = addItem(Q, 'group', None); g.conv = dict(conv); g.name = 'Lock'   # (an id after the copies': none is overwritten)
    for x in [c2, t2, *xs]:
        x.parent = g.id; Q.items[x.id] = x; g.children.append(x.id)
    for a in c2.anchors:
        a.att = None; Q.anchors[a.id] = a
    Q.gen += 1
    made, _msg = convertTube(Q, t2)
    if not made:
        return None
    settle(Q)
    ctx = buildRowTables(Q)
    T = [ctx.T.get(e.id) for e in made] if ctx.T is not None else []
    if not T or any(r is None or not r.rows for r in T) or len({len(r.rows) for r in T}) != 1:
        return None
    cl = getattr(it, 'cl', None)
    if not cl or len(cl) < 2:
        e = Q.items[made[0].id]
        return [_arcFraction(e, q) for q in T[0].rows]
    out = []                                                        # (each loop: the centre of its points on the edges, on the rings' centre line)
    for k in range(len(T[0].rows)):
        q = V3(0, 0, 0)
        for r in T:
            q.add(r.rows[k])
        out.append(_lineFraction(cl, q.multiplyScalar(1 / len(T))))
    out[0] = 0.0; out[len(out) - 1] = 1.0
    return out


def _lineFraction(pts, p):
    """where p lies along the polyline pts, as a share of its points (index / (n - 1): the rings of rebuildTube are one
    per point of it.cl)"""
    best = (INF, 0.0)
    for i in range(len(pts) - 1):
        a = pts[i]; b = pts[i + 1]; ab = V3().subVectors(b, a); l2 = ab.dot(ab)
        u = clamp(V3().subVectors(p, a).dot(ab) / l2, 0, 1) if l2 > 0 else 0
        d = a.clone().lerp(b, u).distanceTo(p)
        if d < best[0]:
            best = (d, i + u)
    return best[1] / (len(pts) - 1)


def rowRings(rings, ts):
    """the rings at the shares ts of the arc (rebuildTube's rings are even along it; between two: lerped)"""
    out = []; n = len(rings)
    for t in ts:
        f = clamp(t, 0, 1) * (n - 1)
        i = min(max(0, math.floor(f)), n - 2); x = f - i
        out.append([a.clone().lerp(b, x) for a, b in zip(rings[i], rings[i + 1])])
    return out


def convertTube(P, it):
    """port of convertTube (hair-modeler.clean.html:6758). Convert to the usual workflow: a curve along every profile
    corner (anchors where the source curve has them, plus a few more where it has only a few), closed walls between
    neighbouring corners; with the tip collapsed all curves end in one shared anchor. Corners glued in the profile at a
    section share one anchor there (a common trunk — where they come apart along the lock, the edges fork in a smooth
    Y); edges glued along the whole lock are one edge. Every anchor gets its cross-section (a.sec = k, lockSecs) and
    every edge is `para` (buildRowTables' para branch). The modifier and its source curve are removed.

    Returns (the edge curves or None, the toast). The caller then does what the original does after (6808-6810):
    S.asel = [], S.sel = the edges, S.surfDirty (the row tables), regenConversion when a conversion is pending,
    onSelChange (groups.panelsRendered with the last edge) and commit."""
    c = P.items.get(it.src)
    if c is None or len(c.samples) < 2 or c.arc is None:           # (!c.arc: an empty array is truthy in JS)
        return None, 'The source curve is missing'
    n = len(it.profile)
    rings = tubeRings(P, it, c, CONVERT_NR)
    NR = len(rings)
    tot = js_or(c.arc[len(c.arc) - 1], 1)
    fr = [c.arc[min(k * 24, len(c.arc) - 1)] / tot for k in range(len(c.anchors))]
    while len(fr) < 6:
        nx = [fr[0]]
        for k in range(1, len(fr)):
            nx.append((fr[k - 1] + fr[k]) / 2); nx.append(fr[k])
        fr = nx
    # curls and noise need more anchors to keep their shape: about 8 per turn / wave
    ex = [x for x in activeExtras(P, it) if x.kind != 'mask']
    if ex:
        s = 0
        for x in ex:
            s = s + (curlTurns(x) * 8 if x.kind == 'curl' else js_or(x.p.get('freq'), 1) * 6)
        need = js_min(160, _js_ceil(s) + 4)
        if need > len(fr):
            fr = [k / (need - 1) for k in range(need)]
    if truthy(it.p.get('collapse')):
        ts = 1 - clamp(nullish(it.p.get('tipLen'), 0.25), 0.01, 1)
        if all(abs(f - ts) > 0.04 for f in fr):
            fr.append(ts); fr.sort()

    def edgeAt(j, f):
        x = clamp(f, 0, 1) * (NR - 1); i = min(math.floor(x), NR - 2)
        return rings[i][j].clone().lerp(rings[i + 1][j], x - i)

    parent = c.parent
    own = it.p.get('step')                                          # (ours, lot 4.7b item 4: the modifier's own loop step; none: as the original)
    if parent is None:
        # the new group takes the conversion settings (loop step…) of a ticked group, else of any hair group that has
        # them — the plain defaults would give a step from the whole model height
        src = _rootSrc(P)
        g = addItem(P, 'group', None); g.name = c.name + ' lock'
        conv = dict(src.conv) if src is not None else {}
        conv['step'] = own if truthy(own) else projectStep(P)       # ({ ...src.conv, step }: a step of src keeps its place)
        g.conv = conv
        if g.id not in P.settings.convChecked:
            P.settings.convChecked.append(g.id)
        P.gen += 1
        parent = g.id
    elif truthy(own):
        # (ours) the lock gets a group of its own inside the curve's, with the curve's group's settings and this step
        g = addItem(P, 'group', parent); g.name = c.name + ' lock'
        g.conv = {**_peekConv(P, c), 'step': own}
        P.gen += 1
        parent = g.id
    tip = newAnchor(P, c.samples[len(c.samples) - 1].p) if truthy(it.p.get('collapse')) else None
    # the anchors of every edge at every section k: corners glued in the profile there share one anchor (a common
    # trunk — where they come apart along the lock, the edges fork in a smooth Y)
    K = len(fr)
    A = [[None] * K for _j in range(n)]
    for k, f in enumerate(fr):
        if tip is not None and k == K - 1:
            for j in range(n):
                A[j][k] = tip
            continue
        pr = profAt(it, f)
        for j in range(n):
            if A[j][k] is not None:
                continue
            run = gluedRun(pr, j); a = newAnchor(P, edgeAt(j, f))
            a.sec = k                                               # cross-section k (lockSecs)
            for i in run:
                A[i][k] = a
    # edges glued along the whole lock are one edge
    keep = []
    for j in range(n):
        if not keep or not all(a is A[keep[len(keep) - 1]][k] for k, a in enumerate(A[j])):
            keep.append(j)
    if len(keep) > 1 and all(a is A[keep[0]][k] for k, a in enumerate(A[keep[len(keep) - 1]])):
        keep.pop()
    made = []
    for j in keep:
        nc = makeCurve(P, parent); nc.name = f'{c.name} edge {j + 1}'
        nc.anchors = A[j]
        if tip is not None:
            tip.sec = K - 1
        nc.para = True                                              # anchor k of every edge — one cross-section (buildRowTables)
        nc.dirty = True
        made.append(nc)
    m = len(made)
    for j in range(m):
        s = makeSurface(P, made[j], made[(j + 1) % m])
        if s is not None:
            s.p['bulge'] = 0
    deleteItem(P, it.id); deleteItem(P, c.id)
    P.gen += 1; P.rev += 1
    glued = any(any(any(i != j and a in y.anchors and a is not tip for i, y in enumerate(made)) for a in x.anchors) for j, x in enumerate(made))
    return made, f'Converted: {m} curves and {m} walls (a closed volume lock)' + (' — glued corners make Y forks' if glued else '')
