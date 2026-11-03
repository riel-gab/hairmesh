"""3.8e: the selected anchors red and the size of the others (item 1), the curves drawn in front of the wall edges
they lie on (item 2: CURVE_OFFSET, only the drawing). The pixels: tools/sim_c38e_shots.py (docs/img/c38e-*)."""
import unittest

try:
    import bpy
    from mathutils import Matrix
except ImportError:
    bpy = None


class _RV3D:
    def __init__(self, perspective=True):
        self.view_distance = 10.0; self.view_perspective = 'PERSP' if perspective else 'ORTHO'; self.is_perspective = perspective


def _diff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


@unittest.skipUnless(bpy, 'needs Blender')
class TestC38e(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hairmesh.blender import draw, ops_gizmo
        cls.draw, cls.gizmo = draw, ops_gizmo

    def test_red_apart_from_the_other_reds_and_the_anchors(self):
        draw = self.draw
        red = draw.SEL_DEFAULT['anchor']
        self.assertEqual(red, (1.0, 0.0, 0.0))
        self.assertEqual(draw.CA['sel'], red); self.assertEqual(draw.CA['last'], red)
        self.assertEqual(draw.SEL_DEFAULT['curve'], (0xb4 / 255.0, 1.0, 0x1e / 255.0), 'the selected curve stays lime')
        # the gizmo's X (0xff3352: pink) and the Scissors' line and dots (0xff5a5a, salmon) are bluer / lighter; the
        # plain anchors yellow and cyan, the weld candidate magenta
        self.assertGreater(_diff(red, self.gizmo.AXIS_COLOR['X']), 0.15)
        self.assertGreater(_diff(red, draw.CUT_LINE[:3]), 0.2)
        for k in ('end', 'ctl', 'auto', 'hover'):
            self.assertGreater(_diff(red, draw.CUT_DOTS[k][0]), 0.13, k)
        for k in ('normal', 'shared', 'weld', 'att'):
            self.assertGreater(_diff(red, draw.CA[k]), 0.6, k)

    def test_sizes(self):
        draw = self.draw
        self.assertEqual(draw.SEL_PX, draw.ANCHOR_PX)
        self.assertAlmostEqual(draw.LAST_PX * 0.66, draw.ANCHOR_PX, msg='the last one: dot and rim as the others, the ring outside')
        self.assertGreater(draw.LAST_PX * (0.5 - 0.33), 2.0, 'the white ring wide enough to read (its outer part fades)')

    def test_preferences_default_and_reset(self):
        from hairmesh.blender import prefs
        # the default of the preference (what "Reset Selection Colors" gives back: property_unset)
        default = prefs.HairMeshPreferences.__annotations__['sel_anchor_color'].keywords['default']
        for got, want in zip(default, self.draw.SEL_DEFAULT['anchor']):
            self.assertAlmostEqual(got, want, places=6)

    def test_curves_pulled_in_front_of_the_walls(self):
        draw = self.draw
        self.assertGreater(draw.CURVE_OFFSET, draw.WALL_OFFSET, 'over a selected wall\'s orange edges (and the live wire: 0.5)')
        proj = Matrix(((1.0, 0, 0, 0), (0, 1.0, 0, 0), (0, 0, -1.002, -0.2002), (0, 0, -1.0, 0)))   # a perspective projection
        for persp, p in ((True, proj), (False, Matrix(((0.1, 0, 0, 0), (0, 0.1, 0, 0), (0, 0, -0.01, -1.0), (0, 0, 0, 1.0))))):
            z = lambda d: draw.offset_projection(p, _RV3D(persp), d)[2][3]
            self.assertLess(z(draw.CURVE_OFFSET), z(draw.WALL_OFFSET), 'nearer the camera: a smaller clip z')
            self.assertLess(z(draw.WALL_OFFSET), z(0.5))


if __name__ == '__main__':
    unittest.main()
