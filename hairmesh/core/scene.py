# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""What the original's frame loop (hair-modeler.clean.html:14799-14824) does to the scene data before the surfaces
are rebuilt, in the same order: dirty curves are rebuilt, then the guides that need it, then the surfaces (row
tables, phase 2 subphase b). Nothing visual.

The frame loop knows `changed`, the curves rebuilt in THIS frame; the add-on has no frame, so the live preview tells
settle which curves were rebuilt since the last row build by someone else (the draw handler rebuilds dirty curves to
draw them: `extra`), and asks which part of the model the row tables depend on (tables_signature) to keep the tables
across an undo (live.row_tables, 16/10/2026)."""
import json

from .curves import rebuildCurve, syncPathCurves
from .guides import rebuildGuide
from .js import truthy
from .model import CONV_KEYS


def settle(P, extra=(), cam_dir=None):
    """Rebuilds every dirty curve and the guides that need it: a disc when it is dirty, a hair curve changed or one of
    its rings changed; a line when it is dirty or a curve it hits changed (the frame loop, 14815-14818). Returns
    what was rebuilt: 'changed' lists the rebuilt curves' ids — those rebuilt now and `extra`, the ones rebuilt since
    the last row build by whoever drew them — the frame loop's `changed` set, which buildRowTables(changed=...)
    takes for an incremental rebuild.
    1.0.2: first the edge curves follow the curves they run along (syncPathCurves, 16623: before the rebuild); the flat
    locks' ribbons last (core/rib.frameRibs, 14823); `cam_dir` is camDir() for a ribbon without its own view (a file's
    `rib`)."""
    syncPathCurves(P)
    n_curves = 0; seen = set(extra); changed = []
    for c in P.curves():
        if c.dirty:
            old = c.samples
            rebuildCurve(P, c); n_curves += 1
            if c.samples is not old and c.id not in seen:       # (a curve that kept its samples did not change: curves.sampleKey)
                seen.add(c.id); changed.append(c.id)
    for cid in extra:
        if cid not in changed:
            changed.append(cid)
    # 1.0.2: the flat locks (16641): every ribbon rebuilt when a curve was rebuilt (or it is dirty, or has none yet); a
    # root that followed its anchors marks its curves dirty and waits for the next frame — run here until it settles
    # (the frames the original would run before the next user action)
    if any(truthy(c.rib) or c.rib_geom is not None for c in P.curves()):
        from .rib import FOLLOW_ROUNDS, frameRibs
        rebuilt = n_curves > 0 or bool(changed)
        for _f in range(FOLLOW_ROUNDS):
            if not frameRibs(P, rebuilt, cam_dir):
                break
            rebuilt = False
            # (the next frame: its crown part first — crownSymSync, the edge curves, updateAttachments, 1.0.3 16622-16627:
            # a root that followed an anchor of a symmetric crown moves its pair; fuzz case 12300009)
            from .crowntool import frameSync
            # (its rebuilds of the dirty curves count as the frame's, 16625 / 16627: a resampled curve is `changed`; its
            # toasts — an anchor come off its connector — are the operator's to say, session.sync: none in a settle)
            old = {c.id: (c.samples, c.dirty) for c in P.curves()}
            frameSync(P)
            for c in P.curves():
                if c.id not in old:
                    continue
                was, dirty = old[c.id]
                if c.samples is not was or (dirty and not c.dirty):
                    rebuilt = True; n_curves += 1
                if c.samples is not was and c.id not in seen:
                    seen.add(c.id); changed.append(c.id)
            for c in P.curves():
                if c.dirty:
                    old = c.samples
                    rebuildCurve(P, c); n_curves += 1; rebuilt = True
                    if c.samples is not old and c.id not in seen:
                        seen.add(c.id); changed.append(c.id)
    # (updateAttachments: anchors on crown connectors are already in place in a saved file; ported in phase 3)
    hair_changed = any(not getattr(P.items.get(cid), 'ring', None) for cid in changed if cid in P.items)
    n_guides = 0
    for g in P.guides():
        if g.kind == 'disc':
            need = g.dirty or hair_changed or any(r in seen for r in (g.rings or []))
        else:
            need = g.dirty or any(h.get('c') in seen for h in g.hits)
        if need:
            rebuildGuide(P, g); n_guides += 1
    return {'curves': n_curves, 'guides': n_guides, 'changed': changed}


def walls_key(P):
    """what the tables read of every wall: which curves it joins and its Clean topology (the pairs of
    straightenLoops): a wall made, removed or switched changes the loops of its lock even when the lock's curves
    are the same, so its curves count as changed (the lock of the example lost a wall and kept its loops otherwise)"""
    return {sf.id: (sf.a, sf.b, bool(sf.p.get('clean'))) for sf in P.surfaces()}


def guides_key(P):
    """what the tables read of every guide besides the curves' shapes (as tables_signature): a guide edited since
    the last build — a line that lost the hits of a deleted curve (deleteItem, 771: S.surfDirty there), one switched
    or removed — reshapes the locks it crosses, before and after, whether or not their curves were resampled (lot
    ux8: a delete no longer resamples every curve, curves.sampleKey). Not its eye: no table reads a guide's
    visibility (3.7: Convert hides the guides it converted, and that must not rebuild a lock). A disc: how many of its
    rings are left (disc_cut)"""
    return {g.id: json.dumps([g.parent, g.kind, g.hits if g.kind != 'disc' else disc_cut(P, g), g.p, g.rings, g.off], default=str, sort_keys=True)
            for g in P.guides()}


def disc_cut(P, g):
    """ours (lot 1.2a, decision 11): how many of a disc's rings are left. deleteItem takes a disc's hits off when one
    of its rings goes, and the hits are in neither key (they follow the rings' samples), so this count is: the locks
    the disc guided are rebuilt (fixed seed 730002). A reopened file counts the same."""
    return sum(1 for r in g.rings or () if r in P.items)


def crowns_key(P):
    """every crown and the curves of the walls rooted on it (wallCrownRoot, 2718: their grids blend onto its
    connector): a crown made, edited or removed — deleteItem takes it with its `part` or `low` curve — reshapes those
    walls, before and after, even when no curve they share an anchor with was resampled (a connector held by `att`)"""
    from .surfaces import wallCrownRoot
    crowns = P.crowns()                                             # (once: every wall asks, lot 3.7-estab)
    out = {k.id: [json.dumps([k.part, k.low, k.parent], default=str, sort_keys=True), []] for k in crowns}   # (not its eye: see guides_key; nor its p — Rings, Whorl, the symmetry: no wall nor row table reads them, crownData only, 4.3)
    if out:
        for sf in P.surfaces():
            r = wallCrownRoot(P, sf, crowns)                        # ({'low': the lower connector, ...}: the first crown with it)
            k = next((k for k in crowns if r is not None and k.low == r['low'].id), None)
            if k is not None:
                out[k.id][1] += [sf.a, sf.b]
    return {i: (s, cs) for i, (s, cs) in out.items()}


def built_state(P):
    """what an incremental row build remembers of the state it was built from (live.row_tables, the tests): the
    sample list of every curve (rebuildCurve makes a new one whenever it resamples), the walls (walls_key), the
    guides with the curves each one shapes (guides_key, guides.guideCurves) and the crowns with the curves of the walls
    rooted on them (crowns_key)"""
    from .guides import guideCurves
    return ({c.id: c.samples for c in P.curves()}, walls_key(P),
            {gid: (k, guideCurves(P, P.items[gid])) for gid, k in guides_key(P).items()}, crowns_key(P))


def offRing(P, cid):
    """port of offRing (hair-modeler.clean.html:16631, the frame loop): the curve is a ring of a disc that is switched off
    (guideOn): it shapes nothing, so its move rebuilds no lock (it used to rebuild all of them: a lag)"""
    from .guides import guideOn
    it = P.items.get(cid)
    ring = getattr(it, 'ring', None)
    if not ring:
        return False
    d = P.items.get(ring)
    return d is not None and not guideOn(P, d)


def disc_moved(P, changed, guides):
    """4.2 (the review of the lot): a disc whose ring was resampled — moved by G / R / S, the gizmo, an anchor drag,
    its radii, Reset deformations — pierces other curves now, while guides_key (no disc hits in it) stays the same:
    the curves it shaped before (`guides`, built_state's) and the ones it shapes after its rebuild (settle ran it)
    join `changed`, as the original rebuilds every lock when a ring changed (`partial`, 16634). The rings of a disc
    switched off are left out first (offRing, 1.0.2: `eff`, 16632). Returns the list."""
    from .guides import guideCurves
    changed = [c for c in changed if not offRing(P, c)]
    seen = set(changed); out = list(changed)
    for g in P.guides():
        if g.kind == 'disc' and any(r in seen for r in (g.rings or [])):
            old = guides.get(g.id)
            for c in (old[1] if old else []) + guideCurves(P, g):
                if c not in seen and c in P.items and P.items[c].type == 'curve':
                    seen.add(c); out.append(c)
    return out


def changed_since(P, samples, walls, guides, touched=(), crowns=None):
    """The curves to pass to the next settle as `extra` for an incremental row build after the state of
    built_state: the ones touched (live.touch), the two curves of every wall made, removed or switched, every curve
    whose sample list is another one (resampled by anyone since), the curves a guide edited since shapes and the
    curves of the walls rooted on a crown edited since, before and after (16/10/2026; guides and crowns since the lot
    ux8). A disc whose ring moved shapes other curves only after its rebuild: the caller adds them after the settle
    (disc_moved, 4.2; live.row_tables)."""
    from .guides import guideCurves
    extra = sorted(touched)
    now = walls_key(P)
    for sid in set(now) | set(walls):
        if now.get(sid) != walls.get(sid):
            extra += [c for c in (now.get(sid) or walls.get(sid))[:2] if c not in extra]
    extra += [c.id for c in P.curves() if samples.get(c.id) is not c.samples and c.id not in extra]
    gnow = guides_key(P)
    for gid in set(gnow) | set(guides):
        old = guides.get(gid)
        if gnow.get(gid) != (old[0] if old else None):
            g = P.items.get(gid)
            extra += [c for c in (old[1] if old else []) + (guideCurves(P, g) if g is not None and g.type == 'guide' else []) if c not in extra]
    if crowns is not None:
        know = crowns_key(P)
        for kid in set(know) | set(crowns):
            a = crowns.get(kid); b = know.get(kid)
            if (a[0] if a else None) != (b[0] if b else None) or (a[1] if a else None) != (b[1] if b else None):
                extra += [c for c in (a[1] if a else []) + (b[1] if b else []) if c not in extra and c in P.items]
    extra = [c for c in extra if (it := P.items.get(c)) is None or it.type == 'curve']   # (an id that names another kind of item now)
    return extra


def inherited(sig, built, guides, P):
    """Tables built from a state (its signature `sig`, the sample lists `built` and the guides with their curves
    `guides` — built_state's) kept for a model read again from its text (an undo, a redo, the Esc of a slider):
    (the curves whose locks must go, the curves whose samples are by value the ones the tables were built from), or
    None when they cannot be kept (signature_changes). Every dirty curve of P is resampled first (fresh from the
    parse). Lot 4.2c, item 1: compared with the state the tables were built from, never with the model before the
    parse as it is now (a slider let go with Esc changed that one in memory only)."""
    from .curves import rebuild_dirty
    rebuild_dirty(P)
    try:
        new = tables_signature(P)
        changed = signature_changes(sig, new, P, guides)
    except Exception:                                               # a model the signature cannot read: from scratch
        return None
    if changed is None:
        return None
    # (1.2: a curve whose shape differs reaches the locks of the Even loops / Proxy shell sets it is in, as a changed item)
    moved = [c.id for c in P.curves() if c.id not in changed and not ((b := built.get(c.id)) is not None and same_samples(b, c.samples))]
    if moved:
        changed = list(changed) + [c for c in evenshell_spread(sig, new, P, list(changed) + moved) if c not in moved and c not in changed]
    gone = set(changed); now = {}
    for c in P.curves():
        b = built.get(c.id)
        if c.id not in gone and b is not None and same_samples(b, c.samples):
            now[c.id] = c.samples
    return changed, now


def same_samples(a, b):
    """two sample lists with the very same points (the curve's shape is unchanged)"""
    if len(a) != len(b):
        return False
    for s, t in zip(a, b):
        p = s.p; q = t.p
        if p.x != q.x or p.y != q.y or p.z != q.z or s.seg != t.seg:
            return False
    return True


def conv_seen(P, g):
    """a group's conv as the tables see it: groupConv (1589) fills the defaults in at the first read, so a conv with
    the defaults filled and the same conv without them build the same tables (lot 3.7-estab: Convert fills them, its
    undo takes them away, and the raw dicts made every lock count as changed after Ctrl+Z — 310 curves, 4 s). The
    step a group without one takes is only certain when the project has a step of its own (defaultStep, 1581: else
    usedStep, which reads the other groups): then the raw conv stands."""
    from .js import truthy
    c = dict(g.conv) if g.conv else {}
    if c.get('step') is None:
        if not truthy(P.settings.step):
            return c or None
        c['step'] = P.S.headH / c['density'] if truthy(c.get('density')) else P.settings.step
    for k in CONV_KEYS:
        if k not in c:
            c[k] = getattr(P.settings, k)
    return c


def _head_place(P):
    """ours (the review of lot 1.1e): the head's center and scale — surfaceData (2740) reads them for every wall's grid
    (the outward side of a lock of two curves), so a state with the head elsewhere is another state of the tables'
    readers: the undo of a Set Head builds from scratch, as Set Head itself does"""
    return [P.S.center.toArray(), P.S.scale]


def tables_signature(P):
    """Everything the row tables depend on BESIDES the shape of the curves (their samples, compared by value by
    same_samples), per item: the conversion settings and the head height ('global'), and under 'items' (by id) the
    curves' identity, parent (convOf) and junction flags, the walls (the locks and their Clean topology), the
    groups' tree and conv, the guides, densities, crowns, Even loops and Proxy shells (a Cap, a Fill holes, a Profile modifier and its Extras:
    read by no table, lots 4.4 / 4.6). Two projects with
    the same signature and the same samples build the same tables, so the tables of one serve the other: an undo
    re-parses the text and the live preview keeps what still fits (signature_changes says which locks must go)."""
    from .hairmesh_io import defaultStep
    g = json.dumps([[getattr(P.settings, k) for k in CONV_KEYS], float(P.S.headH), float(defaultStep(P)), _head_place(P)], default=str, separators=(',', ':'))   # (4.4: the project step in effect, usedStep — every lock's backfillTrunk reads it; lot 1.1e: the head's center and scale, read by every grid)
    items = {}
    for it in P.items.values():
        t = it.type
        if t == 'curve':
            sig = ['c', it.id, it.parent, [(a.id, list(a.pass_) if a.pass_ else None, a.sec, (a.att.c, a.att.t) if a.att else None) for a in it.anchors],
                   it.closed, it.ring, it.junc, it.conn, it.para, truthy(it.path)]   # (1.1a-2: c.path, read by extendAlongHost 2504 and tailMates 11658 as a truth test)
        elif t == 'surface':
            sig = ['s', it.id, it.a, it.b, it.parent, bool(it.p.get('clean'))]   # (the other p keys shape the grid, not the loops)
        elif t == 'group':
            # (the converted meshes among the children are read by no table: Convert adds them, its undo takes them away)
            kids = [c for c in it.children if getattr(P.items.get(c), 'type', None) not in ('mesh', 'cap', 'skin', 'tip', 'tube', 'xmod')]   # (4.4: a Cap / Fill holes / a modifier: no table; a Density: its own entry)
            sig = ['g', it.id, it.parent, kids, conv_seen(P, it), bool(it.guidesOff)]
        elif t in ('guide', 'tip', 'crown', 'cap', 'skin', 'tube', 'xmod'):
            # (a guide's or a crown's eye: read by no table, 3.7; a disc's hits are derived from the samples)
            sig = [t, it.id, it.parent, it.visible if t not in ('guide', 'crown') else None, getattr(it, 'kind', None), getattr(it, 'hits', None) if getattr(it, 'kind', None) != 'disc' else disc_cut(P, it),
                   dict(it.p) if getattr(it, 'p', None) and t != 'crown' else None,   # (4.3: a crown's p — Rings, Whorl, the symmetry — is read by crownData only, never by a table)
                   getattr(it, 'rings', None), getattr(it, 'off', None), {str(k): list(w) for k, w in it.mask.items()} if t == 'tip' and it.mask else getattr(it, 'mask', None),
                   getattr(it, 'part', None), getattr(it, 'low', None), getattr(it, 'src', None), getattr(it, 'tube', None)]
        elif t in ('even', 'shell'):
            sig = _evenshell_sig(it)
        else:
            continue
        items[it.id] = json.dumps(sig, default=str, separators=(',', ':'))
    return {'global': g, 'items': items}


def _evenshell_sig(it):
    """(1.2) what shellSets / shellSetsBuild read of an Even loops / Proxy shell item, 3008-3352 — its place in the tree
    (shellsOf), the eye, p (str; eq, fall), the painted masks per curve, and of a shell: baked, the locks taken out
    (off), the Brush deformation with its version v — v is written by the build itself when None"""
    t = it.type
    d = it.def_ if t == 'shell' else None
    return [t, it.id, it.parent, it.visible, dict(it.p) if it.p else None,
            {str(k): list(w) for k, w in it.mask.items()} if it.mask is not None else None,
            bool(it.baked) if t == 'shell' else None, list(it.off) if t == 'shell' and it.off is not None else None,
            [d.ns, d.na, list(d.d), d.v] if d is not None else None]


def spread_changed(P, changed):
    """(1.2, the review of phase 2, finding 1) the curves `changed` since the last row build (settle's 'changed', the
    curves touched) spread over the Even loops / Proxy shell sets in effect (evenshell_spread): the incremental build
    floods its rebuild through shared anchors only (buildRowTables' dirty), but a set reads every lock of its group and
    a shell's rings reach locks of any group — without this an anchor dragged on a set's carrier left the other locks
    of the set with the tables of before, which Convert then read. Cheap when no such item is in effect."""
    if not changed:
        return changed
    items = {it.id: json.dumps(_evenshell_sig(it), default=str, separators=(',', ':')) for it in list(P.shells()) + list(P.evens())}
    if not items:
        return changed
    sig = {'items': items}
    changed = list(changed)
    if any((c := P.items.get(i)) is None or c.type != 'curve' for i in changed):
        # (a curve gone since — deleted, split, merged —: nothing says which set it was in; every Even loops group in
        # effect is rebuilt, as a shell in effect rebuilds every lock)
        from .groups import curvesIn
        have = set(changed)
        for s in items.values():
            d = json.loads(s)
            g = P.items.get(d[2])
            if _evenshell_on(d) and g is not None and g.type == 'group':
                changed += [c.id for c in curvesIn(P, g) if c.id not in have and not have.add(c.id)]
    return evenshell_spread(sig, sig, P, changed)


def _sig_curves_under(items, gid):
    """the curves under group `gid` (its children, down the tree) as the signature `items` holds them: a group gone
    from the model (an undo of its making) is still there"""
    out = []; stack = [gid]; seen = set()
    while stack:
        i = stack.pop()
        s = items.get(i)
        if s is None or i in seen:
            continue
        seen.add(i)
        d = json.loads(s)
        if d[0] == 'c':
            out.append(i)
        elif d[0] == 'g':
            stack.extend(d[3])
    return out


def signature_changes(old, new, P, old_guides=None):
    """The curves whose locks must be rebuilt for the tables built under signature `old` to serve the model P (of
    signature `new`): the curves that differ, the two curves of a wall that differs, the curves inside a group that
    differs, the curves a guide that differs shapes, the curves under the group of a crown, a Density or an Even loops
    item that differs, every curve for a Proxy shell that differs and is or was in effect (a Cap, a Fill holes, a Profile modifier or an Extra: none). None when the tables cannot be kept at all: the
    settings, the head height or the project step differ, or a group or guide that differs is gone and nothing
    says which locks it shaped.

    `old_guides` (built_state's, the state the tables were built from: {guide id: (key, the curves it shaped)}) names
    the locks of a guide that differs or is gone (lot 4.2c, item 1: the undo of a disc made the next click rebuild
    every lock from scratch, 4.6 s; a guide gone now names the curves it shaped then, and a disc that differs the
    ones it shapes now — rebuilt first if the parse left it unbuilt, as the frame's settle would)."""
    from .effects import convTouch
    from .groups import curvesIn
    from .guides import guideCurves, rebuildGuide
    if old['global'] != new['global']:
        return None
    out = []

    def add(ids):
        for i in ids:
            if i not in out:
                out.append(i)
    for iid in set(old['items']) | set(new['items']):
        a = old['items'].get(iid); b = new['items'].get(iid)
        if a == b:
            continue
        kind = json.loads(b or a)[0]
        it = P.items.get(iid)
        if kind == 'c':
            add([iid])
        elif kind == 's':
            for sig in (a, b):
                if sig:
                    add(json.loads(sig)[2:4])
        elif kind == 'g':
            if it is None or it.type != 'group':
                if b or not a:
                    return None
                # (its own loop step reaches other locks only through the project step in effect — usedStep —, which the
                # global part of the signature holds since lot 4.4: unchanged, the group's own locks alone; a crown reads
                # its group's step in crownData only, never a table)
                add([c for c in _sig_curves_under(old['items'], iid) if c in P.items])   # (gone: the curves it held then)
                continue
            ga = json.loads(a) if a else None; gb = json.loads(b) if b else None
            if ga and gb and ga[:4] == gb[:4] and ga[5:] == gb[5:]:
                # only the conv differs (an undo of a group's slider): the locks that read it (lot ux8, part 2)
                ca = ga[4] or {}; cb = gb[4] or {}
                t = convTouch(P, {iid}, {k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k)})
                add([c.id for c in P.curves()] if t is None else t)
            else:
                add([c.id for c in curvesIn(P, it)])
        elif kind == 'guide':
            if a and old_guides is not None and iid in old_guides:  # the locks it shaped when the tables were built
                add([c for c in old_guides[iid][1] if c in P.items])
            elif a and json.loads(a)[4] != 'disc':                  # a line's hits then
                add([h.get('c') for h in (json.loads(a)[5] or []) if h.get('c') in P.items])
            elif a:
                return None                                         # (a disc and nothing says what it pierced)
            if it is None or it.type != 'guide':
                if b:
                    return None
                continue                                            # (gone: the locks it shaped, above)
            if it.kind == 'disc' and it.hitPts is None:
                rebuildGuide(P, it)                                 # (fresh from the parse: the curves through it now)
            add(guideCurves(P, it))
        elif kind in ('cap', 'skin', 'tube', 'xmod'):
            continue                                                # (lot 4.4: read by the conversion and the previews alone, never by a row table; 4.6: a Profile modifier and its Extras too)
        elif kind in ('crown', 'tip'):
            # (lot 4.4, item 1c: a crown made or deleted, then Ctrl+Z, rebuilt every lock from scratch. No row table
            # reads the crown item: its connectors and curves — and the lock curves attached to them, att in their own
            # signatures — come in as curves; its group's curves, then and now, are rebuilt. A Density shapes the
            # locks of its group and sub-groups — tipOf, the nearest up the tree, lockTip —: the same curves; item 5,
            # the undo of a group deleted with its Density rebuilt every lock)
            for sig in (a, b):
                if sig:
                    gid = json.loads(sig)[2]
                    add([c for c in _sig_curves_under(old['items'], gid) if c in P.items])
                    g = P.items.get(gid)
                    if g is not None and g.type == 'group':
                        add([c.id for c in curvesIn(P, g)])
        elif kind in ('even', 'shell'):
            # (1.2) an item no table reads then nor now — hidden, not painted, a shell not converted (shellSetsBuild 3058-3060)
            # — rebuilds nothing; an Even loops item shapes the locks of its group and sub-groups only (shellsOf, the take
            # of 3332): the same curves as a Density; a Proxy shell's rings are taken by every lock running through three
            # of them, OF ANY GROUP (ringIn 3315), and each ring's gap gw is the longest over all those locks: every lock
            if not any(sig and _evenshell_on(json.loads(sig)) for sig in (a, b)):
                continue
            if kind == 'shell':
                add([c.id for c in P.curves()])
                continue
            for sig in (a, b):
                if sig:
                    gid = json.loads(sig)[2]
                    add([c for c in _sig_curves_under(old['items'], gid) if c in P.items])
                    g = P.items.get(gid)
                    if g is not None and g.type == 'group':
                        add([c.id for c in curvesIn(P, g)])
        else:
            return None
    return evenshell_spread(old, new, P, out)


def evenshell_spread(old, new, P, ids):
    """(1.2) the curves `ids` to rebuild, spread over what the Even loops / Proxy shell sets carry from one lock to
    another beyond the shared anchors (buildRowTables' flood): a set reads every lock of its item's group (the carrier
    is the longest, the axis the mean of their spines, the Density, the walls and the group settings of each — gl,
    convOf, lockTip) and a Proxy shell's rings reach every lock running through them, of any group (ringIn 3315, the
    gap gw the longest over all of them). So with a shell in effect then or now anything that changes rebuilds every
    lock, and with an Even loops item a change touching its group's locks (or the ones welded to them) rebuilds the
    whole group — the tables kept are the ones a build from scratch makes (an undo: the original rebuilds everything)."""
    if not ids:
        return ids
    on = [json.loads(s) for sg in (old, new) for s in sg['items'].values() if s.startswith('["even"') or s.startswith('["shell"')]
    on = [d for d in on if _evenshell_on(d)]
    if not on:
        return ids
    if any(d[0] == 'shell' for d in on):
        return [c.id for c in P.curves()]
    from .groups import curvesIn
    from .rowhist import _reach
    out = list(ids); have = set(out); grown = True
    while grown:                                                    # (a group rebuilt may touch another Even loops group)
        grown = False
        for d in on:
            g = P.items.get(d[2])
            if g is None or g.type != 'group':
                continue
            mine = list(curvesIn(P, g))
            # (finding e of the review of phase 2: evenParts reads the samples of every curve of the mask, also of one moved
            # out of the group after it was painted — its shape cuts the parts)
            painted = [c for c in (P.items.get(int(i)) for i in (d[5] or {})) if c is not None and c.type == 'curve']
            if have & _reach(P, mine + painted):
                for c in mine:
                    if c.id not in have:
                        have.add(c.id); out.append(c.id); grown = True
    return out


def evenshell_touch(P, k):
    """(1.2) the curves to rebuild when a Density, Even loops or Proxy shell item `k` is shown, hidden or deleted (touchExtra
    12077 / deleteItem 876 of 1.0.3: S.surfDirty, the original builds every lock again): the locks of its group, or —
    a Proxy shell that builds a set when shown (converted, a mask: shellSetsBuild 3058) — every lock: its rings are
    taken by locks of any group (ringIn 3315) and their gap is the longest over all of them; then spread over what the
    sets in effect now carry (evenshell_spread: with another shell in effect, every lock — a Density switched under it
    changes the gap of its rings for every lock taking them)"""
    from .groups import curvesIn
    if k.type == 'shell' and k.mask is not None and truthy(getattr(k, 'baked', None)):
        return [c.id for c in P.curves()]
    g = P.items.get(k.parent)
    ids = [c.id for c in curvesIn(P, g)] if g is not None and g.type == 'group' else []
    sig = tables_signature(P)
    return evenshell_spread(sig, sig, P, ids)


def _evenshell_on(sig):
    """an Even loops / Proxy shell signature (tables_signature) that shellSetsBuild builds a set from (3058-3060): visible,
    painted and — a shell — converted"""
    return sig[3] is not False and sig[5] is not None and (sig[0] == 'even' or bool(sig[6]))
