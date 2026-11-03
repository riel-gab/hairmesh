# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Port of the Curve math of the original (hair-modeler.clean.html:789-1013, plus passRule at 4872-4897).

A curve is a chain of anchors; between two anchors runs a cubic Bezier whose control points are the anchors'
handles. Handles are never stored (except frozen overrides, c.ovr): curveHandles derives them from the anchors, the
neighbouring curves (welded trunks, Y junctions), Roundness (c.round) and Smoothing (c.fair). rebuildCurve then
samples every segment 24 times and accumulates the arc length.

Functions keep the original's names and take the Project `P` where the original reads the global `curves()`: the
scene order (file order) matters, several of them return the first curve that matches. Anchors are compared by
identity, as `===` does. See docs/js-para-python.md for the rest of the conventions.

An optional DecisionTrace records every comparison against a numeric threshold and which branch each handle
took, so a parity mismatch can be traced to the decision it sits next to.
"""
import math
from struct import pack as _pack

from .fdlibm import asin as _asin, tan as _tan
from .js import at, clamp, index_of, js_max, js_or, js_pow, js_round
from .model import Sample
from .trace import DecisionTrace, get_trace, set_trace, set_ctx, dec as _dec, br as _br   # noqa: F401  (re-exported for the tests)
from .vec3 import V3


# ---------------------------------------------------------------------------------------------------------------
# curve math
# ---------------------------------------------------------------------------------------------------------------
def bez(p0, p1, p2, p3, t, out=None):
    # port of bez (hair-modeler.clean.html:790)
    u = 1 - t
    if out is None:
        out = V3()
    return out.set(0, 0, 0).addScaledVector(p0, u * u * u).addScaledVector(p1, 3 * u * u * t).addScaledVector(p2, 3 * u * t * t).addScaledVector(p3, t * t * t)


# Tangent of a curve that passes THROUGH anchor `a` (a is interior there) — for Y-junctions
# Bessel tangent: the direction of the parabola through 3 anchors. The shorter chord gets more weight,
# so unevenly spaced anchors (e.g. a freshly inserted one) still give a clean arc instead of a kink.
def autoTangent(A, i):
    # port of autoTangent (hair-modeler.clean.html:797)
    if not (0 < i < len(A) - 1):                                      # A[i - 1] / A[i + 1] would be undefined in JS
        raise IndexError(f'autoTangent needs an interior anchor, got {i} of {len(A)}')
    d0 = A[i].pos.clone().sub(A[i - 1].pos); d1 = A[i + 1].pos.clone().sub(A[i].pos)
    h0 = d0.length(); h1 = d1.length()
    if _dec('autoTangent.h0', h0, 1e-9) < 1e-9:
        _br('autoTangent.degenerate'); return d1.normalize()
    if _dec('autoTangent.h1', h1, 1e-9) < 1e-9:
        _br('autoTangent.degenerate'); return d0.normalize()
    t = d0.multiplyScalar(h1 / h0).add(d1.multiplyScalar(h0 / h1))
    if _dec('autoTangent.tLengthSq', t.lengthSq(), 1e-14) < 1e-14:
        _br('autoTangent.collinearFallback'); return A[i + 1].pos.clone().sub(A[i - 1].pos).normalize()
    return t.normalize()


def junctionTangent(P, c, a):
    # port of junctionTangent (hair-modeler.clean.html:805)
    # (the original scans every curve; a curve not using `a` never passes `0 < k`, so only the curves using it are
    # tried, in the same curves() order: the first host found is the same)
    for h in P.anchor_curves().get(a.id, ()):
        if h is c or h.conn or h.junc:
            continue                                                  # curves ending on a crown connector leave it freely
        k = index_of(h.anchors, a)
        if 0 < k < len(h.anchors) - 1:
            return autoTangent(h.anchors, k)
    return None


# Curves welded along a run of anchors (a common trunk) share one tangent at every anchor of the run, including
# the fork where they part — so their shared segments are the very same curve. Curves that merely cross at one
# anchor (no common segment) don't affect each other.
def sharedTangent(P, c, i):
    # port of sharedTangent (hair-modeler.clean.html:816)
    A = c.anchors; a = A[i]; own = autoTangent(A, i)
    sum_ = own.clone(); n = 1
    for h in P.anchor_curves().get(a.id, ()):                      # (the curves using a, in curves() order: the same terms, summed in the same order)
        if h is c or h.junc:
            continue
        k = index_of(h.anchors, a)
        if k <= 0 or k >= len(h.anchors) - 1:
            continue
        hp = h.anchors[k - 1]; hn = h.anchors[k + 1]
        s = 0
        if hp is at(A, i - 1) or hn is at(A, i + 1):
            s = 1
        elif hp is at(A, i + 1) or hn is at(A, i - 1):
            s = -1
        if not s:
            continue
        sum_.addScaledVector(autoTangent(h.anchors, k), s); n += 1
    if n > 1:
        _br('sharedTangent.shared')
        return sum_.normalize() if _dec('sharedTangent.sumLengthSq', sum_.lengthSq(), 1e-12) > 1e-12 else own
    return own


# Circle through three points: centre, unit normal, radius (None when they lie on one line)
def circleThrough(a, b, c):
    # port of circleThrough (hair-modeler.clean.html:831)
    ab = b.clone().sub(a); ac = c.clone().sub(a); n = V3().crossVectors(ab, ac); n2 = n.lengthSq()
    lim = 1e-10 * ab.lengthSq() * ac.lengthSq()
    if _dec('circleThrough.n2', n2, lim) < lim:
        _br('circleThrough.collinear'); return None
    o = V3().crossVectors(n, ab).multiplyScalar(ac.lengthSq()).add(V3().crossVectors(ac, n).multiplyScalar(ab.lengthSq())).divideScalar(2 * n2).add(a)
    return {'o': o, 'n': n.normalize(), 'r': o.distanceTo(a)}


# Bezier handle length that reproduces a circular arc of radius r between two points `d` apart
def arcHandle(d, r):
    # port of arcHandle (hair-modeler.clean.html:838)
    th = 2 * _asin(clamp(d / (2 * r), 0, 1))                        # Math.asin / Math.tan = fdlibm's (V8), bit-identical
    return 4 / 3 * _tan(th / 4) * r


# Roundness (curve.round 0…1): the tangent and handle lengths at every anchor move towards those of the circular arc
# through the anchor and its two neighbours — 3 anchors: a parabola at 0, a circle arc at 1. Anchors shared with other
# curves keep the common tangent (welded trunks must stay identical).
def roundAt(P, c, i, dir, lenIn, lenOut):
    # port of roundAt (hair-modeler.clean.html:842)
    A = c.anchors; n = len(A); k = js_or(c.round, 0)
    if not (k > 0) or c.closed or n < 3 or any(h is not c for h in P.anchor_curves().get(A[i].id, ())):   # (shared with another curve)
        if k > 0:
            _br('roundAt.skipped')
        return None
    j = clamp(i, 1, n - 2); C = circleThrough(A[j - 1].pos, A[j].pos, A[j + 1].pos)
    if not C:
        return None
    Pp = A[i].pos; t = V3().crossVectors(C['n'], Pp.clone().sub(C['o'])).normalize()
    if _dec('roundAt.tDir', t.dot(A[j + 1].pos.clone().sub(A[j - 1].pos)), 0) < 0:
        t.negate()
    _br('roundAt.applied')
    return {
        'dir': dir.clone().lerp(t, k).normalize(),
        'lenIn': lenIn + (arcHandle(Pp.distanceTo(A[i - 1].pos), C['r']) - lenIn) * k if i > 0 else lenIn,
        'lenOut': lenOut + (arcHandle(Pp.distanceTo(A[i + 1].pos), C['r']) - lenOut) * k if i < n - 1 else lenOut,
    }


# A junction curve (Unite locks, c.junc) copies the curve it runs along on every shared segment and crosses the lock
# straight along the junction line; it never bends the curves it follows (sharedTangent / junctionTangent skip it)
def juncHandles(P, c, i):
    # port of juncHandles (hair-modeler.clean.html:856)
    A = c.anchors; Pp = A[i].pos
    _br('juncHandles')

    # the control point next to `at_` on the segment at_ → to of a non-junction curve that has it (either direction)
    def ctrl(at_, to):
        for h in P.curves():
            if h.junc or h is c:
                continue
            k = -1
            for j, x in enumerate(h.anchors):
                if x is at_ and at(h.anchors, j + 1) is to:
                    k = j; break
            if k >= 0:
                return curveHandles(P, h, k)['hout']
            q = -1
            for j, x in enumerate(h.anchors):
                if x is to and at(h.anchors, j + 1) is at_:
                    q = j; break
            if q >= 0:
                return curveHandles(P, h, q + 1)['hin']                # runs the other way
        return None

    hin = Pp.clone(); hout = Pp.clone()
    if i > 0:
        r = ctrl(A[i], A[i - 1]); hin = r if r is not None else Pp.clone().lerp(A[i - 1].pos, 1 / 3)
    if i < len(A) - 1:
        r = ctrl(A[i], A[i + 1]); hout = r if r is not None else Pp.clone().lerp(A[i + 1].pos, 1 / 3)
    return {'hin': hin, 'hout': hout}


# ---- junctions: every anchor next to `a` on any curve (the directions leaving the junction), by anchor id ----
def junctionNeighbours(P, a):
    # port of junctionNeighbours (hair-modeler.clean.html:4872); a Map → dict keeps insertion order
    m = {}
    for c in P.curves():
        if c.ring:
            continue
        A = c.anchors; i = index_of(A, a)
        if i < 0:
            continue
        if i > 0:
            m[A[i - 1].id] = A[i - 1]
        if i < len(A) - 1:
            m[A[i + 1].id] = A[i + 1]
    return m


# Rotate junction: anchor.pass = [id, id] — the two directions that pass SMOOTHLY through the junction; every other
# direction leaves along that smooth tangent (the sharp crotch). Returns the directions of the in / out handles of
# curve c at anchor i, or None when the anchor has no (valid) override.
def passRule(P, c, i):
    # port of passRule (hair-modeler.clean.html:4885)
    A = c.anchors; a = A[i]
    if not a.pass_ or c.closed or c.ring:
        return None
    nb = junctionNeighbours(P, a); p = nb.get(a.pass_[0]); q = nb.get(a.pass_[1])
    if p is None or q is None:
        return None
    T = autoTangent([p, a, q], 1)                                     # from p towards q

    def toward(n):
        if n is q:
            return {'d': T.clone(), 'br': False}
        if n is p:
            return {'d': T.clone().negate(), 'br': False}
        return {'d': T.clone() if _dec('passRule.toward', T.dot(n.pos.clone().sub(a.pos)), 0) >= 0 else T.clone().negate(), 'br': True}

    _br('passRule.applied')
    return {'din': toward(A[i - 1]) if i > 0 else None, 'dout': toward(A[i + 1]) if i < len(A) - 1 else None}


# derivatives of the natural C2 cubic spline through the anchors (chord-length parameters), cached per anchor positions
def fairSpline(c):
    # port of fairSpline (hair-modeler.clean.html:936)
    A = c.anchors; n = len(A); key = tuple((a.pos.x, a.pos.y, a.pos.z) for a in A)
    if c.fair_cache is not None and c.fair_cache['key'] == key:
        return c.fair_cache
    Pp = [a.pos for a in A]; h = []
    for i in range(n - 1):
        h.append(js_max(_dec('fairSpline.h', Pp[i].distanceTo(Pp[i + 1]), 1e-9), 1e-9))
    # tridiagonal system for D: natural ends (2 D0 + D1 = 3 d0, D(n−2) + 2 D(n−1) = 3 d(n−2)), inside
    # h(i) D(i−1) + 2 (h(i−1) + h(i)) D(i) + h(i−1) D(i+1) = 3 (h(i) d(i−1) + h(i−1) d(i)),  d(i) = (P(i+1) − P(i)) / h(i)
    d = [Pp[i + 1].clone().sub(Pp[i]).divideScalar(hh) for i, hh in enumerate(h)]
    a, b, cc, r = [], [], [], []
    for i in range(n):
        if i == 0:
            a.append(0); b.append(2); cc.append(1); r.append(d[0].clone().multiplyScalar(3))
        elif i == n - 1:
            a.append(1); b.append(2); cc.append(0); r.append(d[n - 2].clone().multiplyScalar(3))
        else:
            a.append(h[i]); b.append(2 * (h[i - 1] + h[i])); cc.append(h[i - 1]); r.append(d[i - 1].clone().multiplyScalar(3 * h[i]).addScaledVector(d[i], 3 * h[i - 1]))
    for i in range(1, n):                                             # Thomas algorithm
        w = a[i] / b[i - 1]; b[i] -= w * cc[i - 1]; r[i].addScaledVector(r[i - 1], -w)
    D = [None] * n; D[n - 1] = r[n - 1].divideScalar(b[n - 1])
    for i in range(n - 2, -1, -1):
        D[i] = r[i].addScaledVector(D[i + 1], -cc[i]).divideScalar(b[i])
    c.fair_cache = {'key': key, 'D': D, 'h': h}
    return c.fair_cache


def _has_in(o):
    return o is not None and o.in_ is not None


def _has_out(o):
    return o is not None and o.out is not None


def curveHandles(P, c, i):
    # port of curveHandles (hair-modeler.clean.html:881)
    saved = set_ctx(c.id, i)
    try:
        return _curveHandles(P, c, i)
    finally:
        set_ctx(*saved)


def _curveHandles(P, c, i):
    if c.junc:
        return juncHandles(P, c, i)
    A = c.anchors; n = len(A); Pp = A[i].pos; sm = A[i].smooth
    o = c.ovr.get(A[i].id)
    lenIn = Pp.distanceTo(A[i - 1].pos) / 3 if i > 0 else 0
    lenOut = Pp.distanceTo(A[i + 1].pos) / 3 if i < n - 1 else 0
    pr = passRule(P, c, i)                                            # rotated junction (anchor.pass)
    if pr:
        def h(side, ln):
            r = pr[side]
            return Pp.clone().addScaledVector(r['d'], ln * sm * (1.35 if r['br'] else 1)) if r else Pp.clone()
        return {'hin': Pp.clone().addScaledVector(o.in_, sm) if _has_in(o) else h('din', lenIn),
                'hout': Pp.clone().addScaledVector(o.out, sm) if _has_out(o) else h('dout', lenOut)}
    if c.closed:                                                      # ring: neighbours wrap around
        _br('curveHandles.closed')
        T = [A[(i + n - 1) % n], A[i], A[(i + 1) % n]]
        dir = autoTangent(T, 1); lenIn = Pp.distanceTo(T[0].pos) / 3; lenOut = Pp.distanceTo(T[2].pos) / 3
    elif 0 < i < n - 1:
        _br('curveHandles.interior')
        dir = sharedTangent(P, c, i)
        r = roundAt(P, c, i, dir, lenIn, lenOut)
        if r:
            dir, lenIn, lenOut = r['dir'], r['lenIn'], r['lenOut']
    else:
        k = 1 if i == 0 else n - 2
        b = A[k].pos.clone().sub(Pp).normalize()                      # direction from end toward its neighbour
        j = junctionTangent(P, c, A[i])
        if j:
            # Y-junction: our branch leaves along the host branch that forms the smaller angle -> sharp crotch,
            # the host curve passes smoothly (obtuse angles stay rounded)
            _br('curveHandles.end.junction')
            d = j.clone().multiplyScalar(1 if _dec('curveHandles.junctionSide', b.dot(j), 0) >= 0 else -1)
            dir = d if i == 0 else d.clone().negate()
            if i == 0:
                lenOut *= 1.35
            else:
                lenIn *= 1.35
        else:
            # natural end: aim at the neighbour's facing handle -> the end segment is a smooth arc
            _br('curveHandles.end.natural')
            aim = b
            if n > 2:
                hk = curveHandles(P, c, k)
                t = (hk['hin'] if i == 0 else hk['hout']).clone().sub(Pp)
                if _dec('curveHandles.aimLengthSq', t.lengthSq(), 1e-14) > 1e-14:
                    aim = t.normalize()
            dir = aim if i == 0 else aim.clone().negate()
            r = roundAt(P, c, i, dir, lenIn, lenOut)
            if r:
                dir, lenIn, lenOut = r['dir'], r['lenIn'], r['lenOut']
    if _has_in(o):
        _br('curveHandles.ovr.in')
    if _has_out(o):
        _br('curveHandles.ovr.out')
    hin = Pp.clone().addScaledVector(o.in_, sm) if _has_in(o) else Pp.clone().addScaledVector(dir, -lenIn * sm)
    hout = Pp.clone().addScaledVector(o.out, sm) if _has_out(o) else Pp.clone().addScaledVector(dir, lenOut * sm)
    # Smoothing (c.fair 0…1): the handles move towards those of the C2 spline through the same anchors — curvature
    # without jumps, the smoothest curve the anchors allow. Frozen handles and anchors shared with other curves (welded
    # trunks, junctions) stay as they are.
    k = js_or(c.fair, 0)
    if k > 0 and not c.closed and n >= 3 and not any(h is not c for h in P.anchor_curves().get(A[i].id, ())):   # (shared with another curve)
        _br('curveHandles.fair')
        F = fairSpline(c)
        if i > 0 and not _has_in(o):
            hin.lerp(Pp.clone().addScaledVector(F['D'][i], -F['h'][i - 1] / 3 * sm), k)
        if i < n - 1 and not _has_out(o):
            hout.lerp(Pp.clone().addScaledVector(F['D'][i], F['h'][i] / 3 * sm), k)
    return {'hin': hin, 'hout': hout}


# sample points of a junction curve's own segments (the junction line — no other curve runs there)
def juncLine(P, c):
    # port of juncLine (hair-modeler.clean.html:873)
    own = set()
    for i in range(len(c.anchors) - 1):
        a = c.anchors[i]; b = c.anchors[i + 1]
        shared = False
        for h in P.curves():
            if h.junc or h is c:
                continue
            for k, x in enumerate(h.anchors):
                if (x is a and at(h.anchors, k + 1) is b) or (x is b and at(h.anchors, k + 1) is a):
                    shared = True; break
            if shared:
                break
        if not shared:
            own.add(i)
    return [s.p for s in c.samples if s.seg in own]


def curveSegs(P, c):
    # port of curveSegs (hair-modeler.clean.html:985): [p0, hout, hin, p3] per Bezier segment
    A = c.anchors; Hs = [curveHandles(P, c, i) for i in range(len(A))]; segs = []
    for i in range(len(A) - 1):
        segs.append([A[i].pos, Hs[i]['hout'], Hs[i + 1]['hin'], A[i + 1].pos])
    if c.closed and len(A) > 2:
        l = len(A) - 1; segs.append([A[l].pos, Hs[l]['hout'], Hs[0]['hin'], A[0].pos])
    return segs


def sampleCurve(P, c, per=24):
    # port of sampleCurve (hair-modeler.clean.html:991)
    out = []; segs = curveSegs(P, c)
    for si, s in enumerate(segs):
        for k in range(per):
            out.append(Sample(p=bez(s[0], s[1], s[2], s[3], k / per), seg=si))
    l = segs[len(segs) - 1]; out.append(Sample(p=l[3].clone(), seg=len(segs) - 1))
    return out


def _bits(v):
    """a vector's coordinates as their bytes: -0.0 and 0.0 are two keys (== would take them as one)"""
    return _pack('<3d', v.x, v.y, v.z)


