"""The conversion sliders of the lot of 21/10/2026 inside Blender (blender -b): they live on the WindowManager
(Blender pushes no undo step of its own for them; deferred.py does, when the slider is let go), the watcher's
decision for every event (pure: deferred.decide), the value confirmed as it shows does nothing (1b), and the
Conversion list's Check All (Tick Every Group since U7) / Uncheck All / Only This (1c) with undo. In background mode there is no event loop,
so a slider commits at once; the drag, the pause, the release, Esc and Ctrl+Z with real events are
tests/test_sim_conv.py (opt-in)."""
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipUnless(bpy, 'needs Blender')
class TestDeferredSliders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import deferred, live, props, session, tool
        cls.hairmesh, cls.deferred, cls.live, cls.props, cls.session, cls.tool = hairmesh, deferred, live, props, session, tool
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
        bpy.ops.hairmesh.new_project(name='dfr')

    @property
    def root(self):
        return bpy.data.objects['HairMesh dfr']                     # afresh: an undo frees the old one

    @property
    def cv(self):
        return bpy.context.window_manager.hairmesh_conv

    def P(self):
        return self.session.get_project(self.root)

    def text(self):
        return bpy.data.texts[self.root['hairmesh_text']].as_string()

    def test_decide(self):
        d = self.deferred.decide; Q = self.deferred.QUIET
        # a drag (the button held when the watcher started)
        self.assertEqual(d(True, False, 'MOUSEMOVE', 'NOTHING', True, 0.0), 'pass')
        self.assertEqual(d(True, False, 'TIMER', 'NOTHING', True, Q / 2), 'pass', 'still moving')
        self.assertEqual(d(True, False, 'TIMER', 'NOTHING', True, Q), 'pause', 'a pause: nothing built, no undo step yet')
        self.assertEqual(d(True, False, 'TIMER', 'NOTHING', False, 5.0), 'pass', 'rebuilt already, still held')
        self.assertEqual(d(True, False, 'LEFTMOUSE', 'RELEASE', False, 0.0), 'end', 'the release: one step')
        self.assertEqual(d(True, False, 'Z', 'PRESS', True, 0.0), 'pass', 'keys do nothing during a drag')
        self.assertEqual(d(True, False, 'ESC', 'PRESS', True, 0.0), 'cancel', 'the UI puts the value back after us')
        self.assertEqual(d(True, False, 'RIGHTMOUSE', 'PRESS', True, 0.0), 'cancel')
        self.assertEqual(d(True, True, 'TIMER', 'NOTHING', True, 0.0), 'end', 'the next tick ends it')
        # a typed value, Ctrl+wheel (not held)
        self.assertEqual(d(False, False, 'TIMER', 'NOTHING', True, Q / 2), 'pass')
        self.assertEqual(d(False, False, 'TIMER', 'NOTHING', True, Q), 'end', 'still for QUIET: rebuild, save, one step')
        self.assertEqual(d(False, False, 'WHEELUPMOUSE', 'PRESS', True, 0.0), 'pass', 'the next wheel step')
        self.assertEqual(d(False, False, 'LEFT_CTRL', 'RELEASE', True, 0.0), 'pass')
        self.assertEqual(d(False, False, 'Z', 'PRESS', True, 0.0), 'end', 'Ctrl+Z: the step is pushed before the undo')
        self.assertEqual(d(False, False, 'LEFTMOUSE', 'PRESS', True, 0.0), 'end', 'a click elsewhere')
        self.assertEqual(d(False, False, 'MOUSEMOVE', 'NOTHING', True, 0.0), 'pass')
        self.assertEqual(d(False, False, 'WINDOW_DEACTIVATE', 'NOTHING', True, 0.0), 'end')

    def test_sliders_live_on_the_window_manager(self):
        P = self.P()
        self.assertEqual(self.props.CONV_ATTRS, {f'{s}_{k}' for s in ('group', 'conv') for k in ('step', 'dev', 'straighten', 'loop_orient', 'tip_mode', 'relax', 'merge_trunks')})
        for attr in self.props.CONV_ATTRS:
            self.assertFalse(hasattr(bpy.context.scene.hairmesh, attr), f'{attr}: not a Scene property (its undo step would come before the rebuild)')
            self.assertIs(type(self.props.data_of(bpy.context, attr)), type(self.cv))
        self.assertIs(type(self.props.data_of(bpy.context, 'group_ticked')), type(bpy.context.scene.hairmesh))
        # in background mode the slider commits at once: saved and rebuilt
        self.cv.conv_dev = 0.45
        self.assertEqual(self.P().settings.dev, 0.45)
        self.assertIn('"dev":0.45', self.text())
        self.assertFalse(self.deferred.pending())

    def test_same_value_does_nothing(self):
        """1b: a double click and Enter on a slider without changing it: nothing is rebuilt or saved"""
        bpy.ops.hairmesh.add_group(name='G')
        P = self.P(); g = P.groups()[0]
        self.cv.group_dev = 0.4
        rev, text, conv = self.P().rev, self.text(), dict(self.P().items[g.id].conv)
        st = self.session.state(self.root, self.P()); st['touched'] = set()
        self.live.last_rebuild.pop(self.root.name, None)
        self.cv.group_dev = self.cv.group_dev                         # what Enter on the unchanged field does (the float32 it shows)
        self.cv.group_step = self.cv.group_step
        self.cv.conv_dev = self.cv.conv_dev
        self.cv.conv_tip_mode = self.cv.conv_tip_mode
        self.assertEqual((self.P().rev, self.text()), (rev, text), 'nothing saved')
        self.assertNotIn(self.root.name, self.live.last_rebuild, 'nothing rebuilt')
        self.assertFalse(st.get('touched'), 'no lock touched')
        self.assertEqual(self.P().items[g.id].conv, conv)
        self.cv.group_step = self.cv.group_step + 2e-7                # a typed value read back through the UI's six decimals: still the same
        self.assertEqual((self.P().rev, self.text()), (rev, text))
        self.cv.group_dev = 0.41
        self.assertEqual(self.P().items[g.id].conv['dev'], 0.41)

    def test_project_step_survives_undo_and_redo(self):
        """the Conversion tab's Loop Step with nothing ticked sets the project step; an undo of a later step and a
        redo read the text back as written (load=False), not with loadProject's "the hair's step wins" (10164) — the
        model kept matching the restored preview (found by tools/sim_conv_slider.py, 21/10/2026)"""
        import json
        bpy.ops.hairmesh.add_group(name='G')
        for x in (0, 40):
            bpy.ops.hairmesh.draw_curve(points_json=json.dumps([[x, 0, 0], [x + 2, 40, 0], [x + 4, 80, 0]]))
        A, B = self.P().curves()
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.create_walls()
        bpy.ops.hairmesh.select(deselect_all=True)
        bpy.ops.hairmesh.tick_all(on=False); bpy.ops.ed.undo_push(message='untick')
        used = self.P().items[self.P().groups()[0].id].conv['step']
        self.cv.conv_step = used * 3; bpy.ops.ed.undo_push(message='project step')
        self.assertAlmostEqual(self.P().settings.step, used * 3, places=5)
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.ed.undo_push(message='select')
        bpy.ops.ed.undo()
        self.assertAlmostEqual(self.P().settings.step, used * 3, places=5, msg='the undo of the selection keeps the project step')
        bpy.ops.ed.undo()
        self.assertNotAlmostEqual(self.P().settings.step or 0, used * 3, places=5)
        bpy.ops.ed.redo()
        self.assertAlmostEqual(self.P().settings.step, used * 3, places=5, msg='redone as written')

    def _two_curves_and_a_wall(self):
        import json
        bpy.ops.hairmesh.add_group(name='G')
        for x in (0, 40):
            bpy.ops.hairmesh.draw_curve(points_json=json.dumps([[x, 0, 0], [x + 2, 40, 0], [x + 4, 80, 0], [x + 6, 120, 0]]))
        A, B = self.P().curves()
        bpy.ops.hairmesh.select(curve=A.id); bpy.ops.hairmesh.select(curve=B.id, extend=True); bpy.ops.hairmesh.create_walls()
        bpy.ops.hairmesh.select(deselect_all=True)
        return self.P().groups()[0].id

    def _mesh_and_full(self, gid):
        """the live mesh's points and the points of a full build of the model as it is now (float32, sorted)"""
        import numpy as np
        from hairmesh.blender import coords
        from hairmesh.core import curves as CM, rows as RW, surfaces as SF
        f = lambda x: round(float(np.float32(x)), 5)
        ob = self.live.live_objects(self.root)[gid]
        mesh = sorted(tuple(f(x) for x in v.co) for v in ob.data.vertices)
        P = self.P(); CM.rebuild_dirty(P); T = RW.buildRowTables(P).T
        full = sorted(tuple(f(x) for x in coords.to_blender(p)) for sf in P.surfaces() for p in SF.surfaceData(P, sf, T).pos)
        return mesh, full

    def test_forget_and_esc_drop_the_unsaved_change(self):
        """the review of 21/10/2026: an undo / a load while a change waits drops it with its model (forget), and Esc
        reads the model back from its text and rebuilds the preview from it (_restore) — even where the UI could not
        put the value back as it was (a group conv without that key, a derived project step)"""
        from hairmesh.core import groups as G
        D = self.deferred
        gid = self._two_curves_and_a_wall()
        text = self.text(); before = self._mesh_and_full(gid)[0]
        dev0 = self.P().items[gid].conv['dev']
        # a change in memory, never saved, pending (a slider outside -b), then an undo / a load: dropped
        P = self.P(); G.setGroupConv(P, P.items[gid], 'dev', 0.95); self.live.touch(self.root, [c.id for c in P.curves()])
        D._S['roots'].add(self.root.name); D._S['dirty'] = True; D._S['starting'] = True
        D.forget()
        self.assertFalse(D.pending(), 'nothing left hanging (no _start timer registered: starting cleared)')
        self.assertEqual(self.P().items[gid].conv['dev'], dev0, 'the model read back from the text')
        # Esc after a rebuild with the dragged value (a pause): the model and the preview of before
        P = self.P(); step0 = P.items[gid].conv['step']; S_step0 = P.settings.step
        G.setGroupConv(P, P.items[gid], 'step', step0 * 2); P.settings.step = 123.0   # (the project step too: a value the text does not hold)
        self.live.touch(self.root, [c.id for c in P.curves()]); D._S['roots'].add(self.root.name)
        self.live.update(bpy.context, self.root)
        self.assertNotEqual(self._mesh_and_full(gid)[0], before, 'the preview followed the drag')
        D._restore(bpy.context)
        self.assertFalse(D.pending())
        self.assertEqual((self.P().items[gid].conv['step'], self.P().settings.step), (step0, S_step0)); self.assertEqual(self.text(), text, 'nothing saved')
        mesh, full = self._mesh_and_full(gid)
        self.assertEqual(mesh, full, 'the preview is the model of before, rebuilt'); self.assertEqual(mesh, before)

    def test_check_all_uncheck_all_only_this(self):
        """1c: the Conversion list's buttons, their rows, undo"""
        for name in ('A', 'B', 'C'):
            bpy.ops.hairmesh.select_group(group=-1); bpy.ops.hairmesh.add_group(name=name)
        P = self.P(); a, b, c = P.groups()
        bpy.ops.hairmesh.tick_all(on=False); bpy.ops.ed.undo_push(message='none')
        self.assertEqual(self.P().settings.convChecked, [])
        bpy.ops.hairmesh.tick_all(on=True); bpy.ops.ed.undo_push(message='all')
        self.assertEqual(self.P().settings.convChecked, [a.id, b.id, c.id])
        bpy.ops.hairmesh.tick_group(group=b.id, only=True, toggle=False); bpy.ops.ed.undo_push(message='only b')
        self.assertEqual(self.P().settings.convChecked, [b.id])
        bpy.ops.ed.undo()
        self.assertEqual(self.P().settings.convChecked, [a.id, b.id, c.id])
        bpy.ops.ed.undo()
        self.assertEqual(self.P().settings.convChecked, [])
        bpy.ops.ed.redo(); bpy.ops.ed.redo()
        self.assertEqual(self.P().settings.convChecked, [b.id])
        # the rows of the Conversion tab: Tick Top Groups / Tick Every Group / Uncheck All on top (U7 C), Only This on every group's row
        rows = [self.tool.flat_items([r]) for r in self.tool.conversion_rows(bpy.context, self.P())]   # (the columns of each row flattened: part 2 of the lot ux7)
        self.assertEqual([(it[1], it[4]) for it in rows[0] if it[1] != 'hairmesh.expand_all'], [('hairmesh.tick_top', {}), ('hairmesh.tick_all', {'on': True}), ('hairmesh.tick_all', {'on': False})])
        rows = [self.tool.flat_items([r]) for r in self.tool.tree_list_rows(bpy.context, self.P())]
        for row, g in zip(rows, (a, b, c)):                         # (the rows of the list: [arrow or indent, the box, the step, Only This])
            self.assertEqual(next(it for it in row if it[1] == 'hairmesh.tick_group' and not it[4].get('only'))[4], {'group': g.id, 'toggle': True})
            self.assertEqual((row[-1][1], row[-1][2], row[-1][4]), ('hairmesh.tick_group', '', {'only': True, 'toggle': False, 'group': g.id}), 'icon only')
            self.assertEqual(self.tool.long_name(row[-1]), 'Only This Group', 'its long text')
            self.assertEqual(row[-1][5], {'list_text': f'Only {g.name}', **({'active': False} if g is b else {})}, 'the list menu names the group; part 1 of the lot ux7: dimmed on the only group ticked (B)')
        listed = [it[2] for it in self.tool.conversion_menu_items(bpy.context) if it[0] == 'op']
        self.assertIn('Tick Top Groups', listed); self.assertIn('Tick Every Group', listed); self.assertIn('Uncheck All', listed)


if __name__ == '__main__':
    unittest.main()
