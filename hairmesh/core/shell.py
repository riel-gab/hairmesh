# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The Proxy shell and Even loops modifiers in the row tables (1.0.3; lines of reference/v1.0.3/hair-modeler.clean.html).

A Proxy shell (item type 'shell', 2962-2970): a simplified tube pulled over a stretch of a lock like a stocking, painted
with its brush on the curves of the group's locks (k.mask, like Density) and converted (k.baked): the painted stretch
(mask >= 50%) becomes the shell — a smoothed axis through the painted locks with elliptic rings round them — and the
loops there are the shell's rings: cut square to its axis, spaced by the loop step, closer on its bends or strictly even
by Equal spacing (p.eq), fading in from its ends over Edge falloff (p.fall). The Even loops modifier (type 'even') is
such a shell too, never drawn: always on where painted, its rings strictly even, their weight x its Strength (p.str).

shellSets(locks) (3008) builds every shell's rings once, before the locks are built; shellAdopt (3362) puts them into a
lock's loops, shellParaCut (3470) cuts the curves exactly by them, shellKeep (3510) keeps them through Fix sharp angles.
The rest is in rows.py (buildRowTables, enforceMaxGap, backfillTrunk).

Not kept (drawing only, never read by the tables): k._rings, k._surf (the stocking drawn), k._geoAt (the Brush grabs
the deformation grid there), k._locks (shellLockList)."""
import copy
import math
from bisect import bisect_left

from .density import lockTip, shellsOf, tipMaskW
from .fdlibm import atan2 as _atan2, cos as _cos, sin as _sin
from .geom import atF, nearestF, nearestWithin, polyLen
from .headobj import float32
from .js import clamp, js_max, js_min, js_or, js_round, nullish, truthy
from .model import SHELL_DEFAULTS
from .rowbase import Row, convOf, levelH, rowPlane
from .vec3 import V3

SHELL_SIDES = 16                                                    # the stocking drawn: an ellipse at each ring (3515)


def _sst(t):
    """shellSetsBuild's sst (3051): smoothstep of t clamped to 0 … 1"""
    t = clamp(t, 0, 1)
    return t * t * (3 - 2 * t)


def _dot_from(q, o, n):
    """q.clone().sub(o).dot(n), on scalars (the same operations in the same order)"""
    return (q.x - o.x) * n.x + (q.y - o.y) * n.y + (q.z - o.z) * n.z


def _samples(c):
    return getattr(c, 'samples', None)


def _off(k):
    return getattr(k, 'off', None)


def _def(k):
    return getattr(k, 'def_', None)


def _src(k):
    """k.src || k: the Even loops item a part (evenParts) was cut from, or the item itself"""
    return getattr(k, 'src', None) or k


# A deformation saved without a version (v) — made on the older, more smoothed shell or on the new one, the file doesn't
# say: built both ways once, the one whose rings sit better on the painted curves kept (v 1 — old, 2 — new)
def shellFitScore(P, st):
    # port of shellFitScore (hair-modeler.clean.html:2989, 1.0.3)
    k = st['k']
    cs = [c for c in (P.items.get(i) for i in sorted(k.mask or {})) if c and _samples(c) and len(c.samples) > 1]   # (Object.keys: integer keys ascending)
    s = 0; n = 0
    for pl in st['planes']:
        if pl['w'] < 0.99:
            continue
        c = V3(); m = 0
        o = pl['p']; nv = pl['n']
        for cv in cs:
            Pp = cv.samples; W = k.mask[cv.id]; best = None; bd = math.inf
            for i in range(len(Pp) - 1):
                d0 = _dot_from(Pp[i].p, o, nv); d1 = _dot_from(Pp[i + 1].p, o, nv)
                if d0 == d1 or d0 * d1 > 0:
                    continue
                if W[js_round(i / (len(Pp) - 1) * (len(W) - 1))] < 0.5:
                    continue
                q = Pp[i].p.clone().lerp(Pp[i + 1].p, d0 / (d0 - d1)); dd = q.distanceTo(o)
                if dd < bd:
                    bd = dd; best = q
            if best is not None:
                c.add(best); m += 1
        if m:
            s += c.divideScalar(m).distanceTo(o) / js_max(pl['ra'], 1e-6); n += 1
    return s / n if n else math.inf


def shellSets(ctx, locks):
    """port of shellSets (hair-modeler.clean.html:3008, 1.0.3): the proxy shells' sets, lock -> [set] (a dict keyed by
    the Lock, in the original's Map order). A baked, visible shell with a deformation saved without v is built both ways
    and the better fit WRITTEN into the item (k.def.v): the model changes inside the build — ctx.MODEL_CHANGED (the
    caller saves it, as for tipConvert) and P.gen."""
    P = ctx.P
    und = [k for k in P.shells() if k.def_ is not None and k.def_.v is None and k.baked and k.visible is not False and k.mask is not None]
    if und:
        sc = {}
        for v in (1, 2):
            for k in und:
                k.def_.v = v
            r = shellSetsBuild(ctx, locks)
            for k in und:
                st = next((x for sets in r.values() for x in sets if x['k'] is k), None)
                sc[(k.id, v)] = shellFitScore(P, st) if st is not None else math.inf
        for k in und:
            k.def_.v = 1 if sc[(k.id, 1)] < sc[(k.id, 2)] else 2
        P.gen += 1; ctx.MODEL_CHANGED = True                        # (an item changed: the memos keyed on gen go; the caller saves it)
    return shellSetsBuild(ctx, locks)