def _anchorKey(a):
    return None if a is None else (a.id, _bits(a.pos))


def sampleKey(P, c):
    """Everything sampleCurve reads to sample a curve that is not a junction curve (ours, lot ux8, the rule of phase
    2.5: a memo that changes no bit): its flags, anchors (id, position, smoothness, pass) and frozen handles, and at
    every anchor the other curves through it — what junctionTangent, sharedTangent, roundAt, the Smoothing test and
    passRule's junctionNeighbours read of them (their flags, where the anchor is in them, its neighbours there), in
    curves() order. A junction curve copies the handles of the curves it runs along (juncHandles: their whole
    neighbourhood), so it has no key: always sampled anew. Roundness and Smoothing count only where they are read (an
    open curve of 3+ anchors; a value that is not > 0 is no smoothing), so a dimmed slider keeps the samples."""
    if c.junc:
        return None
    ac = P.anchor_curves(); an = []
    for a in c.anchors:
        nb = []
        for h in ac.get(a.id, ()):
            if h is c:
                continue
            A = h.anchors; k = index_of(A, a)
            nb.append((h.id, h.junc, h.conn, h.ring, h.closed, k, len(A), _anchorKey(A[k - 1] if k > 0 else None), _anchorKey(A[k + 1] if 0 <= k < len(A) - 1 else None)))
        an.append((a.id, _bits(a.pos), a.smooth, tuple(a.pass_) if a.pass_ else None, tuple(nb)))
    ovr = tuple((k, None if o.in_ is None else _bits(o.in_), None if o.out is None else _bits(o.out)) for k, o in c.ovr.items())
    shapes = not c.closed and len(c.anchors) >= 3                  # Roundness / Smoothing are read only then (roundAt 843, curveHandles 929)
    rnd = js_or(c.round, 0); fair = js_or(c.fair, 0)
    return (c.closed, c.ring, rnd if shapes and rnd > 0 else 0, fair if shapes and fair > 0 else 0, tuple(an), ovr)


