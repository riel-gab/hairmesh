# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The rows of the HairMesh Outliner (U1 of docs/fase-3-ux-revisao.md, 15/10/2026): a pure description of what the
original's renderOutliner shows (8211-8261) — the head, the service groups first (orderRoots 8207: Imported, Meshes,
Guides), the groups in a tree with their switches (row 8469-8531: the arrow, the mode A / ∿ / ⌀, the colour, the
label tags, the guides' power, the eye), inside a group the curves in the left column and the walls in the right
(8237-8249, "row i = curve i | wall i"), every other item (the types of phases 4 and 5: guides and discs with their
power ⏻ (8510-8523), modifiers and their extras by kind (XKIND 6432), crowns, caps, fill holes, density, meshes
pending or done) as a plain row — with the highlights of the selection: the selected rows, the groups holding
something selected (hl, 8222-8225) and the rows related to the selection (OL_REL, 8226-8232: the walls of a selected
curve, the two curves of a selected wall), with the precedence of the original's row classes (8470: selected, then
holding, then related).

Additions of the port (docs/features-adicionadas.md): the project row on top (16/10/2026, 1c of the manual test:
like Blender's "Scene Collection", everything hangs under it, head included; Move here / Paste on it = the root
level — the original drops on the empty space of the tree, 8555); a curve its group's mode hides right now is
marked (U2), so nothing ever looks lost; the filter (names and type words); "open on select" (the groups above the
selection open while it is selected: the original opens on F and on a drop only).

Nothing here touches Blender; blender/ops_outliner.py draws the rows and runs the clicks. The expand / collapse
state and the filter are the caller's (UI state, not the model: the original keeps `expanded` in the file but never
commits a click on the arrow)."""
from dataclasses import dataclass

from . import groups as G
from .edit import isVisible
from .effects import modeActs
from .guides import guideOn

# the original's ICONS (711) as Blender icons: ☺ head, ▣ group, ∿ curve, ▦ surface, ◆ mesh, ┼ guide (◎ disc), ⬡ tube
# (modifier), ✦ xmod (extra), ◠ crown, ◒ cap, ⬢ imported mesh, ◭ skin (fill holes), ⋎ tip (density)
ICONS = {'head': 'MESH_MONKEY', 'group': 'OUTLINER_COLLECTION', 'curve': 'CURVE_DATA', 'surface': 'MOD_LATTICE', 'mesh': 'MESH_DATA',
         'guide': 'CURVE_PATH', 'disc': 'MESH_CIRCLE', 'tube': 'MOD_SCREW', 'xmod': 'MODIFIER', 'crown': 'FORCE_VORTEX', 'cap': 'MESH_CAPSULE',
         'imesh': 'IMPORT', 'skin': 'MOD_SKIN', 'tip': 'MOD_DECIM', 'project': 'FILE'}
# the extras by kind (XKIND 6432: ◐ mask, ⬤ thickness, ⟳ twist, ≋ noise, ➰ curls, ✥ transform)
XMOD_ICONS = {'mask': 'MOD_MASK', 'thick': 'MOD_SOLIDIFY', 'twist': 'MOD_SIMPLEDEFORM', 'noise': 'MOD_NOISE', 'curl': 'MOD_CURVE', 'xform': 'ORIENTATION_GLOBAL'}
HOLDS_ICON = 'COLLECTION_COLOR_05'                                  # a group holding the selection: the blue collection (childsel, CSS 235: blue)
RELATED_ICON = 'COLORSET_02_VEC'                                    # a related row: the orange marker before the name (rel, CSS 177: pale orange)
MODE_LABELS = {'auto': 'A', 'show': '∿', 'hide': '⌀'}                # GUIDE_MODES (11938)
MODE_ORDER = ('auto', 'show', 'hide')                               # the click on the mode button cycles them (8488)
# what the filter matches besides the name: a word for the type (the Smart Rename's type names, SR_TYPES 8292)
TYPE_WORDS = {'group': 'group', 'curve': 'curve', 'surface': 'wall surface', 'tube': 'modifier tube', 'xmod': 'extra', 'guide': 'guide',
              'mesh': 'mesh', 'imesh': 'imported mesh', 'crown': 'crown', 'cap': 'cap', 'skin': 'fill holes', 'tip': 'density', 'head': 'head'}


@dataclass(eq=False)
class Row:
    kind: str                                                       # 'root' | 'head' | 'item' | 'pair'
    depth: int = 0
    id: int | None = None
    type: str = ''
    name: str = ''
    icon: str = 'DOT'
    selected: bool = False                                          # S.sel has it (the root row: nothing selected, the root level)
    related: bool = False                                           # OL_REL
    holds: bool = False                                             # a group holding something selected (childsel)
    hidden: bool = False                                            # the eye is off (isVisible: it or an ancestor)
    mode_hidden: bool = False                                       # U2: a curve its group's mode hides right now
    free: bool = False                                              # a free curve (looseCurveIds)
    crown: str | None = None                                        # a crown's connector curve (its parting line or lower connector): the crown's name (ours)
    expanded: bool = True                                           # groups: open
    group: dict | None = None                                       # groups: mode, color, labels, ticked, step, project_step, off, guides_power, service
    power: bool | None = None                                       # guides: the ⏻ switch (not it.off); None = no switch on this row
    power_effective: bool = True                                    # guides: False when the switch is on but a group above switches its guides off (guideOn 722)
    pending: bool = False                                           # meshes: ◇ while the conversion is pending (8475)
    left: 'Row | None' = None                                       # pairs: the curve (left column)
    right: 'Row | None' = None                                      # pairs: the wall (right column)

    @property
    def highlight(self):
        """the original's row class (8470), one at a time: 'selected' > 'holds' > 'related' > ''"""
        return 'selected' if self.selected else 'holds' if self.holds else 'related' if self.related else ''


