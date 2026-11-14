# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Fill the area under the pointer (1.0.2): port of "◎ Fill the area under the pointer (Shift+RMB → Detect the fill
outline here)" (hair-modeler.clean.html:8133-8296).

  fillCurves, nearCurves, fillHits, fillPlanAt   the curves the rays may stop at, the 32 rays from the pointer on the
                                        screen, the two sides of the area (an X pair: the zone, or the side facing)
  stretchPts, planOutline, holeOutline  the outline shown while the confirmation pie is open (showFillViz, 9251, and
                                        startFillAt's hole branch, 8283): flat [x0, y0, z0, x1, y1, z1, ...] in model
                                        space, one segment per six numbers (what LineSegmentsGeometry.setPositions takes)
  holeAt                                a hole in the walls round the pointer: a closed loop of free stretches
  startFillAt                           the pie item: the curves made fresh (8278), the hole first, else the plan; the
                                        confirmation pie's title, the outline, or the toast when there is no outline
  runFillPlan, fillFound                the confirmation: the area gets its wall (the usual way, or on edge curves; a
                                        hole: walls.fillCycle)

The original's comment (8133):
From the place the pie was opened, rays go out on the screen in every direction; each stops at the first curve it meets
(a nearer one to the camera wins a tie). The two hits on opposite sides lying closest together give the sides of the
area. A side that is one of two curves joined in an X shows the area the part of that pair facing it (above the X one
curve, below it the other — an edge curve); both sides of one X pair — the zone of the pair the pointer is in. The
outline is shown; confirmed, the area gets a NURBS wall the fitting way: two plain curves — the usual wall (Y trunks:
between the facing branches); edge curves — a wall on them.

The view is a function, as the core's other screen tools take it: screen(p) -> (x, y, z), pixels of the viewport with
y DOWN (the original's toScreen, 8414: (v.x + 1) / 2 * W, (1 - v.y) / 2 * H, v.z the clip depth), or None (behind the
camera: the segment is left out, as one with z >= 1). The pointer m = (x, y) in the same pixels. A projector with y up
mirrors the 32 rays (k <-> 32 - k): their order changes, and so do the ties (`t < best.t - 3`, `sc < best.sc`) — the
caller turns y round (H - y) for both the projector and the pointer. The 3 px of the tie and the 1e-9 of a parallel
ray are the original's, in pixels, not scaled.

Pure Python, no bpy. The selection, the undo step (commit), the pie and the drawing are the caller's: the results say
what the original sets (`sel`: the new S.sel, None when it stays); `regen` (a function or None) runs where the
original calls regenConversion. Visibility is the outliner's eye (edit.isVisible: no isolation in the port).

Line numbers: every one in this module is of 1.0.2 (reference/hair-modeler.clean.html)."""
import math

from . import fdlibm
from .edit import hairCurves, isVisible
from .js import truthy
from .walls import createSurfaces, fillCycle, makeSurface
from .xjunc import pairEnvelope, pairZones, pathCurve, xPartner, xShared

# the toasts (8276, 8291, 8191-8197) and the pies' texts (8273, 8286-8287, 8294-8295)
OPEN_OVER = 'Open the pie over the viewport, on the area to fill'
NO_OUTLINE = 'No outline around the pointer — there must be curves on both sides of it'
NOTHING = 'Nothing to fill there'
FILLED_ALREADY = 'This area is filled already'
FILLED_ZONE = 'Filled: a wall in this zone of the X (the curves stay whole)'
FILLED_OUTLINE = 'Filled: a wall along the highlighted outline (the curves stay whole)'
ITEM_LABEL = 'Detect the fill outline here'                         # (◎, 8273)
CONFIRM_LABEL = 'Fill the highlighted outline'                      # (✔, 8286 / 8294)
CANCEL_LABEL = 'Cancel'

PER = 24                                                            # samples per segment (sampleCurve's per; stretchPts, 9250)
TIE_PX = 3                                                          # 8155 / 8238: hits within 3 px — the nearer to the camera
PARALLEL = 1e-9                                                     # 8151 / 8234


def filledBetween(x, y):
    # the toast of runFillPlan's plain branch (8192)
    return f'Filled: a wall between {x.name} and {y.name}'


def holeFilledMessage(corners):
    # the toast of fillCycle (3382)
    return f'Hole filled: {corners} corner{"" if corners == 1 else "s"}, a wall between two curves along its edges (Bulge 0 — set it in the surface)'


def fillCurves(P):
    # port of fillCurves (hair-modeler.clean.html:9211): the curves a ray may stop at — no service curves (junction,
    # edge), no flat lock, no crown connector; shown by the eye; sampled
    return [c for c in hairCurves(P) if not truthy(c.junc) and not truthy(c.path) and not truthy(c.rib) and not truthy(c.conn)
            and isVisible(P, c) and len(c.samples) > 1]


def nearCurves(cs):
    # port of nearCurves (hair-modeler.clean.html:9213): q -> the squared distance to the nearest sample point of cs
    Pp = [s.p for c in cs for s in c.samples]

    def near(q):
        d = math.inf
        for p in Pp:
            e = p.distanceToSquared(q)
            if e < d:                                               # (Math.min: the sums of squares are never NaN nor -0)
                d = e
        return d
    return near


def _screenSegs(points, screen):
    """the screen segments between consecutive points both in front (z < 1), as (i, a, b): `sp[i].z < 1 && sp[i +
    1].z < 1` (8146 / 8228); a point the projector gives None for (behind the camera) counts as z >= 1"""
    sp = [screen(p) for p in points]
    out = []
    for i in range(len(sp) - 1):
        a = sp[i]; b = sp[i + 1]
        if a is not None and b is not None and a[2] < 1 and b[2] < 1:
            out.append((i, a, b))
    return out


_RAYS: list[tuple[int, float, float, float]] = [
    (k, fdlibm.cos(ang), fdlibm.sin(ang), ang) for k, ang in [(k, k * math.pi / 16) for k in range(32)]]   # (8149 / 8232: ang = k * Math.PI / 16)


def _cast(segs, m, px=1.0):
    """the 32 rays from m (8148-8158 / 8231-8241): per ray k (angle k·π/16) the hit of the least t, a hit within 3 px
    of it nearer to the camera (less z) winning. segs: [(tag, a, b)]. Returns [(tag, t, z, u, ang)], one per ray that
    hits, in the order of k.

    The original's loop is ray by ray over the segments; here segment by segment over the rays, each ray's segments
    still in their order (the tie of 3 px depends on it), and every operation the original does on a pair that can
    hit is the same IEEE operation on the same operands (tests/helpers/fillref.py keeps the original's loop; the
    judge: tests/test_fillat_parity.py, TestDirect). What a segment needs whatever the ray is (ex, ey, wx, wy, the
    numerator of t: wx·ey − wy·ex, b.z − a.z) is computed once. A pair is left out before the division only where the
    original's tests leave it out: |den| < 1e-9, or t's numerator 0 (t = ±0) or of the sign opposite to den's (t < 0,
    or −0 when it underflows) — so a finite numerator > 0 needs den >= 1e-9 and one < 0 needs den <= −1e-9; a NaN or
    infinite numerator takes the original's way (±inf / ±inf is NaN, which the tests let through). u is computed only past `t <= 0` (it only rejects)."""
    # ours (lot 1.2a, the owner's decision 13): the 3 px of the tie are the browser's CSS pixels in the original, which
    # follow the page's zoom; here `px`, the interface scale (1.0: the original's 3, the same comparisons bit for bit)
    mx = m[0]; my = m[1]; par = PARALLEL; npar = -PARALLEL; tie = TIE_PX * px; rays = _RAYS; inf = math.inf
    best: list[tuple | None] = [None] * 32
    for tag, a, b in segs:
        ax = a[0]; ay = a[1]; ex = b[0] - ax; ey = b[1] - ay
        wx = ax - mx; wy = ay - my; num = wx * ey - wy * ex; az = a[2]; dz = b[2] - az
        if 0 < num < inf:
            for k, dx, dy, ang in rays:
                den = dx * ey - dy * ex
                if den < par:                                  # (parallel, or den <= -1e-9: t < 0)
                    continue
                t = num / den
                if t <= 0:
                    continue
                u = (wx * dy - wy * dx) / den
                if u < 0 or u > 1:
                    continue
                z = az + dz * u
                bk = best[k]
                if bk is None or t < bk[1] - tie or (abs(t - bk[1]) <= tie and z < bk[2]):
                    best[k] = (tag, t, z, u, ang)
        elif -inf < num < 0:
            for k, dx, dy, ang in rays:
                den = dx * ey - dy * ex
                if den > npar:                                 # (parallel, or den >= 1e-9: t < 0)
                    continue
                t = num / den
                if t <= 0:
                    continue
                u = (wx * dy - wy * dx) / den
                if u < 0 or u > 1:
                    continue
                z = az + dz * u
                bk = best[k]
                if bk is None or t < bk[1] - tie or (abs(t - bk[1]) <= tie and z < bk[2]):
                    best[k] = (tag, t, z, u, ang)
        elif num == 0:
            continue                                                # (t = ±0 on every ray: t <= 0; den is finite then)
        else:                                                       # (NaN, ±inf: the original's tests as they are — inf / ±inf is NaN)
            for k, dx, dy, ang in rays:
                den = dx * ey - dy * ex
                if abs(den) < par:
                    continue
                t = num / den; u = (wx * dy - wy * dx) / den
                if t <= 0 or u < 0 or u > 1:
                    continue
                z = az + dz * u
                bk = best[k]
                if bk is None or t < bk[1] - tie or (abs(t - bk[1]) <= tie and z < bk[2]):
                    best[k] = (tag, t, z, u, ang)
    return [h for h in best if h is not None]


