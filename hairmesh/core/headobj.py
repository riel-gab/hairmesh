# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The reference head: the OBJ parser of the original and the scene scale it derives from the head's box."""
import re
import json
import struct

from .js import js_or
from .model import SceneState
from .vec3 import Vec3

_WS = re.compile(r'\s+')


def parseOBJ(text):
    # port of parseOBJ (hair-modeler.clean.html:3169). Returns (v: flat [x, y, z, ...], faces: [[i, j, k, ...]]).
    v, faces = [], []
    for line in text.split('\n'):
        t = line.strip()
        if t.startswith('v '):
            p = _WS.split(t)
            v.append(float(p[1])); v.append(float(p[2])); v.append(float(p[3]))
        elif t.startswith('f '):
            nv = len(v) // 3
            f = []
            for s in _WS.split(t)[1:]:
                i = int(s.split('/')[0])
                f.append(nv + i if i < 0 else i - 1)
            if len(f) >= 3:
                faces.append(f)
    return v, faces


def obj_sets(text):
    """the texture sets of an OBJ head, one per material slot in the order its faces first use them (headFromOBJ,
    hair-modeler.clean.html:3660-3681: bySlot keyed by each face's material; 'Default' for faces before any usemtl):
    [{'name'}]. Only what normLook reads (the names)."""
    mats, slots, cur = [], [], -1
    for line in text.split('\n'):
        t = line.strip()
        if t.startswith('usemtl '):
            n = t[7:].strip()
            if n not in mats:
                mats.append(n)
            cur = mats.index(n)
        elif t.startswith('f '):
            if len(_WS.split(t)[1:]) >= 3 and cur not in slots:   # (parseOBJ keeps faces of 3+ corners)
                slots.append(cur)
    return [{'name': 'Default' if m < 0 else mats[m]} for m in slots]


TEX_SLOTS = ('map', 'alphaMap')                                     # (TEX_SLOTS, 4796)


def normLook(look, sets):
    """port of normLook (hair-modeler.clean.html:4802): a look fitted to the texture sets of its mesh — a set of the
    old look kept by its place (same name, or the same number of sets) or else by its name"""
    L = look or {}
    old = L.get('sets') or []

    def one(i, st):
        o = (old[i] if i < len(old) and (old[i].get('name') == st['name'] or len(old) == len(sets)) else None) \
            or next((x for x in old if x.get('name') == st['name']), None) or {}
        maps = {}
        for k in TEX_SLOTS:
            if o.get('maps') and k in o['maps']:
                maps[k] = o['maps'][k]
        r = {'name': st['name'], 'on': o.get('on') is not False}
        if o.get('color'):
            r['color'] = o['color']
        if o.get('alpha') is not None:
            r['alpha'] = bool(o['alpha'])
        r['maps'] = maps
        return r
    return {'tex': bool(L.get('tex')), 'flat': bool(L.get('flat')), 'sets': [one(i, st) for i, st in enumerate(sets)]}


def head_look(look, src):
    """the look a head gets when it is made (makeHeadItem 3800: normLook(look, head.sets)): an OBJ's sets are known
    here; another format's are not (no GLB / FBX reader): its stored look is kept as read"""
    if src and src.get('ext') == 'obj' and src.get('text') is not None:
        return normLook(look, obj_sets(src['text']))
    return json.loads(json.dumps(look)) if look else None


def float32(x):
    """x rounded to the nearest float32, as a Python float (what a Float32BufferAttribute stores)."""
    return struct.unpack('<f', struct.pack('<f', x))[0]


def fit_to_box(v):
    """port of fitToBox (hair-modeler.clean.html:4184), the geometric part: S.scale, S.headH, S.yRef, S.center.

    The box comes from Box3.setFromObject(head.obj): three.js reads the positions from a Float32BufferAttribute,
    so every coordinate is rounded to float32 first. getSize is max - min, getCenter is (min + max) * 0.5.
    """
    if len(v) < 3:
        # (a head without vertices: an empty box's getCenter is (0, 0, 0), not the start-up frame's; the original's yRef
        # would be box.max.y = -Infinity there, kept at 1 here as before 4.1 — a case no real head reaches)
        return SceneState(center=Vec3())
    xs = [float32(x) for x in v[0::3]]
    ys = [float32(y) for y in v[1::3]]
    zs = [float32(z) for z in v[2::3]]
    return box_frame((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def box_frame(mn, mx):
    """The geometric part of fitToBox (hair-modeler.clean.html:6288 in 1.0.1) for a non-empty box (min, max)."""
    size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
    S = SceneState()
    S.scale = js_or(max(size[0], size[1], size[2]), 1)              # Math.max(size.x, size.y, size.z) || 1
    S.headH = js_or(size[1], S.scale)                               # size.y || S.scale
    S.yRef = mx[1]                                                  # box.max.y
    S.center = Vec3(mn[0] + mx[0], mn[1] + mx[1], mn[2] + mx[2]).multiplyScalar(0.5)   # box.getCenter
    return S


def head_geometry(head):
    """(v, faces) of a Head whose src is an OBJ; None for other formats (GLB/GLTF heads are not ported yet)."""
    if not head or not head.src or head.src.get('ext') != 'obj' or not head.src.get('text'):
        return None
    return parseOBJ(head.src['text'])


def obj_text(v, faces):
    """The inverse of parseOBJ: `v x y z` with the shortest round-trip form of each double (what JavaScript's `+str`
    and Python's float() both read back exactly) and 1-based `f` lines. `v` is flat [x, y, z, ...]."""
    out = []
    for i in range(0, len(v), 3):
        out.append(f'v {v[i]!r} {v[i + 1]!r} {v[i + 2]!r}')
    for f in faces:
        out.append('f ' + ' '.join(str(i + 1) for i in f))
    return '\n'.join(out) + '\n'
