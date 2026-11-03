# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Port of the three.js r160 raycast path the disc guide uses (rebuildDisc, hair-modeler.clean.html:1140):
Raycaster.set / .far, intersectObject(mesh) -> Mesh.raycast -> Ray.intersectTriangle, with the bounding sphere
and box early-outs and the final sort by distance.

Sources (node_modules/three/src): math/Ray.js (at, recast, intersectSphere, intersectBox, intersectTriangle,
applyMatrix4), objects/Mesh.js (raycast, _computeIntersections, checkGeometryIntersection, checkIntersection),
core/Raycaster.js (intersectObject, ascSort), core/BufferGeometry.js (computeBoundingBox, computeBoundingSphere),
math/Box3.js, math/Sphere.js, math/Vector3.js (applyMatrix4, transformDirection, fromBufferAttribute).

The mesh is an indexed triangle geometry whose positions were stored as Float32 (Float32BufferAttribute): the
caller rounds them (headobj.float32) before building a TriMesh. The mesh's matrixWorld is the identity: applying
it is exact (x*1 + y*0 + z*0 + 0), except that Ray.applyMatrix4 re-normalises the direction, which is reproduced.
"""
import math
from dataclasses import dataclass, field

from .js import js_max, js_min
from .vec3 import V3, Vec3

FrontSide, BackSide, DoubleSide = 0, 1, 2


@dataclass(eq=False, kw_only=True)
class Box3:
    min: Vec3 = field(default_factory=lambda: V3(math.inf, math.inf, math.inf))
    max: Vec3 = field(default_factory=lambda: V3(-math.inf, -math.inf, -math.inf))

    def expandByPoint(self, p):
        self.min.x = js_min(self.min.x, p.x); self.min.y = js_min(self.min.y, p.y); self.min.z = js_min(self.min.z, p.z)   # (Math.min: a NaN spreads)
        self.max.x = js_max(self.max.x, p.x); self.max.y = js_max(self.max.y, p.y); self.max.z = js_max(self.max.z, p.z)
        return self

    def setFromPoints(self, pts):
        self.min.set(math.inf, math.inf, math.inf); self.max.set(-math.inf, -math.inf, -math.inf)
        for p in pts:
            self.expandByPoint(p)
        return self

    def expandByScalar(self, s):
        self.min.x -= s; self.min.y -= s; self.min.z -= s
        self.max.x += s; self.max.y += s; self.max.z += s
        return self

    def getCenter(self, target):
        return target.addVectors(self.min, self.max).multiplyScalar(0.5)

    def getSize(self, target):
        return target.subVectors(self.max, self.min)

    def containsPoint(self, p):
        return not (p.x < self.min.x or p.x > self.max.x or p.y < self.min.y or p.y > self.max.y or p.z < self.min.z or p.z > self.max.z)

    def intersectsBox(self, box):
        # Box3.intersectsBox: using 6 splitting planes to rule out intersections
        return not (box.max.x < self.min.x or box.min.x > self.max.x or box.max.y < self.min.y or box.min.y > self.max.y
                    or box.max.z < self.min.z or box.min.z > self.max.z)

    def clone(self):
        return Box3(min=self.min.clone(), max=self.max.clone())


@dataclass(eq=False, kw_only=True)
class Sphere:
    center: Vec3 = field(default_factory=V3)
    radius: float = -1.0

    def containsPoint(self, p):
        return p.distanceToSquared(self.center) <= self.radius * self.radius


class Plane:
    # math/Plane.js: the parts planeHit (hair-modeler.clean.html:5178) uses
    def __init__(self, normal=None, constant=0.0):
        self.normal = normal if normal is not None else V3(1, 0, 0)
        self.constant = constant

    def setFromNormalAndCoplanarPoint(self, normal, point):
        self.normal.copy(normal)
        self.constant = -point.dot(self.normal)
        return self

    def distanceToPoint(self, point):
        return self.normal.dot(point) + self.constant


def triangleNormal(a, b, c, target):
    # math/Triangle.js getNormal: (c - b) x (a - b), unit length (the zero vector for a degenerate triangle)
    target.subVectors(c, b)
    v0 = V3().subVectors(a, b)
    target.cross(v0)
    lsq = target.lengthSq()
    if lsq > 0:
        return target.multiplyScalar(1 / math.sqrt(lsq))
    return target.set(0, 0, 0)


class Ray:
    def __init__(self, origin=None, direction=None):
        self.origin = origin if origin is not None else V3()
        self.direction = direction if direction is not None else V3(0, 0, -1)

    def set(self, origin, direction):
        self.origin.copy(origin); self.direction.copy(direction)
        return self

    def copy(self, ray):
        self.origin.copy(ray.origin); self.direction.copy(ray.direction)
        return self

    def at(self, t, target):
        return target.copy(self.origin).addScaledVector(self.direction, t)

    def recast(self, t):
        self.origin.copy(self.at(t, V3()))
        return self

    def applyMatrix4_identity(self):
        # Ray.applyMatrix4 with the identity: origin unchanged, direction re-normalised by transformDirection
        self.direction.normalize()
        return self

    def distanceToPlane(self, plane):
        # Ray.js distanceToPlane: None when the ray misses (parallel, or the plane is behind the origin)
        denominator = plane.normal.dot(self.direction)
        if denominator == 0:
            if plane.distanceToPoint(self.origin) == 0:             # line is coplanar, return origin
                return 0
            return None
        t = -(self.origin.dot(plane.normal) + plane.constant) / denominator
        return t if t >= 0 else None

    def intersectPlane(self, plane, target):
        t = self.distanceToPlane(plane)
        if t is None:
            return None
        return self.at(t, target)

    def intersectSphere(self, sphere, target):
        v = V3().subVectors(sphere.center, self.origin)
        tca = v.dot(self.direction)
        d2 = v.dot(v) - tca * tca
        radius2 = sphere.radius * sphere.radius
        if d2 > radius2:
            return None
        thc = math.sqrt(radius2 - d2)
        t0 = tca - thc; t1 = tca + thc
        if t1 < 0:
            return None
        if t0 < 0:
            return self.at(t1, target)
        return self.at(t0, target)

    def intersectBox(self, box, target):
        o = self.origin; d = self.direction
        invdirx = _inv(d.x); invdiry = _inv(d.y); invdirz = _inv(d.z)
        if invdirx >= 0:
            tmin = (box.min.x - o.x) * invdirx; tmax = (box.max.x - o.x) * invdirx
        else:
            tmin = (box.max.x - o.x) * invdirx; tmax = (box.min.x - o.x) * invdirx
        if invdiry >= 0:
            tymin = (box.min.y - o.y) * invdiry; tymax = (box.max.y - o.y) * invdiry
        else:
            tymin = (box.max.y - o.y) * invdiry; tymax = (box.min.y - o.y) * invdiry
        if tmin > tymax or tymin > tmax:
            return None
        if tymin > tmin or tmin != tmin:
            tmin = tymin
        if tymax < tmax or tmax != tmax:
            tmax = tymax
        if invdirz >= 0:
            tzmin = (box.min.z - o.z) * invdirz; tzmax = (box.max.z - o.z) * invdirz
        else:
            tzmin = (box.max.z - o.z) * invdirz; tzmax = (box.min.z - o.z) * invdirz
        if tmin > tzmax or tzmin > tmax:
            return None
        if tzmin > tmin or tmin != tmin:
            tmin = tzmin
        if tzmax < tmax or tmax != tmax:
            tmax = tzmax
        if tmax < 0:
            return None
        return self.at(tmin if tmin >= 0 else tmax, target)

    def intersectsBox(self, box):
        return self.intersectBox(box, V3()) is not None

    def intersectTriangle(self, a, b, c, backfaceCulling, target):
        # Ray.js:395 (from GeometricTools GteIntrRay3Triangle3)
        edge1 = V3().subVectors(b, a)
        edge2 = V3().subVectors(c, a)
        normal = V3().crossVectors(edge1, edge2)
        DdN = self.direction.dot(normal)
        if DdN > 0:
            if backfaceCulling:
                return None
            sign = 1
        elif DdN < 0:
            sign = -1; DdN = -DdN
        else:
            return None
        diff = V3().subVectors(self.origin, a)
        DdQxE2 = sign * self.direction.dot(edge2.crossVectors(diff, edge2))
        if DdQxE2 < 0:
            return None
        DdE1xQ = sign * self.direction.dot(edge1.cross(diff))
        if DdE1xQ < 0:
            return None
        if DdQxE2 + DdE1xQ > DdN:
            return None
        QdN = -sign * diff.dot(normal)
        if QdN < 0:
            return None
        return self.at(QdN / DdN, target)


def _inv(x):
    # 1 / x with JavaScript's division by zero (±Infinity, NaN for -0 handled by copysign)
    if x == 0:
        return math.copysign(math.inf, x)
    return 1 / x


class TriMesh:
    """An indexed triangle geometry at the identity transform, with the bounds three.js computes lazily."""

    def __init__(self, positions, index, side=DoubleSide):
        self.pos = positions                                        # list[Vec3], already float32-rounded by the caller
        self.index = index                                          # flat list of vertex indices, 3 per triangle
        self.side = side
        self.boundingBox = None
        self.boundingSphere = None
        self._tri_boxes = None                                      # (min, max) arrays, one row per triangle (triangles_touching)
        self._tri_edges = None                                      # (a, edge1, edge2, normal, |edge1|, |edge2|, |normal|) arrays (ray_candidates)

    def triangles_touching(self, box):
        """The starts (index offsets, ascending) of the triangles whose bounding box touches `box` ((min x, y, z),
        (max x, y, z)): a prefilter for a short ray segment (ours, 16/10/2026), exact — a triangle a segment hits
        has that point in both boxes."""
        import numpy as np
        if self._tri_boxes is None:
            idx = self.index; n = len(idx) // 3
            P = np.array([(p.x, p.y, p.z) for p in self.pos], dtype=np.float64)
            tri = P[np.array(idx[:n * 3], dtype=np.int64).reshape(n, 3)]   # n x 3 corners x 3 coordinates
            self._tri_boxes = (tri.min(axis=1), tri.max(axis=1))
        lo, hi = self._tri_boxes
        (x0, y0, z0), (x1, y1, z1) = box
        ok = (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1) & (hi[:, 2] >= z0) & (lo[:, 2] <= z1)
        return (np.flatnonzero(ok) * 3).tolist()

    PREFILTER_MIN = 64                                              # (fewer triangles: the plain loop is as quick)
    LOOSE = 1e-9                                                    # the margin, relative to each test's magnitude (the rounding of either side is ~1e-15 of it)

    def ray_candidates(self, ray, backfaceCulling):
        """The starts (index offsets, ascending) of the triangles Ray.intersectTriangle(a, b, c, backfaceCulling) may
        accept (ours, lot 4.7b item 5: the occlusion test of Shift + drag raycast the head ~10 times per move, 36 ms
        each): its tests run here in float64 numpy, each loosened by LOOSE times its magnitude — far above the rounding
        of either side — so this is a superset of the triangles it accepts, and testing only these gives the same
        hits in the same order. A triangle whose DdN is too close to 0 to have a sure sign stays in."""
        import numpy as np
        if self._tri_edges is None:
            idx = self.index; n = len(idx) // 3
            P = np.array([(p.x, p.y, p.z) for p in self.pos], dtype=np.float64)
            tri = P[np.array(idx[:n * 3], dtype=np.int64).reshape(n, 3)]
            a = tri[:, 0]; e1 = tri[:, 1] - a; e2 = tri[:, 2] - a; nn = np.cross(e1, e2)
            self._tri_edges = (a, e1, e2, nn, np.linalg.norm(e1, axis=1), np.linalg.norm(e2, axis=1), np.linalg.norm(nn, axis=1))
        a, e1, e2, nn, l1, l2, ln = self._tri_edges
        d = np.array((ray.direction.x, ray.direction.y, ray.direction.z), dtype=np.float64)
        o = np.array((ray.origin.x, ray.origin.y, ray.origin.z), dtype=np.float64)
        ld = float(np.linalg.norm(d))
        diff = o - a; lq = np.linalg.norm(diff, axis=1)
        DdN = nn @ d
        t0 = self.LOOSE * ld * ln
        sure = np.abs(DdN) > t0
        s = np.where(DdN > 0, 1.0, -1.0)
        q2 = np.einsum('ij,j->i', np.cross(diff, e2), d) * s; t1 = self.LOOSE * ld * lq * l2
        q1 = np.einsum('ij,j->i', np.cross(e1, diff), d) * s; t2 = self.LOOSE * ld * l1 * lq
        qn = -np.einsum('ij,ij->i', diff, nn) * s; t3 = self.LOOSE * lq * ln
        ok = (q2 >= -t1) & (q1 >= -t2) & (q2 + q1 <= np.abs(DdN) + t0 + t1 + t2) & (qn >= -t3)
        if backfaceCulling:
            ok &= DdN < t0                                          # (DdN > 0 is culled; near 0: unsure, kept)
        ok |= ~sure                                                 # (an unsure sign: the exact test decides)
        return (np.flatnonzero(ok) * 3).tolist()

    def computeBoundingBox(self):
        # BufferGeometry.computeBoundingBox: Box3.setFromBufferAttribute
        self.boundingBox = Box3().setFromPoints(self.pos)
        return self.boundingBox

    def computeBoundingSphere(self):
        # BufferGeometry.computeBoundingSphere: centre of the box, radius = the farthest vertex
        box = Box3().setFromPoints(self.pos)
        center = box.getCenter(V3())
        maxRadiusSq = 0
        for p in self.pos:
            maxRadiusSq = js_max(maxRadiusSq, center.distanceToSquared(p))
        self.boundingSphere = Sphere(center=center, radius=math.sqrt(maxRadiusSq))
        return self.boundingSphere


class Raycaster:
    def __init__(self, near=0, far=math.inf):
        self.ray = Ray()
        self.near = near
        self.far = far

    def set(self, origin, direction):
        self.ray.set(origin, direction)                             # direction is assumed to be normalised

    def intersectObject(self, mesh, seg_box=None):
        """Raycaster.intersectObject(mesh, recursive=false): intersections sorted by distance. `seg_box` (ours,
        16/10/2026): the box ((min x, y, z), (max x, y, z)) the ray's segment [near, far] lies in; only the
        triangles whose box touches it are tested (TriMesh.triangles_touching) — a hit on the segment lies in both
        boxes, so the hits are the same and come out in the same order."""
        intersects = []
        _mesh_raycast(mesh, self, intersects, seg_box)
        intersects.sort(key=lambda h: h['distance'])                # ascSort: a.distance - b.distance (stable in both)
        return intersects


PREFILTER = [True]                                                  # (tests/test_raycast_prefilter.py turns it off to compare)


def _mesh_raycast(mesh, raycaster, intersects, seg_box=None):
    # Mesh.raycast (Mesh.js:150), matrixWorld = identity
    if mesh.boundingSphere is None:
        mesh.computeBoundingSphere()
    sphere = mesh.boundingSphere                                    # _sphere.applyMatrix4(identity): unchanged
    ray = Ray().copy(raycaster.ray).recast(raycaster.near)
    if not sphere.containsPoint(ray.origin):
        hit = ray.intersectSphere(sphere, V3())
        if hit is None:
            return
        d = raycaster.far - raycaster.near                          # (far - near) ** 2: x * x, as V8 computes x ** 2 (js_pow)
        if ray.origin.distanceToSquared(hit) > d * d:
            return
    ray = Ray().copy(raycaster.ray).applyMatrix4_identity()        # _ray.copy(raycaster.ray).applyMatrix4(_inverseMatrix)
    if mesh.boundingBox is not None:
        if not ray.intersectsBox(mesh.boundingBox):
            return
    _compute_intersections(mesh, raycaster, intersects, ray, seg_box)


def _compute_intersections(mesh, raycaster, intersects, rayLocal, seg_box=None):
    # Mesh._computeIntersections, indexed geometry, one material, no drawRange / groups
    pos = mesh.pos; idx = mesh.index
    if seg_box is not None:
        starts = mesh.triangles_touching(seg_box)
    elif len(idx) >= 3 * TriMesh.PREFILTER_MIN and mesh.side != BackSide and PREFILTER[0]:
        starts = mesh.ray_candidates(rayLocal, mesh.side == FrontSide)   # (the same hits: ray_candidates)
    else:
        starts = range(0, len(idx) - len(idx) % 3, 3)
    for i in starts:
        a = idx[i]; b = idx[i + 1]; c = idx[i + 2]
        hit = _check_intersection(mesh, raycaster, rayLocal, pos[a], pos[b], pos[c])
        if hit is not None:
            hit['faceIndex'] = i // 3
            intersects.append(hit)


def _check_intersection(mesh, raycaster, ray, pA, pB, pC):
    # Mesh.js checkIntersection
    point = V3()
    if mesh.side == BackSide:
        intersect = ray.intersectTriangle(pC, pB, pA, True, point)
    else:
        intersect = ray.intersectTriangle(pA, pB, pC, mesh.side == FrontSide, point)
    if intersect is None:
        return None
    world = point.clone()                                           # applyMatrix4(identity): exact
    distance = raycaster.ray.origin.distanceTo(world)
    if distance < raycaster.near or distance > raycaster.far:
        return None
    # Mesh.checkGeometryIntersection: intersection.face = { a, b, c, normal: Triangle.getNormal(vA, vB, vC) }
    return {'distance': distance, 'point': world, 'normal': triangleNormal(pA, pB, pC, V3())}
