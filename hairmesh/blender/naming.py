# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Ours (3.8a, item 1 of the manual test of 3.7b): the names of the .blend a project may not take, because a datablock
that is not this project's holds them — another project (in this scene or in another one) or something of the
user's. core/names.unique_names skips them the way it skips a name two groups share: " (2)", " (3)"…

Left to Blender, a name taken elsewhere comes out ".001", and Blender reads a name's own ".6" as such a suffix:
"manual-3.6" taken gave "manual-3.001". Names are unique per kind of datablock in the whole file, so the check looks a
name up in all of bpy.data (collections, objects, meshes) — one lookup per name asked, never a walk over the file: the
project's own datablocks are the few under its root. A mesh nobody uses (left by Blender's delete until the next
purge) holds no name."""
import bpy

from ..core.names import free_name


def _root_collection(root):
    return next((c for c in bpy.data.collections if c.get('hairmesh_root') == root.name), None)


def held_by_others(root):
    """held(kind, name) for core/names.unique_names: kind 'group' — the group's collection "X", its objects "X live" /
    "X mesh" or their data; kind 'mesh' — a converted mesh's own name (object or data)"""
    cols = set(); obs = set(); data = set()
    if root is not None:
        rc = _root_collection(root)
        stack = [rc] if rc is not None else []
        while stack:
            c = stack.pop()
            if c is rc or c.get('hairmesh_id') is not None:
                cols.add(c.name)
            stack.extend(c.children)
        for o in root.children:                                     # (the live and mesh objects are parented to the root)
            if o.get('hairmesh_type') in ('live', 'mesh'):
                obs.add(o.name)
                if o.data is not None:
                    data.add(o.data.name)

    def ours(o):
        return o.name in obs or (root is not None and o.get('hairmesh_type') in ('live', 'mesh') and o.get('hairmesh_root') == root.name)   # (or ours without its parent: Alt+P)

    def ob_held(n):
        o = bpy.data.objects.get(n)
        return o is not None and not ours(o)

    def data_held(n):
        me = bpy.data.meshes.get(n)
        if me is None or n in data or me.users == 0:
            return False
        o = bpy.data.objects.get(n)
        return not (o is not None and ours(o) and o.data == me)     # (the data of our object without its parent: rename_data keeps their names equal)

    def held(kind, name):
        if kind == 'group':
            c = bpy.data.collections.get(name)
            if c is not None and name not in cols:
                return True
            for n in (f'{name} live', f'{name} mesh'):
                if ob_held(n) or data_held(n):
                    return True
            return False
        return ob_held(name) or data_held(name)
    return held


def root_name(name):
    """the name a new project's root takes for its datablocks: `name`, or "name (2)"… when another project (or anything
    else) holds its collection "HairMesh: X", its Empty "HairMesh X" or its texts "hairmesh/X.json" """
    from .session import project_text_name
    return free_name(name, lambda n: f'HairMesh: {n}' in bpy.data.collections or f'HairMesh {n}' in bpy.data.objects
                     or project_text_name(n) in bpy.data.texts)


def rename_data(ob, name):
    """the data of one of our objects follows its name (the next "X live" data not ".001"), when nobody else uses it and
    the name is free"""
    me = ob.data
    if me is not None and me.users == 1 and me.name != name and bpy.data.meshes.get(name) is None:
        me.name = name
