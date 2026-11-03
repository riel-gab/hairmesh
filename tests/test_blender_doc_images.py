"""The images of the documentation still show what the code does: the steps of Getting Started (tools/doc_tutorial.py)
run here without a window, and the state measured after each one is the .json written beside its image when the
images were taken (tools/gen_doc_images.py). A change of the add-on that moves a pie item, renames a label or changes
what a step leaves fails here until the images are taken again; tests/test_doc_claims.py then checks the text."""
import contextlib
import importlib.util
import json
import os
import unittest

try:
    import bpy
except ImportError:
    bpy = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pub = os.path.join(ROOT, 'public')
IMG = os.path.join(_pub if os.path.isdir(_pub) else ROOT, 'docs', 'img')


def tutorial():
    spec = importlib.util.spec_from_file_location('doc_tutorial', os.path.join(ROOT, 'tools', 'doc_tutorial.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def saved(name):
    with open(os.path.join(IMG, name + '.json'), encoding='utf-8') as f:
        return json.load(f)


@unittest.skipUnless(bpy, 'needs Blender')
class TestDocImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hairmesh
        cls.hairmesh = hairmesh
        bpy.ops.wm.read_factory_settings()
        try:
            hairmesh.register()
        except ValueError:
            pass

    @classmethod
    def tearDownClass(cls):
        cls.hairmesh.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def check(self, T, name, pie):
        got = json.loads(json.dumps(T.measure(bpy.context, pie)))   # (as the .json has it: lists, null)
        with self.subTest(image=name):
            self.assertEqual(got, saved(name), f'{name}: the code no longer gives what the image shows; run tools/gen_doc_images.py')

    def test_the_steps_give_what_the_images_show(self):
        T = tutorial()
        ov = contextlib.nullcontext
        shots = 0
        for step, _what, key, shot in T.STEPS:
            if key is not None:
                T.press_headless(key)
            T.run(step, ov, headless=True)
            if shot is not None:
                name, kind = shot
                self.check(T, name, 'main' if kind == 'pie' else None)
                shots += 1
        from hairmesh.core import conversion as CONV
        if CONV.viewOf(T.project()) != 'live':
            T.ok(T.view_op('live'), 'Live')
        for name, _key, pie in T.PAGE_SHOTS:
            if name == 'subtool-pie':
                T.ok(bpy.ops.hairmesh.select(deselect_all=True), 'Deselect')
            else:
                T.select_curves(ov)
            self.check(T, name, pie)
            shots += 1
        self.assertGreater(shots, 0)


if __name__ == '__main__':
    unittest.main()
