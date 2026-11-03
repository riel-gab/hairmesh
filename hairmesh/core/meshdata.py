# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""What a mesh may hold before it goes to Blender (pure, no bpy): Mesh.from_pydata takes the data as it is, and a
face index out of range, a face with a repeated vertex or a non-finite coordinate are the classic ways to make Blender
write where it should not, or to leave Mesh.validate() dropping faces after the per-face attributes were set. A grid
of a degenerate wall (two curves tip to tip, the sides of a triangle, a zero-width row) is still a valid grid in the
original and in the port, but the preview must survive whatever surfaceData returns: clean_mesh keeps only the faces
Blender can take and says which they were, so a per-face attribute follows."""
import itertools
import math


def _finite(p):
    return math.isfinite(p[0]) and math.isfinite(p[1]) and math.isfinite(p[2])


def clean_mesh(pos, faces):
    """pos: [(x, y, z)], faces: [[i, j, k, ...]] -> (pos, faces, kept): the positions with every non-finite coordinate
    zeroed (a loose vertex Blender's validate would zero anyway), the faces that reference only finite vertices, in
    range, with three or more DISTINCT indices (duplicates within a face removed, as mergeWeld collapses a quad to a
    triangle), and `kept`, the index of each surviving face in the input. Faces are copied, never aliased."""
    n = len(pos)
    bad = set()
    try:                                                            # (the usual case at once: every coordinate finite, lot 3.7-estab)
        fine = all(map(math.isfinite, itertools.chain.from_iterable(pos)))
    except TypeError:
        fine = False
    if fine:
        out_pos = [(p[0], p[1], p[2]) for p in pos]
    else:
        out_pos = []
        for i, p in enumerate(pos):
            if _finite(p):
                out_pos.append((p[0], p[1], p[2]))
            else:
                bad.add(i); out_pos.append((0.0, 0.0, 0.0))
    out_faces = []; kept = []
    for fi, f in enumerate(faces):
        if len(f) >= 3 and all(isinstance(v, int) for v in f) and len(set(f)) == len(f) and min(f) >= 0 and max(f) < n and (not bad or bad.isdisjoint(f)):
            out_faces.append(list(f)); kept.append(fi)                 # (a sound face: as the loop below would leave it)
            continue
        g = []
        ok = True
        for v in f:
            if not isinstance(v, int) or v < 0 or v >= n or v in bad:
                ok = False; break
            if v not in g:
                g.append(v)
        if ok and len(g) >= 3:
            out_faces.append(g); kept.append(fi)
    return out_pos, out_faces, kept


def mesh_problems(pos, faces):
    """A short list of what clean_mesh would change (for tests and logs): 'non-finite vertex i', 'face i: ...'."""
    out = []
    n = len(pos)
    for i, p in enumerate(pos):
        if not _finite(p):
            out.append(f'non-finite vertex {i}')
    for fi, f in enumerate(faces):
        if any(not isinstance(v, int) or v < 0 or v >= n for v in f):
            out.append(f'face {fi}: index out of range')
        elif len(set(f)) < 3:
            out.append(f'face {fi}: fewer than 3 distinct vertices')
        elif len(set(f)) < len(f):
            out.append(f'face {fi}: repeated vertex')
    return out
