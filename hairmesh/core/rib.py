# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The flat lock and the volume branch of 1.0.2 (Draw → Lock, `c.rib`; hair-modeler.clean.html:5813-6241): the curve
is the middle line of a flat ribbon, or — started from 3 or more anchors of other curves — of a closed branch.

  c.rib = { k: { anchorId: { w, r } } — half width and rotation (°) around the curve at that anchor; smooth (0…1) —
  width / rotation evened out along the lock; tip — share of the length narrowing to a point; view — the camera
  direction when drawn (the ribbon faces it); ends: [anchorId, anchorId] | null — started from two anchors of other
  curves: the root spans them (its width and place follow them) and Convert welds the edge curves to them;
  ext / offL / offR / glue — painted weights (MASK_N values); extInv, extH, extFade, offFade }

The ribbon itself is only a display in the original (rebuildRib, never read by buildGroupMesh); its frame (ribF,
ribRings, ribX) is what Convert reads, so it is kept on the curve like the samples (model.Curve.ribF / ribRings /
ribX / ribDirty), and rib_geom holds what the original's ribObj draws (ribbonGeometry / volumeGeometry), for the
viewport. The frame rule of 14823 is frameRibs; the root that follows its anchors (6003-6006), followRoots.

Pure Python, no bpy. The camera direction (camDir, 6350) comes in as `cam_dir`: a ribbon drawn by the port always
has its `view`; only a `rib` without one (a file) reads it. Toasts come back as messages."""
import hashlib
import json
import math
from array import array
from typing import cast

from . import fdlibm
from .brush import brushWeight
from .curves import markAnchorsDirty
from .density import MASK_N
from .edit import curvesWith, deleteItem, makeCurve, newAnchor, newCurveLook
from .geom import pointOnCurve
from .headobj import float32
from .js import NAN, clamp, index_of, js_hypot, js_max, js_min, js_or, js_round, nullish, truthy
from .mat4 import Quat, applyQuaternion
from .model import Anchor, HandleOverride
from .tube import degToRad, maskAt
from .vec3 import V3
from .walls import makeSurface

DEFAULT_CAM_DIR = (0.0, 0.0, -1.0)                                  # (three's camera looks down -Z: a `rib` without `view` and no camera given)
RIB_R = 30                                                          # S.ribR (5818): the half width of the next point, px
RIB_R_MIN = 3; RIB_R_MAX = 400                                      # clamp(..., 3, 400) (7731, 7901, 11372)
RIB_COL = 0xb04fc0                                                  # RIB_COL (6796)
RIB_BR = {'ext': ('Extrude', 0xff9a30), 'off': ('Offset', 0x33a0ff), 'glue': ('Glue', 0x4de673)}   # RIB_BR (6913)
RIB_BRUSH = {'on': False, 'kind': 'ext', 'strength': 1, 'intensity': 0.5}                       # S.ribBrush (5935)
CLEAR_KEYS = {'ext': ('ext',), 'off': ('off', 'offL', 'offR'), 'glue': ('glue',)}               # the Clear buttons (6133-6138)
PROP_DEFAULTS = {'smooth': 0.4, 'tip': 0.25, 'extH': 1, 'extFade': 0, 'offFade': 0}            # the sliders' `def` (6109-6135)

FOLLOW_ROUNDS = 8                                                   # (ours) the frames a chain of roots may take to settle: scene.settle, session.sync
NO_SHAPE = 'The lock has no shape yet'                            # 6157, 6184
NO_BRANCH = 'The branch has no shape yet'                           # 6058
VOLUME_STARTS = 'A volume branch starts from the {n} anchors — click on to place its next points, Esc — finish'   # 6223
LOCK_STARTS = 'The lock starts between the two anchors — click on to place its next points, Esc — finish'        # 6224


def _cam(cam_dir):
    return cam_dir.clone() if cam_dir is not None else V3(*DEFAULT_CAM_DIR)   # camDir(): a new vector every call


def _key(a):
    return str(a.id)                                                # (rib.k: an object, its keys strings, as JSON keeps them)


class RibF(list):
    """the list ribFrame returns (one RibSample per curve sample), with the original's `.root`"""
    root = None


class RibSample:
    """{ p, A, T, w, s } of ribFrame (5916): p the sample's own point (not a copy, as the original), A the across axis
    (unit), T the tangent, w the half width, s the arc fraction"""
    __slots__ = ('p', 'A', 'T', 'w', 's')

    def __init__(self, p, A, T, w, s):
        self.p = p; self.A = A; self.T = T; self.w = w; self.s = s


# ---------------- the scene helpers (5822-5855) ----------------

def anchorById(P, aid):
    # port of anchorById (hair-modeler.clean.html:6799): only anchors some curve uses (a deleted one is gone)
    for c in P.curves():
        for a in c.anchors:
            if a.id == aid:
                return a
    return None


def ribHost(P, x):
    # port of ribHost (hair-modeler.clean.html:6801): the curve an end anchor sits on (its trunk)
    for k in curvesWith(P, x):
        if not k.ring and not k.conn and not k.closed and not k.junc and not truthy(k.rib):
            return k
    return None


def ribTrunkDir(P, pair):
    # port of ribTrunkDir (hair-modeler.clean.html:6802): the way the trunk goes on past each anchor (unit), averaged
    d = V3()
    for x in pair:
        h = ribHost(P, x)
        if h is None:
            continue
        i = index_of(h.anchors, x); A = h.anchors
        v = x.pos.clone().sub(A[i - 1].pos) if i > 0 else x.pos.clone().sub(A[1].pos) if len(A) > 1 else None
        if v is not None and v.lengthSq() > 1e-12:
            d.add(v.normalize())
    return d.normalize() if d.lengthSq() > 1e-8 else None


def ribEnds(P, c):
    # port of ribEnds (hair-modeler.clean.html:6812): the anchors a lock started from (2: a flat lock between them, 3 or
    # more: a volume branch through all of them), or None — fewer than 2, or one of them gone
    e = c.rib.get('ends') if truthy(c.rib) else None
    if e is None or not truthy(e) or len(e) < 2:                   # (None is falsy too: the same test, said to the type checker)
        return None
    lst = [anchorById(P, i) for i in e]
    return cast('list[Anchor]', lst) if all(a is not None for a in lst) else None


