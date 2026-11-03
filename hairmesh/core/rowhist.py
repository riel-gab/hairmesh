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
and crown (a cap, a Fill holes, a Profile modifier and its Extras: read by no table). Two states with the same fingerprint for a component build the same entries for it: the
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


def global_part(P, sig):
    """(project-wide part, guides list): the guides go into each component's part, with their hits on its curves"""
    from .guides import guideOn
    from .hairmesh_io import defaultStep
    items = sig['items']
    rest = []; guides = []
    for it in P.items.values():
        t = it.type
        if t in ('curve', 'surface', 'group', 'mesh', 'cap', 'skin', 'tube', 'xmod'):
            continue                                                # (4.4 / 4.6: read by no row table — never in a component's fingerprint)
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
    return (json.dumps([sig['global'], repr(defaultStep(P)), rest], default=str, separators=(',', ':')), guides)


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
