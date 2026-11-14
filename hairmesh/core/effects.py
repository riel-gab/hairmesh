# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Ours (lot ux7, part 1 — docs/fase-3-plano-ciclo-nativo.md, "Estado"): what a control of the menus acts on in the
current state, so that a control that would change nothing right now is drawn dimmed with the reason and where to do
what was probably wanted (the rule of the project: no control may look as if it works when it has no effect).

Pure: reads the model, never changes it (no groupConv filling, no label set). Each answer follows the original's own
rule, cited per function. Where an exact answer would need the whole build, the answer leans to "it acts" (a control
is never dimmed while it may still change something): the per-lock conversion values count a group as read when any
curve or wall of a lock sits directly in it, where the build reads the group of the lock's first curve alone."""
from .js import truthy


def nearestGroup(P, it):
    """the group convOf (1845) takes the values of: the nearest group above `it`, else None (the project defaults
    S) — convOf's walk without its side effect (groupConv fills a missing conv)"""
    p = P.parentOf(it) if it is not None else None
    while p is not None:
        if p.type == 'group':
            return p
        p = P.parentOf(p)
    return None


def _locks(P):
    """the locks as buildRowTables makes them (surfaceComponents 1852, the curves with 2+ samples — 2+ anchors before
    they are sampled): [(curves, walls)]"""
    from .rows import surfaceComponents
    walls_of = {}
    for s in P.surfaces():
        walls_of.setdefault(s.a, []).append(s); walls_of.setdefault(s.b, []).append(s)
    out = []
    for comp in surfaceComponents(P):
        cs = [c for c in (P.items.get(i) for i in comp) if c is not None and (len(c.samples) >= 2 if c.samples else len(c.anchors) >= 2)]
        if not cs:
            continue
        ws = list({id(w): w for c in comp for w in walls_of.get(c, ())}.values())
        out.append((cs, ws))
    return out


def convReaders(P, gids):
    """(locks, crowns) whose conversion values come from one of the groups `gids` (ids; None in it = the project
    defaults): a lock reads the conv of the group above its first curve (CP = convOf(L.cs[0]), rows 2726;
    buildGroupMesh 13769 convOf(walls[0].a)) — counted here when any of its curves or walls sits in one of the groups
    (see the module's note) —, a crown its step (crownStep 11091: convOf(k))."""
    gids = set(gids)
    locks = 0
    for cs, ws in _locks(P):
        owners = {(g.id if g is not None else None) for g in (nearestGroup(P, x) for x in (*cs, *ws))}
        if owners & gids:
            locks += 1
    crowns = 0
    for k in P.of_type('crown'):
        g = nearestGroup(P, k)
        if (g.id if g is not None else None) in gids:
            crowns += 1
    return locks, crowns