class RibRoot:
    """ribRoot's { mid, A0, B0, prof, w0 } (5849); prof: [{x, y, t}]"""
    __slots__ = ('mid', 'A0', 'B0', 'prof', 'w0')

    def __init__(self, mid, A0, B0, prof, w0):
        self.mid = mid; self.A0 = A0; self.B0 = B0; self.prof = prof; self.w0 = w0


def ribRoot(ends, T0):
    # port of ribRoot (hair-modeler.clean.html:6815): the root cross-section of a lock started from anchors: their
    # centre, half width w0, the across axis A0 and B0 = T0 × A0, and every anchor's place in that frame (x along A0,
    # y along B0, t along T0 — off the cross plane)
    mid = V3()
    for a in ends:
        mid.add(a.pos)
    mid.divideScalar(len(ends))
    A0 = ends[1].pos.clone().sub(ends[0].pos)
    if len(ends) > 2:
        bd = -1
        for a in ends:
            for b in ends:
                d = a.pos.distanceToSquared(b.pos)
                if d > bd:
                    bd = d; A0 = b.pos.clone().sub(a.pos)
        if T0 is not None:
            q = A0.clone().addScaledVector(T0, -A0.dot(T0))
            if q.lengthSq() > 1e-12:
                A0 = q
    A0.normalize()
    B0 = V3().crossVectors(T0, A0).normalize() if T0 is not None else V3()
    prof = []
    for a in ends:
        d = a.pos.clone().sub(mid)
        prof.append({'x': d.dot(A0), 'y': d.dot(B0), 't': d.dot(T0) if T0 is not None else 0})
    w0 = ends[0].pos.distanceTo(ends[1].pos) / 2 if len(ends) == 2 else js_max(1e-6, *[js_hypot(q['x'], q['y']) for q in prof])
    return RibRoot(mid, A0, B0, prof, w0)


def ringOrder(lst, T0):
    # port of ringOrder (hair-modeler.clean.html:6829): the anchors of a volume branch in order around their centre
    # (seen along the trunk) — the ring the walls go round (a stable sort, as Array.sort)
    r = ribRoot(lst, T0)
    o = [(a, fdlibm.atan2(r.prof[i]['y'], r.prof[i]['x'])) for i, a in enumerate(lst)]
    return [a for a, _ang in sorted(o, key=lambda e: e[1])]


def ribAnchorVals(P, c):
    """port of ribAnchorVals (hair-modeler.clean.html:6834): width / rotation at every anchor (an anchor without its own
    — added later — takes them from its neighbours). Writes c.rib.k = {} when it is missing, as the original."""
    R = c.rib
    K = R.get('k')
    if not truthy(K):
        K = R['k'] = {}
    n = len(c.anchors)
    known = [i for i, a in enumerate(c.anchors) if truthy(K.get(_key(a)))]

    def v(j, key):
        return js_or(K[_key(c.anchors[j])].get(key), 0)

    def at_(i, key, default):
        if not known:
            return default
        lo = -1; hi = -1
        for j in known:
            if j <= i:
                lo = j
            if j >= i and hi < 0:
                hi = j
        if lo < 0:
            return v(hi, key)
        if hi < 0 or hi == lo:
            return v(lo, key)
        return v(lo, key) + (v(hi, key) - v(lo, key)) * (i - lo) / (hi - lo)
    w = []; r = []
    for i in range(n):
        w.append(at_(i, 'w', P.S.scale * 0.02)); r.append(at_(i, 'r', 0))
    return {'w': w, 'r': r}


# ---------------- the frame of the ribbon (5871-5929) ----------------

def _sq(x):
    return x * x                                                    # (`x ** 2`: exactly x * x in V8)


_QBUF = array('d')                                                  # ribFrame's Gaussian weights, N × N (scratch, no state: grown, never read stale)
_QBUF_MAX = 1024 * 1024


