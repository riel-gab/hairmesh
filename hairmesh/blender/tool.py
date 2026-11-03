# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The HairMesh WorkSpaceTool (toolbar entry below HardOps / BoxCutter), the Shift+Alt+H toggle, the sub-tool
switch, and the contextual menus every action is reachable from:
  - the pie (Shift+RMB), a port of the original's openMainPie (7891): anchors selected -> anchor actions, curves ->
    curve actions, else the tools and the project;
  - the HairMesh Menu (D, as HardOps and BoxCutter open their main menu on D; the popover button of the tool header
    and of the N panel): a pop-up panel in the style of BoxCutter's Box Helper (HAIRMESH_PT_popup, opened with
    wm.call_panel keep_open) — since 15/10/2026 (U5 of docs/fase-3-ux-revisao.md) with a column of tab icons on the
    left and the active tab alone on the right: Selection (the properties of what is selected: the #props panel of
    the original), Modifiers (key 6), Conversion, Options, Project; the tab is remembered in the preferences; the same
    content as a plain list menu (HAIRMESH_MT_menu) in Object > HairMesh;
  - the sub-tool pie (Q, HAIRMESH_MT_subtools): Select / Draw / Scissors, Guides, Crown, the HairMesh Menu and its
    Modifiers tab.
Rule of the project (14/10/2026): nothing important lives only in the N panel; menus and pies come first, the N
panel is a complement. The pop-up and the Q pie are additions of this port (docs/features-adicionadas.md).

The menus are built from pure descriptions (pie_items, popup_tabs / popup_sections, subtool_pie_items; menu_items is
derived from the sections; the ACT table gives every action its one name, U6) that the draw functions interpret, so
the tests check the content without a window. The tool's own
keys live in its keymap and only work while it is active.

