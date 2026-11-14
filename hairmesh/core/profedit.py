# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Step 4.6b (D4.6): the profile editor's rules — a port of profileEditor (hair-modeler.clean.html:10167) without the
canvas: the frame's scale and the screen <-> profile mapping, the point under the pointer, the snap of Shift, the
glued points that move together (Ctrl: one alone), the glue to a neighbour within 8 px, a point added on the nearest
edge (RMB) and one deleted (double click). Screen coordinates are the frame's own: x to the right, y DOWN, in pixels
of the original's 238 x 188 frame (blender/profile_editor.py scales the UI to it, so the 8 and 9 px are the original's).

For a modifier's three profiles (tubeProps 7160-7188) a point added in one goes into the profiles after it at the
same place on that edge and into the ones before it glued to its neighbour (insertAt), and a point deleted goes from
all three."""
import math

from .js import clamp, js_hypot, js_round
from .tube import gluedRun

FRAME_W, FRAME_H = 238, 188                                         # (6825)
PICK_PX = 9                                                         # nearest(): within 9 px (6858)
GLUE_PX = 8                                                         # the neighbours of a moved run within 8 px take it over (6886)
TOO_FEW = 'A profile needs at least 3 points'                       # (6903)


def scale(W=FRAME_W, H=FRAME_H):
    """sc (6829): the profile's unit in pixels"""
    return min(W / 2.5, H / 2.4)


def to_screen(q, W=FRAME_W, H=FRAME_H):
    """toS (10173): a profile point -> frame pixels (y down)"""
    sc = scale(W, H)
    return (W / 2 + q[0] * sc, H / 2 - q[1] * sc)


def to_profile(sx, sy, W=FRAME_W, H=FRAME_H):
    """toP (6830): frame pixels -> a profile point, clamped to [-1.2, 1.2] x [-1.1, 1.1]"""
    sc = scale(W, H)
    return [clamp((sx - W / 2) / sc, -1.2, 1.2), clamp((H / 2 - sy) / sc, -1.1, 1.1)]


def nearest(pts, sx, sy, W=FRAME_W, H=FRAME_H):
    """nearest (6858): the index of the point within PICK_PX of (sx, sy), the nearest, else -1"""
    bi = -1; bd = PICK_PX
    for i, q in enumerate(pts):
        x, y = to_screen(q, W, H)
        d = js_hypot(x - sx, y - sy)
        if d < bd:
            bd = d; bi = i
    return bi


def snap(q, shift):
    """snap (6859): Shift rounds both coordinates to 0.05"""
    return [js_round(v * 20) / 20 for v in q] if shift else q


def press(pts, i, ctrl, glue=True):
    """the LMB press on point i (6863-6866): the run that moves — the glued points together, Ctrl: this one alone"""
    return gluedRun(pts, i) if glue and not ctrl else [i]


def move(pts, group, sx, sy, shift, ctrl, glue=True, W=FRAME_W, H=FRAME_H, skip=()):
    """a drag step (6880-6891): the moved run's points go to the pointer (snapped with Shift); without Ctrl, the
    neighbour before its first point or after its last within GLUE_PX takes it over (the points glue). `skip` (ours,
    lot 1.0.3): points that move along with the run (move_many), never a neighbour to glue to"""
    n = len(pts)
    q = snap(to_profile(sx, sy, W, H), shift)
    if glue and not ctrl:
        x, y = to_screen(q, W, H)
        for k in ((group[0] - 1 + n) % n, (group[len(group) - 1] + 1) % n):
            if k in group or k in skip:
                continue
            kx, ky = to_screen(pts[k], W, H)
            if js_hypot(kx - x, ky - y) < GLUE_PX:
                q = list(pts[k]); break
    for i in group:
        pts[i] = list(q)
    return q


def insert_at(pts, sx, sy, shift, W=FRAME_W, H=FRAME_H):
    """the RMB (6868-6877): the nearest edge on screen (bi, the point after it at bi + 1) and the parameter s along it,
    and the new point (the pointer, snapped). Returns (bi, s, q); the caller inserts (a single profile: at bi + 1).
    (Shift + RMB never reaches the editor in the original: the window's handler opens the main pie first, 7968.)"""
    bi = 0; bd = math.inf; bt = 0.5
    n = len(pts)
    for i in range(n):
        ax, ay = to_screen(pts[i], W, H); bx, by = to_screen(pts[(i + 1) % n], W, H)
        vx = bx - ax; vy = by - ay; l2 = vx * vx + vy * vy
        t = clamp(((sx - ax) * vx + (sy - ay) * vy) / l2, 0, 1) if l2 > 0 else 0
        d = js_hypot(ax + vx * t - sx, ay + vy * t - sy)
        if d < bd:
            bd = d; bi = i; bt = t
    return bi, bt, snap(to_profile(sx, sy, W, H), shift)


