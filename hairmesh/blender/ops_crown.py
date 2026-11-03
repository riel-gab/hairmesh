# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Phase 4.3 inside Blender: the Crown sub-tool (the original's crown tool, hair-modeler.clean.html:5839-5842,
6037-6040, 7345-7841, 8960-8968), its panel (the Crown block of the D's Selection tab and of the N panel), the crown's
pie, and the connectors' gestures that live in the Select tool's drag (ops_select: Ctrl + drag slides, Ctrl + click
detaches, the Attach pie after a release near a connector).

  Crown       a click on the head puts the whorl there (createCrown); Create fits a crown on the head's top; an anchor
              is dragged as in Select. New crowns are Symmetric when the D says so (Scene.hairmesh.crown_sym).
  The crown   Rings, Whorl (WindowManager properties through deferred.py), + Crown Curve, Symmetric (the side pie,
              askCrownSym 7799), Mirror Again; the parting line, the lower connector and the crown curves are clicked
              in Select (curves) or picked from the pie (Shift + RMB: a selection no click gives at once).

The data part is core/crowntool.py (bit for bit with the original: tests/test_crowntool_parity.py); its frame step
(crownSymSync, updateAttachments) runs at the end of every operator that changes the model (session.save_project,
D4.8) and on every move of a drag (live.schedule). Every operator has a form that needs no viewport."""
from math import hypot

import bpy
from bpy.props import EnumProperty, FloatVectorProperty, IntProperty
from bpy.types import PropertyGroup

from ..core import crowntool as CT, curves as CM, edit, groups as G
from ..core.crown import crownCurves
from ..core.selection import Selection
from ..core.vec3 import V3
from . import deferred, live, owner, pick, select, session
from .ops_draw import NAV_EVENTS
from .ops_select import PICK_ANCHOR_PX, View

KEYS = ((('LMB',), 'Release on the Head: Create the Crown'), (('Esc',), 'Cancel'))
CLICK_PX = 5                                                        # crownPlace: a drag longer than this places nothing (6038)
HELP = '\n'.join(('Click on the head where the whorl (the back end of the parting) should be: a crown is built there from the reference.',
                  'Create: fit it on the top of the head.',
                  'Crown curves run from the lower connector to the parting line (both ends attached).',
                  "Drag a lock curve's end onto a connector (they all show while an end is dragged, a green dot where it would attach): the Attach pie.",
                  'Ctrl + drag: slide an attached anchor along its connector. Ctrl + click: detach it (also in the anchor pie).',
                  'Click them in Select (1), or Shift + right click (the pie) with the crown selected: the parting line, the lower connector, the crown curves.',
                  'Rings 0: taken from the loop step.',
                  'X (it asks first) or Delete: delete the selected crown.'))
ATTACH = {'root': None, 'anchor': None, 'conn': None}               # the anchor a release left near a connector (checkAttach), for the Attach pie


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def selected_crown(P, sel):
    """the crown the panel shows: the last selected crown (renderToolPanel 8967)"""
    for iid in reversed(sel.items):
        it = P.items.get(iid)
        if it is not None and it.type == 'crown':
            return it
    return None


def finish(context, root, P, sel, report, msg, touch=None):
    """the end of a crown edit: the selection, the panels (onSelChange), one undo step (the frame's sync inside
    save_project, D4.8), the preview"""
    select.store(context.scene.hairmesh, sel)
    G.panelsRendered(P, sel.items[len(sel.items) - 1] if sel.items else None)
    CM.rebuild_dirty(P)
    session.save_project(root, P)
    live.touch(root, touch if touch is not None else crown_curve_ids(P))
    live.update(context, root)
    select.redraw(context)
    if msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def crown_curve_ids(P, k=None):
    """the curves of a crown's group and its connectors (S.surfDirty for a crown: the walls rooted on it follow)"""
    ks = [k] if k is not None else P.crowns()
    out = []
    for kk in ks:
        for c in P.curves():
            if (c.parent == kk.parent or c.id in (kk.part, kk.low)) and c.id not in out:
                out.append(c.id)
    return out


def make_crown(context, root, P, target, normal, report):
    hm = context.scene.hairmesh
    sel = select.load(hm).prune(P)
    CM.rebuild_dirty(P)
    k, msg = CT.createCrown(P, sel, target, normal, sym=hm.crown_sym, head_shown=pick.head_visible(root))
    return finish(context, root, P, sel, report, msg, touch=crown_curve_ids(P, k))


# ---------------- the sub-tool's click (crownPlace, 5842 / 6037-6040) ----------------

class HAIRMESH_OT_crown_tool(bpy.types.Operator):
    """Click on the head where the whorl (the back end of the parting) should be to build a crown there; an anchor is dragged as in Select"""
    bl_idname = 'hairmesh.crown_tool'
    bl_label = 'HairMesh Crown'
    bl_options = {'REGISTER', 'UNDO'}

    point: FloatVectorProperty(name='Point', description="Non-modal form: the clicked point on the head, in the model's space (the original's axes: Y up)", size=3, options={'HIDDEN', 'SKIP_SAVE'})
    normal: FloatVectorProperty(name='Normal', description="Non-modal form: the head's face normal there (model space)", size=3, default=(0.0, 1.0, 0.0), options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.mode == 'OBJECT' and session.find_root(context) is not None

    def invoke(self, context, event):
        hm = context.scene.hairmesh
        if hm.subtool != 'CROWN' or event.alt or event.oskey or event.ctrl:
            return {'PASS_THROUGH'}                                 # (Ctrl: hairmesh.select's paint)
        v = View.of(context)
        if v is None:
            return {'PASS_THROUGH'}
        if pick.pick_anchor(v.anchors(select.load(hm).prune(v.P)), v.project, event.mouse_region_x, event.mouse_region_y, v.px(PICK_ANCHOR_PX)) is not None:
            return {'PASS_THROUGH'}                                 # (an anchor is hairmesh.select's, which Blender tries after this item: keymaps.TOOL_ITEMS)
        self.view = v; self.x0, self.y0 = event.mouse_region_x, event.mouse_region_y
        context.window_manager.modal_handler_add(self)
        live.header(context.area, 'HairMesh Crown: Place Whorl', KEYS)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        live.header(context.area, None)

    def cancel(self, context):
        self._end(context)

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end(context)
            x, y = event.mouse_region_x, event.mouse_region_y
            if hypot(x - self.x0, y - self.y0) > self.view.px(CLICK_PX):
                return {'CANCELLED'}                                # (6038: a drag places nothing)
            v = self.view; root = v.root; P = v.P
            hit = None
            if P.head is not None and P.head.visible and pick.head_visible(root):
                hit = edit.pickHead(P, pick.model_ray(root, context.region, context.region_data, (x, y)))
            if hit is None:
                self.report({'INFO'}, CT.CLICK_HEAD)
                return {'CANCELLED'}
            return make_crown(context, root, P, hit['point'].clone(), hit['normal'].clone().normalize(), self.report)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type in NAV_EVENTS:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root, P, _sel = select.current(context)
        return make_crown(context, root, P, V3(*self.point), V3(*self.normal), self.report)


class HAIRMESH_OT_crown_create(bpy.types.Operator):
    """Create a crown fitted on the top of the head (the reference's crown, scaled to the head's width); symmetric when "Symmetric" is on"""
    bl_idname = 'hairmesh.crown_create'
    bl_label = 'Create Crown'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, _sel = select.current(context)
        return make_crown(context, root, P, None, None, self.report)


def _crown_poll(cls, context):
    if not _poll(context):
        return False
    _r, P, sel = select.current(context)
    if P is None or selected_crown(P, sel) is None:
        cls.poll_message_set('Select a crown (Crown, 5: click on the head or Create)')
        return False
    return True


class HAIRMESH_OT_crown_add_curve(bpy.types.Operator):
    """Add a crown curve in the middle of the widest gap between the crown curves, shaped like its neighbors"""
    bl_idname = 'hairmesh.crown_add_curve'
    bl_label = '+ Crown Curve'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _crown_poll(cls, context)

    def execute(self, context):
        root, P, sel = select.current(context)
        k = selected_crown(P, sel)
        CM.rebuild_dirty(P)
        c, msg = CT.addCrownCurve(P, sel, k)
        if c is None:
            self.report({'INFO'}, 'The crown needs two crown curves at least')
            return {'CANCELLED'}
        return finish(context, root, P, sel, self.report, msg, touch=crown_curve_ids(P, k))


class HAIRMESH_OT_crown_select(bpy.types.Operator):
    """Select a part of the crown: the parting line, the lower connector or the crown curves (the pie with the crown selected)"""
    bl_idname = 'hairmesh.crown_select'
    bl_label = 'Select a Part of the Crown'
    bl_options = {'REGISTER', 'UNDO'}

    what: EnumProperty(name='Part', items=(('PART', 'Parting Line', 'The parting line (the upper connector)'), ('LOW', 'Lower Connector', 'The lower connector'),
                                           ('CURVES', 'Crown Curves', 'The crown curves (from the lower connector to the parting line)')), default='PART')

    @classmethod
    def poll(cls, context):
        return _crown_poll(cls, context)

    @classmethod
    def description(cls, context, properties):
        return {'PART': 'Select the parting line', 'LOW': 'Select the lower connector', 'CURVES': 'Select the crown curves'}[properties.what]

    def execute(self, context):
        _root, P, sel = select.current(context)
        k = selected_crown(P, sel)
        ids = [k.part] if self.what == 'PART' else [k.low] if self.what == 'LOW' else [r['c'].id for r in crownCurves(P, k)]
        select.store(context.scene.hairmesh, Selection([i for i in ids if i in P.items], []))   # (8812: S.asel = []; S.sel = the ids)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_crown_symmetric(bpy.types.Operator):
    """Make the crown symmetric: this half is copied onto the other across the head's center (X); then moving an anchor on either side moves its pair"""
    bl_idname = 'hairmesh.crown_symmetric'
    bl_label = 'Make the Crown Symmetric'
    bl_options = {'REGISTER', 'UNDO'}

    master: IntProperty(name='Master Side', description="1: the character's left (+X) onto its right; -1: the right (-X) onto the left", default=1, min=-1, max=1)

    @classmethod
    def poll(cls, context):
        return _crown_poll(cls, context)

    def execute(self, context):
        root, P, sel = select.current(context)
        k = selected_crown(P, sel)
        CM.rebuild_dirty(P)
        msg = CT.crownMakeSymmetric(P, sel, k, -1 if self.master < 0 else 1)
        if msg == CT.NOTHING_TO_MIRROR:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        return finish(context, root, P, sel, self.report, msg, touch=crown_curve_ids(P, k))


class HAIRMESH_OT_crown_sym_ask(bpy.types.Operator):
    """Make the crown symmetric: choose which half is copied onto the other (a pie at the pointer); on already, turn it off, both halves free. Mirror Again: the pie again"""
    bl_idname = 'hairmesh.crown_sym_ask'
    bl_label = 'Crown Symmetry'
    bl_options = {'REGISTER', 'UNDO'}

    again: bpy.props.BoolProperty(name='Mirror Again', description='Copy one half onto the other again (e.g. after adding crown curves on one side)', default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _crown_poll(cls, context)

    @classmethod
    def description(cls, context, properties):
        if properties.again:
            return 'Copy one half onto the other again (e.g. after adding crown curves on one side)'
        return ("Mirror one half of the crown onto the other across the head's center (X); then moving an anchor on either side "
                "moves its pair, the parting line stays in the middle. On already: symmetry off")

    def invoke(self, context, event):
        _root, P, sel = select.current(context)
        k = selected_crown(P, sel)
        if not self.again and k.p.get('sym') and k.p['sym'].get('on'):
            return self.execute(context)
        bpy.ops.wm.call_menu_pie('INVOKE_DEFAULT', name=HAIRMESH_MT_crown_sym.bl_idname)
        return {'CANCELLED'}                                        # (the pie only opens: its item is the step)

    def execute(self, context):
        root, P, sel = select.current(context)
        k = selected_crown(P, sel)
        if self.again or not (k.p.get('sym') and k.p['sym'].get('on')):
            self.report({'INFO'}, 'Pick a side in the pie (Symmetric opens it)')
            return {'CANCELLED'}
        return finish(context, root, P, sel, self.report, CT.crownSymOff(P, k), touch=[])


class HAIRMESH_MT_crown_sym(bpy.types.Menu):
    """Choose which half of the crown is mirrored onto the other"""
    bl_idname = 'HAIRMESH_MT_crown_sym'
    bl_label = 'Make the Crown Symmetric'

    def draw(self, context):
        pie = self.layout.menu_pie()
        op = pie.operator('hairmesh.crown_symmetric', text='Left (+X) → Right (−X)', icon='MOD_MIRROR'); op.master = 1
        op = pie.operator('hairmesh.crown_symmetric', text='Right (−X) → Left (+X)', icon='MOD_MIRROR'); op.master = -1


# ---------------- the connectors (checkAttach's pie, detachFromConnector) ----------------

def ask_attach(root, a, conn):
    ATTACH.update(root=root.name, anchor=a.id, conn=conn.id)


class HAIRMESH_OT_crown_attach(bpy.types.Operator):
    """Attach the curve's end to the connector: it becomes an anchor of the connector (drag it to reshape the connector, Ctrl + drag slides it along, Ctrl + click detaches)"""
    bl_idname = 'hairmesh.crown_attach'
    bl_label = 'Attach to the Connector'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    connector: IntProperty(name='Connector', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        aid = self.anchor if self.anchor >= 0 else ATTACH['anchor']; cid = self.connector if self.connector >= 0 else ATTACH['conn']
        ATTACH.update(root=None, anchor=None, conn=None)
        a = P.anchors.get(aid) if aid is not None else None; conn = P.items.get(cid) if cid is not None else None
        if a is None or conn is None or conn.type != 'curve' or not conn.conn:
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        before = [c.id for c in edit.curvesWith(P, a)]
        msg = CT.connectToConnector(P, a, conn)
        return finish(context, root, P, sel, self.report, msg, touch=before + [conn.id])


class HAIRMESH_OT_attach_dismiss(bpy.types.Operator):
    """Leave the anchor where it is, not attached to the connector"""
    bl_idname = 'hairmesh.attach_dismiss'
    bl_label = "Don't Attach"
    bl_options = {'INTERNAL'}                                       # (nothing changes: no undo step)

    def execute(self, context):
        ATTACH.update(root=None, anchor=None, conn=None)
        return {'FINISHED'}


class HAIRMESH_MT_attach_pie(bpy.types.Menu):
    """Attach the dragged curve end to this crown connector"""
    bl_idname = 'HAIRMESH_MT_attach_pie'
    bl_label = 'Near a Crown Connector'

    def draw(self, context):
        pie = self.layout.menu_pie()
        root = session.find_root(context)
        P = session.get_project(root) if root is not None else None
        conn = P.items.get(ATTACH['conn']) if P is not None and ATTACH['conn'] is not None else None
        if conn is None:
            pie.label(text='The connector is gone', icon='INFO')
            return
        pie.operator('hairmesh.attach_dismiss', text="Don't Attach", icon='X')   # (G5: W no, E yes)
        op = pie.operator('hairmesh.crown_attach', text=f'Attach to {conn.name}', icon='CONSTRAINT')
        op.anchor = ATTACH['anchor']; op.connector = conn.id


class HAIRMESH_OT_crown_detach(bpy.types.Operator):
    """Detach from the connector: the curve gets an anchor of its own at the same spot (the connector keeps its shape); an attached anchor just lets go. A crown curve stays on its connector"""
    bl_idname = 'hairmesh.crown_detach'
    bl_label = 'Detach from the Connector'
    bl_options = {'REGISTER', 'UNDO'}

    anchor: IntProperty(name='Anchor', description='The anchor (default: the last selected one)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        aid = self.anchor if self.anchor >= 0 else (sel.anchors[len(sel.anchors) - 1] if sel.anchors else None)
        a = P.anchors.get(aid) if aid is not None else None
        if a is None:
            self.report({'INFO'}, 'Select an anchor on a connector first')
            return {'CANCELLED'}
        CM.rebuild_dirty(P)
        before = [c.id for c in edit.curvesWith(P, a)]
        msg = CT.detachFromConnector(P, sel, a)
        if msg is None:
            self.report({'INFO'}, CT.STAYS)                         # (7907)
            return {'CANCELLED'}
        return finish(context, root, P, sel, self.report, msg, touch=before)


def detach_offered(P, a):
    """the anchor pie's "⛓ Detach from the connector" (7907): attached, or on a connector and on a curve that is not one"""
    cs = edit.curvesWith(P, a)
    return a.att is not None or (any(c.conn for c in cs) and any(not c.conn and not c.ring for c in cs))


# ---------------- the panel: Rings and Whorl (crownProps 7805-7822) ----------------

def _crown():
    root, P, sel = select.current(bpy.context)
    return root, P, (selected_crown(P, sel) if P is not None else None)


def _get_rings(self):
    _r, _P, k = _crown()
    return int(k.p.get('rings') or 0) if k is not None else 0


def _set_rings(self, v):
    root, P, k = _crown()
    if k is None or int(k.p.get('rings') or 0) == int(v):
        return
    CT.setCrownParam(k, 'rings', int(v))
    deferred.changed(bpy.context, root)                             # (S.surfDirty, 7810: no lock reads it — the crown's preview follows by its key, live.crown_previews)


def _get_whorl(self):
    _r, _P, k = _crown()
    return 1 if k is not None and k.p.get('whorl') == 'seam' else 0


def _set_whorl(self, v):
    root, P, k = _crown()
    w = 'seam' if v == 1 else 'merge'
    if k is None or (k.p.get('whorl') == 'seam') == (w == 'seam'):
        return
    CT.setCrownParam(k, 'whorl', w)
    deferred.changed(bpy.context, root)


class HairMeshCrownProps(PropertyGroup):
    """The crown's panel (crownProps 7805): on the WindowManager, so the rebuild and the undo step follow the slider when
    it stops or is let go (deferred.py). Views on the model, nothing is stored here."""
    crown_rings: IntProperty(name='Rings', description='Rings between the lower connector and the parting line (0: from the loop step)', min=0, max=40, get=_get_rings, set=_set_rings)
    crown_whorl: EnumProperty(name='Whorl', description='Where many loops meet at the whorl',
                              items=(('MERGE', 'Merge Loops', 'Neighboring loops merge in pairs below the whorl (a triangle each): at most 3 reach it', 0),
                                     ('SEAM', 'Seam on the Crown Curve', 'The loops next to the crown curve nearest the whorl end on that curve, herringbone-like', 1)),
                              get=_get_whorl, set=_set_whorl)


CROWN_ATTRS = frozenset(HairMeshCrownProps.__annotations__)


# ---------------- the D and the N: rows ----------------

def crown_rows(context, P, k, room):
    """The crown sheet (crownProps 7805-7841) by category: the crown (name, group; what crownData says), Rings and Whorl,
    + Crown Curve, Symmetric (the side pie) and Mirror Again. The parts are selected by a click (Select) or the pie."""
    from .tool import group_label, label_and, text, why_item
    sy = k.p.get('sym') if isinstance(k.p.get('sym'), dict) else None
    on = bool(sy and sy.get('on'))
    delete = ('op', 'hairmesh.delete_selected', 'Delete Crown', 'X', {'keep_shape': False})
    rows = [[('label', k.name, 'FORCE_VORTEX')], *label_and(group_label(P, k), delete, room), [text(CT.infoText(k), 'INFO')],
            [('prop', 'crown_rings', 'Rings'), ('op', 'hairmesh.crown_add_curve', '+ Crown Curve', 'ADD', {})], [('prop', 'crown_whorl', 'Whorl')]]
    sym = [('op', 'hairmesh.crown_sym_ask', 'Symmetric', 'MOD_MIRROR', {}, {'depress': on})]
    if on:
        sym += [('op', 'hairmesh.crown_sym_ask', 'Mirror Again', 'MOD_MIRROR', {'again': True}),
                why_item(f'Symmetric: {CT.sym_text(sy["master"])}, plane X = {P.S.center.x:.2f}. Crown curves added later are not mirrored: Mirror Again.')]
    rows.append(sym)
    return rows


def crown_tool_rows(context, room):
    """the Crown's tool panel (renderToolPanel 8960-8968) as rows: Symmetric (for new crowns), Create, then the selected
    crown's sheet; its help (HELP) is the "?" of the D's tab column (item 9 of the lot after 4.8: tool.how_to)"""
    rows = [[('toggle', 'crown_sym', 'Symmetric (New Crowns)', 'MOD_MIRROR')],
            [('op', 'hairmesh.crown_create', 'Create Crown', 'ADD', {})]]
    return rows


def crown_pie_items(context, P, k):
    """the Shift+RMB pie with a crown the last selected item (ours: the original's panel buttons, 8812-8814, are
    selections — they live here): the parting line, the lower connector, the crown curves; Delete Crown; + Crown Curve;
    Symmetric; Rings in the N box; the HairMesh Menu"""
    from .tool import MENU_OP, pie_box
    sy = k.p.get('sym') if isinstance(k.p.get('sym'), dict) else None
    on = bool(sy and sy.get('on'))
    return [('op', 'hairmesh.crown_select', 'Parting Line', 'CURVE_DATA', {'what': 'PART'}), ('op', 'hairmesh.crown_select', 'Lower Connector', 'CURVE_DATA', {'what': 'LOW'}),
            ('op', 'hairmesh.delete_selected', 'Delete Crown', 'X', {'keep_shape': False}), pie_box(('prop', 'crown_rings', 'Rings')),
            ('op', 'hairmesh.crown_add_curve', '+ Crown Curve', 'ADD', {}), MENU_OP,
            ('op', 'hairmesh.crown_sym_ask', 'Symmetry Off' if on else 'Symmetric', 'MOD_MIRROR', {}),
            ('op', 'hairmesh.crown_select', 'Crown Curves', 'CURVE_DATA', {'what': 'CURVES'})]


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.hairmesh_crown = bpy.props.PointerProperty(type=HairMeshCrownProps)


def unregister():
    ATTACH.update(root=None, anchor=None, conn=None)
    del bpy.types.WindowManager.hairmesh_crown
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


CLASSES = (HAIRMESH_OT_attach_dismiss, HairMeshCrownProps, HAIRMESH_OT_crown_tool, HAIRMESH_OT_crown_create, HAIRMESH_OT_crown_add_curve, HAIRMESH_OT_crown_select,
           HAIRMESH_OT_crown_symmetric, HAIRMESH_OT_crown_sym_ask, HAIRMESH_MT_crown_sym, HAIRMESH_OT_crown_attach, HAIRMESH_MT_attach_pie,
           HAIRMESH_OT_crown_detach)
