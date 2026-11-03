# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Blender-facing part of HairMesh: operators, menus, panels, the WorkSpaceTool, its shortcuts and drawing, and
the bridge to Blender objects."""
import bpy

from . import brush, convert_op, deferred, draw, icons, import_project, keymaps, live, migrate_op, ops_draw, ops_edges, ops_extra, ops_gizmo, ops_groups, ops_guides, ops_crown, ops_profile, ops_topology, ops_transform, profile_editor, ops_outliner, ops_scissors, ops_select, ops_walls, ops_weld, owner, prefs, project_ops, props, session, slow, tool, ui

_CLASSES = (import_project.HAIRMESH_OT_import_project, project_ops.HAIRMESH_OT_new_project, project_ops.HAIRMESH_OT_set_head,
            project_ops.HAIRMESH_OT_export_project, project_ops.HAIRMESH_OT_save_project, migrate_op.HAIRMESH_OT_migrate_project, *convert_op.CLASSES)
_MENUS = ((bpy.types.TOPBAR_MT_file_import, import_project.menu_func_import),
          (bpy.types.TOPBAR_MT_file_import, migrate_op.menu_func_import),   # 3.7b: Migrate Hair, beside the import
          (bpy.types.TOPBAR_MT_file_export, project_ops.menu_func_export),
          (bpy.types.VIEW3D_MT_object, project_ops.menu_func_object))   # Object > HairMesh: the whole HairMesh Menu as a submenu


def register():
    owner.register()                                                # the owner id Blender stamps on what follows (workspace filter); the filter notice
    props.register()
    icons.register()                                                # icons/hairmesh.png for the buttons and the pies (bpy.utils.previews)
    prefs.register()
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    for menu, fn in _MENUS:
        menu.append(fn)
    ops_draw.register()
    ops_select.register()
    ops_weld.register()
    ops_walls.register()
    ops_scissors.register()
    ops_guides.register()                                           # 4.2: the Guides sub-tool and the guide's panel
    ops_crown.register()                                            # 4.3: the Crown sub-tool, the crown's panel and pies
    ops_edges.register()                                            # 4.9: the Edges sub-tool
    ops_topology.register()                                         # 4.4: the Topology page of the Modifiers tab: Cap, Fill holes, Density
    ops_profile.register()                                          # 4.6: its Profile tab and the modifier's sheet
    profile_editor.register()                                       # 4.6b: the profile editor (D4.6)
    brush.register()                                                # 4.7b (D4.4): the brush session — the weights of the Extras, the Density (4.8)
    ops_extra.register()                                            # 4.7b: the Extra tab and an Extra's sheet
    ops_transform.register()                                        # 3.8c: G / R / S on the selected anchors
    ops_gizmo.register()                                            # 3.8b: the anchor gizmo (before the tool: its bl_widget) and Shift+D
    ops_groups.register()
    ops_outliner.register()                                         # the HairMesh Outliner (O), U1
    live.register()
    deferred.register()                                             # the conversion sliders' rebuild and undo step when the slider is let go
    tool.register()                                                 # the WorkSpaceTool: its keymap goes to keyconfigs.addon
    ui.register()
    keymaps.register()                                              # Shift+Alt+H, keyconfigs.addon
    draw.register()
    slow.register()                                                 # the slow-action log ("HairMesh slow: …" in the system console), every operator above


def unregister():
    slow.unregister()
    draw.unregister()
    keymaps.unregister()
    ui.unregister()
    tool.unregister()
    deferred.unregister()
    live.unregister()
    ops_outliner.unregister()
    ops_groups.unregister()
    ops_gizmo.unregister()
    ops_transform.unregister()
    ops_extra.unregister()
    brush.unregister()
    profile_editor.unregister()
    ops_profile.unregister()
    ops_topology.unregister()
    ops_edges.unregister()
    ops_crown.unregister()
    ops_guides.unregister()
    ops_scissors.unregister()
    ops_walls.unregister()
    ops_weld.unregister()
    ops_select.unregister()
    ops_draw.unregister()
    for menu, fn in reversed(_MENUS):
        menu.remove(fn)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    prefs.unregister()
    icons.unregister()
    props.unregister()
    owner.unregister()
    session.forget()
    from . import pick
    pick.clear_caches()
