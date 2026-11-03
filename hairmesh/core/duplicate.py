# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Duplicate (3.8b): port of duplicateSelection (hair-modeler.clean.html:9904) and the guide makers it uses.

A module of its own, as scissors.py: it needs groups.outlinerOrder / convTargets, and groups.py imports edit.py.
Pure Python, no bpy. The selection is ids (selection.Selection); the new selection is returned for the caller to store;
toasts, undo steps (commit) and display objects are the caller's."""
from . import model as M
from .edit import addItem, makeCurve, newAnchor
from .groups import convTargets, curvesIn, outlinerOrder
from .guides import discBasis, discRings
from .hairmesh_io import defaultStep
from .js import clamp, index_of, js_or
from .rowbase import groupConv
from .vec3 import V3


def topSelected(P, sel_items):
    # port of topSelected (hair-modeler.clean.html:8561): the selected items in outliner order, without the items whose
    # ancestor is also selected
    sel = set(sel_items)
    out = []
    for iid in outlinerOrder(P):
        if iid not in sel:
            continue
        p = P.parentOf(P.items[iid]); top = True
        while p is not None:
            if p.id in sel:
                top = False
                break
            p = P.parentOf(p)
        if top:
            out.append(iid)
    return out


def guidesGroup(P):
    # port of guidesGroup (1035): the Guides group, made when there is none
    g = next((i for i in P.items.values() if i.type == 'group' and i.guidesGroup), None)
    if g is None:
        g = addItem(P, 'group', None, {'name': 'Guides'})
        g.guidesGroup = True; g.guides = 'show'; g.color = '#3fbf7a'; g.expanded = False
    return g


def guidesGroupForNew(P):
    # port of guidesGroupForNew (1044): a hidden Guides group is shown again, the guides that were in it stay hidden one
    # by one (the toast after 2.3 s is the caller's)
    g = guidesGroup(P)
    if not g.visible or g.guides == 'hide':
        if not g.visible:
            for iid in g.children:
                c = P.items.get(iid)
                if c is not None and not getattr(c, 'ring', None):
                    c.visible = False
        g.visible = True
        if g.guides == 'hide':
            g.guides = 'show'
    return g


def curStep(P):
    # port of curStep (1054): the first ticked group's loop step, else the default
    t = convTargets(P)
    return js_or(groupConv(P, t[0]).get('step') if t else None, defaultStep(P))   # (convTargets gives the groups themselves; the review of 4.2)


def guideDefaultsNow(P):
    # port of guideDefaultsNow (1055)
    d = P.settings.guideDefaults
    r = d.get('radius')
    return {**d, 'radius': r if r is not None else clamp(4 * curStep(P) / P.S.headH, 0.002, 0.5)}


def makeGuide(P, parent, hits, p=None):
    # port of makeGuide (1056), the data part: a guide line in the Guides group (`parent` is not used, as there)
    return addItem(P, 'guide', guidesGroupForNew(P).id, {'kind': 'line', 'hits': hits, 'p': p if p is not None else guideDefaultsNow(P),'samples': [], 'dirty': True})


def makeDisc(P, C, N, rOut, rIn, outPts=None, inPts=None):
    # port of makeDisc (1070), the data part: a NURBS disc and its two rings (closed curves of 6 anchors each)
    from .fdlibm import cos, sin
    G = guidesGroupForNew(P)
    g = addItem(P, 'guide', G.id, {'kind': 'disc', 'hits': [], 'p': {**guideDefaultsNow(P), 'rOut': rOut, 'rIn': rIn}, 'samples': [], 'dirty': True})
    g.name = 'Disc ' + str(P.counters['guide'])
    u, v = discBasis(N.clone().normalize())

    def ring(pts, r, nm):
        c = makeCurve(P, G.id); c.closed = True; c.ring = g.id; c.name = g.name + ' ' + nm
        if pts is None:
            pts = [C.clone().addScaledVector(u, r * cos(k * 3.141592653589793 / 3)).addScaledVector(v, r * sin(k * 3.141592653589793 / 3)) for k in range(6)]
        c.anchors = [newAnchor(P, p) for p in pts]; c.dirty = True
        return c
    g.rings = [ring(outPts, rOut, 'outer').id, ring(inPts, rIn, 'inner').id]
    return g


def onlyList(P, m):
    # port of onlyList (6445)
    o = m.p.get('only')
    lst = o if isinstance(o, list) else ([o] if o is not None else [])
    return [i for i in lst if i in P.items]


def duplicateMessage(made):
    # the toasts of duplicateSelection (9907, 9981): `made` None = nothing to duplicate (no top item, no anchor's curve)
    return 'Nothing to duplicate' if made is None else f"Duplicated {len(made)} item(s) — the copy's anchors are selected"


