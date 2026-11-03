"""Phase 3.2, pure core: the Draw tool's geometry (placePoint on the head or on the camera-facing plane),
addDrawPoint / finishDrawing, deleteItem, and the raycaster additions (Plane, intersectPlane, face normals)."""
import unittest

from hairmesh.core import edit, model as M
from hairmesh.core.headobj import obj_text
from hairmesh.core.raycast import Plane, Ray, triangleNormal
from hairmesh.core.vec3 import V3

# a 2 x 2 x 2 cube around the origin, faces wound counter-clockwise seen from outside (front faces outward)
CUBE_V = [-1, -1, -1, 1, -1, -1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, -1, 1, 1, 1, 1, -1, 1, 1]
CUBE_F = [[4, 5, 6, 7], [1, 0, 3, 2], [5, 1, 2, 6], [0, 4, 7, 3], [7, 6, 2, 3], [0, 1, 5, 4]]


def cube_project():
    P = edit.new_project()
    edit.set_head(P, 'cube.obj', {'ext': 'obj', 'text': obj_text(CUBE_V, CUBE_F)})
    return P


class TestRaycastAdditions(unittest.TestCase):
    def test_plane_and_intersect_plane(self):
        pl = Plane().setFromNormalAndCoplanarPoint(V3(0, 0, -1), V3(0, 0, -3))
        self.assertEqual(pl.constant, -3.0)
        hit = Ray(V3(1, 2, 5), V3(0, 0, -1)).intersectPlane(pl, V3())
        self.assertEqual(hit.toArray(), [1, 2, -3])
        self.assertIsNone(Ray(V3(0, 0, -5), V3(0, 0, -1)).intersectPlane(pl, V3()), 'the plane is behind the ray')
        self.assertIsNone(Ray(V3(0, 0, 0), V3(1, 0, 0)).intersectPlane(pl, V3()), 'parallel')
        self.assertEqual(Ray(V3(0, 0, -3), V3(1, 0, 0)).intersectPlane(pl, V3()).toArray(), [0, 0, -3], 'coplanar origin: t = 0')

    def test_triangle_normal(self):
        n = triangleNormal(V3(0, 0, 0), V3(1, 0, 0), V3(0, 1, 0), V3())
        self.assertEqual(n.toArray(), [0, 0, 1])
        self.assertEqual(triangleNormal(V3(0, 0, 0), V3(1, 0, 0), V3(2, 0, 0), V3()).toArray(), [0, 0, 0], 'degenerate')

    def test_head_trimesh_and_hits(self):
        P = cube_project()
        mesh = edit.head_trimesh(P)
        self.assertEqual(len(mesh.pos), 8); self.assertEqual(len(mesh.index), 6 * 2 * 3)
        self.assertIs(edit.head_trimesh(P), mesh, 'memoised')
        hit = edit.pickHead(P, Ray(V3(0, 0, 5), V3(0, 0, -1)))
        self.assertEqual(hit['point'].toArray(), [0, 0, 1]); self.assertEqual(hit['normal'].toArray(), [0, 0, 1]); self.assertEqual(hit['distance'], 4.0)
        self.assertIsNone(edit.pickHead(P, Ray(V3(0, 0, 0), V3(0, 0, -1))), 'from inside only back faces are met: culled (MeshStandardMaterial is FrontSide)')
        self.assertIsNone(edit.pickHead(P, Ray(V3(5, 0, 5), V3(0, 0, -1))), 'misses the cube')
        hit = edit.pickHead(P, Ray(V3(0.25, -0.5, 9), V3(0, 0, -1)))
        self.assertEqual(hit['point'].toArray(), [0.25, -0.5, 1], 'the nearest face, in a triangle of the fan')


class TestPlacePoint(unittest.TestCase):
    def test_on_the_head_lifted_along_the_normal(self):
        P = cube_project()
        self.assertEqual((P.S.scale, P.S.headH), (2.0, 2.0))
        p = edit.placePoint(P, Ray(V3(0, 0, 5), V3(0, 0, -1)), V3(0, 0, -1), V3())
        self.assertEqual(p.toArray(), [0, 0, 1 + 2 * 0.012], 'hit + normal * S.scale * 0.012')
        p = edit.placePoint(P, Ray(V3(5, 0.5, 0.5), V3(-1, 0, 0)), V3(-1, 0, 0), V3())
        self.assertEqual(p.toArray(), [1 + 2 * 0.012, 0.5, 0.5])

    def test_plane_fallbacks(self):
        P = cube_project()
        # missing the head: the camera-facing plane through the pivot
        p = edit.placePoint(P, Ray(V3(5, 0, 5), V3(0, 0, -1)), V3(0, 0, -1), V3(0, 0, -3))
        self.assertEqual(p.toArray(), [5, 0, -3])
        # a curve being drawn: through its last anchor instead
        d = edit.DrawState(); d.curve = edit.makeCurve(P, None)
        d.curve.anchors.append(edit.newAnchor(P, V3(0, 0, 2.5)))
        p = edit.placePoint(P, Ray(V3(5, 0, 5), V3(0, 0, -1)), V3(0, 0, -1), V3(0, 0, -3), d)
        self.assertEqual(p.toArray(), [5, 0, 2.5])
        # a hidden head counts as no head
        P.head.visible = False
        p = edit.placePoint(P, Ray(V3(0, 0, 5), V3(0, 0, -1)), V3(0, 0, -1), V3(0, 0, -3))
        self.assertEqual(p.toArray(), [0, 0, -3])
        # no head at all, and a ray parallel to the plane: nothing
        P2 = edit.new_project()
        self.assertEqual(edit.placePoint(P2, Ray(V3(0, 0, 5), V3(0, 0, -1)), V3(0, 0, -1), V3(1, 1, -3)).toArray(), [0, 0, -3])
        self.assertIsNone(edit.placePoint(P2, Ray(V3(0, 0, 5), V3(1, 0, 0)), V3(0, 0, -1), V3(1, 1, -3)))


