# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Cross-sections of a converted modifier's lock (1.0.1): the curves `para` made by Convert to curves & walls, every
anchor with its section number (a.sec, file key `sc`). Pure Python, no bpy.

  lockSecs (1435)          anchor id -> section number of the lock's curves, or None
  curveSecAt (1464)        the section number at a point of one curve
  resampleParallel (1473)  the lock's curves resampled by section (buildRowTables' `para` branch, 2601-2619)

lockSecs and curveSecAt lived in scissors.py (3.8a, the wall cut of such a lock); they moved here in 4.1 so that the
row tables (rows.py) read them without importing the scissors (which import edit / weld)."""
import math
from functools import cmp_to_key

from .js import clamp, js_max, js_or

INF = math.inf


def _cmp(v):
    # a comparator's result as Array.prototype.sort reads it (only the sign; NaN as 0)
    return -1 if v < 0 else 1 if v > 0 else 0


# Cross-sections of a converted modifier's lock (c.para curves): every anchor has a section number (a.sec — anchor k of
# the edges made by Convert is section k, a cut's anchors get the section they lie in). Anchors without one (put in
# later — RMB, a cut's end, Insert…) lie between their curve's neighbours that have one: by arc length. Files from
# before a.sec: the curves with the most common anchor count number theirs by index. → Map anchor → section, or null
# when the curves don't make one lock of sections (not increasing along a curve, different first / last section).
def lockSecs(cs):
    """port of lockSecs (hair-modeler.clean.html:1435): anchor id -> section number, or None when the curves don't
    make one lock of sections (not increasing along a curve, different first / last section)"""
    sec = {}
    for c in cs:
        for a in c.anchors:
            if a.sec is not None:
                sec[a.id] = a.sec
    if not sec:
        cnt = {}
        for c in cs:
            cnt[len(c.anchors)] = cnt.get(len(c.anchors), 0) + 1
        n = sorted(cnt.items(), key=cmp_to_key(lambda x, y: _cmp((y[1] - x[1]) or (y[0] - x[0]))))[0][0]
        for c in cs:
            if len(c.anchors) == n:
                for k, a in enumerate(c.anchors):
                    if a.id not in sec:
                        sec[a.id] = k
    for c in cs:
        A = c.anchors
        if all(a.id in sec for a in A):
            continue
        if c.arc is None or len(c.samples) != (len(A) - 1) * 24 + 1:
            return None
        for k in range(len(A)):
            if A[k].id in sec:
                continue
            i = k - 1; j = k + 1
            while i >= 0 and A[i].id not in sec:
                i -= 1
            while j < len(A) and A[j].id not in sec:
                j += 1
            if i < 0 or j >= len(A):
                return None
            si = sec[A[i].id]; sj = sec[A[j].id]
            ak = c.arc[k * 24]; ai = c.arc[i * 24]; aj = c.arc[j * 24]
            sec[A[k].id] = si + (sj - si) * (ak - ai) / js_or(aj - ai, 1)
    f = cs[0].anchors; s0 = sec.get(f[0].id); s1 = sec.get(f[len(f) - 1].id)
    for c in cs:
        S = [sec.get(a.id) for a in c.anchors]
        if abs(S[0] - s0) > 1e-3 or abs(S[len(S) - 1] - s1) > 1e-3:
            return None
        for k in range(1, len(S)):
            if not (S[k] > S[k - 1]):
                return None
    return sec


def curveSecAt(c, sec, p):
    # port of curveSecAt (hair-modeler.clean.html:1464): the section number at point p of curve c (its nearest sample)
    bi = 0; bd = INF
    for i, s in enumerate(c.samples):
        d = s.p.distanceToSquared(p)
        if d < bd:
            bd = d; bi = i
    k = min(c.samples[bi].seg, len(c.anchors) - 2); a0 = c.arc[k * 24]; a1 = c.arc[(k + 1) * 24]
    s0 = sec[c.anchors[k].id]; s1 = sec[c.anchors[k + 1].id]
    return s0 + (s1 - s0) * clamp((c.arc[bi] - a0) / js_or(a1 - a0, 1), 0, 1)


# The curves of such a lock, n points each, resampled by section: point i is the same cross-section on every curve
# (inside a segment each curve goes by its own arc length). The spacing follows the mean arc length per section.
# (Plain resample by arc length slides the edges of a curl apart: the outer edge of a turn is far longer than the inner.)
def resampleParallel(cs, n, sec):
    """port of resampleParallel (hair-modeler.clean.html:1473). `sec`: lockSecs(cs) (anchor id -> section).

    The original groups the samples into a sparse array by segment (`out[s.seg]`); forEach and the sum of the
    segment lengths skip its holes, map keeps them: a hole is None here, with the same skips."""
    per = []
    for c in cs:
        out = []
        for s in c.samples:                                         # (out[s.seg] = out[s.seg] || []).push(s.p)
            while len(out) <= s.seg:
                out.append(None)
            if out[s.seg] is None:
                out[s.seg] = []
            out[s.seg].append(s.p)
        for si, Pp in enumerate(out):                               # a segment ends where the next starts
            if Pp is None:
                continue
            nx = out[si + 1] if si + 1 < len(out) else None
            if nx:
                Pp.append(nx[0])
        segs = []
        for Pp in out:
            if Pp is None:
                segs.append(None); continue
            L = [0]
            for i in range(1, len(Pp)):
                L.append(L[i - 1] + Pp[i].distanceTo(Pp[i - 1]))
            segs.append((Pp, L))
        C = [0]
        for g in segs:
            if g is not None:
                C.append(C[len(C) - 1] + g[1][len(g[1]) - 1])
        per.append((segs, C, [sec.get(a.id) for a in c.anchors]))

    def where(q, s):
        S = q[2]; k = 0
        while k < len(S) - 2 and S[k + 1] < s:
            k += 1
        return k, clamp((s - S[k]) / js_or(S[k + 1] - S[k], 1), 0, 1)

    def length(q, s):
        k, t = where(q, s); C = q[1]
        return C[k] + t * (C[k + 1] - C[k])

    def at(g, t):
        Pp, L = g
        if len(Pp) < 2:
            return Pp[0].clone()
        d = t * L[len(L) - 1]; j = 0
        while j < len(Pp) - 2 and L[j + 1] < d:
            j += 1
        sl = L[j + 1] - L[j]
        return Pp[j].clone().lerp(Pp[j + 1], clamp((d - L[j]) / sl, 0, 1) if sl > 0 else 0)
    # mean arc length against the section, finely sampled, then read back at n even steps
    S = per[0][2]; s0 = S[0]; s1 = S[len(S) - 1]; K = js_max(400, n * 4); Ls = [0]; Ss = [s0]
    for i in range(1, K + 1):
        s = s0 + (s1 - s0) * i / K; Ss.append(s)
        acc = 0
        for q in per:                                               # per.reduce((a, q) => a + len(q, s), 0), in order
            acc = acc + length(q, s)
        Ls.append(acc / len(per))
    tot = js_or(Ls[K], 1e-9); out = [[] for _ in cs]
    j = 0
    for i in range(n):
        d = tot * i / (n - 1)
        while j < K - 1 and Ls[j + 1] < d:
            j += 1
        s = Ss[j] + (Ss[j + 1] - Ss[j]) * clamp((d - Ls[j]) / js_or(Ls[j + 1] - Ls[j], 1), 0, 1)
        for ci, q in enumerate(per):
            k, t = where(q, s)
            out[ci].append(at(q[0][k], t))
    return out