def ribFrame(P, c, cam_dir=None):
    """port of ribFrame (hair-modeler.clean.html:6848): the ribbon — per curve sample its middle point, across vector A
    (unit), half width w; edges L = p − A·w, R = p + A·w. A RibF (with .root) or None (fewer than 2 samples)."""
    sm = c.samples; N = len(sm)
    if N < 2 or c.arc is None:
        return None
    vals = ribAnchorVals(P, c); AW = vals['w']; AR = vals['r']; n = len(c.anchors); ends = ribEnds(P, c)
    Pp = [s.p for s in sm]
    T = []
    for i in range(N):
        t = Pp[min(i + 1, N - 1)].clone().sub(Pp[max(i - 1, 0)])
        T.append(t.normalize() if t.lengthSq() > 1e-20 else V3(0, -1, 0))
    root = ribRoot(ends, T[0]) if ends else None
    if ends:
        assert root is not None                                     # (ribRoot always makes one)
        AW[0] = root.w0; AR[0] = 0
    arc = c.arc
    tot = js_or(arc[N - 1], 1); per = (N - 1) / max(1, n - 1)
    # between the anchors: a smooth monotone cubic over the arc length (no kinks at the anchors, no overshoot);
    # Profile smoothing evens it out further — a Gaussian along the lock (the ends extended flat, so they don't jump)
    sA = [arc[min(N - 1, js_round(k * per))] for k in range(n)]

    def curve(V):
        m = len(V)
        if m < 2:
            v0 = js_or(V[0] if V else None, 0)
            return [v0 for _ in sm]
        d = []; t = []
        for k in range(m - 1):
            d.append((V[k + 1] - V[k]) / js_max(sA[k + 1] - sA[k], 1e-9))
        for k in range(m):
            t.append(d[0] if k == 0 else d[m - 2] if k == m - 1 else 0 if d[k - 1] * d[k] <= 0 else (d[k - 1] + d[k]) / 2)
        for k in range(m - 1):
            if d[k] == 0:
                t[k] = t[k + 1] = 0
                continue
            a = t[k] / d[k]; b = t[k + 1] / d[k]; h = a * a + b * b
            if h > 9:
                s = 3 / math.sqrt(h); t[k] = s * a * d[k]; t[k + 1] = s * b * d[k]
        out = []
        for x in arc:
            k = 0
            while k < m - 2 and x > sA[k + 1]:
                k += 1
            hh = js_max(sA[k + 1] - sA[k], 1e-9); u = clamp((x - sA[k]) / hh, 0, 1); u2 = u * u; u3 = u2 * u
            out.append((2 * u3 - 3 * u2 + 1) * V[k] + (u3 - 2 * u2 + u) * hh * t[k] + (-2 * u3 + 3 * u2) * V[k + 1] + (u3 - u2) * hh * t[k + 1])
        return out
    w = curve(AW); r = curve(AR)
    sg = nullish(c.rib.get('smooth'), 0.4) * 0.12 * tot
    if sg > tot * 1e-4:
        two = 2 * sg * sg
        exp = fdlibm.exp

        def gauss2(V, U):
            # the original's gauss (5900) run on V and U at once: the same weights, each computed once — and once for
            # (i, j) and (j, i), as (a − b)² and (b − a)² are the same double. Every sum in its own order: the same bits
            ds = tot / (N - 1); ext = math.ceil(3 * sg / ds)
            # (a scratch buffer kept between calls — every entry read here was written in this call; a lock of more than
            # 1024 samples takes its own, so the kept one stays under 8 MB)
            Q = _QBUF if N * N <= _QBUF_MAX else array('d', bytes(8 * N * N))
            if len(Q) < N * N:
                Q.frombytes(bytes(8 * (N * N - len(Q))))
            ov = []; ou = []
            for i in range(N):
                s = 0; su = 0; ws = 0; x = arc[i]; row = i * N
                for j in range(N):
                    if j < i:
                        q = Q[j * N + i]
                    else:
                        q = exp(-_sq(arc[j] - x) / two); Q[row + j] = q
                    s += q * V[j]; su += q * U[j]; ws += q
                for e in range(1, ext + 1):
                    q0 = exp(-_sq(x + e * ds) / two); q1 = exp(-_sq(tot + e * ds - x) / two)
                    s += q0 * V[0] + q1 * V[N - 1]; su += q0 * U[0] + q1 * U[N - 1]; ws += q0 + q1
                ov.append(s / ws); ou.append(su / ws)
            return ov, ou
        w0 = w[0]; w, r = gauss2(w, r)
        if ends:                                                    # the root stays on its anchors
            dw = w0 - w[0]; r0 = r[0]; k15 = sg * 1.5
            w = [x + dw * exp(-_sq(arc[i] / k15)) for i, x in enumerate(w)]
            r = [x - r0 * exp(-_sq(arc[i] / k15)) for i, x in enumerate(r)]
    tip = nullish(c.rib.get('tip'), 0.25)
    if tip > 0:
        w2 = []
        for i, x in enumerate(w):
            t = arc[i] / tot
            if t <= 1 - tip:
                w2.append(x)
            else:
                u = (1 - t) / tip; w2.append(x * u * (2 - u))
        w = w2

    def perp(v, t):
        return v.clone().addScaledVector(t, -v.dot(t))
    if ends:
        assert root is not None
        A0 = root.A0.clone()
    else:
        view = c.rib.get('view')
        # new V3(...view): a missing component is three's default 0, a null one +0 in the arithmetic, the rest ignored
        vw = V3(*[0.0 if i >= len(view) or view[i] is None else view[i] for i in range(3)]) if truthy(view) else _cam(cam_dir)
        A0 = V3().crossVectors(T[0], vw)
        if A0.lengthSq() < 1e-10:
            A0 = perp(V3(0, 1, 0), T[0])
        A0.normalize()
    A = perp(A0, T[0])
    if A.lengthSq() < 1e-12:
        A = perp(V3(1, 0, 0), T[0])
    A.normalize()
    out = RibF()
    q = Quat()
    for i in range(N):
        if i:
            applyQuaternion(A, q.setFromUnitVectors(T[i - 1], T[i])); A = perp(A, T[i])
            if A.lengthSq() < 1e-12:
                A = perp(V3(0, 1, 0), T[i])
            A.normalize()
        a = applyQuaternion(A.clone(), Quat().setFromAxisAngle(T[i], degToRad(r[i])))   # Vector3.applyAxisAngle
        if i == 0 and ends:
            a = A0.clone()                                          # the root lies exactly on the two anchors
        out.append(RibSample(Pp[i], a, T[i], float(js_max(0, w[i])), arc[i] / tot))   # (Math.max(0, ±0) is +0: a double, never Python's int 0, whose negation stays +0)
    out.root = root
    return out


def ribRings(F):
    # port of ribRings (hair-modeler.clean.html:6900): a volume branch: ring j of every sample — the root anchors'
    # places in the root frame, carried along (B = T × A) and scaled by the width; their small offsets along the trunk
    # at the root fade out over the first tenth of the length
    R = F.root
    if R is None or len(R.prof) < 3:
        return None
    out = []
    for f in F:
        B = V3().crossVectors(f.T, f.A).normalize(); k = f.w / R.w0; fade = fdlibm.exp(-_sq(f.s / 0.1))
        out.append([f.p.clone().addScaledVector(f.A, q['x'] * k).addScaledVector(B, q['y'] * k).addScaledVector(f.T, q['t'] * fade) for q in R.prof])
    return out


# ---------------- Extrude / Offset / Glue (5930-5999) ----------------

def ribVolOf(P, c):
    # port of ribVolOf (hair-modeler.clean.html:6914)
    e = ribEnds(P, c)
    return bool(e and len(e) > 2)


def ribCurvesSel(P, sel_items, sel_anchors):
    """port of ribCurvesSel (hair-modeler.clean.html:6915): the flat locks (not volume branches) selected directly or by
    an anchor. sel_items: S.sel (ids), sel_anchors: S.asel (anchor ids)."""
    sel = set(sel_items); asel = set(sel_anchors)
    return [c for c in P.curves() if truthy(c.rib) and not ribVolOf(P, c) and (c.id in sel or any(a.id in asel for a in c.anchors))]


