"""What the documentation says about its images holds in the scene each image was taken from. tools/gen_doc_images.py
writes, beside every image it takes, a .json with the state measured at that moment (tools/doc_tutorial.py, measure());
the pages list what they claim about an image in a comment beside it:

    ![The two curves](img/gs-draw.png)
    <!-- claims gs-draw: curves=2 subtool=DRAW -->

A claim is `key=value`: a number or word of the .json (curves, walls, walls_shown, groups, pending, confirmed,
meshes_shown, view, subtool, selected_curves, selected_items, selected_anchors; `null` for none), `color=orange` (the
material of every mesh shown, `color=` for none, `a,b` for two), or `pie.S="New Group"` (the label of a direction of
the pie open in the image: W E S N NW NE SW SE; `pie.N=null` for an empty one). A table with the header
`Direction | Numpad | Item` is checked against the pie of the image before it. The .json files are what the code gave
when the images were taken; tests/test_blender_doc_images.py checks the code still gives that. Runs without Blender."""
import glob
import json
import os
import re
import shlex
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pub = os.path.join(ROOT, 'public')
BASE = _pub if os.path.isdir(_pub) else ROOT                       # (this repository: public/; the public one: its root)
DOCS = os.path.join(BASE, 'docs')
IMG = os.path.join(DOCS, 'img')
PAGES = sorted(glob.glob(os.path.join(DOCS, '*.md'))) + [os.path.join(BASE, 'README.md')]

CLAIMS = re.compile(r'<!--\s*claims\s+([\w.-]+)\s*:(.*?)-->', re.S)
IMAGE_LINK = re.compile(r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
DIRECTION_WORDS = {'Left': 'W', 'Right': 'E', 'Bottom': 'S', 'Top': 'N', 'Top left': 'NW', 'Top right': 'NE',
                   'Bottom left': 'SW', 'Bottom right': 'SE'}


def images(page, text):
    """[(match start, name)] of the images of docs/img a page shows (its links resolved from the page's folder)"""
    out = []
    for m in IMAGE_LINK.finditer(text):
        link = re.split(r'[#?]', m.group(1))[0]
        path = os.path.normpath(os.path.join(os.path.dirname(page), link))
        if os.path.dirname(path) == os.path.normpath(IMG):
            out.append((m.start(), os.path.splitext(os.path.basename(path))[0]))
    return out


def fmt(value):
    """a value of the .json as a claim writes it"""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return ','.join(fmt(v) for v in value)
    return str(value)


def state(name):
    path = os.path.join(IMG, name + '.json')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def pie_label(st, direction):
    for s in (st.get('pie') or {}).get('slots', ()):
        if s['dir'] == direction:
            return s['label']
    raise KeyError(f'no pie open in this image, or no direction {direction}')


def problems(st, claims):
    """the claims (`key=value` words) that do not hold in the state `st`"""
    words = shlex.split(claims)
    if not words:
        return ['no claim']
    out = []
    for word in words:
        key, _, want = word.partition('=')
        if not _:
            out.append(f'{word!r}: not key=value')
            continue
        try:
            got = pie_label(st, key[4:]) if key.startswith('pie.') else st[key]
            ok = fmt(got) == want
        except KeyError as e:
            out.append(f'{key}: {e}')
            continue
        if not ok:
            out.append(f'{key}: the text says {want!r}, the image has {got!r}')
    return out


def tables(text):
    """[(offset, [[cells]])] of the Markdown tables with the header Direction | Numpad | Item"""
    out = []
    lines = text.split('\n')
    pos = 0
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if cells == ['Direction', 'Numpad', 'Item']:
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith('|'):
                    break
                rows.append([c.strip() for c in row.strip().strip('|').split('|')])
            out.append((pos, rows))
        pos += len(line) + 1
    return out


class TestDocClaims(unittest.TestCase):
    def test_every_claim_holds_in_its_image(self):
        seen = 0
        for page in PAGES:
            text = open(page, encoding='utf-8').read()
            shown = {n for _, n in images(page, text)}
            for name, claims in CLAIMS.findall(text):
                rel = os.path.relpath(page, BASE)
                with self.subTest(page=rel, image=name):
                    self.assertIn(name, shown, f'{rel} claims about {name}, which it does not show')
                    st = state(name)
                    self.assertIsNotNone(st, f'no {name}.json: run tools/gen_doc_images.py')
                    self.assertEqual(problems(st, claims), [])
                seen += 1
        self.assertGreater(seen, 0)

    def test_every_measured_image_has_claims(self):
        for page in PAGES:
            text = open(page, encoding='utf-8').read()
            claimed = {n for n, _ in CLAIMS.findall(text)}
            for name in {n for _, n in images(page, text)}:
                if state(name) is not None:
                    self.assertIn(name, claimed, f'{os.path.relpath(page, BASE)} shows {name} with nothing it claims about it')

    def test_pie_tables_match_their_pie(self):
        found = 0
        for page in PAGES:
            text = open(page, encoding='utf-8').read()
            for at, rows in tables(text):
                before = [n for start, n in images(page, text) if start < at and (state(n) or {}).get('pie')]
                self.assertTrue(before, f'{os.path.relpath(page, BASE)}: a pie table with no pie image before it')
                slots = {s['dir']: s for s in state(before[-1])['pie']['slots']}
                dirs = [DIRECTION_WORDS.get(r[0]) for r in rows]
                self.assertEqual(len(dirs), len(set(dirs)), 'a direction twice')
                self.assertEqual(set(dirs), {d for d, s in slots.items() if s['label']}, 'the directions of the pie')
                for (direction, numpad, item), d in zip(rows, dirs):
                    with self.subTest(page=os.path.relpath(page, BASE), row=direction):
                        self.assertEqual(numpad, str(slots[d]['numpad']))
                        self.assertEqual(item.split(':')[0].strip(), slots[d]['label'])
                found += 1
        self.assertGreater(found, 0)

    def test_the_checker_catches_a_wrong_claim(self):
        st = {'curves': 2, 'color': ['orange'], 'view': None, 'meshes': [],
              'pie': {'slots': [{'dir': 'S', 'numpad': 2, 'label': 'New Group'}, {'dir': 'N', 'numpad': 8, 'label': None}]}}
        self.assertEqual(problems(st, 'curves=2 color=orange pie.S="New Group" pie.N=null view=null meshes='), [])
        self.assertEqual(len(problems(st, 'curves=3 color=light pie.S=Delete pie.N=x walls=1 view=None')), 6)
        self.assertEqual(problems(st, '  '), ['no claim'])

    def test_links_resolve_from_the_page(self):
        page = os.path.join(DOCS, 'x.md')
        text = '![a](img/a.png) ![b](./img/b.png "t") ![c](<img/c.png>) ![d](../docs/img/d.gif#x) ![e](other/e.png)'
        self.assertEqual([n for _, n in images(page, text)], ['a', 'b', 'c', 'd'])


if __name__ == '__main__':
    unittest.main()
