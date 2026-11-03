# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The project kept in the .blend (phase 3): Text datablocks are the source of truth, a core Project in memory is
the working copy every operator reads and writes.

Datablocks (D7 of docs/fase-3-plano-ciclo-nativo.md):
  hairmesh/<name>.json         the project as the .hairmesh file, minus the head's source file (and minus thumb / refs,
                               which the port never keeps)
  hairmesh/<name>.head.<ext>   the head's source file itself: the OBJ text, or the base64 of a GLB
  hairmesh/<name>.meshes.json  (3.7) the coordinates of the converted meshes, {id: {token, pos, faces}}: the mesh items
                               stay in the project text with `data: null`, so the text every operator rewrites does not
                               carry ~1 MB of vertices; written only when a mesh's data changed (its token)
The root Empty names them in its custom properties (hairmesh_text, hairmesh_head_text, hairmesh_meshes_text), and
keeps the pending conversion (P.conv, the original's S.conv: in the undo and in an exported file, never in this text) in hairmesh_conv.

The in-memory Project is cached per root and keyed by a hash of the JSON text, so an undo, a reload or a hand edit of
the text is noticed on the next access, while operators that only read get the same instance (with its samples and
row tables) back. Whoever changes the model ends with save_project(), which writes the datablocks and refreshes the
cache.

Writing the text (16/10/2026, docs/fase-3-desempenho.md): Text.from_string and Text.write insert the buffer one
character at a time into the current line, reallocating it each time, so a 300 KB project on its single JSON line
cost 2.6 s per operator on the example; a line's `body` is set in one memcpy, so the text is cleared and its one line
set (_set_text). The cache key is Python's hash of the text (0.1 ms) instead of SHA-1 (1 ms): the getters of the
panels call get_project dozens of times per redraw."""
import json

import bpy

from ..core import model as M
from ..core.hairmesh_io import ProjectFormatError, head_src_split, project_from_dict, project_json_text, project_to_dict
from ..core.vec3 import V3
from . import errorlog, slow

TEXT_PREFIX = 'hairmesh/'
TEXT_REFS = (('hairmesh_text', 'hairmesh_text_ref'), ('hairmesh_head_text', 'hairmesh_head_text_ref'), ('hairmesh_meshes_text', 'hairmesh_meshes_text_ref'))
_cache = {}                                                         # root object name -> (text name, sha1 of its content, Project)
_uid = {}                                                           # project text name -> the highest UID a model of it reached in this session (the review of 4.2 ∥ 4.5)


def _uid_key(root):
    """the project's text names its UID (a root copied by Blender's Shift+D shares the text, and so the counter)"""
    return root.get('hairmesh_text') or root.name


def keep_uid(root, proj):
    """remember `proj`'s UID as reached: the next model read from the text never hands out an id below it"""
    k = _uid_key(root)
    _uid[k] = max(_uid.get(k, 0), proj.uid)


def project_text_name(name):
    return f'{TEXT_PREFIX}{name}.json'


def head_text_name(name, ext):
    return f'{TEXT_PREFIX}{name}.head.{ext or "obj"}'


def meshes_text_name(name):
    return f'{TEXT_PREFIX}{name}.meshes.json'


def _sha(text):
    """the cache key of a text: its length and Python's hash (consistent within the process, enough to tell an
    undo, a reload or a hand edit from the text we wrote ourselves)"""
    return (len(text), hash(text))


def _key(text, root):
    """the cache key of a root's model: its text, its pending conversion and the tokens of its meshes' data (all
    undone together; an operation may change the meshes alone)"""
    return (_sha(text), root.get('hairmesh_conv') or '', root.get('hairmesh_mesh_tokens') or '')


def _set_text(txt, content):
    """the datablock's content replaced in O(n): one line set whole (a multi-line file, the head's OBJ, goes
    through from_string, whose cost is per line)"""
    if '\n' in content:
        txt.from_string(content)
        return
    txt.clear()
    txt.lines[0].body = content


def _split(d):
    """the file dict -> (JSON text without the head's file, payload, key, ext).

    A Text datablock cannot hold NUL characters (a NUL ends the line and drops the rest of it; a trailing NUL, which
    some OBJ writers leave, just disappears), so they are taken out of the payload and their positions kept in the
    JSON (`src.nul`) for _rejoin to put them back: the export stays byte-identical to the imported file."""
    meta, payload, key = head_src_split(d['head']['src'] if d.get('head') else None)
    if payload is not None:
        if '\x00' in payload:
            meta = {**meta, 'nul': [i for i, ch in enumerate(payload) if ch == '\x00']}
            payload = payload.replace('\x00', '')
        d = {**d, 'head': {**d['head'], 'src': meta}}
    return project_json_text(d), payload, key, (meta or {}).get('ext')


