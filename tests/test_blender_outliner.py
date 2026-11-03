"""The HairMesh Outliner inside Blender (blender -b): the row click (hairmesh.select_item: Shift, Ctrl on a group),
expand / collapse and reveal (WindowManager state), the eye of a group, of a curve and of a wall (the live mesh and
the picking drop a hidden wall: 1a of 16/10/2026), the curves mode button, the guides' power, cut / paste and
move-here with the walls going along (U3), rename of a curve, a wall and a group (the one rename: the dialog
operator, the bar's name field, F2 in the 3D View Generic keymap), the X of the bar on a group, the color rows of
the scene kept in step with the groups, the keys O and F2, the panels, undo."""
import json
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(bpy, 'needs Blender')
class TestOutliner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import draw, keymaps, live, ops_outliner, pick, select, session, tool
        from hairmesh.core import edit, groups as G, outliner as OL
        cls.hairmesh, cls.draw, cls.keymaps, cls.live, cls.ops_outliner, cls.pick, cls.select, cls.session, cls.tool = hairmesh, draw, keymaps, live, ops_outliner, pick, select, session, tool
        cls.edit, cls.G, cls.OL = edit, G, OL
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
        wm = bpy.context.window_manager
        wm.hairmesh_outliner_open = '{}'; wm.hairmesh_clipboard = '[]'; wm.hairmesh_outliner_filter = ''
        bpy.ops.hairmesh.new_project(name='ol')
        self.root = bpy.data.objects['HairMesh ol']
        bpy.ops.hairmesh.add_group(name='Fringe')
        P = self.P(); self.g = P.groups()[0]
        self.A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0)); self.B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0))
        bpy.ops.hairmesh.select(curve=self.A.id); bpy.ops.hairmesh.select(curve=self.B.id, extend=True); bpy.ops.hairmesh.create_walls()
        self.s = self.P().surfaces()[0]
        bpy.ops.hairmesh.select_group(group=-1)
        self.R = self.curve((80, 0, 0), (82, 40, 0))                # at the root
        bpy.ops.hairmesh.select(deselect_all=True)

    @property
    def hm(self):
        return bpy.context.scene.hairmesh

    def P(self):
        return self.session.get_project(self.root)

    def sel(self):
        return self.select.load(self.hm)

    def curve(self, *pts):
        bpy.ops.hairmesh.draw_curve(points_json=json.dumps([list(p) for p in pts]))
        P = self.P()
        return P.curves()[len(P.curves()) - 1]

    def rows(self):
        return self.ops_outliner.rows_of(bpy.context, self.P())

    def live_faces(self):
        ob = next(iter(self.live.live_objects(self.root).values()))
        return len(ob.data.polygons)

    def test_rows_select_and_highlights(self):
        rows, info = self.rows()
        self.assertEqual([r.kind for r in rows], ['root', 'item', 'pair', 'pair', 'item'], 'the project row first (1c), no "(root level)" row at the bottom')
        self.assertEqual((rows[0].name, rows[0].depth, rows[1].name, rows[1].depth, rows[2].left.name, rows[2].right.name, rows[3].left.name, rows[4].name),
                         ('ol', 0, 'Fringe', 1, self.A.name, self.s.name, self.B.name, self.R.name))
        self.assertTrue(rows[0].selected, 'nothing selected: the project row is the pressed one (the root level)')
        self.assertEqual(info['mode_hidden'], 2, 'A and B hidden by Auto with nothing selected (U2)')
        self.assertTrue(rows[2].left.mode_hidden)
        self.assertEqual(bpy.ops.hairmesh.select_item(item=self.A.id), {'FINISHED'})
        self.assertEqual(self.sel().items, [self.A.id])
        rows, info = self.rows()
        self.assertTrue(rows[2].left.selected); self.assertTrue(rows[2].right.related); self.assertTrue(rows[1].holds); self.assertEqual(info['mode_hidden'], 0)
        self.assertEqual((rows[2].left.highlight, rows[2].right.highlight, rows[1].highlight, rows[0].selected), ('selected', 'related', 'holds', False))
        bpy.ops.hairmesh.select_item(item=self.s.id, extend=True)
        self.assertEqual(self.sel().items, [self.A.id, self.s.id])
        bpy.ops.hairmesh.select_item(item=self.A.id, extend=True)
        self.assertEqual(self.sel().items, [self.s.id], 'Shift on a selected item takes it out')
        self.assertEqual(bpy.ops.hairmesh.select_item(item=self.g.id), {'FINISHED'})
        self.assertEqual(self.sel().items, [self.g.id]); self.assertIs(self.select.selected_group(self.P(), self.sel()), self.P().items[self.g.id])
        self.assertTrue(self.rows()[0][1].selected)
        self.assertEqual(bpy.ops.hairmesh.select_item(item=self.g.id, contents=True), {'FINISHED'})
        self.assertEqual(sorted(self.sel().items), sorted([self.A.id, self.B.id, self.s.id]), 'Ctrl on a group: everything inside (selectContents 8463)')
        self.assertEqual(bpy.ops.hairmesh.select_item(item=self.B.id, rename=True), {'FINISHED'}, 'a double click selects (the dialog needs a window)')
        self.assertEqual(self.sel().items, [self.B.id])
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.select_item(item=9999)
        # the project row's click: the root level (nothing selected)
        self.assertEqual(bpy.ops.hairmesh.select_group(group=-1), {'FINISHED'}); self.assertEqual(self.sel().items, [])

    def test_expand_reveal_filter(self):
        wm = bpy.context.window_manager
        self.assertEqual(bpy.ops.hairmesh.outliner_expand(group=self.g.id), {'FINISHED'})
        self.assertEqual(json.loads(wm.hairmesh_outliner_open), {str(self.g.id): False}, "the arrow: a switch of this session over the file's expanded")
        rows, _info = self.rows()
        self.assertEqual([r.kind for r in rows], ['root', 'item', 'item'], 'Fringe closed: nothing inside'); self.assertFalse(rows[1].expanded)
        bpy.ops.hairmesh.select_item(item=self.A.id)
        self.assertEqual(bpy.ops.hairmesh.outliner_reveal(), {'FINISHED'})
        self.assertEqual(json.loads(wm.hairmesh_outliner_open), {str(self.g.id): True}, 'the group above the selected curve opened')
        bpy.ops.hairmesh.outliner_expand(group=self.g.id)
        wm.hairmesh_outliner_filter = self.B.name
        rows, info = self.rows()
        self.assertEqual([r.kind for r in rows], ['root', 'item', 'item']); self.assertEqual(rows[2].name, self.B.name); self.assertEqual(info['matches'], 1)
        self.assertTrue(rows[1].expanded, 'the filter opens the group holding the match')
        wm.hairmesh_outliner_filter = 'wall'
        rows, info = self.rows()
        self.assertEqual(([r.kind for r in rows], info['matches'], rows[2].name), (['root', 'item', 'item'], 1, self.s.name), 'the type word finds the wall')
        wm.hairmesh_outliner_filter = ''
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(bpy.ops.hairmesh.outliner_reveal(), {'CANCELLED'}, 'nothing selected')
        self.assertEqual(bpy.ops.hairmesh.outliner_expand(group=self.g.id), {'FINISHED'}); self.assertEqual(json.loads(wm.hairmesh_outliner_open), {str(self.g.id): True})
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.outliner_expand(group=9999)
        self.assertEqual(self.ops_outliner.project_name(self.root), 'ol')
        self.assertFalse(self.ops_outliner.prefs.open_on_select(bpy.context), 'registered by hand: the preference is off')

    def test_eyes_of_group_curve_and_wall(self):
        self.assertEqual(bpy.ops.hairmesh.toggle_visible(item=self.g.id), {'FINISHED'})
        P = self.P(); self.assertFalse(P.items[self.g.id].visible)
        self.assertTrue(next(iter(self.live.live_objects(self.root).values())).hide_viewport, 'the live walls of a hidden group are hidden')
        rows, _info = self.rows(); self.assertTrue(rows[1].hidden); self.assertTrue(rows[2].left.hidden)
        bpy.ops.hairmesh.toggle_visible(item=self.g.id); self.assertTrue(self.P().items[self.g.id].visible)
        self.assertEqual(bpy.ops.hairmesh.toggle_visible(item=-1), {'CANCELLED'}, 'no head')
        # 1a: the eye of the wall row hides that wall alone: out of the live mesh, out of the picking, not outlined
        n = self.live_faces(); self.assertGreater(n, 0)
        bpy.ops.ed.undo_push(message='before the wall eye')
        self.assertEqual(bpy.ops.hairmesh.toggle_visible(item=self.s.id), {'FINISHED'})
        P = self.P(); self.assertFalse(P.items[self.s.id].visible); self.assertTrue(P.items[self.g.id].visible)
        self.assertEqual(self.live_faces(), 0, 'the hidden wall left the live mesh (the group object stays, shown)')
        self.assertFalse(next(iter(self.live.live_objects(self.root).values())).hide_viewport)
        rows, _info = self.rows(); self.assertTrue(rows[2].right.hidden); self.assertFalse(rows[2].left.hidden, 'the curve beside it keeps its eye on')
        from hairmesh.core.raycast import Ray
        from hairmesh.core.vec3 import V3
        meshes = self.pick.wall_meshes(self.root, P)
        self.assertIn(self.s.id, meshes, 'the grid is still computed')
        ray = Ray(V3(22.0, 40.0, 50.0), V3(0.0, 0.0, -1.0))         # the model's space (Y up): straight at the wall
        self.assertIsNone(self.pick.pick_wall(P, ray, meshes, head=False), 'a hidden wall is not picked (pickables 5661)')
        bpy.ops.ed.undo_push(message='wall hidden')
        bpy.ops.ed.undo(); self.root = bpy.data.objects['HairMesh ol']
        self.assertTrue(self.P().items[self.s.id].visible); self.assertEqual(self.live_faces(), n, 'undo: the wall is back in the mesh')
        bpy.ops.ed.redo(); self.root = bpy.data.objects['HairMesh ol']
        self.assertFalse(self.P().items[self.s.id].visible); self.assertEqual(self.live_faces(), 0)
        bpy.ops.hairmesh.toggle_visible(item=self.s.id); self.assertEqual(self.live_faces(), n, 'the eye again: shown')
        self.assertIsNotNone(self.pick.pick_wall(self.P(), ray, self.pick.wall_meshes(self.root, self.P()), head=False), 'picked again')
        # the eye of the curve in the same row: the curve goes from the drawing, the wall stays (as the original)
        P = self.P(); before = len(self.draw.geometry(P, self.root.matrix_world)['lines'])
        bpy.ops.hairmesh.toggle_visible(item=self.A.id)
        P = self.P(); self.assertFalse(P.items[self.A.id].visible)
        rows, _info = self.rows(); self.assertTrue(rows[2].left.hidden); self.assertFalse(rows[2].right.hidden)
        after = len(self.draw.geometry(P, self.root.matrix_world)['lines'])
        self.assertLess(after, before, 'A is not drawn'); self.assertGreater(after, 0, 'B and R are')
        self.assertEqual(self.live_faces(), n, 'the wall between stays')
        bpy.ops.hairmesh.toggle_visible(item=self.A.id); self.assertTrue(self.P().items[self.A.id].visible)
        self.assertEqual(len(self.draw.geometry(self.P(), self.root.matrix_world)['lines']), before)

    def test_mode_power_rename_and_keys(self):
        self.assertEqual(bpy.ops.hairmesh.cycle_guides_mode(group=self.g.id), {'FINISHED'}); self.assertEqual(self.P().items[self.g.id].guides, 'show')
        self.assertEqual(self.rows()[1]['mode_hidden'], 0, 'Show: nothing hidden by the mode')
        bpy.ops.hairmesh.cycle_guides_mode(group=self.g.id); self.assertEqual(self.P().items[self.g.id].guides, 'hide')
        bpy.ops.hairmesh.cycle_guides_mode(group=self.g.id); self.assertEqual(self.P().items[self.g.id].guides, 'auto')
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.cycle_guides_mode(group=self.A.id)
        # the guides' power (phase 4 rows): a guide inside Fringe, the ⏻ of the guide and of the group
        P = self.P()
        guide = self.edit.addItem(P, 'guide', self.g.id, {'name': 'Guide 1', 'kind': 'line'})
        self.session.save_project(self.root, P)
        rows, info = self.rows()
        self.assertTrue(info['guides']); self.assertTrue(rows[1].group['guides_power'])
        grow = next(r for r in rows if r.kind == 'item' and r.id == guide.id); self.assertEqual((grow.power, grow.icon), (True, self.OL.ICONS['guide']))
        self.assertEqual(bpy.ops.hairmesh.toggle_guide_power(item=guide.id), {'FINISHED'}); self.assertTrue(self.P().items[guide.id].off)
        self.assertEqual(bpy.ops.hairmesh.toggle_guide_power(item=self.g.id), {'FINISHED'}); self.assertTrue(self.P().items[self.g.id].guidesOff)
        bpy.ops.hairmesh.toggle_guide_power(item=self.g.id); self.assertFalse(self.P().items[self.g.id].guidesOff)
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.toggle_guide_power(item=self.A.id)
        # rename: an item by id, else the last selected one — a curve, a wall, a group (the one rename, U6)
        self.assertEqual(bpy.ops.hairmesh.rename_item(item=self.A.id, name='  Front '), {'FINISHED'}); self.assertEqual(self.P().items[self.A.id].name, 'Front')
        bpy.ops.hairmesh.select_item(item=self.s.id)
        self.assertEqual(bpy.ops.hairmesh.rename_item(name='Wall A'), {'FINISHED'}); self.assertEqual(self.P().items[self.s.id].name, 'Wall A')
        bpy.ops.hairmesh.select_item(item=self.g.id)
        self.assertEqual(bpy.ops.hairmesh.rename_item(name='Bang'), {'FINISHED'}); self.assertEqual(self.P().items[self.g.id].name, 'Bang'); self.assertIn('Bang', bpy.data.collections)
        self.assertEqual(next(iter(self.live.live_objects(self.root).values())).name, 'Bang live')
        # the bar's name field (1d): a view on the last selected item
        self.assertEqual(self.hm.item_name, 'Bang')
        self.hm.item_name = ' Bang 2 '
        self.assertEqual(self.P().items[self.g.id].name, 'Bang 2'); self.assertIn('Bang 2', bpy.data.collections)
        bpy.ops.hairmesh.select_item(item=self.B.id)
        self.assertEqual(self.hm.item_name, self.B.name)
        self.hm.item_name = 'Back'; self.assertEqual(self.P().items[self.B.id].name, 'Back')
        self.hm.item_name = ''; self.assertEqual(self.P().items[self.B.id].name, 'Back', 'empty keeps the name (8278)')
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(self.hm.item_name, ''); self.hm.item_name = 'x'
        self.assertEqual(bpy.ops.hairmesh.rename_item(name='x'), {'CANCELLED'})
        keys = {(idname, ev['type']) for idname, ev, _p in self.keymaps.TOOL_ITEMS}
        self.assertIn(('wm.call_panel', 'O'), keys); self.assertNotIn(('hairmesh.rename_item', 'F2'), keys)
        self.assertEqual([(idname, ev['type'], p) for idname, ev, p in self.keymaps.GENERIC_ITEMS], [('hairmesh.rename_item', 'F2', {'tool_only': True})], 'F2 in the 3D View Generic keymap: the viewport and the sidebar')
        self.assertEqual([p for idname, ev, p in self.keymaps.TOOL_ITEMS if ev['type'] == 'O'], [{'name': 'HAIRMESH_PT_outliner', 'keep_open': True}])

    def test_cut_paste_move_here_and_undo(self):
        wm = bpy.context.window_manager
        bpy.ops.hairmesh.add_group(name='Side', parent=-2)
        P = self.P(); h = next(x for x in P.groups() if x.name == 'Side')
        self.assertEqual(bpy.ops.hairmesh.outliner_paste(group=h.id), {'CANCELLED'}, 'nothing cut')
        bpy.ops.hairmesh.select_item(item=self.R.id)
        self.assertEqual(bpy.ops.hairmesh.outliner_cut(), {'FINISHED'}); self.assertEqual(json.loads(wm.hairmesh_clipboard), [self.R.id])
        bpy.ops.ed.undo_push(message='cut')
        self.assertEqual(bpy.ops.hairmesh.outliner_paste(group=h.id), {'FINISHED'})
        P = self.P(); self.assertEqual(P.items[self.R.id].parent, h.id); self.assertEqual(json.loads(wm.hairmesh_clipboard), [], 'the clipboard is spent')
        bpy.ops.ed.undo_push(message='paste')
        self.assertEqual(bpy.ops.hairmesh.outliner_cut(item=self.A.id), {'FINISHED'})
        self.assertEqual(bpy.ops.hairmesh.outliner_paste(group=-1), {'FINISHED'})
        P = self.P(); self.assertIsNone(P.items[self.A.id].parent, 'pasted at the root level'); self.assertEqual(P.items[self.s.id].parent, self.g.id, 'B stays in Fringe: the wall stays (U3)')
        bpy.ops.ed.undo_push(message='paste 2')
        bpy.ops.hairmesh.outliner_cut(item=self.B.id)
        self.assertEqual(bpy.ops.hairmesh.outliner_paste(group=-1), {'FINISHED'})
        P = self.P(); self.assertIsNone(P.items[self.B.id].parent); self.assertIsNone(P.items[self.s.id].parent, 'U3: A is already at the root, so the wall A-B goes to the root with B')
        self.assertEqual(sorted(self.live.live_objects(self.root)), [-1], 'the live object went with the wall')
        bpy.ops.ed.undo_push(message='paste 3')
        bpy.ops.hairmesh.select_item(item=self.A.id); bpy.ops.hairmesh.select_item(item=self.B.id, extend=True); bpy.ops.hairmesh.outliner_cut()
        self.assertEqual(bpy.ops.hairmesh.outliner_paste(group=h.id, with_walls=False), {'FINISHED'})
        P = self.P(); self.assertEqual((P.items[self.A.id].parent, P.items[self.B.id].parent, P.items[self.s.id].parent), (h.id, h.id, None), "with_walls off: the original's rule, the wall stays")
        bpy.ops.ed.undo_push(message='paste 4')
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.outliner_cut(item=self.g.id); bpy.ops.hairmesh.outliner_paste(group=9999)
        # move-here is the Move to Group of the selection: the wall into Fringe takes its two curves, the live object follows
        bpy.ops.hairmesh.select_item(item=self.s.id)
        self.assertEqual(bpy.ops.hairmesh.move_to_group(group=self.g.id), {'FINISHED'})
        P = self.P(); self.assertEqual((P.items[self.s.id].parent, P.items[self.A.id].parent, P.items[self.B.id].parent), (self.g.id, self.g.id, self.g.id), 'a moved wall takes its curves (U3)')
        self.assertEqual(sorted(self.live.live_objects(self.root)), [self.g.id])
        bpy.ops.ed.undo_push(message='move here')
        bpy.ops.ed.undo(); self.root = bpy.data.objects['HairMesh ol']
        P = self.P(); self.assertEqual((P.items[self.s.id].parent, P.items[self.A.id].parent), (None, h.id), 'undo: the wall back at the root, A back in Side'); self.assertEqual(sorted(self.live.live_objects(self.root)), [-1])
        bpy.ops.ed.undo(); self.root = bpy.data.objects['HairMesh ol']
        self.assertEqual(self.P().items[self.A.id].parent, None, 'undo: A back at the root')

    def test_delete_group_from_the_bar(self):
        """the bar's X is hairmesh.delete_selected: a selected group goes with everything inside (deleteSelected 8106), its collection and live object too"""
        bpy.ops.hairmesh.select_item(item=self.g.id)
        self.assertIn('Fringe', bpy.data.collections); self.assertEqual(sorted(self.live.live_objects(self.root)), [self.g.id])
        bpy.ops.ed.undo_push(message='before the delete')
        self.assertEqual(bpy.ops.hairmesh.delete_selected(), {'FINISHED'})
        P = self.P()
        self.assertNotIn(self.g.id, P.items); self.assertNotIn(self.A.id, P.items); self.assertNotIn(self.s.id, P.items); self.assertIn(self.R.id, P.items)
        self.assertNotIn('Fringe', bpy.data.collections, 'the collection of the gone group went'); self.assertEqual(self.live.live_objects(self.root), {})
        self.assertEqual(self.sel().items, []); self.assertEqual([e.gid for e in self.hm.rows], [])
        bpy.ops.ed.undo_push(message='group deleted'); bpy.ops.ed.undo(); self.root = bpy.data.objects['HairMesh ol']
        self.assertIn(self.g.id, self.P().items, 'undo: the group is back'); self.assertIn('Fringe', bpy.data.collections)

    def test_colour_rows_panels_and_menus(self):
        hm = self.hm
        self.assertEqual([e.gid for e in hm.rows], [self.g.id], 'one color row per group, kept by save_project')
        bpy.ops.hairmesh.add_group(name='Side', parent=-2)
        P = self.P(); h = next(x for x in P.groups() if x.name == 'Side')
        self.assertEqual([e.gid for e in self.hm.rows], [self.g.id, h.id])
        row = next(e for e in self.hm.rows if e.gid == h.id)
        self.assertEqual(tuple(round(c, 2) for c in row.color), (0.29, 0.5, 0.84), 'the default color (#4a7fd6)')
        row.color = (0.25, 0.75, 0.5)
        self.assertEqual(self.P().items[h.id].color, '#40bf80', 'the swatch writes the group color'); self.assertEqual(self.P().items[self.g.id].color, None)
        self.assertEqual(next(r for r in self.rows()[0] if r.kind == 'item' and r.id == h.id).group['color'], '#40bf80')
        bpy.ops.hairmesh.select_item(item=h.id); bpy.ops.hairmesh.delete_group()
        self.assertEqual([e.gid for e in self.hm.rows], [self.g.id], 'the row of a deleted group goes')
        for name in ('HAIRMESH_PT_outliner', 'HAIRMESH_PT_outliner_dock'):
            self.assertTrue(hasattr(bpy.types, name), name)
        self.assertEqual(bpy.types.HAIRMESH_PT_outliner.bl_label, 'HairMesh Outliner')
        self.assertTrue(bpy.types.HAIRMESH_PT_outliner_dock.poll(bpy.context))
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(self.tool.pie_items(bpy.context)[7], self.tool.OUTLINER_OP, 'the outliner in the pie')
        self.assertIn(self.tool.OUTLINER_OP_S, self.tool.popup_top(bpy.context), 'and on top of the HairMesh Menu')
        self.assertEqual(self.OL.summary_text(self.rows()[1]), '4 items · 2 hidden by the group mode', 'A, B, R and the wall')


if __name__ == '__main__':
    unittest.main()
