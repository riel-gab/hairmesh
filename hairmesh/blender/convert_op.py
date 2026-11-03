# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Convert, Confirm, Cancel and the Live / Baked view (3.7, the original's flow: startConversion / confirmConversion /
cancelConversion, hair-modeler.clean.html:10010-10039). The project in the .blend is the source: Convert adds one
pending mesh item per ticked group (core/conversion.py) and the "<group> mesh" objects follow the items
(meshes.sync, through live.update), with the session's row tables (incremental), reusing the data of a mesh whose
signature did not change (the incremental reconversion, ours).

In the viewport Convert runs as a modal, one mesh per timer tick, so the status bar says what it is doing
("Converting 5/14: Base Main mesh") and the cursor counts; Esc gives up with nothing changed (the model is read again
from its text). In background mode, and from scripts, execute does it in one go."""
import time

import bpy
from bpy.props import EnumProperty

from ..core import conversion as CONV
from ..core import convert as CV
from ..core import crown as CR
from ..core.hairmesh_io import ProjectFormatError
from . import live, meshes, owner, session, slow

find_root = session.find_root
load_project_from_blend = session.get_project


def collection_of_group(root_col, gid):
    stack = [root_col]
    while stack:
        col = stack.pop()
        if col.get('hairmesh_id') == gid:
            return col
        stack.extend(col.children)
    return root_col


def nothing_to_convert(P):
    """why Convert would make no mesh, in the original's words (startConversion 10012: "Tick the groups to convert";
    10023: "The ticked groups contain no walls" — walls, in the port), else None"""
    if not [g for g in P.settings.convChecked if g in P.items]:
        return 'Tick the groups to convert (the Conversion tab)'
    if not CV.conversionPlan(P):
        return 'The ticked groups contain no walls'
    return None


def _project(context):
    root = find_root(context)
    if root is None:
        return None, None
    try:
        return root, session.get_project(root)
    except (ValueError, ProjectFormatError):
        return root, None


def _why_tooltip(context, fn, doc):
    """a dynamic description: why the button does nothing now (part 1 of the lot ux7), else its own text"""
    try:
        _root, P = _project(context)
        why = fn(P) if P is not None else None
    except Exception:                                               # (a tooltip must never raise)
        why = None
    return why or doc


def _done_report(n, seconds, stats):
    reused = stats.get('reused', 0)
    how = f' ({stats.get("built", n)} built, {reused} unchanged)' if reused else ''
    return f'HairMesh: {n} mesh{"" if n == 1 else "es"} converted in {seconds:.1f} s{how}. Orange = preview: Confirm or Cancel (D > Conversion)'


class HAIRMESH_OT_convert(bpy.types.Operator):
    """Convert the ticked groups into quad meshes: a preview (orange) to Confirm or Cancel. The walls and curves converted are hidden; Live / Baked switches the view"""
    bl_idname = 'hairmesh.convert'
    bl_label = 'Convert HairMesh Groups'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        """part 1 of the lot ux7: why Convert, dimmed, would make nothing"""
        def why(P):
            w = nothing_to_convert(P)
            return f'Nothing to convert now: {w[0].lower()}{w[1:]}' if w else None
        return _why_tooltip(context, why, cls.__doc__)

    def _start(self, context):
        """the checks, then the generator of startConversion: (root, P, steps) or None (reported)"""
        self.root = find_root(context)
        try:
            P = load_project_from_blend(self.root)
        except (ValueError, ProjectFormatError) as e:
            self.report({'ERROR'}, f'Could not read the project: {e}')
            return None
        why = nothing_to_convert(P)
        if why:                                                     # the original's toasts (startConversion 10012, 10023): before any table is built
            self.report({'INFO'}, why)
            return None
        self.P = P
        self.t0 = time.perf_counter()
        self.reuse, self.stats = meshes.reuser(self.root, P)         # (the earlier meshes captured now: startConversion cancels a pending conversion first)
        self.cold = (session.runtime(self.root) or {}).get('rows') is None
        self.steps = CONV.startConversionSteps(P, lambda: meshes.tables(self.root, P), CR.crownData, self._reuse, replace=True)
        return self.steps

    def _reuse(self, mesh):
        return self.reuse(mesh)

    def _finish(self, context, msg):
        P = self.P
        if msg:
            self.report({'INFO'}, msg)
            session.forget_model(self.root.name)                    # (cancelConversion may have run: the text still holds the state before)
            return {'CANCELLED'}
        made = [P.items[i] for i in P.conv['entries']]
        with slow.part('signatures'):
            meshes.remember(self.root, P, made)
        session.save_project(self.root, P)
        meshes.just_built(self.root, P, made)                       # (a crown's mesh has no signature: not rebuilt by the live.update below, 3.8a)
        with slow.part('the preview and the objects (live.update)'):
            live.update(context, self.root)
        self.report({'INFO'}, _done_report(len(made), time.perf_counter() - self.t0, self.stats))
        return {'FINISHED'}

    def execute(self, context):
        steps = self._start(context)
        if steps is None:
            return {'CANCELLED'}
        with slow.part('the meshes built (startConversion)') as what:
            while True:
                try:
                    next(steps)
                except StopIteration as e:
                    msg = e.value
                    break
            what[0] = f'{self.stats.get("built", 0)} built, {self.stats.get("reused", 0)} reused'
        return self._finish(context, msg)

    # ---- the viewport: one mesh per tick, the progress said ----
    def invoke(self, context, event):
        if bpy.app.background or context.window is None:
            return self.execute(context)
        steps = self._start(context)
        if steps is None:
            return {'CANCELLED'}
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        self.n = None
        context.window.cursor_set('WAIT')
        self._say(context, 'HairMesh: converting…')
        return {'RUNNING_MODAL'}

    def _say(self, context, text):
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass

    def _end(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        if self.n:
            wm.progress_end()
        self._say(context, None)
        try:
            context.window.cursor_set('DEFAULT')
        except Exception:
            pass

    def modal(self, context, event):
        if event.type == 'ESC':
            self._end(context)
            session.forget_model(self.root.name)                    # nothing changed: the model as its text holds it
            self.report({'INFO'}, 'Conversion stopped: nothing changed')
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}                                # (the model is half-made: no other edit meanwhile)
        try:
            with slow.part('a mesh built (startConversion)'):        # (3.8a: the step's line said "nothing of the add-on's own")
                i, n, mesh = next(self.steps)
        except StopIteration as e:
            self._end(context)
            return self._finish(context, e.value)
        except Exception as e:
            self._end(context)
            session.forget_model(self.root.name)
            self.report({'ERROR'}, f'Conversion failed: {e!r}')
            return {'CANCELLED'}
        wm = context.window_manager
        if self.n is None and n:
            self.n = n
            wm.progress_begin(0, n)
        if self.n:
            wm.progress_update(i)
        if i < n:
            name = self._next_name(i)
            first = ' (building the row tables first: a few seconds after opening the file)' if i == 0 and self.cold else ''
            self._say(context, f'HairMesh: converting {i + 1}/{n}: {name}{first}… (Esc stops)')
        else:
            self._say(context, f'HairMesh: {n} meshes converted, making the objects…')
        return {'RUNNING_MODAL'}

    def _next_name(self, i):
        if not hasattr(self, '_names'):
            ticked = {g for g in self.P.settings.convChecked if g in self.P.items}
            self._names = [self.P.items[g].name + ' mesh' for g in self.P.settings.convChecked
                           if g in self.P.items and (CV.collect(self.P, g, ticked, ['surface']) or CV.collect(self.P, g, ticked, ['crown']))]
        return self._names[i] if i < len(self._names) else ''


def _no_pending(P):
    return None if P is None or P.conv else 'No conversion is pending: Convert first (D > Conversion)'


class HAIRMESH_OT_confirm_conversion(bpy.types.Operator):
    """Keep the converted meshes: the preview becomes the hair mesh; the meshes of these groups from an earlier conversion are replaced"""
    bl_idname = 'hairmesh.confirm_conversion'
    bl_label = 'Confirm HairMesh Conversion'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        return _why_tooltip(context, _no_pending, cls.__doc__)

    def execute(self, context):
        root, P = _project(context)
        if P is None or not P.conv:
            self.report({'INFO'}, _no_pending(P) or 'No HairMesh project')
            return {'CANCELLED'}
        n = CONV.confirmConversion(P)
        session.save_project(root, P)
        live.update(context, root)
        self.report({'INFO'}, f'Conversion confirmed: {n} mesh{"" if n == 1 else "es"}')
        return {'FINISHED'}


class HAIRMESH_OT_cancel_conversion(bpy.types.Operator):
    """Drop the converted preview: its meshes are deleted and the walls and curves come back"""
    bl_idname = 'hairmesh.cancel_conversion'
    bl_label = 'Cancel HairMesh Conversion'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        return _why_tooltip(context, lambda P: None if P is None or P.conv else 'Nothing to cancel: no conversion is pending', cls.__doc__)

    def execute(self, context):
        root, P = _project(context)
        if P is None or not P.conv:
            self.report({'INFO'}, 'Nothing to cancel: no conversion is pending')
            return {'CANCELLED'}
        n = len(P.conv['entries'])
        CONV.cancelConversion(P)
        session.save_project(root, P)
        live.update(context, root)
        self.report({'INFO'}, f'Conversion cancelled: {n} preview mesh{"" if n == 1 else "es"} removed, the walls and curves are back')
        return {'FINISHED'}


VIEWS = (('LIVE', 'Live', 'Show the walls and curves the meshes were converted from, hide the meshes', 'MOD_LATTICE', 0),
         ('BAKED', 'Baked', 'Show the converted meshes, hide the walls and curves they were converted from', 'MESH_DATA', 1))


def view_why(P, mode):
    """why the Live / Baked button does nothing now, else None"""
    if P is None:
        return 'No HairMesh project'
    v = CONV.viewOf(P)
    if v is None:
        return 'No converted meshes yet: Convert first (D > Conversion)'
    if v == mode.lower():
        return ('Already Live: the walls and curves are shown, the meshes hidden' if mode == 'LIVE'
                else 'Already Baked: the meshes are shown, their walls and curves hidden')
    return None


class HAIRMESH_OT_conversion_view(bpy.types.Operator):
    """Switch the view. Live: the walls and curves shown, the converted meshes hidden. Baked: the meshes shown, what they were converted from hidden"""
    bl_idname = 'hairmesh.conversion_view'
    bl_label = 'HairMesh Live / Baked'
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name='View', items=VIEWS, default='BAKED')

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        mode = properties.mode
        return _why_tooltip(context, lambda P: view_why(P, mode), dict((v[0], v[2]) for v in VIEWS)[mode])

    def execute(self, context):
        root, P = _project(context)
        why = view_why(P, self.mode)
        if why:
            self.report({'INFO'}, why)
            return {'CANCELLED'}
        CONV.setView(P, self.mode.lower())
        session.save_project(root, P)
        live.update(context, root)
        n = len(CONV.meshes(P))
        self.report({'INFO'}, f'Live: {n} mesh{"" if n == 1 else "es"} hidden, the walls and curves shown' if self.mode == 'LIVE'
                    else f'Baked: {n} mesh{"" if n == 1 else "es"} shown, the walls and curves hidden')
        return {'FINISHED'}


CLASSES = (HAIRMESH_OT_convert, HAIRMESH_OT_confirm_conversion, HAIRMESH_OT_cancel_conversion, HAIRMESH_OT_conversion_view)
