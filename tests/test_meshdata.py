"""hairmesh.core.meshdata (pure): what a grid must satisfy before Mesh.from_pydata, so a degenerate wall never
hands Blender a face it cannot take (15/10/2026: two crashes right after walls were made over odd curves)."""
import math
import unittest

from hairmesh.core.meshdata import clean_mesh, mesh_problems


class TestCleanMesh(unittest.TestCase):
    def test_a_good_grid_passes_untouched(self):
        pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
        faces = [[0, 1, 2, 3]]
        p, f, kept = clean_mesh(pos, faces)
        self.assertEqual((p, f, kept), (pos, faces, [0]))
        self.assertIsNot(f[0], faces[0], 'faces are copied')
        self.assertEqual(mesh_problems(pos, faces), [])

    def test_non_finite_vertices(self):
        pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (float('nan'), 1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 2.0, float('inf'))]
        faces = [[0, 1, 2, 3], [0, 1, 3], [1, 3, 4]]
        p, f, kept = clean_mesh(pos, faces)
        self.assertEqual(p[2], (0.0, 0.0, 0.0)); self.assertEqual(p[4], (0.0, 0.0, 0.0), 'zeroed, as validate would')
        self.assertEqual(f, [[0, 1, 3]]); self.assertEqual(kept, [1], 'faces touching a bad vertex go')
        self.assertEqual(mesh_problems(pos, faces), ['non-finite vertex 2', 'non-finite vertex 4'])

    def test_bad_indices_and_repeats(self):
        pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
        faces = [[0, 1, 4, 3], [0, 1, -1], [0, 1, 1, 2], [0, 1, 1], [0, 1, 2, 2, 3], [0, 1, 2.0]]
        p, f, kept = clean_mesh(pos, faces)
        self.assertEqual(f, [[0, 1, 2], [0, 1, 2, 3]]); self.assertEqual(kept, [2, 4], 'a repeat is dropped within the face; fewer than 3 distinct = no face')
        self.assertEqual(mesh_problems(pos, faces)[:2], ['face 0: index out of range', 'face 1: index out of range'])
        self.assertIn('face 2: repeated vertex', mesh_problems(pos, faces)); self.assertIn('face 3: fewer than 3 distinct vertices', mesh_problems(pos, faces))

    def test_empty(self):
        self.assertEqual(clean_mesh([], []), ([], [], []))
        self.assertTrue(all(math.isfinite(x) for p in clean_mesh([(float('-inf'), 0.0, 0.0)], [])[0] for x in p))


if __name__ == '__main__':
    unittest.main()
