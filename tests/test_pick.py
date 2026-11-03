"""Phase 3.0: screen-space picking (port of pickAnchor / pickCurve / nearestOnScreen) over a fixed projection.
Needs mathutils (the Blender Python), nothing of bpy."""
import unittest

try:
    import mathutils  # noqa: F401
    HAVE_MATHUTILS = True
except ImportError:
    HAVE_MATHUTILS = False

from hairmesh.core import edit, scene
from hairmesh.core.vec3 import Vec3


def ortho(p):
    """x right, y up, 10 px per unit, origin at (100, 100); z = 0 (in front)"""
    return (100 + p.x * 10, 100 + p.y * 10, 0.0)


@unittest.skipUnless(HAVE_MATHUTILS, 'needs mathutils (run with the Blender Python)')
class TestPick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hairmesh.blender import pick
        cls.pick = pick
        P = edit.new_project()
        c1 = edit.makeCurve(P, None)
        for p in ((0, 0, 0), (4, 0, 0), (8, 0, 0)):
            c1.anchors.append(edit.newAnchor(P, Vec3(*p)))
        c2 = edit.makeCurve(P, None)
        for p in ((0, 5, 0), (4, 5, 1), (8, 5, 0)):
            c2.anchors.append(edit.newAnchor(P, Vec3(*p)))
        scene.settle(P)
        cls.P, cls.c1, cls.c2 = P, c1, c2

    def test_pick_anchor(self):
        pick = self.pick
        anchors = list(self.P.anchors.values())
        a = pick.pick_anchor(anchors, ortho, 141, 103)              # 1 px right, 3 px up of anchor (4, 0): 3.2 px away
        self.assertEqual(a.pos.toArray(), [4, 0, 0])
        self.assertIsNone(pick.pick_anchor(anchors, ortho, 141, 112), '12 px away: outside the 10 px radius')
        self.assertIsNotNone(pick.pick_anchor(anchors, ortho, 141, 112, radius=14))
        # the nearest wins among several (25 px from (0, 0), 15 px from (4, 0))
        a = pick.pick_anchor(anchors, ortho, 125, 100, radius=30)
        self.assertEqual(a.pos.toArray(), [4, 0, 0])
        # beyond the far plane (z > 1) and behind the camera (None) are skipped
        far = lambda p: (ortho(p)[0], ortho(p)[1], 1.5)
        self.assertIsNone(pick.pick_anchor(anchors, far, 140, 100))
        self.assertIsNone(pick.pick_anchor(anchors, lambda p: None, 140, 100))

    def test_pick_curve(self):
        pick = self.pick
        r = pick.pick_curve([self.c1, self.c2], ortho, 120, 103)    # 3 px above the straight curve 1 at x = 2
        self.assertIs(r['curve'], self.c1)
        self.assertAlmostEqual(r['point'].x, 2.0, places=6); self.assertAlmostEqual(r['point'].y, 0.0, places=6)
        self.assertEqual(r['seg'], 0)
        self.assertIsNone(pick.pick_curve([self.c1, self.c2], ortho, 120, 108), '8 px: outside the 7 px radius')
        r = pick.pick_curve([self.c1, self.c2], ortho, 160, 151)    # near curve 2 at x = 6
        self.assertIs(r['curve'], self.c2)
        self.assertEqual(r['seg'], 1)
        self.assertGreater(r['point'].z, 0.0, 'the point is interpolated along the 3D samples (curve 2 bulges in z)')
        # the end of a segment is clamped: a cursor beyond the curve's end still picks the last sample
        r = pick.pick_curve([self.c1], ortho, 185, 101)
        self.assertIs(r['curve'], self.c1); self.assertAlmostEqual(r['point'].x, 8.0, places=6)

    def test_nearest_on_screen(self):
        pick = self.pick
        r = pick.nearest_on_screen(self.c1, ortho, 140, 130)
        self.assertAlmostEqual(r['d'], 30.0, places=6)
        self.assertAlmostEqual(r['point'].x, 4.0, places=6)
        self.assertIsNone(pick.nearest_on_screen(self.c1, lambda p: None, 140, 130))

    def test_region_projector_matches_to_screen(self):
        from mathutils import Matrix
        pick = self.pick

        class R: width = 200; height = 100

        class V: perspective_matrix = Matrix.Identity(4)
        project = pick.region_projector(R, V, Matrix.Identity(4))
        s = project(Vec3(0, 0, 0))                                   # NDC (0, 0) -> the centre of the region
        self.assertEqual((round(s[0], 6), round(s[1], 6), round(s[2], 6)), (100.0, 50.0, 0.0))
        s = project(Vec3(1, 0.5, -0.5))                             # original (x, y, z) -> Blender (x, -z, y) = (1, 0.5, 0.5)
        self.assertEqual((round(s[0], 6), round(s[1], 6), round(s[2], 6)), (200.0, 75.0, 0.5))
        V.perspective_matrix = Matrix.Diagonal((1, 1, 1, 1)); V.perspective_matrix[3][2] = -1.0; V.perspective_matrix[3][3] = 0.0   # w = -z_blender
        project = pick.region_projector(R, V, Matrix.Identity(4))
        self.assertIsNone(project(Vec3(0, 1, 0)), 'behind the camera: Blender z = 1 -> w = -1')
        s = project(Vec3(0, -2, 0))                                 # Blender z = -2 -> w = 2: x/w, y/w
        self.assertEqual((round(s[0], 6), round(s[1], 6)), (100.0, 50.0))


