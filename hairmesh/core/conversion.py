# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
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
    """port of startConversion (hair-modeler.clean.html:10010): (made, message). The caller has the row tables T
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
    P.rev += 1
    return None


def restoreConv(P):
    """ours: a file with a conversion pending has the original's {entries, hidden} alone (restore, 1265). What Convert said
    of the confirmed meshes is worked out again: those the pending meshes replace, hidden, deleted by Confirm. The file
    does not say which of them were on screen: Cancel shows them all (its own test of their walls stands)."""
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
    """port of regenConversion (hair-modeler.clean.html:10026): the pending meshes built again — ours: `only(mesh)`
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


def confirmConversion(P):
    """port of confirmConversion (hair-modeler.clean.html:10027): the pending meshes kept; ours: the confirmed meshes
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
    P.rev += 1
    return n


def cancelConversion(P):
    """port of cancelConversion (hair-modeler.clean.html:10028): the pending meshes deleted, what Convert hid shown
    again (and, ours, the confirmed meshes they would have replaced)."""
    if not P.conv:
        return False
    c = P.conv; P.conv = None
    for iid in c['entries']:
        edit.deleteItem(P, iid)
    # ours: a replaced mesh comes back unless walls (or crowns) it converted are on screen now — Live while the preview
    # was up showed them, and the mesh over its own walls was the mixed view of the manual test (lot 3.7-estab); decided
    # before what Convert hid comes back (those were on screen before Convert, with the mesh already hidden)
    back = []
    for iid in c.get('shown') or ():
        it = P.items.get(iid)
        if it is not None and not any(P.items.get(s) is not None and edit.isVisible(P, P.items[s]) for s in [*(it.srcSurfs or ()), *(it.srcCrowns or ())]):
            back.append(it)
    for iid in c['hidden']:
        it = P.items.get(iid)
        if it is not None:
            it.visible = True
    for it in back:
        it.visible = True
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


def meshSignature(P, mesh, grid_rev):
    """what buildConvMesh (9998) reads for this mesh, besides the row tables (whose share of it the revisions of the
    walls' grids stand for: `grid_rev`, wall id -> revision, bumped whenever the grid is computed again). None when
    it cannot be said (a crown: crownData reads more; a wall without a grid revision): rebuild."""
    if mesh.srcCrowns:
        return None
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
    return _j([walls, [[s.id for s in u] for u in units], [_conv_peek(P, u[0]) for u in units],
               _conv_peek(P, live[0]) if live else settings_conv(P), defaultStep(P),
               [sym, P.S.center.toArray()] if sym else None])


# ---------------- ours: the Live / Baked view ----------------

def meshSources(P, mesh, owners=None):
    """what a mesh converted, as startConversion's collect sees it (the walls, curves, guides and crowns of its group,
    not descending into a sub-group that has a mesh of its own)"""
    if mesh.parent not in P.items:
        return []
    if owners is None:
        owners = {m.parent for m in meshes(P)}
    out = CV.collect(P, mesh.parent, owners, SOURCE_TYPES)
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
