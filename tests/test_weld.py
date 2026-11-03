"""Phase 3.4, pure core (hairmesh.core.weld): the weld candidate on screen, checkWeld's decision, welding with a Y
extension, joining, unwelding, splitting, merging, the new curves from anchors, rotate junction, optimize.
The bit-for-bit comparison with the original is in test_script_parity (script-weld.json, script-optimize.json);
here the rules are checked one by one on small scenes."""
import unittest

from hairmesh.core import curves as CM, edit, weld as W
from hairmesh.core.selection import Selection
from hairmesh.core.vec3 import V3
from tests.test_anchors import curve


def screen_xy(p):
    """a projector that drops z: x, y are the screen coordinates"""
    return (p.x, p.y)


class TestCandidate(unittest.TestCase):
    def test_find_weld_candidate_and_choice(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (10, 10, 0), (20, 20, 0)])
        B = curve(P, [(30, 0, 0), (25, 5, 0), (21, 19, 0)])         # B's end 1.4 px from A's end
        C = curve(P, [(50, 0, 0), (60, 10, 0)])
        lst = edit.anchorList(P)
        a = B.anchors[2]
        self.assertIs(W.findWeldCandidate(P, a, lst, screen_xy), A.anchors[2])
        self.assertIsNone(W.findWeldCandidate(P, B.anchors[1], lst, screen_xy), 'nothing within 14 px')
        self.assertIsNone(W.findWeldCandidate(P, A.anchors[1], lst, lambda p: None), 'behind the camera: no candidate')
        D = curve(P, [(0, 0, 5), A.anchors[1]])                     # d0 shares a curve with a1: a1 is never its candidate, a0 (same screen spot) is
        self.assertIsNone(W.findWeldCandidate(P, D.anchors[0], [A.anchors[1]], screen_xy))
        self.assertIs(W.findWeldCandidate(P, D.anchors[0], [A.anchors[1], A.anchors[0]], screen_xy), A.anchors[0])
        # the decision: two ends -> the usual weld; a start on an end -> "Start meets end"
        self.assertEqual(W.weld_choice(P, a, A.anchors[2]), ('weld',))
        self.assertEqual(W.weld_choice(P, C.anchors[0], A.anchors[2]), ('startEnd', C, A))
        self.assertEqual(W.weld_choice(P, A.anchors[2], C.anchors[0]), ('startEnd', C, A))
        self.assertEqual(W.weld_choice(P, A.anchors[1], C.anchors[0]), ('weld',), 'a middle anchor is no sole end')
        self.assertEqual(W.soleEnd(P, A.anchors[0]), (A, True)); self.assertIsNone(W.soleEnd(P, A.anchors[1]))
        self.assertIsNone(W.soleEnd(P, A.anchors[1]))
        self.assertTrue(W.sharesCurve(P, A.anchors[0], A.anchors[2])); self.assertFalse(W.sharesCurve(P, A.anchors[0], B.anchors[0]))