# An Even loops modifier painted in several separate places: each is one stretch of its own (built as one, the rings
# ran from the first painted place to the last and turned over the whole way between them — the mesh fell apart). The
# painted runs of its curves (mask >= 50%) joined where they come within 2 loop steps of each other -> parts { src: k,
# mask: the part's runs only } (the same id / name / settings)
def evenParts(ctx, k):
    # port of evenParts (hair-modeler.clean.html:3021, 1.0.3); a part is a shallow copy of the item ({ ...k, mask, src: k })
    if k.mask is None:
        return [k]
    P = ctx.P; h = levelH(ctx); runs = []
    for cid in sorted(k.mask):                                      # (Object.entries: integer keys ascending)
        W = k.mask[cid]; c = P.items.get(cid)
        if not c or not _samples(c) or len(c.samples) < 2:
            continue
        i = 0; nW = len(W); ns = len(c.samples)
        while i < nW:
            if W[i] < 0.5:
                i += 1; continue
            j = i
            while j + 1 < nW and W[j + 1] >= 0.5:
                j += 1
            a = max(0, i - 1); b = min(nW - 1, j + 1)                # (the run with its soft edges)
            pts = [c.samples[js_round(q / (nW - 1) * (ns - 1))].p for q in range(i, j + 1)]
            runs.append({'id': cid, 'a': a, 'b': b, 'pts': pts}); i = j + 1
    if len(runs) < 2:
        return [k]
    par = list(range(len(runs)))

    def find(i):
        r = i
        while par[r] != r:
            r = par[r]
        while par[i] != r:                                          # (the original's path compression: the same roots)
            par[i], i = r, par[i]
        return r

    def near(A, B):
        return any(any(p.distanceTo(q) < 2 * h for q in B['pts']) for p in A['pts'])
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            if find(i) != find(j) and near(runs[i], runs[j]):
                par[find(i)] = find(j)
    groups = {}
    for i, r in enumerate(runs):
        groups.setdefault(find(i), []).append(r)
    if len(groups) < 2:
        return [k]
    # (a stray touch of the brush — a part of fewer than 12 painted points — makes no stretch of its own: a few dabs on the
    # far curves made 3 rings in the middle of a proxy shell, two stretch ends right next to each other, the mesh a mess)
    big = [rs for rs in groups.values() if sum(len(r['pts']) for r in rs) >= 12]
    if not big:
        return []
    out = []
    for rs in big:
        mask = {}
        for r in rs:
            W = k.mask[r['id']]; M = mask.get(r['id'])
            if M is None:
                M = mask[r['id']] = [0] * len(W)
            for q in range(r['a'], r['b'] + 1):
                M[q] = W[q]
        part = copy.copy(k); part.mask = mask; part.src = k
        out.append(part)
    return out


# The shell's deformation (the Brush tool on a selected, converted shell): k.def = { ns, na, d } — a grid of ns points
# along the shell (s 0 … 1) x na round it (angle a), each a displacement in the ring's own frame [along the axis (n),
# along the long axis (u), across (v)]; a coarse grid read back smoothly — Catmull-Rom both ways
def shellDefLocal(k, s, a):
    # port of shellDefLocal (hair-modeler.clean.html:3521, 1.0.3)
    D = _def(k)
    if D is None:
        return None

    def cr(p0, p1, p2, p3, t):
        return 0.5 * (2 * p1 + (p2 - p0) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (3 * p1 - p0 - 3 * p2 + p3) * t * t * t)
    ns = D.ns; na = D.na; dd = D.d
    x = clamp(s, 0, 1) * (ns - 1); i1 = min(math.floor(x), ns - 2); tx = x - i1
    y = math.fmod(math.fmod(a / (2 * math.pi), 1) + 1, 1) * na; j1 = math.floor(y) % na; ty = y - math.floor(y)   # (JS % is fmod)

    def g(i, j, c):
        return dd[(clamp(i, 0, ns - 1) * na + ((j % na) + na) % na) * 3 + c]
    out = [0, 0, 0]
    for c in range(3):
        def row(i):
            return cr(g(i, j1 - 1, c), g(i, j1, c), g(i, j1 + 1, c), g(i, j1 + 2, c), ty)
        out[c] = cr(row(i1 - 1), row(i1), row(i1 + 1), row(i1 + 2), tx)
    return out


# the deformation's mean shift along the axis at s (round the ring) — at the ends it moves the shell's end (shellSets)
def shellAxMean(k, s):
    # port of shellAxMean (hair-modeler.clean.html:3534, 1.0.3)
    D = _def(k)
    if D is None:
        return 0
    m = 0; n = D.na
    for j in range(n):
        v = shellDefLocal(k, s, 2 * math.pi * j / n)
        assert v is not None                                        # (the same _def(k) as above: not None)
        m += v[0]
    return m / n


# a ring's points, deformed; r.ax — the mean axial shift already laid into the ring's place when it was made
def shellRingPts(k, r):
    # port of shellRingPts (hair-modeler.clean.html:3536, 1.0.3)
    out = []
    for b in r['base']:
        L = shellDefLocal(k, r['s'], b['a'])
        out.append(b['p'].clone().addScaledVector(r['n'], L[0] - js_or(r['ax'], 0)).addScaledVector(r['u'], L[1]).addScaledVector(r['v'], L[2]) if L else b['p'].clone())
    return out


def _median_rad(gl, rMax):
    """the TYPICAL radius of shellSetsBuild (3077, 3119): the median of the locks' radii (sorted numerically), else rMax"""
    r = sorted(x for x in (js_or(e['L'].rad, 0) for e in gl) if x > 0)
    return r[len(r) >> 1] if r else rMax


