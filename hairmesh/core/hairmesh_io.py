# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Reading a .hairmesh project (JSON) into the data model: what loadProject (hair-modeler.clean.html:9450) and
restore (1218) do with the file, minus everything that is display state."""
import json
import math
import re
import os
from typing import Any

from . import model as M
from .headobj import box_frame, fit_to_box, head_geometry, head_look
from .js import js_max, js_min, js_or, to_fixed, to_precision_number, truthy
from .vec3 import Vec3

STRIP_KEYS = ('refs', 'thumb')          # reference-board images and the thumbnail: heavy, never part of the geometry


class ProjectFormatError(ValueError):
    pass


def _v3(a):
    return Vec3(a[0], a[1], a[2])


def read_project(path, strip=STRIP_KEYS):
    with open(path, encoding='utf-8') as f:
        text = f.read()
    return load_project_text(text, os.path.basename(path), strip=strip)


def load_project_text(text, name='project', strip=STRIP_KEYS):
    try:
        P = json.loads(text)
    except ValueError as e:
        raise ProjectFormatError('Not a project file') from e
    return project_from_dict(P, name, strip=strip)


def project_from_dict(P, name='project', strip=STRIP_KEYS, load=True):
    """port of the data part of loadProject (9450) + restore (1218). P is mutated: stripped keys are removed.
    load=False: the project's own text read back inside a session (the session's model after an undo, a redo, a
    reopened .blend) — what restore (1358) does with a snapshot: the model comes back exactly as it was written, so
    loadProject's fix-ups of a saved file stay out (the stale global step, 12813: the Conversion tab's project loop
    step undone came back as the hair's step — 21/10/2026)."""
    if not isinstance(P, dict) or P.get('app') != 'anime-hair-modeler':
        raise ProjectFormatError('Not a Stylized HairMesh Creator project')
    if P.get('preset'):
        raise ProjectFormatError('This is a preset, not a project')
    stripped = [k for k in strip if k in P]
    for k in stripped:
        del P[k]

    proj = M.Project(name=name, app=P['app'], version=P.get('version', 1), stripped=stripped)

    # ---- head (loadProject 9461-9465) ----
    h = P.get('head')
    if h and h.get('src'):
        proj.head = M.Head(id=h['id'], name=h.get('name', ''), visible=h.get('visible', True), showWire=bool(h.get('showWire')), src=h['src'],
                           look=head_look(h.get('look'), h['src']))      # 1.0.2 (loadProject 12798: makeHeadItem → normLook)
        geo = head_geometry(proj.head)
        if geo:
            proj.S = fit_to_box(geo[0])                             # restore → fitToBox when the head changed (1265)

    # ---- UID (9466-9468) ----
    max_id = proj.head.id if proj.head else 0
    for o in P['items']:
        max_id = max(max_id, o['id'])
    for a in P['anchors']:
        max_id = max(max_id, a['id'])
    proj.uid = max(1, max_id + 1)

    # ---- anchors (restore 1231-1235) ----
    for r in P['anchors']:
        a = anchor_from_dict(r)
        proj.anchors[a.id] = a

    # ---- items, in file order (restore 1236-1257) ----
    for o in P['items']:
        proj.items[o['id']] = _item_from_dict(o, proj)

    # ---- no head: the frame comes from the hair (loadProject 14394 calls fitToHair) ----
    if proj.head is None:
        frame = fit_to_hair(proj)
        if frame is not None:
            proj.S = frame

    proj.roots = list(P['roots'])
    proj.counters = dict(P.get('counters') or {})
    meshMaskMerge(proj)                                             # 1.0.2 (restore, 1393: after the roots, whose masks it takes out)
    c = P.get('conv')
    if c:                                                           # (restore, 1265: a file saved with a conversion pending opens with it; a session's own text never has it)
        proj.conv = {'entries': list(c['entries']), 'hidden': list(c['hidden']), 'replaced': [], 'shown': []}
    proj.camera = P.get('camera')
    cr = P.get('credits')
    proj.credits = [c for c in cr if c and c.get('name')] if isinstance(cr, list) and cr else None

    # ---- settings (loadProject 9470-9483) ----
    st = P.get('settings') or {}
    S = proj.settings
    for k in M.CONV_KEYS:
        if st.get(k) is not None:
            setattr(S, k, st[k])
    if st.get('step') is None and truthy(st.get('density')):
        S.step = proj.S.headH / st['density']
    # older saves could carry a stale global step: the step the hair actually uses wins (9473; loadProject only)
    u = usedStep(proj) if load else None
    if truthy(u) and (not truthy(S.step) or abs(S.step / u - 1) > 0.02):
        S.step = u
    if st.get('guideDefaults'):
        S.guideDefaults = {**S.guideDefaults, **st['guideDefaults']}
    if st.get('xray') is not None:
        S.xray = max(0, min(1, st['xray']))
    if st.get('guideOpacity') is not None:
        S.guideOpacity = st['guideOpacity']
    if st.get('brushMode'):
        S.brushMode = st['brushMode']
    applyView(S, st.get('view'))                                    # 1.0.3 (14405): the viewport display of this project
    # settings of a later version: kept as read, written back after the known ones
    S.extra = {k: _copy(v) for k, v in st.items() if k not in _SETTINGS_KNOWN}
    S.convChecked = [i for i in (st.get('convChecked') or []) if i in proj.items]
    if load:
        # guides made before the Guides group existed move into it (loadProject 10172-10173; lot 4.2: missing until
        # then, found in 4.1)
        gg = [g for g in proj.guides() if not getattr(proj.items.get(g.parent), 'guidesGroup', False)]
        if any(g.parent is not None and (g.parent not in proj.items or proj.items[g.parent].type != 'group') for g in gg):
            raise ProjectFormatError('A guide sits in a group the file does not have')   # (the original's loadProject throws there: "Could not open")
        if gg:
            from .duplicate import guidesGroup
            G = guidesGroup(proj)
            for g in gg:
                arr = proj.childrenOf(g.parent)
                if g.id in arr:
                    arr.remove(g.id)
                elif arr:
                    arr.pop()                                       # (splice(indexOf = -1, 1): the original drops the last one)
                g.parent = G.id; G.children.append(g.id)
        from .groups import panelsRendered
        panelsRendered(proj, None)                                  # (loadProject ends rendering the outliner, 8261: orderRoots, and renderConvList -> syncConvUI fills the first ticked group's conv, 9097)
    return proj