def convTouch(P, gids, keys, project=False):
    """Ours (lot ux8, part 2): the curves whose locks must be rebuilt when the conversion values `keys` of the groups
    `gids` change — the curves of the locks convReaders counts (a superset of the locks that read them: CP =
    convOf(L.cs[0]), 2633) —, [] when nothing the live preview builds reads them, None for every curve. `project`:
    the project defaults S changed too (setConv: the step set in the Conversion tab is S.step, 9105), so the locks
    reading S — outside any group, or in a group whose conv lacks one of the keys (groupConv fills it from S, 1589) —
    count as well. mergeTrunks and mergeDist are read by Convert alone (buildGroupMesh 13769: the live preview never merges); a loop
    step is also read by a crown (crownStep 11091 — in crownData and the reach of the frame's sync alone, no row table:
    scene.signature_changes keeps the tables over it since lot 4.4; here every curve still, the cautious answer for a
    slider), by usedStep in a project without a step of its own
    ('project' of stepReaders: defaultStep moves for every lock), and by Optimize Anchors (no lock). The project's
    step is read by the trunk backfill as well (backfillTrunk runs with CP = S, 2726: its tolerances are 0.1 and 0.25
    x the step), whatever conv the groups hold: the locks of trunkLocks count then (test_sim_conv, 28/10/2026: with
    every group holding its own step, the project's Loop Step touched nothing and its drag rebuilt nothing, where
    the original's S.surfDirty rebuilds every lock and 45 curves of the example change). The Fill holes' candidates
    read the project step as well (skinCandidates' levelH with CP = S, 9351, lot 4.4): no lock to touch — the
    preview follows by its key (live.topo_previews), and a project step's change always runs the update."""
    keys = set(keys) - {'mergeTrunks', 'mergeDist'}                 # (1.0.2: mergeDist, read inside mergeTrunks alone)
    if not keys:
        return []
    gids = set(gids)
    if 'step' in keys and (convReaders(P, gids)[1] or not truthy(P.settings.step) and stepReaders(P, gids) in ('locks', 'project')):
        return None                                                 # (usedStep counts the step of the first group above every wall)
    if project and 'step' in keys and any(nearestGroup(P, k) is None for k in P.of_type('crown')):
        return None
    locks = _locks(P)
    trunk = trunkLocks(P, locks) if project and 'step' in keys else ()
    out = []; seen = set()
    for i, (cs, ws) in enumerate(locks):
        owners = {g for g in (nearestGroup(P, x) for x in (*cs, *ws))}
        ids = {(g.id if g is not None else None) for g in owners}
        if not ids & gids and i not in trunk and not (project and any(g is None or not g.conv or any(_reads_S(g.conv, k) for k in keys) for g in owners)):
            continue
        for c in cs:
            if c.id not in seen:
                seen.add(c.id); out.append(c.id)
    return out


def _reads_S(conv, k):
    """whether a group's conv takes value k from the project defaults S: a key it lacks (groupConv fills it, 1668),
    and for the step also a null one (groupConv) or 0 (levelH 1846: CP.step || defaultStep())"""
    return k not in conv or k == 'step' and not truthy(conv.get('step'))


def trunkLocks(P, locks=None):
    """the indices (in the order of _locks, or of `locks` when given) of the locks the trunk backfill may give a loop:
    a curve of the lock shares a segment — two anchors in a row on both curves — with a curve of another lock (the
    pairs of backfillTrunk, 3852, read from the anchors alone: a superset of the pairs of the sampled curves)"""
    locks = _locks(P) if locks is None else locks
    lockOf = {}; byA = {}
    for i, (cs, _ws) in enumerate(locks):
        for c in cs:
            lockOf[c.id] = i
            for a in c.anchors:
                byA.setdefault(a.id, []).append(c)
    out = set()
    for i, (cs, _ws) in enumerate(locks):
        for A in cs:
            for B in {id(B): B for a in A.anchors for B in byA[a.id]}.values():
                j = lockOf[B.id]
                if j == i or i in out and j in out:
                    continue
                at = {}
                for k, a in enumerate(B.anchors):
                    at.setdefault(a.id, k)                          # (indexOf: the first)
                if any(A.anchors[k].id in at and A.anchors[k + 1].id in at and abs(at[A.anchors[k].id] - at[A.anchors[k + 1].id]) == 1
                       for k in range(len(A.anchors) - 1)):
                    out.add(i); out.add(j)
    return out


def stepReaders(P, gids):
    """what reads a loop step kept in one of the groups `gids` — 'locks', 'crown' (convReaders), 'curves' (the
    tolerance Optimize Anchors takes from a curve's group: optimizeAnchors 5557, convOf(c).step), 'project' (in a
    project with no loop step of its own, S.step null: usedStep 1821, the step most walls' groups use, is then the
    project loop step — a group counts when it is the first group above a wall that has a step, or would be once its
    slider gives it one) — or None"""
    locks, crowns = convReaders(P, gids)
    if locks:
        return 'locks'
    if crowns:
        return 'crown'
    gids = set(gids)
    for c in P.curves():
        g = nearestGroup(P, c)
        if g is not None and g.id in gids:
            return 'curves'
    if not truthy(P.settings.step):
        for s in P.surfaces():
            p = P.parentOf(s)
            while p is not None:
                if p.type == 'group' and p.id in gids:
                    return 'project'
                if p.type == 'group' and p.conv and truthy(p.conv.get('step')):
                    break
                p = P.parentOf(p)
    return None