def next_mode(mode):
    """the mode after `mode` when the A / ∿ / ⌀ button is clicked (8488)"""
    return MODE_ORDER[(MODE_ORDER.index(mode if mode in MODE_ORDER else 'auto') + 1) % 3]


def highlights(P, sel_items=(), sel_anchors=()):
    """(hl, rel): the groups holding something selected — the ancestors of every selected item, and of every curve
    holding a selected anchor (and of its disc, for a ring) (8222-8225); the rows related to the selection — the
    walls of a selected curve, the two curves of a selected wall (8226-8232)."""
    hl = set(); rel = set()

    def up(it):
        p = P.parentOf(it) if it is not None else None
        while p is not None:
            hl.add(p.id)
            p = P.parentOf(p)
    for iid in sel_items:
        it = P.items.get(iid)
        if it is None:
            continue
        up(it)
        if it.type == 'curve':
            for s in P.surfaces():
                if s.a == iid or s.b == iid:
                    rel.add(s.id)
        elif it.type == 'surface':
            rel.add(it.a); rel.add(it.b)
    if sel_anchors:
        aset = set(sel_anchors)
        for c in P.curves():
            if any(a.id in aset for a in c.anchors):
                up(c)
                if c.ring:
                    up(P.items.get(c.ring))
    return hl, rel


def ordered_roots(P):
    """orderRoots (8207) without sorting the model: Imported, then Meshes, then Guides, then the rest in its order"""
    def rank(iid):
        it = P.items.get(iid)
        if it is None or it.type != 'group':
            return 2
        return -1 if it.importGroup else 0 if it.meshesGroup else 1 if it.guidesGroup else 2
    return sorted(P.roots, key=rank)


def group_has_guides(P, g):
    """groupHasGuides (723): a guide inside the group, at any depth"""
    for iid in g.children:
        it = P.items.get(iid)
        if it is None:
            continue
        if it.type == 'guide' or (it.type == 'group' and group_has_guides(P, it)):
            return True
    return False


