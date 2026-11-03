# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Lot 4.4 (docs/fase-4-plano.md, step 4.4): the Topology tab of the Modifier tool — Cap, Fill holes and Density, items
of a group (types 'cap', 'skin', 'tip') that shape the mesh of its locks. Ports of capTargets (hair-modeler.clean.html:7204),
capRef (7207), addCaps (7252), addTips (7318), addSkin (9514), skinCandidates (9347), convertSkin (9474), and the data
part of rebuildCaps (9526) and rebuildSkins (9461): the previews (D4.5), as {pos, faces} in the model's space.

The original's add / convert functions end in onSelChange, renderOutliner and commit, and S.surfDirty: here they
return what they made, the toast and the new selection; the caller (blender/ops_topology.py) stores the selection,
saves the project and rebuilds the live preview (touching the group's curves for S.surfDirty)."""
import math

from . import curves as CM
from .convert import capGrid, capOf, collect, lockGrid, lockGroups, lockQuery, levelH_of
from .edit import addItem, makeCurve
from .geom import resample
from .js import js_max, js_min, js_or, js_pow, to_int32
from .model import CAP_DEFAULTS, SKIN_DEFAULTS
from .density import TIP_DEFAULTS
from .rowbase import settings_conv
from .surfaces import surfaceData, wallsOf
from .walls import hasSurface, makeSurface

CAP_MODES = (('quad', 'Quads'), ('point', 'Collapse to a point'), ('tris', 'Fewest triangles'))   # (9212)
CAP_TIP = 'Select groups (or anything in them), then add'                                        # (7249, 7313)
NO_TARGET = 'Select a group (or something in it) first'                                          # (7254, 7320, 9516)
NOTHING_TO_FILL = 'Nothing to fill — no uncovered gaps between the curves'                       # (9478)
FILL_EXISTS = 'These groups already have Fill holes'                                             # (9522)


def capTargets(P, sel_items):
    """port of capTargets (7204): the groups the selected items are in (a selected group itself; a cap counts for none),
    each once, in the order of the selection"""
    out = []
    for iid in sel_items:
        it = P.items.get(iid)
        if it is None:
            continue
        g = it if it.type == 'group' else (None if it.type == 'cap' else P.parentOf(it))
        if g is not None and g.type == 'group' and g not in out:
            out.append(g)
    return out


def capRef(P):
    """port of capRef (7207): the typical lock width (the median distance from the middle of a wall's first curve to its
    second) — the range of the bevel's Size slider"""
    ws = []
    for s in P.surfaces():
        A = P.items.get(s.a); B = P.items.get(s.b)
        if not A or not B or len(A.samples) < 2 or len(B.samples) < 2:
            continue
        p = A.samples[len(A.samples) >> 1].p; d = math.inf
        for q in B.samples:
            d = js_min(d, q.p.distanceToSquared(p))
        ws.append(math.sqrt(d))
    ws.sort()
    return ws[len(ws) >> 1] if ws else P.S.scale * 0.02


def addCaps(P, sel_items, defaults=None):
    """port of addCaps (7252): a cap in every target group with the defaults for new caps (S.capDefaults) — a group
    that has one gets the defaults instead (one cap per group). Returns (made, toast); made None: nothing to do."""
    gs = capTargets(P, sel_items)
    if not gs:
        return None, NO_TARGET
    made = []; df = defaults or CAP_DEFAULTS
    for g in gs:
        old = next((x for x in (P.items.get(i) for i in g.children) if x and x.type == 'cap'), None)
        if old:
            old.p = {**df}; made.append(old); continue
        k = addItem(P, 'cap', g.id, {'p': {**df}, 'srcSurfs': []})
        k.name = f'Cap ({g.name})'; made.append(k)
    return made, f'{len(made)} caps' if len(made) > 1 else made[0].name


def addTips(P, sel_items, defaults=None):
    """port of addTips (7318): a Density in every target group (one per group: an existing one takes the density of the
    defaults, its mask stays). Returns (made, toast); made None: nothing to do. (The original turns its brush on: 4.8.)"""
    gs = capTargets(P, sel_items)
    if not gs:
        return None, NO_TARGET
    made = []; df = defaults or TIP_DEFAULTS
    for g in gs:
        old = next((x for x in (P.items.get(i) for i in g.children) if x and x.type == 'tip'), None)
        if old:
            old.p = {**old.p, 'dens': df['dens']}; made.append(old); continue
        k = addItem(P, 'tip', g.id, {'p': {**df, 'v': 2}})
        k.name = f'Density ({g.name})'; made.append(k)
    lead = f'{len(made)} density modifiers' if len(made) > 1 else made[0].name
    return made, f'{lead} — now paint where it works (Ctrl — erase, Shift — smooth)'


def addSkin(P, sel_items, defaults=None):
    """port of addSkin (9514): Fill holes in every target group that has none. Returns (made, toast): made None with
    the toast when nothing was made; no toast otherwise (the original says nothing)."""
    gs = capTargets(P, sel_items)
    if not gs:
        return None, NO_TARGET
    made = []
    for g in gs:
        if any(getattr(P.items.get(i), 'type', None) == 'skin' for i in g.children):
            continue
        k = addItem(P, 'skin', g.id, {'p': {**SKIN_DEFAULTS, **(defaults or {})}, 'cands': []})
        k.name = f'Fill holes ({g.name})'; made.append(k)
    if not made:
        return None, FILL_EXISTS
    return made, None


class _Piece:
    """a piece of a curve between two nodes (skinCandidates' pc): compared by identity, as the original's objects"""
    __slots__ = ('id', 'c', 'i0', 'i1', 'u', 'v', 'len')

    def __init__(self, id_, c, i0, i1):
        self.id = id_; self.c = c; self.i0 = i0; self.i1 = i1; self.u = c.anchors[i0]; self.v = c.anchors[i1]
        self.len = c.arc[min(i1 * 24, len(c.arc) - 1)] - c.arc[i0 * 24]


def _key(p, tol):
    return (math.floor(p.x / tol), math.floor(p.y / tol), math.floor(p.z / tol))


def skinCandidates(P, k, T):
    """port of skinCandidates (9347): the walls Fill holes would make in its group — the holes (closed outlines of curve
    pieces between welded anchors) and, with `open`, a wall between each open edge curve and its nearest neighbour —
    where no wall of the group covers and not inside a closed lock. Returns [{'kind': 'hole', 'X', 'Y', 'a', 'b'} |
    {'kind': 'pair', 'ca', 'cb', 'a', 'b'}]: X / Y the two anchor lists of a hole's outline, ca / cb the two curves of a
    pair, a / b the two point rows of the preview's strip (16 points each)."""
    g = P.items.get(k.parent)
    if not g or g.type != 'group':
        return []
    kids = [x for x in (P.items.get(i) for i in g.children) if x]
    cs = [c for c in kids if c.type == 'curve' and not c.ring and not c.conn and not c.junc and len(c.samples) >= 2]
    walls = [s for s in kids if s.type == 'surface']
    h = levelH_of(P, settings_conv(P)); tol = 0.45 * h               # (levelH() after buildRowTables: CP = S)
    # what is covered already: points over every wall of the group (between its columns too)
    grid = {}

    def addCover(p):
        grid.setdefault(_key(p, tol), []).append(p)

    def covered(p):
        x, y, z = _key(p, tol); t2 = tol * tol
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q in grid.get((x + dx, y + dy, z + dz), ()):
                        if q.distanceToSquared(p) < t2:
                            return True
        return False
    widths = []
    for s in wallsOf(walls):
        d = surfaceData(P, s, T)
        if not d:
            continue
        M = to_int32(s.p.get('segs') or 0) + 1; R = len(d.pos) / M; pos = d.pos
        r = 0
        while r < R:
            for j in range(M - 1):
                for t in (0, 0.25, 0.5, 0.75):
                    addCover(pos[r * M + j].clone().lerp(pos[r * M + j + 1], t))
            r += 1
        r = 0
        while r < R:
            addCover(pos[r * M + M - 1]); r += 1
        rh = to_int32(R) >> 1
        widths.append(pos[rh * M].distanceTo(pos[rh * M + M - 1]))
    widths.sort()
    wRef = widths[len(widths) >> 1] if widths else 3 * h
    closed = [G for G in (lockGrid(P, w, T) for w in lockGroups(wallsOf(walls))) if G]

    def inside(p):
        for G in closed:
            q = lockQuery(G, p)
            if q['inside'] and q['dist'] > 0.2 * h:
                return True
        return False
    NR = 16

    def patch(X, Y):
        # a patch between two point paths (from the same start to the same end, or a ruled strip): its inner samples
        a = resample(X, NR); b = resample(Y, NR); inner = []
        for r in range(2, NR - 2):
            for t in (0.25, 0.5, 0.75):
                inner.append(a[r].clone().lerp(b[r], t))
        return {'a': a, 'b': b, 'inner': inner, 'width': js_max(*[p.distanceTo(b[i]) for i, p in enumerate(a)])}

    def ok(Pp):
        if Pp['width'] < 0.2 * h:
            return False
        cov = sum(1 for p in Pp['inner'] if covered(p)); ins = sum(1 for p in Pp['inner'] if inside(p))
        return cov < 0.5 * len(Pp['inner']) and ins < 0.5 * len(Pp['inner'])
    out = []
    # ---- holes: cycles of curve pieces between nodes (anchors shared by two curves of the group, or curve ends)
    use = {}
    for c in cs:
        for a in c.anchors:
            use[id(a)] = use.get(id(a), 0) + 1
    pieces = []; adj = {}; node = {}
    for c in cs:
        nodes = [i for i, a in enumerate(c.anchors) if i == 0 or i == len(c.anchors) - 1 or use.get(id(a)) > 1]
        for q in range(len(nodes) - 1):
            pc = _Piece(len(pieces), c, nodes[q], nodes[q + 1])
            pieces.append(pc)
            for n in (pc.u, pc.v):
                node.setdefault(id(n), n); adj.setdefault(id(n), []).append(pc)
    seen = set(); cycles = []; steps = [0]

    def dfs(s, at, path, nodes_, L):
        steps[0] += 1
        if steps[0] > 150000 or len(cycles) > 3000:
            return
        for pc in adj.get(id(at), ()):
            if any(x is pc for x in path):
                continue
            to = pc.v if pc.u is at else pc.u
            if to is s and len(path) >= 1:
                cyc = [*path, pc]; key = tuple(sorted(x.id for x in cyc))
                if key not in seen and len({id(x.c) for x in cyc}) >= 2:
                    seen.add(key); cycles.append({'cyc': cyc, 'start': s, 'L': L + pc.len})
                continue
            if any(x is to for x in nodes_) or len(path) >= 5:
                continue
            path.append(pc); nodes_.append(to); dfs(s, to, path, nodes_, L + pc.len); path.pop(); nodes_.pop()
    for sid in list(adj.keys()):
        s = node[sid]
        dfs(s, s, [], [s], 0)
    cycles.sort(key=lambda x: x['L'])                               # (Array.prototype.sort is stable: ties keep their order)

    def piecePts(pc, frm):
        S0 = pc.i0 * 24; S1 = min(pc.i1 * 24, len(pc.c.samples) - 1)
        Pp = [x.p for x in pc.c.samples[S0:S1 + 1]]
        return Pp if frm is pc.u else Pp[::-1]

    def pieceAnch(pc, frm):
        A = pc.c.anchors[pc.i0:pc.i1 + 1]
        return A if frm is pc.u else A[::-1]
    for cy in cycles:
        cyc = cy['cyc']; at = cy['start']
        # walk the outline: anchors and points, corners where the curve changes
        A = []; Pt = []; cornerA = []; cornerP = []
        for i, pc in enumerate(cyc):
            an = pieceAnch(pc, at); pt = piecePts(pc, at)
            if i == 0 or pc.c is not cyc[i - 1].c:
                cornerA.append(len(A)); cornerP.append(len(Pt))
            A.extend(an[:-1]); Pt.extend(pt[:-1])
            at = pc.v if pc.u is at else pc.u
        n = len(A); m = len(Pt)
        if n < 2 or m < 4:
            continue
        cum = [0]
        for i in range(1, m + 1):
            cum.append(cum[i - 1] + Pt[i - 1].distanceTo(Pt[i % m]))
        best = None
        for x in range(len(cornerA)):
            for y in range(x + 1, len(cornerA)):
                a = cum[cornerP[y]] - cum[cornerP[x]]; d = abs(2 * a - cum[m])
                if not best or d < best['d']:
                    best = {'d': d, 'x': x, 'y': y}
        if not best:
            continue
        pu = cornerP[best['x']]; pw = cornerP[best['y']]; au = cornerA[best['x']]; aw = cornerA[best['y']]
        XP = Pt[pu:pw + 1]; YP = []; XA = A[au:aw + 1]; YA = []
        i = pu
        while True:
            YP.append(Pt[i])
            if i == pw:
                break
            i = (i + m - 1) % m
        i = au
        while True:
            YA.append(A[i])
            if i == aw:
                break
            i = (i + n - 1) % n
        Pp = patch(XP, YP)
        if not ok(Pp):
            continue
        for p in Pp['inner']:
            addCover(p)
        out.append({'kind': 'hole', 'X': XA, 'Y': YA, 'a': Pp['a'], 'b': Pp['b']})
    # ---- open curves: edge curves (<= 1 wall) and their nearest neighbour
    if k.p.get('open'):
        deg = {}
        for s in walls:
            for cid in (s.a, s.b):
                deg[cid] = deg.get(cid, 0) + 1
        inHole = set()
        for o in out:
            for a in [*o['X'], *o['Y']]:
                for c in cs:
                    if any(x is a for x in c.anchors):
                        inHole.add(id(c))

        def isOpen(c):                                              # (both curves of a pair must be open edges)
            return deg.get(c.id, 0) <= 1 and id(c) not in inHole
        RS = {id(c): resample([x.p for x in c.samples], 24) for c in cs}

        def one(Pa, Q):
            s = 0
            for p in Pa:
                s = s + js_pow(js_min(*[q.distanceToSquared(p) for q in Q]), 0.5)
            return s / len(Pa)

        def gap(a, b):
            Aa = RS[id(a)]; Bb = RS[id(b)]
            return (one(Aa, Bb) + one(Bb, Aa)) / 2
        done = set()
        for a in cs:
            if not isOpen(a):
                continue
            best = None
            for b in cs:
                key = (min(a.id, b.id), max(a.id, b.id))
                if b is a or not isOpen(b) or hasSurface(P, a, b) or key in done:
                    continue
                if any(any(x is y for y in b.anchors) for x in a.anchors):   # (welded / crossing curves: that's a hole's business)
                    continue
                if RS[id(a)][12].distanceTo(RS[id(b)][12]) > 6 * wRef:
                    continue
                d = gap(a, b)
                if d < 2.5 * wRef and (not best or d < best['d']):
                    best = {'d': d, 'b': b}
            if not best:
                continue
            b = best['b']; Aa = RS[id(a)]; Bb = RS[id(b)]
            rev = Aa[0].distanceTo(Bb[0]) + Aa[23].distanceTo(Bb[23]) > Aa[0].distanceTo(Bb[23]) + Aa[23].distanceTo(Bb[0])
            Pp = patch(Aa, Bb[::-1] if rev else Bb)
            if not ok(Pp):
                continue
            done.add((min(a.id, b.id), max(a.id, b.id)))
            for p in Pp['inner']:
                addCover(p)
            out.append({'kind': 'pair', 'ca': a, 'cb': b, 'a': Pp['a'], 'b': Pp['b']})
    return out


def skinPreview(P, k, T):
    """the data part of rebuildSkins (9461) for one Fill holes: k.cands set (the panel counts them) and the preview's
    strips as {pos, faces} (quads between the two point rows of each candidate), None without a candidate"""
    k.cands = skinCandidates(P, k, T) if k.visible else []
    pos = []; faces = []
    for c in k.cands:
        o = len(pos)
        for i, p in enumerate(c['a']):
            pos.append(p); pos.append(c['b'][i])
        for i in range(len(c['a']) - 1):
            faces.append([o + 2 * i, o + 2 * i + 1, o + 2 * i + 3, o + 2 * i + 2])
    return {'pos': pos, 'faces': faces} if faces else None


def capPreview(P, k, T):
    """the data part of rebuildCaps (9526) for one cap: k.srcSurfs (the walls of the locks it closes) and its fill and
    bevel rings as {pos, faces} (capGrid's capFaces on each closed lock), None when it closes nothing"""
    parts = []; k.srcSurfs = []
    g = P.items.get(k.parent)
    if g and g.type == 'group' and k.visible:
        surfs = [s for s in collect(P, g.id, set(), ['surface']) if capOf(P, s) is k]
        for walls in lockGroups(wallsOf(surfs)):
            lg = lockGrid(P, walls, T); c = capGrid(lg, k.p) if lg else None
            for w in walls:
                k.srcSurfs.append(w.id)
            if c and c.capFaces:
                parts.append({'pos': c.pos, 'faces': c.capFaces})
    if not parts:
        return None
    pos = []; faces = []                                            # (concatMeshes 9562)
    for m in parts:
        o = len(pos); pos.extend(m['pos']); faces.extend([[i + o for i in f] for f in m['faces']])
    return {'pos': pos, 'faces': faces}


def convertSkin(P, k, T=None):
    """port of convertSkin (9474): the previewed walls become NURBS walls — a pair, a wall between its two curves; a
    hole, two junction curves along its outline ("Hole Curve N") and a wall between them. `T`: the row tables of the
    current state (buildRowTables after the dirty curves are rebuilt; built here when None). Returns (made, toast):
    made None with the toast when there is nothing to fill. Every curve is marked dirty, as the original does (the
    sampling memo gives the unchanged ones back as they were)."""
    from . import rows
    g = P.items.get(k.parent)
    if not g:
        return None, None
    CM.rebuild_dirty(P)
    if T is None:
        T = rows.buildRowTables(P).T
    cands = skinCandidates(P, k, T)
    if not cands:
        return None, NOTHING_TO_FILL
    made = []; bulge = js_or(k.p.get('bulge'), 0)
    for c in cands:
        if c['kind'] == 'pair':
            s = makeSurface(P, c['ca'], c['cb'])
            if s:
                s.parent = g.id; s.p['bulge'] = bulge; made.append(s)
            continue

        def mk(lst):
            cv = makeCurve(P, g.id); cv.anchors = lst; cv.junc = True; cv.name = 'Hole ' + cv.name
            return cv
        x = mk(c['X']); y = mk(c['Y'])
        made.append(addItem(P, 'surface', g.id, {'a': x.id, 'b': y.id, 'dirty': True, 'p': {'segs': 1, 'bulge': bulge, 'flip': False}}))
    for cv in P.curves():
        cv.dirty = True
    holes = sum(1 for c in cands if c['kind'] == 'hole'); pairs = sum(1 for c in cands if c['kind'] == 'pair')
    return made, f'{len(made)} wall{"" if len(made) == 1 else "s"} made ({holes} holes, {pairs} between open curves)'
