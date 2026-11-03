"""What a modal leaves when Blender ends it (a window closed, a file loaded) or the Pencil's loop goes between strokes: the
keys of the status bar (blender/modal_keys.py, G10), the state of the drawing, the model. The run of a modal itself is
in the test_sim_*."""
import unittest
from types import SimpleNamespace

try:
    import bpy
except ImportError:
    bpy = None

KEYS = ((('Esc',), 'Cancel'),)


@unittest.skipUnless(bpy, 'needs Blender')
class TestModalExits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import draw, modal_keys, ops_draw, ops_guides, ops_select
        cls.hairmesh, cls.draw, cls.keys, cls.ops_draw, cls.ops_guides, cls.ops_select = hairmesh, draw, modal_keys, ops_draw, ops_guides, ops_select
        try:
            hairmesh.register()
        except ValueError:
            pass

    @classmethod
    def tearDownClass(cls):
        try:
            cls.hairmesh.unregister()
        except Exception:
            pass

    def setUp(self):
        self.keys._keys[0] = KEYS

    def tearDown(self):
        self.keys._keys[0] = None
        self.ops_guides.pencil = None

    def test_the_pencil_loop_takes_its_keys_with_it(self):
        self.ops_guides.pencil = {'root': 'x', 'at': None, 'pc': None}
        self.ops_guides.cancel_pencil()
        self.assertIsNone(self.ops_guides.pencil)
        self.assertIsNone(self.keys._keys[0])

    def test_no_loop_leaves_the_keys_of_another_modal(self):
        self.ops_guides.cancel_pencil()
        self.assertEqual(self.keys._keys[0], KEYS)

    def test_empty_keys_draw_blenders_own_bar(self):
        drawn = []

        class Header:
            _draw_orig = lambda self, context: drawn.append(context)    # noqa: E731
        self.keys._keys[0] = None
        self.keys._draw(Header(), 'context')
        self.assertEqual(drawn, ['context'])

    def test_draw_and_circle_leave_nothing_drawn(self):
        self.draw.ghost = ((0, 0, 0), (1, 1, 1)); self.draw.drawing = 1
        self.ops_draw.HAIRMESH_OT_draw_curve.cancel(None, bpy.context)
        self.assertEqual((self.draw.ghost, self.draw.drawing), (None, None))
        self.assertIsNone(self.keys._keys[0])
        self.draw.overlay = ('circle', 0, 0, 10)
        self.ops_select.HAIRMESH_OT_select_circle.cancel(None, bpy.context)
        self.assertIsNone(self.draw.overlay)

    def test_smoothness_goes_back(self):
        from hairmesh.core import edit
        a = SimpleNamespace(smooth=0.25)
        calls = []
        real = edit.smoothDrag
        edit.smoothDrag = lambda P, anchors, starts, dx: calls.append((anchors, starts, dx))
        try:
            this = SimpleNamespace(root=SimpleNamespace(name='no root'), P=None, anchors=[a], starts=[0.25])
            self.ops_select.HAIRMESH_OT_smooth_drag.cancel(this, bpy.context)
        finally:
            edit.smoothDrag = real
        self.assertEqual(calls, [([a], [0.25], 0)], 'the drag at no distance: every anchor at its start')
        self.assertIsNone(self.keys._keys[0])


if __name__ == '__main__':
    unittest.main()