def group_info(P, g, loose):
    """the switches of a group row (8481-8526): the curves mode, the colour, the label tags shown (Container; Free
    curves only while the group has free curves, labelOn 8268), the conversion tick (ours: the original shows it in
    the Convert list only), its loop step against the project's (U4), the guides' power when it holds guides, and
    the group whose colour covers its own (color_from, ours: 21/10/2026)"""
    hair = not (g.guidesGroup or g.meshesGroup)
    cov = G.colorCoveredBy(P, g)
    return {'mode': g.guides or 'auto', 'color': g.color, 'color_from': cov.name if cov is not None else None,
            'labels': [k for k in ('container', 'loose') if G.labelOn(P, g, k) and (k != 'loose' or any(c in loose for c in g.children))],
            'ticked': g.id in P.settings.convChecked,
            'step': G.convShown(P, g)['step'] if hair else None, 'project_step': G.projectStep(P), 'off': G.stepOff(P, g) if hair else False,
            'guides_power': (not g.guidesOff) if group_has_guides(P, g) else None,
            'service': bool(g.guidesGroup or g.meshesGroup or g.importGroup),
            'mode_acts': modeActs(P, g)}                            # ours (part 1 of the lot ux7): the mode reaches the curves lying in the group itself only (guidesShown 11914)


def item_icon(it):
    """the icon of an item row (ICONS 711; a disc guide ◎, an extra by its kind, a pending mesh ◇ = the mesh icon dimmed)"""
    if it.type == 'guide' and getattr(it, 'kind', None) == 'disc':
        return ICONS['disc']
    if it.type == 'xmod':
        return XMOD_ICONS.get(getattr(it, 'kind', None), ICONS['xmod'])
    return ICONS.get(it.type, 'DOT')


def matches_filter(it, text):
    """the filter (ours): the name, or a word for the type ("wall", "curve", "density"...), or "free" for a free
    curve is the caller's; any case"""
    return text in (it.name or '').lower() or text in TYPE_WORDS.get(getattr(it, 'type', 'head'), '')   # (the Head has no type field)


def outliner_rows(P, sel_items=(), sel_anchors=(), collapsed=frozenset(), filter_text='', project_name='', open_selected=False):
    """The rows, top to bottom, and a summary: {'items': the items listed (no groups, no head), 'groups', 'mode_hidden':
    the curves hidden by their group's mode, 'matches': the items matching the filter (None without one), 'guides':
    whether any row carries a ⏻ (the column exists only then)}.
    The first row is the project (kind 'root': `project_name`, else the project's own name), everything else one
    level under it, the head first (1c, 16/10/2026). `collapsed`: the ids of the groups drawn closed (every group is
    open by default here; the original opens a group on the arrow, on F and on a drop into it); `open_selected`
    keeps the groups above the selected item open (reveal_targets) whatever `collapsed` says. `filter_text`: only
    the items whose name or type word contains it (any case), with the groups that hold them, opened."""
    sel = set(sel_items)
    hl, rel = highlights(P, sel_items, sel_anchors)
    loose = G.looseCurveIds(P)
    shown = {c.id for c in G.shownCurves(P, sel_items, sel_anchors)[0]}
    text = filter_text.strip().lower()
    if open_selected:
        _it, above = reveal_targets(P, sel_items, sel_anchors)
        if above:
            collapsed = frozenset(collapsed) - set(above)
    matches = None
    keep_groups = set()
    if text:
        matches = {it.id for it in P.items.values() if it.type != 'group' and (matches_filter(it, text) or (it.type == 'curve' and it.id in loose and 'free'.startswith(text)))}

        def holds(g):
            found = False
            for iid in g.children:
                it = P.items.get(iid)
                if it is None:
                    continue
                if it.type == 'group':
                    if holds(it) or matches_filter(it, text):
                        found = True
                elif iid in matches:
                    found = True
            if found or matches_filter(g, text):
                keep_groups.add(g.id)
            return found or matches_filter(g, text)
        for iid in P.roots:
            it = P.items.get(iid)
            if it is not None and it.type == 'group':
                holds(it)
    info = {'items': 0, 'groups': 0, 'mode_hidden': 0, 'matches': len(matches) if matches is not None else None, 'guides': False}
    crowns = {}                                                     # connector curve id -> its crown (k.part, k.low)
    for k in P.of_type('crown'):
        for cid in (k.part, k.low):
            if cid is not None:
                crowns[cid] = k

    def item_row(it, depth):
        r = Row(kind='item', depth=depth, id=it.id, type=it.type, name=it.name, icon=item_icon(it),
                selected=it.id in sel, related=it.id in rel, holds=it.id in hl, hidden=not isVisible(P, it))
        if it.type == 'group':
            r.expanded = it.id not in collapsed or it.id in keep_groups
            r.group = group_info(P, it, loose)
            if r.group['guides_power'] is not None:
                info['guides'] = True
            info['groups'] += 1
        else:
            info['items'] += 1
            if it.type == 'curve':
                r.free = it.id in loose
                if it.conn:
                    k = crowns.get(it.id)
                    r.crown = k.name if k is not None else None
                r.mode_hidden = it.id not in shown and not r.hidden and len(it.anchors) >= 2
                if r.mode_hidden:
                    info['mode_hidden'] += 1
            elif it.type == 'guide':
                r.power = not it.off; r.power_effective = guideOn(P, it); info['guides'] = True   # ⏻ (8510-8523)
            elif it.type == 'mesh':
                r.pending = bool(getattr(it, 'pending', False))
        return r

    def wanted(it):
        if matches is None:
            return True
        return it.id in keep_groups if it.type == 'group' else it.id in matches

    def walk(ids, depth, in_group, out):
        items = [it for it in (P.items.get(i) for i in ids) if it is not None and not getattr(it, 'ring', None) and wanted(it)]
        pairs = [it for it in items if it.type in ('curve', 'surface')] if in_group else []
        cs = [it for it in pairs if it.type == 'curve']; ss = [it for it in pairs if it.type == 'surface']
        two = bool(cs) and bool(ss)
        block = False
        for it in items:
            if two and it.type in ('curve', 'surface'):
                if block:
                    continue
                block = True
                for i in range(max(len(cs), len(ss))):
                    out.append(Row(kind='pair', depth=depth, left=item_row(cs[i], depth) if i < len(cs) else None, right=item_row(ss[i], depth) if i < len(ss) else None))
                continue
            r = item_row(it, depth)
            out.append(r)
            if it.type == 'group' and r.expanded and it.children:
                walk(it.children, depth + 1, True, out)
    rows = [Row(kind='root', depth=0, type='project', name=project_name or P.name or 'Project', icon=ICONS['project'], selected=not sel_items and not sel_anchors)]
    if P.head is not None and (matches is None or matches_filter(P.head, text)):
        rows.append(Row(kind='head', depth=1, id=P.head.id, type='head', name=P.head.name or 'head', icon=ICONS['head'], hidden=not P.head.visible))
    walk(ordered_roots(P), 1, False, rows)
    return rows, info