def ribFade(s, f):
    # port of ribFade (hair-modeler.clean.html:6917): smoothstep over the share f of the length at the tip
    if not (f > 0) or s <= 1 - f:
        return 1
    u = clamp((1 - s) / f, 0, 1)
    return u * u * (3 - 2 * u)


def _n(v):
    return 0 if v is None else v                                    # (null in JS arithmetic: 0)


def _sm(W):
    # the painted weights are evened out ([1 2 1] passes, ends kept) so the shifted edges run in smooth lines, not in steps
    if not truthy(W):
        return W
    a = W
    for _p in range(6):
        L = len(a)
        a = [(_n(a[max(i - 1, 0)]) + 2 * _n(v) + _n(a[min(i + 1, L - 1)])) / 4 for i, v in enumerate(a)]
    return a


def ribExtShape(P, c, F):
    """port of ribExtShape (hair-modeler.clean.html:6918): the extruded lock's cross-section [L, R, R', L'] per sample:
    {rings, e, o, g}, or None without an Extrude weight. Moves an older file's one Offset (off) to offL / offR, in the
    data, as the original."""
    R = c.rib; X = R.get('ext')
    if not truthy(X) or not any(v is not None and v > 0.002 for v in X):
        return None
    if truthy(R.get('off')):                                        # (older: one Offset for both edges)
        R['offL'] = R['offL'] if truthy(R.get('offL')) else list(R['off'])
        R['offR'] = R['offR'] if truthy(R.get('offR')) else list(R['off'])
        R['off'] = None
    SX = _sm(X); SL = _sm(R.get('offL')); SR = _sm(R.get('offR')); SG = _sm(R.get('glue'))
    inv = -1.0 if truthy(R.get('extInv')) else 1.0; H = nullish(R.get('extH'), 1)   # (doubles: -1 * 0 is −0 in JS, an int 0 in Python)
    e = []; o = []; g = []; rings = []
    extFade = js_or(R.get('extFade'), 0); offFade = js_or(R.get('offFade'), 0)
    for f in F:
        ee = clamp(maskAt(SX, f.s), 0, 1) * ribFade(f.s, extFade); fo = ribFade(f.s, offFade); gg = clamp(maskAt(SG, f.s), 0, 1)
        oL = clamp(maskAt(SL, f.s), -1, 1) * fo; oR = clamp(maskAt(SR, f.s), -1, 1) * fo   # each edge on its own
        B = V3().crossVectors(f.T, f.A).normalize(); top = f.p.clone().addScaledVector(B, inv * ee * H * f.w)
        xL = -float(clamp(f.w * (1 - oL), 0, 2 * f.w)); xR = clamp(f.w * (1 - oR), 0, 2 * f.w); xm = (xL + xR) / 2   # glued: both come to their middle
        e.append(ee); g.append(gg)
        rings.append([f.p.clone().addScaledVector(f.A, -f.w), f.p.clone().addScaledVector(f.A, f.w),
                      top.clone().addScaledVector(f.A, xR + (xm - xR) * gg), top.addScaledVector(f.A, xL + (xm - xL) * gg)])
    return {'rings': rings, 'e': e, 'o': o, 'g': g}


def ribColWeights(c, F, kind):
    """the data of ribCols (hair-modeler.clean.html:6936) with the brush on this lock: per sample (the colour hex the
    lock's purple blends to, the blend 0…1) — the active brush's weights (orange: extruded, blue / orange: offset out /
    in, green: glued). The colour space is the viewport's."""
    out = []
    for f in F:
        if kind == 'off':
            vl = maskAt(c.rib.get('offL'), f.s); vr = maskAt(c.rib.get('offR'), f.s)
            v = vl if abs(vl) > abs(vr) else vr
        else:
            v = maskAt(c.rib.get(kind), f.s)
        out.append((0x33a0ff if kind == 'off' and v < 0 else RIB_BR[kind][1], clamp(abs(v), 0, 1)))
    return out


def paintRib(P, sel_items, sel_anchors, m, op, amt, brush, R, hard, screen):
    """port of paintRib (hair-modeler.clean.html:6951): the brush paints the weights of the selected flat locks under
    the circle (mask-brush rules: Ctrl — erase, Shift — smooth; Offset like Thickness: Ctrl — inverted, Ctrl+Alt —
    erase). m = (x, y) px; op 'paint' | 'invert' | 'erase' | 'smooth' (brush.weightOp with kind 'thick' for Offset);
    amt: brush.FIRST_DAB on the press, brush.dabAmount on a move; brush: S.ribBrush (kind, strength, intensity); R,
    hard: S.brushR, S.brushHard; screen(p): toScreen (x, y, z). Returns whether a weight changed (those locks dirty)."""
    kind = brush['kind']; lo = -1 if kind == 'off' else 0; changed = False
    target = 0 if op == 'erase' else (-1 if op == 'invert' else 1) * (brush['intensity'] if kind == 'off' else brush['strength'])
    for c in ribCurvesSel(P, sel_items, sel_anchors):
        if c.arc is None or len(c.samples) < 2:
            continue
        rib = c.rib
        if truthy(rib.get('off')):
            rib['offL'] = list(rib['off']); rib['offR'] = list(rib['off']); rib['off'] = None
        # Offset: each extruded edge has its own weights and is painted where that edge is (the left one: ring point 3, the right: 2)
        lanes = (('offL', 3), ('offR', 2)) if kind == 'off' else ((kind, -1),)
        for key, col in lanes:
            W = rib.get(key)
            if not truthy(W):
                fresh: list[float] = [0] * MASK_N                   # new Array(MASK_N).fill(0) (ints, as the original's 0: the file writes 0)
                W = rib[key] = fresh
            src = list(W); ch = False                               # (W.slice(): its length; past it — undefined)
            for k in range(MASK_N):
                Pt = pointOnCurve(c, k / (MASK_N - 1))
                if Pt is None:
                    continue
                if col >= 0 and c.ribX is not None:
                    rings = c.ribX['rings']; Pt = rings[js_round(k / (MASK_N - 1) * (len(rings) - 1))][col]
                s = screen(Pt)
                if s is None or s[2] > 1:
                    continue
                d = js_hypot(s[0] - m[0], s[1] - m[1])
                if d >= R:
                    continue
                # weights saved shorter than MASK_N: past their end W[k] is undefined — NaN in the arithmetic, the
                # array grows, the gaps are holes (undefined too: NaN here; null in the file, as JSON.stringify writes)
                bw = brushWeight(d, R, hard); old = W[k] if k < len(W) else NAN; o_ = _n(old)
                if op == 'smooth':
                    sm = 0; n = 0
                    for j in range(k - 3, k + 4):
                        if 0 <= j < MASK_N:
                            sm = sm + _n(src[j] if j < len(src) else NAN); n += 1
                    v = o_ + (sm / n - o_) * bw * 0.5
                elif op == 'erase':
                    v = js_max(0, o_ - bw * amt) if o_ > 0 else js_min(0, o_ + bw * amt)
                else:
                    v = clamp(o_ + target * bw * amt, lo, 1)
                if k >= len(W):
                    W.extend([NAN] * (k + 1 - len(W)))
                W[k] = v
                if v != old:                                        # (NaN !== NaN, undefined !== NaN, null !== a number: so here)
                    ch = True
            if ch:
                c.ribDirty = True; changed = True
    return changed


