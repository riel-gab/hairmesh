# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The live preview of the walls (D4): one regenerable, unselectable Mesh object per group, "<group> live", holding
the grids of the group's walls (surfaceData), rebuilt from the row tables after every edit. The row tables are
kept per root (session.state) so a rebuild after an edit is incremental (buildRowTables(changed, ctx)), like the
original's frame loop; and, as the original's rebuildSurfaces (2950), only the walls whose lock was rebuilt
(LOCKS_REBUILT) or that are dirty themselves (a setting changed) get their grid computed again: the others keep
theirs (session.state['grids']). Every grid carries a revision (state['grid_rev']) and every live mesh remembers the
revisions it was built from — on the mesh datablock itself (SHOWN_KEY), so an undo that restores the mesh restores
what it shows with it (lot 3.7-estab, bug 1: kept in Python, it survived the undo the mesh did not, and Convert, Ctrl+Z,
Convert left the walls of the undone state under the orange meshes) —, so a group's mesh is made again exactly when a
grid it shows is newer or gone, whoever computed that grid first (the draw handler's overlay of a selected wall asks for the same
grids through grid_of). Each face of a live mesh carries the id of its wall (the FACE_ATTR attribute), so a click on
a live wall can select it (pick.wall_under).

What counts as changed (16/10/2026, docs/fase-3-desempenho.md, the lost update of the manual test): the frame loop
of the original knows the curves it rebuilt in the frame; here several parties rebuild dirty curves — settle, the
draw handler (to draw them), the operators — and the one who rebuilt the curve cleared its dirty flag, so a rebuild
that asked settle alone saw nothing changed and left the lock as it was when a redraw came between the last move and
the rebuild (the second drag of the report). row_tables now remembers the sample list of every curve the tables were
built from (state['built']: rebuildCurve always makes a new list) and treats a curve whose list is another object
as changed, whoever rebuilt it. Edits that change the loops without touching a curve's shape (a guide switched, a
group's conv, Clean topology, a move between groups) say which curves' locks must go (touch): the incremental path
with that set is bit-identical to the full rebuild the original runs there (tests/test_rows_incremental), at a
fraction of the cost; invalidate_rows (from scratch) stays for the Rebuild button and for what nobody can name.
After an undo the text is parsed again and the model is a new instance: the tables of the previous instance are kept
when everything they depend on besides the curves' shapes is the same (scene.tables_signature), and the curves whose
samples differ by value are the changed ones (_inherit) — the click after a Ctrl+Z no longer rebuilds every lock.

When the preview is rebuilt (decided 15/10/2026, after the undo of a deleted wall brought back an orphan live object
and Blender crashed twice right after a wall was made): every operator that changes the model calls update() before
it returns, so the live objects are part of the operator's own undo step and Blender's memfile undo restores model
and preview together. Blender assumes that bpy.data only changes inside undo steps: a timer rebuilding the meshes
0.25 s after the operator left the step with the old preview and the live state out of sync with it, and the next
undo reused the in-memory Collection (changed by the timer) while re-reading the Object from the step, which came
back linked to nothing ("Object 'root live' has no evaluated mesh data" on the next click). The debounced timer
(schedule) now serves only the drag of anchors, inside a modal operator whose release calls update() again; a
pending timer is cancelled by update(). Since 16/10/2026 the timer is re-armed by every move, so it fires DEBOUNCE
after the cursor stops (a drag never stalls on a rebuild while the mouse moves; a pause shows the wall). Meshes are
refilled in place (clear_geometry + from_pydata), never replaced by new datablocks, so an edit changes a mesh's
content and no ID is created or freed outside an operator.

The grids go through core.meshdata.clean_mesh before from_pydata (finite coordinates, faces with 3+ distinct indices
in range), the per-face attribute is set after validate() and only when the counts still agree, and one wall's or
one group's failure is reported, never raised: a degenerate wall (the sides of a triangle, two Ys tip to tip) must
leave the preview standing.