def _rejoin(src, stored):
    """the head's file back into src from its datablock's text (see _split)"""
    nul = src.pop('nul', None)
    if nul:
        for i in nul:                                               # ascending original positions: each insert lands right
            stored = stored[:i] + '\x00' + stored[i:]
    src['b64' if src.get('ext') == 'glb' else 'text'] = stored      # importHead (3236): glb -> b64, else text


def _new_text(name, content):
    txt = bpy.data.texts.new(name)
    _set_text(txt, content)
    txt.use_fake_user = True                                        # survives even with no object pointing at it
    return txt


def store_project(name, proj):
    """The datablocks of a new root (importer, New project). Returns (project text, head text or None)."""
    text, payload, key, ext = _split(project_to_dict(proj, mesh_data=False))
    txt = _new_text(project_text_name(name), text)
    head_txt = _new_text(head_text_name(name, ext), payload) if payload is not None else None
    return txt, head_txt


def bind(root, proj, txt, head_txt):
    """Points the root at its datablocks and caches the model."""
    root['hairmesh_text'] = txt.name
    if head_txt is not None:
        root['hairmesh_head_text'] = head_txt.name
    elif 'hairmesh_head_text' in root:
        del root['hairmesh_head_text']
    _refer(root)
    save_meshes(root, proj)
    _refer(root)
    _set_conv(root, proj)
    _cache[root.name] = (txt.name, _key(txt.as_string(), root), proj)
    _sync_rows(proj)


def _text(root, name_key):
    """The text a key of the root points at: the pointer first, the name for a root saved before there were pointers."""
    txt = root.get(name_key + '_ref')
    return txt if txt is not None else bpy.data.texts.get(root.get(name_key) or '')


def _refer(root):
    """ID pointers beside the names: a name brings nothing along, so without them an Append takes the Empty and leaves
    the texts in the other file. The root's own name goes along too: adopt_imported finds what an Append renamed."""
    for name_key, ref_key in TEXT_REFS:
        txt = bpy.data.texts.get(root.get(name_key) or '')
        if txt is None:
            root.pop(ref_key, None)
        elif root.get(ref_key) is not txt:
            root[ref_key] = txt
    if root.get('hairmesh_self') != root.name:
        root['hairmesh_self'] = root.name


def _follow(root):
    """The names of the texts from the pointers: a text renamed from outside (Make Local over a name in use)."""
    for name_key, ref_key in TEXT_REFS:
        txt = root.get(ref_key)
        if txt is not None and root.get(name_key) != txt.name:
            root[name_key] = txt.name


def adopt_imported(obs):
    """After an Append, on the objects it brought (a copy made by Shift+D also holds an old name, and must not take
    the stamps of the original): what arrived renamed (" .001") is found again. The texts through their pointers, the
    head as the stamped child (never a pointer: it would count as a user, and X would no longer delete the head), the
    stamps of the root's children and collection, which hold its name, follow it."""
    for ob in obs:
        if ob.library is not None or not ob.get('hairmesh_text'):
            continue
        _follow(ob)
        kids = ob.children_recursive
        head = next((c for c in kids if c.get('hairmesh_type') == 'head'), None)
        if head is not None and ob.get('hairmesh_head') != head.name:
            ob['hairmesh_head'] = head.name
        old = ob.get('hairmesh_self')
        if old and old != ob.name:
            for held in (*kids, *ob.users_collection):
                if held.get('hairmesh_root') == old:
                    held['hairmesh_root'] = ob.name
            ob['hairmesh_self'] = ob.name


def _set_conv(root, proj):
    if proj.conv:
        root['hairmesh_conv'] = json.dumps(proj.conv)
    elif 'hairmesh_conv' in root:
        del root['hairmesh_conv']


_mesh_written = {}                                                  # root name -> the (id, token) pairs of the meshes text as last written / read


def _mesh_key(proj):
    return tuple((it.id, it.token) for it in proj.items.values() if it.type == 'mesh' and it.data is not None)


