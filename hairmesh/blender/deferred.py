# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Settings whose rebuild is too slow to follow every step of a slider (1a of the manual test of 21/10/2026: dragging
a conversion slider rebuilt the locks at every step and froze Blender, the Loop Step most of all — it reaches every
lock of the ticked groups, or of the project). The model changes at once, the preview and the undo step follow when
the value stops changing or the slider is let go.

What Blender does, measured with real events in a second Blender (docs/fase-3-plano-ciclo-nativo.md, "Estado", the
lot of 21/10/2026): the setter of a property runs at every step of a drag and NOT again on the release; for a
Scene-owned property the UI pushes ONE undo step after the release, for a WindowManager-owned one none. A rebuild
deferred past the release of a Scene property would fall outside its undo step (the model in the step, the old
preview with it — the orphan of 15/10/2026 again). So the conversion sliders are WindowManager properties
(props.HairMeshConvProps) and this module makes their undo step itself:

- the setter changes the model in memory, names the locks it touches (live.touch) and calls changed();
- a timer, on the next turn of the event loop, starts HAIRMESH_OT_commit_setting — never the setter itself: an
  operator invoked while the UI applies a typed value hangs Blender;
- the operator is modal and passes every event through (the slider keeps working). When it starts, the window's
  last non-motion event says whether the left button is held (a drag) — Blender keeps it in the event state while
  the mouse moves. Holding: the release ends it (a pause says what the release will rebuild, lot 4.8b below). Not holding (a typed value, Ctrl+wheel): QUIET seconds without a
  change, or any click or key, end it — a Ctrl+Z waits for the step to be pushed, then undoes it.
- ending = the preview rebuilt if the model changed since the last rebuild, the model saved, FINISHED: Blender pushes
  one undo step with model and preview together (CANCELLED when the text came out the same: no step).
- Esc or a right click during a drag: the model is read back from its text — nothing was saved since the drag began,
  so it is exactly the model before it, whatever the UI puts back (a derived project step, a group's conv without
  that key) — the preview is rebuilt from it, CANCELLED: no step.
- Nothing is left hanging: the watcher's cancel() (its handler removed by a file load, a closed window) clears the
  running flag; an undo, a redo or a load forgets what was pending, the unsaved model with it (forget); a watcher
  that cannot start commits at once, or drops the change.
The rebuild shows the wait cursor, and a row-table rebuild longer than live.WAIT_AFTER reports "N curves rebuilt in X
s".

Lot 4.2c, item 2 (the owner's request: the disc's Outer Radius froze Blender 5-7 s at every pause of the drag): while
the interaction lasts the root is held (live.hold) — the model changes and is drawn at once (a disc's rings, a guide's
zone), but no row table is built behind the slider's back (the draw handler, a pick). Lot 4.8b, item 6 (the owner's
decision: what is heavy recalculates only when it ends; every slider here costs 60 to 5000 ms a step,
docs/fase-4-desempenho.md): a pause rebuilds nothing either, the locks wait for the release, built ONCE there.
Esc reads the model back from its text, whose state is the one the tables were built from: nothing to rebuild.

