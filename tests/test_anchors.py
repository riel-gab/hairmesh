"""Phase 3.3, pure core: the anchor editing ports (insert on a shared segment, delete with and without keeping the
shape, the Y-branch rule, recalculate, reverse, even out, smoothness, the drag plane) and the selection rules."""
import unittest

from hairmesh.core import curves as CM, edit, model as M, scene
from hairmesh.core.raycast import Ray
from hairmesh.core.selection import Selection
from hairmesh.core.vec3 import V3


def curve(P, pts, parent=None):
    c = edit.makeCurve(P, parent)
    for p in pts:
        c.anchors.append(p if isinstance(p, M.Anchor) else edit.newAnchor(P, V3(*p)))
    return c


class TestHelpers(unittest.TestCase):
    def test_curves_with_and_hair_curves_and_anchor_list(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (10, 10, 0), (20, 20, 0)])
        B = curve(P, [(0, 5, 5), A.anchors[1], A.anchors[2]])
        self.assertEqual(edit.curvesWith(P, A.anchors[1]), [A, B])
        self.assertEqual(edit.curvesWith(P, A.anchors[0]), [A])
        self.assertEqual(edit.hairCurves(P), [A, B])
        self.assertFalse(edit.isRingAnchor(P, A.anchors[0]))
        self.assertEqual([a.id for a in edit.anchorList(P)], [a.id for a in A.anchors] + [B.anchors[0].id], 'every anchor once, in curves() order')
        B.visible = False
        self.assertEqual(edit.anchorList(P), A.anchors, 'a hidden curve shows no anchors')
        B.visible = True
        B.ring = 99
        self.assertEqual(edit.anchorList(P), A.anchors, 'disc points only while the disc or one of them is selected')
        self.assertEqual(len(edit.anchorList(P, sel_items=[99])), 4)
        self.assertEqual(len(edit.anchorList(P, sel_anchors=[B.anchors[0].id])), 4)
        self.assertTrue(edit.isRingAnchor(P, B.anchors[0]))
        self.assertEqual(edit.hairCurves(P), [A])

    def test_curves_on_segment(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (10, 10, 0), (20, 20, 0), (30, 30, 0)])
        B = curve(P, [(0, 5, 5), A.anchors[1], A.anchors[2]])
        C = curve(P, [A.anchors[2], (40, 0, 0), A.anchors[1]])            # uses both anchors but not as neighbours
        self.assertEqual(edit.curvesOnSegment(P, A, 1), [A.id, B.id])
        self.assertEqual(edit.curvesOnSegment(P, A, 0), [A.id])
        self.assertEqual(edit.curvesOnSegment(P, A, 3), [A.id], 'past the last segment: just the curve')
        self.assertEqual(edit.curvesOnSegment(P, C, 0), [C.id])
        B.visible = False
        self.assertEqual(edit.curvesOnSegment(P, A, 1), [A.id], 'hidden curves do not count')


class TestInsert(unittest.TestCase):
    def test_insert_on_a_shared_segment_goes_into_every_welded_curve(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (10, 10, 0), (20, 20, 0), (30, 30, 0)])
        B = curve(P, [(0, 5, 5), A.anchors[1], A.anchors[2]])
        C = curve(P, [A.anchors[2], A.anchors[1]])                       # the same segment the other way round
        D = curve(P, [A.anchors[1], (5, 50, 0), A.anchors[2]])            # both anchors, not neighbours
        gen = P.gen
        a = edit.insertAnchorAt(P, A, 1, V3(15, 15, 0))
        self.assertEqual([x.id for x in A.anchors][1:3], [A.anchors[1].id, a.id])
        self.assertIs(B.anchors[2], a); self.assertIs(C.anchors[1], a, 'inserted between the two whichever way the curve runs')
        self.assertNotIn(a, D.anchors)
        self.assertTrue(A.dirty and B.dirty and C.dirty)
        self.assertGreater(P.gen, gen)
        self.assertEqual(a.smooth, 1); self.assertIs(P.anchors[a.id], a)
        # a closed curve's wrapping segment has no anchors[seg + 1]: nothing is inserted anywhere (the original's undefined)
        E = curve(P, [(0, 0, 9), (1, 0, 9), (1, 1, 9)]); E.closed = True
        n = len(E.anchors)
        edit.insertOnSegment(P, E, 2, edit.newAnchor(P, V3(0.5, 0.5, 9)))
        self.assertEqual(len(E.anchors), n)


