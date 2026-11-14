# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""X junctions and edge curves (1.0.2): port of "X junctions: independent upper and lower zones"
(hair-modeler.clean.html:6776-6897) and "Walls round an X, the curves kept whole" (8001-8142).

  xSides, xAnchors, lockAround, flatStretch, wallsAcrossX   what an X is, where the X anchors of some curves are, the
                                        curves of the lock around one, a stretch of an edge curve in real curves
  separateAtX, separateGroupX           "Separate the zones at this X" (the anchor pie) / "Separate zones at X
                                        junctions (N)" (the group's panel): the lock's walls cut across at the X, the
                                        curves kept whole (the new walls run on edge curves along them)
  pathCurve, pairZones, pairEnvelope, xPairs, xPathWalls, xShared, xPartner
                                        the walls round an X (the curve pie's first item with two curves in an X), the
                                        zones of two curves joined in an X, the side of a pair facing a neighbour
  xZoneWalls                            dead code in the original (nothing calls it): ported with its test, no caller
  canSeparateAt, xCount                 the condition of the anchor pie's item (10122-10124) and the count of the
                                        group's button (11039)

pathAnchors / syncPathCurves (8006-8022) live in core/curves.py — the frame's part of the port calls them — and are
re-exported here. Pure Python, no bpy. Ids come from Project.uid and names from nextNumber / serviceName, as in the
original (makeCurve spends an id and a "Curve N" number before serviceName names the edge curve "EC<n>"), so the
parity scripts (tests/test_xjunc_parity.py) get the very same project file. The selection, the undo step (commit), the
toasts' display and regenConversion are the caller's: `regen` (a function or None) runs where the original calls
regenConversion (once per X inside separateGroupX, as there).

The original's comment on the X junctions (6776):
An X: an anchor that two or more curves of a lock pass through (not their ends) and that has two or more different
ways on BOTH sides — curves crossing there, or welded strands swapping partners (one pair shares the stretch above,
another pair the stretch below). A Y (one way on one side) is not an X. "Separate the zones" cuts the whole lock
across there (as the scissors do, walls kept on both sides): the curves through the X are split at it, the others where
the cross plane through it crosses them (the crossing nearest to it). The upper and the lower part are then two locks
that only touch at the cut anchors — their loops are built independently and both end on the cut row.

Line numbers: every one in this module is of 1.0.2 (reference/hair-modeler.clean.html)."""
import json
import math

from .curves import pathAnchors, rebuildCurve, syncPathCurves   # noqa: F401 (re-exported)
from .edit import curvesWith, deleteItem, makeCurve
from .groups import isDescendant, serviceName
from .hairmesh_io import defaultStep
from .js import clamp, index_of, js_max, js_or, js_sign, truthy
from .scissors import insertKeepShape
from .vec3 import V3
from .walls import createSurfaces, makeSurface

NO_X = 'This anchor is no X junction'                                # 6831
NO_WALLS = 'No walls made'                                          # 8103 (and 8131)
NO_GROUP_X = 'No X junctions with walls across them in this group'   # 6896


def separatedMessage(made, kept):
    # the toast of separateAtX (6885)
    return (f'Zones separated at the X: {made} walls above and below it — the curves stay whole'
            + (f' ({kept} wall{"s" if kept > 1 else ""} with a curve too far from it — left as they were)' if kept else ''))


def groupMessage(k):
    # the toast of separateGroupX (6896)
    return f'{k} X junction{"s" if k > 1 else ""}: the walls above and below are separate locks now — the curves stay whole' if k else NO_GROUP_X


def xWallsMessage(n):
    # the toast of xPathWalls (8103)
    return f'{n} walls round the X — the curves stay whole (the walls run on edge curves that follow them)' if n else NO_WALLS


# ======================= X junctions (6776-6897) =======================

def xSides(P, J, cs):
    # port of xSides (hair-modeler.clean.html:7760): anchor J is an X of the curves cs? {'thru': [(curve, index)], 'n':
    # the unit direction across} or None
    thru = [(d, i) for d, i in ((d, index_of(d.anchors, J)) for d in cs if not d.ring and not truthy(d.rib)) if i > 0 and i < len(d.anchors) - 1]
    if len(thru) < 2:
        return None
    A = thru[0][0].anchors; ref = A[thru[0][1] + 1].pos.clone().sub(A[thru[0][1] - 1].pos)
    before = set(); after = set(); dir_ = V3()
    for d, i in thru:                                               # each curve's neighbours, oriented the same way
        n1 = d.anchors[i - 1]; n2 = d.anchors[i + 1]
        if n2.pos.clone().sub(n1.pos).dot(ref) < 0:
            n1, n2 = n2, n1
        before.add(n1); after.add(n2); dir_.add(n2.pos.clone().sub(n1.pos).normalize())
    if len(before) >= 2 and len(after) >= 2:
        return {'thru': thru, 'n': dir_.normalize() if dir_.lengthSq() > 1e-12 else ref.normalize()}
    return None


def xAnchors(P, cs):
    # port of xAnchors (hair-modeler.clean.html:7771): the X anchors among the inner anchors of the curves cs, each once,
    # in the order they are met
    set_ = set(cs); seen = set(); out = []
    for c in cs:
        for k in range(1, len(c.anchors) - 1):
            J = c.anchors[k]
            if J in seen:
                continue
            seen.add(J)
            if xSides(P, J, [d for d in curvesWith(P, J) if d in set_]):
                out.append(J)
    return out


def lockAround(P, J):
    # port of lockAround (hair-modeler.clean.html:7780): the curves of the lock (joined by walls) around anchor J
    ids = dict.fromkeys(c.id for c in curvesWith(P, J) if not c.ring); grew = True
    while grew:
        grew = False
        for s in P.surfaces():
            if (s.a in ids) != (s.b in ids):
                ids[s.a] = None; ids[s.b] = None; grew = True
    return [c for c in (P.items.get(i) for i in ids) if c is not None and c.type == 'curve' and not c.ring]


def _findIndexById(anchors, aid):
    for i, a in enumerate(anchors):
        if a.id == aid:
            return i
    return -1


def flatStretch(P, c, i0, i1):
    """port of flatStretch (hair-modeler.clean.html:7787): the stretch i0…i1 (i0 < i1) of curve c in terms of real
    curves: an edge curve's stretch → the stretches of the curves its path runs along (an edge curve made of edge
    curves would hang on curves that go away with their walls). [{'c', 'i0', 'i1'}]"""
    if not truthy(c.path):
        return [{'c': c, 'i0': i0, 'i1': i1}]
    out = []; pos = 0
    for cid, a0, a1 in c.path:
        s = P.items.get(cid)
        if s is None:
            return [{'c': c, 'i0': i0, 'i1': i1}]
        j0 = _findIndexById(s.anchors, a0); j1 = _findIndexById(s.anchors, a1)
        if j0 < 0 or j1 < 0:
            return [{'c': c, 'i0': i0, 'i1': i1}]
        ln = abs(j1 - j0); d = 1 if j1 >= j0 else -1; lo = js_max(i0, pos); hi = min(i1, pos + ln)
        if hi > lo:
            out.append({'c': s, 'i0': j0 + d * (lo - pos), 'i1': j0 + d * (hi - pos)})
        pos += ln
    return out if out else [{'c': c, 'i0': i0, 'i1': i1}]


def wallsAcrossX(P, J):
    # port of wallsAcrossX (hair-modeler.clean.html:7800): the walls one of whose curves passes through J (not ending there)
    def across(s):
        for iid in (s.a, s.b):
            c = P.items.get(iid)
            if c is None:
                continue
            i = index_of(c.anchors, J)
            if i > 0 and i < len(c.anchors) - 1:
                return True
        return False
    return [s for s in P.surfaces() if across(s)]


def canSeparateAt(P, a):
    """the anchor pie's item "✂ Separate the zones at this X" (10122-10124): shown for the last selected anchor when it
    is an X of the curves through it and a wall still runs across it"""
    return a is not None and xSides(P, a, curvesWith(P, a)) is not None and len(wallsAcrossX(P, a)) > 0


def separateAtX(P, J, silent=False, regen=None):
    """port of separateAtX (hair-modeler.clean.html:7806): the zones separated at an X — the curves kept whole. The lock's
    walls are cut across at the X: every wall curve gets its cut place (curves through the X — the X itself; the others —
    where the cross plane through the X crosses them, nearest to it: an anchor there, or one put in keeping the shape),
    and every wall whose two curves are both cut becomes two walls — an upper and a lower one — on edge curves along the
    parts of its curves on each side. The curves stay as they were; the upper and the lower walls are separate locks
    touching at the cut.
    Returns (separated: made > 0, the toast or None when silent, the walls made). The old walls go after their two new
    ones exist (6881), so the rule of deleteItem (899) keeps every service curve a new wall or edge curve still uses:
    no `keep` needed here (cutWall needs one: it deletes first)."""
    cs = lockAround(P, J)
    csset = set(cs)
    xs = xSides(P, J, [c for c in curvesWith(P, J) if c in csset])
    if not xs:
        return False, None if silent else NO_X, []
    walls = [s for s in P.surfaces() if any(c.id == s.a or c.id == s.b for c in cs)]
    wc = [c for c in (P.items.get(i) for i in dict.fromkeys(x for s in walls for x in (s.a, s.b))) if c is not None and c.type == 'curve']
    for c in wc:
        if c.dirty or not c.samples:
            rebuildCurve(P, c)                                      # (right after an undo the curves aren't sampled yet)
    n = xs['n']

    def side(p):
        return p.clone().sub(J.pos).dot(n)
    # where each wall curve is cut: through J — J; else the plane's crossing nearest to J (not too far: a curling lock
    # crosses the plane again elsewhere)
    cut = {}; near = []
    for c in wc:
        i = index_of(c.anchors, J)
        if i > 0 and i < len(c.anchors) - 1:
            cut[c] = J; continue
        if i >= 0:
            continue                                                # (ends at J — it is one side already)
        sm = c.samples; best = None; bd = math.inf
        for k in range(len(sm) - 1):
            d0 = side(sm[k].p); d1 = side(sm[k + 1].p)
            if d0 == d1 or d0 * d1 > 0:
                continue
            t = d0 / (d0 - d1); p = sm[k].p.clone().lerp(sm[k + 1].p, t); dist = p.distanceTo(J.pos)
            if dist < bd:
                bd = dist; seg = sm[k].seg; best = {'c': c, 'seg': seg, 'u': clamp((k - seg * 24 + t) / 24, 0, 1), 'dist': dist}
        if best is not None:
            near.append(best)
    ds = sorted(h['dist'] for h in near); med = ds[len(ds) >> 1] if ds else 0
    lim = js_max(3 * med, 4 * js_or(P.settings.step, defaultStep(P)))
    for h in near:
        if h['dist'] > lim or h['c'] in cut:
            continue
        A = h['c'].anchors
        a = A[h['seg']] if h['u'] < 0.03 else A[h['seg'] + 1] if h['u'] > 0.97 else None
        if a is not None and (a is A[0] or a is A[len(A) - 1]):
            continue                                                # (right at an end — nothing to cut)
        if a is None:
            a = insertKeepShape(P, h['c'], h['seg'], h['u'])        # (it goes into every curve along that segment)
            for o in near:
                if o is not h and o['c'] not in cut and any(x is a for x in o['c'].anchors):
                    cut[o['c']] = a
        cut[h['c']] = a
    made = 0; kept = 0; out = []
    # one edge curve per curve part: walls sharing a curve share its part on each side (they stay one lock)
    edges = {}

    def edge(c, i0, i1, parent):
        st = flatStretch(P, c, i0, i1)
        key = '|'.join(f"{q['c'].id}:{q['c'].anchors[q['i0']].id}-{q['c'].anchors[q['i1']].id}" for q in st)
        if key not in edges:
            edges[key] = pathCurve(P, st, parent)
        return edges[key]
    for s in walls:
        A = P.items.get(s.a); B = P.items.get(s.b)
        if A is None or B is None:
            continue
        if A not in cut or B not in cut:
            kept += 1; continue
        ia = index_of(A.anchors, cut[A]); ib = index_of(B.anchors, cut[B])
        if ia <= 0 or ib <= 0:
            kept += 1; continue
        # the parts on the same side of the X go together (the curves may run opposite ways)
        pa = [[0, ia], [ia, len(A.anchors) - 1]]; pb = [[0, ib], [ib, len(B.anchors) - 1]]
        if js_sign(side(A.anchors[0].pos)) != js_sign(side(B.anchors[0].pos)):   # (Math.sign: -0 and 0 count the same, as !== says)
            pb.reverse()
        for k in range(2):
            a0, a1 = pa[k]; b0, b1 = pb[k]
            if a1 <= a0 or b1 <= b0:
                continue
            x = edge(A, a0, a1, s.parent); y = edge(B, b0, b1, s.parent)
            w = makeSurface(P, x, y) if x is not y else None
            if w is not None:
                w.p = dict(s.p); w.visible = s.visible; made += 1; out.append(w)
        deleteItem(P, s.id)                                         # (its own edge curves go with it, unused now)
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    if regen is not None:
        regen()                                                     # (if (S.conv) regenConversion(), 6883)
    return made > 0, None if silent else separatedMessage(made, kept), out


def groupXCurves(P, g):
    # the curves separateGroupX and the group's button look at (6892, 11039): not rings, in the group or below it
    return [c for c in P.curves() if not c.ring and isDescendant(P, c.id, g.id)]


def xCount(P, g):
    """the count of the group's button "✂ Separate zones at X junctions (N)" (11039): its X anchors with walls across"""
    return len([a for a in xAnchors(P, groupXCurves(P, g)) if wallsAcrossX(P, a)])


