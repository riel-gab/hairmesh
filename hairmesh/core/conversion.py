# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The conversion flow (3.7): Convert makes one pending mesh item per ticked group, Confirm keeps them, Cancel takes
them away (startConversion / regenConversion / confirmConversion / cancelConversion, hair-modeler.clean.html:
10010-10032). The meshes are items of the project, as in the original (addItem('mesh'), named "<group> mesh"); the
original's S.conv = {entries, hidden} is P.conv, which goes in the file as S.conv does (snapshot 1218) and comes back
from it (restore 1265); restoreConv works out what is ours.

Ours (docs/features-adicionadas.md):
- `replaced`: Convert again on a group that already has a confirmed mesh (or over walls a confirmed mesh of another
  group was made from) hides that mesh while the new one is pending; Confirm deletes it (the original keeps both),
  Cancel shows it again — unless its walls are shown by then (Live during the preview).
- the incremental reconversion: a mesh's signature (meshSignature) names everything buildConvMesh reads besides the
  row tables' content — the revisions of its walls' grids stand for that, since a grid is computed again exactly
  when its lock is rebuilt or the wall changed (blender/live.py) —; regenConversion(only=...) and startConversion
  (reuse=...) skip the meshes whose signature did not change, which gives the same data a full rebuild gives
  (tests/test_blender_convert37).
- the Live / Baked view (setView / viewOf): the meshes shown and what they converted hidden, or the other way round —
  what the outliner's eyes would do, in one step.