In background mode (the tests) there is no event loop: changed() commits at once, as the sliders always did."""
import time

import bpy

from . import errorlog, live, modal_keys, owner, select, session, slow

QUIET = 0.3                                                         # seconds without a change before the preview is rebuilt
TICK = 0.05                                                         # the watcher's timer
# events that do not end an interaction that is not a drag: motion, timers, the wheel (Ctrl+wheel steps a slider)
# and the modifier keys (held for it)
CONTINUE = frozenset({'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER', 'TIMER0', 'TIMER1', 'TIMER2', 'TIMER_REPORT',
                      'TIMERREGION', 'TIMER_JOBS', 'TIMER_AUTOSAVE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE',
                      'WHEELOUTMOUSE', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_ALT', 'RIGHT_ALT',
                      'OSKEY', 'NONE'})

_S = {'roots': set(), 'last': 0.0, 'dirty': False, 'starting': False, 'running': False, 'gen': 0, 'win': 0, 'later': False}


def pending():
    """True while a change waits for its rebuild and undo step (the tests, the simulations)"""
    return bool(_S['roots']) or _S['running'] or _S['starting']


def decide(held, cancel, ev_type, ev_value, dirty, still, pending=False):
    """What the watcher does with one event (pure; tests/test_blender_deferred.py): 'end' (rebuild if needed, save, push the
    step; the event goes on), 'pause' (a drag stopped: nothing built, keep watching), 'cancel' (the UI puts the start value back
    after this event: end on the next tick) or 'pass'. held: the drag's button; cancel: Esc seen during the drag;
    dirty: the model changed since the last rebuild; still: seconds since the last change; pending: a change waits
    for its undo step that needs no rebuild (a value nothing reads: lot ux8, part 2)."""
    if ev_type == 'TIMER':
        if cancel:
            return 'end'
        if (dirty or pending and not held) and still >= QUIET:
            return 'pause' if held else 'end'
        return 'pass'
    if held:
        if ev_type == 'LEFTMOUSE' and ev_value == 'RELEASE' or ev_type == 'WINDOW_DEACTIVATE':
            return 'end'
        if ev_type in ('ESC', 'RIGHTMOUSE') and ev_value == 'PRESS':
            return 'cancel'
        return 'pass'
    if ev_type == 'WINDOW_DEACTIVATE' or ev_type not in CONTINUE and ev_value != 'NOTHING':
        return 'end'
    return 'pass'


def changed(context, root, rebuild=True):
    """A setter changed the model of this root in memory and touched the locks: rebuild and push the undo step when
    the interaction ends (at once in background mode). rebuild=False: nothing the preview builds reads the value (a
    dimmed slider, Merge Shared Trunks): saved with its undo step, nothing rebuilt, nothing reported."""
    if bpy.app.background:
        _commit(context, root, rebuild)
        return
    _S['roots'].add(root.name); _S['last'] = time.perf_counter(); _S['dirty'] = _S['dirty'] or rebuild
    if rebuild:
        live.hold(root.name)                                        # (4.2c: the locks wait for the pause, when cheap, or the release)
        _S['later'] = False                                         # (a new value: the next pause says what it costs)
    select.redraw(context)                                          # the rest of the panel (the list's steps, the project step) follows the value
    if not (_S['running'] or _S['starting']):
        _S['starting'] = True
        _S['win'] = next((i for i, w in enumerate(context.window_manager.windows) if w == context.window), 0)
        bpy.app.timers.register(_start, first_interval=0.0, persistent=True)   # (persistent: a load in between finds it and clears `starting`)


def forget():
    """An undo, a redo or a file load: what was pending belongs to the data that went — the change never saved goes
    with it (the model is read from the restored text again: an undo that restores the same text would otherwise
    keep it), the watcher, if any, ends without a step."""
    _discard()
    _S['gen'] += 1
    if not bpy.app.timers.is_registered(_start):
        _S['starting'] = False


def _discard():
    """the pending change dropped: the models of its roots read from their texts again"""
    for n in _S['roots']:
        session.forget_model(n)
        live.hold(n, False)
    if _S['later']:
        _status(bpy.context, None)
    _S['roots'].clear(); _S['dirty'] = False; _S['later'] = False


def _release_holds():
    for n in _S['roots']:
        live.hold(n, False)


def _estimate():
    """(curves, seconds) the release will rebuild, over every changed root (live.estimate)"""
    tot = 0.0; n = 0
    for root in _roots():
        try:
            k, sec = live.estimate(root, session.get_project(root))
        except Exception:
            continue
        tot += sec; n += k
    return n, tot


def _roots():
    return [r for r in (bpy.data.objects.get(n) for n in sorted(_S['roots'])) if r is not None and session.is_root(r)]


@slow.timed('a conversion slider committed')
def _commit(context, root, rebuild=True):
    session.save_project(root, session.get_project(root))
    if rebuild:
        live.update(context, root)
    select.redraw(context)


@slow.timed('a conversion slider: the watcher started')
def _start():
    """the timer that starts the watcher in the window of the slider, over a 3D View; when it cannot be started the
    change is committed at once (EXEC: one undo step), or dropped when even that fails — never left hanging"""
    _S['starting'] = False
    if not _S['roots'] or _S['running']:
        return None
    try:
        wins = list(bpy.context.window_manager.windows)
        win = wins[_S['win']] if _S['win'] < len(wins) else (wins[0] if wins else None)
        area = next((a for a in win.screen.areas if a.type == 'VIEW_3D'), None) if win is not None else None
        over = {'window': win, 'screen': win.screen} if win is not None else {}
        if area is not None:
            over.update(area=area, region=next((r for r in area.regions if r.type == 'WINDOW'), None))
        with bpy.context.temp_override(**over):
            if area is not None and 'RUNNING_MODAL' in bpy.ops.hairmesh.commit_setting('INVOKE_DEFAULT', True):
                return None
            if _S['roots'] and not _S['running']:
                bpy.ops.hairmesh.commit_setting('EXEC_DEFAULT', True)
    except Exception as e:                                          # a timer must never raise
        errorlog.record('timer: deferred commit')
        print('HairMesh: the setting could not be committed:', repr(e))
    if _S['roots'] and not _S['running']:
        _discard()
        print('HairMesh: a conversion setting was dropped (no window to commit it from)')
    return None


def _rebuild(context, op=None):
    """the preview of the changed roots, with the wait cursor; a row-table rebuild that took long is reported (one
    that only refilled grids and meshes has nothing to say: live.last_rebuild is read from this very update)"""
    _S['dirty'] = False; _S['later'] = False
    win = getattr(context, 'window', None)
    if win is not None:
        win.cursor_modal_set('WAIT')
    try:
        for root in _roots():
            live.last_rebuild.pop(root.name, None)
            t0 = time.perf_counter()
            held = live.held(root.name)
            live.hold(root.name, False)
            try:
                live.update(context, root, commit=False)          # (the change may still be dropped by Esc: nothing written yet, 3.7)
            finally:
                if held and _S['running']:
                    live.hold(root.name)                          # (the drag goes on: held again until the next pause or the release)
            if root.name in live.last_rebuild:
                msg = live.rebuild_report(root, time.perf_counter() - t0)
                if msg and op is not None:
                    op.report({'INFO'}, msg)
    finally:
        if win is not None:
            win.cursor_modal_restore()
    select.redraw(context)


def _finish(context, op):
    """the end of the interaction: rebuild what is left, save; True when the model's text changed (a step to push)"""
    changed_any = False
    _release_holds()
    _status(context, None)                                          # (before the rebuild: live's announcement may follow)
    try:
        if _S['dirty']:
            _rebuild(context, op)
        for root in _roots():
            txt = session._text(root, 'hairmesh_text')
            before = txt.as_string() if txt is not None else None
            session.save_project(root, session.get_project(root))
            changed_any = changed_any or txt is None or txt.as_string() != before
    finally:
        _S['roots'].clear(); _S['dirty'] = False; _S['later'] = False
        select.redraw(context)
    return changed_any


