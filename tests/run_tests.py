"""Runs the HairMesh test suite (unittest, no extra dependencies).

Three ways to run it:

  # pure-core tests with the Python bundled in Blender (tests that need bpy are skipped)
  "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Blender\\5.1\\python\\bin\\python.exe" tests\\run_tests.py

  # everything, inside Blender (headless). --python-exit-code must come BEFORE --python: it then turns an uncaught
  # exception into exit code 1; sys.exit(n) propagates in any case.
  python tools/blender_run.py -- -b --factory-startup --python-exit-code 1 --python tests\\run_tests.py

  # standard unittest discovery
  python -m unittest discover -s tests -t .

Optional arguments (after `--` when run through Blender): test name patterns, e.g. `run_tests.py -- test_vec3`.
The process exits with 1 when a test fails.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import bpy  # noqa: F401
    IN_BLENDER = True
except ImportError:
    IN_BLENDER = False


class FlushedResult(unittest.TextTestResult):
    """`RUN <name>` before every test, flushed: when Blender crashes, the last line says which test took it down"""
    def startTest(self, test):
        print(f'RUN {test.id()}', flush=True)
        super().startTest(test)

    def addSkip(self, test, reason):
        """`SKIP <name> | <reason>` on a line of its own (tools/export_public.py reads it)"""
        super().addSkip(test, reason)
        print(f'\nSKIP {test.id()} | {" ".join(str(reason).split())}', flush=True)


def main(argv):
    patterns = [a for a in argv if not a.startswith('-')]
    loader = unittest.defaultTestLoader
    if patterns:
        loader.testNamePatterns = ['*' + p + '*' for p in patterns]
    suite = loader.discover(os.path.join(ROOT, 'tests'), pattern='test_*.py', top_level_dir=ROOT)
    print(f"HairMesh tests | python {sys.version.split()[0]} | {'inside Blender ' + bpy.app.version_string if IN_BLENDER else 'no bpy (Blender tests skipped)'}")
    result = unittest.TextTestRunner(resultclass=FlushedResult, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    # Blender keeps its own options in sys.argv; ours come after `--`
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else (sys.argv[1:] if not IN_BLENDER else [])
    code = main(args)
    sys.exit(code)