# One shell over SEVERAL locks: a tail made of locks welded side by side (a groove, a branch — the fork between them)
# is one tube (the original's comment block at 2979-2988). For each converted, visible shell the locks it covers; along
# the longest one's spine the shell's centre (the mean of the covered locks' spines near there), smoothed; its
# cross-section an ellipse round their curves; its RINGS made here, once: every lock running inside the stocking takes
# exactly those planes (shellAdopt).
def shellSetsBuild(ctx, locks):
    # port of shellSetsBuild (hair-modeler.clean.html:3050, 1.0.3): Map lock -> [set { k, P, locks, sps, w, planes, … }]
    P = ctx.P; DBG = ctx.DBG
    out = {}
    ks = list(P.shells())
    for e in P.evens():
        ks.extend(evenParts(ctx, e))
    for k in ks:
        ev = k.type == 'even'
        # (k._rings = null: the stocking drawn, not kept)
        if k.visible is False or not (truthy(getattr(k, 'baked', None)) or ev) or k.mask is None:
            continue
        off = set(_off(k) or []); cov = []; src = _src(k)
        for L in locks:
            if L.key in off or not any(x is src for x in shellsOf(P, L)):
                continue
            m = tipMaskW(k, L)
            if m is not None and any(v >= 0.5 for v in m):
                cov.append({'L': L, 'm': m})
        if not cov:
            continue
        cov.sort(key=lambda x: -x['L'].len)                         # (b.L.len - a.L.len, stable)
        Pl = cov[0]['L']; psp = Pl.sp; N = len(psp); D = [0]
        for i in range(1, N):
            D.append(D[i - 1] + psp[i].distanceTo(psp[i - 1]))
        # the locks WELDED to a painted one (sharing a segment) are part of the same tail: the shell is fitted round them too
        # within the painted zone
        segs = set()
        for x in cov:
            for c in x['L'].cs:
                for q in range(len(c.anchors) - 1):
                    a = c.anchors[q]; b = c.anchors[q + 1]
                    segs.add((a.id, b.id) if a.id < b.id else (b.id, a.id))

        def weldedTo(L):
            for c in L.cs:
                A = c.anchors
                for q in range(len(A) - 1):
                    a = A[q]; b = A[q + 1]
                    if ((a.id, b.id) if a.id < b.id else (b.id, a.id)) in segs:
                        return True
            return False
        welded = [L for L in locks if not any(x['L'] is L for x in cov) and L.key not in off and weldedTo(L)]
        gl = cov + [{'L': L, 'm': None} for L in welded]
        rMax = js_or(js_max(*[js_or(x['L'].rad, 0) for x in gl]), 1e-6)
        # each lock near each point of the carrier's spine (its nearest spine point within reach; not past a lock's end)
        # (Even loops: within 2 TYPICAL radii)
        rNear = _median_rad(gl, rMax) if ev else rMax
        near = []
        for x in gl:
            XL = x['L']; lst = []
            for i in range(N):
                if XL is Pl:
                    hit = (i, 0)                                    # (P.sp.indexOf(p): its own i-th point)
                else:
                    hit = nearestWithin(XL.sp, psp[i], 2 * rNear, XL.grids, XL.spLen)
                lst.append(hit if hit is not None and (XL is Pl or (hit[0] > 0.5 and hit[0] < len(XL.sp) - 1.5)) else None)
            near.append(lst)
        C = []; mU = [0.0] * N                                      # (mU: a Float32Array — every write rounded to float32)
        if not ev:
            for i in range(N):
                for li, x in enumerate(cov):
                    hit = near[li][i]
                    if hit is not None:
                        m = x['m']; mU[i] = float32(js_max(mU[i], m[clamp(js_round(hit[0]), 0, len(m) - 1)]))
        else:
            # (Even loops: its stretch on the carrier straight from the painted points of the curves, each to the nearest
            # point of the carrier's spine)
            for cid in sorted(k.mask):                              # (Object.entries: integer keys ascending)
                W = k.mask[cid]; c = P.items.get(cid)
                if not c or not _samples(c) or len(c.samples) < 2 or not any(any(cc is c for cc in x['L'].cs) for x in cov):
                    continue
                ns = len(c.samples)
                for q in range(len(W)):
                    if W[q] < 0.5:
                        continue
                    hh = nearestWithin(psp, c.samples[js_round(q / (len(W) - 1) * (ns - 1))].p, 2 * rNear, Pl.grids, Pl.spLen)
                    if hh is None:
                        continue
                    i = clamp(js_round(hh[0]), 0, N - 1); mU[i] = float32(js_max(mU[i], W[q]))
        for i in range(N):
            c = V3(); sw = 0
            # (a painted lock counts only where it is painted itself; a welded one only inside the painted zone)
            for li, x in enumerate(gl):
                hit = near[li][i]
                if hit is None:
                    continue
                m = x['m']
                ok = (x['L'] is Pl or m[clamp(js_round(hit[0]), 0, len(m) - 1)] >= 0.5) if m is not None else mU[i] >= 0.5
                if not ok:
                    near[li][i] = None; continue
                wt = len(x['L'].cs); c.addScaledVector(atF(x['L'].sp, hit[0]), wt); sw += wt
            C.append(c.divideScalar(sw) if sw else psp[i].clone())
        # (Even loops: no fade — every ring of its stretch at full strength)
        w = [0.0] * N; fall = 0 if ev else clamp(nullish(k.p.get('fall'), SHELL_DEFAULTS['fall']), 0, 1)   # (w: a Float32Array)
        i = 0
        while i < N:                                                # every painted stretch: full inside, fading to its ends
            if mU[i] < 0.5:
                i += 1; continue
            j = i
            while j + 1 < N and mU[j + 1] >= 0.5:
                j += 1
            a = D[i]; b = D[j]; r = fall * 0.5 * (b - a)
            for q in range(i, j + 1):
                w[q] = float32(_sst(js_min(D[q] - a, b - D[q]) / r) if r > 1e-9 else 1)
            i = j + 1
        if not any(x > 0 for x in w):
            continue
        # (smoothed over ~3 radii — a TYPICAL radius, the median of the locks')
        rTyp = _median_rad(gl, rMax)
        # (a shell deformed before that keeps the old window)
        kd = _def(k)
        legacy = kd is not None and kd.v == 1 and not isinstance(kd.v, bool)
        # (Even loops: smoothed over 3 x the largest radius; not where it reaches a lock's end)
        evAtEnd = False
        if ev:
            za = -1; zb = -1
            for q in range(N):
                if w[q] > 0:
                    if za < 0:
                        za = q
                    zb = q
            evAtEnd = za >= 0 and (D[za] < 1.5 * levelH(ctx) or D[N - 1] - D[zb] < 1.5 * levelH(ctx))
        unit = js_or(D[N - 1], 1e-9) / (N - 1)
        ws = clamp(js_round(3 * (rMax if (legacy or (ev and not evAtEnd)) else rTyp) / unit), 2, math.floor(N / 4))

        def sm(get):
            S = [0.0] * (N + 1)                                     # (a Float64Array)
            for j in range(N):
                S[j + 1] = S[j] + get(j)
            res = []
            for i in range(N):
                r = min(ws, i, N - 1 - i); res.append((S[i + r + 1] - S[i - r]) / (2 * r + 1))
            return res
        sx = sm(lambda i: C[i].x); sy = sm(lambda i: C[i].y); sz = sm(lambda i: C[i].z)
        sps = [V3(x, sy[i], sz[i]) for i, x in enumerate(sx)]
        # the cross-section: an ellipse on the main axes of the covered curves' points round the centre, u — its long axis,
        # ra / rb — the reach along u and across
        U = []; RA = []; RB = []; OFF = []
        for i in range(N):
            t = sps[min(i + 1, N - 1)].clone().sub(sps[max(i - 1, 0)]).normalize(); pts = []; o = sps[i]
            ox_ = o.x; oy_ = o.y; oz_ = o.z; tx = t.x; ty = t.y; tz = t.z
            # (each curve where the cross-section's plane cuts it, near its point of that lock's spine)
            for li, x in enumerate(gl):
                hit = near[li][i]
                if hit is None:
                    continue
                j0 = js_round(hit[0])
                for Q in x['L'].Ps:
                    best = None; bd = math.inf
                    for j in range(max(0, j0 - 12), min(len(Q) - 2, j0 + 12) + 1):
                        q0 = Q[j]; q1 = Q[j + 1]
                        d0 = (q0.x - ox_) * tx + (q0.y - oy_) * ty + (q0.z - oz_) * tz; d1 = (q1.x - ox_) * tx + (q1.y - oy_) * ty + (q1.z - oz_) * tz
                        if d0 == d1 or d0 * d1 > 0:
                            continue
                        f = j + d0 / (d0 - d1)
                        if abs(f - j0) < bd:
                            bd = abs(f - j0); best = f
                    if best is None:
                        continue
                    # (only a curve running along the axis there)
                    jb = clamp(math.floor(best), 0, len(Q) - 2); tq = Q[jb + 1].clone().sub(Q[jb]).normalize()
                    if abs(tq.dot(t)) < 0.6:
                        continue
                    d = atF(Q, best).sub(o)
                    if d.length() < 3 * rMax:
                        pts.append(d.addScaledVector(t, -d.dot(t)))
            e1 = V3(0, 1, 0).cross(t).normalize() if abs(t.y) < 0.9 else V3(1, 0, 0).cross(t).normalize()
            e2 = V3().crossVectors(t, e1)
            a = 0.0; b = 0.0; c = 0.0
            for d in pts:
                xx = d.dot(e1); yy = d.dot(e2); a += xx * xx; b += xx * yy; c += yy * yy
            ang = 0.5 * _atan2(2 * b, a - c); u = e1.clone().multiplyScalar(_cos(ang)).addScaledVector(e2, _sin(ang)).normalize()
            if U and u.dot(U[len(U) - 1]) < 0:
                u.negate()                                          # (the long axis keeps its side along the shell)
            # the ring stands in the middle of the curves' extent
            v = V3().crossVectors(t, u); u0 = math.inf; u1 = -math.inf; v0 = math.inf; v1 = -math.inf
            for d in pts:
                xx = d.dot(u); yy = d.dot(v); u0 = js_min(u0, xx); u1 = js_max(u1, xx); v0 = js_min(v0, yy); v1 = js_max(v1, yy)
            if pts:
                OFF.append(u.clone().multiplyScalar((u0 + u1) / 2).addScaledVector(v, (v0 + v1) / 2)); RA.append(js_or((u1 - u0) / 2, Pl.rad)); RB.append(js_or((v1 - v0) / 2, Pl.rad))
            else:
                OFF.append(V3()); RA.append(Pl.rad); RB.append(Pl.rad)
            U.append(u)
        ra = sm(lambda i: RA[i]); rb = sm(lambda i: RB[i]); R = [js_max(x, rb[i]) for i, x in enumerate(ra)]
        ox = sm(lambda i: OFF[i].x); oy = sm(lambda i: OFF[i].y); oz = sm(lambda i: OFF[i].z)
        ctr = [p.clone().add(V3(ox[i], oy[i], oz[i])) for i, p in enumerate(sps)]   # the rings' centres
        ux = sm(lambda i: U[i].x); uy = sm(lambda i: U[i].y); uz = sm(lambda i: U[i].z)
        Us = []
        for i in range(N):
            vv = V3(ux[i], uy[i], uz[i]); Us.append(vv.normalize() if vv.lengthSq() > 1e-12 else U[i])
        # THE RINGS — the shell's own loops, made here once along its axis (with the carrier's group settings: CP swapped
        # and given back, 3173 / 3310)
        keepCP = ctx.CP; ctx.CP = convOf(P, Pl.cs[0]); CP = ctx.CP
        h = levelH(ctx); dev = clamp(nullish(CP.get('dev'), 0.3), 0, 1); eq = 1 if ev else clamp(nullish(k.p.get('eq'), SHELL_DEFAULTS['eq']), 0, 1)
        Ds = [0]
        for i in range(1, N):
            Ds.append(Ds[i - 1] + sps[i].distanceTo(sps[i - 1]))

        def tan(i):
            return sps[min(i + 1, N - 1)].clone().sub(sps[max(i - 1, 0)]).normalize()
        # the Density modifier on the locks under the shell (the strongest of the locks there)
        tips = [lockTip(P, x['L']) for x in gl]; dM = [1.0] * N     # (dM: a Float32Array filled with 1)
        for i in range(N):
            m = 1
            for li in range(len(gl)):
                tp = tips[li]; hh = near[li][i]
                if not tp or hh is None:
                    continue
                vv = 1 + (tp['dens'] - 1) * tp['mw'][clamp(js_round(hh[0]), 0, len(tp['mw']) - 1)]
                if abs(vv - 1) > abs(m - 1):
                    m = vv
            dM[i] = float32(m)
        stepI = []
        for i in range(N):
            if i == 0 or i == N - 1:
                stepI.append(h / dM[i]); continue
            bk = clamp(tan(i - 1).angleTo(tan(i + 1)) / js_max(Ds[i + 1] - Ds[i - 1], 1e-9) * h / (math.pi / 6), 0, 1)
            stepI.append(h * js_max(1 - dev * bk, 0.25) / dM[i])

        def fOf(d):
            # (`while (i < N - 2 && Ds[i + 1] < d) i++` over the non-decreasing arc lengths: bisect_left over Ds[1 .. N - 2])
            # (a NaN d: `Ds[1] < NaN` is false, i stays 0)
            i = bisect_left(Ds, d, 1, N - 1) - 1 if d == d else 0
            s = Ds[i + 1] - Ds[i]
            return i + (clamp((d - Ds[i]) / s, 0, 1) if s > 0 else 0)

        def atA(A, f):
            return A[clamp(js_round(f), 0, N - 1)]
        planes = []
        # (Even loops) the share of the painted locks' curves painted at a ring: each lock crossing its plane near the ring
        tol = nullish(DBG.get('evenCoverTol'), 0.5)

        def evCover(pl):
            tot = 0; pw = 0
            po = pl['p']; pn = pl['n']
            for x in cov:
                sp = x['L'].sp; f = None; bd = math.inf
                for j in range(len(sp) - 1):
                    d0 = _dot_from(sp[j], po, pn); d1 = _dot_from(sp[j + 1], po, pn)
                    if d0 == d1 or d0 * d1 > 0:
                        continue
                    t = d0 / (d0 - d1); dd = sp[j].clone().lerp(sp[j + 1], t).distanceTo(pl['c'])
                    if dd < bd:
                        bd = dd; f = j + t
                if f is None or bd > 3 * pl['R']:
                    continue
                n = len(x['L'].cs); t = tol * h / (js_or(x['L'].len, 1e-9) / (len(sp) - 1)); tot += n
                m = x['m']
                for i in range(max(0, math.floor(f - t)), min(len(m) - 1, math.ceil(f + t)) + 1):
                    if m[i] >= 0.5:
                        pw += n; break
            return pw / tot if tot else 1
        # One simple tube from the first to the last painted point; the deformation's mean shift ALONG the axis at each
        # end moves that end (shellAxMean)
        z0 = -1; z1 = -1
        for q in range(N):
            if w[q] > 0:
                if z0 < 0:
                    z0 = q
                z1 = q
        zA = Ds[z0]; zB = Ds[z1]; Dend = Ds[N - 1]
        # (the deformation's s is counted from the PAINTED ends, never from the moved ones)

        def sOfD(d):
            return (d - zA) / js_or(zB - zA, 1)
        eA = clamp(zA + shellAxMean(k, 0), 0, Dend); eB0 = clamp(zB + shellAxMean(k, 1), 0, Dend); eB = js_max(eB0, eA + h)
        LEN = eB - eA

        def dOf(u):
            return eA + clamp(u, 0, 1) * LEN

        def fallW(u):
            return _sst(js_min(u, 1 - u) / (fall * 0.5)) if fall > 0 else 1
        # (the rings of the fading ends stand as the nearest ring of full strength)
        fFull0 = fOf(dOf(fall * 0.5)); fFull1 = fOf(dOf(1 - fall * 0.5))
        # Even loops: the rings' tilt turns evenly from the plane at the stretch's start to the one at its end — at a lock's
        # root the plane of the curves' root points, else the plane square to the axis
        evN = None
        if ev:
            def fitN(pts):                                          # the plane through points: the least spread direction
                if len(pts) < 3:
                    return None
                c = V3()
                for p in pts:
                    c.add(p)
                c.divideScalar(len(pts)); Cm = [0, 0, 0, 0, 0, 0]
                for p in pts:
                    x = p.x - c.x; y = p.y - c.y; z = p.z - c.z
                    Cm[0] += x * x; Cm[1] += x * y; Cm[2] += x * z; Cm[3] += y * y; Cm[4] += y * z; Cm[5] += z * z
                tr = Cm[0] + Cm[3] + Cm[5]; n = V3(1, 1, 1).normalize()
                for _ in range(60):                                 # power iteration on (tr·I − C): its top vector is C's least
                    x = tr * n.x - (Cm[0] * n.x + Cm[1] * n.y + Cm[2] * n.z); y = tr * n.y - (Cm[1] * n.x + Cm[3] * n.y + Cm[4] * n.z); z = tr * n.z - (Cm[2] * n.x + Cm[4] * n.y + Cm[5] * n.z)
                    n.set(x, y, z); ln = n.length()
                    if ln < 1e-12:
                        return None
                    n.divideScalar(ln)
                return n

            def endN(atStart, d):
                t = rowPlane(ctx.CP, sps, fOf(d))
                if not t:
                    return None
                sp0 = psp[0] if atStart else psp[N - 1]
                if not (d < 1.5 * h if atStart else Dend - d < 1.5 * h):
                    return None
                pts = []
                for x in gl:
                    xs = x['L'].sp; s0 = xs[0] if atStart else xs[len(xs) - 1]
                    if s0.distanceTo(sp0) > 3 * rMax:
                        continue
                    for Q in x['L'].Ps:
                        pts.append(Q[0] if atStart else Q[len(Q) - 1])
                n = fitN(pts)
                return (n.negate() if n.dot(t['n']) < 0 else n) if n is not None else None
            nA = endN(True, zA); nB = endN(False, zB)
            # (the end plane's tilt only over the first 2.5 loop steps)
            LZ = zB - zA

            def fade(x):
                t = clamp(1 - x / (2.5 * h), 0, 1)
                return t * t * (3 - 2 * t)
            if nA is not None or nB is not None:
                def tiltN(s, ax):
                    s = clamp(s, 0, 1); n = ax.clone()
                    if nA is not None:
                        n.lerp(nA, fade(s * LZ))
                    if nB is not None:
                        n.lerp(nB, fade((1 - s) * LZ))
                    return n.normalize()
                evN = tiltN

        def frameAtD(d):
            f = fOf(clamp(d, 0, Dend)); pl = rowPlane(ctx.CP, sps, clamp(f, js_min(fFull0, fFull1), js_max(fFull0, fFull1)))
            if not pl:
                return None
            n = evN((d - zA) / js_or(zB - zA, 1), pl['n']) if evN else pl['n']
            Uf = atA(Us, f); u = Uf.clone().addScaledVector(n, -Uf.dot(n)).normalize()
            return {'f': f, 'n': n, 'u': u, 'v': V3().crossVectors(n, u), 'c': atF(ctr, f), 'A': atA(ra, f), 'B': atA(rb, f), 'o': atF(sps, f) if evN else None}

        # a ring at layout position u (0 … 1 over the shell's present length); its s in the deformation's coordinates
        def ringAt(uu, wq):
            d = dOf(uu); F = frameAtD(d)
            if F is None:
                return None
            s = sOfD(d); base = []
            for q2 in range(SHELL_SIDES):
                a = 2 * math.pi * q2 / SHELL_SIDES
                base.append({'a': a, 'p': F['c'].clone().addScaledVector(F['u'], _cos(a) * 1.08 * F['A']).addScaledVector(F['v'], _sin(a) * 1.08 * F['B'])})
            return {'w': wq, 's': s, 'n': F['n'], 'u': F['u'], 'v': F['v'], 'ax': shellAxMean(k, s), 'base': base, 'F': F}
        # where the rings go: as many as the loop step fits; Equal spacing 0 — closer on the axis's bends (Deviation),
        # 100% — strictly even (G by the step with bends, GE without)
        NG = 200; G = [0]; GE = [0]

        def stE(i):
            return h / dM[i]
        for q in range(1, NG + 1):
            d0 = dOf((q - 1) / NG); d1 = dOf(q / NG); i0 = clamp(js_round(fOf(d0)), 0, N - 1); i1 = clamp(js_round(fOf(d1)), 0, N - 1)
            G.append(G[q - 1] + (d1 - d0) / ((stepI[i0] + stepI[i1]) / 2)); GE.append(GE[q - 1] + (d1 - d0) / ((stE(i0) + stE(i1)) / 2))
        nR = js_max(1, js_round(G[NG] + (GE[NG] - G[NG]) * eq))

        def sAt(A, x):
            g = A[NG] * x; t = 1
            while t < NG and A[t] < g:
                t += 1
            return (t - 1 + clamp((g - A[t - 1]) / js_or(A[t] - A[t - 1], 1), 0, 1)) / NG
        run = 0; gapped = False; kdef = _def(k)
        for q in range(nR + 1):
            sA = sAt(G, q / nR); s = sA + (sAt(GE, q / nR) - sA) * eq; wq = fallW(s) * (clamp(nullish(k.p.get('str'), 1), 0, 1) if ev else 1)
            if wq < 0.02:
                continue
            ring = ringAt(s, wq)
            if ring is None:
                continue
            ring['pts'] = shellRingPts(k, ring); F = ring['F']
            # deformed: the loop's plane is the deformed ring's — through its centre, its normal by Newell's sum (on the
            # axis's side, leaning at most 45° off it); which locks are inside is still judged round the UNDEFORMED centre,
            # the ellipse grown by how far the ring was pushed out
            cP = F['o'] or F['c']; nP = ring['n']; grow = 0
            if kdef is not None:
                rp = ring['pts']; cP = V3()
                for p in rp:
                    cP.add(p)
                cP.divideScalar(len(rp))
                nn = V3()
                for q2, p in enumerate(rp):
                    p2 = rp[(q2 + 1) % len(rp)]; nn.add(V3().crossVectors(p.clone().sub(cP), p2.clone().sub(cP)))
                if nn.lengthSq() > 1e-12:
                    nn.normalize()
                    if nn.dot(ring['n']) < 0:
                        nn.negate()
                    ang = nn.angleTo(ring['n']); mx = math.pi / 4
                    nP = ring['n'].clone().lerp(nn, mx / ang).normalize() if ang > mx else nn
                for q2, p in enumerate(rp):
                    d = p.clone().sub(ring['base'][q2]['p']); d.addScaledVector(ring['n'], -d.dot(ring['n'])); grow = js_max(grow, d.length())
            # (Even loops: only where it is painted — at least half of the curves of the painted locks this ring crosses)
            if ev and DBG.get('evenCover') is not False and evCover({'p': cP, 'n': nP, 'c': F['c'], 'R': js_max(F['A'], F['B']) + grow}) < 0.5:
                gapped = True; continue
            if gapped and planes:
                run += 1                                            # (run: the painted band the ring belongs to)
            gapped = False
            # ap — the plane square to the shell's axis right there: what a fading ring is mixed with, the same for every lock
            apl = rowPlane(ctx.CP, sps, fOf(dOf(s)))
            # (st — the shell's step there, with the Density: enforceMaxGap judges a gap between two fading rings by it)
            planes.append({'run': run, 'st': stE(clamp(js_round(fOf(dOf(s))), 0, N - 1)), 'p': cP, 'n': nP, 'w': wq, 'c': F['c'], 'R': js_max(F['A'], F['B']) + grow, 'u': ring['u'],
                           'ra': F['A'] + grow, 'rb': F['B'] + grow,
                           'ap': {'p': atF(ctr, fOf(dOf(s))), 'n': apl['n'].clone().negate() if apl['n'].dot(nP) < 0 else apl['n']} if apl else None})
            del ring['F']
        # (the surface drawn — k._surf, finer than the rings — is not made: drawing only)
        ctx.CP = keepCP
        if not planes:
            continue
        # the locks taking the rings: the covered ones and every lock welded to one of them …
        take = {}
        for x in cov:
            take[x['L']] = True
        for L in welded:
            take[L] = True

        # … and every lock running inside the stocking through three rings or more, of any group
        def ringIn(pl, L):
            sp = L.sp; n = pl['n']; vv = V3().crossVectors(n, pl['u']); A = 1.25 * pl['ra'] + L.rad; B = 1.25 * pl['rb'] + L.rad
            po = pl['p']
            for j in range(len(sp) - 1):
                d0 = _dot_from(sp[j], po, n); d1 = _dot_from(sp[j + 1], po, n)
                if d0 == d1 or d0 * d1 > 0:
                    continue
                if abs(d1 - d0) < 0.6 * sp[j].distanceTo(sp[j + 1]):
                    continue                                        # (running across the ring, not along it)
                d = sp[j].clone().lerp(sp[j + 1], d0 / (d0 - d1)).sub(pl['c']); xa = d.dot(pl['u']) / A; xb = d.dot(vv) / B
                if xa * xa + xb * xb <= 1:                          # (V8's x ** 2 is x * x)
                    return True
            return False
        for L in locks:
            if L in take or L.key in off:
                continue
            n = 0
            for pl in planes:
                if ringIn(pl, L):
                    n += 1
                    if n >= 3:
                        break
            if n >= 3:
                take[L] = True
        # (Even loops: only the locks of its own group (and sub-groups))
        if ev:
            for L in list(take):
                if not any(x is src for x in shellsOf(P, L)):
                    del take[L]
        # gw — the gap from each ring to the next as enforceMaxGap measures it, the longest over all the shell's locks
        for i, pl in enumerate(planes):
            pl['pi'] = i; pl['gw'] = 0
        for L in take:
            spl = js_or(polyLen(L.sp), 1e-9)
            for Pc in L.Ps:
                sc = js_max(polyLen(Pc) / spl, 0.25); A = [0]
                for j in range(1, len(Pc)):
                    A.append(A[j - 1] + Pc[j].distanceTo(Pc[j - 1]))

                def cut(pl, Pc=Pc, A=A, L=L):
                    best = None; bd = math.inf; po = pl['p']; pn = pl['n']; pc = pl['c']; lim = 2 * pl['R'] + L.rad
                    for j in range(len(Pc) - 1):
                        d0 = _dot_from(Pc[j], po, pn); d1 = _dot_from(Pc[j + 1], po, pn)
                        if d0 == d1 or d0 * d1 > 0:
                            continue
                        t = d0 / (d0 - d1); q = Pc[j].clone().lerp(Pc[j + 1], t); dd = q.distanceTo(pc)
                        if dd < lim and dd < bd:
                            bd = dd; best = A[j] + (A[j + 1] - A[j]) * t
                    return best
                prev = cut(planes[0])
                for i in range(len(planes) - 1):
                    nx = cut(planes[i + 1])
                    if prev is not None and nx is not None and nx > prev:
                        planes[i]['gw'] = js_max(planes[i]['gw'], (nx - prev) / sc)
                    prev = nx
        # (k._locks — the list of the shell's locks shown — is not kept: interface only)
        bound = {}                                                  # (painted / welded — the looser inside test)
        for x in cov:
            bound[x['L']] = True
        for L in welded:
            bound[L] = True
        st = {'k': k, 'P': Pl, 'locks': take, 'sps': sps, 'R': R, 'U': Us, 'ra': ra, 'rb': rb, 'w': w, 'D': D, 'mU': mU, 'cov': cov, 'planes': planes, 'bound': bound}
        for L in take:
            out.setdefault(L, []).append(st)
    return out


