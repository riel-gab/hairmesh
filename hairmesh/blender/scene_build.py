# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Builds the Blender view of a project: a root Empty (carries the scale), a collection tree mirroring the groups,
one Curve object per curve, the head as a Mesh, and the project as Text datablocks (the source of truth, see
session.py).

Everything created here can be regenerated from the Text datablocks; phase 3 adds the way back (objects -> model)."""
import os

import bpy

from ..core.curves import curveHandles, rebuildCurve
from ..core.headobj import head_geometry
from ..core.names import unique_names
from . import naming, session
from .coords import to_blender, to_blender_xyz

TEXT_PREFIX = session.TEXT_PREFIX


def make_root(context, name, proj, *, scale=1.0):
    """The root of a project in the current scene: its collection, the Empty (scale, custom properties) and the Text
    datablocks holding the model. Returns (root empty, root collection). `name` taken by another project (or anything
    else) in the .blend: "name (2)" (blender/naming.py; never Blender's ".001")."""
    shown = name
    name = naming.root_name(name)
    txt, head_txt = session.store_project(name, proj)
    root_col = bpy.data.collections.new(f'HairMesh: {name}')
    context.scene.collection.children.link(root_col)
    root = bpy.data.objects.new(f'HairMesh {name}', None)
    root_col['hairmesh_root'] = root.name                           # the collection tree of this root Empty
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = proj.S.scale * 0.25 if proj.head else 1.0
    root.scale = (scale, scale, scale)
    root['hairmesh_project'] = shown                               # (the label, the Save As file name: the project's own name)
    root['hairmesh_data_name'] = name                              # (the datablocks' names: " (2)" when another project holds them, 3.8a)
    root['hairmesh_source'] = proj.name
    root['hairmesh_scale'] = scale
    root['hairmesh_model_scale'] = proj.S.scale
    session.bind(root, proj, txt, head_txt)
    root_col.objects.link(root)
    return root, root_col


def make_curve_object(proj, c, mode='BEZIER'):
    cu = bpy.data.curves.new(c.name, 'CURVE')
    cu.dimensions = '3D'
    cu.resolution_u = 24                                            # the original samples 24 points per segment
    if mode == 'BEZIER':
        # the core's handles become Blender's: hin = handle_left, anchor = co, hout = handle_right
        sp = cu.splines.new('BEZIER')
        sp.bezier_points.add(len(c.anchors) - 1)
        for i, (a, bp) in enumerate(zip(c.anchors, sp.bezier_points)):
            h = curveHandles(proj, c, i)
            bp.handle_left_type = 'FREE'
            bp.handle_right_type = 'FREE'
            bp.co = to_blender(a.pos)
            bp.handle_left = to_blender(h['hin'])
            bp.handle_right = to_blender(h['hout'])
        sp.use_cyclic_u = bool(c.closed)
    else:
        # the core's own sampling (rebuildCurve), as a polyline: what the parity tests compare
        if c.dirty:
            rebuildCurve(proj, c)
        pts = [s.p for s in c.samples]
        if c.closed and len(pts) > 1:
            pts = pts[:-1]                                          # the last sample is the first anchor again
        sp = cu.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        for p, pt in zip(pts, sp.points):
            pt.co = (*to_blender(p), 1.0)
        sp.use_cyclic_u = bool(c.closed)
    ob = bpy.data.objects.new(c.name, cu)
    ob['hairmesh_id'] = c.id
    ob['hairmesh_type'] = 'curve'
    ob['hairmesh_anchors'] = [a.id for a in c.anchors]
    if c.closed: ob['hairmesh_closed'] = True
    if c.ring is not None: ob['hairmesh_ring'] = c.ring
    if c.junc: ob['hairmesh_junc'] = True
    if c.conn: ob['hairmesh_conn'] = c.conn
    return ob


def make_head_object(proj):
    geo = head_geometry(proj.head)
    if not geo:
        return None
    v, faces = geo
    me = bpy.data.meshes.new(proj.head.name or 'head')
    verts = [to_blender_xyz(v[i], v[i + 1], v[i + 2]) for i in range(0, len(v), 3)]
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    ob = bpy.data.objects.new(proj.head.name or 'head', me)
    ob['hairmesh_id'] = proj.head.id
    ob['hairmesh_type'] = 'head'
    ob.hide_viewport = not proj.head.visible
    return ob


def build_project(context, proj, *, scale=1.0, curve_mode='BEZIER', import_head=True, curve_objects=True):
    """Creates the whole view of `proj` in the current scene and returns (root empty, stats dict). `curve_objects`:
    a Blender Curve object per hair curve (optional since the tool draws the curves itself)."""
    name = os.path.splitext(proj.name)[0] or 'project'
    root, root_col = make_root(context, name, proj, scale=scale)

    # the group tree, in the outliner's order
    cols = {None: root_col}
    stats = {'groups': 0, 'curves': 0, 'skipped_curves': 0, 'head': False}
    names = unique_names(proj, naming.held_by_others(root))         # (groups that share a name, or one held elsewhere: "X (2)", as sync_collections names them)

    def walk(ids):
        for iid in ids:
            it = proj.items.get(iid)
            if it is None or it.type != 'group':
                continue
            col = bpy.data.collections.new(names[it.id])
            cols[it.parent].children.link(col)
            col['hairmesh_id'] = it.id
            col.hide_viewport = not it.visible
            col.hide_render = not it.visible
            cols[it.id] = col
            stats['groups'] += 1
            walk(it.children)
    walk(proj.roots)

    for c in proj.curves() if curve_objects else ():
        if len(c.anchors) < 2:                                      # the original draws nothing for these either
            stats['skipped_curves'] += 1
            continue
        ob = make_curve_object(proj, c, curve_mode)
        ob.parent = root
        ob.hide_viewport = not c.visible
        ob.hide_render = not c.visible
        cols.get(c.parent, root_col).objects.link(ob)
        stats['curves'] += 1

    if import_head and proj.head is not None:
        head = make_head_object(proj)
        if head is not None:
            head.parent = root
            root_col.objects.link(head)
            root['hairmesh_head'] = head.name
            stats['head'] = True
    return root, stats