def anchor_from_dict(r):
    """an anchor record of the file ({id, p, s, ps?, at?, sc?}) as restore (1231-1236) makes it"""
    a = M.Anchor(id=r['id'], pos=_v3(r['p']), smooth=r['s'])
    if r.get('ps'):
        a.pass_ = list(r['ps'])
    if r.get('at'):
        a.att = M.Attachment(c=r['at']['c'], t=r['at']['t'])
        a.attPos = a.pos.clone()
    if r.get('sc') is not None:                                     # 1.0.1: if (r.sc != null) a.sec = r.sc (restore, 1236)
        a.sec = r['sc']
    return a


_SETTINGS_KNOWN = frozenset(M.CONV_KEYS) | {'convChecked', 'guideDefaults', 'xray', 'guideOpacity', 'brushMode', 'density', 'view'}


_HEX6 = re.compile(r'#[0-9a-f]{6}\Z', re.I)                     # /^#[0-9a-f]{6}$/i


def _num(v):
    """the unary plus of applyView (`+v[k]`) on what a JSON file can hold"""
    if v is True or v is False:
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return 0
    if isinstance(v, str):
        try:
            return float(v) if v.strip() else 0
        except ValueError:
            return math.nan
    return math.nan


def applyView(S, v):
    # port of applyView (hair-modeler.clean.html:15818 of 1.0.3), the data part: each value taken only when it is one
    if not isinstance(v, dict):
        return
    for k in M.VIEW_SHOW:
        if v.get(k) is True or v.get(k) is False:
            S.view[k] = v[k]
    for k in M.VIEW_OP:
        n = _num(v[k]) if k in v else math.nan
        if n > 0 and n <= 1:
            if k == 'guideOpacity':
                S.guideOpacity = n
            else:
                S.view[k] = n
    if 'xray' in v and v['xray'] is not None:
        n = _num(v['xray'])
        if n >= 0 and n <= 1:
            S.xray = max(0, min(1, n))                              # setXray (15772)
    if v.get('oneColor') is True or v.get('oneColor') is False:
        S.view['oneColor'] = v['oneColor']
    mc = v.get('modelColor')
    if isinstance(mc, str) and _HEX6.match(mc):
        S.view['modelColor'] = mc


