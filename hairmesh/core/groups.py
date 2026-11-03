# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Groups and conversion parameters (phase 3.6): the data part of what the original's outliner and panels do with
groups — creating one, moving items between groups (the outliner's drag and drop), the group panel (colour, the
curves-and-anchors mode, the labels, the loop step against the project's, Detach from neighbouring groups, the
symmetry flags) and the Conversion panel (the ticked groups, the sliders that edit them, the loop step set by the
reference cube), plus what the frame loop derives from them for the display (the active groups, the free curves,
guidesShown). Pure Python, no bpy; selection, undo steps (commit), toasts and redraws belong to the caller. Line
numbers refer to hair-modeler.clean.html, 1.0.1."""
from . import model as M
from .curves import markAnchorsDirty
from .edit import addItem, newAnchor
from .hairmesh_io import defaultStep
from .rowbase import groupConv

CONV_KEYS = M.CONV_KEYS
SYM_AXES = ('x', 'y', 'z')
GUIDE_MODES = ('auto', 'show', 'hide')                              # GUIDE_MODES (11938): Curves & anchors: Auto / always shown / hidden
LABELS = ('container', 'loose')                                     # the group labels the 1.0.1 panel offers (8658): Container, Free curves visible
DEFAULT_COLOR = '#4a7fd6'                                           # the colour input's value for a group without one (8645)


# ======================= the tree (753, 8557) =======================

def isDescendant(P, iid, ancestor_id):
    # port of isDescendant (753): `iid` is `ancestor_id` or lies under it
    it = P.items.get(iid)
    while it is not None:
        if it.id == ancestor_id:
            return True
        it = P.parentOf(it)
    return False


def outlinerOrder(P):
    # port of outlinerOrder (8557): every item id in the order the outliner shows them (depth first, file order)
    out = []

    def walk(ids):
        for iid in ids:
            out.append(iid)
            it = P.items.get(iid)
            if it is not None and it.type == 'group':
                walk(it.children)
    walk(P.roots)
    return out


def groupsOf(P):
    """the hair groups the Conversion panel lists (renderConvList, 9031): not the Guides / Meshes groups"""
    return [g for g in P.groups() if not g.guidesGroup and not g.meshesGroup]


def subGroups(P, g):
    """g and every group nested in it, depth first (the `down` of convTargets and matchProjectStep)"""
    out = []

    def down(x):
        out.append(x)
        for cid in x.children:
            c = P.items.get(cid)
            if c is not None and c.type == 'group':
                down(c)
    down(g)
    return out


def curvesIn(P, g):
    """the curves inside g, at any depth (the `walk` of detachGroup, 3002)"""
    out = []

    def walk(ids):
        for iid in ids:
            it = P.items.get(iid)
            if it is None:
                continue
            if it.type == 'group':
                walk(it.children)
            elif it.type == 'curve':
                out.append(it)
    walk(g.children)
    return out


# ======================= creating, moving, renaming (7919, 8568, 8278) =======================

def orderRoots(P):
    # port of orderRoots (8207): the Imported, Meshes and Guides groups first (a stable sort: the rest keeps its order)
    def rank(iid):
        it = P.items.get(iid)
        if it is None or it.type != 'group':
            return 2                                                # (a curve at the root: `it.importGroup` is undefined -> falsy)
        return -1 if it.importGroup else 0 if it.meshesGroup else 1 if it.guidesGroup else 2
    P.roots.sort(key=rank)


def panelsRendered(P, selected=None):
    """What the original's re-rendering of the outliner does to the model (renderOutliner, 8211-8261, called after
    every outliner / panel action): orderRoots; renderProps, whose group panel fills the selected group's conv
    from the defaults (groupConv, 8668: `selected` = the last selected item, the port's active collection) and whose
    extra panel fills an Extra modifier's missing settings (xmodProps, 7039; 4.7); and renderConvList -> syncConvUI,
    which fills the first ticked group's conv (9097). Idempotent after the first fill; the parity scripts depend on
    calling it where the original renders."""
    orderRoots(P)
    it = P.items.get(selected) if selected is not None else None
    if it is not None and it.type == 'group' and not it.guidesGroup and not it.meshesGroup:
        groupConv(P, it)
    if it is not None and it.type == 'tube':                        # (4.6: a modifier's sheet, tubeProps 7153-7159: its radius reference, its three profiles)
        from .tube import ensureTubeRef, ownProfs
        ensureTubeRef(P, it); ownProfs(it)
    elif it is not None and it.type == 'xmod':
        from .tube import xmodProps                                 # (tube imports this module)
        xmodProps(P, it)
    t = convTargets(P)
    if t:
        groupConv(P, t[0])


def createGroup(P, parent=None, name=None):
    """The pie's "Create empty group" (7919; the outliner's "+", 3884): addItem('group'), ticked for conversion
    (S.convChecked.add), and selected — which renders its panel, and the panel fills its conv from the defaults
    (groupConv, 8668). `parent` is an addition of the port (the original creates at the root and the user drags
    the group in, moveItems 'into'); `name` is the outliner's rename (8278)."""
    g = addItem(P, 'group', parent)
    P.settings.convChecked.append(g.id)
    panelsRendered(P, g.id)
    if name is not None:
        renameItem(P, g, name)
    return g


