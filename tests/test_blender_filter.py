"""The workspace's "Filter Add-ons" inside Blender (blender -b): the add-on registered with the owner id set, as
addon_utils.enable does; what Blender stamps on its own (keymap, menu draw functions); the visible() helper; the
toolbar entry, static on purpose (the icon stays like HardOps's); the operator polls, the import / export menu
entries, the pie and the D menu; the tool switched on in a workspace that filters the add-on out: inert, nothing
drawn, its keymap stamped with the owner so Blender keeps it from firing, and never silent: a notice in the header
and in the N panel (the one panel registered without the owner id), with a button that ticks the add-on."""
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER = 'hairmesh'                                                  # the module name when registered from the repository


class Ctx:
    """A context seen through a chosen workspace; everything else is bpy.context's."""
    def __init__(self, workspace):
        self.workspace = workspace

    def __getattr__(self, name):
        return getattr(bpy.context, name)


class Layout:
    """Records what a menu or header draw function puts in its layout."""
    def __init__(self):
        self.ops = []
        self.labels = []

    def operator(self, idname, **kw):
        self.ops.append(idname)
        return self

    def label(self, text='', **kw):
        self.labels.append(text)

    def prop(self, *a, **kw):
        pass

    def prop_enum(self, *a, **kw):
        pass

    def menu(self, idname, **kw):
        self.ops.append('menu:' + idname)

    def popover(self, panel, **kw):
        self.ops.append('popover:' + panel)

    def row(self, **kw):
        return self

    def column(self, **kw):
        return self

    def separator(self, **kw):
        pass


class Menu:
    def __init__(self):
        self.layout = Layout()