def _sameSamples(a, b):
    """the very same points, bit for bit (a signed zero counts)"""
    if len(a) != len(b) or not b:
        return False
    for s, t in zip(a, b):
        p = s.p; q = t.p
        if p.x != q.x or p.y != q.y or p.z != q.z or s.seg != t.seg:
            return False
        if (p.x == 0 or p.y == 0 or p.z == 0) and _bits(p) != _bits(q):
            return False
    return True


def rebuildCurve(P, c):
    # port of rebuildCurve (hair-modeler.clean.html:1325), minus the line geometry (a Blender object does that job).
    # Ours (lot ux8): a curve whose sampleKey is the one its samples were made from keeps them — the very list, so
    # nobody sees it as changed: the original marks every curve dirty after a delete (deleteItem 785), a weld… and
    # resampled them all (0.2 s in the example), and every lock counted as changed (4.6 s). A junction curve (no
    # key) is resampled and keeps its old list when the points come out the same. Not while a decision trace records
    # (the parity tests read every branch).
    c.dirty = False
    if len(c.anchors) < 2:
        c.samples = []; c.sample_key = None; return
    key = sampleKey(P, c) if get_trace() is None else None
    if key is not None and key == c.sample_key and c.samples:
        return
    S = sampleCurve(P, c)
    c.sample_key = key                                              # (after: a sampling that raised leaves the old key with the old samples)
    if key is None and get_trace() is None and _sameSamples(S, c.samples):
        return                                                      # (a junction curve: resampled, the very same points)
    c.samples = S
    c.arc = [0]
    for i in range(1, len(c.samples)):
        c.arc.append(c.arc[i - 1] + c.samples[i].p.distanceTo(c.samples[i - 1].p))


