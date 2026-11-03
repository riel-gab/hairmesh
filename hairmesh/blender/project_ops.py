# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Project operators (phase 3.1): a new project in the current scene, the head mesh, and the .hairmesh export."""
import os

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..core import edit
from ..core.hairmesh_io import ProjectFormatError, write_project
from ..core.headobj import obj_text
from . import owner, session, tool
from .coords import from_blender
from .scene_build import make_root


def head_obj_text(root, ob, depsgraph):
    """The evaluated mesh of `ob` in the root's local space (the model's space, axis-swapped to the original's), as
    the OBJ text the original would have imported. Vertices are Blender floats (float32), written in their shortest
    form so both parsers read back the same doubles."""
    ob_eval = ob.evaluated_get(depsgraph)
    me = ob_eval.to_mesh()
    try:
        M = root.matrix_world.inverted() @ ob.matrix_world
        v = []
        for vert in me.vertices:
            x, y, z = M @ vert.co
            v.extend(from_blender(x, y, z))
        faces = [list(p.vertices) for p in me.polygons]
    finally:
        ob_eval.to_mesh_clear()
    return obj_text(v, faces)


def set_head_object(context, root, proj, ob):
    """Makes `ob` the project's head: its geometry goes into the model (setHead, 3217) and the head datablock, the
    object is tagged, the previous head object untagged. The object itself is left where and how it is."""
    text = head_obj_text(root, ob, context.evaluated_depsgraph_get())
    head = edit.set_head(proj, ob.name + '.obj', {'ext': 'obj', 'text': text})
    prev = session.head_object(root)
    if prev is not None and prev != ob:
        for k in ('hairmesh_type', 'hairmesh_id'):
            if k in prev:
                del prev[k]
    ob['hairmesh_type'] = 'head'
    ob['hairmesh_id'] = head.id
    root['hairmesh_head'] = ob.name
    root['hairmesh_model_scale'] = proj.S.scale
    root.empty_display_size = proj.S.scale * 0.25
    session.save_project(root, proj, head=True)
    return head


class HAIRMESH_OT_new_project(bpy.types.Operator):
    """Start a HairMesh project in this scene: a root Empty with the project kept as text inside the .blend. The active mesh can be its head (where the hair is drawn)"""
    bl_idname = 'hairmesh.new_project'
    bl_label = 'New HairMesh Project'
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name='Name', description='Project name (also the name of the text datablocks and of the .hairmesh file on export)', default='hair-project')
    use_head: BoolProperty(name='Active Mesh Is the Head', description="The active Mesh object's geometry becomes the project's head, in the root's local space", default=True)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def invoke(self, context, event):
        ob = context.active_object
        self.use_head = bool(ob is not None and ob.type == 'MESH')
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'name')
        ob = context.active_object
        row = layout.row(); row.enabled = bool(ob is not None and ob.type == 'MESH')
        row.prop(self, 'use_head', text=f'Head: {ob.name}' if ob is not None and ob.type == 'MESH' else 'Head: (no active mesh)')

    def execute(self, context):
        name = self.name.strip() or 'hair-project'
        if name.lower().endswith('.hairmesh'):
            name = name[:-len('.hairmesh')]
        ob = context.active_object
        head_ob = ob if self.use_head and ob is not None and ob.type == 'MESH' else None
        proj = edit.new_project(name + '.hairmesh')
        root, root_col = make_root(context, name, proj, scale=1.0)
        if head_ob is not None:
            try:
                set_head_object(context, root, proj, head_ob)
            except ValueError as e:
                self.report({'WARNING'}, f'Head not set: {e}')
        for o in context.view_layer.objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({'INFO'}, f'HairMesh: new project "{name}"{" with head " + head_ob.name if head_ob is not None and proj.head else ""}; kept in text "{root["hairmesh_text"]}"')
        tool.activate(context, self.report)                         # the tool switches on (in the screen's 3D View when called from elsewhere; nothing in -b)
        return {'FINISHED'}


class HAIRMESH_OT_set_head(bpy.types.Operator):
    """Make the active Mesh object the head of the HairMesh project (the surface the hair is drawn on). Run it again after moving or editing the head"""
    bl_idname = 'hairmesh.set_head'
    bl_label = 'Set HairMesh Head'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return owner.visible(context) and ob is not None and ob.type == 'MESH' and session.find_root(context) is not None

    def execute(self, context):
        root = session.find_root(context)
        ob = context.active_object
        if session.is_root(ob) or (ob.parent is not None and ob.get('hairmesh_type') in ('curve', 'mesh')):
            self.report({'ERROR'}, 'Pick the head mesh, not a HairMesh object')
            return {'CANCELLED'}
        try:
            proj = session.get_project(root)
            head = set_head_object(context, root, proj, ob)
        except (ValueError, ProjectFormatError) as e:
            self.report({'ERROR'}, f'Could not set the head: {e}')
            return {'CANCELLED'}
        from . import live
        live.invalidate_rows(root)                                  # S.headH is the default loop step: every lock from scratch
        live.update(context, root)
        self.report({'INFO'}, f'HairMesh: head "{ob.name}" (id {head.id}), height {proj.S.headH:g}; loop step {proj.S.headH / 24:g} by default')
        return {'FINISHED'}