def moveItems(P, ids, target_id=None, zone='into'):
    """port of moveItems (8568), the outliner's drag and drop: `zone` = 'into' (target = the group to put them in),
    'root' (the top level), 'before' / 'after' (target = the sibling to put them next to). The head and the target
    itself are skipped; a group cannot go inside itself (returns (moved ids, "Cannot put a group inside itself")).
    Returns (the ids moved, None)."""
    ids = [i for i in ids if i != target_id and i in P.items and P.items[i].type != 'head']
    if not ids:
        return [], None
    new_parent = None
    if zone != 'root':
        t = P.items.get(target_id)
        if t is None:
            return [], None
        new_parent = t.id if zone == 'into' else t.parent
    if new_parent is not None and any(isDescendant(P, new_parent, i) for i in ids):
        return [], 'Cannot put a group inside itself'
    for i in ids:
        it = P.items[i]
        old = P.childrenOf(it.parent)
        old.remove(i)                                               # splice(indexOf): the first occurrence
        it.parent = new_parent
    arr = P.childrenOf(new_parent)
    if zone in ('before', 'after'):
        k = arr.index(target_id) + (1 if zone == 'after' else 0)
        arr[k:k] = ids
    else:
        arr.extend(ids)
    if zone == 'into':
        P.items[new_parent].expanded = True
    P.gen += 1; P.rev += 1                                          # parents changed: memos keyed on gen (anchor_curves, usedStep, convOf)
    return ids, None


def parentSelection(P, order):
    """port of parentSelection (hair-modeler.clean.html:8179), P: the selected items (S.sel in pick order) go into
    the LAST one, which must be a group; an item whose ancestor is selected too goes with it. Returns (ids, target
    id, None) for moveItems(ids, target, 'into'), or ([], None, the original's toast) when the selection does not
    serve — the key passes on then (ours, D4.3)."""
    order = [i for i in order if i in P.items]
    if len(order) < 2:
        return [], None, 'Select the objects, then the new parent (a group) last'
    target = P.items[order[len(order) - 1]]
    if target.type != 'group':
        return [], None, 'The last selected object must be a group'
    rest = order[:-1]
    ids = [i for i in rest if not any(o != i and isDescendant(P, i, o) for o in rest)]
    if any(isDescendant(P, target.id, i) for i in ids):              # (moveItems would refuse it: "Cannot put a group inside itself")
        return [], None, 'Cannot put a group inside itself'
    return ids, target.id, None


def unparentSelection(P, order):
    """port of unparentSelection (8187), Shift+P: the top selected items that live in a group go to the root level
    (moveItems(ids, null, 'root')). Returns (ids, None) or ([], the original's toast)."""
    from .duplicate import topSelected
    ids = [i for i in topSelected(P, order) if P.items[i].parent is not None]
    if not ids:
        return [], 'Nothing to take out of a group'
    return ids, None