def surfDirtyCurves(P, made):
    """the curves whose locks a copied Cap / Fill holes / Density reads — the original sets S.surfDirty for those
    (9951-9953): the curves of the group each one lies in (every curve at the root). Ours: live.touch takes them."""
    out = {}
    todo = list(made or ())
    while todo:
        it = todo.pop()
        if it.type == 'group':
            todo.extend(P.items[c] for c in it.children if c in P.items)
        elif it.type in ('cap', 'skin', 'tip'):
            g = P.items.get(it.parent) if it.parent is not None else None
            for c in (curvesIn(P, g) if g is not None else P.curves()):
                out[c.id] = True
    return list(out)


def duplicateSelection(P, sel_items, sel_anchors):
    """port of duplicateSelection (hair-modeler.clean.html:9904), the data part. `sel_items` = S.sel (ids),
    `sel_anchors` = S.asel (ids, pick order). Returns (made, new_sel_items, new_sel_anchors): the copies of the top
    items, the new S.sel (their ids) and the new S.asel (the copy's anchors, so the gizmo moves the copy right away);
    made is None when there is nothing to duplicate ("Nothing to duplicate" is the caller's toast; the selection stays),
    and may be [] when every top item is one the original does not copy (a disc ring alone: "Duplicated 0 item(s)")."""
    roots = topSelected(P, sel_items)
    asel = set(sel_anchors)
    if not roots and asel:
        roots = [iid for iid in outlinerOrder(P) if P.items[iid].type == 'curve' and any(a.id in asel for a in P.items[iid].anchors)]
    if not roots:
        return None, list(sel_items), list(sel_anchors)
    in_set = {}                                                     # (a dict: the JS Set iterates in insertion order, a Python set by hash)

    def mark(iid):
        in_set[iid] = True
        it = P.items[iid]
        if it.type == 'group':
            for c in it.children:
                mark(c)
    for iid in roots:
        mark(iid)
    for iid in list(in_set):                                        # a wall takes its two curves (9910)
        it = P.items[iid]
        if it.type == 'surface':
            for c in (it.a, it.b):
                if c in P.items and c not in in_set:
                    roots.append(c); mark(c)
    # a curve goes with its modifier and the modifier's extras (Mask, Thickness, Twist, Noise, Curls) (9911-9913)
    for iid in list(in_set):
        if P.items[iid].type == 'curve':
            for t in [t for t in P.tubes() if t.src == iid]:
                for m in [t] + [x for x in P.of_type('xmod') if x.tube == t.id]:
                    if m.id not in in_set:
                        roots.append(m.id); mark(m.id)
    amap = {}; imap = {}; new_surfs = []; new_guides = []; new_anchors = []; new_tubes = []

    def cpA(a):
        n = amap.get(a.id)
        if n is None:
            n = newAnchor(P, a.pos); n.smooth = a.smooth
            if a.att:
                n.att = M.Attachment(c=a.att.c, t=a.att.t)
            if a.pass_:
                n.pass_ = list(a.pass_)
            if a.sec is not None:
                n.sec = a.sec
            amap[a.id] = n; new_anchors.append(n)
        return n
    new_crowns = []; new_x = []

    def place(n, after):
        # (9920) right after the item it copies, in the parent's list
        if after is None:
            return
        arr = P.childrenOf(n.parent)
        arr.pop(index_of(arr, n.id))                                # splice(indexOf(n.id), 1)
        arr.insert(index_of(arr, after) + 1, n.id)

    def copy(it, parent, after):
        if it.type == 'group':
            n = addItem(P, 'group', parent)
            n.color = it.color; n.expanded = it.expanded; n.sym = dict(it.sym) if it.sym else None; n.guides = it.guides or 'auto'
            n.conv = dict(it.conv) if it.conv else None; n.labels = dict(it.labels) if it.labels else None; n.guidesOff = bool(it.guidesOff)
            place(n, after); n.name = it.name + ' copy'; n.visible = it.visible
            for c in it.children:
                copy(P.items[c], n.id, None)
            return n
        if it.type == 'curve' and it.ring:
            return None                                             # disc rings are copied with their disc
        if it.type == 'curve':
            n = makeCurve(P, parent); n.anchors = [cpA(a) for a in it.anchors]
            if it.round:
                n.round = it.round
            if it.fair:
                n.fair = it.fair
            if it.junc:
                n.junc = True
            if it.para:
                n.para = True
            for aid, o in it.ovr.items():
                a = next((x for x in it.anchors if x.id == aid), None)
                if a is not None:
                    n.ovr[cpA(a).id] = M.HandleOverride(in_=o.in_.clone() if o.in_ else None, out=o.out.clone() if o.out else None)
        elif it.type == 'surface':
            n = addItem(P, 'surface', parent, {'a': it.a, 'b': it.b, 'p': dict(it.p), 'dirty': True}); new_surfs.append(n)
        elif it.type == 'guide' and it.kind == 'disc':
            rs = discRings(P, it)
            if len(rs) < 2:
                return None
            n = makeDisc(P, V3(), V3(0, 1, 0), it.p.get('rOut'), it.p.get('rIn'), [a.pos for a in rs[0].anchors], [a.pos for a in rs[1].anchors])
            n.p.update(it.p)
            for c in discRings(P, n):
                new_anchors.extend(c.anchors)
        elif it.type == 'guide':
            n = makeGuide(P, parent, [dict(h) for h in it.hits], dict(it.p)); new_guides.append(n)
        elif it.type == 'mesh':
            if P.conv and it.id in (P.conv.get('replaced') or ()):
                # ours (3.8b, found by the stress test): a confirmed mesh a pending one is replacing (P.conv['replaced'],
                # 3.7) is not copied — the copy of the pending mesh, confirmed, stands for the group's result; both
                # copies would be two meshes of one group shown together
                return None
            # (the same data object, as the original's `data: it.data`; setItemObj is the Blender side's)
            n = addItem(P, 'mesh', parent, {'data': it.data, 'pending': False, 'srcSurfs': list(it.srcSurfs), 'symBaked': it.symBaked, 'token': it.token})
        elif it.type == 'crown':
            n = addItem(P, 'crown', parent, {'part': it.part, 'low': it.low, 'p': {**it.p, 'sym': None}, 'dirty': True}); new_crowns.append(n)
        elif it.type == 'xmod' and it.mesh is not None:             # a mesh modifier: copied with its mesh when that is copied too
            if it.mesh in roots:
                return None
            n = addItem(P, 'xmod', parent, {'mesh': it.mesh, 'kind': it.kind, 'p': dict(it.p), 'mask': list(it.mask) if it.mask else None}); new_x.append(n)
        elif it.type == 'xmod':
            n = addItem(P, 'xmod', parent, {'tube': it.tube, 'kind': it.kind, 'p': dict(it.p), 'mask': list(it.mask) if it.mask else None}); new_x.append(n)
        elif it.type == 'tube':
            n = addItem(P, 'tube', parent, {'src': it.src, 'profile': [list(q) for q in it.profile], 'p': dict(it.p), 'dirty': True}); new_tubes.append(n)
            if it.profs:
                n.profs = [[list(q) for q in Pr] for Pr in it.profs]; n.profile = n.profs[0]
        elif it.type == 'cap':
            n = addItem(P, 'cap', parent, {'p': dict(it.p), 'srcSurfs': []})
        elif it.type == 'skin':
            n = addItem(P, 'skin', parent, {'p': dict(it.p), 'cands': []})
        elif it.type == 'tip':
            n = addItem(P, 'tip', parent, {'p': dict(it.p)})
        elif it.type == 'imesh':
            n = addItem(P, 'imesh', parent, {'src': it.src, 'dirty': True})
            aft = n.id
            for x in [x for x in P.of_type('xmod') if x.mesh == it.id]:
                y = addItem(P, 'xmod', parent, {'mesh': n.id, 'kind': x.kind, 'p': dict(x.p), 'mask': list(x.mask) if x.mask else None, 'name': x.name + ' copy', 'visible': x.visible})
                place(y, aft); aft = y.id
        else:
            return None
        if it.type == 'curve' and it.conn:
            n.conn = it.conn
        if it.type == 'guide' and it.off:
            n.off = True
        n.name = it.name + ' copy'; n.visible = it.visible; imap[it.id] = n.id; place(n, after)
        return n
    made = [n for n in (copy(P.items[iid], P.items[iid].parent, iid) for iid in roots) if n is not None]
    for oid, nid in imap.items():                                   # a Tip mask follows its copied curves (9966)
        o = P.items.get(oid); n = P.items.get(nid)
        if o is not None and n is not None and o.type == 'tip' and o.mask:
            n.mask = {imap.get(int(cid), cid): list(W) for cid, W in o.mask.items()}
    for s in new_surfs:
        s.a = imap.get(s.a, s.a); s.b = imap.get(s.b, s.b)
    for t in new_tubes:
        t.src = imap.get(t.src, t.src)
    for g in new_guides:
        for h in g.hits:
            h['c'] = imap.get(h.get('c'), h.get('c'))
    for k in new_crowns:
        k.part = imap.get(k.part, k.part); k.low = imap.get(k.low, k.low)
    for x in new_x:
        if x.mesh is not None:
            x.mesh = imap.get(x.mesh, x.mesh)
            m = P.items.get(x.mesh)
            if m is not None and hasattr(m, 'dirty'):
                m.dirty = True
            continue
        x.tube = imap.get(x.tube, x.tube)
        if x.p.get('only') is not None:
            x.p['only'] = [imap.get(i, i) for i in onlyList(P, x)]
        t = P.items.get(x.tube)
        if t is not None:
            t.dirty = True
    id_of = {aid: n.id for aid, n in amap.items()}
    for n in new_anchors:
        if n.att:
            n.att.c = imap.get(n.att.c, n.att.c); n.attPos = None
        if n.pass_:
            n.pass_ = [id_of.get(i, i) for i in n.pass_]
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    return made, [n.id for n in made], [n.id for n in new_anchors]