class TestDrawing(unittest.TestCase):
    def test_add_points_and_finish(self):
        P = cube_project()
        g = edit.addItem(P, 'group', None, {'name': 'Hair'})
        d = edit.DrawState()
        ray = lambda x, y: Ray(V3(x, y, 5), V3(0, 0, -1))
        a1 = edit.addDrawPoint(P, d, ray(0, 0), V3(0, 0, -1), V3(), parent=g.id)
        c = d.curve
        self.assertIsNotNone(c); self.assertEqual((c.parent, c.name, c.id), (g.id, 'Curve 1', g.id + 1)); self.assertTrue(c.dirty)
        self.assertEqual(a1.id, c.id + 1); self.assertEqual(a1.pos.toArray(), [0, 0, 1 + 2 * 0.012])
        a2 = edit.addDrawPoint(P, d, ray(0.5, 0), V3(0, 0, -1), V3(), parent=g.id)
        self.assertIs(d.curve, c); self.assertEqual([a.id for a in c.anchors], [a1.id, a2.id])
        self.assertIsNone(edit.addDrawPoint(P, d, Ray(V3(5, 0, 5), V3(1, 0, 0)), V3(0, 0, -1), V3(), parent=g.id), 'a ray that lands nowhere adds nothing')
        self.assertEqual(len(c.anchors), 2)
        kept = edit.finishDrawing(P, d)
        self.assertIs(kept, c); self.assertIsNone(d.curve); self.assertIn(c.id, P.items); self.assertEqual(g.children, [c.id])
        self.assertIsNone(edit.finishDrawing(P, d), 'nothing being drawn')

    def test_single_point_curve_is_discarded(self):
        P = cube_project()
        d = edit.DrawState()
        edit.addDrawPoint(P, d, Ray(V3(0, 0, 5), V3(0, 0, -1)), V3(0, 0, -1), V3())
        c = d.curve
        uid = P.uid
        self.assertIsNone(edit.finishDrawing(P, d))
        self.assertNotIn(c.id, P.items); self.assertEqual(P.roots, [])
        self.assertEqual((P.uid, P.counters['curve']), (uid, 1), 'ids and counters are not given back (the original does not either)')
        self.assertEqual(edit.makeCurve(P, None).name, 'Curve 1', 'but the next curve is numbered from what is still in the scene')

    def test_delete_item(self):
        P = edit.new_project()
        g = edit.addItem(P, 'group', None)
        sub = edit.addItem(P, 'group', g.id)
        c1 = edit.makeCurve(P, sub.id); c2 = edit.makeCurve(P, sub.id); c3 = edit.makeCurve(P, None)
        for c in (c1, c2, c3):
            for k in range(2):
                c.anchors.append(edit.newAnchor(P, V3(k, 0, 0)))
        s = edit.addItem(P, 'surface', sub.id, {'a': c1.id, 'b': c2.id, 'p': {'segs': 1}})
        s2 = edit.addItem(P, 'surface', None, {'a': c2.id, 'b': c3.id, 'p': {'segs': 1}})
        gd = edit.addItem(P, 'guide', None, {'kind': 'line', 'hits': [{'c': c1.id, 't': 0.5}, {'c': c3.id, 't': 0.5}], 'p': {}})
        P.settings.convChecked = [g.id, sub.id]
        for c in P.curves():
            c.dirty = False
        edit.deleteItem(P, c1.id)
        self.assertNotIn(c1.id, P.items); self.assertNotIn(s.id, P.items, 'the wall on it goes too'); self.assertIn(s2.id, P.items)
        self.assertNotIn(gd.id, P.items, 'the guide lost its second hit: deleted')
        self.assertEqual(sub.children, [c2.id])
        self.assertTrue(all(c.dirty for c in P.curves()))
        edit.deleteItem(P, g.id)
        self.assertEqual(set(P.items), {c3.id}, 'a group takes its sub-groups and their content, and the wall that joined c2 to c3 goes with c2')
        self.assertEqual(P.roots, [c3.id]); self.assertEqual(P.settings.convChecked, [])
        edit.deleteItem(P, 999)                                     # unknown: nothing happens
