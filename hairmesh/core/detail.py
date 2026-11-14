# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Detail proxy (1.0.3; hair-modeler.clean.html 2774-2961 of reference/v1.0.3): lockDetail and proxyLoops.

Detail on a simple tube (a dent, a groove, curves forking inside the lock — X / Y): there the loops are placed by a
PROXY — the simple tube round the detail — and only carried over to the detailed curves (proxyLoops), so the detail
doesn't skew them. A simple tube is built as before. Called by buildRowTables (rows.py); the conversion parameter
`proxy` (on unless it is False) turns it off."""
import math

from .geom import atF, atF3, polyLen
from .js import INF, NAN, clamp, js_max, js_or, js_pow, js_round, truthy
from .vec3 import V3

DETAIL_DENS = 0.15                                                  # (2785) the loops get up to this much denser in a detailed stretch (spineRows)

# Speed (lot 1.2, F2), without changing a bit: the first port, line for line as the original, is kept in
# tests/helpers/detailref.py and tests/test_detail_speed.py judges these against it (the example's every call, drawn
# locks with NaN and signed zeros), every float by its bits and its type. What changed:
#  - js_max / js_min of two or three values written out: `if v != v or m != m: m = NAN elif v > m: m = v` is
#    js_max(m, v) exactly — NaN on either side gives NaN (the NAN js_max returns), else Python's max((m, v)), which keeps
#    the first of two equal values (so the int 0 a loop starts from stays an int 0, and +0 / -0 keep js_max's choice);
#    clamp and the smoothstep of the zones the same way (clamp's NaN test first, then its two comparisons);
#  - the running maximum over a window (`m = 0; for j: m = js_max(m, raw[j])`) as max() of the slice: max() returns
#    the first occurrence of the largest value, as the scan with a strict `>` does, and the 0 the scan starts from
#    stays only when nothing is > 0: `mx if mx > 0 else 0`. Only for a row without NaN (max() over a NaN depends on
#    where it is): a row with one takes the literal scan;
#  - the crossing of each curve with the cross-section plane searched outward from i (see _crossing);
#  - _hyp2 for js_hypot of two values; the per-edge length of the hull hoisted out of the per-point loop (the same
#    expression on the same inputs); scalar forms of the Vector3 chains (the same operations, in the same order).


def _hyp2(x, y):
    """js_hypot(x, y) — V8's Math.hypot — for two values, the same operations: the NaN / Infinity cuts (an
    infinity wins over a NaN, wherever each is), the largest magnitude, then the Kahan sum of the two normalised squares, which for two terms is exactly
    n1 * n1 + n2 * n2 (the first step adds n1 * n1 to 0.0 and leaves a compensation of 0.0, n * n being finite and
    >= +0), sqrt(sum) * max."""
    ax = abs(x); ay = abs(y)
    if ax == INF or ay == INF:                                      # (V8: an infinity wins over a NaN, wherever each is)
        return INF
    if ax != ax or ay != ay:
        return NAN
    mx = 0.0
    if ax > mx:
        mx = ax
    if ay > mx:
        mx = ay
    if mx == 0:
        return 0.0
    n1 = ax / mx; n2 = ay / mx
    return math.sqrt(n1 * n1 + n2 * n2) * mx


def _crossing(Px, Py, Pz, i, j0, j1, ox, oy, oz, nx, ny, nz):
    """The fractional index of the crossing of curve P (its coordinates) with the plane through o square to n nearest
    to i, over the segments j0 .. j1 (lockDetail, 2818-2826), or -1. The original scans j0 .. j1 upward and keeps a
    crossing when |f - i| < bd: the least |f - i|, and of equal ones the smallest j. Each segment's test is a function of
    its own two points (d at a point is the same expression whether it is the scan's d0 or d1), and its crossing
    f = j + d0 / (d0 - d1) lies in [j, j + 1] (d0, d1 apart or one of them 0: the quotient's magnitude is at most 1,
    and rounding is monotonic), so |f - i| >= lb(j) = j - i for j >= i, i - j - 1 below (exact small integers). Here the
    segments are tried by lb (j = i, i - 1, i + 1, i - 2, ...) and the search stops once lb > bd: no segment left can
    reach bd, or tie it. Ties keep the smaller j, NaN never wins (as in the scan): the same crossing, bit for bit."""
    bd = INF; bf = -1; bj = -1; k = 0
    while k <= bd:
        jr = i + k; jl = i - 1 - k
        if jr > j1 and jl < j0:
            break
        for j in (jr, jl):
            if j < j0 or j > j1:
                continue
            d0 = (Px[j] - ox) * nx + (Py[j] - oy) * ny + (Pz[j] - oz) * nz
            d1 = (Px[j + 1] - ox) * nx + (Py[j + 1] - oy) * ny + (Pz[j + 1] - oz) * nz
            if d0 == d1 or d0 * d1 > 0:
                continue
            dx = Px[j] - Px[j + 1]; dy = Py[j] - Py[j + 1]; dz = Pz[j] - Pz[j + 1]   # P[j].distanceTo(P[j + 1])
            if abs(d1 - d0) < 0.35 * math.sqrt(dx * dx + dy * dy + dz * dz):
                continue
            f = j + d0 / (d0 - d1); df = abs(f - i)
            if df < bd or (df == bd and j < bj):
                bd = df; bf = f; bj = j
        k += 1
    return bf


def _avg(n, w, val):
    """lockDetail's avg (2792): moving average over ±w by prefix sums (the window shrinks at the ends: they stay put)"""
    S = [0.0] * (n + 1)
    for j in range(n):
        S[j + 1] = S[j] + val[j]
    out = []
    for i in range(n):
        r = min(w, i, n - 1 - i)
        out.append((S[i + r + 1] - S[i - r]) / (2 * r + 1))
    return out


def _cr(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def lockDetail(L):
    # port of lockDetail (hair-modeler.clean.html:2787, 1.0.3): the detailed stretches of lock L, found before its rows
    # are made — every curve's cross-section point at each spine point (a converted modifier: its own point of that
    # section; else cut by the plane square to the smoothed spine), in a frame carried along the spine. A curve is
    # detailed where its point (a) leaves the smooth course of its own neighbourhood along the lock (a dent, a bump:
    # high-pass over ~2 radii), or (b) lies inside the convex hull of the cross-section deeper than along most of the
    # lock (a groove; a lock concave all along is a simple tube of its own shape), or (c) is an extra curve of an
    # X / Y fork inside the lock, where it runs on its own.
    # -> None (a simple tube) or {'d': per curve, detail at each spine point (0…1), 'w': the zone (0…1, faded at its
    # ends), 's': its strength (0…1), 'sps' / 't': the smoothed spine and its tangents, 'extra', 'dev', 'concEx'}
    C = len(L.Ps); N = len(L.sp)
    if C < 3 or N < 8 or not (L.rad > 0):
        return None
    sp = L.sp; rad = L.rad
    unit = js_or(polyLen(sp), 1e-9) / (N - 1)
    wS = clamp(js_round(1.5 * rad / unit), 2, math.floor(N / 6)); wH = clamp(js_round(2 * rad / unit), 3, math.floor(N / 4))
    sx = _avg(N, wS, [p.x for p in sp]); sy = _avg(N, wS, [p.y for p in sp]); sz = _avg(N, wS, [p.z for p in sp])
    sps = [V3(sx[i], sy[i], sz[i]) for i in range(N)]
    t = []
    for i in range(N):
        v = sps[min(i + 1, N - 1)].clone().sub(sps[max(i - 1, 0)])
        t.append(v.normalize() if v.lengthSq() > 1e-16 else None)
    for i in range(N):
        if t[i] is None:
            t[i] = (t[i - 1] if i else (next((x for x in t if x is not None), None) or V3(0, 1, 0))).clone()
    # the frame: u carried along (projected onto each cross-section plane), v = t × u
    u = []; v = []
    u0 = V3(0, 1, 0)
    if abs(u0.dot(t[0])) > 0.9:
        u0.set(1, 0, 0)
    for i in range(N):
        p = (u[i - 1] if i else u0).clone()
        p.addScaledVector(t[i], -p.dot(t[i]))
        if p.lengthSq() < 1e-12:
            p.copy(u[i - 1] if i else u0)
        u.append(p.normalize()); v.append(V3().crossVectors(t[i], p).normalize())
    # cross-section points, in the frame
    win = math.ceil(N * 0.15); X = []
    para = L.para
    spx = [o.x for o in sps]; spy = [o.y for o in sps]; spz = [o.z for o in sps]
    fr = [(t[i].x, t[i].y, t[i].z, u[i].x, u[i].y, u[i].z, v[i].x, v[i].y, v[i].z) for i in range(N)]
    for P in L.Ps:
        out = []; Px = Py = Pz = None
        if not para:
            Px = [q.x for q in P]; Py = [q.y for q in P]; Pz = [q.z for q in P]
        for i in range(N):
            ox = spx[i]; oy = spy[i]; oz = spz[i]; nx, ny, nz, ux, uy, uz, vx, vy, vz = fr[i]
            if para:
                q = P[i]; qx = q.x; qy = q.y; qz = q.z
            else:
                bf = _crossing(Px, Py, Pz, i, max(0, i - win), min(N - 2, i + win), ox, oy, oz, nx, ny, nz)
                if bf >= 0:
                    qx, qy, qz = atF3(P, bf)
                else:
                    q = P[i]; qx = q.x; qy = q.y; qz = q.z
            ex = qx - ox; ey = qy - oy; ez = qz - oz                # q.clone().sub(sps[i]), then .dot(u[i]) / .dot(v[i])
            out.append((ex * ux + ey * uy + ez * uz, ex * vx + ey * vy + ez * vz))
        X.append(out)
    XT = list(zip(*X))                                              # XT[i][c] = X[c][i]
    # ring radius (mean); where the tube is thin (a pointed tip) the shape counts less
    Rm = []; fade = []
    for i in range(N):
        m = 0
        for p in XT[i]:
            m += _hyp2(p[0], p[1])
        m /= C; Rm.append(js_max(m, 1e-9)); fade.append(clamp(m / (0.4 * rad), 0, 1))
    Rr = [js_max(r, 0.3 * rad) for r in Rm]
    # (a) high-pass of the cross-section's SHAPE (scaled by the ring radius): the ring against its smoothed course, less
    # what moves the whole ring alike — the best affine fit (a bend: the smoothed spine cuts its corner; a tube narrowing
    # to its tip, a flat lock widening, a twist). What is left moves single curves: a dent, a bump, a curve forking off.
    nX = [[(p[0] / Rm[i], p[1] / Rm[i]) for i, p in enumerate(x)] for x in X]
    sm = []
    for x in nX:
        ax = _avg(N, wH, [p[0] for p in x]); ay = _avg(N, wH, [p[1] for p in x])
        sm.append([(ax[i], ay[i]) for i in range(N)])
    smT = list(zip(*sm)); nXT = list(zip(*nX))                      # [i][c]: the sums below run over c in order, as before
    dev = [[0] * N for _ in range(C)]
    for i in range(N):
        Si = smT[i]; Bi = nXT[i]
        ax = 0; ay = 0; bx = 0; by = 0
        for c in range(C):
            sa = Si[c]; sb = Bi[c]
            ax += sa[0]; ay += sa[1]; bx += sb[0]; by += sb[1]
        ax /= C; ay /= C; bx /= C; by /= C
        saa = 0; sab = 0; sbb = 0; pxa = 0; pxb = 0; pya = 0; pyb = 0   # Σ a'a'ᵀ and Σ b'a'ᵀ
        for c in range(C):
            sa = Si[c]; sb = Bi[c]
            a0 = sa[0] - ax; a1 = sa[1] - ay; b0 = sb[0] - bx; b1 = sb[1] - by
            saa += a0 * a0; sab += a0 * a1; sbb += a1 * a1; pxa += b0 * a0; pxb += b0 * a1; pya += b1 * a0; pyb += b1 * a1
        eps = 1e-6 * (saa + sbb + 1e-12); saa += eps; sbb += eps     # (a flat ring: collinear points)
        det2 = saa * sbb - sab * sab; i00 = sbb / det2; i01 = -sab / det2; i11 = saa / det2
        m00 = pxa * i00 + pxb * i01; m01 = pxa * i01 + pxb * i11; m10 = pya * i00 + pyb * i01; m11 = pya * i01 + pyb * i11
        fi = fade[i]
        for c in range(C):
            sa = Si[c]; sb = Bi[c]
            a0 = sa[0] - ax; a1 = sa[1] - ay
            dev[c][i] = _hyp2(sb[0] - bx - (m00 * a0 + m01 * a1), sb[1] - by - (m10 * a0 + m11 * a1)) * fi
    # (b) depth inside the hull of the cross-section, minus the curve's usual depth (20th percentile along the lock)
    conc: list[list[float]] = [[0] * N for _ in range(C)]
    for i in range(N):
        pts = sorted(((p[0], p[1], ci) for ci, p in enumerate(XT[i])), key=_xy)   # (a.x - b.x || a.y - b.y; both sorts are stable)
        lo = []; hi = []
        for p in pts:
            while len(lo) > 1 and _cr(lo[-2], lo[-1], p) <= 0:
                lo.pop()
            lo.append(p)
        for j in range(len(pts) - 1, -1, -1):
            p = pts[j]
            while len(hi) > 1 and _cr(hi[-2], hi[-1], p) <= 0:
                hi.pop()
            hi.append(p)
        H = lo[:-1] + hi[:-1]
        if len(H) < 3:
            continue                                                # (a flat cross-section: no inside)
        on = {p[2] for p in H}
        if len(on) == C:
            continue                                                # (every point on the hull: nothing inside)
        nH = len(H); edges = []
        for e in range(nH):
            a = H[e]; b = H[(e + 1) % nH]; ex = b[0] - a[0]; ey = b[1] - a[1]; h = _hyp2(ex, ey)
            edges.append((a[0], a[1], ex, ey, h if h != 0 and h == h else 1e-12))   # js_or(js_hypot(...), 1e-12)
        rr = Rr[i]
        for p in pts:
            if p[2] in on:
                continue
            p0 = p[0]; p1 = p[1]; m = INF
            for a0, a1, ex, ey, ab in edges:
                val = abs(ex * (p1 - a1) - ey * (p0 - a0)) / ab        # abs(_cr(a, b, p)) / ab
                if val != val or m != m:                            # m = js_min(m, val)
                    m = NAN
                elif val < m:
                    m = val
            conc[p[2]][i] = m / rr
    concEx = []
    for c in conc:
        q = sorted(c)[math.floor(0.2 * (N - 1))]
        row = []
        for x in c:
            y = x - q
            row.append(NAN if y != y else y if y > 0 else 0)        # js_max(0, x - q)
        concEx.append(row)
    # (c) X / Y forks inside the lock: curves running together over a stretch (one point of the cross-section) and
    # parting — a cut across a wall (a V on it), a strand forking off. The extra curves go (`extra`): while some curve
    # still runs together with another one left, the one doing so over most of its length is dropped (on a tie, one with
    # anchors put in later — no section number); what's left is the simple tube. An extra curve is detail where it runs
    # on its own (the diagonal); where it lies on a curve of the tube it is that curve. (Only where the tube is thick: at
    # a pointed tip every curve meets.)
    extra = set(); dT = [[0] * N for _ in range(C)]
    tolC = 0.04; with_ = [[0] * C for _ in range(C)]; co = bytearray(N * C * C)   # co[(i·C + a)·C + b]: a and b share the point at spine point i
    thick = 0
    for i in range(N):
        if fade[i] < 1:
            continue
        thick += 1; t2 = js_pow(tolC * Rr[i], 2); Xi = XT[i]
        for a in range(C):
            pa0, pa1 = Xi[a]
            for b in range(a + 1, C):
                pb = Xi[b]; dx = pa0 - pb[0]; dy = pa1 - pb[1]
                if dx * dx + dy * dy < t2:
                    co[(i * C + a) * C + b] = co[(i * C + b) * C + a] = 1; with_[a][b] += 1; with_[b][a] += 1
    minRun = max(3, math.ceil(0.05 * thick)); noSec = [sum(1 for a in c.anchors if a.sec is None) for c in L.cs]

    def onAny(a, i):
        o = (i * C + a) * C
        for b in range(C):
            if b != a and co[o + b] and b not in extra:
                return True
        return False
    while True:
        live = [c for c in range(C) if c not in extra]
        cand = [a for a in live if any(b != a and with_[a][b] >= minRun for b in live)]
        if not cand or len(live) <= 3:
            break
        # (the share of the curve lying on ANY other: a curve two cut curves start from runs with both, but only before the fork)
        share = {a: sum(1 for i in range(N) if onAny(a, i)) for a in cand}
        cand.sort(key=lambda a: (-share[a], -noSec[a], -a))         # share.get(b) - share.get(a) || noSec[b] - noSec[a] || b - a
        extra.add(cand[0])
    for e in extra:
        for i in range(N):
            if fade[i] >= 1 and not onAny(e, i):
                dT[e][i] = 1
    r2 = math.ceil(wH / 2)
    d = []; nanD = False
    ba1 = 0.3 - 0.15; ba2 = 0.2 - 0.08                               # (_sstep's b - a, the same value on every call)
    for ci in range(C):
        # (hand-drawn curves wobble by up to ~0.15 of the radius — no detail)
        # raw[i] = js_max(sstep(dev, 0.15, 0.3), sstep(concEx, 0.08, 0.2), dT), sstep(x, a, b) = t * t * (3 - 2 * t)
        # with t = clamp((x - a) / (b - a), 0, 1): a NaN argument of the clamp makes it NaN and js_max then NaN; dT is 0 or 1
        dv = dev[ci]; ce = concEx[ci]; dt = dT[ci]; raw = []; nan = False
        for i in range(N):
            x1 = (dv[i] - 0.15) / ba1; x2 = (ce[i] - 0.08) / ba2
            if x1 != x1 or x2 != x2:
                raw.append(NAN); nan = True; continue
            x1 = x1 if x1 < 1 else 1; x1 = x1 if x1 > 0 else 0       # clamp(x, 0, 1)
            x2 = x2 if x2 < 1 else 1; x2 = x2 if x2 > 0 else 0
            m = x1 * x1 * (3 - 2 * x1); y = x2 * x2 * (3 - 2 * x2); z = dt[i]
            if y > m:
                m = y
            if z > m:
                m = z
            raw.append(m)
        row = []
        if not nan:                                                 # max() of the window (see the top of the module)
            for i in range(N):
                mx = max(raw[max(0, i - r2):min(N - 1, i + r2) + 1])
                row.append(mx if mx > 0 else 0)
        else:
            nanD = True
            for i in range(N):
                m = 0
                for j in range(max(0, i - r2), min(N - 1, i + r2) + 1):
                    m = js_max(m, raw[j])
                row.append(m)
        d.append(row)
    w0 = []
    if not nanD:
        for col in zip(*d):
            mx = max(col)
            w0.append(mx if mx > 0 else 0)
    else:
        for i in range(N):
            m = 0
            for x in d:
                m = js_max(m, x[i])
            w0.append(m)
    if js_max(*w0) < 0.05:
        return None
    w = _avg(N, r2, w0)
    s0 = []
    for di, vi, ei, ti in zip(zip(*d), zip(*dev), zip(*concEx), zip(*dT)):
        m = 0
        for ci in range(C):
            # m = js_max(m, d * clamp(js_max((dev - 0.15) / 0.35, (concEx - 0.08) / 0.3, dT), 0, 1))
            e = (vi[ci] - 0.15) / 0.35; y = (ei[ci] - 0.08) / 0.3
            if e != e or y != y:
                e = NAN
            else:
                if y > e:
                    e = y
                z = ti[ci]
                if z > e:
                    e = z
                e = e if e < 1 else 1; e = e if e > 0 else 0
            x = di[ci] * e
            if x != x or m != m:
                m = NAN
            elif x > m:
                m = x
        s0.append(m)
    s = [x * (1 if w[i] > 0.01 else 0) for i, x in enumerate(_avg(N, r2, s0))]
    return {'d': d, 'w': w, 's': s, 'sps': sps, 't': t, 'extra': extra, 'dev': dev, 'concEx': concEx}


def _xy(p):
    return (p[0], p[1])


def proxyLoops(L, rows, Fs, det):
    # port of proxyLoops (hair-modeler.clean.html:2928, 1.0.3): loops in the detailed stretches (lockDetail) — the
    # curves of the simple tube there (no detail at that loop: the proxy) keep their points; the loop's plane goes
    # through them (their centre, square to their mean direction) and the detailed curves are cut by it (the crossing
    # nearest to where the loop is now), by the zone weight. With fewer than two such curves the plane is the smoothed
    # spine's. Loops where locks part or meet are not touched: on a neighbour's trunk, at a fork (forkSnap), across a
    # gap; on a guide hit or a Y junction of this lock only the held points stay.
    R = len(rows); C = len(L.Ps); N = len(L.sp); win = math.ceil(N * 0.15)
    dd = det['d']; dw = det['w']; dt = det['t']; extra = det['extra']
    for k in range(1, R - 1):
        r = rows[k]; i = clamp(js_round(r.f), 0, N - 1); w = dw[i]
        if w < 0.01 or truthy(r.shell):
            continue                                                # (a proxy shell rules there)
        if L.shellSpan and r.f >= L.shellSpan[0] and r.f <= L.shellSpan[1]:
            continue                                                # (loops put in between its rings too)
        if r.tm or r.tgap or r.forkC or r.exactC:                   # sz(r.tm) || r.tgap || sz(r.forkC) || sz(r.exactC)
            continue
        # (a Y junction loop of this lock or a guide hit: the points held there stay, the other detailed curves still go)
        base = []; move = []
        for ci in range(C):
            if dd[ci][i] < 0.25 and ci not in extra:
                base.append(ci)
            elif not ((r.jfix is not None and ci in r.jfix) or (r.fixed is not None and ci in r.fixed)):
                move.append(ci)
        if not move:
            continue
        if len(base) >= 2:
            p = V3(); n = V3()
            for ci in base:
                P = L.Ps[ci]; f = Fs[ci][k]; j = clamp(math.floor(f), 0, len(P) - 2); tg = P[j + 1].clone().sub(P[j])
                p.add(atF(P, f))
                if tg.lengthSq() > 1e-16:
                    tg.normalize(); n.addScaledVector(tg, -1 if tg.dot(dt[i]) < 0 else 1)
            p.divideScalar(len(base))
            if n.lengthSq() < 1e-12:
                n.copy(dt[i])
            n.normalize()
        else:
            p = atF(det['sps'], r.f); n = dt[i]
        px = p.x; py = p.y; pz = p.z; nx = n.x; ny = n.y; nz = n.z
        for ci in move:
            P = L.Ps[ci]; F = Fs[ci]; f0 = F[k]; best = None; bd = math.inf
            for j in range(max(0, math.floor(f0 - win)), min(len(P) - 2, math.ceil(f0 + win)) + 1):
                a = P[j]; b = P[j + 1]
                d0 = (a.x - px) * nx + (a.y - py) * ny + (a.z - pz) * nz   # P[j].clone().sub(p).dot(n)
                d1 = (b.x - px) * nx + (b.y - py) * ny + (b.z - pz) * nz
                if d0 == d1 or d0 * d1 > 0 or abs(d1 - d0) < 0.35 * a.distanceTo(b):
                    continue
                f = j + d0 / (d0 - d1)
                if abs(f - f0) < bd:
                    bd = abs(f - f0); best = f
            if best is not None:
                F[k] = clamp(f0 + (best - f0) * w, F[k - 1] + 0.05, js_max(F[k + 1] - 0.05, F[k - 1] + 0.05))
