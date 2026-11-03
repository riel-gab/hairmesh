# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Migration (1.0.1): another project's hair — curves, NURBS surfaces (the walls), modifiers and crowns, with their
groups — brought into this project. port of headFit / migrateIn / migrateData / baseName / migrateFile
(hair-modeler.clean.html:10408-10499): ids renumbered by the UID; the head, the guides, the meshes, the imported meshes
and their groups stay behind; everything comes in under a new group named after the file (a preset comes in as its
own group). Hair made around another head comes in fitted to this scene's head (headFit); without a head in the
scene the frame comes from the hair (fitToHair).

Pure (no bpy): the caller stores the selection the result names, commits and refreshes the views
(blender/migrate_op.py). What migrateData does to the original's scene through restore (1220) is done here on the
model directly: the project's items and anchors stay as they are and the migrated ones are appended in the order the
snapshot would hold them."""
import copy
import json
import math
import re
from collections import ChainMap
from dataclasses import dataclass, field
from types import SimpleNamespace

from . import groups as G
from .fdlibm import cbrt
from .hairmesh_io import anchor_from_dict, fit_to_hair, item_from_dict
from .headobj import float32, parseOBJ
from .js import js_div, js_num_str, to_precision_number, truthy
from .vec3 import Vec3

PROJ_RE = re.compile(r'\.(hairmesh|json)\Z', re.IGNORECASE)        # 1280 (JavaScript's $ without the m flag: the very end)
LEFT_BEHIND = ('head', 'guide', 'mesh', 'imesh')                    # 10432


@dataclass
class Migrated:
    """what migrateData did: `ok` (its return value), `msg` (its toast), `sel` (S.sel after it: the new group, or the
    preset's top items), the ids of the items that came in (the new group last), of the curves among them, and the
    fit used (headFit's {k, from, to}, or None); `note` (ours, migrateFile): why the hair was not fitted although both
    sides have a head (unread_head)"""
    ok: bool
    msg: str
    sel: list = field(default_factory=list)
    items: list = field(default_factory=list)
    curves: list = field(default_factory=list)
    fit: dict | None = None
    note: str | None = None


def baseName(n):
    # port of baseName (10495): the file's name without .hairmesh / .json
    return PROJ_RE.sub('', n or 'project', count=1)


def head_box(src):
    """The box Box3.setFromObject gives the head the original builds from the file `src` (headPartsFromSrc 3313 ->
    headFromOBJ 3266): every position of the OBJ as a Float32BufferAttribute holds it (computeBoundingBox reads the
    whole attribute, the vertices no face uses too), as ((min x, y, z), (max x, y, z)). (None, True) for a file
    without a vertex (Box3.isEmpty); (None, False) for a head the port cannot read (a GLB: not ported, as on import)."""
    if not src or src.get('ext') != 'obj' or not isinstance(src.get('text'), str):
        return None, False
    v, _faces = parseOBJ(src['text'])
    if len(v) < 3:
        return None, True
    xs = [float32(x) for x in v[0::3]]
    ys = [float32(y) for y in v[1::3]]
    zs = [float32(z) for z in v[2::3]]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))), True


def _top(b):
    # headFit's top(b): the middle of the box across, its top
    return Vec3((b[0][0] + b[1][0]) / 2, b[1][1], (b[0][2] + b[1][2]) / 2)


def headFit(proj, P):
    """port of headFit (10414): the uniform scale k and the shift that take the source project's head (stored in its
    file, P.head.src) onto this scene's head, box to box — the tops and the middles across matched, k the geometric
    mean of the three axis ratios (Math.cbrt: fdlibm.cbrt, V8's bits). None: nothing to do — no head on one side, an
    empty head, the same head (k within 1e-3 of 1 and the tops within 1e-3 of the head's height)."""
    h = P.get('head') if isinstance(P, dict) else None
    if proj.head is None or not truthy(h) or not isinstance(h, dict) or not truthy(h.get('src')):
        return None
    try:
        sb, _ok = head_box(h['src'])
    except (ValueError, TypeError, IndexError, AttributeError):     # (the original: catch -> null)
        return None
    db, _ok = head_box(proj.head.src)
    if sb is None or db is None:
        return None
    ss = (sb[1][0] - sb[0][0], sb[1][1] - sb[0][1], sb[1][2] - sb[0][2])   # getSize: max - min
    ds = (db[1][0] - db[0][0], db[1][1] - db[0][1], db[1][2] - db[0][2])
    k = cbrt(js_div(ds[0], ss[0]) * js_div(ds[1], ss[1]) * js_div(ds[2], ss[2]))
    frm = _top(sb); to = _top(db)
    if not (k > 0) or not math.isfinite(k) or (abs(k - 1) < 1e-3 and frm.distanceTo(to) < 1e-3 * ds[1]):
        return None
    return {'k': k, 'from': frm, 'to': to}


def unread_head(proj, P):
    """ours: why headFit could not compare the heads although both sides have one — a GLB head, which the port does not
    read (the original would fit the hair to it); None when there is nothing to say"""
    h = P.get('head') if isinstance(P, dict) else None
    if proj.head is None or not isinstance(h, dict) or not truthy(h.get('src')):
        return None
    for who, src in (('the file', h['src']), ('this scene', proj.head.src)):
        try:
            box, ok = head_box(src)
        except (ValueError, TypeError, IndexError, AttributeError):
            continue
        if not ok:
            return f'the head of {who} is a {str((src or {}).get("ext") or "?").upper()}, which the add-on does not read: the hair came in at its own size and place'
    return None


def rebuild_key(proj):
    """ours: what the locks of a project read beyond their own curves, walls and groups, and a migration can change —
    the frame (S: the head's box, or the hair's without a head: fitToHair) and the project loop step (defaultStep:
    usedStep counts the step of every wall's group). The original rebuilds every lock after a migration (restore makes
    every curve anew); the add-on rebuilds them all only when this key changed, else just the locks of the curves that
    came in — the same tables, bit for bit (tests/test_migrate.py, TestMigrateIncremental)."""
    from .hairmesh_io import defaultStep
    S = proj.S
    return (S.scale, S.headH, S.yRef, S.center.toArray(), defaultStep(proj))


def migrateIn(proj, P, name, wrap=True):
    # port of migrateIn (10425)
    return migrateData(proj, P, name, wrap, headFit(proj, P))


def migrateFile(proj, text, filename):
    """port of migrateFile (10496): the file's text read as a project; a preset comes in under its own name, as its
    own group (no wrapping group); any other file under a new group named after the file"""
    try:
        P = json.loads(text)
    except ValueError:
        return Migrated(False, 'Not a project file')
    preset = isinstance(P, dict) and truthy(P.get('preset'))
    if preset:
        name = P.get('name') if truthy(P.get('name')) else baseName(filename)
    else:
        name = baseName(filename)
    note = unread_head(proj, P)
    r = migrateIn(proj, P, str(name), not preset)
    if r.ok:
        r.note = note
    return r


def migrateData(proj, P, name, wrap=True, fit=None):
    """port of migrateData (10427). The project is changed only when the result is ok: everything is read and built
    first, then put in at once (ours: a file the original would choke on halfway — a record missing a key, a value of
    the wrong kind — gives a message and leaves the project as it was). `fit` (headFit): positions go
    through it (anchors, the anchors of Even out, asrc), lengths are scaled by k (frozen handles, the radii of the
    modifiers and of their extras, the groups' loop steps). The new items and anchors are appended after the project's
    own, in the order restore(snap) would hold them (the kept items in the order the walk found them, then the new
    group); their ids follow the UID: the items first, then the anchors, then the new group."""
    if not isinstance(P, dict) or P.get('app') != 'anime-hair-modeler' or not isinstance(P.get('items'), list):
        return Migrated(False, 'Not a Stylized HairMesh Creator project')
    try:
        r = _migrate(proj, P, name, wrap, fit)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, RecursionError) as e:
        return Migrated(False, f'{name}: the file could not be read as a project ({type(e).__name__}: {e})')
    return r if isinstance(r, Migrated) else r()                    # (the change itself outside the try: whatever fails there reaches the caller)


def _migrate(proj, P, name, wrap, fit):
    """reads the file and builds what comes in; returns a Migrated failure, or the function that puts it in"""
    byId = {}
    for o in P['items']:
        byId[o.get('id')] = o
    keep = {}                                                       # a Set: insertion order

    def walk(iid):
        o = byId.get(iid)
        if not o or o.get('type') in LEFT_BEHIND or (o.get('type') == 'xmod' and o.get('mesh') is not None):
            return
        if o.get('type') == 'group' and (truthy(o.get('guidesGroup')) or truthy(o.get('meshesGroup')) or truthy(o.get('importGroup'))):
            return
        keep[iid] = True
        if o['type'] == 'group':
            for c in o['children']:
                walk(c)
    roots = P['roots'] if truthy(P.get('roots')) else [o.get('id') for o in P['items'] if o.get('parent') is None]
    for r in roots:
        walk(r)
    ch = True
    while ch:                                                       # things whose sources stayed behind go too
        ch = False
        for iid in list(keep):
            o = byId[iid]; t = o.get('type')
            if ((t == 'surface' and not (o.get('a') in keep and o.get('b') in keep)) or (t == 'tube' and o.get('src') not in keep)
                    or (t == 'xmod' and o.get('tube') not in keep) or (t == 'crown' and not (o.get('part') in keep and o.get('low') in keep))):
                del keep[iid]; ch = True
    if not any(byId[iid].get('type') != 'group' for iid in keep):
        return Migrated(False, f'Nothing to migrate: no curves, surfaces or modifiers in {name}')
    PA = {}
    for a in P.get('anchors') or []:
        PA[a.get('id')] = a
    uid = proj.uid
    imap = {}; amap = {}; used = {}
    for iid in keep:
        imap[iid] = uid; uid += 1
        o = byId[iid]
        if o['type'] == 'curve':
            for a in o['anchors']:
                used[a] = True
    for a in used:
        amap[a] = uid; uid += 1
    missing = [a for a in used if a not in PA]
    if missing:                                                     # ours: the original would make curves with holes (and fail later)
        return Migrated(False, f'{name}: its curves refer to anchors the file does not have ({", ".join(map(str, missing[:5]))})')

    def R(iid):
        return imap.get(iid)                                        # imap.get(id) ?? null

    def RA(aid):
        return amap[aid] if aid in amap else aid                    # amap.get(id) ?? id
    k = fit['k'] if fit else None

    def fitPt(p):
        f, t = fit['from'], fit['to']                               # new V3(...p).sub(from).multiplyScalar(k).add(to)
        return [(p[0] - f.x) * k + t.x, (p[1] - f.y) * k + t.y, (p[2] - f.z) * k + t.z]

    def scale(q, keys):
        if isinstance(q, dict):
            for key in keys:
                v = q.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):   # typeof q[key] === 'number'
                    q[key] = v * k
    top = None
    if wrap:
        top = {'id': uid, 'type': 'group', 'name': name, 'parent': None, 'visible': True, 'children': [], 'color': None, 'expanded': True, 'sym': None,
               'guides': 'auto', 'conv': None, 'guidesGroup': False, 'meshesGroup': False, 'importGroup': False, 'labels': None}
        uid += 1
    new = []
    for iid in keep:
        o = copy.deepcopy(byId[iid])                                # JSON.parse(JSON.stringify(...))
        t = o['type']
        o['id'] = imap[iid]
        o['parent'] = imap[o['parent']] if o.get('parent') is not None and o['parent'] in keep else (top['id'] if top else None)
        if t == 'group':
            o['children'] = [R(c) for c in o['children'] if c in keep]
        if t == 'curve':
            o['anchors'] = [RA(a) for a in o['anchors']]
            o['ovr'] = [[RA(e[0]), e[1]] for e in (o.get('ovr') or [])]
        if fit:
            if t == 'curve':
                for e in o['ovr']:
                    v = e[1]
                    for s in ('in', 'out'):
                        if truthy(v.get(s)):
                            v[s] = [x * k for x in v[s]]
                if truthy(o.get('asrc')):
                    o['asrc'] = [fitPt(p) for p in o['asrc']]
            if t == 'group':
                scale(o.get('conv'), ('step',))
            if t == 'tube':
                scale(o.get('p'), ('r0', 'r1', 'r2', 'rRef'))
            if t == 'xmod':
                scale(o.get('p'), ('amp', 'ampRef', 'r0', 'r1', 'r2', 'r3', 'radRef'))
        if t == 'surface':
            o['a'] = R(o.get('a')); o['b'] = R(o.get('b'))
        if t == 'tube':
            o['src'] = R(o.get('src'))
        if t == 'xmod':
            o['tube'] = R(o.get('tube'))
            p = o.get('p')
            if isinstance(p, dict) and p.get('only') is not None:
                only = p['only']
                p['only'] = [imap[x] for x in only if imap.get(x) is not None] if isinstance(only, list) else imap.get(only)
        if t == 'crown':
            o['part'] = R(o.get('part')); o['low'] = R(o.get('low'))
        new.append(o)
    tops = [imap[iid] for iid in roots if iid in keep]
    if top:
        top['children'] = list(tops)
        new.append(top)
    anchors = []
    for aid in used:
        a = PA[aid]
        n = {**a, 'id': amap[aid], 'p': fitPt(a['p']) if fit else list(a['p'])}
        if truthy(a.get('ps')):
            n['ps'] = [RA(x) for x in a['ps']]
        if truthy(a.get('at')):
            c = imap.get(a['at'].get('c'))
            if c is not None:
                n['at'] = {'c': c, 't': a['at']['t']}
            else:
                n.pop('at', None)
        anchors.append(n)
    # ---- restore(snap): the project as it was, then the anchors and items that came in (built first, put in at once) ----
    snap_anchors = len({id(a): a for c in proj.curves() for a in c.anchors}) + len(anchors)   # snapshot's anchors: those of the curves
    made_anchors = {r['id']: anchor_from_dict(r) for r in anchors}
    view = SimpleNamespace(anchors=ChainMap(made_anchors, proj.anchors))   # (item_from_dict looks a curve's anchors up there)
    made_items = [item_from_dict(o, view) for o in new]
    for r in anchors:                                               # (what fitToHair reads: numbers, checked before anything goes in)
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in r['p'][:3]) or len(r['p']) < 3:
            raise TypeError(f'anchor {r["id"]}: position {r["p"]!r}')
    return lambda: _commit(proj, name, fit, keep, byId, top, tops, new, anchors, made_anchors, made_items, snap_anchors, uid)


