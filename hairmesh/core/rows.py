# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Surface (NURBS loft) -> quad topology: the row tables (hair-modeler.clean.html:1412-2730).

A lock = the curves connected through surfaces. buildRowTables gives every curve of a lock the same number of
rows (loop positions) so the walls weld cleanly: the lock's spine is cut by planes spaced by the loop step (bends,
guides and density change the spacing), welded trunks take the neighbour's loops one to one, Y junctions and
guide hits pin loops, then the loops are cleaned (slivers, max gap, sharp angles, guide priority, coincident
curves). The original's module globals live in a RowBuild (rowbase.py)."""
import math
from bisect import bisect_left

from .density import lockTip, tipConvert
from .fdlibm import asin as _asin, cos as _cos
from .geom import atF, chunkBoxes, inBox, nearBoxes, nearestF, polyBox, polyIndex, polyLen, resample, zoneWeight
from .guides import lockGuideRows
from .js import at, clamp, includes, index_of, js_max, js_min, js_or, js_round, js_str, nullish, to_fixed, truthy
from .junction import lockJunctionRows
from .raycast import Box3
from .rowbase import Comp, Lock, Row, RowBuild, RowTable, convOf, levelH, rowPlane, settings_conv   # noqa: F401 (re-exported)
from .sections import lockSecs, resampleParallel
from .trace import br as _br, dec as _dec, dec_near, set_ctx
from .trunks import curveRowF, trunkFade, trunkLevels, trunkOnly, trunkOwnFs
from .vec3 import V3

NS = 160                                                            # samples per resampled lock curve (1598)
ALIGN_TILT = 30                                                     # degrees (1629)
CLEAN_K = 400                                                       # (2347)


# Curves connected through surfaces form one lock. All walls of a lock share one row table,
# so shared edge curves get identical vertices and the walls weld cleanly on conversion.
def surfaceComponents(P):
    # port of surfaceComponents (hair-modeler.clean.html:1603)
    par = {}

    def find(x):
        while par[x] != x:
            x = par[x]
        return x
    for s in P.surfaces():
        if s.a not in P.items or s.b not in P.items:
            continue
        for cid in (s.a, s.b):
            if cid not in par:
                par[cid] = cid
        ra = find(s.a); rb = find(s.b)
        if ra != rb:
            par[ra] = rb
    comps = {}
    for cid in par:
        comps.setdefault(find(cid), []).append(cid)
    return list(comps.values())


# A branch curve that starts at an anchor welded to the MIDDLE of another lock's curve (Y-junction) is,
# for loop building, continued back along that host curve so all curves of the branch start together.
def extendAlongHost(P, c, lockCurves, need, byAnchor=None):
    # port of extendAlongHost (hair-modeler.clean.html:2191)
    # byAnchor (P.anchor_curves()): only the curves using J are tried, in curves() order, so the first host found is
    # the one the original's scan of every curve finds (a curve not using J never passes `0 < k`).
    if need < 1e-6:
        return None
    for end in (0, len(c.anchors) - 1):
        J = c.anchors[end]
        host = None
        for h in (P.curves() if byAnchor is None else byAnchor.get(J.id, ())):
            if any(h is x for x in lockCurves) or h.conn or len(h.samples) <= 2:
                continue
            k = index_of(h.anchors, J)
            if 0 < k < len(h.anchors) - 1:
                host = h; break
        if not host:
            continue
        hs = [s.p for s in host.samples]; idx = index_of(host.anchors, J) * 24   # 24 samples per segment
        own = [s.p for s in c.samples] if end == 0 else [s.p for s in c.samples][::-1]   # starts at J
        tip = own[len(own) - 1]
        dir_ = -1 if hs[max(idx - 6, 0)].distanceTo(tip) > hs[min(idx + 6, len(hs) - 1)].distanceTo(tip) else 1   # away from the tip
        ext = []; acc = 0; i = idx
        while acc < need:
            j = i + dir_
            if j < 0 or j >= len(hs):
                break
            acc += hs[j].distanceTo(hs[i]); ext.append(hs[j]); i = j
        if ext:
            _br('extendAlongHost.extended')
            return ext[::-1] + own
    return None


