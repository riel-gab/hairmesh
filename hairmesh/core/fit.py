# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""fitPolyline (hair-modeler.clean.html:3750): a polyline of points -> as few anchors as keep the curve on it. Pure
Python, no project: Path -> curve (the Edges) and the Freehand of the Draw page both go through it. Line numbers refer to
reference/hair-modeler.clean.html, 1.0.1."""
import math

from .curves import sampleCurve
from .js import clamp, js_max, js_min, js_round
from .model import Anchor, Curve

FIT_TOL = 0.003                                                     # tol = L * 0.003 (3757)
_SHARP = 50 * (math.pi / 180)                                       # THREE.MathUtils.degToRad(50)


class _NoProject:
    """the project of fitPolyline's trial curve: its anchors are its own, no other curve uses them (3769)"""
    @staticmethod
    def anchor_curves():
        return {}

    @staticmethod
    def curves():
        return ()


def segDist3(p, a, b):
    # port of segDist3 (hair-modeler.clean.html:3659)
    ab = b.clone().sub(a); l2 = ab.lengthSq()
    t = clamp(p.clone().sub(a).dot(ab) / l2, 0, 1) if l2 > 0 else 0
    return p.distanceTo(a.clone().addScaledVector(ab, t))


def fitPolyline(pts, closed, fit_tol=FIT_TOL):
    """port of fitPolyline (hair-modeler.clean.html:3750): [(the point, sharp)], clones of points of `pts`; a sharp one
    (the polyline turns by more than 50 degrees there) comes back with sharp True: smoothness 0, a corner instead of an
    overshooting arc. fit_tol (ours, the Freehand's Simplify): the tolerance's share of the length; the default is the
    original's."""
    P = [*pts, pts[0]] if closed else pts
    N = len(P)

    def sharpAt(i):
        a = (i - 1 + N - 1) % (N - 1) if closed else i - 1
        b = (i + 1) % (N - 1) if closed else i + 1
        if not closed and (i == 0 or i == N - 1):
            return False
        u = P[i].clone().sub(P[a]); w = P[b].clone().sub(P[i])
        return u.lengthSq() > 0 and w.lengthSq() > 0 and u.angleTo(w) > _SHARP

    def mark(p, i):
        return p.clone(), sharpAt(i)

    if N <= 2 or (closed and N <= 4):
        return [mark(p, i) for i, p in enumerate(pts)]
    L = 0
    for i in range(1, N):
        L += P[i].distanceTo(P[i - 1])
    tol = js_max(L * fit_tol, 1e-7)
    keep = bytearray(N); keep[0] = keep[N - 1] = 1
    if closed:
        keep[js_round((N - 1) / 3)] = 1
        keep[js_round(2 * (N - 1) / 3)] = 1
    for i in range(N - 1):
        if sharpAt(i):
            keep[i] = 1
    if closed and keep[0]:
        keep[N - 1] = 1

    def kept():
        return [i for i in range(N) if keep[i]]

    def dp(i, j):                                                   # (the original's recursion on a stack: a long stroke would pass Python's limit; each span is independent)
        stack = [(i, j)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            k = -1; md = tol
            for q in range(i + 1, j):
                d = segDist3(P[q], P[i], P[j])
                if d > md:
                    md = d; k = q
            if k >= 0:
                keep[k] = 1
                stack.append((i, k)); stack.append((k, j))

    ids = kept()
    for s in range(len(ids) - 1):
        dp(ids[s], ids[s + 1])
    for _iter in range(12):
        ids = kept(); lst = ids[:-1] if closed else ids
        c = Curve(id=0, type='curve', name='', anchors=[Anchor(id=-1 - k, pos=P[i], smooth=0 if sharpAt(i) else 1) for k, i in enumerate(lst)], closed=closed, round=0, fair=0)
        sm = sampleCurve(_NoProject, c, 12)
        added = False
        for s in range(len(ids) - 1):
            if ids[s + 1] - ids[s] < 2:
                continue
            seg = []
            for k, x in enumerate(sm):
                if x.seg == s:
                    seg.append(x.p)
                    if k + 1 < len(sm) and sm[k + 1].seg != s:
                        seg.append(sm[k + 1].p)
            if s == len(ids) - 2:
                seg.append(sm[-1].p)
            k = -1; md = tol * 1.5
            for q in range(ids[s] + 1, ids[s + 1]):
                d = math.inf
                for j in range(len(seg) - 1):
                    d = js_min(d, segDist3(P[q], seg[j], seg[j + 1]))
                if d > md:
                    md = d; k = q
            if k >= 0:
                keep[k] = 1; added = True
        if not added:
            return [mark(P[i], i) for i in lst]
    return [mark(P[i], i) for i in range(N) if keep[i] and (not closed or i < N - 1)]
