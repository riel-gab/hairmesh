# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Ours (3.7b): the names the Blender side gives what it makes for the groups and the converted meshes — a collection
per group, a "<group> live" object per group with walls, a "<group> mesh" object per mesh item.

Blender's names are unique per kind of datablock; the model's are not: two groups may share a name, as in the
original (a migrated file brings its groups' names along: the example migrated into the example has every group
twice). Left to Blender, the second would come out "Bang_01.001", and which one got the suffix would depend on the
order the objects were made in. Here the first group with a name keeps it and the next ones get " (2)", " (3)"… in the
model's order (stable: a group keeps its place in the items), skipping any name another group holds as its own; a
converted mesh takes its group's name ("Bang_01 (2) mesh"), so it follows the group and never another mesh's place.
The model is not renamed (the project file stays the original's).

Unique in the whole .blend (3.8a, item 1 of the manual test of 3.7b): `held(kind, name)` says whether a datablock that
is NOT this project's (another project, in this scene or another, or the user's) holds a name — kind 'group' for a
group's Blender name (its collection "X", its objects "X live" / "X mesh" and their data), 'mesh' for a converted
mesh's own name. Such a name is skipped the same way: " (2)", " (3)"… — never Blender's ".001", which also eats a
name's own ".6" ("manual-3.6" taken came out "manual-3.001"). The predicate comes from blender/naming.py."""


def _never(kind, name):
    return False


def _groups(P, held):
    its = [it for it in P.items.values() if it.type == 'group']
    own = {it.name for it in its}
    used = set(); out = {}
    for it in its:
        n = it.name
        if n in used or held('group', n):
            i = 2
            while f'{it.name} ({i})' in used or f'{it.name} ({i})' in own or held('group', f'{it.name} ({i})'):
                i += 1
            n = f'{it.name} ({i})'
        used.add(n)
        out[it.id] = n
    return out


def unique_names(P, held=None):
    """{item id: name} for the groups and the mesh items of P. A mesh named after its group ("<group> mesh", what
    Convert names it) takes its group's unique name; a mesh and the confirmed one it replaces share theirs (the replaced
    one's object adds " (replaced)", blender/meshes.object_name); a mesh whose name no longer follows its group (the
    group renamed after Convert) keeps its own, with " (k)" when meshes of different groups share it (by the groups'
    order). `held`: see the module's docstring (None: nothing outside the project counts)."""
    held = held or _never
    out = _groups(P, held)
    order = {iid: i for i, iid in enumerate(P.items)}
    meshes = [it for it in P.items.values() if it.type == 'mesh']
    base = {}
    for m in meshes:
        g = P.items.get(m.parent) if m.parent is not None else None
        base[m.id] = f'{out[g.id]} mesh' if g is not None and g.type == 'group' and m.name == f'{g.name} mesh' else m.name
    by = {}
    for m in meshes:
        by.setdefault(base[m.id], []).append(m)
    taken = set(base.values())
    for b, ms in by.items():
        parents = sorted({m.parent for m in ms}, key=lambda p: order.get(p, -1))
        first = 0 if held('mesh', b) else 1                         # (a name another datablock holds: the first group renumbered too;
        i = 2                                                       #  one named after its group is free already, through 'group')
        for p in parents[first:]:
            while f'{b} ({i})' in taken or held('mesh', f'{b} ({i})'):
                i += 1
            n = f'{b} ({i})'; taken.add(n)
            for m in ms:
                if m.parent == p:
                    base[m.id] = n
    # two meshes of one group with the same name (ours, 3.8b: the same mesh duplicated twice, Shift+D, gives two
    # "<name> copy" side by side): the later ones " (2)", " (3)"... — a pending mesh and the confirmed one it replaces
    # keep sharing theirs (object_name adds " (replaced)")
    pending = set((P.conv or {}).get('entries') or ())
    replaced = set((P.conv or {}).get('replaced') or ())
    seen = {}; seen_rep = {}
    for m in meshes:
        if m.id in pending:
            continue
        k = (m.parent, base[m.id])
        book = seen_rep if m.id in replaced else seen               # (the first replaced one of a name shares the pending's; more replaced ones of that name are numbered)
        if k not in book:
            book[k] = m.id
            continue
        i = 2
        while f'{base[m.id]} ({i})' in taken or held('mesh', f'{base[m.id]} ({i})'):
            i += 1
        base[m.id] = f'{base[m.id]} ({i})'; taken.add(base[m.id])
    out.update(base)
    return out


def group_name(P, gid, names=None):
    """the Blender name of group `gid` ("root" for the root level, None)"""
    if gid is None or gid not in P.items:
        return 'root'
    return (names if names is not None else unique_names(P)).get(gid, P.items[gid].name)


def free_name(name, held):
    """`name`, or "name (2)", "name (3)"… the first one `held(name)` says is free (a project's root collection and
    Empty, which no group names)"""
    if not held(name):
        return name
    i = 2
    while held(f'{name} ({i})'):
        i += 1
    return f'{name} ({i})'