def adoptSamples(P, old):
    """Ours (lot 3.7-estab): a project read again from its text (an undo, a redo) takes the sample lists of the
    instance before, with the key each was made from (sample_key); its curves stay dirty, and rebuildCurve keeps a list
    only when the key it computes now is that one — the rule of the lot ux8, so no bit changes. Resampling the 333
    curves of the example after every undo cost ~0.25 s."""
    if old is None:
        return 0
    n = 0
    for c in P.curves():
        oc = old.items.get(c.id)
        if oc is not None and getattr(oc, 'type', None) == 'curve' and oc.sample_key is not None and oc.samples:
            c.samples = oc.samples; c.arc = oc.arc; c.sample_key = oc.sample_key; n += 1
    return n


def markAnchorsDirty(P, anchors):
    # port of markAnchorsDirty (hair-modeler.clean.html:1334): the curves using a moved anchor are dirty, and so are
    # the curves touching them (Y-junction tangents depend on neighbours)
    aset = set(anchors); cs = P.curves()
    dirty = {c: True for c in cs if any(a in aset for a in c.anchors)}
    touched = set()
    for c in dirty:
        for a in c.anchors:
            touched.add(a)
    for c in cs:
        if any(a in touched for a in c.anchors):
            dirty[c] = True
    for c in dirty:
        c.dirty = True


