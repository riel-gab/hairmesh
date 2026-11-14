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
HAIRMESH_TEST_ORDER=reverse runs the suite backwards (each class's tests still together): a test that only passes after
another one shows.
The process exits with 1 when a test fails.

tests/expected_failures.json lists tests known to fail until a later lot ports what they check: {test id: {why, kind,
lot, fails}} (lot 1.1a-2). Each runs as an expected failure, and only the failure registered counts as expected:
`fails` = the exception type, the assertion that fails (the text of the test's line, never its number; of the tests/helpers/
line when the test method is a mixin's, lot 1.2), the fields that
differ when it compares two dicts (the values kept by wrap_assert, compared key by key) and a fixed piece of
the message. A test that fails another way is a failure (`XFAIL-MISMATCH <id> | <what differs>`); one that passes is an
unexpected success (`XPASS <id>`), and the run fails: take it off the list. `XFAIL <id> | <why>` on a line of its own
otherwise (tools/verify_batch.py shows them on a line apart). tools/xfail_audit.py writes `fails` from a run.

tests/local_only.json lists the tests that run only on the owner's machine ({id, class or module: {where, why}}; where
"pc": skipped when HAIRMESH_CLOUD is set, as in the cloud judge; where "windows": skipped off Windows). They are
skipped with the reason (`SKIP <id> | local only ...`); `tools/verify_batch.py --local-only` runs the "pc" ones.
"""
import json
import os
import re
import sys
import time
import traceback
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
    """`RUN <name>` before every test, flushed: when Blender crashes, the last line says which test took it down; after
    it, `TIME <name> <test s> <since the last test s>` on a line of its own (the second number also holds the class's
    setUpClass and the module's import: tools/test_times.py sums them)"""
    _last = None

    def startTest(self, test):
        print(f'RUN {test.id()}', flush=True)
        self._t0 = time.perf_counter()
        if FlushedResult._last is None:
            FlushedResult._last = self._t0
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        now = time.perf_counter()
        print(f'\nTIME {test.id()} {now - self._t0:.3f} {now - FlushedResult._last:.3f}', flush=True)
        FlushedResult._last = now

    def addExpectedFailure(self, test, err):
        entry = EXPECTED.get(test.id())
        try:
            bad = mismatch(entry, failure_sig(err)) if isinstance(entry, dict) else None
        except Exception as e:                                      # (a comparison that raises: never an expected failure)
            bad = f'the failure could not be read: {type(e).__name__}: {e}'
        if bad:
            print(f'\nXFAIL-MISMATCH {test.id()} | {bad}', flush=True)
            self.addFailure(test, err)                                  # (the real failure, with its traceback)
            return
        super().addExpectedFailure(test, err)
        why = entry.get('why', '') if isinstance(entry, dict) else entry
        print(f'\nXFAIL {test.id()} | {" ".join(str(why or "").split())}', flush=True)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        print(f'\nXPASS {test.id()} | passes: take it off tests/expected_failures.json', flush=True)

    def addSkip(self, test, reason):
        """`SKIP <name> | <reason>` on a line of its own (tools/export_public.py reads it)"""
        super().addSkip(test, reason)
        print(f'\nSKIP {test.id()} | {" ".join(str(reason).split())}', flush=True)


EXPECTED_FILE = os.path.join(ROOT, 'tests', 'expected_failures.json')
_FIELD = re.compile(r"^[-+] .*?'([A-Za-z_]\w*)': ")


def diff_keys(a, b, key=None, out=None):
    """the dict keys under which a and b differ (the innermost key above each difference; `key` for a top level that
    is not a dict), compared structurally"""
    out = set() if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out.add(str(k))
            elif a[k] != b[k]:
                diff_keys(a[k], b[k], str(k), out)
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        for x, y in zip(a, b):
            if x != y:
                diff_keys(x, y, key, out)
    elif a != b and key is not None:
        out.add(key)
    return out


def wrap_assert(test):
    """test.assertEqual keeps the two values on the AssertionError it raises (failure_sig reads the fields from them)"""
    orig = test.assertEqual

    def assertEqual(first, second, msg=None):
        try:
            orig(first, second, msg)
        except AssertionError as e:
            e.hm_pair = (first, second)
            raise
    test.assertEqual = assertEqual


def fixed_head(text):
    """the start of a message up to the first value in it (a number, a repr, unittest's [N chars]): what stays when
    only the values change"""
    return re.match(r'[^\[{(\d\'"-]*', text).group().strip()[:60]


_NUMBER = re.compile(r'-?\d+(?:\.\d+(?:e[-+]?\d+)?|e[-+]?\d+)')   # (a measured value: a point or an exponent; an id or an action number stays)


def same_text(want, got):
    """True when `want` is in `got` with every measured number (a point or an exponent) taken as any: a residue that
    moves is the same failure; an action or curve number is not"""
    return _NUMBER.sub('#', want) in _NUMBER.sub('#', got or '')


def failure_sig(err):
    """what a failure was: {type, at (the stripped text of the failing line of the test), fields (the dict keys on the
    changed lines of unittest's diff, sorted; None without a diff), msg (the custom message of the assertion, or the
    fixed head of the first line, fixed_head)}

    Only the first assertion that fails counts: the ones after it, and the subTests after it, never run (unittest stops
    the test there), so a registered failure hides a second regression further down the same test. An entry with
    `fields: []` (a non-dict comparison) checks the assertion and the message, not which value changed. The message is
    compared with its measured numbers taken as any number (same_text)."""
    etype, value, tb = err
    at = helper = None
    for fr in traceback.extract_tb(tb):
        if os.sep + 'tests' + os.sep in fr.filename and os.path.basename(fr.filename).startswith('test_'):
            at = (fr.line or '').strip()
        elif os.sep + 'tests' + os.sep + 'helpers' + os.sep in fr.filename:
            helper = (fr.line or '').strip()                        # (lot 1.2: a test method of a mixin in tests/helpers/, splitrun.SplitRun)
    at = at or helper
    text = str(value)
    lines = text.splitlines()
    if hasattr(value, 'hm_pair'):                                   # (wrap_assert: the values themselves)
        fields = sorted(diff_keys(*value.hm_pair))
    else:
        diff = [ln for ln in lines[1:] if ln[:2] in ('- ', '+ ')]
        fields = sorted({m.group(1) for ln in diff for m in [_FIELD.match(ln)] if m}) if diff else None
    first = lines[0] if lines else ''
    last = lines[-1] if lines else ''
    # (unittest appends ' : <msg>' to its own text, after the diff: the custom message is the end of the last line)
    msg = last.rsplit(' : ', 1)[1] if ' : ' in last else fixed_head(first)
    return {'type': etype.__name__, 'at': at, 'fields': fields, 'msg': msg}


def mismatch(entry, sig):
    """None when the failure is the one `entry['fails']` registers, else what differs (a short text)"""
    want = (entry or {}).get('fails')
    if not want:
        return 'no registered failure (fails) for this test'
    out = []
    for k in ('type', 'at'):
        if want.get(k) is not None and want[k] != sig[k]:
            out.append(f'{k}: {sig[k]!r} (registered {want[k]!r})')
    if want.get('fields') is not None and want['fields'] != sig['fields']:
        out.append(f"fields: {sig['fields']} (registered {want['fields']})")
    if want.get('msg') and not same_text(want['msg'], sig['msg']):
        out.append(f"msg: {sig['msg']!r} (registered {want['msg']!r})")
    return '; '.join(out) or None


def _load_expected():
    try:
        with open(EXPECTED_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except OSError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith('_')}


EXPECTED = _load_expected()


def _iter(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _iter(t)
        else:
            yield t


def mark_expected(suite, expected=None):
    """every test of `suite` whose id is in `expected` runs as an expected failure; the ids found"""
    expected = EXPECTED if expected is None else expected
    found = []
    for t in _iter(suite):
        if t.id() in expected:
            t.__unittest_expecting_failure__ = True                 # (what @unittest.expectedFailure sets; TestCase.run reads it on the case)
            t.maxDiff = None                                        # (the whole diff: the fields that differ are part of the failure)
            wrap_assert(t)
            found.append(t.id())
    return found


LOCAL_FILE = os.path.join(ROOT, 'tests', 'local_only.json')


def _load_local():
    try:
        with open(LOCAL_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except OSError:
        return {}
    return {k: v for k, v in data.items() if not k.startswith('_')}


LOCAL = _load_local()


def local_skip(entry, cloud=None, platform=None):
    """why a test of tests/local_only.json does not run here (None when it runs): `where` "pc" = only on the owner's
    machine (a window on a real monitor, a graphics card, real events), skipped when HAIRMESH_CLOUD is set; "windows" =
    only on Windows, skipped on any other system"""
    cloud = os.environ.get('HAIRMESH_CLOUD') if cloud is None else cloud
    platform = sys.platform if platform is None else platform
    where = entry.get('where')
    if where == 'pc' and cloud:
        return f'local only (the owner\'s machine): {entry.get("why", "")}'
    if where == 'windows' and platform != 'win32':
        return f'Windows only: {entry.get("why", "")}'
    return None


def mark_local(suite, local=None, cloud=None, platform=None):
    """skips the tests of `suite` that tests/local_only.json keeps off this machine; a key is a test id, a class or a
    module (then setUpClass does not run either). The ids skipped"""
    local = LOCAL if local is None else local
    found = []
    for t in _iter(suite):
        tid = t.id()
        for key, entry in local.items():
            if tid != key and not tid.startswith(key + '.'):
                continue
            why = local_skip(entry, cloud, platform)
            if why is None:
                break
            if tid == key:
                setattr(t, t._testMethodName, unittest.skip(why)(lambda: None))
            else:
                type(t).__unittest_skip__ = True                    # (a class or a module: its setUpClass stays off too)
                type(t).__unittest_skip_why__ = why
            found.append(tid)
            break
    return found


def main(argv):
    patterns = [a for a in argv if not a.startswith('-')]
    loader = unittest.defaultTestLoader
    if patterns:
        loader.testNamePatterns = ['*' + p + '*' for p in patterns]
    suite = loader.discover(os.path.join(ROOT, 'tests'), pattern='test_*.py', top_level_dir=ROOT)
    if os.environ.get('HAIRMESH_TEST_ORDER') == 'reverse':
        suite = unittest.TestSuite(list(_iter(suite))[::-1])        # (the other order: the classes and their tests backwards, each class still in one block)
    mark_expected(suite)
    mark_local(suite)
    print(f"HairMesh tests | python {sys.version.split()[0]} | {'inside Blender ' + bpy.app.version_string if IN_BLENDER else 'no bpy (Blender tests skipped)'}")
    result = unittest.TextTestRunner(resultclass=FlushedResult, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    # Blender keeps its own options in sys.argv; ours come after `--`
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else (sys.argv[1:] if not IN_BLENDER else [])
    code = main(args)
    sys.exit(code)
