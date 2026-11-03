# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Walls and locks between curves, the data part of the original's curve pie and surface panel (phase 3.5):
creating walls (createSurfaces / makeSurface, with resolveTrunks picking the facing branches of two trunks), the
volume lock (closed), filling a hole bounded by welded curves (fillHole), the average curve of two curves, the
width segments of a wall turned into curves (segmentsToCurves), and the surface panel's edits (segments, offset,
spread, bulge, flip, clean topology, the second surface). Pure Python; selection, undo and toasts are the caller's.

Line numbers refer to reference/hair-modeler.clean.html, 1.0.1."""
import math

from .curves import markAnchorsDirty, rebuildCurve
from .edit import addItem, deleteItem, makeCurve, newAnchor
from .geom import resample
from .js import at, clamp, index_of, js_max, to_int32, truthy
from .surfaces import hasTwin, surfaceData, twinWall


def hasSurface(P, c1, c2):
    # port of hasSurface (hair-modeler.clean.html:2963)
    return any((s.a == c1.id and s.b == c2.id) or (s.a == c2.id and s.b == c1.id) for s in P.surfaces())


def makeSurface(P, c1, c2):
    # port of makeSurface (hair-modeler.clean.html:2965): one wall between two curves (None when one exists already)
    if hasSurface(P, c1, c2):
        return None
    return addItem(P, 'surface', c1.parent, {'a': c1.id, 'b': c2.id, 'dirty': True, 'p': {'segs': 1, 'bulge': 0.15, 'flip': False}})


def sharedRun(c1, c2):
    # port of sharedRun (hair-modeler.clean.html:3139): the longest run of 2+ anchors the two curves share in the
    # same order: {i0, i1 (on c1), j0, j1 (on c2)} or None
    best = None
    A1 = c1.anchors; A2 = c2.anchors
    for i in range(len(A1)):
        j = index_of(A2, A1[i])
        if j < 0:
            continue
        n = 1
        while i + n < len(A1) and at(A2, j + n) is A1[i + n]:
            n += 1
        if n >= 2 and (best is None or n > best['i1'] - best['i0'] + 1):
            best = {'i0': i, 'i1': i + n - 1, 'j0': j, 'j1': j + n - 1}
    return best


def trunkMates(P, c):
    # port of trunkMates (hair-modeler.clean.html:2970): c and every curve sharing a trunk with it (directly or
    # through others); a click on a trunk picks any one of them
    out = [c]; cs = [h for h in P.curves() if not h.junc and not h.ring and not h.conn]
    i = 0
    while i < len(out):
        for h in cs:
            if h not in out and sharedRun(out[i], h):
                out.append(h)
        i += 1
    return out


def curveGap(a, b):
    # port of curveGap (hair-modeler.clean.html:2976): mean distance between two curves (each sample of one to the
    # nearest of the other, both ways). `** 0.5` of the original is Math.pow(x, 0.5) = sqrt, exact.
    pa = resample([s.p for s in a.samples], 40); pb = resample([s.p for s in b.samples], 40)

    def one(Pp, Q):
        s = 0
        for p in Pp:
            s += math.sqrt(min(q.distanceToSquared(p) for q in Q))
        return s / len(Pp)
    return (one(pa, pb) + one(pb, pa)) / 2


def resolveTrunks(P, lst):
    """port of resolveTrunks (hair-modeler.clean.html:2985). Two trunks selected (each curve has trunk mates, the
    two are not mates of each other): the wall goes between the branches facing each other, the closest pair, not
    between whichever curves the clicks picked. Several selected curves of one trunk count as that trunk. Returns
    the SAME list object when nothing is resolved (callers test identity, as the original tests `res !== list`)."""
    if len(lst) < 2:
        return lst
    for c in lst:
        if c.dirty:
            rebuildCurve(P, c)
    groups = []
    for c in lst:
        g = next((x for x in groups if c in x['mates']), None)
        if g is not None:
            g['sel'].append(c)
        else:
            groups.append({'mates': trunkMates(P, c), 'sel': [c]})
    if len(groups) != 2 or any(len(g['mates']) < 2 for g in groups):
        return lst                                                  # only "trunk <-> trunk"
    best = None
    for c in [*groups[0]['mates'], *groups[1]['mates']]:
        if c.dirty or len(c.samples) < 2:
            rebuildCurve(P, c)
    for a in groups[0]['mates']:
        for b in groups[1]['mates']:
            if len(a.samples) < 2 or len(b.samples) < 2:
                continue
            d = curveGap(a, b)
            if best is None or d < best['d']:
                best = {'d': d, 'a': a, 'b': b}
    return [best['a'], best['b']] if best else lst


# Fill a hole bounded by pieces of the selected curves (welded at the corners): the shortest closed loop along their
# segments that uses every selected curve is found and split at two corners into two paths of about equal length;
# each path becomes a junction curve (it copies the curves it runs along - c.junc) and a wall between the two fills
# the hole (it closes to a point at both corners). E.g. an X of two curves + a third curve across their ends -> the
# triangle between the crossing and the third curve.
NO_LOOP = 'No closed loop through these curves — weld them at the corners of the hole first'
FEW_CURVES = 'Select the curves around the hole (at least 2)'


def fillHole(P, sel):
    """port of fillHole (hair-modeler.clean.html:3028). Returns (surface, corners, None), or (None, 0, message)
    when there is no hole to fill."""
    cs = [c for c in sel if c is not None and c.type == 'curve' and len(c.anchors) >= 2]
    if len(cs) < 2:
        return None, 0, FEW_CURVES
    adj = {}                                                        # anchor -> [{to, c}], a Map keyed by the Anchor itself

    def add(a, b, c):
        adj.setdefault(a, []).append({'to': b, 'c': c})
    for c in cs:
        for k in range(len(c.anchors) - 1):
            add(c.anchors[k], c.anchors[k + 1], c); add(c.anchors[k + 1], c.anchors[k], c)

    def ln(a, b):
        return a.pos.distanceTo(b.pos)
    best = None; count = 0; steps = 0

    # every simple cycle (bounded search): anchors path + the curve of each step
    def dfs(start, path, via, L):
        nonlocal best, count, steps
        if count > 20000:
            return
        steps += 1
        if steps > 300000 or (best is not None and L >= best['L']):
            return
        v = path[len(path) - 1]
        for e in adj.get(v, []):
            to = e['to']; c = e['c']
            if len(path) > 1 and to is path[len(path) - 2] and c is via[len(via) - 1]:
                continue
            L2 = L + ln(v, to)
            if to is start and len(path) >= 3:
                count += 1
                cv = set(via); cv.add(c)
                if all(x in cv for x in cs) and (best is None or L2 < best['L']):
                    best = {'path': list(path), 'via': [*via, c], 'L': L2}
                continue
            if to in path:
                continue
            path.append(to); via.append(c); dfs(start, path, via, L2); path.pop(); via.pop()
    for s in dict.fromkeys(a for c in cs for a in c.anchors):
        dfs(s, [s], [], 0)
    if best is None:
        return None, 0, NO_LOOP
    Pth = best['path']; n = len(Pth); V = best['via']               # V[i]: the curve from Pth[i] to Pth[i+1]
    corners = [i for i in range(n) if V[i] is not V[(i + n - 1) % n]]
    if len(corners) < 2:
        corners = [0, n // 2]
    cum = [0]
    for i in range(n):
        cum.append(cum[i] + ln(Pth[i], Pth[(i + 1) % n]))
    split = None
    for u in corners:
        for w in corners:
            if w <= u:
                continue
            a = cum[w] - cum[u]; b = cum[n] - a; d = abs(a - b)
            if split is None or d < split['d']:
                split = {'u': u, 'w': w, 'd': d}
    X = []; Y = []
    for i in range(split['u'], split['w'] + 1):
        X.append(Pth[i])
    i = split['u']
    while True:
        Y.append(Pth[i])
        if i == split['w']:
            break
        i = (i + n - 1) % n
    parent = cs[0].parent

    def mk(lst):
        c = makeCurve(P, parent); c.anchors = lst; c.junc = True; c.name = 'Hole ' + c.name
        return c
    cx = mk(X); cy = mk(Y)
    s = addItem(P, 'surface', parent, {'a': cx.id, 'b': cy.id, 'dirty': True, 'p': {'segs': 1, 'bulge': 0, 'flip': False}})
    markAnchorsDirty(P, [*X, *Y])
    P.rev += 1
    return s, len(corners), None


ALREADY = 'Surfaces between these curves already exist'


def createSurfaces(P, lst, closed, exact=False):
    """port of createSurfaces (hair-modeler.clean.html:3076): walls between consecutive curves (in selection order);
    closed = also last -> first; without `exact`, two trunks resolve to their facing branches. Returns (made, msg):
    the new walls and the original's toast, if any (ALREADY when nothing was made)."""
    res = lst if exact else resolveTrunks(P, lst)
    msg = None
    if res is not lst:
        if hasSurface(P, res[0], res[1]) and len(lst) == 2:
            msg = 'The facing branches already have a wall — made it between the selected curves'   # fall back to the selection
        else:
            lst = res; closed = False; msg = f'Wall between the facing branches: {lst[0].name} ↔ {lst[1].name}'
    made = []
    for i in range(len(lst) - 1):
        s = makeSurface(P, lst[i], lst[i + 1])
        if s is not None:
            made.append(s)
    if closed and len(lst) >= 3:
        s = makeSurface(P, lst[len(lst) - 1], lst[0])
        if s is not None:
            made.append(s)
    if not made:
        return [], ALREADY
    P.rev += 1
    return made, msg