def itemsToMoveTogether(P, ids, target_id=None, zone='into'):
    """U3 of docs/fase-3-ux-revisao.md (ours, 16/10/2026; the original's moveItems takes the ids it is given and
    nothing more, so a dragged curve leaves its wall behind and the lock ends up with the loops of one group and
    the mesh of another): the ids to move together with `ids` into the target of `zone` — every wall whose two
    curves go (one of them moving, the other moving or already in the target group) and the two curves of every
    moving wall. Items inside a moving group go with it anyway and are never listed twice. The result is the input
    order, then the walls added in file order, then their curves; feed it to moveItems. Pure."""
    ids = [i for i in ids if i in P.items]
    if zone == 'root':
        new_parent = None
    elif zone == 'into':
        new_parent = target_id
    else:
        t = P.items.get(target_id)
        new_parent = t.parent if t is not None else None
    moving = set(ids)                                               # the ids and everything inside the moving groups
    for i in ids:
        it = P.items[i]
        if it.type == 'group':
            for x in descendantIds(P, it):
                moving.add(x)
    out = list(ids)

    def add(iid):
        if iid not in moving and iid in P.items:
            moving.add(iid); out.append(iid)
    walls = [sf for sf in P.surfaces() if sf.id in set(ids)]        # the walls given: their curves come along
    for sf in P.surfaces():
        if sf.id in moving:
            continue
        a_in = sf.a in moving; b_in = sf.b in moving
        a_there = sf.a in P.items and P.items[sf.a].parent == new_parent
        b_there = sf.b in P.items and P.items[sf.b].parent == new_parent
        if (a_in or b_in) and (a_in or a_there) and (b_in or b_there):
            add(sf.id)
    for sf in walls:
        add(sf.a); add(sf.b)
    return out


def descendantIds(P, g):
    """every item id inside a group, at any depth (groups included)"""
    out = []

    def walk(ids):
        for iid in ids:
            it = P.items.get(iid)
            if it is None:
                continue
            out.append(iid)
            if it.type == 'group':
                walk(it.children)
    walk(g.children)
    return out


def renameItem(P, it, name):
    """the outliner's rename (startRename, 8278): the trimmed text, or the old name when empty. Returns whether it changed."""
    nn = name.strip() or it.name
    ch = nn != it.name
    it.name = nn
    if ch:
        P.rev += 1
    return ch


# ======================= the group panel (8640-8690) =======================

def setColor(P, g, css):
    """the colour input (8646) / "Reset colour" (8649): '#rrggbb' or None"""
    g.color = css if css else None
    P.rev += 1


def colorCoveredBy(P, g):
    """ours (part 2 of the manual test of 21/10/2026): the group whose colour the curves and walls of g show instead
    of g's own — the topmost coloured group above g (tintOf 754: "a parent's colour covers its sub-groups' colours",
    the swatch's tooltip says so too, 8489) — or None when nothing above g has a colour (g's own colour, or none, is
    what shows). For the sheet and the outliner row: "Color from <group>" and the dimmed swatch."""
    top = None
    p = P.parentOf(g)
    while p is not None:
        if getattr(p, 'color', None):
            top = p
        p = P.parentOf(p)
    return top


def setGuidesMode(P, g, mode):
    """the "Curves & anchors" select (8653): 'auto' | 'show' | 'hide'"""
    if mode not in GUIDE_MODES:
        raise ValueError(f'guides mode {mode!r}')
    g.guides = mode
    P.rev += 1


def setLabel(P, g, k, on):
    """a label checkbox (8662): it.labels = {...labels, [k]: v}; the caller marks the surfaces dirty (S.surfDirty)"""
    g.labels = {**(g.labels or {}), k: bool(on)}
    P.gen += 1