def _units(P, surfs, root, flip=None):
    """the pieces a mesh's walls come out in: the ported mergeUnits (14220); with `flip`, the same rule with that
    group's Container label taken as switched (what the switch would do)"""
    if flip is None:
        from .convert import mergeUnits
        return mergeUnits(P, surfs, root)
    units = {}
    for s in surfs:
        chain = []; p = P.parentOf(s)
        while p is not None:
            chain.insert(0, p)
            if p.id == root:
                break
            p = P.parentOf(p)
        key = 'all'
        for i, g in enumerate(chain):
            on = bool(g.labels and g.labels.get('container'))
            if flip is not None and g is flip:
                on = not on
            if on:
                key = chain[i + 1].id if i + 1 < len(chain) else str(g.id) + ':own'
        units.setdefault(key, []).append(s)
    return list(units.values())


def _meshes(P):
    """the meshes Convert makes with the current ticks (startConversion 14243, collect 13214): [(group, walls)] — the
    loop of convert.conversionPlan, keeping the walls"""
    from .convert import collect
    gids = [i for i in P.settings.convChecked if i in P.items]
    checked = set(gids)
    out = []
    for gid in gids:
        surfs = collect(P, gid, checked, ['surface']); crs = collect(P, gid, checked, ['crown'])
        if surfs or crs:
            out.append((P.items[gid], surfs))
    return out


def mergeReaders(P, gids):
    """how many pieces of the meshes Convert makes read Merge Shared Trunks from one of the groups `gids`:
    buildGroupMesh (13769) merges a piece's locks when it has 2+ of them and convOf(surfs[0]) — the group of the
    piece's first wall — says so (the live preview never merges: only Convert)"""
    from .convert import lockGroups
    from .surfaces import hasTwin, twinWall
    gids = set(gids)
    n = 0
    for g, surfs in _meshes(P):
        for unit in _units(P, surfs, g.id):
            walls = [w for s in unit for w in ((s, twinWall(s)) if hasTwin(s) else (s,))]
            if len(lockGroups(P, walls)) < 2:
                continue
            owner = nearestGroup(P, unit[0])
            if (owner.id if owner is not None else None) in gids:
                n += 1
    return n


def meshOf(P, g):
    """the ticked group whose mesh the walls lying in g go into: g itself when ticked, else the nearest ticked group
    above it (collect 13214 walks the sub-groups that are not ticked); None when no mesh takes them"""
    checked = set(P.settings.convChecked)
    p = g
    while p is not None:
        if p.type == 'group' and p.id in checked:
            return p
        p = P.parentOf(p)
    return None


def mergeWhy(P, g):
    """why g's Merge Shared Trunks changes nothing (None: it acts): ('off', None) — Convert takes no wall of g (it is
    not ticked, nor inside a ticked group); ('one', m) — the piece of m's mesh holding g's walls has a single lock;
    ('from', m, X) — that piece takes the switch from X, the group of its first wall (buildGroupMesh 9578); ('none',
    None) — g has no walls of its own"""
    if mergeReaders(P, {g.id}):
        return None
    from .convert import lockGroups
    from .surfaces import hasTwin, twinWall
    own = [s for s in P.surfaces() if nearestGroup(P, s) is g]
    if not own:
        return ('none', None)
    m = meshOf(P, g)
    if m is None:
        return ('off', None)
    own_ids = {id(s) for s in own}
    for mesh, surfs in _meshes(P):
        if mesh is not m:
            continue
        for unit in _units(P, surfs, m.id):
            if not any(id(s) in own_ids for s in unit):
                continue
            walls = [w for s in unit for w in ((s, twinWall(s)) if hasTwin(s) else (s,))]
            if len(lockGroups(P, walls)) < 2:
                return ('one', m)
            return ('from', m, nearestGroup(P, unit[0]))
    return ('off', None)