def fillHits(P, m, screen, px=1.0):
    """port of fillHits (hair-modeler.clean.html:9214): the rays' hits on the fillCurves — [{'c', 'si' (sample segment +
    the fraction along it), 't' (pixels from m), 'z', 'ang'}]"""
    segs = []
    for c in fillCurves(P):
        for i, a, b in _screenSegs([s.p for s in c.samples], screen):
            segs.append(((c, i), a, b))
    return [{'c': tag[0], 'si': tag[1] + u, 't': t, 'z': z, 'ang': ang} for tag, t, z, u, ang in _cast(segs, m, px)]


def fillPlanAt(P, m, screen, px=1.0):
    """port of fillPlanAt (hair-modeler.clean.html:9232): the area at viewport point m — {'sides': [stretches,
    stretches], 'zone'} (stretches: [{'c', 'i0', 'i1'}], indices into c.anchors, either way) or None"""
    H = fillHits(P, m, screen, px); best = None
    for i in range(len(H)):
        for j in range(i + 1, len(H)):
            da = abs(H[i]['ang'] - H[j]['ang']); dd = min(da, 2 * math.pi - da)
            if dd < 2 * math.pi / 3 or H[i]['c'] is H[j]['c']:
                continue
            sc = H[i]['t'] + H[j]['t'] + 0.001 * (H[i]['z'] + H[j]['z'])
            if best is None or sc < best[2]:
                best = (H[i], H[j], sc)
    if best is None:
        return None
    lst = fillCurves(P); A = best[0]['c']; B = best[1]['c']

    def segOf(c, si):
        return min(len(c.anchors) - 2, c.samples[min(math.floor(si), len(c.samples) - 1)].seg)
    if xShared(A, B):                                               # inside one X pair: the zone the pointer is in
        Z = pairZones(A, B); sg = segOf(A, best[0]['si'])
        z = next((z for z in Z['zones'] if sg >= min(z['a']) and sg < max(z['a'])), None)
        if z is None:
            return None
        return {'sides': [[{'c': A, 'i0': z['a'][0], 'i1': z['a'][1]}], [{'c': B, 'i0': z['b'][0], 'i1': z['b'][1]}]], 'zone': True}

    def side(c, other):
        p = xPartner(c, lst)
        return pairEnvelope(c, p, pairZones(c, p), nearCurves([other])) if p is not None and p is not other else [{'c': c, 'i0': 0, 'i1': len(c.anchors) - 1}]
    return {'sides': [side(A, B), side(B, A)], 'zone': False}