# Row positions along the lock spine, as fractional sample indices 0..N-1 (the original's comment block, 1620-1631).
def spineRows(ctx, sp, refPlanes, guideRows=(), welded=False, junctionRows=()):
    # port of spineRows (hair-modeler.clean.html:1632)
    S = ctx.P.S; CP = ctx.CP
    N = len(sp)
    L = [0]
    for i in range(1, N):
        L.append(L[i - 1] + sp[i].distanceTo(sp[i - 1]))
    tot = js_or(L[N - 1], 1e-9)

    def arcAt(f):
        i = clamp(math.floor(f), 0, N - 2); return L[i] + (L[i + 1] - L[i]) * (f - i)

    def fAt(d):
        i = 0
        while i < N - 2 and L[i + 1] < d:
            i += 1
        s = L[i + 1] - L[i]
        return i + (clamp((d - L[i]) / s, 0, 1) if s > 0 else 0)
    h = levelH(ctx); yRef = S.yRef; dev = clamp(nullish(CP.get('dev'), 0.3), 0, 1)   # noqa: F841 (yRef unused since 10.10)
    gRows = [Row(f=g['f'], plane=g['plane'], fixed=g['fixed'], guide=True, local=g['local'], gid=g['gid']) for g in guideRows]
    myG = {g['gid']: True for g in guideRows}                       # the guides crossing this lock
    gz = [{'d': arcAt(g['f']), 'dens': g['dens'], 'rad': g['rad'], 'blend': g['blend'], 'prio': js_or(g['prio'], 0)} for g in guideRows]
    gw = lambda g, x: zoneWeight(g['rad'], g['blend'], x)
    TIPZ = ctx.TIPZ
    tipM = (lambda d: 1 + (TIPZ['dens'] - 1) * TIPZ['mw'][clamp(js_round(fAt(d)), 0, N - 1)]) if TIPZ else (lambda d: 1)

    def mult(d):
        m = 1
        for g in gz:
            m += (g['dens'] - 1) * gw(g, abs(d - g['d']))
        return js_max(m, 0.1) * tipM(d)
    # Bends: loops get closer where the lock turns — by up to `deviation` (30° of turn per step = full effect)
    tanAt = lambda i: sp[min(i + 1, N - 1)].clone().sub(sp[max(i - 1, 0)]).normalize()
    bendK = [0] * N
    for i in range(1, N - 1):
        bendK[i] = tanAt(i - 1).angleTo(tanAt(i + 1)) / js_max(L[i + 1] - L[i - 1], 1e-9)

    def bendAt(d):
        f = fAt(d); i = clamp(js_round(f), 0, N - 1); s = 0; n = 0
        for j in range(max(0, i - 4), min(N - 1, i + 4) + 1):
            s += bendK[j]; n += 1
        return clamp((s / n) * h / (math.pi / 6), 0, 1)

    def prioAt(d):
        p = 0
        for g in gz:
            if g['prio'] > 0:
                p = js_max(p, g['prio'] * gw(g, abs(d - g['d'])))
        return p
    stepAt = lambda d: h * js_max(1 - dev * bendAt(d) * (1 - prioAt(d)), 0.25) / mult(d)
    # 0) loops shared with the neighbouring lock (a plane leaning more than ALIGN_TILT from the lock's own is left out;
    #    loops on a welded trunk are always taken)
    aligned = []; tiltCos = _cos(ALIGN_TILT * math.pi / 180)
    if refPlanes:
        for pl in refPlanes:
            for i in range(N - 1):
                d0 = sp[i].clone().sub(pl['p']).dot(pl['n']); d1 = sp[i + 1].clone().sub(pl['p']).dot(pl['n'])
                if d0 == d1 or d0 * d1 > 0:
                    continue
                f = i + d0 / (d0 - d1)
                if _dec('spineRows.reach', atF(sp, f).distanceTo(pl['c']), pl['reach']) >= pl['reach']:
                    continue
                if not pl.get('trunk'):
                    own = rowPlane(CP, sp, f)
                    if own and _dec('spineRows.tilt', abs(own['n'].dot(pl['n'])), tiltCos) < tiltCos:
                        _br('spineRows.tiltRejected'); break
                aligned.append(Row(f=f, plane={'p': pl['p'], 'n': pl['n']}, pts=pl['pts'], aligned=True, open=pl.get('open', False), trunk=bool(pl.get('trunk')), lg=pl['lg'] or None, pj=bool(pl['junc'])))
                break
    aligned.sort(key=lambda r: r.f)
    # the guide wins there — but never over a welded trunk
    sameGuide = lambda r: r.lg is not None and any(i in myG for i in r.lg)
    for k in range(len(aligned) - 1, -1, -1):
        if (not welded or not aligned[k].trunk) and not sameGuide(aligned[k]) and _dec('spineRows.alignedPrio', prioAt(arcAt(aligned[k].f)), 0.5) >= 0.5:
            _br('spineRows.alignedDroppedByGuide'); del aligned[k]
    # Y junction loops — unless a guide with Priority ≥ 50% rules there
    jRows = [Row(f=j['f'], plane=j['plane'], jfix=j['fixed'], junc=True) for j in junctionRows if _dec('spineRows.juncPrio', prioAt(arcAt(j['f'])), 0.5) < 0.5]
    rows = [Row(f=0), *aligned, *gRows, *jRows]
    rows.sort(key=lambda r: r.f)
    # guide loops win over junction loops, those over shared loops, those over levels
    pri = lambda r: 3 if r.guide else 2 if r.junc else 1 if r.aligned else 0
    minGap = 0.35 * h; kept = [rows[0]]
    for r in range(1, len(rows)):
        last = kept[len(kept) - 1]; gap = arcAt(rows[r].f) - arcAt(last.f)
        wide = (rows[r].junc or last.junc) and not rows[r].guide and not last.guide and not rows[r].trunk and not last.trunk
        lim = js_max(minGap, 0.95 * stepAt(arcAt(rows[r].f))) if wide else minGap
        if _dec('spineRows.keepGap', gap, lim) >= lim:
            kept.append(rows[r])
        elif pri(rows[r]) > pri(last) and len(kept) > 1:
            _br('spineRows.replacedByPriority'); kept[len(kept) - 1] = rows[r]
        elif (rows[r].guide or rows[r].junc) and len(kept) == 1 and _dec('spineRows.firstGap', gap, 0.02 * h) > 0.02 * h:
            kept.append(rows[r])
    while len(kept) > 1:
        l = kept[len(kept) - 1]; gap = tot - arcAt(l.f)
        if _dec('spineRows.endGap', gap, 0.02 * h) < 0.02 * h or (gap < minGap and not l.guide and not l.junc):
            kept.pop()
        else:
            break
    kept.append(Row(f=N - 1))
    # Fill every gap between fixed rows with loops spaced by stepAt(): the number of loops = the gap measured in
    # local steps, placed evenly in that measure.
    out = []
    for r in range(len(kept)):
        out.append(kept[r])
        if r == len(kept) - 1:
            break
        a = kept[r]; b = kept[r + 1]; d0 = arcAt(a.f); g = arcAt(b.f) - d0
        K = 48; cum = [0]
        for q in range(1, K + 1):
            cum.append(cum[q - 1] + (g / K) / stepAt(d0 + g * (q - 0.5) / K))
        G = cum[K]
        # one-to-one with the neighbour — except across a local guide zone of the neighbour (both loops `open`)
        if a.aligned and b.aligned and not (a.open and b.open) and _dec('spineRows.alignedGapG', G, 1.5) < 1.5:
            continue
        cnt = clamp(js_round(G) - 1, 0, 1000) if math.isfinite(G) else 0   # safety net against runaway counts
        if math.isfinite(G):
            _dec('spineRows.roundG', G - math.floor(G), 0.5)        # how close the loop count is to flipping
        for q in range(1, cnt + 1):
            tgt = G * q / (cnt + 1); j = 1
            while j < K and cum[j] < tgt:
                j += 1
            s = (tgt - cum[j - 1]) / js_or(cum[j] - cum[j - 1], 1)
            out.append(Row(f=fAt(d0 + g * (j - 1 + s) / K)))
    for r in out:
        if not r.plane:
            r.plane = rowPlane(CP, sp, r.f)
        d = arcAt(r.f); r.tgt = stepAt(d); r.prio = prioAt(d)
    # Inside the influence radius loops become parallel to the guide; the tilted plane is only handed to neighbours
    for r in out:
        if r.guide or r.aligned or r.junc or not r.plane:
            continue                                                # (a junction loop stays on its junction)
        d = arcAt(r.f)
        for k in range(len(gz)):
            w = gw(gz[k], abs(d - gz[k]['d']))
            if w <= 0:
                continue
            if not r.plane0:
                r.plane0 = r.plane
            r.plane = {'p': r.plane['p'], 'n': r.plane['n'].clone().lerp(guideRows[k]['plane']['n'], w).normalize()}
            if r.blend is None:
                r.blend = []
            r.blend.append({'g': gRows[k], 'w': w})                 # curveRowF: take on the guide loop's shape
    return out


# Sliver guard: a loop closer to its neighbour than 35% of the ORIGINAL neighbouring spacing (and under half the
# base loop step) on any curve of the lock is removed — the whole loop. At most 20% of the loops can go.
def dropSlivers(ctx, rows, Fs, Ps, plen=None):
    # port of dropSlivers (hair-modeler.clean.html:2279); plen: polyLen of every P, when already known
    R = len(rows); keep = [True] * R; h = levelH(ctx)
    unit = [(polyLen(P) if plen is None else plen[ci]) / (len(P) - 1) for ci, P in enumerate(Ps)]
    og = [[(f - F[k - 1]) * unit[ci] if k else 0 for k, f in enumerate(F)] for ci, F in enumerate(Fs)]
    pri = lambda k: 3 if k == 0 or k == R - 1 or rows[k].exact or (rows[k].tm is not None and len(rows[k].tm)) else 2 if rows[k].guide or rows[k].junc or js_or(rows[k].prio, 0) >= 0.5 else 1 if rows[k].aligned else 0
    maxDrop = math.floor(R * 0.2)
    for _ in range(maxDrop):
        idx = [k for k, v in enumerate(keep) if v]
        worst = None; wr = 0.35
        for j in range(1, len(idx)):
            for ci in range(len(Fs)):
                F = Fs[ci]; u = unit[ci]; a = idx[j - 1]; b = idx[j]
                if pri(a) == 3 and pri(b) == 3:
                    continue                                        # neither may go
                g = (F[b] - F[a]) * u
                if g >= 0.5 * h:
                    continue
                ref = js_max(js_or(at(og[ci], a), 0), js_or(at(og[ci], b + 1), 0))
                if ref <= 1e-9:
                    continue
                gp = (F[a] - F[idx[j - 2]]) * u if j > 1 else 0; gn = (F[idx[j + 1]] - F[b]) * u if j < len(idx) - 1 else 0
                if _dec('dropSlivers.ratio', g / ref, wr) < wr:
                    wr = g / ref; worst = {'j': j, 'gp': gp, 'gn': gn}
        if not worst:
            break
        a = idx[worst['j'] - 1]; b = idx[worst['j']]
        if pri(a) == 3 and pri(b) == 3:
            break
        drop = (a if pri(a) < pri(b) else b) if pri(a) != pri(b) else (a if worst['gp'] < worst['gn'] else b)
        keep[drop] = False; _br('dropSlivers.dropped')
    return keep