def createSurface(P, c1, c2):
    # port of createSurface (hair-modeler.clean.html:3089)
    return createSurfaces(P, [c1, c2], False)


# ---- Width segments -> curves: every inner column line of a wall (with its bulge) becomes a curve, and the wall is
# replaced by a chain of flat one-segment walls a -> c1 -> ... -> b (2 curves + a 3-segment trapezoid -> 4 curves,
# 3 walls). Column ends that meet a driver curve's end (a pointed tip, a shared root) are welded to that anchor. A
# second surface becomes a wall of its own. ----
NO_SEGS = 'Select a NURBS surface with width segments (Width segments ≥ 1)'


def segmentsToCurves(P, lst):
    """port of segmentsToCurves (hair-modeler.clean.html:3092). Returns (curves made, walls made, message or None).
    Builds the row tables of the whole project (the original's buildRowTables())."""
    from .rows import buildRowTables
    lst = [w for w in lst if w is not None and w.type == 'surface' and to_int32(w.p.get('segs')) >= 2]
    if not lst:
        return [], [], NO_SEGS
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)
    T = buildRowTables(P).T; madeC = []; madeS = []
    for w in lst:
        d = surfaceData(P, w, T); A = P.items.get(w.a); B = P.items.get(w.b)
        if d is None or A is None or B is None:
            continue
        segs = to_int32(w.p['segs']); Mw = segs + 1; R = len(d.pos) // Mw; K = max(len(A.anchors), len(B.anchors), 3)
        ends = [A.anchors[0], B.anchors[0], A.anchors[len(A.anchors) - 1], B.anchors[len(B.anchors) - 1]]; tol = 1e-4 * P.S.scale
        chain = [A]
        for j in range(1, segs):
            col = [d.pos[k * Mw + j] for k in range(R)]
            L = [0]
            for k in range(1, R):
                L.append(L[k - 1] + col[k].distanceTo(col[k - 1]))

            def pt(s):
                k = 0
                while k < R - 2 and L[k + 1] < s:
                    k += 1
                g = L[k + 1] - L[k]
                return col[k].clone().lerp(col[k + 1], clamp((s - L[k]) / g, 0, 1) if g > 0 else 0)
            anchors = []
            for q in range(K):
                p = pt(L[R - 1] * q / (K - 1))
                e = next((a for a in ends if a.pos.distanceTo(p) < tol), None) if (q == 0 or q == K - 1) else None
                anchors.append(e if e is not None else newAnchor(P, p))
            c = makeCurve(P, w.parent); c.anchors = anchors
            if truthy(A.fair) or truthy(B.fair):
                c.fair = max(A.fair or 0, B.fair or 0)
            madeC.append(c); chain.append(c)
        chain.append(B)
        for i in range(len(chain) - 1):
            p = {'segs': 1, 'bulge': 0}
            if 'flip' in w.p:                                       # an undefined flip is dropped by JSON.stringify
                p['flip'] = w.p['flip']
            if w.p.get('bfade') is not None:
                p['bfade'] = w.p['bfade']
            madeS.append(addItem(P, 'surface', w.parent, {'a': chain[i].id, 'b': chain[i + 1].id, 'dirty': True, 'p': p}))
        if hasTwin(w):
            t = twinWall(w)
            madeS.append(addItem(P, 'surface', w.parent, {'a': t.a, 'b': t.b, 'dirty': True, 'p': {k: v for k, v in t.p.items() if v is not None}}))
        deleteItem(P, w.id)
    markAnchorsDirty(P, [a for c in madeC for a in c.anchors])
    P.rev += 1
    return madeC, madeS, None


