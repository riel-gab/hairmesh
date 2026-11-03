"""Phase 3.3 inside Blender (blender -b): the Select tool's operators in their non-modal forms (the modal ones need a
viewport), the selection state on the scene, the panel sliders, the drawing colors of the selection and the overlay."""
import json
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(bpy, 'needs Blender')
class TestSelectOperators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import draw, live, ops_select, select, session
        from hairmesh.core import curves as CM, edit
        cls.hairmesh, cls.draw, cls.live, cls.ops_select, cls.select, cls.session, cls.CM, cls.edit = hairmesh, draw, live, ops_select, select, session, CM, edit
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            hairmesh.register()
        except ValueError:
            pass

    @classmethod
    def tearDownClass(cls):
        cls.hairmesh.unregister()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.session.forget()
        bpy.ops.hairmesh.new_project(name='sel')
        self.root = bpy.data.objects['HairMesh sel']
        self.hm = bpy.context.scene.hairmesh
        pts = lambda *ps: json.dumps([list(p) for p in ps])
        bpy.ops.hairmesh.draw_curve(points_json=pts((0, 0, 0), (10, 20, 2), (20, 40, -3), (30, 55, 1), (40, 60, 0)))
        bpy.ops.hairmesh.draw_curve(points_json=pts((50, 0, 0), (52, 30, 0), (55, 60, 0)))
        self.P = self.session.get_project(self.root)
        self.A, self.B = self.P.curves()

    def sel(self):
        return self.select.load(self.hm)

    def text(self):
        return json.loads(bpy.data.texts[self.root['hairmesh_text']].as_string())

    def test_select_clicks(self):
        A, B = self.A, self.B
        self.assertEqual(self.sel().items, [B.id], 'drawing a curve selects it')
        self.assertEqual(bpy.ops.hairmesh.select(anchor=A.anchors[1].id), {'FINISHED'})
        s = self.sel(); self.assertEqual((s.items, s.anchors), ([], [A.anchors[1].id]))
        bpy.ops.hairmesh.select(anchor=A.anchors[2].id, extend=True)
        self.assertEqual(self.sel().anchors, [A.anchors[1].id, A.anchors[2].id])
        bpy.ops.hairmesh.select(anchor=A.anchors[1].id, toggle=True)
        self.assertEqual(self.sel().anchors, [A.anchors[2].id])
        bpy.ops.hairmesh.select(curve=A.id)
        s = self.sel(); self.assertEqual((s.items, s.anchors), ([A.id], []))
        bpy.ops.hairmesh.select(curve=B.id, extend=True)
        self.assertEqual(self.sel().items, [A.id, B.id])
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertFalse(self.sel())
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.select(anchor=99999)
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.select()
        self.assertEqual(bpy.ops.hairmesh.select_circle(), {'CANCELLED'}, 'the Circle Select needs the viewport')
        root, P, s = self.select.current(bpy.context)
        self.assertIs(root, self.root); self.assertIs(P, self.P)

    def test_move_anchors_saves_and_marks_dirty(self):
        A = self.A
        bpy.ops.hairmesh.select(anchor=A.anchors[0].id)
        bpy.ops.hairmesh.select(anchor=A.anchors[1].id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.move_anchors(delta=(1.0, -2.0, 0.5)), {'FINISHED'})
        self.assertEqual(A.anchors[0].pos.toArray(), [1.0, -2.0, 0.5]); self.assertEqual(A.anchors[1].pos.toArray(), [11.0, 18.0, 2.5])
        self.assertFalse(A.dirty, 'the operator marked the curve dirty and the live update inside it rebuilt the curve (settle)')
        self.assertEqual(A.samples[0].p.toArray(), [1.0, -2.0, 0.5], 'the samples follow the moved anchor')
        saved = {a['id']: a['p'] for a in self.text()['anchors']}
        self.assertEqual(saved[A.anchors[0].id], [1.0, -2.0, 0.5], 'written to the datablock (one undo step)')
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(bpy.ops.hairmesh.move_anchors(delta=(1.0, 0.0, 0.0)), {'CANCELLED'}, 'nothing selected')

    def test_insert_anchor(self):
        A = self.A
        self.CM.rebuild_dirty(self.P)
        n = len(A.anchors)
        res = bpy.ops.hairmesh.insert_anchor(curve=A.id, sample=30, t=0.4)
        self.assertEqual(res, {'FINISHED'})
        self.assertEqual(len(A.anchors), n + 1)
        a = A.anchors[2]
        self.assertEqual(self.sel().anchors, [a.id], 'the new anchor is the selection')
        self.assertEqual(self.sel().items, [])
        self.assertIn(a.id, {x['id'] for x in self.text()['anchors']})
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.insert_anchor(curve=A.id, sample=9999)
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.insert_anchor()

    def test_delete_selected(self):
        A, B = self.A, self.B
        bpy.ops.hairmesh.select(anchor=A.anchors[2].id)
        self.assertEqual(bpy.ops.hairmesh.delete_selected(), {'FINISHED'})
        self.assertEqual(len(A.anchors), 4)
        self.assertEqual(self.sel().items, [A.id], 'the edited curve stays selected'); self.assertEqual(self.sel().anchors, [])
        self.assertEqual(A.ovr, {})
        bpy.ops.hairmesh.select(anchor=A.anchors[1].id)
        bpy.ops.hairmesh.delete_selected(keep_shape=True)
        self.assertEqual(len(A.anchors), 3); self.assertTrue(A.ovr, 'keep shape: frozen handles')
        self.assertEqual(len(self.text()['items'][0]['ovr']), len(A.ovr))
        # a 2-anchor curve losing one goes away
        bpy.ops.hairmesh.select(anchor=B.anchors[1].id)
        bpy.ops.hairmesh.delete_selected()
        self.assertEqual(len(B.anchors), 2)
        bpy.ops.hairmesh.select(anchor=B.anchors[0].id)
        bpy.ops.hairmesh.delete_selected()
        self.assertNotIn(B.id, self.P.items); self.assertEqual(self.sel().items, [])
        # curves: the selected items are deleted
        bpy.ops.hairmesh.select(curve=A.id)
        bpy.ops.hairmesh.delete_selected()
        self.assertEqual(self.P.curves(), []); self.assertFalse(self.sel())
        self.assertEqual(self.text()['items'], [])
        self.assertEqual(bpy.ops.hairmesh.delete_selected(), {'CANCELLED'}, 'nothing selected')

    def test_smoothness_reverse_recalc_and_sliders(self):
        A, B = self.A, self.B
        hm = self.hm
        bpy.ops.hairmesh.select(anchor=A.anchors[1].id)
        bpy.ops.hairmesh.select(anchor=A.anchors[3].id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.smooth_drag(value=0.3), {'FINISHED'})
        self.assertEqual((A.anchors[1].smooth, A.anchors[3].smooth, A.anchors[2].smooth), (0.3, 0.3, 1.0))
        self.assertAlmostEqual(bpy.context.window_manager.hairmesh_slide.anchor_smooth, 0.3, places=6, msg='the slider reads the last selected anchor')
        bpy.context.window_manager.hairmesh_slide.anchor_smooth = 0.8
        self.assertEqual((A.anchors[1].smooth, A.anchors[3].smooth), (0.8, 0.8), 'the slider writes every selected anchor')
        self.assertEqual({a['id']: a['s'] for a in self.text()['anchors']}[A.anchors[1].id], 0.8, 'and saves')
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.smooth_drag()
        self.assertEqual(bpy.ops.hairmesh.recalc_handles(), {'FINISHED'})
        self.assertEqual((A.anchors[1].smooth, A.anchors[3].smooth), (1.0, 1.0))
        # the curve sliders and buttons
        bpy.ops.hairmesh.select(curve=A.id)
        bpy.ops.hairmesh.select(curve=B.id, extend=True)
        self.assertEqual(bpy.context.window_manager.hairmesh_slide.curve_round, 0.0)
        bpy.context.window_manager.hairmesh_slide.curve_round = 0.5
        self.assertEqual((A.round, B.round), (0.5, 0.5)); self.assertEqual(bpy.context.window_manager.hairmesh_slide.curve_round, 0.5)
        bpy.context.window_manager.hairmesh_slide.curve_fair = 0.25
        self.assertEqual((A.fair, B.fair), (0.25, 0.25))
        before = [a.pos.toArray() for a in A.anchors]
        bpy.context.window_manager.hairmesh_slide.curve_even = 0.5
        self.assertEqual(A.asm, 0.5); self.assertEqual([p.toArray() for p in A.asrc], before)
        self.assertNotEqual([a.pos.toArray() for a in A.anchors], before)
        self.assertEqual(B.asm, 0.5, 'every selected curve with 3 or more anchors is evened')
        bpy.context.window_manager.hairmesh_slide.curve_even = 0.0
        self.assertEqual([a.pos.toArray() for a in A.anchors], before)
        first = A.anchors[0]
        self.assertEqual(bpy.ops.hairmesh.reverse_curves(), {'FINISHED'})
        self.assertIs(A.anchors[len(A.anchors) - 1], first)
        self.assertEqual(self.text()['items'][0]['anchors'][len(A.anchors) - 1], first.id)
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(bpy.ops.hairmesh.reverse_curves(), {'CANCELLED'})
        self.assertEqual(bpy.ops.hairmesh.recalc_handles(), {'CANCELLED'})
        self.assertEqual(bpy.context.window_manager.hairmesh_slide.anchor_smooth, 1.0); self.assertEqual(bpy.context.window_manager.hairmesh_slide.curve_round, 0.0)

    def test_drawing_colours_follow_the_selection(self):
        from hairmesh.core.selection import Selection
        A, B = self.A, self.B
        draw = self.draw
        M = self.root.matrix_world
        g = draw.geometry(self.P, M)
        self.assertEqual(set(g['line_colors']), {draw.CURVE}, 'nothing selected: grey curves')
        self.assertTrue(all(c == draw.CA['normal'] for c in g['anchors']['col']))
        sel = Selection([B.id], [])
        g = draw.geometry(self.P, M, sel)
        na = 2 * (len(A.samples) - 1)
        self.assertEqual(set(g['line_colors'][:na]), {draw.CURVE}); self.assertEqual(set(g['line_colors'][na:]), {draw.CURVE_SEL})
        sel = Selection([], [A.anchors[1].id, A.anchors[3].id])
        g = draw.geometry(self.P, M, sel, snap=B.id)
        self.assertEqual(set(g['line_colors'][na:]), {draw.CURVE_SNAP}, 'the curve an anchor slides along is cyan')
        cols = g['anchors']['col']; sizes = g['anchors']['size']
        self.assertEqual(cols[1], draw.CA['sel']); self.assertEqual(sizes[1], draw.SEL_PX)
        self.assertEqual(draw.SEL_PX, draw.ANCHOR_PX, '3.8e: a selected anchor is as big as the others')
        self.assertEqual(cols[3], draw.CA['last']); self.assertEqual(sizes[3], draw.LAST_PX, 'the last selected anchor: the ring outside a dot as big as the others')
        self.assertEqual(cols[0], draw.CA['normal']); self.assertEqual(sizes[0], draw.ANCHOR_PX * draw.START_SCALE)
        self.assertEqual(draw.overlay_points(('box', 10, 20, 30, 40)), [(10, 20), (30, 20), (30, 40), (10, 40)])
        circ = draw.overlay_points(('circle', 100, 100, 10))
        self.assertEqual(len(circ), 48); self.assertAlmostEqual(circ[0][0], 110.0); self.assertAlmostEqual(circ[12][1], 110.0)
        self.assertIsNone(draw.overlay_points(None))

    def test_panels_poll_on_the_selection(self):
        from hairmesh.blender import ui
        ctx = bpy.context
        self.assertFalse(ui.HAIRMESH_PT_anchors.poll(ctx)); self.assertTrue(ui.HAIRMESH_PT_curve.poll(ctx), 'the drawn curve is selected')
        bpy.ops.hairmesh.select(anchor=self.A.anchors[0].id)
        self.assertTrue(ui.HAIRMESH_PT_anchors.poll(ctx)); self.assertFalse(ui.HAIRMESH_PT_curve.poll(ctx))
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertFalse(ui.HAIRMESH_PT_anchors.poll(ctx)); self.assertFalse(ui.HAIRMESH_PT_curve.poll(ctx))
        for name in ('select', 'move_anchors', 'insert_anchor', 'smooth_drag', 'delete_selected', 'select_circle', 'reverse_curves', 'recalc_handles'):
            self.assertTrue(hasattr(bpy.ops.hairmesh, name), name)

    def test_live_preview_follows_an_edit(self):
        from hairmesh.core import edit
        P = self.P
        A, B = self.A, self.B
        g = edit.addItem(P, 'group', None, {'name': 'Hair'})
        A.parent = g.id; B.parent = g.id; P.roots.remove(A.id); P.roots.remove(B.id); g.children.extend([A.id, B.id]); P.gen += 1
        edit.addItem(P, 'surface', g.id, {'a': A.id, 'b': B.id, 'p': {'segs': 4}})
        self.session.save_project(self.root, P)
        objs = self.live.rebuild(bpy.context, self.root)
        self.assertEqual(list(objs), [g.id])
        v0 = [tuple(v.co) for v in objs[g.id].data.vertices]
        bpy.ops.hairmesh.select(anchor=A.anchors[0].id)
        bpy.ops.hairmesh.move_anchors(delta=(0.0, 0.0, 5.0))
        objs = self.live.rebuild(bpy.context, self.root)
        self.assertNotEqual([tuple(v.co) for v in objs[g.id].data.vertices], v0, 'the live walls follow the moved anchor')