def containerActs(P, g):
    """whether switching g's Container label changes the pieces of a mesh Convert makes (mergeUnits 14220: a Container
    keeps each of its sub-groups — and its own walls — a piece apart)"""
    for m, surfs in _meshes(P):
        a = _units(P, surfs, m.id)
        b = _units(P, surfs, m.id, flip=g)
        if {frozenset(id(s) for s in u) for u in a} != {frozenset(id(s) for s in u) for u in b}:
            return True
    return False


def symActs(P, g):
    """whether g's Symmetry switch changes a mesh Convert makes: buildConvMesh (14231) mirrors a mesh by the nearest
    group with symmetry on, from the mesh's group up (symOf 14035) — g counts for the meshes of g and of the groups
    inside it with no group switched on between. The original also shows a live mirrored copy (symChanged 14066),
    which the port makes in phase 5: until then the switch acts on Convert alone."""
    for m, surfs in _meshes(P):
        p = m
        while p is not None:
            if p is g:
                return True
            if p.type == 'group' and p.sym and p.sym.get('on'):
                break                                               # a nearer group decides this mesh
            p = P.parentOf(p)
    return False


def symWhy(P, g):
    """why g's Symmetry switch changes nothing (None: it acts): ('off', None) — no mesh Convert makes holds g's walls
    or its sub-groups'; ('mesh', m) — g's walls go into the mesh of m, a group above g, which follows its own
    symmetry and its ancestors'; ('inside', X) — every mesh of g and of the groups inside it follows a nearer group X
    with symmetry on"""
    if symActs(P, g):
        return None
    mine = [mesh for mesh, _s in _meshes(P) if mesh is g or isAbove(P, g, mesh)]
    if mine:
        for mesh in mine:
            p = mesh
            while p is not None and p is not g:
                if p.type == 'group' and p.sym and p.sym.get('on'):
                    return ('inside', p)
                p = P.parentOf(p)
        return ('off', None)
    m = meshOf(P, g)
    return ('mesh', m) if m is not None else ('off', None)


def isAbove(P, g, it):
    """whether the group g lies above `it`"""
    p = P.parentOf(it)
    while p is not None:
        if p is g:
            return True
        p = P.parentOf(p)
    return False


def modeActs(P, g):
    """whether g's Curves & anchors mode (Auto / Show / Hide) shows or hides anything: guidesShown (16440) reads the
    mode of an item's direct parent only — the curves (crown connectors too) and guides lying in g itself, never in
    its sub-groups"""
    for iid in g.children:
        it = P.items.get(iid)
        if it is not None and ((it.type == 'curve' and not it.ring) or it.type == 'guide'):
            return True
    return False


def looseActs(P, g, loose=None):
    """whether "Free curves visible" shows anything: in Auto mode, for the free curves lying in g itself (guidesShown
    11920, hasLoose 16468; looseCurveIds 16451 — joined to no wall, no modifier, no crown)"""
    if (g.guides or 'auto') != 'auto':
        return False
    if loose is None:
        from .groups import looseCurveIds
        loose = looseCurveIds(P)
    return any(iid in loose for iid in g.children)


def matchActs(P, g):
    """whether Match Project Step changes a step: matchProjectStep (1833) gives g and the groups inside it the
    project loop step"""
    from .groups import convShown, projectStep, subGroups
    st = projectStep(P)
    return any(convShown(P, x)['step'] != st for x in subGroups(P, g))


# ---- curves and anchors (the curve panel 8690-8731, the anchor panel 8629-8636) ----

def _curveShapes(c):
    """whether a curve's Roundness / Smoothing can change it at all: an open curve of 3+ anchors that is not a
    junction curve (roundAt 962, curveHandles 1001 / 929: a closed curve, a ring, fewer than 3 anchors or a junction
    curve — juncHandles 976 — never read them)"""
    return not c.ring and not c.closed and not c.junc and len(c.anchors) >= 3


