"""Lot 4.8c. Item 4: a number field confirmed untouched writes nothing (its text holds six digits, 1234.568 reads
"1234.57"), and a value really typed still counts. Item 7: the runner's RUN line."""
import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    import bpy
except ImportError:
    bpy = None


def single(v):
    return struct.unpack('f', struct.pack('f', v))[0]


class TestFieldText(unittest.TestCase):
    """coords has no bpy: the rule alone"""
    @classmethod
    def setUpClass(cls):
        if bpy is None:
            raise unittest.SkipTest('hairmesh.blender needs bpy')
        from hairmesh.blender import coords
        cls.C = coords

    def test_six_digits_from_one_up_six_decimals_below(self):
        for v, text in ((1234.568, '1234.57'), (12345.67, '12345.7'), (123456.7, '123457'), (15.52, '15.5200'), (1.0, '1.00000'), (0.3, '0.300000')):
            self.assertEqual(self.C.field_text(v), text)

    def test_enter_on_any_value_is_the_same(self):
        for v in (1234.568, 1000.5, 5678.123, 9999.995, 12345.67, 99999.9, 0.123456, 3.14159, 15.5213):
            stored = self.C.f32(v)
            typed_back = self.C.f32(single(float(self.C.field_text(stored))))
            self.assertTrue(self.C.field_same(stored, typed_back), v)

    def test_a_typed_value_counts(self):
        self.assertFalse(self.C.field_same(1234.568, 1234.58))
        self.assertFalse(self.C.field_same(1234.568, 1234.0))
        self.assertFalse(self.C.field_same(0.0, 0.5))
        self.assertFalse(self.C.field_same(None, 0.5))

    def test_other_kinds_compare_plainly(self):
        self.assertTrue(self.C.field_same('a', 'a')); self.assertFalse(self.C.field_same('a', 'b')); self.assertFalse(self.C.field_same(3, 4))


@unittest.skipIf(bpy is None, 'needs bpy')
class TestLoopStepEnter(unittest.TestCase):
    def setUp(self):
        from hairmesh.blender import coords, session
        from hairmesh.core import edit, groups as G
        self.C = coords; self.G = G
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.P = edit.new_project()
        self.g = edit.addItem(self.P, 'group', None, {})

    def enter(self, stored, g=None):
        val = self.C.f32(single(float(self.C.field_text(stored))))
        return self.G.convUnchanged(self.P, 'step', val, g, tol=1e-6, echo=self.C.field_same)

    def test_project_step(self):
        for v in (1234.568, 7777.777, 12345.67):
            self.G.setConv(self.P, 'step', v)
            self.assertTrue(self.enter(v), v)

    def test_group_step(self):
        for v in (1234.568, 7777.777):
            self.G.setGroupConv(self.P, self.g, 'step', v)
            self.assertTrue(self.enter(v, self.g), v)

    def test_really_typed(self):
        self.G.setConv(self.P, 'step', 1234.568)
        self.assertFalse(self.G.convUnchanged(self.P, 'step', 1234.58, None, tol=1e-6, echo=self.C.field_same))



@unittest.skipUnless(bpy, 'needs Blender')
class TestBrushSession(unittest.TestCase):
    def test_aim_keeps_what_was_seen_for_the_same_view_only(self):
        from types import SimpleNamespace
        from hairmesh.blender import pick

        def project(key):
            f = lambda p: None
            f.key = key
            return f
        occ = SimpleNamespace(view=None, memo={}, screen={})
        context = SimpleNamespace(region=None, region_data=None)
        pick.Occluder.aim(occ, context, project('A'))
        occ.memo['seen'] = True
        pick.Occluder.aim(occ, context, project('A'))
        self.assertEqual(occ.memo, {'seen': True})
        pick.Occluder.aim(occ, context, project('B'))               # (the Empty moved: the key carries its matrix)
        self.assertEqual(occ.memo, {})

    def test_a_stroke_of_a_dead_session_warms_nothing(self):
        from types import SimpleNamespace
        from unittest import mock
        from hairmesh.blender import brush as BRU, live, session
        name = 'c48c closing'
        root = bpy.data.objects.new(name, None)
        self.addCleanup(bpy.data.objects.remove, root)
        op = SimpleNamespace(kind='TIP', root_name=name, stroke={'changed': True, 'cache': {}}, pointer_hidden=False, _warm=mock.Mock(), header=mock.Mock())
        with mock.patch.dict(BRU.END, {'TIP': lambda *a: None}), mock.patch.object(session, 'get_project'), mock.patch.object(session, 'save_project'), mock.patch.object(live, 'update'), \
                mock.patch.object(BRU, 'bpy', SimpleNamespace(data=bpy.data, ops=SimpleNamespace(ed=SimpleNamespace(undo_push=lambda **kw: None)))), mock.patch.object(BRU.select, 'redraw'):
            BRU.HAIRMESH_OT_brush._end_stroke(op, bpy.context, closing=True)
        op._warm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