def save_meshes(root, proj):
    """The meshes' coordinates into their datablock, when a mesh's data changed since it was last written (3.7).
    Data without a token (a file just imported) gets one."""
    from ..core.conversion import new_token
    for it in proj.items.values():
        if it.type == 'mesh' and it.data is not None and not it.token:
            it.token = new_token()
    key = _mesh_key(proj)
    txt = _text(root, 'hairmesh_meshes_text')
    if txt is not None and _mesh_written.get(root.name) == key:
        return
    if txt is None and not key:
        return
    payload = json.dumps({str(it.id): {'token': it.token, 'pos': [p.toArray() for p in it.data.pos], 'faces': it.data.faces}
                          for it in proj.items.values() if it.type == 'mesh' and it.data is not None}, separators=(',', ':'))
    if txt is None:
        txt = _new_text(meshes_text_name(root.get('hairmesh_data_name') or root.get('hairmesh_project') or root.name), payload)
        root['hairmesh_meshes_text'] = txt.name
    else:
        _set_text(txt, payload)
    root['hairmesh_mesh_tokens'] = json.dumps(key)                  # (in the cache key: the meshes' text is too big to hash at every access)
    _mesh_written[root.name] = key


def _rejoin_meshes(root, proj):
    """the meshes' coordinates back into the model from their datablock"""
    txt = _text(root, 'hairmesh_meshes_text')
    _mesh_written[root.name] = ()
    if txt is None:
        return
    try:
        payload = json.loads(txt.as_string() or '{}')
    except ValueError:
        return
    for it in proj.items.values():
        e = payload.get(str(it.id)) if it.type == 'mesh' else None
        if e is not None:
            it.data = M.MeshData(pos=[V3(*p) for p in e['pos']], faces=[list(f) for f in e['faces']])
            it.token = e.get('token')
    _mesh_written[root.name] = _mesh_key(proj)


def _sync_rows(proj):
    """the outliner's color rows of the scene follow the groups (props.sync_rows); never raises"""
    try:
        from . import props
        scene = bpy.context.scene
        if scene is not None:
            props.sync_rows(scene.hairmesh, proj)
    except Exception as e:                                          # (no scene, a draw context: the rows catch up at the next save)
        errorlog.record('session: outliner rows')
        print('HairMesh outliner rows:', repr(e))


def save_project(root, proj, *, head=False):
    """Writes the model back into the datablocks. `head=True` also (re)writes the head's file datablock."""
    keep_uid(root, proj)
    sync(root, proj)                                                # (4.3, D4.8: the crown part of the original's frame, before the text)
    _follow(root)
    txt = _text(root, 'hairmesh_text')
    if txt is None:
        raise ProjectFormatError(f'text datablock "{root.get("hairmesh_text")}" not found; the project is gone from this .blend')
    proj.rev += 1                                                   # whatever changed, the viewport must redraw it
    st = _state.get(root.name)
    if st is not None and st.get('P') is proj:
        st.pop('model_changed', None)                               # (4.1: what the row build changed is in this very write: live.save_model_changes has nothing left)
    from . import meshes
    meshes.deleted_by_user(root, proj)                              # (a converted mesh whose object Blender's delete removed: gone from the model too)
    with slow.part('project text written'):
        text, payload, key, ext = _split(project_to_dict(proj, mesh_data=False))
        _set_text(txt, text)
    with slow.part('mesh coordinates written'):
        save_meshes(root, proj)
    _set_conv(root, proj)
    _sync_rows(proj)
    if head:
        old = _text(root, 'hairmesh_head_text')
        if payload is None:
            if 'hairmesh_head_text' in root:
                del root['hairmesh_head_text']
        else:
            if old is None or not old.name.endswith('.' + (ext or 'obj')):
                old = _new_text(head_text_name(root.get('hairmesh_data_name') or root.get('hairmesh_project') or 'project', ext), payload)
            else:
                _set_text(old, payload)
            root['hairmesh_head_text'] = old.name
    _refer(root)
    _cache[root.name] = (txt.name, _key(text, root), proj)


def get_project(root):
    """The root's model: the cached instance while the text is unchanged, else parsed again from the datablocks."""
    txt = _text(root, 'hairmesh_text')
    if txt is None:
        raise ProjectFormatError(f'text datablock "{root.get("hairmesh_text")}" not found; the project is gone from this .blend')
    text = txt.as_string()
    h = _key(text, root)
    c = _cache.get(root.name)
    if c is not None and c[0] == txt.name and c[1] == h:
        return c[2]
    with slow.part('project text parsed (an undo, a redo or an edit by hand)'):
        proj = _parse(root, txt, text, h)
    if c is not None:
        from ..core.curves import adoptSamples
        adoptSamples(proj, c[2])                                    # (the samples of the instance before, where their key still holds)
    return proj


