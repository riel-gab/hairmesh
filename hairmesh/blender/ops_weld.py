# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Phase 3.4 inside Blender: welding and the anchor actions of the original's pie (hair-modeler.clean.html:4843-5107,
5487-5625, openMainPie 7891-7902) as operators, the pie that checkWeld opens when a dragged anchor is released near
another one, and the menus every action lives in (rule of 14/10/2026: everything reachable from pies and the
HairMesh Menu; the N panel is a complement).

  hairmesh.weld (a, b, mode)            the pie after a drag (HAIRMESH_MT_weld_pie, `pending`): "Weld anchors into one";
                                        or, the start of one curve on the end of another, "Reverse X, then weld" /
                                        "Join into one curve"; "Don't weld" is hairmesh.weld_dismiss
  hairmesh.unweld_anchor (anchor, curve) Ctrl + click on a welded anchor of a selected curve (ops_select), or the
                                        "Unweld from" submenu of the anchor menus (HAIRMESH_MT_unweld)
  hairmesh.split_at_anchor              "Split curve at the anchor"
  hairmesh.curve_between                "Create curve between the anchors" (exactly 2 selected)
  hairmesh.curve_through                "Create curve through the anchors (n, in pick order)"
  hairmesh.rotate_junction (anchor, choice)   "Rotate junction" submenu (HAIRMESH_MT_junction): a curve passes
                                        smoothly, or Automatic
  hairmesh.optimize_anchors (only)      "Optimize anchors — keep the shape" (the selected curves) and, with `only`,
                                        the 1.0.1 "Optimize the selected anchors — keep the shape"
  hairmesh.merge_coincident             "Merge anchors at identical places" (2+ selected curves; the curve pie)

Every operator has a form that needs no viewport (ids as properties, else the selection), ends with
session.save_project (one undo step) and live.update. Move / Rotate / Scale of the original's pie are the gizmos
of D11 (step 3.8); "Detach from the connector" is the crown's (phase 4)."""
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from ..core import curves as CM, edit, weld as W
from ..core.js import index_of
from ..core.selection import Selection
from . import live, owner, select, session
from .ops_draw import head_visible, target_group

pending = None                                                      # the weld a released drag asks about: {'a', 'b', 'choice', 'title'} (S.weldCand + the open pie)


def _finish(context, root, P, sel=None, report=None, msg=None):
    if sel is not None:
        select.store(context.scene.hairmesh, sel)
    session.save_project(root, P)                                   # the original's commit(): one undo step
    live.update(context, root)                                      # the preview inside the operator's own undo step
    select.redraw(context)
    if report is not None and msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def _anchor(P, sel, aid):
    """the anchor given by id, else the last selected one"""
    if aid >= 0:
        return P.anchors.get(aid)
    return P.anchors.get(sel.last_anchor) if sel.anchors else None


# ---------------- the weld pie (checkWeld, 4876) ----------------

def ask_weld(a, b, P):
    """What the release of a drag found: fills `pending` for the pie. Returns the pie's title."""
    global pending
    choice = W.weld_choice(P, a, b)
    title = 'Start meets end' if choice[0] == 'startEnd' else 'Anchors are close'
    pending = {'a': a.id, 'b': b.id, 'choice': choice[0], 'st': choice[1].id if choice[0] == 'startEnd' else None,
               'st_name': choice[1].name if choice[0] == 'startEnd' else None, 'title': title}
    return title


def weld_pie_items():
    """the slots of the weld pie for `pending` (pure): [W, E, S, N]"""
    p = pending
    if p is None:
        return [('label', 'No weld pending', 'INFO')]
    dismiss = ('op', 'hairmesh.weld_dismiss', "Don't Weld", 'X', {})
    if p['choice'] == 'startEnd':
        return [dismiss, ('op', 'hairmesh.weld', f'Reverse {p["st_name"]}, then Weld', 'ARROW_LEFTRIGHT', {'a': p['a'], 'b': p['b'], 'mode': 'REVERSE'}),
                ('op', 'hairmesh.weld', 'Join into One Curve', 'CURVE_DATA', {'a': p['a'], 'b': p['b'], 'mode': 'JOIN'}), ('label', p['title'], 'DECORATE_KEYFRAME')]
    return [dismiss, ('op', 'hairmesh.weld', 'Weld Anchors into One', 'AUTOMERGE_ON', {'a': p['a'], 'b': p['b'], 'mode': 'WELD'}),
            ('empty',), ('label', p['title'], 'DECORATE_KEYFRAME')]