def setGuidePower(P, it, on):
    """the ⏻ of the outliner (8510-8523): a guide's effect on the loops (it.off), or every guide's inside a group
    (g.guidesOff); hiding (the eye) never switches a guide off. The caller marks the surfaces dirty (S.surfDirty)
    and regenerates the conversion. Returns whether the switch is on afterwards."""
    if it.type == 'guide':
        it.off = not on
    elif it.type == 'group':
        it.guidesOff = not on
    else:
        raise ValueError(f'no guides power on a {it.type}')
    P.gen += 1; P.rev += 1
    return bool(on)


def guidePowerCurves(P, it):
    """the curves whose locks a ⏻ click on `it` (a guide, or a group holding guides) changes: the curves the guide
    shapes (guides.guideCurves), every guide inside the group for a group"""
    from .guides import guideCurves
    out = []
    if it.type == 'guide':
        gs = [it]
    else:
        gs = []

        def walk(ids):
            for iid in ids:
                x = P.items.get(iid)
                if x is None:
                    continue
                if x.type == 'guide':
                    gs.append(x)
                elif x.type == 'group':
                    walk(x.children)
        walk(it.children)
    for g in gs:
        for cid in guideCurves(P, g):
            if cid not in out:
                out.append(cid)
    return out


def movedCurveIds(P, moved):
    """the curves whose locks a move between groups changes (convOf follows the parent): the moved curves and the
    curves inside every moved group"""
    out = []
    for iid in moved:
        it = P.items.get(iid)
        if it is None:
            continue
        cs = [it] if it.type == 'curve' else curvesIn(P, it) if it.type == 'group' else []
        for c in cs:
            if c.id not in out:
                out.append(c.id)
    return out


def labelOn(P, g, k):
    # port of labelOn (8268) for the stored labels ('loose' is looseOn: on unless set off)
    return looseOn(g) if k == 'loose' else bool(g.labels and g.labels.get(k))


def looseOn(g):
    # port of looseOn (11936): "Free curves visible" is on unless the label says false
    return not (g.labels and g.labels.get('loose') is False)


def setSym(P, g, on=None, axis=None):
    """the Symmetry checkbox and axis (8681-8686): it.sym = {...sym, on} / {...sym, axis}; symChanged (9850) is
    S.surfDirty, the caller's. Returns the sym dict."""
    sym = dict(g.sym) if g.sym else {'on': False, 'axis': 'x'}
    if on is not None:
        sym['on'] = bool(on)
    if axis is not None:
        if axis not in SYM_AXES:
            raise ValueError(f'symmetry axis {axis!r}')
        sym['axis'] = axis
    g.sym = sym
    P.gen += 1
    return sym


def symInfo(P, g):
    """(on, axis, the mirror plane's coordinate: S.center[axis], the head centre; 8688)"""
    sym = g.sym or {'on': False, 'axis': 'x'}
    ax = sym.get('axis') or 'x'
    return bool(sym.get('on')), ax, getattr(P.S.center, ax)


def matchProjectStep(P, g):
    """port of matchProjectStep (1661): the group and the groups inside it take the project loop step (for locks
    made elsewhere: modifiers, pasted, imported). Returns the step; the caller marks the surfaces dirty."""
    st = projectStep(P)
    for x in subGroups(P, g):
        groupConv(P, x)['step'] = st
    P.gen += 1
    return st


def projectStep(P):
    # port of projectStep = defaultStep (1657): S.step || usedStep() || S.headH / 24
    return defaultStep(P)


def stepOff(P, g):
    """the group panel's warning colour (8668): the group's own step is more than 1 % away from the project's"""
    own = convShown(P, g)['step']; ps = projectStep(P)
    return abs(own / ps - 1) > 0.01