def separateGroupX(P, g, regen=None):
    """port of separateGroupX (hair-modeler.clean.html:7866): every X junction of the locks in a group whose walls still
    run across it, one after the other (each separation can make the next X — its edge curves count). Returns (k, the
    toast): k the X separated (a commit only when k)."""
    tried = set(); k = 0
    for _guard in range(30):
        J = next((a for a in xAnchors(P, groupXCurves(P, g)) if a not in tried and wallsAcrossX(P, a)), None)
        if J is None:
            break
        tried.add(J)
        if separateAtX(P, J, True, regen)[0]:
            k += 1
    return k, groupMessage(k)


# ======================= walls round an X (8001-8142) =======================

def _stringify(path):
    # JSON.stringify of a c.path (ids are integers: Python's json writes them as V8 does)
    return json.dumps(path, separators=(',', ':'))


def pathCurve(P, stretches, parent):
    """port of pathCurve (hair-modeler.clean.html:9095): stretches [{'c', 'i0', 'i1'}] (indices into c.anchors, either
    way) → one curve: the source curve itself when a stretch is a whole curve, else an edge curve along them (one along
    the same stretches already there — walls made before — is that one: its walls are found, not doubled)"""
    if len(stretches) == 1:
        q = stretches[0]; c = q['c']
        if min(q['i0'], q['i1']) == 0 and max(q['i0'], q['i1']) == len(c.anchors) - 1:
            return c
    path = [[q['c'].id, q['c'].anchors[q['i0']].id, q['c'].anchors[q['i1']].id] for q in stretches]
    key = _stringify(path); rkey = _stringify([[c, b, a] for c, a, b in path][::-1])
    old = next((x for x in P.curves() if truthy(x.path) and (_stringify(x.path) == key or _stringify(x.path) == rkey)), None)
    if old is not None:
        return old
    e = makeCurve(P, parent); e.junc = True; e.path = path; e.name = serviceName(P, 'EC'); e.anchors = pathAnchors(P, path) or []; e.dirty = True
    return e


