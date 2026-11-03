"""docs/shortcuts.md (public/docs/ here) is what tools/gen_shortcuts_doc.py makes of the code: a key changed in the
keymaps, a modal's keys or a sub-tool fails here until the generator runs again."""
import importlib.util
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def generator():
    spec = importlib.util.spec_from_file_location('gen_shortcuts_doc', os.path.join(ROOT, 'tools', 'gen_shortcuts_doc.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(bpy, 'needs Blender')
class TestShortcutsDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        cls.hairmesh = hairmesh
        bpy.ops.wm.read_factory_settings(use_empty=True)
        try:
            hairmesh.register()
        except ValueError:
            pass

    @classmethod
    def tearDownClass(cls):
        cls.hairmesh.unregister()

    def test_the_page_is_what_the_code_gives(self):
        gen = generator()
        path = gen.target()
        self.assertTrue(os.path.isfile(path), f'{path} is missing: run tools/gen_shortcuts_doc.py')
        with open(path, encoding='utf-8') as f:
            self.assertEqual(f.read(), gen.render(), 'shortcuts.md is out of date: run tools/gen_shortcuts_doc.py')

    def test_every_key_of_the_tables_is_on_the_page(self):
        from hairmesh.blender import keymaps
        gen = generator()
        text = gen.render()
        keys = {line.split('|')[1].strip() for line in text.splitlines() if line.startswith('| ') and not line.startswith('| Key ')}
        self.assertIn('Shift+Alt+H', keys)
        self.assertIn('LMB Double-Click', keys)
        self.assertIn('1', keys)
        for idname, event, _p in keymaps.TOOL_ITEMS + keymaps.GENERIC_ITEMS:
            twin = event.get('value') == 'DOUBLE_CLICK' and idname in keymaps.DOUBLE_PRESS   # (one row with its press)
            if event['type'] not in ('MOUSEMOVE', 'NONE') and not twin:
                self.assertIn(gen.key_label(event), keys)
        self.assertFalse([k for k in keys if k.startswith('Any ')], 'the "any modifier" of an item is not a key')

    def test_every_tool_key_says_when_it_acts(self):
        from hairmesh.blender import keymaps
        gen = generator()
        tool_ids = {i for i, _e, _p in keymaps.TOOL_ITEMS}
        for idname in tool_ids:
            known = idname in gen.WHEN or idname in gen.ANY_SUBTOOL or idname.startswith('hairmesh.subtool_')
            self.assertTrue(known, f'{idname}: a new tool key — say when it acts (WHEN or ANY_SUBTOOL in tools/gen_shortcuts_doc.py)')
        for idname in set(gen.WHEN) - {'hairmesh.toggle_tool'}:
            self.assertIn(idname, tool_ids, f'{idname}: no longer a tool key')


if __name__ == '__main__':
    unittest.main()