def _commit(proj, name, fit, keep, byId, top, tops, new, anchors, made_anchors, made_items, snap_anchors, uid):
    k = fit['k'] if fit else None
    proj.anchors.update(made_anchors)
    for it in made_items:
        proj.items[it.id] = it
    if top:
        proj.roots.append(top['id'])
    else:
        proj.roots.extend(tops)
    proj.uid = uid + snap_anchors                                   # restore makes every anchor with new Anchor(), which takes an id (713) before it is overwritten
    if proj.head is None:                                           # if (!S.head) fitToHair()
        frame = fit_to_hair(proj)
        if frame is not None:
            proj.S = frame
    # curves().forEach(c => c.dirty = true); S.surfDirty = true — restore made every curve anew. Here the curves already in
    # the project keep their samples: nothing they are sampled from changed (no anchor of theirs is shared with the hair
    # that came in: curves.sampleKey, which would keep the very same lists, costs 0.1-0.2 s over the example); the
    # curves that came in are dirty from the start (item_from_dict)
    proj.gen += 1
    sel = [top['id']] if top else list(tops)                        # S.sel = new Set(top ? [top.id] : tops)
    G.panelsRendered(proj, sel[len(sel) - 1] if sel else None)      # onSelChange(); renderOutliner()
    n = sum(1 for iid in keep if byId[iid]['type'] == 'curve')
    msg = (f'Migrated {n} curve{"" if n == 1 else "s"} from {name}' + (f' into the group "{name}"' if top else '')
           + (f' — fitted to the head (scale ×{js_num_str(to_precision_number(k, 3))})' if fit else ''))
    return Migrated(True, msg, sel, [o['id'] for o in new], [o['id'] for o in new if o['type'] == 'curve'], fit)
