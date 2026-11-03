# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The error log (lot 4.9b, item 6b): every Python exception of the add-on — an operator's, a modal step's, a draw
handler's, a timer's — written with its traceback to the file HAIRMESH_ERROR_LOG names. Off without the variable: then
`PATH` is None, `record` returns at once and nothing is installed.

Who writes:
- `slow.action` (every hairmesh.* operator and modal step, every timed setter), where the exception passes on its way out;
- the places that catch an exception so the viewport goes on (draw.py, deferred.py, live.py): `record` in their `except`;
- `sys.excepthook`, for what reaches Blender uncaught: the draw handlers and timers of any module, handlers, setters.
  Blender prints those through PyErr_Print, which calls the hook. It keeps the ones with a frame of this package.

The same place failing at every redraw writes MAX_SAME entries, not one per frame. `tests/helpers/stress.py` and
`tools/prepare_manual_test.py` read this file; the core knows nothing of it."""
import os
import sys
import time
import traceback

PATH = os.environ.get('HAIRMESH_ERROR_LOG') or None
HEAD = '=== '
MAX_SAME = 3
_same = {}
_hook = [None]                                                      # the sys.excepthook found when installed
HERE = os.sep + 'hairmesh' + os.sep


def record(where, exc=None):
    """writes the exception being handled (or `exc`, a sys.exc_info triple) as an entry; never raises"""
    if PATH is None:
        return
    try:
        et, ev, tb = exc or sys.exc_info()
        if ev is None or getattr(ev, '_hm_logged', False):
            return
        try:
            ev._hm_logged = True                                    # (the same exception on its way out: one entry)
        except AttributeError:
            pass
        frames = traceback.extract_tb(tb)
        last = frames[len(frames) - 1] if frames else None
        sig = (where, et.__name__, last.filename if last else '', last.lineno if last else 0)
        _same[sig] = _same.get(sig, 0) + 1
        if _same[sig] > MAX_SAME:
            return
        text = ''.join(traceback.format_exception(et, ev, tb))
        with open(PATH, 'a', encoding='utf-8') as f:
            f.write(f'{HEAD}{time.strftime("%Y-%m-%d %H:%M:%S")} | {where}\n{text}\n')
    except Exception:
        pass


def _ours(tb):
    while tb is not None:
        if HERE in tb.tb_frame.f_code.co_filename:
            return True
        tb = tb.tb_next
    return False


def _excepthook(et, ev, tb):
    if _ours(tb):
        record('uncaught (a draw handler, a timer, a handler, a setter)', (et, ev, tb))
    (_hook[0] or sys.__excepthook__)(et, ev, tb)


def register():
    if PATH is not None and sys.excepthook is not _excepthook:
        _hook[0] = sys.excepthook
        sys.excepthook = _excepthook
    _same.clear()


def unregister():
    if sys.excepthook is _excepthook:
        sys.excepthook = _hook[0] or sys.__excepthook__
    _hook[0] = None


def entries(path=None, start=0):
    """the entries of the file from byte `start`: [{'where', 'text'}]"""
    path = path or PATH
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8', errors='replace') as f:
        f.seek(start)
        body = f.read()
    if not body.strip():
        return []
    out = []
    for chunk in body.split('\n' + HEAD) if not body.startswith(HEAD) else ('\n' + body).split('\n' + HEAD)[1:]:
        head, _nl, text = chunk.partition('\n')
        out.append({'where': head.partition(' | ')[2], 'text': text.strip()})
    return out


def size(path=None):
    path = path or PATH
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0