class TestDelete(unittest.TestCase):
    def test_remove_keep_shape_freezes_the_neighbours(self):
        P = edit.new_project()
        pts = [(0, 0, 0), (10, 8, 0), (20, 14, 1), (30, 18, 1), (40, 20, 2), (50, 20, 2)]
        A = curve(P, pts); B = curve(P, [(x, y, z + 100) for x, y, z in pts])   # B: the same curve, deleted without keep
        a0, a1, a2, a3, a4, a5 = A.anchors
        scene.settle(P)
        before = [s.p.clone() for s in A.samples]; before_b = [s.p.clone() for s in B.samples]
        edit.removeAnchorFromCurve(P, A, a2, True)
        edit.removeAnchorFromCurve(P, B, B.anchors[2], False)
        self.assertEqual(A.anchors, [a0, a1, a3, a4, a5])
        self.assertEqual(sorted(A.ovr), sorted([a1.id, a3.id]), 'the neighbours of the deleted anchor get frozen handles')
        self.assertIsNotNone(A.ovr[a1.id].out); self.assertIsNotNone(A.ovr[a1.id].in_, 'a1 is interior: its in handle is frozen too')
        self.assertIsNotNone(A.ovr[a3.id].in_); self.assertIsNotNone(A.ovr[a3.id].out)
        self.assertEqual(len(B.anchors), 5); self.assertEqual(B.ovr, {}, 'without keep: nothing frozen')
        A.dirty = True; B.dirty = True; scene.settle(P)
        # the shape stays: the old samples lie closer to the new polyline than after a plain deletion, within 2 % of the
        # length (one cubic with the fitted handles replaces the two segments; measured 0.68 vs 0.80 on a length of 55.6)
        def dev(c, old):
            return max(min(p.distanceTo(s.p) for s in c.samples) for p in old)
        dk = dev(A, before); dp = dev(B, before_b)
        self.assertLess(dk, dp, f'keep shape {dk} vs plain {dp}')
        self.assertLess(dk, 0.02 * A.arc[len(A.arc) - 1])
        # the ends: the one neighbour is frozen on the side that faced the deleted anchor
        C = curve(P, [(0, 0, 0), (8, 15, 3), (15, 28, -1), (25, 40, 2)])
        c0, c1, c2, c3 = C.anchors
        edit.removeAnchorFromCurve(P, C, c0, True)
        self.assertEqual(list(C.ovr), [c1.id]); self.assertIsNotNone(C.ovr[c1.id].out); self.assertIsNone(C.ovr[c1.id].in_)
        edit.removeAnchorFromCurve(P, C, c3, True)
        self.assertIsNotNone(C.ovr[c2.id].in_); self.assertIsNone(C.ovr[c2.id].out)
        edit.removeAnchorFromCurve(P, C, c3, True)                      # not in the curve any more: nothing happens
        self.assertEqual(C.anchors, [c1, c2])
        edit.removeAnchorFromCurve(P, C, c1, True)                      # 2 anchors: keep is moot
        self.assertEqual(C.anchors, [c2]); self.assertNotIn(c1.id, C.ovr)

    def test_delete_anchors(self):
        P = edit.new_project()
        g = edit.addItem(P, 'group', None)
        A = curve(P, [(0, 0, 0), (10, 20, 2), (20, 40, -3), (30, 55, 1), (40, 60, 0)], g.id)
        a0, a1, a2, a3, a4 = A.anchors
        B = curve(P, [(-15, 5, 4), a1, a2, (35, 48, -8)], g.id)
        D = curve(P, [(50, 0, 0), (52, 30, 0)])
        E = curve(P, [(5, -10, 30), a2, a3, a4], g.id)                  # a Y branch: own anchor + the host's trunk
        sf = edit.addItem(P, 'surface', g.id, {'a': A.id, 'b': B.id})
        # a shared anchor leaves every curve using it; the curves touched are the new selection
        touched = edit.deleteAnchors(P, [a1], False)
        self.assertEqual(touched, [A.id, B.id])
        self.assertEqual(A.anchors, [a0, a2, a3, a4]); self.assertEqual(B.anchors, [B.anchors[0], a2, B.anchors[2]])
        self.assertTrue(all(c.dirty for c in P.curves()))
        # a curve left with one anchor is deleted (and its walls with it)
        touched = edit.deleteAnchors(P, [D.anchors[0]], False)
        self.assertEqual(touched, []); self.assertNotIn(D.id, P.items); self.assertNotIn(D.id, P.roots)
        # the Y branch loses its own anchor: every segment left is the host's, the branch goes
        touched = edit.deleteAnchors(P, [E.anchors[0]], False)
        self.assertEqual(touched, []); self.assertNotIn(E.id, P.items); self.assertIn(A.id, P.items)
        self.assertEqual(A.anchors, [a0, a2, a3, a4], 'the host keeps its anchors')
        self.assertIn(sf.id, P.items, 'the wall between A and B is untouched')
        # disc points cannot be deleted
        R = curve(P, [(0, 0, 50), (1, 0, 50), (1, 1, 50)]); R.ring = 777
        self.assertIsNone(edit.deleteAnchors(P, [R.anchors[0]], False))
        self.assertEqual(len(R.anchors), 3)
        self.assertEqual(edit.deleteAnchors(P, [R.anchors[0], a0], False), [A.id], 'the disc point is skipped, the other goes')
        # the curve being drawn may stay with one anchor
        Dr = curve(P, [(0, 0, 70), (1, 0, 70)])
        self.assertEqual(edit.deleteAnchors(P, [Dr.anchors[0]], False, drawing=Dr), [Dr.id]); self.assertEqual(len(Dr.anchors), 1)

    def test_recalc_handles(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (8, 15, 3), (15, 28, -1), (25, 40, 2), (32, 52, 0)])
        a0, a1, a2, a3, a4 = A.anchors
        a1.smooth = 0.3; a3.smooth = 0.5
        edit.removeAnchorFromCurve(P, A, a2, True)
        self.assertTrue(A.ovr)
        edit.recalcHandles(P, [a1])
        self.assertNotIn(a1.id, A.ovr); self.assertIn(a3.id, A.ovr); self.assertEqual(a1.smooth, 1); self.assertEqual(a3.smooth, 0.5)
        edit.recalcCurves(P, [A])
        self.assertEqual(A.ovr, {}); self.assertEqual([a.smooth for a in A.anchors], [1, 1, 1, 1])


