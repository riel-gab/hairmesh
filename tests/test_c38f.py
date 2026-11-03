"""3.8f: the dot of the first click of the wall cut (scissors.leadHover, ours). The core alone; the hover with real
events and its cost: tests/test_sim_c38f.py."""
import unittest

from hairmesh.core import edit, scene, scissors as SC
from hairmesh.core.vec3 import Vec3


def ortho(p):
    """x right, y up, 10 px per unit, origin at (100, 100); z = 0 (in front)"""
    return (100 + p.x * 10, 100 + p.y * 10, 0.0)


class _G:
    width = 5.0


class _Sf:
    name = 'Surface 1'; id = -1


class TestLead(unittest.TestCase):
    def setUp(self):
        P = edit.new_project()
        A = edit.makeCurve(P, None)
        for p in ((0, 0, 0), (4, 0, 0), (8, 0, 0)):
            A.anchors.append(edit.newAnchor(P, Vec3(*p)))
        B = edit.makeCurve(P, None)
        for p in ((0, 5, 0), (4, 5, 1), (8, 5, 0)):
            B.anchors.append(edit.newAnchor(P, Vec3(*p)))
        scene.settle(P)
        self.P, self.A, self.B = P, A, B
        self.cut = SC.SurfCut(_Sf(), A, B, _G())

    def hover(self, m):
        return SC.surfCutHover(self.P, self.cut, m, ortho, None)

    def test_dot_on_the_nearer_curve(self):
        self.assertTrue(self.hover((122, 103)))                    # 3 px above A at x = 2.2: a new point there
        L = self.cut.lead
        self.assertEqual(L['side'], 0); self.assertIsNone(L['a'])
        self.assertAlmostEqual(L['pos'].y, 0.0, places=6); self.assertAlmostEqual(L['pos'].x, 2.2, delta=0.05)
        self.assertTrue(self.hover((122, 147)))                    # 3 px below B
        self.assertEqual(self.cut.lead['side'], 1)

    def test_an_anchor_within_10_px(self):
        self.hover((143, 102))                                     # 3.6 px from the anchor (4, 0)
        self.assertIs(self.cut.lead['a'], self.A.anchors[1])
        self.assertEqual(self.cut.lead['pos'].toArray(), [4, 0, 0])

    def test_out_of_reach_none(self):
        self.hover((122, 103))
        self.assertTrue(self.hover((140, 125)), 'gone: a change')
        self.assertIsNone(self.cut.lead)
        self.assertFalse(self.hover((141, 125)), 'still none: no redraw')
        self.assertNotIn('lead', [k for _p, k in SC.surfCutView(self.P, self.cut)[1]])

    def test_same_place_no_redraw(self):
        self.hover((122, 103))
        self.assertFalse(self.hover((122, 103)))

    def test_the_click_lands_on_the_dot(self):
        for m in ((122, 103), (143, 102), (161, 148)):
            self.cut = SC.SurfCut(_Sf(), self.A, self.B, _G())
            self.hover(m)
            L = self.cut.lead
            dots = SC.surfCutView(self.P, self.cut)[1]
            self.assertEqual([(p.toArray(), k) for p, k in dots], [(L['pos'].toArray(), 'lead')])
            uid = self.P.uid
            self.assertEqual(SC.surfCutDown(self.P, self.cut, m, 0, False, ortho, None), {})
            S = self.cut.start
            self.assertEqual((S['side'], S['a'], S['pos'].toArray(), S.get('seg'), S.get('u')),
                             (L['side'], L['a'], L['pos'].toArray(), L.get('seg'), L.get('u')))
            if L['a'] is None:
                self.assertEqual((S['v'].id, self.P.uid), (uid, uid + 1), 'the hover took no id: the click\'s is the original\'s')

    def test_the_hover_takes_no_id(self):
        uid = self.P.uid
        for m in ((122, 103), (123, 103), (161, 148), (140, 125)):
            self.hover(m)
        self.assertEqual(self.P.uid, uid)

    def test_backspace_clears_the_dot(self):
        self.hover((122, 103))
        SC.surfCutDown(self.P, self.cut, (122, 103), 0, False, ortho, None)
        SC.removeLast(self.cut)
        self.assertIsNone(self.cut.start); self.assertIsNone(self.cut.lead)


if __name__ == '__main__':
    unittest.main()