# ---------------- rebuildRib and what the original draws (6000-6054) ----------------

def _follow(P, c):
    # the root follows the centre of its anchors (rebuildRib, 6003-6005): the anchor moved, or None
    ends = ribEnds(P, c)
    if ends and c.anchors:
        mid = V3()
        for a in ends:
            mid.add(a.pos)
        mid.divideScalar(len(ends))
        if mid.distanceToSquared(c.anchors[0].pos) > 1e-12:
            c.anchors[0].pos.copy(mid); markAnchorsDirty(P, [c.anchors[0]]); c.ribDirty = True; P.rev += 1
            return c.anchors[0]
    return None


def followRoots(P):
    """The first part of rebuildRib (6003-6006) for every lock started from anchors, at the end of an operator (the
    Blender side, session.sync): a root whose anchors moved goes to their centre (its curves dirty, the ribbon
    rebuilt by the next frame). Returns the anchors moved."""
    moved = []
    for c in P.curves():
        if truthy(c.rib):
            a = _follow(P, c)
            if a is not None:
                moved.append(a)
    return moved


def followFrames(P, edges):
    """(ours) the frames after an operator's crown part (session.sync, live._drag_sync) while the roots of the locks
    started from anchors follow them (followRoots; the ribbons come last in the frame, 14823): each next frame starts
    with its crown part — crownSymSync, syncPathCurves, updateAttachments (crowntool.frameSync, 1.0.3 16622-16627) — so a
    root that followed an anchor of a symmetric crown moves its pair (fuzz case 12300009), and a root that is another
    lock's end anchor moves its follower a round later. `edges` gets the edge curves syncPathCurves moved (once each).
    Returns (the anchors moved, the toasts of updateAttachments)."""
    from .crowntool import frameSync
    moved = []; toasts = []
    for _r in range(FOLLOW_ROUNDS):
        m = followRoots(P)
        if not m:
            break
        moved.extend(m)
        ch = []
        m2, t2 = frameSync(P, ch)
        moved.extend(m2); toasts.extend(t2)
        edges.extend(c for c in ch if c not in edges)
    return moved, toasts


def rebuildRib(P, c, cam_dir=None):
    """port of rebuildRib (hair-modeler.clean.html:6977), the data part: c.ribF / ribRings / ribX and what ribObj
    draws (rib_geom). Started from anchors whose centre the root left: the root moves there, marks its curves dirty,
    stays dirty and returns that anchor — the ribbon waits for the next frame, as the original (6005). Else None."""
    c.ribDirty = False; c.ribX = None; c.rib_key = None
    moved = _follow(P, c)
    if moved is not None:
        return moved
    F = ribFrame(P, c, cam_dir)
    c.rib_geom = None
    if F is None:
        return None
    rings = ribRings(F)
    if rings:
        c.ribF = F; c.ribRings = rings; c.rib_geom = volumeGeometry(P, rings, F, 'volume')
    else:
        c.ribRings = None
        X = ribExtShape(P, c, F)
        if X:
            c.ribF = F; c.ribX = X; c.rib_geom = volumeGeometry(P, X['rings'], F, 'extruded')
        else:
            c.rib_geom = ribbonGeometry(P, F); c.ribF = F
    c.rib_key = ribKey(P, c, cam_dir)                               # (after: ribAnchorVals / ribExtShape may have written into rib)
    return None


def ribKey(P, c, cam_dir):
    """(ours, lot 1.1f) everything a ribbon is made from: the curve's samples and arc (the very lists: rebuildCurve
    keeps them while their key holds), its anchors, the `rib` itself (with k and the weights), the end anchors' places,
    the model's scale and — with no view of its own — the camera's direction. The frame rule (14823) rebuilds every
    ribbon whenever any curve changed; one whose key is the one it was made from would come out the same, bit for bit."""
    ends = ribEnds(P, c)
    view = c.rib.get('view')
    rest = (tuple(a.id for a in c.anchors), c.anchors[0].pos.toArray() if c.anchors else None,
            json.dumps(c.rib), tuple((a.id, *a.pos.toArray()) for a in ends) if ends else None, P.S.scale,
            None if truthy(view) else _cam(cam_dir).toArray())
    return (c.samples, c.arc, rest)


def ribStamp(g):
    """(ours) a ribbon's stamp by content (rib_geom: kind, M, pos, index) — the live mesh keeps it in the .blend, so it
    must be the same in every session"""
    h = hashlib.blake2b(digest_size=16)
    h.update(repr((g['kind'], g['M'], len(g['pos']), len(g['index']))).encode())
    h.update(array('d', g['pos']).tobytes())                        # (the bits: a signed zero is another point)
    h.update(array('q', g['index']).tobytes())
    return h.hexdigest()


def _sameKey(k, c, P, cam_dir):
    return k is not None and k[0] is c.samples and k[1] is c.arc and k[2] == ribKey(P, c, cam_dir)[2]