class TestReverse(unittest.TestCase):
    def test_reverse_swaps_anchors_handles_hits_and_attachments(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (8, 15, 3), (15, 28, -1), (25, 40, 2)])
        a0, a1, a2, a3 = A.anchors
        A.ovr[a1.id] = M.HandleOverride(in_=V3(1, 0, 0), out=None)
        B = curve(P, [(0, 0, 9), (1, 1, 9)])
        g = edit.addItem(P, 'guide', None, {'kind': 'line', 'hits': [{'c': A.id, 't': 0.25}, {'c': B.id, 't': 0.5}]})
        disc = edit.addItem(P, 'guide', None, {'kind': 'disc'}); disc.dirty = False
        t = edit.addItem(P, 'tube', None, {'src': A.id}); t.dirty = False
        x = edit.addItem(P, 'xmod', None, {'tube': t.id, 'mask': [0.1, 0.2, 0.7]})
        a3.att = M.Attachment(c=A.id, t=0.2)
        R = curve(P, [(0, 0, 50), (1, 0, 50)]); R.ring = 5
        out = edit.reverseCurves(P, [A, R, None, g])
        self.assertEqual(out, [A], 'rings and non-curves are skipped')
        self.assertEqual(A.anchors, [a3, a2, a1, a0])
        self.assertIsNone(A.ovr[a1.id].in_); self.assertEqual(A.ovr[a1.id].out.toArray(), [1, 0, 0], 'in and out swapped')
        self.assertEqual([h['t'] for h in g.hits], [0.75, 0.5]); self.assertTrue(g.dirty); self.assertTrue(disc.dirty)
        self.assertTrue(t.dirty); self.assertEqual(x.mask, [0.7, 0.2, 0.1])
        self.assertEqual(a3.att.t, 0.8)
        self.assertTrue(A.dirty)
        self.assertEqual(edit.reverseCurves(P, []), [])


