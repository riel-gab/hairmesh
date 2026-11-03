# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The only place where coordinates change system: the core works in the original's space (three.js, Y up, head
units); Blender is Z up. (x, y, z)_three -> (x, -z, y)_blender. Scale is applied by the root Empty, never to data."""
import math
import struct


def to_blender(v):
    """Vec3 (or anything with x, y, z) in the original space -> Blender tuple."""
    return (v.x, -v.z, v.y)


def to_blender_xyz(x, y, z):
    return (x, -z, y)


def from_blender(x, y, z):
    """Blender tuple -> original space (for phase 4, editing)."""
    return (x, z, -y)


def f32(v):
    """A value that went through a Blender FloatProperty (single precision) back to the shortest decimal that
    reads as the same float32: the slider's 0.3 arrives as 0.30000001192092896 and would be stored like that in the
    project; the original's sliders (double) store 0.3. Seven significant digits identify every float32."""
    return float(f'{v:.7g}')


def field_text(v):
    """the text Blender's number field shows when it is edited (ui_but_string_get_ex with UI_PRECISION_FLOAT_MAX and
    the exponent form): six digits in all for a float32 from 1 up, six decimals below (1234.568 reads 1234.57)"""
    x = struct.unpack('f', struct.pack('f', v))[0]
    digits = math.floor(math.log10(abs(x))) + 1 if x else 0
    if digits < -6 or digits > 12:
        return f'{x:.6g}'
    return f'{x:.{min(max(6 - digits, 0), 6)}f}'


def field_same(stored, v):
    """True when `v` (through f32) is what a field showing `stored` gives back when confirmed untouched: Enter on a
    number that is not all in those six digits comes back rounded, and a field nobody changed writes nothing"""
    if stored == v:
        return True
    if isinstance(stored, bool) or isinstance(v, bool) or not isinstance(stored, (int, float)) or not isinstance(v, float):
        return False
    return f32(float(field_text(stored))) == f32(v)


def auto_scale(head_height):
    """The power of ten that puts the head's bounding-box height into [0.1 m, 1 m).

    539.6 (ZBrush-like units) -> 0.001; 25 (cm) -> 0.01; 0.25 (already meters) -> 1; 1800 (mm) -> 0.0001.
    """
    if not head_height or head_height <= 0 or math.isinf(head_height) or math.isnan(head_height):
        return 1.0
    return 10.0 ** -(math.floor(math.log10(head_height)) + 1)