def ribbonGeometry(P, F):
    """what rebuildRib (6015-6031) draws for a flat ribbon: pos (Float32, 2 vertices per sample: L = p − A·w,
    R = p + A·w), index (triangles (a, a+1, a+3), (a, a+3, a+2)) and lines (the outline: both edges, the root and the
    tip — a cut across where the tip isn't a point; segment ends, Float32), flat lists in the original's order"""
    N = len(F); pos = []; idx = []
    for i, f in enumerate(F):
        l_ = f.p.clone().addScaledVector(f.A, -f.w); rr = f.p.clone().addScaledVector(f.A, f.w)
        pos.extend((float32(l_.x), float32(l_.y), float32(l_.z), float32(rr.x), float32(rr.y), float32(rr.z)))
        if i < N - 1:
            a = 2 * i; idx.extend((a, a + 1, a + 3, a, a + 3, a + 2))

    def Pk(k):
        return pos[k * 3:k * 3 + 3]
    seg = []
    for i in range(N - 1):
        seg.extend(Pk(2 * i)); seg.extend(Pk(2 * i + 2)); seg.extend(Pk(2 * i + 1)); seg.extend(Pk(2 * i + 3))
    seg.extend(Pk(0)); seg.extend(Pk(1))
    if F[N - 1].w > P.S.scale * 1e-4:
        seg.extend(Pk(2 * N - 2)); seg.extend(Pk(2 * N - 1))
    return {'kind': 'ribbon', 'M': 2, 'pos': pos, 'index': idx, 'lines': seg}


def volumeGeometry(P, rings, F, kind='volume'):
    """what ribVolumeObj (hair-modeler.clean.html:7014) draws for a volume branch / an extruded lock: a closed tube
    through its rings (M vertices each), an outline along every ring point's line, the root ring and (a cut tip) the
    last ring; flat Float32 lists in the original's order"""
    N = len(rings); M = len(rings[0]); pos = []; idx = []
    for R in rings:
        for q in R:
            pos.extend((float32(q.x), float32(q.y), float32(q.z)))
    for i in range(N - 1):
        for j in range(M):
            a = i * M + j; b = i * M + (j + 1) % M; d = (i + 1) * M + (j + 1) % M; e = (i + 1) * M + j
            idx.extend((a, b, d, a, d, e))
    seg = []

    def Pq(i, j):
        q = rings[i][j]
        return (float32(q.x), float32(q.y), float32(q.z))
    for j in range(M):
        for i in range(N - 1):
            seg.extend(Pq(i, j)); seg.extend(Pq(i + 1, j))

    def ring(i):
        for j in range(M):
            seg.extend(Pq(i, j)); seg.extend(Pq(i, (j + 1) % M))
    ring(0)
    if F[N - 1].w > P.S.scale * 1e-4:
        ring(N - 1)
    return {'kind': kind, 'M': M, 'pos': pos, 'index': idx, 'lines': seg}


def frameRibs(P, changed, cam_dir=None):
    """The flat locks' part of the original's frame (14823): every ribbon is rebuilt when it is dirty, when any curve
    was rebuilt in this frame (`changed`: truthy) or when it has no shape yet; a curve that is no lock any more loses
    its ribbon. Returns the roots moved (rebuildRib), whose curves wait for the next frame. (Ours: a ribbon whose
    ribKey is the one it was made from is left as it is — the same result, the same objects.)"""
    moved = []
    for c in P.curves():
        if truthy(c.rib):
            if c.ribDirty or c.rib_geom is None or (changed and not _sameKey(c.rib_key, c, P, cam_dir)):
                a = rebuildRib(P, c, cam_dir)
                if a is not None:
                    moved.append(a)
        elif c.rib_geom is not None:
            c.rib_geom = None
    return moved


def hasRibs(P):
    return any(truthy(c.rib) or c.rib_geom is not None for c in P.curves())


# ---------------- Convert (6055-6076, 6150-6204) ----------------

def _dropUnused(P, c, start):
    """(ours, as weld / union do) the anchors a conversion made and left out (the first of idx, the deduplicated ones,
    the one the tip replaced) and those of the deleted middle curve: out of the project's register when no curve uses
    them (the file only holds the anchors curves use; the original's are garbage)"""
    used = {a.id for k in P.curves() for a in k.anchors}
    for aid in [i for i in P.anchors if i >= start] + [a.id for a in c.anchors]:
        if aid not in used:
            P.anchors.pop(aid, None)


def _indices(n, N):
    per = (N - 1) / max(1, n - 1); idx = []
    for k in range(n):
        idx.append(js_round(k * per))
        if n < 6 and k < n - 1:
            idx.append(js_round((k + 0.5) * per))
    return idx


def _place(P, cv, after_id, off):
    arr = P.childrenOf(cv.parent)
    arr.pop(index_of(arr, cv.id))
    arr.insert(index_of(arr, after_id) + off, cv.id)


def _trunk(P, cv, x):
    # (each edge curve starts on the anchor and goes on back along that anchor's curve to its root — a common trunk)
    h = ribHost(P, x); i = index_of(h.anchors, x) if h is not None else -1
    if i > 0:
        assert h is not None                                        # (i > 0: h was found)
        cv.anchors = h.anchors[:i] + cv.anchors


def ribVolumeConvert(P, c):
    """port of ribVolumeConvert (hair-modeler.clean.html:7034): a curve along every root anchor's line — it starts on that
    anchor and goes on back along the anchor's own curve to its root (a common trunk); walls round the ring (closed); a
    pointed tip — one shared anchor. Returns (the curves made, the message)."""
    F = c.ribF; R = c.ribRings; ends = ribEnds(P, c)
    if F is None or not R or not ends:
        return [], NO_BRANCH
    n = len(c.anchors); N = len(F); idx = _indices(n, N); start = P.uid
    tip = newAnchor(P, F[N - 1].p) if F[N - 1].w <= P.S.scale * 1e-4 else None
    made = []
    for j, x in enumerate(ends):
        cv = makeCurve(P, c.parent); cv.name = f'{c.name} {j + 1}'
        cv.anchors = [newAnchor(P, R[i][j]) for i in idx]; cv.anchors[0] = x
        if tip is not None:
            cv.anchors[len(cv.anchors) - 1] = tip
        _trunk(P, cv, x)
        newCurveLook(cv); cv.dirty = True
        _place(P, cv, c.id, 1 + j)
        made.append(cv)
    for j in range(len(made)):
        s = makeSurface(P, made[j], made[(j + 1) % len(made)])
        if s is not None:
            s.p['bulge'] = 0
    deleteItem(P, c.id); _dropUnused(P, c, start)
    P.gen += 1; P.rev += 1
    return made, f'{c.name}: {len(made)} curves and {len(made)} walls round the branch (joined to its anchors)'