# Deviation guard: wherever two consecutive loops are further apart than step × (1 + deviation) on some curve,
# loops are inserted — a lock never turns into a long box without loops.
def enforceMaxGap(ctx, rows, Fs, Ps, sp, dev, spans, plen=None, spLen=None):
    # port of enforceMaxGap (hair-modeler.clean.html:2309); plen / spLen: polyLen of the curves and the spine, when known
    hL = levelH(ctx)
    pl = [polyLen(P) for P in Ps] if plen is None else plen
    unit = [pl[ci] / (len(P) - 1) for ci, P in enumerate(Ps)]; spl = js_or(polyLen(sp) if spLen is None else spLen, 1e-9)
    scale = [js_max(pl[ci] / spl, 0.25) for ci in range(len(Ps))]   # a curve on the outside of a bend is longer
    oR = [rows[0]]; oF = [[F[0]] for F in Fs]; eps = 1e-6
    onTrunk = lambda a, b: bool(spans) and any(any(a.f >= s0 - eps and b.f <= s1 + eps for s0, s1 in lst) for lst in spans.values())
    for k in range(1, len(rows)):
        a = rows[k - 1]; b = rows[k]; tgt = js_max(js_min(js_or(a.tgt, hL), js_or(b.tgt, hL)), 0.05 * hL)
        # between two loops shared exactly with an earlier lock that lock already made the gap right
        if (a.exact and b.exact) or onTrunk(a, b):
            oR.append(b)
            for ci, F in enumerate(Fs):
                oF[ci].append(F[k])
            continue
        worst = 0
        for ci, F in enumerate(Fs):
            worst = js_max(worst, (F[k] - F[k - 1]) * unit[ci] / (tgt * scale[ci]))
        if _dec('enforceMaxGap.worst', worst, 1 + dev) > 1 + dev:
            m = js_min(js_max(math.ceil(worst / (1 + dev)), js_round(worst)), 200)   # capped: never runaway
            _br('enforceMaxGap.inserted')
            for q in range(1, m):
                s = q / m
                oR.append(Row(f=a.f + (b.f - a.f) * s, plane=None, tgt=tgt, inserted={'a': a, 'b': b, 's': s}, prio=js_min(js_or(a.prio, 0), js_or(b.prio, 0))))
                for ci, F in enumerate(Fs):
                    oF[ci].append(F[k - 1] + (F[k] - F[k - 1]) * s)
        oR.append(b)
        for ci, F in enumerate(Fs):
            oF[ci].append(F[k])
    return oR, oF


def _unitInv(l2):
    """The factor normalize() multiplies by: 1 / (length() || 1), with length() = sqrt(l2) (three.js divideScalar)."""
    l = math.sqrt(l2)
    return 1 / (l if l == l and l != 0 else 1)


# Fix sharp angles: each loop slides along the curves so its edges stand square to their walls (the original's
# comment block at 2336-2346: stiffness 1 / width², groups of coincident points, Clean Topology walls CLEAN_K stiffer).
def straightenLoops(ctx, rows, Fs, Ps, pairs, s, plen=None):
    # port of straightenLoops (hair-modeler.clean.html:2348); plen: polyLen of every P, when already known
    if not pairs or (not (s > 0) and not any(p[2] for p in pairs)):
        return
    R = len(rows); C = len(Ps); h = levelH(ctx); thr = 60 * (1 - s); tol = 0.02 * h; hold = 0.1
    unit = [(polyLen(P) if plen is None else plen[ci]) / (len(P) - 1) for ci, P in enumerate(Ps)]

    def ptTan(P, f):
        # atF(P, f) and tanF(P, f) = P[i + 1].clone().sub(P[i]).normalize() together, on scalars: both start from
        # clamp(floor(f), 0, N - 2), so the index is computed once; returns (x, y, z, tx, ty, tz)
        i = math.floor(f); n2 = len(P) - 2
        i = 0 if i <= 0 else n2 if i >= n2 else i
        t = f - i
        t = 0 if t <= 0 else 1 if t >= 1 else t
        a = P[i]; b = P[i + 1]; ax = a.x; ay = a.y; az = a.z; tx = b.x - ax; ty = b.y - ay; tz = b.z - az
        inv = _unitInv(tx * tx + ty * ty + tz * tz)
        return ax + tx * t, ay + ty * t, az + tz * t, tx * inv, ty * inv, tz * inv
    pinned = lambda k, ci: k == 0 or k == R - 1 or (rows[k].fixed is not None and ci in rows[k].fixed) or (rows[k].exactC is not None and ci in rows[k].exactC) \
        or (rows[k].tm is not None and ci in rows[k].tm) or (rows[k].jfix is not None and ci in rows[k].jfix)
    F0 = [list(F) for F in Fs]
    for k in range(1, R - 1):
        # curves whose points of this loop coincide move as one (a group); a group with a held point stays
        par = list(range(C))

        def find(x):
            while par[x] != x:
                t = par[par[x]]; par[x] = t; x = t
            return x
        P0 = [atF(Ps[ci], F[k]) for ci, F in enumerate(Fs)]
        for i in range(C):
            for j in range(i + 1, C):
                if P0[i].distanceTo(P0[j]) < tol:
                    a = find(i); b = find(j)
                    if a != b:
                        par[a] = b
        fixedG = set()
        for ci in range(C):
            if pinned(k, ci):
                fixedG.add(find(ci))
        # how far (arc length) each group may go: towards the neighbouring loops, leaving at least 35% of the gap
        lo = [-math.inf] * C; hi = [math.inf] * C
        for ci in range(C):
            g = find(ci); gp = (Fs[ci][k] - Fs[ci][k - 1]) * unit[ci]; gn = (Fs[ci][k + 1] - Fs[ci][k]) * unit[ci]
            lo[g] = js_max(lo[g], -(gp - js_min(0.35 * gp, 0.25 * h))); hi[g] = js_min(hi[g], gn - js_min(0.35 * gn, 0.25 * h))
        x = [0] * C                                                 # arc-length shift of each group
        # (the groups are fixed for this loop: find() is resolved once per pair, not 40 times)
        pg = [(i, j, clean, find(i), find(j), Fs[i][k], Fs[j][k], Ps[i], Ps[j]) for i, j, clean in pairs]
        for _ in range(40):
            num = [0] * C; den = [0] * C
            for i, j, clean, gi, gj, Fik, Fjk, Pi, Pj in pg:
                if gi == gj:
                    continue
                fi = Fik + x[gi] / unit[i]; fj = Fjk + x[gj] / unit[j]
                # (the original's Vector3 chain on scalars, same operations: e = B - A, Lw = |e|, m = normalised
                # tanF(i) + tanF(j), sh = e . m; the 40 iterations x pairs x loops are the hot path of this function)
                Ax, Ay, Az, tix, tiy, tiz = ptTan(Pi, fi); Bx, By, Bz, tjx, tjy, tjz = ptTan(Pj, fj)
                ex = Bx - Ax; ey = By - Ay; ez = Bz - Az; Lw = math.sqrt(ex * ex + ey * ey + ez * ez)
                if Lw < 1e-9:
                    continue
                mx = tix + tjx; my = tiy + tjy; mz = tiz + tjz; ml2 = mx * mx + my * my + mz * mz
                if ml2 < 1e-12:
                    continue
                inv = _unitInv(ml2); mx *= inv; my *= inv; mz *= inv
                sh = ex * mx + ey * my + ez * mz; off = _asin(clamp(abs(sh) / Lw, 0, 1)) * 180 / math.pi   # degrees away from square (fdlibm asin = V8's)
                w = 1 if clean else s * clamp((off - thr) / 10, 0, 1)
                if w <= 0:
                    continue
                m2 = js_max(Lw, 0.05 * h); m2 *= m2                 # Math.max(L, 0.05 * h) ** 2: V8's x ** 2 is x * x (Python's ** is not always: js_pow)
                kw = (CLEAN_K if clean else 1) * w / m2
                num[gi] += kw * (x[gi] + sh); den[gi] += kw           # square when i moves on by sh, or j back by sh
                num[gj] += kw * (x[gj] - sh); den[gj] += kw
            moved = 0
            for g in range(C):
                if find(g) != g or g in fixedG or not den[g]:
                    continue
                t = clamp(num[g] / (den[g] + hold / (h * h)), lo[g], hi[g]); d = 0.5 * (t - x[g])
                x[g] += d; moved = js_max(moved, abs(d))
            if moved < 1e-4 * h:
                break
        for ci in range(C):
            Fs[ci][k] += x[find(ci)] / unit[ci]
    # guide priority: in the zone the result goes back towards the guide's placement, by the priority there
    for ci, F in enumerate(Fs):
        for k, r in enumerate(rows):
            if r.prio and 0 < k < R - 1 and not pinned(k, ci):
                F[k] += (F0[ci][k] - F[k]) * r.prio
        for k in range(1, R - 1):
            F[k] = clamp(F[k], F[k - 1] + 0.05, js_max(F[k + 1] - 0.05, F[k - 1] + 0.05))