def insert_three(P, k, bi, s, q):
    """insertAt (tubeProps 7160): the new point q in profile k at bi + 1; in the profiles after k at the same place on
    that edge (a + (b - a) s); in the ones before k glued to the nearer end of that edge"""
    for i, A in enumerate(P):
        a = A[bi]; b = A[(bi + 1) % len(A)]
        A.insert(bi + 1, list(q) if i == k else [a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s] if i > k else list(a if s < 0.5 else b))


def can_delete(pts):
    """a double click deletes a point only while the profile keeps at least 3 (6903)"""
    return len(pts) > 3


# ---- the selection (lot 1.0.2, ours: the original's click only drags, 6853-6897; docs/features-adicionadas.md) ----
# A point is (frame k, index i); a modifier's three profiles share their indices (insert_three, a delete from all three).

def select_press(sel, p, shift):
    """a press on point p -> (selection, what the release does): an unselected point is selected at once (alone, or
    added with Shift) so a drag moves the point shown selected; on a selected one the release decides (select_release)"""
    if p not in sel:
        return ([*sel, p] if shift else [p]), None
    return list(sel), ('toggle' if shift else 'only', p)


def select_release(sel, pending):
    """a release without a drag: Shift+click on a selected point takes it out (Blender's toggle); a click alone keeps
    only it"""
    if pending is None:
        return list(sel)
    how, p = pending
    return [q for q in sel if q != p] if how == 'toggle' else [p]


def delete_points(Ps, idx, sel):
    """X / Delete on the selected points (and the double click, one index): the indices go from every profile of Ps;
    None (nothing done) when a profile would keep fewer than 3 (6903); else the selection left, its indices moved down"""
    idx = sorted(set(idx))
    if not idx or any(len(A) - len(idx) < 3 for A in Ps):
        return None
    for A in Ps:
        for i in reversed(idx):
            A.pop(i)
    gone = set(idx)
    return [(k, i - sum(1 for d in idx if d < i)) for k, i in sel if i not in gone]


def insert_selection(sel, bi, k):
    """a point inserted at bi + 1 of frame k (RMB): the new point is the selection"""
    return [(k, bi + 1)]


# ---- the drag of the selection (lot 1.0.3, ours: the original drags one point, 6863-6891) ----

def drag_group(pts, sel, k, i, ctrl, glue=True):
    """the press on point i of frame k that may become a drag, after select_press: (group, others). `group` is the run
    press() moves to the pointer, as the original's drag of one point; `others`, the other selected points of frame k
    (with their glued runs; Ctrl: each alone, as press()), which move by the same offset. Selected points of the other
    frames stay. Empty when point i is not selected or is the only selected point of frame k: the drag of one point"""
    group = press(pts, i, ctrl, glue)
    if (k, i) not in sel:
        return group, []
    others = []
    for kk, j in sel:
        if kk != k or j in group or j in others or not 0 <= j < len(pts):
            continue
        for m in press(pts, j, ctrl, glue):
            if m not in group and m not in others:
                others.append(m)
    return group, others


def drag_start(pts, i, others):
    """where the dragged point and the others were at the press (move_many measures the offset from them)"""
    return {j: list(pts[j]) for j in (i, *others)}


def move_many(pts, i, group, others, start, sx, sy, shift, ctrl, glue=True, W=FRAME_W, H=FRAME_H):
    """a drag step of the selection: `group` (point i's run) as move() does — to the pointer, snapped with Shift, glued
    to a neighbour that does not move —; each of `others` from where it was at the press by the offset point i made.
    The frame's bounds (to_profile's) hold for every point: the offset is cut so that none leaves it, and the whole
    selection stops at the edge, its shape kept (a point clamped alone would land on a neighbour clamped to the same
    edge, and the two would stay glued). Without others, move() itself (bit for bit)"""
    q = move(pts, group, sx, sy, shift, ctrl, glue, W, H, skip=others)
    if others:
        a = start[i]
        dx = q[0] - a[0]; dy = q[1] - a[1]
        cx = clamp(dx, min(0, max(-1.2 - start[j][0] for j in others)), max(0, min(1.2 - start[j][0] for j in others)))   # (min / max with 0: a point already outside, from a file, never turns the bounds round)
        cy = clamp(dy, min(0, max(-1.1 - start[j][1] for j in others)), max(0, min(1.1 - start[j][1] for j in others)))
        if cx != dx or cy != dy:                                    # (an offset in [dx, 0] keeps point i inside too)
            q = [clamp(a[0] + cx, -1.2, 1.2), clamp(a[1] + cy, -1.1, 1.1)]   # (the clamp: a rounding past the edge)
            for g in group:
                pts[g] = list(q)
        for j in others:
            b = start[j]
            pts[j] = [clamp(b[0] + cx, -1.2, 1.2), clamp(b[1] + cy, -1.1, 1.1)]   # (the clamp: a rounding past the edge)
    return q
