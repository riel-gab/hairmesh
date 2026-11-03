import os
import sys
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSkeleton(unittest.TestCase):
    def test_core_imports_without_bpy(self):
        import hairmesh.core  # noqa: F401
        if 'bpy' not in sys.modules:
            # the core must not pull bpy in; this can only be checked outside Blender
            for name in list(sys.modules):
                if name.startswith('hairmesh.core'):
                    self.assertNotIn('bpy', sys.modules)

    def test_manifest(self):
        with open(os.path.join(ROOT, 'hairmesh', 'blender_manifest.toml'), 'rb') as f:
            m = tomllib.load(f)
        self.assertEqual(m['id'], 'hairmesh')
        self.assertEqual(m['type'], 'add-on')
        self.assertGreaterEqual(tuple(int(x) for x in m['blender_version_min'].split('.')), (5, 1, 0))
        # the credit to the original (the add-on's description in the extensions list); Blender's rules for a
        # tagline: at most 64 characters, ending in a letter, a digit or a closing bracket
        t = m['tagline']
        self.assertEqual(t, 'Port of Stylized HairMesh Creator by catarackta')
        self.assertLessEqual(len(t), 64)
        self.assertTrue(t[-1].isalnum() or t[-1] in ')]}')

    @unittest.skipUnless('bpy' in sys.modules, 'needs Blender')
    def test_register_unregister_in_blender(self):
        import hairmesh
        hairmesh.register()
        hairmesh.unregister()
