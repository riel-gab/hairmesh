"""Phase 3.4 inside Blender (blender -b): the weld pie and its operators, Ctrl + click unweld through the non-modal
form of hairmesh.select, split / curve between / curve through / rotate junction / optimize / merge, the menus they
live in (the SW slot of the anchor pie, the rows of the pop-up, the list), and the weld candidate color."""
import json
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(bpy, 'needs Blender')
class TestWeldOperators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import draw, ops_weld, select, session, tool
        from hairmesh.core import weld as W
        cls.hairmesh, cls.draw, cls.ops_weld, cls.select, cls.session, cls.tool, cls.W = hairmesh, draw, ops_weld, select, session, tool, W
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
        bpy.ops.hairmesh.new_project(name='weld')
        self.root = bpy.data.objects['HairMesh weld']
        self.hm = bpy.context.scene.hairmesh

    def curve(self, *pts):
        bpy.ops.hairmesh.draw_curve(points_json=json.dumps([list(p) for p in pts]))
        P = self.session.get_project(self.root)
        return P, P.curves()[len(P.curves()) - 1]

    def sel(self):
        return self.select.load(self.hm)

    def saved(self):
        return json.loads(bpy.data.texts[self.root['hairmesh_text']].as_string())

    @staticmethod
    def ops(items):
        out = []
        for it in items:
            if it[0] == 'box':
                out.extend(TestWeldOperators.ops(it[1]))
            elif it[0] == 'block':
                out.extend(TestWeldOperators.ops([x for row in it[4] for x in row]))
            elif it[0] == 'op':
                out.append((it[1], it[4]))
            elif it[0] == 'menu':
                out.append(('menu', it[1]))
        return out

    def test_weld_pie_and_weld(self):
        P, A = self.curve((0, 0, 0), (0, 30, 0), (0, 60, 0), (0, 90, 0), (0, 120, 0))
        P, B = self.curve((40, 20, 0), (20, 45, 0), (1, 61, 0))
        a2 = A.anchors[2]; b2 = B.anchors[2]
        title = self.ops_weld.ask_weld(b2, a2, P)
        self.assertEqual(title, 'Anchors are close')
        items = self.ops(self.ops_weld.weld_pie_items())
        self.assertEqual(items, [('hairmesh.weld_dismiss', {}), ('hairmesh.weld', {'a': b2.id, 'b': a2.id, 'mode': 'WELD'})])
        self.assertEqual(bpy.ops.hairmesh.weld_dismiss(), {'FINISHED'}); self.assertIsNone(self.ops_weld.pending)
        self.assertEqual(bpy.ops.hairmesh.weld(a=b2.id, b=a2.id, mode='WELD'), {'FINISHED'})
        P = self.session.get_project(self.root); A, B = P.curves()
        self.assertEqual(len(B.anchors), 5, 'the Y: B extended along A')
        self.assertIs(B.anchors[2], A.anchors[2])
        self.assertEqual(self.sel().anchors, [a2.id], 'S.asel = [b]')
        self.assertEqual(len([a for a in self.saved()['anchors']]), 7, 'saved: 5 + 3 - 1 anchors')
        # a start on an end: the other pie
        P, C = self.curve((100, 0, 0), (100, 50, 0)); P, D = self.curve((101, 51, 0), (120, 90, 0))
        title = self.ops_weld.ask_weld(D.anchors[0], C.anchors[1], P)
        self.assertEqual(title, 'Start meets end')
        items = self.ops(self.ops_weld.weld_pie_items())
        self.assertEqual([i[0] for i in items], ['hairmesh.weld_dismiss', 'hairmesh.weld', 'hairmesh.weld'])
        self.assertEqual([i[1].get('mode') for i in items[1:]], ['REVERSE', 'JOIN'])
        self.assertEqual(bpy.ops.hairmesh.weld(a=D.anchors[0].id, b=C.anchors[1].id, mode='JOIN'), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertNotIn(D.id, P.items); self.assertEqual(len(P.items[C.id].anchors), 3)
        self.assertEqual(self.sel().items, [C.id], 'the joined curve is selected')
        with self.assertRaises(RuntimeError):                       # ERROR reports: not a start-meets-end pair; no anchors given
            bpy.ops.hairmesh.weld(a=A.anchors[0].id, b=A.anchors[4].id, mode='REVERSE')
        with self.assertRaises(RuntimeError):
            bpy.ops.hairmesh.weld(a=-1, b=-1)
        self.assertTrue(hasattr(bpy.types, 'HAIRMESH_MT_weld_pie'))
        self.assertEqual(self.ops_weld.weld_pie_items()[0][0], 'label', 'nothing pending: a label')

    def test_ctrl_click_unwelds(self):
        P, A = self.curve((0, 0, 0), (0, 30, 0), (0, 60, 0), (0, 90, 0))
        P, B = self.curve((40, 20, 0), (1, 61, 0))
        bpy.ops.hairmesh.weld(a=B.anchors[1].id, b=A.anchors[2].id)
        P = self.session.get_project(self.root); A, B = P.curves()
        shared = A.anchors[2]
        bpy.ops.hairmesh.select(curve=B.id)
        self.assertEqual(bpy.ops.hairmesh.select(anchor=shared.id, toggle=True), {'FINISHED'})
        P = self.session.get_project(self.root); A, B = P.curves()
        self.assertIsNot(B.anchors[1], A.anchors[2], 'B came apart with its own anchor')
        self.assertEqual(B.anchors[1].pos.toArray(), A.anchors[2].pos.toArray())
        self.assertEqual(self.sel().items, [B.id], 'the selection stays')
        # not shared any more: a plain Ctrl + click toggles the anchor
        bpy.ops.hairmesh.select(anchor=B.anchors[1].id, toggle=True)
        self.assertEqual(self.sel().anchors, [B.anchors[1].id])
        # the operator form, with the curve given
        bpy.ops.hairmesh.weld(a=B.anchors[1].id, b=A.anchors[2].id)
        P = self.session.get_project(self.root); A, B = P.curves()
        self.assertEqual(bpy.ops.hairmesh.unweld_anchor(anchor=A.anchors[2].id, curve=A.id), {'FINISHED'})
        P = self.session.get_project(self.root); A, B = P.curves()
        self.assertIsNot(A.anchors[2], B.anchors[1])
        self.assertEqual(bpy.ops.hairmesh.unweld_anchor(anchor=A.anchors[2].id, curve=A.id), {'CANCELLED'}, 'nothing to unweld')

    def test_split_between_through_junction(self):
        P, H = self.curve((0, 0, 0), (0, 30, 0), (0, 60, 0), (0, 90, 0), (0, 120, 0))
        P, I = self.curve((60, 10, 0), (40, 40, 0), (1, 61, 0))
        bpy.ops.hairmesh.weld(a=I.anchors[2].id, b=H.anchors[2].id)   # I extended along H: a trunk h2, h3, h4
        P = self.session.get_project(self.root); H, I = P.curves()
        self.assertEqual(len(I.anchors), 5)
        bpy.ops.hairmesh.select(anchor=H.anchors[3].id)
        more = self.ops(self.ops_weld.anchor_more_items(bpy.context))
        self.assertIn(('hairmesh.split_at_anchor', {}), more); self.assertIn(('menu', 'HAIRMESH_MT_unweld'), more)
        self.assertIn(('hairmesh.optimize_anchors', {'only': True}), more)
        self.assertNotIn(('hairmesh.curve_between', {}), more, 'one anchor selected')
        self.assertEqual([i[1]['curve'] for i in self.ops(self.ops_weld.unweld_items(bpy.context))], [H.id, I.id])
        self.assertEqual(self.ops_weld.junction_items(bpy.context), [], 'two directions only: no junction')
        pie = self.tool.pie_items(bpy.context)
        self.assertEqual(pie[6], ('menu', 'HAIRMESH_MT_anchor_more', 'Weld / Split / Curves', 'DECORATE_KEYFRAME'), 'the SW slot')
        popup = self.ops([it for _t, _i, rows in self.tool.popup_sections(bpy.context) for row in rows for it in row])
        self.assertIn(('hairmesh.split_at_anchor', {}), popup); self.assertIn(('menu', 'HAIRMESH_MT_unweld'), popup)
        menu = self.ops(self.tool.menu_items(bpy.context))
        self.assertIn(('hairmesh.optimize_anchors', {'only': True}), menu)
        self.assertEqual(bpy.ops.hairmesh.split_at_anchor(), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertEqual(len(P.curves()), 3, 'H, I and one shared part')
        self.assertEqual(sorted(self.sel().items), sorted(c.id for c in P.curves()), 'the pieces are selected')
        self.assertEqual(bpy.ops.hairmesh.split_at_anchor(anchor=H.anchors[0].id), {'CANCELLED'})
        # curve between two anchors (the first of H and the last of I), through, and the junction submenu
        P, L = self.curve((200, 0, 0), (200, 40, 0), (200, 80, 0)); P, M = self.curve((260, 0, 0), (260, 40, 0), (260, 80, 0))
        bpy.ops.hairmesh.select(anchor=L.anchors[1].id); bpy.ops.hairmesh.select(anchor=M.anchors[1].id, extend=True)
        more = self.ops(self.ops_weld.anchor_more_items(bpy.context))
        self.assertIn(('hairmesh.curve_between', {}), more); self.assertIn(('hairmesh.curve_through', {}), more)
        self.assertEqual(bpy.ops.hairmesh.curve_between(), {'FINISHED'})
        P = self.session.get_project(self.root); c = P.items[self.sel().items[0]]
        self.assertEqual(len(c.anchors), 5, 'as long as the curves it joins: root of L, across, on along M')
        self.assertEqual(c.parent, L.parent)
        bpy.ops.hairmesh.select(anchor=L.anchors[0].id); bpy.ops.hairmesh.select(anchor=M.anchors[2].id, extend=True); bpy.ops.hairmesh.select(anchor=H.anchors[0].id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.curve_through(), {'FINISHED'})
        P = self.session.get_project(self.root); t = P.items[self.sel().items[0]]
        self.assertEqual(len(t.anchors), 3); self.assertIsNone(t.parent)
        with self.assertRaises(RuntimeError):                       # an ERROR report: no anchors selected
            bpy.ops.hairmesh.curve_between()
        # a crossing: rotate junction
        P, Pc = self.curve((300, 0, 0), (305, 40, 0), (310, 80, 0))
        P, Q = self.curve((270, 30, 0), (305, 40, 0.0), (350, 60, 0))
        bpy.ops.hairmesh.weld(a=Q.anchors[1].id, b=Pc.anchors[1].id)
        P = self.session.get_project(self.root); j = P.items[Pc.id].anchors[1]
        bpy.ops.hairmesh.select(anchor=j.id)
        jr = self.ops(self.ops_weld.junction_items(bpy.context))
        self.assertEqual([i[1]['choice'] for i in jr], [0, 1], 'P and Q pass smoothly; no Automatic yet')
        self.assertIn(('menu', 'HAIRMESH_MT_junction'), self.ops(self.ops_weld.anchor_more_items(bpy.context)))
        self.assertEqual(bpy.ops.hairmesh.rotate_junction(choice=1), {'FINISHED'})
        P = self.session.get_project(self.root); j = P.items[Pc.id].anchors[1]
        self.assertEqual(j.pass_, [P.items[Q.id].anchors[0].id, P.items[Q.id].anchors[2].id])
        self.assertEqual([i[1]['choice'] for i in self.ops(self.ops_weld.junction_items(bpy.context))], [0, 1, -1])
        self.assertEqual(bpy.ops.hairmesh.rotate_junction(choice=-1), {'FINISHED'})
        self.assertIsNone(self.session.get_project(self.root).items[Pc.id].anchors[1].pass_)
        with self.assertRaises(RuntimeError):                       # an ERROR report: no such choice
            bpy.ops.hairmesh.rotate_junction(choice=5)

    def test_optimize_and_merge(self):
        import math
        pts = [(60 * math.cos(math.radians(k * 7.5)), 60 * math.sin(math.radians(k * 7.5)), 0) for k in range(13)]
        P, U = self.curve(*pts)
        bpy.ops.hairmesh.select(curve=U.id)
        c = self.ops(self.tool.pie_items(bpy.context))
        self.assertIn(('hairmesh.optimize_anchors', {'only': False}), c); self.assertNotIn(('hairmesh.merge_coincident', {}), c, 'one curve')
        self.assertEqual(bpy.ops.hairmesh.optimize_anchors(), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertLess(len(P.items[U.id].anchors), 13); self.assertEqual(self.sel().items, [U.id])
        self.assertEqual(bpy.ops.hairmesh.optimize_anchors(), {'FINISHED'}, 'nothing more to remove: the original still commits and says so')
        self.assertEqual(len(self.session.get_project(self.root).items[U.id].anchors), len(P.items[U.id].anchors))
        # only the selected anchors
        P, Z = self.curve((200, 0, 0), (200.5, 15, 0), (201, 30, 0), (201.6, 45, 0), (202.1, 60, 0), (202.6, 75, 0), (203, 90, 0))
        z = list(Z.anchors)
        bpy.ops.hairmesh.select(anchor=z[2].id); bpy.ops.hairmesh.select(anchor=z[3].id, extend=True)
        self.assertEqual(bpy.ops.hairmesh.optimize_anchors(only=True), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertEqual([a.id for a in P.items[Z.id].anchors], [z[0].id, z[1].id, z[4].id, z[5].id, z[6].id])
        self.assertEqual(self.sel().anchors, [], 'S.asel = []')
        # merge coincident anchors of two curves
        P, J = self.curve((300, 0, 0), (300, 40, 0)); P, K = self.curve((300, 40, 0), (340, 60, 0))
        bpy.ops.hairmesh.select(curve=J.id); bpy.ops.hairmesh.select(curve=K.id, extend=True)
        popup = self.ops([it for _t, _i, rows in self.tool.popup_sections(bpy.context) for row in rows for it in row])
        self.assertIn(('hairmesh.merge_coincident', {}), popup); self.assertIn(('hairmesh.optimize_anchors', {'only': False}), popup)
        self.assertEqual(bpy.ops.hairmesh.merge_coincident(), {'FINISHED'})
        P = self.session.get_project(self.root)
        self.assertIs(P.items[K.id].anchors[0], P.items[J.id].anchors[1])
        self.assertEqual(bpy.ops.hairmesh.merge_coincident(), {'CANCELLED'}, 'no anchors at identical places')

    def test_weld_candidate_colour(self):
        P, A = self.curve((0, 0, 0), (0, 30, 0), (0, 60, 0))
        g = self.draw.geometry(P, self.root.matrix_world, weld=A.anchors[1].id)
        i = [k for k, s in enumerate(g['anchors']['size']) if s == self.draw.WELD_PX]
        self.assertEqual(len(i), 1); self.assertEqual(g['anchors']['col'][i[0]], self.draw.CA['weld'])
        self.assertIsNone(self.draw.weld_cand)
        for name in ('HAIRMESH_MT_anchor_more', 'HAIRMESH_MT_unweld', 'HAIRMESH_MT_junction', 'HAIRMESH_MT_weld_pie'):
            self.assertTrue(hasattr(bpy.types, name), name)