@unittest.skipUnless(bpy, 'needs Blender')
class TestContextMenus(unittest.TestCase):
    """The pie (Shift+RMB), the HairMesh Menu pop-up (D) and its list form (Object > HairMesh) follow the selection:
    anchors -> anchor actions, curves -> curve actions, else the sub-tools and the project (rule of 14/10/2026: every
    action reachable from menus and pies). The Q pie lists the sub-tools."""
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import session, tool
        cls.hairmesh, cls.session, cls.tool = hairmesh, session, tool
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            hairmesh.register()
        except ValueError:
            pass

    @classmethod
    def tearDownClass(cls):
        cls.hairmesh.unregister()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.session.forget()

    @staticmethod
    def ops(items):
        out = []
        for it in items:
            if it[0] == 'box':
                out.extend(TestContextMenus.ops(it[1]))
            elif it[0] == 'block':
                out.extend(TestContextMenus.ops([x for row in it[4] for x in row]))
            elif it[0] == 'op':
                out.append((it[1], it[4]))
            elif it[0] == 'subtool':
                out.append(('hairmesh.set_subtool', {'subtool': it[1], 'activate': True}))
            elif it[0] in ('prop', 'menu', 'toggle'):
                out.append((it[0], it[1]))
        return out

    @staticmethod
    def popup(context):
        """the pop-up's items, flattened"""
        from hairmesh.blender import tool
        return TestContextMenus.ops([it for _t, _i, rows in tool.popup_sections(context) for row in rows for it in row])

    MENU = ('wm.call_panel', {'name': 'HAIRMESH_PT_popup', 'keep_open': True})

    def test_without_a_project(self):
        pie = self.tool.pie_items(bpy.context)
        self.assertEqual(len(pie), 8, 'W, E, S, N, NW, NE, SW, SE')
        ops = self.ops(pie)
        self.assertEqual([o for o in ops if o[0] == 'hairmesh.set_subtool'], [], 'the sub-tools are in the Q pie')
        self.assertIn(('hairmesh.new_project', {}), ops); self.assertIn(self.MENU, ops)
        self.assertNotIn(('menu', 'HAIRMESH_MT_walls'), ops)
        menu = self.ops(self.tool.menu_items(bpy.context))
        self.assertIn(('hairmesh.new_project', {}), menu); self.assertIn(('hairmesh.import_project', {}), menu)
        self.assertNotIn(('prop', 'anchor_smooth'), menu); self.assertNotIn(('menu', 'HAIRMESH_MT_walls'), menu, 'Walls / Lock lives with the curves (U6)')
        # the pop-up (U5): the top row and the four tabs; without a project only Project has buttons
        sections = self.tool.popup_sections(bpy.context)
        self.assertEqual([s[0] for s in sections], ['Sub-tool', 'Selection', 'Modifiers', 'Conversion', 'Viewport', 'Project'])
        self.assertEqual([it[1] for it in sections[0][2][0][:5]], ['SELECT', 'DRAW', 'SCISSORS', 'GUIDES', 'CROWN'], 'the sub-tool buttons, one row (4.2: Guides; 4.3: Crown)')
        self.assertEqual(sections[0][2][0][7], self.tool.OUTLINER_OP_S, 'and the outliner')
        popup = self.popup(bpy.context)
        self.assertIn(('hairmesh.new_project', {}), popup); self.assertIn(('hairmesh.import_project', {}), popup)
        self.assertNotIn(('toggle', 'drag_on_head'), popup)
        self.assertEqual(self.tool.popup_tabs(bpy.context)['SELECTION'], [[('label', 'No HairMesh project', 'INFO')]])
        self.assertEqual(self.tool.popup_tabs(bpy.context)['MODIFIERS'], [[('label', 'No HairMesh project', 'INFO')]])
        self.assertEqual(self.tool.active_tab(bpy.context), 'SELECTION', 'registered by hand: no preferences, the default tab')
        self.assertEqual([o for o in menu if o[0] == 'hairmesh.set_subtool'], [o for o in popup if o[0] == 'hairmesh.set_subtool'], 'the list menu is the pop-up flattened')
        # the Q pie: the three sub-tools, the HairMesh Menu, (4.2) Guides in the NW slot, (4.3) Crown in NE and the Modifiers tab in SW
        q = [o for o in self.ops(self.tool.subtool_pie_items(bpy.context)) if o != ('wm.call_panel', {'name': 'HAIRMESH_PT_outliner', 'keep_open': True}) and o != ('menu', 'HAIRMESH_MT_more_tools')]
        self.assertEqual(q, [('hairmesh.set_subtool', {'subtool': k, 'activate': True}) for k in ('SELECT', 'DRAW', 'SCISSORS')] + [self.MENU, ('hairmesh.set_subtool', {'subtool': 'GUIDES', 'activate': True}), ('hairmesh.set_subtool', {'subtool': 'CROWN', 'activate': True}), ('hairmesh.open_modifiers', {})])
        self.assertEqual(bpy.types.HAIRMESH_MT_subtools.bl_label, 'HairMesh Sub-tool Pie')

    def test_anchors_then_curves(self):
        bpy.ops.hairmesh.new_project(name='menus')
        root = bpy.data.objects['HairMesh menus']
        bpy.ops.hairmesh.draw_curve(points_json=json.dumps([[0, 0, 0], [1, 2, 0], [2, 4, 0]]))
        P = self.session.get_project(root); A = P.curves()[0]
        # the drawn curve is selected: the curve pie and menu
        pie = self.ops(self.tool.pie_items(bpy.context)); menu = self.ops(self.tool.menu_items(bpy.context))
        popup = self.popup(bpy.context)
        for where in (pie, menu, popup):
            self.assertIn(('hairmesh.reverse_curves', {}), where)
            self.assertIn(('hairmesh.delete_selected', {'keep_shape': False}), where)
            for p in ('curve_round', 'curve_fair', 'curve_even'):
                self.assertIn(('prop', p), where)
            self.assertNotIn(('prop', 'anchor_smooth'), where); self.assertNotIn(('hairmesh.select', {'deselect_all': True}), where)   # (4.3, item 1)
        self.assertIn(('menu', 'HAIRMESH_MT_walls'), pie)
        self.assertIn(('hairmesh.set_head', {}), menu); self.assertIn(('hairmesh.export_project', {}), menu); self.assertIn(('prop', 'live_preview'), menu)
        self.assertIn(('toggle', 'live_preview'), popup); self.assertIn(('toggle', 'drag_on_head'), popup); self.assertIn(('hairmesh.live_rebuild', {}), popup)
        self.assertEqual([s[0] for s in self.tool.popup_sections(bpy.context)], ['Sub-tool', 'Selection', 'Modifiers', 'Conversion', 'Viewport', 'Project'])
        tabs = self.tool.popup_tabs(bpy.context)
        self.assertEqual(tabs['SELECTION'][0][0], ('label', A.name, 'CURVE_DATA'), 'the Selection tab: the curve'); self.assertEqual(tabs['SELECTION'][1][0][1], 'Group: (root level)')
        self.assertEqual(self.ops(tabs['OPTIONS'][0])[:2], [('toggle', 'live_preview'), ('hairmesh.live_rebuild', {})])
        self.assertEqual(self.ops(tabs['PROJECT'][-3]), [('hairmesh.save_project', {}), ('hairmesh.export_project', {})])
        self.assertEqual(self.ops(tabs['PROJECT'][-2]), [('hairmesh.import_project', {}), ('hairmesh.new_project', {})])
        self.assertEqual(self.ops(tabs['PROJECT'][-1]), [('hairmesh.migrate_project', {})], '3.7b: Migrate Hair, a row of its own')
        self.assertEqual(len(self.tool.pie_items(bpy.context)), 8)
        # anchors selected: the anchor pie and menu
        bpy.ops.hairmesh.select(anchor=A.anchors[1].id)
        pie = self.ops(self.tool.pie_items(bpy.context)); menu = self.ops(self.tool.menu_items(bpy.context)); popup = self.popup(bpy.context)
        for where in (pie, menu, popup):
            self.assertIn(('hairmesh.delete_selected', {'keep_shape': False}), where); self.assertIn(('hairmesh.delete_selected', {'keep_shape': True}), where)
            self.assertIn(('prop', 'anchor_smooth'), where)
            self.assertNotIn(('hairmesh.reverse_curves', {}), where); self.assertNotIn(('prop', 'curve_round'), where)
            self.assertIn(self.MENU, where) if where is pie else self.assertIn(('wm.call_panel', {'name': 'HAIRMESH_PT_outliner', 'keep_open': True}), where)   # the outliner is one click away (U1)
        self.assertEqual(len(self.tool.pie_items(bpy.context)), 8)
        items = self.tool.pie_items(bpy.context)
        self.assertEqual(items[3][0], 'box', 'the N slot: a box with the label and the Smoothness slider')
        self.assertEqual(items[6], ('menu', 'HAIRMESH_MT_anchor_more', 'Weld / Split / Curves', 'DECORATE_KEYFRAME'), "SW: the rest of the original's anchor pie (3.4)")
        self.assertIn('No walls yet: the live preview shows them as they are made.', self.tool.texts(self.tool.popup_tabs(bpy.context)['OPTIONS']), 'part 1 of the lot ux7: the Viewport tab says why Live Preview is dimmed, in the "i" of its row (G2)')
        labels = [it[1] for it in self.tool.menu_items(bpy.context) if it[0] == 'label' and it[2] != 'BLANK1']   # (BLANK1: the next lines of a wrapped text)
        self.maxDiff = None
        self.assertEqual([l for l in labels if not l.startswith(('Sliders edit', '0 groups · 1 curve', 'Nothing ticked', 'Project defaults', 'No walls yet', 'Drag on Head needs', 'X-ray: no curves', 'in ', 'Recalculate: nothing'))],
                         ['Sub-tool', 'Selection', 'Anchors Selected: 1', 'Shape', 'Weld / Split / Curves', 'Transform', 'Modifiers', 'Conversion', 'No groups', 'Convert makes no mesh · Sliders edit the project defaults', 'Viewport', 'Project', 'menus', 'Not saved to a .hairmesh yet (Save asks where)', 'Visibility'],
                         'the tabs of the pop-up in order (U5), the project name in Project, the blocks of the anchors sheet as titles (part 2 of 16/10/2026; the Transform block without its "Gizmo" label since item 8 of the lot after 4.8)')
        self.assertEqual([it for it in self.tool.menu_items(bpy.context) if it[0] == 'op' and not it[2]], [], 'icon-only buttons of the pop-up get a text in the list')
        self.assertEqual([it[0] for it in self.tool.menu_items(bpy.context) if it[0] in ('subtool', 'toggle')], [], 'buttons and toggles become operators and checkboxes')
        # the sub-tool key operators and the Object menu
        self.assertEqual(bpy.ops.hairmesh.subtool_draw(), {'FINISHED'}); self.assertEqual(bpy.context.scene.hairmesh.subtool, 'DRAW')
        self.assertEqual(bpy.ops.hairmesh.subtool_select(), {'FINISHED'}); self.assertEqual(bpy.context.scene.hairmesh.subtool, 'SELECT')
        self.assertEqual(bpy.types.HAIRMESH_MT_pie.bl_label, 'HairMesh Pie'); self.assertEqual(bpy.types.HAIRMESH_MT_menu.bl_label, 'HairMesh Menu')

    def test_paint_disc_and_drag_on_head_option(self):
        """The additions of 15/10/2026 (docs/features-adicionadas.md): the blue disc of the Ctrl + drag stroke is an
        overlay like the Circle Select's, and Drag on Head is a scene option, off by default."""
        from hairmesh.blender import draw, ops_select
        pts = draw.overlay_points(('paint', 100.0, 50.0, 7.0))
        self.assertEqual(len(pts), 48)
        self.assertAlmostEqual(pts[0][0], 107.0); self.assertAlmostEqual(pts[12][1], 57.0)
        self.assertEqual(ops_select.PAINT_CURSOR_PX, ops_select.PICK_CURVE_PX, 'the disc shows the pick radius')
        self.assertEqual(draw.PAINT_FILL[3], 0.35); self.assertEqual(len(draw.PAINT_LINE), 4)
        hm = bpy.context.scene.hairmesh
        self.assertFalse(hm.drag_on_head, 'off by default: the original drags on the view plane')
        hm.drag_on_head = True
        self.assertTrue(bpy.context.scene.hairmesh.drag_on_head)
