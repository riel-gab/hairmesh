# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""A double click inside a modal operator (lot 4.7b, item 1: the profile editor's delete never worked by hand).

Blender never hands DOUBLE_CLICK to an operator's modal(): wm_event_modalkeymap_begin turns it into a PRESS first
("This bypass just disables support for double-click in modal handlers", wm_event_system.cc), so `event.value ==
'DOUBLE_CLICK'` in a modal is dead code and event_simulate can't show it (it never pairs presses either). A modal
pairs the two presses itself, with Blender's own rule (wm_event_is_double_click): the same button, the first press
released without becoming a drag, the second within the drag threshold of it (Preferences > Input > Drag Threshold)
and less than mouse_double_click_time after it. The time is the handler's (Python sees no event time stamps): a
slow first click — one that commits an edit on its release — eats into it, so a click that doesn't drag must not
commit."""
import time

import bpy


def drag_threshold(ui=None):
    """WM_event_drag_threshold for a mouse: the preference times the UI scale, in region pixels"""
    p = bpy.context.preferences
    return p.inputs.drag_threshold_mouse * (p.system.ui_scale if ui is None else ui)


class DoubleClick:
    """press(event) -> True when this press is the second one of a double click; release(event) after the first
    one's release; dragged() when the first press became a drag (no pairing)"""
    def __init__(self, ui=None):
        self.ui = ui
        self.last = None                                            # [type, time, x, y, released]

    def press(self, event):
        now = time.perf_counter()
        x, y = event.mouse_region_x, event.mouse_region_y
        L = self.last
        th = drag_threshold(self.ui)
        dbl = (L is not None and L[0] == event.type and L[4] and abs(x - L[2]) <= th and abs(y - L[3]) <= th
               and (now - L[1]) * 1000.0 < bpy.context.preferences.inputs.mouse_double_click_time)
        self.last = None if dbl else [event.type, now, x, y, False]   # (a third press starts over: one double click, one action)
        return dbl

    def release(self, event):
        if self.last is not None and self.last[0] == event.type:
            self.last[4] = True

    def other(self, event):
        """another button or key pressed: Blender's prev_type is no longer the button's release, no pair"""
        if event.value == 'PRESS' and (self.last is None or event.type != self.last[0]):
            self.last = None

    def moved(self, event):
        """the button held past the drag threshold of its press: a drag, no pair"""
        L = self.last
        if L is not None and not L[4]:
            th = drag_threshold(self.ui)
            if abs(event.mouse_region_x - L[2]) > th or abs(event.mouse_region_y - L[3]) > th:
                self.last = None

    def dragged(self):
        self.last = None