def _parse(root, txt, text, h):
    P = json.loads(text)
    head_txt = _text(root, 'hairmesh_head_text')
    if P.get('head') and P['head'].get('src') and head_txt is not None:
        _rejoin(P['head']['src'], head_txt.as_string())
    proj = project_from_dict(P, root.get('hairmesh_source') or f'{root.get("hairmesh_project", root.name)}.hairmesh', strip=(), load=False)   # our own text: back as written (restore, not loadProject)
    # the original's UID belongs to the session and never goes back: its restore (1220) even moves it on, one per
    # anchor it makes again (new Anchor, 713). Ours never goes back either (an intentional difference: it does not
    # move on by the anchors — ids made after an undo differ from the original's, no file or table depends on them):
    # an undo must not hand out again the ids of items it took away, which the incremental tables of the state before
    # still name (the review of 4.2 ∥ 4.5: a curve id of before became a wall's after Ctrl+Z, and the row build failed)
    prev = _cache.get(root.name)
    if prev is not None:
        keep_uid(root, prev[2])
    proj.uid = max(proj.uid, _uid.get(_uid_key(root), 0))
    _rejoin_meshes(root, proj)
    try:
        proj.conv = json.loads(root.get('hairmesh_conv') or 'null')
    except ValueError:
        proj.conv = None
    from ..core.crowntool import recordSym
    recordSym(proj)                                                 # (4.3: a model read again is the state after a frame: the next crownSymSync compares with it)
    _cache[root.name] = (txt.name, h, proj)
    return proj


_sync_said = {}                                                     # root name -> the toasts of the last sync (live.update says them)


def sync(root, proj):
    """D4.8 (4.3): the crown part of the original's frame (crownSymSync, updateAttachments, 12060-12063) at the end
    of every operator that changes the model: a symmetric crown's pairs follow, attached anchors sit on their
    connector, one moved away comes off (its toast waits for live.update). Inside an operator only."""
    from ..core.crowntool import frameSync
    moved, toasts = frameSync(proj)
    if moved:
        from . import live
        ids = {id(a) for a in moved}
        live.touch(root, [c.id for c in proj.curves() if any(id(a) in ids for a in c.anchors)])
    if toasts:
        _sync_said[root.name] = toasts[len(toasts) - 1]
    return moved


def forget(root_name=None):
    """Drops the cache (all of it, or one root's): tests and file reloads."""
    if root_name is None:
        _cache.clear(); _state.clear(); _current.clear(); _saved.clear(); _mesh_written.clear(); _uid.clear()
    else:
        _cache.pop(root_name, None); _state.pop(root_name, None); _saved.pop(root_name, None); _mesh_written.pop(root_name, None)
        for k in [k for k, v in _current.items() if v == root_name]:
            del _current[k]


def forget_model(root_name):
    """Drops the cached model of one root and nothing else: the next get_project reads the text again (a change made
    in memory and never saved is gone: deferred.py on Esc, an undo or a load); the runtime state (the row tables)
    stays, for the re-read model to inherit what still fits (live._inherit)."""
    c = _cache.pop(root_name, None)
    if c is not None:
        k = c[0]                                                    # (the text's name: _uid_key of that root)
        _uid[k] = max(_uid.get(k, 0), c[2].uid)                     # (ids spent in memory only stay spent: a refused cut, a Convert stopped)


_saved = {}                                                         # root name -> (the .hairmesh path, the cache key of the text as last saved / opened, or None: changed)


def _text_of(root):
    txt = _text(root, 'hairmesh_text')
    return txt.as_string() if txt is not None else None


def mark_saved(root, path):
    """1c of the manual test of the lot ux5 (ours: the original keeps a file handle, proj.handle 10096, and marks the
    project saved, markSaved 10088): the .hairmesh this project is saved to / was opened from, and its text as
    written. Kept twice: on the root (the .blend remembers it) and here, outside the undo — an undo must not forget
    the path, nor make a project look saved that differs from the file on disk."""
    import hashlib
    text = _text_of(root) or ''
    root['hairmesh_path'] = path
    root['hairmesh_path_sha1'] = hashlib.sha1(text.encode('utf-8')).hexdigest()
    _saved[root.name] = (path, _sha(text))
    _disk[path] = _stamp(path)


_disk = {}                                                          # .hairmesh path -> (mtime, size) as this session last wrote / read it


