"""Lot 4.7b -> 4.8, item 6 (the owner's request, ours): Alt in an anchor drag (Select's drag and G) slides the anchors
over the head, each keeping the distance it had when Alt went down — core/edit.surfaceDists / followSurface over a
`nearest(p) -> (closest point, normal)`; the Blender side gives the head's (pick.head_nearest, a BVH). Here a sphere."""
import math
import unittest

from hairmesh.core import edit
from hairmesh.core.vec3 import V3

C = V3(0, 0.5, 0); RAD = 0.3


def sphere(p):
    v = p.clone().sub(C); L = v.length()
    n = v.multiplyScalar(1 / L) if L > 0 else V3(0, 1, 0)
    return C.clone().addScaledVector(n, RAD), n.clone()


class TestFollowSurface(unittest.TestCase):
    def setUp(self):
        self.P = edit.new_project()
        self.anchors = [edit.newAnchor(self.P, V3(0.0, 0.5, RAD + d)) for d in (0.02, 0.05, 0.1)]

    def test_each_keeps_its_distance(self):
        d0 = edit.surfaceDists(self.anchors, sphere)
        self.assertEqual([round(d, 9) for d in d0], [0.02, 0.05, 0.1])
        for a in self.anchors:                                      # (the free move: across and a little inwards)
            a.pos.add(V3(0.2, 0.1, -0.12))
        edit.followSurface(self.P, self.anchors, d0, sphere)
        for a, d in zip(self.anchors, d0):
            self.assertAlmostEqual(a.pos.distanceTo(C) - RAD, d, places=9)

    def test_from_inside_goes_out_along_the_normal(self):
        d0 = edit.surfaceDists(self.anchors[:1], sphere)
        a = self.anchors[0]
        a.pos.copy(C).add(V3(0, 0, 0.1))                            # (the free move took it inside the head)
        edit.followSurface(self.P, [a], d0, lambda p: (C.clone().add(V3(0, 0, RAD)), V3(0, 0, 1)))
        self.assertAlmostEqual(a.pos.z, RAD + 0.02, places=9)

    def test_under_the_surface_it_stays_under(self):
        """the review of the lot: an anchor already under the head when Alt goes down (a root under the scalp) keeps its
        side — it slides under the surface at its depth, never jumps out"""
        a = edit.newAnchor(self.P, V3(0.0, 0.5, RAD - 0.03))
        d0 = edit.surfaceDists([a], sphere)
        self.assertAlmostEqual(d0[0], -0.03, places=9)
        a.pos.add(V3(0.15, 0.05, 0.0))
        edit.followSurface(self.P, [a], d0, sphere)
        self.assertAlmostEqual(RAD - a.pos.distanceTo(C), 0.03, places=9)

    def test_no_surface_no_move(self):
        a = self.anchors[0]; before = a.pos.toArray()
        edit.followSurface(self.P, [a], [None], sphere)
        self.assertEqual(a.pos.toArray(), before)
        self.assertEqual(edit.surfaceDists([a], lambda p: None), [None])
        self.assertTrue(math.isfinite(a.pos.x))


if __name__ == '__main__':
    unittest.main()