# loops that stay in a shell: the lock's ends, guide loops, a welded trunk's, across a gap, guide hits. A Y junction loop
# of the lock and its held points go where the shell holds fully; in the fade they stay
def shellHeld(r, k, R):
    # port of shellHeld (hair-modeler.clean.html:3356, 1.0.3)
    return k == 0 or k == R - 1 or r.guide or (r.junc and not r.shellFree) or (r.tm is not None and len(r.tm) > 0) or bool(r.tgap) \
        or (r.fixed is not None and len(r.fixed) > 0) or (r.jfix is not None and len(r.jfix) > 0 and not r.shellFree)


def _inBound(st, L):
    b = st.get('bound') if st else None
    return b is not None and L in b


# a lock in a shell: inside the shell its own loops go and the shell's rings take their place — where a ring's plane
# crosses this lock's spine inside the stocking (the original's comment block at 3357-3361)
def shellAdopt(ctx, L, rows, st):
    # port of shellAdopt (hair-modeler.clean.html:3362, 1.0.3)
    planes = st['planes']
    if not planes:
        return rows
    DBG = ctx.DBG
    N = len(L.sp); h = levelH(ctx); sp = L.sp; D = [0]
    for i in range(1, N):
        D.append(D[i - 1] + sp[i].distanceTo(sp[i - 1]))
    isEven = st['k'].type == 'even'; inB = _inBound(st, L)
    # (a painted or welded lock only has to be within twice the ellipse; Even loops: four times)
    g = (4 if isEven else 2) if inB else 1.25

    def inside(pl, q):
        if not pl['u']:
            return q.distanceTo(pl['c'] or pl['p']) <= 1.5 * js_or(pl['R'], L.rad) + L.rad
        d = q.clone().sub(pl['c']); v = V3().crossVectors(pl['n'], pl['u']); A = g * pl['ra'] + L.rad; B = g * pl['rb'] + L.rad
        xa = d.dot(pl['u']) / A; xb = d.dot(v) / B
        return xa * xa + xb * xb <= 1

    def arcAt(f):
        i = clamp(math.floor(f), 0, N - 2)
        return D[i] + (D[i + 1] - D[i]) * (f - i)
    ats = []
    for pl in planes:
        o = pl['p']; n = pl['n']; f0 = nearestF(sp, pl['c'] or o, L.spIdx); best = None; bd = math.inf
        ox = o.x; oy = o.y; oz = o.z; nx = n.x; ny = n.y; nz = n.z
        for j in range(N - 1):
            a = sp[j]; b = sp[j + 1]
            d0 = (a.x - ox) * nx + (a.y - oy) * ny + (a.z - oz) * nz; d1 = (b.x - ox) * nx + (b.y - oy) * ny + (b.z - oz) * nz
            if d0 == d1 or d0 * d1 > 0:
                continue
            f = j + d0 / (d0 - d1)
            if abs(f - f0) < bd:
                bd = abs(f - f0); best = f
        if best is None or best < 0.5 or best > N - 1.5:
            continue
        if not inside(pl, atF(sp, best)):
            continue                                                # (this lock has left the stocking there)
        jb = clamp(math.floor(best), 0, N - 2); tq = sp[jb + 1].clone().sub(sp[jb]).normalize()
        if abs(tq.dot(n)) < 0.6:
            continue                                                # (crossing the ring: leaving)
        a = dict(pl); a['f'] = best; a['d'] = arcAt(best); ats.append(a)
    if not ats:
        return rows
    ats.sort(key=lambda a: a['f'])
    # (a lock that isn't the tail's own takes one unbroken run of rings, its longest)
    if not inB and DBG.get('ringRun') is not False and all(a.get('pi') is not None for a in ats):
        best = [0, 0]; s = 0
        for i in range(1, len(ats) + 1):
            if i == len(ats) or abs(ats[i]['pi'] - ats[i - 1]['pi']) != 1:
                if i - s > best[1] - best[0]:
                    best = [s, i]
                s = i
        ats = ats[best[0]:best[1]]
    # (own loops nearer than 0.6 step to the shell's first / last ring go too)
    lo = ats[0]['d'] - 0.6 * h; hi = ats[len(ats) - 1]['d'] + 0.6 * h; R = len(rows)
    for r in rows:
        d = arcAt(r.f)
        if r.junc and d > lo and d < hi and any(a['w'] >= 0.99 and abs(a['d'] - d) < 2 * h for a in ats):
            r.shellFree = True; r.junc = False; r.jfix = None
    # (another shell's rings on this lock stay)

    def other(r):
        return r.shellRing is not None and r.shellSet is not st
    # (only where a ring of it stands near)
    nearRingOff = DBG.get('nearRing') is False

    def nearRing(d):
        return nearRingOff or any(abs(a['d'] - d) < 1.5 * h for a in ats)
    out = [r for k, r in enumerate(rows) if shellHeld(r, k, R) or other(r) or arcAt(r.f) < lo or arcAt(r.f) > hi or not nearRing(arcAt(r.f))]

    # a ring's loop plane: a fading one mixed with the plane square to the shell's axis there (the same for every lock)
    def ringPl(a):
        own = a['ap'] or rowPlane(ctx.CP, sp, a['f']) or {'p': a['p'], 'n': a['n']}
        return {'p': own['p'].clone().lerp(a['p'], a['w']), 'n': own['n'].clone().lerp(a['n'], a['w']).normalize()}

    def offPl(r, a):
        ap = a['p']; an = a['n']
        return js_max(*[abs(atF(L.Ps[ci], f).clone().sub(ap).dot(an)) for ci, f in r.tm.items()])
    # a welded trunk's loop lying at a ring IS that ring; two trunk loops at one ring are one
    drop = set()
    for a in ats:
        tms = [(r, offPl(r, a)) for r in out if r not in drop and r.tm is not None and len(r.tm) and abs(arcAt(r.f) - a['d']) < 0.6 * h]
        tms = [x for x in tms if x[1] < 0.3 * h]
        tms.sort(key=lambda x: x[1])
        if not tms:
            continue
        keep = tms[0][0]; keep.tm = dict(keep.tm)
        for r, _ in tms[1:]:
            for ci, f in r.tm.items():
                if ci not in keep.tm:
                    keep.tm[ci] = f
            drop.add(r)
        keep.tplane = ringPl(a); keep.shell = a['w']; keep.shellSet = st; keep.shellRing = a
    # the shell comes first: where it holds fully, a welded trunk's loop off every ring goes too (from half strength on;
    # each painted band of an Even loops modifier on its own)
    bands = {}
    for a in ats:
        if a['w'] >= 0.5:
            bands.setdefault(js_or(a.get('run'), 0), []).append(a)
    for full in bands.values():
        if full:
            fLo = full[0]['d'] - 0.3 * h; fHi = full[len(full) - 1]['d'] + 0.3 * h
            for k, r in enumerate(out):
                if r not in drop and r.tm is not None and len(r.tm) and r.shellRing is None and not r.guide and not (r.fixed is not None and len(r.fixed)) \
                        and 0 < k < len(out) - 1 and arcAt(r.f) > fLo and arcAt(r.f) < fHi:
                    drop.add(r)
            # Even loops painted over a proxy shell: it wins there — the shell's rings in its zone go (a step beyond its ends too)
            mE = nullish(DBG.get('evenMargin'), 1) * h if isEven else 0
            if isEven:
                for k, r in enumerate(out):
                    if other(r) and 0 < k < len(out) - 1 and arcAt(r.f) > fLo - mE and arcAt(r.f) < fHi + mE:
                        drop.add(r)
    # another shell's ring within 0.6 step: the one of less weight goes (Even loops' ring always wins)
    skip = set()
    for a in ats:
        for r in out:
            if not other(r) or r in drop or abs(arcAt(r.f) - a['d']) >= 0.6 * h:
                continue
            if not isEven and js_or(r.shell, 0) >= a['w']:
                skip.add(id(a))
            else:
                drop.add(r)
    if drop:
        out = [r for r in out if r not in drop]
    for a in ats:
        if id(a) in skip:
            continue
        # (a trunk loop that took this ring above is it)
        near = next((r for r in out if r.shellRing is a or abs(arcAt(r.f) - a['d']) < 0.3 * h), None)
        pl = ringPl(a)
        if near is not None:
            continue                                                # (a loop that stays is there — a trunk one took the ring above)
        nr = rows[0]
        for r in rows:
            if abs(r.f - a['f']) < abs(nr.f - a['f']):
                nr = r
        out.append(Row(f=a['f'], lvl=None, plane=pl, tgt=js_or(nr.tgt, h), prio=js_or(nr.prio, 0), shell=a['w'], shellSet=st, shellRing=a, adopted=True))
    out.sort(key=lambda r: r.f)
    return out


