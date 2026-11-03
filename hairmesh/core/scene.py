# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""What the original's frame loop (hair-modeler.clean.html:12055-12075) does to the scene data before the surfaces
are rebuilt, in the same order: dirty curves are rebuilt, then the guides that need it, then the surfaces (row
tables, phase 2 subphase b). Nothing visual.

The frame loop knows `changed`, the curves rebuilt in THIS frame; the add-on has no frame, so the live preview tells
settle which curves were rebuilt since the last row build by someone else (the draw handler rebuilds dirty curves to
draw them: `extra`), and asks which part of the model the row tables depend on (tables_signature) to keep the tables
across an undo (live.row_tables, 16/10/2026)."""
import json

from .curves import rebuildCurve
from .guides import rebuildGuide
from .model import CONV_KEYS


def settle(P, extra=()):
    """Rebuilds every dirty curve and the guides that need it: a disc when it is dirty, a hair curve changed or one of
    its rings changed; a line when it is dirty or a curve it hits changed (the frame loop, 12067-12070). Returns
    what was rebuilt: 'changed' lists the rebuilt curves' ids — those rebuilt now and `extra`, the ones rebuilt since
    the last row build by whoever drew them — the frame loop's `changed` set, which buildRowTables(changed=...)
    takes for an incremental rebuild."""
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
    visibility (3.7: Convert hides the guides it converted, and that must not rebuild a lock)"""
    return {g.id: json.dumps([g.parent, g.kind, g.hits if g.kind != 'disc' else None, g.p, g.rings, g.off], default=str, sort_keys=True)
            for g in P.guides()}


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


def disc_moved(P, changed, guides):
    """4.2 (the review of the lot): a disc whose ring was resampled — moved by G / R / S, the gizmo, an anchor drag,
    its radii, Reset deformations — pierces other curves now, while guides_key (no disc hits in it) stays the same:
    the curves it shaped before (`guides`, built_state's) and the ones it shapes after its rebuild (settle ran it)
    join `changed`, as the original rebuilds every lock when a ring changed (`partial`, 12068). Returns the list."""
    from .guides import guideCurves
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
        changed = signature_changes(sig, tables_signature(P), P, guides)
    except Exception:                                               # a model the signature cannot read: from scratch
        return None
    if changed is None:
        return None
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


def tables_signature(P):
    """Everything the row tables depend on BESIDES the shape of the curves (their samples, compared by value by
    same_samples), per item: the conversion settings and the head height ('global'), and under 'items' (by id) the
    curves' identity, parent (convOf) and junction flags, the walls (the locks and their Clean topology), the
    groups' tree and conv, the guides, densities and crowns (a Cap, a Fill holes, a Profile modifier and its Extras:
    read by no table, lots 4.4 / 4.6). Two projects with
    the same signature and the same samples build the same tables, so the tables of one serve the other: an undo
    re-parses the text and the live preview keeps what still fits (signature_changes says which locks must go)."""
    from .hairmesh_io import defaultStep
    g = json.dumps([[getattr(P.settings, k) for k in CONV_KEYS], float(P.S.headH), float(defaultStep(P))], default=str, separators=(',', ':'))   # (4.4: the project step in effect, usedStep — every lock's backfillTrunk reads it)
    items = {}
    for it in P.items.values():
        t = it.type
        if t == 'curve':
            sig = ['c', it.id, it.parent, [(a.id, list(a.pass_) if a.pass_ else None, a.sec, (a.att.c, a.att.t) if a.att else None) for a in it.anchors],
                   it.closed, it.ring, it.junc, it.conn, it.para]
        elif t == 'surface':
            sig = ['s', it.id, it.a, it.b, it.parent, bool(it.p.get('clean'))]   # (the other p keys shape the grid, not the loops)
        elif t == 'group':
            # (the converted meshes among the children are read by no table: Convert adds them, its undo takes them away)
            kids = [c for c in it.children if getattr(P.items.get(c), 'type', None) not in ('mesh', 'cap', 'skin', 'tip', 'tube', 'xmod')]   # (4.4: a Cap / Fill holes / a modifier: no table; a Density: its own entry)
            sig = ['g', it.id, it.parent, kids, conv_seen(P, it), bool(it.guidesOff)]
        elif t in ('guide', 'tip', 'crown', 'cap', 'skin', 'tube', 'xmod'):
            # (a guide's or a crown's eye: read by no table, 3.7; a disc's hits are derived from the samples)
            sig = [t, it.id, it.parent, it.visible if t not in ('guide', 'crown') else None, getattr(it, 'kind', None), getattr(it, 'hits', None) if getattr(it, 'kind', None) != 'disc' else None,
                   dict(it.p) if getattr(it, 'p', None) and t != 'crown' else None,   # (4.3: a crown's p — Rings, Whorl, the symmetry — is read by crownData only, never by a table)
                   getattr(it, 'rings', None), getattr(it, 'off', None), {str(k): list(w) for k, w in it.mask.items()} if t == 'tip' and it.mask else getattr(it, 'mask', None),
                   getattr(it, 'part', None), getattr(it, 'low', None), getattr(it, 'src', None), getattr(it, 'tube', None)]
        else:
            continue
        items[it.id] = json.dumps(sig, default=str, separators=(',', ':'))
    return {'global': g, 'items': items}


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
    differs, the curves a guide that differs shapes, the curves under the group of a crown or a Density that differs
    (a Cap, a Fill holes, a Profile modifier or an Extra: none). None when the tables cannot be kept at all: the
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
        else:
            return None
    return out
