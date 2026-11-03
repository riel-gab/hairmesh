# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""three.js r160 Matrix4 / Matrix3 / Quaternion, the parts the anchor gizmo uses (gizmoMatrix, 4592; the drag's
objectChange, 4660), in the same operation order, so a drag gives the same bits as the original.

Elements are column-major lists, as three's `elements`. Pure Python, no bpy."""
from math import sqrt
from sys import float_info

from . import fdlibm

EPSILON = float_info.epsilon                                        # Number.EPSILON (2 ** -52)


class Quat:
    # port of THREE.Quaternion (three/src/math/Quaternion.js)
    __slots__ = ('x', 'y', 'z', 'w')

    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x = x; self.y = y; self.z = z; self.w = w

    def clone(self):
        return Quat(self.x, self.y, self.z, self.w)

    def copy(self, q):
        self.x = q.x; self.y = q.y; self.z = q.z; self.w = q.w
        return self

    def toArray(self):
        return [self.x, self.y, self.z, self.w]

    def setFromAxisAngle(self, axis, angle):
        half = angle / 2; s = fdlibm.sin(half)
        self.x = axis.x * s; self.y = axis.y * s; self.z = axis.z * s; self.w = fdlibm.cos(half)
        return self

    def setFromUnitVectors(self, vFrom, vTo):
        # port of Quaternion.setFromUnitVectors (three/src/math/Quaternion.js r160; the tube's framesAlong, 6385):
        # assumes direction vectors vFrom and vTo are normalized. The zeros are float (+0): JS has no int zero
        r = vFrom.dot(vTo) + 1
        if r < EPSILON:
            # vFrom and vTo point in opposite directions
            r = 0.0
            if abs(vFrom.x) > abs(vFrom.z):
                self.x = -vFrom.y; self.y = vFrom.x; self.z = 0.0; self.w = r
            else:
                self.x = 0.0; self.y = -vFrom.z; self.z = vFrom.y; self.w = r
        else:
            # crossVectors( vFrom, vTo ); // inlined to avoid cyclic dependency on Vector3
            self.x = vFrom.y * vTo.z - vFrom.z * vTo.y
            self.y = vFrom.z * vTo.x - vFrom.x * vTo.z
            self.z = vFrom.x * vTo.y - vFrom.y * vTo.x
            self.w = r
        return self.normalize()

    def invert(self):                                               # conjugate (unit length assumed)
        self.x *= -1; self.y *= -1; self.z *= -1
        return self

    def length(self):
        return sqrt(self.x * self.x + self.y * self.y + self.z * self.z + self.w * self.w)   # Math.sqrt: correctly rounded both sides

    def normalize(self):
        l = self.length()
        if l == 0:
            self.x = 0; self.y = 0; self.z = 0; self.w = 1
        else:
            l = 1 / l
            self.x = self.x * l; self.y = self.y * l; self.z = self.z * l; self.w = self.w * l
        return self

    def multiply(self, q):
        return self.multiplyQuaternions(self, q)

    def premultiply(self, q):
        return self.multiplyQuaternions(q, self)

    def multiplyQuaternions(self, a, b):
        qax, qay, qaz, qaw = a.x, a.y, a.z, a.w
        qbx, qby, qbz, qbw = b.x, b.y, b.z, b.w
        self.x = qax * qbw + qaw * qbx + qay * qbz - qaz * qby
        self.y = qay * qbw + qaw * qby + qaz * qbx - qax * qbz
        self.z = qaz * qbw + qaw * qbz + qax * qby - qay * qbx
        self.w = qaw * qbw - qax * qbx - qay * qby - qaz * qbz
        return self


def applyQuaternion(v, q):
    # port of Vector3.applyQuaternion
    vx, vy, vz = v.x, v.y, v.z
    qx, qy, qz, qw = q.x, q.y, q.z, q.w
    tx = 2 * (qy * vz - qz * vy)
    ty = 2 * (qz * vx - qx * vz)
    tz = 2 * (qx * vy - qy * vx)
    v.x = vx + qw * tx + qy * tz - qz * ty
    v.y = vy + qw * ty + qz * tx - qx * tz
    v.z = vz + qw * tz + qx * ty - qy * tx
    return v