def detachGroup(P, g):
    """port of detachGroup (3001): the curves of the group welded to curves of other groups get anchors of their own
    there (welds inside the group stay); ends attached to another group's crown connector are let go too. Returns
    (shared anchors detached, ends let go), or None when the group is not welded to other groups (the toast
    "<name> isn't welded to other groups"). The caller marks the surfaces dirty and clears the anchor selection."""
    inside = curvesIn(P, g)
    inside_set = set(inside)
    outA = set()
    for c in P.curves():
        if c not in inside_set:
            for a in c.anchors:
                outA.add(a)
    mapping = {}                                                    # old anchor -> its new copy (a Map keyed by the Anchor)
    for c in inside:
        ch = False
        new_anchors = []
        for a in c.anchors:
            if a not in outA:
                new_anchors.append(a); continue
            if a not in mapping:
                n = newAnchor(P, a.pos); n.smooth = a.smooth
                mapping[a] = n
            ch = True; new_anchors.append(mapping[a])
        c.anchors = new_anchors
        if ch:
            for a, n in mapping.items():
                if a.id in c.ovr:
                    c.ovr[n.id] = c.ovr.pop(a.id)                   # ovr.set(n.id, ...); ovr.delete(a.id): the entry moves to the end
    att = 0
    for c in inside:
        for a in c.anchors:
            if a.att is not None:
                h = P.items.get(a.att.c)
                if h is not None and h not in inside_set:
                    a.att = None; a.attPos = None; att += 1; mapping[a] = a
    if not mapping:
        return None
    markAnchorsDirty(P, [*mapping.keys(), *mapping.values()])
    P.gen += 1; P.rev += 1
    return len(mapping) - att, att


# ======================= the Conversion panel (9028-9130) =======================

def tickGroup(P, g, on):
    """the checkbox of the conversion list (9036): S.convChecked.add / delete (a Set: insertion order kept)"""
    cc = P.settings.convChecked
    if on and g.id not in cc:
        cc.append(g.id)
    elif not on and g.id in cc:
        cc.remove(g.id)


def tickAll(P, on):
    """ours (1c of the manual test of 21/10/2026, docs/features-adicionadas.md): Tick Every Group (Check All until
    U7) / Uncheck All of the conversion list — every group of the list ticked, in list order (as one would tick them
    one by one: a mesh per group, each sub-group apart from its parent), or none"""
    for g in groupsOf(P):
        tickGroup(P, g, on)


def tickTop(P):
    """ours (U7 C, docs/fase-3-ux-revisao.md): Tick Top Groups — exactly the hair groups with no hair group above
    them ticked (convTree depth 0, in list order), every other unticked: one mesh per tree, its sub-groups merged
    into it (collect 9133 walks the sub-groups that are not ticked)"""
    P.settings.convChecked.clear()
    for g, depth, _state, _via in convTree(P):
        if depth == 0:
            tickGroup(P, g, True)


def convUnit(P, g, root):
    """ours (U7 A): the piece the walls lying directly in g take inside the mesh of `root` (the ticked group at or
    above g whose mesh g goes into) — mergeUnits' key (9987), on groups instead of walls: the chain from root down to
    g, and the deepest group labelled Container on it decides (its sub-group on the way is the piece; the container
    itself for its own walls). None: the mesh's common piece, merged with the rest (shared trunks welded when Merge
    Shared Trunks is on); g: a piece of its own; another group X: merged with X's piece."""
    chain = []
    p = g
    while p is not None:
        chain.insert(0, p)
        if p is root:
            break
        p = P.parentOf(p)
    unit = None
    for i, x in enumerate(chain):
        if x.labels and x.labels.get('container'):
            unit = chain[i + 1] if i + 1 < len(chain) else x
    return unit


def tickOnly(P, g):
    """ours (1c): Only This — this group ticked alone"""
    P.settings.convChecked.clear()
    tickGroup(P, g, True)


def convTargets(P):
    # port of convTargets (9045): the ticked groups and the groups nested in them, in outliner order, each once
    out = []; seen = set()
    checked = set(P.settings.convChecked)
    for iid in outlinerOrder(P):
        it = P.items.get(iid)
        if iid in checked and it is not None and it.type == 'group':
            for x in subGroups(P, it):
                if x.id not in seen:
                    seen.add(x.id); out.append(x)
    return out