Pure: no bpy. Every mesh data rebuilt gets a new `token` (the Blender side refills an object when its token differs)."""
import json
import uuid

from . import convert as CV
from . import edit
from . import model as M
from .curves import rebuildCurve
from .hairmesh_io import defaultStep
from .rowbase import settings_conv

SOURCE_TYPES = ['surface', 'curve', 'guide', 'crown']               # what Convert hides (startConversion 10021)


def new_token():
    return uuid.uuid4().hex[:16]


def meshes(P):
    return [it for it in P.items.values() if it.type == 'mesh']


def _build(P, mesh, T, crown_data):
    CV.buildConvMesh(P, mesh, T, None, crown_data)
    mesh.token = new_token()


def startConversion(P, T, crown_data=None, reuse=None, replace=True):
    """port of startConversion (hair-modeler.clean.html:14243): (made, message). The caller has the row tables T
    built after the curves settled (the original's buildGroupMesh builds them itself: the same tables).
    `reuse(mesh)` (ours): (data, symBaked, token) of an earlier build with the same signature, or None — the mesh is
    not built again. `replace` (ours): the confirmed meshes of the converted groups are hidden until Confirm deletes
    them. Returns P.conv's entries made (empty: nothing converted, with the original's toast as the message)."""
    steps = startConversionSteps(P, T, crown_data, reuse, replace)
    while True:
        try:
            next(steps)
        except StopIteration as e:
            return (P.conv['entries'] if P.conv else []), e.value


def startConversionSteps(P, T, crown_data=None, reuse=None, replace=True):
    """startConversion one mesh at a time (a generator, for the modal's progress): yields (index, count, mesh)
    after each mesh is made; P.conv is set when it ends, and the generator returns the original's toast when nothing
    was converted. T may be a callable (built lazily, after the first yield: the modal says "building the row tables"
    meanwhile)."""
    gids = [i for i in P.settings.convChecked if i in P.items]
    if not gids:
        return 'Tick the groups to convert'
    if P.conv:
        cancelConversion(P)
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)
    checked = set(gids)
    plan = [(gid, CV.collect(P, gid, checked, ['surface']), CV.collect(P, gid, checked, ['crown'])) for gid in gids]
    plan = [x for x in plan if x[1] or x[2]]
    conv = {'entries': [], 'hidden': [], 'replaced': [], 'shown': []}
    old = [m for m in meshes(P) if not m.pending] if replace else []
    yield (0, len(plan), None)
    if callable(T):
        T = T()
    for n, (gid, surfs, crs) in enumerate(plan):
        mesh = edit.addItem(P, 'mesh', gid, {'pending': True, 'srcSurfs': [s.id for s in surfs], 'srcCrowns': [k.id for k in crs]})
        mesh.name = P.items[gid].name + ' mesh'
        got = reuse(mesh) if reuse is not None else None
        if got is not None:
            mesh.data, mesh.symBaked, mesh.token = got
            CV.fillConvs(P, mesh)                                   # (what building it would have done to the groups' conv)
        else:
            _build(P, mesh, T, crown_data)
        conv['entries'].append(mesh.id)
        for h in CV.collect(P, gid, checked, SOURCE_TYPES):
            if h.visible:
                h.visible = False; conv['hidden'].append(h.id)
        yield (n + 1, len(plan), mesh)
    if not conv['entries']:
        return 'The ticked groups contain no walls'                 # (10023: "no NURBS surfaces"; walls in the port)
    # ours: the confirmed meshes the new ones replace — a converted group's, and any other whose walls the new meshes
    # all cover (a sub-group's mesh, when the parent is converted with the sub-group unticked: two meshes over the same
    # walls otherwise, lot 3.7-estab). A mesh only partly covered (the parent's, when the sub-group alone is converted)
    # stays: deleting it on Confirm would lose its other walls (the original keeps both). A Shift+D copy elsewhere
    # stays too (convertedHere, 3.8b)
    for m in old:
        if replacedBy(P, m, conv['entries']):
            conv['replaced'].append(m.id)
            if m.visible:
                m.visible = False; conv['shown'].append(m.id)
    P.conv = conv
    _outliner(P)
    P.rev += 1
    return None


def restoreConv(P):
    """ours: a file with a conversion pending has the original's {entries, hidden} alone (restore, 1265). What Convert said
    of the confirmed meshes is worked out again: those the pending meshes replace, hidden, deleted by Confirm. The file
    does not say which of them were on screen: Cancel shows those whose walls are not on screen once what Convert hid
    is back (cancelConversion)."""
    c = P.conv
    if not c:
        return
    for m in meshes(P):
        if m.id not in c['entries'] and replacedBy(P, m, c['entries']):
            c['replaced'].append(m.id); c['shown'].append(m.id)
            m.visible = False


def replacedBy(P, m, entries):
    """ours (3.7, lot 3.7-estab): the confirmed mesh m is replaced by the pending meshes `entries` — it is the mesh of
    a group they convert, or every wall it converted that still exists (3.8a: one cut in two, or deleted with its
    curve, covers nothing) is converted by them (a sub-group's mesh under a converted parent). A Shift+D copy is not
    (convertedHere)."""
    es = [P.items[i] for i in entries if i in P.items]
    if m.parent in {e.parent for e in es}:
        return True
    alive = [s for s in m.srcSurfs or () if s in P.items]
    covered = {s for e in es for s in e.srcSurfs}
    return bool(alive) and covered.issuperset(alive) and convertedHere(P, m)


def _in_tree(P, item, gid):
    g = P.items.get(item.parent) if item.parent is not None else None
    while g is not None and g.id != gid:
        g = P.items.get(g.parent) if g.parent is not None else None
    return g is not None


def convertedHere(P, m):
    """ours (3.8b): False for a Shift+D copy of a mesh. The copy names the walls of the group it was copied from
    (srcSurfs copied as they are, 9940) and sits over them on purpose: converting that group must not replace it (the
    original keeps both). Told apart from a mesh whose walls were moved to another group after converting (replaced
    by that group's conversion, lot 3.7-estab): the copy's group has walls of its own (the copies of the walls, a group
    duplicated), none of which it converted; the group the walls were moved out of has none left."""
    if m.parent is None:
        return True
    walls = [P.items[s] for s in m.srcSurfs or () if s in P.items]
    if not walls or any(_in_tree(P, w, m.parent) for w in walls):
        return True
    return not any(_in_tree(P, s, m.parent) for s in P.surfaces())


def regenConversion(P, T, crown_data=None, only=None):
    """port of regenConversion (hair-modeler.clean.html:14259): the pending meshes built again — ours: `only(mesh)`
    False keeps a mesh whose signature did not change. Returns the meshes rebuilt."""
    if not P.conv:
        return []
    out = []
    for iid in P.conv['entries']:
        m = P.items.get(iid)
        if m is not None and (only is None or only(m)):
            _build(P, m, T, crown_data); out.append(m)
    if out:
        P.rev += 1
    return out


def _outliner(P):
    """what renderOutliner, at the end of startConversion / confirmConversion / cancelConversion, does to the model in
    1.0.2: nameServiceCurves (1154) — the service curves Convert hid are shown again (they have no eye)"""
    from .groups import nameServiceCurves
    nameServiceCurves(P)


def adoptions(P):
    """ours (lot 1.1d): {pending mesh id: the confirmed mesh id it takes the place of} for Confirm, one to one — the
    replaced mesh of the same group first (of two there, the one sharing the most walls), else one whose walls the
    pending mesh covers (replacedBy). The Blender layer gives the pending mesh the confirmed mesh's object
    (meshes.adopt): what the user put on it stays."""
    c = P.conv
    if not c:
        return {}
    entries = [P.items[i] for i in c['entries'] if i in P.items]
    replaced = [P.items[i] for i in c.get('replaced') or () if i in P.items]
    out = {}
    for e in entries:                                               # (two passes: every mesh of the same group first,
        mine = set(e.srcSurfs or ())                                # the one sharing the most walls with it; a tie, the first)
        same = [r for r in replaced if r.parent == e.parent and r.id not in out.values()]
        if same:
            out[e.id] = max(same, key=lambda r: len(mine.intersection(r.srcSurfs or ()))).id
    for r in replaced:
        if r.id in out.values():
            continue
        alive = [s for s in r.srcSurfs or () if s in P.items]
        e = next((e for e in entries if e.id not in out and alive and set(e.srcSurfs or ()).issuperset(alive)), None)
        if e is not None:
            out[e.id] = r.id
    return out


def confirmConversion(P):
    """port of confirmConversion (hair-modeler.clean.html:14260): the pending meshes kept; ours: the confirmed meshes
    they replace are deleted. Returns the count confirmed (0: nothing pending)."""
    if not P.conv:
        return 0
    c = P.conv
    n = 0
    for iid in c['entries']:
        m = P.items.get(iid)
        if m is not None:
            m.pending = False; n += 1
    P.conv = None
    for iid in c.get('replaced') or ():
        if iid in P.items:
            edit.deleteItem(P, iid)
    _outliner(P)
    P.rev += 1
    return n


def cancelConversion(P):
    """port of cancelConversion (hair-modeler.clean.html:14261): the pending meshes deleted, what Convert hid shown
    again (ours: not what a mesh on screen now converted) and, ours, the confirmed meshes they would have replaced
    (unless their walls are on screen by then)."""
    if not P.conv:
        return False
    c = P.conv; P.conv = None
    for iid in c['entries']:
        edit.deleteItem(P, iid)
    # ours (lot 1.0.3, seed 808382 of the stress): what Convert hid comes back unless a mesh on screen now converted it —
    # Baked while the preview was up showed a confirmed mesh only partly covered by the pending ones (the parent's, when
    # a sub-group alone was converted) over walls Convert had hidden; Cancel keeps that mesh shown and its walls hidden
    on_screen = [m for m in meshes(P) if edit.isVisible(P, m) and convertedHere(P, m)]
    owners = {m.parent for m in meshes(P)}
    covered = {h.id for m in on_screen for h in meshSources(P, m, owners)}
    for iid in c['hidden']:
        it = P.items.get(iid)
        if it is not None and iid not in covered:
            it.visible = True
    # ours: a replaced mesh comes back unless walls (or crowns) it converted are on screen now — Live while the preview
    # was up showed them, and the mesh over its own walls was the mixed view of the manual test (lot 3.7-estab). Decided
    # after what Convert hid came back (lot 1.0.3): a mesh shown before Convert had its walls hidden then, so none of them
    # is among those; one whose walls were on screen with it (a mixed view, or every replaced mesh of a file reopened
    # with the preview up: restoreConv) stays hidden over them
    back = []
    for iid in c.get('shown') or ():
        it = P.items.get(iid)
        if it is not None and not any(P.items.get(s) is not None and edit.isVisible(P, P.items[s]) for s in [*(it.srcSurfs or ()), *(it.srcCrowns or ())]):
            back.append(it)
    for it in back:
        it.visible = True
    _outliner(P)
    P.rev += 1
    return True


# ---------------- ours: the incremental reconversion ----------------

def _conv_peek(P, it):
    """convOf (1596) without filling the group's conv (groupConv writes the defaults into it)"""
    p = P.parentOf(it) if it is not None else None
    while p:
        if p.type == 'group':
            c = dict(p.conv or {})
            for k in M.CONV_KEYS:
                c.setdefault(k, getattr(P.settings, k))
            if c.get('step') is None:
                c['step'] = ('density', c.get('density'), P.S.headH) if c.get('density') else defaultStep(P)
            return c
        p = P.parentOf(p)
    return settings_conv(P)


def _j(x):
    return json.dumps(x, sort_keys=True, default=str)


def crownSignature(P, k):
    """ours (lot 1.1e, issue #5): what crownData (crown.py, 6862) reads for crown k, so that a mesh made from a crown
    has a signature like any other — the crown's settings and loop step (crownStep: the step of its group's conv, the
    default step; nothing else of the conv), both connectors (their shape and their anchors, in order: connT), every curve with an end on a connector
    (crownCurves 6841, the loops at the lock curves' ends: its shape, the end anchors and what they are attached to), in
    curves() order, and the walls whose two curves start on the lower connector (their columns: segs, segPos) —
    shapes by the exact digest of the samples (rowhist). Reads nothing of the crown's eye (crownData does not)"""
    from .rowhist import _sample_digest
    part = P.items.get(k.part); low = P.items.get(k.low)
    if not part or not low:
        return [k.id, None]
    cache = {}

    def on(a, h):
        return (a.att is not None and a.att.c == h.id) or any(x is a for x in h.anchors)

    def end(a):
        return [a.id, [a.att.c, repr(a.att.t)] if a.att is not None else None, on(a, low), on(a, part)]
    curves = []; low_ends = set()
    for c in P.curves():
        A = c.anchors
        ends = [A[0], A[len(A) - 1]] if A else []
        conn = c is part or c is low
        touches = any(on(a, low) or on(a, part) for a in ends)
        if any(on(a, low) for a in ends):
            low_ends.add(c.id)
        if conn or touches:
            curves.append([c.id, bool(c.ring), bool(c.conn), len(A), _sample_digest(cache, c), [end(a) for a in ends], [a.id for a in A] if conn else None])
    walls = [[s.id, s.a, s.b, _j(s.p)] for s in P.surfaces() if s.a in low_ends and s.b in low_ends]
    return [k.id, k.part, k.low, _j(k.p), _j(_conv_peek(P, k).get('step')), repr(defaultStep(P)), curves, walls]   # (crownStep 6860: the step of its group's conv, else the default)


def meshSignature(P, mesh, grid_rev):
    """what buildConvMesh (14231) reads for this mesh, besides the row tables (whose share of it the revisions of the
    walls' grids stand for: `grid_rev`, wall id -> revision, bumped whenever the grid is computed again — or, lot 1.1e,
    wall id -> what the grid was computed from, by content: blender/live.py's grid_fp) and with what crownData reads
    for its crowns (crownSignature; lot 1.1e, issue #5: such a mesh had no signature and was rebuilt at every
    update). None when it cannot be said (a wall without a grid revision): rebuild."""
    walls = []
    live = []
    for iid in mesh.srcSurfs:
        s = P.items.get(iid)
        if s is None or s.type != 'surface':
            walls.append(None)
            continue
        r = grid_rev.get(s.id)
        if r is None:
            return None
        live.append(s)
        cap = CV.capOf(P, s)
        walls.append((s.id, s.a, s.b, r, _j(s.p), _j(_conv_peek(P, P.items.get(s.a) or s)), _j(_conv_peek(P, s)), (cap.id, _j(cap.p)) if cap else None))
    g = P.items.get(mesh.parent)
    sym = CV.symOf(P, g) if g is not None else None
    units = CV.mergeUnits(P, live, mesh.parent) if live else []
    crowns = [crownSignature(P, k) if (k := P.items.get(i)) is not None and k.type == 'crown' else None for i in mesh.srcCrowns or ()]
    return _j([walls, [[s.id for s in u] for u in units], [_conv_peek(P, u[0]) for u in units],
               _conv_peek(P, live[0]) if live else settings_conv(P), defaultStep(P),
               [sym, P.S.center.toArray()] if sym else None, *([crowns] if crowns else [])])


# ---------------- ours: the Live / Baked view ----------------

def meshSources(P, mesh, owners=None):
    """what a mesh converted, as startConversion's collect sees it (the walls, curves, guides and crowns of its group,
    not descending into a sub-group that has a mesh of its own)"""
    if mesh.parent not in P.items:
        return []
    if owners is None:
        owners = {m.parent for m in meshes(P)}
    out = CV.collect(P, mesh.parent, owners, SOURCE_TYPES)
    # (lot 1.1a-2: the service curves left out — 1.0.2 shows them again at the end of Convert / Confirm / Cancel,
    # renderOutliner -> nameServiceCurves 1154: they have no eye, so the view neither counts nor hides them)
    out = [h for h in out if not (h.type == 'curve' and h.junc)]
    # (4.4, the owner's decision: the walls the mesh replaced — its srcSurfs — wherever they live now; a Shift+P took one
    # out of the converted group after the Confirm and Baked left it showing under the mesh: seed 3 of the stress)
    have = {h.id for h in out}
    out += [P.items[s] for s in (mesh.srcSurfs or ()) if s not in have and s in P.items and P.items[s].type == 'surface']
    return out


def _shown_meshes(P):
    replaced = set((P.conv or {}).get('replaced') or ())
    return [m for m in meshes(P) if m.id not in replaced]


def viewOf(P):
    """'baked' (every mesh shown, what they converted hidden), 'live' (the other way round), 'mixed', or None (no
    mesh)"""
    ms = _shown_meshes(P)
    if not ms:
        return None
    owners = {m.parent for m in ms}
    src = [h for m in ms for h in meshSources(P, m, owners)]
    if all(m.visible for m in ms) and not any(h.visible for h in src):
        return 'baked'
    if not any(m.visible for m in ms) and all(h.visible for h in src):
        return 'live'
    return 'mixed'


def setView(P, mode):
    """the Live / Baked buttons: 'baked' shows the meshes and hides what they converted, 'live' the other way round.
    Returns the count of items switched."""
    ms = _shown_meshes(P)
    owners = {m.parent for m in ms}
    baked = mode == 'baked'
    n = 0
    for m in ms:
        if m.visible != baked:
            m.visible = baked; n += 1
        for h in meshSources(P, m, owners):
            if h.visible == baked:
                h.visible = not baked; n += 1
    if n:
        P.rev += 1
    return n
