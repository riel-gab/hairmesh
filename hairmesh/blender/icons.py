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
_coll = None


def get(name):
    """the icon_value of icons/<name>.png, or 0 when it is not loaded"""
    if _coll is None:
        return 0
    try:
        return _coll[name].icon_id
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
