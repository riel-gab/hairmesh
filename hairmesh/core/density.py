# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Density modifier (item type 'tip', hair-modeler.clean.html:1438-1491 in 1.0; 1505-1586 in 1.0.1): where its mask is painted along the
curves of a lock, the loop spacing is divided by 1 + (dens - 1) x weight. The weights are kept in Float32Arrays by
the original, so every stored value is rounded to float32 here too."""
import math

from .geom import pointOnCurve
from .headobj import float32
from .js import clamp, js_max, js_or, nullish
from .trace import br as _br

MASK_N = 96                                                         # mask weights per curve (hair-modeler.clean.html:5937)
TIP_DEFAULTS = {'dens': 1.5, 'v': 2}


def tipOf(P, L):
    # port of tipOf (hair-modeler.clean.html:1441): the nearest Density item up the hierarchy
    k = None
    g = P.parentOf(L.cs[0])
    while g and not k:
        if g.type == 'group':
            k = next((x for x in (P.items.get(i) for i in g.children) if x and x.type == 'tip'), None)
        g = P.parentOf(g)
    return k


# does the zone sit at the spine's end (the curves' last anchors are nearer to it)
def tipAtEnd(L):
    # port of tipAtEnd (1447)
    s0 = L.sp[0]; s1 = L.sp[len(L.sp) - 1]
    vote = 0
    for c in L.cs:
        e = c.anchors[len(c.anchors) - 1] if c.anchors else None
        if e:
            vote += 1 if e.pos.distanceTo(s1) < e.pos.distanceTo(s0) else -1
    return vote >= 0


# the mask weight of every curve of the lock at each spine point (L.Ps[ci][i] <-> the nearest point of curve ci)
def tipCurveMask(W, c, P):
    # port of tipCurveMask (1454); returns float32-rounded weights
    out = [0.0] * len(P); sm = c.samples; tot = js_or(c.arc[len(c.arc) - 1], 1)
    for i in range(len(P)):
        bj = 0; bd = math.inf; q = P[i]; qx = q.x; qy = q.y; qz = q.z
        for j in range(len(sm)):                                    # sm[j].p.distanceToSquared(P[i]), on scalars
            s = sm[j].p; dx = s.x - qx; dy = s.y - qy; dz = s.z - qz
            d = dx * dx + dy * dy + dz * dz
            if d < bd:
                bd = d; bj = j
        f = c.arc[bj] / tot * (MASK_N - 1); a = math.floor(f); b = min(a + 1, MASK_N - 1)
        out[i] = float32(W[a] + (W[b] - W[a]) * (f - a))
    return out


def tipMaskW(k, L):
    # port of tipMaskW (1463): the strongest curve's weight at every spine point
    if not k.mask:
        return None
    out = None
    for ci, c in enumerate(L.cs):
        W = k.mask.get(c.id)
        if not W or not c.arc or len(c.samples) < 2 or not any(v > 0 for v in W):
            continue
        m = tipCurveMask(W, c, L.Ps[ci])
        if out is None:
            out = [0.0] * len(m)
        for i in range(len(m)):
            out[i] = max(out[i], m[i])                              # both float32 values: the max is one of them
    return out


# an old Tip item's weights along the spine: its tip zone, minus its mask
def tipLegacyW(k, L):
    # port of tipLegacyW (1474)
    sp = L.sp; n = len(sp); r = clamp(js_or(k.p.get('radius'), 0.3), 0.01, 1); atEnd = tipAtEnd(L); old = tipMaskW(k, L)
    D = [0]
    for i in range(1, n):
        D.append(D[i - 1] + sp[i].distanceTo(sp[i - 1]))
    tot = js_or(D[n - 1], 1); out = [0.0] * n
    for i in range(n):
        x = D[i] / tot if atEnd else 1 - D[i] / tot; t = clamp((x - (1 - r)) / r, 0, 1)
        out[i] = float32(t * t * (3 - 2 * t) * (1 - (old[i] if old else 0)))
    return out


def lockTip(P, L):
    # port of lockTip (hair-modeler.clean.html:1484): { mw, dens } of the lock, or None
    k = tipOf(P, L)
    if not k or k.visible is False or k.p.get('dens') == 1:
        return None
    mw = tipMaskW(k, L) if k.p.get('v') == 2 else tipLegacyW(k, L)
    if mw:
        _br('lockTip.zone')
        return {'mw': mw, 'dens': clamp(js_or(k.p.get('dens'), 1.5), 0.2, 5)}
    return None


# an old Tip item → a Density item: its zone (minus its old mask) painted into the curves' masks — at every mask point
# the weight of the nearest spine point of each lock (the largest where a curve is in several locks)
def tipConvert(P, ctx, k):
    """port of tipConvert (hair-modeler.clean.html:1568), called by buildRowTables for every Tip item without p.v (2728).

    The locks are the ones the build just left in ctx.LOCK_CACHE (the original's LOCK_CACHE.values()). Changes the item
    in place — the mask, p = { dens, v: 2 } (its radius goes), the name "Tip density…" → "Density…" — and returns the
    ids of the curves of its locks (their tables were built with the old zone: a caller that keeps the tables
    rebuilds them, live.row_tables); an empty list when none of its locks is built (yet): the item stays as it is. The mask's keys go in ascending id order, the
    order a JS object with integer keys keeps whatever the insertion order (the file written after has them so).
    (k.maskV, the key of the zone preview, is not ported: no preview reads it.) In Blender the change reaches the
    project text through the next operator (live.update), never through the draw handler or a timer that built it."""
    mask = {}; n = 0; ids = []
    for e in ctx.LOCK_CACHE.values():
        L = e['L']
        if tipOf(P, L) is not k or not all(c.id in P.items for c in L.cs):
            continue
        w = tipLegacyW(k, L); n += 1; ids.extend(c.id for c in L.cs)
        for ci, c in enumerate(L.cs):
            if not c.arc or len(c.samples) < 2:
                continue
            Pp = L.Ps[ci]
            W = mask.get(c.id)
            if W is None:
                W = mask[c.id] = [0] * MASK_N                       # new Array(MASK_N).fill(0)
            for i in range(MASK_N):
                q = pointOnCurve(c, i / (MASK_N - 1)); qx = q.x; qy = q.y; qz = q.z
                bj = 0; bd = math.inf
                for j in range(len(Pp)):                            # P[j].distanceToSquared(q), on scalars
                    s = Pp[j]; dx = s.x - qx; dy = s.y - qy; dz = s.z - qz
                    d = dx * dx + dy * dy + dz * dz
                    if d < bd:
                        bd = d; bj = j
                W[i] = js_max(W[i], w[bj])
    if not n:
        return []                                                   # its locks aren't built (yet) — stays as it is
    _br('tipConvert.converted')
    for cid in [cid for cid, W in mask.items() if not any(v > 0.001 for v in W)]:
        del mask[cid]
    k.mask = {cid: mask[cid] for cid in sorted(mask)} if mask else None
    k.p = {'dens': nullish(k.p.get('dens'), 1.5), 'v': 2}
    if k.name.startswith('Tip density'):
        k.name = 'Density' + k.name[len('Tip density'):]           # k.name.replace(/^Tip density/, 'Density')
    P.gen += 1                                                      # (an item changed: the memos keyed on gen go)
    return ids


def paintTipMask(P, locks, sel_ids, m, op, amt, strength, R, hard, screen, seen, pts_of=None):
    """port of paintTipMask (hair-modeler.clean.html:6585): one dab of the Density brush at m = (x, y) pixels on the
    masks of the Density items (k.mask[curve id], MASK_N weights 0...1) along the curves of the locks under them —
    the selected Density items, else all of them. op: 'paint' | 'erase' (Ctrl) | 'smooth' (Shift); amt: how much the
    dab adds (core/brush: FIRST_DAB on the press, dabAmount on a move); strength: S.tipBrush.strength; R, hard:
    S.brushR, S.brushHard in the pixels of `screen(p)` (toScreen: (x, y, z), or None behind the camera). locks: the
    LOCK_CACHE's locks in its order (a curve in two locks of one Density is painted twice, as there). seen(L, c, i, p):
    1.0.1's occlusion (6591-6599), asked only for a point within R — the point is seen: nothing in front of it on the
    way from the camera, or this lock itself (its walls, caps, converted mesh), never another lock or the head.
    Returns whether a weight changed. (k.maskV, the preview's key, is not ported: brush.draw_tips keys on the masks.)
    pts_of(c): the MASK_N points pointOnCurve(c, i / (MASK_N - 1)) of a curve, when the caller keeps them (the same
    points: the Blender side keeps them while the curve keeps its samples)."""
    from .brush import brushWeight
    from .edit import isVisible
    from .js import js_hypot
    tips = [it for it in P.items.values() if it.type == 'tip']      # tipItems() (6976): items' order
    sel = [P.items[i] for i in sel_ids if i in P.items and P.items[i].type == 'tip']
    ks = sel if sel else tips
    target = 0 if op == 'erase' else strength
    changed = False
    for L in locks:
        k = tipOf(P, L)
        if not k or k not in ks:
            continue
        for c in L.cs:
            if c.arc is None or len(c.samples) < 2 or not isVisible(P, c):
                continue
            W = k.mask.get(c.id) if k.mask else None
            new = W is None
            if new:
                W = [0] * MASK_N                                    # new Array(MASK_N).fill(0)
            src = list(W); ch = False
            pts = pts_of(c) if pts_of is not None else None
            for i in range(MASK_N):
                p = pts[i] if pts is not None else pointOnCurve(c, i / (MASK_N - 1))
                s = screen(p)
                if s is None or s[2] > 1:
                    continue
                d = js_hypot(s[0] - m[0], s[1] - m[1])
                if d >= R or not seen(L, c, i, p):
                    continue
                bw = brushWeight(d, R, hard); old = W[i]; o = 0 if old is None else old
                if op == 'smooth':
                    sm = 0; n = 0
                    for j in range(i - 3, i + 4):
                        if 0 <= j < MASK_N:
                            sm = sm + (0 if src[j] is None else src[j]); n += 1
                    W[i] = o + (sm / n - o) * bw * 0.5
                elif op == 'erase':
                    W[i] = js_max(0, o - bw * amt)
                else:
                    W[i] = clamp(o + target * bw * amt, 0, 1)
                if W[i] != old:
                    ch = True
            if ch:
                if k.mask is None:
                    k.mask = {}
                if new:
                    k.mask[c.id] = W
                    k.mask = {cid: k.mask[cid] for cid in sorted(k.mask)}   # (a JS object keeps integer keys in ascending order)
                changed = True
    return changed
