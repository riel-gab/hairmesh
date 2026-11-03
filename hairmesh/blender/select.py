# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The selection state on the Blender side: the core's Selection (ids of curves and anchors, hairmesh.core.selection)
kept as JSON in Scene.hairmesh (sel_curves, sel_anchors), so it is saved with the .blend and takes part in undo. Every
operator loads it, changes it through the Selection's rules and stores it back; the draw handler and the panels read
it. Anchors and curves are looked up in the project of the scene's root."""
import json

from ..core.selection import Selection
from . import session


def load(hm):
    """Scene.hairmesh -> Selection (ids as stored; prune(P) drops what the model no longer has)."""
    try:
        items = json.loads(hm.sel_curves or '[]'); anchors = json.loads(hm.sel_anchors or '[]')
    except ValueError:
        items, anchors = [], []
    return Selection([int(i) for i in items], [int(a) for a in anchors])


def store(hm, sel):
    hm.sel_curves = json.dumps(list(sel.items)); hm.sel_anchors = json.dumps(list(sel.anchors))


def current(context):
    """(root, project, selection pruned to the model), or (None, None, an empty Selection) without a project."""
    root = session.find_root(context)
    if root is None:
        return None, None, Selection()
    try:
        P = session.get_project(root)
    except Exception:
        return None, None, Selection()
    return root, P, load(context.scene.hairmesh).prune(P)


def anchors_of(P, sel):
    """The selected anchors as Anchor objects, in selection order (the last is the one the panel shows)."""
    return [P.anchors[i] for i in sel.anchors if i in P.anchors]


def has_any(hm):
    """anything of the HairMesh selected: anchors or items (curves, walls, groups…) — what G / R / S and the gizmo
    act on (3.8d)"""
    return (hm.sel_anchors or '[]') != '[]' or (hm.sel_curves or '[]') != '[]'


def gizmo_targets(P, sel):
    """edit.gizmoTargets (4563) of the selection: the anchors, else everything inside the items; None: nothing moves"""
    from ..core import edit
    return edit.gizmoTargets(P, sel.items, anchors_of(P, sel))


def curves_of(P, sel):
    """The selected curves (selectedCurves, 7843), in selection order."""
    out = []
    for i in sel.items:
        it = P.items.get(i)
        if it is not None and it.type == 'curve':
            out.append(it)
    return out


def walls_of(P, sel):
    """The selected walls (surface items of S.sel), in selection order (3.5)."""
    out = []
    for i in sel.items:
        it = P.items.get(i)
        if it is not None and it.type == 'surface':
            out.append(it)
    return out


def last_item(P, sel):
    """The last selected item, the one the original's properties panel shows (renderProps, 8627), or None."""
    if not sel.items:
        return None
    return P.items.get(sel.items[len(sel.items) - 1])


def last_wall(P, sel):
    it = last_item(P, sel)
    return it if it is not None and it.type == 'surface' else None


def selected_group(P, sel):
    """The selected group (the last selected item when it is a group: renderProps 8645), or None (U1, 15/10/2026)."""
    from ..core import groups as G
    return G.selectedGroup(P, sel.items)


def target_group(P, sel):
    """targetGroup (4729): the id of the group of the last selected item (itself for a group); None at the root."""
    from ..core import groups as G
    return G.targetGroup(P, sel.items)


def redraw(context):
    screen = context.screen
    for area in screen.areas if screen is not None else ():
        if area.type == 'VIEW_3D':
            area.tag_redraw()