The toolbar icon ignores the workspace's "Filter Add-ons" (owner.py), like HardOps's and BoxCutter's: the tool
system has no notion of owner and register_tool is global, so the icon stays in every workspace (decided on
14/10/2026). Clicked in a workspace that filters the add-on out, the tool is active but inert: nothing is drawn
(draw.py), and its keymap does not fire because Blender stamped our owner id on it (WM_keymap_poll checks
BKE_workspace_owner_id_check), exactly as HardOps's keymap behaves. The header then says so, with the button that
ticks HairMesh in the workspace (owner.py; the N panel shows the same notice)."""
import os

import bpy
from bpy.props import BoolProperty, EnumProperty

from ..core import convert as CV, effects as E, groups as G
from . import fit, icons, keymaps, ops_groups, ops_walls, ops_weld, owner, prefs, props, select, session
from .props import SUBTOOLS

TOOL_ID = keymaps.TOOL_ID
POPUP_ID = 'HAIRMESH_PT_popup'                                      # the HairMesh Menu pop-up panel (D, the header popover)
POPUP_PROPS = {'name': POPUP_ID, 'keep_open': True}                 # wm.call_panel's properties: keep_open = it stays open when the mouse leaves it, until a click outside it or Esc (kept on purpose, 15/10/2026)
NAME_PAD = 1.2                                                      # the name of the sub-tool in the tool's header: its margins (subtool_name_units)
POPUP_UNITS_X = 22                                                  # its width, in UI units, the same in every tab (part 2a of the lot ux7: 16, and 24 for Conversion, made the menu change size between tabs; BoxCutter's helper uses 20)
TOP_ICON = 1.5                                                      # 4.4, item 4: the scale_x of an icon button of the top row (the sub-tools, the Outliner, Undo / Redo)
TOP_ICON_W = 2.0                                                    # what a sub-tool's button comes to with it, in units (measured at 1.0 and 1.25)
TAB_UNITS = 1.8                                                     # the column of tab icons
BODY_PAD = 0.5                                                      # the pop-up's margins around the active tab (measured: 13.75 units of tab at 16 wide)
BOX_PAD = 0.6                                                       # a block's box: its border and margins
ROOM = POPUP_UNITS_X - TAB_UNITS - BODY_PAD                         # the width a row of the active tab gets, in units
LIST_ROOM = 26.0                                                    # where a paragraph wraps in the list menu (Object > HairMesh: a menu widens to its longest row)
PIE_ROOM = 14.0                                                     # ... in a pie's box
OUTLINER_ID = 'HAIRMESH_PT_outliner'                                # the HairMesh Outliner pop-up panel (O; ops_outliner.py)
OUTLINER_PROPS = {'name': OUTLINER_ID, 'keep_open': True}
OUTLINER_OP = ('op', 'wm.call_panel', 'HairMesh Outliner', 'OUTLINER', dict(OUTLINER_PROPS))   # from a pie slot
MORE_TOOLS_MENU = ('menu', 'HAIRMESH_MT_more_tools', 'More Tools', 'THREE_DOTS')   # the Q pie's SE: what has no direction of its own
OUTLINER_OP_S = ('op', 'wm.call_panel', '', 'OUTLINER', dict(OUTLINER_PROPS), {'list_text': 'HairMesh Outliner'})   # the icon button of the pop-up's top row
_prev_tool = None                                                   # the tool active before Shift+Alt+H switched to ours


def active_tool_id(context, mode='OBJECT'):
    try:
        t = context.workspace.tools.from_space_view3d_mode(mode, create=False)
    except (AttributeError, TypeError):
        return None
    return t.idname if t is not None else None


def is_active(context):
    return active_tool_id(context) == TOOL_ID


def activate(context, report=None):
    """Switches to the HairMesh tool, remembering the tool in use so Shift+Alt+H comes back to it. Shared by the
    toggle, New Project and the sub-tool buttons of the N panel. Outside Object Mode it only warns (the mode is not
    changed). Called away from a 3D View (a menu, F3 in another editor) it uses the screen's first 3D View; without
    one (background mode) it does nothing. True when the tool is active afterwards."""
    if not owner.visible(context):
        return False
    if context.mode != 'OBJECT':
        if report is not None:
            report({'WARNING'}, 'The HairMesh tool works in Object Mode')
        return False
    if is_active(context):
        return True
    return bool(_in_view3d(context, _activate_here))


def _activate_here(context):
    global _prev_tool
    _prev_tool = active_tool_id(context)
    return bpy.ops.wm.tool_set_by_id(name=TOOL_ID) == {'FINISHED'}


def _in_view3d(context, fn):
    """fn(context) run in a 3D View (wm.tool_set_by_id wants one): the context's own area, else the screen's first
    3D View under an override; None when the screen has none (or there is no screen: background mode)."""
    area = context.area
    if area is not None and area.type == 'VIEW_3D':
        return fn(context)
    screen = context.screen
    area = next((a for a in screen.areas if a.type == 'VIEW_3D'), None) if screen is not None else None
    if area is None:
        return None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with context.temp_override(area=area, region=region):
        return fn(bpy.context)


def deactivate(context):
    """Leaves the HairMesh tool: back to the tool used before Shift+Alt+H, else Select Box. False when ours is not
    the active tool of this workspace."""
    if not is_active(context):
        return False
    name = _prev_tool if _prev_tool and _prev_tool != TOOL_ID else 'builtin.select_box'
    return bool(_in_view3d(context, lambda c: bpy.ops.wm.tool_set_by_id(name=name) == {'FINISHED'}))


class HAIRMESH_WT_tool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = TOOL_ID
    bl_label = keymaps.TOOL_LABEL
    bl_description = ('Model stylized hair: draw curves on the head, weld them, stretch walls between them and convert the locks '
                      'into quad meshes (Shift+Alt+H toggles the tool)')
    bl_icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons', 'hairmesh')   # icons/hairmesh.dat (tools/make_icon.py, traced from docs/img/icon-source.png)
    bl_widget = 'HAIRMESH_GGT_anchors'                               # 3.8b: the anchor gizmo (ops_gizmo.py)
    bl_keymap = keymaps.tool_keymap()

    def draw_settings(context, layout, tool):
        if not owner.visible(context):                              # the tool is active in a workspace that filters the add-on out
            layout.label(text=owner.NOTICE, icon='ERROR')
            layout.operator(owner.HAIRMESH_OT_show_in_workspace.bl_idname, text='Show HairMesh Here', icon='CHECKBOX_HLT')
            return
        hm = context.scene.hairmesh
        row = layout.row(align=True)
        row.popover(panel=POPUP_ID, text='', **icons.icon_kw(LOGO))   # the HairMesh Menu (the same pop-up as D)
        for key, _label, _d, icon, _n in SUBTOOLS:                  # (icons alone; the active one's name beside them, as in the D)
            row.prop_enum(hm, 'subtool', key, text='', icon=icon)
        name = layout.row()
        name.ui_units_x = subtool_name_units()
        name.label(text=subtool_name(hm))
        if hm.subtool == 'SELECT':                                  # 3.8b: the gizmo modes (dimmed when they cannot act: the tooltip says why)
            from .ops_gizmo import blocked_reason
            row = layout.row(align=True)
            for it in gizmo_items(hm, blocked_reason(context) is None):
                draw_item(row, hm, it)
        if hm.subtool == 'SCISSORS':                                # 3.8a: the Scissors' two modes (8974)
            row = layout.row(align=True)
            for k, label, icon in (('CURVE', 'Curve', 'CURVE_DATA'), ('SURFACE', 'Wall', 'SURFACE_NSURFACE')):
                row.operator('hairmesh.set_cut_mode', text=label, icon=icon, depress=hm.cut_mode == k).mode = k
        root = session.find_root(context)
        layout.separator(factor=0.5)
        if root is None:
            layout.operator('hairmesh.new_project', text='New Project...', icon='ADD')
            layout.operator('hairmesh.import_project', text='Import .hairmesh...', icon='IMPORT')   # open an existing project straight away (1d of the manual test of the lot ux5)
        else:
            layout.label(text=root.get('hairmesh_project', root.name), icon='OUTLINER_OB_EMPTY')
            head = session.head_object(root)
            layout.operator('hairmesh.set_head', text='Set Head' if head is None else f'Head: {head.name}', icon='MESH_MONKEY')
            try:
                P = session.get_project(root)
            except Exception:                                       # (an unreadable project text: the header still draws)
                P = None
            if P is None:
                layout.operator('hairmesh.convert', text='Convert', icon='PLAY')
                return
            # 3.7: Convert, and Confirm / Cancel while a preview is pending, Live / Baked once there are meshes (part 1 of
            # the lot ux7: dimmed when they would do nothing; the tooltip says why)
            from ..core import conversion as CONV
            c = convert_items(P, short=True)
            row = layout.row(align=True)
            items = [c['convert']] + ([c['confirm'], c['cancel']] if P.conv else [])
            for it in items:
                draw_item(row, hm, it)
            if CONV.meshes(P):
                row = layout.row(align=True)
                draw_item(row, hm, c['live']); draw_item(row, hm, c['baked'])


class HAIRMESH_OT_toggle_tool(bpy.types.Operator):
    """Switch to the HairMesh tool; when it is already active, back to the tool used before"""
    bl_idname = 'hairmesh.toggle_tool'
    bl_label = 'Toggle HairMesh Tool'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        if is_active(context):
            from .ops_guides import cancel_pencil
            cancel_pencil()                                         # (between its strokes no modal would see the tool go off)
            deactivate(context)
            return {'FINISHED'}
        return {'FINISHED'} if activate(context, self.report) else {'CANCELLED'}


def _brush_follows(context, was):
    from . import brush
    if context.scene.hairmesh.subtool != was:
        bpy.ops.ed.undo_push(message='HairMesh Sub-tool')          # (the sub-tool is on the Scene: without its step a Ctrl+Z of the first stroke would leave it too)
    brush.follow_subtool(context)


class HAIRMESH_OT_set_subtool(bpy.types.Operator):
    """Switch the HairMesh sub-tool"""
    bl_idname = 'hairmesh.set_subtool'
    bl_label = 'HairMesh Sub-tool'
    bl_options = {'REGISTER'}

    subtool: EnumProperty(name='Sub-tool', items=SUBTOOLS, default='SELECT')
    activate: BoolProperty(name='Activate the Tool', description='Also switch to the HairMesh tool when it is not active (the buttons of the N panel and the menus; the tool keys never do this)', default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    @classmethod
    def description(cls, context, properties):
        """(4.2c: the tooltip names the sub-tool of the button — it said "Select / Draw / Scissors" over Guides too)"""
        return next((it[2] for it in SUBTOOLS if it[0] == properties.subtool), cls.__doc__)

    def invoke(self, context, event):
        if self.activate:
            return self.execute(context)                            # a button: always acts, even on the current sub-tool
        hm = context.scene.hairmesh
        if hm.subtool == self.subtool:
            return {'PASS_THROUGH'}
        return self.execute(context)

    def execute(self, context):
        was = context.scene.hairmesh.subtool
        context.scene.hairmesh.subtool = self.subtool
        if self.activate:
            activate(context, self.report)                          # outside Object Mode: the warning, the sub-tool still set
        _brush_follows(context, was)
        return {'FINISHED'}


def _subtool_key_operator(key, label, desc):
    """One operator per sub-tool for the KEYS (1 / 2 / 3, K, Esc), so Preferences > Keymap names each one ("HairMesh
    Draw Sub-tool"...): the keymap list shows an operator's label, never its properties, and a single
    hairmesh.set_subtool with a property read as three identical "HairMesh Sub-tool" rows (reported on 14/10/2026).
    The buttons keep hairmesh.set_subtool (with `activate`). Pressed on the sub-tool it already shows, the key is
    swallowed (1 / 2 / 3 must never reach Blender's collection toggles, 15/10/2026), unless the keymap item says
    `pass_through`: Esc in Select stays Blender's."""
    class Op(bpy.types.Operator):
        bl_idname = f'hairmesh.subtool_{key.lower()}'
        bl_label = f'HairMesh {label} Sub-tool'
        bl_description = desc
        bl_options = {'REGISTER'}
        subtool = key

        pass_through: BoolProperty(name='Pass Through', description='Already on this sub-tool: let Blender have the key (Esc) instead of swallowing it (1 / 2 / 3, K)', default=False, options={'HIDDEN', 'SKIP_SAVE'})

        @classmethod
        def poll(cls, context):
            return owner.visible(context)

        def invoke(self, context, event):
            if context.scene.hairmesh.subtool == self.subtool:
                return {'PASS_THROUGH'} if self.pass_through else {'CANCELLED'}
            if self.pass_through and context.scene.hairmesh.subtool == 'EDGES':   # (Esc with edges selected deselects them first; the order of a tool's keymap items is not ours: the user keyconfig holds them reversed)
                from .ops_edges import has_edges
                if has_edges(context):
                    return {'PASS_THROUGH'}
            if self.pass_through and context.scene.hairmesh.subtool == 'GUIDES':   # (the same for the Pencil's loop between two strokes, 8053: the first Esc drops it)
                from . import ops_guides
                if ops_guides.pencil is not None:
                    return {'PASS_THROUGH'}
            return self.execute(context)

        def execute(self, context):
            was = context.scene.hairmesh.subtool
            context.scene.hairmesh.subtool = self.subtool
            _brush_follows(context, was)
            return {'FINISHED'}
    Op.__name__ = Op.__qualname__ = f'HAIRMESH_OT_subtool_{key.lower()}'
    return Op


SUBTOOL_KEY_OPS = tuple(_subtool_key_operator(key, label, desc) for key, label, desc, _icon, _n in SUBTOOLS)
SUBTOOL_KEY_IDS = {op.subtool: op.bl_idname for op in SUBTOOL_KEY_OPS}   # 'DRAW' -> 'hairmesh.subtool_draw'


class HAIRMESH_OT_open_modifiers(bpy.types.Operator):
    """Open the HairMesh Menu on the Modifiers tab: Profile, Extra and Topology, with any sub-tool"""
    bl_idname = 'hairmesh.open_modifiers'
    bl_label = 'HairMesh Modifiers Tab'
    bl_options = {'INTERNAL'}                                       # (no undo step, not in the operator history: the tab is a preference)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        p = prefs.get(context)
        if p is not None:
            p.menu_tab = 'MODIFIERS'
        if not bpy.app.background and bpy.ops.wm.call_panel.poll():  # (in background mode its poll says yes and the pop-up crashes: no real window)
            bpy.ops.wm.call_panel(**POPUP_PROPS)
        return {'FINISHED'}


# ---------------- the contextual menus (openMainPie, 7891) ----------------
# An item is a tuple: ('op', idname, text, icon, {props}[, {'active': False, 'emboss': False, 'list_text': text in the list menu}]) | ('indent', n) (n levels of
# a tree: blank icons) | ('prop', attr, text[, {'active': False} | {'enabled': False}]) (a property of
# Scene.hairmesh, or of WindowManager.hairmesh_conv for the conversion sliders: props.data_of; dimmed when not active;
# a slider for a fraction, a number field for a measure or a count: is_slider) |
# ('toggle', attr, text, icon[, {'active': False}]) (a bool of Scene.hairmesh as a toggle button; a checkbox in a list menu) |
# ('subtool', key, text, icon) (the sub-tool button, pressed while it is the active one) | ('menu', idname, text, icon) |
# ('label', text, icon[, {'split': f}]) (a label taking the share f of its row) | ('sep',) | ('box', [items]) (a column box in a pie slot, several rows) | ('empty',) (a pie slot left
# free) | ('row', [items]) (items side by side inside a pie's box) | ('block', key, title, icon, rows, open by default[, why]) (a
# titled block of rows of a sheet, collapsible — the header toggles it, the state is remembered in the preferences:
# block_open —, a box in the pop-up and the N panel, a titled run of items in the list menu; `why`: the reason its dimmed
# controls say, in the tooltip of the "i" at the end of its header, G2: a property has no dynamic tooltip). A 'prop'
# with {'expand': True} is an enum as buttons side by side (G6). pie_items gives exactly 8, in Blender's pie order: W, E,
# S, N, NW, NE, SW, SE.
#
# U6 of docs/fase-3-ux-revisao.md (15/10/2026): ONE name per action — the long text in the pie and in the list menu
# (Object > HairMesh), the short one inside a row of 2+ buttons of the pop-up and of the N panel; an icon-only
# button carries its long text in the list (long_name). The wall-making items keep the original's own labels
# (surfaceMenuItems 6157) in the long form.

FREE_COL = 6.0                                                      # the group sheet's Free Curves column, beside the Curves buttons
LIVE_ICON = 'SHADING_SOLID'                                         # Live Preview (G8: MOD_LATTICE is the wall's)
MERGE_ICON = 'SHARPCURVE'                                           # Merge Shared Trunks (G8: AUTOMERGE_ON is Merge Coincident Anchors')
LOGO = icons.PREFIX + 'hairmesh'                                    # the hair-in-a-ring image icon (icons.py); draw_item resolves it to icon_value
MENU_OP = ('op', 'wm.call_panel', 'HairMesh Menu', LOGO, dict(POPUP_PROPS))   # the pop-up, from a pie slot
MODIFIERS_OP = ('op', 'hairmesh.open_modifiers', 'Modifiers', 'MODIFIER', {})   # the pop-up on its Modifiers tab (key 6, the Q pie)
WALLS_MENU = ('menu', 'HAIRMESH_MT_walls', 'Walls / Lock', 'MOD_LATTICE')
GROUP_MENU = ('menu', 'HAIRMESH_MT_group', 'Group', 'OUTLINER_COLLECTION')   # the group ACTIONS (the list of groups is the outliner's)
CONV_MENU = ('menu', 'HAIRMESH_MT_conversion', 'Conversion', 'MOD_REMESH')
MOVE_MENU = ('menu', 'HAIRMESH_MT_move_to_group', 'Move to Group', 'OUTLINER_COLLECTION')

# key -> (idname, long text, short text, icon, properties)
ACT = {
    'recalc_anchors': ('hairmesh.recalc_handles', 'Recalculate Handles', 'Recalculate', 'FILE_REFRESH', {}),
    'recalc_curves': ('hairmesh.recalc_handles', 'Recalculate All Anchors', 'Recalculate', 'FILE_REFRESH', {}),
    'reverse': ('hairmesh.reverse_curves', 'Reverse Direction', 'Reverse', 'ARROW_LEFTRIGHT', {}),
    'delete_anchors': ('hairmesh.delete_selected', 'Delete Anchors', 'Delete', 'X', {'keep_shape': False}),
    'keep_shape': ('hairmesh.delete_selected', 'Delete Anchors, Keep Shape', 'Keep Shape', 'HANDLE_ALIGNED', {'keep_shape': True}),
    'delete_curves': ('hairmesh.delete_selected', 'Delete Curves', 'Delete', 'X', {'keep_shape': False}),
    'optimize': ('hairmesh.optimize_anchors', 'Optimize Anchors (Keep Shape)', 'Optimize', 'MOD_SIMPLIFY', {'only': False}),
    'merge': ('hairmesh.merge_coincident', 'Merge Coincident Anchors', 'Merge', 'AUTOMERGE_ON', {}),
    'new_group': ('hairmesh.add_group', 'New Group', 'New Group', 'ADD', {}),
    'new_subgroup': ('hairmesh.add_group', 'New Sub-group', 'New Sub-group', 'ADD', {}),
    'rename_group': ('hairmesh.rename_item', 'Rename Group...', 'Rename...', 'SORTALPHA', {}),   # the one rename (U6, 16/10/2026): the selected item, a group here
    'delete_group': ('hairmesh.delete_group', 'Delete Group', 'Delete', 'X', {}),
    'match': ('hairmesh.match_project_step', 'Match Project Step', 'Match', 'LINKED', {}),
    'detach': ('hairmesh.detach_group', 'Detach from Neighboring Groups', 'Detach', 'UNLINKED', {}),
    'set_head': ('hairmesh.set_head', 'Set Head', 'Set Head', 'MESH_MONKEY', {}),
    'convert': ('hairmesh.convert', 'Convert', 'Convert', 'PLAY', {}),
    # 3.7: the original's Confirm / Cancel (10038) and ours, the Live / Baked view
    'confirm': ('hairmesh.confirm_conversion', 'Confirm Conversion', 'Confirm', 'CHECKMARK', {}),
    'cancel_conv': ('hairmesh.cancel_conversion', 'Cancel Conversion', 'Cancel', 'CANCEL', {}),
    'view_live': ('hairmesh.conversion_view', 'Live View', 'Live', 'MOD_LATTICE', {'mode': 'LIVE'}),
    'view_baked': ('hairmesh.conversion_view', 'Baked View', 'Baked', 'MESH_DATA', {'mode': 'BAKED'}),
    'save': ('hairmesh.save_project', 'Save .hairmesh', 'Save', 'FILE_TICK', {}),   # 1c of the manual test of the lot ux5: back into the file it came from
    'export': ('hairmesh.export_project', 'Save .hairmesh As...', 'Save As...', 'EXPORT', {}),
    'import': ('hairmesh.import_project', 'Import .hairmesh...', 'Import...', 'IMPORT', {}),
    'migrate': ('hairmesh.migrate_project', 'Migrate Hair from .hairmesh...', 'Migrate Hair...', 'APPEND_BLEND', {}),   # 3.7b: the original's File > Migrate
    'new_project': ('hairmesh.new_project', 'New Project...', 'New...', 'ADD', {}),
    'rebuild': ('hairmesh.live_rebuild', 'Rebuild Live Preview', '', 'FILE_REFRESH', {}),
    'step_from_object': ('hairmesh.step_from_object', 'Loop Step from Object Height', '', 'EMPTY_SINGLE_ARROW', {}),
    # the Conversion list (ours, 1c of 21/10/2026): every group ticked / none / this one alone (text only: 1b of the
    # manual test of 21/10/2026, the checkbox icons read as the state of something)
    # U7 C (docs/fase-3-ux-revisao.md): Check All became two — the top groups alone (a mesh per tree, the sub-groups
    # merged into it) and every group (a mesh per group, as the example comes)
    'tick_top': ('hairmesh.tick_top', 'Tick Top Groups', 'Tick Top Groups', 'NONE', {}),
    'tick_all': ('hairmesh.tick_all', 'Tick Every Group', 'Tick Every Group', 'NONE', {'on': True}),
    'untick_all': ('hairmesh.tick_all', 'Uncheck All Groups', 'Uncheck All', 'NONE', {'on': False}),
    'tick_only': ('hairmesh.tick_group', 'Only This Group', '', 'SOLO_OFF', {'only': True, 'toggle': False}),
    'clear_color': ('hairmesh.clear_group_color', 'Clear Group Color', 'Clear Color', 'X', {}),
    # 3.8b: the anchor gizmo's modes (the original's E / R / T) and Duplicate (its D; Shift+D here)
    'gizmo_move': ('hairmesh.gizmo_move', 'Move Gizmo', 'Move', 'EMPTY_ARROWS', {}),
    'gizmo_rotate': ('hairmesh.gizmo_rotate', 'Rotate Gizmo', 'Rotate', 'DRIVER_ROTATIONAL_DIFFERENCE', {}),
    'gizmo_scale': ('hairmesh.gizmo_scale', 'Scale Gizmo', 'Scale', 'MOD_LENGTH', {}),
    'gizmo_space': ('hairmesh.gizmo_space', 'Gizmo Axes: World / Local', 'Axes', 'ORIENTATION_GLOBAL', {}),
    'duplicate': ('hairmesh.duplicate', 'Duplicate', 'Duplicate', 'DUPLICATE', {}),
    # 4.0 (D4.3): the original's F and P / Shift+P (8007, 8014-8015), contextual keys of the tool
    'frame': ('hairmesh.frame_selection', 'Frame Selection (F)', 'Frame (F)', 'ZOOM_SELECTED', {}),
    'parent_last': ('hairmesh.parent_to_last', 'Parent to Last Selected Group (P)', 'Parent (P)', 'CON_CHILDOF', {}),
    'unparent': ('hairmesh.unparent', 'Unparent to Root (Shift+P)', 'Unparent (Shift+P)', 'UNLINKED', {}),
    # 3.8c: G / R / S on the selected anchors (the owner's decision of 24/10/2026), the key in the text
    'grab': ('hairmesh.grab_anchors', 'Move Selection (G)', 'Move (G)', 'EMPTY_ARROWS', {}),
    'rotate': ('hairmesh.rotate_anchors', 'Rotate Selection (R)', 'Rotate (R)', 'DRIVER_ROTATIONAL_DIFFERENCE', {}),
    'scale': ('hairmesh.scale_anchors', 'Scale Selection (S)', 'Scale (S)', 'MOD_LENGTH', {}),
    # Blender's own undo / redo as buttons (1a of the second manual test of 21/10/2026): inside a pop-up no key reaches
    # a keymap, so Ctrl+Z / Ctrl+Shift+Z do nothing while the HairMesh Menu or the Outliner is open under the mouse
    'undo': ('hairmesh.undo_step', 'Undo', '', 'LOOP_BACK', {'redo': False}),
    'redo': ('hairmesh.undo_step', 'Redo', '', 'LOOP_FORWARDS', {'redo': True}),
}
UNDO_REDO = (('op', 'hairmesh.undo_step', '', 'LOOP_BACK', {'redo': False}), ('op', 'hairmesh.undo_step', '', 'LOOP_FORWARDS', {'redo': True}))


def act(key, short=False, text=None, **props):
    """an 'op' item of the ACT table: the long text, or the short one in a row, or a text given"""
    idname, long, sh, icon, p = ACT[key]
    return ('op', idname, text if text is not None else (sh if short else long), icon, {**p, **props})


def long_name(item):
    """the long text of an 'op' item (the ACT table by idname and properties), for the list menu and the N panel"""
    for idname, long, _sh, _icon, p in ACT.values():
        if item[1] == idname and all(item[4].get(k) == v for k, v in p.items()) and (p or not item[4]):
            return long                                             # (the item may carry more, e.g. Only This Group's group id)
    return item[1].split('.')[1].replace('_', ' ').title()


# the tabs of the HairMesh Menu (U5; the Box Helper of BoxCutter keeps the active one in its preferences too)
TABS = (('SELECTION', 'Selection', 'The properties and actions of the selected anchors, curve, wall or group', 'HM:selection', 0),
        ('MODIFIERS', 'Modifiers', 'The modifiers: Profile, Extra and Topology, with their brushes', 'MODIFIER', 4),   # (a tab, not a sub-tool: the owner's decision; the number is new, the others stay)
        ('CONVERSION', 'Conversion', 'The groups ticked for conversion, the loop step, the sliders, Convert', 'MOD_REMESH', 1),
        ('OPTIONS', 'Viewport', 'What the viewport shows and how it reacts: the live preview, Drag on Head, the x-ray, the guides', 'VIEW3D', 3),
        ('PROJECT', 'Project', 'The project of the scene: head, save / save as, import, new, migrate hair', 'FILE', 2))   # Project last (1a of the manual test of the lot ux5; the numbers kept: the saved menu_tab stays valid)
TAB_SPLIT = TAB_UNITS / POPUP_UNITS_X                               # the icon column (the Box Helper: 0.09 of 20 units)
MAX_ROWS = 18                                                       # the budget of a sheet with the blocks as they open by default (visible_rows; tests/test_blender_sheets): never the whole screen


_blocks_fallback = {}                                              # the blocks' state when the add-on has no preferences (registered by hand: the tests)


def _blocks_state(context):
    p = prefs.get(context)
    if p is None:
        return _blocks_fallback
    import json
    try:
        d = json.loads(p.sheet_blocks or '{}')
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def block_open(context, key, default=True):
    """whether the block `key` of a sheet is drawn open: as the user left it (the preferences, between openings and
    sessions), else `default`"""
    return bool(_blocks_state(context).get(key, default))


def set_block_open(context, key, on):
    import json
    p = prefs.get(context)
    if p is None:
        _blocks_fallback[key] = bool(on)
        return
    d = _blocks_state(context); d[key] = bool(on)
    p.sheet_blocks = json.dumps(d)


class HAIRMESH_OT_toggle_block(bpy.types.Operator):
    """Open or close this block of the sheet (remembered between openings)"""
    bl_idname = 'hairmesh.toggle_block'
    bl_label = 'HairMesh Sheet Block'
    bl_options = {'REGISTER', 'INTERNAL'}                           # UI state: no undo step

    key: bpy.props.StringProperty(name='Block', default='')
    default: BoolProperty(name='Open by Default', default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        set_block_open(context, self.key, not block_open(context, self.key, self.default))
        for win in context.window_manager.windows:                  # the pop-up and the N panel
            for area in win.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


def flat_items(rows):
    """every item of the rows, the blocks' rows and the columns' items included (the tests, the list menu)"""
    out = []
    for row in rows:
        for it in row:
            if it[0] == 'block':
                out.extend(flat_items(it[4]))
            elif it[0] == 'cols':
                out.extend(flat_items([c[1] for c in it[1]]))
            elif it[0] == 'list' and len(it) > 4:
                out.extend(flat_items(it[4]))                       # (the lines of the list of the Extras)
            else:
                out.append(it)
    return out


def whys(rows):
    """the reasons the rows carry in their "i" buttons (a block's, a row's), in blocks and columns too"""
    out = []
    for row in rows:
        for it in row:
            if it[0] == 'block':
                out += ([it[6]] if len(it) > 6 and it[6] else []) + whys(it[4])
            elif it[0] == 'cols':
                out += whys([c[1] for c in it[1]])
            elif it[0] == 'op' and it[1] == 'hairmesh.why':
                out.append(it[4]['why'])
    return out


def texts(rows):
    """every text the rows say, whole — the labels, the paragraphs and the reasons of the "i" buttons, in blocks and
    columns too (the tests)"""
    return [it[1] for it in flat_items(rows) if it[0] in ('label', 'text') and it[1]] + whys(rows)


def tree_texts(context, P):
    """{group id: the whole text of its row in the Conversion tree, the lines it wraps into joined} (the tests)"""
    return {e[0].id: ' '.join(ops_groups.tick_parts(P, e[0], e[2], e[3], e[6], e[7])[0]) for e in conversion_tree(context, P)}


def visible_rows(context, rows, width=ROOM):
    """how many rows the sheet draws at `width` units with the blocks as they are now: a closed block is its header
    row alone, a paragraph its wrapped lines, a row too narrow for its items one row per item (draw_rows)"""
    n = 0
    for row in rows:
        if not row:
            continue
        head = row[0]
        if head[0] == 'block':
            n += 1
            if block_open(context, head[1], head[5]):
                n += visible_rows(context, head[4], width - BOX_PAD)
        elif head[0] == 'text':
            n += len(text_lines(head, width))
        elif head[0] == 'list':
            n += head[1] + 1                                        # (its rows and the list's grip)
        elif head[0] == 'cols':
            n += cols_height(context, head[1], width)
        elif row_fits(context, row, width):
            n += 1
        else:
            n += sum(alone_height(context, it, width) for it in row if it[0] != 'sep')   # (a separator stacks into nothing)
    return n


def alone_height(context, it, width, hm=None):
    """the rows an item takes on a row of its own: a label too wide wraps, a control too wide says its name above"""
    if it[0] == 'label' and it[1] and fit.label_need(it[1], it[2] not in (None, 'NONE')) > width:
        return len(text_lines(text(it[1], it[2]), width))
    if too_wide_alone(context, it, width, hm):
        return 1 + len(text_lines(text(item_text(it), 'NONE'), width))
    return 1


def cols_fit(context, columns, width, hm=None):
    """whether a 'cols' row shows every control whole at `width`: the fixed columns leave the free one its room, and
    each control gets its share of its column (labels wrap in their column)"""
    fixed = sum(c[0] for c in columns if c[0])
    if width - fixed < 0.5:
        return False
    for c in columns:
        w = c[0] if c[0] else width - fixed
        ests = [item_est(context, x, hm) for x in c[1]]
        tot = sum(ests) or 1.0
        for x, e in zip(c[1], ests):
            if x[0] not in ('label', 'text', 'indent', 'sep', 'empty') and item_need(context, x, hm) > w * e / tot + 1e-6:
                return False
    return True


def cols_items(columns):
    """the items of a 'cols' row that does not fit, one per row (the indentation left out)"""
    return [[x] for c in columns for x in c[1] if x[0] not in ('indent', 'sep', 'empty') and not (x[0] == 'label' and not x[1])]


def cols_height(context, columns, width, hm=None):
    """the rows a 'cols' row takes: one, plus the extra lines of a label wrapped in its column; stacked when it does
    not fit"""
    if not cols_fit(context, columns, width, hm):
        return sum(alone_height(context, r[0], width, hm) for r in cols_items(columns))
    fixed = sum(c[0] for c in columns if c[0])
    n = 1
    for c in columns:
        w = c[0] if c[0] else width - fixed
        ests = [item_est(context, x, hm) for x in c[1]]
        tot = sum(ests) or 1.0
        for x, e in zip(c[1], ests):
            if x[0] in ('label', 'text') and x[1]:
                ww = w * e / tot
                if x[0] == 'text' or fit.label_need(x[1], x[2] not in (None, 'NONE')) > ww:
                    n = max(n, len(text_lines(x if x[0] == 'text' else text(x[1], x[2]), ww)))
    return n


def stacked(context, rows, width=ROOM, hm=None):
    """the rows draw_rows would draw one item per row at `width` (the pop-up is laid out so that none does: the tests)"""
    out = []
    for row in rows:
        if not row:
            continue
        head = row[0]
        if head[0] == 'block':
            if block_open(context, head[1], head[5]):
                out.extend(stacked(context, head[4], width - BOX_PAD, hm))
        elif head[0] == 'cols':
            if not cols_fit(context, head[1], width, hm):
                out.append(row)
        elif head[0] not in ('text', 'list') and not row_fits(context, row, width, hm):
            out.append(row)
    return out


def block(key, title, icon, rows, open_default=True, why=None):
    """a block row; `why`: the reason of its dimmed controls (the "i" of its header), or None"""
    return [('block', key, title, icon, rows, open_default, *([why] if why else []))]


def why_item(why):
    """the "i" of a row: its tooltip is the reason (a property has no dynamic tooltip, G2); a click says it again"""
    return ('op', 'hairmesh.why', '', 'INFO', {'why': why}, {'emboss': False, 'list_text': 'Why'})


def undo_op(redo):
    return bpy.ops.ed.redo if redo else bpy.ops.ed.undo


class HAIRMESH_OT_undo_step(bpy.types.Operator):
    """Undo or redo a step from inside a pop-up: a pop-up keeps the keys while it is open, so Ctrl+Z does nothing there"""
    bl_idname = 'hairmesh.undo_step'
    bl_label = 'Undo'
    bl_options = {'INTERNAL'}                                       # (no undo step of its own)

    redo: BoolProperty(name='Redo', default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if not undo_op(properties.redo).poll():
            return 'No step to redo' if properties.redo else 'No step to undo'
        return (('Redo the last step undone' if properties.redo else 'Undo the last step')
                + ': a pop-up keeps the keys while it is open, so Ctrl+Z and Ctrl+Shift+Z do nothing here')

    def execute(self, context):
        op = undo_op(self.redo)
        if not op.poll():                                           # (dimmed: a click only says so)
            self.report({'INFO'}, 'No step to redo' if self.redo else 'No step to undo')
            return {'CANCELLED'}
        op()
        return {'FINISHED'}


class HAIRMESH_OT_why(bpy.types.Operator):
    """Show why the controls here are dimmed"""
    bl_idname = 'hairmesh.why'
    bl_label = 'Note'
    bl_options = {'INTERNAL'}                                       # (no undo step: it only says something)

    why: bpy.props.StringProperty(name='Why', default='', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return properties.why or cls.__doc__

    def execute(self, context):
        self.report({'INFO'}, self.why)
        return {'FINISHED'}


def text(t, icon='INFO', **opts):
    """a paragraph: wrapped at draw time by the width it gets (a label never wraps in Blender: it is cut with "…");
    the first line carries the icon, the next ones a blank icon so the lines start together"""
    return ('text', t, icon, opts) if opts else ('text', t, icon)


def hint(t, icon='INFO'):
    """a paragraph row: a short state line, only when the whole sheet is blocked (G2)"""
    return [text(t, icon)]


def text_lines(item, width):
    icon = item[2]
    room = width - (fit.ICON if icon and icon != 'NONE' else 0.0) - fit.LABEL_PAD
    if len(item) > 3 and item[3].get('path'):
        return fit.wrap_path(item[1], room)                         # (a file path: broken after its separators)
    return fit.wrap(item[1], room)


# ---- a slider or a number field (the interface lot after 4.8, item 7: the owner's request; docs/interface.md) ----

SLIDER_SUBTYPES = frozenset({'FACTOR', 'PERCENTAGE'})


def is_slider(rna):
    """a filled bar only for a fraction of a closed range — the subtypes FACTOR and PERCENTAGE (Roundness, Bulge, a
    brush's strength or hardness, the conversion's Deviation...), where how much of the bar is full says something; a
    measure or a count (Radius, Rotation, Size, Segments, Density, the loop step, the guides' radii...) is a number
    field, dragged or typed, as Quad Remesher's Quad Count: its soft_min / soft_max only bound a drag"""
    return rna is not None and rna.type in ('FLOAT', 'INT') and rna.subtype in SLIDER_SUBTYPES


# ---- what an item needs to show its text whole (part 2b of the lot ux7: no text is ever cut with "…") ----

def _prop_rna(context, attr, hm=None):
    data = props.data_of(context, attr, hm)
    return data, data.bl_rna.properties.get(attr)


def _prop_value_text(data, rna, attr):
    """the value a slider or a field shows, as Blender writes it"""
    try:
        v = getattr(data, attr)
    except Exception:
        return ''
    if rna.type == 'FLOAT':
        return f'{v:.{rna.precision}f}'
    if rna.type == 'INT':
        return str(v)
    if rna.type == 'ENUM':
        item = next((e for e in rna.enum_items if e.identifier == v), None)
        return item.name if item is not None else str(v)
    if rna.type == 'STRING':
        return str(v)
    return ''


def item_est(context, item, hm=None):
    """the width Blender counts an item at when a row of a panel shares its width (ui_item_size in a vertical
    layout: an item with text ten units, an icon alone one): the row gives each its share in proportion"""
    k = item[0]
    if k == 'indent':
        return float(item[1])
    if k == 'sep':
        return 0.4
    if k == 'label':
        return 10.0 if item[1] else 1.0
    if k == 'prop':
        if item[2] or expanded(item):
            return 10.0
        data, rna = _prop_rna(context, item[1], hm)
        if rna is None:
            return 10.0
        if rna.type == 'BOOLEAN' or (rna.type == 'FLOAT' and getattr(rna, 'array_length', 0) and rna.subtype in ('COLOR', 'COLOR_GAMMA')):
            return 1.0
        return 10.0
    if k in ('op', 'toggle', 'menu', 'subtool'):
        return 10.0 if item[2] else 1.0
    return 10.0


def expanded(item):
    """whether a 'prop' item is an enum as buttons side by side (G6)"""
    return len(item) > 3 and bool(item[3].get('expand'))


def enum(attr, label='', ok=True):
    """an enum of 2 or 3 short options as buttons side by side (G6), its name before them; dimmed when it changes
    nothing now"""
    return ('prop', attr, label, {'expand': True, **({} if ok else {'active': False})})


def item_need(context, item, hm=None):
    """the width an item needs to show its text whole, in units (fit.py)"""
    k = item[0]
    if k == 'indent':
        return float(item[1])
    if k in ('sep', 'empty'):
        return 0.0
    if k == 'label':
        return fit.label_need(item[1], item[2] not in (None, 'NONE')) if item[1] else 0.0
    if k in ('op', 'toggle', 'subtool'):
        return fit.button_need(item[2], item[3] not in (None, 'NONE')) if item[2] else 0.0   # (an icon alone has no text to cut)
    if k == 'menu':
        return fit.button_need(item[2], item[3] not in (None, 'NONE')) + 0.6 if item[2] else 0.0   # (the menu's arrow)
    if k == 'prop':
        data, rna = _prop_rna(context, item[1], hm)
        if rna is None:
            return 1.0
        name = item[2]
        if rna.type in ('FLOAT', 'INT') and not getattr(rna, 'array_length', 0):
            return fit.slider_need(name, _prop_value_text(data, rna, item[1])) if name else fit.units(_prop_value_text(data, rna, item[1])) + fit.CLIP_PAD
        if rna.type == 'ENUM' and expanded(item):                   # (the buttons share the width evenly)
            n = len(rna.enum_items)
            return (fit.label_need(f'{name}:', False) if name else 0.0) + n * max(fit.button_need(e.name, False) for e in rna.enum_items)
        if rna.type == 'ENUM':
            value = fit.button_need(_prop_value_text(data, rna, item[1]), False) + 0.6
            return max((fit.label_need(name, False)) / fit.ENUM_LABEL, value / (1.0 - fit.ENUM_LABEL)) if name else value
        if rna.type == 'STRING':
            icon = len(item) > 3 and item[3].get('icon')
            return fit.button_need(_prop_value_text(data, rna, item[1]), bool(icon))
        if rna.type == 'BOOLEAN':
            return fit.button_need(name, True) if name else 0.0
        return 0.0                                                  # a colour swatch
    return 0.0


def item_text(item):
    k = item[0]
    return item[1] if k in ('label', 'text') else item[2] if k in ('op', 'toggle', 'menu', 'subtool', 'prop') else ''


def too_wide_alone(context, item, width, hm=None):
    """a control with a text that does not show whole even in a row of its own (a narrow N panel): drawn as its text
    in a paragraph and the control without text under it (draw_rows)"""
    return item[0] in ('op', 'toggle', 'menu', 'prop', 'subtool') and bool(item_text(item)) and item_need(context, item, hm) > width + 1e-6


def without_text(item):
    return (*item[:2], '', *item[3:])


def named(item):
    """an icon-only 'op' item with its long text (the 'list_text' it carries, else long_name), keeping whether it is
    dimmed or pressed: the list menu's form, and a stacked row's (draw_rows: a row of its own has room for the name)"""
    opts = item[5] if len(item) > 5 else {}
    keep = {k: opts[k] for k in ('active', 'depress') if k in opts and opts[k] is (False if k == 'active' else True)}
    return (item[0], item[1], opts.get('list_text') or long_name(item), item[3], item[4], *([keep] if keep else []))


def stacked_form(context, it, width, hm=None):
    """an item of a row too narrow for it, drawn on a row of its own (draw_rows): an icon-only button with its name —
    the long one, else the ACT table's short one — when that fits the row (item 8 of the lot after 4.8: Confirm /
    Cancel / Live / Baked in a narrow N panel), else as it is"""
    if it[0] == 'prop' and expanded(it) and item_need(context, it, hm) > width + 1e-6:   # (a narrow N panel: the enum as a dropdown)
        return (*it[:3], {k: v for k, v in it[3].items() if k != 'expand'})
    if it[0] == 'op' and not it[2] and it[1] != 'hairmesh.outliner_expand':
        n = named(it)
        for t in (n[2], short_name(it)):
            if t and item_need(context, (*n[:2], t, *n[3:]), hm) <= width + 1e-6:
                return (*n[:2], t, *n[3:])
    return it


def short_name(item):
    """the short text of an 'op' item in the ACT table (its long_name's pair), or None"""
    for idname, _long, sh, _icon, p in ACT.values():
        if item[1] == idname and all(item[4].get(k) == v for k, v in p.items()) and (p or not item[4]):
            return sh or None
    return None


def row_fits(context, row, width, hm=None):
    """whether every item of a row shows its text whole at `width`: the row gives each item its share in proportion
    to Blender's estimate (item_est); a label with a 'split' share takes that share"""
    if row and row[0][0] == 'label' and len(row[0]) > 3 and row[0][3].get('split'):
        f = row[0][3]['split']
        return item_need(context, row[0], hm) <= f * width and row_fits(context, row[1:], (1.0 - f) * width, hm)
    ests = [item_est(context, it, hm) for it in row]
    total = sum(ests) or 1.0
    return all(item_need(context, it, hm) <= width * e / total + 1e-6 for it, e in zip(row, ests))


def dim(ok):
    """the options of a control that changes nothing now (part 1 of the lot ux7): dimmed, still usable"""
    return () if ok else ({'active': False},)


def dim_op(item, ok):
    """an 'op' item dimmed when it would change nothing now (its tooltip and its report say why)"""
    return item if ok else (*item[:5], {**(item[5] if len(item) > 5 else {}), 'active': False})


def _anchor_actions(hm, P, anchors):
    n = len(anchors)
    smooth_ok = E.smoothActs(P, anchors); recalc_ok = E.recalcActs(P, anchors)
    return {
        'delete': act('delete_anchors', text=f'Delete Anchor{"s" if n > 1 else ""}'),
        'keep': act('keep_shape', text=f'Delete Anchor{"s" if n > 1 else ""}, Keep Shape'),
        'recalc': dim_op(act('recalc_anchors'), recalc_ok),
        'smooth': ('prop', 'anchor_smooth', 'Smoothness', *dim(smooth_ok)),
        'smooth_why': ' '.join(t for t in (None if smooth_ok else 'Smoothness: these anchors lie on junction curves only, which never read it (Unite Locks makes them).',
                                        None if recalc_ok else RECALC_HINT) if t) or None,
        'label': ('label', f'Anchors Selected: {n}', 'DECORATE_KEYFRAME'),
        # the same actions with short texts, for the rows of the pop-up
        'delete_s': act('delete_anchors', short=True),
        'keep_s': act('keep_shape', short=True),
        'recalc_s': dim_op(without_text(act('recalc_anchors')), recalc_ok),
        # 3.4: the rest of the original's anchor pie, as a submenu (the SW slot) and as rows of the pop-up
        'more': ('menu', ops_weld.HAIRMESH_MT_anchor_more.bl_idname, 'Weld / Split / Curves', 'DECORATE_KEYFRAME'),
        'more_rows': _rows(ops_weld.anchor_more_items_short(P, anchors), 2),
    }


GIZMO_KEYS = (('TRANSLATE', 'gizmo_move'), ('ROTATE', 'gizmo_rotate'), ('SCALE', 'gizmo_scale'))


def gizmo_items(hm, ok, short=True):
    """the three gizmo modes as buttons, pressed while on (the original's E / R / T tools); dimmed without anchors
    (the tooltip says why: hairmesh.gizmo_* description), still usable: the mode waits for the anchors"""
    return [dim_op((*act(k, short=short)[:5], {'depress': hm.gizmo_mode == m}), ok) for m, k in GIZMO_KEYS]


GIZMO_MENU = ('menu', 'HAIRMESH_MT_gizmo', 'Gizmo', 'GIZMO')


def gizmo_why(context, P):
    """why the gizmo and G / R / S cannot act on the HairMesh selection now, or None (3.8d: anchors, curves, walls and
    groups act, and since 4.0 the converted meshes inside; imported meshes are phase 5, edit.gizmoBlocked)"""
    from .ops_gizmo import blocked_reason
    return blocked_reason(context)


def gizmo_rows(context, hm, ok):
    """the rows of the Transform block: the modes, the axes and the locks (the original's W and X / Y / Z), Duplicate.
    `ok`: the selection can move. Dimmed, still usable — the values wait for the gizmo: the modes without a selection or
    with the tool off (the gizmo lives in the tool, Select); the axes and locks also with no mode on. G / R / S have no
    buttons (a Blender user presses the keys; the "?" lists them)"""
    on = is_active(context)
    modes_ok = ok and on
    rest_ok = modes_ok and hm.gizmo_mode != 'NONE'
    sp = hm.gizmo_space == 'LOCAL'
    space = dim_op(('op', 'hairmesh.gizmo_space', '', 'ORIENTATION_LOCAL' if sp else 'ORIENTATION_GLOBAL', {}, {'list_text': 'Local Axes' if sp else 'World Axes'}), rest_ok)
    locks = [dim_op(('op', 'hairmesh.gizmo_lock', f'Lock {a}', 'LOCKED' if getattr(hm, f'gizmo_lock_{a.lower()}') else 'UNLOCKED', {'axis': a}, {'depress': getattr(hm, f'gizmo_lock_{a.lower()}')}), rest_ok) for a in 'XYZ']
    # (item 8 of the lot after 4.8, the owner's request: the gizmo's modes, its axes and Duplicate as one row of icons —
    # the 3D View header's own gizmo buttons are icons —, each named by its tooltip and in the list menu (long_name /
    # list_text); the "Gizmo" label went: the block's title says it. 3.8d: no split label in this aligned column, it hid
    # the last button of the rows below)
    gz = [(*it[:2], '', *it[3:]) for it in gizmo_items(hm, modes_ok)]
    dup = ('op', 'hairmesh.duplicate', '', 'DUPLICATE', {}, {'list_text': 'Duplicate'})
    return [[*gz, ('sep',), space, dup], locks]


def gizmo_block(key, context, hm, ok, why=None):
    """the Transform block, closed by default; `why`: the reason the selection cannot move, in the "i" of its header"""
    say = ('The gizmo and G / R / S work with the HairMesh tool active (Shift+Alt+H), in Select.' if not is_active(context) else
           why or 'The gizmo and G / R / S act on the selection: anchors, curves, walls or a group.' if not ok else
           'Axes and locks: turn on a gizmo (Move, Rotate or Scale).' if hm.gizmo_mode == 'NONE' else None)
    return block(key, 'Transform', 'EMPTY_ARROWS', gizmo_rows(context, hm, ok), False, say)


def _rows(items, per_row):
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def curve_hint(P, cs, round_ok, even_ok):
    """why the shape sliders of these curves are dimmed (roundAt 843, curveHandles 883 / 929, evenAnchors 974)"""
    if not round_ok:
        c = cs[len(cs) - 1]
        if c.ring or c.closed:
            return 'Roundness, Smoothing and Even Out do not shape a closed curve'
        if c.junc:
            return 'Roundness, Smoothing and Even Out do not shape a junction curve (Unite Locks makes them)'
        return 'Roundness, Smoothing and Even Out shape a curve of 3+ anchors: insert one (RMB on the curve)'
    if not even_ok:
        return 'Even Out moves the free inner anchors: every inner anchor here is shared with another curve or attached'
    return None


def more_curves(n):
    """what the title of a sheet says about the other curves selected: " (+2 curves)", or nothing"""
    return f' (+{n} curve{"" if n == 1 else "s"})' if n > 0 else ''


def _curve_actions(hm, P, cs):
    n = len(cs)
    it = cs[len(cs) - 1]
    round_ok = E.curveActs(P, cs, 'round'); even_ok = E.curveActs(P, cs, 'asm')
    recalc_ok = E.recalcActs(P, list(dict.fromkeys(a for c in cs for a in c.anchors)))
    return {
        'reverse': act('reverse'),
        'recalc': dim_op(act('recalc_curves'), recalc_ok),
        'delete': act('delete_curves', text='Delete Curve' if n == 1 else f'Delete {n} Curves'),
        'round': ('prop', 'curve_round', 'Roundness', *dim(round_ok)),
        'fair': ('prop', 'curve_fair', 'Smoothing', *dim(round_ok)),
        'even': ('prop', 'curve_even', 'Even Out Anchors', *dim(even_ok)),
        'shape_why': ' '.join(t for t in (curve_hint(P, cs, round_ok, even_ok), None if recalc_ok else RECALC_HINT) if t) or None,
        'label': ('label', it.name + more_curves(n - 1), 'CURVE_DATA'),
        'ring': bool(it.ring),
        'reverse_s': act('reverse', short=True),
        'recalc_s': dim_op(act('recalc_curves', short=True), recalc_ok),
        'delete_s': act('delete_curves', short=True),
        # 3.4: optimize the curves' anchors; merge coincident anchors (2+ curves)
        'optimize': act('optimize'),
        'merge': act('merge') if n >= 2 else None,
        'optimize_s': act('optimize', short=True),
        'merge_s': act('merge', short=True) if n >= 2 else None,
        # 3.5: the wall items of the original's curve pie (surfaceMenuItems, 6157) and the walls these curves drive
        'walls_rows': _rows(ops_walls.curve_wall_items(P, cs, short=True), 2),
        'drives': ops_walls.drives_text(P, cs),                     # (lot 4.3, item 1: named, no Select buttons)
        # 3.6: the outliner's drag into another group, as a submenu
        'move': MOVE_MENU,
    }


def _subtool_items():
    return [('op', 'hairmesh.set_subtool', label, icon, {'subtool': key, 'activate': True}) for key, label, _d, icon, _n in SUBTOOLS]


def _subtool_buttons():
    """the sub-tool buttons of the D's top row: icons alone, every name in its tooltip (the descriptions start with it)"""
    return [('subtool', key, '', icon) for key, _label, _d, icon, _n in SUBTOOLS]


def subtool_name(hm):
    """the active sub-tool's name, a label beside the icons: no button changes width with the sub-tool (lot 4.8b: Blender
    reuses the clicked button for a frame, its icon over the new text, and a growing button moved the row under it)"""
    return next((label for key, label, _d, _i, _n in SUBTOOLS if key == hm.subtool), '')


def subtool_name_units(sc=None):
    """the room of that name: the widest of them whole, with a label's margins and what Blender's clipping keeps (only the
    window shows a cut name: tools/sim_c49b_names.py, on the pixels)"""
    return max(fit.units(t[1], sc) for t in SUBTOOLS) + NAME_PAD


def _selection(context):
    """(root, P, selected anchors, selected curves, the last selected wall or None, the selected group or None): the
    anchors come first, as in the original's renderProps (8627); a wall or a group shows when it is the LAST
    selected item (3.5, U1)."""
    root, P, sel = select.current(context)
    anchors = select.anchors_of(P, sel) if P is not None else []
    cs = select.curves_of(P, sel) if P is not None else []
    wall = select.last_wall(P, sel) if P is not None and not anchors else None
    group = select.selected_group(P, sel) if P is not None and not anchors else None
    return root, P, anchors, cs, wall, group


def group_rows(context, P, room=ROOM):
    """The group sheet (the original's group panel, 8645-8689, plus the outliner's actions) as rows for the selected
    group, by category (part 2 of the lot of 16/10/2026): the group itself (name, color, Visible, New Sub-group /
    Rename / Delete, the group it lives in and Move to Group), then the blocks Display (the curves mode, Free
    Curves), Conversion (Convert This Group, the loop step with Match and the project's, the sliders, Merge Shared
    Trunks, Container), Symmetry (closed by default) and Connections (Detach, closed by default). With no group
    selected the rows of an empty Selection tab (New Group, the curves hidden by Auto). Returns (rows, title).
    U4 (15/10/2026): the group's Loop Step slider edits the group alone; the project step is shown beside it and
    edited in the Conversion tab. 21/10/2026: Visible is the eye beside the name; a color covered by a group above
    says "Color from <group>" beside a dimmed swatch."""
    root, P_, sel = select.current(context)
    g = select.selected_group(P, sel)
    if g is None:
        return [[('label', 'Nothing selected', 'INFO')], [act('new_group')], *hidden_rows(P, sel, context, room)], '(root level)'
    # the colour (part 2 of 21/10/2026): when a group above has a colour, that colour is the one the curves and walls
    # show (tintOf 754) — a row says where it comes from; the swatch is locked while it is covered (part 3 of the
    # second manual test of 21/10/2026: changing it did nothing). A group with a colour of its own gets Clear Color
    # ("Reset colour" of the group panel, 8649). Visible beside the name (an eye toggle). Part 2 of the lot ux7: the
    # name, the swatch, Clear Color and the eye in one row of fixed columns (the title row said the name twice); a name
    # too long for its field is said whole in a paragraph above it
    cov = G.colorCoveredBy(P, g)
    swatch = ('prop', 'group_color', '', {'enabled': False}) if cov is not None else ('prop', 'group_color', '')
    clear = [(1.0, [act('clear_color', text='')])] if g.color else []
    eye = (1.0, [('toggle', 'group_visible', '', 'HIDE_OFF' if g.visible else 'HIDE_ON')])
    name_room = room - 2.0 - (1.0 if g.color else 0.0) - 1.0
    if fit.button_need(g.name, True) <= name_room:
        rows = [[cols((None, [('prop', 'group_name', '', {'icon': 'OUTLINER_COLLECTION'})]), (2.0, [swatch]), *clear, eye)]]
    else:                                                           # (a name its field would cut: said whole, renamed with Rename, F2 or a double click in the outliner)
        rows = [[text(g.name, 'OUTLINER_COLLECTION')], [cols((None, [swatch]), *clear, eye)]]
    if cov is not None:
        rows.append([text(f'Color from {cov.name}', 'LINKED')])
    rows += [[act('new_subgroup', short=True), act('rename_group', short=True), act('delete_group', short=True)],
             *label_and(group_label(P, g), MOVE_MENU, room, dim_op(act('detach', short=True), E.detachActs(P, g)))]   # (Detach beside them: the Connections block held it alone — part 1 of the lot ux7, the rows the hints need)
    hair = not g.guidesGroup and not g.meshesGroup
    # part 1 of the lot ux7: what changes nothing now is dimmed, with why and where (core/effects.py)
    mode_ok = E.modeActs(P, g); loose_ok = E.looseActs(P, g) if hair else True
    display = [[cols((None, [enum('group_guides', 'Curves', mode_ok)]), (FREE_COL, [('toggle', 'group_loose', 'Free Curves', 'CURVE_DATA', *dim(loose_ok))]))] if hair else [enum('group_guides', 'Curves', mode_ok)]]
    dwhy = (('No curves of its own: set the mode on its sub-groups.' if G.subGroups(P, g)[1:] else 'No curves yet: the mode acts on the curves drawn here.') if not mode_ok else   # (guidesShown 11914 reads the mode of an item's own group)
            ('Free Curves acts in Auto mode only.' if (g.guides or 'auto') != 'auto' else 'Free Curves: no free curve here (one with no wall, modifier or crown).') if not loose_ok else None)
    rows.append(block('group_display', 'Display', 'HIDE_OFF', display, True, dwhy))
    if hair:
        ps = G.projectStep(P); off = G.stepOff(P, g)
        _g, _d, state, via = next((r for r in G.convTree(P) if r[0] is g), (g, 0, 'off', None))
        if state == 'via':                                          # part 2 of 21/10/2026: included through a ticked group above — the list's dimmed ticked box (the click: a mesh of its own)
            kind, unit = ops_groups.where_in(P, g, via)             # U7 A: merged / apart / in another sub-group's piece
            full = f'Converted with {via.name}' + (f' (piece of {unit.name})' if kind == 'with' else f', {kind}')
            if fit.button_need(full, True) <= room - BOX_PAD:
                tick_rows = [[('op', 'hairmesh.tick_group', full, 'CHECKBOX_HLT', {'group': g.id, 'toggle': True}, {'active': False})]]
            else:                                                   # (long names: the button and what it means under it, never cut)
                more = f'Into the mesh of {via.name}, ' + (f'in the piece of {unit.name}.' if kind == 'with' else 'merged with the rest of it.' if kind == 'merged' else 'a piece of its own.')
                tick_rows = [[('op', 'hairmesh.tick_group', 'Converted with a Group Above', 'CHECKBOX_HLT', {'group': g.id, 'toggle': True}, {'active': False}), why_item(more)]]
        else:
            makes = state != 'ticked' or g.id in {x.id for x, _n, _k in (ops_groups.plan_of(P) or [])}
            tick_rows = [[('toggle', 'group_ticked', 'Convert This Group' + (' (No Mesh)' if not makes else ' (Own Mesh)' if state == 'ticked' and via is not None else ''), 'CHECKBOX_HLT')]]
        # U4 (15/10/2026): the group's step edits the group alone; Match gives it (and the groups inside) the project
        # loop step, which the button says (part 2 of the lot ux7: the row "Project loop step" joined the button)
        match = dim_op(act('match', text=f'Match Project: {ops_groups.step_text(ps)}'), E.matchActs(P, g))   # (dimmed: already the project's)
        if off:
            match = (*match[:3], 'ERROR', *match[4:])
        locks, _crowns = E.convReaders(P, {g.id}); step_ok = E.stepReaders(P, {g.id}); lock_ok = locks > 0
        mwhy = E.mergeWhy(P, g); cont_ok = E.containerActs(P, g)
        conv = [*tick_rows,
                [('prop', 'group_step', 'Loop Step', *dim(step_ok)), match],
                [('prop', 'group_dev', 'Deviation', *dim(lock_ok)), ('prop', 'group_straighten', 'Fix Sharp Angles', *dim(lock_ok))],
                [('prop', 'group_loop_orient', 'Loops Horiz ↔ Perp', *dim(lock_ok))],
                [enum('group_tip_mode', 'Tips', lock_ok), ('prop', 'group_relax', 'Relax', *dim(lock_ok))],
                [('toggle', 'group_merge_trunks', 'Merge Shared Trunks', MERGE_ICON, *dim(mwhy is None)), ('toggle', 'group_container', 'Container', 'FILE_FOLDER', *dim(cont_ok))]]
        rows.append(block('group_conversion', 'Conversion — This Group Only', 'MOD_REMESH', conv, True, conversion_why(P, g, lock_ok, step_ok, mwhy, cont_ok, state) or None))   # 1c (21/10/2026): selecting a group does not tick it; these sliders edit it alone (setGroupConv), the Conversion tab the ticked groups
    on, ax, plane = G.symInfo(P, g)
    swhy = E.symWhy(P, g)
    sym = [[('toggle', 'group_sym_on', 'Symmetry', 'MOD_MIRROR', *dim(swhy is None)), enum('group_sym_axis', 'Axis', on and swhy is None)],
           [dim_op(('op', 'hairmesh.apply_symmetry', 'Apply Symmetry', 'MOD_MIRROR', {}), on)]]   # 4.10: applySymmetry (8689: disabled while off — dimmed, its tooltip says why)
    say = f'Mirror Plane: {ax.upper()} = {round(plane, 2) + 0.0:.2f} (Head Center). ' + (sym_why_text(g, swhy) if swhy else SYM_ON_CONVERT)   # (it acts, but only on Convert: when it shows)
    rows.append(block('group_symmetry', 'Symmetry', 'MOD_MIRROR', sym, False, say))
    return rows, g.name


def conversion_why(P, g, lock_ok, step_ok, mwhy, cont_ok, state):
    """why the conversion values of a group's sheet are dimmed, and where to do what was probably wanted (part 1 of
    the lot ux7; core/effects.py: a lock reads the values of the group above its first curve, convOf 1672)"""
    out = []
    own = any(E.nearestGroup(P, s) is g for s in P.surfaces())
    inside = any(G.isDescendant(P, s.id, g.id) for s in P.surfaces())
    if not lock_ok:
        if not own and inside:
            out.append(f'No walls of its own: its sub-groups use their own settings. To change them all, tick {g.name} in the Conversion tab.')
        elif not own:
            out.append('No walls yet: these values apply to the walls made here.')
        else:
            out.append('No lock reads these values: a lock takes them from the group of its first curve.')
        if step_ok:
            out.append('The Loop Step still acts (' + {'crown': 'its crown', 'curves': 'Optimize Anchors on its curves', 'project': 'the project loop step'}.get(step_ok, 'the locks') + ').')
        mwhy = None                                                 # (Merge: said by the first sentence; Container below, it has its own reasons)
    elif mwhy is not None:
        if mwhy[0] == 'off':
            out.append(f'{"Merge and Container act" if not cont_ok else "Merge acts"} on Convert: tick {g.name} (Conversion tab).')
            cont_ok = True                                          # (said)
        elif mwhy[0] == 'one':
            out.append('Merge: one lock here, nothing to merge.')
        elif mwhy[0] == 'from':
            out.append(f'Merge: the mesh of {mwhy[1].name} takes it from {mwhy[2].name if mwhy[2] else "the project"}.')
    if not cont_ok:
        m = E.meshOf(P, g)
        if not G.subGroups(P, g)[1:]:
            out.append('Container: no sub-groups.')
        elif m is None:
            out.append(f'Container acts on Convert: tick {g.name}.')
        else:
            out.append('Container: its sub-groups are ticked on their own.' if state == 'ticked' else f'Container: no sub-group with walls goes into the mesh of {m.name}.')
    return ' '.join(out)


SYM_ON_CONVERT = 'Mirror this group on Convert, or click Apply Symmetry to generate editable curves now'
# 3b of the lot ux8: in a ticked group with no walls the Loop Step stays live — setConv (9105) makes it the project loop
# step S.step too, which the welded-trunk backfill between locks reads (backfillTrunk with CP = S, 2726) and the groups
# made from now on start with (createGroup -> groupConv -> defaultStep)
STEP_LIVE_HINT = ('The ticked groups have no walls yet: these values apply to the walls made in them. The Loop Step stays live: '
                  'it also sets the project loop step, used by the welded-trunk backfill between locks and by the groups made from now on.')
RECALC_HINT = 'Recalculate: nothing to reset. It acts only on a handle frozen by "Delete Anchor, Keep Shape" or on a Smoothness below 1.'


def sym_why_text(g, why):
    """why the Symmetry switch of a group changes nothing now (core/effects.symWhy)"""
    if why[0] == 'mesh':
        return f'{g.name} goes into the mesh of {why[1].name}, which follows the Symmetry of {why[1].name} (and of the groups above it): switch it there.'
    if why[0] == 'inside':
        return f'The meshes inside follow {why[1].name}, whose own Symmetry is on.'
    return f'Symmetry acts on Convert: tick {g.name} or a parent group in Conversion'


def label_and(label, item, width=ROOM, *more):
    """a label and items side by side when the label shows whole in its share of the row, else the label as a
    paragraph above the items (part 2b of the lot ux7: "Group: <a long name>" was cut)"""
    if fit.label_need(label[1], label[2] not in (None, 'NONE')) <= width / (2 + len(more)):
        return [[label, item, *more]]
    return [[text(label[1], label[2])], [item, *more]]


def hidden_rows(P, sel, context=None, room=ROOM):
    """U2 (15/10/2026): the curves a group's Auto mode hides right now, by group — the user must never think a curve
    is lost; selecting the group (in the Outliner, O) shows them (lot 4.3, item 1: no selection buttons; before, each
    group was a button that selected it). Nothing when nothing is hidden.
    1b of the manual test of the lot ux5: a tree as the outliner's and the Conversion list's — the groups with hidden
    curves and the groups above them, the same indentation, the same arrows and the same open / closed groups
    (ops_outliner.is_open; all open without a context); a closed group says how many are hidden inside it."""
    from ..core import edit
    shown = {c.id for c in G.shownCurves(P, sel.items, sel.anchors)[0]}
    own = {}
    for c in P.curves():
        if c.ring or len(c.anchors) < 2 or not edit.isVisible(P, c) or c.id in shown:
            continue
        g = P.parentOf(c)
        if g is not None and g.type == 'group' and (g.guides or 'auto') == 'auto':
            own[g.id] = own.get(g.id, 0) + 1
    if not own:
        return []
    tree = []                                                       # (group, depth, its own hidden curves, hidden inside its sub-groups) of the groups with any, outliner order

    def walk(ids, depth):
        total = 0
        for iid in ids:
            g = P.items.get(iid)
            if g is None or g.type != 'group':
                continue
            at = len(tree); tree.append(None)
            inside = walk(g.children, depth + 1)
            n = own.get(g.id, 0)
            tree[at] = (g, depth, n, inside) if n + inside else None
            total += n + inside
        return total
    walk(P.roots, 0)
    tree = [t for t in tree if t is not None]
    from . import ops_outliner
    n = sum(own.values())
    rows = [[text(f'{n} curve{"s" if n != 1 else ""} hidden by Auto (select the group in the Outliner, O, to show them):', 'HIDE_ON')]]
    closed = None
    for i, (g, depth, mine, inside) in enumerate(tree):
        if closed is not None:
            if depth > closed:
                continue                                            # inside a closed group
            closed = None
        kids = i + 1 < len(tree) and tree[i + 1][1] > depth
        opened = ops_outliner.is_open(context, g) if kids and context is not None else True
        parts = [g.name] + ([f'({mine})'] if mine else []) + ([f'· {inside} inside'] if inside and not opened else [])
        rows += tree_rows(depth, tree_arrow(g, kids, opened), ('label', '', 'OUTLINER_COLLECTION'), parts, room=room)
        if not opened:
            closed = depth
    return rows


def tree_arrow(g, kids, opened):
    """the arrow of a tree row that opens / closes the group (the outliner's state), or a blank icon"""
    if not kids:
        return ('indent', 1)
    return ('op', 'hairmesh.outliner_expand', '', 'DISCLOSURE_TRI_DOWN' if opened else 'DISCLOSURE_TRI_RIGHT', {'group': g.id}, {'emboss': False})


def tree_rows(depth, arrow, op, parts, right=(), room=ROOM):
    """a row of a tree (the Conversion list, the hidden curves) as fixed columns (part 2 of the lot ux7): the
    indentation and the arrow, the button `op` with the text `parts` (the name, then what is said about it), the
    columns `right` ((units, [items]) ...). A text too long for the button goes on in lines under it, never cut: the
    first line on the button, the rest as labels where the button's text starts."""
    left = depth + 1.0
    fixed = sum(u for u, _its in right)
    room_text = max(TREE_TEXT_MIN, room - left - fixed - fit.ICON - fit.CLIP_PAD)   # (a very narrow panel: the 'cols' row stacks rather than splitting words letter by letter)
    lines = fit.wrap_parts(parts, room_text, sep=' ')
    first = ('label', lines[0], op[2]) if op[0] == 'label' else (op[0], op[1], lines[0], *op[3:])
    lead = ([('indent', depth)] if depth else []) + [arrow]
    rows = [[cols((left, lead), (None, [first], {'align': 'LEFT'}), *right)]]
    for ln in lines[1:]:
        rows.append([cols((left, [('indent', depth)] if depth else []), (None, [('label', ln, 'BLANK1')], {'align': 'LEFT'}), *[(u, []) for u, *_r in right])])
    return rows


def defaults_text(P):
    """1d of the second manual test of 21/10/2026: with nothing ticked, who reads the project defaults (S) the sliders
    edit — the locks outside every group (groups.defaultsUsers; a crown outside them reads the step alone) — and, when
    no lock does, that the sliders change nothing but the Loop Step (the project loop step: backfillTrunk's tolerance
    between welded locks, the ≠ marks). Returns (the text, whether no lock reads them: the other sliders dimmed)."""
    nl, nk, total = G.defaultsUsers(P)
    crowns = f' and {nk} crown{"" if nk == 1 else "s"} (the Loop Step only)' if nk else ''
    if nl:
        return f'Project defaults: used by {nl} lock{"" if nl == 1 else "s"}{crowns} outside any group. New groups start with these values.', False
    if not total:
        return (f'Project defaults: no lock yet{crowns}. Locks made outside any group will use them; new groups start '
                'with these values.'), True
    return (f'Project defaults: used by 0 locks{crowns} — every lock is in a group with its own values. Only the Loop '
            'Step acts here (welded trunks between locks, the ≠ marks); new groups start with these values.'), True


STEP_COL = 2.4                                                      # the tree's loop step column ("15.5 ≠", "1550 ≠")
TREE_TEXT_MIN = 3.0                                                 # the least room a tree's text wraps in
ONLY_COL = 1.0                                                      # its Only This column (★)
MESH_COL = 1.0                                                      # its column of the mesh icon (a group that makes a mesh of its own)
LIST_PAD = 1.6                                                      # what a list takes from its width (its scroll bar, its margins)
TREE_MIN = 4                                                        # the fewest tree rows the Conversion tab shows (the wheel scrolls past them)
TREE = {'P': None, 'entries': {}, 'walls': {}, 'room': ROOM}        # what the list's filter_items found for its draw_item (every draw of the list writes it anew; the model, no RNA)


def conversion_tree(context, P):
    """Part 2 of the second manual test of 21/10/2026 (ours; the original lists the groups flat, renderConvList
    9030): the Conversion list as the HairMesh Outliner's tree — the same indentation, the same arrows and the same
    open / closed groups (ops_outliner.is_open: the file's `expanded`, then the user's switches of this session).
    The entries shown (a closed group's insides left out): [(group, depth, state, via, kids, opened, ticked inside,
    makes a mesh, groups inside)]."""
    from . import ops_outliner
    tree = G.convTree(P)
    makes = {g.id for g, _n, _k in (ops_groups.plan_of(P) or [])}   # U7 A: a ticked group that makes no mesh says so
    out = []; closed = None
    for i, (g, depth, state, via) in enumerate(tree):
        if closed is not None:
            if depth > closed:
                continue                                            # inside a closed group
            closed = None
        kids = i + 1 < len(tree) and tree[i + 1][1] > depth
        opened = ops_outliner.is_open(context, g) if kids else True
        inside = total = 0
        if not opened:                                              # closed: the groups ticked inside it would not show — the row says how many
            for x, d, s, _v in tree[i + 1:]:
                if d <= depth:
                    break
                inside += s == 'ticked'; total += 1
        out.append((g, depth, state, via, kids, opened, inside, g.id in makes, total))
        if not opened:
            closed = depth
    return out


def only_item(g, P=None):
    """the ★ (Only This) of a group's row (dimmed when it is already the only group ticked)"""
    opts = {'list_text': f'Only {g.name}'}
    if P is not None and not E.tickOnlyActs(P, g):
        opts['active'] = False
    return ('op', *ACT['tick_only'][:1], '', ACT['tick_only'][3], {**ACT['tick_only'][4], 'group': g.id}, opts)


def tree_entry_row(P, entry, room=ROOM, walls=None):
    """one group of the Conversion list as a 'cols' row: the arrow, the checkbox with the name, its walls and, closed, how
    many of the groups inside are ticked (ops_groups.tick_parts, whole in the checkbox's tooltip), the mesh icon where
    it makes a mesh of its own, its own loop step ("≠" when it differs from the project's, U4) and Only This. A list has
    no second line: a narrow panel drops the step, then the count. `walls`: ops_groups.wall_counts, for a list of rows"""
    g, depth, state, via, kids, opened, inside, makes, total = entry
    walls = ops_groups.wall_counts(P) if walls is None else walls
    _parts, op = ops_groups.tick_parts(P, g, state, via, inside, makes, walls)
    said = f'{g.name} ({walls.get(g.id, 0)})' + (f' · {inside}/{total}' if total else '')
    st = ops_groups.step_text(G.convShown(P, g)['step']) + (' ≠' if G.stepOff(P, g) else '')
    left = depth + 1.0
    width = room - LIST_PAD
    free = width - left - MESH_COL - ONLY_COL - fit.ICON - fit.CLIP_PAD
    right = [(STEP_COL, [('label', st, 'NONE')])]
    if fit.units(said) > free - STEP_COL:
        right = []
        if fit.units(said) > free:
            said = g.name
    mesh = ('label', '', 'OUTLINER_OB_MESH' if makes else 'BLANK1')
    lead = ([('indent', depth)] if depth else []) + [tree_arrow(g, kids, opened)]
    return cols((left, lead), (None, [(op[0], op[1], said, *op[3:])], {'align': 'LEFT'}), (MESH_COL, [mesh]), *right, (ONLY_COL, [only_item(g, P)]))


def tree_list_rows(context, P, room=ROOM):
    """the rows of the Conversion list, one per group shown (what its draw_item draws; the tests)"""
    return [[tree_entry_row(P, e, room)] for e in conversion_tree(context, P)]


class HAIRMESH_UL_conversion(bpy.types.UIList):
    """The Conversion tab's groups: a list, so the wheel scrolls it in the pop-up (hm.rows has one row per group; the
    tree's order, depth and open / closed groups come from conversion_tree)"""
    bl_idname = 'HAIRMESH_UL_conversion'

    def filter_items(self, context, data, propname):
        rows = getattr(data, propname)
        _r, P, _s = select.current(context)
        TREE['P'] = P
        TREE['entries'] = {e[0].id: e for e in conversion_tree(context, P)} if P is not None else {}
        TREE['walls'] = ops_groups.wall_counts(P) if P is not None else {}
        pos = {gid: i for i, gid in enumerate(TREE['entries'])}
        name = self.filter_name.lower()                             # (the list's own search field)
        shown = [r.gid in pos and name in TREE['entries'][r.gid][0].name.lower() for r in rows]
        by_tree = sorted((i for i, on in enumerate(shown) if on), key=lambda i: pos[rows[i].gid])
        order = [0] * len(rows)
        for new, i in enumerate(by_tree + [i for i, on in enumerate(shown) if not on]):
            order[i] = new
        return [self.bitflag_filter_item if on else 0 for on in shown], order

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        e = TREE['entries'].get(item.gid)
        if e is not None:
            draw_item(layout, data, tree_entry_row(TREE['P'], e, TREE['room'], TREE['walls']), TREE['room'] - LIST_PAD)


NO_GROUPS = 'No groups to open or close yet'


def _has_groups(context):
    _r, P, _s = select.current(context)
    return P is not None and bool(P.groups())


class HAIRMESH_OT_expand_all(bpy.types.Operator):
    """Open every group of the list, or close them all when they are all open"""
    bl_idname = 'hairmesh.expand_all'
    bl_label = 'Expand / Collapse All'
    bl_options = {'REGISTER', 'INTERNAL'}                           # UI state: no undo step

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    @classmethod
    def description(cls, context, properties):
        return cls.__doc__ if _has_groups(context) else NO_GROUPS

    def execute(self, context):
        from . import ops_outliner
        if not _has_groups(context):
            self.report({'INFO'}, NO_GROUPS)
            return {'CANCELLED'}
        ops_outliner.expand_all(context)
        return {'FINISHED'}


def capped(lead, names, room, lines=2, icon=True):
    """`lead` and as many of `names` as fit in `lines` lines of `room` units, the rest counted ("+3 more")"""
    room -= (fit.ICON if icon else 0.0) + fit.LABEL_PAD
    for k in range(len(names), -1, -1):
        t = lead + ', '.join(names[:k]) + (f', +{len(names) - k} more' if k < len(names) else '')
        if len(fit.wrap(t, room)) <= lines:
            return t
    return lead + f'{len(names)}'


def meshes_text(P, room=ROOM):
    """U7 A of docs/fase-3-ux-revisao.md (ours): the meshes Convert makes (convert.conversionPlan: startConversion's
    collect on ids, nothing built), named after their groups — as many names as two lines hold"""
    plan = ops_groups.plan_of(P)
    if plan is None:
        return 'Convert makes: unknown (an item of the project is missing).'
    if not plan:
        ticked = set(P.settings.convChecked)
        return ('Convert makes no mesh: nothing ticked.' if not any(g.id in ticked for g in G.groupsOf(P)) else
                'Convert makes no mesh: the ticked groups have no walls.')
    names = [g.name for g, _n, _k in plan]
    return capped(f'Convert makes {len(names)} mesh{"" if len(names) == 1 else "es"}: ', names, room)


def sliders_text(P, t, room=ROOM):
    """what the Conversion sliders edit: the ticked groups and the groups nested in them (convTargets 9045)"""
    n = len(G.groupsOf(P))
    if len(t) == n > 3:
        return f'Sliders edit all {n} groups (ticked, or inside a ticked group).'
    return capped(f'Sliders edit the ticked groups and the groups inside them ({len(t)}): ', [g.name for g in t], room)


def conversion_rows(context, P, room=ROOM, list_menu=False):
    """The Conversion tab (renderConvList + the sliders, 9028-9110): Tick Top Groups / Tick Every Group / Uncheck All (U7
    C) and Expand / Collapse All, the groups' list with their own loop step (U4) and Only This, one line saying what
    Convert makes and what the sliders edit (the ticked groups and the groups nested in them: convTargets 9045; with
    nothing ticked the project defaults), the names in its tooltip, the sliders (dimmed when they change nothing), the
    loop step from the active object (the reference cube's place), Convert. The list shows as many groups as the 18
    rows leave room for; the wheel scrolls the rest."""
    below = conversion_below(context, P, room)
    if not G.groupsOf(P):
        return [[it] for it in ops_groups.tick_items(P)] + below
    head = [dim_op(act('tick_top', short=True), E.tickTopActs(P)), dim_op(act('tick_all', short=True), E.tickAllActs(P, True)),
            dim_op(act('untick_all', short=True), E.tickAllActs(P, False))]
    if list_menu:                                                   # Object > HairMesh: every group, its whole text (a menu widens to it), no list
        return [head] + [[ops_groups.tick_item(P, e[0], e[2], e[3], e[6], e[7]), only_item(e[0], P)] for e in conversion_tree(context, P)] + below
    expand = (1.0, [('op', 'hairmesh.expand_all', '', icons.EXPAND_ALL, {}, {'list_text': 'Expand / Collapse All'})])
    if row_fits(context, head, room - 1.0):
        top = [[cols((None, head), expand)]]
    else:                                                           # (a narrow N panel: one button per row, Expand / Collapse All beside the first)
        top = [[cols((None, [head[0]]), expand)]] + [[it] for it in head[1:]]
    n = len(conversion_tree(context, P))
    return top + [[('list', max(1, min(n, tree_space(context, below, room))), room)]] + below


def tree_space(context, below, room=ROOM):
    """the rows the Conversion list gets: what MAX_ROWS leaves after the tick row, the list's grip and the rows under it"""
    return max(TREE_MIN, MAX_ROWS - 2 - visible_rows(context, below, room))


def conversion_below(context, P, room=ROOM):
    """the rows of the Conversion tab under the tree: what Convert makes, what the sliders edit, the sliders, Convert"""
    t = G.convTargets(P)
    t = [g for g in t if not g.guidesGroup and not g.meshesGroup]   # (a service group nested in a ticked one is a target too, never listed)
    idle = False
    plan = ops_groups.plan_of(P)
    m = len(plan) if plan else 0
    said = ['Convert makes no mesh' if not m else f'Convert makes {m} mesh{"" if m == 1 else "es"}',
            f'Sliders edit {len(t)} group{"" if len(t) == 1 else "s"}' if t else 'Sliders edit the project defaults']
    tip = [meshes_text(P, 1e6)] if G.groupsOf(P) else []            # (the names in the tooltip of the line's "i")
    if t:
        tip.append(sliders_text(P, t, 1e6))
    else:
        dtext, idle = defaults_text(P)
        tip.append(dtext)
    merge_ok = True
    if t and not idle:                                              # part 1 of the lot ux7: the ticked groups (and the groups inside them) read by no lock
        ids = {g.id for g in t}
        if not E.convReaders(P, ids)[0]:
            idle = True
            tip.append(STEP_LIVE_HINT)
        elif not E.mergeReaders(P, ids):
            merge_ok = False
            tip.append('Merge Shared Trunks: no piece of these meshes has two locks that take it from the ticked groups.')
    below = [[('label', ' · '.join(said), 'OUTLINER_OB_MESH'), why_item('\n'.join(tip))]]
    d = dim(not idle)                                               # (a value nothing reads: dimmed, still editable for the walls to come)
    below += [[cols((None, [('prop', 'conv_step', 'Loop Step')]), (1.0, [act('step_from_object', short=True)]))],
              [('prop', 'conv_dev', 'Deviation', *d), ('prop', 'conv_straighten', 'Fix Sharp Angles', *d)],
              [('prop', 'conv_loop_orient', 'Loops Horiz ↔ Perp', *d)],
              [enum('conv_tip_mode', 'Tips', not idle), ('prop', 'conv_relax', 'Relax', *d)],
              [('toggle', 'conv_merge_trunks', 'Merge Shared Trunks', MERGE_ICON, *dim(merge_ok and not idle))]]
    below += convert_rows(context, P, short=True)
    return below


def convert_items(P, short=False):
    """3.7: Convert, Confirm, Cancel (the original's three buttons, 10037-10039) and the Live / Baked view (ours), each
    dimmed when it would do nothing now (the tooltip says why), the current view pressed"""
    from ..core import conversion as CONV
    view = CONV.viewOf(P)
    pend = bool(P.conv)
    live, baked = act('view_live', short=short), act('view_baked', short=short)
    # (a radio pair: the current view pressed — clicking it again only says so —; both dimmed with no mesh yet)
    live = (*live[:5], {'depress': view == 'live', **({'active': False} if view is None else {})})
    baked = (*baked[:5], {'depress': view == 'baked', **({'active': False} if view is None else {})})
    return {'convert': dim_op(act('convert', short=short), not convert_why(P)), 'confirm': dim_op(act('confirm', short=short), pend),
            'cancel': dim_op(act('cancel_conv', short=short), pend), 'live': live, 'baked': baked}


def conversion_state(context, P):
    """3.7: what the converted meshes are now, said where Convert is (the original's #convState, 10035, and ours:
    out of date, the view), or None"""
    from ..core import conversion as CONV
    from . import meshes
    if P.conv:
        n = len(P.conv['entries'])
        return f'Preview: {n} mesh{"" if n == 1 else "es"} (orange). Adjust the sliders, then Confirm or Cancel.'
    ms = CONV.meshes(P)
    if not ms:
        return None
    root = session.find_root(context)
    old = meshes.out_of_date(root, P) if root is not None else []
    n = len(ms)
    if old:
        return f'{len(old)} of {n} converted meshes changed since (edits): Convert again rebuilds only those.'
    view = CONV.viewOf(P)
    return f'{n} converted mesh{"" if n == 1 else "es"}' + (' shown (Baked).' if view == 'baked' else ' hidden (Live).' if view == 'live' else ', some shown.')


def convert_rows(context, P, short=True):
    """the rows of Convert in the Conversion tab: the state, Convert · Confirm · Cancel · Live · Baked — item 8 of the
    lot after 4.8 (the owner's request): Convert keeps its text, the four others are icons named by their tooltips and
    in the list menu (long_name), one row (the tab is at its 18 rows: the tree gets it)"""
    c = convert_items(P, short)
    st = conversion_state(context, P)
    return ([[text(st, 'MESH_DATA')]] if st else []) + [[c['convert'], *icon_only(c['confirm'], c['cancel']), ('sep',), *icon_only(c['live'], c['baked'])]]


def icon_only(*items):
    """'op' items without their text (their long text in the list menu: long_name, or the 'list_text' they carry)"""
    return [without_text(it) for it in items]


def convert_why(P):
    """why Convert makes nothing now (the original's toasts, startConversion 10012 / 10023), else None"""
    from .convert_op import nothing_to_convert
    try:
        return nothing_to_convert(P)
    except Exception:                                               # (drawing must never raise: an inconsistent project)
        return None


def file_rows(root):
    """1c of the manual test of the lot ux5: the .hairmesh Save writes to and whether the project changed since
    (session.file_state: the text's key against the one saved). Part 2b of the lot ux7: the file's name, then its
    folder whole, wrapped (a shortened path was cut with "…")"""
    path, dirty = session.file_state(root)
    if not path:
        return [[text('Not saved to a .hairmesh yet (Save asks where)', 'FILE_BLANK')]]
    folder, name = os.path.split(path)
    rows = [[text(name, 'FILE_TICK' if not dirty else 'FILE')], [('label', f'in {short_folder(folder)}', 'BLANK1'), why_item(path)]]
    if dirty:
        rows.append([('label', 'Unsaved changes in the .hairmesh', 'ERROR')])
    return rows


def short_folder(folder, room=ROOM - 2.0):
    """the end of a folder's path, as many of its last folders as a row shows ("…\\reference\\examples"): the whole
    path is in the tooltip of the "i" beside it"""
    parts = [x for x in folder.replace('/', '\\').split('\\') if x]
    for k in range(len(parts)):
        t = ('…\\' if k else '') + '\\'.join(parts[k:])
        if fit.label_need(t, True) <= room:
            return t
    return '…\\' + parts[-1] if parts else folder


def save_acts(root):
    """whether Save writes anything: not when the .hairmesh holds this project already (no change here, none on the
    disk); without a file it asks where (it acts)"""
    path, dirty = session.file_state(root)
    return not path or dirty is not False or session.disk_changed(path)


MIGRATE_NO_PROJECT = ('Migrate Hair brings the hair of another .hairmesh into the project of this scene, and there is none yet: '
                      'start one with New Project (or Import .hairmesh), then migrate.')


def project_rows(context, root, P):
    """The Project tab: the name, the counts, the file (path, unsaved changes), the head, save / save as / import /
    new, migrate hair (3.7b) (without a project: new / import, and Migrate dimmed with what to do first)."""
    if root is None:
        return [[act('new_project', text='New Project...')], [act('import', text='Import .hairmesh...')],
                [dim_op(act('migrate'), False), why_item(MIGRATE_NO_PROJECT)]]
    head = session.head_object(root)
    rows = [[('label', root.get('hairmesh_project', root.name), 'FILE')]]
    if P is not None:
        rows.append([('label', ' · '.join(ops_groups.counts(P)), 'CURVE_DATA')])
    rows += file_rows(root)
    rows += [[act('set_head', text='Set Head' if head is None else f'Head: {head.name}')],
             [dim_op(act('save', short=True), save_acts(root)), act('export', short=True)],
             [act('import', short=True), act('new_project', short=True)],
             [act('migrate')]]
    return rows


def viewport_state(context):
    """(walls, a head to drag on, curves): what the viewport's switches have to act on"""
    from . import pick
    root, P, _sel = select.current(context)
    walls = bool(P is not None and P.surfaces())
    head_ok = bool(P is not None and P.head is not None and P.head.visible and pick.head_visible(root))   # (the modal's own test, ops_select: the drag follows the model's head)
    curves = bool(P is not None and any(not c.ring for c in P.curves()))
    return walls, head_ok, curves


def options_rows(context):
    """The Viewport tab: the live preview (and its rebuild), Drag on Head, the x-ray of the curves, the guide lines'
    opacity. Dimmed while they have nothing to act on — no wall to preview, no head to drag on, no curve to show
    through the head, no guide —, the reason in the "i" at the end of the row (a property has no tooltip of its own)."""
    from . import pick
    root, P, _sel = select.current(context)
    walls, head_ok, curves = viewport_state(context)
    guides = bool(P is not None and P.guides())

    def why(say):
        return [why_item(say)] if say else []
    head_says = None if head_ok else 'Drag on Head needs a head: Project > Set Head.' if P is None or P.head is None else 'The head is hidden: its eye in the HairMesh Outliner (O).'
    xray_says = ('No curves yet.' if not curves else
                 "In this view Blender's X-ray (Alt+Z) shows every curve and anchor through the head; this X-ray applies to the other views and the file." if pick.blender_xray(context) else None)
    return [[('toggle', 'live_preview', 'Live Preview', LIVE_ICON, *dim(walls)), dim_op(act('rebuild', short=True), walls), *why(None if walls else 'No walls yet: the live preview shows them as they are made.')],
            [('toggle', 'drag_on_head', 'Drag on Head', 'SNAP_FACE', *dim(head_ok)), *why(head_says)],
            [('prop', 'settings_xray', 'X-ray', *dim(curves)), *why(xray_says)],
            [('prop', 'settings_guide_opacity', 'Guide Opacity', *dim(guides)), *why(None if guides else 'No guides yet: Guides (4).')]]


def _flat(rows):
    return [it for row in rows for it in row]


def group_label(P, it):
    """the group an item lives in, as a label row of the curve / wall / group sections (3.6): the outliner shows it
    in the original; here the pop-up and the panel say it"""
    g = P.parentOf(it)
    return ('label', f'Group: {g.name}' if g is not None and g.type == 'group' else 'Group: (root level)', 'OUTLINER_COLLECTION')


def group_menu_items(context):
    """Group ▸ (the pie's SW slot without a selection, the list): the group ACTIONS on the selected group — new
    sub-group, rename, delete, match the project step, detach, visible, the curves mode — else New Group and a hint;
    the list of the groups is the HairMesh Outliner's (U6)."""
    root, P, sel = select.current(context)
    if P is None:
        return [('label', 'No HairMesh project', 'INFO')]
    g = select.selected_group(P, sel)
    if g is None:
        return [('label', 'No group selected (the HairMesh Outliner, O)', 'INFO'), act('new_group')]
    return [('label', g.name, 'OUTLINER_COLLECTION'), act('new_subgroup'), act('rename_group'), act('delete_group'), ('sep',),
            act('match'), act('detach'), ('prop', 'group_visible', 'Visible'), ('prop', 'group_guides', 'Curves')] + ([act('clear_color')] if g.color else []) + [('sep',), MOVE_MENU]


def conversion_menu_items(context):
    root, P, _sel = select.current(context)
    if P is None:
        return [('label', 'No HairMesh project', 'INFO')]
    return _list_items(conversion_rows(context, P, list_menu=True))


def move_to_group_menu_items(context):
    root, P, sel = select.current(context)
    if P is None:
        return [('label', 'No HairMesh project', 'INFO')]
    if not sel.items:
        return [('label', 'Select curves, walls or groups first', 'INFO')]
    last = select.last_item(P, sel)
    # 4.0: P / Shift+P first (greyed with the reason when the selection does not serve: their poll)
    return [act('parent_last'), act('unparent'), *ops_groups.move_to_group_items(P, last.parent if last is not None else None)]


def curve_rows(context, P, cs, room=ROOM):
    """The curve sheet (the original's curve panel, 8690-8731, and its pie's wall items) by category: the curve
    (name, the group it lives in; Move to Group, Delete), then Shape (Roundness, Smoothing, Even Out
    Anchors, Reverse / Recalculate, Optimize / Merge) and Walls (the wall-making items of the original's curve pie
    with 2+ curves, and "Drives:" — the walls these curves drive, named: lot 4.3, item 1, no selection buttons —
    a click on a wall in Select selects it)."""
    hm = context.scene.hairmesh
    c = _curve_actions(hm, P, cs)
    rows = [[c['label']], *label_and(group_label(P, cs[len(cs) - 1]), c['move'], room, c['delete_s'])]
    tb = next((t for t in P.tubes() if t.src == cs[len(cs) - 1].id), None)
    if tb is not None:                                              # (lot 4.7b, item 3, ours: the modifier of the curve, one click away — its sheet is the Modifiers')
        rows.append([('op', 'hairmesh.select', f'Its Modifier: {tb.name}', 'MOD_SCREW', {'item': tb.id})])
    shape = [[c['round']], [c['fair']]] + ([] if c['ring'] else [[c['even']]]) + [[c['reverse_s'], c['optimize_s'], c['recalc_s']]] + ([[c['merge_s']]] if c['merge_s'] else [])
    rows.append(block('curve_shape', 'Shape', 'CURVE_DATA', shape, True, c['shape_why']))
    walls = list(c['walls_rows'])
    if ops_walls.unite_offered(P, select.load(hm).prune(P).items):
        walls.append([ops_walls.UNITE])                             # (4.10: Unite Locks, the original's curve pie 7927)
    if c['drives']:
        walls.append([text(c['drives'], 'MOD_LATTICE')])
    if walls:
        rows.append(block('curve_walls', 'Walls', 'MOD_LATTICE', walls))
    why = gizmo_why(context, P)
    rows.append(gizmo_block('curve_transform', context, hm, why is None, why))   # 3.8d: the curves move as items (gizmoTargets 4575)
    from .ops_profile import CLIP
    if CLIP[0] is not None and not c['ring']:                       # (4.6: the curve panel's "Paste modifier" when one is copied and the curve is no ring, 8725)
        rows.append([('op', 'hairmesh.paste_modifier', 'Paste Modifier', 'PASTEDOWN', {})])
    return rows


def wall_rows(context, P, sf, room=ROOM):
    """The wall sheet (the surface panel, 8732-8822) by category: the wall (name, the group it lives in; Move to
    Group, Delete), then the blocks of ops_walls.wall_actions: Shape, Second Surface. The curves it joins beside its
    group; other curves selected in the title."""
    w = ops_walls.wall_actions(context.scene.hairmesh, P, sf, short=True)
    sel = select.load(context.scene.hairmesh).prune(P)
    unite = [[ops_walls.UNITE]] if ops_walls.unite_offered(P, sel.items) else []   # (4.10)
    label = ('label', w['label'][1] + more_curves(len(select.curves_of(P, sel))), w['label'][2])
    g = group_label(P, sf)
    where = ('label', f'{g[1].removeprefix("Group: ")} ({w["drivers"][1]})', g[2])
    return [[label], *label_and(where, MOVE_MENU, room, w['delete'])] + unite + w['blocks']


def pair(a, b, width=ROOM):
    """two labels side by side when each shows whole in its half of the row, else one above the other (a long
    name: part 2b of the lot ux7)"""
    if max(fit.label_need(a[1]), fit.label_need(b[1])) <= width / 2:
        return [[a, b]]
    return [[a], [b]]


def anchor_rows(context, P, anchors):
    """The anchors sheet (the original's anchor panel, 8629-8636, and the 3.4 part of its pie) by category: the
    selection (how many; Delete, Keep Shape), then Shape (Smoothness, Recalculate beside it), Weld / Split / Curves
    (between, through, optimize the selected, split, unweld, rotate junction) and Transform, closed."""
    hm = context.scene.hairmesh
    a = _anchor_actions(hm, P, anchors)
    rows = [[a['label']], [a['delete_s'], a['keep_s']],
            block('anchor_shape', 'Shape', 'SMOOTHCURVE', [[a['smooth'], a['recalc_s']]], True, a['smooth_why'])]
    if a['more_rows']:
        rows.append(block('anchor_weld', 'Weld / Split / Curves', 'DECORATE_KEYFRAME', a['more_rows']))
    rows.append(gizmo_block('anchor_transform', context, hm, True))
    return rows


def pie_slot(item):
    """G4: in a pie an item that does not apply now is gone, its direction stays empty (in the sheets it stays dimmed,
    the tooltip says why)"""
    if item is None:
        return ('empty',)
    k = item[0]
    opts = (item[5] if len(item) > 5 else {}) if k == 'op' else (item[3] if len(item) > 3 else {}) if k == 'prop' else (item[4] if len(item) > 4 else {}) if k in ('toggle', 'menu') else {}
    return item if opts.get('active', True) and opts.get('enabled', True) else ('empty',)


def pie_box(*items):
    """G3: the box of the N direction — up to 3 sliders, no action buttons, menus or notices; what does not apply is
    gone, an empty box leaves the direction empty"""
    kept = []
    for it in items:
        it = ('row', [x for x in map(pie_slot, it[1]) if x[0] != 'empty']) if it[0] == 'row' else pie_slot(it)
        if it[0] != 'empty' and (it[0] != 'row' or it[1]):
            kept.append(it)
    return ('box', kept) if kept else ('empty',)


def gizmo_slot(context, P):
    """G3: Gizmo in the SE direction where the selection can be transformed, else the direction stays empty"""
    return GIZMO_MENU if gizmo_why(context, P) is None else ('empty',)


def view_slots(P, c):
    """(W, E) of a confirmed conversion: the view to switch to (the current one is no action, G4)"""
    from ..core import conversion as CONV
    view = CONV.viewOf(P)
    return (('empty',) if view == 'live' else pie_slot(c['live'])), (('empty',) if view == 'baked' else pie_slot(c['baked']))


def mesh_pie_items(context, P, m):
    """the Shift+RMB pie with a converted mesh the last selected item: Cancel / Confirm while pending, else the views;
    Delete, Convert, the group it comes from (a selection no click gives at once), the Conversion menu"""
    c = convert_items(P)
    w, e = (pie_slot(c['cancel']), pie_slot(c['confirm'])) if m.pending else view_slots(P, c)
    src = P.parentOf(m)
    if src is None or src.type != 'group' or src.meshesGroup:
        s = P.items.get(next(iter(m.srcSurfs or ()), None))
        src = P.parentOf(s) if s is not None else None
    back = ('op', 'hairmesh.select', 'Select Source Group', 'OUTLINER_COLLECTION', {'item': src.id}) if src is not None and src.type == 'group' else ('empty',)
    return [w, e, ('op', 'hairmesh.delete_selected', 'Delete Mesh', 'X', {'keep_shape': False}), pie_slot(c['convert']), back, MENU_OP, CONV_MENU, gizmo_slot(context, P)]


def pie_items(context):
    """The 8 slots of the Shift+RMB pie for the current selection (W, E, S, N, NW, NE, SW, SE), by G3: HairMesh Menu in
    NE, Delete in S, Gizmo in SE where the selection can be transformed, a direction without an item empty. While a wall
    is being cut, the cut alone (openMainPie 7892: `if (S.surfCut) { openCutPie(…); return; }`)."""
    from .ops_scissors import cut_of, wall_cut_pie_items
    if cut_of(context) is not None:
        return wall_cut_pie_items(context)
    hm = context.scene.hairmesh
    if hm.subtool == 'EDGES':
        from .ops_edges import edges_pie_items
        got = edges_pie_items(context, MENU_OP)
        if got is not None:
            return got
    root, P, anchors, cs, wall, group = _selection(context)
    menu = MENU_OP
    last = select.last_item(P, select.load(hm).prune(P)) if P is not None else None
    if anchors:
        from .ops_guides import selected_ring
        ring = selected_ring(P, select.load(hm).prune(P))
        a = _anchor_actions(hm, P, anchors)
        recalc = pie_slot(a['recalc'])
        # the SW slot: the rest of the original's anchor pie as a submenu (curve between / through, optimize the selected
        # anchors, split, unweld, rotate junction); a disc's ring: Select the Disc, a selection no click gives at once
        if ring is not None:
            return [('op', 'hairmesh.disc_of_ring', 'Select the Disc', 'MESH_CIRCLE', {}), a['keep'], a['delete'], pie_box(a['smooth']), recalc, menu, a['more'], gizmo_slot(context, P)]
        return [recalc, a['keep'], a['delete'], pie_box(a['smooth']), act('duplicate'), menu, a['more'], gizmo_slot(context, P)]
    if wall is not None:
        # a wall selected (3.5): the surface panel's actions and settings (select its curves, delete, the sliders)
        pie = list(ops_walls.wall_actions(hm, P, wall)['pie'])
        pie[5] = menu; pie[6] = WALLS_MENU; pie[7] = gizmo_slot(context, P)
        return pie
    if group is not None:
        # a group selected: its actions, the panel's switches in the N box
        return [act('new_subgroup'), act('rename_group'), act('delete_group'),
                pie_box(('toggle', 'group_visible', 'Visible', 'HIDE_OFF'), enum('group_guides', 'Curves', E.modeActs(P, group))),
                MOVE_MENU, menu, CONV_MENU, gizmo_slot(context, P)]
    if last is not None and last.type == 'mesh' and not cs:
        return mesh_pie_items(context, P, last)
    if last is not None and last.type == 'crown':
        from .ops_crown import crown_pie_items
        return crown_pie_items(context, P, last)                    # (4.3: the parts of the crown — selections no click gives at once — and its actions)
    if last is not None and last.type == 'tube' and not cs:
        from .ops_profile import tube_pie_items
        return tube_pie_items(context, P, last)                     # (4.6: Convert, its curve, the radii, Copy / Paste)
    if last is not None and last.type in ('cap', 'skin', 'tip') and not cs:
        from .ops_topology import topo_pie_items
        return topo_pie_items(context, P, last)                     # (4.4: its controls, the Fill's Convert)
    if last is not None and last.type == 'guide' and not cs:
        from .ops_guides import guide_pie_items
        return guide_pie_items(context, P, last)                    # (lot 4.3, item 1: the guide's — Select Crossed Curves, derived, lives here)
    if cs:
        c = _curve_actions(hm, P, cs)
        # (the curves and a guide line selected last, as createGuide leaves them: its Select Crossed Curves in the NW
        # direction; the rest is the original's curve pie, 7922-7930)
        nw = (('op', 'hairmesh.guide_select_crossed', 'Select Crossed Curves', 'RESTRICT_SELECT_OFF', {}) if last is not None and last.type == 'guide' and last.kind != 'disc'
              else c['move'])
        return [c['reverse'], c['optimize'], c['delete'], pie_box(c['round'], c['fair'], *([] if c['ring'] else [c['even']])), nw, menu, WALLS_MENU, gizmo_slot(context, P)]
    if root is None:
        return [act('import'), act('new_project'), ('empty',), ('empty',), ('empty',), menu, ('empty',), ('empty',)]
    # nothing selected, a project (Group, Set Head and the sub-tools are in the HairMesh Menu and in the Q pie); while a
    # conversion is pending its Cancel and Confirm take W and E, as in the pending mesh's pie
    walls, head_ok, _curves = viewport_state(context)
    c = convert_items(P) if P is not None else None
    if c is None:
        return [('empty',), ('empty',), act('new_group'), ('empty',), ('empty',), menu, ('empty',), OUTLINER_OP]
    from ..core import conversion as CONV
    view = CONV.viewOf(P)
    w = pie_slot(c['cancel']) if P.conv else pie_slot(('toggle', 'live_preview', 'Live Preview', LIVE_ICON, *dim(walls)))
    e = pie_slot(c['confirm']) if P.conv else pie_slot(c['live'] if view == 'baked' else c['baked'])
    return [w, e, act('new_group'), pie_slot(c['convert']), pie_slot(('toggle', 'drag_on_head', 'Drag on Head', 'SNAP_FACE', *dim(head_ok))), menu,
            pie_slot(dim_op(act('save'), save_acts(root))), OUTLINER_OP]


def subtool_pie_items(context):
    """The Q pie: Select (W), Draw (E), Scissors (S), the HairMesh Menu (N), Guides (NW), Crown (NE), the Modifiers tab
    (SW), More Tools (SE: Brush, Edges, the HairMesh Outliner): props.SUBTOOL_PLAN gives each sub-tool its direction."""
    from .props import ready_subtools
    slots = [('empty',)] * 8
    for key, label, _d, icon, _n, _step, _k, slot in ready_subtools():
        if slot is not None:                                        # (Brush and Edges: no direction left, they live in a sub-menu)
            slots[slot] = ('op', 'hairmesh.set_subtool', label, icon, {'subtool': key, 'activate': True})
    slots[3] = MENU_OP
    slots[6] = MODIFIERS_OP
    slots[7] = MORE_TOOLS_MENU
    return slots


def more_tools_items(context):
    """More Tools (the Q pie's SE): the sub-tools without a direction, then the HairMesh Outliner"""
    from .props import ready_subtools
    return [*(('op', 'hairmesh.set_subtool', t[1], t[3], {'subtool': t[0], 'activate': True}) for t in ready_subtools() if t[7] is None), OUTLINER_OP]   # (no M / V beside them: a menu only finds keys outside a tool's keymap)


def popup_top(context):
    """The row above the tabs of the pop-up: the sub-tool buttons, the HairMesh Outliner (U1) and Undo / Redo (1a of
    the second manual test of 21/10/2026: the pop-up swallows Ctrl+Z; the buttons keep it open on the state undone)."""
    return [*_subtool_buttons(), OUTLINER_OP_S, ('sep',), *(dim_op(it, undo_op(it[4]['redo']).poll()) for it in UNDO_REDO)]


def selection_rows(context, room=ROOM):
    """The Selection tab (the #props panel of the original, renderProps 8627: anchors first, else the last selected
    item — wall, group, curve), each as its sheet by category (anchor_rows, wall_rows, group_rows, curve_rows);
    nothing selected: New Group and the curves hidden by Auto (U2). `room`: the width it is drawn at. With the
    Scissors (3.8a), its panel first (renderToolPanel's ✂ Scissors, 8974): the modes, the help, Cut / Cancel."""
    if context.scene.hairmesh.subtool == 'DRAW':
        # ours: the Draw page first (the original's Draw has no panel, renderToolPanel 8948), anything selected in a closed block below
        root, P, sel = select.current(context)
        if P is None:
            return _selection_rows(context, room)
        from .ops_draw import draw_tool_rows
        return [*draw_tool_rows(context, P, room), block('draw_selection', 'Selection', 'RESTRICT_SELECT_OFF', _selection_rows(context, room - BOX_PAD), False)]
    if context.scene.hairmesh.subtool == 'SCISSORS':
        from .ops_scissors import scissors_rows
        return [*scissors_rows(context), block('scissors_selection', 'Selection', 'RESTRICT_SELECT_OFF', _selection_rows(context, room - BOX_PAD), False)]
    if context.scene.hairmesh.subtool == 'BRUSH':
        from .brush import anchor_brush_rows
        return [*anchor_brush_rows(context), block('brush_selection', 'Selection', 'RESTRICT_SELECT_OFF', _selection_rows(context, room - BOX_PAD), False)]
    if context.scene.hairmesh.subtool == 'EDGES':
        from .ops_edges import edges_rows
        return [*edges_rows(context, room), block('edges_selection', 'Selection', 'RESTRICT_SELECT_OFF', _selection_rows(context, room - BOX_PAD), False)]
    if context.scene.hairmesh.subtool == 'CROWN':
        # 4.3: the Crown's panel first (renderToolPanel 8960-8968: the help, Symmetric, + Create), then the selected
        # crown's sheet (8967: the last crown of S.sel), anything else selected in a closed block below
        from .ops_crown import crown_rows, crown_tool_rows, selected_crown
        root, P, sel = select.current(context)
        rows = crown_tool_rows(context, room)
        k = selected_crown(P, sel) if P is not None else None
        if k is not None:
            return rows + crown_rows(context, P, k, room)
        return [*rows, block('crown_selection', 'Selection', 'RESTRICT_SELECT_OFF', _selection_rows(context, room - BOX_PAD), False)]
    if context.scene.hairmesh.subtool == 'GUIDES':
        # 4.2: the Guides' panel first (renderToolPanel 9015-9025: the modes, the help, + Create), then the selected
        # guide's sheet (or the settings for new guides); anything else selected in a closed block below
        from .ops_guides import guide_rows, guides_rows, selected_guide
        root, P, sel = select.current(context)
        rows = guides_rows(context, room)
        g = selected_guide(P, sel) if P is not None else None
        last = select.last_item(P, sel) if P is not None else None
        if g is not None and last is g:
            return rows + _selection_rows(context, room)
        if g is not None:                                           # (renderToolPanel 9025: the last guide of S.sel, whatever was selected after it)
            rows = rows + guide_rows(context, P, g, room)
        elif P is not None:
            from .ops_guides import ring_rows, selected_ring
            ring = selected_ring(P, sel)
            if ring is not None:
                return rows + ring_rows(context, P, ring, room)     # (4.2c: a ring clicked in Guides: its sheet, open)
        return [*rows, block('guides_selection', 'Selection', 'RESTRICT_SELECT_OFF', _selection_rows(context, room - BOX_PAD), False)]
    return _selection_rows(context, room)


def modifiers_rows(context, room=ROOM):
    """The Modifiers tab (the owner's decision: a tab of the D, not a sub-tool; the original pins its panel, pinMod 6918):
    Profile / Extra / Topology (modifierPanel 6915, 6928-6936), the page, then the sheet of the selected modifier, Extra,
    Cap, Fill holes or Density (the last one of S.sel). The rest of the selection is the Selection tab's."""
    from .ops_extra import extra_tab_rows, xmod_rows
    from .ops_profile import mod_tab_row, profile_tab_rows, tube_rows
    from .ops_topology import modifier_tool_rows, topo_sheet
    root, P, sel = select.current(context)
    if P is None:
        return [[('label', 'No HairMesh project', 'INFO')]]
    last = select.last_item(P, sel)
    mt = context.scene.hairmesh_profile.mod_tab
    sheet = (tube_rows(context, P, last, room) if last.type == 'tube' else xmod_rows(context, P, last, room, brush=mt != 'EXTRA') if last.type == 'xmod'
             else topo_sheet(context, P, last, room)) if last is not None else None
    tab = (profile_tab_rows(context, room, sheet is not None) if mt == 'PROFILE' else extra_tab_rows(context, room, sheet) if mt == 'EXTRA'
           else modifier_tool_rows(context, room, sheet is not None, label=False))
    return [mod_tab_row(context), *tab, *(sheet or ())]


def _selection_rows(context, room=ROOM):
    root, P, anchors, cs, wall, group = _selection(context)
    if P is None:
        return [[('label', 'No HairMesh project', 'INFO')]]
    if anchors:
        from .ops_guides import ring_rows, selected_ring
        ring = selected_ring(P, select.load(context.scene.hairmesh).prune(P))
        if ring is not None:
            return ring_rows(context, P, ring, room)                # (4.2c, item 5: a disc's ring, not "6 anchors")
        return anchor_rows(context, P, anchors)
    if wall is not None:
        return wall_rows(context, P, wall, room)
    if group is not None:
        return group_rows(context, P, room)[0]                      # (the name field carries the group icon: the title row said the name twice)
    last = select.last_item(P, select.load(context.scene.hairmesh).prune(P))
    if last is not None and last.type == 'mesh':
        return mesh_rows(context, P, last, room)
    if last is not None and last.type == 'crown':
        from .ops_crown import crown_rows
        return crown_rows(context, P, last, room)                   # 4.3: the crown sheet (crownProps 7805)
    if last is not None and last.type == 'guide':
        from .ops_guides import guide_rows
        return guide_rows(context, P, last, room)                   # 4.2: the guide sheet (guideControls 8843)
    if last is not None and last.type == 'tube':
        from .ops_profile import tube_rows
        return tube_rows(context, P, last, room)                    # 4.6: the sheet of a Profile modifier (tubeProps 7143)
    if last is not None and last.type == 'xmod':
        from .ops_extra import xmod_rows
        return xmod_rows(context, P, last, room)                    # 4.7b: the sheet of an Extra (xmodProps 7039)
    if last is not None and last.type in ('cap', 'skin', 'tip'):
        from .ops_topology import topo_sheet
        return topo_sheet(context, P, last, room)                   # 4.4: the sheet of a Cap / Fill holes / Density (capProps 7341, skinProps 9490, tipProps 7333)
    if cs:
        return curve_rows(context, P, cs, room)
    rows, _title = group_rows(context, P, room)
    return rows


def mesh_rows(context, P, m, room=ROOM):
    """3.7: the sheet of a converted mesh (the original's mesh panel, 8833-8836: "Awaiting confirmation." while
    pending, the vertex and face counts), with Convert · Confirm · Cancel and Live · Baked"""
    d = m.data
    q = sum(1 for f in d.faces if len(f) == 4) if d else 0
    t = sum(1 for f in d.faces if len(f) == 3) if d else 0
    say = f'{"Awaiting confirmation (orange preview). " if m.pending else ""}Vertices: {len(d.pos) if d else 0}, quads: {q}, tris: {t}'
    return [*pair(('label', m.name, 'MESH_DATA'), group_label(P, m), room), [text(say, 'INFO')]] + convert_rows(context, P, short=True)


def popup_tabs(context, list_menu=False):
    """The tabs of the HairMesh Menu (U5): key -> rows (`list_menu`: as the list menu shows them)"""
    root, P, _sel = select.current(context)
    return {'SELECTION': selection_rows(context), 'MODIFIERS': modifiers_rows(context), 'CONVERSION': conversion_rows(context, P, list_menu=list_menu) if P is not None else [[('label', 'No HairMesh project', 'INFO')]],
            'OPTIONS': options_rows(context) if root is not None else [[('label', 'No HairMesh project', 'INFO')]], 'PROJECT': project_rows(context, root, P)}


def popup_sections(context, list_menu=False):
    """The HairMesh Menu as sections (title, icon, rows): the top row, then the tabs in order — what the list
    menu (Object > HairMesh) shows one after the other, and what the tests read."""
    tabs = popup_tabs(context, list_menu)
    top = [it for it in popup_top(context) if it[0] != 'sep' and it[1] != 'hairmesh.undo_step'] + ([HELP_OP] if list_menu else [])   # (the list menu sits under Blender's own Edit > Undo; the "?" of the tab column: How To)
    return [('Sub-tool', 'TOOL_SETTINGS', [top])] + [(label, icon, tabs[key]) for key, label, _d, icon, _n in TABS]


def active_tab(context):
    """the tab the pop-up shows: the one remembered in the preferences (Selection when the add-on is registered by
    hand, as in the tests)"""
    p = prefs.get(context)
    return p.menu_tab if p is not None else 'SELECTION'


# ---- the How To as a "?" (item 9 of the lot after 4.8, the owner's request; docs/interface.md) ----

HELP_OP = ('op', 'hairmesh.how_to', '', 'QUESTION', {}, {'list_text': 'How To'})   # the "?" of the D's tab column (and of the N panel's Scissors)
HELP_UNITS = 18                                                     # the width of the How To pop-up, in UI units


SELECT_HELP = '\n'.join(('Click: select a curve, a wall or an anchor. Shift + click adds; a box selects anchors.',
                         'Double click a curve: its anchors (again: the curve).',
                         'Drag an anchor: move it. Shift while dragging: slide it along the curve.',
                         'B + drag: smoothness of the anchor.',
                         'G / R / S: move, rotate, scale. X / Y / Z lock an axis, Esc cancels.',
                         'E: Move gizmo.',
                         'H: hide the selection. Shift + H: hide the rest. Alt + H: reveal.',
                         'Shift + right click: the pie (derived selections: a wall\'s curves, the curves a guide crosses).'))
DRAW_HELP = '\n'.join(('Click: place an anchor on the head.', 'Enter: finish the curve.', 'Esc: cancel the curve.'))
MODIFIERS_HELP = '6 opens this tab, with any sub-tool. A brush paints until Esc, its button again, or another tab or sub-tool.'


def modifiers_how_to(context):
    """(where, text): the How To of the Modifiers tab, of the page it shows"""
    from .ops_profile import APPLY_TIP
    mt = context.scene.hairmesh_profile.mod_tab
    if mt == 'PROFILE':
        where, say = 'Modifiers > Profile', APPLY_TIP
    elif mt == 'TOPOLOGY':
        from .ops_topology import CAP_TEXT, DENSITY_TEXT, FILL_TEXT, TOPO_TABS
        tt = context.scene.hairmesh_topo.topo_tab
        where, say = f'Modifiers > Topology > {next(t[1] for t in TOPO_TABS if t[0] == tt)}', {'CAP': CAP_TEXT, 'FILL': FILL_TEXT}.get(tt, DENSITY_TEXT)
    else:
        from .ops_extra import EXTRA_HELP
        where, say = 'Modifiers > Extra', EXTRA_HELP
    return where, f'{say}\n{MODIFIERS_HELP}'


def how_to(context):
    """(where, text): the How To of the Modifiers tab while the D shows it, whatever the sub-tool (its brushes work from
    the tab), else of the active sub-tool and of its tab or mode, one command per line (G2: the sheets say no keys)"""
    if active_tab(context) == 'MODIFIERS':
        return modifiers_how_to(context)
    hm = context.scene.hairmesh
    sub = hm.subtool
    label = next((t[1] for t in SUBTOOLS if t[0] == sub), sub.title())
    if sub == 'SCISSORS':
        from .ops_scissors import HELP_CURVE, HELP_SURFACE
        from .props import CUT_MODES
        return f'Scissors > {next(m[1] for m in CUT_MODES if m[0] == hm.cut_mode)}', HELP_CURVE if hm.cut_mode == 'CURVE' else HELP_SURFACE
    if sub == 'GUIDES':
        from .ops_guides import HELP
        from .props import GUIDE_MODES
        return f'Guides > {next(m[1] for m in GUIDE_MODES if m[0] == hm.guide_mode)}', HELP[hm.guide_mode]
    if sub == 'CROWN':
        from .ops_crown import HELP
        return 'Crown', HELP
    if sub == 'BRUSH':
        from .brush import ANCHOR_HELP
        return label, ANCHOR_HELP
    if sub == 'EDGES':
        from .ops_edges import HELP
        return label, HELP
    if sub == 'DRAW' and hm.draw_mode == 'FREEHAND':
        from .ops_draw import FREEHAND_HELP
        return 'Draw > Freehand', FREEHAND_HELP
    return label, {'SELECT': SELECT_HELP, 'DRAW': DRAW_HELP}[sub]


class HAIRMESH_OT_how_to(bpy.types.Operator):
    """Show the How To of the sub-tool and of its tab, or of the Modifiers tab (a click: the whole text in a small window)"""
    bl_idname = 'hairmesh.how_to'
    bl_label = 'How To'
    bl_options = {'INTERNAL'}                                       # (no undo step, not in the operator history: it only shows a text)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    @classmethod
    def description(cls, context, properties):
        where, say = how_to(context)
        return f'{where}: {say}'                                    # (Blender heads the tooltip with the label, "How To")

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=round(HELP_UNITS * fit.UNIT_PX))

    def draw(self, context):
        where, say = how_to(context)
        col = self.layout.column(align=True)
        col.label(text=f'How To: {where}', icon='QUESTION')
        col.separator(factor=0.5)
        for line in say.split('\n'):
            draw_item(col, context.scene.hairmesh, text(line, 'DOT'), HELP_UNITS - 1.0)

    def execute(self, context):
        return {'FINISHED'}


def menu_items(context):
    """The HairMesh Menu as a plain list (Object > HairMesh): the sections of the pop-up, one after the other, with
    the toggles as checkboxes, the sub-tool buttons as operators and the icon-only buttons with their long text."""
    items = []
    for title, icon, rows in popup_sections(context, list_menu=True):
        if items:
            items.append(('sep',))
        items.append(('label', title, icon))
        items.extend(_list_items(rows))
    items += [('sep',), ('label', 'Visibility', 'HIDE_OFF'), ('op', 'hairmesh.hide_selected', 'Hide Selected', 'HIDE_ON', {}),
              ('op', 'hairmesh.hide_unselected', 'Hide Unselected', 'HIDE_ON', {}), ('op', 'hairmesh.reveal_hidden', 'Reveal Hidden', 'HIDE_OFF', {})]
    return items


def _list_items(rows):
    """the rows as the items of a list menu: a block is its title and its rows one after the other"""
    items = []
    for row in rows:
        for it in row:
            if it[0] == 'indent' or (it[0] == 'op' and it[1] == 'hairmesh.outliner_expand'):
                continue                                            # (the tree's indentation and arrows: the pop-up's)
            if it[0] == 'row':
                items.extend(_list_items([it[1]]))
                continue
            if it[0] == 'list' and len(it) > 4:
                items.extend(_list_items(it[4]))                    # (the list of the Extras: its lines, one after the other)
                continue
            if it[0] == 'cols':
                items.extend(_list_items([c[1] for c in it[1]]))
                continue
            if it[0] == 'text':
                for i, line in enumerate(text_lines(it, LIST_ROOM)):
                    items.append(('label', line, it[2] if i == 0 else ('BLANK1' if it[2] not in (None, 'NONE') else 'NONE')))
                continue
            if it[0] == 'block':
                items.append(('label', it[2], it[3]))
                if len(it) > 6 and it[6]:
                    items.append(named(why_item(it[6])))             # (the same tooltip, on a row of its own)
                items.extend(_list_items(it[4]))
            elif it[0] == 'subtool':                                # (a list names every sub-tool: the top row shows the active one's name alone, 4.4)
                items.append(('op', 'hairmesh.set_subtool', next(t[1] for t in SUBTOOLS if t[0] == it[1]), it[3], {'subtool': it[1], 'activate': True}))
            elif it[0] == 'toggle':
                items.append(('prop', it[1], it[2] or props.prop_name(it[1]), *it[4:5]))   # (an icon-only toggle: its name beside the checkbox; dimmed as in the pop-up)
            elif it[0] == 'op' and not it[2]:
                items.append(named(it))                             # (dimmed and pressed in the list too)
            else:
                items.append(it)
    return items


def draw_item(layout, hm, item, width=None):
    """draws one item description; an icon 'HM:<name>' is one of the add-on's own images (icons.py). `width`: the
    units the item gets (a paragraph wraps by it)"""
    kind = item[0]
    if kind == 'op':
        _k, idname, txt, icon, op_props = item[:5]                  # (not `props` nor `text`: the module and the function, needed below)
        opts = item[5] if len(item) > 5 else {}
        lay = layout
        if not opts.get('active', True):
            lay = layout.row(align=True); lay.active = False         # dimmed, still a button (a group included through a ticked one; part 1 of the lot ux7: an action that would change nothing now)
            lay.alignment = getattr(layout, 'alignment', 'EXPAND')   # (in a left-aligned column the button keeps its text's width)
        op = lay.operator(idname, text=txt, emboss=opts.get('emboss', True), depress=opts.get('depress', False), **icons.icon_kw(icon))
        for k, v in op_props.items():
            setattr(op, k, v)
    elif kind == 'indent':
        for _ in range(item[1]):
            layout.label(text='', icon='BLANK1')                    # the tree's indentation, a row without an arrow: an icon's width, as the outliner's (and the arrow's)
    elif kind == 'prop':
        lay = layout
        opts = item[3] if len(item) > 3 else {}
        if not (opts.get('active', True) and opts.get('enabled', True)):
            lay = layout.row(align=True); lay.alignment = layout.alignment
            lay.active = opts.get('active', True)                   # dimmed, still editable (a slider that changes nothing now)
            lay.enabled = opts.get('enabled', True)                 # locked (a group colour covered by a parent's)
        kw = {'icon': opts['icon']} if opts.get('icon') else {}
        data = props.data_of(bpy.context, item[1], hm)
        if opts.get('expand'):                                      # (G6: the enum's options as buttons, its name before them)
            r = lay
            if item[2]:                                             # (a split: a plain row gives the name a share of its own and cuts it)
                r = lay.split(factor=fit.label_need(f'{item[2]}:', False) / item_need(bpy.context, item, hm), align=True)
                r.label(text=f'{item[2]}:')
            r.row(align=True).prop(data, item[1], expand=True)
        else:
            lay.prop(data, item[1], text=item[2], slider=is_slider(data.bl_rna.properties.get(item[1])), **kw)   # (item 7: a fraction a slider, a measure or a count a field)
    elif kind == 'toggle':
        lay = layout
        opts = item[4] if len(item) > 4 else {}
        if not (opts.get('active', True) and opts.get('enabled', True)):
            lay = layout.row(align=True); lay.active = opts.get('active', True)   # dimmed, still editable (a project default nothing reads)
            lay.enabled = opts.get('enabled', True)                 # locked (Second Surface with no bulge)
            lay.alignment = layout.alignment
        lay.prop(props.data_of(bpy.context, item[1], hm), item[1], text=item[2], icon=item[3], toggle=True)
    elif kind == 'text':
        for i, line in enumerate(text_lines(item, width if width is not None else ROOM)):
            r = layout.row(align=True)
            if len(item) > 3 and not item[3].get('active', True):
                r.active = False
            r.label(text=line, **icons.icon_kw(item[2] if i == 0 else ('BLANK1' if item[2] not in (None, 'NONE') else 'NONE')))
    elif kind == 'cols':
        draw_cols(layout, hm, item[1], width if width is not None else ROOM)
    elif kind == 'subtool':
        _k, key, txt, icon = item
        op = layout.operator('hairmesh.set_subtool', text=txt, icon=icon, depress=hm.subtool == key)
        op.subtool = key; op.activate = True
    elif kind == 'menu':
        lay = layout
        if len(item) > 4 and not item[4].get('active', True):
            lay = layout.row(align=True); lay.active = False         # dimmed, still a menu (it says why when opened; 4.2: the guide's group list)
            lay.alignment = getattr(layout, 'alignment', 'EXPAND')
        lay.menu(item[1], text=item[2], **icons.icon_kw(item[3]))
    elif kind == 'label':
        if width is not None and item[1] and fit.label_need(item[1], item[2] not in (None, 'NONE')) > width:
            draw_item(layout.column(align=True), hm, text(item[1], item[2]), width)   # never cut with "…": wrapped (part 2b of the lot ux7)
        else:
            layout.label(text=item[1], **icons.icon_kw(item[2]))
    elif kind == 'sep':
        layout.separator()
    elif kind == 'row':
        r = layout.row(align=True)
        for sub in item[1]:
            draw_item(r, hm, sub)
    elif kind == 'box':
        col = layout.column()
        if item[1]:
            # a pie's box as wide as its widest item: left to the pie, it took the width of one of its items and cut the
            # others ("Conv…", "Live Vi…")
            col.ui_units_x = max(sum(item_need(bpy.context, x, hm) for x in sub[1]) if sub[0] == 'row' else item_need(bpy.context, sub, hm) for sub in item[1]) + BOX_PAD
        box = col.box()
        for sub in item[1]:
            draw_item(box, hm, sub, PIE_ROOM if sub[0] == 'text' else None)
    elif kind == 'empty':
        layout.separator()
    elif kind == 'block':
        _k, key, title, icon, rows, default = item[:6]
        opened = block_open(bpy.context, key, default)
        hdr = layout.row(align=True)
        left = hdr.row(align=True); left.alignment = 'LEFT'         # (G9: the title beside its arrow, never centred away from it)
        op = left.operator(HAIRMESH_OT_toggle_block.bl_idname, text=title, icon='DISCLOSURE_TRI_DOWN' if opened else 'DISCLOSURE_TRI_RIGHT', emboss=False)
        op.key = key; op.default = default
        if len(item) > 6 and item[6]:
            right = hdr.row(align=True); right.alignment = 'RIGHT'
            draw_item(right, hm, why_item(item[6]))
        if opened:
            draw_rows(layout.box(), hm, rows, (width if width is not None else ROOM) - BOX_PAD)


def cols(*columns):
    """a row of columns of fixed widths (part 2 / 3 of the lot ux7): ((units, [items]), ...) — None for the column
    that takes the rest — drawn with splits, so the columns of every such row line up whatever their texts (a row
    shares its width in proportion to its items' texts: the columns of rows with longer names would move)"""
    return ('cols', list(columns))


def draw_cols(layout, hm, columns, width):
    fixed = sum(c[0] for c in columns if c[0])
    rest = max(0.5, width - fixed)
    widths = [c[0] if c[0] else rest for c in columns]
    for column, w, target in zip(columns, widths, fit.split_cols(layout, widths)):
        its = column[1]
        opts = column[2] if len(column) > 2 else {}
        if opts.get('align'):
            target.alignment = opts['align']                       # (a tree's text: left, as the outliner's names)
        if not its:
            target.label(text='')
        ests = [item_est(bpy.context, it, hm) for it in its]
        tot = sum(ests) or 1.0
        for it, e in zip(its, ests):
            draw_item(target, hm, it, w * e / tot)


def clipped(context, rows, width=ROOM, hm=None):
    """the items draw_rows would show cut ("…") or not at all at `width` units — [(item, units needed, units given)] — following
    the same choices: a paragraph wraps, a label too long for its place wraps, a row that does not fit stacks, the
    columns of a 'cols' row keep their widths (tests/test_blender_ux7.py walks every tab, sheet and menu with it)"""
    out = []

    def check(it, w):
        if it[0] in ('text', 'indent', 'sep', 'empty', 'label', 'list'):
            return                                                  # (paragraphs and labels wrap)
        if it[0] == 'cols':
            if not cols_fit(context, it[1], w, hm):
                out.extend(clipped(context, cols_items(it[1]), w, hm))
                return
            fixed = sum(c[0] for c in it[1] if c[0])
            for u, its, *_o in it[1]:
                cw = u if u else max(0.5, w - fixed)
                ests = [item_est(context, x, hm) for x in its]
                tot = sum(ests) or 1.0
                for x, e in zip(its, ests):
                    check(x, cw * e / tot)
            return
        if it[0] == 'block':
            if block_open(context, it[1], it[5]):
                out.extend(clipped(context, it[4], w - BOX_PAD, hm))
            return
        need = item_need(context, it, hm)
        if need > w + 1e-6:
            out.append((it, round(need, 2), round(w, 2)))

    for row in rows:
        if not row:
            continue
        if row[0][0] in ('block', 'text', 'cols', 'list'):
            check(row[0], width)
            out += [(it, 0.0, 0.0) for it in row[1:]]               # (draw_rows draws only the first item of such a row)
            continue
        fits = row_fits(context, row, width, hm)
        forms = [stacked_form(context, it, width, hm) for it in row if it[0] != 'sep'] if not fits else []
        parts = [row] if fits else [[without_text(it)] if too_wide_alone(context, it, width, hm) else [it] for it in forms]
        for part in parts:
            w = width
            if part[0][0] == 'label' and len(part[0]) > 3 and part[0][3].get('split') and len(part) > 1:
                w = (1.0 - part[0][3]['split']) * width; part = part[1:]
            ests = [item_est(context, it, hm) for it in part]
            tot = sum(ests) or 1.0
            for it, e in zip(part, ests):
                check(it, w * e / tot)
    return out


def draw_rows(layout, hm, rows, width=ROOM, align=True):
    """rows of items, each row its items side by side (the sub-tool row taller, as the shape buttons of BoxCutter's
    helper; rows of buttons a little taller than sliders). `width`: the units the rows get — a paragraph wraps by it,
    and a row whose items would not show their texts whole there is drawn one item per row (part 2b of the lot ux7:
    never "…"; the sheets are laid out so this does not happen in the pop-up, a narrow N panel stacks)."""
    col = layout.column(align=align)
    ctx = bpy.context
    for row in rows:
        if not row:
            continue
        head = row[0]
        if head[0] == 'block':
            draw_item(col.column(align=True), hm, head, width)      # a block: its header, then its rows in a box
            continue
        if head[0] == 'list':
            TREE['room'] = head[2]
            ul, lines, active = head[3] if len(head) > 3 else ('HAIRMESH_UL_conversion', 'rows', 'hairmesh_conv_active')
            col.template_list(ul, '', hm, lines, ctx.window_manager, active, rows=head[1], maxrows=head[1], sort_lock=True)
            continue
        if head[0] == 'cols' and not cols_fit(ctx, head[1], width, hm):
            draw_rows(col, hm, cols_items(head[1]), width, align)    # (a narrow panel: one item per row)
            continue
        if head[0] in ('text', 'cols'):
            r = col.row(align=True) if head[0] == 'cols' else col.column(align=True)
            if head[0] == 'cols' and any(it[0] in ('op', 'toggle', 'menu') for c in head[1] for it in c[1]):
                r.scale_y = 1.15
            draw_item(r, hm, head, width)
            continue
        stack = not row_fits(ctx, row, width, hm)
        for part in ([[stacked_form(ctx, it, width, hm)] for it in row if it[0] != 'sep'] if stack else [row]):
            if stack and too_wide_alone(ctx, part[0], width, hm):
                draw_item(col.column(align=True), hm, text(item_text(part[0]), 'NONE'), width)   # its name as a paragraph, the control without text under it
                part = [without_text(part[0])]
            r = col.row(align=True)
            if part[0][0] == 'subtool':
                r.scale_y = 1.4
            elif any(it[0] in ('op', 'toggle', 'menu') for it in part):
                r.scale_y = 1.15
            if len(part) > 1 and all(it[0] == 'sep' or (it[0] == 'op' and not it[2]) for it in part):
                r.scale_x = TOP_ICON                                # (a row of icons alone keeps its natural widths, packed on the left: as wide as the D's top row, item 8)
            w = width
            if part[0][0] == 'label' and len(part[0]) > 3 and part[0][3].get('split') and len(part) > 1:
                f = part[0][3]['split']
                sp = r.split(factor=f, align=True)                  # a label that needs most of the row ("Color from <group>")
                draw_item(sp.row(align=True), hm, part[0], f * width)
                r = sp.row(align=True); w = (1.0 - f) * width
                part = part[1:]
            ests = [item_est(ctx, it, hm) for it in part]
            total = sum(ests) or 1.0
            for it, e in zip(part, ests):
                draw_item(r, hm, it, w * e / total)
    return col


def draw_popup(layout, context):
    """The pop-up panel (U5): the sub-tool row on top, then the column of tab icons on the left (the tab remembered
    in the preferences, as BoxCutter's Box Helper keeps its shape type) and the rows of the active tab alone."""
    p = prefs.get(context)
    tab = active_tab(context)
    layout.ui_units_x = POPUP_UNITS_X                               # every tab the same width (part 2a of the lot ux7)
    hm = context.scene.hairmesh
    top = layout.row(align=True)
    top.scale_y = 1.3
    items = popup_top(context)
    subs = [it for it in items if it[0] == 'subtool']; rest = [it for it in items if it[0] != 'subtool']
    # (the sub-tools packed on the left at their natural widths, joined, the active one's name after them; the Outliner
    # and Undo / Redo on the right)
    right_w = sum(TOP_ICON for it in rest if it[0] != 'sep') + 0.5
    sp = top.split(factor=1.0 - right_w / POPUP_UNITS_X, align=True)
    # (a split, not a row sized by ui_units_x: beside the icons a label kept the width of its text, and Blender cut that one to
    # "Scis..." with room to spare: tools/sim_c49b_names.py, on the pixels)
    left = sp.split(factor=len(subs) * TOP_ICON_W / (POPUP_UNITS_X - right_w), align=True)
    bar = left.row(align=True); bar.alignment = 'LEFT'; bar.scale_x = TOP_ICON
    for it in subs:
        draw_item(bar, hm, it)
    left.row().label(text=subtool_name(hm))
    right = sp.row(align=True); right.alignment = 'RIGHT'
    for it in rest:
        draw_item(right, hm, it)
    layout.separator(factor=0.4)
    split = layout.split(factor=TAB_SPLIT)
    col = split.column(align=True)
    col.scale_y = 1.5
    if p is not None:
        col.prop(p, 'menu_tab', expand=True, icon_only=True)
    else:
        for _key, _label, _d, icon, _n in TABS:
            col.label(text='', **icons.icon_kw(icon))
    col.separator()
    draw_item(col, hm, HELP_OP)                                     # (item 9: the How To of the sub-tool and its tab, a tooltip and a click away)
    body = split.column()
    rows = popup_tabs(context)[tab]
    draw_rows(body, hm, rows, ROOM)                                 # (the sheets keep within MAX_ROWS with the blocks as designed; a block the user opens may add rows)


class HAIRMESH_PT_popup(bpy.types.Panel):
    """Open the HairMesh Menu (D): the sub-tools, then the tabs Selection / Modifiers / Conversion / Viewport / Project"""
    bl_idname = POPUP_ID
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'                                       # drawn only as a popup / popover, never docked (BoxCutter's helper is the same)
    bl_label = 'HairMesh Menu'
    bl_ui_units_x = POPUP_UNITS_X

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        draw_popup(self.layout, context)


class HAIRMESH_MT_pie(bpy.types.Menu):
    """Open the context pie menu for the active selection (Shift+RMB)"""
    bl_idname = 'HAIRMESH_MT_pie'
    bl_label = 'HairMesh Pie'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        pie = self.layout.menu_pie()
        hm = context.scene.hairmesh
        for item in pie_items(context):
            draw_item(pie, hm, item)


class HAIRMESH_MT_subtools(bpy.types.Menu):
    """Open the sub-tool pie (Q): Select, Draw, Scissors and the HairMesh Menu"""
    bl_idname = 'HAIRMESH_MT_subtools'
    bl_label = 'HairMesh Sub-tool Pie'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        pie = self.layout.menu_pie()
        hm = context.scene.hairmesh
        for item in subtool_pie_items(context):
            draw_item(pie, hm, item)


class HAIRMESH_MT_menu(bpy.types.Menu):
    """List everything the tool does (Object > HairMesh): the tabs of the HairMesh Menu pop-up"""
    bl_idname = 'HAIRMESH_MT_menu'
    bl_label = 'HairMesh Menu'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        hm = context.scene.hairmesh
        for item in menu_items(context):
            draw_item(self.layout, hm, item)


class HAIRMESH_MT_walls(bpy.types.Menu):
    """Create walls and locks: with 2+ curves selected the wall items (wall / facing branches / exactly these / volume lock / fill the hole / average curve / merge) and the walls the curves drive;
    with a wall selected its actions"""
    bl_idname = 'HAIRMESH_MT_walls'
    bl_label = 'HairMesh Walls'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        hm = context.scene.hairmesh
        for item in ops_walls.walls_menu_items(context):
            draw_item(self.layout, hm, item)


class HAIRMESH_MT_group(bpy.types.Menu):
    """Act on the selected group: new sub-group, rename, delete, match the project step, detach from
    the neighboring groups, visible, the curves mode, move; New Group without a selected group"""
    bl_idname = 'HAIRMESH_MT_group'
    bl_label = 'HairMesh Group'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        hm = context.scene.hairmesh
        for item in group_menu_items(context):
            draw_item(self.layout, hm, item)


class HAIRMESH_MT_conversion(bpy.types.Menu):
    """Show the Conversion tab: the groups ticked for conversion, the sliders that edit them (or the project
    defaults), the loop step from the active object's height, Convert"""
    bl_idname = 'HAIRMESH_MT_conversion'
    bl_label = 'HairMesh Conversion'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        hm = context.scene.hairmesh
        for item in conversion_menu_items(context):
            draw_item(self.layout, hm, item)


class HAIRMESH_MT_more_tools(bpy.types.Menu):
    """Open the Brush, the Edges or the HairMesh Outliner"""
    bl_idname = 'HAIRMESH_MT_more_tools'
    bl_label = 'More Tools'

    def draw(self, context):
        for it in more_tools_items(context):
            draw_item(self.layout, context.scene.hairmesh, it)


class HAIRMESH_MT_gizmo(bpy.types.Menu):
    """Set the gizmo: its three modes, its axes and the locks"""
    bl_idname = 'HAIRMESH_MT_gizmo'
    bl_label = 'Gizmo'

    def draw(self, context):
        hm = context.scene.hairmesh
        from .ops_gizmo import blocked_reason
        why = blocked_reason(context)
        ok = why is None and is_active(context)
        draw_item(self.layout, hm, act('frame'))                     # 4.0: F (the original's frameAll), greyed by its poll when it cannot act
        for it in gizmo_items(hm, ok, short=False):
            draw_item(self.layout, hm, it)
        if why:
            from .ops_gizmo import blocked_short
            self.layout.label(text=blocked_short(why), icon='INFO')   # (short: a menu widens to its longest row; the buttons' tooltips say it whole)
        self.layout.separator()
        for it in _list_items(gizmo_rows(context, hm, why is None)):   # (the axes and the locks, named: the block's icons get their texts)
            if it[0] == 'op' and it[1] in ('hairmesh.gizmo_space', 'hairmesh.gizmo_lock'):
                draw_item(self.layout, hm, it)


class HAIRMESH_MT_move_to_group(bpy.types.Menu):
    """Move the selected curves, walls and groups into a group"""
    bl_idname = 'HAIRMESH_MT_move_to_group'
    bl_label = 'Move to HairMesh Group'

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        hm = context.scene.hairmesh
        for item in move_to_group_menu_items(context):
            draw_item(self.layout, hm, item)


def menu_func_object(self, context):
    """Object > HairMesh: the HairMesh Menu as a submenu (Blender filters it with the workspace's owner list)."""
    self.layout.separator()
    self.layout.menu(HAIRMESH_MT_menu.bl_idname, text='HairMesh', **icons.icon_kw(LOGO))


_CLASSES = (HAIRMESH_MT_more_tools, HAIRMESH_OT_toggle_tool, HAIRMESH_OT_set_subtool, HAIRMESH_OT_toggle_block, HAIRMESH_OT_undo_step, HAIRMESH_OT_why, HAIRMESH_UL_conversion, HAIRMESH_OT_expand_all, HAIRMESH_OT_how_to, *SUBTOOL_KEY_OPS, HAIRMESH_OT_open_modifiers, HAIRMESH_MT_walls, HAIRMESH_MT_gizmo, HAIRMESH_MT_group, HAIRMESH_MT_conversion,
            HAIRMESH_MT_move_to_group, HAIRMESH_MT_menu, HAIRMESH_MT_pie, HAIRMESH_MT_subtools, HAIRMESH_PT_popup)


def register():
    bpy.types.WindowManager.hairmesh_conv_active = bpy.props.IntProperty(name='Conversion List', get=lambda _s: -1, set=lambda _s, _v: None)   # (a list needs an active row: none, a click on a row's empty part selects nothing)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    from bl_ui.space_toolsystem_toolbar import VIEW3D_PT_tools_active as tools
    hops = any(getattr(t, 'idname', None) == 'Hops' for t in tools._tools['OBJECT'])   # (register_tool prints a warning when `after` finds nothing)
    bpy.utils.register_tool(HAIRMESH_WT_tool, after={'Hops'} if hops else None, separator=True)   # below HardOps when it is installed, else at the end


def unregister():
    bpy.utils.unregister_tool(HAIRMESH_WT_tool)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.WindowManager, 'hairmesh_conv_active'):
        del bpy.types.WindowManager.hairmesh_conv_active
