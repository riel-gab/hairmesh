# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The draw handlers of new code (the rule of 06/11/2026): their handles live under one key of
bpy.app.driver_namespace, so a reload of the add-on or a modal that ended badly finds and removes them; older handlers
move here when a lot touches them. add(name, …) replaces a handler of the same name; remove(name) is safe to call
twice; remove_all() at unregister."""
import bpy

KEY = 'hairmesh_draw_handlers'


def _handles():
    d = bpy.app.driver_namespace.get(KEY)
    if not isinstance(d, dict):
        d = bpy.app.driver_namespace[KEY] = {}
    return d


def add(name, space, fn, args=(), region='WINDOW', kind='POST_PIXEL'):
    """space.draw_handler_add(fn, args, region, kind), kept by name"""
    remove(name)
    _handles()[name] = (space, space.draw_handler_add(fn, args, region, kind), region)


def remove(name):
    h = _handles().pop(name, None)
    if h is None:
        return
    space, handle, region = h
    try:
        space.draw_handler_remove(handle, region)
    except (ValueError, ReferenceError):                            # (already gone: a reload)
        pass


def has(name):
    return name in _handles()


def remove_all():
    for name in list(_handles()):
        remove(name)