class HAIRMESH_MT_weld_pie(bpy.types.Menu):
    """Weld a dragged anchor released near another one: weld, or (a start on an end)
    reverse then weld / join into one curve; Don't weld"""
    bl_idname = 'HAIRMESH_MT_weld_pie'
    bl_label = 'HairMesh Weld'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        from .tool import draw_item
        pie = self.layout.menu_pie()
        hm = context.scene.hairmesh
        for item in weld_pie_items():
            draw_item(pie, hm, item)


class HAIRMESH_OT_weld_dismiss(bpy.types.Operator):
    """Don't weld: the dragged anchor stays where it was dropped, on its own"""
    bl_idname = 'hairmesh.weld_dismiss'
    bl_label = "Don't Weld"
    bl_options = {'REGISTER'}

    def execute(self, context):
        global pending
        pending = None
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_weld(bpy.types.Operator):
    """Weld anchor A into anchor B (one shared anchor; a curve end welded into the middle of a longer curve is extended along it: a Y); a start on an end can instead reverse the curve first, or join the two into one curve"""
    bl_idname = 'hairmesh.weld'
    bl_label = 'Weld HairMesh Anchors'
    bl_options = {'REGISTER', 'UNDO'}

    a: IntProperty(name='Anchor A', description='The anchor dropped (it goes away)', default=-1)
    b: IntProperty(name='Anchor B', description='The anchor it was dropped on (the shared one afterwards)', default=-1)
    mode: EnumProperty(name='How', items=(('WELD', 'Weld Anchors into One', 'The usual weld'),
                                          ('REVERSE', 'Reverse, Then Weld', 'The start of one curve meets the end of another: reverse the curve whose start it is, so the ends meet, then weld'),
                                          ('JOIN', 'Join into One Curve', 'The curve that ends there goes on with the other one\'s anchors; the other one is gone')), default='WELD')

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        global pending
        root, P, sel = select.current(context)
        a = P.anchors.get(self.a); b = P.anchors.get(self.b)
        if a is None or b is None or a is b:
            self.report({'ERROR'}, 'Pass two different anchor ids (a, b)')
            return {'CANCELLED'}
        pending = None
        CM.rebuild_dirty(P)                                         # the arcs a frame would have made (extendYBranches, joinCurves read them)
        if self.mode == 'JOIN':
            ce = W.joinCurves(P, a, b)
            if ce is not None:
                return _finish(context, root, P, Selection([ce.id], []), self.report, f'Joined into one curve: {ce.name}')
            ext = []                                                # the fallback: a plain weld
        elif self.mode == 'REVERSE':
            choice = W.weld_choice(P, a, b)
            if choice[0] != 'startEnd':
                self.report({'ERROR'}, 'Not a start-meets-end pair: use the plain weld')
                return {'CANCELLED'}
            ext = W.reverseThenWeld(P, a, b, choice[1], choice[2])
        else:
            ext = W.weld(P, a, b)
        msg = f'Anchors welded — {", ".join(ext)} extended along the longer curve (Y)' if ext else 'Anchors welded'
        return _finish(context, root, P, Selection([], [b.id]), self.report, msg)


# ---------------- the anchor actions ----------------