def convTree(P):
    """ours (part 2 of the second manual test of 21/10/2026): the Conversion list as a tree — [(group, depth, state,
    via)] in outliner order (the service groups left out, their sub-groups listed in their place). state: 'ticked'
    (in S.convChecked), 'via' (not ticked, inside a ticked group: convTargets (9045) edits it and the conversion puts
    it in that group's mesh — collect (9133) walks the sub-groups that are not ticked themselves), 'off'. via: the
    nearest ticked group above (the mesh it goes into; for a ticked group under a ticked one, the original converts
    it as a mesh of its own), else None. The groups whose state is not 'off' are exactly the hair groups of
    convTargets(P) (a service group nested in a ticked group is a target there, never listed)."""
    checked = set(P.settings.convChecked)
    out = []

    def walk(ids, depth, via):
        for iid in ids:
            g = P.items.get(iid)
            if g is None or g.type != 'group':
                continue
            if g.guidesGroup or g.meshesGroup:
                walk(g.children, depth, via)
                continue
            own = g.id in checked
            out.append((g, depth, 'ticked' if own else 'via' if via is not None else 'off', via))
            walk(g.children, depth + 1, g if own else via)
    walk(P.roots, 0, None)
    return out


def convShown(P, g):
    """A group's conv as its panel would show it after groupConv (1665) — the stored values with the project
    defaults where a key is missing and the step derived as groupConv does (density, else the default step) — but
    WITHOUT filling the group (a property getter must not change the model; the setters do, through groupConv)."""
    from .js import truthy
    conv = dict(g.conv or {})
    if conv.get('step') is None:
        conv['step'] = P.S.headH / conv['density'] if truthy(conv.get('density')) else defaultStep(P)
    for k in CONV_KEYS:
        if k not in conv:
            conv[k] = getattr(P.settings, k)
    return conv


def convSource(P):
    """what the sliders show (syncConvUI, 9097): the first ticked group's conv, else the project defaults (S);
    read without filling (convShown)"""
    t = convTargets(P)
    return convShown(P, t[0]) if t else {k: getattr(P.settings, k) for k in CONV_KEYS}


def convValue(P, k):
    """a slider's value (9100): the step shown is src.step || defaultStep()"""
    src = convSource(P)
    v = src.get(k)
    if k == 'step':
        return v if v else defaultStep(P)
    return v


def setConv(P, k, v):
    """port of setConv (9105): the ticked groups (and the groups nested in them) take the value, or the project's
    defaults (S[k]) when nothing is ticked; the step set last is always the project's loop step (S.step). The
    caller marks the surfaces dirty (S.surfDirty) and regenerates the conversion. Returns the targets."""
    if k not in CONV_KEYS:
        raise ValueError(f'conversion parameter {k!r}')
    t = convTargets(P)
    if t:
        for g in t:
            groupConv(P, g)[k] = v
    else:
        setattr(P.settings, k, v)
    if k == 'step':
        P.settings.step = v
    P.gen += 1                                                      # a group's conv.step (and S.step) feed usedStep / defaultStep: their memos key on gen
    return t


def inGroup(P, it):
    """whether a group lies above `it` (convOf, 1672, finds a group's conv; else it reads the project defaults S) —
    convOf's own walk, without its side effect (it fills the group's conv: groupConv)"""
    p = P.parentOf(it) if it is not None else None
    while p is not None:
        if p.type == 'group':
            return True
        p = P.parentOf(p)
    return False


def defaultsUsers(P):
    """ours (1d of the second manual test of 21/10/2026): (locks, crowns, all the locks) — the locks and crowns that
    read the project defaults S, for the Conversion tab with nothing ticked: a lock takes the conv of its first curve
    (CP = convOf(L.cs[0]), 2633), a crown its own but reads only its step (crownStep, 7481), and convOf finds no group
    above them. In the example every curve is inside a group with its own conv: (0, 0, n). Whatever
    the count, the project loop step also sets the tolerance of the welded-trunk backfill between locks
    (backfillTrunk runs with CP = S, 2726: 0.1 x step) and new groups start with the defaults (createGroup ->
    groupConv)."""
    from .rows import surfaceComponents
    locks = total = 0
    for comp in surfaceComponents(P):
        cs = [c for c in (P.items.get(i) for i in comp)              # buildRowTables' filter (2+ samples; 2+ anchors before the curves are sampled)
              if c is not None and (len(c.samples) >= 2 if c.samples else len(c.anchors) >= 2)]
        if cs:
            total += 1
            if not inGroup(P, cs[0]):
                locks += 1
    crowns = sum(1 for k in P.of_type('crown') if not inGroup(P, k))
    return locks, crowns, total