class TestEvenAndSmooth(unittest.TestCase):
    def test_even_anchors_goes_back_to_zero_and_restarts_after_a_move(self):
        P = edit.new_project()
        A = curve(P, [(60, 0, 0), (63, 10, 3), (59, 20, -2), (64, 30, 2), (60, 40, -3), (63, 50, 1), (60, 60, 0)])
        before = [a.pos.toArray() for a in A.anchors]
        CM.evenAnchors(P, [A], 0.5)
        self.assertEqual(A.asm, 0.5); self.assertEqual([p.toArray() for p in A.asrc], before, 'asrc = the positions before')
        self.assertEqual(A.anchors[0].pos.toArray(), before[0]); self.assertEqual(A.anchors[6].pos.toArray(), before[6], 'the ends stay')
        self.assertNotEqual(A.anchors[2].pos.toArray(), before[2])
        mid = A.anchors[2].pos.toArray()
        CM.evenAnchors(P, [A], 0.0)
        self.assertEqual([a.pos.toArray() for a in A.anchors], before, 'back to 0 is exact')
        CM.evenAnchors(P, [A], 0.5)
        self.assertEqual(A.anchors[2].pos.toArray(), mid)
        A.anchors[3].pos.add(V3(0, 0, 1))                           # moved by hand: asrc starts anew from the current positions
        CM.evenAnchors(P, [A], 0.5)
        self.assertNotEqual([p.toArray() for p in A.asrc], before)
        self.assertEqual(CM.evenIters(0), 0); self.assertEqual(CM.evenIters(1), 60); self.assertEqual(CM.evenIters(0.5), 15)
        # anchors shared with another curve and attached ones stay
        B = curve(P, [(0, 0, 0), (1, 5, 0), (0, 10, 0), (1, 15, 0), (0, 20, 0)])
        C = curve(P, [B.anchors[2], (9, 9, 9)])
        B.anchors[3].att = M.Attachment(c=C.id, t=0.5)
        b = [a.pos.toArray() for a in B.anchors]
        CM.evenAnchors(P, [B, C], 1.0)
        self.assertEqual(B.anchors[2].pos.toArray(), b[2]); self.assertEqual(B.anchors[3].pos.toArray(), b[3]); self.assertNotEqual(B.anchors[1].pos.toArray(), b[1])
        self.assertIsNone(C.asm, 'a 2-anchor curve is skipped')

    def test_smoothness_and_curve_props(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (8, 15, 3), (15, 28, -1)])
        B = curve(P, [(0, 0, 9), (8, 15, 9), (15, 28, 9)])
        scene.settle(P)
        self.assertEqual(edit.smoothDrag(P, A.anchors, [1, 1, 1], -100), 0.5)
        self.assertEqual([a.smooth for a in A.anchors], [0.5, 0.5, 0.5]); self.assertTrue(A.dirty)
        edit.smoothDrag(P, A.anchors[:1], [0.5], 400)
        self.assertEqual(A.anchors[0].smooth, 1, 'clamped')
        edit.setSmooth(P, A.anchors, 0.25)
        self.assertEqual([a.smooth for a in A.anchors], [0.25] * 3)
        scene.settle(P)
        edit.setCurveProp(P, [A, B], 'round', 0.7)
        self.assertEqual((A.round, B.round), (0.7, 0.7)); self.assertTrue(A.dirty and B.dirty)
        scene.settle(P)
        CM.fairSpline(A)
        self.assertIsNotNone(A.fair_cache)
        edit.setCurveProp(P, [A], 'fair', 0.4)
        self.assertEqual(A.fair, 0.4); self.assertIsNone(A.fair_cache, 'the fairSpline cache is dropped')

    def test_fit_handles(self):
        # a least-squares fit with chord-length parameters: a symmetric arc gives equal handle lengths and a cubic
        # that runs close to the points (the parameters are not the Bezier's own, so the lengths are not exactly the
        # source's 14.14); a degenerate fit falls back to a third of the chord
        p0 = V3(0, 0, 0); p3 = V3(30, 0, 0)
        seg = [p0, V3(10, 10, 0), V3(20, 10, 0), p3]
        pts = [CM.bez(*seg, k / 24) for k in range(25)]
        d1 = V3(1, 1, 0).normalize(); d2 = V3(-1, 1, 0).normalize()
        f = CM.fitHandles(pts, p0, p3, d1, d2)
        self.assertAlmostEqual(f['a'], f['b'], places=9); self.assertGreater(f['a'], 10); self.assertLess(f['a'], 15)
        fit = [p0, p0.clone().addScaledVector(d1, f['a']), p3.clone().addScaledVector(d2, f['b']), p3]
        self.assertLess(max(min(p.distanceTo(CM.bez(*fit, k / 48)) for k in range(49)) for p in pts), 0.6, 'within 8 % of the arc height (7.5)')
        f = CM.fitHandles([p0, p3], p0, p3, V3(1, 0, 0), V3(-1, 0, 0))
        self.assertEqual((f['a'], f['b']), (10.0, 10.0), 'a degenerate fit: a third of the chord')


