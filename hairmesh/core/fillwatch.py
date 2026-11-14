# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The decisions of "Detect Fill Outline Here" between its pies (lot 1.1f), apart from Blender: pure Python, no bpy.
The pies, the outline drawn and the undo step are blender/ops_fill.py; the search is core/fillat.py.

  watch      the watcher that clears the outline when the confirmation pie goes (the original's onClose, 8287 / 8295:
             every way of closing it hides fillViz)
  y_down     the core's screen tools take pixels with y DOWN (the original's toScreen, 8414); a Blender region's
  pointer    projector and its mouse coordinates have y up: both are turned round with the region's height"""


def watch(event_type):
    """the watcher's decision at an event that reaches it: the confirmation pie, in front of it, takes every event
    while it is open, so the first input event that gets through means the pie is gone (Fill, Cancel, Esc, a click
    outside it). 'wait' for what is no input (a timer, nothing), else 'closed'."""
    if event_type.startswith('TIMER') or event_type == 'NONE':
        return 'wait'
    return 'closed'


def y_down(project, h):
    """a projector with y down from one with y up (pick.region_projector, ops_scissors.matrix_projector) for a region
    `h` pixels high; None (behind the camera) stays None"""
    def screen(p):
        r = project(p)
        return None if r is None else (r[0], h - r[1], r[2])
    return screen


def pointer(point, h):
    """the pointer (region pixels, y up) as the core takes it: y down"""
    return (point[0], h - point[1])