def convUnchanged(P, k, v, g=None, tol=0.0, echo=None):
    """ours (1b of the manual test of 21/10/2026): True when giving conversion parameter k the value v changes
    nothing the loops read, so a slider confirmed with the value it shows (a double click and Enter) does nothing:
    the group's own value (g: setGroupConv), or every target of the Conversion panel (setConv: the ticked groups and
    the groups nested in them, else the project defaults; a step set there is the project's loop step too). tol: a
    number within tol x max(1, |value|) counts as the same (a slider holds a float32 and its text field reads back
    six decimals: what it shows is not the stored double). echo(stored, v): True when v is what the field gives back
    for a stored value (coords.field_same)."""
    def same(a):
        if isinstance(v, bool) or isinstance(a, bool) or not isinstance(v, (int, float)) or not isinstance(a, (int, float)):
            return a == v
        return abs(a - v) <= tol * max(1.0, abs(a)) or (echo is not None and echo(a, v))
    if g is not None:
        return same(convShown(P, g)[k])
    t = convTargets(P)
    if t:
        if not all(same(convShown(P, x)[k]) for x in t):
            return False
    elif k != 'step' and not same(getattr(P.settings, k)):
        return False
    return k != 'step' or same(projectStep(P))


def setGroupConv(P, g, k, v):
    """One group's own parameter (the port's Selection tab edits the selected group alone, where the original's
    Conversion panel edits every ticked group: setConv): groupConv(g)[k] = v. Unlike setConv, the step does NOT
    become the project's loop step (U4, 15/10/2026: the group's slider changed the project step under the user and
    the "differs" mark moved to other groups; the project step is edited in the Conversion tab alone). The caller
    marks the surfaces dirty."""
    if k not in CONV_KEYS:
        raise ValueError(f'conversion parameter {k!r}')
    groupConv(P, g)[k] = v
    P.gen += 1


# ======================= what the frame loop derives for the display (11905-11936) =======================

def activeGroups(P, sel_items=(), sel_anchors=(), drawing=None, active_group=None):
    """port of activeGroups (11905): the groups one is working in — the ancestors of every selected item (and of
    every curve holding a selected anchor, and of the curve being drawn), plus a selected group with everything
    nested in it. `active_group` is the port's selected group: the active collection (the original selects a group
    in the outliner)."""
    act = set()

    def up(it):
        p = it if it.type == 'group' else P.parentOf(it)
        while p is not None:
            act.add(p.id)
            p = P.parentOf(p)

    def down(g):
        for x in subGroups(P, g):
            act.add(x.id)
    for iid in sel_items:
        it = P.items.get(iid)
        if it is None or it.type == 'head':
            continue
        up(it)
        if it.type == 'group':
            down(it)
    g = P.items.get(active_group) if active_group is not None else None
    if g is not None and g.type == 'group':
        up(g); down(g)
    if sel_anchors:
        aset = set(sel_anchors)
        for c in P.curves():
            if any(a.id in aset for a in c.anchors):
                up(c)
    if drawing is not None:
        up(drawing)
    return act


def looseCurveIds(P):
    """port of looseCurveIds (11925): free curves — joined to no wall and no modifier, not a connector, a junction,
    a disc ring or a crown curve, with no attached anchor"""
    from .crown import crownCurves
    used = set()
    for it in P.items.values():
        if it.type == 'surface':
            used.add(it.a); used.add(it.b)
        elif it.type == 'tube':
            used.add(it.src)
        elif it.type == 'crown':
            used.add(it.part); used.add(it.low)
            for r in crownCurves(P, it):
                used.add(r['c'].id)
    out = set()
    for c in P.curves():
        if c.id not in used and not c.ring and not c.conn and not c.junc and not any(a.att is not None for a in c.anchors):
            out.add(c.id)
    return out