class Mat4:
    # port of THREE.Matrix4 (three/src/math/Matrix4.js)
    __slots__ = ('e',)

    def __init__(self, e=None):
        self.e = list(e) if e is not None else [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def set(self, n11, n12, n13, n14, n21, n22, n23, n24, n31, n32, n33, n34, n41, n42, n43, n44):
        te = self.e
        te[0] = n11; te[4] = n12; te[8] = n13; te[12] = n14
        te[1] = n21; te[5] = n22; te[9] = n23; te[13] = n24
        te[2] = n31; te[6] = n32; te[10] = n33; te[14] = n34
        te[3] = n41; te[7] = n42; te[11] = n43; te[15] = n44
        return self

    def clone(self):
        return Mat4(self.e)

    def copy(self, m):
        self.e[:] = m.e
        return self

    def makeTranslation(self, x, y, z):
        return self.set(1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1)

    def makeScale(self, x, y, z):
        return self.set(x, 0, 0, 0, 0, y, 0, 0, 0, 0, z, 0, 0, 0, 0, 1)

    def makeRotationFromQuaternion(self, q):
        # compose(_zero, q, _one)
        te = self.e
        x, y, z, w = q.x, q.y, q.z, q.w
        x2 = x + x; y2 = y + y; z2 = z + z
        xx = x * x2; xy = x * y2; xz = x * z2
        yy = y * y2; yz = y * z2; zz = z * z2
        wx = w * x2; wy = w * y2; wz = w * z2
        te[0] = (1 - (yy + zz)) * 1; te[1] = (xy + wz) * 1; te[2] = (xz - wy) * 1; te[3] = 0
        te[4] = (xy - wz) * 1; te[5] = (1 - (xx + zz)) * 1; te[6] = (yz + wx) * 1; te[7] = 0
        te[8] = (xz + wy) * 1; te[9] = (yz - wx) * 1; te[10] = (1 - (xx + yy)) * 1; te[11] = 0
        te[12] = 0; te[13] = 0; te[14] = 0; te[15] = 1
        return self

    def multiply(self, m):
        return self.multiplyMatrices(self, m)

    def premultiply(self, m):
        return self.multiplyMatrices(m, self)

    def multiplyMatrices(self, a, b):
        ae = a.e; be = b.e; te = self.e
        a11, a12, a13, a14 = ae[0], ae[4], ae[8], ae[12]
        a21, a22, a23, a24 = ae[1], ae[5], ae[9], ae[13]
        a31, a32, a33, a34 = ae[2], ae[6], ae[10], ae[14]
        a41, a42, a43, a44 = ae[3], ae[7], ae[11], ae[15]
        b11, b12, b13, b14 = be[0], be[4], be[8], be[12]
        b21, b22, b23, b24 = be[1], be[5], be[9], be[13]
        b31, b32, b33, b34 = be[2], be[6], be[10], be[14]
        b41, b42, b43, b44 = be[3], be[7], be[11], be[15]
        te[0] = a11 * b11 + a12 * b21 + a13 * b31 + a14 * b41
        te[4] = a11 * b12 + a12 * b22 + a13 * b32 + a14 * b42
        te[8] = a11 * b13 + a12 * b23 + a13 * b33 + a14 * b43
        te[12] = a11 * b14 + a12 * b24 + a13 * b34 + a14 * b44
        te[1] = a21 * b11 + a22 * b21 + a23 * b31 + a24 * b41
        te[5] = a21 * b12 + a22 * b22 + a23 * b32 + a24 * b42
        te[9] = a21 * b13 + a22 * b23 + a23 * b33 + a24 * b43
        te[13] = a21 * b14 + a22 * b24 + a23 * b34 + a24 * b44
        te[2] = a31 * b11 + a32 * b21 + a33 * b31 + a34 * b41
        te[6] = a31 * b12 + a32 * b22 + a33 * b32 + a34 * b42
        te[10] = a31 * b13 + a32 * b23 + a33 * b33 + a34 * b43
        te[14] = a31 * b14 + a32 * b24 + a33 * b34 + a34 * b44
        te[3] = a41 * b11 + a42 * b21 + a43 * b31 + a44 * b41
        te[7] = a41 * b12 + a42 * b22 + a43 * b32 + a44 * b42
        te[11] = a41 * b13 + a42 * b23 + a43 * b33 + a44 * b43
        te[15] = a41 * b14 + a42 * b24 + a43 * b34 + a44 * b44
        return self

    def determinant(self):
        te = self.e
        n11, n12, n13, n14 = te[0], te[4], te[8], te[12]
        n21, n22, n23, n24 = te[1], te[5], te[9], te[13]
        n31, n32, n33, n34 = te[2], te[6], te[10], te[14]
        n41, n42, n43, n44 = te[3], te[7], te[11], te[15]
        return (
            n41 * (+ n14 * n23 * n32 - n13 * n24 * n32 - n14 * n22 * n33 + n12 * n24 * n33 + n13 * n22 * n34 - n12 * n23 * n34)
            + n42 * (+ n11 * n23 * n34 - n11 * n24 * n33 + n14 * n21 * n33 - n13 * n21 * n34 + n13 * n24 * n31 - n14 * n23 * n31)
            + n43 * (+ n11 * n24 * n32 - n11 * n22 * n34 - n14 * n21 * n32 + n12 * n21 * n34 + n14 * n22 * n31 - n12 * n24 * n31)
            + n44 * (- n13 * n22 * n31 - n11 * n23 * n32 + n11 * n22 * n33 + n13 * n21 * n32 - n12 * n21 * n33 + n12 * n23 * n31))

    def invert(self):
        te = self.e
        n11, n21, n31, n41 = te[0], te[1], te[2], te[3]
        n12, n22, n32, n42 = te[4], te[5], te[6], te[7]
        n13, n23, n33, n43 = te[8], te[9], te[10], te[11]
        n14, n24, n34, n44 = te[12], te[13], te[14], te[15]
        t11 = n23 * n34 * n42 - n24 * n33 * n42 + n24 * n32 * n43 - n22 * n34 * n43 - n23 * n32 * n44 + n22 * n33 * n44
        t12 = n14 * n33 * n42 - n13 * n34 * n42 - n14 * n32 * n43 + n12 * n34 * n43 + n13 * n32 * n44 - n12 * n33 * n44
        t13 = n13 * n24 * n42 - n14 * n23 * n42 + n14 * n22 * n43 - n12 * n24 * n43 - n13 * n22 * n44 + n12 * n23 * n44
        t14 = n14 * n23 * n32 - n13 * n24 * n32 - n14 * n22 * n33 + n12 * n24 * n33 + n13 * n22 * n34 - n12 * n23 * n34
        det = n11 * t11 + n21 * t12 + n31 * t13 + n41 * t14
        if det == 0:
            return self.set(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        detInv = 1 / det
        te[0] = t11 * detInv
        te[1] = (n24 * n33 * n41 - n23 * n34 * n41 - n24 * n31 * n43 + n21 * n34 * n43 + n23 * n31 * n44 - n21 * n33 * n44) * detInv
        te[2] = (n22 * n34 * n41 - n24 * n32 * n41 + n24 * n31 * n42 - n21 * n34 * n42 - n22 * n31 * n44 + n21 * n32 * n44) * detInv
        te[3] = (n23 * n32 * n41 - n22 * n33 * n41 - n23 * n31 * n42 + n21 * n33 * n42 + n22 * n31 * n43 - n21 * n32 * n43) * detInv
        te[4] = t12 * detInv
        te[5] = (n13 * n34 * n41 - n14 * n33 * n41 + n14 * n31 * n43 - n11 * n34 * n43 - n13 * n31 * n44 + n11 * n33 * n44) * detInv
        te[6] = (n14 * n32 * n41 - n12 * n34 * n41 - n14 * n31 * n42 + n11 * n34 * n42 + n12 * n31 * n44 - n11 * n32 * n44) * detInv
        te[7] = (n12 * n33 * n41 - n13 * n32 * n41 + n13 * n31 * n42 - n11 * n33 * n42 - n12 * n31 * n43 + n11 * n32 * n43) * detInv
        te[8] = t13 * detInv
        te[9] = (n14 * n23 * n41 - n13 * n24 * n41 - n14 * n21 * n43 + n11 * n24 * n43 + n13 * n21 * n44 - n11 * n23 * n44) * detInv
        te[10] = (n12 * n24 * n41 - n14 * n22 * n41 + n14 * n21 * n42 - n11 * n24 * n42 - n12 * n21 * n44 + n11 * n22 * n44) * detInv
        te[11] = (n13 * n22 * n41 - n12 * n23 * n41 - n13 * n21 * n42 + n11 * n23 * n42 + n12 * n21 * n43 - n11 * n22 * n43) * detInv
        te[12] = t14 * detInv
        te[13] = (n13 * n24 * n31 - n14 * n23 * n31 + n14 * n21 * n33 - n11 * n24 * n33 - n13 * n21 * n34 + n11 * n23 * n34) * detInv
        te[14] = (n14 * n22 * n31 - n12 * n24 * n31 - n14 * n21 * n32 + n11 * n24 * n32 + n12 * n21 * n34 - n11 * n22 * n34) * detInv
        te[15] = (n12 * n23 * n31 - n13 * n22 * n31 + n13 * n21 * n32 - n11 * n23 * n32 - n12 * n21 * n33 + n11 * n22 * n33) * detInv
        return self


def mat3_from_mat4(m):
    """Matrix3.setFromMatrix4: the upper 3x3, column-major (the Matrix3's elements)"""
    me = m.e
    return [me[0], me[1], me[2], me[4], me[5], me[6], me[8], me[9], me[10]]


def applyMatrix3(v, e):
    # port of Vector3.applyMatrix3 (e = Matrix3 elements, column-major)
    x, y, z = v.x, v.y, v.z
    v.x = e[0] * x + e[3] * y + e[6] * z
    v.y = e[1] * x + e[4] * y + e[7] * z
    v.z = e[2] * x + e[5] * y + e[8] * z
    return v


def applyMatrix4(v, m):
    # port of Vector3.applyMatrix4
    x, y, z = v.x, v.y, v.z
    e = m.e
    w = 1 / (e[3] * x + e[7] * y + e[11] * z + e[15])
    v.x = (e[0] * x + e[4] * y + e[8] * z + e[12]) * w
    v.y = (e[1] * x + e[5] * y + e[9] * z + e[13]) * w
    v.z = (e[2] * x + e[6] * y + e[10] * z + e[14]) * w
    return v
