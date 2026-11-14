# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The selection of the original (S.sel: selected items, S.asel: selected anchors; hair-modeler.clean.html:696) and
what a click, a box or a paint stroke does to it (the pointer handlers, 5827-5838, 5856-5866, 6049-6053, and
selectItem, 11472). Pure: ids only, so the Blender side can keep it on the Scene (undo, saved with the .blend) and
every rule is testable without a viewport.

The two lists exclude each other, as in the original: picking an anchor clears the items, picking an item clears
the anchors. `items` keeps insertion order (a Set in the original); `anchors` is ordered and its LAST entry is the
anchor the panel shows (S.asel[S.asel.length - 1]), drawn in the 'last' colour.

The curves of the selection's locks (lockCurvesFrom, 1.0.2: the pie's "Select all curves of the lock") read the
project too: module functions below the class."""
from dataclasses import dataclass, field

from .js import truthy


@dataclass(eq=False)
class Selection:
    items: list = field(default_factory=list)                       # S.sel: ids of curves (later guides, walls, groups)
    anchors: list = field(default_factory=list)                     # S.asel: anchor ids

    def __bool__(self):
        return bool(self.items or self.anchors)

    @property
    def last_anchor(self):
        return self.anchors[len(self.anchors) - 1] if self.anchors else None

    def click_anchor(self, aid, shift=False, ctrl=False):
        """A click on anchor `aid` (5827-5831): Ctrl toggles it, Shift (or a click on an already selected anchor)
        makes it the last one, a plain click selects it alone; the items are cleared. Returns whether the anchor is
        selected afterwards (the drag starts only then, 5832)."""
        if ctrl:
            self.anchors = [x for x in self.anchors if x != aid] if aid in self.anchors else [*self.anchors, aid]
        elif shift or aid in self.anchors:
            self.anchors = [*[x for x in self.anchors if x != aid], aid]
        else:
            self.anchors = [aid]
        self.items = []
        return aid in self.anchors

    def select_item(self, iid, add=False):
        """port of selectItem (11472): `add` (Shift) toggles the item, else it is the only selection; the anchors are
        cleared. A re-selected item moves to the end (the last selected is the one the panel shows)."""
        self.anchors = []
        if not add:
            self.items = []
        if add and iid in self.items:
            self.items.remove(iid)
        else:
            if iid in self.items:
                self.items.remove(iid)
            self.items.append(iid)

    def box(self, inside, add=False):
        """The marquee released (6049-6053): `inside` = ids of the anchors in the box (empty for a click or a box of
        3 px or less). With anchors inside they become the selection (Shift: added, in order, each once); a box with
        nothing inside clears everything unless Shift is held."""
        if inside:
            self.anchors = list(dict.fromkeys([*self.anchors, *inside])) if add else list(inside)
            self.items = []
        elif not add:
            self.anchors = []; self.items = []

    def paint_mode(self, cid):
        """Ctrl + drag over curves (5856-5859): the first curve touched decides, remove when it was selected."""
        return 'remove' if cid in self.items else 'add'

    def paint(self, ids, mode):
        """paintSel's inner loop (5861-5864): the curves welded along the touched segment all go together. Returns
        whether anything changed."""
        changed = False
        for iid in ids:
            if mode == 'add' and iid not in self.items:
                self.items.append(iid); changed = True
            if mode == 'remove' and iid in self.items:
                self.items.remove(iid); changed = True
        return changed

    def circle(self, anchor_ids=(), curve_ids=(), deselect=False):
        """The Circle Select of this port (C; the original has none): a stroke that starts over anchors paints
        anchors, one that starts over a bare stretch of curve paints curves; Shift takes them out. Returns whether
        anything changed."""
        changed = False
        if anchor_ids:
            if deselect:
                keep = [a for a in self.anchors if a not in set(anchor_ids)]
                changed = len(keep) != len(self.anchors); self.anchors = keep
            else:
                new = [a for a in anchor_ids if a not in self.anchors]
                if new or self.items:
                    changed = True
                self.anchors = [*self.anchors, *new]; self.items = []
        elif curve_ids:
            if deselect:
                changed = self.paint(curve_ids, 'remove')
            else:
                changed = self.paint(curve_ids, 'add') or bool(self.anchors)
                self.anchors = []
        return changed

    def prune(self, P):
        """What deleteItem (780-784) and restore (1268-1269) do: only items that still exist, only anchors a curve
        still uses."""
        self.items = [i for i in self.items if i in P.items]
        alive = {a.id for c in P.curves() for a in c.anchors}
        self.anchors = [a for a in self.anchors if a in alive]
        return self

    def copy(self):
        return Selection(list(self.items), list(self.anchors))


# ======================= the curves of the selection's locks (10173-10205, 1.0.2) =======================

def lockCurvesFrom(P, from_ids, anchor_ids=()):
    """port of lockCurvesFrom (hair-modeler.clean.html:11751): the curves of the locks the given items and anchors
    belong to — anchors (their curves, not a disc's ring), curves, walls (both curves), profile modifiers (the curve),
    their extras (through the modifier) and crowns (the parting line, the lower connector and the crown curves); a
    lock = curves joined by walls (as scissors.lockOf). An edge curve (c.path) counts with the curves it runs along and
    is given as them. Curves in the order the original's Set gathers them."""
    from .crown import crownCurves
    seed = {}
    for aid in anchor_ids:
        for c in P.curves():                                        # curvesWith(a), by the anchor's id (S.asel holds ids here)
            if any(x.id == aid for x in c.anchors) and not c.ring:
                seed[c.id] = None
    for iid in from_ids:
        it = P.items.get(iid)
        if it is None:
            continue
        if it.type == 'xmod' and it.tube is not None:
            it = P.items.get(it.tube)
        if it is None:
            continue
        if it.type == 'curve' and not it.ring:
            seed[it.id] = None
        elif it.type == 'surface':
            seed[it.a] = None; seed[it.b] = None
        elif it.type == 'tube':
            seed[it.src] = None
        elif it.type == 'crown':
            for x in [it.part, it.low, *[r['c'].id for r in crownCurves(P, it)]]:
                seed[x] = None
    if not seed:
        return []
    # (walls on edge curves: a curve counts with the edge curves running along it, an edge curve as the curves under it)
    for c in P.curves():
        if truthy(c.path) and any(p[0] in seed for p in c.path):       # (c.path: [] is true in JS, as rows.py reads it)
            seed[c.id] = None
    ids = dict(seed); grew = True
    while grew:
        grew = False
        for s in P.surfaces():
            if (s.a in ids) != (s.b in ids):
                ids.setdefault(s.a, None); ids.setdefault(s.b, None); grew = True
    for iid in list(ids):
        c = P.items.get(iid)
        if c is not None and truthy(getattr(c, 'path', None)):
            for p in c.path:
                ids.setdefault(p[0], None)
            ids.pop(iid, None)
    return [c for c in (P.items.get(i) for i in ids) if c is not None and c.type == 'curve' and not truthy(c.path)]


def lockCurvesOfSelection(P, sel):
    # port of lockCurvesOfSelection (hair-modeler.clean.html:11750): lockCurvesFrom(S.sel, S.asel)
    return lockCurvesFrom(P, sel.items, sel.anchors)


def lockSelectItem(P, sel):
    """the pie's item of lockSelectItem (hair-modeler.clean.html:11777): how many curves it would select, None when
    nothing of a lock is selected (no item). Service curves (junc) count, as in the original."""
    n = len(lockCurvesOfSelection(P, sel))
    return n if n else None


def selectLockCurves(P, sel):
    """port of selectLockCurves (hair-modeler.clean.html:11771): (the new selection — the curves of the locks, no
    anchors —, the toast), or (None, None) when there is nothing to select. No undo step of its own (no commit)."""
    cs = lockCurvesOfSelection(P, sel)
    if not cs:
        return None, None
    n = len(cs)
    return Selection([c.id for c in cs], []), f'{n} curve{"" if n == 1 else "s"} of the lock selected'
