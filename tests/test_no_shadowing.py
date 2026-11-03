"""A function that assigns a name its module holds (a function, a class, an import) and also calls it or reads it as a
module raises UnboundLocalError on that branch: tool.draw_item with `text` (the review of lot 4.7b -> 4.8, a crash since
2b82f06: only a label too wide for its row got there). No Blender needed."""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def shadowed(tree):
    """[(function, name)] of the functions that store a module-level name and call it or read an attribute of it"""
    mod = {x.name for x in tree.body if isinstance(x, (ast.FunctionDef, ast.ClassDef))}
    for x in tree.body:
        if isinstance(x, (ast.Import, ast.ImportFrom)):
            mod |= {(a.asname or a.name).split('.')[0] for a in x.names}
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        nodes = list(ast.walk(fn))
        free = {n for s in nodes if isinstance(s, (ast.Global, ast.Nonlocal)) for n in s.names}
        stored = {s.id for s in nodes if isinstance(s, ast.Name) and isinstance(s.ctx, ast.Store)} & mod - free
        used = {c.func.id for c in nodes if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)} \
            | {c.value.id for c in nodes if isinstance(c, ast.Attribute) and isinstance(c.value, ast.Name)}
        out += [(fn.name, n) for n in sorted(stored & used)]
    return out


class TestNoShadowing(unittest.TestCase):
    def test_the_lint_sees_the_case(self):
        self.assertEqual(shadowed(ast.parse('def text(t):\n    return t\ndef f(x):\n    text = x\n    return text(1)\n')), [('f', 'text')])

    def test_the_add_on(self):
        found = []
        for d, _sub, files in os.walk(os.path.join(ROOT, 'hairmesh')):
            for name in files:
                if name.endswith('.py'):
                    path = os.path.join(d, name)
                    with open(path, encoding='utf-8') as f:
                        found += [(os.path.relpath(path, ROOT), *s) for s in shadowed(ast.parse(f.read()))]
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