def ribExtConvert(P, c):
    """port of ribExtConvert (hair-modeler.clean.html:7133): four curves L, R (base edges) and TL, TR (the extruded edges)
    with four walls round them (base, two sides, top). Where two curves lie on each other (zero extrusion, a point tip,
    a glued top face) they share their anchors — a Y where the glued top face turns into one curve — and the wall
    between them closes to a line (no holes). Returns (the curves made, the message)."""
    F = c.ribF; X = c.ribX; ends = ribEnds(P, c)
    if F is None or X is None:
        return [], NO_SHAPE
    n = len(c.anchors); N = len(F); idx = _indices(n, N); eps = P.S.scale * 1e-4; start = P.uid
    col = (0, 1, 3, 2); cv = []
    for nm in ('L', 'R', 'TL', 'TR'):
        k = makeCurve(P, c.parent); k.name = f'{c.name} {nm}'; cv.append(k)
    A = []
    for i in idx:
        q = [X['rings'][i][j].clone() for j in col]
        if X['g'][i] >= 0.95:                                       # a glued top face: one ridge
            mid = q[2].clone().lerp(q[3], 0.5); q[2].copy(mid); q[3].copy(mid)
        an = [newAnchor(P, p) for p in q]
        for k in range(1, 4):
            for j in range(k):
                if an[j].pos.distanceTo(an[k].pos) <= eps:
                    an[k] = an[j]
                    break
        A.append(an)
    if ends:                                                        # the root edges start on the two anchors
        o0 = A[0][0]; o1 = A[0][1]
        A[0] = [ends[0] if a is o0 else ends[1] if a is o1 else a for a in A[0]]
    for j, k in enumerate(cv):
        k.anchors = [a[j] for a in A]
    if ends:
        for k, x in ((cv[0], ends[0]), (cv[1], ends[1])):
            _trunk(P, k, x)
    for j, k in enumerate(cv):
        newCurveLook(k); _place(P, k, c.id, 1 + j); k.dirty = True
    for a, b in ((0, 1), (0, 2), (1, 3), (2, 3)):
        s = makeSurface(P, cv[a], cv[b])
        if s is not None:
            s.p['bulge'] = 0
    deleteItem(P, c.id); _dropUnused(P, c, start)
    P.gen += 1; P.rev += 1
    return cv, f'{c.name}: 4 curves and 4 walls (extruded; shared anchors where the edges meet)'


def ribConvert(P, c, cam_dir=None):
    """port of ribConvert (hair-modeler.clean.html:7157): ✔ Convert — two curves along the edges (anchors where the middle
    curve has them; a few more on a short curve), a flat wall between them; a pointed tip — one shared end anchor;
    started from two anchors — the edge curves start on them. The middle curve goes. A volume branch and an extruded
    lock go their own ways. Returns (the curves made — the selection after —, the message). The caller selects them
    (S.asel = [], S.sel = them) and saves."""
    if c.ribDirty or c.ribF is None:
        rebuildRib(P, c, cam_dir)
    if c.ribRings:
        return ribVolumeConvert(P, c)
    if c.ribX:
        return ribExtConvert(P, c)
    F = c.ribF
    if F is None or len(F) < 2:
        return [], NO_SHAPE
    n = len(c.anchors); N = len(F); idx = _indices(n, N); start = P.uid
    ends = ribEnds(P, c); L = makeCurve(P, c.parent); R = makeCurve(P, c.parent)
    L.name = c.name + ' L'; R.name = c.name + ' R'
    for cv, s in ((L, -1), (R, 1)):
        cv.anchors = [newAnchor(P, F[i].p.clone().addScaledVector(F[i].A, s * F[i].w)) for i in idx]
    if F[N - 1].w <= P.S.scale * 1e-4:
        t = newAnchor(P, F[N - 1].p); L.anchors[len(L.anchors) - 1] = t; R.anchors[len(R.anchors) - 1] = t
    # started from two anchors: each edge curve starts there and goes on back along that anchor's curve to its root — the
    # same anchors (a common trunk, a Y where the lock leaves it)
    if ends:
        for cv, x in ((L, ends[0]), (R, ends[1])):
            cv.anchors[0] = x
            _trunk(P, cv, x)
    newCurveLook(L); newCurveLook(R)                                # (Roundness 60%, Smoothing 50% — as drawn curves)
    for cv in (L, R):
        _place(P, cv, c.id, 1 if cv is L else 2); cv.dirty = True
    s = makeSurface(P, L, R)
    if s is not None:
        s.p['bulge'] = 0
    deleteItem(P, c.id); _dropUnused(P, c, start)
    P.gen += 1; P.rev += 1
    return [L, R], f'{c.name}: 2 curves and a wall' + (' (joined to the two anchors)' if ends else '')


# ---------------- drawing (addDrawPoint 6205-6234) ----------------

class LockDraw:
    """what the Lock mode of the Draw tool reads (ribDrawOn() true): `anchors` — S.asel at the first click, in pick
    order (anchor objects of other curves: 2 start a flat lock between them, 3 or more a volume branch); `ribR` —
    S.ribR (the circle's radius, px); `wpp(p)` — worldPerPixelAt(p) (anchorbrush.worldPerPixelAt with the view)"""
    __slots__ = ('anchors', 'ribR', 'wpp')

    def __init__(self, anchors=(), ribR: float = RIB_R, wpp=None):
        self.anchors = list(anchors); self.ribR = ribR; self.wpp = wpp


def lockPair(lock):
    # `pair` of addDrawPoint (6209): [...new Set(S.asel)] when 2 or more
    sel = list(dict.fromkeys(lock.anchors)) if lock is not None else []
    return sel if len(sel) >= 2 else None