def rebuild_dirty(P):
    """What the frame loop does for curves (11311): rebuild every dirty curve. Order does not matter: handles only
    depend on anchor positions, never on other curves' samples."""
    n = 0
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c); n += 1
    return n


# ---------------------------------------------------------------------------------------------------------------
# Even out anchors (956-983) and the handle fit of "delete, keep shape" (1000)
# ---------------------------------------------------------------------------------------------------------------
# ---- Even out anchors (c.asm 0…1): the anchors move — the wobble of a wavy curve (e.g. made from an uneven edge path)
# smooths out while the overall shape stays (Taubin smoothing: alternate shrink / inflate steps, no shrinking). Always
# computed from the positions before (c.asrc), so the slider can go back; anchors moved by hand meanwhile start anew.
# Curve ends, anchors shared with other curves and attached anchors stay. ----
def evenIters(v):
    # port of evenIters (hair-modeler.clean.html:960): gentle at the start of the slider (`** 2` is x * x in V8)
    return js_round(js_pow(clamp(v, 0, 1), 2) * 60)


def taubin(P, fixed, closed, iters):
    # port of taubin (hair-modeler.clean.html:961)
    X = [p.clone() for p in P]; n = len(X)
    for it in range(iters * 2):
        k = -0.53 if it % 2 else 0.5
        Y = [p.clone() for p in X]
        for i in range(n):
            if fixed[i]:
                continue
            a = X[(i + n - 1) % n] if closed else at(X, i - 1)
            b = X[(i + 1) % n] if closed else at(X, i + 1)
            if a is None or b is None:
                continue
            Y[i].addScaledVector(a.clone().add(b).multiplyScalar(0.5).sub(X[i]), k)
        X = Y
    return X