# A new curve halfway between two curves: both are sampled at the same arc-length fractions (as many anchors as the
# denser curve) and averaged. Ends the two curves share (e.g. a welded tip) stay shared.
def averageCurve(P, c1, c2):
    # port of averageCurve (hair-modeler.clean.html:6169); None when a curve has no samples yet
    if len(c1.samples) < 2 or len(c2.samples) < 2:
        return None
    n = max(len(c1.anchors), len(c2.anchors))
    P1 = resample([s.p for s in c1.samples], n)
    P2 = resample([s.p for s in c2.samples], n)
    a2 = list(c2.anchors)
    if P1[0].distanceTo(P2[0]) + P1[n - 1].distanceTo(P2[n - 1]) > P1[0].distanceTo(P2[n - 1]) + P1[n - 1].distanceTo(P2[0]):
        P2.reverse(); a2.reverse()
    c = makeCurve(P, c1.parent)
    c.name = 'Average curve'
    for i in range(n):
        c.anchors.append(newAnchor(P, P1[i].clone().add(P2[i]).multiplyScalar(0.5)))
    a1 = c1.anchors
    if a1[0] is a2[0]:
        c.anchors[0] = a1[0]
    if a1[len(a1) - 1] is a2[len(a2) - 1]:
        c.anchors[n - 1] = a1[len(a1) - 1]
    arr = P.childrenOf(c.parent); arr.remove(c.id); arr.insert(arr.index(c1.id) + 1, c.id)
    c.dirty = True
    P.rev += 1
    return c


