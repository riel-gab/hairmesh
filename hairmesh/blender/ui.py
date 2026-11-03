# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The N panel (sidebar tab "HairMesh"), the same tabs as the HairMesh Menu (U5 / U6, 15/10/2026): Outliner (the
HairMesh Outliner, ops_outliner), Selection (the properties of the selection: anchors first, else the last selected
item — curve, wall, group — as the original's renderProps, 8627; sub-panels Anchors / Curve / Wall / Group), Conversion,
Project (with the tool switch), Options, Shortcuts. The rows come from the same descriptions as the pop-up (tool.py)."""
import bpy

from . import fit, icons, keymaps, select, session, tool
from ..core.hairmesh_io import ProjectFormatError
from .props import SUBTOOLS


def room(context, sub=True):
    """the units the rows of a panel get: the sidebar's width less its margins, scroll bar and tabs (a sub-panel a
    little less); the pop-up's width without a region (-b). Part 2b of the lot ux7: the rows wrap and stack by it."""
    region = getattr(context, 'region', None)
    if region is None or region.width <= 0:
        return tool.ROOM
    from .ops_outliner import SIDEBAR_PAD
    return max(6.0, region.width / (fit.UNIT_PX * fit.scale()) - SIDEBAR_PAD - (0.6 if sub else 0.0))


def _rows(layout, context, rows, sub=True):
    tool.draw_rows(layout, context.scene.hairmesh, rows, room(context, sub))


class HAIRMESH_PT_selection(bpy.types.Panel):
    """The Selection tab: what is selected, in one line; the sub-panels hold the properties."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Selection'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return session.find_root(context) is not None

    def draw(self, context):
        layout = self.layout
        root, P, sel = select.current(context)
        if P is None:
            layout.label(text='Project text unreadable', icon='ERROR')
            return
        anchors = select.anchors_of(P, sel)
        it = select.last_item(P, sel)
        if anchors:
            layout.label(text=f'Anchors: {len(anchors)}', icon='DECORATE_KEYFRAME')
        elif it is not None:
            icon = {'curve': 'CURVE_DATA', 'surface': 'MOD_LATTICE', 'group': 'OUTLINER_COLLECTION'}.get(it.type, 'DOT')
            n = len(sel.items)
            _rows(layout, context, [[tool.text(it.name + (f'  (+{n - 1} more)' if n > 1 else ''), icon)]], sub=False)
        else:
            _rows(layout, context, tool.group_rows(context, P, room(context, sub=False))[0], sub=False)   # Nothing selected, New Group, the curves hidden by Auto (U2)


class HAIRMESH_PT_scissors(bpy.types.Panel):
    """The Scissors panel: its two modes, what is cut, Cut / Cancel while a wall is
    being cut — shown with the Scissors sub-tool"""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Scissors'
    bl_parent_id = 'HAIRMESH_PT_selection'
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return session.find_root(context) is not None and context.scene.hairmesh.subtool == 'SCISSORS'

    def draw_header(self, context):
        """the How To of the mode as a "?" (item 9 of the lot after 4.8: its block left the rows; tool.how_to)"""
        self.layout.operator(tool.HAIRMESH_OT_how_to.bl_idname, text='', icon='QUESTION', emboss=False)

    def draw(self, context):
        from .ops_scissors import scissors_rows
        _rows(self.layout, context, scissors_rows(context))


class HAIRMESH_PT_anchors(bpy.types.Panel):
    """The anchor panel: shown while anchors are selected."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Anchors'
    bl_parent_id = 'HAIRMESH_PT_selection'

    @classmethod
    def poll(cls, context):
        hm = context.scene.hairmesh
        return hm.sel_anchors not in ('', '[]') and session.find_root(context) is not None

    def draw(self, context):
        layout = self.layout
        root, P, sel = select.current(context)
        anchors = select.anchors_of(P, sel) if P is not None else []
        if not anchors:
            layout.label(text='The selected anchors are gone', icon='INFO')
            return
        _rows(layout, context, tool.anchor_rows(context, P, anchors))   # the same sheet as the HairMesh Menu (part 2 of the lot of 16/10/2026)


def _last_is(context, type_):
    """the panels' poll: no anchors selected and the last selected item of this type (renderProps shows the last)"""
    hm = context.scene.hairmesh
    if hm.sel_anchors not in ('', '[]') or hm.sel_curves in ('', '[]') or session.find_root(context) is None:
        return False
    root, P, sel = select.current(context)
    it = select.last_item(P, sel) if P is not None else None
    return it is not None and it.type == type_


class HAIRMESH_PT_curve(bpy.types.Panel):
    """The curve panel: the last selected curve, while curves and no anchors are selected."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Curve'
    bl_parent_id = 'HAIRMESH_PT_selection'

    @classmethod
    def poll(cls, context):
        return _last_is(context, 'curve')

    def draw(self, context):
        layout = self.layout
        root, P, sel = select.current(context)
        cs = select.curves_of(P, sel) if P is not None else []
        if not cs:
            layout.label(text='The selected curves are gone', icon='INFO')
            return
        _rows(layout, context, tool.curve_rows(context, P, cs, room(context)))    # the same sheet as the HairMesh Menu


class HAIRMESH_PT_wall(bpy.types.Panel):
    """The wall panel: the last selected wall, while no anchors are selected."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Wall'
    bl_parent_id = 'HAIRMESH_PT_selection'

    @classmethod
    def poll(cls, context):
        return _last_is(context, 'surface')

    def draw(self, context):
        layout = self.layout
        root, P, sel = select.current(context)
        sf = select.last_wall(P, sel) if P is not None else None
        if sf is None:
            layout.label(text='The selected wall is gone', icon='INFO')
            return
        _rows(layout, context, tool.wall_rows(context, P, sf, room(context)))     # the same sheet as the HairMesh Menu


