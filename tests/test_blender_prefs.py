"""The add-on's preferences page (G7 and the outside review): the names of the properties never change (a saved preference
stays valid), every label in Title Case, American English, every tooltip starting with a verb, nothing that speaks of the
original or to "you", the three selection colors on one line. The page itself: captured by hand on 30/10/2026."""
import re
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import bpy
except ImportError:
    bpy = None

# the names the saved preferences hold: renaming one loses the user's value
SAVED = {'global_shortcut', 'tool_shortcuts', 'menu_tab', 'new_group_guides', 'outliner_open_on_select', 'sel_curve_color',
         'sel_anchor_color', 'last_anchor_mark', 'sheet_blocks', 'profile_presets'}
SMALL_WORDS = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'in', 'of', 'on', 'or', 'over', 'the', 'to'}   # lower case inside a Title
VERBS = {'Keep', 'Remember', 'Restore', 'Set', 'Show', 'Toggle', 'Turn'}
BANNED = re.compile(r'colour|\byou\b|\byour\b|the original', re.I)


def title_case(s):
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", s)
    return bool(words) and all(w[0].isupper() or (w in SMALL_WORDS and i) for i, w in enumerate(words))


class Node:
    """A layout that keeps its shape: which props went into which row (FakeLayout gives every call a new, shapeless one)."""
    def __init__(self):
        self.children, self.props, self.labels = [], [], []

    def _child(self, **_kw):
        c = Node()
        self.children.append(c)
        return c

    row = column = box = _child

    def prop(self, _data, attr, **_kw):
        self.props.append(attr)

    def label(self, text='', **_kw):
        self.labels.append(text)

    def operator(self, *_a, **_kw):
        return SimpleNamespace()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@unittest.skipUnless(bpy, 'needs Blender')
class TestPreferencesPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hairmesh.blender import keymaps, prefs
        cls.prefs, cls.keymaps = prefs, keymaps
        cls.keywords = {k: v.keywords for k, v in prefs.HairMeshPreferences.__annotations__.items()}

    def tooltips(self):
        """(where, text) of every tooltip of the page: properties, enum items, operators"""
        for k, kw in self.keywords.items():
            yield k, kw['description']
            items = kw.get('items', ())
            for item in items() if callable(items) else items:
                yield f'{k}.{item[0]}', item[2]
        for op in (self.prefs.HAIRMESH_OT_keymap_restore, self.prefs.HAIRMESH_OT_selection_colors_reset):
            yield op.bl_idname, op.__doc__

    def test_the_saved_names_are_the_same(self):
        self.assertEqual(set(self.keywords), SAVED)

    def test_title_case(self):
        P = self.prefs
        labels = [kw['name'] for kw in self.keywords.values()]
        labels += [P.HAIRMESH_OT_keymap_restore.bl_label, P.HAIRMESH_OT_selection_colors_reset.bl_label]
        with mock.patch.object(P, 'draw_shortcuts'):
            page = Node()
            P.draw_preferences(SimpleNamespace(), page, bpy.context)
        labels += [t for n in page.walk() for t in n.labels if t != P.CREDIT]
        self.assertIn('Selection Colors', labels)
        for s in labels:
            self.assertTrue(title_case(s), s)

    def test_title_case_of_the_shortcut_headings(self):
        import inspect
        src = inspect.getsource(self.prefs.draw_shortcuts)
        headings = re.findall(r"keymaps\.\w+_KM: '([^']+)'", src)
        self.assertEqual(len(headings), 3)
        for s in headings:
            self.assertTrue(title_case(s), s)

    def test_tooltips_start_with_a_verb(self):
        seen = 0
        for at, s in self.tooltips():
            seen += 1
            self.assertIn(s.split()[0], VERBS, f'{at}: {s}')
        self.assertGreater(seen, 15)

    def test_american_english_and_no_second_person(self):
        for at, s in self.tooltips():
            self.assertNotRegex(s, BANNED, at)
        for k, kw in self.keywords.items():
            self.assertNotRegex(kw['name'], BANNED, k)

    def test_the_three_colors_on_one_line(self):
        P = self.prefs
        page = Node()
        with mock.patch.object(P, 'draw_shortcuts'):
            P.draw_preferences(SimpleNamespace(), page, bpy.context)
        rows = [n for n in page.walk() if set(n.props) & set(P.SEL_COLOR_PROPS)]
        self.assertEqual(len(rows), 1, 'one row')
        self.assertEqual(tuple(rows[0].props), P.SEL_COLOR_PROPS)