class TestDrag(unittest.TestCase):
    def test_drag_on_the_plane_facing_the_camera(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (10, 0, 0), (20, 0, 0)])
        a = A.anchors[1]
        cam = V3(0, 0, -1)                                          # looking down -z: the drag plane is z = 0 through the anchor
        ray0 = Ray(V3(10, 0, 50), V3(0, 0, -1))
        d = edit.startAnchorDrag([a], ray0, cam, a.pos)
        self.assertEqual(d.startHit.toArray(), [10, 0, 0]); self.assertEqual(d.starts[0].toArray(), [10, 0, 0]); self.assertFalse(d.moved)
        self.assertTrue(edit.dragAnchors(P, d, Ray(V3(13, 4, 50), V3(0, 0, -1))))
        self.assertEqual(a.pos.toArray(), [13, 4, 0]); self.assertTrue(d.moved); self.assertTrue(A.dirty)
        self.assertTrue(edit.dragAnchors(P, d, Ray(V3(11, 1, 50), V3(0, 0, -1))))
        self.assertEqual(a.pos.toArray(), [11, 1, 0], 'always from the start, never cumulative')
        self.assertFalse(edit.dragAnchors(P, d, Ray(V3(0, 0, 50), V3(1, 0, 0))), 'a ray parallel to the plane moves nothing')
        self.assertEqual(a.pos.toArray(), [11, 1, 0])
        # a slide, then Shift released: free again from where it is, no jump
        B = curve(P, [(0, 20, 0), (30, 20, 0)]); scene.settle(P)
        edit.dragSnap(P, d, B, V3(12, 20, 0))
        self.assertEqual(d.snap, B.id); self.assertEqual(a.pos.toArray(), [12, 20, 0])
        edit.dragSnapRelease(d, Ray(V3(12, 20, 50), V3(0, 0, -1)))
        self.assertIsNone(d.snap); self.assertEqual(d.starts[0].toArray(), [12, 20, 0]); self.assertEqual(d.startHit.toArray(), [12, 20, 0])
        edit.dragAnchors(P, d, Ray(V3(12, 21, 50), V3(0, 0, -1)))
        self.assertEqual(a.pos.toArray(), [12, 21, 0])
        # several anchors move together
        d = edit.startAnchorDrag(A.anchors, Ray(V3(0, 0, 50), V3(0, 0, -1)), cam, A.anchors[0].pos)
        edit.dragAnchors(P, d, Ray(V3(1, 2, 50), V3(0, 0, -1)))
        self.assertEqual([x.pos.toArray() for x in A.anchors], [[1, 2, 0], [13, 23, 0], [21, 2, 0]])
        edit.moveAnchors(P, A.anchors, V3(-1, -2, 0))
        self.assertEqual([x.pos.toArray() for x in A.anchors], [[0, 0, 0], [12, 21, 0], [20, 0, 0]])
        self.assertEqual(edit.planeHit(Ray(V3(5, 5, 5), V3(0, 0, -1)), V3(0, 0, 1), V3(0, 0, 1)).toArray(), [5, 5, 1])

    def test_drag_on_the_head(self):
        """The "Drag on Head" option (an addition of this port, docs/features-adicionadas.md): the lead anchor goes to
        the head surface under the cursor, lifted 1.2 % of the model size (as placePoint), the others keep their
        offsets; off the head the plane drag restarts from where they are (dragRebase), no jump."""
        from tests.test_draw import cube_project
        P = cube_project()                                          # a 2 x 2 x 2 cube: P.S.scale = 2, the lift is 0.024
        lift = P.S.scale * 0.012
        A = curve(P, [(0, 0, 1 + lift), (0.5, 0, 1 + lift), (3, 0, 1 + lift)])
        a = A.anchors[1]
        cam = V3(0, 0, -1); down = V3(0, 0, -1)
        d = edit.startAnchorDrag([a], Ray(V3(0.5, 0, 5), down), cam, a.pos)
        self.assertEqual((d.lead, d.on_head), (0, False))
        self.assertTrue(edit.dragOnHead(P, d, Ray(V3(0.2, 0.3, 5), down)))
        for got, want in zip(a.pos.toArray(), [0.2, 0.3, 1 + lift]):
            self.assertAlmostEqual(got, want, places=12)
        self.assertTrue(d.moved); self.assertTrue(d.on_head); self.assertTrue(A.dirty)
        # a side face: lifted along ITS normal
        self.assertTrue(edit.dragOnHead(P, d, Ray(V3(5, 0.5, 0.5), V3(-1, 0, 0))))
        for got, want in zip(a.pos.toArray(), [1 + lift, 0.5, 0.5]):
            self.assertAlmostEqual(got, want, places=12)
        # the cursor leaves the head: nothing moves; the plane restarts from here, and a move is relative, no jump
        self.assertFalse(edit.dragOnHead(P, d, Ray(V3(5, 5, 5), down)))
        for got, want in zip(a.pos.toArray(), [1 + lift, 0.5, 0.5]):
            self.assertAlmostEqual(got, want, places=12)
        edit.dragRebase(d, Ray(V3(5, 5, 5), down))
        self.assertFalse(d.on_head); self.assertEqual(d.ref.toArray(), a.pos.toArray()); self.assertEqual(d.startHit.toArray(), [5, 5, 0.5])
        edit.dragAnchors(P, d, Ray(V3(5.5, 5, 5), down))
        for got, want in zip(a.pos.toArray(), [1.5 + lift, 0.5, 0.5]):
            self.assertAlmostEqual(got, want, places=12)
        # several anchors: the clicked one (ref = its pos) leads, the others follow with the same offset
        b = A.anchors[0]; b.pos.set(0, 0, 1 + lift); a.pos.set(0.5, 0, 1 + lift)
        d = edit.startAnchorDrag([b, a], Ray(V3(0.5, 0, 5), down), cam, a.pos)
        self.assertEqual(d.lead, 1)
        self.assertTrue(edit.dragOnHead(P, d, Ray(V3(0.0, 0.25, 5), down)))
        for got, want in zip(a.pos.toArray() + b.pos.toArray(), [0.0, 0.25, 1 + lift, -0.5, 0.25, 1 + lift]):
            self.assertAlmostEqual(got, want, places=12)
        self.assertEqual(edit.startAnchorDrag([b, a], Ray(V3(0, 0, 5), down), cam, a.pos.clone()).lead, 1, 'a copy of the position finds the anchor by value')
        self.assertEqual(edit.startAnchorDrag([b, a], Ray(V3(0, 0, 5), down), cam, V3(9, 9, 9)).lead, 0, 'unknown: the first')
        # no head: never on the head
        Q = edit.new_project(); B = curve(Q, [(0, 0, 0), (1, 0, 0)])
        dq = edit.startAnchorDrag([B.anchors[0]], Ray(V3(0, 0, 5), down), cam, B.anchors[0].pos)
        self.assertFalse(edit.dragOnHead(Q, dq, Ray(V3(0, 0, 5), down)))
        self.assertIsNone(edit.headPoint(Q, Ray(V3(0, 0, 5), down)))