# Guide Priority: every loop in the zone goes towards the guide loop's shape (r.gT) by Priority × zone weight.
def guidePriority(rows, Fs):
    # port of guidePriority (hair-modeler.clean.html:2405)
    R = len(rows)
    for ci, F in enumerate(Fs):
        moved = False; F0 = list(F); own = [True] * R              # own: the loop has a place of its own (moved or held)
        for k in range(1, R - 1):
            r = rows[k]
            held = (r.fixed is not None and ci in r.fixed) or (r.exactC is not None and ci in r.exactC) or (r.tm is not None and ci in r.tm) or (r.jfix is not None and ci in r.jfix)
            if not r.prio or held:
                continue
            if r.gT is None or ci not in r.gT:
                own[k] = False; continue
            F[k] += (r.gT[ci] - F[k]) * clamp(r.prio, 0, 1) * (r.gW[ci] if r.gW is not None and ci in r.gW else 1); moved = True
        # loops put in after the guide shapes were made (max-gap insertions) keep their place between the moved loops
        if moved:
            k = 1
            while k < R - 1:
                if not own[k]:
                    a = k - 1; b = k + 1
                    while b < R - 1 and not own[b]:
                        b += 1
                    for q in range(k, b):
                        s = (F0[q] - F0[a]) / js_or(F0[b] - F0[a], 1); F[q] = F[a] + (F[b] - F[a]) * s
                    k = b - 1
                k += 1
        if moved:
            _br('guidePriority.moved')
            for k in range(1, R - 1):
                F[k] = clamp(F[k], F[k - 1] + 0.05, js_max(F[k + 1] - 0.05, F[k - 1] + 0.05))


# Curves of one lock running together: their loop points there must be the very same (a pinned one wins).
def unifyCoincident(ctx, rows, Fs, Ps, Pidx=None, plen=None):
    # port of unifyCoincident (hair-modeler.clean.html:2432); Pidx / plen: polyIndex and polyLen of every curve of Ps (speed only)
    R = len(rows); h = levelH(ctx); tol = 0.02 * h; C = len(Ps)
    unit = [(polyLen(P) if plen is None else plen[ci]) / (len(P) - 1) for ci, P in enumerate(Ps)]
    pinned = lambda k, ci: (rows[k].fixed is not None and ci in rows[k].fixed) or (rows[k].exactC is not None and ci in rows[k].exactC) or (rows[k].tm is not None and ci in rows[k].tm) or (rows[k].jfix is not None and ci in rows[k].jfix)

    bbT = [polyBox(P, tol) for P in Ps]                             # (speed: outside the padded box the first test below fails for sure)
    chT = [chunkBoxes(ix, tol) for ix in Pidx] if Pidx is not None else None   # (and outside every padded stretch box too: chunkBoxes)

    def put(dst, k, p):                                             # curve dst takes point p for loop k, if it can
        if not inBox(p, bbT[dst]) or (chT is not None and not nearBoxes(p, chT[dst])):
            return False
        P = Ps[dst]; f = nearestF(P, p, Pidx[dst] if Pidx is not None else None)
        if atF(P, f).distanceTo(p) > tol or abs(f - Fs[dst][k]) * unit[dst] > 0.5 * h:
            return False
        if f <= Fs[dst][k - 1] + 0.02 or f >= Fs[dst][k + 1] - 0.02:
            return False
        Fs[dst][k] = f; _br('unifyCoincident.put'); return True
    bb = [Box3().setFromPoints(P).expandByScalar(tol) for P in Ps]
    for i in range(C):
        for j in range(i + 1, C):
            if not bb[i].intersectsBox(bb[j]):
                continue
            for k in range(1, R - 1):
                pi = pinned(k, i); pj = pinned(k, j)
                if pi and pj:
                    continue
                if pj:
                    put(i, k, atF(Ps[j], Fs[j][k]))
                else:
                    put(j, k, atF(Ps[i], Fs[i][k]))


# the loop planes lock D passes on: to a welded lock all of them; to a neighbour without the local-guide ones
def planesFor(D, welded, myG=None):
    # port of planesFor (hair-modeler.clean.html:2478)
    if welded:
        return [{**pl, 'open': False} for pl in D.planes]
    out = []; skipped = False
    for pl in D.planes:
        own = myG is not None and pl['lg'] is not None and len(pl['lg']) and all(i in myG for i in pl['lg'])
        if pl['local'] and not own:
            if out:
                out[len(out) - 1]['open'] = True
            skipped = True; continue
        out.append({**pl, 'open': skipped}); skipped = False
    return out


