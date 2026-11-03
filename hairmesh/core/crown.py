# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Crown (whorl / parting), hair-modeler.clean.html:6724-7238: the conversion-time part only — crownData builds the
crown mesh between the lower connector and the parting line from the crown curves. The tools that create and edit
crowns (createCrown, connectors, symmetry) are phase 3."""
import math

from . import model as M
from .fdlibm import exp as _exp
from .geom import faceCenter, faceNormal, pointOnCurve, polyLen, resample, segPos
from .hairmesh_io import defaultStep
from .js import js_min, at, clamp, includes, index_of, js_max, js_or, js_round, js_sign, to_int32, nullish
from .raycast import Box3
from .rowbase import convOf
from .trace import br as _br, dec as _dec
from .vec3 import V3

WHORL_IN = 2                                                        # at most this many crown loops reach one parting vertex


def connT(h, a):
    # port of connT (hair-modeler.clean.html:6854): arc fraction of anchor a on connector curve h
    if a.att and a.att.c == h.id:
        return a.att.t
    i = index_of(h.anchors, a); L = h.arc
    if i < 0:
        return 0
    return L[min(i * 24, len(L) - 1)] / js_or(L[len(L) - 1], 1) if L and len(L) else i / max(len(h.anchors) - 1, 1)


def crownCurves(P, k):
    # port of crownCurves (hair-modeler.clean.html:6841): the curves running from the lower connector to the parting
    part = P.items.get(k.part); low = P.items.get(k.low); out = []
    if not part or not low:
        return out
    for c in P.curves():
        if c.ring or c.conn or len(c.anchors) < 2:
            continue
        # an end is on a connector when it is attached to it, or when it IS one of its anchors (shared)
        A = c.anchors; a0 = A[0]; a1 = A[len(A) - 1]
        on = lambda a, h: (a.att is not None and a.att.c == h.id) or includes(h.anchors, a)
        if on(a0, low) and on(a1, part):
            out.append({'c': c, 'u': connT(low, a0), 's': connT(part, a1), 'rev': False})
        elif on(a1, low) and on(a0, part):
            out.append({'c': c, 'u': connT(low, a1), 's': connT(part, a0), 'rev': True})
    return sorted(out, key=lambda x: x['u'])


def crownStep(P, k):
    # port of crownStep (6860)
    cv = convOf(P, k); return js_or(cv.get('step'), defaultStep(P))


def crownData(P, k):
    # port of crownData (hair-modeler.clean.html:6862); the original's comments explain each block
    part = P.items.get(k.part); low = P.items.get(k.low)
    if not part or not low or len(part.samples) < 2 or len(low.samples) < 2 or not part.arc or not low.arc:
        return None
    step = crownStep(P, k); NV = 33
    rad = []
    for r in crownCurves(P, k):
        if len(r['c'].samples) < 2:
            continue
        Pp = [s.p for s in r['c'].samples]
        rad.append({**r, 'P': resample(Pp[::-1] if r['rev'] else Pp, NV)})
    if not rad:
        return None
    # vertical loops: at both ends of the lower connector, at every crown curve and every lock curve attached to it;
    # longer gaps along the connector are filled at about the loop step
    Lw = low.arc[len(low.arc) - 1]; us = [0, 1, *(r['u'] for r in rad)]
    for c in P.curves():
        if not c.conn and not c.ring:                               # (exclZone is always null since 10.10)
            for a in (at(c.anchors, 0), at(c.anchors, len(c.anchors) - 1)):
                if a and ((a.att and a.att.c == low.id) or includes(low.anchors, a)):
                    us.append(connT(low, a))
    us.sort()
    U = []
    for u in us:
        if not U or (u - U[len(U) - 1]) * Lw > step * 0.1:
            U.append(u)
    # a lock wall whose two curves both start on the lower connector: the crown gets the same columns there

    def onLow(c):
        if not c:
            return None
        for a in (at(c.anchors, 0), at(c.anchors, len(c.anchors) - 1)):
            if a and ((a.att and a.att.c == low.id) or includes(low.anchors, a)):
                return connT(low, a)
        return None
    walls = []
    for s in P.surfaces():
        ua = onLow(P.items.get(s.a)); ub = onLow(P.items.get(s.b))
        if ua is not None and ub is not None and abs(ua - ub) > 1e-6:
            walls.append({'lo': js_min(ua, ub), 'hi': js_max(ua, ub), 'ua': ua, 'ub': ub, 'segs': js_max(1, to_int32(nullish(s.p.get('segs'), 0))), 'p': s.p})
    near = lambda x, y: abs(x - y) * Lw < step * 0.1
    corner = lambda u: any(near(u, w['lo']) or near(u, w['hi']) for w in walls)
    inWall = lambda u: not corner(u) and any(w['lo'] < u < w['hi'] for w in walls)
    cols = [w['ua'] + (w['ub'] - w['ua']) * segPos((j + 1) / w['segs'], w['p'], w['segs']) for w in walls for j in range(w['segs'] - 1)]
    U2 = []
    for u in sorted([u for u in U if not inWall(u)] + cols):
        if not U2 or not near(u, U2[len(U2) - 1]):
            U2.append(u)
    loopsU = []
    for i in range(len(U2)):
        loopsU.append(U2[i])
        if i == len(U2) - 1:
            continue
        mid = (U2[i] + U2[i + 1]) / 2
        if any(w['lo'] < mid < w['hi'] for w in walls):
            continue                                                # within a wall: its columns only
        n = js_round((U2[i + 1] - U2[i]) * Lw / step) - 1
        for k2 in range(1, n + 1):
            loopsU.append(U2[i] + (U2[i + 1] - U2[i]) * k2 / (n + 1))
    # the parting line: vertices at about the loop step; every loop ends on the nearest one (V-shaped order)
    Lp = part.arc[len(part.arc) - 1]; nP = js_max(1, js_round(Lp / step))

    def sAt(u):
        if u <= rad[0]['u']:
            return rad[0]['s']
        for i in range(len(rad) - 1):
            if u <= rad[i + 1]['u']:
                w = (u - rad[i]['u']) / js_max(rad[i + 1]['u'] - rad[i]['u'], 1e-9); return rad[i]['s'] + (rad[i + 1]['s'] - rad[i]['s']) * w
        return rad[len(rad) - 1]['s']
    idx = [js_round(clamp(sAt(u), 0, 1) * nP) for u in loopsU]
    mi = 0
    for i, v in enumerate(idx):
        if v < idx[mi]:
            mi = i
    for i in range(mi - 1, -1, -1):
        idx[i] = max(idx[i], idx[i + 1])
    for i in range(mi + 1, len(idx)):
        idx[i] = max(idx[i], idx[i - 1])
    PV = [pointOnCurve(part, j / nP) for j in range(nP + 1)]

    # shape of every loop: the neighbouring crown curves blended, ends pinned to the connectors
    def shape(u, end):
        i = 0
        while i < len(rad) - 2 and u > rad[i + 1]['u']:
            i += 1
        A = rad[i]; B = rad[min(i + 1, len(rad) - 1)]; w = 0 if B is A else clamp((u - A['u']) / js_max(B['u'] - A['u'], 1e-9), 0, 1)
        base = [p.clone().lerp(B['P'][k2], w) for k2, p in enumerate(A['P'])]; L0 = pointOnCurve(low, u)
        c0 = L0.clone().sub(base[0]); c1 = end.clone().sub(base[NV - 1])
        return [p.clone().addScaledVector(c0, 1 - k2 / (NV - 1)).addScaledVector(c1, k2 / (NV - 1)) for k2, p in enumerate(base)]
    paths = [shape(u, PV[idx[i]]) for i, u in enumerate(loopsU)]
    avgLen = sum(polyLen(Pp) for Pp in paths) / len(paths)
    NR = k.p['rings'] if js_or(k.p.get('rings'), 0) > 0 else js_max(2, js_round(avgLen / step))
    Rs = [resample(Pp, NR + 1) for Pp in paths]; n = len(paths)
    # The whorl: a run of neighbouring loops ending on one parting vertex is a fan (see the original's comments)
    e = [NR - 1] * n; side = [0] * n; seam = k.p.get('whorl') == 'seam'
    wr = rad[0]
    for r in rad:
        if r['s'] < wr['s']:
            wr = r
    onCurve = [any(near(r['u'], u) for r in rad) for u in loopsU]   # a loop on a crown curve always goes up to the parting

    def thin(a, b):
        alive = list(range(a, b + 1))
        free = lambda i: 0 < i < n - 1 and not onCurve[i]           # the connector's end loops and the crown curves' stay
        if seam and idx[a] == idx[mi]:
            _br('crownData.seam')
            c = a
            for i in alive:
                if abs(loopsU[i] - wr['u']) < abs(loopsU[c] - wr['u']):
                    c = i
            R = math.ceil((len(alive) - WHORL_IN) / 2)
            r = max(1, NR - 1 - R)
            while r <= NR - 2 and len(alive) > WHORL_IN:
                p = alive.index(c)
                kill = [i for i in (at(alive, p - 1), at(alive, p + 1)) if i is not None and free(i)][:len(alive) - WHORL_IN]
                if not kill:
                    break
                for i in kill:
                    e[i] = r; side[i] = 1 if i < c else -1
                alive = [i for i in alive if i not in kill]
                r += 1
            return
        _br('crownData.merge')
        r = 1
        while r <= NR - 2 and len(alive) > WHORL_IN:
            last = r == NR - 2; cand = []
            for q in range(1, len(alive) - 1):
                if free(alive[q]):
                    cand.append({'q': q, 'd': Rs[alive[q - 1]][r + 1].distanceTo(Rs[alive[q + 1]][r + 1])})
            cand.sort(key=lambda x: x['d'])
            kill = set(); taken = set()
            for x in cand:
                if len(alive) - len(kill) <= WHORL_IN:
                    break
                if not last and x['d'] > step * 1.2:
                    break                                           # not bunched up yet (the top band takes the rest)
                if x['q'] - 1 in taken or x['q'] + 1 in taken:
                    continue                                        # one ending loop between two going on
                taken.add(x['q']); kill.add(alive[x['q']])
            for i in kill:
                e[i] = r
            alive = [i for i in alive if i not in kill]
            r += 1
    i0 = 0
    while i0 < n:
        i1 = i0
        while i1 + 1 < n and idx[i1 + 1] == idx[i0]:
            i1 += 1
        if i1 - i0 + 1 > WHORL_IN:
            thin(i0, i1)
        i0 = i1 + 1
    pos = [p.clone() for p in PV]; vid = []
    for i, R in enumerate(Rs):
        row = []
        for r in range(e[i] + 1):
            row.append(len(pos)); pos.append(R[r].clone())
        vid.append(row)
    fork = {}                                                       # an ending loop -> the loop it branches off
    faces = []; V = lambda i, r: vid[i][r]
    for r in range(NR - 1):
        # the loops going on to ring r + 1, and between two of them those ending on ring r
        up = [i for i in range(n) if e[i] > r]
        for t in range(len(up) - 1):
            Lu = up[t]; Ru = up[t + 1]; J = [i for i in range(Lu + 1, Ru) if e[i] == r]
            if not J:
                faces.append([V(Lu, r), V(Ru, r), V(Ru, r + 1), V(Lu, r + 1)]); continue
            toR = (side[J[0]] > 0) if side[J[0]] else pos[V(J[0], r)].distanceTo(pos[V(Ru, r)]) < pos[V(Lu, r)].distanceTo(pos[V(J[0], r)])
            B = [Lu, *J, Ru]
            for j in J:
                fork[j] = Ru if toR else Lu
            if toR:
                faces.append([V(Lu, r), V(J[0], r), V(Ru, r + 1), V(Lu, r + 1)])
                for q in range(1, len(B) - 1):
                    faces.append([V(B[q], r), V(B[q + 1], r), V(Ru, r + 1)])
            else:
                faces.append([V(J[len(J) - 1], r), V(Ru, r), V(Ru, r + 1), V(Lu, r + 1)])
                for q in range(1, len(B) - 1):
                    faces.append([V(B[q - 1], r), V(B[q], r), V(Lu, r + 1)])
    top = [i for i in range(n) if e[i] == NR - 1]
    for t in range(len(top) - 1):
        i = top[t]; i2 = top[t + 1]
        a = vid[i][NR - 1]; b = vid[i2][NR - 1]; p0 = idx[i]; p1 = idx[i2]
        if p0 == p1:
            faces.append([a, b, p1]); continue
        seq = []; j = p1; sg = js_sign(p0 - p1)
        while True:
            seq.append(j)
            if j == p0:
                break
            j += sg
        faces.append([a, b, seq[0], seq[1]])
        for j in range(1, len(seq) - 1):
            faces.append([a, seq[j], seq[j + 1]])
    # Forks: a loop j ending on ring r and the loop X it branches off meet gradually, like a Y (a bell over ±4 rings)
    still = lambda i: onCurve[i] or i == 0 or i == n - 1
    bell = lambda t: 0.5 * _exp(-t * t / 4.5)
    for j, X in fork.items():
        r = e[j]; fx = not still(X)
        for t in range(-4, 5):
            q = r + 1 + t; w = bell(t)
            if q < 1:
                continue
            if fx and q <= e[X]:
                pos[V(X, q)].lerp(Rs[j][q], w)
            if t < 0:
                pos[V(j, q)].lerp(Rs[X][q], w if fx else js_min(0.9, 2 * w))
    # outward winding: away from a point well below the crown
    box = Box3().setFromPoints(pos); cen = box.getCenter(V3()); cen.y -= box.getSize(V3()).length()
    vote = 0
    for f in faces:
        vote += js_sign(faceNormal(pos, f).dot(faceCenter(pos, f).sub(cen)))
    if _dec('crownData.vote', vote, 0) < 0:
        for f in faces:
            f.reverse()
    k.info = {'loops': len(paths), 'rings': NR, 'part': nP + 1}
    k.dbg = {'vid': vid, 'e': e, 'onCurve': onCurve, 'loopsU': loopsU, 'idx': idx, 'paths': paths}   # for tests
    return M.MeshData(pos=pos, faces=faces)