@unittest.skipUnless(bpy, 'needs Blender')
class TestWorkspaceFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from _bpy import _bl_owner_id_get, _bl_owner_id_set
        from hairmesh.blender import draw, import_project, keymaps, owner, project_ops, tool
        cls.hairmesh, cls.draw, cls.import_project, cls.keymaps, cls.owner, cls.project_ops, cls.tool = hairmesh, draw, import_project, keymaps, owner, project_ops, tool
        bpy.ops.wm.read_factory_settings(use_empty=True)
        prev = _bl_owner_id_get()
        _bl_owner_id_set(OWNER)                                     # what addon_utils.enable does around register()
        try:
            hairmesh.register()
        except ValueError:
            pass
        finally:
            _bl_owner_id_set(prev)
        bpy.context.window_manager.keyconfigs.update()

    @classmethod
    def tearDownClass(cls):
        cls.hairmesh.unregister()

    def setUp(self):
        self.ws = bpy.data.workspaces[0]
        self.ws.use_filter_by_owner = False
        for o in list(self.ws.owner_ids):
            self.ws.owner_ids.remove(o)

    tearDown = setUp

    def hidden(self):
        self.ws.use_filter_by_owner = True
        return Ctx(self.ws)

    def test_blender_stamped_the_owner_on_what_it_filters_itself(self):
        self.assertEqual(self.owner.OWNER, OWNER)
        kc = bpy.context.window_manager.keyconfigs
        self.assertEqual(kc.addon.keymaps[self.keymaps.TOOL_KM].bl_owner_id, OWNER, 'the tool keymap: WM_keymap_poll filters it')
        self.assertEqual(kc.user.keymaps[self.keymaps.TOOL_KM].bl_owner_id, OWNER, 'and its user copy')
        self.assertEqual(kc.user.keymaps[self.keymaps.GLOBAL_KM].bl_owner_id, '', "the 3D View keymap is Blender's: Shift+Alt+H is not filtered by itself")
        self.assertEqual(self.import_project.menu_func_import._owner, OWNER, 'menu draw functions get _owner (Menu.append)')
        self.assertEqual(self.project_ops.menu_func_export._owner, OWNER)
        self.assertEqual(self.project_ops.menu_func_object._owner, OWNER)
        self.assertFalse(bpy.types.TOPBAR_MT_file_import.bl_owner_use_filter, 'but File > Import ignores owners')
        self.assertFalse(bpy.types.TOPBAR_MT_file_export.bl_owner_use_filter)

    def test_visible(self):
        v = self.owner.visible
        self.assertTrue(v(Ctx(self.ws)), 'filter off')
        self.assertTrue(v(Ctx(None)), 'no workspace (background mode)')
        ctx = self.hidden()
        self.assertFalse(v(ctx), 'filter on, add-on not ticked')
        self.ws.owner_ids.new(OWNER)
        self.assertTrue(v(ctx), 'ticked')
        self.ws.owner_ids.new('bl_ext.user_default.other')
        self.assertTrue(v(ctx))
        self.ws.owner_ids.remove(self.ws.owner_ids[OWNER])
        self.assertFalse(v(ctx))
        with bpy.context.temp_override(workspace=self.ws):
            self.assertFalse(v(), 'the real context under an override')
            self.assertFalse(v(bpy.context))

    def test_toolbar_entry_stays_whatever_the_filter_says(self):
        """Decided on 14/10/2026: the icon does not follow "Filter Add-ons" (HardOps's does not either). The entry
        register_tool put in the static list is kept as it is, so the tool system finds the tool in every
        workspace and Preferences > Keymap lists its keymap in the 3D View > Tool tree."""
        from bl_ui.space_toolsystem_common import ToolDef, ToolSelectPanelHelper, item_from_id
        cls = ToolSelectPanelHelper._tool_class_from_space_type('VIEW_3D')
        tools = cls._tools['OBJECT']
        td = self.tool.HAIRMESH_WT_tool._bl_tool
        self.assertIs(tools[-1], td, 'the ToolDef itself, at the end (no HardOps here)')
        self.assertIsNone(tools[-2], 'with the separator register_tool put before it')
        self.assertFalse(any(callable(t) for t in tools), 'no dynamic item of ours')

        def ids(items):
            return [t.idname if isinstance(t, ToolDef) else t for t in items]
        shown = ids(cls.tools_from_context(Ctx(self.ws), mode='OBJECT'))
        self.assertEqual(shown[-2:], [None, self.keymaps.TOOL_ID], 'filter off: separator + tool')
        self.assertEqual(ids(cls.tools_from_context(self.hidden(), mode='OBJECT')), shown, 'filtered: the same')
        self.assertIs(item_from_id(Ctx(self.ws), 'VIEW_3D', self.keymaps.TOOL_ID), td, 'found, so it can be clicked')
        self.assertIn(self.keymaps.TOOL_KM, [km.name for km in bpy.context.window_manager.keyconfigs.user.keymaps])

    def test_operators_poll_false_when_filtered_out(self):
        ops = ('new_project', 'import_project', 'export_project', 'set_head', 'convert', 'draw_curve', 'live_rebuild', 'toggle_tool', 'set_subtool',
               'subtool_draw', 'select', 'insert_anchor', 'smooth_drag', 'delete_selected', 'select_circle', 'reverse_curves', 'recalc_handles', 'move_anchors')
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.hairmesh.new_project(name='filter', use_head=True)
        with bpy.context.temp_override(workspace=self.ws):
            self.assertTrue(bpy.ops.hairmesh.new_project.poll()); self.assertTrue(bpy.ops.hairmesh.set_subtool.poll())
            self.assertTrue(bpy.ops.hairmesh.export_project.poll()); self.assertTrue(bpy.ops.hairmesh.convert.poll())
            self.ws.use_filter_by_owner = True
            for name in ops:
                self.assertFalse(getattr(bpy.ops.hairmesh, name).poll(), name)
            self.ws.owner_ids.new(OWNER)
            self.assertTrue(bpy.ops.hairmesh.new_project.poll()); self.assertTrue(bpy.ops.hairmesh.convert.poll())
            self.assertTrue(bpy.ops.hairmesh.import_project.poll()); self.assertTrue(bpy.ops.hairmesh.live_rebuild.poll())
        self.assertTrue(bpy.ops.hairmesh.keymap_restore.poll(), 'the preferences button is not filtered (internal, reached from the preferences)')

    def test_menus(self):
        m = Menu(); self.import_project.menu_func_import(m, Ctx(self.ws)); self.assertEqual(m.layout.ops, ['hairmesh.import_project'])
        m = Menu(); self.project_ops.menu_func_export(m, Ctx(self.ws)); self.assertEqual(m.layout.ops, ['hairmesh.export_project'])
        ctx = self.hidden()
        m = Menu(); self.import_project.menu_func_import(m, ctx); self.assertEqual(m.layout.ops, [])
        m = Menu(); self.project_ops.menu_func_export(m, ctx); self.assertEqual(m.layout.ops, [])
        for name in ('HAIRMESH_MT_pie', 'HAIRMESH_MT_walls', 'HAIRMESH_MT_menu', 'HAIRMESH_MT_subtools', 'HAIRMESH_PT_popup'):
            self.assertFalse(getattr(bpy.types, name).poll(ctx), name)
        self.ws.owner_ids.new(OWNER)
        for name in ('HAIRMESH_MT_pie', 'HAIRMESH_MT_walls', 'HAIRMESH_MT_menu', 'HAIRMESH_MT_subtools', 'HAIRMESH_PT_popup'):
            self.assertTrue(getattr(bpy.types, name).poll(ctx), name)
        m = Menu(); self.import_project.menu_func_import(m, ctx); self.assertEqual(m.layout.ops, ['hairmesh.import_project'])

    def test_tool_switched_on_in_a_filtering_workspace_is_inert(self):
        """Clicked on the icon in a workspace that filters the add-on out, the tool stays active (nothing makes it
        leave) but has no effect: the draw handler returns before touching gpu, the header shows a notice only,
        our activate() path declines, and the keymap is Blender's business: both copies of the tool keymap carry
        the owner id, so WM_keymap_poll (BKE_workspace_owner_id_check) keeps every item from firing, as it does
        for HardOps's keymap. A key press itself cannot be sent in background mode."""
        t, draw = self.tool, self.draw
        ws = bpy.context.workspace                                  # the window's workspace, where the tool is switched on (not workspaces[0])
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
            self.assertEqual(bpy.ops.wm.tool_set_by_id(name=self.keymaps.TOOL_ID), {'FINISHED'}, 'the click on the icon')
        self.assertTrue(t.is_active(bpy.context))
        self.addCleanup(setattr, ws, 'use_filter_by_owner', False)
        self.addCleanup(lambda: [ws.owner_ids.remove(o) for o in list(ws.owner_ids)])
        ws.use_filter_by_owner = True
        with bpy.context.temp_override(area=area, region=region):
            self.assertFalse(self.owner.visible(bpy.context))
            self.assertIsNone(draw._draw(), 'nothing drawn (gpu is never imported here)')
            self.assertTrue(t.is_active(bpy.context), 'and the tool is still the active one')
            m = Menu(); self.tool.HAIRMESH_WT_tool.draw_settings(bpy.context, m.layout, None)
            self.assertEqual(m.layout.ops, ['hairmesh.show_in_workspace'], 'the header: only the button that ticks the add-on')
            self.assertEqual(len(m.layout.labels), 1); self.assertIn('Filter Add-ons', m.layout.labels[0])
            self.assertIn('clicks and keys are off', m.layout.labels[0], 'and why nothing happens')
            self.assertTrue(bpy.types.HAIRMESH_PT_hidden.poll(bpy.context), 'the N panel says it too')
            self.assertFalse(t.activate(bpy.context), 'our own activation path declines (the panel buttons are hidden anyway)')
            self.assertTrue(t.is_active(bpy.context))
            kc = bpy.context.window_manager.keyconfigs
            for kcfg in (kc.addon, kc.user):
                km = kcfg.keymaps[self.keymaps.TOOL_KM]
                self.assertEqual(km.bl_owner_id, OWNER, 'WM_keymap_poll refuses the keymap in this workspace')
                self.assertTrue(all(kmi.active for kmi in km.keymap_items), 'the items themselves are untouched')
            ws.owner_ids.new(OWNER)
            self.assertTrue(self.owner.visible(bpy.context))
            m = Menu(); self.tool.HAIRMESH_WT_tool.draw_settings(bpy.context, m.layout, None)
            self.assertFalse(any('Filter Add-ons' in l for l in m.layout.labels), 'ticked again: the normal header')
            self.assertTrue({'hairmesh.new_project', 'hairmesh.convert'} & set(m.layout.ops), 'with its buttons')
        with bpy.context.temp_override(area=area, region=region):
            self.assertTrue(t.deactivate(bpy.context))
        self.assertFalse(hasattr(t, 'leave_later'), 'the exit-on-filter path is gone')
        self.assertIsNotNone(draw._handle)

    def test_the_notice_panel_is_not_filtered_and_shows_only_for_the_inert_tool(self):
        from _bpy import _bl_owner_id_get, _bl_owner_id_set
        P = bpy.types.HAIRMESH_PT_hidden
        self.assertEqual(P.bl_category, 'HairMesh')
        # register_unowned: no owner id while Blender registers the class (rna_Panel_register stamps the one set at that
        # moment; a Panel's own owner id is not readable from Python), and the one in use is set back afterwards
        seen = []

        class Probe(bpy.types.Panel):
            bl_idname = 'HAIRMESH_PT_owner_probe'; bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_label = 'probe'

            @classmethod
            def register(cls):                                      # called by register_class, right after the RNA registration
                seen.append(_bl_owner_id_get())

            def draw(self, context):
                pass
        prev = _bl_owner_id_get()
        _bl_owner_id_set(OWNER)
        try:
            self.owner.register_unowned(Probe)
            self.assertEqual(_bl_owner_id_get(), OWNER, 'the owner being stamped is set back')
        finally:
            _bl_owner_id_set(prev)
        bpy.utils.unregister_class(Probe)
        self.assertEqual(seen, [None])
        t = self.tool
        ws = bpy.context.workspace
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        self.addCleanup(setattr, ws, 'use_filter_by_owner', False)
        self.addCleanup(lambda: [ws.owner_ids.remove(o) for o in list(ws.owner_ids)])
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
            self.assertFalse(P.poll(bpy.context), 'add-on visible: nothing to say')
            ws.use_filter_by_owner = True
            self.assertFalse(P.poll(bpy.context), 'filtered on purpose and not in use: no trace in the sidebar')
            bpy.ops.wm.tool_set_by_id(name=self.keymaps.TOOL_ID)
            self.assertTrue(P.poll(bpy.context), 'filtered with the tool active: the notice')
            m = Menu(); P.draw(m, bpy.context)
            self.assertEqual(m.layout.ops, ['hairmesh.show_in_workspace'])
            self.assertTrue(any('Filter Add-ons' in l for l in m.layout.labels))
            ws.owner_ids.new(OWNER)
            self.assertFalse(P.poll(bpy.context), 'ticked: the real panels are back')
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')

    def test_show_in_workspace_ticks_the_add_on(self):
        op = bpy.ops.hairmesh.show_in_workspace
        with bpy.context.temp_override(workspace=self.ws):
            self.assertFalse(op.poll(), 'offered only where the add-on is filtered out')
            self.ws.use_filter_by_owner = True
            self.assertTrue(op.poll())
            self.assertEqual(op(), {'FINISHED'})
            self.assertIn(OWNER, self.ws.owner_ids)
            self.assertTrue(self.owner.visible(bpy.context))
            self.assertFalse(op.poll())
            self.assertTrue(bpy.ops.hairmesh.new_project.poll(), 'and the add-on works there again')
        self.assertTrue(self.ws.use_filter_by_owner, 'the filter itself stays on: only HairMesh is ticked')
