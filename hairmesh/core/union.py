# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The union of locks, Y / X junctions (4.10): port of the section "Union of locks" (hair-modeler.clean.html:
3438-3627, 1.0.2) — unionSelectedLocks (4540), the main pie's "Unite locks (Y / X junction)" (10156), with anchorFrac,
sharedRun (walls.py), unionLocks, facingWelded, facingGeometric, weldBand, closeJunction, recalcGroup (4579) and
gluedLocks (4590). (The section was 3129-3251 in 1.0.1: tools/refline.py.)

Pure Python, no bpy. Ids come from Project.uid and names from nextNumber, as in the original, so the parity scripts
(tests/test_union_parity.py) get the very same project file. The selection is ids (S.sel); the new one, the toast and
the undo step (commit) are the caller's.

The original's comment on the section (3439):
Locks (rings of walls) that run into each other. Where they overlap, their facing edge curves become one (shared
anchors — a common trunk) and the facing walls are removed. Where the trunk ends, the inner side of each lock is
closed by a new wall: one of its curves runs along one edge, crosses the lock along the junction line (from where one
edge leaves the trunk to where the other does) and continues along the other edge; the other curve is that edge.
Overlap from the root → Y, in the middle → X (two junctions), at the tip → λ. New walls keep the bulge of the removed
ones, fading to 0 towards the junction (p.jfade). Already welded edges (shared anchors) are used as they are; edges
that only pass through the other lock are first welded over the overlap (their average path)."""
import copy
import math

from .convert import lockGrid, lockGroups, lockQuery
from .curves import markAnchorsDirty, rebuildCurve
from .edit import addItem, deleteItem, makeCurve, newAnchor
from .geom import pointOnCurve
from .groups import serviceName
from .hairmesh_io import defaultStep
from .js import index_of, js_div, js_max, js_or, js_round
from .rows import buildRowTables, surfaceComponents
from .surfaces import surfaceData
from .walls import sharedRun

# the toasts of unionSelectedLocks (4540, 3246, 3249)
UNION_NONE_SEL = 'Select walls (or curves) of two locks that run into each other'
UNION_NO_OVERLAP = 'No overlapping locks found: their facing walls must share curves or run into each other'


def unionMessage(nY, nX, nWeld):
    # the toast of a union made (3249)
    parts = [x for x in (f'{nY} Y' if nY else None, f'{nX} X' if nX else None) if x]
    return f"Locks united: {', '.join(parts)} junction{'s' if nY + nX > 1 else ''}{' (edges welded over the overlap)' if nWeld else ''}"


def anchorFrac(c, i):
    # port of anchorFrac (hair-modeler.clean.html:4417): the arc fraction of anchor i along the anchors' polyline
    L = 0; d = 0
    for k in range(1, len(c.anchors)):
        s = c.anchors[k].pos.distanceTo(c.anchors[k - 1].pos); L += s
        if k <= i:
            d += s
    return d / L if L > 0 else 0


def unionLocks(P, sel_items):
    """port of unionLocks (hair-modeler.clean.html:4461): the locks touched by the selection (walls and curves):
    whole rings, without second surfaces. `sel_items` = S.sel (ids). The pie offers "Unite locks" when this gives two
    or more (10156)."""
    cs = set()
    for iid in sel_items:
        it = P.items.get(iid)
        if it is None:
            continue
        if it.type == 'surface':
            cs.add(it.a); cs.add(it.b)
        if it.type == 'curve':
            cs.add(iid)
    comps = [cp for cp in surfaceComponents(P) if any(i in cs for i in cp)]
    return lockGroups(P, [s for s in P.surfaces() if any(s.a in cp for cp in comps)])


def canUnite(P, sel_items):
    # the pie's condition for its "⊕ Unite locks (Y / X junction)" item (10156)
    return len(unionLocks(P, sel_items)) >= 2


def facingWeldedAll(P, A, B):
    """port of facingWeldedAll (hair-modeler.clean.html:4471, 1.0.2): facing walls already welded: both edges of wa
    share a run of anchors with the edges of wb. Every such pair, the deepest first: a wall can share runs with two
    walls of the other lock (a lock branching off at a slant shares the root with its outer wall too) — the one
    sharing more anchors is the facing one (Unite locks takes it); ties keep their order."""
    out = []

    def common(x, y):
        s = {id(a) for a in y.anchors}
        return len([a for a in x.anchors if id(a) in s])
    for wa in A:
        for wb in B:
            for x1, x2 in ((wb.a, wb.b), (wb.b, wb.a)):
                a1 = P.items.get(wa.a); a2 = P.items.get(wa.b); b1 = P.items.get(x1); b2 = P.items.get(x2)
                if a1 is a2 or b1 is b2:
                    continue
                r1 = sharedRun(a1, b1); r2 = sharedRun(a2, b2)
                if r1 and r2:
                    out.append({'wa': wa, 'wb': wb, 'a1': a1, 'a2': a2, 'b1': b1, 'b2': b2, 'r1': r1, 'r2': r2, 'score': common(a1, b1) + common(a2, b2)})
    return sorted(out, key=lambda f: -f['score'])                  # (.sort((x, y) => y.score - x.score): stable, as sorted)


def facingGeometric(P, A, B, T):
    """port of facingGeometric (hair-modeler.clean.html:4483): facing walls found by geometry: the walls reaching
    deepest into the other lock; per edge pair the stretch (arc fractions) where the edges run inside the other lock"""
    GA = lockGrid(P, A, T); GB = lockGrid(P, B, T)
    if GA is None or GB is None:
        return None

    def mid(w):
        ww = copy.copy(w); ww.p = {**w.p, 'segs': 2, 'segOff': 0, 'segSpread': 0}   # ({ ...w, p: { ...w.p, segs: 2, … } })
        d = surfaceData(P, ww, T)
        return [p for i, p in enumerate(d.pos) if i % 3 == 1] if d is not None else []

    def deepest(L, G):
        best = None; bf = 0.02
        for w in L:
            m = mid(w)
            if not m:
                continue
            f = len([p for p in m if lockQuery(G, p)['inside']]) / len(m)
            if f > bf:
                bf = f; best = w
        return best
    wa = deepest(A, GB); wb = deepest(B, GA)
    if wa is None or wb is None:
        return None

    def mp(iid):
        c = P.items[iid]; return c.samples[len(c.samples) >> 1].p
    straight = mp(wa.a).distanceTo(mp(wb.a)) + mp(wa.b).distanceTo(mp(wb.b)) <= mp(wa.a).distanceTo(mp(wb.b)) + mp(wa.b).distanceTo(mp(wb.a))
    x1, x2 = (wb.a, wb.b) if straight else (wb.b, wb.a)
    a1 = P.items.get(wa.a); a2 = P.items.get(wa.b); b1 = P.items.get(x1); b2 = P.items.get(x2)

    def span(c, G):
        t0 = None; t1 = None; L = c.arc[len(c.arc) - 1]
        for i, s in enumerate(c.samples):
            if lockQuery(G, s.p)['inside']:
                t = js_div(c.arc[i], L)
                if t0 is None:
                    t0 = t
                t1 = t
        return None if t0 is None else [t0, t1]

    def near(c, p):
        bi = 0; bd = math.inf
        for i, s in enumerate(c.samples):
            d = s.p.distanceToSquared(p)
            if d < bd:
                bd = d; bi = i
        return js_div(c.arc[bi], c.arc[len(c.arc) - 1])

    def band(a, b):
        sa = span(a, GB); sb = span(b, GA)
        if not sa and not sb:
            return None
        if not sa:
            sa = sorted(near(a, pointOnCurve(b, t)) for t in sb)    # .sort((x, y) => x - y)
        if not sb:
            sb = sorted(near(b, pointOnCurve(a, t)) for t in sa)
        return {'ta': sa, 'tb': sb}
    k1 = band(a1, b1); k2 = band(a2, b2)
    if k1 is None or k2 is None:
        return None
    return {'wa': wa, 'wb': wb, 'a1': a1, 'a2': a2, 'b1': b1, 'b2': b2, 'bands': [k1, k2]}


def weldBand(P, a, b, ta, tb):
    """port of weldBand (hair-modeler.clean.html:4505): make curves a and b one over the given stretches: new shared
    anchors along their average path replace both stretches. The curves are not resampled (dirty): a later pair of the
    same union reads their old samples, as the original does. Ours: the anchors no curve uses any more leave
    P.anchors (as weld.weldInto does; the file only holds the anchors curves use)."""
    la = a.arc[len(a.arc) - 1]; lb = b.arc[len(b.arc) - 1]
    ln = ((ta[1] - ta[0]) * la + (tb[1] - tb[0]) * lb) / 2; gap = (js_div(la, len(a.anchors) - 1) + js_div(lb, len(b.anchors) - 1)) / 2
    K = js_max(1, js_round(ln / js_max(gap, 1e-9)))
    common = []
    old = [*a.anchors, *b.anchors]
    for i in range(int(K) + 1 if K == K else 0):                    # (K NaN: `i <= K` is false at once)
        s = i / K
        p = pointOnCurve(a, ta[0] + (ta[1] - ta[0]) * s).add(pointOnCurve(b, tb[0] + (tb[1] - tb[0]) * s)).multiplyScalar(0.5)
        common.append(newAnchor(P, p))

    def swap(c, t):
        L = c.arc[len(c.arc) - 1]; m = js_div(0.35, len(c.anchors) - 1)

        def fr(k):
            return js_div(c.arc[min(k * 24, len(c.arc) - 1)], L)
        before = [x for k, x in enumerate(c.anchors) if fr(k) < t[0] - m]
        after = [x for k, x in enumerate(c.anchors) if fr(k) > t[1] + m]
        c.anchors = [*before, *common, *after]
        for iid in list(c.ovr):
            if not any(x.id == iid for x in c.anchors):
                del c.ovr[iid]
    swap(a, ta); swap(b, tb)
    used = {id(x) for c in P.curves() for x in c.anchors}
    for x in old:
        if id(x) not in used:
            P.anchors.pop(x.id, None)
    P.gen += 1                                                      # (the curves' anchors changed: memos key on gen)
    markAnchorsDirty(P, common); a.dirty = True; b.dirty = True


def closeJunction(P, w, p, q, p0, p1, q0, q1):
    """port of closeJunction (hair-modeler.clean.html:4521): replace the facing wall w (edges p, q; trunk on p =
    anchors p0..p1, on q = q0..q1) by the walls closing its inner side. Returns the new curves and walls, in order
    (curve, wall, curve, wall)."""
    def sub(c, i, j):
        return c.anchors[i:j + 1]

    def end(c):
        return len(c.anchors) - 1

    def dedupe(lst):
        return [a for k, a in enumerate(lst) if k == 0 or a is not lst[k - 1]]
    pieces = []
    if p1 < end(p) or q1 < end(q):                                  # past the trunk (Y: below the fork)
        pieces.append([[*sub(p, 0, p1), *sub(q, q1, end(q))], p] if anchorFrac(p, p1) <= anchorFrac(q, q1) else [[*sub(q, 0, q1), *sub(p, p1, end(p))], q])
    if p0 > 0 or q0 > 0:                                            # before the trunk (X: above where they meet)
        pieces.append([[*sub(q, 0, q0), *sub(p, p0, end(p))], p] if anchorFrac(q, q0) <= anchorFrac(p, p0) else [[*sub(p, 0, p0), *sub(q, q0, end(q))], q])
    made = []
    for lst, other in pieces:
        c = makeCurve(P, w.parent); c.anchors = dedupe(lst); c.junc = True; c.name = serviceName(P, 'JC')   # 1.0.2 (closeJunction, 3560)
        # { segs: w.p.segs, bulge: w.p.bulge, flip: w.p.flip, jfade: 0.3 }: a key the wall does not have is undefined
        # there, which the file does not write
        sp = {k: w.p[k] for k in ('segs', 'bulge', 'flip') if k in w.p}
        sp['jfade'] = 0.3
        s = addItem(P, 'surface', w.parent, {'a': c.id, 'b': other.id, 'dirty': True, 'p': sp})
        made += [c, s]
    deleteItem(P, w.id)
    return made


def unionSelectedLocks(P, sel_items, tables=None, allPairs=False):
    """port of unionSelectedLocks (hair-modeler.clean.html:4540). `sel_items` = S.sel (ids of walls or curves of the
    locks). Every pair of the locks touched (unionLocks) is tried in order: facing walls already welded (facingWelded)
    or found by geometry (facingGeometric, whose edges are first welded over the overlap: weldBand); a pair whose
    facing walls were used by an earlier pair is skipped; each facing wall is replaced by the walls closing its lock's
    inner side (closeJunction). A trunk that starts at the root counts as a Y, anything else as an X.

    Returns (made, toast, welded): `made` = the new junction curves and walls in the original's order ([] when
    nothing was united), the toast (UNION_NONE_SEL, UNION_NO_OVERLAP or unionMessage) and `welded` = how many pairs
    were welded by geometry. After a union (made not empty) every curve is dirty and the caller does what the original
    does (3247-3248): S.surfDirty (the row tables), regenConversion when a conversion is pending, S.asel = [],
    S.sel = the walls among `made`, onSelChange, commit. When nothing was united the selection stays and the original
    makes no undo step — but edges welded by geometry (welded > 0) stay welded: the model changed.

    `allPairs` (1.0.2): every pair of facing walls of two locks (a lock lying along two walls of the other has two),
    not only the deepest one; the Y / X count is the first pair's.

    Two guards of the original never fire, since closeJunction deletes both facing walls (A and B hold only live walls)
    and weldBand puts the same run of new anchors into both curves in the same order: a wall in `used`, and a weld that
    leaves no shared run. They are kept, as there.
    Ours (no bit changes): the row tables a pair's facingGeometric reads are built again only when the model changed
    since the last build (a weld or a junction made); a pair that found nothing changes nothing."""
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)
    locks = [[s for s in L if not s.twin] for L in unionLocks(P, sel_items)]
    if len(locks) < 2:
        return [], UNION_NONE_SEL, 0
    made = []; nY = 0; nX = 0; nWeld = 0; used = set()
    T = None                                                        # (ours: the tables of the last build while nothing changed)
    for i in range(len(locks)):
        for j in range(i + 1, len(locks)):
            A = [s for s in locks[i] if s.id in P.items]; B = [s for s in locks[j] if s.id in P.items]
            if not A or not B:
                continue
            fs = facingWeldedAll(P, A, B)
            if not fs:
                if T is None:
                    T = tables() if tables is not None else buildRowTables(P).T   # (`tables`, ours: the caller's row tables of the model as it is now — Blender's live ones, kept up to date incrementally: the same tables)
                g = facingGeometric(P, A, B, T)
                if g is None:
                    continue
                weldBand(P, g['a1'], g['b1'], g['bands'][0]['ta'], g['bands'][0]['tb']); weldBand(P, g['a2'], g['b2'], g['bands'][1]['ta'], g['bands'][1]['tb']); nWeld += 1
                T = None
                f = {**g, 'r1': sharedRun(g['a1'], g['b1']), 'r2': sharedRun(g['a2'], g['b2'])}
                if not f['r1'] or not f['r2']:
                    continue
                fs = [f]
            if not allPairs:
                fs = fs[:1]
            first = True
            for f in fs:
                if f['wa'].id in used or f['wb'].id in used or f['wa'].id not in P.items or f['wb'].id not in P.items:
                    continue
                used.add(f['wa'].id); used.add(f['wb'].id)
                wa = f['wa']; wb = f['wb']; a1 = f['a1']; a2 = f['a2']; b1 = f['b1']; b2 = f['b2']; r1 = f['r1']; r2 = f['r2']
                # wa = (a1, a2) in this order? closeJunction gets the edges as the wall has them
                xa = [a1, a2, r1['i0'], r1['i1'], r2['i0'], r2['i1']] if wa.a == a1.id else [a2, a1, r2['i0'], r2['i1'], r1['i0'], r1['i1']]
                xb = [b1, b2, r1['j0'], r1['j1'], r2['j0'], r2['j1']] if wb.a == b1.id else [b2, b1, r2['j0'], r2['j1'], r1['j0'], r1['j1']]
                if first:                                           # (only the first pair is counted)
                    if r1['i0'] > 0 or r2['i0'] > 0 or r1['j0'] > 0 or r2['j0'] > 0:
                        nX += 1
                    else:
                        nY += 1
                first = False
                made += closeJunction(P, wa, *xa)
                made += closeJunction(P, wb, *xb)
                T = None
    if not made:
        return [], UNION_NO_OVERLAP, nWeld
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    return made, unionMessage(nY, nX, nWeld), nWeld


def weldRejoins(P, cs, tol):
    """port of weldRejoins (hair-modeler.clean.html:4432, 1.0.2): a curve that leaves an anchor shared with another
    curve and comes back to that curve's very next anchor, staying close to it (an anchor put into one of them only —
    e.g. 37 -> 198 -> 42 along another curve's 37 -> 42), runs along the same stretch: its in-between anchors are put
    onto the other curve (the other curve's shape kept — insertKeepShape) and shared, so the common trunk runs on to
    where the curves really part. Within tol only. Returns how many stretches were welded.
    Ours: an anchor no curve uses any more leaves P.anchors (as weldBand does)."""
    from .curves import bez, curveSegs
    from .scissors import insertKeepShape
    n = 0
    for X in cs:
        for Y in cs:
            if X is Y:
                continue
            i = 0
            while i + 1 < len(X.anchors):
                p = X.anchors[i]; q = X.anchors[i + 1]; j = index_of(Y.anchors, p); k = index_of(Y.anchors, q)
                if j < 0 or k < 0 or abs(k - j) < 2:
                    i += 1
                    continue
                mids = Y.anchors[min(j, k) + 1:max(j, k)]
                if any(index_of(X.anchors, a) >= 0 for a in mids):
                    i += 1
                    continue
                seg = curveSegs(P, X)[i]
                hits = []
                for a in mids:
                    u = 0; d = math.inf
                    for s in range(97):
                        e = bez(seg[0], seg[1], seg[2], seg[3], s / 96).distanceTo(a.pos)
                        if e < d:
                            d = e; u = s / 96
                    hits.append({'a': a, 'u': u, 'd': d})
                if j > k:
                    hits.reverse()                                  # (in order along X)
                if any(h['d'] > tol or h['u'] < 0.02 or h['u'] > 0.98 or (m and h['u'] <= hits[m - 1]['u']) for m, h in enumerate(hits)):
                    i += 1
                    continue
                # the furthest first: what is left of segment i is its start up to the last cut, re-parameterized
                top = 1
                for h in reversed(hits):
                    a = insertKeepShape(P, X, i, h['u'] / top); top = h['u']
                    for c in P.curves():
                        x = index_of(c.anchors, h['a'])
                        if x >= 0 and c is not X:
                            c.anchors[x] = a; c.ovr.pop(h['a'].id, None); c.dirty = True
                    if not any(index_of(c.anchors, h['a']) >= 0 for c in P.curves()):
                        P.anchors.pop(h['a'].id, None)
                P.gen += 1                                          # (the curves' anchors changed: memos key on gen)
                X.dirty = True; Y.dirty = True; n += 1; i += len(hits)
                i += 1
    return n


def gluedLocks(P, g):
    # port of gluedLocks (hair-modeler.clean.html:4590, 1.0.2): how many locks of group g are glued to another lock of
    # g (their curves share anchors)
    from .convert import collect
    locks = lockGroups(P, [s for s in collect(P, g.id, set(), ['surface']) if not s.twin])
    if len(locks) < 2:
        return 0
    owner = {}
    for li, L in enumerate(locks):
        for s in L:
            for iid in (s.a, s.b):
                c = P.items.get(iid)
                if c is not None:
                    for a in c.anchors:
                        owner.setdefault(a, set()).add(li)
    glued = set()
    for s in owner.values():
        if len(s) > 1:
            glued |= s
    return len(glued)


def recalcGroup(P, g, tables=None):
    """port of recalcGroup (hair-modeler.clean.html:4579, 1.0.2): the group's glued locks recalculated for the
    conversion — curves wandering off a shared stretch and coming back are welded onto it (the trunk ends where the
    locks really part), then every pair of facing walls is closed from the fork (Unite locks): the wall between the
    trunks goes, the inner sides below the fork start at the fork row.
    Returns (made, message, changed, sel): `made` = what the union made (as unionSelectedLocks), the toast, whether the
    model changed (welds or a union) and the new S.sel (the new walls after a union, else the group's walls). The
    caller does what the original does when `changed` (commit, regenConversion)."""
    from .convert import collect
    walls = [s for s in collect(P, g.id, set(), ['surface']) if not s.twin]
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)
    cs = []
    for iid in dict.fromkeys(x for s in walls for x in (s.a, s.b)):
        c = P.items.get(iid)
        if c is not None and c.type == 'curve' and not c.ring and not c.junc:
            cs.append(c)
    welded = weldRejoins(P, cs, 0.75 * js_or(P.settings.step, defaultStep(P)))
    if welded:
        for c in P.curves():
            if c.dirty:
                rebuildCurve(P, c)
    made, msg, nweld = unionSelectedLocks(P, [s.id for s in walls], tables, allPairs=True)
    wall_ids = [s.id for s in walls]
    if made:
        return made, msg, True, [x.id for x in made if x.type == 'surface']   # (it commits — one undo step with the welds)
    if welded:
        for c in P.curves():
            c.dirty = True
        P.gen += 1; P.rev += 1
        return [], f"{welded} stretch{'es' if welded > 1 else ''} welded; no facing walls to replace", True, wall_ids
    return [], msg, bool(nweld), wall_ids