class HAIRMESH_OT_unweld_anchor(bpy.types.Operator):
    """Unweld: the selected curve (or the one given) gets its own anchor at the spot, apart from the other curves, which stay welded to each other (Ctrl + click on a welded anchor of a selected curve)"""
    bl_idname = 'hairmesh.unweld_anchor'
    bl_label = 'Unweld HairMesh Anchor'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', description='The welded anchor (default: the last selected one)', default=-1)
    curve: IntProperty(name='Curve', description='The curve that gets its own anchor (default: the selected curves)', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        a = _anchor(P, sel, self.anchor)
        if a is None:
            self.report({'ERROR'}, 'Select an anchor, or pass its id')
            return {'CANCELLED'}
        items = [self.curve] if self.curve >= 0 else sel.items
        kept = W.unweldAnchor(P, a, items)
        if kept is None:
            self.report({'INFO'}, 'Nothing to unweld: the anchor must be shared, and the curve to come apart selected (Ctrl + click it)')
            return {'CANCELLED'}
        msg = f'Anchor unwelded from {kept[0].name if len(kept) == 1 else f"{len(kept)} curves"}'
        return _finish(context, root, P, None, self.report, msg)


class HAIRMESH_OT_split_at_anchor(bpy.types.Operator):
    """Split every curve running through the anchor in two there; identical pieces (a common trunk) become one curve. The pieces still meet at the anchor (Unweld separates them)"""
    bl_idname = 'hairmesh.split_at_anchor'
    bl_label = 'Split HairMesh Curve at Anchor'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', description='Default: the last selected anchor', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        a = _anchor(P, sel, self.anchor)
        if a is None:
            self.report({'ERROR'}, 'Select an anchor, or pass its id')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        out = W.splitAtAnchor(P, a)
        if not out:
            self.report({'INFO'}, 'No curve runs through this anchor')
            return {'CANCELLED'}
        return _finish(context, root, P, Selection(out, []), self.report, f'Split into {len(out)} curves')


class HAIRMESH_OT_curve_between(bpy.types.Operator):
    """Add a curve between the two selected anchors, welded to both, as long as the curves it joins (it shares their trunks); a middle anchor keeps it off the head"""
    bl_idname = 'hairmesh.curve_between'
    bl_label = 'HairMesh Curve Between Anchors'
    bl_options = {'REGISTER', 'UNDO'}

    a: IntProperty(name='Anchor A', description='Default: the first selected anchor', default=-1)
    b: IntProperty(name='Anchor B', description='Default: the second selected anchor', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        if self.a >= 0 and self.b >= 0:
            a = P.anchors.get(self.a); b = P.anchors.get(self.b)
        elif len(sel.anchors) == 2:
            a = P.anchors.get(sel.anchors[0]); b = P.anchors.get(sel.anchors[1])
        else:
            a = b = None
        if a is None or b is None or a is b:
            self.report({'ERROR'}, 'Select exactly two anchors (or pass a and b)')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        r = W.curveBetween(P, a, b, target_group=target_group(context, P), head_visible=head_visible(root))
        if r is None:
            return {'CANCELLED'}
        c, shared = r
        msg = (f'Curve created between the anchors — it shares the trunk{"s" if len(shared) > 1 else ""} of {" and ".join(shared)}' if shared
               else 'Curve created between the anchors')
        return _finish(context, root, P, Selection([c.id], []), self.report, msg)


class HAIRMESH_OT_curve_through(bpy.types.Operator):
    """Add a free curve through the selected anchors in the order they were picked: new anchors at their places, welded to nothing"""
    bl_idname = 'hairmesh.curve_through'
    bl_label = 'HairMesh Curve Through Anchors'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        anchors = select.anchors_of(P, sel)
        CM.rebuild_dirty(P)
        c = W.curveThroughAnchors(P, anchors) if anchors else None
        if c is None:
            self.report({'INFO'}, 'Pick at least two anchors in different places')
            return {'CANCELLED'}
        return _finish(context, root, P, Selection([c.id], []), self.report, f'{c.name}: a new curve through {len(c.anchors)} anchors (not joined to anything)')


class HAIRMESH_OT_rotate_junction(bpy.types.Operator):
    """Rotate junction: which curve passes smoothly through this junction (the others leave along its tangent, the sharp crotch); Automatic goes back to the automatic tangents"""
    bl_idname = 'hairmesh.rotate_junction'
    bl_label = 'Rotate HairMesh Junction'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', description='Default: the last selected anchor', default=-1)
    choice: IntProperty(name='Choice', description='The n-th curve of the junction choices; -1: automatic', default=-1)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        a = _anchor(P, sel, self.anchor)
        if a is None:
            self.report({'ERROR'}, 'Select an anchor, or pass its id')
            return {'CANCELLED'}
        choices = [c for c in W.junctionChoices(P, a) if c[1] is not None]
        if self.choice < 0:
            pair = None
        elif self.choice < len(choices):
            pair = choices[self.choice][1]
        else:
            self.report({'ERROR'}, f'No junction choice {self.choice} (there are {len(choices)})')
            return {'CANCELLED'}
        W.setPass(P, a, pair)
        return _finish(context, root, P, None, self.report, 'Junction rotated' if pair else 'Junction: automatic tangents')


class HAIRMESH_OT_optimize_anchors(bpy.types.Operator):
    """Optimize anchors: removes anchors one by one (the one changing the shape least first) while the curves keep their shape (within 0.3 % of their length or 3 % of the loop step); ends, junctions and attached anchors stay"""
    bl_idname = 'hairmesh.optimize_anchors'
    bl_label = 'Optimize HairMesh Anchors'
    bl_options = {'REGISTER', 'UNDO'}

    only: BoolProperty(name='Only the Selected Anchors', description='Just the selected anchors may go (the rest of their curves stays as it is), with the whole curve held to its tolerance', default=False)

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        if self.only:
            anchors = select.anchors_of(P, sel); aset = set(anchors)
            cs = [c for c in P.curves() if not c.ring and not c.closed and any(x in aset for x in c.anchors)]
            only = aset
        else:
            cs = select.curves_of(P, sel); only = None
        if not cs:
            self.report({'INFO'}, 'Select curves (or anchors, with Only the Selected Anchors) first')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        n = W.optimizeAnchors(P, cs, only)
        msg = f'Removed {n} anchor{"s" if n > 1 else ""} — the shape is kept' if n else 'Nothing to remove without changing the shape'
        return _finish(context, root, P, Selection(sel.items, []), self.report, msg)


class HAIRMESH_OT_merge_coincident(bpy.types.Operator):
    """Merge anchors of the selected curves that sit at the same place into one shared anchor (anchors of one curve are never merged with each other)"""
    bl_idname = 'hairmesh.merge_coincident'
    bl_label = 'Merge HairMesh Anchors at Identical Places'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        cs = select.curves_of(P, sel)
        if len(cs) < 2:
            self.report({'INFO'}, 'Select two or more curves first')
            return {'CANCELLED'}
        n = W.mergeCoincident(P, cs, P.S.scale * 1e-3)
        if not n:
            self.report({'INFO'}, 'No anchors at identical places')
            return {'CANCELLED'}
        return _finish(context, root, P, Selection(sel.items, []), self.report, f'Merged {n} anchor{"s" if n > 1 else ""}')


# ---------------- the menus (pure descriptions, drawn by tool.draw_item) ----------------

def anchor_more_items(context):
    """The 3.4 part of the anchor pie (openMainPie 7893-7902), for the selected anchors: curve between (exactly 2),
    curve through (2+), optimize the selected anchors, split at the last anchor (a curve runs through it), the
    Unweld from submenu (the last anchor is shared), the Rotate junction submenu (3+ directions)."""
    root, P, sel = select.current(context)
    return _anchor_more(P, select.anchors_of(P, sel) if P is not None else [], False)


def anchor_more_items_short(P, anchors):
    """the same items with short texts, for the rows of the HairMesh Menu pop-up"""
    return _anchor_more(P, anchors, True)


def _anchor_more(P, anchors, short):
    if not anchors:
        return []
    n = len(anchors); a = anchors[n - 1]
    out = []
    if n == 2:
        out.append(('op', 'hairmesh.curve_between', 'Between' if short else 'Create Curve Between the Anchors', 'CURVE_DATA', {}))
    if n >= 2:
        out.append(('op', 'hairmesh.curve_through', 'Through' if short else f'Create Curve Through the Anchors ({n})', 'OUTLINER_OB_CURVE', {}))
    out.append(('op', 'hairmesh.optimize_anchors', 'Optimize Selected' if short else 'Optimize Selected Anchors', 'MOD_SIMPLIFY', {'only': True}))
    if any(not c.closed and 0 < index_of(c.anchors, a) < len(c.anchors) - 1 for c in edit.curvesWith(P, a)):
        out.append(('op', 'hairmesh.split_at_anchor', 'Split' if short else 'Split Curve at the Anchor', 'MOD_PHYSICS', {}))
    if len(edit.curvesWith(P, a)) >= 2 and not edit.isRingAnchor(P, a):
        out.append(('menu', HAIRMESH_MT_unweld.bl_idname, 'Unweld' if short else 'Unweld from', 'UNLINKED'))
    if W.junctionChoices(P, a):
        out.append(('menu', HAIRMESH_MT_junction.bl_idname, 'Junction' if short else 'Rotate Junction', 'FORCE_VORTEX'))
    from .ops_crown import detach_offered
    if detach_offered(P, a):                                        # (4.3: the anchor pie's "⛓ Detach from the connector", 7907)
        out.append(('op', 'hairmesh.crown_detach', 'Detach' if short else 'Detach from the Connector', 'UNLINKED', {'anchor': a.id}))
    return out


def unweld_items(context):
    root, P, sel = select.current(context)
    a = P.anchors.get(sel.last_anchor) if P is not None and sel.anchors else None
    if a is None:
        return []
    users = edit.curvesWith(P, a)
    if len(users) < 2:
        return []
    return [('op', 'hairmesh.unweld_anchor', c.name, 'CURVE_DATA', {'anchor': a.id, 'curve': c.id}) for c in users]


def junction_items(context):
    root, P, sel = select.current(context)
    a = P.anchors.get(sel.last_anchor) if P is not None and sel.anchors else None
    if a is None:
        return []
    out = []; k = 0
    for label, pair in W.junctionChoices(P, a):
        if pair is None:
            out.append(('op', 'hairmesh.rotate_junction', 'Automatic', 'HM:auto', {'anchor': a.id, 'choice': -1}))
        else:
            out.append(('op', 'hairmesh.rotate_junction', label, 'CURVE_DATA', {'anchor': a.id, 'choice': k})); k += 1
    return out


def curve_more_items(context):
    """The 3.4 part of the curve pie: optimize the selected curves, merge coincident anchors (2+ curves)."""
    root, P, sel = select.current(context)
    cs = select.curves_of(P, sel) if P is not None else []
    if not cs:
        return []
    out = [('op', 'hairmesh.optimize_anchors', 'Optimize Anchors (Keep Shape)', 'MOD_SIMPLIFY', {'only': False})]
    if len(cs) >= 2:
        out.append(('op', 'hairmesh.merge_coincident', 'Merge Coincident Anchors', 'AUTOMERGE_ON', {}))
    return out


class _ItemsMenu(bpy.types.Menu):
    items = staticmethod(lambda context: [])

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        from .tool import draw_item
        hm = context.scene.hairmesh
        items = self.items(context)
        if not items:
            self.layout.label(text='Nothing for this selection', icon='INFO')
        for item in items:
            draw_item(self.layout, hm, item)


class HAIRMESH_MT_anchor_more(_ItemsMenu):
    """More anchor actions: curve between / through the anchors, optimize the selected anchors, split at the anchor, unweld, rotate junction"""
    bl_idname = 'HAIRMESH_MT_anchor_more'
    bl_label = 'Weld / Split / Curves'
    items = staticmethod(anchor_more_items)


class HAIRMESH_MT_unweld(_ItemsMenu):
    """Unweld the last selected anchor from one of the curves using it: that curve gets its own anchor there"""
    bl_idname = 'HAIRMESH_MT_unweld'
    bl_label = 'Unweld from'
    items = staticmethod(unweld_items)


class HAIRMESH_MT_junction(_ItemsMenu):
    """Rotate junction: which curve passes smoothly through the last selected anchor"""
    bl_idname = 'HAIRMESH_MT_junction'
    bl_label = 'Rotate Junction'
    items = staticmethod(junction_items)


_CLASSES = (HAIRMESH_OT_weld_dismiss, HAIRMESH_OT_weld, HAIRMESH_OT_unweld_anchor, HAIRMESH_OT_split_at_anchor, HAIRMESH_OT_curve_between,
            HAIRMESH_OT_curve_through, HAIRMESH_OT_rotate_junction, HAIRMESH_OT_optimize_anchors, HAIRMESH_OT_merge_coincident,
            HAIRMESH_MT_weld_pie, HAIRMESH_MT_anchor_more, HAIRMESH_MT_unweld, HAIRMESH_MT_junction)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global pending
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    pending = None