# the shell's loops cut exactly by its planes, by the weight (the original's comment block at 3465-3469)
def shellParaCut(ctx, L, rows, Fs):
    # port of shellParaCut (hair-modeler.clean.html:3470, 1.0.3)
    R = len(rows); win = math.ceil(len(L.sp) * 0.15); h = levelH(ctx); unit = [(L.plen[ci] if L.plen is not None else polyLen(Pc)) / (len(Pc) - 1) for ci, Pc in enumerate(L.Ps)]
    # (a cut too near the loop before is a sliver only when that loop is the shell's own, cut by its ring)
    cutOk = [[0] * R for _ in L.Ps]

    def held(r, ci):
        return (r.fixed is not None and ci in r.fixed) or (r.tm is not None and ci in r.tm) or (r.jfix is not None and ci in r.jfix)
    for k in range(1, R - 1):
        r = rows[k]
        if not truthy(r.shell):
            continue
        pl = r.tplane or r.plane0 or r.plane; o = pl['p']; n = pl['n']; ring = r.shellRing
        vR = V3().crossVectors(ring['n'], ring['u']) if ring and ring['u'] else None
        gR = (4 if r.shellSet['k'].type == 'even' else 2) if r.shellSet and _inBound(r.shellSet, L) else 1.3
        ox = o.x; oy = o.y; oz = o.z; nx = n.x; ny = n.y; nz = n.z
        for ci, Pc in enumerate(L.Ps):
            if held(r, ci):
                continue
            f0 = Fs[ci][k]; best = None; bd = math.inf
            for j in range(max(0, math.floor(f0 - win)), min(len(Pc) - 2, math.ceil(f0 + win)) + 1):
                a = Pc[j]; b = Pc[j + 1]
                d0 = (a.x - ox) * nx + (a.y - oy) * ny + (a.z - oz) * nz; d1 = (b.x - ox) * nx + (b.y - oy) * ny + (b.z - oz) * nz
                if d0 == d1 or d0 * d1 > 0 or abs(d1 - d0) < 0.35 * a.distanceTo(b):
                    continue
                f = j + d0 / (d0 - d1)
                if abs(f - f0) < bd:
                    bd = abs(f - f0); best = f
            if best is None:
                continue
            # (plus the lock's own radius, as shellAdopt takes it)
            if vR is not None:
                d = atF(Pc, best).sub(ring['c']); xa = d.dot(ring['u']) / (gR * ring['ra'] + L.rad); xb = d.dot(vR) / (gR * ring['rb'] + L.rad)
                if xa * xa + xb * xb > 1:
                    continue
            # (next to a held point the ring still wins down to a tenth of a step)
            gap = (best - Fs[ci][k - 1]) * unit[ci]
            if gap < 0.3 * h:
                p = rows[k - 1]; hp = held(p, ci)
                if hp and gap >= 0.1 * h:
                    pass                                            # (the ring's cut stays)
                elif k < 2 or (truthy(p.shell) and cutOk[ci][k - 1]) or hp or (best - Fs[ci][k - 2]) * unit[ci] < 0.6 * h:
                    continue
                else:
                    Fs[ci][k - 1] = (Fs[ci][k - 2] + best) / 2
            Fs[ci][k] = best; cutOk[ci][k] = 1                      # (the plane is mixed with the lock's own by the weight already)
    for F in Fs:
        for k in range(1, R - 1):
            F[k] = clamp(F[k], F[k - 1] + 0.05, js_max(F[k + 1] - 0.05, F[k - 1] + 0.05))


# Fix sharp angles squares the walls one by one — in the shell its loops stay the shell's (by the weight)
def shellKeep(rows, Fs, F0):
    # port of shellKeep (hair-modeler.clean.html:3510, 1.0.3)
    R = len(rows)
    for ci, F in enumerate(Fs):
        for k in range(1, R - 1):
            sh = rows[k].shell
            if truthy(sh):
                F[k] += (F0[ci][k] - F[k]) * sh


__all__ = ['SHELL_SIDES', 'evenParts', 'shellAdopt', 'shellAxMean', 'shellDefLocal', 'shellFitScore', 'shellHeld', 'shellKeep', 'shellParaCut', 'shellRingPts', 'shellSets', 'shellSetsBuild']