def stretchPts(q):
    # port of stretchPts (hair-modeler.clean.html:9250): the sample points of a stretch (24 a segment), in its direction
    c = q['c']; i0 = q['i0']; i1 = q['i1']
    lo = min(i0, i1) * PER; hi = min(max(i0, i1) * PER, len(c.samples) - 1)
    Pp = [s.p for s in c.samples[lo:hi + 1]]
    return Pp if i0 <= i1 else Pp[::-1]


def _segments(lines):
    arr = []
    for Pp in lines:
        for i in range(len(Pp) - 1):
            a = Pp[i]; b = Pp[i + 1]
            arr.extend((a.x, a.y, a.z, b.x, b.y, b.z))
    return arr


def planOutline(plan):
    """the outline showFillViz (hair-modeler.clean.html:9251) shows for a plan: each side's stretches run one after the
    other; [] when there is nothing to show (the original hides the line then)"""
    if plan is None:
        return []
    return _segments([[p for q in side for p in stretchPts(q)] for side in plan['sides']])


def holeOutline(hole):
    # the outline of a hole (startFillAt, hair-modeler.clean.html:8283): its free stretches' points
    return _segments(hole['pts'])


def holeAt(P, m, screen, stats=None, px=1.0):
    """port of holeAt (hair-modeler.clean.html:9274): the hole under the pointer — a closed loop of free stretches
    (stretches of curves, anchor to anchor, that exactly one shown wall runs along: the rim of a hole). Rays from the
    pointer stop at the first free stretch they meet (whatever curve it lies on — a junction / Fill-hole curve too);
    the closed loop of free stretches through the nearest hit taking in the most hits (then the shortest) is the hole.
    {'P' (anchors round it), 'V' (the curve of each step), 'parent', 'pts'} or None. The search is bounded as there:
    200000 calls of the walk, paths up to 300 anchors. `stats` (a dict, ours): gets 'steps', the walk's calls."""
    by = {}                                                         # (Map, insertion order) key -> {a, b, walls, cs}

    def key(a, b):
        return f'{a.id}-{b.id}' if a.id < b.id else f'{b.id}-{a.id}'
    for s in P.surfaces():
        if not isVisible(P, s):
            continue
        for iid in (s.a, s.b):
            c = P.items.get(iid)
            if c is None or c.type != 'curve':
                continue
            for k in range(len(c.anchors) - 1):
                a = c.anchors[k]; b = c.anchors[k + 1]
                if a is b:
                    continue
                kk = key(a, b)
                if kk not in by:
                    by[kk] = {'a': a, 'b': b, 'walls': {}, 'cs': {}, 'pts': None}   # (Sets: dicts, insertion order)
                e = by[kk]; e['walls'][s.id] = None; e['cs'][c] = None
    free = [e for e in by.values() if len(e['walls']) == 1]
    if not free:
        return None

    def segPts(c, e):                                               # the stretch's points along curve c
        A = c.anchors; k = -1
        for i in range(len(A)):
            nx = A[i + 1] if i + 1 < len(A) else None
            if (A[i] is e['a'] and nx is e['b']) or (A[i] is e['b'] and nx is e['a']):
                k = i; break
        if k < 0:
            return None
        Pp = [A[k].pos, *(s.p for s in c.samples if s.seg == k), A[k + 1].pos]
        return Pp if A[k] is e['a'] else Pp[::-1]
    segs = []
    for e in free:
        c = next((x for x in e['cs'] if len(x.samples) > 1), None)
        if c is None:
            continue
        e['pts'] = segPts(c, e)
        if e['pts'] is None:
            continue
        for _i, a, b in _screenSegs(e['pts'], screen):
            segs.append((e, a, b))
    hits = [{'e': tag, 't': t, 'z': z} for tag, t, z, _u, _ang in _cast(segs, m, px)]
    if len(hits) < 16:
        return None                                                 # (not surrounded by free stretches)
    hitSet = {id(h['e']) for h in hits}
    first = hits[0]
    for h in hits[1:]:
        if h['t'] < first['t']:
            first = h
    fe = first['e']
    # closed loops through the nearest hit: paths from its one end to the other along free stretches (bounded search)
    adj = {}
    for e in free:
        for x, y in ((e['a'], e['b']), (e['b'], e['a'])):
            adj.setdefault(x, []).append((y, e))
    best: list[dict | None] = [None]; steps = [0]

    def walk(v, path, es, used):
        steps[0] += 1
        if steps[0] > 200000:
            return
        for to, e in adj.get(v, ()):
            if id(e) in used or e is fe:
                continue
            if to is fe['a']:
                loop = [*es, e, fe]; score = sum(1 for x in loop if id(x) in hitSet); L = 0
                for x in loop:
                    L = L + x['a'].pos.distanceTo(x['b'].pos)
                bl = best[0]
                if bl is None or score > bl['score'] or (score == bl['score'] and L < bl['L']):
                    best[0] = {'P': list(path), 'es': loop, 'score': score, 'L': L}   # ([...path, to].slice(0, -1))
                continue
            if any(q is to for q in path) or len(path) > 300:
                continue
            used.add(id(e)); path.append(to); es.append(e); walk(to, path, es, used); path.pop(); es.pop(); used.discard(id(e))
    walk(fe['b'], [fe['a'], fe['b']], [], set())
    if stats is not None:
        stats['steps'] = steps[0]
    bl = best[0]
    if bl is None or len(bl['P']) < 3:
        return None
    # the curve of each step: the one the step before had, while it runs on (fewer corners), else any (an ordinary first)
    Pl = bl['P']; n = len(Pl); V = []
    for i in range(n):
        a = Pl[i]; b = Pl[(i + 1) % n]; e = by[key(a, b)]
        cs = sorted(e['cs'], key=lambda x: 1 if truthy(x.junc) else 0)   # (stable, as Array.prototype.sort)
        V.append(V[i - 1] if i and any(x is V[i - 1] for x in cs) else cs[0])
    if V[n - 1] is not V[0] and V[0] in by[key(Pl[n - 1], Pl[0])]['cs']:
        V[n - 1] = V[0]
    pts = [e['pts'] if e['pts'] else [e['a'].pos, e['b'].pos] for e in bl['es']]
    return {'P': Pl, 'V': V, 'parent': P.items[next(iter(fe['walls']))].parent, 'pts': pts}