def pairZones(A, B):
    """port of pairZones (hair-modeler.clean.html:9107): two curves joined in an X (sharing anchors / runs of anchors in
    the middle): their zones — stretches between the shared places where they run apart (root → first X, X → X, last X
    → tip). Shared runs (a common trunk) are no zone. {'zones': [{'a': [i0, i1], 'b': [j0, j1]}], 'trunks': [[i0, i1]
    on A], 'rev' (B runs the other way)}"""
    sh = set(a for a in A.anchors if a in B.anchors)
    jA = [k for k in range(len(A.anchors)) if A.anchors[k] in sh]
    if len(jA) >= 2:
        rev = index_of(B.anchors, A.anchors[jA[len(jA) - 1]]) < index_of(B.anchors, A.anchors[jA[0]])
    else:
        rev = A.anchors[0].pos.distanceTo(B.anchors[len(B.anchors) - 1].pos) < A.anchors[0].pos.distanceTo(B.anchors[0].pos)
    nB = len(B.anchors)

    def bAt(k):
        if A.anchors[k] in sh:
            return index_of(B.anchors, A.anchors[k])
        return (nB - 1 if rev else 0) if k == 0 else (0 if rev else nB - 1)
    marks = sorted(dict.fromkeys([0, *jA, len(A.anchors) - 1])); zones = []; trunks = []
    for m in range(len(marks) - 1):
        p = marks[m]; q = marks[m + 1]; jp = bAt(p); jq = bAt(q)
        trunk = A.anchors[p] in sh and A.anchors[q] in sh and q == p + 1 and abs(jq - jp) == 1
        if trunk:
            trunks.append([p, q]); continue
        if jp == jq:
            continue                                                # (B has nothing there)
        zones.append({'a': [p, q], 'b': [jp, jq]})
    return {'zones': zones, 'trunks': trunks, 'rev': rev}


