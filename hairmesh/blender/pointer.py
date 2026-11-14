# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The system pointer while a modal draws a circle at the cursor (the brushes, the Draw's Lock width, Split Ends, the
Pick of Merge Distance): as over Blender 5.1's brushes, a paint cross or a dot, never hidden; Blender's own back over
the interface, before a key that may open a menu, when the window loses the focus and at every exit.

Blender 5.1 (paint_cursor.cc, paint_update_mouse_cursor, 1488-1513): the brush's size, its diameter in pixels, between
12 and 28 (both excluded) gives WM_CURSOR_DOT ("PAINT_CROSS" would overlap the circle), any other WM_CURSOR_PAINT; no
cursor is set while a modal cursor is on (1490) or a temporary region (a menu, a pie) is open (1497-1503). The Python
names: rna_wm_api.cc 47-48 (WM_CURSOR_PAINT is "PAINT_CROSS", WM_CURSOR_DOT is "DOT").

The rule (cursor_for, wanted) and who owns the pointer (Holder) are pure: no bpy at the top, tests/test_pointer.py runs
them without Blender. The Blender side (claim, ask, follow, release, reset) only calls the methods of the Window it is
handed, and looks a window up again by its pointer, never keeping one."""

DOT_ABOVE = 12                                                      # paint_cursor.cc:1510: brush_size < 28 && brush_size > 12
DOT_BELOW = 28
KEEP = 'KEEP'                                                       # wanted: the pointer stays as it is
MOVES = frozenset(('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'))
NAV = frozenset(('MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE',
                 'MOUSESMARTZOOM', 'NDOF_MOTION', 'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4', 'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7',
                 'NUMPAD_8', 'NUMPAD_9', 'NUMPAD_PERIOD', 'NUMPAD_MINUS', 'NUMPAD_PLUS', 'NUMPAD_SLASH', 'NUMPAD_ASTERIX', 'HOME'))
MODIFIERS = frozenset(('LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY'))
DEACTIVATE = 'WINDOW_DEACTIVATE'                                    # reaches every modal, whatever they return (wm_event_system.cc:845-848, 2704)
LOG_SIZE = 64
# the modals that draw a circle of their own; the drawing of a lock ('draw') shows its width session's ('brush')
OWN_CIRCLE = frozenset(('brush', 'split', 'merge', 'select'))


def cursor_for(diameter):
    """the pointer over the viewport for a circle of this diameter (px at 100 %, rounded: Blender's size is an int):
    'DOT' or 'PAINT_CROSS'; None with no circle (Blender's own pointer)"""
    if diameter is None:
        return None
    d = int(round(diameter))
    return 'DOT' if DOT_ABOVE < d < DOT_BELOW else 'PAINT_CROSS'


def may_open_menu(t, v):
    """a press that goes on to Blender and may open a menu or a pie: Blender gives a modal no event until it closes, and
    sets no paint cursor while it is open (paint_cursor.cc:1497-1503). Navigation, a modifier held and the left button
    (a click, never a menu) open none"""
    return v == 'PRESS' and t not in NAV and t not in MODIFIERS and t != 'LEFTMOUSE'


def wanted(t, v, inside, diameter, adjusting=False, passed=True):
    """the pointer a modal asks for after an event: a cursor name, None (Blender's own back) or KEEP.
    t, v: the event's type and value; inside: the cursor over the viewport itself (not a panel, a header, the pop-up
    that started the modal, another area); diameter: the circle's (px at 100 %), None with no circle; adjusting: a radial
    or drag change of the size under way (Blender's radial control sets no cursor: the pointer stays as it was);
    passed: the modal lets the event go on to Blender (PASS_THROUGH), not to itself or to its own session below it"""
    if t == DEACTIVATE:
        return None                                                 # (another window has the focus: it comes back with the next move here)
    if adjusting:
        return KEEP
    if t in MOVES:
        return cursor_for(diameter) if inside else None
    if passed:
        return None if may_open_menu(t, v) else KEEP
    if v == 'PRESS' and inside:
        return cursor_for(diameter)                                 # (a key of the modal: the size changed, the dot or the cross for it)
    return KEEP


class Holder:
    """who owns a window's pointer: the modals with a circle, in the order they started (the last one is on top, as
    Blender's modal handlers: the one added last gets the events first). Only the top one's asks count. A modal cursor is
    no stack (wm_cursors.cc:479-495): whoever restores one (a rebuild's wait cursor, wm.redraw_timer) takes ours too, so a
    modal asks again at every move and a set is never skipped as already done; the restore happens once.
    Actions: ('set', window, cursor) and ('restore', window), a window being its pointer (an int)."""

    def __init__(self):
        self.owners = []
        self.win = None                                             # the window the cursor was set on
        self.cursor = None                                          # what was asked last and not restored since
        self.log = []                                               # the last actions: (action, cursor or None, owner) — the tests read it

    def _note(self, what, owner):
        self.log.append((what, self.cursor, owner))
        del self.log[:-LOG_SIZE]

    def top(self):
        return self.owners[-1] if self.owners else None

    def claim(self, owner):
        """a modal starts: on top (a claim it held already stays where it is)"""
        if owner not in self.owners:
            self.owners.append(owner)

    def shows(self, owner):
        """this owner's circle is the one on the screen: no other modal with a circle of its own started above it (a
        Pick over Split Ends: one circle, the Pick's; when the Pick ends the one below shows again). A modal below
        waits: its circle hidden, the events left to Blender (lot 1.2c, item 5)"""
        tops = [o for o in self.owners if o in OWN_CIRCLE]
        return not tops or tops[-1] == owner or owner not in self.owners

    def holds(self, owner):
        """this owner is on top and asked for a cursor not restored since"""
        return self.top() == owner and self.cursor is not None

    def _restore(self, owner):
        if self.cursor is None:
            return []
        win = self.win
        self._note('restore', owner)
        self.cursor = None; self.win = None
        return [('restore', win)]

    def ask(self, owner, win, want):
        """the actions for `want` (wanted's answer) of `owner` in the window `win` (None: no window, -b)"""
        if want == KEEP:
            return []
        self.claim(owner)
        if self.top() != owner:
            return []                                               # (the modal above owns the pointer)
        if want is None:
            return self._restore(owner)
        if win is None:
            return []
        out = self._restore(owner) if self.win is not None and self.win != win else []
        self.cursor = want; self.win = win
        self._note('set', owner)
        return out + [('set', win, want)]

    def release(self, owner):
        """a modal ends: its claim goes; the cursor back once if it was the top one's. The one below asks at its next
        event"""
        if owner not in self.owners:
            return []
        was_top = self.top() == owner
        self.owners.remove(owner)
        return self._restore(owner) if was_top or not self.owners else []

    def reset(self):
        """every claim dropped (a file loaded, the add-on off)"""
        self.owners = []
        return self._restore(None)


HOLD = Holder()


# ---- the Blender side ----

def _window(ptr):
    import bpy
    for w in bpy.context.window_manager.windows:
        if w.as_pointer() == ptr:
            return w
    return None


def _apply(actions, win=None):
    """the actions on their windows; a window closed meanwhile is skipped. Never raises: it runs from the exits of the
    modals, their except blocks too"""
    for a in actions:
        try:
            w = win if win is not None and win.as_pointer() == a[1] else _window(a[1])
            if w is None:
                continue
            if a[0] == 'set':
                w.cursor_modal_set(a[2])
            else:
                w.cursor_modal_restore()
        except Exception:
            from . import errorlog
            errorlog.record('pointer: ' + a[0])


def claim(owner):
    HOLD.claim(owner)


def ask(owner, context, want):
    """`want`: a cursor name, None or KEEP, in the window of `context`"""
    win = getattr(context, 'window', None)
    _apply(HOLD.ask(owner, win.as_pointer() if win is not None else None, want), win)


def follow(owner, context, event, inside, diameter, adjusting=False, passed=True):
    """after a modal handled an event: the pointer `wanted` says"""
    ask(owner, context, wanted(event.type, event.value, inside, diameter, adjusting, passed))


def holds(owner):
    return HOLD.holds(owner)


def shows(owner):
    return HOLD.shows(owner)


def release(owner):
    """the modal `owner` ended (any way: Esc, a click that finishes, an exception, cancel, the sub-tool left). Needs no
    context nor instance: a cancel may run on the class"""
    _apply(HOLD.release(owner))


def reset():
    _apply(HOLD.reset())