def lockHostParent(P, pair):
    # addDrawPoint (6211): the group of the first anchor's curve (not a disc ring), or None (targetGroup)
    host = next((k for k in curvesWith(P, pair[0]) if not k.ring), None) if pair else None
    return host.parent if host is not None else None, host is not None


def startLock(P, c, pair, cam_dir):
    """addDrawPoint's first click in Lock mode (6213-6226): c.rib made; started from anchors, the root anchor at their
    centre (as wide as they span, its handle along their trunks) and the message — the click only starts the lock.
    Returns (the root anchor or None, the message or None)."""
    d = (ribTrunkDir(P, pair) or _cam(cam_dir)) if pair else None
    ends = ringOrder(pair, d) if pair and len(pair) > 2 else pair
    c.rib = {'k': {}, 'smooth': 0.4, 'tip': 0.25, 'view': _cam(cam_dir).toArray(), 'ends': [a.id for a in ends] if ends else None}
    if not ends:
        return None, None
    # the root: at the centre of the anchors, as wide as they span — wherever the first click was (that click only
    # starts the lock). It leaves along their trunks (the root's handle points the way the trunks go on)
    R0 = ribRoot(ends, d); r = newAnchor(P, R0.mid)
    c.anchors.append(r); c.rib['k'][_key(r)] = {'w': R0.w0, 'r': 0}
    if d is not None:
        c.ovr[r.id] = HandleOverride(in_=None, out=d.clone().multiplyScalar(js_max(R0.w0 * 2.5, P.S.scale * 0.01)))
    c.dirty = True; c.ribDirty = True
    return r, VOLUME_STARTS.format(n=len(ends)) if len(ends) > 2 else LOCK_STARTS


def lockPointWidth(P, c, a, p, lock, cam_dir):
    """addDrawPoint (6233): the width of a point added to a curve with `rib` — whatever the Draw mode (`if (c.rib)`):
    the circle's radius at that place (S.ribR · worldPerPixelAt(p): `lock` brings both); started from anchors: the
    root's width. A caller adding a point to a free lock without them is a programming error, never a width of 0."""
    e = ribEnds(P, c)
    if e:
        w = ribRoot(e, ribTrunkDir(P, e) or _cam(cam_dir)).w0
    else:
        if lock is None or lock.wpp is None or lock.ribR is None:
            raise ValueError('a point of a flat lock needs the circle radius and worldPerPixelAt (rib.LockDraw with ribR and wpp)')
        w = lock.ribR * lock.wpp(p)
    c.rib['k'][_key(a)] = {'w': w, 'r': 0}; c.ribDirty = True


# ---------------- the panels' data (ribAnchorProps 7055, ribCurveProps 7077) ----------------

def ribAnchorList(P, sel_anchors):
    """ribAnchorProps' list (6079-6080): (anchor, curve) for every selected anchor (S.asel, anchor ids in order) and each
    flat lock through it"""
    out = []
    for aid in sel_anchors:
        a = P.anchors.get(aid)
        if a is None:
            continue
        for c in curvesWith(P, a):
            if truthy(c.rib):
                out.append((a, c))
    return out


def ribAnchorPanel(P, sel_anchors):
    """what ribAnchorProps (7055) shows, or None (no flat lock anchor selected): the last pair's values — n (the list's
    length), w and r at its anchor, wmax (the Width slider's max, wmax / 400 its step), at_root and ends (the root of a
    lock started from anchors: no sliders, its shape follows them). Reads ribAnchorVals (which writes rib.k = {})."""
    lst = ribAnchorList(P, sel_anchors)
    if not lst:
        return None
    la, lc = lst[len(lst) - 1]
    cur = ribAnchorVals(P, lc); li = index_of(lc.anchors, la)
    ends = ribEnds(P, lc); w = cur['w'][li]
    return {'n': len(lst), 'w': w, 'r': cur['r'][li], 'wmax': js_max(w * 3, P.S.scale * 0.05),
            'at_root': bool(ends and li == 0), 'ends': len(ends) if ends else 0}


def ribAnchorSet(P, sel_anchors, key, v):
    """ribAnchorProps' set(key, v) (6083): the slider writes `key` ('w' half width | 'r' rotation °) at every selected
    anchor of every flat lock through it — an anchor without its own values gets the current ones first"""
    for a, c in ribAnchorList(P, sel_anchors):
        vals = ribAnchorVals(P, c); i = index_of(c.anchors, a)
        o = c.rib['k'].get(_key(a))
        if not truthy(o):
            o = c.rib['k'][_key(a)] = {'w': vals['w'][i], 'r': vals['r'][i]}
        o[key] = v; c.ribDirty = True


def ribProp(c, key, v):
    # ribCurveProps' sliders (6106): c.rib[key] = v (smooth, tip, extH, extFade, offFade)
    c.rib[key] = v; c.ribDirty = True


def ribPropValue(c, key):
    # the slider's value (6104): c.rib[key] ?? its default
    return nullish(c.rib.get(key), PROP_DEFAULTS[key])


def ribInvert(c):
    # ⇅ Invert extrude (6130)
    c.rib['extInv'] = not truthy(c.rib.get('extInv')); c.ribDirty = True


def ribClear(c, kind):
    # a brush section's Clear (6118): its painted weights removed
    for k in CLEAR_KEYS[kind]:
        c.rib[k] = None
    c.ribDirty = True


def ribPlain(c):
    # Plain curve (6147): the flat lock dropped, the curve kept
    c.rib = None; c.ribDirty = False


def ribBrushToggled(P):
    # the brush buttons' redraw (7089): every lock's colours change
    for k in P.curves():
        if truthy(k.rib):
            k.ribDirty = True


def ribCurvePanel(P, c):
    """what ribCurveProps (7077) shows: vol (a volume branch: no Extrude / Offset / Glue), n (its anchors), the sliders'
    values (smooth, tip, extH, extFade, offFade), extInv"""
    ne = ribEnds(P, c); vol = bool(ne and len(ne) > 2)
    out = {'vol': vol, 'n': len(ne) if ne else 0, 'extInv': truthy(c.rib.get('extInv'))}
    for k in PROP_DEFAULTS:
        out[k] = ribPropValue(c, k)
    return out
