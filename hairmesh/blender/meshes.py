# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The converted meshes in Blender (3.7): one Mesh object per mesh item of the project, "<group> mesh", in the group's
collection beside "<group> live", refilled from the model like the live preview (sync, called by live.update: every
operator). The model is the source: a mesh item's data lives in the project (session.py keeps the coordinates in
their own datablock) and the object shows it; an object whose item is gone is taken down, a missing one is made again.

Preview vs. converted, always told apart on screen (the manual test before 3.7: "I clicked Convert and thought
nothing happened, the mesh came out exactly over the live walls"): a pending mesh is orange with its wireframe
("HairMesh Preview"), a confirmed one a light hair tone without it ("HairMesh Mesh"); the walls and curves a
conversion converted are hidden by it (the original's startConversion), and the tool draws a banner at the top of the
viewport (banner, drawn by draw.py).

While a conversion is pending, every edit rebuilds the pending meshes (the original's regenConversion), but only the
ones whose signature changed (conversion.meshSignature over the grid revisions of live.py): follow_conversion."""
import json
import weakref
import time
from math import copysign

import bpy

from ..core import conversion as CONV
from ..core import crown as CR
from ..core import edit
from ..core.meshdata import clean_mesh
from ..core.names import unique_names
from . import errorlog, naming, session, slow
from .coords import to_blender

MAT_PREVIEW = 'HairMesh Preview'
MAT_MESH = 'HairMesh Mesh'
COLOR_PREVIEW = (1.0, 0.42, 0.08, 1.0)                              # orange: not confirmed yet
COLOR_MESH = (0.86, 0.80, 0.70, 1.0)                                # a light hair tone: converted
MADE_KEY = 'hairmesh_mesh_made'                                     # on the root: the mesh item ids sync made an object for (JSON list)
last_regen = {}                                                     # root name -> (seconds, meshes rebuilt) of the last follow_conversion (tests, reports)


STAMPS = ('hairmesh_type', 'hairmesh_id', 'hairmesh_root', 'hairmesh_token', 'hairmesh_group', 'hairmesh_group_id', 'hairmesh_pending', 'hairmesh_sym', 'hairmesh_name', 'hairmesh_obname',
          'hairmesh_model_name')


def mesh_objects(root, clean=True):
    """the "<group> mesh" objects of this root made by the 3.7 flow, by mesh item id (an object of the earlier
    Convert, without the token stamp, is the user's now and left alone). A copy made by Shift+D carries the same
    stamps: the object named as the model last named it is ours, the others lose the stamps (the user's objects now).
    `clean` False: only look (the draw handler, the picking — nothing of bpy.data changes outside an operator)."""
    found = {}
    for o in bpy.data.objects:
        if o.get('hairmesh_type') == 'mesh' and 'hairmesh_token' in o and (o.parent == root or o.get('hairmesh_root') == root.name):
            found.setdefault(o.get('hairmesh_id'), []).append(o)
    out = {}
    for iid, obs in found.items():
        mine = next((o for o in obs if o.name == o.get('hairmesh_obname')), obs[0])   # (hairmesh_obname: the name the object had when sync last named it)
        out[iid] = mine
        for o in obs if clean else ():
            if o is not mine:
                for k in STAMPS:
                    if k in o:
                        del o[k]
    return out


def _material(name, color):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.diffuse_color = color                                    # the Solid view's colour (Material colour type, the default)
        m['hairmesh_material'] = True
    return m


def _fill(me, data):
    verts, faces, _kept = clean_mesh([to_blender(p) for p in data.pos], [list(f) for f in data.faces])
    me.clear_geometry()
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()


def sync(context, root, P, commit=True):
    """the mesh objects follow the mesh items: made, refilled (token), recolored (pending or not), shown or hidden
    (the item's eye and its groups'), named and placed in the group's collection; the others taken down"""
    from .convert_op import collection_of_group
    existing = mesh_objects(root)
    root_col = next((c for c in bpy.data.collections if c.get('hairmesh_root') == root.name), None) or context.scene.collection
    items = [m for m in CONV.meshes(P) if m.data is not None]
    # an item whose object this sync made and that is gone from the file: the user deleted it with Blender's own
    # delete (X, the outliner) — that deletes the mesh (lot 3.7-estab: it came back at the next edit). Seen here,
    # inside the next operator: nothing of the add-on may write the model from a handler. The ids made are kept on
    # the root (MADE_KEY), so an undo brings them back with the objects.
    if commit and deleted_by_user(root, P, existing):              # (not in a look that may still be dropped: live.update(commit=False))
        session.save_project(root, P)
        items = [m for m in CONV.meshes(P) if m.data is not None]
    elif not commit:                                                # (the look leaves them gone: the next saved edit deletes their items)
        made = set(json.loads(root.get(MADE_KEY) or '[]'))
        items = [m for m in items if m.id not in made or m.id in existing]
    # the objects of items that are gone first (lot 3.7-estab: Convert with a conversion pending replaced the items,
    # and the new objects, made before the old ones went, came out "Bang_01 mesh.001"): one of the same group is
    # taken over by the group's new item (the same name and place; the data kept when the token is the same — the
    # reused meshes of Convert again), the others removed
    ids = {m.id for m in items}
    spare = {}
    for iid in [i for i in existing if i not in ids]:
        ob = existing.pop(iid)
        spare.setdefault(ob.get('hairmesh_group_id', -1), []).append(ob)
    for m in items:
        if m.id not in existing and spare.get(m.parent if m.parent is not None else -1):
            ob = spare[m.parent if m.parent is not None else -1].pop(0)
            ob['hairmesh_id'] = m.id
            existing[m.id] = ob
    for obs in spare.values():
        for ob in obs:
            _remove(ob)
    # the names first: a confirmed mesh that a pending one will replace gives its name up while pending (and gets it
    # back on Cancel); the ones giving a name up before the ones taking one
    names = unique_names(P, naming.held_by_others(root))            # (3.7b: the meshes of groups that share a name: "X mesh", "X (2) mesh"; 3.8a: or one held elsewhere)
    base = {m.id: names.get(m.id, m.name) for m in items}
    want_name = {m.id: object_name(P, m, names) for m in items}
    moves = [(m, existing[m.id]) for m in items if m.id in existing and existing[m.id].get('hairmesh_name') != want_name[m.id]]
    for m, ob in sorted(moves, key=lambda x: want_name[x[0].id] == base[x[0].id]):
        old = ob.get('hairmesh_model_name')                         # the item's own name in the model when the object was last named
        if old is None:
            old = (ob.get('hairmesh_name') or '').removesuffix(' (replaced)')   # (an object named before 3.7b)
        renamed = old != m.name                                     # the item renamed in the model: the model's name (not a " (2)" that moved, 3.7b)
        if renamed or ob.name == ob.get('hairmesh_obname'):         # else only "(replaced)" or the suffix: a name the user gave stays
            ob.name = want_name[m.id]
            ob['hairmesh_obname'] = ob.name
            me = ob.data
            if me is not None and me.get('hairmesh_data') and me.users == 1 and bpy.data.meshes.get(ob.name) is None:
                me.name = ob.name                                   # (the data gives its name up too: the next "Fringe mesh" data not ".001")
        ob['hairmesh_name'] = want_name[m.id]; ob['hairmesh_model_name'] = m.name
    for m in items:
        ob = existing.pop(m.id, None)
        if ob is not None and ob.name not in context.scene.objects:   # an orphan: made anew
            _remove(ob)
            ob = None
        try:
            if ob is None:
                me = bpy.data.meshes.new(want_name[m.id])
                me['hairmesh_data'] = True                          # (ours: removed when nothing uses it any more)
                ob = bpy.data.objects.new(want_name[m.id], me)
                ob.parent = root
                ob['hairmesh_type'] = 'mesh'; ob['hairmesh_id'] = m.id; ob['hairmesh_root'] = root.name; ob['hairmesh_token'] = ''
                ob['hairmesh_name'] = want_name[m.id]; ob['hairmesh_obname'] = ob.name; ob['hairmesh_group_id'] = m.parent if m.parent is not None else -1
                ob['hairmesh_model_name'] = m.name
                collection_of_group(root_col, m.parent).objects.link(ob)
            if ob.get('hairmesh_token') != m.token:
                with slow.part('mesh object refilled', m.name):
                    _fill(ob.data, m.data)
                ob['hairmesh_token'] = m.token
            # what the model says is applied when it CHANGES (the item made, confirmed, renamed, moved): a confirmed
            # mesh is the user's deliverable — the material, the name, the collection they give it stay
            g = P.items.get(m.parent)
            ob['hairmesh_group'] = g.name if g is not None else ''; ob['hairmesh_sym'] = bool(m.symBaked)
            pend = bool(m.pending)
            if ob.get('hairmesh_pending') != pend:
                mat = _material(MAT_PREVIEW, COLOR_PREVIEW) if pend else _material(MAT_MESH, COLOR_MESH)
                ob.data.materials.clear(); ob.data.materials.append(mat)
                ob.show_wire = pend
                ob.hide_select = pend                               # a preview is not a deliverable yet
                ob['hairmesh_pending'] = pend
            hide = not edit.isVisible(P, m)
            if ob.hide_viewport != hide:
                ob.hide_viewport = hide
            gid = m.parent if m.parent is not None else -1
            if ob.get('hairmesh_group_id') != gid:                  # the mesh moved to another group (or was just made)
                want = collection_of_group(root_col, m.parent)
                if want.name not in [c.name for c in ob.users_collection]:
                    for c in list(ob.users_collection):
                        c.objects.unlink(ob)
                    want.objects.link(ob)
                ob['hairmesh_group_id'] = gid
        except Exception as e:                                      # one mesh must not take the others down
            errorlog.record('meshes: sync')
            print(f'HairMesh mesh {m.name!r}: {e!r}')
    have = mesh_objects(root)
    alive = {m.id for m in CONV.meshes(P)}
    # (an item made before keeps its place until deleted_by_user takes it: a look with commit=False saw its object gone)
    now = json.dumps(sorted({m.id for m in items if m.id in have} | (set(json.loads(root.get(MADE_KEY) or '[]')) & alive)))
    if root.get(MADE_KEY, '[]') != now:
        root[MADE_KEY] = now
    _purge_data()
    for name in (MAT_PREVIEW, MAT_MESH):                            # (the orange one once everything is confirmed: nothing left over)
        mat = bpy.data.materials.get(name)
        if mat is not None and mat.get('hairmesh_material') and mat.users == 0:
            bpy.data.materials.remove(mat)


def deleted_by_user(root, P, existing=None):
    """the mesh items whose object sync made and that Blender's own delete removed since: deleted from the model
    (called by session.save_project, the end of every operator that changes the model, and by sync). Returns them."""
    made = set(json.loads(root.get(MADE_KEY) or '[]'))
    if not made:
        return []
    if existing is None:
        existing = mesh_objects(root)
    gone = [m for m in CONV.meshes(P) if m.data is not None and m.id in made and m.id not in existing]
    if gone:                                                        # (an object of ours this root no longer finds — unparented, the root renamed — is not deleted)
        ours = {o.get('hairmesh_id') for o in bpy.data.objects if o.get('hairmesh_type') == 'mesh'}
        gone = [m for m in gone if m.id not in ours]
    for m in gone:
        edit.deleteItem(P, m.id)
        print(f'HairMesh: {m.name} deleted (its object was deleted)')
    if gone:
        root[MADE_KEY] = json.dumps(sorted(made - {m.id for m in gone}))
        _purge_data()
    return gone


def _purge_data():
    for me in [me for me in bpy.data.meshes if me.users == 0 and me.get('hairmesh_data')]:
        bpy.data.meshes.remove(me)                                  # (the data of an object Blender's delete removed)


def object_name(P, m, names=None):
    """the name of a mesh item's object: the item's ("<group> mesh"), applied when it changes (a name the user gives
    the object stays); while a pending mesh replaces it, "<group> mesh (replaced)" — Blender's names are unique, and
    the new one must not come out "<group> mesh.001" (lot 3.7-estab). Groups that share a name (3.7b): the mesh takes
    its group's Blender name, "<group> (2) mesh" (core/names.py)"""
    n = (names if names is not None else unique_names(P)).get(m.id, m.name)
    if P.conv and m.id in (P.conv.get('replaced') or ()):
        return f'{n} (replaced)'
    return n


def _remove(ob):
    me = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def tables(root, P):
    """the row tables of the session (incremental: live.row_tables) with every wall's grid revision current"""
    from . import live
    live.grids(root, P)
    return session.state(root, P)['rows'].T


def signatures(root, P):
    """the signature each mesh data was built with, by the data's token (lot 3.7-estab: by token and not by mesh id, so
    a data an undo brought back is matched with what IT was built from, never with what a later build of the same item
    was)"""
    st = session.state(root, P)
    return st.setdefault('mesh_sig', {})


RECENT = 64                                                         # the meshes built lately kept for Convert to reuse (per root)
_recent = {}                                                        # root name -> {(group, walls, crowns, signature): (data, symBaked, token)}, oldest first


def _recent_key(m, sig):
    return (m.parent, tuple(m.srcSurfs or ()), tuple(m.srcCrowns or ()), sig)


def remember(root, P, ms):
    """the signature of each mesh as it was just built (or reused); the data kept among the recent builds (lot
    3.7-estab: Cancel, or Ctrl+Z of a Convert, took the meshes away, and Convert again built all 14 from scratch)"""
    st = session.state(root, P)
    rev = st.get('grid_rev') or {}
    sigs = signatures(root, P)
    recent = _recent.setdefault(root.name, {})
    made = st.get('baked') or {}
    for m in ms:
        made.pop(m.token, None)                                     # (a build gave it: what was baked, or came in a file, has its signature from now on)
        sig = CONV.meshSignature(P, m, rev)
        sigs[m.token] = sig
        if sig is not None and m.data is not None:
            k = _recent_key(m, sig)
            recent.pop(k, None)
            recent[k] = (m.data, m.symBaked, m.token)
    while len(recent) > RECENT:
        recent.pop(next(iter(recent)))


_fresh = {}                                                         # root name -> (weakref to P, (P.rev, P.gen), tokens): the meshes just built, the model as it was then


def just_built(root, P, ms):
    """Convert, after writing the project and before its live.update: these meshes are what the model gives now —
    until the model changes (P.rev / P.gen move on; another P after an undo)"""
    _fresh[root.name] = (weakref.ref(P), (P.rev, P.gen), {m.token for m in ms})


BAKING = 'baking'                                                   # (a baked token whose release's live.update has not run yet)


def baking(root, P, ms):
    """4.0 (D4.11): the release of a drag baked these meshes (edit.bakeMeshes); before its live.update. The baked
    data is kept by follow_conversion through that update (the original's release calls no regenConversion,
    4643-4660), whatever the update does to the model (P.rev / P.gen), and gets no signature (mesh_sig): Convert never
    reuses it (reuser), out_of_date never vouches for it."""
    b = session.state(root, P).setdefault('baked', {})
    for m in ms:
        b[m.token] = BAKING


def baked(root, P):
    """after the release's live.update: each baked data remembers the signature of its walls as they are now (the
    grids the update rebuilt), so a pending baked mesh stays where it was moved through what changes nothing of its
    walls (the eye of another group, a rename, Live / Baked) and is rebuilt from the curves by an edit of them (ours:
    the original's regenConversion rebuilds every pending mesh at any edit, docs/features-adicionadas.md, 4.0)"""
    st = session.state(root, P)
    b = st.get('baked') or {}
    rev = st.get('grid_rev') or {}
    by = {m.token: m for m in CONV.meshes(P)}
    for tok, v in list(b.items()):
        if v == BAKING:
            m = by.get(tok)
            b[tok] = CONV.meshSignature(P, m, rev) if m is not None else None


def changed(root, P, m):
    """whether mesh m would come out different now: its signature differs from the one it was built with (True when
    either is unknown — except for a mesh built since the model last changed: the live.update right after Convert
    rebuilt the example's mesh, whose crown gives no signature, a second time for nothing: 1 s, item 4 of the manual
    test of 3.7b). A baked data (4.0): unchanged while its walls are as they were when it was baked."""
    f = _fresh.get(root.name)
    if f is not None and f[0]() is P and f[1] == (P.rev, P.gen) and m.token in f[2]:
        return False
    st = session.state(root, P)
    rev = st.get('grid_rev') or {}
    b = st.get('baked')
    if b and m.token in b:
        v = b[m.token]
        if v == BAKING:
            return False
        now = CONV.meshSignature(P, m, rev)
        return v is None or now is None or v != now
    old = signatures(root, P).get(m.token)
    now = CONV.meshSignature(P, m, rev)
    return old is None or now is None or old != now


def out_of_date(root, P):
    """ours: the confirmed meshes an edit has touched since they were made (known only for the meshes made or
    checked in this session: the others are not claimed)"""
    rev = session.state(root, P).get('grid_rev') or {}
    sigs = signatures(root, P)
    out = []
    for m in CONV.meshes(P):
        if m.pending or sigs.get(m.token) is None:
            continue
        if CONV.meshSignature(P, m, rev) != sigs[m.token]:
            out.append(m)
    return out


def follow_conversion(context, root, P, save=True):
    """regenConversion (10026) after an edit, for the pending meshes whose signature changed; writes the project when
    one was rebuilt and `save` (live.update's commit). Returns the meshes rebuilt."""
    if not P.conv:
        return []
    t0 = time.perf_counter()
    T = tables(root, P)
    before = {iid: (P.items[iid].data, P.items[iid].symBaked, P.items[iid].token) for iid in P.conv['entries'] if iid in P.items}
    with slow.part('pending meshes rebuilt (regenConversion)') as what:
        rebuilt = CONV.regenConversion(P, T, CR.crownData, lambda m: changed(root, P, m))
        # a mesh that came out the very same (a crown's, rebuilt at every edit: no signature says what crownData
        # reads; one whose walls' grids were computed again equal) keeps its data and token: nothing to refill or write
        same = [m for m in rebuilt if m.id in before and _same_data(before[m.id][0], m.data) and before[m.id][1] == m.symBaked]
        for m in same:
            m.data, m.symBaked, m.token = before[m.id]
        if same:
            remember(root, P, same)
        rebuilt = [m for m in rebuilt if m not in same]
        what[0] = ', '.join(m.name for m in rebuilt) or 'none'
    if rebuilt:
        remember(root, P, rebuilt)
        if save:
            session.save_project(root, P)
    last_regen[root.name] = (time.perf_counter() - t0, len(rebuilt))
    return rebuilt


def _same_data(a, b):
    if a is None or b is None or a.faces != b.faces or len(a.pos) != len(b.pos):
        return False
    for p, q in zip(a.pos, b.pos):
        if p.x != q.x or p.y != q.y or p.z != q.z:
            return False
        if copysign(1.0, p.x) != copysign(1.0, q.x) or copysign(1.0, p.y) != copysign(1.0, q.y) or copysign(1.0, p.z) != copysign(1.0, q.z):
            return False                                            # (-0.0 and 0.0: bit for bit)
    return True


def reuser(root, P):
    """for Convert: reuse(new mesh) -> the data of an earlier mesh of the same group, made from the same walls, whose
    signature is the one the new mesh has now (the incremental reconversion), else None. The earlier meshes are
    captured here, before startConversion cancels a pending conversion; the signatures are read at each call (the
    grid revisions are current only once the tables were built)."""
    by_group = {}
    for m in CONV.meshes(P):
        if m.data is not None:
            by_group.setdefault(m.parent, []).append(m)
    stats = {'reused': 0, 'built': 0}

    def reuse(new):
        rev = session.state(root, P).get('grid_rev') or {}
        sigs = signatures(root, P)
        sig = CONV.meshSignature(P, new, rev)
        for old in by_group.get(new.parent, ()):
            if sig is not None and old.srcSurfs == new.srcSurfs and list(old.srcCrowns or ()) == list(new.srcCrowns or ()) and sigs.get(old.token) == sig:
                stats['reused'] += 1
                return old.data, old.symBaked, old.token
        got = _recent.get(root.name, {}).get(_recent_key(new, sig)) if sig is not None else None
        if got is not None:
            stats['reused'] += 1
            return got
        stats['built'] += 1
        return None
    return reuse, stats


def banner(P):
    """the line the tool draws at the top of the viewport about the meshes (pure): (text, kind) or None —
    kind 'preview' (orange), 'baked', 'live'"""
    if P is None:
        return None
    if P.conv:
        n = len(P.conv['entries'])
        return (f'CONVERSION PREVIEW · {n} mesh{"" if n == 1 else "es"} (orange) · Confirm or Cancel: D > Conversion', 'preview')
    ms = CONV.meshes(P)
    if not ms:
        return None
    view = CONV.viewOf(P)
    n = len(ms)
    what = f'{n} converted mesh{"" if n == 1 else "es"}'
    if view == 'baked':
        return (f'BAKED · {what} shown, walls and curves hidden · Live: D > Conversion', 'baked')
    if view == 'live':
        return (f'LIVE · {what} hidden · Baked: D > Conversion', 'live')
    return (f'{what}, some shown · Live / Baked: D > Conversion', 'live')
