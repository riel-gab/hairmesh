"""G10: a modal says its mode and values in the header and its keys in the status bar (blender/modal_keys.py). Real status bars:
tools/sim_ui_look.py."""
import importlib
import unittest

try:
    import bpy
except ImportError:
    bpy = None

MODULES = ('brush', 'ops_crown', 'ops_draw', 'ops_gizmo', 'ops_guides', 'ops_scissors', 'ops_select', 'ops_transform', 'profile_editor')


def key_lists(mod):
    """every key tuple a module keeps: (name, keys)"""
    for name in dir(mod):
        if name.endswith('KEYS') and name.isupper() and name != 'KEY_ICONS':
            v = getattr(mod, name)
            if isinstance(v, dict):
                yield from ((f'{name}[{k}]', x) for k, x in v.items())
            else:
                yield name, v


@unittest.skipUnless(bpy, 'needs Blender')
class TestModalKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        from hairmesh.blender import live, modal_keys
        cls.hairmesh, cls.live, cls.mk = hairmesh, live, modal_keys
        cls.mods = [importlib.import_module(f'hairmesh.blender.{m}') for m in MODULES]
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            hairmesh.register()
        except ValueError:
            pass

    @classmethod
    def tearDownClass(cls):
        try:
            cls.hairmesh.unregister()
        except Exception:
            pass

    def test_every_list_of_keys_is_well_made(self):
        icons = set(bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items.keys())
        seen = 0
        for mod in self.mods:
            for name, keys in key_lists(mod):
                if name in ('ZONE_KEYS',) or not isinstance(keys, tuple) or all(isinstance(x, str) for x in keys):
                    continue                                        # (ops_guides.ZONE_KEYS: slider names; ops_transform.AXIS_KEYS: key names)
                seen += 1
                for entry in keys:
                    self.assertEqual(len(entry), 2, (mod.__name__, name, entry))
                    names, what = entry
                    self.assertIsInstance(names, tuple, (mod.__name__, name))
                    self.assertIsInstance(what, str)
                    self.assertTrue(what[0].isupper() or what[0].isdigit(), (name, what))
                    self.assertNotIn('…', what)
        self.assertGreaterEqual(seen, 15)
        for icon in self.mk.KEY_ICONS.values():
            self.assertIn(icon, icons)

    def test_text_and_the_state(self):
        keys = ((('LMB', 'Enter'), 'Confirm'), (('Esc',), 'Cancel'))
        self.assertEqual(self.mk.keys_text(keys), 'LMB / Enter: Confirm · Esc: Cancel')
        area = type('Area', (), {'header_text_set': lambda self, t: said.append(t)})()
        said = []
        self.live.header(area, 'HairMesh Draw', keys)
        self.assertEqual(self.mk._keys[0], keys)
        self.assertIsNone(self.mk.current(), 'no modal runs: the status bar goes back')
        self.live.header(area, 'x', keys)
        self.live.header(area, None)
        self.assertIsNone(self.mk._keys[0], 'text None: the two go back')
        self.assertEqual(said[-1], None)


if __name__ == '__main__':
    unittest.main()
