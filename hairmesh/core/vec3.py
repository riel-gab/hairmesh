# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Port of three.js r160 `Vector3` (node_modules/three/src/math/Vector3.js), only the methods the original uses.

Mutable, like the original: methods modify `self` and return it, `clone()` makes a copy. Every formula keeps the
exact order of operations of three.js so that results agree bit for bit with the JavaScript (see
docs/js-para-python.md, "three.js"). Method names are the original ones on purpose.
"""
import math

from .fdlibm import acos as _acos
from .js import clamp, js_or


class Vec3:
    __slots__ = ('x', 'y', 'z')

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z

    # ---- construction / copying ----
    def set(self, x, y, z):
        self.x = x; self.y = y; self.z = z
        return self

    def clone(self):
        return Vec3(self.x, self.y, self.z)

    def copy(self, v):
        self.x = v.x; self.y = v.y; self.z = v.z
        return self

    def toArray(self):
        return [self.x, self.y, self.z]

    def fromArray(self, a, offset=0):
        self.x = a[offset]; self.y = a[offset + 1]; self.z = a[offset + 2]
        return self

    def equals(self, v):
        return v.x == self.x and v.y == self.y and v.z == self.z

    # ---- arithmetic ----
    def add(self, v):
        self.x += v.x; self.y += v.y; self.z += v.z
        return self

    def addVectors(self, a, b):
        self.x = a.x + b.x; self.y = a.y + b.y; self.z = a.z + b.z
        return self

    def addScaledVector(self, v, s):
        self.x += v.x * s; self.y += v.y * s; self.z += v.z * s
        return self

    def sub(self, v):
        self.x -= v.x; self.y -= v.y; self.z -= v.z
        return self

    def subVectors(self, a, b):
        self.x = a.x - b.x; self.y = a.y - b.y; self.z = a.z - b.z
        return self

    def multiplyScalar(self, scalar):
        self.x *= scalar; self.y *= scalar; self.z *= scalar
        return self

    def divideScalar(self, scalar):
        # three.js: this.multiplyScalar(1 / scalar)  (the reciprocal, then a multiplication)
        if scalar == 0:
            inv = math.copysign(math.inf, scalar) if isinstance(scalar, float) else math.inf   # 1 / 0 in JS
        else:
            inv = 1 / scalar
        return self.multiplyScalar(inv)

    def negate(self):
        self.x = -self.x; self.y = -self.y; self.z = -self.z
        return self

    # ---- products ----
    def dot(self, v):
        return self.x * v.x + self.y * v.y + self.z * v.z

    def cross(self, v):
        return self.crossVectors(self, v)

    def crossVectors(self, a, b):
        ax, ay, az = a.x, a.y, a.z
        bx, by, bz = b.x, b.y, b.z
        self.x = ay * bz - az * by
        self.y = az * bx - ax * bz
        self.z = ax * by - ay * bx
        return self

    # ---- lengths ----
    def lengthSq(self):
        return self.x * self.x + self.y * self.y + self.z * self.z

    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalize(self):
        # three.js: this.divideScalar(this.length() || 1)
        return self.divideScalar(js_or(self.length(), 1))

    def setLength(self, length):
        return self.normalize().multiplyScalar(length)

    def distanceToSquared(self, v):
        dx = self.x - v.x; dy = self.y - v.y; dz = self.z - v.z
        return dx * dx + dy * dy + dz * dz

    def distanceTo(self, v):
        return math.sqrt(self.distanceToSquared(v))

    def angleTo(self, v):
        denominator = math.sqrt(self.lengthSq() * v.lengthSq())
        if denominator == 0:
            return math.pi / 2
        theta = self.dot(v) / denominator
        return _acos(clamp(theta, -1, 1))                           # Math.acos = fdlibm's, not the platform libm's

    # ---- interpolation ----
    def lerp(self, v, alpha):
        self.x += (v.x - self.x) * alpha
        self.y += (v.y - self.y) * alpha
        self.z += (v.z - self.z) * alpha
        return self

    def lerpVectors(self, v1, v2, alpha):
        self.x = v1.x + (v2.x - v1.x) * alpha
        self.y = v1.y + (v2.y - v1.y) * alpha
        self.z = v1.z + (v2.z - v1.z) * alpha
        return self

    def __repr__(self):
        return f'Vec3({self.x!r}, {self.y!r}, {self.z!r})'


def V3(x=0.0, y=0.0, z=0.0):
    """`new V3(x, y, z)` of the original (V3 = THREE.Vector3)."""
    return Vec3(x, y, z)