# ---- the surface panel (8732-8822): what its controls do to the model ----
SEG_KEYS = ('segOff', 'segSpread')
WALL_KEYS = ('segs', 'segOff', 'segSpread', 'bulge', 'flip', 'bfade', 'jfade', 'b2segs', 'b2bulge', 'b2flip')


def setWallProp(P, sf, key, value):
    """A control of the surface panel (8745-8822): `it.p[key] = v; it.dirty = true` (upd). `segs` is the stored
    value (segments + 1: the slider shows segs - 1)."""
    sf.p[key] = value; sf.dirty = True
    P.rev += 1


def setSecondSurface(P, sf, on):
    """The "Second surface" checkbox (8798-8803): switched on for the first time, the twin gets its defaults (this
    wall's bulge and segments, the opposite side)."""
    sf.p['b2on'] = on
    if on and sf.p.get('b2bulge') is None:
        sf.p.update({'b2bulge': sf.p['bulge'], 'b2segs': js_max(1, to_int32(sf.p.get('segs'))), 'b2flip': not truthy(sf.p.get('flip'))})
    sf.dirty = True
    P.rev += 1


def setClean(P, surfaces, on):
    """The "Clean topology" checkbox (8788-8794): every selected surface (and the one shown); the loops of the whole
    scene are rebuilt (S.surfDirty), since a clean wall slants its neighbours. Returns the surfaces changed."""
    for x in surfaces:
        x.p['clean'] = on
    P.gen += 1; P.rev += 1                                          # a row-table parameter changed: the memos keyed on gen go
    return list(surfaces)


def wallCurves(P, sf):
    """the two driver curves of a wall, (a, b), either None when deleted"""
    ca = P.items.get(sf.a); cb = P.items.get(sf.b)
    return (ca if ca is not None and ca.type == 'curve' else None, cb if cb is not None and cb.type == 'curve' else None)


def wallsOfCurves(P, cs):
    """the walls driven by any of the curves (the Curve panel's "Drives:", 8730), in file order"""
    ids = {c.id for c in cs}
    return [s for s in P.surfaces() if s.a in ids or s.b in ids]


def surfaceMenuChoices(P, sc):
    """port of surfaceMenuItems (hair-modeler.clean.html:6157), as pure choices: [(key, label)] for the selected
    curves `sc` (2+). Keys: 'walls' (createSurfaces open; between two trunks it is the facing branches), 'exact'
    (exactly these curves, only when trunks resolve), 'closed' (3+), 'fill', 'average' (2), 'merge'."""
    tr = resolveTrunks(P, sc) is not sc                             # two trunks: one wall between the facing branches
    n = len(sc)
    out = [('walls', 'Wall between the two trunks (facing branches)' if tr else 'Create NURBS surface (wall)' if n == 2 else f'Walls between {n} curves (open)')]
    if tr:
        out.append(('exact', 'Wall between exactly these two curves' if n == 2 else f'Walls between exactly these {n} curves'))
    if n >= 3:
        out.append(('closed', f'Volume lock from {n} curves (closed)'))
    out.append(('fill', 'Fill the hole between these curves'))
    if n == 2:
        out.append(('average', 'Create average curve'))
    out.append(('merge', 'Merge anchors at identical places'))
    return out