def pairEnvelope(A, B, Z, near):
    """port of pairEnvelope (hair-modeler.clean.html:9124): the side of an X pair facing curve / unit `near` (a distance
    function): per zone the stretch of A or of B lying nearer, the trunks between. [{'c', 'i0', 'i1'}]"""
    # (the stretch's own anchors, not its ends — the ends are shared: an X, a common tip — and say nothing about the side)
    def mean(c, i0, i1):
        lo = min(i0, i1); hi = max(i0, i1); v = V3(); n = 0
        for k in range(lo + 1, hi):
            v.add(c.anchors[k].pos); n += 1
        return v.divideScalar(n) if n else c.anchors[lo].pos.clone().lerp(c.anchors[hi].pos, 0.5)
    parts = sorted([*({'at': z['a'][0], 'z': z} for z in Z['zones']), *({'at': t[0], 't': t} for t in Z['trunks'])], key=lambda x: x['at'])   # (a - b: stable)
    out = []
    for pt in parts:
        t = pt.get('t')
        if t is not None:
            out.append({'c': A, 'i0': t[0], 'i1': t[1]}); continue
        z = pt['z']
        da = near(mean(A, z['a'][0], z['a'][1])); db = near(mean(B, z['b'][0], z['b'][1]))
        out.append({'c': A, 'i0': z['a'][0], 'i1': z['a'][1]} if da <= db else {'c': B, 'i0': z['b'][0], 'i1': z['b'][1]})
    return out