# A loop a later lock had to put on an earlier lock's trunk is a vertex the earlier lock doesn't have — a T in the
# mesh. The earlier lock gets that loop too, across all its walls (the original's comment block at 2647-2652).
def backfillTrunk(ctx, T, locks):
    # port of backfillTrunk (hair-modeler.clean.html:2653)
    h = levelH(ctx); tolOn = 0.1 * h; tolSame = 0.25 * h
    lockOf = {}
    for L in locks:
        for c in L.cs:
            lockOf[c.id] = L

    # (segD, arcOf, onPoly and has are the original's Vector3 chains written out on scalars, the same operations in
    # the same order as nearestF / distanceTo: these run over a million segments on the example)
    def segD(p, a, b):
        ax = a.x; ay = a.y; az = a.z; abx = b.x - ax; aby = b.y - ay; abz = b.z - az
        l2 = abx * abx + aby * aby + abz * abz
        if l2 > 0:
            t = ((p.x - ax) * abx + (p.y - ay) * aby + (p.z - az) * abz) / l2
            t = 0 if t <= 0 else 1 if t >= 1 else t
        else:
            t = 0
        dx = (ax + abx * t) - p.x; dy = (ay + aby * t) - p.y; dz = (az + abz * t) - p.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    # (speed, ours, lot 4.2c: nothing moves during the backfill — the curves' samples stay, a loop point is never
    # changed, only new ones are INSERTED — so arcOf(c, p) and onPoly(p, pr) are fixed for a given (curve or stretch,
    # point) and are computed once: memos keyed by identity, each entry holding the point itself so its id cannot be
    # reused while the memo lives. has(c, p) is the OR of one test per loop of c, and a loop is only ever added: its
    # answer is kept too and turned True by noteAdded when a loop within tolSame comes in (below). The six passes of
    # the original asked all three again and again: on a tangled model (docs/fase-4-desempenho.md, "Achado do
    # stress") 12.5 million onPoly, 4.2 million has and a million arcOf calls, ~550 s.)
    arcMemo = {}                                                    # curve id -> { id(p): (p, arc position) }

    # arc position of p along curve c (its samples), and the point at an arc position
    def arcOf(c, p):
        m = arcMemo.get(c.id)
        if m is None:
            m = arcMemo[c.id] = {}
        ent = m.get(id(p))
        if ent is not None and ent[0] is p:
            return ent[1]
        s = c.samples; A = c.arc; best = 0; bd = math.inf; px = p.x; py = p.y; pz = p.z
        for i in range(len(s) - 1):
            a = s[i].p; b = s[i + 1].p; ax = a.x; ay = a.y; az = a.z
            abx = b.x - ax; aby = b.y - ay; abz = b.z - az; l2 = abx * abx + aby * aby + abz * abz
            if l2 > 0:
                t = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / l2
                t = 0 if t <= 0 else 1 if t >= 1 else t
            else:
                t = 0
            dx = (ax + abx * t) - px; dy = (ay + aby * t) - py; dz = (az + abz * t) - pz
            d = dx * dx + dy * dy + dz * dz
            if d < bd:
                bd = d; best = A[i] + (A[i + 1] - A[i]) * t
        m[id(p)] = (p, best)
        return best

    def atArc(c, x):
        s = c.samples; A = c.arc
        # (the original's `while (i < A.length - 2 && A[i + 1] < x) i++` over the non-decreasing arc lengths: the first
        # i whose A[i + 1] is not below x, at most len - 2 — bisect_left over A[1 .. len - 2] lands on the same i)
        i = bisect_left(A, x, 1, len(A) - 1) - 1
        t = clamp((x - A[i]) / js_or(A[i + 1] - A[i], 1), 0, 1)
        return s[i].p.clone().lerp(s[i + 1].p, t)
    # pairs of curves of different locks sharing a stretch of segments: [A, B, stretch polyline of A, its end points]
    pairs = []
    all_ = [c for L in locks for c in L.cs if T.get(c.id) and len(c.samples) > 1 and c.arc]
    live = lambda c: ctx.LOCKS_REBUILT is None or c.id in ctx.LOCKS_REBUILT
    byAnchor = {}
    for c in all_:
        for a in c.anchors:
            byAnchor.setdefault(a, {})[c] = True
    for A in all_:
        Bs = {}
        for a in A.anchors:
            for c in byAnchor[a]:
                Bs[c] = True
        for B in Bs:
            if A is B or lockOf.get(A.id) is lockOf.get(B.id) or (not live(A) and not live(B)):
                continue
            sh = []
            for k in range(len(A.anchors) - 1):
                ia = index_of(B.anchors, A.anchors[k]); ib = index_of(B.anchors, A.anchors[k + 1])
                if ia >= 0 and ib >= 0 and abs(ia - ib) == 1:
                    sh.append(k)
            if not sh:
                continue
            poly = [s.p for s in A.samples if s.seg in sh]; ends = []
            for q, k in enumerate(sh):
                if q == 0 or sh[q - 1] != k - 1:
                    ends.append(A.anchors[k].pos)
                if q == len(sh) - 1 or sh[q + 1] != k + 1:
                    ends.append(A.anchors[k + 1].pos)
            # (bb: Box3().setFromPoints(poly).expandByScalar(tolOn), as a tuple — polyBox exact grows it by tolOn itself)
            pairs.append({'A': A, 'B': B, 'poly': poly, 'ends': ends, 'bb': polyBox(poly, tolOn, exact=True)})
    if not pairs:
        return
    pairsByB = {}                                                   # (speed: the pairs whose B is a curve, in `pairs` order)
    for pr in pairs:
        pairsByB.setdefault(id(pr['B']), []).append(pr)
    onMemo = {}                                                     # id(pr) -> { id(p): (p, on the stretch) }

    def onPoly(p, pr):
        m = onMemo.get(id(pr))
        if m is None:
            m = onMemo[id(pr)] = {}
        ent = m.get(id(p))
        if ent is not None and ent[0] is p:
            return ent[1]
        r = _onPoly(p, pr)
        m[id(p)] = (p, r)
        return r

    def _onPoly(p, pr):
        bb = pr['bb']; px = p.x; py = p.y; pz = p.z
        if px < bb[0] or py < bb[1] or pz < bb[2] or px > bb[3] or py > bb[4] or pz > bb[5]:   # bb.containsPoint(p)
            return False
        Pp = pr['poly']
        a = Pp[0]; ax = a.x; ay = a.y; az = a.z
        for k in range(len(Pp) - 1):                                # segD(p, Pp[k], Pp[k + 1]) < tolOn, inlined
            b = Pp[k + 1]; bx = b.x; by = b.y; bz = b.z
            abx = bx - ax; aby = by - ay; abz = bz - az; l2 = abx * abx + aby * aby + abz * abz
            if l2 > 0:
                t = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / l2
                t = 0 if t <= 0 else 1 if t >= 1 else t
            else:
                t = 0
            dx = (ax + abx * t) - px; dy = (ay + aby * t) - py; dz = (az + abz * t) - pz
            if math.sqrt(dx * dx + dy * dy + dz * dz) < tolOn:
                return True
            ax = bx; ay = by; az = bz
        return False
    # has(c, p) is the OR of one test per loop of c, and a loop is only ever added: an answer, once computed over the
    # loops c has at that moment, can only turn True — noteAdded does that for the loop it puts in. So the memo holds
    # the current answer for every (c, p) asked so far and a question costs a lookup; on the tangled model the loops
    # added ran to ~40 per curve and 4 million questions scanned them again each time (160 million sqrt).
    hasMemo = {}                                                    # curve id -> { id(p): [p, within tolSame of a loop of c] }

    def has(c, p):                                                  # any(q.distanceTo(p) < tolSame for q in rows)
        cid = c.id; m = hasMemo.get(cid)
        if m is None:
            m = hasMemo[cid] = {}
        ent = m.get(id(p))
        if ent is not None and ent[0] is p:
            return ent[1]
        px = p.x; py = p.y; pz = p.z; r = False
        for q in T[cid].rows:                                       # (its loops as they are now)
            dx = q.x - px; dy = q.y - py; dz = q.z - pz
            if math.sqrt(dx * dx + dy * dy + dz * dz) < tolSame:
                r = True; break
        m[id(p)] = [p, r]
        return r
    ver = {}                                                        # curve id -> loops the backfill put into it (onRows' key)

    def noteAdded(cid, q):                                          # a loop put into curve cid: the answers about cid may turn True
        ver[cid] = ver.get(cid, 0) + 1
        m = hasMemo.get(cid)
        if m:
            qx = q.x; qy = q.y; qz = q.z
            for ent in m.values():
                if not ent[1]:
                    p = ent[0]; dx = qx - p.x; dy = qy - p.y; dz = qz - p.z   # (q.distanceTo(p), q the loop, as in has)
                    if math.sqrt(dx * dx + dy * dy + dz * dz) < tolSame:
                        ent[1] = True
    onCache = {}                                                    # id(pr) -> (ver of A then, the loops of A on the stretch, in row order)

    def onRows(pr):                                                 # [p for p in rows(A) if onPoly(p, pr)], kept while A's rows are the same
        A = pr['A']; v = ver.get(A.id, 0); ent = onCache.get(id(pr))
        if ent is None or ent[0] != v:
            ent = onCache[id(pr)] = (v, [q for q in T[A.id].rows if onPoly(q, pr)])
        return ent[1]
    for _ in range(6):
        # unmatched vertices: a point of A's loop on the stretch shared with B that B has no vertex for
        want = {}                                                   # lock -> [{ c: B, p }]
        for pr in pairs:
            B = pr['B']; ends = pr['ends']
            for p in onRows(pr):                                    # (the loops of A: `if not onPoly(p, pr) ... continue`)
                if any(e.distanceTo(p) < tolSame for e in ends) or has(B, p):
                    continue
                Y = lockOf[B.id]
                want.setdefault(Y, []).append({'c': B, 'p': p})
        if not want:
            return
        added = 0
        for Y, lst in want.items():
            rowsOf = lambda c: T[c.id].rows
            done = []
            for item in lst:
                B = item['c']; p = item['p']
                if has(B, p) or any(q.distanceTo(p) < tolSame for q in done):
                    continue
                # where it goes in B's loops
                RB = rowsOf(B); sp = arcOf(B, p); sB = [arcOf(B, q) for q in RB]
                k = 1
                while k < len(RB) and sB[k] < sp:
                    k += 1
                if k <= 0 or k >= len(RB):
                    continue
                t = (sp - sB[k - 1]) / js_or(sB[k] - sB[k - 1], 1)
                if not (0.1 < t < 0.9):
                    continue                                        # (a sliver otherwise)
                # every curve of Y: the share of the gap — or another lock's unmatched vertex lying in that gap on it
                pts = []
                for Cc in Y.cs:
                    if Cc is B:
                        pts.append(p.clone()); continue
                    Rr = rowsOf(Cc); a0 = arcOf(Cc, Rr[k - 1]); a1 = arcOf(Cc, Rr[k]); guess = atArc(Cc, a0 + (a1 - a0) * t)
                    best = None; bd = 0.5 * js_max(a1 - a0, 1e-6)
                    for pr in pairsByB.get(id(Cc), ()):             # (`for pr in pairs: if pr['B'] is Cc`, the same order)
                        for q in onRows(pr):                        # (the loops of pr.A: `if not onPoly(q, pr) ... continue`)
                            if has(Cc, q):
                                continue
                            aq = arcOf(Cc, q)
                            if aq <= a0 or aq >= a1:
                                continue
                            d = abs(aq - (a0 + (a1 - a0) * t))
                            if d < bd:
                                bd = d; best = q.clone()
                    pts.append(best or guess)
                for ci, Cc in enumerate(Y.cs):
                    rowsOf(Cc).insert(k, pts[ci]); noteAdded(Cc.id, pts[ci])
                comp = T[B.id].comp
                if comp and comp.cent and at(comp.cent, k - 1) and at(comp.cent, k):
                    comp.cent.insert(k, comp.cent[k - 1].clone().lerp(comp.cent[k], t))
                done.append(p); added += 1; _br('backfillTrunk.added')
        if not added:
            return