class TestWeld(unittest.TestCase):
    def test_weld_extends_a_y_branch(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (0, 30, 0), (0, 60, 0), (0, 90, 0), (0, 120, 0)])
        B = curve(P, [(40, 20, 0), (20, 45, 0), (1, 61, 0)])
        CM.rebuild_dirty(P)
        ext = W.weld(P, B.anchors[2], A.anchors[2])
        self.assertEqual(ext, [B.name], 'B is extended along the longer curve (Y)')
        self.assertEqual([a.id for a in B.anchors], [B.anchors[0].id, B.anchors[1].id, A.anchors[2].id, A.anchors[3].id, A.anchors[4].id], 'the trunk side away from B\'s free end')
        self.assertTrue(all(c.dirty for c in P.curves()))
        self.assertNotIn(ext[0], [c.name for c in P.curves() if c is not B])
        # a weld of two ends: nothing to extend
        C = curve(P, [(100, 0, 0), (100, 50, 0)]); D = curve(P, [(101, 51, 0), (100, 100, 0)])
        CM.rebuild_dirty(P)
        self.assertEqual(W.weld(P, D.anchors[0], C.anchors[1]), [])
        self.assertIs(D.anchors[0], C.anchors[1])

    def test_reverse_then_weld_and_join(self):
        P = edit.new_project()
        C = curve(P, [(20, 90, 0), (30, 50, 0), (40, 10, 0)])      # its START meets D's end
        D = curve(P, [(0, 0, 0), (5, 40, 0), (18, 88, 0)])
        D.anchors[1].smooth = 0.3
        CM.rebuild_dirty(P)
        kind, st, other = W.weld_choice(P, C.anchors[0], D.anchors[2])
        self.assertEqual((kind, st, other), ('startEnd', C, D))
        W.reverseThenWeld(P, C.anchors[0], D.anchors[2], st, other)
        self.assertIs(C.anchors[2], D.anchors[2], 'C reversed, its (old) start welded into D\'s end')
        self.assertEqual(D.anchors[1].smooth, 1, 'both recalculated right away')
        # join: E's end on F's start
        E = curve(P, [(100, 0, 0), (100, 50, 0)]); F = curve(P, [(101, 51, 0), (120, 90, 0), (130, 130, 0)])
        G = curve(P, [(200, 0, 0), (200, 100, 0)])
        s = edit.addItem(P, 'surface', None, {'a': F.id, 'b': G.id, 'p': {'segs': 1}})
        CM.rebuild_dirty(P)
        ce = W.joinCurves(P, E.anchors[1], F.anchors[0])
        self.assertIs(ce, E); self.assertNotIn(F.id, P.items)
        self.assertEqual(len(E.anchors), 4); self.assertEqual((s.a, s.b), (E.id, G.id))
        # not a start-end pair: the fallback is a plain weld
        H = curve(P, [(300, 0, 0), (300, 50, 0)]); I = curve(P, [(400, 0, 0), (301, 51, 0)])
        CM.rebuild_dirty(P)
        self.assertIsNone(W.joinCurves(P, H.anchors[1], I.anchors[1])); self.assertIs(H.anchors[1], I.anchors[1])

    def test_unweld_split_merge(self):
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (0, 30, 0), (0, 60, 0), (0, 90, 0)])
        B = curve(P, [(40, 20, 0), A.anchors[1], A.anchors[2], A.anchors[3]])
        C = curve(P, [(60, 20, 0), A.anchors[2]])
        a1 = A.anchors[1]
        self.assertIsNone(W.unweldAnchor(P, a1, []), 'no selected curve: the usual Ctrl + click')
        self.assertIsNone(W.unweldAnchor(P, A.anchors[0], [A.id]), 'not shared')
        kept = W.unweldAnchor(P, a1, [B.id])
        self.assertEqual(kept, [B]); self.assertIsNot(B.anchors[1], a1); self.assertEqual(B.anchors[1].pos.toArray(), a1.pos.toArray())
        self.assertIs(A.anchors[1], a1)
        kept = W.unweldAnchor(P, A.anchors[2], [A.id, B.id, C.id])
        self.assertEqual(kept, [B, C], 'all users selected: the first keeps the old one')
        # split: A through its anchor 2 (B now has its own anchors there... re-weld a trunk first)
        P = edit.new_project()
        H = curve(P, [(0, 0, 0), (0, 30, 0), (0, 60, 0), (0, 90, 0), (0, 120, 0)])
        I = curve(P, [(60, 10, 0), (40, 40, 0), H.anchors[2], H.anchors[3], H.anchors[4]])
        s = edit.addItem(P, 'surface', None, {'a': H.id, 'b': I.id, 'p': {'segs': 1}})
        CM.rebuild_dirty(P)
        out = W.splitAtAnchor(P, H.anchors[3])
        parts = [c for c in P.curves() if c.name.endswith(' part')]
        self.assertEqual(len(parts), 1, 'identical second pieces merged into one curve')
        self.assertEqual(sorted(out), sorted([H.id, I.id, parts[0].id]))
        self.assertEqual(len(H.anchors), 4); self.assertEqual(len(I.anchors), 4); self.assertEqual(len(parts[0].anchors), 2)
        self.assertEqual([(x.a, x.b) for x in P.surfaces()], [(H.id, I.id)], 'the wall between the first pieces stays; the one between the merged second pieces goes')
        self.assertEqual(W.splitAtAnchor(P, H.anchors[0]), [], 'no curve runs through an end')
        # merge coincident
        J = curve(P, [(100, 0, 0), (100, 40, 0)]); K = curve(P, [(100, 40, 0), (140, 60, 0)]); L = curve(P, [(100, 40, 0.5), (160, 10, 0)])
        self.assertEqual(W.mergeCoincident(P, [J, K, L], 0.1), 1); self.assertIs(K.anchors[0], J.anchors[1]); self.assertIsNot(L.anchors[0], J.anchors[1])
        self.assertEqual(W.mergeCoincident(P, [J, K], 0.1), 0)

    def test_curves_from_anchors_and_junction(self):
        P = edit.new_project()
        L = curve(P, [(0, 0, 0), (0, 40, 0), (0, 80, 0), (0, 120, 0)])
        M = curve(P, [(60, 0, 0), (60, 40, 0), (60, 80, 0), (60, 120, 0)])
        CM.rebuild_dirty(P)
        c, shared = W.curveBetween(P, L.anchors[1], M.anchors[2])
        self.assertEqual([a is x for a, x in zip(c.anchors, [L.anchors[0], L.anchors[1], None, M.anchors[2], M.anchors[3]])], [True, True, False, True, True])
        self.assertEqual(c.anchors[2].pos.toArray(), [30, 60, 0]); self.assertEqual(shared, [L.name, M.name])
        self.assertIsNone(W.curveBetween(P, L.anchors[0], L.anchors[0]))
        c2, shared = W.curveBetween(P, L.anchors[2], L.anchors[3])
        self.assertEqual(shared, [L.name], 'both on one curve: only the root part')
        t = W.curveThroughAnchors(P, [L.anchors[0], M.anchors[0], M.anchors[0], L.anchors[3]])
        self.assertEqual(len(t.anchors), 3, 'a repeated place is skipped'); self.assertIsNone(t.parent)
        self.assertTrue(all(a not in L.anchors and a not in M.anchors for a in t.anchors), 'welded to nothing')
        self.assertIsNone(W.curveThroughAnchors(P, [L.anchors[0]]))
        # rotate junction: a crossing P x Q with R ending there
        P2 = edit.new_project()
        Pc = curve(P2, [(0, 0, 0), (5, 40, 0), (10, 80, 0)])
        Q = curve(P2, [(-30, 30, 0), Pc.anchors[1], (50, 60, 0)])
        R = curve(P2, [(30, 100, 0), Pc.anchors[1]])
        j = Pc.anchors[1]
        ch = W.junctionChoices(P2, j)
        self.assertEqual([lbl for lbl, _p in ch], [f'{Pc.name} — smooth through', f'{Q.name} — smooth through', f'{R.name} — smooth through'])
        self.assertEqual(ch[0][1], [Pc.anchors[0].id, Pc.anchors[2].id])
        W.setPass(P2, j, ch[2][1])
        self.assertEqual(j.pass_[0], R.anchors[0].id)
        first = j.pass_[1]
        ch = W.junctionChoices(P2, j)
        self.assertEqual(ch[len(ch) - 1][0], '↺ Automatic')
        W.setPass(P2, j, ch[2][1])
        self.assertNotEqual(j.pass_[1], first, 'again: the next straightest direction')
        self.assertIsNotNone(CM.passRule(P2, Pc, 1))
        W.setPass(P2, j, None); self.assertIsNone(j.pass_)
        self.assertEqual(W.junctionChoices(P2, Pc.anchors[0]), [], 'fewer than 3 directions')

    def test_optimize(self):
        import math
        P = edit.new_project()
        pts = [(60 * math.cos(math.radians(k * 7.5)), 60 * math.sin(math.radians(k * 7.5)), 0) for k in range(13)]
        U = curve(P, pts)
        V = curve(P, [(100, 0, 0), (100, 50, 0), (130, 50, 0), (130, 100, 0)])   # a zigzag: nothing can go
        CM.rebuild_dirty(P)
        n = W.optimizeAnchors(P, [U, V])
        self.assertGreater(n, 5); self.assertLess(len(U.anchors), 13); self.assertEqual(len(V.anchors), 4)
        self.assertIs(U.anchors[0], P.anchors[min(a.id for a in U.anchors)])
        self.assertEqual(W.optimizeAnchors(P, [U]), 0, 'nothing more to remove without changing the shape')
        # only: just the given anchors may go
        Z = curve(P, [(200, 0, 0), (200.5, 15, 0), (201, 30, 0), (201.6, 45, 0), (202.1, 60, 0), (202.6, 75, 0), (203, 90, 0)])
        z = list(Z.anchors)
        CM.rebuild_dirty(P)
        self.assertEqual(W.optimizeAnchors(P, [Z], {z[2], z[3]}), 2)
        self.assertEqual(Z.anchors, [z[0], z[1], z[4], z[5], z[6]])
        # a fork stays, an attached anchor stays
        P = edit.new_project()
        X1 = curve(P, [(0, 0, 0), (1, 20, 0), (2, 40, 0), (20, 70, 0), (30, 100, 0)])
        X2 = curve(P, [(-30, 30, 0), X1.anchors[2], X1.anchors[3]])
        CM.rebuild_dirty(P)
        x = list(X1.anchors)
        W.optimizeAnchors(P, [X1])
        self.assertIn(x[2], X1.anchors, 'the fork anchor stays (X2 has other neighbours there)'); self.assertIn(x[3], X1.anchors)
        self.assertEqual((X1.anchors[0], X1.anchors[len(X1.anchors) - 1]), (x[0], x[4]), 'the ends stay')
        self.assertEqual(X2.anchors[1:], [x[2], x[3]], 'X2 untouched')
        self.assertEqual(W.distToSeg(V3(0, 1, 0), V3(-1, 0, 0), V3(1, 0, 0)), 1.0)

    def test_selection_after_the_edits(self):
        """what the callers do with the selection: unweld leaves it, a weld selects the kept anchor, a split selects
        the pieces, a new curve selects itself"""
        P = edit.new_project()
        A = curve(P, [(0, 0, 0), (0, 30, 0), (0, 60, 0)]); B = curve(P, [(40, 20, 0), (1, 61, 0)])
        CM.rebuild_dirty(P)
        sel = Selection([], [B.anchors[1].id])
        W.weld(P, B.anchors[1], A.anchors[2])
        sel = Selection([], [A.anchors[2].id]).prune(P)
        self.assertEqual(sel.anchors, [A.anchors[2].id])
        self.assertEqual(Selection([], [999]).prune(P).anchors, [], 'the welded-away anchor is gone from the project')


if __name__ == '__main__':
    unittest.main()
