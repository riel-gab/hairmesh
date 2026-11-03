# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Symmetry applied once (4.10): port of applySymmetry (hair-modeler.clean.html:9858, 1.0.1), the group panel's
"Apply symmetry" button (8687-8689). The live mirrored display (syncMirrors, 9832) is the Blender side's; the mirror
of the converted meshes at conversion time (mirrorData) lives in convert.py.

Pure Python, no bpy. Ids come from Project.uid and names from nextNumber, as in the original, so the parity scripts
(tests/test_symmetry_parity.py) get the very same project file. The toast, the undo step (commit) and the display
objects (setItemObj, renderOutliner) are the caller's."""
from . import model as M
from .conversion import new_token
from .convert import mirrorPt
from .duplicate import makeGuide
from .edit import addItem, makeCurve, newAnchor
from .js import index_of


def symmetryMessage(mg):
    # the toast of applySymmetry (9900)
    return f'Symmetry applied — the mirrored half is in "{mg.name}"'


def applySymmetry(P, g):
    """port of applySymmetry (hair-modeler.clean.html:9858): bake the mirrored copy into real, editable curves and
    surfaces (anchors lying on the plane stay shared). The copy goes into its own group "<name> mirror" right after
    the original (sub-groups are recreated inside it), with the same colour and conversion settings.

    What the original does, step by step (and so this port):
    - every sub-group gets a group "<name> mirror" inside the mirror of its parent (at the end of its list); the mirror
      group of `g` goes right after `g`. Colour, Curves & anchors (guides), conv, expanded, visible and labels are
      copied, not the symmetry, the Guides / Meshes flags nor guidesOff; a ticked group's mirror is ticked too;
    - every curve of the group (its sub-groups too, rings included) gets a mirrored curve "<name> mirror" with
      Roundness, Smoothing and `para` (1.0.1), its anchors mirrored about S.center[axis] — an anchor within
      S.scale * 1e-4 of the plane is the same anchor (shared, a weld across the plane) —, each new anchor with the
      smoothness and the cross-section `sec` (1.0.1) of its source; the handle overrides mirrored (the axis component
      negated). Not copied: closed, ring, junc, conn, Even out (asm / asrc), the anchors' pass / att;
    - every wall: "<name> mirror" between the mirrored curves (a curve outside the group stays as it is), its p copied;
    - every guide in the group (makeGuide, 9890: in the Guides group, whatever its parent — makeGuide ignores it):
      a guide LINE "<name> mirror" through the same arc fractions of the mirrored curves, its p copied. A disc in the
      group is no exception: its hits (where the hair pierces it, found by rebuildDisc every frame) become the hits of
      a guide line "Disc N mirror" — the disc itself (its rings) is not mirrored;
    - every converted mesh not mirrored yet (symBaked false): "<name> mirror", the points mirrored and every face
      reversed, confirmed (pending false), with no source walls;
    - tubes, extras, crowns, caps, fill holes, density items: not copied.
    Then the group's symmetry is switched off (g.sym = {...sym, on: false}) and every curve is dirty.

    Returns (the mirror group, the toast), or (None, None) when the group's symmetry is not on (the original returns
    without a word; its button is disabled then, 8688). The selection is not changed (the group stays selected)."""
    sym = g.sym
    if not sym or not sym.get('on'):
        return None, None
    ax = sym.get('axis'); eps = P.S.scale * 1e-4
    cc = P.settings.convChecked

    def cloneGroup(src, parent, after):
        n = addItem(P, 'group', parent); n.name = src.name + ' mirror'
        n.color = src.color; n.guides = src.guides or 'auto'; n.conv = dict(src.conv) if src.conv else None
        n.expanded = src.expanded; n.visible = src.visible; n.labels = dict(src.labels) if src.labels else None
        if after is not None:
            arr = P.childrenOf(parent)
            arr.pop(index_of(arr, n.id))                            # splice(indexOf(n.id), 1)
            arr.insert(index_of(arr, after) + 1, n.id)              # splice(indexOf(after) + 1, 0, n.id)
        if src.id in cc and n.id not in cc:                         # S.convChecked.add (a Set)
            cc.append(n.id)
        return n
    mg = cloneGroup(g, g.parent, g.id)
    gmap = {g.id: mg.id}
    lst = []

    def walk(ids):
        for iid in ids:
            it = P.items[iid]
            if it.type == 'group':
                gmap[it.id] = cloneGroup(it, gmap.get(it.parent), None).id
                walk(it.children)
            else:
                lst.append(it)
    walk(list(g.children))

    def par(it):
        p = gmap.get(it.parent)
        return mg.id if p is None else p                            # gmap.get(it.parent) ?? mg.id
    amap = {}; imap = {}

    def cpA(a):
        n = amap.get(a.id)
        if n is None:
            if abs(getattr(a.pos, ax) - getattr(P.S.center, ax)) < eps:
                n = a
            else:
                n = newAnchor(P, mirrorPt(P, a.pos, ax)); n.smooth = a.smooth
                if a.sec is not None:
                    n.sec = a.sec                                   # 1.0.1 (9877)
            amap[a.id] = n
        return n

    def flipV(v):
        q = v.clone(); setattr(q, ax, -getattr(q, ax)); return q
    for cv in [i for i in lst if i.type == 'curve']:
        n = makeCurve(P, par(cv)); n.name = cv.name + ' mirror'; n.visible = cv.visible
        if cv.round:
            n.round = cv.round
        if cv.fair:
            n.fair = cv.fair
        if cv.para:
            n.para = True                                           # 1.0.1 (9882)
        n.anchors = [cpA(a) for a in cv.anchors]
        for aid, o in cv.ovr.items():
            a = next((x for x in cv.anchors if x.id == aid), None)
            if a is not None:
                n.ovr[cpA(a).id] = M.HandleOverride(in_=flipV(o.in_) if o.in_ else None, out=flipV(o.out) if o.out else None)
        imap[cv.id] = n.id
    for s in [i for i in lst if i.type == 'surface']:
        addItem(P, 'surface', par(s), {'name': s.name + ' mirror', 'visible': s.visible, 'a': imap.get(s.a, s.a), 'b': imap.get(s.b, s.b), 'p': dict(s.p), 'dirty': True})
    for gd in [i for i in lst if i.type == 'guide']:
        n = makeGuide(P, gd.parent, [{'c': imap.get(h.get('c'), h.get('c')), 't': h.get('t')} for h in gd.hits], dict(gd.p))
        n.name = gd.name + ' mirror'; n.visible = gd.visible
    for m in [i for i in lst if i.type == 'mesh' and not i.symBaked]:
        data = M.MeshData(pos=[mirrorPt(P, p, ax) for p in m.data.pos], faces=[list(reversed(f)) for f in m.data.faces])
        addItem(P, 'mesh', par(m), {'name': m.name + ' mirror', 'pending': False, 'srcSurfs': [], 'data': data, 'token': new_token()})
    g.sym = {**sym, 'on': False}
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    return mg, symmetryMessage(mg)