def summary_text(info):
    """the footer: "N items · M hidden by Auto" (and the filter's hits)"""
    s = f'{info["items"]} item{"" if info["items"] == 1 else "s"}'
    if info['mode_hidden']:
        s += f' · {info["mode_hidden"]} hidden by the group mode'
    if info['matches'] is not None:
        s += f' · {info["matches"]} match{"" if info["matches"] == 1 else "es"}'
    return s


def reveal_targets(P, sel_items=(), sel_anchors=()):
    """revealInOutliner (8586-8597): the item to show — the last selected one, else a lock curve of the last
    selected anchor (its disc for a ring) — and the groups to open above it. Returns (item or None, ancestor ids)."""
    from .edit import curvesWith
    it = P.items.get(sel_items[len(sel_items) - 1]) if sel_items else None
    if it is None and sel_anchors:
        a = P.anchors.get(sel_anchors[len(sel_anchors) - 1])
        cs = curvesWith(P, a) if a is not None else []
        it = next((c for c in cs if not c.conn and not c.ring), cs[0] if cs else None)
    if it is not None and getattr(it, 'ring', None):
        it = P.items.get(it.ring)
    if it is None:
        return None, []
    out = []
    p = P.parentOf(it)
    while p is not None:
        out.append(p.id)
        p = P.parentOf(p)
    return it, out


def double_click(item, now, last, dbl_time):
    """The port's double click on a row's name (1d, 16/10/2026): a layout button cannot see Blender's double-click
    event (it runs once per click), so the second click on the SAME item within the double-click time of the
    preferences counts as one. `last` is (item, time) of the previous click. Returns (is a double click, the new
    `last`): after a double click the memory is cleared, so a third click starts over."""
    if last is not None and last[0] == item and 0 <= now - last[1] <= dbl_time:
        return True, None
    return False, (item, now)
