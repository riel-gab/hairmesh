# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Editing the model the way the original's tools do (phase 3): creating items, curves, anchors and the head.

Pure Python, no bpy. Ids come from `Project.uid` (the original's UID) and names from `nextNumber` / `counters`
exactly as in the original, so a scene built by the same actions on both sides gets the same ids and names
(the parity scripts of the harness, `--script`, depend on it).
"""
import math
import re
from dataclasses import dataclass, field

from . import model as M
from .curves import bez, curveHandles, curveSegs, fitHandles, markAnchorsDirty
from .headobj import fit_to_box, float32, head_geometry
from .js import at, clamp, index_of, js_max, js_min, js_round
from .mat4 import Mat4, Quat, applyMatrix3, applyMatrix4, applyQuaternion, mat3_from_mat4
from .raycast import FrontSide, Plane, Raycaster, TriMesh
from .rowbase import groupConv
from .vec3 import V3, Vec3

# port of TYPE_NAMES (hair-modeler.clean.html:709)
TYPE_NAMES = {'group': 'Group', 'curve': 'Curve', 'surface': 'Surface', 'mesh': 'Mesh', 'guide': 'Guide', 'tube': 'Modifier', 'xmod': 'Extra',
              'crown': 'Crown', 'cap': 'Cap', 'imesh': 'Imported mesh', 'skin': 'Fill holes', 'tip': 'Density'}
# port of counters (708): the initial object of a fresh scene (no 'tip': nextNumber adds it on the first Density item)
COUNTERS0 = {'group': 0, 'curve': 0, 'surface': 0, 'mesh': 0, 'guide': 0, 'tube': 0, 'xmod': 0, 'crown': 0, 'cap': 0, 'imesh': 0, 'skin': 0}

_CLASSES = {'group': M.Group, 'curve': M.Curve, 'surface': M.Surface, 'guide': M.Guide, 'crown': M.Crown, 'cap': M.Cap, 'skin': M.Skin,
            'tip': M.Tip, 'tube': M.Tube, 'xmod': M.XMod, 'imesh': M.IMesh, 'mesh': M.MeshItem}


def new_project(name='hair-project.hairmesh'):
    """A fresh scene: what the original has right after it starts (UID 1, no items, no head, default settings)."""
    return M.Project(name=name, uid=1, counters=dict(COUNTERS0))


def nextNumber(P, type_):
    # port of nextNumber (hair-modeler.clean.html:730): one more than the highest "<Type> N" still in the scene
    # (deleting Modifier 1 and making a new one gives Modifier 1 again, not Modifier 2)
    re_ = re.compile('^' + re.escape(TYPE_NAMES[type_]) + ' ([0-9]+)$')
    mx = 0
    for it in P.items.values():
        if it.type == type_:
            m = re_.match(it.name)
            if m:
                mx = max(mx, int(m.group(1)))
    P.counters[type_] = mx + 1
    return P.counters[type_]


def addItem(P, type_, parent, props=None):
    # port of addItem (hair-modeler.clean.html:735). The name is numbered even when `props` overrides it: nextNumber
    # runs (and bumps counters) before the props are applied, like the original's Object.assign.
    iid = P.uid; P.uid += 1
    name = f'{TYPE_NAMES[type_]} {nextNumber(P, type_)}'
    it = _CLASSES.get(type_, M.Item)(id=iid, type=type_, name=name, parent=parent if parent is not None else None, visible=True)
    for k, v in (props or {}).items():
        if k == 'obj':
            continue                                                # the original's display object: nothing to keep
        if not hasattr(it, k):
            raise AttributeError(f'{type_} items have no field {k!r}')
        setattr(it, k, v)
    P.items[it.id] = it
    P.childrenOf(it.parent).append(it.id)
    P.gen += 1                                                      # structural change: memos keyed on gen (phase 2.5)
    return it


def makeCurve(P, parent):
    # port of makeCurve (hair-modeler.clean.html:1025), minus the line object
    return addItem(P, 'curve', parent, {'anchors': [], 'ovr': {}, 'samples': [], 'dirty': True})


def newAnchor(P, pos):
    # port of class Anchor (hair-modeler.clean.html:712): a fresh id, a copy of the position, smooth 1
    a = M.Anchor(id=P.uid, pos=pos.clone(), smooth=1); P.uid += 1
    P.anchors[a.id] = a
    return a


def isVisible(P, it):
    # port of isVisible (hair-modeler.clean.html:742) without isoKept: isolation is view state the port leaves to Blender
    while it is not None:
        if not it.visible:
            return False
        it = P.parentOf(it)
    return True


def tintOf(P, it):
    # port of tintOf (751): the colour of the TOPMOST coloured group above it (a parent's colour covers its sub-groups'),
    # as the css string the file stores ('#3fbf7a'), or None
    c = None
    p = P.parentOf(it)
    while p is not None:
        if getattr(p, 'color', None):
            c = p.color
        p = P.parentOf(p)
    return c


def set_head(P, name, src):
    """port of makeHeadItem + setHead (hair-modeler.clean.html:3210-3227), the data part: the head gets the next id,
    `src` is the file itself ({ ext, text } or { ext, b64 }) and the scene scale comes from its bounding box
    (fitToBox, 4184), which also makes every curve and surface dirty."""
    geo = head_geometry(M.Head(id=0, name=name, src=src))
    if geo is None or not geo[0]:
        raise ValueError('No geometry in the file')                 # setHead: toast('No geometry in the file')
    head = M.Head(id=P.uid, name=name, visible=True, showWire=False, src=src); P.uid += 1
    P.head = head
    P.S = fit_to_box(geo[0])
    for c in P.curves():
        c.dirty = True
    for s in P.surfaces():
        s.dirty = True
    P.gen += 1
    return head


# ======================= Deleting (760) =======================

def deleteItem(P, iid):
    """port of deleteItem (hair-modeler.clean.html:760), the data part: an item and what depends on it (a group's
    children, a curve's walls / tubes / crowns / guide hits, a tube's extras, a guide's rings), out of its parent's
    list, the roots and the ticked groups. Selection state (S.sel, S.asel) lives on the Blender side: callers drop
    the ids themselves. Display objects do not exist here."""
    it = P.items.get(iid)
    if it is None:
        return
    if it.type == 'group':
        for cid in list(it.children):
            deleteItem(P, cid)
    if it.type == 'curve':
        for s in P.surfaces():
            if s.a == iid or s.b == iid:
                deleteItem(P, s.id)
        for t in P.tubes():
            if t.src == iid:
                deleteItem(P, t.id)
        for k in P.crowns():
            if k.part == iid or k.low == iid:
                deleteItem(P, k.id)
    if it.type == 'tube':
        for x in P.of_type('xmod'):
            if x.tube == iid:
                deleteItem(P, x.id)
    if it.type == 'xmod':
        t = P.items.get(it.tube if it.tube is not None else it.mesh)
        if t is not None:
            t.dirty = True
    if it.type == 'imesh':
        for x in P.of_type('xmod'):
            if x.mesh == iid:
                deleteItem(P, x.id)
    if it.type == 'curve':
        for g in P.guides():
            if g.kind != 'disc' and any(h.get('c') == iid for h in g.hits):
                g.hits = [h for h in g.hits if h.get('c') != iid]; g.dirty = True
                if len(g.hits) < 2:
                    deleteItem(P, g.id)
    if it.type == 'guide' and it.rings:
        rs = it.rings; it.rings = []
        for r in rs:
            deleteItem(P, r)
    arr = P.childrenOf(it.parent)
    if iid in arr:
        arr.remove(iid)                                             # indexOf + splice: the first occurrence
    del P.items[iid]
    if iid in P.settings.convChecked:
        P.settings.convChecked.remove(iid)
    if it.type == 'curve':
        for c in P.curves():
            c.dirty = True
    if P.conv:                                                      # 787: a deleted mesh leaves the pending conversion
        if it.type == 'mesh' and iid in P.conv['entries']:
            # ours (3.7, conversion.py): a confirmed mesh no pending mesh replaces any more is kept, and shown again
            # (3.8b: by the same rule as startConversion — a sub-group's mesh the deleted one covered comes back too)
            from .conversion import replacedBy
            rest = [e for e in P.conv['entries'] if e in P.items]
            for oid in list(P.conv.get('replaced') or ()):
                o = P.items.get(oid)
                if o is not None and not replacedBy(P, o, rest):
                    P.conv['replaced'].remove(oid)
                    if oid in (P.conv.get('shown') or ()):
                        # (3.8b, found by the stress test: it comes back in the view the deleted one was in — hidden
                        # in Live, where the walls show, shown in Baked)
                        P.conv['shown'].remove(oid); o.visible = it.visible
        P.conv['entries'] = [e for e in P.conv['entries'] if e in P.items]
        if not P.conv['entries']:
            for oid in P.conv.get('shown') or ():
                if oid in P.items:
                    P.items[oid].visible = True
            P.conv = None
    P.gen += 1; P.rev += 1


def touchGroup(P, gid):
    """What selecting a group does to the model in the original: its properties panel shows the group's loop step
    (8025: groupConv(it).step), and groupConv fills the group's missing conv defaults from S. The Blender side calls
    it when a group becomes the target of a new curve (the active collection); the parity scripts do the same."""
    g = P.items.get(gid) if gid is not None else None
    if g is not None and g.type == 'group':
        groupConv(P, g)


# ======================= Drawing curves (4645-4665) =======================

@dataclass(eq=False)
class DrawState:
    """S.drawing: the curve being drawn, None between drawings."""
    curve: M.Curve | None = None


def head_trimesh(P):
    """The head as the three.js mesh the original raycasts (headFromOBJ, 3184): Float32 positions, faces fanned
    into triangles, front faces only (MeshStandardMaterial's default side). Memoised per head on the project."""
    key = (id(P.head), P.head.id if P.head else None)
    memo = P._memo.get('head_trimesh')
    if memo is not None and memo[0] == key:
        return memo[1]
    mesh = None
    geo = head_geometry(P.head)
    if geo:
        v, faces = geo
        pos = [V3(float32(v[i]), float32(v[i + 1]), float32(v[i + 2])) for i in range(0, len(v), 3)]
        idx = []
        for f in faces:
            for k in range(1, len(f) - 1):
                idx.extend((f[0], f[k], f[k + 1]))
        mesh = TriMesh(pos, idx, FrontSide)
    P._memo['head_trimesh'] = (key, mesh)
    return mesh


_raycaster = Raycaster()                                            # the module-level `raycaster` of the original (5172)


def pickHead(P, ray):
    """port of pickObjects(x, y, S.head.meshes) (5181): the nearest hit on the head's front faces, or None."""
    mesh = head_trimesh(P)
    if mesh is None:
        return None
    _raycaster.set(ray.origin, ray.direction)
    hits = _raycaster.intersectObject(mesh)
    return hits[0] if hits else None


def placePoint(P, ray, cam_dir, pivot, drawing=None, head_visible=True):
    """port of placePoint (4647). `ray` is the cursor's ray (rayAt, 5176) in the model's space, `cam_dir` the camera's
    forward direction (camDir, 4246), `pivot` the orbit pivot. On the head: the hit lifted 1.2 % of the model size
    along the face normal. Off it (or without a head): the plane facing the camera through the last anchor of the
    curve being drawn, or through the pivot."""
    p = headPoint(P, ray) if P.head is not None and P.head.visible and head_visible else None
    if p is not None:
        return p
    c = drawing.curve if drawing is not None else None
    ref = c.anchors[len(c.anchors) - 1].pos if c is not None and c.anchors else pivot
    return ray.intersectPlane(Plane().setFromNormalAndCoplanarPoint(cam_dir, ref), V3())


def headPoint(P, ray):
    """The first half of placePoint (4741): where the ray hits the head, lifted 1.2 % of the model size along the face
    normal; None when it misses. Shared with the drag on the head (dragOnHead)."""
    hit = pickHead(P, ray)
    if hit is None:
        return None
    n = hit['normal'].clone().normalize()                          # face.normal.transformDirection(identity) re-normalises
    return hit['point'].clone().addScaledVector(n, P.S.scale * 0.012)


def addDrawPoint(P, drawing, ray, cam_dir, pivot, parent=None, head_visible=True):
    """port of addDrawPoint (4653): a click of the Draw tool. The first click of a curve makes it (makeCurve into
    `parent`, the original's targetGroup(); the Blender side passes the active collection's group). Returns the
    new anchor, or None when the ray hits nothing (parallel to the fallback plane)."""
    p = placePoint(P, ray, cam_dir, pivot, drawing, head_visible)
    if p is None:
        return None
    return addDrawPointAt(P, drawing, p, parent)


def addDrawPointAt(P, drawing, p, parent=None):
    """The second half of addDrawPoint: the point is known (tests, the non-modal operator). Returns the anchor."""
    c = drawing.curve
    if c is None:
        c = makeCurve(P, parent); drawing.curve = c
    a = newAnchor(P, p)
    c.anchors.append(a); c.dirty = True
    P.rev += 1
    return a


def finishDrawing(P, drawing):
    """port of finishDrawing (4659): the drawing ends; a curve with fewer than 2 points is deleted ("A curve needs at
    least 2 points"). Returns the curve kept, or None."""
    c = drawing.curve; drawing.curve = None
    if c is None:
        return None
    if len(c.anchors) < 2:
        deleteItem(P, c.id)
        return None
    return c


# ======================= Anchors: the Select tool (phase 3.3) =======================
# The scene helpers the editing functions use (1025, 1126, 4760), the list of anchors the viewport shows and picks
# (rebuildAnchors, 1363), and the data part of what the pointer handlers and the panels do to anchors: move (5912),
# slide (snapDragToCurve is in blender/pick.py: it needs the screen), smoothness (5924, 8634), insert (5784, 4819),
# delete (4784), recalculate (4803), reverse (7947), the curve sliders (8700-8720). Selection state (S.sel, S.asel)
# is in selection.py; toasts, undo steps (commit) and redraws belong to the caller.

def hairCurves(P):
    # port of hairCurves (hair-modeler.clean.html:1025): the curves that are not the ring of a NURBS disc
    return [c for c in P.curves() if not c.ring]


def isRingAnchor(P, a):
    # port of isRingAnchor (hair-modeler.clean.html:1126)
    return any(c.ring and a in c.anchors for c in P.curves())


def curvesWith(P, a):
    # port of curvesWith (hair-modeler.clean.html:4760): the curves using anchor a, in scene order
    return [c for c in P.curves() if a in c.anchors]


def anchorList(P, sel_items=(), sel_anchors=(), shown=None):
    """port of the list part of rebuildAnchors (hair-modeler.clean.html:1374-1385): S.anchorList, the anchors the
    viewport shows and picks: every anchor of a visible curve once, in curves() order; the ring points of a NURBS
    disc only while the disc, or one of its points, is selected. `shown` (3.6): the ids of the hair curves their
    group's mode shows (groups.shownCurves): a curve left out takes its anchors with it (1381; the attached anchors
    of a hidden curve that stay for the crown connectors are phase 4)."""
    asel = set(sel_anchors); sel = set(sel_items)
    seen = set(); out = []
    for c in P.curves():
        if not isVisible(P, c):
            continue
        if c.ring and not (c.ring in sel or any(a.id in asel for a in c.anchors)):
            continue
        if shown is not None and not c.ring and c.id not in shown:
            continue
        for a in c.anchors:
            if a.id not in seen:
                seen.add(a.id); out.append(a)
    return out


def planeHit(ray, point, normal):
    # port of planeHit (hair-modeler.clean.html:5655): where the cursor's ray meets the plane through `point` with
    # `normal` (the drag plane faces the camera), or None
    return ray.intersectPlane(Plane().setFromNormalAndCoplanarPoint(normal, point), V3())


# ---- dragging anchors (5756, 5912-5919) ----

@dataclass(eq=False)
class AnchorDrag:
    """the original's drag = { type: 'anchor', ... } (startAnchorDrag, 5756): the anchors moved together, where each
    started, the drag plane (normal n through ref, facing the camera) and the cursor's first hit on it; `snap` is the
    id of the curve the single anchor slides along while Shift is held (drag.snap), `inserted` marks the anchor the
    right button just made (the release commits even without a move)."""
    list: list
    starts: list
    n: Vec3
    ref: Vec3
    startHit: Vec3 | None
    moved: bool = False
    follow: list | None = None                                      # ours (lot 4.7b item 6): with Alt down, each anchor's distance from the head (surfaceDists)
    snap: int | None = None
    inserted: bool = False
    lead: int = 0                                                   # the anchor under the cursor (the clicked one): the one dragOnHead puts on the head
    on_head: bool = False                                           # the last move put the anchors on the head (dragOnHead), not on the plane


def startAnchorDrag(anchors, ray, cam_dir, ref):
    # port of startAnchorDrag (hair-modeler.clean.html:5756): `ray` is the cursor's ray (rayAt), `cam_dir` camDir();
    # `ref` is the clicked anchor's position (a.pos itself, or a copy of it): that anchor leads a drag on the head
    n = cam_dir.clone()
    anchors = list(anchors)
    lead = next((i for i, a in enumerate(anchors) if a.pos is ref), None)
    if lead is None:
        lead = next((i for i, a in enumerate(anchors) if a.pos.equals(ref)), 0)
    return AnchorDrag(list=anchors, starts=[a.pos.clone() for a in anchors], n=n, ref=ref.clone(), startHit=planeHit(ray, ref, n), lead=lead)


def dragAnchors(P, drag, ray):
    # the free move of the 'anchor' case of pointermove (hair-modeler.clean.html:5917-5919): the anchors follow the
    # cursor on the drag plane. False when the ray misses the plane (nothing moves).
    h = planeHit(ray, drag.ref, drag.n)
    if h is None or drag.startHit is None:
        return False
    d = h.sub(drag.startHit)
    for a, s in zip(drag.list, drag.starts):
        a.pos.copy(s).add(d)
    drag.moved = True; markAnchorsDirty(P, drag.list); P.rev += 1
    return True


def dragSnap(P, drag, curve, point):
    # Shift held with one anchor and snapDragToCurve found `point` on `curve` (hair-modeler.clean.html:5735-5737, 5913)
    drag.snap = curve.id
    drag.list[0].pos.copy(point)
    drag.moved = True; markAnchorsDirty(P, drag.list); P.rev += 1


def dragSnapRelease(drag, ray):
    # Shift released after a slide (hair-modeler.clean.html:5914-5916): free again from here, no jump
    drag.snap = None
    drag.starts = [a.pos.clone() for a in drag.list]; drag.ref = drag.list[0].pos.clone(); drag.startHit = planeHit(ray, drag.ref, drag.n)


def dragOnHead(P, drag, ray):
    """An addition of this port (the "Drag on Head" option, docs/features-adicionadas.md; the original only drags on
    the plane): the lead anchor goes to the head surface under the cursor, lifted as placePoint lifts a drawn
    point (headPoint), and the other anchors of the drag keep their offsets to it (the same delta, as dragAnchors).
    False when the ray misses the head: nothing moves, the caller falls back to the plane (dragRebase first, so
    there is no jump when the anchors had been on the head)."""
    p = headPoint(P, ray)
    if p is None:
        return False
    d = p.sub(drag.starts[drag.lead])
    for a, s in zip(drag.list, drag.starts):
        a.pos.copy(s).add(d)
    drag.moved = True; drag.on_head = True; markAnchorsDirty(P, drag.list); P.rev += 1
    return True


def dragRebase(drag, ray):
    """The plane drag restarts from where the anchors are now (as dragSnapRelease does after a slide): the plane
    through the lead anchor, facing the camera, and the cursor's hit on it as the new origin."""
    drag.on_head = False
    drag.starts = [a.pos.clone() for a in drag.list]; drag.ref = drag.list[drag.lead].pos.clone(); drag.startHit = planeHit(ray, drag.ref, drag.n)


def surfaceDists(anchors, nearest):
    """ours (lot 4.7b item 6, the owner's request: Alt in a drag): each anchor's distance from the surface now —
    nearest(p) -> (the closest point of the surface, its face's normal) or None; None where there is none"""
    out = []
    for a in anchors:
        h = nearest(a.pos)
        if h is None:
            out.append(None); continue
        q, n = h
        d = q.distanceTo(a.pos)
        out.append(-d if a.pos.clone().sub(q).dot(n) < 0 else d)   # (signed: an anchor under the surface stays under it)
    return out


def followSurface(P, anchors, dists, nearest):
    """ours (lot 4.7b item 6): every anchor back at its own distance from the surface (surfaceDists), over the
    surface's closest point, on its own side — along the way it went, or along the face's normal when it crossed: the
    anchors slide over the head as a shell, each keeping the distance (and the side) it had when Alt went down"""
    for a, d0 in zip(anchors, dists):
        if d0 is None:
            continue
        h = nearest(a.pos)
        if h is None:
            continue
        q, n = h
        side = 1 if d0 >= 0 else -1
        v = a.pos.clone().sub(q); L = v.length()
        u = v.multiplyScalar(1 / L) if L > 1e-12 and v.dot(n) * side >= 0 else n.clone().normalize().multiplyScalar(side)
        a.pos.copy(q).addScaledVector(u, abs(d0))
    markAnchorsDirty(P, anchors); P.rev += 1


def moveAnchors(P, anchors, d):
    """A drag by `d` in one go (the non-modal form, scripts): every anchor from where it is, as dragAnchors."""
    for a in anchors:
        a.pos.add(d)
    markAnchorsDirty(P, anchors); P.rev += 1


# ---- smoothness (5803, 5924-5928, 8634) ----

def smoothDrag(P, anchors, starts, dx):
    # the 'smooth' case of pointermove (hair-modeler.clean.html:5924-5927): B + a horizontal drag, 200 px = 100 %
    v = dx / 200
    for a, s in zip(anchors, starts):
        a.smooth = clamp(s + v, 0, 1)
    markAnchorsDirty(P, anchors); P.rev += 1
    return anchors[len(anchors) - 1].smooth if anchors else None   # the HUD shows the last one


def setSmooth(P, anchors, v):
    # the Smoothness slider of the anchor panel (hair-modeler.clean.html:8634): every selected anchor
    for a in anchors:
        a.smooth = v
    markAnchorsDirty(P, anchors); P.rev += 1


def setCurveProp(P, cs, key, v):
    # the Roundness / Smoothing sliders of the curve panel (hair-modeler.clean.html:8700-8711): every selected curve;
    # Smoothing also drops the fairSpline cache (c._fair = null)
    for c in cs:
        setattr(c, key, v)
        if key == 'fair':
            c.fair_cache = None
    markAnchorsDirty(P, [a for c in cs for a in c.anchors]); P.rev += 1


# ---- inserting (4819, 5784-5787) ----

def insertOnSegment(P, c, seg, a):
    # port of insertOnSegment (hair-modeler.clean.html:4819). A new anchor on segment seg of curve c. When other
    # curves are welded along that very segment (both of its anchors are shared and neighbours there too, a Y-trunk),
    # the anchor goes into all of them, so they stay joined.
    p = at(c.anchors, seg); q = at(c.anchors, seg + 1)
    for h in P.curves():
        A = h.anchors; i = index_of(A, p); j = index_of(A, q)
        if i < 0 or j < 0 or abs(i - j) != 1:
            continue
        A.insert(max(i, j), a)
    P.gen += 1; P.rev += 1


def insertAnchorAt(P, c, seg, point):
    # the right button on a curve (hair-modeler.clean.html:5784-5787): a new anchor at the picked point of the segment
    a = newAnchor(P, point)
    insertOnSegment(P, c, seg, a); markAnchorsDirty(P, [a])
    return a


def curvesOnSegment(P, c, seg):
    # port of curvesOnSegment (hair-modeler.clean.html:5845): ids of the curve and of every curve sharing its segment
    # `seg` (both anchors shared and neighbours there)
    p = at(c.anchors, seg); q = at(c.anchors, seg + 1); out = [c.id]
    if p is None or q is None:
        return out
    for h in hairCurves(P):
        if h is c or not isVisible(P, h):
            continue
        i = index_of(h.anchors, p); j = index_of(h.anchors, q)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            out.append(h.id)
    return out


# ---- deleting (4761-4802) ----

def removeAnchorFromCurve(P, c, a, keep):
    # port of removeAnchorFromCurve (hair-modeler.clean.html:4761). `keep`: the shape stays, the neighbours' handles
    # are frozen (c.ovr) to the cubic that best fits the two segments the anchor was in
    i = index_of(c.anchors, a)
    if i < 0:
        return
    n = len(c.anchors)
    if keep and n > 2:
        segs = curveSegs(P, c); Hs = [curveHandles(P, c, k) for k in range(n)]

        def setO(an, side, v):
            o = c.ovr.get(an.id)
            if o is None:
                o = M.HandleOverride()
            setattr(o, 'in_' if side == 'in' else 'out', v)
            c.ovr[an.id] = o

        def freeze(k, side):
            an = c.anchors[k]; sm = js_max(an.smooth, 0.05)
            setO(an, side, (Hs[k]['hin'] if side == 'in' else Hs[k]['hout']).clone().sub(an.pos).divideScalar(sm))
        if 0 < i < n - 1:
            if i - 1 > 0:
                freeze(i - 1, 'in')
            if i + 1 < n - 1:
                freeze(i + 1, 'out')
            pts = []
            for s in (segs[i - 1], segs[i]):
                for k in range(25):
                    pts.append(bez(*s, k / 24))
            A0 = c.anchors[i - 1]; A3 = c.anchors[i + 1]
            d1 = segs[i - 1][1].clone().sub(A0.pos)
            if d1.lengthSq() < 1e-14:
                d1 = pts[1].clone().sub(A0.pos)
            d1.normalize()
            d2 = segs[i][2].clone().sub(A3.pos)
            if d2.lengthSq() < 1e-14:
                d2 = pts[len(pts) - 2].clone().sub(A3.pos)
            d2.normalize()
            f = fitHandles(pts, A0.pos, A3.pos, d1, d2); la = f['a']; lb = f['b']
            setO(A0, 'out', d1.multiplyScalar(la / js_max(A0.smooth, 0.05)))
            setO(A3, 'in', d2.multiplyScalar(lb / js_max(A3.smooth, 0.05)))
        elif i == 0:
            freeze(1, 'out')
        else:
            freeze(n - 2, 'in')
    del c.anchors[i]; c.ovr.pop(a.id, None)


def deleteAnchors(P, lst, keep, drawing=None):
    """port of deleteAnchors (hair-modeler.clean.html:4784), the data part. Returns the ids of the curves that lost
    anchors and still exist (the original's new selection, S.sel), or None when every anchor given was a disc point
    ("Disc points can't be deleted: use Reset deformations" is the caller's toast). `drawing` is the curve being
    drawn (S.drawing), which may stay with one anchor."""
    lst = [a for a in lst if not isRingAnchor(P, a)]                # a disc always keeps its 6 + 6 points
    if not lst:
        return None
    touched = {}
    for a in lst:
        for c in curvesWith(P, a):
            removeAnchorFromCurve(P, c, a, keep); touched[c.id] = True
    for c in P.curves():
        if len(c.anchors) < 2 and c is not drawing:
            deleteItem(P, c.id)
    # a Y branch whose own anchors are all gone runs only along its host's trunk (every segment shared with another
    # curve): it is no curve of its own any more, deleted, the host stays a single curve

    def segOn(x, y, c):
        return any(h is not c and not h.ring and any((v is x and at(h.anchors, k + 1) is y) or (v is y and at(h.anchors, k + 1) is x) for k, v in enumerate(h.anchors))
                   for h in P.curves())
    for iid in list(touched):
        c = P.items.get(iid)
        if c is None or c.ring or len(c.anchors) < 2:
            continue
        if all(k == 0 or segOn(c.anchors[k - 1], x, c) for k, x in enumerate(c.anchors)):
            deleteItem(P, c.id)
    for c in P.curves():
        c.dirty = True
    P.gen += 1; P.rev += 1
    return [iid for iid in touched if iid in P.items]


# ---- recalculate (4803-4810) ----

def recalcHandles(P, lst):
    # port of recalcHandles (hair-modeler.clean.html:4803), the data part: frozen handles (left by "delete, keep
    # shape") and smoothness tweaks dropped, back to clean automatic arcs ("Handles recalculated" is the caller's)
    ids = {a.id for a in lst}
    for c in P.curves():
        for iid in list(c.ovr.keys()):
            if iid in ids:
                del c.ovr[iid]
    for a in lst:
        a.smooth = 1
    markAnchorsDirty(P, lst); P.rev += 1


def recalcCurves(P, cs):
    # port of recalcCurves (hair-modeler.clean.html:4810): every anchor of the curves, once, in order
    recalcHandles(P, list(dict.fromkeys(a for c in cs for a in c.anchors)))


# ---- reverse (7947) ----

def reverseCurves(P, cs):
    """port of reverseCurves (hair-modeler.clean.html:7947), the data part. Reverse direction: the anchors go the
    other way (the root becomes the tip). Everything tied to a place on the curve stays where it is: frozen handles
    swap in / out, guide line hits and attachments to a connector get 1 - t, painted weights of the curve's modifiers
    (mask / thickness) are flipped; modifiers then sweep from the new root. Returns the curves reversed."""
    cs = [c for c in cs if c is not None and c.type == 'curve' and not c.ring]
    if not cs:
        return []
    ids = {c.id for c in cs}
    for c in cs:
        c.anchors.reverse()
        for k, o in list(c.ovr.items()):
            c.ovr[k] = M.HandleOverride(in_=o.out or None, out=o.in_ or None)
        c.dirty = True
    tubes = set()
    for it in P.items.values():
        if it.type == 'guide':
            if it.kind == 'disc':
                it.dirty = True
            else:
                for h in it.hits:
                    if h.get('c') in ids:
                        h['t'] = 1 - h['t']
                it.dirty = True
        if it.type == 'tube' and it.src in ids:
            tubes.add(it.id); it.dirty = True
    for it in P.items.values():
        if it.type == 'xmod' and it.mask and it.tube in tubes:
            it.mask.reverse()
    for c in P.curves():
        for a in c.anchors:
            if a.att and a.att.c in ids:
                a.att.t = 1 - a.att.t
    markAnchorsDirty(P, [a for c in cs for a in c.anchors])
    P.gen += 1; P.rev += 1
    return cs


# ======================= The anchor gizmo (4404-4708; 3.8b, D11 (a)) =======================
# The original's gizmo is three TransformControls on one proxy; what they do to the model is here: where the drag
# starts (gizmoTargets 4567: the selected anchors, the pivot at the LAST one; localFrame 4546), the one matrix of the
# whole drag (gizmoMatrix 4592) and what every move does (the objectChange handler 4660: positions from the start,
# frozen handles by the increment, the Shift slide). The proxy's state (position, quaternion, scale) is the caller's:
# the Blender gizmo (blender/ops_gizmo.py) or the parity scripts set it, as TransformControls does in the original.

GIZMO_MODES = ('translate', 'rotate', 'scale')
# ours (3.8c): Blender's axes as unit vectors of the model's frame ((x, y, z)_three -> (x, -z, y)_blender): a handle or a
# G / R / S constraint named X / Y / Z turns about / moves along these (Y is the model's -z), and scales the model axis
AXIS_LOCAL = {'X': V3(1, 0, 0), 'Y': V3(0, 0, -1), 'Z': V3(0, 1, 0)}
AXIS_SCALE = {'X': 'x', 'Y': 'z', 'Z': 'y'}


def ownQ(it):
    # port of ownQ (hair-modeler.clean.html:4532) for the items an anchor's frame reads (curves, discs, groups): it.q,
    # its rotation relative to its parent group. (The imported-mesh branches read a Transform modifier: phase 5.)
    return Quat(*it.q) if it is not None and it.q else Quat()


def worldQ(P, it):
    # port of worldQ (4541): its own rotation turned by every group above it (nothing: the identity)
    if it is None:
        return Quat()
    q = ownQ(it)
    p = P.parentOf(it)
    while p is not None:
        q.premultiply(ownQ(p))
        p = P.parentOf(p)
    return q


def localFrame(P, anchors, sel_items=()):
    # port of localFrame (4546): the axes of the (first non-ring) curve of the last selected anchor; without anchors
    # (3.8d) the world rotation of the last selected item (4548)
    if not anchors:
        return worldQ(P, P.items.get(sel_items[len(sel_items) - 1])) if sel_items else Quat()
    cs = curvesWith(P, anchors[len(anchors) - 1])
    c = next((k for k in cs if not k.ring), None) or (cs[0] if cs else None)
    if c is None:
        return Quat()
    return worldQ(P, P.items.get(c.ring) if c.ring else c)


@dataclass(eq=False)
class GizmoDrag:
    """tcState (4638), the anchor case: the mode, the proxy where the drag started (pos = the pivot, quat = the axes),
    the anchors and where each started, the last matrix applied (the frozen handles turn by the increment), and the
    curve a single anchor slides along while Shift is held (st.snap)."""
    mode: str
    pos: Vec3
    quat: Quat
    list: list
    starts: list
    lastM: Mat4
    snap: int | None = None
    moved: bool = False
    space: str = 'world'                                            # the axes: 'world' or 'local' (ours, 3.8c: G / R / S turn by them)
    items: list = field(default_factory=list)                       # S.sel when the drag moves items, not anchors (3.8d): rotateOwnAxes on release
    meshes: list = field(default_factory=list)                      # st.meshes (4638): the converted meshes inside the selection, baked on release (4646)


@dataclass(eq=False)
class GizmoTargets:
    """what gizmoTargets (4563) returns: the anchors to move, the converted meshes (`mesh` items with data) and the
    imported ones (imesh) inside the selection, the pivot, and S.sel when the selection is items (empty for anchors)"""
    anchors: list
    meshes: list
    imeshes: list
    pivot: Vec3
    items: list


def gizmoTargets(P, sel_items, anchors=()):
    """port of gizmoTargets (hair-modeler.clean.html:4563; the reference-image branches are not the hair's): the
    selected anchors with the pivot on the last one; else everything inside the selected items — a group its children
    (any depth), a curve its anchors, a wall the anchors of its two curves, a disc guide its rings, a tube its curve, a
    modifier its tube (or its imported mesh), a crown its parting, its lower connector and the curves between them; a
    shared anchor once — with the pivot at the centre of their box (with the meshes' boxes). None: nothing to move."""
    if anchors:
        anchors = list(anchors)
        return GizmoTargets(anchors, [], [], anchors[len(anchors) - 1].pos.clone(), [])
    from .crown import crownCurves
    from .guides import discRings
    got = {}; meshes = {}; ims = {}                                 # (insertion-ordered sets by identity, as the original's Set)

    def put(a):
        got.setdefault(id(a), a)

    def add(iid):
        it = P.items.get(iid)
        if it is None:
            return
        if it.type == 'imesh':                                      # (if (it.obj): an imported mesh is always loaded here)
            ims.setdefault(id(it), it)
            return
        if it.type == 'xmod' and it.mesh is not None:
            add(it.mesh)
            return
        if it.type == 'group':
            for c in list(it.children):
                add(c)
        elif it.type == 'guide' and it.kind == 'disc':
            for c in discRings(P, it):
                for a in c.anchors:
                    put(a)
        elif it.type == 'curve':
            for a in it.anchors:
                put(a)
        elif it.type == 'surface':
            for cid in (it.a, it.b):
                c = P.items.get(cid)
                if c is not None:
                    for a in c.anchors:
                        put(a)
        elif it.type == 'tube':
            c = P.items.get(it.src)
            if c is not None:
                for a in c.anchors:
                    put(a)
        elif it.type == 'xmod':
            add(it.tube)
        elif it.type == 'crown':
            for c in [it.part, it.low, *[r['c'].id for r in crownCurves(P, it)]]:
                add(c)
        elif it.type == 'mesh' and it.data:
            meshes.setdefault(id(it), it)

    for iid in list(sel_items):
        add(iid)
    if not got and not meshes and not ims:
        return None
    lo = V3(math.inf, math.inf, math.inf); hi = V3(-math.inf, -math.inf, -math.inf)   # THREE.Box3 (empty)
    for a in got.values():                                          # box.expandByPoint: min.min(p), max.max(p)
        p = a.pos
        lo.set(js_min(lo.x, p.x), js_min(lo.y, p.y), js_min(lo.z, p.z)); hi.set(js_max(hi.x, p.x), js_max(hi.y, p.y), js_max(hi.z, p.z))
    for m in meshes.values():                                       # box.union(m.bbox) (4586): min.min(b.min), max.max(b.max)
        _, bl, bh = meshBox(m)
        lo.set(js_min(lo.x, bl.x), js_min(lo.y, bl.y), js_min(lo.z, bl.z)); hi.set(js_max(hi.x, bh.x), js_max(hi.y, bh.y), js_max(hi.z, bh.z))
    # (imeshes: their boxes join the pivot in the original (4587); moving them is phase 5, and the callers refuse a
    # drag that holds any — see gizmoBlocked)
    piv = V3() if lo.x > hi.x or lo.y > hi.y or lo.z > hi.z else V3().addVectors(lo, hi).multiplyScalar(0.5)   # box.getCenter
    return GizmoTargets(list(got.values()), list(meshes.values()), list(ims.values()), piv, list(sel_items))


def frameBox(P, sel_items, anchors=()):
    """port of frameAll (hair-modeler.clean.html:4390), F: the box framed — the selected anchors; else what the
    gizmo would move in the selected items (their anchors and the converted meshes' boxes); else the head; else every
    curve's anchors — as (centre, r): the camera goes back r / tan(fov / 2) from the centre (4400). None: an empty box.
    (The imported meshes' boxes of 4394 are phase 5.)"""
    lo = V3(math.inf, math.inf, math.inf); hi = V3(-math.inf, -math.inf, -math.inf)

    def put(p):
        lo.set(js_min(lo.x, p.x), js_min(lo.y, p.y), js_min(lo.z, p.z)); hi.set(js_max(hi.x, p.x), js_max(hi.y, p.y), js_max(hi.z, p.z))
    tg = gizmoTargets(P, sel_items) if not anchors and sel_items else None
    if anchors:
        for a in anchors:
            put(a.pos)
    elif tg is not None and (tg.anchors or tg.meshes):
        for a in tg.anchors:
            put(a.pos)
        for m in tg.meshes:                                         # box.union(new Box3().setFromPoints(m.data.pos)) (4394): not the cached m.bbox
            for p in m.data.pos:
                put(p)
    elif head_trimesh(P) is not None:
        for p in head_trimesh(P).pos:
            put(p)
    else:
        for c in P.curves():
            for a in c.anchors:
                put(a.pos)
    if lo.x > hi.x or lo.y > hi.y or lo.z > hi.z:
        return None
    c = V3().addVectors(lo, hi).multiplyScalar(0.5)
    size = V3().subVectors(hi, lo)
    r = js_max(size.length() * 0.6, P.S.scale * (0.02 if anchors or tg is not None else 0.1))
    return c, r


def meshBox(m):
    """m.bbox of the original (4586): Box3.setFromPoints(m.data.pos), made the first time the gizmo reads it and kept
    until the bake clears it (4649) — as the original, also when regenConversion gives a pending mesh new data (its
    buildConvMesh leaves m.bbox: the pivot reads the old box until the item is made anew, by an undo / a load: here
    the session's parse). Returns (data the box was made of, min, max)."""
    if m.bbox is None:
        lo = V3(math.inf, math.inf, math.inf); hi = V3(-math.inf, -math.inf, -math.inf)
        for p in m.data.pos:
            lo.set(js_min(lo.x, p.x), js_min(lo.y, p.y), js_min(lo.z, p.z)); hi.set(js_max(hi.x, p.x), js_max(hi.y, p.y), js_max(hi.z, p.z))
        m.bbox = (m.data, lo, hi)
    return m.bbox


def gizmoBlocked(tg):
    """ours (3.8d): why this port cannot run the gizmo / G / R / S on these targets, or None. The converted meshes
    move with the selection since 4.0 (bakeMeshes, D4.11); the imported ones (their Transform modifier, 4645) come
    with the import of meshes, in phase 5."""
    if tg is None:
        return 'Nothing in the selection moves: a guide line, a modifier without a curve or the head have no anchors'
    if tg.imeshes:
        return ('The selection holds imported meshes, which the original moves with the curves: they come with the import '
                'of meshes (phase 5). Select the curves, the walls or the converted meshes instead (box, or Shift+click)')
    return None


def bakeMeshes(P, st, Mx):
    """The release of a drag that holds converted meshes (hair-modeler.clean.html:4646-4651): the matrix of the whole
    drag baked into each mesh item's data (new positions, the same faces; m.bbox = null): the model stays the truth
    (Convert again builds from the curves, as the original's). Each new data gets a new token (ours: the Blender object
    is refilled from it). Returns the meshes baked."""
    from .conversion import new_token
    for m in st.meshes:
        m.data = _baked(m.data, Mx)
        m.bbox = None
        m.token = new_token()
    if st.meshes:
        P.rev += 1
    return list(st.meshes)


def _baked(data, Mx):
    # { pos: m.data.pos.map(p => p.clone().applyMatrix4(M)), faces: m.data.faces } (4649)
    return M.MeshData(pos=[applyMatrix4(p.clone(), Mx) for p in data.pos], faces=data.faces)


def startGizmo(P, anchors, mode, space='world'):
    """The drag of the anchor gizmo starts (updateGizmo 4706 + dragging-changed 4636): `anchors` = S.asel in pick
    order (the proxy on the last one) or the GizmoTargets of a selection of items (3.8d: the proxy on the centre of
    their box, the local axes the last item's); the axes the world's or the local frame's."""
    if mode not in GIZMO_MODES:
        raise ValueError(f'gizmo mode {mode!r}')
    tg = anchors if isinstance(anchors, GizmoTargets) else gizmoTargets(P, (), anchors)
    q = localFrame(P, tg.anchors if not tg.items else [], tg.items) if space == 'local' else Quat()
    return GizmoDrag(mode=mode, pos=tg.pivot.clone(), quat=q, list=list(tg.anchors),
                     starts=[a.pos.clone() for a in tg.anchors], lastM=Mat4(), space='local' if space == 'local' else 'world',
                     items=list(tg.items), meshes=list(tg.meshes))


def rotateOwnAxes(P, st, quat):
    """port of rotateOwnAxes (hair-modeler.clean.html:4551), called on the release of a Rotate drag (4653) with the
    proxy's last quaternion: the turn D = quat · q0⁻¹ turns the own rotation (it.q) of every top selected item with
    it. Anchors (a component) never turn their curve's axes: nothing when the drag moved anchors."""
    if not st.items:
        return
    from .duplicate import topSelected
    D = quat.clone().multiply(st.quat.clone().invert())
    for iid in topSelected(P, st.items):
        it = P.items.get(iid)
        if it is None or it.type in ('imesh', 'head') or (it.type == 'xmod' and it.mesh is not None):
            continue
        par = P.parentOf(it)
        Pq = worldQ(P, par) if par is not None else Quat()
        own = Pq.clone().invert().multiply(D).multiply(Pq).multiply(ownQ(it)).normalize()
        it.q = own.toArray()


def gizmoMatrix(st, pos, quat, scale, lock=None, ctrl=False, shift=False):
    """port of gizmoMatrix (hair-modeler.clean.html:4592): the whole change of a drag as one matrix about the start
    pivot. `pos` / `quat` / `scale` = the proxy now; `lock` = S.axisLock ({'x': bool, ...}); `ctrl` / `shift` = the
    keys held (scale: Ctrl uniform, Shift 0.1 steps). (The reference-image branches are not the anchors'.)"""
    lk = lock or {}
    q0 = st.quat; q0i = q0.clone().invert()
    d = applyQuaternion(pos.clone().sub(st.pos), q0i)
    for k in 'xyz':
        if lk.get(k):
            setattr(d, k, 0)
    applyQuaternion(d, q0)
    sc = scale.clone()
    for k in 'xyz':
        if lk.get(k) or not math.isfinite(getattr(sc, k)):
            setattr(sc, k, 1)
    if st.mode == 'scale' and ctrl:
        k = 'x'
        for b in 'yz':                                              # ['x', 'y', 'z'].reduce((a, b) => |sc[b] - 1| > |sc[a] - 1| ? b : a)
            if abs(getattr(sc, b) - 1) > abs(getattr(sc, k) - 1):
                k = b
        v = getattr(sc, k); sc.set(v, v, v)
    if st.mode == 'scale' and shift:
        for k in 'xyz':
            setattr(sc, k, js_round(getattr(sc, k) * 10) / 10)
    for k in 'xyz':
        if abs(getattr(sc, k)) < 1e-6:
            setattr(sc, k, 1e-6)
    R0 = Mat4().makeRotationFromQuaternion(q0)
    return (Mat4().makeTranslation(st.pos.x + d.x, st.pos.y + d.y, st.pos.z + d.z)
            .multiply(Mat4().makeRotationFromQuaternion(quat.clone().multiply(q0i)))
            .multiply(R0).multiply(Mat4().makeScale(sc.x, sc.y, sc.z)).multiply(R0.clone().invert())
            .multiply(Mat4().makeTranslation(-st.pos.x, -st.pos.y, -st.pos.z)))


def gizmoStep(P, st, M):
    """One move of the gizmo (the objectChange handler, hair-modeler.clean.html:4668-4673): every anchor = where it
    started × M; the frozen handles (c.ovr) of the moved anchors turn / stretch by the INCREMENT since the last move
    (the 3×3 part of M · lastM⁻¹), as the original applies them move after move."""
    for a, s in zip(st.list, st.starts):
        applyMatrix4(a.pos.copy(s), M)
    det = st.lastM.determinant()
    inc = mat3_from_mat4(M.clone().multiply(st.lastM.clone().invert())) if abs(det) > 1e-12 else None
    st.lastM.copy(M)
    ids = {a.id for a in st.list}
    if inc is not None:
        for c in P.curves():
            for aid, o in c.ovr.items():
                if aid in ids:
                    if o.in_ is not None:
                        applyMatrix3(o.in_, inc)
                    if o.out is not None:
                        applyMatrix3(o.out, inc)
    st.moved = True


def nearestOnCurve3(c, p):
    # port of nearestOnCurve3 (hair-modeler.clean.html:5694): the point of curve c nearest to p in 3D
    best = {'d': math.inf, 'point': None}; ab = V3(); ap = V3()
    S = c.samples
    for i in range(len(S) - 1):
        a = S[i].p; b = S[i + 1].p; ab.subVectors(b, a); ap.subVectors(p, a)
        l2 = ab.lengthSq(); t = clamp(ap.dot(ab) / l2, 0, 1) if l2 > 0 else 0
        q = a.clone().addScaledVector(ab, t); d = q.distanceTo(p)
        if d < best['d']:
            best = {'d': d, 'point': q}
    return best


def gizmoSlide(P, st, level_h):
    """Shift with the Move gizmo on one anchor (4676-4692): it sits on the curve nearest (in 3D) to where the gizmo
    puts it, any group, and slides along it; another curve takes over only when nearer by 10 % + 0.01 · levelH.
    Returns the curve slid along (st.snap), or None. `level_h` = levelH() (the caller's)."""
    a = st.list[0]; best = None; cur = None
    for k in hairCurves(P):
        if a in k.anchors or not isVisible(P, k) or len(k.samples) < 2:
            continue
        r = nearestOnCurve3(k, a.pos); r['c'] = k
        if best is None or r['d'] < best['d']:
            best = r
        if k.id == st.snap:
            cur = r
    if cur is not None and best is not None and cur['d'] <= best['d'] * 1.1 + 0.01 * level_h:
        best = cur
    if best is not None:
        st.snap = best['c'].id
        a.pos.copy(best['point'])
        return best['c']
    return None


def gizmoMoved(P, st):
    """The end of every move (4693): markAnchorsDirty(st.list)"""
    markAnchorsDirty(P, st.list); P.rev += 1


def gizmoRotation(q0, axis, angle, space='world'):
    """The proxy's quaternion after TransformControls turns it by `angle` about the unit `axis` (TransformControls.js,
    pointerMove, rotate): world (and the view ring) = axisAngle · q0; local = q0 · axisAngle, normalised"""
    t = Quat().setFromAxisAngle(axis, angle)
    if space == 'local':
        return q0.clone().multiply(t).normalize()
    return t.multiply(q0).normalize()
