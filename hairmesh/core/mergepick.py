# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The Pick of Merge Distance (lot 1.1b): a circle at the cursor whose diameter, in the model's units at the place under
the cursor, becomes the merge distance. Pure Python, no bpy: the keys of the modal as a state machine, the size of the
circle and the distance it measures; the circle, the header and the undo step are blender/ops_mergepick.py. Line
numbers refer to reference/hair-modeler.clean.html (1.0.2).

  PICK_R (11582)               the circle's radius at the start, in pixels (S.pickR)
  mergePickWorld (13165)       2 * S.pickR * worldPerPixelAt(the point under the cursor, else the pivot)
  the keys (7533-7538, 7730, 7900, 11593): LMB sets the distance and ends; Ctrl + wheel x1.1 / x0.9; Ctrl + RMB drag
  sizes the circle by the drag's x (2-400 px); Esc cancels. The add-on's rule for its modals adds RMB alone as a cancel
  (the original has no RMB there); every other event goes on, as in the original (Alt, the middle button, the numpad,
  the timers), and so does every event over a panel, a header or another area.
"""
from .anchorbrush import worldPerPixelAt
from .js import clamp

PICK_R = 12                                                         # port of S.pickR = 12 (hair-modeler.clean.html:11582)
R_MIN, R_MAX = 2, 400                                               # clamp(…, 2, 400) (7730, 7900)

KEYS = ((('LMB',), 'Set the Distance'), (('Ctrl', 'Wheel'), 'Circle Size'), (('Ctrl', 'RMB Drag'), 'Circle Size'),
        (('Esc', 'RMB'), 'Cancel'))


def mergePickWorld(view, hit, pivot, r, scale):
    # port of mergePickWorld (hair-modeler.clean.html:13165): `hit` the point under the cursor or None, `view` a BrushView
    return 2 * r * worldPerPixelAt(view, hit if hit is not None else pivot, scale)


class Pick:
    """the state of the modal: r, the circle's radius in pixels; drag = (r0, x0) while Ctrl + RMB drags"""

    def __init__(self, r=PICK_R):
        self.r = r
        self.drag = None

    def key(self, kind, value, x=0, ctrl=False, alt=False, inside=True):
        """one event -> what the modal does: 'set' (LMB: the distance, then the end), 'cancel', 'size' (the circle
        changed: redraw), 'move' (the cursor moved over the viewport: the header's value), 'away' (it moved off the
        viewport: no circle, the event goes on to the interface), 'pass' (to the viewport or the interface) or 'eat'
        (nothing). kind / value: Blender's event type and value names; x: the cursor's x in the viewport's pixels (also
        off it: a drag goes on there, 7730); inside: the cursor over the viewport itself, not over a panel, a header or
        another area (a click there is the interface's: the original's canvas never gets it)."""
        if self.drag is not None:                                   # (7730: the drag sizes the circle until the button is let go)
            if kind == 'MOUSEMOVE':
                r0, x0 = self.drag
                self.r = clamp(r0 + (x - x0), R_MIN, R_MAX)
                return 'size'
            if kind == 'RIGHTMOUSE' and value == 'RELEASE':
                self.drag = None
                return 'size'
            if kind == 'ESC' and value == 'PRESS':
                self.drag = None
                return 'cancel'
            return 'eat'
        if kind == 'ESC':
            return 'cancel' if value == 'PRESS' else 'eat'          # (11593: Esc cancels wherever the cursor is)
        if not inside:
            return 'away' if kind == 'MOUSEMOVE' else 'pass'
        if kind == 'MOUSEMOVE':
            return 'move'
        if alt:
            return 'pass'                                           # (7535: Alt still navigates)
        if kind in ('WHEELUPMOUSE', 'WHEELDOWNMOUSE'):
            if not ctrl:
                return 'pass'                                       # (the wheel alone zooms the view)
            self.r = clamp(self.r * (1.1 if kind == 'WHEELUPMOUSE' else 0.9), R_MIN, R_MAX)   # (7900: deltaY < 0 is the wheel up)
            return 'size'
        if kind == 'LEFTMOUSE':
            return 'set' if value == 'PRESS' else 'eat'
        if kind == 'RIGHTMOUSE':
            if value != 'PRESS':
                return 'eat'
            if ctrl:
                self.drag = (self.r, x)                             # (7536)
                return 'size'
            return 'cancel'
        return 'pass'                                               # (the rest goes on, as in the original: the middle button, the numpad, the timers)