def xShared(c1, c2):
    # port of xShared (hair-modeler.clean.html:9176): anchors two curves both pass through (an X — neither curve ends
    # there), in the order along the first curve
    def inner(c, a):
        i = index_of(c.anchors, a)
        return i > 0 and i < len(c.anchors) - 1
    return [a for a in c1.anchors if inner(c1, a) and inner(c2, a)]


def xPairs(cs):
    """port of xPairs (hair-modeler.clean.html:9142): pairs of the curves joined in an X (sharing an anchor that neither
    ends on); a curve in two such pairs — left out. Tuples (c1, c2): a pair is found by identity in the original (a Map
    keyed by the array); the pairs are distinct, so a tuple's equality is that identity."""
    all_ = []
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            if xShared(cs[i], cs[j]):
                all_.append((cs[i], cs[j]))
    return [p for p in all_ if len([q for q in all_ if p[0] in q or p[1] in q]) == 1]


def _unitCurves(u):
    return u if isinstance(u, tuple) else (u,)


def xPathWalls(P, cs, regen=None):
    """port of xPathWalls (hair-modeler.clean.html:9147): walls round X junctions — the selected curves in their order
    across the lock (single curves and X pairs); a wall per zone inside every X pair, and between neighbours (an X pair
    shows a neighbour the side facing it — an edge curve switching from one of its curves to the other at the X).
    Nothing is cut: the curves stay whole and editable. Returns (walls made, the toast, pairs): no X pair — the plain
    createSurfaces (its own toast and selection: (made, msg, False)); with pairs the selection becomes the walls made,
    even none (8102), and the toast is this one's."""
    pairs = xPairs(cs)
    if not pairs:
        made, msg = createSurfaces(P, cs, False)
        return made, msg, False
    inPair = {}
    for p in pairs:
        for c in p:
            inPair[c] = p
    units = []
    for c in cs:
        p = inPair.get(c)
        if p is not None:
            if not any(u is p for u in units):
                units.append(p)
        else:
            units.append(c)

    def pts(u):
        return [a.pos for c in _unitCurves(u) for a in c.anchors]

    def ctr(u):
        Pp = pts(u); s = V3()
        for p in Pp:
            s.add(p)
        return s.divideScalar(len(Pp))
    # order across the lock: along the line through the two unit centres farthest apart
    if len(units) > 2:
        C = [ctr(u) for u in units]; bd = -1; ax = V3(1, 0, 0)
        for p in C:
            for q in C:
                d = p.distanceToSquared(q)
                if d > bd:
                    bd = d; ax = q.clone().sub(p)
        o = sorted(({'u': u, 's': C[i].dot(ax)} for i, u in enumerate(units)), key=lambda x: x['s'])   # (x.s - y.s: stable)
        units[:] = [x['u'] for x in o]
    parent = cs[0].parent; made = []

    def wall(x, y):
        if x is y:
            return
        s = makeSurface(P, x, y)
        if s is not None:
            made.append(s)
    Zs = {p: pairZones(p[0], p[1]) for p in pairs}
    # inside each X pair: a wall per zone between edge curves along that zone of each curve
    for p in pairs:
        for z in Zs[p]['zones']:
            wall(pathCurve(P, [{'c': p[0], 'i0': z['a'][0], 'i1': z['a'][1]}], parent), pathCurve(P, [{'c': p[1], 'i0': z['b'][0], 'i1': z['b'][1]}], parent))

    # between neighbours: the side each one shows the other
    # distance to a neighbour: to its curves' sample points (along them, not just their anchors)
    def nearTo(u):
        Pp = []
        for c in _unitCurves(u):
            Pp.extend([s.p for s in c.samples] if c.samples else [a.pos for a in c.anchors])

        def f(q):
            d = math.inf
            for p in Pp:
                e = p.distanceToSquared(q)
                if e < d:
                    d = e
            return d
        return f

    def side(u, other):
        return pathCurve(P, pairEnvelope(u[0], u[1], Zs[u], nearTo(other)), parent) if isinstance(u, tuple) else u
    for i in range(len(units) - 1):
        wall(side(units[i], units[i + 1]), side(units[i + 1], units[i]))
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    if regen is not None:
        regen()                                                     # (if (S.conv) regenConversion(), 8101)
    return made, xWallsMessage(len(made)), True


