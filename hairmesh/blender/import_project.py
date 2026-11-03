# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""File > Import > HairMesh Project (.hairmesh)."""
import json
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core import conversion as CONV
from ..core.hairmesh_io import ProjectFormatError, project_from_dict
from . import owner
from .coords import auto_scale
from .scene_build import build_project


class HAIRMESH_OT_import_project(bpy.types.Operator, ImportHelper):
    """Import a Stylized HairMesh Creator project: curves, groups and the reference head, with the project kept in the .blend"""
    bl_idname = 'hairmesh.import_project'
    bl_label = 'Import HairMesh Project'
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = '.hairmesh'
    filter_glob: StringProperty(default='*.hairmesh', options={'HIDDEN'})

    scale_mode: EnumProperty(
        name='Scale',
        items=(('AUTO', 'Automatic', 'A power of ten that makes the head between 0.1 m and 1 m tall'),
               ('MANUAL', 'Manual', 'Use the scale below (project units to meters)')),
        default='AUTO')
    scale: FloatProperty(name='Manual Scale', description='Blender units per project unit', default=1.0, min=1e-9, soft_min=1e-6, soft_max=1000.0, precision=6)
    curve_mode: EnumProperty(
        name='Curves',
        items=(('BEZIER', 'Bezier', 'Exact anchors and handles'),
               ('POLY', 'Sampled Polyline', "The core's own sampling, 24 points per segment (parity debugging)")),
        default='BEZIER')
    import_head: BoolProperty(name='Import Head', description='The reference head embedded in the project, as a mesh', default=True)
    curve_objects: BoolProperty(name='Curve Objects', description='Also make a Blender Curve object per hair curve (the HairMesh tool draws the curves itself; these are for use outside it)', default=False)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'scale_mode')
        if self.scale_mode == 'MANUAL':
            layout.prop(self, 'scale')
        layout.prop(self, 'import_head')
        layout.prop(self, 'curve_objects')
        if self.curve_objects:
            layout.prop(self, 'curve_mode')

    def execute(self, context):
        try:
            with open(self.filepath, encoding='utf-8-sig') as f:   # (the browser's File.text() drops a byte-order mark too)
                text = f.read()
            P = json.loads(text)
            proj = project_from_dict(P, os.path.basename(self.filepath))
        except (OSError, ValueError, ProjectFormatError) as e:
            self.report({'ERROR'}, f'Could not open the project: {e}')
            return {'CANCELLED'}
        CONV.restoreConv(proj)
        scale = auto_scale(proj.S.headH if proj.head else None) if self.scale_mode == 'AUTO' else self.scale
        root, stats = build_project(context, proj, scale=scale, curve_mode=self.curve_mode, import_head=self.import_head, curve_objects=self.curve_objects)
        for ob in context.view_layer.objects:
            ob.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        from . import live, meshes, session
        # (restore, 1262: the meshes of a pending conversion are the file's, a baked one with them: kept while their walls stand,
        # and never taken by Convert for what it would build)
        meshes.baking(root, proj, [m for m in CONV.meshes(proj) if m.pending])
        live.update(context, root)                                  # the walls come with the import (one undo step)
        meshes.baked(root, proj)
        session.mark_saved(root, os.path.abspath(self.filepath))    # Save (the Project tab) writes back here (1c of the manual test of the lot ux5)
        self.report({'INFO'}, f'HairMesh: {len(proj.curves())} curves in {stats["groups"]} groups'
                              f'{", head" if stats["head"] else ""}; scale {scale:g}'
                              f'{" (automatic)" if self.scale_mode == "AUTO" else ""}; project kept in text "{root["hairmesh_text"]}"')
        return {'FINISHED'}


def menu_func_import(self, context):
    if owner.visible(context):                                      # File > Import is not filtered by Blender (bl_owner_use_filter = False)
        self.layout.operator(HAIRMESH_OT_import_project.bl_idname, text='HairMesh Project (.hairmesh)')
