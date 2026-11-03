# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The brushes' shared core (lot 4.7b; the Density brush, 4.8, and the anchor brush, 4.9, reuse it): the fall-off of a
dab, how much a dab adds, the radius and hardness keys and drag, and which operation a press makes. Pure Python, no
bpy; the circle, the keys and the undo step per stroke are blender/brush.py. Line numbers refer to
reference/hair-modeler.clean.html, 1.0.1.

  BRUSH_R / BRUSH_HARD (701)    the original's S.brushR (px) and S.brushHard
  brushWeight (6145)            full strength within hardness x radius, then smoothstep down to 0 at the radius
  dabAmount (5944)              a move's dab adds more the longer the move (the press: 0.2, paintMask's default)
  smaller / bigger (8018-8019)  [ / ]: x0.85 / x1.15, kept within 8-800 px
  adjust (5932-5933)            Ctrl + RMB drag: right = bigger, up = harder (the original's y grows downwards)
  weightOp / tipOp (5765)       the press's operation: Shift smooths; the mask and Density: Ctrl erases; thickness and
                                twist: Ctrl inverts, Ctrl+Alt erases"""
from .js import clamp, js_max, js_min

BRUSH_R = 70                                                        # S.brushR (701), px
BRUSH_HARD = 0.3                                                    # S.brushHard (701)
R_MIN = 8; R_MAX = 800                                              # the keys and the drag (5932, 6060, 8018-8019); the panels' sliders stop at 400 (7021, 9009)
FIRST_DAB = 0.2                                                     # paintMask(m, op, amt = 0.2): the press (5766)


def brushWeight(d, R, hard):
    """port of brushWeight (hair-modeler.clean.html:6145): the weight of a dab at d px from its centre, R the radius,
    hard the share of the radius at full strength"""
    r0 = hard * R
    if d <= r0:
        return 1
    t = clamp((d - r0) / js_max(R - r0, 1e-6), 0, 1)
    return 1 - t * t * (3 - 2 * t)


def dabAmount(dd):
    """a move's dab (5944): min(1, dd / 25) * 0.35, dd the pixels moved since the last event"""
    return js_min(1, dd / 25) * 0.35


def smaller(R):
    return js_max(R_MIN, R * 0.85)                                  # brushSmaller (8018)


def bigger(R):
    return js_min(R_MAX, R * 1.15)                                  # brushBigger (8019)


def adjust(r0, h0, dx, dy_down):
    """the Ctrl + RMB drag (5932-5933): (radius, hardness) dx px to the right and dy_down px downwards of the press"""
    return clamp(r0 + dx, R_MIN, R_MAX), clamp(h0 - dy_down / 200, 0, 1)


def weightOp(kind, shift, ctrl, alt):
    """the weight brush's press (5765): 'smooth' | 'erase' | 'invert' | 'paint'"""
    if shift:
        return 'smooth'
    if kind == 'mask':
        return 'erase' if ctrl else 'paint'
    return ('erase' if alt else 'invert') if ctrl else 'paint'


def tipOp(shift, ctrl):
    """the Density brush's press (5765, tip): Shift smooths, Ctrl erases"""
    return 'smooth' if shift else 'erase' if ctrl else 'paint'


def weightPress(kind, ctrl, alt):
    """whether a press paints at all (5763): Alt is the original's navigation, except Ctrl + Alt on thickness / twist
    (the erase)"""
    return not alt or (ctrl and kind in ('thick', 'twist'))
