# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Welded trunks and the loop positions on each curve (hair-modeler.clean.html:1762-2185): trunkLevels,
trunkOnly, trunkOwnFs, trunkFade and curveRowF. Called by buildRowTables (rows.py) with the RowBuild context."""
import math

from .geom import atF, chunkBoxes, inBox, nearBoxes, nearestF, polyBox, polyLen
from .js import at, clamp, index_of, js_max, js_min, js_or, js_round, js_str, nullish, to_fixed
from .rowbase import Row, levelH, rowPlane
from .trace import br as _br, dec as _dec, dec_near
from .vec3 import V3


def _sk(a, b):
    # the original's segment key `a.id + '_' + b.id` (ordered by id); a tuple serves the same lookups
    return (a.id, b.id) if a.id < b.id else (b.id, a.id)


def segsOf(D):
    # trunkLevels' segsOf: the anchor segments of lock D, cached on it
    if D.segSet is None:
        D.segSet = set()
        for c in D.cs:
            for k in range(1, len(c.anchors)):
                D.segSet.add(_sk(c.anchors[k - 1], c.anchors[k]))
    return D.segSet


# Welded trunks (curves of this lock running along curves of locks built earlier): this lock's loops must go exactly
# through those locks' loop vertices on the common curves. (See the original's comment block at 1752-1761.)
def trunkLevels(ctx, L, rows):
    # port of trunkLevels (hair-modeler.clean.html:1762)
    if not ctx.TRUNK_PTS:
        return rows
    hL = levelH(ctx); tol = 0.02 * hL; N = len(L.sp); unit = js_or(polyLen(L.sp) if L.spLen is None else L.spLen, 1e-9) / (N - 1); R = len(rows)
    groups = {}                                                     # neighbour loop key -> { lock, at: Map(ci -> f) }
    wsegs = set()
    for D in (ctx.TRUNK_LOCKS or []):
        wsegs |= segsOf(D)
    tolW = 0.1 * hL

    def onWSeg(c, q):
        s = c.samples; bi = 0; bd = math.inf
        for k in range(len(s)):
            d = s[k].p.distanceToSquared(q)
            if d < bd:
                bd = d; bi = k
        g = s[bi].seg if at(s, bi) else None
        return g is not None and g < len(c.anchors) - 1 and _sk(c.anchors[g], c.anchors[g + 1]) in wsegs

    for ci, P in enumerate(L.Ps):
        bb = polyBox(P, tolW, exact=True)                           # Box3().setFromPoints(P).expandByScalar(tolW), as a tuple
        Pi = L.Pidx[ci] if L.Pidx is not None else None
        # (speed: a point farther than tolW from every stretch of P has d > tolW > tol: `d >= tol` holds and `d < tolW`
        # does not, so the `continue` below is certain and it is skipped before nearestF — chunkBoxes, the padded
        # stretch boxes; lot 4.2c)
        ch = chunkBoxes(Pi, tolW) if Pi is not None else None
        for q in ctx.TRUNK_PTS:
            if not inBox(q, bb):                                    # (bb.containsPoint(q))
                continue
            if ch is not None and not nearBoxes(q, ch):
                continue
            f = nearestF(P, q, Pi)
            if not (f > 0.5 and f < N - 1.5):
                continue
            d = atF(P, f).distanceTo(q)
            if _dec('trunkLevels.onTrunk', d, tol) >= tol and not (d < tolW and onWSeg(L.cs[ci], q)):
                continue
            key = ctx.TRUNK_KEY.get(q)
            if key is None:
                continue
            g = groups.get(key)
            if g is None:
                g = {'key': key, 'lock': key.split(':')[0], 'at': {}}; groups[key] = g
            if ci not in g['at']:
                g['at'][ci] = f

    # a level's place on the spine: the mean of its points' stations along the lock (the foot of each point on the spine)
    def avg(m):
        s = 0
        for ci, f in m.items():
            s += nearestF(L.sp, atF(L.Ps[ci], f), L.spIdx)
        return s / len(m)
    G = list(groups.values())
    for g in G:
        g['s'] = avg(g['at'])
    # a lone crossing point is no trunk: a loop counts only with another loop of the same lock within 2 steps
    G = [g for g in G if any(o is not g and o['lock'] == g['lock'] and abs(o['s'] - g['s']) * unit < 2 * hL for o in G)]
    if not G:
        return rows
    G.sort(key=lambda g: g['s'])
    # `rank` = how many trunk loops lie before this one on its curve (mean over its curves)
    onCurve = {}
    for g in G:
        for ci, f in g['at'].items():
            onCurve.setdefault(ci, []).append(f)
    for a in onCurve.values():
        a.sort()
    for g in G:
        s = 0
        for ci, f in g['at'].items():
            s += index_of(onCurve[ci], f)
        g['rank'] = s / len(g['at'])
    # a guide loop of one neighbour goes with the same guide's loop of the other, never with an ordinary loop
    for g in G:
        li, k = (int(x) for x in g['key'].split(':'))
        D = at(ctx.TRUNK_LOCKS, li - 1) if ctx.TRUNK_LOCKS else None
        g['gid'] = nullish(at(D.rowGid, k), None) if D and D.rowGid is not None else None
    gRows = [r for r in rows if r.guide and r.plane]

    def zoneAt(s):
        if not gRows:
            return None
        nr = rows[0]
        for r in rows:
            if abs(r.f - s) < abs(nr.f - s):
                nr = r
        if not (js_or(nr.prio, 0) >= 0.5):
            return None
        g = gRows[0]
        for r in gRows:
            if abs(r.f - s) < abs(g.f - s):
                g = r
        return g.plane['n']

    def hOf(at_, n):
        s = 0
        for ci, f in at_.items():
            s += atF(L.Ps[ci], f).dot(n)
        return s / len(at_)
    lv = []
    for g in G:
        best = None; bd = math.inf; zn = zoneAt(g['s'])
        for l in lv:
            # loops lying on top of each other on a curve (within ¼ step) are one loop, whatever made them
            same = any(ci in l['at'] and abs(l['at'][ci] - f) * unit < 0.25 * hL for ci, f in g['at'].items())
            if not same and (g['lock'] in l['locks'] or not all(ci not in l['at'] or abs(l['at'][ci] - f) * unit < 2 * tol for ci, f in g['at'].items())):
                continue
            ds = abs(g['s'] - l['s']) * unit; dr = abs(g['rank'] - l['rank'])
            if (g['gid'] is not None or l['gid'] is not None) and g['gid'] != l['gid'] and not same:
                continue
            if same:
                d = 0
            elif g['gid'] is not None:
                d = ds
            elif zn:
                d = abs(hOf(g['at'], zn) - hOf(l['at'], zn))
                if d >= 0.5 * hL:
                    continue
            else:
                if ds >= hL or (dr >= 0.5 and ds >= 0.5 * hL):     # same rank within a step, or (as before) within half a step
                    continue
                d = dr * hL + ds
            if d < bd:
                bd = d; best = l
        if best:
            for ci, f in g['at'].items():
                if ci not in best['at']:
                    best['at'][ci] = f
            best['locks'][g['lock']] = True; best['keys'][g['key']] = True; best['s'] = avg(best['at'])
            best['rank'] = (best['rank'] * best['n'] + g['rank']) / (best['n'] + 1); best['n'] += 1
        else:
            lv.append({'s': g['s'], 'rank': g['rank'], 'n': 1, 'gid': g['gid'], 'at': dict(g['at']), 'locks': {g['lock']: True}, 'keys': {g['key']: True}})
    lv.sort(key=lambda l: l['s'])
    rk = []
    for r in rows:
        s = set()
        if r.pts is not None:
            for q in r.pts:
                k = ctx.TRUNK_KEY.get(q)
                if k is not None:
                    s.add(k)
        rk.append(s)

    # is the level on a welded trunk: one of its curves shares an anchor segment with a curve of the lock that made it
    def welds(l):
        for li in l['locks']:
            D = at(ctx.TRUNK_LOCKS, int(li) - 1) if ctx.TRUNK_LOCKS else None
            if not D:
                return True                                         # (keys count locks from 1) unknown — keep it
            ds = segsOf(D)
            for ci in l['at']:
                c = L.cs[ci]
                if any(_sk(c.anchors[k - 1], c.anchors[k]) in ds for k in range(1, len(c.anchors))):
                    return True
        return False
    tr = ctx.DBG['trace']
    if tr is not None and any(c.id in tr for c in L.cs):
        ctx.DBG['traceOut']['levels'] = {'rows': [float(to_fixed(r.f, 2)) for r in rows],
                                         'lv': [{'s': float(to_fixed(l['s'], 2)), 'locks': ','.join(l['locks']), 'at': ' '.join(f'{ci}:{to_fixed(atF(L.Ps[ci], f).y, 1)}' for ci, f in l['at'].items())} for l in lv]}
    ks = list(range(1, R - 1))
    n = len(ks); m = len(lv); lo = lv[0]['s'] - 0.5 * hL / unit; hi = lv[m - 1]['s'] + 0.5 * hL / unit
    skipRow = lambda i: hL if lo < rows[ks[i]].f < hi else 0

    # (a Y junction loop takes a neighbour's loop only lying on the junction — a loop elsewhere would pull it off)
    def pair(i, j):
        d = abs(rows[ks[i]].f - lv[j]['s']) * unit
        if d > 2 * hL or (rows[ks[i]].junc and d > 0.15 * hL):
            return math.inf
        rg = rows[ks[i]].gid if rows[ks[i]].guide else None; lg = nullish(lv[j]['gid'], None)
        if rg is not None and rg == lg:
            return 0
        for k in lv[j]['keys']:
            if k in rk[ks[i]]:
                return 0.5 * d
        return d
    dp = [[math.inf] * (m + 1) for _ in range(n + 1)]; how = [[0] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            v = dp[i][j]
            if v == math.inf:
                continue
            if i < n and v + skipRow(i) < dp[i + 1][j]:
                dp[i + 1][j] = v + skipRow(i); how[i + 1][j] = 1
            if j < m and v + hL < dp[i][j + 1]:
                dp[i][j + 1] = v + hL; how[i][j + 1] = 2
            if i < n and j < m:
                c = v + pair(i, j)
                if c < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = c; how[i + 1][j + 1] = 3
    extra = {}                                                      # row index a -> levels to add right after it
    i = n; j = m
    while i > 0 or j > 0:
        h = how[i][j]
        if h == 3:
            rows[ks[i - 1]].tm = lv[j - 1]['at']; i -= 1; j -= 1
        elif h == 1:
            i -= 1
        else:
            # a level no row took gets a row. On a WELDED trunk always; where the curves only lie on another lock's
            # curves (copied, not welded) — not when it would crowd a row next to it (a third of a step)
            a = ks[i - 1] if i else 0; b = ks[i] if i < n else R - 1; l = lv[j - 1]
            tight = (l['s'] - rows[a].f) * unit < hL / 3 or (rows[b].f - l['s']) * unit < hL / 3
            # (next to a Y junction loop only a welded trunk's loop — a copied curve's loop there would squeeze it)
            nearJ = any(r.junc and abs(r.f - l['s']) * unit < 0.35 * hL for r in rows)
            if (not tight and not nearJ) or welds(l):
                extra.setdefault(a, []).insert(0, l)
            j -= 1

    # A loop held on some curves by a neighbour's trunk points is one loop: on the other curves it is cut by a plane
    # through those points (the row's own direction, tilted to hold them all)
    def trunkPlane(n0, at_):
        pts = [atF(L.Ps[ci], f) for ci, f in at_.items()]
        c = V3()
        for p in pts:
            c.add(p)
        c.divideScalar(len(pts))
        n = n0.clone(); u = None; ud = 1e-6
        for a in pts:
            for b in pts:
                d = a.distanceTo(b)
                if d > ud:
                    ud = d; u = b.clone().sub(a)
        if u:
            u.normalize(); mm = n.clone().addScaledVector(u, -n.dot(u))
            if mm.lengthSq() > 1e-8:
                n = mm
        return {'p': c, 'n': n.normalize()}
    ownN = lambda r: (r.plane or rowPlane(ctx.CP, L.sp, r.f) or {'n': V3(0, 1, 0)})['n']
    for r in rows:
        if r.tm is not None and len(r.tm):
            r.tplane = trunkPlane(ownN(r), r.tm)
    if not extra:
        return rows
    out = []
    for a, r in enumerate(rows):
        out.append(r)
        add = extra.get(a)
        if not add or a >= R - 1:
            continue
        b = rows[a + 1]; prev = r.f
        for q, l in enumerate(add):
            f = clamp(l['s'], prev + 0.02, b.f - 0.02 * (len(add) - q)); prev = f
            t = (f - r.f) / js_or(b.f - r.f, 1); na = r.plane['n'] if r.plane else None; nb = b.plane['n'] if b.plane else None
            nn = na.clone().lerp(nb, t) if na and nb else (na or nb or V3(0, 1, 0)).clone()
            plane = {'p': atF(L.sp, f), 'n': nn.normalize() if nn.lengthSq() > 1e-12 else V3(0, 1, 0)}
            _br('trunkLevels.addedRow')
            out.append(Row(f=f, plane=plane, tplane=trunkPlane(plane['n'], l['at']), tm=l['at'], exact=True,
                           tgt=min(js_or(r.tgt, hL), js_or(b.tgt, hL)), prio=min(js_or(r.prio, 0), js_or(b.prio, 0)), added=True))
    # The added loop also crosses the curves of the other side. Where it lands at the end of their trunk, the Y junction
    # on that curve moves by a loop: the nearest pinned loops there are let go and spread evenly (curveRowF `free`)
    pin = lambda r, ci: (r.tm is not None and ci in r.tm) or (r.fixed is not None and ci in r.fixed) or (r.jfix is not None and ci in r.jfix)
    for ci in range(len(L.Ps)):
        for k, x in enumerate(out):
            if not x.added or ci in x.tm:
                continue
            after = [q for q in range(k + 1, len(out) - 1) if out[q].tm is not None and ci in out[q].tm]
            last = -1
            for q in range(k - 1, 0, -1):
                if out[q].tm is not None and ci in out[q].tm:
                    last = q; break
            if last < 0 or len(after) > 1:
                continue
            go = [k, *after]
            if not after:
                go.append(last)
            q = k + 1; c = 0
            while q < len(out) - 1 and c < 2 and not pin(out[q], ci):
                go.append(q); q += 1; c += 1
            for q in go:                                            # curveRowF: `free` — no plane cut, spread instead
                r = out[q]
                if r.jfix is not None and ci in r.jfix:
                    continue                                        # (a Y junction loop stays on its junction)
                if r.tm is not None and ci in r.tm:
                    t = dict(r.tm); del t[ci]; r.tm = t
                if r.free is None:
                    r.free = {}
                r.free[ci] = True
    return out


# Strictly one to one on a welded trunk: between the first and the last loop a curve holds from the lock built before
# (r.tm), no loop of this lock's own may cross that curve. Only between two neighbouring trunk loops of a curve whose
# stretch really lies on a welded lock's curve.
def trunkOnly(ctx, L, rows, welded):
    # port of trunkOnly (hair-modeler.clean.html:1953)
    R = len(rows); tol = 0.1 * levelH(ctx)                          # (welded curves part by up to ~0.06 step mid-segment)
    wb = [(D.Ps, D.Pidx if D.Pidx is not None else [None] * len(D.Ps), [polyBox(Q, tol) for Q in D.Ps],
           [chunkBoxes(qi, tol) if qi is not None else None for qi in (D.Pidx if D.Pidx is not None else [None] * len(D.Ps))]) for D in welded]   # (speed: padded boxes, stretch indexes and their padded boxes)
    onWeld = lambda p: any(any(inBox(p, bb) and (ch is None or nearBoxes(p, ch)) and atF(Q, nearestF(Q, p, qi)).distanceTo(p) < tol for Q, qi, bb, ch in zip(Qs, Qi, bbs, chs)) for Qs, Qi, bbs, chs in wb)
    spans = {}                                                      # curve index -> [[a, b] spine f] trunk stretches
    for ci, P in enumerate(L.Ps):
        at_ = sorted(({'s': r.f, 'f': r.tm[ci]} for r in rows if r.tm is not None and ci in r.tm), key=lambda x: x['s'])
        # a trunk running from the lock's root (or to its tip): the stretch before the first (after the last) trunk loop counts too
        if at_:
            N = len(P); a0 = at_[0]; a1 = at_[len(at_) - 1]
            if a0['f'] > 1 and onWeld(atF(P, a0['f'] / 2)):
                at_.insert(0, {'s': rows[0].f, 'f': 0})
            if a1['f'] < N - 2 and onWeld(atF(P, (a1['f'] + N - 1) / 2)):
                at_.append({'s': rows[R - 1].f, 'f': N - 1})
        for k in range(1, len(at_)):
            if not onWeld(atF(P, (at_[k - 1]['f'] + at_[k]['f']) / 2)):
                continue
            spans.setdefault(ci, []).append([at_[k - 1]['s'], at_[k]['s']])
    L.trunkSpans = spans if spans else None                         # enforceMaxGap puts no loop into them
    if not spans:
        return rows
    out = []
    for k, r in enumerate(rows):
        if k == 0 or k == R - 1 or (r.tm is not None and len(r.tm)):
            out.append(r); continue
        drop = False
        for ci, lst in spans.items():
            if not (r.fixed is not None and ci in r.fixed) and any(a < r.f < b for a, b in lst):
                drop = True; break
        if not drop:
            out.append(r)
        else:
            _br('trunkOnly.dropped')
    # A trunk loop of ANOTHER neighbour crossing this curve's trunk stretch: spread evenly between the held points
    held = lambda r, ci: (r.tm is not None and ci in r.tm) or (r.fixed is not None and ci in r.fixed) or (r.jfix is not None and ci in r.jfix)
    for k, r in enumerate(out):
        if k == 0 or k == len(out) - 1:
            continue
        for ci, lst in spans.items():
            if not held(r, ci) and any(a < r.f < b for a, b in lst):
                if r.free is None:
                    r.free = {}
                r.free[ci] = True
    tr = ctx.DBG['trace']
    if tr is not None and any(c.id in tr for c in L.cs):
        ctx.DBG['traceOut']['trunkOnly'] = [js_str(float(to_fixed(r.f, 2))) + ':' + (''.join(str(k) for k in r.tm) if r.tm is not None else '-') + '/' + (''.join(str(ci) for ci in r.free) if r.free is not None else '-') for r in out]
    return out


# trunkOwnFs: where every curve would be cut with the trunk loops cut by the lock's own plane (None — no trunk loops)
def trunkOwnFs(ctx, L, rows):
    # port of trunkOwnFs (hair-modeler.clean.html:1997)
    tr = [r for k, r in enumerate(rows) if r.tm is not None and len(r.tm) and 0 < k < len(rows) - 1]
    if not tr:
        return None
    ex = [{'exact': r.exact, 'exactC': r.exactC, 'gT': r.gT} for r in rows]   # (curveRowF marks these — the real pass does)
    saved = [{'r': r, 'tm': r.tm, 'tplane': r.tplane, 'free': r.free, 'plane0': r.plane0} for r in tr]
    # the lock's own plane — in a guide zone leaning to the nearest guide loop's plane by the priority there
    gr = [r for r in rows if r.guide and r.plane]

    def ownPlane(r):
        o = rowPlane(ctx.CP, L.sp, r.f)
        if not o or not gr or not (r.prio is not None and r.prio > 0):
            return o
        g = gr[0]
        for x in gr:
            if abs(x.f - r.f) < abs(g.f - r.f):
                g = x
        gn = g.plane['n'].clone().negate() if g.plane['n'].dot(o['n']) < 0 else g.plane['n']
        return {'p': o['p'], 'n': o['n'].clone().lerp(gn, clamp(r.prio, 0, 1)).normalize()}
    for r in tr:
        r.tm = None; r.tplane = None; r.free = None; r.plane0 = ownPlane(r) or r.plane0
    Fo = [curveRowF(ctx, P, rows, L.sp, ci, L.plen[ci] if L.plen is not None else None, L.spLen) for ci, P in enumerate(L.Ps)]
    for s in saved:
        s['r'].tm = s['tm']; s['r'].tplane = s['tplane']; s['r'].free = s['free']; s['r'].plane0 = s['plane0']
    for k, r in enumerate(rows):
        r.exact = ex[k]['exact']; r.exactC = ex[k]['exactC']; r.gT = ex[k]['gT']
    return Fo


def trunkFade(ctx, L, rows, Fs, Fo):
    # port of trunkFade (hair-modeler.clean.html:2016): the trunk pulls the free curves by distance (within 2.5 steps)
    unit = [(polyLen(P) if L.plen is None else L.plen[ci]) / (len(P) - 1) for ci, P in enumerate(L.Ps)]; Dm = 2.5 * levelH(ctx); R = len(rows)
    for k in range(1, R - 1):
        r = rows[k]
        if r.tm is None or not len(r.tm):
            continue
        held = lambda ci: ci in r.tm or (r.fixed is not None and ci in r.fixed) or (r.jfix is not None and ci in r.jfix) or (r.free is not None and ci in r.free)
        offs = [{'p': atF(L.Ps[j], Fo[j][k]), 'o': (Fs[j][k] - Fo[j][k]) * unit[j]} for j in r.tm]
        for ci, P in enumerate(L.Ps):
            if held(ci):
                continue
            q = atF(P, Fo[ci][k]); sw = 0; so = 0; mx = 0
            for o in offs:
                t = clamp(1 - q.distanceTo(o['p']) / Dm, 0, 1); w = t * t * (3 - 2 * t); sw += w; so += w * o['o']; mx = js_max(mx, w)
            Fs[ci][k] = clamp(Fo[ci][k] + ((so / sw) * mx / unit[ci] if sw > 0 else 0), 0, len(P) - 1)
    for F in Fs:
        for k in range(1, len(F) - 1):
            F[k] = js_max(F[k], F[k - 1] + 0.05)
        for k in range(len(F) - 2, 0, -1):
            F[k] = js_min(F[k], F[k + 1] - 0.05)


# Where each loop lands on one edge curve: the loop is the cut of a plane through the spine point. Where the curve
# runs almost inside the plane, it follows the spine instead, so loops never fold.
def curveRowF(ctx, P, rows, sp, ci, plenP=None, spLen=None):
    # port of curveRowF (hair-modeler.clean.html:2038); plenP / spLen: polyLen of P and sp, when already known
    N = len(P); win = N * 0.15; R = len(rows)
    match = {}                                                      # loops on a welded trunk (trunkLevels): row -> f here
    for k, r in enumerate(rows):
        if r.tm is not None and ci in r.tm:
            match[k] = r.tm[ci]

    def cut(r):
        pl = r.tplane or r.plane0 or r.plane or rowPlane(ctx.CP, sp, r.f)   # (tplane: through a neighbour's trunk points)
        if not pl:
            return None
        s0 = pl['p']; n = pl['n']; sx = s0.x; sy = s0.y; sz = s0.z; nx = n.x; ny = n.y; nz = n.z
        best = None; bd = math.inf
        i0 = max(0, math.floor(r.f - win)); i1 = min(N - 2, math.ceil(r.f + win))
        # (P[i].clone().sub(s0).dot(n) on scalars; a segment's d1 is the next segment's d0, the same expression)
        a = P[i0]; d1 = (a.x - sx) * nx + (a.y - sy) * ny + (a.z - sz) * nz
        for i in range(i0, i1 + 1):
            d0 = d1; b = P[i + 1]; d1 = (b.x - sx) * nx + (b.y - sy) * ny + (b.z - sz) * nz
            if d0 == d1 or d0 * d1 > 0:
                continue
            lim = 0.35 * P[i].distanceTo(b)
            if dec_near('curveRowF.cutSteep', abs(d1 - d0), lim) < lim:
                continue
            f = i + d0 / (d0 - d1); d = abs(f - r.f)
            if d < bd:
                bd = d; best = f
        return best

    def snap(r, k):
        if r.fixed is not None and ci in r.fixed:
            return r.fixed[ci]                                      # a guide line crosses this curve here
        if r.jfix is not None and ci in r.jfix:
            return r.jfix[ci]                                       # this curve's Y junction
        if k in match:                                              # welded trunk -> exact
            r.exact = True
            if r.exactC is None:
                r.exactC = {}
            r.exactC[ci] = True
            return match[k]
        if r.free is not None and ci in r.free:
            return None                                             # spread between its neighbours (trunkLevels)
        return cut(r)
    delta = []
    for k, r in enumerate(rows):
        s = snap(r, k); delta.append(None if s is None else s - r.f)
    delta[0] = 0; delta[R - 1] = 0
    # A cut out of order (a neighbour's plane leaning across this lock) is let go and filled in from the loops around it
    held = lambda k: k == 0 or k == R - 1 or (rows[k].fixed is not None and ci in rows[k].fixed) or k in match or (rows[k].jfix is not None and ci in rows[k].jfix)
    uc = (polyLen(P) if plenP is None else plenP) / (N - 1); us = js_or(polyLen(sp) if spLen is None else spLen, 1e-9) / (len(sp) - 1)
    at_ = lambda k: rows[k].f + delta[k]
    st = [0]
    crowds = lambda k, j: at_(k) <= at_(j) + 0.05 or ((rows[k].aligned or rows[j].aligned) and (at_(k) - at_(j)) * uc < 0.3 * (rows[k].f - rows[j].f) * us)
    for k in range(1, R):
        if delta[k] is None:
            continue
        while len(st) > 1 and crowds(k, st[len(st) - 1]) and held(k) and not held(st[len(st) - 1]):
            delta[st.pop()] = None
        if crowds(k, st[len(st) - 1]) and not held(k):
            delta[k] = None; continue
        st.append(k)
    pk = 0
    for k in range(1, R):
        if delta[k] is not None:
            for q in range(pk + 1, k):
                delta[q] = delta[pk] + (delta[k] - delta[pk]) * (rows[q].f - rows[pk].f) / js_or(rows[k].f - rows[pk].f, 1)
            pk = k
    # loops let go at a moved Y junction (trunkLevels `free`): evenly spaced between the loops around them
    k = 1
    while k < R - 1:
        if rows[k].free is not None and ci in rows[k].free:
            e = k
            while e + 1 < R - 1 and rows[e + 1].free is not None and ci in rows[e + 1].free:
                e += 1
            a = k - 1; b = e + 1; Fa = rows[a].f + delta[a]; Fb = rows[b].f + delta[b]
            for q in range(k, e + 1):
                delta[q] = Fa + (Fb - Fa) * (q - a) / (b - a) - rows[q].f
            k = e
        k += 1
    # Hand-over between a neighbour's loops and this lock's own ones: the shared loop's slant fades out linearly over the
    # own loops up to the next fixed loop
    ex = []
    for k, r in enumerate(rows):
        if not (r.aligned or k in match) or k == 0 or k == R - 1 or delta[k] is None:
            ex.append(0); continue
        o = cut(Row(f=r.f, plane=rowPlane(ctx.CP, sp, r.f)))
        ex.append(0 if o is None else r.f + delta[k] - o)
    if any(e != 0 for e in ex):
        anchor = lambda k: k == 0 or k == R - 1 or rows[k].aligned or rows[k].guide or rows[k].junc or k in match or (rows[k].fixed is not None and ci in rows[k].fixed)
        a = 0
        for k in range(1, R):
            if not anchor(k):
                continue
            if (ex[a] or ex[k]) and k - a > 1:
                for q in range(a + 1, k):
                    if delta[q] is None:
                        continue
                    delta[q] += ex[a] + (ex[k] - ex[a]) * (rows[q].f - rows[a].f) / js_or(rows[k].f - rows[a].f, 1)
            a = k
    # blend near guides: the loop becomes a copy of the guide loop shifted along the lock, weighted by closeness
    raw = list(delta)
    for k, r in enumerate(rows):
        if r.blend is None or k in match or r.tplane or (r.free is not None and ci in r.free):
            continue
        for b in r.blend:
            gk = index_of(rows, b['g'])
            if 0 < gk < R - 1:
                delta[k] = delta[k] * (1 - b['w']) + raw[gk] * b['w']
    # the guide loop's shape for every loop in a guide zone (guidePriority pulls the finished loops towards it)
    gIdx = [k for k, r in enumerate(rows) if r.guide and 0 < k < R - 1 and raw[k] is not None]
    if gIdx:
        for k, r in enumerate(rows):
            if not r.prio or k == 0 or k == R - 1:
                continue
            gk = gIdx[0]
            for q in gIdx:
                if abs(rows[q].f - r.f) < abs(rows[gk].f - r.f):
                    gk = q
            gn = rows[gk].plane['n'] if rows[gk].plane else None; f0 = r.f + nullish(delta[k], raw[gk])
            t = None
            if gn:
                s0 = r.tplane['p'] if r.tplane else atF(sp, r.f); bd = math.inf; sn = 1
                # (P[i].clone().sub(s0).dot(gn) on scalars, as in cut(): the same operations in the same order, a
                # segment's d1 being the next one's d0 — the same expression; a disc over the hair puts every loop of
                # the lock in its zone, and this scan ran ~50 segments x 2 clones for each of them: lot 4.2c)
                sx = s0.x; sy = s0.y; sz = s0.z; nx = gn.x; ny = gn.y; nz = gn.z
                i0 = max(0, math.floor(f0 - win)); i1 = min(N - 2, math.ceil(f0 + win))
                if i0 <= i1:
                    a = P[i0]; d1 = (a.x - sx) * nx + (a.y - sy) * ny + (a.z - sz) * nz
                for i in range(i0, i1 + 1):
                    d0 = d1; b = P[i + 1]; d1 = (b.x - sx) * nx + (b.y - sy) * ny + (b.z - sz) * nz
                    if d0 == d1 or d0 * d1 > 0:
                        continue
                    f = i + d0 / (d0 - d1)
                    if abs(f - f0) < bd:
                        bd = abs(f - f0); t = f; sn = abs(d1 - d0) / js_or(P[i].distanceTo(b), 1)
                # where this curve runs almost along the guide's plane the guide pulls that point less, by the angle
                t2 = clamp((sn - 0.17) / (0.5 - 0.17), 0, 1)
                if r.gW is None:
                    r.gW = {}
                r.gW[ci] = t2 * t2 * (3 - 2 * t2)
            if r.gT is None:
                r.gT = {}
            r.gT[ci] = clamp(nullish(t, r.f + raw[gk]), 0, N - 1)
    F = [clamp(r.f + delta[k], 0, N - 1) for k, r in enumerate(rows)]
    F[0] = 0; F[R - 1] = N - 1
    # guide hits are fixed: a loop that comes close to (or past) one slides towards its evenly spread spot between them
    pinned = [0] + [k for k, r in enumerate(rows) if ((r.fixed is not None and ci in r.fixed) or k in match or (r.jfix is not None and ci in r.jfix)) and 0 < k < R - 1] + [R - 1]
    for q in range(len(pinned) - 1):
        a = pinned[q]; b = pinned[q + 1]
        for k in range(a + 1, b):
            E = F[a] + (F[b] - F[a]) * (rows[k].f - rows[a].f) / js_or(rows[b].f - rows[a].f, 1)
            mg = 0.5 * js_max(min(E - F[a], F[b] - E), 0.05)       # margin: half the even spacing
            t = clamp(1 - min(F[k] - F[a], F[b] - F[k]) / mg, 0, 1)
            F[k] += (E - F[k]) * t * t * (3 - 2 * t)
    # Relax loop spacing: each loop slides along this curve towards the spot that splits its two neighbours in the
    # same ratio as on the spine. Guide and shared (aligned) loops don't move.
    iters = js_round(js_or(ctx.CP.get('relax'), 0) * 30)
    if iters:
        _br('curveRowF.relax')
        pin = set(pinned)
        for k, r in enumerate(rows):
            if r.aligned:
                pin.add(k)
        sg = lambda k: js_max(rows[k].f - rows[k - 1].f, 1e-6)
        F0 = list(F)
        for _ in range(iters):
            for k in range(1, R - 1):
                if k in pin:
                    continue
                t = F[k - 1] + (F[k + 1] - F[k - 1]) * sg(k) / (sg(k) + sg(k + 1))
                F[k] += 0.5 * (t - F[k])
        for k, r in enumerate(rows):
            if r.prio:
                F[k] += (F0[k] - F[k]) * r.prio                     # guide priority keeps the guide's placement
    for k in range(1, R - 1):
        F[k] = js_max(F[k], F[k - 1] + 0.05)
    for k in range(R - 2, 0, -1):
        F[k] = js_min(F[k], F[k + 1] - 0.05)
    return F
