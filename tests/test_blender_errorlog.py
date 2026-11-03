"""The error log (lot 4.9b, item 6b), in -b: an operator, a modal step, a draw handler and a timer raising leave their
entries with the traceback; without HAIRMESH_ERROR_LOG, no file and nothing installed. -b has no draw loop nor timers: the
handler and the timer are reached by what Blender does with them (the hook PyErr_Print calls); test_sim_errorlog runs them
for real."""
import os
import sys
import tempfile
import unittest

try:
    import bpy
except ImportError:
    bpy = None

FAKE = os.path.join('somewhere', 'hairmesh', 'blender', 'a_module_of_the_test.py')


def raised(body, filename=FAKE):
    """the exc_info of `body`, raised in a function whose file is `filename`"""
    ns = {}
    exec(compile(f'def boom():\n    {body}\n', filename, 'exec'), ns)
    try:
        ns['boom']()
    except Exception:
        return sys.exc_info()


@unittest.skipIf(bpy is None, 'needs Blender')
class TestErrorLog(unittest.TestCase):
    def setUp(self):
        from hairmesh.blender import errorlog
        self.E = errorlog
        self.file = os.path.join(tempfile.mkdtemp(prefix='hairmesh-errlog-'), 'errors.log')
        self.keep = (errorlog.PATH, sys.excepthook)
        errorlog.unregister()
        errorlog.PATH = self.file
        errorlog.register()

    def tearDown(self):
        self.E.unregister()
        self.E.PATH = self.keep[0]
        sys.excepthook = self.keep[1]
        self.E.register()

    def wheres(self):
        return [e['where'] for e in self.E.entries()]

    def test_an_operator_through_the_slow_log(self):
        from hairmesh.blender import slow

        class HAIRMESH_OT_err_test(bpy.types.Operator):
            bl_idname = 'hairmesh.err_test'
            bl_label = 'Error test'

            def execute(self, context):
                raise ValueError('operator boom')
        bpy.utils.register_class(HAIRMESH_OT_err_test)
        try:
            slow._wrap_method(HAIRMESH_OT_err_test, 'execute', 'hairmesh.err_test')
            with self.assertRaises(RuntimeError):
                bpy.ops.hairmesh.err_test()
        finally:
            bpy.utils.unregister_class(HAIRMESH_OT_err_test)
        got = self.E.entries()
        self.assertEqual([e['where'] for e in got], ['hairmesh.err_test'])
        self.assertIn('ValueError: operator boom', got[0]['text'])
        self.assertIn('Traceback', got[0]['text'])

    def test_a_modal_step(self):
        from hairmesh.blender import slow

        class Fake:
            def modal(self, context, event):
                raise KeyError('modal boom')
        slow._wrap_method(Fake, 'modal', 'hairmesh.fake (a modal step)')
        with self.assertRaises(KeyError):
            Fake().modal(None, None)
        self.assertEqual(self.wheres(), ['hairmesh.fake (a modal step)'])
        self.assertIn('modal boom', self.E.entries()[0]['text'])

    def test_a_draw_handler_that_swallows_it(self):
        from hairmesh.blender import draw
        real = draw._outline_batches

        def boom(*_a):
            raise ValueError('outline boom')
        draw._outline_batches = boom
        draw._outline_error.clear()
        try:
            draw._draw_mesh_outlines(None, None, None, None, None, 1, 1)
        finally:
            draw._outline_batches = real
            draw._outline_error.clear()
        self.assertEqual(self.wheres(), ['draw: mesh outline'])
        self.assertIn('outline boom', self.E.entries()[0]['text'])

    def test_a_draw_handler_or_a_timer_that_lets_it_through(self):
        for what in ('draw boom', 'timer boom'):
            sys.excepthook(*raised(f'raise ValueError("{what}")'))
        self.assertEqual(len(self.E.entries()), 2)
        self.assertIn('timer boom', self.E.entries()[1]['text'])

    def test_another_add_ons_is_not_ours(self):
        sys.excepthook(*raised('raise ValueError("not ours")', os.path.join('addons', 'other', 'x.py')))
        self.assertEqual(self.E.entries(), [])

    def test_the_same_place_failing_every_redraw(self):
        exc = None
        for i in range(10):
            exc = raised('raise ValueError("again")')
            self.E.record('draw: guides', exc)
        self.assertEqual(len(self.E.entries()), self.E.MAX_SAME)

    def test_one_exception_on_its_way_out_is_one_entry(self):
        exc = raised('raise ValueError("once")')
        self.E.record('the first catch', exc)
        sys.excepthook(*exc)
        self.assertEqual(self.wheres(), ['the first catch'])

    def test_entries_from_a_position(self):
        self.E.record('one', raised('raise ValueError("1")'))
        at = self.E.size()
        self.E.record('two', raised('raise ValueError("2")'))
        self.assertEqual([e['where'] for e in self.E.entries(start=at)], ['two'])

    def test_off_without_the_variable(self):
        self.E.unregister()
        self.E.PATH = None
        hook = sys.excepthook
        self.E.register()
        self.assertIs(sys.excepthook, hook, 'nothing installed')
        self.E.record('x', raised('raise ValueError("no file")'))
        self.assertFalse(os.path.exists(self.file))
        self.assertEqual(self.E.entries(), [])