def _restore(context, op=None):
    """Esc during a drag: the models read back from their texts (unsaved since the drag began: the model before it,
    exactly), the preview rebuilt from them; nothing to push"""
    roots = _roots()
    _release_holds()
    _status(context, None)
    try:
        for root in roots:
            session.forget_model(root.name)
        _S['dirty'] = True
        _rebuild(context, op)                                       # (4.2c: the text read back is the state the tables were built from — nothing rebuilt when the drag rebuilt nothing)
    finally:
        _S['roots'].clear(); _S['dirty'] = False; _S['later'] = False
        select.redraw(context)


class HAIRMESH_OT_commit_setting(bpy.types.Operator):
    """Rebuild the preview and keep the new setting (one undo step) when the slider is let go"""
    bl_idname = 'hairmesh.commit_setting'
    bl_label = 'HairMesh Conversion Setting'                        # the undo step's name (Edit > Undo History)
    bl_options = {'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        _S['running'] = False
        return {'FINISHED'} if _finish(context, self) else {'CANCELLED'}

    def invoke(self, context, event):
        if _S['running']:
            return {'CANCELLED'}
        self.held = event.type == 'LEFTMOUSE' and event.value == 'PRESS'
        self.esc = False                                           # Esc / RMB seen during the drag (not `cancel`: that is the method)
        self.gen = _S['gen']
        _S['running'] = True
        self._timer = context.window_manager.event_timer_add(TICK, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context, through):
        context.window_manager.event_timer_remove(self._timer)
        _S['running'] = False
        if self.gen != _S['gen']:                                   # an undo or a load in between: nothing to keep
            done = False
        elif self.esc:
            _restore(context, self); done = False                   # Esc: the model before the drag, no step
        else:
            done = _finish(context, self)
        return ({'FINISHED'} if done else {'CANCELLED'}) | ({'PASS_THROUGH'} if through else set())

    def cancel(self, context):
        """Blender removed the handler (a file loaded, the window closed): the flag must not stay up"""
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        _S['running'] = False
        _release_holds()
        _status(context, None)

    def modal(self, context, event):
        if self.gen != _S['gen']:
            return self._end(context, event.type != 'TIMER')
        what = decide(self.held, self.esc, event.type, event.value, _S['dirty'] and not _S['later'], time.perf_counter() - _S['last'], bool(_S['roots']))
        if what == 'end':
            return self._end(context, event.type != 'TIMER')
        if what == 'pause':                                         # (the release builds it, once)
            _S['later'] = True
            n, sec = _estimate()
            _status(context, f'HairMesh: the locks follow on release ({n} curves, ~{sec:.1f} s) · Esc: back as it was')
        elif what == 'cancel':
            self.esc = True
        return {'PASS_THROUGH'}


def _status(context, text):
    """the status bar's text (None: Blender's own back); a text owns it against the clearing timer of live's last
    announcement"""
    if text is not None:
        live._said_n[0] += 1
    try:
        ws = getattr(context, 'workspace', None)
        if ws is not None:
            ws.status_text_set(text if text is not None else modal_keys.current())
    except Exception:
        pass


def register():
    bpy.utils.register_class(HAIRMESH_OT_commit_setting)


def unregister():
    bpy.utils.unregister_class(HAIRMESH_OT_commit_setting)
    if bpy.app.timers.is_registered(_start):
        bpy.app.timers.unregister(_start)
    _release_holds()
    _S['roots'].clear(); _S['dirty'] = False; _S['running'] = False; _S['starting'] = False; _S['later'] = False