def planNames(plan):
    # the names in the confirmation pie's title (8293): a side that is one whole curve — its name, else its curves' names
    out = []
    for sd in plan['sides']:
        if len(sd) == 1 and min(sd[0]['i0'], sd[0]['i1']) == 0 and max(sd[0]['i0'], sd[0]['i1']) == len(sd[0]['c'].anchors) - 1:
            out.append(sd[0]['c'].name)
        else:
            out.append(' + '.join(dict.fromkeys(q['c'].name for q in sd)))
    return out


def startFillAt(P, m, screen, px=1.0):
    """the decision of startFillAt (hair-modeler.clean.html:9345) past its pane check (OPEN_OVER is the caller's, when
    the pie was not opened over a viewport): the dirty curves rebuilt (8278: right after a load or an undo they are not
    sampled), then a hole round the pointer, else the area's plan. Returns {'hole', 'plan' (one of them, or neither),
    'title' (the confirmation pie's, or None), 'outline' (the flat segments to show, [] for none), 'toast' (NO_OUTLINE
    when there is nothing, else None)}. Nothing is changed but the curves' samples."""
    from .curves import rebuildCurve
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)
    # (holeAt projects only the free stretches' points, fillPlanAt the shown curves' samples: few in common — a memo
    # of the projections shared by the two cost more than it saved, 15-25 ms against the hole search's 5 on the example)
    # a hole in the walls round the pointer: filled like Fill hole (two junction curves along its rim, a wall between)
    hole = holeAt(P, m, screen, px=px)
    if hole is not None:
        names = dict.fromkeys(c.name for c in hole['V'])
        return {'hole': hole, 'plan': None, 'title': f'Fill the hole: {" · ".join(names)}', 'outline': holeOutline(hole), 'toast': None}
    plan = fillPlanAt(P, m, screen, px)
    if plan is None:
        return {'hole': None, 'plan': None, 'title': None, 'outline': [], 'toast': NO_OUTLINE}
    names = planNames(plan)
    return {'hole': None, 'plan': plan, 'title': f'Fill: {names[0]} ↔ {names[1]}', 'outline': planOutline(plan), 'toast': None}