def evenAnchors(P, lst, v):
    # port of evenAnchors (hair-modeler.clean.html:971): the "Even out anchors" slider of the curve panel, on every
    # curve of `lst` (the selected curves). The anchors are recomputed from c.asrc (the positions before the first
    # use of the slider); when the current positions are no longer what asrc + asm give (an anchor was moved by hand),
    # asrc starts anew from the current positions.
    touched = []
    cs = P.curves()
    for c in lst:
        if c.ring or len(c.anchors) < 3:
            continue
        n = len(c.anchors)
        fixed = [(not c.closed and (i == 0 or i == n - 1)) or bool(a.att) or any(h is not c and a in h.anchors for h in cs) for i, a in enumerate(c.anchors)]
        valid = (c.asrc is not None and len(c.asrc) == n
                 and all(p.distanceTo(c.anchors[i].pos) < 1e-6 * P.S.scale for i, p in enumerate(taubin(c.asrc, fixed, c.closed, evenIters(js_or(c.asm, 0))))))
        if not valid:
            c.asrc = [a.pos.clone() for a in c.anchors]
        for i, p in enumerate(taubin(c.asrc, fixed, c.closed, evenIters(v))):
            c.anchors[i].pos.copy(p)
        c.asm = v; touched.extend(c.anchors)
    markAnchorsDirty(P, touched)
    P.rev += 1


