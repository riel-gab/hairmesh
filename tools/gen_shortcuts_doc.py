"""Writes docs/shortcuts.md (the public documentation) from the add-on's own tables: the default keymaps (global, the
tool's, the sidebar's), the sub-tool pie and the keys every modal shows in the status bar. Runs inside Blender:

    python tools/blender_run.py -- -b --factory-startup --python tools/gen_shortcuts_doc.py [-- --check]

--check writes nothing and exits with 1 when the file is not what the code gives (tests/test_blender_shortcuts_doc.py
does the same). In this repository the file is public/docs/shortcuts.md; in the public one, docs/shortcuts.md."""
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# (title, module of hairmesh.blender, attribute): the keys of each modal, as its status bar shows them
MODALS = (
    ('Draw: placing anchors', 'ops_draw', 'KEYS'),
    ('Draw: Freehand stroke', 'ops_draw', 'STROKE_KEYS'),
    ('Select: dragging anchors', 'ops_select', 'DRAG_KEYS'),
    ('Select: sliding along a connector', 'ops_select', 'SLIDE_KEYS'),
    ('Select: smoothness (B)', 'ops_select', 'SMOOTH_KEYS'),
    ('Circle Select (C)', 'ops_select', 'CIRCLE_KEYS'),
    ('Move, Rotate, Scale (G, R, S)', 'ops_transform', 'MODAL_KEYS'),
    ('Gizmo handles', 'ops_gizmo', 'KEYS'),
    ('Scissors: Curve', 'ops_scissors', 'CURVE_KEYS'),
    ('Scissors: Wall', 'ops_scissors', 'WALL_KEYS'),
    ('Guides: Line', 'ops_guides', 'LINE_KEYS'),
    ('Guides: NURBS Disc', 'ops_guides', 'DISC_KEYS'),
    ('Guides: Pencil', 'ops_guides', 'PENCIL_KEYS'),
    ('Crown', 'ops_crown', 'KEYS'),
    ('Brush', 'brush', 'BRUSH_KEYS'),
    ('Brush: painting', 'brush', 'PAINT_KEYS'),
    ('Brush: radius and hardness', 'brush', 'ADJUST_KEYS'),
    ('Edges: box', 'ops_edges', 'KEYS'),
    ('Profile editor', 'profile_editor', 'KEYS'),
)
# when a tool key acts (the operators pass the key on otherwise); the ones not here act in any sub-tool
SELECTED = 'Something of HairMesh selected'
IN_SELECT = 'Select, something of HairMesh selected'
WHEN = {
    'hairmesh.toggle_tool': 'Object Mode, tool on or off',
    'hairmesh.guide_pencil_cancel': 'Guides > Pencil, a loop being drawn', 'hairmesh.guide_pencil_finish': 'Guides > Pencil, a loop being drawn',
    'hairmesh.edges_deselect': 'Edges, with edges selected', 'hairmesh.draw_curve': 'Draw',
    'hairmesh.scissors': 'Scissors > Curve', 'hairmesh.wall_cut': 'Scissors > Wall', 'hairmesh.guide_tool': 'Guides > Line or NURBS Disc',
    'hairmesh.guide_pencil': 'Guides > Pencil', 'hairmesh.edge_click': 'Edges', 'hairmesh.select_curve_anchors': 'Select, on a curve',
    'hairmesh.select': 'Select', 'hairmesh.crown_tool': 'Crown', 'hairmesh.insert_anchor': 'On a curve',
    'hairmesh.smooth_drag': 'Anchors selected', 'hairmesh.delete_selected': SELECTED, 'hairmesh.delete_ask': SELECTED,
    'hairmesh.delete_now': SELECTED, 'hairmesh.hide_selected': SELECTED, 'hairmesh.hide_unselected': SELECTED,
    'hairmesh.reveal_hidden': 'Something of HairMesh hidden', 'hairmesh.deselect_all': SELECTED,
    'hairmesh.grab_anchors': IN_SELECT, 'hairmesh.rotate_anchors': IN_SELECT, 'hairmesh.scale_anchors': IN_SELECT, 'hairmesh.gizmo_move': SELECTED,
    'hairmesh.gizmo_rotate': SELECTED, 'hairmesh.gizmo_scale': SELECTED, 'hairmesh.duplicate': SELECTED,
    'hairmesh.frame_selection': SELECTED, 'hairmesh.parent_to_last': 'A group selected last', 'hairmesh.unparent': 'Items inside a group selected',
}
# the tool keys that act in any sub-tool; every tool key is in WHEN or here (the test checks both ways)
ANY_SUBTOOL = {'hairmesh.select_circle', 'hairmesh.open_modifiers', 'hairmesh.edge_hover', 'wm.call_panel', 'wm.call_menu_pie'}
ESC_TO_SELECT = 'Any other sub-tool'                               # (Esc back to Select; in Select it is Blender's Esc)
MODIFIERS = ('Ctrl', 'Shift', 'Alt')
ALTERNATIVES = {('Shift', 'MMB')}                                   # (Circle Select: Shift + LMB, or MMB, deselects)
DIGITS = {'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4', 'FIVE': '5', 'SIX': '6'}
PAINT_NAMES = {'MASK': 'Mask', 'THICK': 'Thickness', 'TWIST': 'Twist', 'TIP': 'Density', 'ANCHOR': 'Anchors',
               'radius': 'Radius (F)', 'hardness': 'Hardness (Shift+F)', 'drag': 'Both (Ctrl + right drag)'}

INTRO = """# Shortcuts

This page is generated from the add-on's code (`tools/gen_shortcuts_doc.py`). Every key is editable in
Edit > Preferences > Add-ons > HairMesh, where each one can also be restored to its default.

Only **Shift+Alt+H** is global. The rest work only while the HairMesh tool is active. Many of them are contextual: they
act on HairMesh only in the situation the When column gives, and leave the key to Blender otherwise.
"""