def write_file(op, root, path):
    """Writes the project of `root` to `path` as the original's Save does (projectData 10050), keeping the thumbnail
    and the reference images of the file it was opened from / last saved to (the port never holds them: in the example
    10 MB of references a plain write would lose); `path` becomes the remembered one. Returns the FINISHED / CANCELLED
    set, reporting through `op`."""
    from ..core.hairmesh_io import kept_fields
    src, _dirty = session.file_state(root)
    kept = kept_fields(src) if src else {}
    if kept is None and os.path.exists(path) and os.path.abspath(path) == os.path.abspath(src):
        op.report({'ERROR'}, f'{os.path.basename(path)} cannot be read: not written over, its reference images would be lost (use Save As)')
        return {'CANCELLED'}
    try:
        proj = session.get_project(root)
        write_project(path, proj, kept or {})
    except (OSError, ValueError, ProjectFormatError) as e:
        op.report({'ERROR'}, f'Could not write the project: {e}')
        return {'CANCELLED'}
    session.mark_saved(root, os.path.abspath(path))
    if kept is None:
        op.report({'WARNING'}, f'Saved to {os.path.basename(path)}, but {os.path.basename(src)} could not be read: its thumbnail and reference images are not in the new file')
        return {'FINISHED'}
    op.report({'INFO'}, f'HairMesh: {len(proj.curves())} curves, {len(proj.surfaces())} walls, {len(proj.groups())} groups'
                        f'{", head" if proj.head else ""} saved to {os.path.basename(path)}'
                        f'{" (thumbnail and reference images kept)" if kept else ""}')
    return {'FINISHED'}


class HAIRMESH_OT_export_project(bpy.types.Operator, ExportHelper):
    """Save the HairMesh project of this scene as a new .hairmesh file; that file becomes the one Save writes to"""
    bl_idname = 'hairmesh.export_project'                           # Save As (1c of the manual test of the lot ux5): the export, which now remembers its path
    bl_label = 'Save HairMesh Project As'
    bl_options = {'REGISTER'}

    filename_ext = '.hairmesh'
    filter_glob: StringProperty(default='*.hairmesh', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    def invoke(self, context, event):
        root = session.find_root(context)
        if not self.filepath and root is not None:
            path, _dirty = session.file_state(root)
            if path:
                self.filepath = path                                # the dialog starts on the remembered file
            else:
                base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ''
                self.filepath = os.path.join(base, f'{root.get("hairmesh_project", "hair-project")}.hairmesh')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        return write_file(self, session.find_root(context), self.filepath)


SAVE_DONE = 'Nothing to save: the .hairmesh holds this project already'


class HAIRMESH_OT_save_project(bpy.types.Operator):
    """Save the HairMesh project back into the .hairmesh it was opened from or last saved to (no dialog); a project never saved asks where, as Save As"""
    bl_idname = 'hairmesh.save_project'
    bl_label = 'Save HairMesh Project'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        try:
            if not session.save_needed(session.find_root(context)):
                return SAVE_DONE
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def invoke(self, context, event):
        path, _dirty = session.file_state(session.find_root(context))
        if not path:
            bpy.ops.hairmesh.export_project('INVOKE_DEFAULT')       # no file yet (a new project): the original opens the picker too (!proj.handle, 10096)
            return {'FINISHED'}
        if session.disk_changed(path):                              # edited elsewhere (the app) since it was opened / saved here: ask before writing over it
            return context.window_manager.invoke_confirm(self, event, title='Overwrite the .hairmesh?',
                                                         message=f'{os.path.basename(path)} changed on disk since it was opened or saved here', confirm_text='Save', icon='WARNING')
        return self.execute(context)

    def execute(self, context):
        root = session.find_root(context)
        path, _dirty = session.file_state(root)
        if not path:
            self.report({'ERROR'}, 'This project was never saved to a file: use Save As')
            return {'CANCELLED'}
        if not session.save_needed(root):                           # part 1 of the lot ux7: the button is dimmed then
            self.report({'INFO'}, SAVE_DONE)
            return {'CANCELLED'}
        return write_file(self, root, path)


def menu_func_export(self, context):
    if owner.visible(context):                                      # File > Export is not filtered by Blender (bl_owner_use_filter = False)
        self.layout.operator(HAIRMESH_OT_export_project.bl_idname, text='HairMesh Project (.hairmesh)')


def menu_func_object(self, context):
    """Object > HairMesh: the HairMesh Menu as a submenu (everything, contextual; tool.menu_items)"""
    self.layout.separator()
    self.layout.menu('HAIRMESH_MT_menu', text='HairMesh', icon='OUTLINER_OB_EMPTY')
