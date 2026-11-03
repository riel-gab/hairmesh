# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""G10 of docs/interface.md: a modal says its mode and values in the header and its keys in the status bar, with Blender's
own icons for the keys and the mouse. A modal's keys are a tuple of ((key names), what they do); live.header takes them
beside the header text, and its None clears both. A toast of live takes the status bar for a moment; current() is what
it gives back."""
import bpy

KEY_ICONS = {'LMB': 'MOUSE_LMB', 'MMB': 'MOUSE_MMB', 'RMB': 'MOUSE_RMB', 'Wheel': 'MOUSE_MMB_SCROLL', 'LMB Drag': 'MOUSE_LMB_DRAG', 'RMB Drag': 'MOUSE_RMB_DRAG',
             'Double-Click': 'MOUSE_LMB_2X', 'Move': 'MOUSE_MOVE', 'Enter': 'EVENT_RETURN', 'Esc': 'EVENT_ESC', 'Shift': 'EVENT_SHIFT', 'Ctrl': 'EVENT_CTRL', 'Alt': 'EVENT_ALT',
             **{c: f'EVENT_{c}' for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'}}

WIDE = {'Esc': 1.8, 'Ctrl': 1.8, 'Alt': 1.4}                         # (the room the wide key icons overflow their label by: template_event_from_keymap_item adds it too)

_keys = [None]                                                      # the keys of the modal running


def keys_text(keys):
    """the keys as one line of text, as they read in the status bar"""
    return ' · '.join(f'{" / ".join(names)}: {what}' for names, what in keys)


def draw_keys(layout, keys):
    """one group per entry: the icon of each key (a name without one as text), then what they do"""
    for names, what in keys:
        row = layout.row(align=True)
        row.alignment = 'LEFT'
        for n in names:
            row.label(text='' if n in KEY_ICONS else n, icon=KEY_ICONS.get(n, 'NONE'))
            if n in WIDE:
                row.separator(factor=WIDE[n])
        row.label(text=what)
        layout.separator(factor=2.0)


def _draw(header, context):
    if _keys[0] is None:                                            # (cleared with no workspace at hand: a window closing)
        type(header)._draw_orig(header, context)
        return
    layout = header.layout
    draw_keys(layout, _keys[0] or ())
    layout.separator_spacer()
    layout.template_reports_banner()
    layout.template_running_jobs()


def _set(context, value):
    try:
        ws = getattr(context, 'workspace', None)
        if ws is not None:
            ws.status_text_set(value)
    except Exception:                                               # (a status bar is never worth an error in a modal)
        pass


def show(context, keys):
    """the status bar draws `keys`; the same keys again do nothing (a header rewritten at every move)"""
    if keys != _keys[0]:
        _keys[0] = keys
        _set(context, _draw)


def clear(context):
    """Blender's status bar back"""
    if _keys[0] is not None:
        _keys[0] = None
        _set(context, None)


def current():
    """what the status bar shows when a toast ends: the keys of the HairMesh modal running, else None (Blender's own)"""
    if _keys[0] is None:
        return None
    try:
        if any(o.bl_idname.startswith('HAIRMESH_OT_') for win in bpy.context.window_manager.windows for o in win.modal_operators):
            return _draw
    except Exception:
        pass
    _keys[0] = None                                                 # (the modal is gone: a file loaded, the window closed)
    return None