def guidesShown(P, it, act, loose):
    """port of guidesShown (11914): whether a curve (or guide) of a group is shown — always at the root; in a group
    by its mode: 'show' always, 'hide' never, 'auto' while the group is active (`act`: activeGroups), a free curve
    (`loose`: looseCurveIds) with the "Free curves visible" label on in any case; a crown connector while any group is
    active."""
    g = P.parentOf(it)
    if g is None:
        return True
    mode = g.guides or 'auto'
    if it.type == 'curve' and it.conn:
        return mode == 'show' or (mode == 'auto' and bool(act))
    if mode == 'auto' and it.type == 'curve' and looseOn(g) and it.id in loose:
        return True
    return mode == 'show' or (mode == 'auto' and g.id in act)


def selectedGroup(P, sel_items):
    """The group whose panel the original shows (renderProps 8645: the LAST selected item, when it is a group), else
    None. Since 15/10/2026 (night; U1 of docs/fase-3-ux-revisao.md) the port selects groups in S.sel as the app does,
    from its own outliner: the active collection no longer stands for the selected group."""
    if not sel_items:
        return None
    it = P.items.get(sel_items[len(sel_items) - 1])
    return it if it is not None and it.type == 'group' else None


def targetGroup(P, sel_items):
    """port of targetGroup (4729): the id of the group new curves go into — the last selected item itself when it
    is a group, else its parent group; None at the root level (the isolated group of the original is not ported)."""
    if not sel_items:
        return None
    it = P.items.get(sel_items[len(sel_items) - 1])
    if it is None or it.type == 'head':
        return None
    return it.id if it.type == 'group' else it.parent


def shownGuides(P, act, loose):
    """The guides the viewport draws (the guide branch of syncVisuals, 11966-11969, S.showGuides being always on
    here): visible — their own eye and their groups' — and shown by their group's mode (guidesShown: Auto, Show,
    Hide); the ⏻ (it.off) never hides one, it only switches its effect on the loops (guideOn). A disc's two rings
    follow the disc (11946), they are not in shownCurves. `act` / `loose` as shownCurves returns them."""
    from .edit import isVisible
    return [g for g in P.guides() if isVisible(P, g) and guidesShown(P, g, act, loose)]


def hiddenCrownCurves(P):
    """ours (lot 4.8b): the curves of the crowns whose eye is off. The crown's connectors and curves are its siblings in
    the group, so the eye of the crown alone leaves them to the group's mode; a curve another shown crown uses stays"""
    from .crown import crownCurves
    from .edit import isVisible
    ks = P.of_type('crown')
    if all(isVisible(P, k) for k in ks):
        return set()
    gone = set(); kept = set()
    for k in ks:
        into = kept if isVisible(P, k) else gone
        into.update((k.part, k.low))
        into.update(r['c'].id for r in crownCurves(P, k))
    return gone - kept


def shownCurves(P, sel_items=(), sel_anchors=(), drawing=None, active_group=None, snap=None):
    """The hair curves the viewport draws and picks (syncVisuals 11952 and rebuildAnchors 1381): visible, with 2+
    anchors, and shown by their group's mode (the curve an anchor slides along with Shift, `snap`, shows anyway).
    Returns (the curves, the active groups, the free curve ids) so the caller can reuse the two sets."""
    from .edit import isVisible
    # ours (lot 4.8b): the Outliner selects what the eye hid, and that selection works nothing: selecting never shows it
    act = activeGroups(P, [i for i in sel_items if (it := P.items.get(i)) is None or isVisible(P, it)], sel_anchors, drawing, active_group)
    loose = looseCurveIds(P)
    gone = hiddenCrownCurves(P)
    out = [c for c in P.curves() if not c.ring and len(c.anchors) >= 2 and isVisible(P, c) and c.id not in gone and (guidesShown(P, c, act, loose) or c.id == snap)]
    return out, act, loose
