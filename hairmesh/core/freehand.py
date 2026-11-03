# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The Freehand mode of the Draw page (ours, docs/features-adicionadas.md; the original has no such thing): a stroke of
screen points -> one polyline on the surface -> one curve through fitPolyline, and only that finished curve goes off the
surface. Pure Python; the viewport only supplies the rays and the hit functions.

The search for a point follows the steps of surfacePoint (hair-modeler.clean.html:6271) without its pickCurve: the nearest
of the `hits` (Project On: the head and the meshes, or the head alone), then the plane. A point that hits nothing keeps the
depth of the last point that hit (the plane facing the view through it); before the first hit, the plane through the pivot,
as planeHit(m, pivot, camDir()).

The lift: a point moved along its own normal carries the surface's noise out with it, more the farther it goes (at 8 % the
stroke runs from side to side), so the fit works on the points ON the surface and each anchor it keeps moves along the
normals averaged over the stroke around it, in a window that grows with the lift.

The filter: a screen sample counts when it lies MIN_PX pixels or more from the last one that counted. The press counts,
the release is one more sample. Fingers shake by a pixel or two and fitPolyline keeps every turn over 50 degrees as a
corner, so samples a pixel apart would turn the shake into corners; six pixels apart it takes a real turn."""
import math
from dataclasses import dataclass, field

from . import edit
from .fit import FIT_TOL, fitPolyline
from .js import js_hypot, js_pow
from .vec3 import Vec3

MIN_PX = 6.0                                                        # the filter, in region pixels at UI scale 1
LIFT = 1.2                                                          # Distance from Head's default, % of the model size: placePoint's 0.012 (4647)
SIMPLIFY_SPAN = 6.0                                                 # Simplify 1 is 2^6 times the original's tolerance
NORMAL_SPAN = 2.5                                                   # the window the lift averages the normals over, in lifts on each side


@dataclass(eq=False)
class Stroke:
    """a stroke in course: the points kept, on the surface; the normal each one is lifted by, None off the surface; the
    last one that hit something and the last screen sample that counted"""
    pts: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    last: Vec3 | None = None
    screen: tuple | None = None


def farEnough(stroke, x, y, min_px=MIN_PX):
    """the filter: True when (x, y) counts, and then it is the new reference"""
    s = stroke.screen
    if s is not None and js_hypot(x - s[0], y - s[1]) < min_px:
        return False
    stroke.screen = (x, y)
    return True


def lifted(p, n, lift):
    return p.clone().addScaledVector(n, lift)


def landing(ray, hits):
    """the answer of `hits` (each hit(ray) -> (point, unit normal or None) or None) nearest along the ray, or None: hair over
    the head wins, as pickObjects over pickables() (surfacePoint, 6272)"""
    best = None; near = math.inf
    for hit in hits:
        h = hit(ray)
        if h is not None:
            d = h[0].distanceTo(ray.origin)
            if d < near:
                best = h; near = d
    return best


def strokePoint(stroke, ray, cam_dir, pivot, hits):
    """where the ray lands: the nearest of `hits`, and that is the last hit, with its normal, or back along the ray where
    the surface gives none (the meshes); else the plane facing the view through the last hit, or the pivot, with no
    normal. Appended to the stroke and returned; None (nothing appended) when the plane is behind the ray."""
    h = landing(ray, hits)
    if h is not None:
        p = stroke.last = h[0].clone()
        n = h[1] if h[1] is not None else ray.direction.clone().multiplyScalar(-1)
    else:
        p = edit.planeHit(ray, stroke.last if stroke.last is not None else pivot, cam_dir)
        n = None
    if p is not None:
        stroke.pts.append(p); stroke.normals.append(n)
    return p


def headHit(P, ray):
    """the head as a hit function, by the three.js raycaster's port"""
    h = edit.pickHead(P, ray)
    return None if h is None else (h['point'].clone(), h['normal'].clone().normalize())


def simplifyTol(simplify):
    """Simplify (0..1) -> fitPolyline's share of the length: 0 is the original's tolerance, bit by bit; more, fewer anchors"""
    return FIT_TOL if simplify <= 0 else FIT_TOL * js_pow(2.0, simplify * SIMPLIFY_SPAN)


def isLine(pts):
    """at least 2 distinct points: less than that is no stroke"""
    return any(p.x != pts[0].x or p.y != pts[0].y or p.z != pts[0].z for p in pts)


def filledNormals(normals):
    """a normal for every point: off the surface the one before it, before the first hit the first one; None with no hit
    at all (a stroke on the plane has nothing to rise from)"""
    cur = next((n for n in normals if n is not None), None)
    if cur is None:
        return None
    out = []
    for n in normals:
        if n is not None:
            cur = n
        out.append(cur)
    return out


def smoothNormal(pts, normals, along, i, reach):
    """the normals around point i, weighted by a triangle `reach` long on each side of it along the stroke, as a unit vector
    (the point's own where they cancel out)"""
    x = y = z = 0.0
    for step in (-1, 1):
        j = i if step > 0 else i - 1
        while 0 <= j < len(pts):
            w = 1.0 - abs(along[j] - along[i]) / reach
            if w <= 0.0:
                break
            n = normals[j]
            x += n.x * w; y += n.y * w; z += n.z * w
            j += step
    v = Vec3(x, y, z)
    return v.normalize() if v.lengthSq() > 0 else normals[i]


def liftedAnchors(pts, normals, fit, lift):
    """the points of `fit` (clones of `pts`, in order), `lift` off the surface along the smoothed normals; as they are with
    no lift or no hit"""
    ns = filledNormals(normals) if lift > 0 and normals else None
    if ns is None:
        return [p for p, _corner in fit]
    along = [0.0]
    for k in range(1, len(pts)):
        along.append(along[k - 1] + pts[k].distanceTo(pts[k - 1]))
    out = []; i = 0
    for p, _corner in fit:
        while pts[i].x != p.x or pts[i].y != p.y or pts[i].z != p.z:
            i += 1
        out.append(lifted(p, smoothNormal(pts, ns, along, i, lift * NORMAL_SPAN), lift))
    return out


def strokeCurve(P, pts, parent=None, simplify=0, sharp=False, normals=None, lift=0):
    """the stroke as one open curve in `parent`, the way a drawing ends (addDrawPointAt, finishDrawing), or None with
    nothing touched: the fit over the points on the surface, then the lift; a corner of the fit is smoothness 0, and every
    anchor with `sharp` (New Anchors)"""
    if not pts or not isLine(pts):
        return None
    fit = fitPolyline(pts, False, simplifyTol(simplify))
    state = edit.DrawState()
    for q, (_p, corner) in zip(liftedAnchors(pts, normals, fit, lift), fit):
        a = edit.addDrawPointAt(P, state, q, parent)
        if corner or sharp:
            a.smooth = 0
    return edit.finishDrawing(P, state)
