# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Migrate Hair from .hairmesh (3.7b): another project's hair — its curves, walls, modifiers and crowns, with their
groups — into the project of the scene, under a new group named after the file (a preset as its own group), fitted
to the project's head (core/migrate.py: migrateFile, the original's File > Migrate, 10496). One undo step; the
selection becomes the new group, as in the original. File > Import, the Project tab of the HairMesh Menu (D) and of
the N panel."""
import os

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core import migrate as MG
from ..core.selection import Selection
from . import live, owner, select, session, slow


class HAIRMESH_OT_migrate_project(bpy.types.Operator, ImportHelper):
    """Bring the hair of another .hairmesh — its curves, walls, modifiers and crowns, with their groups — into the project of this scene, under a new group named after the file, fitted to this project's head (a preset comes in as its own group)"""
    bl_idname = 'hairmesh.migrate_project'
    bl_label = 'Migrate Hair from .hairmesh'
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = '.hairmesh'
    filter_glob: StringProperty(default='*.hairmesh;*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not owner.visible(context):
            return False
        if session.find_root(context) is None:                      # (part 1 of the lot ux7: dimmed with the reason and what to do)
            cls.poll_message_set('No HairMesh project in this scene to migrate into: start one with New Project (or Import .hairmesh) — HairMesh Menu (D) > Project, or the N panel — then migrate')
            return False
        return True

    def execute(self, context):
        win = getattr(context, 'window', None)
        if win is not None:                                         # the hair that came in gets its row tables: seconds for a whole project
            win.cursor_set('WAIT')
        try:
            return self._migrate(context)
        finally:
            if win is not None:
                win.cursor_set('DEFAULT')

    def _migrate(self, context):
        root = session.find_root(context)
        P = session.get_project(root)
        name = os.path.basename(self.filepath)
        try:
            with open(self.filepath, encoding='utf-8-sig') as f:   # (the browser's File.text() drops a byte-order mark too)
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            self.report({'ERROR'}, f'Could not read {name}: {e}')
            return {'CANCELLED'}
        with slow.part('the tables brought up to date first'):
            live.row_tables(root, P)                                # (right after an undo they are inherited against THIS state; against the migrated one, its new
                                                                    # densities, caps and crowns would make the inheritance give up: every lock again, 9 s on the example)
        key = MG.rebuild_key(P)
        with slow.part('hair migrated', name) as what:
            try:
                r = MG.migrateFile(P, text, name)
            except Exception as e:                                  # (migrateData builds everything before it changes the model; never leave a half-changed one in the cache)
                session.forget_model(root.name)
                self.report({'ERROR'}, f'Could not migrate {name}: {e!r}')
                return {'CANCELLED'}
            what[0] = f'{len(r.curves)} curves, {len(r.items)} items from {name} (the row tables below build their locks)' if r.ok else None
        if not r.ok:                                                # (the project is unchanged: nothing to undo)
            self.report({'WARNING'}, r.msg)
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection(list(r.sel), []))   # S.sel = the new group (or the preset's top items); S.asel = []
        session.save_project(root, P)                               # commit(): the one undo step
        from .ops_groups import sync_collections
        sync_collections(context, root, P)
        if MG.rebuild_key(P) != key:                                # the frame (no head: from the hair) or the project loop step moved: every lock
            live.invalidate_rows(root)
            slow.note('every lock rebuilt', 'the frame or the project loop step changed with the hair that came in')
        else:
            live.touch(root, r.curves)                              # the locks of the curves that came in (the same tables as a full build)
        live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, r.msg)
        if r.note:
            self.report({'WARNING'}, r.note[0].upper() + r.note[1:])
        return {'FINISHED'}


def menu_func_import(self, context):
    if owner.visible(context):                                      # File > Import is not filtered by Blender (bl_owner_use_filter = False)
        self.layout.operator(HAIRMESH_OT_migrate_project.bl_idname, text='HairMesh: Migrate Hair (.hairmesh)')