def runFillPlan(P, plan, regen=None):
    """port of runFillPlan (hair-modeler.clean.html:9257): the area of a plan gets its wall. Returns {'toast', 'sel' (the
    new S.sel: [ids], or None when it stays), 'walls' (made)}. An edge curve pathCurve makes stays even when nothing
    else happens (8190-8191: the original leaves it; dropOrphanServiceCurves takes it later)."""
    parent = plan['sides'][0][0]['c'].parent
    x = pathCurve(P, plan['sides'][0], parent); y = pathCurve(P, plan['sides'][1], parent)
    if x is y:
        return {'toast': NOTHING, 'sel': None, 'walls': []}
    if not truthy(x.path) and not truthy(y.path):                   # two plain curves — the usual way
        made, _msg = createSurfaces(P, [x, y], False)               # (its own toast is covered by this one; S.sel only when made)
        return {'toast': filledBetween(x, y), 'sel': [s.id for s in made] if made else None, 'walls': made}
    s = makeSurface(P, x, y)
    if s is None:
        return {'toast': FILLED_ALREADY, 'sel': None, 'walls': []}
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    if regen is not None:
        regen()                                                     # (if (S.conv) regenConversion(), 8195)
    return {'toast': FILLED_ZONE if plan['zone'] else FILLED_OUTLINE, 'sel': [s.id], 'walls': [s]}


def fillFound(P, found, regen=None):
    """the confirmation pie's "✔ Fill the highlighted outline" for what startFillAt found: a hole — fillCycle (9356,
    3358: its toast, the wall selected), a plan — runFillPlan. Same return as runFillPlan. Neither (startFillAt found
    no outline: the original opens no pie then, 8291) — nothing is made, the toast NO_OUTLINE."""
    hole = found.get('hole')
    if hole is None and found.get('plan') is None:
        return {'toast': NO_OUTLINE, 'sel': None, 'walls': []}
    if hole is not None:
        s, corners = fillCycle(P, hole['P'], hole['V'], hole['parent'])
        if regen is not None:
            regen()                                                 # (fillCycle: if (S.conv) regenConversion(), 3380)
        return {'toast': holeFilledMessage(corners), 'sel': [s.id], 'walls': [s]}
    return runFillPlan(P, found['plan'], regen)