def _stamp(path):
    import os
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def disk_changed(path):
    """whether the file at `path` changed on disk since this session last read or wrote it (edited in the app, say):
    a Save over it asks first. False when this session never stamped it (after a reload of the .blend) or it is gone."""
    then = _disk.get(path)
    now = _stamp(path)
    return then is not None and now is not None and now != then


def save_needed(root):
    """whether Save writes anything (part 1 of the lot ux7): not when the .hairmesh holds this project already — no
    change here, none on the disk; without a file it asks where (it acts)"""
    path, dirty = file_state(root)
    return not path or dirty is not False or disk_changed(path)


def file_state(root):
    """(the .hairmesh path or None, whether the project changed since it was saved / opened — None when unknown).
    Cheap: the text's cache key against the one saved (the SHA-1 kept on the root only once per session, after a
    reload of the .blend)."""
    e = _saved.get(root.name)
    text = _text_of(root)
    if e is None:
        path = root.get('hairmesh_path')
        if not path:
            return None, None
        import hashlib
        same = text is not None and hashlib.sha1(text.encode('utf-8')).hexdigest() == root.get('hairmesh_path_sha1')
        e = _saved[root.name] = (path, _sha(text) if same else None)
    path, key = e
    return path, (text is None or key != _sha(text))


_state = {}                                                         # root name -> runtime state tied to one Project instance


def state(root, proj):
    """Per-root runtime state that belongs to this Project instance (the row tables of the live preview, ...): a
    fresh dict whenever the model was parsed again (undo, reload), since the old tables point at old items. The
    previous instance's dict stays under 'prev' until the live preview has looked at it (live.row_tables keeps the
    tables that still fit the re-parsed model, 16/10/2026)."""
    st = _state.get(root.name)
    if st is None or st.get('P') is not proj:
        prev = st
        if prev is not None and prev.get('rows') is None:
            prev = prev.get('prev')                                 # (two undos in a row with no rebuild between: the last tables there were)
        if prev is not None:
            prev.pop('prev', None)                                  # (no chain beyond that: one set of tables is all that is ever used)
        st = {'P': proj, 'prev': prev}
        _state[root.name] = st
    return st


def runtime(root):
    """The root's runtime state dict if one exists (no parse, no creation), else None: for code that only wants to
    drop or read a cache (the live preview's row tables and grids)."""
    return _state.get(root.name)


def is_root(ob):
    """An editable project's root. A linked one is not: its texts belong to the other file, and nothing here may write
    them; it shows as plain Blender objects until Make Local brings the texts along."""
    if ob is None or not ob.get('hairmesh_text') or ob.library is not None or ob.override_library is not None:
        return False
    txt = ob.get('hairmesh_text_ref')
    return txt is None or txt.library is None


def roots_in(scene):
    return [o for o in scene.objects if is_root(o)]


_current = {}                                                       # scene name -> the root last found through the active / selected objects


def find_root(context):
    """The HairMesh root Empty the user means: the active object or one of its parents, else a selected object or
    one of its parents, else (several roots in the scene) the root found that way the last time in this scene,
    else the only root in the scene.

    The active object is taken from the view layer as well as from the context: inside a 3D View (the draw handler,
    the tool header, the D pop-up) the context reports no active object while that object is hidden in the viewport,
    and the Live Preview toggle hides the live meshes — clicked in the outliner, a live mesh is the active object,
    and the toggle made the project vanish from the viewport and from the pop-up (1a of the manual test of
    15/10/2026, two projects in the scene). The last root found is remembered for the same reason, and so that the
    project does not change under the user when they click a cube to take the loop step from it."""
    scene = context.scene
    vl = getattr(context, 'view_layer', None)
    seen = set()
    for ob in [context.active_object, vl.objects.active if vl is not None else None, *getattr(context, 'selected_objects', [])]:
        while ob is not None and ob.name not in seen:
            seen.add(ob.name)
            if is_root(ob) and ob.name in scene.objects:            # (3.8a: a root of another scene is not this scene's project)
                _current[scene.name] = ob.name
                return ob
            ob = ob.parent
    roots = roots_in(scene)
    if len(roots) == 1:
        _current[scene.name] = roots[0].name
        return roots[0]
    name = _current.get(scene.name)
    ob = scene.objects.get(name) if name else None
    return ob if ob is not None and is_root(ob) else None


def head_object(root):
    """The Mesh object marked as the project's head, if it still exists."""
    name = root.get('hairmesh_head')
    ob = bpy.data.objects.get(name) if name else None
    return ob if ob is not None and ob.type == 'MESH' else None
