# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Phase 3.4: welding anchors, Y junctions and the anchor pie of the original (hair-modeler.clean.html:4829-5107,
5487-5625, 7891-7902), the data part. Pure Python, no bpy.

  weld / weldInto / extendYBranches     an anchor dropped on another becomes one shared anchor; a curve END welded to
                                        the MIDDLE of a longer curve is extended along it back to its start (a Y)
  soleEnd / weld_choice / findWeldCandidate   what checkWeld decides after a drag: weld, or (the start of one curve
                                        on the end of another) reverse-and-weld or join into one curve
  joinCurves                            the two curves become one
  unweldAnchor                          Ctrl + click: a selected curve gets its own anchor at the spot
  mergeCoincident                       anchors of the given curves at the same place welded (the curve pie)
  splitAtAnchor                         every curve through the anchor cut in two (identical pieces merged)
  curveBetween / curveThroughAnchors    new curves from the selected anchors
  junctionChoices / setPass             "rotate junction": which pair of directions passes smoothly (a.pass)
  recalcSilent                          handles recalculated without an undo step (part of a weld)
  optimizeAnchors                       anchors removed one by one while the curves keep their shape (1.0.1: `only`)

Selection changes (S.sel, S.asel), undo steps (commit), toasts and the conversion refresh (regenConversion) belong to
the caller; the functions return what the caller needs for them. Ids and names come from Project.uid / nextNumber
as in the original, so the parity scripts of the harness (tools/js-harness/lib/script.mjs: weld, join, unweld, split,
merge, curveBetween, curveThrough, rotateJunction, optimize) give the same project file on both sides."""
import math

from . import model as M
from .curves import bez, curveHandles, markAnchorsDirty, sampleCurve
from .edit import addItem, curvesWith, deleteItem, isRingAnchor, makeCurve, newAnchor, pickHead, removeAnchorFromCurve
from .geom import polyLen
from .hairmesh_io import defaultStep
from .js import at, clamp, index_of, js_hypot, js_max
from .raycast import Ray
from .rowbase import convOf
from .vec3 import V3

WELD_PX = 14                                                        # findWeldCandidate (4862): another anchor this close on screen


# ======================= welding (4829-4871) =======================

def sharesCurve(P, a, b):
    # port of sharesCurve (hair-modeler.clean.html:4860)
    return any(a in c.anchors and b in c.anchors for c in P.curves())


def findWeldCandidate(P, a, anchor_list, screen):
    """port of findWeldCandidate (hair-modeler.clean.html:4861). `anchor_list` is S.anchorList (edit.anchorList),
    `screen(pos)` the projector (toScreen: (x, y[, z]) or None when the point is behind the camera, where the
    original's toScreen would still give a number; such anchors cannot be candidates here). The nearest other anchor
    within 14 px that shares no curve with `a`, or None. (The two filters run only for an anchor near enough to win —
    the same best, they only skip — lot 4.7b item 5: sharesCurve walks every curve, for every shown anchor, on every
    move of a drag.)"""
    if isRingAnchor(P, a):
        return None
    sa = screen(a.pos)
    if sa is None:
        return None
    best = None; bd = WELD_PX
    for b in anchor_list:
        if b is a:
            continue
        sb = screen(b.pos)
        if sb is None:
            continue
        d = js_hypot(sa[0] - sb[0], sa[1] - sb[1])
        if d < bd and not sharesCurve(P, a, b) and not isRingAnchor(P, b):
            bd = d; best = b
    return best


def soleEnd(P, x):
    # port of soleEnd (hair-modeler.clean.html:4866): the one open curve anchor x is an end of (and no other curve
    # uses x): (c, start) or None
    us = curvesWith(P, x)
    if len(us) != 1:
        return None
    c = us[0]
    if c.ring or c.closed or c.conn or len(c.anchors) < 2:
        return None
    if c.anchors[0] is x:
        return c, True
    if c.anchors[len(c.anchors) - 1] is x:
        return c, False
    return None


def weld_choice(P, a, b):
    """The decision of checkWeld (hair-modeler.clean.html:4876-4898) once the candidate b is known: ('weld',) for
    the usual pie "Anchors are close", or ('startEnd', st, other) for "Start meets end": the start of one curve
    dropped on the end of another; `st` is the curve whose start it is (reversed before the weld by the first
    option), `other` the one it meets; the second option joins the two into one curve."""
    ea = soleEnd(P, a); eb = soleEnd(P, b)
    if ea and eb and ea[0] is not eb[0] and ea[1] != eb[1]:
        st = ea[0] if ea[1] else eb[0]
        other = (eb if ea[1] else ea)[0]
        return 'startEnd', st, other
    return ('weld',)


def weldInto(P, a, b):
    # port of weldInto (hair-modeler.clean.html:4952): replace anchor a by anchor b in every curve (b becomes the shared one)
    for c in P.curves():
        ch = False
        for k in range(len(c.anchors)):
            if c.anchors[k] is a:
                c.anchors[k] = b; ch = True
                o = c.ovr.get(a.id)
                if o is not None:
                    if b.id not in c.ovr:
                        c.ovr[b.id] = o
                    del c.ovr[a.id]
        if ch:
            c.anchors = [x for k, x in enumerate(c.anchors) if k == 0 or c.anchors[k - 1] is not x]
    P.anchors.pop(a.id, None)                                       # no curve uses a any more (the file only holds the anchors curves use)
    P.gen += 1


def extendYBranches(P, J):
    """port of extendYBranches (hair-modeler.clean.html:4936). Y: when the END of a curve is welded to the MIDDLE of
    a longer curve, the short one is extended along the long one back to its start: both then share the trunk
    (common anchors) up to the fork, so the Y converts as one merged trunk. The trunk side is the one leading away
    from the short curve's free end. Returns the names of the curves extended."""
    out = []
    for c in P.curves():
        if c.ring or c.closed or c.conn or len(c.anchors) < 2:
            continue
        e = 0 if c.anchors[0] is J else 1 if c.anchors[len(c.anchors) - 1] is J else -1
        if e < 0:
            continue
        ln = c.arc[len(c.arc) - 1] if c.arc else 0
        host = None
        for h in P.curves():
            if h is c or h.ring or h.closed or h.conn:
                continue
            i = index_of(h.anchors, J)
            if not (0 < i < len(h.anchors) - 1):
                continue
            if not h.arc or not c.arc or h.arc[len(h.arc) - 1] > ln:
                host = h; break
        if host is None:
            continue
        i = index_of(host.anchors, J)
        free = (c.anchors[len(c.anchors) - 1] if e == 0 else c.anchors[0]).pos
        before = host.anchors[:i]; after = list(reversed(host.anchors[i + 1:]))   # both ordered far end -> J
        side = before if before[0].pos.distanceTo(free) >= after[0].pos.distanceTo(free) else after
        if any(x in c.anchors for x in side):
            continue
        c.anchors = [*side, *c.anchors] if e == 0 else [*c.anchors, *reversed(side)]
        c.dirty = True; out.append(c.name)
    P.gen += 1
    return out


def recalcSilent(P, cs):
    # port of recalcSilent (hair-modeler.clean.html:4813): handles recalculated without the undo step / toast
    lst = list(dict.fromkeys(a for c in cs if c is not None for a in c.anchors)); ids = {a.id for a in lst}
    for c in P.curves():
        for iid in list(c.ovr.keys()):
            if iid in ids:
                del c.ovr[iid]
    for a in lst:
        a.smooth = 1
    markAnchorsDirty(P, lst)


def weld(P, a, b, before=None):
    """port of weld (hair-modeler.clean.html:4900): anchor a goes into b everywhere, Y branches ending at b are
    extended, `before` runs after the weld (recalcSilent of the start + end case), every curve is dirty. Returns the
    names of the extended curves (the toast: "Anchors welded — X extended along the longer curve (Y)"). The caller
    selects b (S.asel = [b]) and commits."""
    weldInto(P, a, b)
    ext = extendYBranches(P, b)
    if before is not None:
        before()
    for c in P.curves():
        c.dirty = True
    P.rev += 1
    return ext


def reverseThenWeld(P, a, b, st, other):
    """The first option of "Start meets end" (4887): st reversed (silently), then the weld, with st and other
    recalculated right away. Returns weld's extended names."""
    from .edit import reverseCurves
    reverseCurves(P, [st])
    return weld(P, a, b, lambda: recalcSilent(P, [st, other]))


def joinCurves(P, a, b):
    """port of joinCurves (hair-modeler.clean.html:4908): anchor a (the end of one curve) dropped on b (the start of
    another, or the other way round): the curve that ends there goes on with the other one's anchors and the other
    one is gone; its walls, modifiers and guide hits move to the joined curve (guide hits by arc length). Falls back
    to a plain weld when the ends do not match. Returns the joined curve (the new selection), or None after the
    fallback (then weld's rules apply: b is the selected anchor)."""
    ea = soleEnd(P, a); eb = soleEnd(P, b)
    if not ea or not eb or ea[0] is eb[0] or ea[1] == eb[1]:
        weld(P, a, b)
        return None
    ce = eb[0] if ea[1] else ea[0]; cs = ea[0] if ea[1] else eb[0]    # ce ends at the joint, cs starts there

    def ln(c):
        return c.arc[len(c.arc) - 1] if c.arc else polyLen([x.pos for x in c.anchors])
    LE = ln(ce); LS = ln(cs); tot = (LE + LS) or 1
    weldInto(P, a, b)                                               # the joint: anchor b
    for g in P.guides():
        if g.kind != 'disc':
            for h in g.hits:
                if h.get('c') == ce.id:
                    h['t'] = h['t'] * LE / tot
                elif h.get('c') == cs.id:
                    h['c'] = ce.id; h['t'] = (LE + h['t'] * LS) / tot
            g.dirty = True
    for c in P.curves():
        for x in c.anchors:
            if x.att and x.att.c == cs.id:
                x.att.c = ce.id; x.att.t = (LE + x.att.t * LS) / tot
    ce.anchors = [*ce.anchors, *cs.anchors[1:]]
    for k, o in cs.ovr.items():
        if k not in ce.ovr:
            ce.ovr[k] = o
    for s in P.surfaces():
        if s.a == cs.id:
            s.a = ce.id
        if s.b == cs.id:
            s.b = ce.id
        s.dirty = True
    own = any(t.src == ce.id for t in P.tubes())
    for t in P.tubes():
        if t.src == cs.id:
            if own:
                deleteItem(P, t.id)
            else:
                t.src = ce.id; t.dirty = True
    deleteItem(P, cs.id)
    _dropDuplicateWalls(P)
    recalcSilent(P, [ce])                                           # a clean curve through the joint
    ce.dirty = True; markAnchorsDirty(P, ce.anchors)
    P.gen += 1; P.rev += 1
    return ce


def _dropDuplicateWalls(P):
    # a wall now between the joined curve and itself / twice (4927, 5055)
    seen = set()
    for s in P.surfaces():
        k = f'{min(s.a, s.b)}_{max(s.a, s.b)}'
        if s.a == s.b or k in seen:
            deleteItem(P, s.id)
        else:
            seen.add(k)


def unweldAnchor(P, a, sel_items):
    """port of unweldAnchor (hair-modeler.clean.html:4843). Ctrl + click on a welded anchor of a SELECTED curve: that
    curve gets its own anchor at the same spot (with the same smoothness and frozen handles); it comes apart from
    the other curves, which stay welded to each other. `sel_items` = S.sel (ids). Returns the curves that got their
    own anchor, or None (-> the usual Ctrl + click) when no selected curve shares that anchor with another curve."""
    users = curvesWith(P, a)
    if len(users) < 2 or isRingAnchor(P, a):
        return None
    sel = [c for c in users if c.id in sel_items]
    if not sel:
        return None
    keep = sel[1:] if len(sel) == len(users) else sel               # all users selected -> the first keeps the old one
    made = []
    for c in keep:
        n = newAnchor(P, a.pos); n.smooth = a.smooth; made.append(n)
        c.anchors = [n if x is a else x for x in c.anchors]
        o = c.ovr.get(a.id)
        if o is not None:
            del c.ovr[a.id]
            c.ovr[n.id] = M.HandleOverride(in_=o.in_.clone() if o.in_ else None, out=o.out.clone() if o.out else None)
    markAnchorsDirty(P, [a, *made])
    P.gen += 1; P.rev += 1
    return keep


def mergeCoincident(P, cs, tol):
    """port of mergeCoincident (hair-modeler.clean.html:4829): anchors of the given curves that sit at the same
    place (within tol) welded into one shared anchor; anchors of one curve are never welded to each other. Returns
    how many were merged (0: "No anchors at identical places")."""
    lst = list(dict.fromkeys(a for c in cs for a in c.anchors))
    n = 0
    for i in range(len(lst)):
        for j in range(i + 1, len(lst)):
            a = lst[i]; b = lst[j]
            if a is None or b is None or a.pos.distanceTo(b.pos) > tol or sharesCurve(P, a, b):
                continue
            weldInto(P, b, a); lst[j] = None; n += 1
    if n:
        for c in P.curves():
            c.dirty = True
        P.rev += 1
    return n


# ======================= junctions (4872-4897, 4977-5016) =======================

def junctionNeighbours(P, a):
    # port of junctionNeighbours (hair-modeler.clean.html:4872): every anchor next to `a` on any curve, by id
    from .curves import junctionNeighbours as jn
    return jn(P, a)


def junctionChoices(P, a):
    """port of junctionChoices (hair-modeler.clean.html:4987), the list part: the pie items "<curve> — smooth
    through" as (label, pair) for every curve using the anchor, and ('↺ Automatic', None) when a.pass is set; empty
    with fewer than 3 directions. setPass applies a choice."""
    nb = junctionNeighbours(P, a)
    if len(nb) < 3:
        return []

    def dr(n):
        return n.pos.clone().sub(a.pos).normalize()
    out = []
    for c in P.curves():
        if c.ring:
            continue
        A = c.anchors; i = index_of(A, a)
        if i < 0:
            continue
        if 0 < i < len(A) - 1:
            pair = [A[i - 1].id, A[i + 1].id]
        else:
            # a curve ending here continues into the straightest other direction (again: the next straightest one)
            n = A[1 if i == 0 else len(A) - 2]; d = dr(n)
            cand = sorted([x for x in nb.values() if x is not n], key=lambda x: dr(x).dot(d))
            if not cand:
                continue
            k = 0
            if a.pass_ and n.id in a.pass_ and cand[0].id in a.pass_:
                k = 1 % len(cand)
            pair = [n.id, cand[k].id]
        out.append((f'{c.name} — smooth through', pair))
    if a.pass_:
        out.append(('↺ Automatic', None))
    return out


def setPass(P, a, pair):
    # the `set` of junctionChoices (4990): a.pass = pair (None: automatic tangents), the junction and its neighbours dirty
    a.pass_ = list(pair) if pair else None
    touched = [a, *junctionNeighbours(P, a).values()]
    markAnchorsDirty(P, touched); P.rev += 1
    return a.pass_


# ======================= split (5021-5064) =======================

def splitAtAnchor(P, J, silent=False):
    """port of splitAtAnchor (hair-modeler.clean.html:5021): every curve running THROUGH the anchor is cut in two
    there. Pieces that come out identical (a common trunk of a Y) become one curve: a Y split at the fork gives 3
    curves, split higher on the trunk a plain curve and a Y. The pieces still meet at the anchor (unweld separates
    them). Walls, guide lines and modifiers follow. Returns the ids of the pieces that exist (the new selection),
    or, silent, the [first, second] piece ids per cut curve; [] when no curve runs through the anchor."""
    cut = [c for c in curvesWith(P, J) if not c.ring and not c.closed and 0 < index_of(c.anchors, J) < len(c.anchors) - 1]
    if not cut:
        return []
    pieces = {}                                                     # curve id -> [first piece id, second piece id]
    for c in cut:
        A = c.anchors; i = index_of(A, J); L = c.arc; tot = (L[len(L) - 1] or 1) if L else 1
        cf = clamp(L[min(i * 24, len(L) - 1)] / tot, 1e-6, 1 - 1e-6) if L else i / (len(A) - 1)
        p1 = A[:i + 1]; p2 = A[i:]; firstKeeps = len(p1) >= len(p2)
        n = makeCurve(P, c.parent); n.round = c.round; n.visible = c.visible; n.name = c.name + ' part'
        n.anchors = p2 if firstKeeps else p1; c.anchors = p1 if firstKeeps else p2
        for aid, o in list(c.ovr.items()):
            if any(x.id == aid for x in n.anchors) and not any(x.id == aid for x in c.anchors):
                n.ovr[aid] = o; del c.ovr[aid]
        o = c.ovr.get(J.id)
        if o is not None:                                           # both pieces keep the cut's handles
            n.ovr[J.id] = M.HandleOverride(in_=o.in_.clone() if o.in_ else None, out=o.out.clone() if o.out else None)
        arr = P.childrenOf(n.parent); arr.remove(n.id); arr.insert(arr.index(c.id) + 1, n.id)
        pieces[c.id] = [c.id, n.id] if firstKeeps else [n.id, c.id]
        for g in P.guides():
            if g.kind != 'disc':
                for h in g.hits:
                    if h.get('c') == c.id:
                        first = h['t'] <= cf; h['c'] = pieces[c.id][0 if first else 1]
                        h['t'] = h['t'] / cf if first else (h['t'] - cf) / (1 - cf); g.dirty = True
    # walls between two cut curves: first pieces together, second pieces together
    for s in list(P.surfaces()):
        pa = pieces.get(s.a); pb = pieces.get(s.b)
        if not pa or not pb:
            continue
        s.a = pa[0]; s.b = pb[0]; s.dirty = True
        addItem(P, 'surface', s.parent, {'a': pa[1], 'b': pb[1], 'p': dict(s.p), 'dirty': True, 'visible': s.visible})
    # identical pieces (a shared trunk) -> one curve
    ids = [i for pr in pieces.values() for i in pr]

    def same(x, y):
        return len(x) == len(y) and (all(a is y[k] for k, a in enumerate(x)) or all(a is y[len(y) - 1 - k] for k, a in enumerate(x)))
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a = P.items.get(ids[i]); b = P.items.get(ids[j])
            if a is None or b is None or not same(a.anchors, b.anchors):
                continue
            for s in P.surfaces():
                if s.a == b.id:
                    s.a = a.id
                if s.b == b.id:
                    s.b = a.id
            for g in P.guides():
                if g.kind != 'disc':
                    for h in g.hits:
                        if h.get('c') == b.id:
                            h['c'] = a.id
            for t in P.tubes():
                if t.src == b.id:
                    t.src = a.id
            deleteItem(P, b.id)
    _dropDuplicateWalls(P)
    out = [i for i in ids if i in P.items]
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    if silent:
        return list(pieces.values())
    return out


# ======================= new curves from anchors (5487-5531) =======================

def curveThroughAnchors(P, lst):
    """port of curveThroughAnchors (hair-modeler.clean.html:5487): a new free curve through the selected anchors in
    the order they were picked: new anchors at their places, welded to nothing, at the top level. Returns the curve,
    or None with fewer than two distinct places ("Pick at least two anchors in different places")."""
    pts = [a for i, a in enumerate(lst) if i == 0 or a.pos.distanceToSquared(lst[i - 1].pos) > 1e-12]
    if len(pts) < 2:
        return None
    c = makeCurve(P, None)
    for a in pts:
        n = newAnchor(P, a.pos.clone()); n.smooth = a.smooth; c.anchors.append(n)
    c.dirty = True
    P.gen += 1; P.rev += 1
    return c


def nearestT(c, p):
    # port of nearestT (hair-modeler.clean.html:7370): arc fraction of the point of curve c nearest to p
    s = c.samples; L = c.arc
    if not L or len(s) < 2:
        return 0
    best = 0; bd = math.inf
    for i in range(len(s) - 1):
        a = s[i].p; ab = s[i + 1].p.clone().sub(a); l2 = ab.lengthSq()
        t = clamp(p.clone().sub(a).dot(ab) / l2, 0, 1) if l2 > 0 else 0
        d = a.clone().addScaledVector(ab, t).distanceToSquared(p)
        if d < bd:
            bd = d; best = (L[i] + t * (L[i + 1] - L[i])) / (L[len(L) - 1] or 1)
    return best


def curveBetween(P, a, b, target_group=None, head_visible=True):
    """port of curveBetween (hair-modeler.clean.html:5496): a new curve between two anchors, welded to both (a middle
    anchor keeps it off the head). As long as the curves it joins, with common trunks: it starts at the root of the
    curve whose anchor lies nearer its root, runs along it to that anchor, across to the other anchor and on along
    the other curve to its end. `target_group` is the original's targetGroup() (the group of the selection), used
    when neither anchor has a hair curve. Returns (curve, [names of the curves whose trunks it shares]), or None
    when a is b."""
    if a is b:
        return None
    host = next((c for c in curvesWith(P, a) if not c.ring), None) or next((c for c in curvesWith(P, b) if not c.ring), None)
    c = makeCurve(P, host.parent if host else target_group)
    mid = a.pos.clone().add(b.pos).multiplyScalar(0.5)
    if P.head is not None and P.head.visible and head_visible:
        d = mid.clone().sub(P.S.center)
        if d.lengthSq() < 1e-12:
            d.set(0, 1, 0)
        d.normalize()
        h = pickHead(P, Ray(mid.clone().addScaledVector(d, P.S.scale), d.clone().negate()))   # raycaster.far = Infinity: the default of edit._raycaster
        if h is not None and h['point'].clone().sub(P.S.center).dot(d) > mid.clone().sub(P.S.center).dot(d):
            mid.copy(h['point']).addScaledVector(d, 0.06 * a.pos.distanceTo(b.pos))
    # an anchor of the lower connector is shared (as with the crown's own curves); on the parting line the new curve
    # gets its own anchor ATTACHED there (Ctrl + drag slides it)

    def end(x):
        h = next((k for k in curvesWith(P, x) if k.conn), None)
        if h is None or h.conn == 'low':
            return x
        n = newAnchor(P, x.pos); n.att = M.Attachment(c=h.id, t=nearestT(h, x.pos))
        return n
    E = {id(a): end(a), id(b): end(b)}

    def hostOf(x):
        return next((k for k in curvesWith(P, x) if not k.ring and not k.conn and not k.closed and not k.junc), None) if E[id(x)] is x else None

    def fr(h, x):
        return index_of(h.anchors, x) / js_max(1, len(h.anchors) - 1) if h is not None else None
    x1, x2 = a, b; h1 = hostOf(a); h2 = hostOf(b)
    f1 = fr(h1, a); f2 = fr(h2, b)
    swap = (f2 < f1) if (h1 and h2) else (f2 <= 0.5) if h2 else (f1 > 0.5) if h1 else False
    if swap:
        x1, x2 = b, a; h1, h2 = h2, h1
    if h1 and h2 and h1 is h2:
        h2 = None                                                   # both on one curve: only the root part
    pre = h1.anchors[:index_of(h1.anchors, x1)] if h1 else []
    post = h2.anchors[index_of(h2.anchors, x2) + 1:] if h2 else []
    if any(x in pre or x is x1 for x in post):
        post = []
    c.anchors = [*pre, E[id(x1)], newAnchor(P, mid), E[id(x2)], *post]; c.dirty = True
    markAnchorsDirty(P, c.anchors)
    P.gen += 1; P.rev += 1
    shared = [n for n, ok in ((h1.name if h1 else None, bool(pre)), (h2.name if h2 else None, bool(post))) if ok]
    return c, shared


# ======================= optimize anchors (5532-5625) =======================

def distToSeg(p, a, b):
    # port of distToSeg (hair-modeler.clean.html:5532)
    ab = b.clone().sub(a); l2 = ab.lengthSq()
    t = clamp(p.clone().sub(a).dot(ab) / l2, 0, 1) if l2 > 0 else 0
    return a.clone().addScaledVector(ab, t).distanceTo(p)


def _cloneOvr(m):
    return {k: M.HandleOverride(in_=o.in_.clone() if o.in_ else None, out=o.out.clone() if o.out else None) for k, o in m.items()}


def optimizeAnchors(P, cs, only=None):
    """port of optimizeAnchors (hair-modeler.clean.html:5546), run to the end (the original slices it with
    longTask so Esc can stop it; here it is synchronous). Removes anchors one by one (the one changing the shape
    least first) while the curve stays within a small tolerance of its original shape (the larger of 0.3 % of its
    length and 3 % of its loop step). Attached anchors, curve ends and junctions stay. An anchor inside a common
    trunk (welded curves: on every curve using it the same neighbours on both sides) goes from all of them at once,
    if each of them keeps its shape; a fork or a crossing (different neighbours) stays. A removal first tries clean
    automatic handles, then frozen handles (as "Delete anchor, keep shape"). `only` (a set of anchors): just these
    may go, the shape still held to the whole curve's tolerance (1.0.1). Returns the number removed."""
    all_ = P.curves()
    # every curve that may change (the given ones and those sharing anchors with them): its shape before, to hold to
    touched = list(dict.fromkeys(cs))
    for c in cs:
        for h in all_:
            if h not in touched and any(a in c.anchors for a in h.anchors):
                touched.append(h)
    info = {}
    for c in touched:
        ref = [s.p for s in sampleCurve(P, c, 8)]
        step = convOf(P, c).get('step') or defaultStep(P)
        info[id(c)] = {'ref': ref, 'tol': js_max(polyLen(ref) * 0.003, step * 0.03), 'refIdx': {id(a): k * 8 for k, a in enumerate(c.anchors)}}

    def localDev(c, i):
        # c.anchors is the trial list (anchor i of the old one removed): the deviation measured around it only
        A = c.anchors; inf = info[id(c)]; ref = inf['ref']; refIdx = inf['refIdx']
        lo = max(0, i - 2); hi = min(len(A) - 1, i + 1)
        H = {k: curveHandles(P, c, k) for k in range(lo, hi + 1)}
        s = []
        for k in range(lo, hi):
            for q in range(13):
                s.append(bez(A[k].pos, H[k]['hout'], H[k + 1]['hin'], A[k + 1].pos, q / 12))
        mx = 0
        for r in range(refIdx[id(A[lo])], refIdx[id(A[hi])] + 1):
            p = ref[r]; d = math.inf; k = 0
            while k < len(s) - 1 and d > 1e-9:
                d = min(d, distToSeg(p, s[k], s[k + 1])); k += 1
            if d > mx:
                mx = d
        return mx

    def groupOf(c, a):
        # the curves anchor a (inner anchor of c) goes from: [c] alone, c with its trunk mates, or None (a junction: stays)
        i = index_of(c.anchors, a); nb = (c.anchors[i - 1], c.anchors[i + 1]); G = [c]
        for h in all_:
            if h is c or a not in h.anchors:
                continue
            k = index_of(h.anchors, a)
            if (h.ring or h.closed or h.junc or h not in touched or k <= 0 or k >= len(h.anchors) - 1
                    or not any(h.anchors[k - 1] is x for x in nb) or not any(h.anchors[k + 1] is x for x in nb)):
                return None
            G.append(h)
        return G

    def trial(G, a, keep):
        # removing a from every curve of G (each one worked out on the state before, so the welded curves stay one):
        # the worst deviation relative to each curve's tolerance, and the resulting lists
        res = []
        for g in G:
            A0 = list(g.anchors); O0 = _cloneOvr(g.ovr); i = index_of(A0, a)
            if keep:
                removeAnchorFromCurve(P, g, a, True)
            else:
                del g.anchors[i]; g.ovr.pop(a.id, None)
            r = {'g': g, 'i': i, 'A': list(g.anchors), 'O': _cloneOvr(g.ovr), 'A0': A0, 'O0': O0}
            g.anchors = A0; g.ovr = O0
            res.append(r)
        for r in res:
            r['g'].anchors = list(r['A']); r['g'].ovr = _cloneOvr(r['O'])
        d = 0
        for r in res:
            d = js_max(d, localDev(r['g'], r['i']) / info[id(r['g'])]['tol'])
        for r in res:
            r['g'].anchors = r['A0']; r['g'].ovr = r['O0']
        return {'d': d, 'keep': keep, 'res': res}
    removed = 0
    for c in cs:
        if c.ring or c.closed or len(c.anchors) < 3:
            continue
        cache = {}                                                  # id(anchor) -> {d, keep, G} (None: can't go)
        while True:
            best = None
            for i in range(1, len(c.anchors) - 1):
                a = c.anchors[i]
                if a.att or (only is not None and a not in only):
                    continue
                if id(a) not in cache:
                    G = groupOf(c, a); r = None
                    if G and all(len(g.anchors) > 2 for g in G):
                        r = trial(G, a, False)
                        if not (r['d'] < 1):
                            r2 = trial(G, a, True); r = r2 if r2['d'] < 1 else None
                    cache[id(a)] = {'d': r['d'], 'keep': r['keep'], 'G': G} if r else None
                r = cache[id(a)]
                if r and (best is None or r['d'] < best['d']):
                    best = {**r, 'a': a}
            if best is None:
                break
            r = trial(best['G'], best['a'], best['keep'])          # apply on the current lists
            for x in r['res']:
                x['g'].anchors = x['A']; x['g'].ovr = x['O']; x['g'].dirty = True
            removed += 1
            i = r['res'][0]['i']
            for k in range(max(0, i - 4), min(len(c.anchors) - 1, i + 3) + 1):
                cache.pop(id(c.anchors[k]), None)
        c.dirty = True
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    return removed

