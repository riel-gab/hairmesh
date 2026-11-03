# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Phase 4.2 inside Blender: the Guides sub-tool (the original's guide tool, hair-modeler.clean.html:5806-5808,
5970-5975, 6025-6035, 6187-6351, 8843-8945, 9015-9024), its three modes (Scene.hairmesh.guide_mode) and the guide's
panel (the Guide block of the D's Selection tab and of the N panel).

  Line      hairmesh.guide_tool (LMB): a dashed green line dragged in the viewport; on the release, the selected
            curves (none selected: every visible one) it crosses make a guide line through the crossings
            (createGuide). A click (5 px) on a guide makes it the active one; elsewhere it picks a curve (Shift adds).
  NURBS Disc a click on the hair puts a ring around the head axis at that height (placeDisc, surfacePoint: the head,
            the walls and the converted meshes, else the curve under the cursor, else the plane through the pivot);
            "+ Create" (hairmesh.disc_create) makes one around the selected curves or the whole hair.
  Pencil    hold LMB and draw a loop: it closes by itself back at its start (> 120 px drawn, < 14 px from the start),
            or Enter closes it; Esc cancels (pencilAdd, finishPencil). The loop becomes a 6-point disc.

The guide's settings are WindowManager properties (HairMeshGuideProps) through deferred.py, as the conversion sliders:
the model changes at once, the locks the guide shapes are rebuilt and one undo step is pushed when the slider stops
or is let go. Without a guide selected (the Guides sub-tool), the same sliders edit the settings for new guides
(S.guideDefaults). The influence zone (D4.7) shows while a guide is selected and for 1.5 s after one of its sliders.

The data part is core/guidetool.py (bit for bit with the original: tests/test_guidetool_parity.py). Every operator
has a form that needs no viewport, for the tests and the stress test."""
import json
from math import hypot

import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, IntVectorProperty, StringProperty, BoolProperty
from bpy.types import PropertyGroup

from ..core import curves as CM, effects as EF, groups as G, guidetool as GT, scene as SC
from ..core.guides import discRings, guideCurves
from ..core.selection import Selection
from ..core.vec3 import V3
from . import deferred, draw as drawmod, live, modal_keys, owner, pick, select, session
from .coords import f32, field_same
from .ops_draw import NAV_EVENTS
from .ops_select import PICK_CURVE_PX, View
from .props import GUIDE_MODES

CLICK_PX = 5                                                        # a stroke this short is a click (6026)
PICK_GUIDE_PX = 7                                                   # pickCurve's 7 px (5674)
HELP_PICK = '\n'.join(('Click a green guide line (between two curves) or a disc\'s light band: select it (here in Line or NURBS Disc, or in Select, 1).',
                       'Click a ring (the yellow or green circle): select that ring alone. Shift + click adds the other.',
                       'G / R / S or the gizmo move a ring; the locks follow on release.',
                       'Alt + A: clear the selection (in Select a click in the empty space too).',
                       'Shift + right click (the pie): the curves a guide line crosses, the disc of a ring.',
                       'X (it asks first) or Delete: delete the selected guide (the curves stay).'))
HELP = {
    'LINE': '\n'.join(('Select curves (1, Select), come back here (4).', 'Drag a line across them: the guide is made where it crosses them (nothing selected: every visible curve).', HELP_PICK)),
    'DISC': '\n'.join(('Click on the hair: a disc goes around the head at that height; the curves through it get a guide loop (green).',
                       '"Create NURBS Disc": one around the selected curves (or all the hair).', HELP_PICK)),
    'PENCIL': '\n'.join(('Hold the left button and draw a loop around the hair.', 'Back at the start, or Enter: it closes by itself. Esc cancels.', HELP_PICK)),
}
NO_GUIDE_HIT = 'Click on the hair (a wall, the head or a curve) to place the disc'
DISC_MADE = 'NURBS disc created: click a ring (in Select, 1) and move, rotate or scale it with G / R / S or the gizmo'   # (the original's toast names its keys E / R / T and its Brush)

pencil = None                                                       # the loop being drawn: {'root': root name, 'pc': guidetool.Pencil}


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def selected_guide(P, sel):
    """the guide the panel shows: the last selected guide (renderToolPanel 9025: the last guide of S.sel)"""
    for iid in reversed(sel.items):
        it = P.items.get(iid)
        if it is not None and it.type == 'guide':
            return it
    return None


def shown_guides(P, sel):
    """the guides the viewport shows (their eye, their groups', the Guides group's mode)"""
    _cs, act, loose = G.shownCurves(P, sel.items, sel.anchors)
    return G.shownGuides(P, act, loose)


def ring_under(P, guides, x, y, project, radius):
    """the ring of a shown disc within `radius` px of (x, y) (pickCurve over the rings: they are not in shownCurves), or
    None"""
    rings = [c for g in guides if g.kind == 'disc' for c in discRings(P, g) if len(c.samples) >= 2]
    ch = pick.pick_curve(rings, project, x, y, radius) if rings else None
    return ch['curve'] if ch is not None else None


def select_ring(sel, ring, add):
    """4.2c, item 5 (ours: the original picks a disc's surface only): a click on a ring selects that ring and its anchors
    — S.sel = the ring, S.asel = its anchors, as the disc's "Select outer ring" (8936) —, one at a time; Shift adds it
    (again: takes it off). Then G / R / S and the gizmo move just that ring."""
    ids = list(dict.fromkeys(a.id for a in ring.anchors))
    if not add:
        return Selection([ring.id], ids)
    mine = set(ids)
    if ring.id in sel.items:
        return Selection([i for i in sel.items if i != ring.id], [a for a in sel.anchors if a not in mine])
    return Selection([*sel.items, ring.id], [*(a for a in sel.anchors if a not in mine), *ids])


def click_on_guide(context, v, sel, x, y, add, surface=True, lines=True, toggle=True):
    """a click on a guide shown in the view `v` (Select and Guides, 4.2c item 5): `lines` — a ring (that ring and its
    anchors), a guide line (pickGuide, 7 px) —, else `surface` — a disc's band (pickObjects over the discs). The new
    selection, or None when the click is on no guide. `add` (Shift) adds a ring; a guide is toggled with it only when
    `toggle` (Select, as a curve; the Guides tool activates it whatever the keys, 6031)."""
    P = v.P
    guides = shown_guides(P, sel)
    if not guides:
        return None
    g = None
    if lines:
        ring = ring_under(P, guides, x, y, v.project, v.px(PICK_CURVE_PX))
        if ring is not None:
            return select_ring(sel, ring, add)
        g = GT.pickGuide(P, guides, x, y, v.project, v.px(PICK_GUIDE_PX))
    if g is None and surface:
        g = pick.disc_under(P, guides, pick.model_ray(v.root, context.region, context.region_data, (x, y)))
    if g is None:
        return None
    out = sel.copy()
    if add and toggle:
        out.select_item(g.id, True)
    else:
        GT.activateGuide(P, out, g)                                 # (6031: the curve selection stays, the guide goes in last)
    return out


def touch_guide(root, P, g, before=()):
    """S.surfDirty for a guide changed: the locks it shaped before and the locks it shapes now (a disc's hits come
    from its rebuild: the frame's settle first)"""
    SC.settle(P)
    live.touch(root, list(before) + [c for c in guideCurves(P, g) if c not in before])


def _finish(context, root, P, sel, report, msg, touch=None):
    select.store(context.scene.hairmesh, sel)
    G.panelsRendered(P, sel.items[len(sel.items) - 1] if sel.items else None)   # onSelChange (7850): the outliner re-rendered
    session.save_project(root, P)
    if touch is not None:
        live.touch(root, touch)
    live.update(context, root)
    select.redraw(context)
    if msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def created(context, root, P, sel, g, report, msg):
    """a guide just made (createGuide, placeDisc, createDiscButton, finishPencil): selected, saved, its locks rebuilt"""
    SC.settle(P)                                                    # (a disc: its hits — the curves through it)
    return _finish(context, root, P, sel, report, msg, touch=guideCurves(P, g))


# ---------------- the modes ----------------

class HAIRMESH_OT_set_guide_mode(bpy.types.Operator):
    """Set the Guides mode: a line across curves, a NURBS disc placed with a click, or a disc drawn with the pencil"""
    bl_idname = 'hairmesh.set_guide_mode'
    bl_label = 'Guides Mode'
    bl_options = {'REGISTER'}

    mode: EnumProperty(name='Mode', items=GUIDE_MODES, default='LINE')

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        hm = context.scene.hairmesh
        hm.subtool = 'GUIDES'
        hm.guide_mode = self.mode
        if self.mode != 'PENCIL':
            cancel_pencil()                                         # (9016: leaving the Pencil drops its loop)
        select.redraw(context)
        return {'FINISHED'}


# ---------------- the line, the disc's click, a click on a guide ----------------

class HAIRMESH_OT_guide_tool(bpy.types.Operator):
    """Drag a line across the selected curves (Line), click on the hair (NURBS Disc) or draw a loop (Pencil); a click on a guide makes it the active one"""
    bl_idname = 'hairmesh.guide_tool'
    bl_label = 'HairMesh Guides'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        hm = context.scene.hairmesh
        if hm.subtool != 'GUIDES' or event.alt or event.oskey:
            return {'PASS_THROUGH'}
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        if hm.guide_mode == 'PENCIL':
            return {'PASS_THROUGH'}                                 # (the Pencil's own keymap item, hairmesh.guide_pencil, takes it)
        self.view = v; self.add = event.shift or event.ctrl; self.shift = event.shift
        self.x0, self.y0 = event.mouse_region_x, event.mouse_region_y
        drawmod.overlay = ('gline', self.x0, self.y0, self.x0, self.y0)
        context.window_manager.modal_handler_add(self)
        line = hm.guide_mode == 'LINE'
        live.header(context.area, 'HairMesh Guides (Line)' if line else 'HairMesh Guides (NURBS Disc)', LINE_KEYS if line else DISC_KEYS)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        drawmod.overlay = None
        live.header(context.area, None)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)                                          # (the window closed, a file loaded: the stroke goes)

    def modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        if event.type == 'MOUSEMOVE':
            if context.scene.hairmesh.guide_mode == 'LINE':
                drawmod.overlay = ('gline', self.x0, self.y0, x, y)    # (6011: the dashed line of the Line mode)
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end(context)
            return self._release(context, x, y)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _release(self, context, x, y):
        v = self.view; P = v.P; hm = context.scene.hairmesh; root = v.root
        project = pick.region_projector(context.region, context.region_data, root.matrix_world)
        sel = select.load(hm).prune(P)
        CM.rebuild_dirty(P)
        click = hypot(x - self.x0, y - self.y0) <= v.px(CLICK_PX)
        if click:
            # a click on a guide / disc makes it the active one (6028-6031; the curve selection stays); on a ring, that
            # ring (4.2c, item 5)
            got = click_on_guide(context, v, sel, x, y, self.shift, toggle=False)
            if got is not None:
                select.store(hm, got); select.redraw(context)
                return {'FINISHED'}
        if hm.guide_mode == 'DISC':
            if not click:
                return {'CANCELLED'}                                # (6033: a drag places nothing)
            ray = pick.model_ray(root, context.region, context.region_data, (x, y))
            pt = pick.surface_point(context, root, P, ray, x, y, [c for c in v.curves() if not c.ring], v.px(PICK_CURVE_PX))
            g = GT.placeDisc(P, sel, pt)
            if g is None:
                self.report({'INFO'}, NO_GUIDE_HIT)
                return {'CANCELLED'}
            return created(context, root, P, sel, g, self.report, DISC_MADE)
        if not click:
            shown = {c.id for c in v.curves()}
            g, msg = GT.createGuide(P, sel, (self.x0, self.y0), (x, y), project, lambda c: c.id in shown)
            if g is None:
                self.report({'INFO'}, msg)
                return {'CANCELLED'}
            return created(context, root, P, sel, g, self.report, msg)
        ch = pick.pick_curve(v.curves(), project, x, y, v.px(PICK_CURVE_PX))   # (6035: elsewhere a click picks a curve)
        if ch is None:
            return {'CANCELLED'}
        sel.select_item(ch['curve'].id, self.add)
        select.store(hm, sel); select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_guide_stroke(bpy.types.Operator):
    """Make a guide line across the selected curves (none selected: every visible one) from a stroke in a view: the non-modal form of the Line mode"""
    bl_idname = 'hairmesh.guide_stroke'
    bl_label = 'Guide Line from a Stroke'
    bl_options = {'REGISTER', 'UNDO'}

    stroke: FloatVectorProperty(name='Stroke', description='The line in region pixels (x0, y0, x1, y1)', size=4, options={'HIDDEN', 'SKIP_SAVE'})
    view_matrix: FloatVectorProperty(name='View', description="The view's perspective matrix (world to clip, row by row) the stroke was drawn in", size=16, options={'HIDDEN', 'SKIP_SAVE'})
    region_size: IntVectorProperty(name='Region', description='The width and height of the region the stroke was drawn in', size=2, default=(1, 1), options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        from .ops_scissors import matrix_projector
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)
        shown = {c.id for c in G.shownCurves(P, sel.items, sel.anchors)[0]}
        x0, y0, x1, y1 = self.stroke
        g, msg = GT.createGuide(P, sel, (x0, y0), (x1, y1), matrix_projector(list(self.view_matrix), tuple(self.region_size), root.matrix_world), lambda c: c.id in shown)
        if g is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        return created(context, root, P, sel, g, self.report, msg)


class HAIRMESH_OT_place_disc(bpy.types.Operator):
    """Place a NURBS disc around the head axis at a point of the model (the non-modal form of the NURBS Disc mode's click)"""
    bl_idname = 'hairmesh.place_disc'
    bl_label = 'Place NURBS Disc'
    bl_options = {'REGISTER', 'UNDO'}

    point: FloatVectorProperty(name='Point', description="The clicked point in the model's space (the original's axes: Y up)", size=3, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        g = GT.placeDisc(P, sel, V3(*self.point))
        return created(context, root, P, sel, g, self.report, DISC_MADE)


class HAIRMESH_OT_disc_create(bpy.types.Operator):
    """Add a new NURBS disc: around the selected curves (or all the visible hair, or the top of the model) at their middle height"""
    bl_idname = 'hairmesh.disc_create'
    bl_label = 'Create NURBS Disc'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        from ..core.edit import isVisible
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)
        g = GT.createDiscButton(P, sel, lambda c: isVisible(P, c))
        return created(context, root, P, sel, g, self.report, DISC_MADE)


# ---------------- the pencil ----------------

def cancel_pencil():
    """cancelPencil (6317): the loop drawn so far goes, and the header and keys that stay between its strokes, which no
    modal ends"""
    global pencil
    was = pencil is not None
    pencil = None
    drawmod.pencil = None
    if not was:
        return
    for screen in bpy.data.screens:                                 # (every workspace's: the stroke may be on another one)
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                live.header(area, None)
    modal_keys.clear(bpy.context)


PENCIL_HEADER = 'HairMesh Guides (Pencil)'
PENCIL_KEYS = ((('LMB Drag',), 'Draw the Loop'), (('Enter',), 'Close the Loop'), (('Esc',), 'Cancel'))
LINE_KEYS = ((('LMB Drag',), 'Make a Guide Line'), (('LMB',), 'Select a Guide'), (('Esc',), 'Cancel'))
DISC_KEYS = ((('LMB',), 'Place a NURBS Disc'), (('LMB',), 'On a Guide: Select It'), (('Esc',), 'Cancel'))


def _pencil_ok(context):
    """the Pencil can go on: the Guides sub-tool in its Pencil mode, the tool on, Object Mode"""
    from . import tool
    hm = context.scene.hairmesh
    return (hm.subtool == 'GUIDES' and hm.guide_mode == 'PENCIL' and context.mode == 'OBJECT' and owner.visible(context)
            and tool.is_active(context))


def finish_pencil(context, report):
    """finishPencil (6318) with the loop drawn so far, in the 3D View of `context`: a disc (one undo step) or the toast"""
    v = View.of(context)
    pc = pencil['pc'] if pencil else None
    cancel_pencil()
    if v is None:
        return {'CANCELLED'}
    cam, _piv = pick.model_cam(v.root, context.region_data)
    P = session.get_project(v.root)
    sel = select.load(context.scene.hairmesh).prune(P)
    g, msg = GT.finishPencil(P, sel, pc, cam)
    if g is None:
        report({'INFO'}, msg)
        select.redraw(context)
        return {'CANCELLED'}
    return created(context, v.root, P, sel, g, report, msg)


class HAIRMESH_OT_guide_pencil(bpy.types.Operator):
    """Hold the left button and draw a loop around the hair; it closes by itself back at its start, or press Enter; Esc cancels. The loop becomes a NURBS disc"""
    bl_idname = 'hairmesh.guide_pencil'
    bl_label = 'HairMesh Guide Pencil'
    bl_options = {'REGISTER', 'UNDO'}

    # One stroke (LMB held) per run: the loop lives in the module (`pencil`, as S.pencil) between strokes, so nothing
    # waits on the window between them — the gizmos, the navigation, the other editors and Blender's own clicks work as
    # always; Enter (hairmesh.guide_pencil_finish) closes it, Esc (hairmesh.guide_pencil_cancel) drops it, and leaving
    # the Pencil (another mode or sub-tool: props' update, set_guide_mode) drops it too (4713).

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        global pencil
        if not _pencil_ok(context) or event.alt or event.oskey:
            return {'PASS_THROUGH'}                                 # (another sub-tool or mode; Alt+LMB is Blender's emulated 3-button mouse)
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        here = (v.root.name, context.region.as_pointer())
        if pencil is None or pencil['at'] != here:
            pencil = {'root': v.root.name, 'at': here, 'pc': GT.Pencil()}   # (5807: the first press starts the loop; another view starts its own — its pixels are its own)
        self.view = v
        live.header(context.area, PENCIL_HEADER, PENCIL_KEYS)
        if self._add(context, event) == 'close':
            # (a stroke that starts back at the loop's start closes it on the press: finished BEFORE any modal handler
            # — lot 4.3, item 2: with the handler added first, the FINISHED operator went into Blender's last-operator
            # list while the handler kept it; the next event the handler freed it, and a timer reading that list
            # (MESHmachine's) crashed Blender: the owner's crash of 29/10/2026, tools/sim_c43_shots.py "pencil")
            return self._finish(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _add(self, context, event):
        v = self.view; root = v.root
        P = v.P = session.get_project(root)                         # (the model as it is now)
        x, y = event.mouse_region_x, event.mouse_region_y
        ray = pick.model_ray(root, context.region, context.region_data, (x, y))
        cam, piv = pick.model_cam(root, context.region_data)
        r = GT.pencilAdd(pencil['pc'], (x, y), ray, cam, piv, lambda: pick.surface_hit(context, root, P, ray),
                         step_px=v.px(4), close_len=v.px(120), close_px=v.px(14))
        show_pencil(context, root)
        return r

    def _end(self, context):
        live.header(context.area, None)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)                                          # (the window closed, a file loaded: load_post drops the loop)

    def _finish(self, context):
        self._end(context)
        return finish_pencil(context, self.report)

    def modal(self, context, event):
        if pencil is None or not _pencil_ok(context):
            cancel_pencil(); self._end(context)                     # (another sub-tool or mode, the tool off, Edit Mode: the loop goes, 4713)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if self._add(context, event) == 'close':
                return self._finish(context)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end(context)
            live.header(context.area, PENCIL_HEADER, PENCIL_KEYS)
            return {'CANCELLED'}                                    # (the stroke ends; the loop stays for the next one — no undo step)
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            return self._finish(context)                            # (8059: Enter closes it)
        if event.type == 'ESC' and event.value == 'PRESS':
            cancel_pencil(); self._end(context)                     # (8053: Esc cancels)
            return {'CANCELLED'}
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}


def show_pencil(context, root):
    from mathutils import Vector
    from .coords import to_blender
    pc = pencil['pc'] if pencil else None
    M = root.matrix_world
    drawmod.pencil = [tuple(M @ Vector(to_blender(p))) for p in pc.pts] if pc is not None and len(pc.pts) >= 2 else None
    if context.area is not None:
        context.area.tag_redraw()


class HAIRMESH_OT_guide_pencil_finish(bpy.types.Operator):
    """Close the pencil loop into a NURBS disc (Enter; passes on when no loop is being drawn)"""
    bl_idname = 'hairmesh.guide_pencil_finish'
    bl_label = 'Close the Pencil Loop'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        if pencil is None or not _pencil_ok(context):
            return {'PASS_THROUGH'}
        live.header(context.area, None)
        return finish_pencil(context, self.report)

    def execute(self, context):
        if pencil is None:
            self.report({'INFO'}, 'No pencil loop is being drawn')
            return {'CANCELLED'}
        return finish_pencil(context, self.report)


class HAIRMESH_OT_guide_pencil_cancel(bpy.types.Operator):
    """Drop the pencil loop (Esc; passes on when no loop is being drawn)"""
    bl_idname = 'hairmesh.guide_pencil_cancel'
    bl_label = 'Cancel the Pencil Loop'
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        if pencil is None:
            return {'PASS_THROUGH'}
        cancel_pencil()
        live.header(context.area, None)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_pencil_disc(bpy.types.Operator):
    """Turn a loop of points into a NURBS disc (the non-modal form of the Pencil)"""
    bl_idname = 'hairmesh.pencil_disc'
    bl_label = 'NURBS Disc from a Loop'
    bl_options = {'REGISTER', 'UNDO'}

    points_json: StringProperty(name='Points', description="The loop's points in the model's space, [[x, y, z], ...]", default='[]', options={'HIDDEN', 'SKIP_SAVE'})
    cam_dir: FloatVectorProperty(name='View Direction', description="The camera's direction in the model's space (the plane's normal when the loop has no area)", size=3, default=(0.0, 0.0, -1.0), options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        pc = GT.Pencil()
        pc.pts = [V3(*p) for p in json.loads(self.points_json)]
        g, msg = GT.finishPencil(P, sel, pc, V3(*self.cam_dir))
        if g is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        return created(context, root, P, sel, g, self.report, msg)


# ---------------- the guide's panel (guideControls 8843) ----------------

def _guide():
    root, P, sel = select.current(bpy.context)
    return root, P, (selected_guide(P, sel) if P is not None else None)


class HAIRMESH_OT_disc_reset(bpy.types.Operator):
    """Reset deformations: both rings of the disc back to circles of their radii in the disc's plane"""
    bl_idname = 'hairmesh.disc_reset'
    bl_label = 'Reset Deformations'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, _P, g = _guide()
        if g is None or g.kind != 'disc':
            cls.poll_message_set('Select a NURBS disc (Guides)')
            return False
        if len(discRings(_P, g)) < 2:
            cls.poll_message_set('This disc has lost a ring: nothing to reset')
            return False
        if not EF.discResetActs(_P, g):                              # (4.4: nothing to reset, nothing rebuilt)
            cls.poll_message_set('Nothing to reset: both rings are circles of their radii already (deform one by dragging its anchors, or with G / R / S)')
            return False
        return True

    def execute(self, context):
        root, P, g = _guide()
        if g is None or not EF.discResetActs(P, g):
            return {'CANCELLED'}
        before = guideCurves(P, g)
        CM.markAnchorsDirty(P, GT.resetDisc(P, g))
        touch_guide(root, P, g, before)
        _r, _P, sel = select.current(context)
        return _finish(context, root, P, sel, self.report, f'{g.name}: deformations reset')


def disc_of_selection(P, sel):
    """the disc the selection is about: the last selected guide when it is a disc, else the disc of a selected ring"""
    g = selected_guide(P, sel)
    if g is not None:
        return g if g.kind == 'disc' else None
    for iid in reversed(sel.items):
        c = P.items.get(iid)
        if c is not None and c.type == 'curve' and c.ring and getattr(P.items.get(c.ring), 'kind', None) == 'disc':
            return P.items[c.ring]
    return None


class HAIRMESH_OT_disc_select_ring(bpy.types.Operator):
    """Select one ring of the disc with its anchors (the ring turns yellow): then G / R / S or the gizmo move, rotate, scale just that ring"""
    bl_idname = 'hairmesh.disc_select_ring'
    bl_label = 'Select Disc Ring'
    bl_options = {'REGISTER', 'UNDO'}

    which: EnumProperty(name='Ring', items=(('OUT', 'Outer', 'The outer ring'), ('IN', 'Inner', 'The inner ring')), default='OUT')
    disc: IntProperty(name='Disc', description='The disc (default: the selected one, or the disc of the selected ring)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, P, sel = select.current(context)
        if P is None or disc_of_selection(P, sel) is None:
            cls.poll_message_set('Select a NURBS disc (Guides)')
            return False
        return True

    def execute(self, context):
        root, P, sel = select.current(context)
        g = P.items.get(self.disc) if self.disc >= 0 else disc_of_selection(P, sel)
        if g is None or g.type != 'guide' or g.kind != 'disc':
            return {'CANCELLED'}
        rs = discRings(P, g)
        ring = rs[0 if self.which == 'OUT' else 1] if len(rs) > (0 if self.which == 'OUT' else 1) else None
        if ring is None:
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection([ring.id], [a.id for a in ring.anchors]))   # (8936: S.sel = the ring, S.asel = its anchors)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_disc_of_ring(bpy.types.Operator):
    """Select the disc this ring belongs to (the pie with a ring selected: the disc from its ring)"""
    bl_idname = 'hairmesh.disc_of_ring'
    bl_label = 'Select the Disc'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, P, sel = select.current(context)
        if P is None or selected_ring(P, sel) is None:
            cls.poll_message_set('Select a ring of a NURBS disc (a click on its line)')
            return False
        return True

    def execute(self, context):
        _root, P, sel = select.current(context)
        ring = selected_ring(P, sel)
        d = P.items.get(ring.ring) if ring is not None else None
        if d is None:
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection([d.id], []))
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_guide_delete(bpy.types.Operator):
    """Delete this guide only: the curves selected with it stay, and the locks it shaped are rebuilt without it"""
    bl_idname = 'hairmesh.guide_delete'
    bl_label = 'Delete Guide'
    bl_options = {'REGISTER', 'UNDO'}

    item: IntProperty(name='Guide', description='The guide (default: the one the panel shows)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, _P, g = _guide()
        if g is None:
            cls.poll_message_set('Select a guide (Guides)')
            return False
        return True

    def execute(self, context):
        from ..core.edit import deleteItem
        root, P, sel = select.current(context)
        g = P.items.get(self.item) if self.item >= 0 else selected_guide(P, sel)
        if g is None or g.type != 'guide':
            return {'CANCELLED'}
        name = g.name
        deleteItem(P, g.id)                                         # (deleteItem 771: the disc's rings go with it)
        sel = Selection([i for i in sel.items if i in P.items], []).prune(P)
        return _finish(context, root, P, sel, self.report, f'{name} deleted (the curves stay)')


class HAIRMESH_OT_guide_select_crossed(bpy.types.Operator):
    """Select the curves the guide line crosses (and the guide)"""
    bl_idname = 'hairmesh.guide_select_crossed'
    bl_label = 'Select Crossed Curves'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, _P, g = _guide()
        if g is None or g.kind == 'disc':
            cls.poll_message_set('Select a guide line (Guides)')
            return False
        return True

    def execute(self, context):
        _root, P, g = _guide()
        select.store(context.scene.hairmesh, Selection(GT.crossedCurves(P, g), []))
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_guide_excl(bpy.types.Operator):
    """Add a group to the guide's list, or take it off: with Exclude, the guide leaves the listed groups alone; with Only, it shapes only them"""
    bl_idname = 'hairmesh.guide_excl'
    bl_label = 'Guide Group List'
    bl_options = {'REGISTER', 'UNDO'}

    group: IntProperty(name='Group', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    remove: BoolProperty(name='Remove', default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, _P, g = _guide()
        if g is None:
            cls.poll_message_set('Select a guide (Guides)')
            return False
        return True

    @classmethod
    def description(cls, context, properties):
        return 'Take this group off the list' if properties.remove else 'Add this group to the list'

    def execute(self, context):
        root, P, g = _guide()
        ex = [i for i in (g.p.get('excl') or []) if (it := P.items.get(i)) is not None and it.type == 'group']
        if self.remove:
            lst = [i for i in ex if i != self.group]
        elif self.group in P.items and self.group not in ex:
            lst = ex + [self.group]
        else:
            return {'CANCELLED'}
        GT.setExclusions(g, lst)
        _r, _P, sel = select.current(context)
        return _finish(context, root, P, sel, self.report, None, touch=guideCurves(P, g))


class HAIRMESH_MT_guide_excl(bpy.types.Menu):
    """Add a group the guide crosses (or a parent group) to its list"""
    bl_idname = 'HAIRMESH_MT_guide_excl'
    bl_label = 'Add a Group'

    def draw(self, context):
        _r, P, g = _guide()
        lay = self.layout
        cand = GT.exclusionCandidates(P, g) if g is not None else []
        if not cand:
            lay.label(text='No other group crosses it', icon='INFO')
            return
        for it, depth in cand:
            op = lay.operator('hairmesh.guide_excl', text='    ' * depth + it.name, icon='OUTLINER_COLLECTION')
            op.group = it.id; op.remove = False


# ---- the sliders: views on the guide's p (or on S.guideDefaults without a guide), set through deferred.py ----

DEFAULTS = {'radius': None, 'blend': 1.0, 'density': 1.0, 'priority': 0.0}
ZONE_KEYS = ('radius', 'blend', 'priority')                         # the sliders that flash the zone (showZone 6210)


def _target():
    """(root, P, guide or None, the dict the sliders edit): the selected guide's p, else S.guideDefaults"""
    root, P, g = _guide()
    if P is None:
        return None, None, None, None
    return root, P, g, (g.p if g is not None else P.settings.guideDefaults)


def _float(key):
    def get(self):
        root, P, g, d = _target()
        if d is None:
            return 0.0
        v = d.get(key)
        if v is None:
            if key == 'radius':
                from ..core.duplicate import guideDefaultsNow
                v = guideDefaultsNow(P)['radius']                   # (the panel shows the radius new guides get: 4 loop steps)
            else:
                v = DEFAULTS[key]
        return float(v)

    def set(self, v):
        root, P, g, d = _target()
        if d is None:
            return
        v = f32(v)
        if g is None:
            if d.get('radius') is None:
                from ..core.duplicate import guideDefaultsNow
                d['radius'] = guideDefaultsNow(P)['radius']         # (8844: the panel without a guide fills the radius first)
            if field_same(d.get(key), v):
                return
            GT.setGuideParam(d, key, v)
            deferred.changed(bpy.context, root, rebuild=False)      # (nothing built reads the defaults: saved, one undo step)
            return
        if field_same(g.p.get(key), v):
            return
        GT.setGuideParam(g, key, v)
        live.touch(root, guideCurves(P, g))                         # S.surfDirty (upd, 8845): the locks the guide shapes
        if key in ZONE_KEYS:
            drawmod.flash_zone(g.id)
        deferred.changed(bpy.context, root)
    return get, set


def _get_local(self):
    root, P, g, d = _target()
    return bool(d is not None and d.get('local') is not False)


def _set_local(self, v):
    root, P, g, d = _target()
    if d is None or (d.get('local') is not False) == bool(v):
        return
    if g is None:
        GT.setGuideParam(d, 'local', bool(v))
        deferred.changed(bpy.context, root, rebuild=False)
        return
    GT.setGuideParam(g, 'local', bool(v))
    live.touch(root, guideCurves(P, g))
    deferred.changed(bpy.context, root)


def _get_excl_mode(self):
    root, P, g, d = _target()
    return 1 if g is not None and g.p.get('exclMode') == 'only' else 0


def _set_excl_mode(self, v):
    root, P, g, d = _target()
    mode = 'only' if v == 1 else 'exclude'
    if g is None or (g.p.get('exclMode') or 'exclude') == mode:
        return
    GT.setExclMode(g, mode)
    live.touch(root, guideCurves(P, g))
    deferred.changed(bpy.context, root)


def _radius(which):
    key = 'rOut' if which == 'out' else 'rIn'

    def get(self):
        root, P, g, d = _target()
        return float(g.p.get(key) or 0.0) if g is not None and g.kind == 'disc' else 0.0

    def set(self, v):
        root, P, g, d = _target()
        if g is None or g.kind != 'disc':
            return
        mn = GT.discMinR(P)
        r = GT.clampedRadius(P, g, which, min(max(f32(v), 2 * mn if which == 'out' else mn), P.S.scale * 1.5))   # (the slider's range, 8925-8927)
        if r == g.p.get(key):
            return
        before = guideCurves(P, g)
        CM.markAnchorsDirty(P, GT.setDiscRadius(P, g, which, r))
        touch_guide(root, P, g, before)
        drawmod.flash_zone(g.id)
        deferred.changed(bpy.context, root)
    return get, set


class HairMeshGuideProps(PropertyGroup):
    """The guide's panel (guideControls 8843-8939): on the WindowManager, so the rebuild and the undo step follow the
    slider when it stops or is let go (deferred.py). Views on the model, nothing is stored here."""
    guide_radius: FloatProperty(name='Influence Radius', description='Zone along the lock around the guide where the loops become parallel to it, as a share of the head height (green while the guide is selected and for a moment after a change). 0: only the loop on the guide', min=0.0, max=0.5, step=0.5, precision=3, get=_float('radius')[0], set=_float('radius')[1])
    guide_blend: FloatProperty(name='Blend', description='0: every loop in the radius is parallel to the guide (the whole zone light green); higher: from the edge of the radius inwards the loops mix back to their own positions (the zone darkens from its edges). Loop Density and Priority fade the same way', min=0.0, max=1.0, subtype='FACTOR', get=_float('blend')[0], set=_float('blend')[1])
    guide_density: FloatProperty(name='Loop Density', description='More (or fewer) loops inside the zone. ×1: no extra loops, the guide only aligns them. Fades like the guide: Blend 0, the whole zone gets the density; Blend 50 %, mainly round the center, weaker towards the edge', min=0.25, max=5.0, step=5, precision=2, get=_float('density')[0], set=_float('density')[1])
    guide_priority: FloatProperty(name='Priority', description="How strongly the guide wins inside its zone over the other loop algorithms (loops from neighboring locks, Fix Sharp Angles, Relax, Deviation on bends, Y junction loops): at 100 % the loops there take the guide loop's direction. From 50 % on it also overrides the loop a Y junction gets. Follows the zone weight", min=0.0, max=1.0, subtype='FACTOR', get=_float('priority')[0], set=_float('priority')[1])
    guide_local: BoolProperty(name='Only Its Own Locks', description='On: the guide shapes only the locks whose curves it crosses. Off: locks welded to these locks along a trunk take the guide loops there too', get=_get_local, set=_set_local)
    guide_excl_mode: EnumProperty(name='Groups', description='Exclude: the guide shapes every lock it crosses except those in the listed groups. Only: it shapes only the locks in the listed groups (an empty list: every lock)',
                            items=(('EXCLUDE', 'Exclude Listed', 'The guide leaves the listed groups alone', 0), ('ONLY', 'Only Listed', 'The guide works only on the listed groups (an empty list: every lock)', 1)),
                            get=_get_excl_mode, set=_set_excl_mode)
    guide_r_out: FloatProperty(name='Outer Radius', description="The disc's outer ring: every point moves radially by the change, so hand deformations are kept (in the project's units; it stays outside the inner ring)", min=0.0, precision=3, get=_radius('out')[0], set=_radius('out')[1])
    guide_r_in: FloatProperty(name='Inner Radius', description="The disc's inner ring (the hole): every point moves radially by the change (in the project's units; it stays inside the outer ring)", min=0.0, precision=3, get=_radius('in')[0], set=_radius('in')[1])


GUIDE_ATTRS = frozenset(HairMeshGuideProps.__annotations__)


# ---------------- the D and the N: rows (pure lists of items, tool.draw_item draws them) ----------------

def gprop(attr, text, **opts):
    """a property of the guide's panel (HairMeshGuideProps on the WindowManager: props.data_of finds it)"""
    return ('prop', 'guide_' + attr, text, opts)


def guide_settings_rows(context, P, g):
    """the sliders of guideControls (8856-8877): Influence Radius, Blend, Loop Density, Priority, Only Its Own Locks"""
    return [[gprop('radius', 'Influence Radius'), gprop('blend', 'Blend')], [gprop('density', 'Loop Density'), gprop('priority', 'Priority')], [gprop('local', 'Only Its Own Locks')]]


def guide_rows(context, P, g, room):
    """The guide sheet (guideControls, 8843-8945) by category: the guide (name, how many curves it shapes; Delete),
    then the disc's radii and Reset, Influence (the sliders) and Groups (Exclude / Only and the list), closed. No
    selection buttons: the original's "Select outer / inner ring" (8936-8937) is a click on the ring, its "Select crossed
    curves" (8941) is in the pie (Shift + RMB)."""
    from .tool import block, enum, group_label, label_and, text
    n = len(g.hits) if g.hits else 0
    rows = [[('label', f'{g.name} ({n} curve{"s" if n != 1 else ""})', 'MESH_CIRCLE' if g.kind == 'disc' else 'IPO_LINEAR')],
            *label_and(group_label(P, g), ('op', 'hairmesh.guide_delete', 'Delete Guide', 'X', {'item': g.id}), room)]
    if g.kind == 'disc':
        # (8923-8939: the radii, Reset deformations; a ring is selected by a click on it — then G / R / S or the gizmo
        # move just that ring)
        rows.append(block('guide_disc', 'Disc', 'MESH_CIRCLE', [[gprop('r_out', 'Outer Radius'), gprop('r_in', 'Inner Radius')],
                                                                [('op', 'hairmesh.disc_reset', 'Reset Deformations', 'LOOP_BACK', {})]]))
    rows.append(block('guide_influence', 'Influence', 'PROP_ON', guide_settings_rows(context, P, g)))
    ex = [it for it in (P.items.get(i) for i in (g.p.get('excl') or [])) if it is not None and it.type == 'group']
    lst = [[enum('guide_excl_mode', 'Mode')]]
    if not ex:
        lst.append([text('None: the guide shapes every lock it crosses.' if g.p.get('exclMode') != 'only' else 'None: an empty Only list shapes every lock.', 'BLANK1')])
    for it in ex:
        lst.append([('label', it.name, 'OUTLINER_COLLECTION'), ('op', 'hairmesh.guide_excl', '', 'X', {'group': it.id, 'remove': True}, {'list_text': f'Remove {it.name}'})])
    cand = GT.exclusionCandidates(P, g)
    lst.append([('menu', 'HAIRMESH_MT_guide_excl', 'Add a Group (Only)' if g.p.get('exclMode') == 'only' else 'Exclude a Group', 'ADD', {'active': bool(cand)})])   # (dimmed with the reason in the block's "i" when no group crosses it)
    rows.append(block('guide_groups', 'Groups', 'OUTLINER_COLLECTION', lst, False, None if cand else 'No other group holds curves the guide crosses.'))
    return rows


def ring_rows(context, P, ring, room):
    """4.2c, item 5 (ours): the sheet of a disc's ring selected (a click on it): which ring of which disc and how to
    move it (the "?"). No selection buttons: the disc is in the pie (derived), the other ring a click"""
    from .tool import gizmo_block
    d = P.items.get(ring.ring)
    rs = discRings(P, d) if d is not None else []
    outer = bool(rs) and rs[0] is ring
    rows = [[('label', f'{"Outer" if outer else "Inner"} Ring of {d.name if d is not None else "a disc"}', 'MESH_CIRCLE')]]
    rows.append(gizmo_block('ring_transform', context, context.scene.hairmesh, True))
    return rows


def guide_pie_items(context, P, g):
    """Lot 4.3, item 1 (ours: the original's pie has no guide items): the slots of the Shift+RMB pie with a guide the
    last selected item and no curve (with curves: theirs, Select Crossed Curves in its NW slot) — Delete Guide in S; a line: Select Crossed Curves (8941, a selection with no click of its own);
    a disc: Reset Deformations, gone while it has nothing to reset (a ring is selected by a click on it); its sliders in the N box; the HairMesh Menu"""
    from .tool import MENU_OP, gizmo_slot, pie_box
    E = ('empty',)
    delete = ('op', 'hairmesh.guide_delete', 'Delete Guide', 'X', {'item': g.id})
    if g.kind == 'disc':
        reset = ('op', 'hairmesh.disc_reset', 'Reset Deformations', 'LOOP_BACK', {}) if bpy.ops.hairmesh.disc_reset.poll() else E
        return [E, E, delete, pie_box(gprop('r_out', 'Outer Radius'), gprop('r_in', 'Inner Radius')), reset, MENU_OP, E, gizmo_slot(context, P)]
    return [E, ('op', 'hairmesh.guide_select_crossed', 'Select Crossed Curves', 'RESTRICT_SELECT_OFF', {}), delete,
            pie_box(gprop('radius', 'Influence Radius'), gprop('density', 'Loop Density')), E, MENU_OP, E, gizmo_slot(context, P)]


def selected_ring(P, sel):
    """the ring the selection is (the last selected item a disc's ring, with its anchors the selected ones), or None"""
    if not sel.items or not sel.anchors:
        return None
    c = P.items.get(sel.items[len(sel.items) - 1])
    if c is None or c.type != 'curve' or not c.ring:
        return None
    mine = {a.id for c2 in (P.items.get(i) for i in sel.items) if c2 is not None and c2.type == 'curve' and c2.ring for a in c2.anchors}
    return c if set(sel.anchors) <= mine else None


def guides_rows(context, room):
    """the Guides' tool panel (renderToolPanel 9015-9025) as rows: the three modes, + Create (NURBS Disc), then the
    selected guide's sheet, or the settings for new guides; the help of the mode (HELP) is the "?" of the D's tab column
    (item 9 of the lot after 4.8: tool.how_to)"""
    from .tool import BOX_PAD, block, text
    hm = context.scene.hairmesh
    rows = [[('op', 'hairmesh.set_guide_mode', label, icon, {'mode': key}, {'depress': hm.guide_mode == key}) for key, label, _d, icon, _n in GUIDE_MODES]]
    if hm.guide_mode == 'DISC':
        rows.append([('op', 'hairmesh.disc_create', 'Create NURBS Disc', 'ADD', {})])
    root, P, sel = select.current(context)
    if P is None:
        return rows
    g = selected_guide(P, sel)
    if g is None:
        rows.append(block('guide_defaults', 'Settings for New Guides', 'PREFERENCES',
                          [[text('Select a guide to edit it; these apply to the guides made next.', 'BLANK1')]] + guide_settings_rows(context, P, None)))
    return rows


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.hairmesh_guide = bpy.props.PointerProperty(type=HairMeshGuideProps)
    for h in _HANDLERS:
        getattr(bpy.app.handlers, h).append(_reset)


def unregister():
    cancel_pencil()
    for h in _HANDLERS:
        lst = getattr(bpy.app.handlers, h)
        if _reset in lst:
            lst.remove(_reset)
    del bpy.types.WindowManager.hairmesh_guide
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


from bpy.app.handlers import persistent  # noqa: E402


@persistent
def _reset(*_a):
    """an undo, a redo, a file loaded: the loop being drawn goes"""
    cancel_pencil()


_HANDLERS = ('undo_post', 'redo_post', 'load_post')
CLASSES = (HairMeshGuideProps, HAIRMESH_OT_set_guide_mode, HAIRMESH_OT_guide_tool, HAIRMESH_OT_guide_stroke, HAIRMESH_OT_place_disc,
           HAIRMESH_OT_disc_create, HAIRMESH_OT_guide_pencil, HAIRMESH_OT_guide_pencil_finish, HAIRMESH_OT_guide_pencil_cancel, HAIRMESH_OT_pencil_disc, HAIRMESH_OT_disc_reset, HAIRMESH_OT_disc_select_ring, HAIRMESH_OT_disc_of_ring,
           HAIRMESH_OT_guide_delete, HAIRMESH_OT_guide_select_crossed, HAIRMESH_OT_guide_excl, HAIRMESH_MT_guide_excl)