class HAIRMESH_PT_group(bpy.types.Panel):
    """The group panel of the selected group (the last selected item when it is a
    group, chosen in the HairMesh Outliner)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Group'
    bl_parent_id = 'HAIRMESH_PT_selection'

    @classmethod
    def poll(cls, context):
        return _last_is(context, 'group')

    def draw(self, context):
        layout = self.layout
        root, P, sel = select.current(context)
        if P is None:
            layout.label(text='Project text unreadable', icon='ERROR')
            return
        rows, title = tool.group_rows(context, P, room(context))
        _rows(layout, context, rows)


class HAIRMESH_PT_conversion(bpy.types.Panel):
    """The Conversion tab: the ticked groups, the sliders over them, the loop step, Convert."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Conversion'
    bl_order = 2
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return session.find_root(context) is not None

    def draw(self, context):
        layout = self.layout
        root, P, sel = select.current(context)
        if P is None:
            layout.label(text='Project text unreadable', icon='ERROR')
            return
        _rows(layout, context, tool.conversion_rows(context, P, room(context, sub=False)), sub=False)


class HAIRMESH_PT_project(bpy.types.Panel):
    """The Project tab: the tool switch, the sub-tools, the project of the scene, its head and counts, export /
    import / new."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Project'
    bl_order = 4                                                    # after Options (1a of the manual test of the lot ux5)

    def draw_header(self, context):
        self.layout.label(text='', **icons.icon_kw(tool.LOGO))     # the hair-in-a-ring logo

    def draw(self, context):
        layout = self.layout
        root = session.find_root(context)
        active = tool.is_active(context)
        row = layout.row()
        row.operator('hairmesh.toggle_tool', text='Tool: on' if active else 'Tool: off', icon='TOOL_SETTINGS', depress=active)
        row.popover(panel=tool.POPUP_ID, text='', **icons.icon_kw(tool.LOGO))   # the HairMesh Menu pop-up (D)
        if root is None:
            n = len(session.roots_in(context.scene))
            _rows(layout, context, [[tool.text('Select a project root' if n > 1 else 'No HairMesh project in this scene', 'INFO')]], sub=False)
            _rows(layout, context, tool.project_rows(context, None, None), sub=False)
            return
        hm = context.scene.hairmesh
        row = layout.row(align=True)
        for key, label, _desc, icon, _n in SUBTOOLS:                # buttons, not the expanded prop: they also switch the tool on
            op = row.operator('hairmesh.set_subtool', text=label, icon=icon, depress=active and hm.subtool == key)
            op.subtool = key
            op.activate = True
        try:
            P = session.get_project(root)
        except (ValueError, ProjectFormatError) as e:
            _rows(layout, context, [[tool.text(f'Project text unreadable: {e}', 'ERROR')]], sub=False)
            return
        rows = tool.project_rows(context, root, P)
        if P.head:
            rows.insert(2, [('label', f'Loop step: {P.settings.step if P.settings.step else P.S.headH / 24:.4g}', 'MOD_ARRAY')])
        _rows(layout, context, rows, sub=False)


class HAIRMESH_PT_options(bpy.types.Panel):
    """The Viewport tab: the live preview, Drag on Head, the x-ray, the guide lines' opacity."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Viewport'
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return session.find_root(context) is not None

    def draw(self, context):
        _rows(self.layout, context, tool.options_rows(context))


class HAIRMESH_PT_shortcuts(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'Shortcuts'
    bl_order = 5
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        rows = []
        for _km, kmi, (km_name, idname, event, props) in keymaps.user_items():
            if kmi is not None:
                key = kmi.to_string(); name = kmi.name; off = not kmi.active   # kmi.name: the operator's label, or the menu / panel label for wm.call_*
            else:
                key = keymaps.event_label(event); name = idname; off = True
            rows.append([tool.text(f'{key}: {name}' + ('  (off)' if off else ''), 'BLANK1' if km_name == keymaps.GLOBAL_KM else 'DOT')])   # (wrapped in a narrow sidebar: part 2b of the lot ux7)
        rows.append([tool.text('Edit them in the add-on preferences', 'PREFERENCES')])
        _rows(self.layout, context, rows, sub=False)


_CLASSES = (HAIRMESH_PT_selection, HAIRMESH_PT_scissors, HAIRMESH_PT_anchors, HAIRMESH_PT_curve, HAIRMESH_PT_wall, HAIRMESH_PT_group, HAIRMESH_PT_conversion,
            HAIRMESH_PT_options, HAIRMESH_PT_project, HAIRMESH_PT_shortcuts)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