def curveActs(P, cs, key):
    """whether the curve slider `key` ('round', 'fair', 'asm') changes one of the curves cs. Even out anchors
    (evenAnchors 1093) moves only the free anchors of a curve of 3+ anchors that is not a ring: not an open curve's
    ends, not an attached anchor, not an anchor shared with another curve."""
    if key in ('round', 'fair'):
        return any(_curveShapes(c) for c in cs)
    all_ = P.curves()
    for c in cs:
        if c.ring or len(c.anchors) < 3:
            continue
        n = len(c.anchors)
        for i, a in enumerate(c.anchors):
            if (not c.closed and (i == 0 or i == n - 1)) or a.att is not None:
                continue
            if any(h is not c and a in h.anchors for h in all_):
                continue
            return True
    return False


def smoothActs(P, anchors):
    """whether the Smoothness of the anchors changes a curve: curveHandles (1001) scales every handle by it — ends,
    frozen and attached anchors included —; only a junction curve (juncHandles 976) never reads it"""
    ids = {a.id for a in anchors}
    return any(not c.junc for c in P.curves() if any(a.id in ids for a in c.anchors))


def recalcActs(P, anchors):
    """whether Recalculate changes anything: recalcHandles (7264) drops the anchors' frozen handles (c.ovr) and sets
    their smoothness back to 1 — nothing to do when none is frozen and all are at 1 (the original says "Handles
    recalculated" anyway)"""
    ids = {a.id for a in anchors}
    if any(a.smooth != 1 for a in anchors):
        return True
    return any(iid in ids for c in P.curves() for iid in c.ovr)


# ---- walls (the surface panel 8745-8822) ----

def fadeActs(P, sf, which):
    """whether a Bulge Fade slider shapes the wall: surfaceData (4006) — the root fade (bfade, a wall rooted on a
    crown's lower connector) scales the bulge and blends the root row onto the connector, so it acts with 2+ width
    segments, or through a second surface of 2+ segments with a bulge (twinWall 4217 copies bfade); the junction fade
    (jfade) only scales the bulge (it also blends the root row on a crown-rooted wall) and the second surface does not
    copy it: it acts with 2+ segments and a bulge, or 2+ segments on a crown-rooted wall"""
    from .surfaces import wallCrownRoot
    if wallCrownRoot(P, sf) is not None:
        return True                                                 # (the review: the fade also weighs the blend onto the connector, delta * (1 - f), whose edge values are not exactly 0)
    if which == 'bfade':
        return False                                                # (only read on a crown-rooted wall: the slider shows there alone)
    segs = max(1, int(sf.p.get('segs') or 0))
    return segs >= 2 and truthy(sf.p.get('bulge'))


def detachActs(P, g):
    """whether Detach from Neighbouring Groups changes anything: detachGroup (4272) gives anchors of their own to the
    curves of g (and of its sub-groups) welded to curves outside it, and lets go their ends attached to another group's
    crown connector — nothing when there is none (the original's toast "isn't welded to other groups", 4290)"""
    from .groups import curvesIn
    inside = curvesIn(P, g)
    inside_set = set(inside)
    out = {id(a) for c in P.curves() if c not in inside_set for a in c.anchors}
    for c in inside:
        for a in c.anchors:
            if id(a) in out:
                return True
            if a.att is not None:
                h = P.items.get(a.att.c)
                if h is not None and h not in inside_set:
                    return True
    return False


# ---- the Conversion list's buttons (ours: Tick Top Groups, Tick Every Group, Uncheck All, Only This) ----

def tickAllActs(P, on):
    """whether Tick Every Group (on) / Uncheck All (off) changes the ticks: tickAll appends the unticked groups of the
    list in order, or removes every tick"""
    from .groups import groupsOf
    if not on:
        return any(i in P.items for i in P.settings.convChecked)
    ticked = set(P.settings.convChecked)
    return any(g.id not in ticked for g in groupsOf(P))


def tickTopActs(P):
    """whether Tick Top Groups changes the ticks (the top groups alone, in list order)"""
    from .groups import convTree
    return [g.id for g, depth, _s, _v in convTree(P) if depth == 0] != list(P.settings.convChecked)


def tickOnlyActs(P, g):
    """whether Only This changes the ticks (this group alone)"""
    return list(P.settings.convChecked) != [g.id]