def fitHandles(pts, P0, P3, d1, d2):
    # port of fitHandles (hair-modeler.clean.html:1000): the handle lengths a, b (P0 + a d1, P3 + b d2) of the cubic
    # that best fits the polyline `pts` in the least-squares sense (chord-length parameters); a degenerate fit or a
    # non-positive length falls back to a third of the chord
    L = [0]
    for k in range(1, len(pts)):
        L.append(L[k - 1] + pts[k].distanceTo(pts[k - 1]))
    tot = js_or(L[len(L) - 1], 1)
    c11 = 0; c12 = 0; c22 = 0; x1 = 0; x2 = 0
    for k, p in enumerate(pts):
        t = L[k] / tot; u = 1 - t; b0 = u * u * u; b1 = 3 * u * u * t; b2 = 3 * u * t * t; b3 = t * t * t
        A1 = d1.clone().multiplyScalar(b1); A2 = d2.clone().multiplyScalar(b2)
        r = p.clone().sub(P0.clone().multiplyScalar(b0 + b1)).sub(P3.clone().multiplyScalar(b2 + b3))
        c11 += A1.dot(A1); c12 += A1.dot(A2); c22 += A2.dot(A2); x1 += r.dot(A1); x2 += r.dot(A2)
    det = c11 * c22 - c12 * c12; chord = P0.distanceTo(P3) / 3
    a = chord; b = chord
    if abs(det) > 1e-14:
        a = (x1 * c22 - x2 * c12) / det; b = (c11 * x2 - c12 * x1) / det
    if not (a > 1e-6):
        a = chord
    if not (b > 1e-6):
        b = chord
    return {'a': a, 'b': b}