def xZoneWalls(P, c1, c2, regen=None):
    """port of xZoneWalls (hair-modeler.clean.html:9183) — nothing in the original calls it (dead code; no menu here
    either). One wall per zone of two curves joined in an X: both are split at the shared anchors (Split curve at the
    anchor — guides and other walls follow), then the pieces facing each other in every zone (nearest middles) get a
    wall. Returns (walls, toast, split): not joined in an X — the plain createSurfaces ((made, msg, False))."""
    from .weld import splitAtAnchor
    Js = xShared(c1, c2)
    if not Js:
        made, msg = createSurfaces(P, [c1, c2], False)
        return made, msg, False
    A1 = list(c1.anchors); A2 = list(c2.anchors); before = set(c.id for c in P.curves())
    for J in Js:
        splitAtAnchor(P, J, True)

    # the pieces of an original curve: the curve itself or new ones, lying on a run of its anchors (in order, either way)
    def piecesOf(A):
        return [c for c in P.curves() if (c.id not in before or c is c1 or c is c2) and not c.ring and len(c.anchors) >= 2 and all(a in A for a in c.anchors)
                and all(k == 0 or abs(index_of(A, a) - index_of(A, c.anchors[k - 1])) == 1 for k, a in enumerate(c.anchors))]
    P1 = piecesOf(A1); P2 = piecesOf(A2)

    def mid(c):
        return c.anchors[len(c.anchors) >> 1].pos.clone().add(c.anchors[(len(c.anchors) - 1) >> 1].pos).multiplyScalar(0.5)
    made = []; used = set()
    for p in P1:
        best = None; bd = math.inf
        for q in P2:
            if q is p or q in used:
                continue
            d = mid(p).distanceTo(mid(q))
            if d < bd:
                bd = d; best = q
        if best is None:
            continue
        used.add(best)
        # (a wall the two had already was split along with them — that one counts)
        s = makeSurface(P, p, best) or next((w for w in P.surfaces() if (w.a == p.id and w.b == best.id) or (w.a == best.id and w.b == p.id)), None)
        if s is not None:
            made.append(s)
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    if regen is not None:
        regen()
    msg = f'{len(made)} walls — one per zone of the X (the curves are split at {"the X anchor" if len(Js) == 1 else str(len(Js)) + " X anchors"})' if made else NO_WALLS
    return made, msg, True


def xPartner(c, lst):
    # port of xPartner (hair-modeler.clean.html:9212): c's partner in an X (the most shared anchors) among lst, or None
    best = None; bn = 0
    for h in lst:
        if h is c:
            continue
        n = len(xShared(c, h))
        if n > bn:
            bn = n; best = h
    return best