# ---- the Topology tab (capControls 10568, tipControls 10618) ----

def capState(k, found):
    """what a Cap's controls change now (ours, 4.4: dimmed with the reason): 'hidden' (its eye: rebuildCaps skips it,
    9530 — nothing acts), 'no_ends' (Start and End both off: capGrid closes nothing, 8649 — only they act), 'no_lock'
    (its group has no closed lock: capOf finds no ring of walls — nothing acts), else 'ok'. `found`: the preview's
    answer (live.topo_found), None when there is none of this state (it acts)"""
    if not k.visible:
        return 'hidden'
    if k.p.get('start') is False and k.p.get('end') is False:
        return 'no_ends'
    return 'no_lock' if found == 0 else 'ok'


def capBevelActs(p):
    """whether Segments and Roundness shape the cap: capGrid adds bevel rings only with a Size (8651: size > 0)"""
    return float(p.get('size') or 0) > 0


def esState(P, k):
    """what an Even loops / Proxy shell item (1.0.3) does to the loops now, by what shellSetsBuild skips (3058-3060 of
    1.0.3: the eye, a shell not converted, no mask) and covers (3061: a weight of 50 % or more); its sheet says it.
    'no_group' (evenProps 10748: in no group it works on nothing), 'hidden', 'not_converted' (shellControls 10786: a
    shell still painting), 'weak' (painted, but nowhere 50 %), 'not_painted', 'ok'"""
    g = P.items.get(k.parent)
    if g is None or g.type != 'group':
        return 'no_group'
    if k.visible is False:
        return 'hidden'
    if k.type == 'shell' and not truthy(getattr(k, 'baked', None)):
        return 'not_converted'
    W = list((k.mask or {}).values())
    if any(v >= 0.5 for w in W for v in w):
        return 'ok'
    return 'weak' if any(v > 0.02 for w in W for v in w) else 'not_painted'


def tipActs(k):
    """whether a Density's slider changes the loops: lockTip reads it where its mask is painted, and only shown
    (tipOf skips no eye — the frame's syncVisuals hides the item; the original's slider acts on the stored value: we
    dim it while nothing is painted, 4.4)"""
    return bool(k.mask) and k.visible


# ---- the list of the Extras (ours: docs/features-adicionadas.md) ----

def activeExtra(P, sel_ids):
    """the active Extra: the last selected item when it is one (the line the list highlights, the sheet below it)"""
    it = P.items.get(sel_ids[len(sel_ids) - 1]) if sel_ids else None
    return it if it is not None and it.type == 'xmod' else None


def extraPaintState(x):
    """what the weight brush can do on the active Extra x (its button dims with the reason): 'none' (no active Extra),
    'no_weights' (Noise, Curls, an imported mesh's Extra: paintMask finds no weights to paint, 6618), 'hidden' (its
    eye: paintMask skips it), else 'ok'"""
    if x is None:
        return 'none'
    if x.mask is None or x.mesh is not None:
        return 'no_weights'
    return 'hidden' if x.visible is False else 'ok'


# ---- 1.1f: the flat locks (ribCurveProps 7077, ribAnchorProps 7055) ----

def _weights(v):
    return [w for w in (v or ()) if w is not None and w == w]       # (a short weight list reads NaN past its end: no weight)


def ribExtruded(c):
    """an Extrude weight that shapes the lock: ribExtShape's own test (5941: some weight above 0.002)"""
    return any(w > 0.002 for w in _weights(c.rib.get('ext'))) if c.rib else False


def ribOffset(c):
    R = c.rib or {}
    return any(abs(w) > 0.002 for k in ('off', 'offL', 'offR') for w in _weights(R.get(k)))


def ribExtActs(c):
    """Invert, Depth and Extrude's Fade to Tip change the lock only where it is extruded (ribExtShape reads them only
    then); a lock not extruded yet: dimmed, "Extrude must be painted first" (the original's note, 6139)"""
    return ribExtruded(c)


def ribOffFadeActs(c):
    # Offset's Fade to Tip: an extruded lock with an Offset painted (fo multiplies the offsets alone, 5941)
    return ribExtruded(c) and ribOffset(c)