class TestSelection(unittest.TestCase):
    def test_click_anchor(self):
        s = Selection([7], [])
        self.assertTrue(s.click_anchor(1))
        self.assertEqual((s.items, s.anchors), ([], [1]), 'a plain click: that anchor alone, the items cleared')
        self.assertTrue(s.click_anchor(2, shift=True)); self.assertEqual(s.anchors, [1, 2])
        self.assertTrue(s.click_anchor(1, shift=True)); self.assertEqual(s.anchors, [2, 1], 'Shift on a selected anchor makes it the last')
        self.assertTrue(s.click_anchor(2)); self.assertEqual(s.anchors, [1, 2], 'a plain click on a selected anchor keeps the selection, it becomes the last')
        self.assertFalse(s.click_anchor(2, ctrl=True)); self.assertEqual(s.anchors, [1], 'Ctrl toggles off: no drag')
        self.assertTrue(s.click_anchor(3, ctrl=True)); self.assertEqual(s.anchors, [1, 3])
        self.assertEqual(s.last_anchor, 3)
        self.assertTrue(s.click_anchor(9)); self.assertEqual(s.anchors, [9])

    def test_select_item_box_paint(self):
        s = Selection([], [1, 2])
        s.select_item(5)
        self.assertEqual((s.items, s.anchors), ([5], []))
        s.select_item(6, add=True); self.assertEqual(s.items, [5, 6])
        s.select_item(5, add=True); self.assertEqual(s.items, [6], 'Shift toggles')
        s.select_item(7, add=True); s.select_item(6, add=False); self.assertEqual(s.items, [6])
        s.box([3, 4]); self.assertEqual((s.items, s.anchors), ([], [3, 4]))
        s.box([4, 5], add=True); self.assertEqual(s.anchors, [3, 4, 5])
        s.box([], add=True); self.assertEqual(s.anchors, [3, 4, 5], 'an empty box with Shift changes nothing')
        s.box([]); self.assertEqual((s.items, s.anchors), ([], []), 'a click in the empty clears')
        self.assertFalse(s)
        self.assertEqual(s.paint_mode(8), 'add')
        self.assertTrue(s.paint([8, 9], 'add')); self.assertEqual(s.items, [8, 9])
        self.assertFalse(s.paint([8], 'add'))
        self.assertEqual(s.paint_mode(8), 'remove')
        self.assertTrue(s.paint([8, 42], 'remove')); self.assertEqual(s.items, [9])
        self.assertIsNone(s.last_anchor)

    def test_circle_and_prune(self):
        s = Selection([5], [])
        self.assertTrue(s.circle(anchor_ids=[1, 2])); self.assertEqual((s.items, s.anchors), ([], [1, 2]))
        self.assertFalse(s.circle(anchor_ids=[1])); self.assertTrue(s.circle(anchor_ids=[2, 3])); self.assertEqual(s.anchors, [1, 2, 3])
        self.assertTrue(s.circle(anchor_ids=[2], deselect=True)); self.assertEqual(s.anchors, [1, 3])
        self.assertFalse(s.circle(anchor_ids=[9], deselect=True))
        self.assertTrue(s.circle(curve_ids=[5, 6])); self.assertEqual((s.items, s.anchors), ([5, 6], []), 'curves clear the anchors')
        self.assertFalse(s.circle(curve_ids=[5]))
        self.assertTrue(s.circle(curve_ids=[6], deselect=True)); self.assertEqual(s.items, [5])
        self.assertFalse(s.circle())
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (1, 0, 0)])
        s = Selection([A.id, 99], [A.anchors[0].id, 1234])
        s.prune(P)
        self.assertEqual((s.items, s.anchors), ([A.id], [A.anchors[0].id]))
        c = s.copy(); c.items.append(1)
        self.assertEqual(s.items, [A.id])


if __name__ == '__main__':
    unittest.main()
