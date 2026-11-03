# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The slow-action log, always on (lot 3.7-estab, item 3): every action of the add-on — an operator's execute /
invoke / modal step, a slider's setter, the viewport's draw, Blender's own undo and redo — that takes THRESHOLD
seconds or more writes one line to the system console:

    HairMesh slow: hairmesh.confirm_conversion 0.84 s (project parsed 0.31 s; row tables 0.42 s: every lock; ...)

The detail names what was done again inside the action (`part`): each part with its time and what it did. Cheap when
it does not fire: one perf_counter at each end of an action, and a part outside an action costs one list test.

Only the outermost action is timed and logged (an operator that calls live.update logs once, with live.update's
parts in its detail). `lines` keeps the last lines written (the tests and the stress test read it)."""
import functools
import os
import sys
import time
from contextlib import contextmanager

from . import errorlog

THRESHOLD = 0.3
PART_MIN = 0.02                                                     # a part shorter than this is left out of the detail
_stack = []                                                         # the open actions: [name, t0, parts]
lines = []                                                          # the last lines written (at most KEEP)
KEEP = 200
timings = []                                                        # (name, seconds) of every outermost action, when `record` is on (the stress test)
record = False


def _write(line):
    lines.append(line)
    del lines[:-KEEP]
    try:
        print(line, file=sys.stdout, flush=True)                   # (Window > Toggle System Console shows it on Windows)
    except Exception:
        pass


def _detail(parts):
    """the parts in order, the ones of the same name summed ("live object refilled ×13 0.45 s: Base_02 live, …")"""
    merged = {}
    for name, dt, what in parts:
        m = merged.setdefault(name, [0, None, []])
        m[0] += 1
        if dt is not None:
            m[1] = (m[1] or 0.0) + dt
        if what and what not in m[2]:
            m[2].append(str(what))
    out = []
    for name, (n, dt, whats) in merged.items():
        if dt is not None and dt < PART_MIN:
            continue
        s = name + (f' ×{n}' if n > 1 else '') + ('' if dt is None else f' {dt:.2f} s')
        if whats:
            w = ', '.join(whats[:4]) + (f', +{len(whats) - 4} more' if len(whats) > 4 else '')
            s += f': {w}'
        out.append(s)
    return '; '.join(out) if out else 'nothing of the add-on\'s own: Blender\'s work around it'


def _name(name):
    try:
        return name() if callable(name) else name
    except Exception:
        return '?'


@contextmanager
def action(name):
    """times an action (`name`: a string, or a callable read only when the line is written); nested actions are parts
    of the outermost one"""
    if _stack:
        with part(_name(name)):
            yield
        return
    rec = [name, time.perf_counter(), []]
    _stack.append(rec)
    try:
        yield
    except Exception:                                               # (no cost until one is raised; written only with HAIRMESH_ERROR_LOG set)
        errorlog.record(_name(name))
        raise
    finally:
        _stack.pop()
        dt = time.perf_counter() - rec[1]
        if record or dt >= THRESHOLD:
            label = _name(name)
            if record:
                timings.append((label, dt))
            if dt >= THRESHOLD:
                _write(f'HairMesh slow: {label} {dt:.2f} s ({_detail(rec[2])})')


@contextmanager
def part(name, what=None):
    """a part of the current action, timed; `what` may be set later through the yielded list ([what])"""
    if not _stack:
        yield [what]
        return
    box = [what]
    t0 = time.perf_counter()
    try:
        yield box
    finally:
        _stack[0][2].append((name, time.perf_counter() - t0, box[0]))


def note(name, what=None):
    """a fact about the current action, untimed (what was rebuilt)"""
    if _stack:
        _stack[0][2].append((name, None, what))


def timed(name):
    """decorator: the function is an action named `name`"""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*a, **kw):
            with action(name):
                return fn(*a, **kw)
        return inner
    return wrap


# ---------------- operators: every hairmesh.* operator's execute / invoke / modal ----------------

def _wrap_method(cls, attr, label):
    # the method as Blender finds it: the class's own or a mixin's (3.8c: G / R / S share _Transform's — a method of a
    # base was not wrapped, and a slow confirm said nothing); Blender's Operator itself has none of these
    fn = next((c.__dict__[attr] for c in cls.__mro__ if attr in c.__dict__ and c.__module__ != 'bpy.types'), None)
    if fn is None or getattr(fn, '_hm_slow', False):
        return

    # (Blender passes as many arguments as the method's code names: the wrappers spell them out)
    if attr == 'execute':
        def inner(self, context):
            with action(lambda: label + _props(self)):
                return fn(self, context)
    else:
        def inner(self, context, event):
            with action(lambda: label + _props(self)):
                return fn(self, context, event)
    functools.update_wrapper(inner, fn)
    del inner.__wrapped__
    inner._hm_slow = True
    inner._hm_orig = fn
    setattr(cls, attr, inner)


def _props(op):
    """the properties set on an operator call, short: "(mode='LIVE')" """
    try:
        ps = op.properties
        got = [(k, getattr(ps, k), ps.bl_rna.properties[k].subtype) for k in ps.bl_rna.properties.keys() if k != 'rna_type' and ps.is_property_set(k)]
    except Exception:
        return ''
    return short_props(got)


PATH_KEYS = ('filepath', 'directory', 'filename')
VALUE_MAX = 40                                                      # a longer value is cut, its quote closed


def short_props(got):
    """[(name, value, subtype)] -> "(a=1, filepath='example.hairmesh')": a path shows its file name alone (the
    folders say nothing about the action and cut the line, item 2 of the manual test of 3.7b); a long value is cut
    with its quote closed ('abc…'); a vector its values, rounded, closed ((1.5, 2, …) — 3.8a: Blender's arrays print
    as the path of their owner)"""
    out = []
    for k, v, sub in got:
        if not isinstance(v, (str, bytes)) and hasattr(v, '__len__') and hasattr(v, '__getitem__'):
            vals = [f'{x:.4g}' if isinstance(x, float) else repr(x) for x in list(v)[:6]]
            out.append(f'{k}=({", ".join(vals)}{", …" if len(v) > 6 else ""})')
            continue
        if isinstance(v, str) and (sub in ('FILE_PATH', 'DIR_PATH', 'FILE_NAME') or k in PATH_KEYS):
            v = os.path.basename(v.rstrip('\\/')) or v
        if isinstance(v, str) and len(v) > VALUE_MAX:
            r = repr(v[:VALUE_MAX - 1])
            out.append(f'{k}={r[:-1]}…{r[-1]}')
        else:
            r = repr(v)
            out.append(f'{k}={r[:VALUE_MAX] + "…" if len(r) > VALUE_MAX else r}')
    s = ', '.join(out)
    return f'({s})' if s else ''


def _operators(base):
    for sub in base.__subclasses__():
        yield sub
        yield from _operators(sub)


def instrument(package):
    """wraps the methods of the operators defined in `package` (Blender looks a registered operator's methods up on
    the class at each call, so the wrappers apply to the classes already registered)"""
    import bpy
    for cls in _operators(bpy.types.Operator):
        if not cls.__module__.startswith(package) or not getattr(cls, 'bl_idname', '').startswith('hairmesh.'):
            continue
        name = cls.bl_idname
        _wrap_method(cls, 'execute', name)
        _wrap_method(cls, 'invoke', name)
        _wrap_method(cls, 'modal', f'{name} (a modal step)')


def uninstrument(package):
    import bpy
    for cls in _operators(bpy.types.Operator):
        if not cls.__module__.startswith(package):
            continue
        for attr in ('execute', 'invoke', 'modal'):
            fn = cls.__dict__.get(attr)
            if fn is not None and getattr(fn, '_hm_slow', False):
                setattr(cls, attr, fn._hm_orig)


# ---------------- Blender's undo / redo: undo_pre -> undo_post ----------------

_undo_t0 = [None]


def _pre(*_a):
    _undo_t0[0] = time.perf_counter()


def _post_named(what):
    def post(*_a):
        t0 = _undo_t0[0]; _undo_t0[0] = None
        if t0 is None:
            return
        dt = time.perf_counter() - t0
        if record:
            timings.append((what, dt))
        if dt >= THRESHOLD:
            _write(f'HairMesh slow: {what} {dt:.2f} s (Blender restoring the data of the step: the meshes and texts the add-on keeps in the .blend)')
    return post


_handlers = []


def register():
    import bpy
    from bpy.app.handlers import persistent
    pre = persistent(_pre)
    undo_post = persistent(_post_named('Ctrl+Z (Blender\'s undo)'))
    redo_post = persistent(_post_named('Ctrl+Shift+Z (Blender\'s redo)'))
    for lst, fn in ((bpy.app.handlers.undo_pre, pre), (bpy.app.handlers.redo_pre, pre),
                    (bpy.app.handlers.undo_post, undo_post), (bpy.app.handlers.redo_post, redo_post)):
        lst.append(fn); _handlers.append((lst, fn))
    instrument(__package__.rsplit('.', 1)[0])
    errorlog.register()


def unregister():
    for lst, fn in _handlers:
        if fn in lst:
            lst.remove(fn)
    _handlers.clear()
    uninstrument(__package__.rsplit('.', 1)[0])
    errorlog.unregister()
