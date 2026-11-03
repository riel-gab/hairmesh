"""Phase 3.5 inside Blender (blender -b): walls and locks as operators (create / exactly these / closed, fill hole,
average curve, segments -> curves), a wall as a selected item (select_wall, a click's non-modal form, Backspace),
the surface panel's properties on Scene.hairmesh, the menus they live in (the wall pie, the Walls / Lock submenu, the
pop-up rows, the list), the N panels' poll, the live preview's incremental grids with the per-face wall ids, and the
selected wall's overlay geometry."""
import json
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(bpy, 'needs Blender')
class TestWallOperators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import draw, live, ops_walls, select, session, tool, ui
        from hairmesh.core import walls as WL
        cls.hairmesh, cls.draw, cls.live, cls.ops_walls, cls.select, cls.session, cls.tool, cls.ui, cls.WL = hairmesh, draw, live, ops_walls, select, session, tool, ui, WL
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
        bpy.ops.hairmesh.new_project(name='walls')
        self.root = bpy.data.objects['HairMesh walls']
        self.hm = bpy.context.scene.hairmesh

    def curve(self, *pts):
        bpy.ops.hairmesh.draw_curve(points_json=json.dumps([list(p) for p in pts]))
        P = self.session.get_project(self.root)
        return P, P.curves()[len(P.curves()) - 1]

    def sel(self):
        return self.select.load(self.hm)

    @staticmethod
    def ops(items):
        out = []
        for it in items:
            if it[0] == 'box':
                out.extend(TestWallOperators.ops(it[1]))
            elif it[0] == 'block':
                out.extend(TestWallOperators.ops([x for row in it[4] for x in row]))
            elif it[0] == 'op':
                out.append((it[1], it[4]))
            elif it[0] in ('menu', 'prop', 'toggle'):
                out.append((it[0], it[1]))
        return out

    def popup(self):
        return self.ops([it for _t, _i, rows in self.tool.popup_sections(bpy.context) for row in rows for it in row])

    def test_create_walls_select_and_menus(self):
        P, A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0), (6, 120, 0))
        P, B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0), (46, 120, 0))
        self.assertEqual(bpy.ops.hairmesh.create_walls(), {'CANCELLED'}, 'one curve selected (the drawn one)')
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True)
        walls = self.ops(self.ops_walls.walls_menu_items(bpy.context))
        self.assertEqual(walls, [('hairmesh.create_walls', {'closed': False, 'exact': False}), ('hairmesh.fill_hole', {}), ('hairmesh.average_curve', {}), ('hairmesh.merge_coincident', {}),
                                 ('hairmesh.set_guide_mode', {'mode': 'LINE'})], '4.2: the last item of the curve pie of the original, "Guide lines (drag across curves)" (7928)')
        self.assertEqual(self.ops_walls.walls_menu_items(bpy.context)[0][2], 'Create Wall')
        popup = self.popup()
        self.assertIn(('hairmesh.create_walls', {'closed': False, 'exact': False}), popup); self.assertIn(('hairmesh.average_curve', {}), popup)
        self.assertIn(('menu', 'HAIRMESH_MT_walls'), self.ops(self.tool.pie_items(bpy.context)))
        self.assertIn(('hairmesh.fill_hole', {}), self.ops(self.tool.menu_items(bpy.context)))
        self.assertEqual(bpy.ops.hairmesh.create_walls(), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertEqual(len(P.surfaces()), 1)
        s = P.surfaces()[0]
        self.assertEqual((s.a, s.b, s.p), (A.id, B.id, {'segs': 1, 'bulge': 0.15, 'flip': False}))
        self.assertEqual(self.sel().items, [s.id], 'the new wall is the selection (S.sel = made)')
        self.assertEqual(bpy.ops.hairmesh.create_walls(), {'CANCELLED'}, 'a wall selected, no curves')
        # the wall pie, pop-up, list and panels
        ctx = bpy.context
        self.assertTrue(self.ui.HAIRMESH_PT_wall.poll(ctx)); self.assertFalse(self.ui.HAIRMESH_PT_curve.poll(ctx)); self.assertFalse(self.ui.HAIRMESH_PT_anchors.poll(ctx))
        pie = self.tool.pie_items(ctx)
        self.assertEqual(len(pie), 8)
        ops = self.ops(pie) + self.ops(next(r for it in pie if it[0] == 'box' for r in it[1] if r[0] == 'row')[1])
        self.assertEqual(ops[:2], [('hairmesh.select_wall_curves', {'which': 'A'}), ('hairmesh.select_wall_curves', {'which': 'B'})], 'W and E: the driver curves')
        self.assertIn(('hairmesh.delete_selected', {'keep_shape': False}), ops); self.assertIn(('prop', 'wall_segs'), ops)
        self.assertIn(('toggle', 'wall_flip'), ops); self.assertIn(('toggle', 'wall_clean'), ops)
        self.assertIn(('toggle', 'wall_b2on'), ops)
        self.assertNotIn(('prop', 'wall_seg_off'), ops, 'one segment: no offset slider'); self.assertNotIn(('hairmesh.segments_to_curves', {}), ops)
        self.assertEqual(pie[5][1], 'wm.call_panel'); self.assertEqual(pie[6], self.tool.WALLS_MENU)
        self.assertEqual([t for t, _i, _r in self.tool.popup_sections(ctx)], ['Sub-tool', 'Selection', 'Modifiers', 'Conversion', 'Viewport', 'Project'])
        self.assertEqual(self.tool.popup_tabs(ctx)['SELECTION'][0][0], ('label', s.name, 'MOD_LATTICE'), 'the Selection tab: the wall')
        self.assertEqual([it[2] for row in self.tool.popup_tabs(ctx)['SELECTION'] for it in row if it[0] == 'block'], ['Shape'], 'the blocks of the wall sheet: Second Surface with one on, the two curves beside the group')
        popup = self.popup()
        self.assertIn(('prop', 'wall_bulge'), popup); self.assertNotIn(('hairmesh.select_wall_curves', {'which': 'BOTH'}), popup)   # (4.3, item 1: the pie only)
        menu = self.ops(self.tool.menu_items(ctx))
        self.assertNotIn(('hairmesh.select_wall_curves', {'which': 'A'}), menu); self.assertIn(('prop', 'wall_clean'), menu)
        self.assertEqual(self.ops(self.ops_walls.walls_menu_items(ctx))[:3], [('hairmesh.select_wall_curves', {'which': 'A'}), ('hairmesh.select_wall_curves', {'which': 'B'}), ('hairmesh.select_wall_curves', {'which': 'BOTH'})])
        # select the driver curves, the wall from the curve panel's list, Shift adds
        self.assertEqual(bpy.ops.hairmesh.select_wall_curves(which='BOTH'), {'FINISHED'})
        self.assertEqual(self.sel().items, [A.id, B.id]); self.assertTrue(self.ui.HAIRMESH_PT_curve.poll(ctx))
        self.assertEqual(self.ops(self.ops_walls.wall_select_items(P, [A])), [('hairmesh.select_wall', {'wall': s.id})])
        self.assertNotIn(('hairmesh.select_wall', {'wall': s.id}), self.popup(), "the curve section names the walls the curves drive (4.3, item 1: no button)")
        self.assertEqual(bpy.ops.hairmesh.select_wall(wall=s.id), {'FINISHED'}); self.assertEqual(self.sel().items, [s.id])
        bpy.ops.hairmesh.select(curve=A.id)
        self.assertEqual(bpy.ops.hairmesh.select(wall=s.id, extend=True), {'FINISHED'}); self.assertEqual(self.sel().items, [A.id, s.id], 'a click on a live wall, non-modal form')
        self.assertTrue(self.ui.HAIRMESH_PT_wall.poll(ctx), 'the last selected item is the wall')
        self.assertEqual(bpy.ops.hairmesh.select_wall_curves(which='A'), {'FINISHED'}); self.assertEqual(self.sel().items, [A.id])
        with self.assertRaises(RuntimeError):                       # an ERROR report: no such wall
            bpy.ops.hairmesh.select_wall(wall=9999)
        self.assertEqual(bpy.ops.hairmesh.select_wall_curves(which='B'), {'CANCELLED'}, 'no wall selected')
        # exactly these / closed over three curves; the hint without enough curves
        P, C = self.curve((80, 0, 10), (82, 40, 10), (84, 80, 10), (86, 120, 10))
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.select(curve=C.id, extend=True)
        keys = [i[1] for i in self.ops_walls.curve_wall_items(P, [A, B, C])]
        self.assertEqual(keys, ['hairmesh.create_walls', 'hairmesh.create_walls', 'hairmesh.fill_hole', 'hairmesh.merge_coincident'])
        self.assertEqual(self.ops_walls.curve_wall_items(P, [A, B, C])[1][2], 'Volume Lock from 3 Curves (Closed)')
        self.assertEqual(bpy.ops.hairmesh.create_walls(closed=True, exact=True), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertEqual([(w.a, w.b) for w in P.surfaces()], [(A.id, B.id), (B.id, C.id), (C.id, A.id)])
        self.assertEqual(len(self.sel().items), 2, 'the two new walls')
        bpy.ops.hairmesh.select(curve=A.id)
        self.assertEqual(self.ops_walls.walls_menu_items(bpy.context)[0][0], 'op', 'one curve: the walls it drives')
        bpy.ops.hairmesh.select(deselect_all=True)
        self.assertEqual(self.ops_walls.walls_menu_items(bpy.context)[0][0], 'label', 'nothing selected: a hint')
        for name in ('create_walls', 'fill_hole', 'average_curve', 'segments_to_curves', 'select_wall', 'select_wall_curves'):
            self.assertTrue(hasattr(bpy.ops.hairmesh, name), name)
        self.assertTrue(hasattr(bpy.types, 'HAIRMESH_PT_wall'))

    def test_wall_properties_and_live_preview(self):
        P, A = self.curve((0, 0, 0), (2, 40, 0), (4, 80, 0), (6, 120, 0))
        P, B = self.curve((40, 0, 0), (42, 40, 0), (44, 80, 0), (46, 120, 0))
        P, C = self.curve((80, 0, 0), (82, 40, 0), (84, 80, 0), (86, 120, 0))
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.select(curve=C.id, extend=True)
        bpy.ops.hairmesh.create_walls()
        P = self.session.get_project(self.root); s1, s2 = P.surfaces()
        bpy.ops.hairmesh.select_wall(wall=s1.id)
        hm = self.hm
        self.assertEqual((bpy.context.window_manager.hairmesh_slide.wall_segs, round(bpy.context.window_manager.hairmesh_slide.wall_bulge, 6), hm.wall_flip, hm.wall_clean, hm.wall_b2on), (0, 0.15, False, False, False), 'the properties read the wall (float32 sliders)')
        bpy.context.window_manager.hairmesh_slide.wall_segs = 2
        P = self.session.get_project(self.root); s1 = P.items[s1.id]
        self.assertEqual(s1.p['segs'], 3, 'the slider shows segs - 1')
        self.assertFalse(s1.dirty, 'the setter dirtied the wall and the live update inside it computed the grid again')
        self.assertEqual(len(self.live.grid_of(self.root, P, s1.id).pos) % 4, 0, 'three segments: four points per row')
        popup = self.popup()
        self.assertIn(('prop', 'wall_seg_off'), popup); self.assertIn(('prop', 'wall_seg_spread'), popup); self.assertIn(('hairmesh.segments_to_curves', {}), popup)
        self.assertNotIn(('prop', 'wall_seg_off'), self.ops(self.tool.pie_items(bpy.context)), 'at most 3 sliders in the box of a pie (G3)')
        bpy.context.window_manager.hairmesh_slide.wall_bulge = 0
        self.assertNotIn(('toggle', 'wall_b2on'), self.ops(next(r for it in self.tool.pie_items(bpy.context) if it[0] == 'box' for r in it[1] if r[0] == 'row')[1]), 'no bulge, no second surface: gone from the pie (G4)')
        bpy.context.window_manager.hairmesh_slide.wall_seg_off = 0.3; bpy.context.window_manager.hairmesh_slide.wall_bulge = 0.3; hm.wall_flip = True
        s1 = self.session.get_project(self.root).items[s1.id]
        self.assertEqual((s1.p['segOff'], s1.p['bulge'], s1.p['flip']), (0.3, 0.3, True), 'float32 sliders stored as the short decimal')
        hm.wall_b2on = True
        s1 = self.session.get_project(self.root).items[s1.id]
        self.assertEqual((s1.p['b2on'], s1.p['b2bulge'], s1.p['b2segs'], s1.p['b2flip']), (True, 0.3, 3, False), 'the twin defaults')
        self.assertEqual((bpy.context.window_manager.hairmesh_slide.wall_b2segs, round(bpy.context.window_manager.hairmesh_slide.wall_b2bulge, 6), hm.wall_b2flip), (2, 0.3, False))
        self.assertIn(('prop', 'wall_b2bulge'), self.popup())
        bpy.context.window_manager.hairmesh_slide.wall_b2segs = 0; hm.wall_b2flip = True
        s1 = self.session.get_project(self.root).items[s1.id]
        self.assertEqual((s1.p['b2segs'], s1.p['b2flip']), (1, True))
        # Clean topology applies to every selected wall and rebuilds the tables from scratch
        bpy.ops.hairmesh.select_wall(wall=s2.id, extend=True)
        st = self.session.state(self.root, self.session.get_project(self.root))
        self.assertIsNotNone(st.get('rows'), 'the live update of the setters built the tables')
        rows0 = st['rows']
        hm.wall_clean = True
        P = self.session.get_project(self.root)
        self.assertTrue(P.items[s1.id].p['clean'] and P.items[s2.id].p['clean'])
        self.assertIs(self.session.state(self.root, P).get('rows'), rows0, 'S.surfDirty, since 16/10/2026 through the incremental path: the same RowBuild')
        self.assertEqual(self.live.last_rebuild[self.root.name][1], 3, 'the lock of the three curves of the two walls was rebuilt (bit-identical to a rebuild from scratch: test_rows_incremental)')
        self.assertEqual(json.loads(bpy.data.texts[self.root['hairmesh_text']].as_string())['items'][-1]['p']['clean'], True, 'saved: one undo step per slider change')
        # the live preview: one object for the group (root level here), a wall id per face, incremental grids
        objs = self.live.rebuild(bpy.context, self.root)
        self.assertEqual(list(objs), [None]); ob = objs[None]
        attr = ob.data.attributes.get(self.live.FACE_ATTR)
        self.assertIsNotNone(attr)
        owners = [0] * len(ob.data.polygons); attr.data.foreach_get('value', owners)
        self.assertEqual(set(owners), {s1.id, s2.id}); self.assertEqual(len(owners), len(ob.data.polygons))
        g1 = self.live.grid_of(self.root, P, s1.id)
        self.assertEqual(owners.count(s1.id), len(g1.faces), 'the twin is part of the wall')
        g, changed = self.live.grids(self.root, P)
        self.assertEqual(changed, set(), 'nothing changed since: no grid recomputed')
        rev = self.live.revision(self.root); grev = dict(st['grid_rev']); me = ob.data; v0 = [tuple(v.co) for v in me.vertices]
        bpy.context.window_manager.hairmesh_slide.wall_bulge = 0.1                                         # the LAST selected wall: s2
        P = self.session.get_project(self.root)
        self.assertEqual(P.items[s2.id].p['bulge'], 0.1); self.assertEqual(P.items[s1.id].p['bulge'], 0.3)
        st = self.session.state(self.root, P)
        self.assertEqual(self.live.revision(self.root), rev + 1, 'the setter recomputed a grid')
        self.assertEqual({k for k in st['grid_rev'] if st['grid_rev'][k] != grev.get(k)}, {s2.id}, 'only the edited wall')
        self.assertIs(ob.data, me, 'the mesh is refilled in place, never replaced')
        self.assertNotEqual([tuple(v.co) for v in me.vertices], v0, 'and the live wall follows the slider at once')
        self.assertEqual(me[self.live.SHOWN_KEY], self.live.shown_key({s1.id: st['grid_rev'][s1.id], s2.id: st['grid_rev'][s2.id]}), 'the mesh remembers the grids it shows')
        rev = self.live.revision(self.root)
        objs2 = self.live.rebuild(bpy.context, self.root)
        self.assertIs(objs2[None], ob, 'the object is reused'); self.assertEqual(self.live.revision(self.root), rev)
        # the overlay of the selected wall: 2 triangles per quad, 4 edges per quad
        geo = self.draw.geometry(P, self.root.matrix_world, self.sel(), walls=[g1])
        self.assertEqual(len(geo['walls']['tris']), 6 * len(g1.faces)); self.assertEqual(len(geo['walls']['lines']), 8 * len(g1.faces))
        self.assertEqual(self.draw.geometry(P, self.root.matrix_world)['walls'], {'tris': [], 'lines': []})
        # the polygon offset (1b of the manual tests of 15/10/2026): the port of GPU_polygon_offset_calc pulls the
        # overlay towards the camera by more than the wireframe overlay pulls the live mesh's wire (dist 1.0, halved
        # in its shader), in perspective and in orthographic views, touching only the projection's depth translation
        from types import SimpleNamespace
        from mathutils import Matrix
        persp = Matrix(((1.5, 0.0, 0.0, 0.0), (0.0, 2.0, 0.0, 0.0), (0.0, 0.0, -1.0002, -0.20002), (0.0, 0.0, -1.0, 0.0)))
        ortho = Matrix(((0.5, 0.0, 0.0, 0.0), (0.0, 0.6, 0.0, 0.0), (0.0, 0.0, -0.002, -1.0), (0.0, 0.0, 0.0, 1.0)))
        self.assertAlmostEqual(self.draw.polygon_offset(persp, 5.0, 1.0), -0.20002 * -0.0025 * 0.5)
        self.assertAlmostEqual(self.draw.polygon_offset(ortho, 5.0, 1.0), 0.00001 * 0.5 * 5.0)
        for proj in (persp, ortho):
            wire = self.draw.polygon_offset(proj, 5.0, 1.0) * 0.5
            self.assertGreater(wire, 0.0); self.assertGreater(self.draw.polygon_offset(proj, 5.0, self.draw.WALL_OFFSET), wire)
        rv = SimpleNamespace(view_distance=5.0, view_perspective='PERSP', is_perspective=True)
        m = self.draw.offset_projection(persp, rv, self.draw.WALL_OFFSET)
        same = [(i, j) for i in range(4) for j in range(4) if (i, j) != (2, 3)]
        self.assertEqual([m[i][j] for i, j in same], [persp[i][j] for i, j in same], 'only the depth translation changes')
        self.assertAlmostEqual(m[2][3], persp[2][3] - self.draw.polygon_offset(persp, 5.0, self.draw.WALL_OFFSET))
        self.assertLess(m[2][3], persp[2][3], 'pulled towards the camera'); self.assertAlmostEqual(persp[2][3], -0.20002, places=6, msg='the matrix given is not touched (a Matrix holds float32)')
        cam = SimpleNamespace(view_distance=5.0, view_perspective='CAMERA', is_perspective=False)
        self.assertAlmostEqual(self.draw.offset_projection(ortho, cam, 1.0)[2][3], ortho[2][3] - self.draw.polygon_offset(ortho, 1.0 / 0.6, 1.0), msg='an orthographic camera view: the distance is 1 / the zoom')
        self.assertGreater(self.draw.WALL_LINE_PX, self.draw.LINE_PX, 'the outline reads over the curves and the 1 px wire')
        self.assertIsNone(self.live.grid_of(self.root, P, A.id), 'not a wall')
        # Backspace on a selected wall deletes it; the object follows
        bpy.ops.hairmesh.select_wall(wall=s2.id)
        self.assertEqual(bpy.ops.hairmesh.delete_selected(), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertEqual([w.id for w in P.surfaces()], [s1.id]); self.assertEqual(self.sel().items, [])
        st = self.session.state(self.root, P)
        self.assertEqual(set(st['grids']), {s1.id}, 'the lock lost a wall: its tables from scratch, every wall of it again, the deleted one gone')
        owners = [0] * len(ob.data.polygons); ob.data.attributes[self.live.FACE_ATTR].data.foreach_get('value', owners)
        self.assertEqual(set(owners), {s1.id}, 'the live mesh followed inside the operator')
        g, changed = self.live.grids(self.root, P)
        self.assertEqual(changed, set())
        self.assertEqual(self.ops_walls.wall_actions(hm, P, P.items[s1.id])['label'][1], s1.name)
        self.live.invalidate_rows(self.root)
        self.assertEqual(self.live.grids(self.root, P)[1], {s1.id}, 'from scratch: every grid again')

    def test_fill_hole_average_and_segments(self):
        P, H = self.curve((520, 0, 0), (535, 60, 0), (550, 120, 0))
        P, I = self.curve((550, 0, 0), (535, 60, 0.0), (520, 120, 0))
        bpy.ops.hairmesh.weld(a=I.anchors[1].id, b=H.anchors[1].id)   # the crossing
        P = self.session.get_project(self.root); H, I = P.curves()
        P, J = self.curve((550, 120, 0.0), (535, 130, 5), (520, 120, 0.0))
        bpy.ops.hairmesh.weld(a=J.anchors[0].id, b=H.anchors[2].id); bpy.ops.hairmesh.weld(a=self.session.get_project(self.root).items[J.id].anchors[2].id, b=I.anchors[2].id)
        bpy.ops.hairmesh.select(curve=H.id); bpy.ops.hairmesh.select(curve=I.id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.fill_hole(), {'CANCELLED'}, 'no closed loop through H and I alone')
        bpy.ops.hairmesh.select(curve=J.id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.fill_hole(), {'FINISHED'})
        P = self.session.get_project(self.root)
        s = P.surfaces()[0]
        self.assertEqual(self.sel().items, [s.id]); self.assertTrue(P.items[s.a].junc and P.items[s.b].junc)
        self.assertEqual(len(P.curves()), 5)
        # the average curve of two curves sharing a tip
        P, K = self.curve((600, 0, 0), (605, 40, 0), (610, 80, 0), (620, 120, 0))
        P, L = self.curve((640, 0, 0), (635, 40, 0), (630, 80, 0), (620, 120, 0.0))
        bpy.ops.hairmesh.weld(a=L.anchors[3].id, b=K.anchors[3].id)
        bpy.ops.hairmesh.select(curve=K.id)
        self.assertEqual(bpy.ops.hairmesh.average_curve(), {'CANCELLED'}, 'exactly two curves')
        bpy.ops.hairmesh.select(curve=L.id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.average_curve(), {'FINISHED'})
        P = self.session.get_project(self.root); avg = P.items[self.sel().items[0]]
        self.assertEqual(avg.name, 'Average curve'); self.assertIs(avg.anchors[3], P.items[K.id].anchors[3])
        # segments -> curves on a wall with 3 width segments and a second surface
        P, O = self.curve((800, 0, 0), (803, 40, 0), (806, 80, 0), (810, 120, 0))
        P, Q = self.curve((850, 0, 0), (847, 40, 0), (844, 80, 0), (854, 120, 0))
        bpy.ops.hairmesh.select(curve=O.id); bpy.ops.hairmesh.select(curve=Q.id, extend=True)
        bpy.ops.hairmesh.create_walls()
        wid = self.sel().items[0]
        bpy.context.window_manager.hairmesh_slide.wall_segs = 2; bpy.context.window_manager.hairmesh_slide.wall_bulge = 0.25; self.hm.wall_b2on = True
        n_curves = len(self.session.get_project(self.root).curves())
        self.assertEqual(bpy.ops.hairmesh.segments_to_curves(), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertNotIn(wid, P.items); self.assertEqual(len(P.curves()), n_curves + 2)
        self.assertEqual(len(self.sel().items), 2, 'the new curves are selected')
        chain = [(w.a, w.b) for w in P.surfaces() if w.id != s.id]
        self.assertEqual(len(chain), 4, 'three flat walls and the twin'); self.assertEqual(chain[3], (Q.id, O.id))
        bpy.ops.hairmesh.select_wall(wall=chain and P.surfaces()[1].id)
        self.assertEqual(bpy.ops.hairmesh.segments_to_curves(), {'CANCELLED'}, 'one segment: nothing to do')

    def test_wall_under_without_a_region(self):
        from hairmesh.blender import pick

        class Ctx:
            region = None; region_data = None
        self.assertIsNone(pick.wall_under(Ctx, self.root, 0, 0))
