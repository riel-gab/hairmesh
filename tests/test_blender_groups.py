"""Phase 3.6 inside Blender (blender -b): groups as collections. The operators (new / select / rename / delete group,
move the selection to a group, match the project step, detach, tick, the loop step from an object's height), the
Group and Conversion properties of Scene.hairmesh (views on the selected group — part of S.sel since 15/10/2026, U1 — and
on the ticked groups),
the collections kept in step with the model (sync_collections), the live objects per group, the menus (the pie's
Group / Conversion submenus, Move to Group, the pop-up sections, the list), the panels, the per-group visibility of
the curves in the drawing and the picking, undo."""
import json
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(bpy, 'needs Blender')
class TestGroupOperators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import draw, live, ops_groups, select, session, tool, ui
        from hairmesh.core import edit, groups as G
        cls.hairmesh, cls.draw, cls.live, cls.ops_groups, cls.select, cls.session, cls.tool, cls.ui, cls.edit, cls.G = hairmesh, draw, live, ops_groups, select, session, tool, ui, edit, G
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
        bpy.ops.hairmesh.new_project(name='grp')
        self.root = bpy.data.objects['HairMesh grp']
        self.root_col = bpy.data.collections['HairMesh: grp']

    @property
    def hm(self):
        return bpy.context.scene.hairmesh                           # read afresh every time (never kept across an undo)

    @property
    def cv(self):
        return bpy.context.window_manager.hairmesh_conv             # the conversion sliders (WindowManager since 21/10/2026: deferred.py)

    def P(self):
        return self.session.get_project(self.root)

    def curve(self, *pts):
        bpy.ops.hairmesh.draw_curve(points_json=json.dumps([list(p) for p in pts]))
        P = self.P()
        return P.curves()[len(P.curves()) - 1]

    def active_gid(self):
        """the selected group's id (the last selected item when it is a group), else None"""
        return self.ops_groups.selected_group(bpy.context, self.P())

    @staticmethod
    def ops(items):
        out = []
        for it in items:
            if it[0] == 'box':
                out.extend(TestGroupOperators.ops(it[1]))
            elif it[0] == 'block':
                out.extend(TestGroupOperators.ops([x for row in it[4] for x in row]))
            elif it[0] == 'op':
                out.append((it[1], it[4]))
            elif it[0] in ('menu', 'prop', 'toggle'):
                out.append((it[0], it[1]))
        return out

    def popup(self):
        return self.ops([it for _t, _i, rows in self.tool.popup_sections(bpy.context) for it in self.tool.flat_items(rows)])

    def test_add_select_rename_delete(self):
        self.assertIsNone(self.active_gid(), 'a new project: nothing selected')
        self.assertEqual(bpy.ops.hairmesh.add_group(name='Fringe'), {'FINISHED'})
        P = self.P(); g = P.groups()[0]
        self.assertEqual((g.name, g.parent, P.roots), ('Fringe', None, [g.id]))
        self.assertEqual(P.settings.convChecked, [g.id], 'ticked for conversion, as the pie does'); self.assertEqual(set(g.conv), set(self.G.CONV_KEYS), 'its panel filled the conv')
        col = bpy.data.collections['Fringe']
        self.assertEqual(col['hairmesh_id'], g.id); self.assertIn(col.name, self.root_col.children)
        self.assertEqual(self.active_gid(), g.id, 'the new group is selected: new curves go into it')
        self.assertEqual(self.select.load(self.hm).items, [g.id])
        A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0))
        self.assertEqual(A.parent, g.id)
        self.assertEqual(bpy.ops.hairmesh.add_group(name='Sub'), {'FINISHED'})
        P = self.P(); sub = next(x for x in P.groups() if x.name == 'Sub')
        self.assertEqual(sub.parent, g.id, "the selected curve's group (targetGroup)"); self.assertIn('Sub', col.children); self.assertEqual(self.active_gid(), sub.id)
        self.assertEqual(bpy.ops.hairmesh.add_group(name='Top', parent=-2), {'FINISHED'})
        P = self.P(); top = next(x for x in P.groups() if x.name == 'Top')
        self.assertIsNone(top.parent); self.assertIn('Top', self.root_col.children)
        # rename through the property: the model, then the collection
        bpy.ops.hairmesh.select_group(group=g.id)
        self.assertEqual(self.active_gid(), g.id); self.assertEqual(self.hm.group_name, 'Fringe')
        self.hm.group_name = '  Bang '
        P = self.P(); self.assertEqual(P.items[g.id].name, 'Bang'); self.assertIn('Bang', bpy.data.collections); self.assertNotIn('Fringe', bpy.data.collections)
        self.assertEqual(bpy.ops.hairmesh.rename_item(name='Bang 2'), {'FINISHED'}); self.assertEqual(self.P().items[g.id].name, 'Bang 2')   # the one rename (U6): the selected item
        self.assertIn('Bang 2', bpy.data.collections)
        self.assertEqual(bpy.ops.hairmesh.select_group(group=-1), {'FINISHED'}); self.assertIsNone(self.active_gid())
        self.assertEqual(self.select.load(self.hm).items, [], '-1: nothing selected')
        self.assertEqual(bpy.ops.hairmesh.rename_item(name='x'), {'CANCELLED'}, 'nothing selected')
        self.assertFalse(hasattr(bpy.types, 'HAIRMESH_OT_rename_group'), 'rename_group is gone: rename_item does it (U6, 16/10/2026)')
        # the generic click (the outliner's row): hairmesh.select item=; Shift adds, a curve selected last is no group
        self.assertEqual(bpy.ops.hairmesh.select(item=g.id), {'FINISHED'}); self.assertEqual(self.active_gid(), g.id)
        bpy.ops.hairmesh.select(item=A.id, extend=True)
        self.assertEqual(self.select.load(self.hm).items, [g.id, A.id]); self.assertIsNone(self.active_gid(), 'the last selected item is a curve')
        self.assertEqual(self.select.target_group(self.P(), self.select.load(self.hm)), g.id, "but new curves go into the curve's group")
        bpy.ops.hairmesh.select_group(group=sub.id, extend=True)
        self.assertEqual(self.select.load(self.hm).items, [g.id, A.id, sub.id]); self.assertEqual(self.active_gid(), sub.id)
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.select(item=9999)
        self.assertEqual(bpy.data.collections['Bang 2'].name, 'Bang 2', 'the collections stay as a view of the groups')
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.select_group(group=9999)
        # delete the group: the curve and the sub-group go, the collections too, the parent becomes the selection
        bpy.ops.hairmesh.select_group(group=sub.id)
        self.assertEqual(bpy.ops.hairmesh.delete_group(), {'FINISHED'})
        P = self.P(); self.assertNotIn(sub.id, P.items); self.assertNotIn('Sub', bpy.data.collections); self.assertEqual(self.active_gid(), g.id)
        self.assertEqual(self.select.load(self.hm).items, [g.id])
        bpy.ops.hairmesh.delete_group()
        P = self.P(); self.assertNotIn(g.id, P.items); self.assertNotIn(A.id, P.items); self.assertEqual([x.name for x in P.groups()], ['Top'])
        self.assertNotIn('Bang 2', bpy.data.collections); self.assertIsNone(self.active_gid())
        self.assertEqual(P.settings.convChecked, [top.id])

    def test_properties_of_the_active_group(self):
        bpy.ops.hairmesh.add_group(name='Fringe')
        P = self.P(); g = P.groups()[0]
        hm = self.hm
        hm.group_color = (0.25, 0.75, 0.5)
        self.assertEqual(self.P().items[g.id].color, '#40bf80')
        self.assertEqual(tuple(round(c, 3) for c in self.hm.group_color), (0.251, 0.749, 0.502), 'the hex read back')
        self.assertTrue(self.hm.group_visible)
        A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0)); B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0))
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.create_walls()
        bpy.ops.hairmesh.select_group(group=g.id)                   # the wall was selected: the group panel needs the group selected
        self.hm.group_visible = False
        P = self.P(); self.assertFalse(P.items[g.id].visible)
        self.assertFalse(bpy.data.collections['Fringe'].hide_viewport, 'the collection is left enabled: disabling it would make it inactive')
        self.assertEqual(self.active_gid(), g.id, 'still the selected group')
        self.assertTrue(next(iter(self.live.live_objects(self.root).values())).hide_viewport, 'the live walls of a hidden group are hidden')
        self.assertEqual(self.G.shownCurves(P, active_group=g.id)[0], [], 'its curves are neither drawn nor picked')
        self.hm.group_visible = True
        P = self.P(); self.assertTrue(P.items[g.id].visible); self.assertFalse(next(iter(self.live.live_objects(self.root).values())).hide_viewport)
        self.assertEqual([c.id for c in self.G.shownCurves(P, active_group=g.id)[0]], [A.id, B.id])
        self.assertEqual(self.hm.group_guides, 'auto')
        self.hm.group_guides = 'hide'; self.assertEqual(self.P().items[g.id].guides, 'hide')
        self.assertTrue(self.hm.group_loose); self.assertFalse(self.hm.group_container)
        self.hm.group_container = True; self.hm.group_loose = False
        self.assertEqual(self.P().items[g.id].labels, {'container': True, 'loose': False})
        self.assertFalse(self.hm.group_sym_on); self.assertEqual(self.hm.group_sym_axis, 'x')
        self.hm.group_sym_on = True; self.hm.group_sym_axis = 'z'
        self.assertEqual(self.P().items[g.id].sym, {'on': True, 'axis': 'z'})
        self.assertTrue(self.hm.group_ticked)
        self.hm.group_ticked = False; self.assertEqual(self.P().settings.convChecked, [])
        self.hm.group_ticked = True; self.assertEqual(self.P().settings.convChecked, [g.id])
        # the group's own conversion parameters; the step set last is the project's loop step
        self.assertAlmostEqual(self.cv.group_step, 1 / 24, places=6); self.assertAlmostEqual(self.cv.group_dev, 0.3, places=6)
        self.cv.group_dev = 0.45; self.cv.group_step = 0.07; self.cv.group_tip_mode = 'gradual'; self.cv.group_merge_trunks = False; self.cv.group_relax = 0.2
        P = self.P(); c = P.items[g.id].conv
        self.assertEqual((c['dev'], c['step'], c['tipMode'], c['mergeTrunks'], c['relax']), (0.45, 0.07, 'gradual', False, 0.2), 'float32 sliders stored as short decimals')
        self.assertIsNone(P.settings.step, "U4: the group's step is not the project's"); self.assertEqual(self.cv.group_tip_mode, 'gradual')
        # the Conversion panel: over the ticked groups (Fringe), else the project defaults
        self.assertAlmostEqual(self.cv.conv_dev, 0.45, places=6)
        self.cv.conv_straighten = 0.6
        P = self.P(); self.assertEqual(P.items[g.id].conv['straighten'], 0.6); self.assertEqual(P.settings.straighten, 1)
        self.hm.group_ticked = False
        self.assertAlmostEqual(self.cv.conv_dev, 0.3, places=6, msg='nothing ticked: S')
        self.cv.conv_dev = 0.35; self.cv.conv_tip_mode = 'gradual'
        P = self.P(); self.assertEqual((P.settings.dev, P.settings.tipMode, P.items[g.id].conv['dev']), (0.35, 'gradual', 0.45))
        self.assertAlmostEqual(self.hm.settings_xray, 0.2, places=6)
        self.hm.settings_xray = 0.5; self.assertEqual(self.P().settings.xray, 0.5)
        # at the root level the group properties are inert
        bpy.ops.hairmesh.select_group(group=-1)
        self.assertEqual(self.hm.group_name, ''); self.hm.group_name = 'nothing'
        self.assertEqual(self.P().items[g.id].name, 'Fringe')

    def test_move_to_group_and_the_live_objects(self):
        A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0), (6, 120, 0))
        B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0), (46, 120, 0))
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.create_walls()
        P = self.P(); s = P.surfaces()[0]
        self.assertEqual(list(self.live.live_objects(self.root)), [-1], 'the root level has the live object')
        bpy.ops.hairmesh.add_group(name='Fringe')
        P = self.P(); g = P.groups()[0]
        self.assertEqual(bpy.ops.hairmesh.move_to_group(group=g.id), {'CANCELLED'}, 'the new group is selected: nothing else to move into it')
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(bpy.ops.hairmesh.move_to_group(group=g.id), {'CANCELLED'}, 'nothing selected')
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True)
        items = self.ops(self.tool.move_to_group_menu_items(bpy.context))
        self.assertEqual(items, [('hairmesh.parent_to_last', {}), ('hairmesh.unparent', {}), ('hairmesh.move_to_group', {'group': -1}), ('hairmesh.move_to_group', {'group': g.id})], '4.0: P / Shift+P first')
        self.assertIn('●', self.tool.move_to_group_menu_items(bpy.context)[2][2], 'the root level is where the selection is')
        bpy.ops.ed.undo_push(message='before the move')
        self.assertEqual(bpy.ops.hairmesh.move_to_group(group=g.id, with_walls=False), {'FINISHED'})
        P = self.P()
        self.assertEqual((P.items[A.id].parent, P.items[B.id].parent, P.items[s.id].parent), (g.id, g.id, None), "with_walls off: the original's moveItems, the wall stays behind")
        bpy.ops.ed.undo_push(message='moved without the wall'); bpy.ops.ed.undo(); self.root = bpy.data.objects['HairMesh grp']
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.move_to_group(group=g.id), {'FINISHED'})
        P = self.P()
        self.assertEqual((P.items[A.id].parent, P.items[B.id].parent, P.items[s.id].parent), (g.id, g.id, g.id), 'U3: the wall between the two moved curves goes along')
        self.assertEqual(P.roots, [g.id]); self.assertEqual(P.items[g.id].children, [A.id, B.id, s.id])
        objs = self.live.live_objects(self.root)
        self.assertEqual(list(objs), [g.id], 'the live object moved with the wall (one per group)')
        self.assertEqual(objs[g.id].name, 'Fringe live'); self.assertIn(objs[g.id].name, bpy.data.collections['Fringe'].objects)
        bpy.ops.ed.undo_push(message='moved into Fringe')
        self.assertEqual(bpy.ops.hairmesh.move_to_group(group=-1), {'FINISHED'})
        P = self.P(); self.assertEqual(P.roots, [g.id, A.id, B.id, s.id]); self.assertEqual(list(self.live.live_objects(self.root)), [-1])
        bpy.ops.ed.undo_push(message='moved to the root')
        # undo: the model and the live objects come back together
        bpy.ops.ed.undo(); self.root = bpy.data.objects['HairMesh grp']   # (an Object wrapper kept across an undo is dead)
        P = self.P(); self.assertEqual(P.roots, [g.id]); self.assertEqual(list(self.live.live_objects(self.root)), [g.id])
        bpy.ops.ed.redo(); self.root = bpy.data.objects['HairMesh grp']
        P = self.P(); self.assertEqual(P.roots, [g.id, A.id, B.id, s.id]); self.assertEqual(list(self.live.live_objects(self.root)), [-1])
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.move_to_group(group=9999)

    def test_menus_panels_and_lists(self):
        ctx = bpy.context
        pie = self.tool.pie_items(ctx)
        self.assertEqual(len(pie), 8); self.assertEqual(pie[6][1], 'hairmesh.save_project'); self.assertEqual(pie[7], self.tool.OUTLINER_OP)
        self.assertNotIn(('hairmesh.export_project', {}), self.ops(pie), 'export went to the HairMesh Menu')
        sections = self.tool.popup_sections(ctx)
        self.assertEqual([t for t, _i, _r in sections], ['Sub-tool', 'Selection', 'Modifiers', 'Conversion', 'Viewport', 'Project'], 'the top row and the five tabs (U5; Project last: 1a of the manual test of the lot ux5)')
        tabs = self.tool.popup_tabs(ctx)
        self.assertEqual(list(tabs), ['SELECTION', 'MODIFIERS', 'CONVERSION', 'OPTIONS', 'PROJECT'])
        self.assertEqual(tabs['SELECTION'][0], [('label', 'Nothing selected', 'INFO')]); self.assertEqual(self.ops(tabs['SELECTION'][1]), [('hairmesh.add_group', {})])
        popup = self.popup()
        self.assertIn(('hairmesh.add_group', {}), popup); self.assertIn(('prop', 'conv_step'), popup); self.assertIn(('hairmesh.step_from_object', {}), popup)
        self.assertIn(('prop', 'settings_xray'), popup)
        self.assertNotIn('HAIRMESH_MT_select_group', [it[1] for it in popup], 'the list of groups is the outliner (U6)'); self.assertFalse(hasattr(bpy.types, 'HAIRMESH_MT_select_group'))
        self.assertEqual(popup.count(('hairmesh.convert', {})), 1, 'Convert once: the Conversion tab'); self.assertEqual(popup.count(('hairmesh.set_head', {})), 1)
        self.assertEqual([it for it in sections[0][2][0]], [*self.tool._subtool_buttons(), self.tool.OUTLINER_OP_S], 'the top row: the sub-tools (the active one named, 4.4) and the outliner')
        menu = self.ops(self.tool.menu_items(ctx))
        self.assertIn(('hairmesh.add_group', {}), menu); self.assertIn(('prop', 'conv_dev'), menu); self.assertIn(('hairmesh.export_project', {}), menu)
        self.assertEqual([it[2] for it in self.tool.menu_items(ctx) if it[0] == 'op' and it[1] == 'hairmesh.live_rebuild'], ['Rebuild Live Preview'], 'icon-only buttons get their long name in the list')
        self.assertEqual(self.tool.conversion_menu_items(ctx)[0], ('label', 'No groups', 'INFO'))
        self.assertFalse(self.ui.HAIRMESH_PT_group.poll(ctx), 'no group selected'); self.assertTrue(self.ui.HAIRMESH_PT_conversion.poll(ctx)); self.assertTrue(self.ui.HAIRMESH_PT_selection.poll(ctx)); self.assertTrue(self.ui.HAIRMESH_PT_options.poll(ctx))
        self.assertEqual(self.ops(self.tool.group_menu_items(ctx))[-1], ('hairmesh.add_group', {}), 'Group >: New Group without a selected group')
        bpy.ops.hairmesh.add_group(name='Fringe')
        P = self.P(); g = P.groups()[0]
        self.assertTrue(self.ui.HAIRMESH_PT_group.poll(ctx), 'the new group is selected')
        rows, title = self.tool.group_rows(ctx, P)
        self.assertEqual(title, 'Fringe')
        flat = self.ops(self.tool.flat_items(rows))
        for key in ('group_name', 'group_color', 'group_guides', 'group_step', 'group_sym_axis'):
            self.assertIn(('prop', key), flat)
        for key in ('group_visible', 'group_container', 'group_loose', 'group_ticked', 'group_sym_on', 'group_merge_trunks'):
            self.assertIn(('toggle', key), flat)
        for idname in ('hairmesh.match_project_step', 'hairmesh.detach_group', 'hairmesh.add_group', 'hairmesh.rename_item', 'hairmesh.delete_group'):
            self.assertIn((idname, {}), flat)
        self.assertIn(('menu', 'HAIRMESH_MT_move_to_group'), flat, 'a group can be moved too')
        self.assertTrue(next(it for it in self.tool.flat_items(rows) if it[0] == 'op' and it[1] == 'hairmesh.match_project_step')[2].startswith('Match Project: '), 'U4: the project step beside the group slider (on the Match button since the lot ux7)')
        self.assertEqual([it[2] for row in rows for it in row if it[0] == 'block'], ['Display', 'Conversion — This Group Only', 'Symmetry'], 'the blocks of the group sheet, in order (part 2 of the lot of 16/10/2026; Detach left Connections for the group row: part 1 of the lot ux7)')
        self.assertEqual(self.tool.flat_items(self.tool.popup_tabs(ctx)['SELECTION'][:1])[0], ('prop', 'group_name', '', {'icon': 'OUTLINER_COLLECTION'}), 'the Selection tab shows the selected group (its name field)')
        gp = self.tool.pie_items(ctx)
        self.assertEqual(self.ops(gp)[:3], [('hairmesh.add_group', {}), ('hairmesh.rename_item', {}), ('hairmesh.delete_group', {})], 'the group pie (U1)')
        self.assertEqual(gp[6], self.tool.CONV_MENU); self.assertEqual(gp[7], ('empty',), 'SE: nothing to move in an empty group (G3)')
        self.assertEqual(self.ops(self.tool.group_menu_items(ctx))[:3], [('hairmesh.add_group', {}), ('hairmesh.rename_item', {}), ('hairmesh.delete_group', {})], 'Group >: the actions on the selected group')
        self.assertEqual(gp[1][2], 'Rename Group...', 'the pie says what it renames (ACT)')
        self.assertNotIn('deselect', self.tool.ACT, '4.3, item 1: no Deselect All button (a click in the empty space, Alt+A)')
        conv = self.ops(self.tool.conversion_menu_items(ctx))
        self.assertEqual(conv[:5], [('hairmesh.tick_top', {}), ('hairmesh.tick_all', {'on': True}), ('hairmesh.tick_all', {'on': False}), ('hairmesh.tick_group', {'group': g.id, 'toggle': True}),
                                    ('hairmesh.tick_group', {'only': True, 'toggle': False, 'group': g.id})], 'Tick Top / Tick Every / Uncheck All (U7 C), the group and Only This (1c of 21/10/2026)')
        self.assertEqual(self.tool.conversion_menu_items(ctx)[3][3], 'CHECKBOX_HLT', 'ticked')
        self.assertIn('Fringe', ' '.join(self.tool.whys(self.tool.conversion_rows(ctx, self.session.get_project(self.root)))), 'the sliders edit Fringe (the tooltip of the line)')
        self.assertEqual(bpy.ops.hairmesh.tick_group(group=g.id, toggle=True), {'FINISHED'})
        self.assertEqual(self.P().settings.convChecked, []); self.assertEqual(self.tool.conversion_menu_items(ctx)[3][3], 'CHECKBOX_DEHLT')
        bpy.ops.hairmesh.tick_group(group=g.id, toggle=False, on=True); self.assertEqual(self.P().settings.convChecked, [g.id])
        self.assertIn('step', self.tool.conversion_menu_items(ctx)[3][2], "U4: the group's own step in the Conversion list")
        self.assertEqual(self.tool.group_menu_items(ctx)[0], ('label', 'Fringe', 'OUTLINER_COLLECTION'))
        # with curves selected: Move to Group in the pie's box and in the pop-up; the curves hidden by Auto (U2)
        A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0)); B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0))
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.create_walls()
        bpy.ops.hairmesh.select(curve=A.id)
        self.assertIn(('menu', 'HAIRMESH_MT_move_to_group'), self.ops(self.tool.pie_items(ctx))); self.assertIn(('menu', 'HAIRMESH_MT_move_to_group'), self.popup())
        self.assertEqual(self.tool.hidden_rows(self.P(), self.select.load(self.hm)), [], 'A selected: Fringe is active, nothing hidden')
        bpy.ops.hairmesh.select(deselect_all=True)
        rows = self.tool.hidden_rows(self.P(), self.select.load(self.hm))
        self.assertEqual(rows[0][0][1], '2 curves hidden by Auto (select the group in the Outliner, O, to show them):'); self.assertEqual(self.ops(self.tool.flat_items([rows[1]])), [])   # (4.3, item 1: named, no selection button)
        self.assertEqual([it[1] for it in self.tool.flat_items([rows[1]]) if it[0] == 'label' and it[1]], [f'{g.name} (2)'])
        self.assertEqual(self.tool.popup_tabs(ctx)['SELECTION'][2:], rows, 'the Selection tab says so')
        for name in ('HAIRMESH_MT_group', 'HAIRMESH_MT_conversion', 'HAIRMESH_MT_move_to_group'):
            self.assertTrue(hasattr(bpy.types, name), name)
        # U2: the preference for new groups (given explicitly here: the add-on is registered by hand, no preferences)
        self.assertEqual(self.ops_groups.new_group_guides(ctx), 'auto')
        bpy.ops.hairmesh.add_group(name='Shown', parent=-2, guides='show')
        self.assertEqual(next(x for x in self.P().groups() if x.name == 'Shown').guides, 'show')

    def test_step_from_object_match_and_detach(self):
        bpy.ops.hairmesh.add_group(name='Fringe')
        P = self.P(); g = P.groups()[0]
        bpy.ops.mesh.primitive_cube_add(size=1.0)                   # 1 unit tall
        cube = bpy.context.active_object; cube.scale = (1.0, 1.0, 2.5)
        self.assertEqual(bpy.ops.hairmesh.step_from_object(), {'FINISHED'})
        P = self.P()
        self.assertAlmostEqual(P.settings.step, 2.5, places=6); self.assertAlmostEqual(P.items[g.id].conv['step'], 2.5, places=6, msg='Fringe is ticked')
        self.assertAlmostEqual(self.ops_groups.object_height(self.root, cube), 2.5, places=6)
        self.root.scale = (2.0, 2.0, 2.0); bpy.context.view_layer.update()   # the model's units: the root's local space (matrix_world after the update)
        self.assertAlmostEqual(self.ops_groups.object_height(self.root, cube), 1.25, places=6)
        self.root.scale = (1.0, 1.0, 1.0); bpy.context.view_layer.update()
        bpy.context.view_layer.objects.active = self.root
        self.assertFalse(bpy.ops.hairmesh.step_from_object.poll(), 'the root is not an object to measure')
        # match the project step
        bpy.ops.hairmesh.select_group(group=g.id)
        self.cv.group_step = 0.5
        self.assertAlmostEqual(self.P().settings.step, 2.5, places=6, msg="U4: the group's slider edits the group alone; the project step stays")
        self.assertEqual(self.P().items[g.id].conv['step'], 0.5)
        self.assertIn('≠', self.ops_groups.tick_items(self.P())[0][2], 'the Conversion list marks the group whose step differs')
        P = self.P(); P.settings.step = 3.0; self.session.save_project(self.root, P)
        self.assertTrue(self.G.stepOff(self.P(), self.P().items[g.id]))
        self.assertEqual(bpy.ops.hairmesh.match_project_step(), {'FINISHED'})
        self.assertEqual(self.P().items[g.id].conv['step'], 3.0)
        # detach: a curve of Fringe welded to a root curve
        A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0))          # in Fringe (selected)
        self.assertEqual(bpy.ops.hairmesh.detach_group(), {'CANCELLED'}, "isn't welded to other groups")
        bpy.ops.hairmesh.select_group(group=-1)
        B = self.curve((30, 0, 0), (32, 40, 0))                     # at the root
        P = self.P()
        from hairmesh.core import weld as W, curves as CM
        CM.rebuild_dirty(P)
        W.weld(P, P.items[B.id].anchors[1], P.items[A.id].anchors[1])   # B's tip onto A's middle anchor
        self.session.save_project(self.root, P)
        shared = self.P().items[A.id].anchors[1]
        self.assertIn(shared, self.P().items[B.id].anchors)
        bpy.ops.hairmesh.select_group(group=g.id)
        self.assertEqual(bpy.ops.hairmesh.detach_group(), {'FINISHED'})
        P = self.P()
        self.assertNotIn(P.items[A.id].anchors[1], P.items[B.id].anchors, 'A has its own anchor there now')
        self.assertEqual(P.items[A.id].anchors[1].pos.toArray(), P.items[B.id].anchors[1].pos.toArray())

    def test_curves_shown_by_the_groups_mode(self):
        from hairmesh.core.selection import Selection
        bpy.ops.hairmesh.add_group(name='Fringe')
        P = self.P(); g = P.groups()[0]
        A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0), (6, 120, 0))
        B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0), (46, 120, 0))
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.create_walls()
        bpy.ops.hairmesh.select_group(group=-1)
        R = self.curve((80, 0, 0), (82, 40, 0))                     # a root curve: always shown
        F = None
        bpy.ops.hairmesh.select_group(group=g.id)
        F = self.curve((100, 0, 0), (102, 40, 0))                   # a free curve of Fringe: shown by the label
        bpy.ops.hairmesh.select(deselect_all=True)
        bpy.ops.hairmesh.select_group(group=-1)
        P = self.P()
        shown, act, loose = self.G.shownCurves(P, active_group=None)
        self.assertEqual([c.id for c in shown], [R.id, F.id], "auto: the lock's curves hide while Fringe is not worked on; the free curve stays")
        geo = self.draw.geometry(P, self.root.matrix_world, Selection(), shown=frozenset(c.id for c in shown))
        self.assertEqual(len(geo['anchors']['pos']), len(R.anchors) + len(F.anchors), 'the hidden curves take their anchors with them')
        self.assertEqual(len(self.edit.anchorList(P, shown={c.id for c in shown})), len(R.anchors) + len(F.anchors))
        shown, _a, _l = self.G.shownCurves(P, active_group=g.id)
        self.assertEqual([c.id for c in shown], [A.id, B.id, R.id, F.id], 'Fringe active: all of its curves')
        shown, _a, _l = self.G.shownCurves(P, sel_items=[P.surfaces()[0].id])
        self.assertEqual([c.id for c in shown], [A.id, B.id, R.id, F.id], 'the wall selected: its group is active')
        shown, _a, _l = self.G.shownCurves(P, sel_items=[g.id])
        self.assertEqual([c.id for c in shown], [A.id, B.id, R.id, F.id], 'the group selected (U1: in S.sel): active too')
        self.hm.group_guides = 'show'                               # nothing selected: inert... select Fringe first
        bpy.ops.hairmesh.select_group(group=g.id); self.hm.group_guides = 'hide'; bpy.ops.hairmesh.select_group(group=-1)
        P = self.P(); shown, _a, _l = self.G.shownCurves(P, active_group=g.id)
        self.assertEqual([c.id for c in shown], [R.id], 'hide: never, even while active')
        # the draw handler's batches use the selection (no gpu here: the key only)
        self.assertEqual(self.draw._cache, {}, 'nothing drawn in background mode')


if __name__ == '__main__':
    unittest.main()
