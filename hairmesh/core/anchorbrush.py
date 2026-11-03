# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Lot 4.9, item M: the anchor brush (S.tool 'brush'), pure Python: what the press captures (Screen or Surface), a move of
the stroke (the plain drag, Shift's smoothing, Ctrl's inflate and Ctrl+Alt's shrink). The projection, the cursor's ray and
what the cursor touches come in a BrushView, so the same code runs with the camera the harness recorded and with a 3D
View (blender/brush.py: the session, the circle, the undo step per stroke). Line numbers refer to
reference/hair-modeler.clean.html, 1.0.1.

Bits: the formulas keep the original's order of operations (docs/js-para-python.md). What is ours only leaves work out,
and never a result: a squared distance rules out an anchor far from the circle before js_hypot; `through` answers
curvesWith for the whole stroke (no item changes in one) and `dirty` is the fixed capture's markAnchorsDirty."""
from dataclasses import dataclass, field

from .brush import brushWeight
from .edit import isRingAnchor, isVisible, planeHit
from .fdlibm import tan
from .groups import activeGroups, guidesShown, looseCurveIds, shownCurves
from .guides import discRings
from .js import js_hypot, js_max
from .rows import surfaceComponents
from .vec3 import V3, Vec3

SCREEN = 'screen'
SURFACE = 'surface'                                                 # S.brushMode (701, 9006-9007)
NEAR_PX = 14                                                        # brushCenter3D's near(14): the curve under the cursor (6092)
SMOOTH_SHARE = 0.15                                                 # smoothStep: w * 0.15 (6114)
STROKE_PX = 250                                                     # the inflate's pixels per 100 % (5948)
NO_LOCK = 'No lock anchors under the brush'                         # startInflate's hud (6136)
DEG2RAD = 3.141592653589793 / 180                                   # THREE.MathUtils.DEG2RAD


def tanHalf(fov):
    """Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) (6084), the fov in degrees"""
    return tan(fov / 2 * DEG2RAD)


@dataclass(eq=False)
class BrushView:
    """the camera of the original as the brush reads it. screen(p): toScreen (x, y, z), None for what a 3D View's
    projector cannot place (behind the camera); ray(m): rayAt; hit(m): the front-most thing under the cursor as
    (point, distance), else None: pickObjects(m.x, m.y, pickables() minus the reference objects); near(m, r): optional, the
    samples of the curves to look at for a radius, in the order of brushCenter3D's loops (a shortcut: any sample left
    out must be farther than r). tan_half: tanHalf(camera.fov), ortho: (camera.top - camera.bottom) / camera.zoom."""
    screen: object
    ray: object
    cam_dir: Vec3
    cam_pos: Vec3
    H: float
    tan_half: float | None = None
    ortho: float | None = None
    hit: object = None
    near: object = None


@dataclass(eq=False)
class Stroke:
    """the original's drag = { type: 'brush' | 'brushSmooth' | 'inflate', ... } (6135, 6139, 6142), and what stays the same
    through a stroke: the anchors the brush works on, the curves through each, the settings of the press"""
    type: str
    anchors: list
    through: dict
    shown: set
    R: float
    hard: float
    mode: str
    scale: float
    px: float = 1
    cap: list = field(default_factory=list)                         # 'brush': (a, w, start, hit0); 'inflate': (a, w, start, cen)
    n: Vec3 | None = None
    sign: int = 1
    last: tuple | None = None
    dist: float = 0
    moved: bool = False
    dirty: list = field(default_factory=list)                       # the curves markAnchorsDirty(the capture) marks: every move of a fixed capture


def anchorOp(shift, ctrl, alt):
    """what a left press of the Brush does (5761, 5768, 5822): 'shrink' (Ctrl+Alt, not orbit), None (Alt alone: the
    navigation), 'inflate' (Ctrl, with or without Shift), 'smooth' (Shift), 'move'"""
    if ctrl and alt:
        return 'shrink'
    if alt:
        return None
    if ctrl:
        return 'inflate'
    return 'smooth' if shift else 'move'


def brushAnchors(P, sel_items, anchor_list):
    """port of brushAnchors (hair-modeler.clean.html:6076): with curves (or a NURBS disc) selected the brush works only on
    their anchors; else on every anchor shown but a disc's rings. `anchor_list`: S.anchorList (edit.anchorList)"""
    items = [it for it in (P.items.get(i) for i in sel_items) if it is not None]
    sc = [it for it in items if it.type == 'curve']
    discs = [g for g in items if getattr(g, 'kind', None) == 'disc']
    if not sc and not discs:
        return [a for a in anchor_list if not isRingAnchor(P, a)]
    keep = set()
    for c in sc:
        keep.update(c.anchors)
    for g in discs:
        for c in discRings(P, g):
            keep.update(c.anchors)
    return [a for a in anchor_list if a in keep]


def brushVisible(P, sel_items, sel_anchors, drawing=None, active_group=None):
    """the ids of the curves c.obj.visible says are on screen (syncVisuals 11945-11952, brushCenter3D's `near`): the hair
    curves their group's mode shows, and a disc's rings while the disc is"""
    out = {c.id for c in shownCurves(P, sel_items, sel_anchors, drawing, active_group)[0]}
    act = None
    for c in P.curves():
        if c.ring:
            d = P.items.get(c.ring)
            if d is None or not isVisible(P, d):
                continue
            if act is None:
                act = activeGroups(P, [i for i in sel_items if (it := P.items.get(i)) is None or isVisible(P, it)], sel_anchors, drawing, active_group)
                loose = looseCurveIds(P)
            if guidesShown(P, d, act, loose):
                out.add(c.id)
    return out


def anchorCurves(P):
    """curvesWith for every anchor at once: {anchor: its curves, in scene order}"""
    out = {}
    for c in P.curves():
        for a in c.anchors:
            cs = out.get(a)
            if cs is None:
                out[a] = [c]
            elif cs[-1] is not c:                                   # (a closed curve that lists an anchor twice: curvesWith has it once)
                cs.append(c)
    return out


def worldPerPixelAt(view, p, scale):
    """port of worldPerPixelAt (hair-modeler.clean.html:6081); `scale`: S.scale"""
    if view.ortho is not None:
        return view.ortho / view.H
    d = js_max(p.clone().sub(view.cam_pos).dot(view.cam_dir), scale * 1e-3)
    return 2 * d * view.tan_half / view.H


def brushCenter3D(P, view, m, R, anchors, shown, near_px=NEAR_PX):
    """port of brushCenter3D (hair-modeler.clean.html:6088): the 3D point the brush touches: the front-most thing under the
    cursor, a nearer curve within near_px of it, else the nearest anchor under the circle. `anchors`: brushAnchors.
    Returns a new point, or None."""
    best = None; bd = float('inf')
    h = view.hit(m) if view.hit is not None else None
    if h is not None:
        best = h[0].clone(); bd = h[1]
    x = m[0]; y = m[1]; cam = view.cam_pos; screen = view.screen
    if view.near is not None:
        samples = view.near(m, near_px)
    else:
        samples = (s for c in P.curves() if c.id in shown for s in c.samples)
    for s in samples:
        q = screen(s.p)
        if q is None or q[2] > 1 or js_hypot(q[0] - x, q[1] - y) > near_px:
            continue
        d = s.p.distanceTo(cam)
        if d < bd:
            bd = d; best = s.p.clone()
    if best is None:
        for a in anchors:
            q = screen(a.pos)
            if q is None or q[2] > 1 or js_hypot(q[0] - x, q[1] - y) > R:
                continue
            d = a.pos.distanceTo(cam)
            if d < bd:
                bd = d; best = a.pos.clone()
    return best


def brushCapture(P, view, m, R, hard, mode, anchors, shown, scale, near_px=NEAR_PX):
    """port of brushCapture (hair-modeler.clean.html:6096): [(anchor, weight)] under the brush"""
    out = []
    if mode != SURFACE:
        x = m[0]; y = m[1]; screen = view.screen
        far = R * R * 1.0001                                        # (past this squared the anchor is out whatever the hypot rounds to)
        for a in anchors:
            s = screen(a.pos)
            if s is None:
                continue
            dx = s[0] - x; dy = s[1] - y
            if dx * dx + dy * dy > far:
                continue
            d = js_hypot(dx, dy)
            if d < R and s[2] < 1:
                out.append((a, brushWeight(d, R, hard)))
        return out
    c = brushCenter3D(P, view, m, R, anchors, shown, near_px)
    if c is None:
        return out
    Rw = R * worldPerPixelAt(view, c, scale)
    for a in anchors:
        d = a.pos.distanceTo(c) / Rw * R
        if d < R:
            out.append((a, brushWeight(d, R, hard)))
    return out


def _marked(st, anchors):
    """markAnchorsDirty (1340) over st.through: the curves through the anchors, and the curves touching those"""
    dirty = {}
    for a in anchors:
        for c in st.through[a]:
            dirty[c] = True
    for c in list(dirty):
        for b in c.anchors:
            for k in st.through[b]:
                dirty[k] = True
    return list(dirty)


def _capture(P, view, m, st):
    return brushCapture(P, view, m, st.R, st.hard, st.mode, st.anchors, st.shown, st.scale, NEAR_PX * st.px)


def smoothStep(P, view, m, st):
    """port of smoothStep (hair-modeler.clean.html:6105): Shift + brush — every anchor under the brush moves towards the
    middle of its neighbours on each curve through it (curve ends stay, rings wrap around). Returns whether one moved."""
    moves = []
    for a, w in _capture(P, view, m, st):
        t = V3(); n = 0
        for c in st.through[a]:
            cs = c.anchors
            k = cs.index(a); L = len(cs)
            if not c.closed and (k <= 0 or k >= L - 1):
                continue
            t.add(cs[(k + L - 1) % L].pos).add(cs[(k + 1) % L].pos); n += 2
        if n:
            moves.append((a, t.divideScalar(n), w * SMOOTH_SHARE))
    for a, t, w in moves:
        a.pos.lerp(t, w)
    if moves:
        for c in _marked(st, [x[0] for x in moves]):
            c.dirty = True
        P.rev += 1
        st.moved = True
    return bool(moves)


def startStroke(P, view, m, op, R, hard, mode, anchors, shown, scale, px=1):
    """the press (5761, 5822): `op` as anchorOp gives it, `anchors`: brushAnchors, `shown`: brushVisible, `px`: the
    viewport's pixels per pixel of the original (the UI scale). Returns the Stroke; a Shift press has moved its first
    step already (startBrush 6139)."""
    st = Stroke('brushSmooth' if op == 'smooth' else 'brush' if op == 'move' else 'inflate', anchors, anchorCurves(P), shown, R, hard, mode, scale, px)
    if op == 'smooth':
        smoothStep(P, view, m, st)
        return st
    if op == 'move':
        _startBrush(P, view, m, st)
    else:
        _startInflate(P, view, m, st, 1 if op == 'inflate' else -1)
    st.dirty = _marked(st, [x[0] for x in st.cap])
    return st


def _startBrush(P, view, m, st):
    # port of startBrush (hair-modeler.clean.html:6138), the plain drag
    n = st.n = view.cam_dir.clone()
    ray = view.ray(m)
    st.cap = [(a, w, a.pos.clone(), planeHit(ray, a.pos, n)) for a, w in _capture(P, view, m, st)]


def _startInflate(P, view, m, st, sign):
    # port of startInflate (hair-modeler.clean.html:6123): Ctrl + brush, inflate (sign 1), Ctrl+Alt, shrink (sign -1). Every
    # anchor under the brush is scaled away from / towards the middle of its lock's cross-section there: the average of
    # the nearest points of all curves of the lock. Curves outside locks and the shared tip stay.
    lockOf = {}
    for ids in surfaceComponents(P):
        for i in ids:
            lockOf[i] = ids
    cap = []
    for a, w in _capture(P, view, m, st):
        c = next((x for x in st.through[a] if x.id in lockOf), None)
        if c is None:
            continue
        cen = V3(); n = 0
        for i in lockOf[c.id]:
            o = P.items.get(i)
            if o is None or not o.samples:
                continue
            best = None; bd = float('inf')
            for s in o.samples:
                d = s.p.distanceToSquared(a.pos)
                if d < bd:
                    bd = d; best = s.p
            cen.add(best); n += 1
        if n >= 2:
            cap.append((a, w, a.pos.clone(), cen.divideScalar(n)))
    st.cap = cap; st.sign = sign; st.last = m


def moveStroke(P, view, m, st):
    """a move of the stroke (the cases 'brush', 'brushSmooth', 'inflate' of pointermove, 5938-5953). Returns whether
    something moved."""
    if st.type == 'brushSmooth':
        return smoothStep(P, view, m, st)
    if st.type == 'brush':
        ray = view.ray(m); n = st.n; cap = st.cap
        for a, w, start, hit0 in cap:
            h = planeHit(ray, start, n)
            if h is not None and hit0 is not None:
                a.pos.copy(start).addScaledVector(h.sub(hit0), w)
    else:
        # the longer the stroke, the stronger: Ctrl fatter, Ctrl+Alt thinner
        st.dist += js_hypot(m[0] - st.last[0], m[1] - st.last[1]) / st.px; st.last = m
        s = 1 + st.dist / STROKE_PX if st.sign > 0 else js_max(0, 1 - st.dist / STROKE_PX)
        for a, w, start, cen in st.cap:
            a.pos.copy(cen).addScaledVector(start.clone().sub(cen), 1 + (s - 1) * w)
    if not st.cap:
        return False
    for c in st.dirty:
        c.dirty = True
    P.rev += 1
    st.moved = True
    return True
