# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Y junctions of a lock (hair-modeler.clean.html:1513-1546): anchors where the common run of two curves of the
lock ends; a loop goes through each. (junctionsFree, 1547, is never called since 10.10 and is not ported.)"""
from .geom import nearestF
from .js import includes, index_of
from .rowbase import rowPlane
from .trace import br as _br


def lockJunctionRows(ctx, L):
    # port of lockJunctionRows (hair-modeler.clean.html:1523)
    if ctx.ANCHOR_CURVES is None:                                   # (!ANCHOR_CURVES: null, an empty Map is truthy)
        return []
    at_ = {}                                                        # junction anchor -> Map(curve index -> f)
    for c in L.cs:
        A = c.anchors
        for k in range(1, len(A) - 1):
            if A[k] in at_:
                continue
            ds = ctx.ANCHOR_CURVES.get(A[k].id) or []
            if any(d is not c and any(d is x for x in L.cs) and includes(d.anchors, A[k - 1]) != includes(d.anchors, A[k + 1]) for d in ds):
                at_[A[k]] = {}
    out = []
    for a, fix in at_.items():
        for ci, c in enumerate(L.cs):
            k = index_of(c.anchors, a)
            if 0 < k < len(c.anchors) - 1:
                fix[ci] = nearestF(L.Ps[ci], a.pos)
        if not fix:
            continue
        f = nearestF(L.sp, a.pos); pl = rowPlane(ctx.CP, L.sp, f)
        if not pl or f < 0.5 or f > len(L.sp) - 1.5:
            continue
        _br('lockJunctionRows.row')
        out.append({'f': f, 'plane': {'p': a.pos.clone(), 'n': pl['n']}, 'fixed': fix})
    return out