def _lock_key(cs):
    return ','.join(str(i) for i in sorted(c.id for c in cs))


def buildRowTables(P, trace_ids=None, changed=None, ctx=None):
    """port of buildRowTables (hair-modeler.clean.html:2502).

    A full rebuild by default. With `changed` (the ids of the curves rebuilt since the last call: scene.settle's
    'changed') and the RowBuild `ctx` returned by that call, only the locks those curves touch are rebuilt,
    transitively through shared anchors (the original's `dirty` flood fill, 2509-2519); the other locks come from
    ctx.LOCK_CACHE with the loops they had, exactly as the app does after an anchor drag.

    The original takes the incremental path only while the set of locks is the one in LOCK_CACHE (2508) and falls
    back to a full rebuild whenever a wall was made or removed. Here (16/10/2026, docs/fase-3-desempenho.md) a lock
    whose key is not in the cache is marked dirty itself, so it seeds the same flood: the locks it touches through
    shared anchors are rebuilt with it and the stale keys are dropped. A lock built from scratch sees the same
    cached neighbours as in a full rebuild, so the tables come out bit-identical to a full rebuild of the same state
    (tests/test_rows_incremental.TestIncrementalEqualsFull checks this on the example for every edit the add-on
    runs this way: a drag, a guide switched, a group's conv, Clean topology, a move between groups, a wall made or
    removed).

    Returns the RowBuild: .T is the table (curve id -> RowTable), .locks the lock descriptors (longest first),
    .LOCKS_REBUILT the ids of the curves whose locks were rebuilt (None after a full rebuild), .LOCK_CACHE and .DBG
    what the original keeps. trace_ids: curve ids whose stage snapshots go to DBG.traceOut."""
    if ctx is None:
        ctx = RowBuild(P)
    if trace_ids is not None:
        ctx.DBG['trace'] = set(trace_ids)
    T = {}; locks = []; ctx.MODEL_CHANGED = False; ctx.STALE_CURVES = set()
    comps = [cs for cs in ([c for c in (P.items.get(i) for i in ids) if c and len(c.samples) >= 2] for ids in surfaceComponents(P)) if cs]
    keys = [_lock_key(cs) for cs in comps]; keyset = set(keys)
    dirty = None
    if changed is not None and ctx.LOCK_CACHE:
        byA = {}                                                    # anchor -> keys of the locks using it (a Map keyed by the Anchor itself)
        for i, cs in enumerate(comps):
            for c in cs:
                for a in c.anchors:
                    byA.setdefault(a, {})[keys[i]] = True
        lockAnchors = {k: [a for c in comps[i] for a in c.anchors] for i, k in enumerate(keys)}
        dirty = {}; stack = []

        def mark(k):
            if k not in dirty:
                dirty[k] = True; stack.append(k)
        for cid in changed:
            c = P.items.get(cid)
            if c:
                for a in c.anchors:
                    for k in byA.get(a, {}):
                        mark(k)
        for k in keys:                                              # (ours) a lock not in the cache: made, split or merged by a wall — built, with what it touches
            if k not in ctx.LOCK_CACHE:
                mark(k)
        while stack:
            for a in lockAnchors[stack.pop()]:
                for k in byA[a]:
                    mark(k)
        for k in [k for k in ctx.LOCK_CACHE if k not in keyset]:    # (ours) locks that no longer exist
            del ctx.LOCK_CACHE[k]
    # (`dirty` may be an EMPTY set: in JS that is still truthy, so nothing is rebuilt and everything comes from the
    # cache; hence the `is None` tests below, never a truth test on the dict)
    # ours (lot 3.7-estab, rowhist.py): a component of locks to build whose every entry was built once for the very
    # inputs it has now (an undo) takes those entries back; the entries built below are kept for that (at the end)
    ctx.LOCKS_RESTORED = set()
    hist_comps = None
    if ctx.HISTORY is not None and ctx.DBG['trace'] is None and keys:
        from . import rowhist
        from .scene import tables_signature
        byA2 = {}
        for i, cs in enumerate(comps):
            for c in cs:
                for a in c.anchors:
                    byA2.setdefault(a, {})[keys[i]] = True
        lockA = {k: [a for c in comps[i] for a in c.anchors] for i, k in enumerate(keys)}
        at_key = {k: i for i, k in enumerate(keys)}
        hist_comps = rowhist.components(keys, lockA, byA2)
        conv0 = {g.id: dict(g.conv) if g.conv else None for g in P.of_type('group')}
        sig = gpart = None
        for comp in hist_comps:
            if dirty is None or not any(k in dirty for k in comp):
                continue
            if sig is None:
                sig = tables_signature(P); gpart = rowhist.global_part(P, sig)
            fp = rowhist.component_fp(P, [c for k in comp for c in comps[at_key[k]]], sig, gpart, ctx.HIST_SAMPLES)
            got = [rowhist.recall(ctx.HISTORY, fp, k) for k in comp]
            if all(e is not None for e in got):
                for k, e in zip(comp, got):
                    e['L'].cs = [P.items.get(c.id, c) for c in e['L'].cs]
                    ctx.LOCK_CACHE[k] = e
                    del dirty[k]
                    ctx.LOCKS_RESTORED.update(c.id for c in e['L'].cs)
                    convOf(P, e['L'].cs[0])                         # (what building it would do to its group's conv: groupConv fills it)
    if dirty is None:
        ctx.LOCK_CACHE = {}
    ctx.LOCKS_REBUILT = set() if dirty is not None else None
    byAnchor = P.anchor_curves()
    for ci0, cs in enumerate(comps):
        key = keys[ci0]
        if dirty is not None and key not in dirty:
            locks.append(ctx.LOCK_CACHE[key]['L']); continue       # unchanged: its geometry from the cache
        if dirty is not None:
            for c in cs:
                ctx.LOCKS_REBUILT.add(c.id)
        own = [[s.p for s in c.samples] for c in cs]; lens = [polyLen(o) for o in own]; Lmax = js_max(*lens)
        # a converted modifier (c.para on every curve, the same anchor count): anchor k of every curve is one cross-section
        # (port of the `para` branch, hair-modeler.clean.html:2600-2619)
        secs = lockSecs(cs) if len(cs) > 1 and all(c.para for c in cs) else None; para = secs is not None
        # a lock from a file without section numbers (numbered by index now): they are written down at once — an anchor put
        # in later must not turn the others of its curve into ones counted by arc length
        if para and all(a.sec is None for c in cs for a in c.anchors):
            byId = {a.id: a for c in cs for a in c.anchors}
            for aid, s in secs.items():
                byId[aid].sec = s
            P.gen += 1; ctx.MODEL_CHANGED = True                    # (anchors changed: the memos keyed on gen go; the caller saves them)
            _br('buildRowTables.secsWritten')
        if para:
            _br('buildRowTables.para')
            Ps = resampleParallel(cs, NS, secs)                     # (no extendAlongHost: `!para && …`)
            ref = Ps[0]
        else:
            src = [extendAlongHost(P, c, cs, Lmax - lens[i], byAnchor) or own[i] for i, c in enumerate(cs)]
            ref = resample(src[0], NS)
            Ps = []
            for ci in range(len(cs)):
                if ci == 0:
                    Ps.append(ref); continue
                Pc = resample(src[ci], NS)
                if _dec('buildRowTables.reverse', (Pc[0].distanceTo(ref[0]) + Pc[NS - 1].distanceTo(ref[NS - 1])) - (Pc[0].distanceTo(ref[NS - 1]) + Pc[NS - 1].distanceTo(ref[0])), 0) > 0:
                    Pc.reverse(); _br('buildRowTables.reversedCurve')
                Ps.append(Pc)
        sp = []
        for i in range(len(ref)):
            v = V3()
            for Pc in Ps:
                v.add(Pc[i])
            sp.append(v.divideScalar(len(Ps)))
        ln = 0; rad = 0
        for i in range(1, NS):
            ln += sp[i].distanceTo(sp[i - 1])
        for i in range(NS):
            for Pc in Ps:
                rad = js_max(rad, Pc[i].distanceTo(sp[i]))
        locks.append(Lock(cs=cs, Ps=Ps, sp=sp, len=ln, rad=rad, topo=None, key=key, para=para, Pidx=[polyIndex(Pc) for Pc in Ps], spIdx=polyIndex(sp),
                          plen=[polyLen(Pc) for Pc in Ps], spLen=polyLen(sp)))
    # longest locks first; a lock welded to an already built one takes over its loops on the common trunk
    locks.sort(key=lambda L: -L.len)
    done = []; loopPts = {}
    ctx.TRUNK_KEY = {}; ctx.TRUNK_LOCKS = done
    ctx.ANCHOR_CURVES = {}
    for c in P.curves():
        for a in c.anchors:
            ctx.ANCHOR_CURVES.setdefault(a.id, []).append(c)
    ctx.DBG['locks'] = []
    progress = getattr(ctx, 'PROGRESS', None); built_n = 0          # (ours, 4.2c: the caller counts the locks built — the cursor's number)
    for L in locks:
        if dirty is not None and L.key not in dirty:                # unchanged and linked to nothing that changed: cached
            e = ctx.LOCK_CACHE[L.key]
            done.append(L)
            for k, q in enumerate(L.allPts):
                ctx.TRUNK_KEY[q] = f'{len(done)}:{e["rowOf"][k]}'
            for cid, v in e['T']:
                T[cid] = v
            ctx.DBG['locks'].append(e['dbg'])
            continue
        saved_ctx = set_ctx(L.key, None)
        ctx.CP = convOf(P, L.cs[0])                                 # this lock's group settings
        hL = levelH(ctx)
        # (speed: a point outside a curve's box padded by the tolerance cannot pass the `< 0.02 * hL` test; polyBox —
        # nor one outside every padded stretch box of the curve: chunkBoxes)
        tolOn = 0.02 * hL; bbOn = [polyBox(Pc, tolOn) for Pc in L.Ps]; chOn = [chunkBoxes(ix, tolOn) for ix in L.Pidx]
        onLock = lambda pl: pl['pts'] is not None and any(any(inBox(q, bbOn[ci]) and nearBoxes(q, chOn[ci]) and atF(Pc, nearestF(Pc, q, L.Pidx[ci])).distanceTo(q) < tolOn for ci, Pc in enumerate(L.Ps)) for q in pl['pts'])
        # welded = sharing at least one SEGMENT (two neighbouring anchors) — a common trunk. The longest common trunk first.
        segKey = lambda a, b: (a.id, b.id) if a.id < b.id else (b.id, a.id)
        mySegs = set()
        for c in L.cs:
            for k in range(len(c.anchors) - 1):
                mySegs.add(segKey(c.anchors[k], c.anchors[k + 1]))

        def weldScore(D):
            n = 0
            for c in D.cs:
                for k in range(len(c.anchors) - 1):
                    if segKey(c.anchors[k], c.anchors[k + 1]) in mySegs:
                        n += 1
            return n
        welded = [x['D'] for x in sorted(({'D': D, 's': weldScore(D)} for D in done), key=lambda x: -x['s']) if x['s'] > 0]
        weldedAny = any(D is not L and weldScore(D) > 0 for D in locks)
        gRowsL = lockGuideRows(P, ctx.CP, L); jRowsL = [] if weldedAny else lockJunctionRows(ctx, L)
        ctx.TIPZ = lockTip(P, L)
        ref = None
        if welded:
            _br('buildRowTables.weldedLock')
            seen = set(); trunk = []
            for D in welded:
                for pl in planesFor(D, True):
                    if id(pl['p']) not in seen and onLock(pl):
                        seen.add(id(pl['p'])); trunk.append({**pl, 'trunk': True})
            rows = spineRows(ctx, L.sp, trunk, gRowsL, True, jRowsL); ref = welded[0]
        else:
            rows = spineRows(ctx, L.sp, None, gRowsL, False, jRowsL)
        ctx.TRUNK_PTS = [q for D in welded for q in D.allPts]     # only welded locks' loop points count
        rows = trunkLevels(ctx, L, rows)
        rows = trunkOnly(ctx, L, rows, welded)
        Fo = trunkOwnFs(ctx, L, rows)                               # (before the real pass: it sets each loop's guide shape)
        # a converted modifier: the loop goes through the same index on every curve (no plane cuts — on a curl a plane
        # across the spine hits the inner and outer edges a turn apart); only a guide hit holds a point elsewhere (2670)
        if L.para:
            Fs = [[r.fixed[ci] if r.fixed is not None and ci in r.fixed else r.f for r in rows] for ci in range(len(L.Ps))]
        else:
            Fs = [curveRowF(ctx, Pc, rows, L.sp, ci, L.plen[ci], L.spLen) for ci, Pc in enumerate(L.Ps)]
        if Fo:
            trunkFade(ctx, L, rows, Fs, Fo)
        ctx.TRUNK_PTS = None
        tr = None
        if ctx.DBG['trace'] is not None and any(c.id in ctx.DBG['trace'] for c in L.cs):
            tr = []; ctx.DBG['traceOut'][','.join(str(c.id) for c in L.cs)] = tr

        def snapT(st):
            if tr is not None:
                tr.append({'st': st,
                           'rows': [{'f': float(to_fixed(r.f, 2)), 'k': 'G' if r.guide else 'J' if r.junc else 'T' if r.added else 'A' if r.aligned else 'I' if r.inserted else 'M' if r.tm is not None else 'o',
                                     'prio': float(to_fixed(js_or(r.prio, 0), 2))} for r in rows],
                           'y': [[float(to_fixed(atF(L.Ps[ci], f).y, 1)) for f in F] for ci, F in enumerate(Fs)]})
        snapT('cut')
        keep = dropSlivers(ctx, rows, Fs, L.Ps, L.plen)
        ctx.DBG['locks'].append({'curves': [c.id for c in L.cs], 'len': float(to_fixed(L.len, 2)), 'h': float(to_fixed(levelH(ctx), 3)), 'rows': len(rows), 'kept': sum(1 for k in keep if k),
                                 'aligned': sum(1 for r in rows if r.aligned), 'guide': sum(1 for r in rows if r.guide), 'ref': ref.cs[0].id if ref else None})
        rows = [r for k, r in enumerate(rows) if keep[k]]; Fs = [[f for k, f in enumerate(F) if keep[k]] for F in Fs]
        snapT('slivers')
        rows, Fs = enforceMaxGap(ctx, rows, Fs, L.Ps, L.sp, clamp(nullish(ctx.CP.get('dev'), 0.3), 0, 1), L.trunkSpans, L.plen, L.spLen)
        snapT('maxgap')
        if not L.para:                                              # (its loops are square already: no Straighten, no Clean topology; 2684)
            pairs = []
            for s in P.surfaces():
                i = next((k for k, c in enumerate(L.cs) if c.id == s.a), -1); j = next((k for k, c in enumerate(L.cs) if c.id == s.b), -1)
                if i >= 0 and j >= 0 and i != j:
                    pairs.append([i, j, bool(s.p and truthy(s.p.get('clean')))])
            straightenLoops(ctx, rows, Fs, L.Ps, pairs, js_or(ctx.CP.get('straighten'), 0), L.plen)
        snapT('straighten')
        guidePriority(rows, Fs)
        snapT('guideprio')
        if not L.para:                                              # (2688)
            unifyCoincident(ctx, rows, Fs, L.Ps, L.Pidx, L.plen)
        snapT('unify')
        pts = [[atF(Pc, f) for f in Fs[ci]] for ci, Pc in enumerate(L.Ps)]
        # debug: kind of every loop (E exact on a welded trunk, A shared, G guide, J Y junction, I inserted, o own)
        # (T a loop added for a neighbour's loop no row took, F let go of the trunk next to it — see trunkLevels)
        for ci, c in enumerate(L.cs):
            ctx.DBG['rowKinds'][c.id] = ''.join('E' if r.exactC is not None and ci in r.exactC else 'T' if r.added else 'F' if r.free is not None and ci in r.free else 'J' if r.junc else 'A' if r.aligned else 'G' if r.guide else 'I' if r.inserted else 'o' for r in rows)
        cent = []
        for k in range(len(rows)):
            v = V3()
            for p in pts:
                v.add(p[k])
            cent.append(v.divideScalar(len(pts)))
        for k, r in enumerate(rows):                                # inserted loops: plane between their neighbours
            if r.plane or not r.inserted:
                continue
            a = r.inserted['a']; b = r.inserted['b']; s = r.inserted['s']
            na = a.plane['n'] if a.plane else None; nb = b.plane['n'] if b.plane else None
            n = na.clone().lerp(nb, s) if na and nb else (na or nb or V3(0, 1, 0)).clone()
            r.plane = {'p': cent[k].clone(), 'n': n.normalize() if n.lengthSq() > 1e-12 else V3(0, 1, 0)}

        # loops made by a local guide (its loop and the loops in its zone) are flagged `local`
        def localRow(r):
            if r.guide:
                return bool(r.local)
            if r.blend is not None:
                return all(b['g'].local for b in r.blend)
            if r.inserted is not None:
                return localRow(r.inserted['a']) and localRow(r.inserted['b'])
            return False

        # the guides that made the loop
        def lgOf(r):
            if r.guide:
                return {r.gid: True}
            if r.blend is not None:
                return {b['g'].gid: True for b in r.blend}
            if r.inserted is not None:
                return {**(lgOf(r.inserted['a']) or {}), **(lgOf(r.inserted['b']) or {})}
            return r.lg or None
        L.planes = []
        for k, r in enumerate(rows):
            rk = 0
            for p in pts:
                rk = js_max(rk, p[k].distanceTo(cent[k]))
            if not r.plane:
                continue
            # points of one shared loop from every lock that uses it (keyed by the plane's point object)
            lp = loopPts.setdefault(id(r.plane['p']), [])
            for p in pts:
                lp.append(p[k])
            L.planes.append({'p': r.plane['p'], 'n': r.plane['n'], 'c': cent[k], 'reach': 2 * rk + levelH(ctx), 'pts': lp, 'local': localRow(r), 'lg': lgOf(r), 'junc': bool(r.junc)})
        done.append(L)
        L.rowGid = [r.gid if r.guide else None for r in rows]       # its guide loops (trunkLevels pairs them only with each other)
        L.allPts = []; rowOf = []
        for p in pts:
            for k, q in enumerate(p):
                L.allPts.append(q); rowOf.append(k); ctx.TRUNK_KEY[q] = f'{len(done)}:{k}'
        comp = Comp(cent=cent, n=len(L.cs)); te = []
        for i, c in enumerate(L.cs):
            v = RowTable(rows=pts[i], comp=comp); T[c.id] = v; te.append([c.id, v])
        ctx.LOCK_CACHE[L.key] = {'L': L, 'T': te, 'rowOf': rowOf, 'dbg': ctx.DBG['locks'][len(ctx.DBG['locks']) - 1]}
        set_ctx(*saved_ctx)
        if progress is not None:
            built_n += 1; progress(built_n)
    ctx.CP = settings_conv(P); ctx.TIPZ = None; ctx.ROWS_V += 1
    backfillTrunk(ctx, T, locks)
    if hist_comps is not None and not truthy(P.settings.step) and any((dict(g.conv) if g.conv else None) != conv0.get(g.id) for g in P.of_type('group')):
        hist_comps = None                                           # (no step of its own and a conv filled meanwhile: the default step may have changed mid-build)
    if hist_comps is not None:
        # the entries just built, under the fingerprint of what they read: taken AFTER the build, which may have filled
        # the groups' conv (groupConv) — a conv without the defaults and the same conv filled are other fingerprints
        # when the project has no step of its own (scene.conv_seen), and every state after this one has them filled
        sig = tables_signature(P); gpart = rowhist.global_part(P, sig)
        for comp in hist_comps:
            if dirty is not None and not any(k in dirty for k in comp):
                continue
            fp = rowhist.component_fp(P, [c for k in comp for c in comps[at_key[k]]], sig, gpart, ctx.HIST_SAMPLES)
            for k in comp:
                rowhist.remember(ctx.HISTORY, fp, k, ctx.LOCK_CACHE[k])
    for k in P.of_type('tip'):                                     # old Tip items → painted Density masks (once, 2728)
        if k.p.get('v') == 2:
            continue
        ids = tipConvert(P, ctx, k)
        if ids:
            # (the tables just built read the old zone: as in the original, they are what this call returns — its frame
            # loop keeps them until the locks are built again, its Convert builds from scratch; ours: the curves, for a
            # caller that keeps the tables and must hold what a build from scratch gives, live.row_tables)
            ctx.MODEL_CHANGED = True; ctx.STALE_CURVES.update(ids)
    ctx.T = T; ctx.locks = locks
    return ctx
