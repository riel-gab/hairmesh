"""Lot 4.7b -> 4.8, item 4 (the owner's decision, ours): a Profile modifier's own loop step (t.p.step; none or 0: it
inherits its group's) — Convert to curves & walls gives the lock that step (at the root: its new group's step; in a
group: a group of its own inside it), the live preview shows the loops the converted lock will have (tube.tubeRows)
instead of rebuildTube's 96 rings, and with the step inherited everything is bit for bit as before (the parity of
tests/test_tube_parity.py, test_extras_parity.py and test_profile_parity.py stands as it was)."""
import unittest

from hairmesh.core import curves as CM, edit, tube as TB
from hairmesh.core.groups import projectStep
from hairmesh.core.vec3 import V3
from tests.helpers.script import settle_frame


def _project(in_group=True, step=0.05):
    P = edit.new_project()
    g = edit.addItem(P, 'group', None) if in_group else None
    if g is not None:
        g.name = 'Hair'; g.conv = {'step': step}
    c = edit.makeCurve(P, g.id if g is not None else None)
    for q in ((0.1, 1.0, 0.2), (0.12, 0.85, 0.26), (0.16, 0.7, 0.3), (0.2, 0.55, 0.31)):
        c.anchors.append(edit.newAnchor(P, V3(*q)))
    CM.rebuild_dirty(P)
    (t,), _m = TB.applyModifier(P, [c.id], TB.builtinProfile('Quad'))
    TB.rebuildTube(P, t)
    return P, g, c, t


def _lock_rows(P, ctx, curve_ids):
    """the number of loops of the lock of these curves in the row tables"""
    for e in ctx.LOCK_CACHE.values():
        L = e['L']
        if {c.id for c in L.cs} & set(curve_ids):
            return len(ctx.T[L.cs[0].id].rows)
    return None


class TestTubeStep(unittest.TestCase):
    def test_inherited(self):
        P, g, c, t = _project()
        self.assertEqual(TB.tubeStep(P, t), 0.05, 'its group\'s step')
        P2, _g, _c, t2 = _project(in_group=False)
        self.assertEqual(TB.tubeStep(P2, t2), projectStep(P2), 'at the root: the project\'s step its new group gets (6777)')

    def test_own_step_at_the_root(self):
        P, _g, c, t = _project(in_group=False)
        t.p = {**t.p, 'step': 0.08}
        made, _msg = TB.convertTube(P, t)
        lock_g = P.parentOf(made[0])
        self.assertEqual((lock_g.name, lock_g.conv['step']), (c.name + ' lock', 0.08))

    def test_own_step_in_a_group(self):
        P, g, c, t = _project()
        t.p = {**t.p, 'step': 0.12}
        made, _msg = TB.convertTube(P, t)
        lg = P.parentOf(made[0])
        self.assertEqual((lg.name, lg.parent, lg.conv['step']), (c.name + ' lock', g.id, 0.12), 'a group of its own inside the curve\'s')
        self.assertTrue(all(P.parentOf(e) is lg for e in made))
        P2, g2, c2, t2 = _project()
        made2, _m = TB.convertTube(P2, t2)
        self.assertIs(P2.parentOf(made2[0]), g2, 'inherited: the edges go into the curve\'s group, as the original')

    def test_a_root_curve_with_the_first_id(self):
        """the review of the lot: a fresh project's first curve (id 1) at the root — the preview's project must not give
        its group an id the copies hold"""
        P, _g, c, t = _project(in_group=False)
        self.assertEqual(c.id, 1)
        fs = TB.tubeRows(P, t)
        self.assertIsNotNone(fs)
        made, _m = TB.convertTube(P, t)
        ctx = settle_frame(P)
        self.assertEqual(len(fs), _lock_rows(P, ctx, [e.id for e in made]))

    def test_the_preview_shows_the_loops_convert_makes(self):
        for step in (0.05, 0.12, 0.2):
            P, g, c, t = _project(step=step)
            fs = TB.tubeRows(P, t)
            made, _m = TB.convertTube(P, t)
            ctx = settle_frame(P)
            n = _lock_rows(P, ctx, [e.id for e in made])
            self.assertEqual(len(fs), n, f'step {step}: the preview\'s loops = the converted lock\'s')
        P, g, c, t = _project(step=0.05)
        t.p = {**t.p, 'step': 0.15}
        TB.rebuildTube(P, t)
        fs = TB.tubeRows(P, t)
        made, _m = TB.convertTube(P, t)
        ctx = settle_frame(P)
        self.assertEqual(len(fs), _lock_rows(P, ctx, [e.id for e in made]), 'its own step: the same')
        self.assertLess(len(fs), 96, 'fewer than the 96 rings of the display')
        self.assertEqual((fs[0], round(fs[-1], 6)), (0.0, 1.0), 'from the root to the tip')


if __name__ == '__main__':
    unittest.main()