def ribBrushActs(c, kind):
    """the brush of a section paints what shapes the lock: Extrude always; Offset and Glue move the extruded edges, so
    they need Extrude painted first (7116)"""
    return kind == 'ext' or ribExtruded(c)


def ribClearActs(c, kind):
    """Clear (6118) changes the lock when its section has weights stored"""
    from .rib import CLEAR_KEYS
    R = c.rib or {}
    return any(bool(R.get(k)) for k in CLEAR_KEYS[kind])


def ribConvertActs(c):
    """Convert makes curves from the lock's shape: a lock with no shape yet (fewer than 2 points of its frame) says
    "The lock has no shape yet" (6157, 6184). In doubt (not built yet — dirty, or fresh from the text with no samples):
    it acts; the click says what the core finds"""
    return c.ribF is None and (c.ribDirty or not c.samples) or c.ribF is not None and len(c.ribF) >= 2


# ---- guides (guideControls 12807) ----

RESET_TOL = 1e-9                                                    # of the outer radius: rings this near their circles are circles (discResetActs)


def discResetActs(P, g):
    """whether Reset Deformations changes the disc: resetDisc (1250) puts both rings back on circles of their radii,
    smoothness 1, no frozen handle. Ours (4.4, the owner's request: "without a deformation it changes nothing and
    rebuilds nothing"): rings within RESET_TOL of the outer radius of those circles count as circles — the original
    moves them by its rounding alone (about 1e-13 on a radius of 300, a fresh disc too) and rebuilds every lock the
    disc pierces for that"""
    from .guidetool import resetTargets
    t = resetTargets(P, g)
    if t is None:
        return False
    rs, targets = t
    if any(c.ovr for c in rs):
        return True
    tol = RESET_TOL * max(abs(g.p['rOut']), 1)
    return any(a.smooth != 1 or a.pos.distanceTo(q) > tol for a, q in targets)


# ---- R on the anchors (lot 1.0.2, issue #1) ----

def rotateActs(P, tg):
    """whether R (a turn of gizmoTargets' targets) changes anything: the selected anchors turn about the last one
    (gizmoTargets 4567), so one anchor alone stays where it is (gizmoStep 4668-4673: start x M); only its frozen
    handles (c.ovr, 4673) turn with it. Items (curves, walls, groups) and two or more anchors: it acts"""
    if tg is None:
        return False
    if tg.items or tg.meshes or tg.imeshes or len(tg.anchors) != 1:
        return True
    aid = tg.anchors[0].id
    return any(o.in_ is not None or o.out is not None for c in P.curves() for k, o in c.ovr.items() if k == aid)


# ---- ours (lot 1.1a): curves made with tools of the original this version does not have yet ----
# (key of the curve's field, the tool's name). Lot 1.1f: empty — the Flat Lock (c.rib) and the X junction's edge
# curves (c.path), the two of 1.0.2, are tools of the add-on now; the mechanism waits for the fields of a later version.
LATER_TOOLS: tuple[tuple[str, str], ...] = ()


def laterTools(P):
    """[(tool name, curves)] for every tool of the original the add-on lacks whose curves are in the project
    (LATER_TOOLS): kept and written back as read, not editable here"""
    out = []
    for key, name in LATER_TOOLS:
        n = sum(1 for c in P.curves() if truthy(getattr(c, key, None)))   # (JS truth: [] and {} count)
        if n:
            out.append((name, n))
    return out


def laterToolsText(P):
    """(the short status line, the tooltip) for laterTools, or None when there is none (G2: the reason in the tooltip)"""
    t = laterTools(P)
    if not t:
        return None
    n = sum(k for _name, k in t)
    names = ', '.join(f'{name} ({k} curve{"" if k == 1 else "s"})' for name, k in t)
    return (f'{n} curve{"" if n == 1 else "s"} from newer tools, kept',
            f'This project has curves made with tools this version does not have yet: {names}. '
            'They are kept and saved back as they are; editing them as such needs a later version.')
