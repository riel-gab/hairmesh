# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""The RMB line over empty space inside Blender (1.0.2: the press 7582-7583, the stroke 7776-7781, the release
7823-7839): with the tool active, outside Draw and the Scissors, RMB dragged from where no curve is draws a line;
on the release an anchor goes in wherever it crosses a curve — the selected curves, or with none selected every shown
one — the shape kept, and the new anchors become the anchor selection (core/scissors.anchorLine).

RMB is Blender's too (the context menu, on the PRESS): a tool-keymap item on RIGHTMOUSE CLICK_DRAG never runs while
the press opens a menu (checked with real events, tests/output/1.1f/U2-exp-rmb.json), so hairmesh.anchor_line takes
the press itself, when nothing of the HairMesh is under the cursor, and waits: past the drag threshold it is a line,
let go before it is a click and the add-on opens what the user's keyconfig binds to RMB in the viewport (the Object
context menu by default) — the menu opens on the release instead of the press. The keys are core/gestures.LineGesture.
Its non-modal form (the stroke, the view's matrix, the region's size) is for the tests and the stress test."""
import traceback

import bpy
from bpy.props import FloatVectorProperty, IntVectorProperty

from ..core import curves as CM, edit, gestures as GS, scissors as SC
from ..core.selection import Selection
from . import draw as drawmod, errorlog, live, owner, pick, select, session, slow
from .ops_scissors import CLICK_PX, matrix_projector
from .ops_select import PICK_CURVE_PX, View, drag_threshold
from .ops_split import shown_curves

LINE_KEYS = ((('RMB Release',), 'Add the Anchors'), (('Esc',), 'Cancel'))
HEADER = 'HairMesh Anchor Line'
CONTEXT_KEYMAPS = ('Object Mode', '3D View')                       # where Blender's RMB of the viewport lives (Object Mode first: it is consulted first)


def takes_rmb(subtool):
    """the sub-tools the line works in: all but Draw and the Scissors (7573, 7568: their RMB is their own)"""
    return GS.line_starts(subtool)


def apply_line(context, root, P, sel, x0, y0, x1, y1, project, curves, ui, report):
    """the release (7823-7839): {'FINISHED'} with the anchors in (selected: S.asel = made, S.sel stays), saved, their
    locks rebuilt; a line that crosses nothing says so and changes nothing — {'CANCELLED'}; a click: nothing at all"""
    with slow.part('curves settled before the line'):
        CM.rebuild_dirty(P)                                         # (the samples a frame would have made: scissorHits reads them)
    shown = {c.id for c in curves}
    with slow.part('the anchors inserted along the line (anchorLine)') as box:
        res = SC.anchorLine(P, x0, y0, x1, y1, select.curves_of(P, sel), lambda c: c.id in shown, project, ui)
        box[0] = f"{len(res['made'])} anchors" if res and 'made' in res else None
    if res is None:
        return {'CANCELLED'}
    if not res['changed'] or not res['made']:                       # (ours, lot 1.2a, decision 12: every crossing skipped — "0 anchors added" — is no undo step; the original commits one, 7836)
        report({'INFO'}, res['toast'])
        return {'CANCELLED'}
    made = res['made']
    select.store(context.scene.hairmesh, Selection(list(sel.items), [a.id for a in made]))
    from . import tool
    tool.show_selection(context)                                    # (lot 1.2c, item 14: the new anchors' sheet in sight under the sub-tool's page)
    with slow.part('the project saved'):
        session.save_project(root, P)                               # (commit, 8831; every crossing skipped returned above: ours, decision 12)
    with slow.part('the locks of the curves touched (live.touch)') as box:
        touched = list(dict.fromkeys(h.id for a in made for h in edit.curvesWith(P, a)))
        live.touch(root, touched)                                   # S.surfDirty (7836)
        box[0] = f'{len(touched)} curves'
    live.update(context, root)                                      # (a pending conversion follows: regenConversion; its own parts)
    select.redraw(context)
    report({'INFO'}, res['toast'])
    return {'FINISHED'}


def context_menu_item(context, kc=None):
    """the item of the user's keyconfig a plain RMB click would have run in the viewport in Object Mode (the add-on's
    own aside): gestures.context_item over the RIGHTMOUSE items of 'Object Mode' and '3D View' as keyconfigs.user
    lists them (the running order) — a PRESS item, else a RELEASE one, else a CLICK one; or None (then nothing opens)"""
    kc = kc if kc is not None else context.window_manager.keyconfigs.user
    found = []
    for name in CONTEXT_KEYMAPS:
        km = kc.keymaps.get(name)
        found.append((name, [k for k in (km.keymap_items if km is not None else ()) if k.type == 'RIGHTMOUSE']))
    at = GS.context_item([(name, [{'idname': k.idname, 'value': k.value, 'active': k.active, 'any': k.any, 'shift': k.shift, 'ctrl': k.ctrl,
                                    'alt': k.alt, 'oskey': k.oskey} for k in items]) for name, items in found])
    return None if at is None else found[at[0]][1][at[1]]


def open_context_menu(context):
    """what Blender's RMB would have opened at the press (the click of the line that never was): the operator of
    context_menu_item, with the properties set on the item. True when it ran"""
    kmi = context_menu_item(context)
    if kmi is None:
        return False
    mod, fn = kmi.idname.split('.', 1)
    op = getattr(getattr(bpy.ops, mod), fn)
    props = {}
    if kmi.properties is not None:
        for p in kmi.properties.bl_rna.properties:
            if p.identifier != 'rna_type' and kmi.properties.is_property_set(p.identifier):
                props[p.identifier] = getattr(kmi.properties, p.identifier)
    try:
        op('INVOKE_DEFAULT', **props)
    except Exception:
        errorlog.record(f'anchor line: the context menu ({kmi.idname})')
        return False
    return True


class HAIRMESH_OT_anchor_line(bpy.types.Operator):
    """Drag a line with the right button from empty space: an anchor goes in wherever it crosses a curve (the selected curves, or every shown one), the shape kept. A click still opens the context menu"""
    bl_idname = 'hairmesh.anchor_line'
    bl_label = 'Insert HairMesh Anchors Along a Line'
    bl_options = {'REGISTER', 'UNDO'}

    stroke: FloatVectorProperty(name='Stroke', description='Non-modal form: the line in region pixels (x0, y0, x1, y1)', size=4, options={'HIDDEN', 'SKIP_SAVE'})
    view_matrix: FloatVectorProperty(name='View', description="Non-modal form: the view's perspective matrix (world to clip, row by row) the line was drawn in", size=16, options={'HIDDEN', 'SKIP_SAVE'})
    region_size: IntVectorProperty(name='Region', description='Non-modal form: the width and height of the region the line was drawn in', size=2, default=(1, 1), options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        hm = context.scene.hairmesh
        mods = dict(shift=event.shift, ctrl=event.ctrl, alt=event.alt, oskey=event.oskey)
        if not GS.line_starts(hm.subtool, **mods):
            return {'PASS_THROUGH'}                                 # (Draw, the Scissors; Shift + RMB: the pie; Ctrl + RMB: a brush's drag)
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        x, y = event.mouse_region_x, event.mouse_region_y
        CM.rebuild_dirty(v.P)
        if not GS.line_starts(hm.subtool, **mods, curve_under=pick.pick_curve(v.curves(), v.project, x, y, v.px(PICK_CURVE_PX)) is not None):
            return {'PASS_THROUGH'}                                 # (a curve under the cursor: RMB inserts an anchor there, 7574-7580)
        self.view = v
        self.g = GS.LineGesture(x, y, max(drag_threshold(context), v.px(CLICK_PX)))   # (past the core's 5 px too: a line the release keeps)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        drawmod.overlay = None
        if context.area is not None:
            live.header(context.area, None)
            context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)                                          # (Blender ends the modal itself: a window closed, a file loaded)

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            errorlog.record('anchor line: the modal')
            traceback.print_exc()
            self._end(context)
            return {'CANCELLED'}

    def _modal(self, context, event):
        x, y = event.mouse_region_x, event.mouse_region_y
        act = self.g.key(event.type, event.value, x, y)
        if act == 'line':
            drawmod.overlay = ('aline', self.g.x0, self.g.y0, x, y)
            live.header(context.area, HEADER, LINE_KEYS)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if act == 'menu':
            self._end(context)
            open_context_menu(context)
            return {'CANCELLED'}                                    # (nothing changed: no undo step)
        if act == 'apply':
            self._end(context)
            root, P, sel = select.current(context)
            if root is None:
                return {'CANCELLED'}
            project = pick.region_projector(context.region, context.region_data, root.matrix_world)   # (the view as it is now: the line may have been navigated through)
            return apply_line(context, root, P, sel, self.g.x0, self.g.y0, x, y, project, self.view.curves(), self.view.ui, self.report)
        if act == 'cancel':
            self._end(context)
            return {'CANCELLED'}
        if act == 'abort':
            self._end(context)
            return {'CANCELLED', 'PASS_THROUGH'}                    # (the key goes on: Ctrl+Z, Tab, a click)
        if act == 'pass':
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root, P, sel = select.current(context)
        if root is None or not any(self.view_matrix):
            return {'CANCELLED'}
        project = matrix_projector(list(self.view_matrix), tuple(self.region_size), root.matrix_world)
        x0, y0, x1, y1 = tuple(self.stroke)
        return apply_line(context, root, P, sel, x0, y0, x1, y1, project, shown_curves(P, context.scene.hairmesh), 1.0, self.report)


CLASSES = (HAIRMESH_OT_anchor_line,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    drawmod.overlay = None
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
