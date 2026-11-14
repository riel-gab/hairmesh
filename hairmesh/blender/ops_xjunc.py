# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""X junctions inside Blender (lot 1.1f): the anchor pie's "Separate Zones at This X" and the group sheet's "Separate X
Junctions (N)" (core/xjunc.py: separateAtX, separateGroupX — the walls of a lock cut across at an X, the curves kept
whole, the new walls on edge curves that follow them).

  hairmesh.separate_x (anchor)          the zones separated at the last selected anchor (or the one given), when it is
                                        an X with walls across it (xjunc.canSeparateAt: the pie shows the item only then)
  hairmesh.separate_group_x (group)     every X of the selected group (or the one given) with walls across it

Both have a form that needs no viewport (ids as properties, else the selection), end with session.save_project (one
undo step) and live.update (a pending conversion follows there, by signature: meshes.follow_conversion). The menus ask
x_count / separate_offered, kept per model state (Project.gen and Project.rev: an X is anchors shared by curves — gen —
and the cross plane reads their places — rev, which every drag step bumps), so a sheet drawn at every redraw never
counts again (xAnchors over the example: about 0.1 s)."""
import bpy
from bpy.props import IntProperty

from ..core import scene as SC, xjunc as XJ
from ..core.selection import Selection
from . import live, owner, select, session, slow, wording

ICON = 'MOD_EXPLODE'                                               # (G8: one icon per concept, used by nothing else — the lock cut across at the X)


def x_count(P, g):
    """xCount (11039): the X anchors of the group with walls across them, kept per model state and group — the sheet
    draws it often. The group sheet shows Separate X Junctions (N) when this is 1 or more (11040)."""
    key = (P.gen, P.rev, g.id)
    c = P._memo.get('x_count')
    if c is None or c[0] != key:
        c = (key, XJ.xCount(P, g)); P._memo['x_count'] = c
    return c[1]


def separate_offered(P, a):
    """the anchor pie's condition (10124: an X of the curves through it, a wall across it), kept per model state and
    anchor — a pie redraws while it is open"""
    key = (P.gen, P.rev, a.id)
    c = P._memo.get('separate_x')
    if c is None or c[0] != key:
        c = (key, XJ.canSeparateAt(P, a)); P._memo['separate_x'] = c
    return c[1]


def hair_group(g):
    """the group panel shows the button for a group of hair only (11033: no Guides, no Meshes group)"""
    return not g.guidesGroup and not g.meshesGroup


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def _finish(context, root, P, sel, report, msg):
    select.store(context.scene.hairmesh, sel)
    session.save_project(root, P)                                   # the original's commit(): one undo step
    live.touch(root, [c.id for c in P.curves()])                    # (every curve dirty, S.surfDirty: 6883)
    live.update(context, root)                                      # the preview (and a pending conversion) inside the same undo step
    select.redraw(context)
    if msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


class HAIRMESH_OT_separate_x(bpy.types.Operator):
    """Cut the lock across at this X: the walls above and below it become separate locks, joined only at the cut. The curves stay whole; the new walls run on edge curves that follow them"""
    bl_idname = 'hairmesh.separate_x'
    bl_label = 'Separate Zones at This X'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', description='The X anchor (default: the last selected anchor)', default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        if P is None:
            return {'CANCELLED'}
        a = P.anchors.get(self.anchor) if self.anchor >= 0 else (P.anchors.get(sel.last_anchor) if sel.anchors else None)
        if a is None:
            self.report({'WARNING'}, 'Select an anchor, or pass its id')
            return {'CANCELLED'}
        SC.settle(P)                                                # (the frame before the click: the edge curves follow, the curves sampled)
        with slow.part('separate the zones at the X'):
            _ok, msg, _made = XJ.separateAtX(P, a)
        if msg == XJ.NO_X:                                          # (6831: nothing changed)
            self.report({'INFO'}, wording.core(msg))
            return {'CANCELLED'}
        # (6884: S.asel = [] and a commit even when no wall could be made — the curves may have got their cut anchors)
        return _finish(context, root, P, Selection([i for i in sel.items if i in P.items], []), self.report, wording.core(msg))


class HAIRMESH_OT_separate_group_x(bpy.types.Operator):
    """Cut the locks of this group across at every X junction (where curves cross or welded strands swap partners): the walls above and below become separate locks (their loops are built independently), joined only at the cut. The curves stay whole; the new walls run on edge curves that follow them"""
    bl_idname = 'hairmesh.separate_group_x'
    bl_label = 'Separate X Junctions'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', description='The group (default: the selected group)', default=-1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, P, sel = select.current(context)
        g = select.selected_group(P, sel) if P is not None else None
        if g is None or not hair_group(g) or x_count(P, g) < 1:
            cls.poll_message_set('Select a group whose locks have an X junction with walls across it')
            return False
        return True

    def execute(self, context):
        root, P, sel = select.current(context)
        if P is None:
            return {'CANCELLED'}
        g = P.items.get(self.group) if self.group >= 0 else select.selected_group(P, sel)
        if g is None or g.type != 'group':
            self.report({'WARNING'}, 'Select a group, or pass its id')
            return {'CANCELLED'}
        if not hair_group(g):                                       # (the sheet's button only for a group of hair, 11033: the id form too)
            self.report({'WARNING'}, 'Separate X Junctions works on a group of hair, not on the Guides or the Meshes group')
            return {'CANCELLED'}
        SC.settle(P)
        gen = P.gen
        with slow.part('separate the X junctions of the group'):
            k, msg = XJ.separateGroupX(P, g)
        if not k and P.gen == gen:                                  # (6895: no commit without an X separated — nothing changed)
            select.store(context.scene.hairmesh, Selection([i for i in sel.items if i in P.items], []))
            select.redraw(context)
            self.report({'INFO'}, wording.core(msg))
            return {'CANCELLED'}
        # (k == 0 with the model changed — cut anchors put in, no wall made: saved all the same, so the scene holds what
        # the core did; the original keeps it in memory for its next commit)
        return _finish(context, root, P, Selection([i for i in sel.items if i in P.items], []), self.report, wording.core(msg))


def separate_item(P, a, short=False):
    """the anchor pie's item (its SW submenu) for the last selected anchor, or None when it does not apply (G4)"""
    if a is None or not separate_offered(P, a):
        return None
    return ('op', 'hairmesh.separate_x', 'Separate X' if short else 'Separate Zones at This X', ICON, {'anchor': a.id})


def group_item(P, g):
    """the group sheet's button and the Group menu's item, with the count; None when it does not apply (11040: hidden)"""
    if g is None or not hair_group(g):
        return None
    n = x_count(P, g)
    return ('op', 'hairmesh.separate_group_x', f'Separate X Junctions ({n})', ICON, {}) if n >= 1 else None


CLASSES = (HAIRMESH_OT_separate_x, HAIRMESH_OT_separate_group_x)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
