# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The add-on's own image icons for buttons, menus and pies, loaded with bpy.utils.previews the way HardOps loads
its menu icons (icons/*.png -> icon_value): hairmesh.png is the hair-in-a-ring drawing of the toolbar icon, and
both files are made by tools/make_icon.py from docs/img/icon-source.png; auto.png, the mark of "automatic"
(Blender's own AUTO is a car), and selection.png, the Selection tab of the D, by the same script. In a layout description an icon
given as 'HM:<name>' means one of these (tool.draw_item); icon_kw turns either kind into the layout keyword."""
import os

import bpy
import bpy.utils.previews

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')
NAMES = ('hairmesh', 'auto', 'selection')
PREFIX = 'HM:'
EXPAND_ALL = 'FULLSCREEN_ENTER'                                     # (one icon for the concept, G8: the group tree of the Conversion tab and the Outliner's bar)
# light marks on nothing, and the kind of button each is drawn on (the theme's colours of that widget): on the light
# buttons of a light theme `auto` was not seen (the Auto mode of every group in the Outliner); `selection` is a tab
# of the D, a radio button, which a light theme may keep dark
ONE_COLOR = {'auto': 'wcol_tool', 'selection': 'wcol_radio'}
DARK = '-dark'
_coll = None


def dark_text(widget='wcol_tool'):
    """whether the theme writes the text of that kind of button dark (its buttons are light): Blender draws its own
    icons in that colour"""
    try:
        r, g, b = getattr(bpy.context.preferences.themes[0].user_interface, widget).text[:3]
    except (AttributeError, IndexError, TypeError):
        return False
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


def inverted(pixels):
    """the pixels (r, g, b, a, ...) with each shade turned over, the alpha kept: a light mark becomes a dark one"""
    out = list(pixels)
    for i in range(0, len(out) - 3, 4):
        out[i] = 1.0 - out[i]; out[i + 1] = 1.0 - out[i + 1]; out[i + 2] = 1.0 - out[i + 2]
    return out


def variant(name):
    """the image to draw for `name` now: a one-colour mark's dark copy when the theme writes the text of its kind of
    button dark (made from the light one the first time it is asked for), else `name` itself — also when the copy
    cannot be made"""
    if _coll is None or name not in ONE_COLOR or not dark_text(ONE_COLOR[name]):
        return name
    key = name + DARK
    if key in _coll:
        return key
    try:
        src = _coll[name]
        copy = _coll.new(key)
        for size, pixels in (('image_size', 'image_pixels_float'), ('icon_size', 'icon_pixels_float')):   # (by name: the stubs of bpy type the sizes and the pixels as single values)
            setattr(copy, size, tuple(getattr(src, size)))
            setattr(copy, pixels, inverted(getattr(src, pixels)))
    except Exception:                                               # (an icon is never worth an error in a draw)
        return name
    return key


def get(name):
    """the icon_value of icons/<name>.png (of its dark copy under a light theme, variant), or 0 when it is not loaded"""
    if _coll is None:
        return 0
    try:
        return _coll[variant(name)].icon_id
    except KeyError:
        return 0


def icon_kw(icon):
    """{'icon': 'X'} for a built-in icon name, {'icon_value': id} for 'HM:<name>' (a built-in fallback when the image
    did not load, so a button never comes out blank)"""
    if icon.startswith(PREFIX):
        v = get(icon[len(PREFIX):])
        return {'icon_value': v} if v else {'icon': 'OUTLINER_OB_EMPTY'}
    return {'icon': icon}


def register():
    global _coll
    _coll = bpy.utils.previews.new()
    for name in NAMES:
        path = os.path.join(DIR, name + '.png')
        if os.path.exists(path):
            _coll.load(name, path, 'IMAGE')


def unregister():
    global _coll
    if _coll is not None:
        bpy.utils.previews.remove(_coll)
        _coll = None
