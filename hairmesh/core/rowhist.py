# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Ours (lot 3.7-estab): the history of the row tables — the LOCK_CACHE entries of buildRowTables kept under a
fingerprint of everything their build read, so that a state the tables were built for once (an undo of a slider, of
an anchor drag, of a deleted curve) gets its entries back instead of building them again.

A lock's entry depends on its component — the locks it shares anchors with, which the incremental build always
rebuilds together (the flood of rows.buildRowTables) — and on what that component reads: the shapes (samples, compared
exactly) and the signatures (scene.tables_signature: what the tables read besides the shapes) of its curves and of
every curve reached from them through shared anchors (extendAlongHost takes the first host in curves() order, so the
order is part of it), of the walls on those curves, of the groups above them (convOf: the conv as the tables see it,
scene.conv_seen), every guide as lockGuideRows reads it for these curves (its signature, normal, ⏻ with the groups
above it, and its hits ON THESE CURVES only — a disc's hits move with every curve that pierces it), and the
project-wide part: the settings and the head height, the default step (usedStep reads the groups), every density
and crown, every Even loops and Proxy shell item and, while one is in effect, the shapes of the locks its sets read
(_evenshell_geometry; a cap, a Fill holes, a Profile modifier and its Extras: read by no table). Two states with the same fingerprint for a component build the same entries for it: the
entries of one are the entries of the other, bit for bit (tests/test_rowhist.py; the stress test compares the meshes
with a conversion from scratch). A build that filled a group's conv (groupConv) in a project without a step of its
own keeps nothing: the default step (usedStep) may have changed in the middle of it (rows.buildRowTables).

Pure (no bpy). Opt-in: ctx.HISTORY is None unless the caller sets a dict (the Blender session does; the harness, the
parity tests and the traces build as before)."""
import hashlib
import json
import struct

KEEP = 16                                                           # fingerprints kept per lock key (the latest; a drag's pauses each make one)
MAX_KEYS = 4000                                                     # lock keys kept in all (the oldest used go first)


def _sample_digest(cache, c):
    """the exact shape of a curve (its sample list; rebuildCurve makes a new list whenever it resamples)"""
    got = cache.get(c.id)
    if got is not None and got[0] is c.samples:
        return got[1]
    s = c.samples or []
    buf = struct.pack(f'<{3 * len(s)}d', *[v for q in s for v in (q.p.x, q.p.y, q.p.z)])
    seg = json.dumps([q.seg for q in s], separators=(',', ':')).encode()
    d = hashlib.blake2b(buf + b'|' + seg, digest_size=16).hexdigest()
    cache[c.id] = (c.samples, d)
    return d


def _reach(P, curves):
    """the ids of the curves reached from `curves` through shared anchors (the curves themselves included)"""
    byA = P.anchor_curves()
    seen = {c.id for c in curves}
    stack = list(curves)
    while stack:
        c = stack.pop()
        for a in c.anchors:
            for h in byA.get(a.id, ()):
                if h.id not in seen:
                    seen.add(h.id); stack.append(h)
    return seen


def _shapes(P, items, seen, cache):
    """the curves `seen` (in curves() order: extendAlongHost's first host) as the tables read them — their signatures,
    exact shapes and anchors' positions (repr keeps a signed zero) —, the walls on them and the groups above them
    (convOf; the Density items are in the project-wide part)"""
    order = [c for c in P.curves() if c.id in seen]
    apos = [[(a.id, repr(a.pos.x), repr(a.pos.y), repr(a.pos.z)) for a in c.anchors] for c in order]
    groups = set()
    for c in order:
        p = P.parentOf(c)
        while p:
            groups.add(p.id); p = P.parentOf(p)
    walls = [items.get(s.id) for s in P.surfaces() if s.a in seen or s.b in seen]   # (the locks they make: which curves are one lock)
    return [[(c.id, items.get(c.id), _sample_digest(cache, c)) for c in order], apos, walls, [(g, items.get(g)) for g in sorted(groups)]]


# (1.2) What a lock's tables read through the Even loops / Proxy shell sets (shell.shellSetsBuild) besides its own
# component: a set is made from the locks its item covers (its group and sub-groups, shellsOf) and the ones welded to
# them (a shared segment: reached through anchors), with its carrier's group settings (convOf). A Proxy shell's rings
# are then taken by every lock running through three of them, of any group (ringIn, 3315), and each ring's gap gw is
# the longest over ALL the locks taking it: with a shell in effect every lock reads every shell and every other lock
# (global_part). An Even loops item's take is cut to its own group (3332): a component reads the Even loops items over
# its curves and the locks of their groups only (component_fp). An item no table reads (hidden, not painted, a shell
# not converted — 3058-3060) is in neither: nothing more than before.
def _evenshell_in_effect(items, k):
    from .scene import _evenshell_on
    s = items.get(k.id)
    return s is not None and _evenshell_on(json.loads(s))


def _evens_part(P, items, order, cache):
    """the Even loops items in effect over the curves `order` (up their groups, as shellsOf) with the shapes of the
    locks of their groups (and the ones welded to them); None when there are none"""
    from .groups import curvesIn
    evs = []
    for c in order:
        g = P.parentOf(c)
        while g:
            if g.type == 'group':
                for i in g.children:
                    x = P.items.get(i)
                    if x is not None and x.type == 'even' and x not in evs and _evenshell_in_effect(items, x):
                        evs.append(x)
            g = P.parentOf(g)
    if not evs:
        return None
    evs.sort(key=lambda x: x.id)
    seen = set()
    for k in evs:
        # (and the curves of its mask: evenParts reads their samples, also of one moved out of the group after it was
        # painted — the review of phase 2, finding e)
        painted = [c for c in (P.items.get(i) for i in (k.mask or {})) if c is not None and c.type == 'curve']
        seen |= _reach(P, list(curvesIn(P, P.items[k.parent])) + painted)
    return [[items[k.id] for k in evs], _shapes(P, items, seen, cache)]


def global_part(P, sig, cache=None):
    """(project-wide part, guides list): the guides go into each component's part, with their hits on its curves.
    `cache`: the sample digests (ctx.HIST_SAMPLES), for the Proxy shell part"""
    from .guides import guideOn
    from .hairmesh_io import defaultStep
    items = sig['items']
    rest = []; guides = []
    for it in P.items.values():
        t = it.type
        if t in ('curve', 'surface', 'group', 'mesh', 'cap', 'skin', 'tube', 'xmod', 'even'):
            continue                                                # (4.4 / 4.6: read by no row table — never in a component's fingerprint; 1.2: an Even loops item in the part of the components it is over)
        if t == 'shell' and not _evenshell_in_effect(items, it):
            continue                                                # (1.2: a shell no table reads)
        if t == 'guide':
            chain = []
            p = P.parentOf(it)
            while p:
                chain.append((p.id, bool(getattr(p, 'guidesOff', False))))
                p = P.parentOf(p)
            sg = items.get(it.id)
            if it.kind != 'disc':                                   # (a line's hits are in its signature: the curves it crosses)
                sg = json.loads(sg); sg[5] = None; sg = json.dumps(sg, default=str, separators=(',', ':'))
            guides.append((it, json.dumps([it.id, sg, it.nrm.toArray() if getattr(it, 'nrm', None) else None, guideOn(P, it), chain],
                                          default=str, separators=(',', ':'))))
        elif it.id in items:
            rest.append(items[it.id])
    # (1.2: a Proxy shell in effect — its item is in `rest` — makes every lock read every other)
    geo = _shapes(P, items, {c.id for c in P.curves()}, {} if cache is None else cache) if any(_evenshell_in_effect(items, k) for k in P.shells()) else None
    return (json.dumps([sig['global'], repr(defaultStep(P)), rest, geo], default=str, separators=(',', ':')), guides)


def component_fp(P, curves, sig, gpart, cache):
    """the fingerprint of a component of locks (their curves)"""
    items = sig['items']
    byA = P.anchor_curves()
    seen = {c.id for c in curves}
    stack = list(curves)
    while stack:                                                    # every curve reached through shared anchors
        c = stack.pop()
        for a in c.anchors:
            for h in byA.get(a.id, ()):
                if h.id not in seen:
                    seen.add(h.id); stack.append(h)
    order = [c for c in P.curves() if c.id in seen]                 # (curves() order: extendAlongHost's first host)
    # (the anchors' positions too: lockJunctionRows and backfillTrunk read them directly, repr keeps a signed zero)
    apos = [[(a.id, repr(a.pos.x), repr(a.pos.y), repr(a.pos.z)) for a in c.anchors] for c in order]
    groups = set()
    for c in order:
        p = P.parentOf(c)
        while p:
            groups.add(p.id); p = P.parentOf(p)
    walls = [s.id for s in P.surfaces() if s.a in seen or s.b in seen]   # (P.surfaces() order: the lock's curves and straighten's pairs follow it)
    head, guides = gpart
    gparts = [(g_sig, [(h.get('c'), h.get('t')) for h in g.hits if h.get('c') in seen]) for g, g_sig in guides]
    parts = [head, gparts,
             [(c.id, items.get(c.id), _sample_digest(cache, c)) for c in order], apos,
             [items.get(w) for w in walls],
             [(g, items.get(g)) for g in sorted(groups)]]
    ev = _evens_part(P, items, order, cache)                        # (1.2: the Even loops items over these locks, with the locks of their groups)
    if ev is not None:
        parts.append(ev)
    return hashlib.blake2b(json.dumps(parts, default=str, separators=(',', ':')).encode(), digest_size=20).hexdigest()


def components(keys, lockAnchors, byA):
    """the lock keys grouped by shared anchors (the flood of buildRowTables), each list in `keys` order"""
    comp = {}
    out = []
    for k in keys:
        if k in comp:
            continue
        cur = [k]; comp[k] = cur; stack = [k]
        while stack:
            for a in lockAnchors[stack.pop()]:
                for j in byA[a]:
                    if j not in comp:
                        comp[j] = cur; cur.append(j); stack.append(j)
        out.append(cur)
    order = {k: i for i, k in enumerate(keys)}
    return [sorted(c, key=order.__getitem__) for c in out]


def remember(hist, fp, key, entry):
    """the entry of lock `key` built under fingerprint `fp`; the oldest fingerprints of the key go"""
    per = hist.pop(key, None) or {}
    hist[key] = per                                                 # (the key used last goes to the end)
    per.pop(fp, None)
    per[fp] = entry
    while len(per) > KEEP:
        per.pop(next(iter(per)))
    while len(hist) > MAX_KEYS:
        hist.pop(next(iter(hist)))


def recall(hist, fp, key):
    per = hist.get(key)
    e = per.get(fp) if per else None
    if e is not None:                                               # (a state taken back is the newest one again)
        per.pop(fp); per[fp] = e
        hist.pop(key); hist[key] = per
    return e