@unittest.skipUnless(HAVE_MATHUTILS, 'needs mathutils (run with the Blender Python)')
class TestPickSelect(unittest.TestCase):
    """Phase 3.3: the box of the marquee, the Circle Select and the Shift slide (snapDragToCurve) over a fixed projection."""
    @classmethod
    def setUpClass(cls):
        from hairmesh.blender import pick
        cls.pick = pick
        P = edit.new_project()
        c1 = edit.makeCurve(P, None)
        for p in ((0, 0, 0), (4, 0, 0), (8, 0, 0)):
            c1.anchors.append(edit.newAnchor(P, Vec3(*p)))
        c2 = edit.makeCurve(P, None)
        for p in ((0, 5, 0), (4, 5, 1), (8, 5, 0)):
            c2.anchors.append(edit.newAnchor(P, Vec3(*p)))
        c3 = edit.makeCurve(P, None)                                 # a curve 3 units further from the camera, 0.3 px off curve 2 on screen
        for p in ((0, 5.03, -3), (4, 5.03, -3), (8, 5.03, -3)):
            c3.anchors.append(edit.newAnchor(P, Vec3(*p)))
        scene.settle(P)
        cls.P, cls.c1, cls.c2, cls.c3 = P, c1, c2, c3

    def test_anchors_in_box_and_circle(self):
        pick = self.pick
        anchors = edit.anchorList(self.P)
        inside = pick.anchors_in_box(anchors, ortho, 95, 95, 145, 160)
        self.assertEqual([a.pos.toArray() for a in inside], [[0, 0, 0], [4, 0, 0], [0, 5, 0], [4, 5, 1], [0, 5.03, -3], [4, 5.03, -3]])
        self.assertEqual(pick.anchors_in_box(anchors, ortho, 200, 200, 300, 300), [])
        far = lambda p: (ortho(p)[0], ortho(p)[1], 1.5)
        self.assertEqual(pick.anchors_in_box(anchors, far, 0, 0, 500, 500), [], 'beyond the far plane: skipped')
        self.assertEqual(pick.anchors_in_box(anchors, lambda p: None, 0, 0, 500, 500), [])
        inside = pick.anchors_in_circle(anchors, ortho, 140, 100, 25)
        self.assertEqual([a.pos.toArray() for a in inside], [[4, 0, 0]], 'only (4, 0): (0, 0) is 40 px away')
        inside = pick.anchors_in_circle(anchors, ortho, 140, 100, 41)
        self.assertEqual(len(inside), 3)
        self.assertEqual(pick.anchors_in_circle(anchors, far, 140, 100, 41), [])

    def test_curves_in_circle(self):
        pick = self.pick
        cs = [self.c1, self.c2, self.c3]
        self.assertEqual(pick.curves_in_circle(cs, ortho, 120, 110, 12), [self.c1], '10 px from curve 1, 40 from the others')
        self.assertEqual(pick.curves_in_circle(cs, ortho, 120, 125, 30), cs)
        self.assertEqual(pick.curves_in_circle(cs, ortho, 300, 300, 10), [])

    def test_snap_drag_to_curve(self):
        pick = self.pick
        P = self.P
        a = self.c1.anchors[1]                                       # the anchor of curve 1 being dragged: its own curve never counts
        # the cursor near curve 2 / 3 (0.3 px apart on screen): the nearer to the camera (curve 2, z smaller) wins the tie
        depth = lambda p: (100 + p.x * 10, 100 + p.y * 10, -p.z / 10)   # z inside the clip range (z > 1 is skipped as beyond the far plane)
        r = pick.snap_drag_to_curve(P, a, depth, 140, 152)
        self.assertIs(r[0], self.c2); self.assertAlmostEqual(r[1].y, 5.0, places=6)
        # hysteresis: the current curve keeps the anchor while it is within 6 px of the best
        r = pick.snap_drag_to_curve(P, a, depth, 140, 152, snap=self.c3.id)
        self.assertIs(r[0], self.c3, 'still the curve slid along: within 6 px of the best')
        r = pick.snap_drag_to_curve(P, a, depth, 140, 190, snap=self.c3.id)
        self.assertIs(r[0], self.c3, 'far from everything (40 px): the current one stays while it is the best anyway')
        # the anchor's own curve never counts: with only curve 1 left, nothing to slide along
        self.c2.visible = False; self.c3.visible = False
        self.assertIsNone(pick.snap_drag_to_curve(P, a, ortho, 140, 103))
        self.c2.visible = True; self.c3.visible = True
        # a point hidden behind the head counts as 25 px further: curve 3 occluded, curve 2 wins even when the cursor favours 3
        occ = lambda p: p.z < -1
        r = pick.snap_drag_to_curve(P, a, ortho, 140, 150, occluded=occ)
        self.assertIs(r[0], self.c2)
        r = pick.snap_drag_to_curve(P, a, ortho, 140, 150, snap=self.c3.id, occluded=occ)
        self.assertIs(r[0], self.c2, 'the occlusion penalty (25) beats the hysteresis (6)')
        # a ring (a disc) never counts
        self.c3.ring = 5
        r = pick.snap_drag_to_curve(P, a, ortho, 140, 150)
        self.assertIs(r[0], self.c2)
        self.c3.ring = None
