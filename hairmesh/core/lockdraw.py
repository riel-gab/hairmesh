# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The Lock mode of the Draw sub-tool (1.0.2: S.drawMode 'lock', 11364-11376): the width of the next point — the circle
at the cursor, S.ribR (its half, px) — and which keys change it. Pure Python, no bpy: blender/brush.py (the session
that draws the circle and reads the keys, kind LOCK) and blender/ops_draw.py (the drawing modal, which leaves those
keys to it) ask here. Line numbers refer to reference/hair-modeler.clean.html, 1.0.2.

  The Width is the whole width on the screen, as the original's slider shows it (Math.round(S.ribR * 2) + 'px',
  11371); the core reads its half (rib.RIB_R_MIN..RIB_R_MAX = 3..400, 11372)
  Ctrl + RMB drag  right = wider: S.ribR = clamp(r0 + dx, 3, 400) (7731)
  [ / ]  ours: x0.85 / x1.15 as the brush's radius (8018-8019); F: Blender's radial control, as the brush's
  (the original's Ctrl + wheel, 7901, is not ported: the wheel stays Blender's zoom)"""
from .js import clamp
from .rib import RIB_R, RIB_R_MAX, RIB_R_MIN

W_MIN = 2 * RIB_R_MIN                                               # 6 px
W_MAX = 2 * RIB_R_MAX                                               # 800 px
W_DEFAULT = 2 * RIB_R                                               # 60 px (S.ribR = 30, 5818)
SNAP = 5                                                            # Ctrl in the radial control: Blender snaps a size to 10 px of diameter


def half(w):
    """S.ribR of a width (px)"""
    return w / 2


def width_of(r):
    """the whole width (px, an integer for the field) of a half width r, kept within 3..400 (11372)"""
    return int(round(clamp(r, RIB_R_MIN, RIB_R_MAX) * 2))


def smaller(w):
    return width_of(half(w) * 0.85)


def bigger(w):
    # (a width of 6 or 7 grows by a pixel at least: x1.15 of so little rounds back to it)
    n = width_of(half(w) * 1.15)
    return n if n != w or w >= W_MAX else w + 1


def dragged(w0, dx):
    """the Ctrl + RMB drag (8716): dx px to the right of the press"""
    return width_of(half(w0) + dx)


def radial(w0, dx, snap=False):
    """F (ours, Blender's radial control as the brush's radius): the half width grows a pixel for a pixel of the
    pointer's way to the right; Ctrl snaps it"""
    r = half(w0) + dx
    if snap:
        r = round(r / SNAP) * SNAP
    return width_of(r)


def lock_key(t, v, shift, ctrl, alt, oskey=False):
    """what a key of the Lock mode does with no width change under way: 'smaller' | 'bigger' | 'radial' | 'drag' |
    None (not one of them: the event goes on, to the drawing or to Blender). t, v: Blender's event type and value"""
    if v != 'PRESS' or alt or oskey:
        return None
    if t in ('LEFT_BRACKET', 'RIGHT_BRACKET') and not shift and not ctrl:
        return 'smaller' if t == 'LEFT_BRACKET' else 'bigger'
    if t == 'F' and not shift and not ctrl:
        return 'radial'
    if t == 'RIGHTMOUSE' and ctrl and not shift:
        return 'drag'
    return None


def leave_to_width(t, v, shift, ctrl, alt, adjusting, oskey=False):
    """whether the drawing modal leaves this event to the width session below it (the session reads the keys of
    lock_key, the moves that put its circle under the cursor, and everything while a width change is under way — the
    LMB or Enter that keeps a radial width, the RMB release that ends a drag)"""
    if adjusting:
        return True
    if t in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
        return True
    return lock_key(t, v, shift, ctrl, alt, oskey) is not None


def header(w):
    return f'Width: {w} px'