def target():
    pub = os.path.join(ROOT, 'public')
    return os.path.join(pub if os.path.isdir(pub) else ROOT, 'docs', 'shortcuts.md')


def cell(text):
    return ' '.join(str(text).split()).replace('|', '\\|')


def first_sentence(text):
    text = ' '.join((text or '').split())
    while True:                                                     # (the asides in brackets out, the nested ones too)
        t = re.sub(r' ?\([^()]*\)', '', text)
        if t == text:
            break
        text = t
    for stop in ('. ', '; '):
        i = text.find(stop)
        if 0 < i:
            text = text[:i]
    return text.rstrip('.')


def key_label(event):
    """'Shift+Alt+H', '1', 'LMB Double-Click'"""
    from hairmesh.blender import keymaps
    if event['type'] in DIGITS:
        key = DIGITS[event['type']]
    else:
        key = keymaps.event_label({'type': event['type']}).replace(' Double Click', '')
    mods = [m.title() for m in ('ctrl', 'shift', 'alt', 'oskey') if event.get(m) and not event.get('any')]
    return '+'.join(mods + [key]) + (' Double-Click' if event.get('value') == 'DOUBLE_CLICK' else '')


def modal_key(names):
    """('Shift', 'F') -> 'Shift+F'; ('Enter', 'RMB') -> 'Enter / RMB'; ('Shift', 'X', 'Y', 'Z') -> 'Shift+X / Y / Z'"""
    names = tuple(names)
    if len(names) > 1 and names[0] in MODIFIERS and names not in ALTERNATIVES:
        if all(n in MODIFIERS for n in names):
            return '+'.join(names)
        return f'{names[0]}+' + ' / '.join(names[1:])
    return ' / '.join(names)


def operator_text(idname, props):
    """(name, what it does) of a keymap item"""
    if idname in ('wm.call_panel', 'wm.call_menu_pie'):
        cls = getattr(bpy.types, props.get('name', ''), None)
        label = getattr(cls, 'bl_label', props.get('name', ''))
        return label, ('Open the pie' if idname == 'wm.call_menu_pie' else 'Open the pop-up')
    mod, op = idname.split('.')
    rna = getattr(getattr(bpy.ops, mod), op).get_rna_type()
    return rna.name, first_sentence(rna.description)


def key_rows(items):
    from hairmesh.blender import keymaps
    rows, seen = [], set()
    for idname, event, props in items:
        if event['type'] == 'MOUSEMOVE':
            continue                                                # (the hover of Edges: no key)
        if event.get('value') == 'DOUBLE_CLICK' and idname in keymaps.DOUBLE_PRESS:
            continue                                                # (a double click is the same press: one row)
        key = 'No default key' if event['type'] == 'NONE' else key_label(event)
        name, what = operator_text(idname, props)
        when = ESC_TO_SELECT if (idname == 'hairmesh.subtool_select' and event['type'] == 'ESC') else WHEN.get(idname, 'Any sub-tool')
        row = (key, when, name, what)
        if row not in seen:
            seen.add(row)
            rows.append(row)
    return rows


def table(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '|'.join('---' for _ in head) + '|']
    out += ['| ' + ' | '.join(cell(c) for c in r) + ' |' for r in rows]
    return out


def modal_rows(keys):
    return [(modal_key(names), what) for names, what in keys]


def render():
    """the text of docs/shortcuts.md"""
    import importlib
    from hairmesh.blender import keymaps
    from hairmesh.blender.props import ready_subtools
    head = ('Key', 'When', 'Action', 'What it does')
    out = [INTRO]
    out += ['## Global', ''] + table(head, key_rows(keymaps.GLOBAL_ITEMS)) + ['']
    out += ['## While the tool is active', '', 'The Action column is the name in the list of shortcuts in the preferences.', '']
    out += table(head, key_rows(keymaps.TOOL_ITEMS)) + ['']
    out += ['Over the viewport and the sidebar (N):', '']
    out += table(head, key_rows(keymaps.GENERIC_ITEMS)) + ['']
    out += ['## Sub-tools (Q pie)', '']

    def what(t):
        s = first_sentence(t[2].split(': ', 1)[-1])
        return s[:1].upper() + s[1:]
    out += table(('Key', 'Sub-tool', 'What it does'), [(DIGITS.get(t[6], t[6]), t[1], what(t)) for t in ready_subtools()]) + ['']
    out += ['## In a modal', '', 'While a modal runs, the status bar shows its keys. These are the same lists.', '']
    for title, mod, attr in MODALS:
        keys = getattr(importlib.import_module(f'hairmesh.blender.{mod}'), attr)
        if isinstance(keys, dict):
            for k, v in keys.items():
                out += [f'### {title}: {PAINT_NAMES.get(k, k)}', ''] + table(('Key', 'Action'), modal_rows(v)) + ['']
        else:
            out += [f'### {title}', ''] + table(('Key', 'Action'), modal_rows(keys)) + ['']
    return '\n'.join(out).rstrip('\n') + '\n'


def ensure_registered():
    import hairmesh
    if not hasattr(bpy.types, 'HAIRMESH_PT_popup'):
        hairmesh.register()


def main(argv):
    ensure_registered()
    text = render()
    path = target()
    old = open(path, encoding='utf-8').read() if os.path.isfile(path) else None
    if '--check' in argv:
        print('shortcuts.md is up to date' if old == text else f'shortcuts.md is out of date: run {os.path.basename(__file__)}')
        return 0 if old == text else 1
    if old != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    print(f'{os.path.relpath(path, ROOT)}: {"written" if old != text else "unchanged"}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []))