With the preview off (Scene.hairmesh.live_preview, the original's "NURBS" toggle, S.showSurfaces 11327) the
"<group> live" objects are hidden and not refilled, but the grids are still computed after every edit, as the
original's frame loop rebuilds the surfaces whether they are shown or not (rebuildSurfaces, 12073): the selected
wall's outline (draw.py) and the wall picking (pick.wall_under) read the same cache, so both work without the mesh
(1a of the manual test of 15/10/2026; the toggle's "no object" side is ours: docs/features-adicionadas.md).

The painted weights of the Extras show on these very meshes (lot 4.8b, item 5: PAINT_ATTR, a color attribute on the
modifiers' own vertices), only while the weight brush runs: nothing is drawn over the mesh, so the colors have the shape
the mesh has (a surface on top of it fought it where the twist and the mask moved the rings) and the wire stays on top.

In background mode timers never fire: update() is synchronous there as everywhere else."""
import itertools
import json
import time
from contextlib import contextmanager

import bpy
import numpy as np
from bpy.app.handlers import persistent

from ..core import edit
from ..core import rows as RW
from ..core import scene as SC
from ..core import surfaces as SF
from ..core.meshdata import clean_mesh
from ..core.names import group_name, unique_names
from . import errorlog, modal_keys, naming, owner, session, slow
from .coords import to_blender

DEBOUNCE = 0.25
FACE_ATTR = 'hairmesh_wall'                                         # per-face int attribute of a live mesh: the wall's item id
SHOWN_KEY = 'hairmesh_shown'                                      # on a live mesh's datablock: shown_key of the grids it shows (restored by an undo with the mesh)
TUBES_KEY = 'hairmesh_tubes'                                        # on a live mesh's datablock: where its modifiers sit, [[tube id, first vertex, vertices, ring size], ...] (the same: an undo restores it with the mesh)
PAINT_ATTR = 'hairmesh_paint'                                       # per-vertex color attribute of a live mesh while the weight brush runs: the painted weights
NEUTRAL = 0.8                                                       # the rest of a painted mesh: the grey a mesh with no material has in Solid
WAIT_AFTER = 0.4                                                    # a rebuild longer than this (seconds) reports its time: the user knows why the UI stalled
_pending = {}                                                       # root name -> the timer function waiting to fire (a drag in progress)
_reported = set()                                                   # (root, wall id) whose grid failed: printed once
last_rebuild = {}                                                   # root name -> (seconds, locks rebuilt or None for all) of the last rebuild (the header, the tests)
_revs = itertools.count(1)                                          # grid revisions: one counter for every wall of every root, never reused (3.7: a converted mesh's signature names them)
builds = {}                                                         # root name -> how many row builds rebuilt a lock (4.2c: the stress test's check)
_held = set()                                                       # root names whose tables wait for a release (hold)
PER_CURVE = 0.015                                                   # seconds per curve of a row build before one was measured
_cost = {'per_curve': PER_CURVE}                                    # the measured mean (estimate)
EXPENSIVE = 0.3                                                     # a rebuild estimated above this waits for the release of a drag (4.2c, item 2)
WARN_AT = 0.3                                                       # a rebuild estimated above this says so before it starts (4.2c, item 4)
PROGRESS_AT = 1.0                                                   # ... and above this counts the locks in the cursor (progress_begin)
_toast = [None, 0.0, 0]                                             # notify(header=True): the text, until when (time.monotonic), its number
SAID_FOR = 4.0                                                      # seconds the status bar keeps "N locks rebuilt in X s"
_ui = {}                                                            # root name -> the context of the operator rebuilding it now (update): the warnings go there
_said = {}                                                          # root name -> the announcement of the rebuild running now (_announce)
_said_n = [0]                                                       # announcements made (the timer clears the status bar of the last one only)
_painting = set()                                                   # root names whose weight brush runs: their meshes carry the colors
_paint_stale = set()                                                # ... and an undo restored their meshes: the modal's next event puts the colors right (a handler never touches a mesh)


def _measured(seconds, n):
    """the mean cost of a curve's lock, from a build that rebuilt enough of them to say (estimate)"""
    if n >= 10 and seconds > 0:
        _cost['per_curve'] = 0.7 * _cost['per_curve'] + 0.3 * seconds / n


def hold(root_name, on=True):
    """4.2c, item 2: while a slider or a ring is dragged and its rebuild is expensive, the root's tables and grids
    stay as they are — the model changes and is drawn (the rings, the zone), the locks wait for the release (its
    update()); off again before that update"""
    (_held.add if on else _held.discard)(root_name)


def held(root_name):
    return root_name in _held


@contextmanager
def as_they_are(root, P):
    """In this block the tables and the grids stay as they are, what waits for a rebuild keeps waiting (built first when
    there are none): a Density stroke reads the locks it paints over and the walls it looks through, and the stroke before
    it changed where their loops go, not their surfaces"""
    st = session.state(root, P)
    if st.get('rows') is None or st.get('built') is None:
        grids(root, P)
    was = root.name in _held
    _held.add(root.name)
    try:
        yield
    finally:
        if not was:
            _held.discard(root.name)


def estimate(root, P):
    """(curves, seconds): what the next row build would rebuild — the curves changed since the tables were built
    (changed_since, the touches, the curves waiting to be resampled) and, for a disc whose ring is among them, the
    curves it pierced and pierces — times the measured mean per curve. From scratch without tables. A guess for the
    warnings and for the drags (no flood through shared anchors, no history): never a result."""
    from ..core.guides import guideCurves
    st = session.runtime(root)
    if st is None or st.get('P') is not P or st.get('rows') is None or st.get('built') is None:
        n = len(P.curves())
        return n, n * _cost['per_curve']
    ids = set(SC.changed_since(P, st['built'], st.get('built_walls') or {}, st.get('built_guides') or {}, st.get('touched') or (), st.get('built_crowns') or {}))
    ids.update(c.id for c in P.curves() if c.dirty)
    bg = st.get('built_guides') or {}
    for g in P.guides():
        if g.kind == 'disc' and any(r in ids for r in (g.rings or [])):
            ids.update((bg.get(g.id) or (None, []))[1]); ids.update(guideCurves(P, g))
    n = sum(1 for i in ids if (c := P.items.get(i)) is not None and c.type == 'curve' and not c.ring)
    return n, n * _cost['per_curve']


def _views(context):
    screen = getattr(context, 'screen', None)
    return [a for a in (screen.areas if screen is not None else ()) if a.type == 'VIEW_3D']


def _say(context, text, header=True, status=None):
    """the status bar and the 3D Views' headers (None: Blender's own back); `status`: a draw function for the status
    bar instead of the text (status_text_set takes one: the progress bar)"""
    try:
        ws = getattr(context, 'workspace', None)
        if ws is not None:
            ws.status_text_set(status if status is not None else text if text is not None else modal_keys.current())
    except Exception:
        pass
    if header:
        for a in _views(context):
            try:
                a.header_text_set(text)
            except Exception:
                pass


def _locks_of(P, ctx, curves):
    """how many locks the curves belong to (the tables' locks; none yet: the walls' components)"""
    if ctx is not None and ctx.locks:
        of = {c.id: L.key for L in ctx.locks for c in L.cs}
        return len({of.get(i, i) for i in curves})
    return len(RW.surfaceComponents(P)) if curves is None else len(curves)


def _announce(root, P, ctx, changed):
    """4.2c, item 4 (the owner's request): before a row build estimated above WARN_AT seconds — the curves to rebuild
    (`changed`, None: every lock) times the measured mean — the status bar and the 3D Views' headers say "Rebuilding N
    locks (~X s)…" (drawn now: a draw is forced, the root held so the draw builds nothing), the cursor waits, and above
    PROGRESS_AT it counts the locks built (the cursor's number). Only inside an operator's update(); never in -b."""
    context = _ui.get(root.name)
    if context is None or bpy.app.background:
        return
    cur = [i for i in changed if (c := P.items.get(i)) is not None and c.type == 'curve' and not c.ring] if changed is not None else None
    n_curves = len(P.curves()) if cur is None else len(cur)
    sec = n_curves * _cost['per_curve']
    if sec <= WARN_AT:
        return
    n = _locks_of(P, ctx if cur is not None else None, cur)
    _said_n[0] += 1
    msg = f'HairMesh: rebuilding {n} lock{"s" if n != 1 else ""} (~{sec:.1f} s)…'
    rec = {'n': n, 'sec': sec, 't0': time.perf_counter(), 'progress': False, 'cursor': False, 'no': _said_n[0], 'msg': msg, 'done': 0,
           'next': PROGRESS_STEP, 'context': context, 'root': root.name,
           'over': {k: v for k in ('window', 'screen', 'area', 'region') if (v := getattr(context, k, None)) is not None}}
    _said[root.name] = rec
    _say(context, msg, status=_status_bar(rec) if sec > PROGRESS_AT else None)
    _draw_now(rec)
    win = getattr(context, 'window', None)
    if win is not None:
        try:
            win.cursor_modal_set('WAIT'); rec['cursor'] = True
        except Exception:
            pass
    if sec > PROGRESS_AT:
        try:
            context.window_manager.progress_begin(0, max(n, 1)); rec['progress'] = True
            rec['wm'] = context.window_manager
        except Exception:
            pass


class heavy:
    """4.3: a long core step inside an operator that is no row build of update() (Unite Locks: the row tables of the
    pairs it looks at; …): above WARN_AT estimated seconds the status bar and the headers say "HairMesh: <what>
    (~X s)…" first (a forced draw, the root held so it builds nothing), the cursor waits; after, "HairMesh: <what>
    done in X s" for SAID_FOR seconds. Nothing in -b."""

    def __init__(self, context, root, what, sec):
        self.on = not bpy.app.background and sec > WARN_AT and context is not None
        self.context, self.root, self.what, self.sec = context, root, what, sec

    def __enter__(self):
        if self.on:
            c = self.context
            _said_n[0] += 1
            _say(c, f'HairMesh: {self.what} (~{self.sec:.1f} s)…')
            self.rec = {'context': c, 'root': self.root.name, 'cursor': False, 'over': {k: v for k in ('window', 'screen', 'area', 'region') if (v := getattr(c, k, None)) is not None}}
            _draw_now(self.rec)
            try:
                c.window.cursor_modal_set('WAIT'); self.rec['cursor'] = True
            except Exception:
                pass
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if self.on:
            if self.rec['cursor']:
                try:
                    self.context.window.cursor_modal_restore()
                except Exception:
                    pass
            _say(self.context, None)
            notify(self.context, f'{self.what} done in {time.perf_counter() - self.t0:.1f} s')
        return False


PROGRESS_STEP = 0.5                                                 # the status bar's bar is drawn again halfway (a forced draw: ~10 ms, and Blender's own console line)


def _status_bar(rec):
    """the status bar while a long rebuild runs: the text and a progress bar (UILayout.progress)"""
    def draw(header, context):
        lay = header.layout
        lay.label(text=rec['msg'], icon='TIME')
        try:
            lay.progress(factor=min(1.0, rec['done'] / max(rec['n'], 1)), type='BAR', text=f'{min(rec["done"], rec["n"])} / {rec["n"]} locks')
        except Exception:
            pass
    return draw


def _draw_now(rec):
    """the window drawn now, inside the operator (the status bar and the headers before the wait): the root held so
    the draw builds nothing; redraw_timer resets a modal cursor, put back after"""
    if rec is None or rec.get('context') is None:
        return
    held_before = rec['root'] in _held
    _held.add(rec['root'])
    try:
        # (inside the context the operator had: DRAW_WIN_SWAP leaves the area and the region of the context it ran in
        # empty — the gizmo's release found context.area None after it; temp_override puts them back on exit)
        with rec['context'].temp_override(**rec['over']):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except Exception:
        pass
    finally:
        if not held_before:
            _held.discard(rec['root'])
    if rec.get('cursor'):
        win = getattr(rec['context'], 'window', None)
        try:
            win.cursor_modal_set('WAIT')
        except Exception:
            pass


def _progress(root_name, done):
    """the row build's hook (RowBuild.PROGRESS): the cursor's count of the locks built, and every PROGRESS_STEP of them
    the status bar's bar drawn again"""
    rec = _said.get(root_name)
    if rec is None or not rec['progress']:
        return
    rec['done'] = done
    try:
        rec['wm'].progress_update(min(done, rec['n']))
    except Exception:
        pass
    if done >= rec['next'] * rec['n'] and done < rec['n']:
        rec['next'] += PROGRESS_STEP
        _say(rec['context'], rec['msg'], header=False, status=_status_bar(rec))   # (set again: the status bar is tagged for the draw)
        _draw_now(rec)


def _announced(root, context):
    """the end of an announced rebuild: the cursor back, the real time in the status bar for SAID_FOR seconds"""
    rec = _said.pop(root.name, None)
    if rec is None:
        return
    dt = time.perf_counter() - rec['t0']
    if rec['progress']:
        try:
            rec['wm'].progress_end()
        except Exception:
            pass
    win = getattr(context, 'window', None)
    if rec['cursor'] and win is not None:
        try:
            win.cursor_modal_restore()
        except Exception:
            pass
    n = rec['n']
    _say(context, None)
    _say(context, f'HairMesh: {n} lock{"s" if n != 1 else ""} rebuilt in {dt:.1f} s', header=False)
    ws = getattr(getattr(context, 'workspace', None), 'name', None); no = rec['no']

    def clear():
        # (a later announcement owns the status bar now; the workspace looked up by name: a file loaded meanwhile freed it)
        w = bpy.data.workspaces.get(ws) if ws is not None and _said_n[0] == no else None
        if w is not None:
            try:
                w.status_text_set(modal_keys.current())
            except Exception:
                pass
        return None
    bpy.app.timers.register(clear, first_interval=SAID_FOR)


def schedule(root_name, delay=DEBOUNCE):
    """Rebuilds the previews of this root `delay` after the LAST call (every move re-arms the timer: the walls
    follow when the cursor pauses, never while it moves). Only for the moves of a drag, inside a modal operator:
    its release must call update() so the undo step gets the result."""
    _drag_sync(root_name)
    if bpy.app.background:
        return
    cancel(root_name)

    def fire():
        if _pending.get(root_name) is not fire:
            return None                                             # cancelled by update()
        _pending.pop(root_name, None)
        root = bpy.data.objects.get(root_name)
        if root is not None and session.is_root(root):
            try:
                if estimate(root, session.get_project(root))[1] > EXPENSIVE:
                    return None                                     # (4.2c, item 2: an expensive rebuild waits for the release's update)
                rebuild(bpy.context, root)
            except Exception as e:                                  # a timer must never raise
                errorlog.record('timer: live preview')
                print('HairMesh live preview:', repr(e))
        return None
    _pending[root_name] = fire
    bpy.app.timers.register(fire, first_interval=delay)


def _drag_sync(root_name):
    """4.3 (D4.8): every move of a drag runs the crown part of the frame — the pair of a moved anchor of a symmetric
    crown and the anchors attached to a moved connector follow while the cursor moves, in memory (the release's
    save_project writes them; Esc puts them back: crowntool.syncSnapshot)"""
    root = bpy.data.objects.get(root_name)
    if root is None or not session.is_root(root):
        return
    try:
        from ..core.crowntool import frameSync
        P = session.get_project(root)
        moved, toasts = frameSync(P)
        if moved:
            ids = {id(a) for a in moved}
            touch(root, [c.id for c in P.curves() if any(id(a) in ids for a in c.anchors)])
        if toasts:
            notify(bpy.context, toasts[len(toasts) - 1], header=True)   # (4.4, 2c: in sight, as the heavy warning)
    except Exception as e:
        errorlog.record('crown sync')
        print('HairMesh crown sync:', repr(e))


def notify(context, text, header=False):
    """a message in the status bar for SAID_FOR seconds (4.3: an anchor that came off its connector); `header` (4.4,
    2c): in the 3D Views' headers too, kept in front of the modals' own header texts while it lasts (live.header)"""
    _said_n[0] += 1
    no = _said_n[0]
    if header:
        _toast[:] = [f'HairMesh: {text}', time.monotonic() + SAID_FOR, _toast[2] + 1]
    _say(context, f'HairMesh: {text}', header=header)
    ws = getattr(getattr(context, 'workspace', None), 'name', None)

    def clear():
        w = bpy.data.workspaces.get(ws) if ws is not None and _said_n[0] == no else None
        if w is not None:
            try:
                w.status_text_set(modal_keys.current())
            except Exception:
                pass
        return None
    tn = _toast[2]

    def clear_header():
        # (the toast's own number: a later toast owns the headers; a HairMesh modal running keeps its header — its
        # next live.header drops the toast — and the headers are looked at again a moment later)
        if _toast[2] != tn:
            return None
        try:                                                        # (a timer never raises)
            if any(o.bl_idname.startswith('HAIRMESH_OT_') for win in bpy.context.window_manager.windows for o in win.modal_operators):
                return 0.25
            for win in bpy.context.window_manager.windows:
                for a in win.screen.areas:
                    if a.type == 'VIEW_3D':
                        a.header_text_set(None)
        except Exception:
            pass
        _toast[0] = None
        return None
    if not bpy.app.background:
        bpy.app.timers.register(clear, first_interval=SAID_FOR)
        if header:
            bpy.app.timers.register(clear_header, first_interval=SAID_FOR)


def header(area, text, keys=None):
    """a modal's header text with the toast of notify(header=True) in front while it lasts — the G / gizmo / Select drags
    rewrite their headers at every move, and the toast must stay in sight (4.4, 2c). `keys`: its keys in the status bar
    (G10, modal_keys); text None: Blender's own header and status bar back"""
    t = _toast[0] if _toast[0] is not None and time.monotonic() < _toast[1] else None
    try:
        area.header_text_set(text if t is None else (t if text is None else f'{t}  ·  {text}'))
    except Exception:
        pass
    if text is None:
        modal_keys.clear(bpy.context)
    elif keys is not None:
        modal_keys.show(bpy.context, keys)


def cancel(root_name=None):
    """Drops the pending timers (one root's, or all): nothing may touch bpy.data after the operator returned."""
    names = [root_name] if root_name is not None else list(_pending)
    for n in names:
        fn = _pending.pop(n, None)
        if fn is not None and bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)


def update(context, root, commit=True):
    """The preview follows the model NOW, inside the operator (before Blender pushes its undo step). Never raises:
    the operator's own result must not depend on the preview. A rebuild from scratch (no tables yet, or everything
    invalidated) shows the wait cursor meanwhile. `commit=False` (3.7): a look at a state that may still be dropped
    (the pause of a slider's drag, the Esc of a drag): the pending meshes are rebuilt in memory and shown, but nothing
    is written into the project's texts — the next save_project carries them."""
    cancel(root.name)
    _ui[root.name] = context                                        # (4.2c, item 4: a heavy row build below says so first: _announce)
    st = session.runtime(root)
    full = st is None or (st.get('rows') is None and not (st.get('prev') or {}).get('rows'))
    full = full or bool(root.get('hairmesh_conv'))                  # (3.7: a pending conversion's meshes may be rebuilt too)
    win = getattr(context, 'window', None)
    if full and win is not None:
        try:
            win.cursor_set('WAIT')
        except Exception:
            win = None
    try:
        out = rebuild(context, root)
    except Exception as e:
        errorlog.record('live: rebuild')
        print('HairMesh live preview:', repr(e))
        out = {}
    try:                                                            # 4.1: what the row build changed in the model, into the text (this operator's step)
        save_model_changes(root, commit)
    except Exception as e:
        errorlog.record('live: project text')
        print('HairMesh project text:', repr(e))
    try:                                                            # 3.7: the pending meshes follow the edit, the mesh objects the model
        from . import meshes
        P = session.get_project(root)
        meshes.follow_conversion(context, root, P, save=commit)
        meshes.sync(context, root, P, commit=commit)
    except Exception as e:
        errorlog.record('live: meshes')
        print('HairMesh meshes:', repr(e))
    try:                                                            # (4.2c: the click's arrays of the walls made now, inside the edit — the first click after it stays under 50 ms)
        from . import pick
        if not bpy.app.background:
            done = last_rebuild.get(root.name)
            with slow.part("the click's arrays of the walls"):
                pick.wall_soup(root, session.get_project(root))
            if done is not None:
                last_rebuild[root.name] = done                      # (the report of this update's build, not the arrays' look at the tables: deferred reads it)
    except Exception as e:
        errorlog.record('live: picking')
        print('HairMesh picking:', repr(e))
    finally:
        _ui.pop(root.name, None)
        _announced(root, context)
        said = session._sync_said.pop(root.name, None)
        if said:
            notify(context, said, header=True)                      # (4.3: updateAttachments' toast — an anchor came off its connector; 4.4: the headers too)
        if full and win is not None:
            try:
                win.cursor_set('DEFAULT')
            except Exception:
                pass
    return out


def live_objects(root):
    """The "<group> live" objects of the root by group id: parented to it, or stamped with its name (an object
    that lost its parent is still ours and is taken down or refilled, never left as a ghost)."""
    return {o.get('hairmesh_group_id', -1): o for o in bpy.data.objects
            if o.get('hairmesh_type') == 'live' and (o.parent == root or o.get('hairmesh_root') == root.name)}


def _current_state(root):
    """the runtime state of the model as it is now (a text read again since: that instance's, with the old one as its
    `prev`), or None without a model"""
    try:
        return session.state(root, session.get_project(root))
    except Exception:
        return session.runtime(root)


def invalidate_rows(root):
    """The original's S.surfDirty where nobody can say which locks are touched: the next rebuild builds the row
    tables from scratch and every grid again (the Rebuild button; a head changed)."""
    st = _current_state(root)
    if st is not None:
        st['rows'] = None; st['built'] = None; st['sig'] = None; st['built_walls'] = None; st['built_guides'] = None; st['built_crowns'] = None; st['touched'] = set(); st['grids'] = {}; st['grid_rev'] = {}; st['wall_sig'] = {}
        st.pop('prev', None)


def touch(root, curve_ids):
    """The original's S.surfDirty where the curves whose locks changed ARE known (a guide switched: the curves it
    hits; a group's conv or a move between groups: the curves inside; Clean topology: the wall's two curves): the
    next rebuild rebuilds those locks and what they touch through shared anchors, and leaves the others — the same
    tables a rebuild from scratch would give (tests/test_rows_incremental.TestIncrementalEqualsFull). Walls made,
    removed or switched need no call: row_tables sees them (scene.changed_since) and counts their curves as changed.
    (4.2c: into the state of the model as it is now — never the instance before a parse, whose touches are dropped)"""
    st = _current_state(root)
    if st is not None:
        st.setdefault('touched', set()).update(i for i in curve_ids if i is not None)


def revision(root):
    """Bumped whenever a grid changes: the draw handler's cache key for the selected walls' overlay."""
    st = session.runtime(root)
    return st.get('grids_rev', 0) if st is not None else 0


def _inherit(st, P):
    """The tables (and the grids) of the previous Project instance, when the text was parsed again (an undo, a
    redo, a hand edit) and they still fit: what the tables depend on besides the curves' shapes is the same, or
    differs in a way that names its locks (scene.signature_changes: a curve's group, a wall, a group's conv, a
    guide). Returns the RowBuild to continue from, with state['built'] naming the curves whose samples are the very
    ones the tables were built from (the others count as changed), or None (from scratch)."""
    prev = st.pop('prev', None)
    if prev and prev.get('rows') is not None and prev['rows'].HISTORY is not None:
        st['history'] = prev['rows'].HISTORY                         # (kept even when the tables cannot be: the undo of a setting)
    if not prev or prev.get('rows') is None or prev.get('built') is None:
        return None
    old = prev['P']; ctx = prev['rows']
    # (4.2c, item 1: against the state the tables were BUILT from — its signature and the guides' curves then —, never
    # the old instance as it is now: a slider dragged and let go with Esc changed that one in memory only, and the text
    # read back is the very state of the tables, nothing to rebuild)
    r = SC.inherited(prev.get('sig') or SC.tables_signature(old), prev['built'], prev.get('built_guides') or {}, P)
    if r is None:
        return None
    _changed, built = r
    st.pop('history', None)
    ctx.P = P
    for L in ctx.locks:                                             # the cached locks name the curves of the new instance
        L.cs = [it if (it := P.items.get(c.id)) is not None and it.type == 'curve' else c for c in L.cs]
    st['rows'] = ctx; st['built'] = built; st['built_walls'] = dict(prev.get('built_walls') or {}); st['built_guides'] = dict(prev.get('built_guides') or {}); st['built_crowns'] = dict(prev.get('built_crowns') or {})
    st['sig'] = prev.get('sig')
    # (the touches the old instance never built belong to it: compared with the state the tables were built from, the
    # signature and the samples say all that differs — an Esc of a slider no longer rebuilds what the drag touched;
    # the touches of the new instance went to its own state already: touch)
    st['touched'] = set(st.get('touched') or ())
    # the grids of the walls that are the same (a, b, p) stay; their lock, if rebuilt, renews them anyway
    sigs = prev.get('wall_sig') or {}
    st['grids'] = dict(prev.get('grids') or {}); st['grid_rev'] = dict(prev.get('grid_rev') or {})
    st['wall_sig'] = dict(sigs); st['grids_rev'] = prev.get('grids_rev', 0); st['pick_meshes'] = dict(prev.get('pick_meshes') or {})
    st['wall_soup_parts'] = dict(prev.get('wall_soup_parts') or {})   # 4.2 ∥ 4.5, item 1: the click's arrays per wall (by grid revision, never reused)
    st['mesh_sig'] = dict(prev.get('mesh_sig') or {})              # 3.7: what each converted mesh was built from (meshes.py)
    st['baked'] = {**(prev.get('baked') or {}), **(st.get('baked') or {})}   # 4.0: what each baked mesh was baked against (meshes.baking / baked; a release right after an undo marked this very state already)
    for sf in P.surfaces():
        if sf.id in st['grids'] and sigs.get(sf.id) == _wall_sig(sf):
            sf.dirty = False
    return ctx


def _wall_sig(sf):
    return (sf.a, sf.b, sf.parent, json.dumps(sf.p, sort_keys=True, default=str))


def row_tables(root, P):
    """The row tables of the project, incremental after the first build: the curves whose sample list is not the
    one the tables were built from (rebuilt since, by anyone), the curves of the walls made, removed or switched
    since, of the guides edited since, and the curves touched (touch) are `changed` (scene.changed_since).
    While the root is held (hold: a slider or a ring dragged whose rebuild is expensive) the tables stay as they
    are, and what changed waits for the release."""
    st = session.state(root, P)
    ctx = st.get('rows')
    if root.name in _held and ctx is not None and st.get('built') is not None:
        return ctx
    if ctx is None:
        with slow.part('the tables of the state before compared (an undo, a redo)'):
            ctx = _inherit(st, P)
    built = st.get('built') if ctx is not None else None
    extra = []
    if built is not None:
        extra = SC.changed_since(P, built, st.get('built_walls') or {}, st.get('built_guides') or {}, st.pop('touched', None) or (), st.get('built_crowns') or {})
    with slow.part('curves settled'):
        changed = SC.settle(P, extra)['changed']
    if built is not None:
        changed = SC.disc_moved(P, changed, st.get('built_guides') or {})   # (4.2: a disc's ring moved: the locks it pierced and pierces)
    if built is not None and not changed and not extra and st.get('fresh') and ctx.T is not None:
        # 4.2c, item 1: nothing changed since the last build of this very model (a click's picking, a redraw): the tables
        # stand as they are — buildRowTables with an empty `changed` would take every lock back from its cache and run
        # the trunk backfill over no live lock (a no-op), ~12 ms, and built_state ~5 ms, at every click
        ctx.LOCKS_REBUILT = set(); ctx.LOCKS_RESTORED = set(); ctx.MODEL_CHANGED = False; ctx.STALE_CURVES = set()
        last_rebuild[root.name] = (0.0, 0, 0)
        return ctx
    if root.name in _ui and root.name not in _said:
        _announce(root, P, ctx, changed if built is not None else None)
    t0 = time.perf_counter()
    if ctx is None:
        ctx = RW.RowBuild(P)
        ctx.HISTORY = st.pop('history', None) or {}                 # the tables of the states built before, for an undo (core/rowhist.py)
    ctx.PROGRESS = (lambda done, _n=root.name: _progress(_n, done)) if root.name in _said else None   # (rows.py counts the locks built)
    with slow.part('row tables') as what:
        ctx = RW.buildRowTables(P, changed=changed if built is not None else None, ctx=ctx)
        what[0] = 'every lock, from scratch' if ctx.LOCKS_REBUILT is None else f'{len(ctx.LOCKS_REBUILT)} curves'
        if ctx.LOCKS_RESTORED:
            what[0] += f', {len(ctx.LOCKS_RESTORED)} curves back from the history'
    model_changed = ctx.MODEL_CHANGED
    if ctx.STALE_CURVES:
        # 4.1: tipConvert painted an old Tip item into a mask after its locks were built with the old zone; those locks
        # are built again with the mask now, so the tables kept here are the ones a build from scratch gives (the
        # original's Convert builds from scratch, 10001; ours reads these: meshes.tables)
        rebuilt, restored = ctx.LOCKS_REBUILT, set(ctx.LOCKS_RESTORED)
        with slow.part('row tables of the locks of an old Tip item converted', f'{len(ctx.STALE_CURVES)} curves'):
            ctx = RW.buildRowTables(P, changed=sorted(ctx.STALE_CURVES), ctx=ctx)
        model_changed = model_changed or ctx.MODEL_CHANGED
        ctx.LOCKS_REBUILT = None if rebuilt is None else rebuilt | ctx.LOCKS_REBUILT   # (the grids follow both builds)
        ctx.LOCKS_RESTORED |= restored
    dt = time.perf_counter() - t0
    n = len(ctx.LOCKS_REBUILT) if ctx.LOCKS_REBUILT is not None else None
    last_rebuild[root.name] = (dt, n, len(ctx.LOCKS_RESTORED))
    _measured(dt, len(P.curves()) if n is None else n)
    if n is None or n:
        builds[root.name] = builds.get(root.name, 0) + 1            # (4.2c, item 6: the stress test's "no build" check)
    st['rows'] = ctx; st['built'], st['built_walls'], st['built_guides'], st['built_crowns'] = SC.built_state(P)
    st['fresh'] = True                                              # (a build ran for this very model: its build-time state is this one's)
    if n is None or n or extra or changed or st.get('sig') is None:
        try:
            st['sig'] = SC.tables_signature(P)                      # (4.2c: what an undo compares with — the state the tables are built from)
        except Exception:
            st['sig'] = None                                        # (an undo then compares with the old instance as it is)
    if model_changed:
        st['model_changed'] = True                                  # (4.1: written by the next update() of an operator, save_model_changes)
    return ctx


def save_model_changes(root, commit=True):
    """4.1 (D4.9): the row build changes the model itself — tipConvert (2728) paints an old Tip item into a Density
    mask, a converted modifier's lock from a file without sections gets them written down (2604). Whoever built the
    tables (the draw handler, the drag's timer, an operator) only changed the Project in memory and flagged it
    (state['model_changed']); here, called by update() inside an operator, the model goes to the project text
    (session.save_project), so the change is part of that operator's undo step. Nothing is written with
    commit=False (a look that may still be dropped) or when the flag belongs to another instance of the model (the
    text was parsed again since: that instance's own build flags it anew). Returns True when it wrote."""
    st = session.runtime(root)
    if not commit or st is None or not st.get('model_changed'):
        return False
    P = st.get('P')
    if P is None or P is not session.get_project(root):
        return False
    st.pop('model_changed', None)
    with slow.part('project text written (what the row build changed: tipConvert, sections)'):
        session.save_project(root, P)
    return True


def _grid(P, sf, T):
    d = SF.surfaceData(P, sf, T)
    if d is not None and SF.hasTwin(sf):
        d2 = SF.surfaceData(P, SF.twinWall(sf), T)
        if d2 is not None:
            d = SF.concatMeshes([d, d2])
    return d


def grids(root, P):
    """The grid of every wall (surfaceData, the twin concatenated; None when the wall has no lock or its grid
    failed), cached per root and recomputed only for the walls whose lock was rebuilt or that are dirty
    (rebuildSurfaces, 2950-2960). Returns (grids by wall id, the ids whose grid changed or went)."""
    st = session.state(root, P)
    if root.name in _held:
        # (held: the walls as they are until the release — also with no tables yet: the forced draw of the warning of a
        # build from scratch must never start that build again inside it, the review of 4.2c)
        return st.setdefault('grids', {}), set()
    ctx = row_tables(root, P); T = ctx.T; only = ctx.LOCKS_REBUILT
    if only is not None and ctx.LOCKS_RESTORED:
        only = only | ctx.LOCKS_RESTORED                            # (their locks' tables came back: the grids follow)
    g = st.setdefault('grids', {}); rev = st.setdefault('grid_rev', {}); sigs = st.setdefault('wall_sig', {})
    alive = set(); changed = set()
    with slow.part('wall grids') as what:
        _grids_into(P, T, only, g, rev, sigs, alive, changed, root)
        what[0] = f'{len(changed)} walls'
    if changed:
        st['grids_rev'] = st.get('grids_rev', 0) + 1
    return g, changed


def _grids_into(P, T, only, g, rev, sigs, alive, changed, root):
    for sf in P.surfaces():
        alive.add(sf.id)
        if only is not None and not sf.dirty and sf.id in g and sf.a not in only and sf.b not in only:
            continue                                                # its lock didn't change: the grid stays
        sf.dirty = False
        try:
            d = _grid(P, sf, T)
        except Exception as e:                                      # one degenerate wall must not take the preview down
            errorlog.record('live: the grid of a wall')
            d = None
            if (root.name, sf.id) not in _reported:
                _reported.add((root.name, sf.id))
                print(f'HairMesh live preview: the grid of {sf.name} ({sf.id}) failed: {e!r}')
        g[sf.id] = d; rev[sf.id] = next(_revs); changed.add(sf.id); sigs[sf.id] = _wall_sig(sf)
    for k in [k for k in g if k not in alive]:
        del g[k]; rev.pop(k, None); sigs.pop(k, None); changed.add(k)


def grid_of(root, P, sf_id):
    """The grid of one wall for the draw handler (the selected wall's outline): from the cache, computed when the
    wall is dirty or missing (the preview may be off, so no rebuild ever built it)."""
    sf = P.items.get(sf_id)
    if sf is None or sf.type != 'surface':
        return None
    st = session.state(root, P)
    g = st.get('grids') or {}
    if sf.dirty or sf_id not in g:
        g, _changed = grids(root, P)
    return g.get(sf_id)


def _fill(me, verts, faces, owners):
    """The mesh's geometry replaced in place by the cleaned grid; the per-face wall ids after validate, only while
    they still line up with the polygons (validate may drop what clean_mesh let through, never the other way)."""
    verts, faces, kept = clean_mesh(verts, faces)
    owners = [owners[i] for i in kept]
    me.clear_geometry()
    me.from_pydata(verts, [], faces)
    me.validate()
    if faces and len(me.polygons) == len(owners):
        attr = me.attributes.get(FACE_ATTR) or me.attributes.new(FACE_ATTR, 'INT', 'FACE')
        attr.data.foreach_set('value', owners)
    me.update()


def _crown_key(P, k):
    """what crownData reads (crown.py: the connectors, the crown curves and the lock curves on the lower connector, the
    walls between them, the crown's p and its loop step) as a short stamp: per curve what its samples were made from
    (curves.sampleKey) and its attachments, per wall its curves and p — the preview is made again only when one of
    them changed"""
    from ..core.crown import crownStep
    conn = {k.part, k.low}
    on = set(conn)
    for c in P.curves():
        if c.id in conn or c.parent == k.parent or any(a.att is not None and a.att.c in conn for a in c.anchors):
            on.add(c.id)
    held = {id(a) for i in conn if i in P.items for a in P.items[i].anchors}
    for c in P.curves():
        if c.id not in on and any(id(a) in held for a in c.anchors):
            on.add(c.id)
    def fp(c):
        return (c.id, c.sample_key if c.sample_key is not None else tuple((s.p.x, s.p.y, s.p.z) for s in c.samples), len(c.samples),   # (a junction curve has no sampleKey: its samples by value)
                tuple((a.att.c, a.att.t) if a.att is not None else None for a in c.anchors))   # (connT reads att.t)
    curves = tuple(fp(c) for c in P.curves() if c.id in on)
    walls = tuple((s.id, s.a, s.b, json.dumps(s.p, sort_keys=True, default=str)) for s in P.surfaces() if s.a in on or s.b in on)   # (crownData's walls on the lower connector: their segs and segPos)
    whole = (json.dumps(k.p, sort_keys=True, default=str), crownStep(P, k), k.visible, curves, walls)
    try:
        return str(hash(whole))                                     # (a short stamp: the shown key goes into the .blend)
    except TypeError:
        return str(hash(repr(whole)))


def crown_previews(root, P):
    """4.3 (D4.5): every crown's surface (crownData, the original's rebuildCrowns 7638) for the live objects and the
    click, kept per crown while what it reads stays the same. {crown id: (key, MeshData or None, k.info)}"""
    from ..core.crown import crownData
    st = session.state(root, P)
    cache = st.setdefault('crown_prev', {})
    out = {}
    for k in P.crowns():
        key = _crown_key(P, k)
        c = cache.get(k.id)
        if c is None or c[0] != key:
            with slow.part('crown preview', k.name):
                c = (key, crownData(P, k), None)
            c = (c[0], c[1], k.info)
        elif k.info is None:
            k.info = c[2]                                           # (the same crown read again — an undo: crownProps' line, 7806)
        out[k.id] = c
    st['crown_prev'] = out
    return out


def _topo_key(P, k, rev):
    """what a Cap's preview (rebuildCaps 9526: capGrid on the closed locks of its group's walls, the cap that applies to
    each: capOf) or a Fill holes' candidates (skinCandidates 9347: its group's curves and walls, every wall for
    hasSurface, the project step) read, as a short stamp — the walls by their grid's revision (the tables they read)"""
    from ..core.convert import collect
    from ..core.hairmesh_io import defaultStep
    g = P.items.get(k.parent)
    if g is None or g.type != 'group':
        return 'none'
    if k.type == 'cap':
        walls = tuple((s.id, s.a, s.b, s.parent, rev.get(s.id, 0)) for s in collect(P, g.id, set(), ['surface']))
        others = tuple((c.id, c.parent, c.visible) for c in P.caps())
        whole = (json.dumps(k.p, sort_keys=True, default=str), k.visible, walls, others)
    else:
        kids = [x for x in (P.items.get(i) for i in g.children) if x is not None]
        curves = tuple((c.id, c.sample_key if c.sample_key is not None else tuple((s.p.x, s.p.y, s.p.z) for s in c.samples), len(c.samples),
                        tuple(a.id for a in c.anchors), c.ring, c.conn, c.junc) for c in kids if c.type == 'curve')
        walls = tuple((s.id, s.a, s.b, rev.get(s.id, 0), json.dumps(s.p, sort_keys=True, default=str)) for s in kids if s.type == 'surface')
        whole = (json.dumps({x: v for x, v in k.p.items() if x != 'bulge'}, sort_keys=True, default=str), k.visible, curves, walls, tuple((s.a, s.b) for s in P.surfaces()),
                 P.settings.step, float(defaultStep(P)))
    try:
        return str(hash(whole))
    except TypeError:
        return str(hash(repr(whole)))


def topo_previews(root, P):
    """4.4 (D4.5): every Cap's preview and every Fill holes' walls to be (core.topology: capPreview, skinPreview),
    kept per item while what it reads stays the same. {item id: (key, {pos, faces} or None, info)}: info is (1 or 0,)
    for a cap (it closes something), (holes, pairs) for a Fill holes"""
    from ..core import topology as TP
    st = session.state(root, P)
    cache = st.setdefault('topo_prev', {})
    rev = st.setdefault('grid_rev', {})
    T = None; out = {}
    for k in [*P.caps(), *P.of_type('skin')]:
        key = _topo_key(P, k, rev)
        c = cache.get(k.id)
        if c is None or c[0] != key:
            if T is None:
                T = row_tables(root, P).T
            try:
                if k.type == 'cap':
                    with slow.part('cap preview', k.name):
                        m = TP.capPreview(P, k, T)
                    c = (key, m, (1 if m is not None else 0,))
                else:
                    with slow.part('Fill holes: the gaps found', k.name):
                        m = TP.skinPreview(P, k, T)
                    c = (key, m, (sum(1 for x in k.cands if x['kind'] == 'hole'), sum(1 for x in k.cands if x['kind'] == 'pair')))
            except Exception as e:                                  # (one item's preview must not take the others down)
                errorlog.record('live: the preview of an item')
                print(f'HairMesh live preview: the preview of {k.name!r} failed: {e!r}')
                c = (key, None, (0,) if k.type == 'cap' else (0, 0))
        out[k.id] = c
    st['topo_prev'] = out
    return out


def _topo_current(root, k):
    """the preview entry of this item if it was made from the model as it is now (its key), else None"""
    st = session.runtime(root) if root is not None else None
    c = (st or {}).get('topo_prev', {}).get(k.id)
    if c is None or not k.visible:
        return None                                                 # (hidden: its preview is empty, not what Convert would find)
    try:
        P = session.get_project(root)
        return c if c[0] == _topo_key(P, k, (st or {}).get('grid_rev') or {}) else None
    except Exception:
        return None


def _tube_key(P, t):
    """what rebuildTube reads: the modifier's settings and profiles, its curve's samples, the head's center and scale;
    4.7b: the settings its lock gets on Convert (the loops of the preview: TB.tubeRows)"""
    from ..core import tube as TB
    c = P.items.get(t.src)
    xs = [(x.id, json.dumps(getattr(x, 'p', None), sort_keys=True, default=str), x.visible, json.dumps(getattr(x, 'mask', None), default=str))
          for x in P.of_type('xmod') if getattr(x, 'tube', None) == t.id]   # (its Extras: tubeRings reads them, 4.7)
    whole = (json.dumps(t.p, sort_keys=True, default=str), json.dumps(t.profile), json.dumps(t.profs), t.visible, t.src, xs,
             json.dumps(TB.tubeConv(P, t), sort_keys=True, default=str),   # (4.7b item 4: the loops follow the group's settings and the project's step)
             (c.sample_key if c.sample_key is not None else tuple((s.p.x, s.p.y, s.p.z) for s in c.samples)) if c is not None else None,
             len(c.samples) if c is not None else 0, P.S.center.x, P.S.center.y, P.S.center.z, P.S.scale)
    try:
        return str(hash(whole))
    except TypeError:
        return str(hash(repr(whole)))


def _tube_rows_mesh(P, t, m):
    """lot 4.7b, item 4 (the owner's decision): the modifier's surface with the loops its converted lock will have
    (TB.tubeRows: its own loop step, else its group's), not rebuildTube's 96 rings — what is seen is what comes out.
    {pos, faces, n, colors, rows}; the painted weights' colors sampled at the rows."""
    from ..core import tube as TB
    fs = TB.tubeRows(P, t)
    if not fs or len(fs) < 2:
        return {'pos': m.pos, 'faces': m.faces, 'n': m.n, 'colors': m.colors, 'rows': len(m.rings), 'nr': len(m.rings), 'fs': None}
    rings = TB.rowRings(m.rings, fs); n = m.n
    faces = []
    for i in range(len(rings) - 1):
        for j in range(n):
            j1 = (j + 1) % n
            faces.append([i * n + j, i * n + j1, (i + 1) * n + j1, (i + 1) * n + j])
    return {'pos': [q for R in rings for q in R], 'faces': faces, 'n': n, 'colors': colors_at(m.colors, fs), 'rows': len(rings), 'nr': len(m.rings), 'fs': fs}


def colors_at(colors, fs):
    """rebuildTube's ring colors (one per ring, even along the arc) at the shares fs of the arc the preview's rings sit
    at (None: as they are)"""
    if not colors or fs is None:
        return colors
    return [colors[min(len(colors) - 1, max(0, int(round(t * (len(colors) - 1)))))] for t in fs]


def tube_previews(root, P):
    """4.6 (D4.5): every Profile modifier's surface (rebuildTube, 6673), kept per modifier while what it reads stays
    the same. {tube id: (key, {pos, faces} or None, None)}"""
    from ..core import tube as TB
    st = session.state(root, P)
    cache = st.setdefault('tube_prev', {})
    out = {}
    for t in P.tubes():
        key = _tube_key(P, t)
        c = cache.get(t.id)
        if c is None or c[0] != key:
            try:
                with slow.part('Profile modifier surface', t.name):
                    m, _w = TB.rebuildTube(P, t)
                    data = _tube_rows_mesh(P, t, m) if m is not None and m.faces else None
            except Exception as e:                                  # (one modifier must not take the update down)
                errorlog.record('live: the surface of a modifier')
                print(f'HairMesh live preview: the surface of {t.name!r} failed: {e!r}')
                data = None
            c = (_tube_key(P, t), data, None)                       # (rebuildTube may write rRef: the key after; 4.7b: the painted weights' colours, _paint_mesh)
        out[t.id] = c
    st['tube_prev'] = out
    return out


def tube_shown(root, P):
    """4.7b: the Profile modifiers whose surface the live object shows now ({tube id: {pos, faces, n, colors}}) — what
    the weight brush paints (paintMask: t.obj.visible); read from the last rebuild, nothing computed"""
    st = session.runtime(root)
    prev = (st or {}).get('tube_prev') or {}
    return {tid: d for tid, (_k, d, _i) in prev.items() if d is not None and tid in P.items and edit.isVisible(P, P.items[tid])}


def tubes_under_curves(root, P):
    """tube_shown, none while the preview is off: the Profile modifiers whose surface is on screen now"""
    return tube_shown(root, P) if bpy.context.scene.hairmesh.live_preview else {}


def tube_colors(root, P, t):
    """the painted weights of the model as it is now at the rings the live mesh shows (those of the last rebuild: a
    pause in a stroke can change them, the twist moves the loops)"""
    from ..core import tube as TB
    c = ((session.runtime(root) or {}).get('tube_prev') or {}).get(t.id)
    data = c[1] if c is not None else None
    if data is None:
        return None
    return colors_at(TB.tubeColors(P, t, data.get('nr') or TB.TUBE_NR), data.get('fs'))


def _paint_clear(me):
    a = me.color_attributes.get(PAINT_ATTR)
    if a is not None:
        me.color_attributes.remove(a)
        me.update()


def _paint_write(me, colors, whole=False):
    """`colors` ({tube id: one [r, g, b] per ring, in three.js's linear space, the attribute's own}) on the vertices the
    modifiers have on this mesh (nobody shares them; clean_mesh and validate drop faces, never a vertex). `whole`: every
    other vertex grey, the attribute gone when nothing here is painted. Blender's Solid view interpolates along the lock."""
    spots = [s for s in json.loads(me.get(TUBES_KEY) or '[]') if colors.get(s[0])]
    if not spots:
        if whole:
            _paint_clear(me)
        return
    nv = len(me.vertices)
    a = me.color_attributes.get(PAINT_ATTR)
    if a is None:
        a = me.color_attributes.new(PAINT_ATTR, 'FLOAT_COLOR', 'POINT')
        me.color_attributes.active_color_name = PAINT_ATTR
        whole = True
    buf = np.empty(nv * 4, dtype=np.float32)
    if whole:
        buf.fill(NEUTRAL)
        buf[3::4] = 1.0
    else:
        a.data.foreach_get('color', buf)
    for tid, base, count, n in spots:
        if base + count <= nv:
            cols = np.asarray(colors[tid], dtype=np.float32)
            buf[base * 4:(base + count) * 4].reshape(count, 4)[:, :3] = cols[np.minimum(np.arange(count) // n, len(cols) - 1)]
    a.data.foreach_set('color', buf)
    me.update()


def _paint_mesh(me, tubes):
    """the whole mesh as the previews say (tube_previews)"""
    _paint_write(me, {tid: c[1].get('colors') for tid, c in tubes.items() if c[1] is not None}, whole=True)


def paint_sync(root, P):
    """every live mesh of the root with the colors of the model as it is"""
    tubes = tube_previews(root, P)
    for ob in live_objects(root).values():
        if ob.data is not None:
            _paint_mesh(ob.data, tubes)


def paint_begin(root, P):
    """the weight brush on: the colors on its meshes, and rebuild keeps them there"""
    _painting.add(root.name)
    paint_sync(root, P)


def paint_end(root_name):
    """the weight brush off: no colors left on the meshes (an undo may bring them back in a mesh of a step made with the
    brush on: rebuild takes them off, and they show nowhere meanwhile, the view's Color being back)"""
    _painting.discard(root_name); _paint_stale.discard(root_name)
    root = bpy.data.objects.get(root_name)
    if root is not None:
        for ob in live_objects(root).values():
            if ob.data is not None:
                _paint_clear(ob.data)


def paint_follow(root_name):
    """the brush's next event after a Ctrl+Z / Ctrl+Shift+Z: the mesh restored gets the colors of the model restored
    (a step made before the brush has none). True when it did"""
    if root_name not in _paint_stale:
        return False
    _paint_stale.discard(root_name)
    root = bpy.data.objects.get(root_name)
    if root is None or not session.is_root(root):
        return False
    paint_sync(root, session.get_project(root))
    return True


def paint_tubes(names, P, colors):
    """a dab: the colors of these modifiers ({tube id: ring colors}) on their groups' meshes, no geometry touched (the
    shape follows when the cursor pauses). `names`: {group key: live object name}, kept through the stroke"""
    groups = {}
    for tid, cols in colors.items():
        t = P.items.get(tid)
        if t is not None:
            groups.setdefault(t.parent if t.parent is not None else -1, {})[tid] = cols
    for gid, part in groups.items():
        ob = bpy.data.objects.get(names.get(gid, ''))
        if ob is not None and ob.data is not None:
            _paint_write(ob.data, part)


def shown_previews(root, P):
    """{item id: (key, data, info)} of the Caps', Fills' and Profile modifiers' previews, as the last update left them:
    what a click and the Density brush's occlusion hit. A model parsed again (Ctrl+Z) has none until the next update:
    built here, once (no data written: they live in the session's state). With the preview off nothing shows, nothing
    is hit (it.obj.visible = vis && S.showSurfaces, 11982, 11990; pickables 5658): the walls' cache alone stays pickable"""
    if not bpy.context.scene.hairmesh.live_preview:
        return {}
    st = session.state(root, P)
    if 'topo_prev' not in st:
        topo_previews(root, P)
    if 'tube_prev' not in st:
        tube_previews(root, P)
    return {**st['topo_prev'], **st['tube_prev']}


def topo_shown(P, k):
    """whether a Cap's / Fill's / modifier's preview shows: a cap only while one of the walls it closes is visible
    (syncVisuals 11990: `k.srcSurfs.some(id => isVisible(items.get(id)))` — Convert and Baked hide them)"""
    if k.type != 'cap':
        return True
    return any(s in P.items and edit.isVisible(P, P.items[s]) for s in (k.srcSurfs or ()))


def topo_found(root, k):
    """what the preview of a Cap / Fill holes found for the model as it is (a cap: 1 when it closes a lock; a Fill:
    the gaps), None when there is none of this state (hidden, the live preview off, stale: the panels act)"""
    c = _topo_current(root, k)
    return sum(c[2]) if c is not None else None


def topo_counts(root, k):
    c = _topo_current(root, k)
    return c[2] if c is not None and len(c[2]) == 2 else (None, None)


def rebuild(context, root, P=None):
    """Rebuilds the "<group> live" objects of the root from the model: the grids of the walls that changed are
    computed again and the meshes of the groups whose grids are newer than what they show are refilled (the others
    stay). Returns the objects by group id."""
    P = P or session.get_project(root)
    g, _changed = grids(root, P)                                    # always: the outline and the picking read this cache
    if not context.scene.hairmesh.live_preview:
        return {}                                                   # the objects stay hidden and as they are (refilled when shown again)
    st = session.state(root, P)
    rev = st.setdefault('grid_rev', {})
    by_group = {}
    for sf in P.surfaces():
        by_group.setdefault(sf.parent, []).append(sf)
    crowns = crown_previews(root, P)                                # (4.3, D4.5: a crown's surface in its group's live object)
    by_crown = {}
    for k in P.crowns():
        by_crown.setdefault(k.parent, []).append(k)
        by_group.setdefault(k.parent, [])
    topo = topo_previews(root, P)                                   # (4.4, D4.5: a Cap's rings and a Fill's walls to be in their group's live object)
    by_topo = {}
    for k in [*P.caps(), *P.of_type('skin')]:
        if k.parent is not None:
            by_topo.setdefault(k.parent, []).append(k); by_group.setdefault(k.parent, [])
    tubes = tube_previews(root, P)                                  # (4.6, D4.5: a Profile modifier's surface in its group's live object)
    for t in P.tubes():
        by_topo.setdefault(t.parent, []).append(t); by_group.setdefault(t.parent, [])
    topo = {**topo, **tubes}
    root_col = next((c for c in bpy.data.collections if c.get('hairmesh_root') == root.name), None) or context.scene.collection
    from .convert_op import collection_of_group
    existing = live_objects(root)
    names = unique_names(P, naming.held_by_others(root))            # (3.7b: groups that share a name: "X live", "X (2) live"; 3.8a: or one held elsewhere)
    scene_objects = context.scene.objects
    for key, ob in list(existing.items()):
        if ob.name not in scene_objects:                            # an orphan (linked to no collection of the scene): made anew below
            me = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
            del existing[key]
    out = {}
    for gid, surfs in by_group.items():
        grp = P.items.get(gid)
        name = f'{group_name(P, gid, names)} live'
        key = gid if gid is not None else -1
        ob = existing.pop(key, None)
        # the mesh shows the walls of the group whose own eye is on (1a of the manual test of 16/10/2026: the eye
        # of a wall row hides that wall alone, as the original's syncVisuals leaves a hidden surface's object
        # invisible, 12005); the group's eye hides the whole object below. A wall switched either way changes
        # what the mesh must show, so it is refilled
        ks = [k for k in by_crown.get(gid, ()) if k.visible and crowns[k.id][1] is not None and crowns[k.id][1].faces]
        ts = [k for k in by_topo.get(gid, ()) if k.visible and topo[k.id][1] is not None and topo_shown(P, k)]
        want = (shown_key({sf.id: rev.get(sf.id, 0) for sf in surfs if sf.visible}) + (json.dumps([[k.id, crowns[k.id][0]] for k in ks]) if ks else '')
                + (json.dumps([[k.id, topo[k.id][0]] for k in ts]) if ts else ''))
        if ob is not None and ob.data is not None and ob.data.get(SHOWN_KEY) == want and (TUBES_KEY in ob.data) == any(k.type == 'tube' for k in ts):   # (the second test: a mesh of a file older than TUBES_KEY, refilled once)
            if ob.name != name and bpy.data.objects.get(name) is None:   # (a name still held: the last loop below, or never when it is not ours)
                ob.name = name
            naming.rename_data(ob, name)
            ob.hide_viewport = not (grp is None or edit.isVisible(P, grp))
            if root.name not in _painting:
                _paint_clear(ob.data)                               # (colors that came back with an undo, or in a file saved with the brush on)
            out[gid] = ob
            continue                                                # it shows these very grids: the mesh stays
        verts = []; faces = []; owners = []; spots = []
        for sf in surfs:
            d = g.get(sf.id)
            if d is None or not sf.visible:
                continue
            base = len(verts)
            verts.extend(to_blender(p) for p in d.pos)
            faces.extend([v + base for v in f] for f in d.faces)
            owners.extend([sf.id] * len(d.faces))
        for k in ks:
            d = crowns[k.id][1]
            base = len(verts)
            verts.extend(to_blender(p) for p in d.pos)
            faces.extend([v + base for v in f] for f in d.faces)
            owners.extend([k.id] * len(d.faces))
        for k in ts:                                                # (pick.topo_hits: a click on them selects the item, its sheet)
            d = topo[k.id][1]
            base = len(verts)
            verts.extend(to_blender(p) for p in d['pos'])
            faces.extend([v + base for v in f] for f in d['faces'])
            owners.extend([k.id] * len(d['faces']))
            if k.type == 'tube':
                spots.append([k.id, base, len(d['pos']), d['n']])
        try:
            if ob is None or ob.data is None:
                me = bpy.data.meshes.new(name) if ob is None else ob.data
                if ob is None:
                    ob = bpy.data.objects.new(name, me)
                    ob.parent = root
                    ob.hide_select = True
                    ob.show_wire = True
                    ob['hairmesh_type'] = 'live'
                    ob['hairmesh_group_id'] = key
                    ob['hairmesh_root'] = root.name
                    (collection_of_group(root_col, gid) if gid is not None else root_col).objects.link(ob)
                else:
                    ob.data = me
            with slow.part('live object refilled', name):
                _fill(ob.data, verts, faces, owners)
            if ob.name != name and bpy.data.objects.get(name) is None:
                ob.name = name
            naming.rename_data(ob, name)
            ob.data[SHOWN_KEY] = want
            if spots:
                ob.data[TUBES_KEY] = json.dumps(spots)
            elif TUBES_KEY in ob.data:
                del ob.data[TUBES_KEY]
            if root.name in _painting:
                _paint_mesh(ob.data, tubes)                         # (clear_geometry took them)
        except Exception as e:                                      # one group's mesh must not take the others down
            errorlog.record('live: the mesh of a group')
            print(f'HairMesh live preview: the mesh of group {name!r} failed: {e!r}')
            if ob is not None and ob.data is not None and SHOWN_KEY in ob.data:
                del ob.data[SHOWN_KEY]
        ob.hide_viewport = not (grp is None or edit.isVisible(P, grp))
        out[gid] = ob
    for key, ob in existing.items():                                # groups that lost their last wall
        me = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    for gid, ob in out.items():                                     # a name another object still held above (gone, or renamed since): taken now
        name = f'{group_name(P, gid, names)} live'
        if ob.name != name and bpy.data.objects.get(name) is None:   # (held by something not ours: left, no rename at every update)
            ob.name = name
        naming.rename_data(ob, name)                                # (3.8a: the data too, or its old name kept holding the group's)
    return out


def shown_key(want):
    """what a live mesh shows, as stamped on its datablock: the walls by id with their grid revisions"""
    return json.dumps(sorted(want.items()))


def set_visible(context, on):
    for root in session.roots_in(context.scene):
        for ob in live_objects(root).values():
            ob.hide_viewport = not on
        if on:
            update(context, root)


def rebuild_report(root, seconds=None):
    """what the last rebuild cost, for a report: '' when it was quick; `seconds` = the whole rebuild as the caller
    timed it (the row tables, the grids and the meshes), else the row tables alone"""
    t, n = last_rebuild.get(root.name, (0.0, None))[:2]
    if seconds is not None:
        t = seconds
    if t < WAIT_AFTER:
        return ''
    return f'HairMesh: {"every lock" if n is None else f"{n} curves"} rebuilt in {t:.1f} s'


NO_WALLS = 'Nothing to rebuild: no walls yet'


class HAIRMESH_OT_live_rebuild(bpy.types.Operator):
    """Rebuild the live preview of the walls now"""
    bl_idname = 'hairmesh.live_rebuild'
    bl_label = 'Rebuild HairMesh Preview'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return owner.visible(context) and session.find_root(context) is not None

    @classmethod
    def description(cls, context, properties):
        try:
            P = session.get_project(session.find_root(context))
            if not P.surfaces():
                return NO_WALLS
        except Exception:                                           # (a tooltip must never raise)
            pass
        return cls.__doc__

    def execute(self, context):
        root = session.find_root(context)
        if not session.get_project(root).surfaces():                # part 1 of the lot ux7: the button is dimmed then
            self.report({'INFO'}, NO_WALLS)
            return {'CANCELLED'}
        context.scene.hairmesh.live_preview = True
        invalidate_rows(root)                                       # the button: everything from scratch
        n = len(update(context, root))
        self.report({'INFO'}, f'HairMesh: {n} live preview objects' + (f' ({rebuild_report(root)})' if rebuild_report(root) else ''))
        return {'FINISHED'}


# ---------------- undo, redo, file load: the caches follow Blender's data ----------------

def _reset_caches():
    """What this module and the draw handler keep in Python about Blender's data goes: the next access reads the
    restored state (session.get_project notices the text's hash; the row tables and grids belong to that instance,
    and the next row build keeps what still fits: _inherit)."""
    from . import deferred, draw
    cancel()
    deferred.forget()
    draw.invalidate()
    _reported.clear()
    _held.clear()                                                   # (4.4, the review: an error mid-drag must not keep a root held for good)


@persistent
def _on_undo(*_args):
    _reset_caches()
    _paint_stale.update(_painting)
    _warm()


def _warm():
    """4.2c, item 1: the tables follow the restored model within the Ctrl+Z itself (what the history of states keeps
    comes back; the rest is rebuilt here, a wait that belongs to the undo and says so in the console), so the click
    after it builds nothing. Only roots built in this session (their state kept); reads the texts, writes no data."""
    # (lot 4.4: never the scene's object list here — after an undo that brings back an object Blender deleted, the
    # view layer's list is not rebuilt yet in undo_post and reading it crashed Blender; the data's list is)
    try:
        roots = [o for o in bpy.data.objects if session.is_root(o)]
    except Exception:
        return
    for root in roots:
        if session.runtime(root) is None:
            continue
        try:
            with slow.action('the row tables after Ctrl+Z / Ctrl+Shift+Z'):
                P = session.get_project(root)
                grids(root, P)
                if bpy.context.scene.hairmesh.live_preview and (P.caps() or P.of_type('skin')):
                    topo_previews(root, P)                          # (what the overlays of draw.py read: a draw builds nothing)
        except Exception as e:                                      # a handler must never raise
            errorlog.record('handler: live preview')
            print('HairMesh live preview:', repr(e))


@persistent
def _on_load(*_args):
    from . import draw, meshes, ops_crown, ops_edges, ops_outliner, pick, profile_editor
    _reset_caches()
    _painting.clear(); _paint_stale.clear()
    session.forget()
    meshes._recent.clear()
    pick.clear_caches(); draw.clear_outlines()                      # (4.2 ∥ 4.5, item 1: the click's arrays and the outline's)
    profile_editor.STATE['op'] = None                               # (a modal the load ended never runs its own end; the draw handlers read this)
    ops_crown.ATTACH.update(root=None, anchor=None, conn=None)
    ops_edges._arrays.clear()
    ops_outliner.LAST_INVOKE.clear()
    modal_keys.clear(bpy.context)                                   # (a modal the load ended: the status bar is the whole app's)


@persistent
def _on_import(context, *_args):
    session.adopt_imported([it.id for it in context.import_items if isinstance(it.id, bpy.types.Object)])


def _handler_lists():
    h = bpy.app.handlers
    return ((h.undo_post, _on_undo), (h.redo_post, _on_undo), (h.load_post, _on_load), (h.blend_import_post, _on_import))


def register():
    bpy.utils.register_class(HAIRMESH_OT_live_rebuild)
    for lst, fn in _handler_lists():
        if fn not in lst:
            lst.append(fn)


def unregister():
    for lst, fn in _handler_lists():
        if fn in lst:
            lst.remove(fn)
    bpy.utils.unregister_class(HAIRMESH_OT_live_rebuild)
    cancel()
    _reported.clear()
    _painting.clear(); _paint_stale.clear()
    last_rebuild.clear(); builds.clear(); _held.clear()
