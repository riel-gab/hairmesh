# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The keys of two modals of the 1.0.2 tools, as state machines (pure Python, no bpy; the modals in blender/ops_split.py
and blender/ops_anchorline.py only turn Blender's events into calls and apply what comes back). Line numbers refer to reference/hair-modeler.clean.html
(1.0.2).

  SplitSession   Scissors > Split Ends: the session over the viewport while the mode is on — the hover (the preview),
                 the click (the split), the Spread (S.splitR, 2-300 px): Ctrl + RMB drag as the original (7562-7565,
                 7729-7737: the drag's x alone), and ours as the brush's keys, [ / ] (x0.85 / x1.15, at least 1 px) and F
                 (Blender's radial control: the pointer's way sets it, LMB or Enter keeps it, RMB or Esc puts it back,
                 Ctrl snaps to 5 px, Shift is precision).
  LineGesture    the RMB pressed over empty space (7583, 7776-7781, 7823-7839): until the pointer passes the drag
                 threshold nothing is decided; let go before, it was a click (the add-on opens what Blender's RMB opens);
                 past it, a line, and its release puts the anchors in (scissors.anchorLine).
"""
from math import hypot

from .js import clamp, js_round

SPREAD_MIN, SPREAD_MAX = 2, 300                                     # the Spread slider (11411) and the drag's clamp (8716)
SNAP = 5                                                            # Ctrl in the radial control: 10 px of diameter, as Blender's
SLOW = 0.1                                                          # Shift in the radial control: precision
NAV = frozenset({'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
                 'MOUSEROTATE', 'MOUSESMARTZOOM', 'NDOF_MOTION'})
MOVES = frozenset({'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'})


def spread_step(r, bigger):
    """[ / ]: the brush's x1.15 / x0.85 (8018-8019), never less than 1 px a press, within 2-300"""
    n = max(r + 1, js_round(r * 1.15)) if bigger else min(r - 1, js_round(r * 0.85))
    return int(clamp(n, SPREAD_MIN, SPREAD_MAX))


def spread_radial(r0, dx, snap=False):
    """F: the spread `dx` pixels of the pointer (at 100 %) after the press — right grows it, a pixel a pixel"""
    r = r0 + dx
    if snap:
        r = js_round(r / SNAP) * SNAP
    return int(js_round(clamp(r, SPREAD_MIN, SPREAD_MAX)))


def spread_drag(r0, x0, x):
    """Ctrl + RMB drag (8716): clamp(r0 + (x - x0), 2, 300), the pixels at 100 %"""
    return int(js_round(clamp(r0 + (x - x0), SPREAD_MIN, SPREAD_MAX)))


class SplitSession:
    """r: the spread in pixels at 100 %; adjust: None, or what F / Ctrl + RMB are changing ({'what': 'radial' | 'drag',
    'r0', ...}) until they end"""

    def __init__(self, r=30):
        self.r = int(r)
        self.adjust = None

    def key(self, kind, value, x=0.0, ctrl=False, alt=False, shift=False, oskey=False, inside=True):
        """one event -> what the session does: 'hover' (the cursor over the viewport: the preview), 'away' (off it: no
        preview, the event goes on), 'split' (LMB: split the curve under the cursor), 'size' (the spread changed),
        'keep' / 'restore' (an adjustment ended: kept, or put back), 'radial' / 'drag' (one began), 'pass' (to Blender
        and the tool keymap: navigation, RMB, Shift + RMB's pie, Esc back to Select, every other key) or 'eat'.
        x: the cursor's x in pixels at 100 % (the region's pixels / the interface scale); inside: over the viewport
        itself, not over a panel, a header or another area."""
        a = self.adjust
        if a is not None:
            if a['what'] == 'drag':                                 # (7730: until the right button is let go)
                if kind in MOVES:
                    self.r = spread_drag(a['r0'], a['x0'], x)
                    return 'size'
                if kind == 'RIGHTMOUSE' and value == 'RELEASE':
                    self.adjust = None
                    return 'keep'
                if kind == 'ESC' and value == 'PRESS':
                    self.r = a['r0']; self.adjust = None
                    return 'restore'
                return 'eat'
            if kind in MOVES:                                       # the radial control
                a['slow'] = a['slow'] or not shift                  # (the Shift still held from before is not Precision)
                a['dx'] += (x - a['x']) * (SLOW if shift and a['slow'] else 1.0)
                a['x'] = x
                self.r = spread_radial(a['r0'], a['dx'], ctrl)
                return 'size'
            if value == 'PRESS' and (kind == 'LEFTMOUSE' or kind in ('RET', 'NUMPAD_ENTER')):
                self.adjust = None
                return 'keep'
            if value == 'PRESS' and kind in ('RIGHTMOUSE', 'ESC'):
                self.r = a['r0']; self.adjust = None
                return 'restore'
            return 'eat'
        if kind in MOVES:
            return 'hover' if inside else 'away'
        if not inside or alt or oskey:
            return 'pass'                                           # (the interface's, Alt's navigation)
        if kind == 'LEFTMOUSE' and value in ('PRESS', 'DOUBLE_CLICK'):
            return 'split'                                          # (7593: the click on a curve, any other key held)
        if value != 'PRESS':
            return 'pass'
        if kind in ('LEFT_BRACKET', 'RIGHT_BRACKET') and not (ctrl or shift):
            self.r = spread_step(self.r, kind == 'RIGHT_BRACKET')
            return 'size'
        if kind == 'F' and not (ctrl or shift):
            self.adjust = {'what': 'radial', 'r0': self.r, 'x': x, 'dx': 0.0, 'slow': True}
            return 'radial'
        if kind == 'RIGHTMOUSE' and ctrl and not shift:
            self.adjust = {'what': 'drag', 'r0': self.r, 'x0': x}   # (7562)
            return 'drag'
        return 'pass'


def line_starts(subtool, shift=False, ctrl=False, alt=False, oskey=False, curve_under=False):
    """whether the RMB press starts a line (7582-7583): not in Draw nor the Scissors (their RMB is their own, 7573 /
    7568), no modifier (Shift + RMB: the pie; Ctrl + RMB: a brush's drag), and no curve under the cursor (RMB there
    inserts an anchor, 7574-7580)"""
    return subtool not in ('DRAW', 'SCISSORS') and not (shift or ctrl or alt or oskey) and not curve_under


CONTEXT_VALUES = (('PRESS', 'ANY'), ('RELEASE',), ('CLICK',))     # the order a plain click meets them: the press, then the
                                                                    # release, then (the release unhandled) the click


def context_item(keymaps):
    """the item a plain RMB click (no drag) would have run in the viewport, from `keymaps`: [(name, [item, ...])] in
    the order Blender consults them ('Object Mode', then '3D View'), each item a dict {idname, value, active, any,
    shift, ctrl, alt, oskey} in the order the keymap in use runs them (keyconfigs.user's list: an add-on's items are
    already there in their running order, the reverse of the add-on's own list). The press is met by PRESS (and ANY)
    items of every keymap first, then the release by RELEASE ones, then the click by CLICK ones; an item that needs a
    modifier never matches a plain click (-1 is "any"), an inactive one or one of the add-on's own never runs. The
    index (keymap, item), or None: then nothing opens"""
    def plain(it):
        return it.get('any') or all(it.get(k, 0) in (0, False, -1) for k in ('shift', 'ctrl', 'alt', 'oskey'))
    for values in CONTEXT_VALUES:
        for k, (_name, items) in enumerate(keymaps):
            for i, it in enumerate(items):
                if it.get('active', True) and it.get('value') in values and plain(it) and '.' in it.get('idname', '') \
                        and not it['idname'].startswith('hairmesh.'):
                    return k, i
    return None


MODIFIER_KEYS = frozenset({'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY'})


class LineGesture:
    """the RMB pressed over empty space at (x0, y0): state 'armed' until the pointer passes `threshold` pixels, then
    'line'"""

    def __init__(self, x0, y0, threshold):
        self.x0, self.y0 = x0, y0
        self.threshold = threshold
        self.state = 'armed'
        self.x, self.y = x0, y0

    def key(self, kind, value, x=0.0, y=0.0):
        """one event -> 'line' (a line now: draw it from the press to (x, y)), 'wait' (not past the threshold yet),
        'menu' (let go as a click: what Blender's RMB opens), 'apply' (let go as a line), 'cancel' (Esc, or the window
        lost its focus: nothing), 'abort' (any other key or button pressed: nothing, and the key goes on — a release
        lost in another window never leaves Ctrl+Z or Tab swallowed) or 'pass' (navigation, a modifier held, timers)"""
        if kind in MOVES:
            self.x, self.y = x, y
            if self.state == 'armed' and hypot(x - self.x0, y - self.y0) > self.threshold:
                self.state = 'line'
            return 'line' if self.state == 'line' else 'wait'
        if kind == 'RIGHTMOUSE' and value == 'RELEASE':
            return 'apply' if self.state == 'line' else 'menu'
        if (kind == 'ESC' and value == 'PRESS') or kind == 'WINDOW_DEACTIVATE':
            return 'cancel'
        if kind in NAV or kind.startswith('NDOF') or kind in MODIFIER_KEYS:
            return 'pass'
        if value in ('PRESS', 'DOUBLE_CLICK'):
            return 'abort'
        return 'pass'
