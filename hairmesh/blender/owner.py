# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The workspace's "Filter Add-ons" (WorkSpace.use_filter_by_owner + owner_ids): what Blender hides on its own and
what the add-on has to hide itself.

While an enabled add-on's register() runs, addon_utils sets the owner id to the add-on's module name
('bl_ext.<repo>.hairmesh' once installed) and Blender stamps it on every panel, menu, menu draw function and keymap
created meanwhile (RNA_struct_state_owner_get on the C side), then hides those by itself in a workspace whose
filter is on and does not list the owner. Measured live on 5.1.2 (14/10/2026):
  - filtered on its own: the N panels (the "HairMesh" category exists only with the add-on ticked), the entries
    appended to VIEW3D_MT_object (the draw functions carry `_owner`), and the tool's keymap, whose add-on and user
    copies have bl_owner_id (WM_keymap_poll checks it);
  - not filtered: the toolbar icon (the tool system has no notion of owner; HardOps's icon stays as well), the
    File > Import / Export entries (those menus set bl_owner_use_filter = False), the Shift+Alt+H item (the user
    copy of the 3D View keymap is Blender's own, owner ''), the draw handler, operators in F3 and menus called by
    name.
Everything in the second list but the toolbar icon goes through visible(context). The icon stays on purpose, like
HardOps's (decided on 14/10/2026): the tool can be switched on in a workspace that filters the add-on out, and is
then inert, since its keymap is filtered by Blender and the draw handler draws nothing (tool.py, draw.py).

So that an inert tool is never a silent one (asked on 14/10/2026), the header of the tool and the N panel then say
why its clicks and keys do nothing, with a button that ticks the add-on in the workspace (show_in_workspace). The N
panel notice is the one class registered WITHOUT the owner id (register_unowned): stamped like the others, Blender
would hide it together with them. It shows only while the HairMesh tool is the active one there: a workspace that
filters HairMesh out on purpose keeps no trace of it in the sidebar."""
import bpy

from . import prefs

OWNER = prefs.PACKAGE                                               # replaced at register() by what Blender used
NOTICE = 'HairMesh is hidden in this workspace (Filter Add-ons): clicks and keys are off'


def register():
    """Records the owner id Blender is stamping on our panels and keymaps right now (the module name, set by
    addon_utils.enable), so visible() agrees with them; prefs.PACKAGE when registered by hand (tests). Then the
    notice: the operator and the N panel."""
    global OWNER
    try:
        from _bpy import _bl_owner_id_get
        OWNER = _bl_owner_id_get() or prefs.PACKAGE
    except ImportError:
        OWNER = prefs.PACKAGE
    bpy.utils.register_class(HAIRMESH_OT_show_in_workspace)
    register_unowned(HAIRMESH_PT_hidden)


def unregister():
    bpy.utils.unregister_class(HAIRMESH_PT_hidden)
    bpy.utils.unregister_class(HAIRMESH_OT_show_in_workspace)


def register_unowned(cls):
    """register_class with no owner id stamped on cls (Blender never filters it by workspace), then the owner id
    that was being stamped is set back."""
    try:
        from _bpy import _bl_owner_id_get, _bl_owner_id_set
    except ImportError:
        bpy.utils.register_class(cls)
        return
    prev = _bl_owner_id_get()
    _bl_owner_id_set(None)
    try:
        bpy.utils.register_class(cls)
    finally:
        _bl_owner_id_set(prev)


def visible(context=None):
    """False only in a workspace whose "Filter Add-ons" is on without this add-on ticked (no workspace, as in
    background mode: True)."""
    ws = getattr(context or bpy.context, 'workspace', None)
    if ws is None or not ws.use_filter_by_owner:
        return True
    return OWNER in ws.owner_ids


def hidden_and_in_use(context):
    """The HairMesh tool is the active one in a workspace that filters the add-on out: inert, so say it."""
    if visible(context):
        return False
    from . import tool
    return tool.is_active(context)


class HAIRMESH_OT_show_in_workspace(bpy.types.Operator):
    """Tick HairMesh in this workspace's Filter Add-ons list: its tool keys, panels, menus and drawing work here again"""
    bl_idname = 'hairmesh.show_in_workspace'
    bl_label = 'Show HairMesh in This Workspace'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return not visible(context)                                 # (so it is offered, in F3 too, only where it is needed)

    def execute(self, context):
        ws = context.workspace
        if OWNER not in ws.owner_ids:
            ws.owner_ids.new(OWNER)                                 # what ticking HairMesh in Workspace > Filter Add-ons does
        for win in context.window_manager.windows:                  # the header, the sidebar and the viewport drawing change
            for area in win.screen.areas:
                area.tag_redraw()
        self.report({'INFO'}, f'HairMesh is ticked in the "{ws.name}" workspace')
        return {'FINISHED'}


class HAIRMESH_PT_hidden(bpy.types.Panel):
    """In place of the HairMesh panels (which Blender hides) while the tool is active in a workspace that filters the
    add-on out. Registered without the owner id (register_unowned)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HairMesh'
    bl_label = 'HairMesh Is Hidden Here'

    @classmethod
    def poll(cls, context):
        return hidden_and_in_use(context)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)                             # (short lines: the sidebar's default width)
        col.alert = True
        col.label(text='Hidden by Filter Add-ons', icon='ERROR')
        col = layout.column(align=True)
        col.label(text="The tool's clicks and keys")
        col.label(text='do nothing in this workspace.')
        layout.operator(HAIRMESH_OT_show_in_workspace.bl_idname, icon='CHECKBOX_HLT')
        layout.label(text='Or pick another tool.')
