# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The selection of the original (S.sel: selected items, S.asel: selected anchors; hair-modeler.clean.html:696) and
what a click, a box or a paint stroke does to it (the pointer handlers, 5827-5838, 5856-5866, 6049-6053, and
selectItem, 7844). Pure: ids only, so the Blender side can keep it on the Scene (undo, saved with the .blend) and
every rule is testable without a viewport.

The two lists exclude each other, as in the original: picking an anchor clears the items, picking an item clears
the anchors. `items` keeps insertion order (a Set in the original); `anchors` is ordered and its LAST entry is the
anchor the panel shows (S.asel[S.asel.length - 1]), drawn in the 'last' colour."""
from dataclasses import dataclass, field


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
        """port of selectItem (7844): `add` (Shift) toggles the item, else it is the only selection; the anchors are
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