def viewData(S):
    # port of viewData (hair-modeler.clean.html:15813 of 1.0.3)
    o = {'xray': S.xray, 'oneColor': bool(S.view['oneColor']), 'modelColor': S.view['modelColor']}
    for k in M.VIEW_SHOW:
        o[k] = S.view[k] is not False
    for k in M.VIEW_OP:
        o[k] = S.guideOpacity if k == 'guideOpacity' else S.view[k]
    return o


def _copy(v):
    """a deep copy of plain JSON data (JSON.parse(JSON.stringify(v)) / cloneLook of the original)"""
    return json.loads(json.dumps(v))


def js_json_copy(v):
    """JSON.parse(JSON.stringify(v)) of plain data that may hold NaN / ±Infinity (a flat lock's weights painted past
    their saved end, 1.1f): those become null, as JSON.stringify writes them"""
    if isinstance(v, dict):
        return {k: js_json_copy(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [js_json_copy(x) for x in v]
    if isinstance(v, float) and (v != v or v in (math.inf, -math.inf)):
        return None
    return v


def meshMaskMerge(proj):
    """port of meshMaskMerge (hair-modeler.clean.html:1426): the separate Mask modifiers of an imported mesh (1.0.1) go
    into its Thickness modifier as `hold` (the first mask becomes that modifier when there is none)"""
    old = [x for x in proj.items.values() if x.type == 'xmod' and x.mesh is not None and x.kind == 'mask']
    for mid in dict.fromkeys(x.mesh for x in old):
        mk = [x for x in old if x.mesh == mid]; on = [x for x in mk if x.visible is not False and x.mask]
        n = max([len(x.mask) if x.mask else 0 for x in mk])
        if not (n > 0):
            continue

        def keep(v):
            k = 1
            for x in on:
                w = x.mask[v] if v < len(x.mask) else None
                k = k * (1 - (w if w else 0))
            return 1 - k
        hold = [keep(v) for v in range(n)]
        th = [x for x in proj.items.values() if x.type == 'xmod' and x.mesh == mid and x.kind == 'thick']
        if not th:                                                  # the first mask becomes the Thickness modifier
            x = mk.pop(0)
            x.kind = 'thick'; x.p = {'amt': 0}; x.mask = [0] * n; x.visible = True
            x.name = re.sub(r'^Mask', 'Thickness', x.name); th = [x]
        for x in th:
            x.hold = list(hold)
        for x in mk:
            del proj.items[x.id]
            arr = proj.roots if x.parent is None else getattr(proj.items.get(x.parent), 'children', None)
            if arr and x.id in arr:
                arr.remove(x.id)
        m = proj.items.get(mid)
        if m is not None and hasattr(m, 'dirty'):
            m.dirty = True


def item_from_dict(o, proj):
    """an item record of the file as restore (1237-1257) makes it; a curve's anchors must be in proj.anchors"""
    return _item_from_dict(o, proj)


def _common(o) -> dict[str, Any]:
    return dict(id=o['id'], type=o['type'], name=o.get('name', ''), parent=o.get('parent'), visible=o.get('visible', True), q=list(o['q']) if o.get('q') else None, raw=o)


def _mask_in(m):
    """copyTipMask (1679 in 1.0.3) of a file's mask: { curve id: a copy of the weights }; None when absent (JS truth: {} kept)"""
    return {int(k): list(v) for k, v in m.items()} if truthy(m) else None


def _mask_out(m):
    """copyTipMask of an item's mask for the file: string keys in the order a JS object lists integer keys (ascending)"""
    return {str(k): list(m[k]) for k in sorted(m, key=int)}


def _fixed3(v):
    """`+v.toFixed(3)` (the shell's deformation in the file, snapshot 1351 in 1.0.3); an integral value as an int, as
    JSON.stringify writes it"""
    x = float(to_fixed(v, 3))
    return int(x) if x == int(x) else x


def _item_from_dict(o, proj):
    t = o['type']
    c = _common(o)
    if t == 'group':
        return M.Group(**c, children=list(o.get('children', [])), color=o.get('color'), expanded=o.get('expanded', True), sym=dict(o['sym']) if o.get('sym') else None,
                       guides=o.get('guides') or 'auto', conv=dict(o['conv']) if o.get('conv') else None, guidesGroup=bool(o.get('guidesGroup')), meshesGroup=bool(o.get('meshesGroup')),
                       importGroup=bool(o.get('importGroup')), labels=dict(o['labels']) if o.get('labels') else None, guidesOff=bool(o.get('guidesOff')))
    if t == 'curve':
        anchors = []
        for aid in o['anchors']:
            a = proj.anchors.get(aid)
            if a is None:
                raise ProjectFormatError(f'curve {o["id"]} refers to unknown anchor {aid}')
            anchors.append(a)
        ovr = {}
        for k, v in o.get('ovr') or []:
            ovr[int(k)] = M.HandleOverride(in_=_v3(v['in']) if v.get('in') else None, out=_v3(v['out']) if v.get('out') else None)
        return M.Curve(**c, anchors=anchors, ovr=ovr, closed=bool(o.get('closed')), ring=o.get('ring'), round=o.get('round'), fair=o.get('fair'),
                       asm=o.get('asm'), asrc=[_v3(a) for a in o['asrc']] if o.get('asrc') else None, junc=bool(o.get('junc')), conn=o.get('conn'),
                       para=bool(o.get('para')),
                       rib=_copy(o['rib']) if truthy(o.get('rib')) else None,  # 1.0.2 (restore, 1359: JS truth, {} kept)
                       path=[list(x) for x in o['path']] if truthy(o.get('path')) else None)   # ([] kept, 1360)
    if t == 'surface':
        return M.Surface(**c, a=o['a'], b=o['b'], p=dict(o.get('p') or {}))
    if t == 'guide':
        return M.Guide(**c, kind=o.get('kind') or 'line', hits=[dict(h) for h in (o.get('hits') or [])], p=dict(o.get('p') or {}), rings=list(o['rings']) if o.get('rings') else None, off=bool(o.get('off')))
    if t == 'crown':
        return M.Crown(**c, part=o.get('part'), low=o.get('low'), p=dict(o.get('p') or {}))
    if t == 'cap':
        return M.Cap(**c, p={**M.CAP_DEFAULTS, **(o.get('p') or {})})   # restore (1252)
    if t == 'skin':
        return M.Skin(**c, p={**M.SKIN_DEFAULTS, **(o.get('p') or {})})  # restore (1253)
    if t == 'tip':
        mask = {int(k): list(v) for k, v in (o.get('mask') or {}).items()} if o.get('mask') else None
        return M.Tip(**c, p={'dens': 1.5, **(o.get('p') or {})}, mask=mask)
    if t == 'even':                                                 # 1.0.3 (restore, 1403 there)
        return M.Even(**c, p={**M.EVEN_DEFAULTS, **(o.get('p') or {})}, mask=_mask_in(o.get('mask')))
    if t == 'shell':                                                # 1.0.3 (restore, 1404 there): off and def as JS truth reads them ([] kept)
        d = o.get('def')
        return M.Shell(**c, p={**M.SHELL_DEFAULTS, **(o.get('p') or {})}, baked=truthy(o.get('baked')), mask=_mask_in(o.get('mask')),
                       off=list(o['off']) if truthy(o.get('off')) else None,
                       def_=M.ShellDef(ns=d['ns'], na=d['na'], d=list(d['d']), v=d.get('v')) if truthy(d) else None)
    if t == 'tube':
        tube = M.Tube(**c, src=o.get('src'), profile=[list(q) for q in (o.get('profile') or [])], p=dict(o.get('p') or {}))
        if o.get('profs') is not None:                              # 1.0.1 (restore, 1254): the three profiles; profile = profs[0], one list
            tube.profs = [[list(q) for q in P] for P in o['profs']]
            if tube.profs:                                          # (an empty list: the original would fail on its next save)
                tube.profile = tube.profs[0]
        return tube
    if t == 'xmod':
        return M.XMod(**c, tube=o.get('tube'), kind=o.get('kind'), p=dict(o.get('p') or {}), mask=list(o['mask']) if o.get('mask') else None, mesh=o.get('mesh'),
                      hold=list(o['hold']) if o.get('hold') else None)   # 1.0.2 (restore, 1373)
    if t == 'imesh':
        return M.IMesh(**c, src=o.get('src'), collide=bool(o.get('collide')), smoothN=bool(o.get('smoothN')), look=_copy(o['look']) if o.get('look') else None)
    if t == 'mesh':
        d = o.get('data')
        data = M.MeshData(pos=[_v3(p) for p in d['pos']], faces=[list(f) for f in d['faces']]) if d else None
        return M.MeshItem(**c, data=data, pending=bool(o.get('pending')), srcSurfs=list(o.get('srcSurfs') or []), srcCrowns=list(o.get('srcCrowns') or []), symBaked=bool(o.get('symBaked')),
                          smoothN=bool(o.get('smoothN')))
    return M.Item(**c)


def fit_to_hair(proj):
    """port of fitToHair (hair-modeler.clean.html:6284, new in 1.0.1): the frame (S.scale, headH, yRef, center) of a
    project without a head, from the box of every curve's anchors — Box3.expandByPoint on the positions as they are
    (doubles: no float32 here, unlike a head's box). None when there is no anchor (box.isEmpty(): the original keeps
    the frame it had)."""
    mn = [float('inf')] * 3
    mx = [float('-inf')] * 3
    for c in proj.curves():
        for a in c.anchors:
            p = a.pos
            mn[0] = js_min(mn[0], p.x); mn[1] = js_min(mn[1], p.y); mn[2] = js_min(mn[2], p.z)
            mx[0] = js_max(mx[0], p.x); mx[1] = js_max(mx[1], p.y); mx[2] = js_max(mx[2], p.z)
    if mx[0] < mn[0] or mx[1] < mn[1] or mx[2] < mn[2]:
        return None
    return box_frame(mn, mx)


def usedStep(proj):
    # port of usedStep (hair-modeler.clean.html:1572): the loop step most walls' groups use, rounded to 6 digits
    # Memoised: levelH calls it for every loop of every lock outside a group (thousands of times per build) while
    # nothing it reads changes. The key is the project generation (bumped by whoever changes items, parents or a
    # group's conv.step, groupConv included) plus the item count as a guard.
    key = (proj.gen, len(proj.items))
    memo = proj._memo.get('usedStep')
    if memo is not None and memo[0] == key:
        return memo[1]
    w = {}                                                          # a Map: insertion order matters for ties
    for s in proj.items.values():
        if s.type != 'surface':
            continue
        p = proj.parentOf(s)
        while p:
            if p.type == 'group' and p.conv and truthy(p.conv.get('step')):
                k = to_precision_number(p.conv['step'], 6)
                w[k] = w.get(k, 0) + 1
                break
            p = proj.parentOf(p)
    best, bn = None, 0
    for k, n in w.items():
        if n > bn:
            bn = n; best = k
    proj._memo['usedStep'] = (key, best)
    return best


def defaultStep(proj) -> float:
    # port of defaultStep (1581): S.step || usedStep() || S.headH / 24
    return js_or(proj.settings.step, js_or(usedStep(proj), proj.S.headH / 24))   # type: ignore[return-value]  (never None: headH / 24 is a number)


def project_json_text(P_dict):
    """The project JSON as stored in the .blend: compact, same key order as the file, stripped keys gone."""
    return json.dumps(P_dict, separators=(',', ':'), ensure_ascii=False)


# ======================= Writing a project (phase 3.1) =======================
# port of projectData (hair-modeler.clean.html:9375) + the data part of snapshot (1181): the same JSON the app saves,
# minus what only the browser has (thumb, refs: the thumbnail and the reference-board images are not kept by the
# port, see docs/fase-3-plano-ciclo-nativo.md D7).

def snapshot(proj, mesh_data=True):
    """port of snapshot (1181) without head / headVisible / refPlanes (projectData deletes them). `conv` (1218): the
    pending conversion goes in the file; the session's text keeps it apart (hairmesh_conv, the undo). Anchors come out in first-seen order along the curves, like
    the original's Map; anchors no curve uses are not written. `mesh_data=False` (ours, 3.7): the mesh items with
    `data: null` (the .blend keeps their coordinates in a datablock of their own, blender/session.py)."""
    anchors = {}
    its = []
    for it in proj.items.values():
        if it.type == 'head':
            continue
        o = {'id': it.id, 'type': it.type, 'name': it.name, 'parent': it.parent, 'visible': it.visible}
        if it.q:
            o['q'] = list(it.q)                                     # own rotation (the gizmo's Local axes)
        t = it.type
        if t == 'group':
            o.update(children=list(it.children), color=it.color, expanded=it.expanded, sym=dict(it.sym) if it.sym else None, guides=it.guides or 'auto',
                     conv=dict(it.conv) if it.conv else None, guidesGroup=bool(it.guidesGroup), meshesGroup=bool(it.meshesGroup), importGroup=bool(it.importGroup),
                     labels=dict(it.labels) if it.labels else None, guidesOff=bool(it.guidesOff))
        elif t == 'imesh':
            o['src'] = it.src                                       # the file (shared, never changed)
            if it.collide:
                o['collide'] = True                                 # 1.0.2 (snapshot, 1301)
            if it.smoothN:
                o['smoothN'] = True
            if it.look:
                o['look'] = _copy(it.look)
        elif t == 'curve':
            ids = []
            for a in it.anchors:
                if a.id not in anchors:
                    r = {'id': a.id, 'p': a.pos.toArray(), 's': a.smooth}
                    if a.pass_:
                        r['ps'] = list(a.pass_)
                    if a.att:
                        r['at'] = {'c': a.att.c, 't': a.att.t}
                    if a.sec is not None:
                        r['sc'] = a.sec                             # 1.0.1 (snapshot, 1193)
                    anchors[a.id] = r
                ids.append(a.id)
            o['anchors'] = ids
            if it.conn:
                o['conn'] = it.conn
            o['ovr'] = [[k, {'in': v.in_.toArray() if v.in_ else None, 'out': v.out.toArray() if v.out else None}] for k, v in it.ovr.items()]
            if it.closed:
                o['closed'] = True
            if it.ring:
                o['ring'] = it.ring
            if it.round:
                o['round'] = it.round
            if it.fair:
                o['fair'] = it.fair
            if it.asm and it.asrc:
                o['asm'] = it.asm; o['asrc'] = [p.toArray() for p in it.asrc]
            if it.junc:
                o['junc'] = True
            if it.para:
                o['para'] = True                                    # 1.0.1 (1204)
            if truthy(it.rib):
                o['rib'] = js_json_copy(it.rib)                     # 1.0.2 (1316): a flat lock (core/rib.py; NaN weights write null)
            if truthy(it.path):
                o['path'] = [list(x) for x in it.path]              # 1.0.2 (1317): an edge curve (kept, not edited here)
        elif t == 'surface':
            o.update(a=it.a, b=it.b, p=dict(it.p))
        elif t == 'guide':
            o.update(kind=it.kind or 'line', hits=[] if it.kind == 'disc' else [dict(h) for h in it.hits], p=dict(it.p), rings=list(it.rings) if it.rings else None, off=bool(it.off))
        elif t == 'mesh':
            d = it.data
            o.update(data={'pos': [p.toArray() for p in d.pos], 'faces': [list(f) for f in d.faces]} if d and mesh_data else None,   # projectData (9378): arrays, not V3
                     pending=it.pending, srcSurfs=list(it.srcSurfs), srcCrowns=list(it.srcCrowns or []), symBaked=bool(it.symBaked))
            if it.smoothN:
                o['smoothN'] = True                                 # 1.0.2 (1321)
        elif t == 'tube':
            o.update(src=it.src, profile=[list(q) for q in it.profile], p=dict(it.p))
            if it.profs is not None:
                o['profs'] = [[list(q) for q in P] for P in it.profs]   # 1.0.1 (1209), after p
        elif t == 'xmod':
            if it.tube is not None:                                 # (an imported mesh's Extra: it.tube undefined, left out by JSON.stringify)
                o['tube'] = it.tube
            o.update(kind=it.kind, p=dict(it.p), mask=list(it.mask) if it.mask else None)
            if it.mesh is not None:
                o['mesh'] = it.mesh
            if it.hold:
                o['hold'] = list(it.hold)                           # 1.0.2 (1323)
        elif t == 'crown':
            o.update(part=it.part, low=it.low, p=dict(it.p))
        elif t in ('cap', 'skin', 'tip'):
            o['p'] = dict(it.p)
            if t == 'tip' and it.mask:
                o['mask'] = {str(k): list(w) for k, w in it.mask.items()}   # copyTipMask (1439); JSON keys are strings
        elif t == 'even':                                           # 1.0.3 (snapshot, 1349-1350 there)
            o['p'] = dict(it.p)
            if truthy(it.mask):
                o['mask'] = _mask_out(it.mask)
        elif t == 'shell':                                          # 1.0.3 (snapshot, 1351 there): p, baked, then mask / off / def when set
            o.update(p=dict(it.p), baked=bool(it.baked))
            if truthy(it.mask):
                o['mask'] = _mask_out(it.mask)
            if truthy(it.off):
                o['off'] = list(it.off)
            D = it.def_
            if D is not None:
                o['def'] = {'ns': D.ns, 'na': D.na, 'd': [_fixed3(v) for v in D.d]}
                if truthy(D.v):
                    o['def']['v'] = D.v
        else:
            for k, v in it.raw.items():                             # a type the port does not model: written back as read
                o.setdefault(k, v)
        its.append(o)
    conv = {'entries': list(proj.conv['entries']), 'hidden': list(proj.conv['hidden'])} if proj.conv and mesh_data else None
    return {'roots': list(proj.roots), 'anchors': list(anchors.values()), 'items': its, 'conv': conv, 'counters': dict(proj.counters)}


def project_to_dict(proj, mesh_data=True):
    """port of projectData (9375): the file as a dict, same keys in the same order as the app writes them, except
    `thumb` and `refs` (not kept) and `camera` only when the project has one (an imported project keeps the file's;
    the original keeps its current camera when the key is missing). `mesh_data`: see snapshot."""
    S = proj.settings
    d = {'app': 'anime-hair-modeler', 'version': 1}
    d.update(snapshot(proj, mesh_data))
    h = proj.head
    d['head'] = {'id': h.id, 'name': h.name, 'visible': h.visible, 'showWire': bool(h.showWire), 'src': h.src or None, 'look': _copy(h.look) if h.look else None} if h else None
    d['settings'] = {'step': S.step, 'dev': S.dev, 'straighten': S.straighten, 'loopOrient': S.loopOrient, 'tipMode': S.tipMode, 'relax': S.relax,
                     'mergeTrunks': S.mergeTrunks, 'mergeDist': S.mergeDist, 'proxy': S.proxy, 'convChecked': list(S.convChecked), 'guideDefaults': dict(S.guideDefaults), 'xray': S.xray,
                     'guideOpacity': S.guideOpacity, 'brushMode': S.brushMode, 'view': viewData(S)}
    for k, v in S.extra.items():                                    # (a later version's settings, as read)
        d['settings'].setdefault(k, _copy(v))
    if proj.camera is not None:
        d['camera'] = proj.camera
    if proj.credits:
        d['credits'] = proj.credits                                 # projectData: proj.credits || undefined
    return d


def kept_fields(path):
    """ours (1c of the manual test of the lot ux5): the fields of a .hairmesh the port does not keep (thumb, refs —
    in the example 10 MB of reference images) as the file at `path` has them, for a Save into that file (or a Save As
    from it) to write them back instead of losing them; {} when the file has none, None when it is missing or cannot
    be read (the caller warns: they are not kept)."""
    try:
        with open(path, encoding='utf-8') as f:
            F = json.load(f)
    except (OSError, ValueError):
        return None
    return {k: F[k] for k in STRIP_KEYS if k in F} if isinstance(F, dict) else None


def with_kept(d, kept):
    """the file dict with the kept fields where projectData (14284) puts them: thumb right after version, refs after
    camera (before credits)"""
    if not kept:
        return d
    out = {}
    for k, v in d.items():
        if k == 'credits' and 'refs' in kept:
            out['refs'] = kept['refs']
        out[k] = v
        if k == 'version' and 'thumb' in kept:
            out['thumb'] = kept['thumb']
    if 'refs' in kept and 'refs' not in out:
        out['refs'] = kept['refs']
    return out


def project_file_text(proj, kept=None):
    """The .hairmesh file: JSON.stringify's compact form (no spaces), UTF-8 as the app writes it; `kept`: the thumb /
    refs of the file it came from (kept_fields)."""
    return json.dumps(with_kept(project_to_dict(proj), kept), separators=(',', ':'), ensure_ascii=False)


def write_project(path, proj, kept=None):
    """the file written whole or not at all: a temporary file beside it, then swapped in (a Save writes over the
    only copy of the reference images: a disk full or a locked file must never leave it cut)"""
    import os
    text = project_file_text(proj, kept)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


HEAD_PAYLOAD_KEYS = ('text', 'b64')                                 # the head file itself inside head.src (3229): OBJ text, or base64 for GLB


def head_src_split(src):
    """D7: the head's source file out of the project JSON. Returns ({ext, …}, payload, key) with key 'text' or
    'b64', or (src, None, None) when the src carries no file."""
    if not src:
        return src, None, None
    for k in HEAD_PAYLOAD_KEYS:
        if src.get(k):
            meta = {kk: v for kk, v in src.items() if kk not in HEAD_PAYLOAD_KEYS}
            return meta, src[k], k
    return src, None, None
