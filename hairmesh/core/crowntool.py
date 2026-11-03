# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Phase 4.3: the Crown tool of the original (hair-modeler.clean.html:7345-7841, 5824-5842, 5988-5994, 6037-6040,
7907, 8960-8968) — the data part, pure and bit for bit with the original (tests/test_crowntool_parity.py, harness
--script ops createCrown, addCrownCurve, crownProp, attach, detachConn, slide, crownSym, crownSymOff, crownFrame).

  Create      crownFit / createCrown: a crown group from the reference (CROWN_TMPL) fitted on the head's top, or with
              its whorl on a clicked point; addCrownCurve: one more crown curve in the widest gap.
  Connectors  updateAttachments (the frame's: attached anchors follow their connector, one moved away comes off with a
              toast), attachAnchor, checkAttach's candidates, connectToConnector / insertIntoConnector (a curve end
              becomes an anchor of the connector), detachFromConnector (Ctrl + click), slideStep (Ctrl + drag).
  Symmetry    crownMakeSymmetric (one half mirrored onto the other across X = S.center.x), crownSymSync (the frame's:
              a moved anchor moves its pair, the middle ones stay on the plane).

The frame of the original runs crownSymSync and updateAttachments every frame (12060-12063); here frameSync runs the
same steps at the end of every operator that changes the model (D4.8: session.save_project) and on every move of a
drag. What needs the viewport (the projector, the picked curve) comes in as arguments; the selection is a
core.selection.Selection (S.sel / S.asel), changed in place. The crown's last synced positions (k._symLast) live on
the crown item (Crown.sym_last), never in the file."""
import re

from . import curves as CM, edit
from .crown import crownCurves, crownStep
from .geom import pointOnCurve, resample
from .js import clamp, js_hypot, js_max, js_or
from .model import Attachment
from .raycast import Box3, Raycaster
from .vec3 import V3
from .weld import nearestT

CROWN_TMPL = {   # from a Maya reference scene of a crown (whorl first; lower connector front-left -> back -> front-right)
    'part': [[-0.292, 152.001, -7.159], [-0.214, 152.122, -5.566], [-0.178, 152.0, -3.971], [-0.125, 151.931, -2.401], [-0.124, 151.477, -0.875]],
    'low': [[-5.735, 149.179, -1.348], [-6.411, 149.301, -4.072], [-6.509, 149.444, -6.919], [-5.284, 149.606, -9.464], [-3.017, 149.751, -11.143], [-0.194, 149.852, -11.672],
            [2.634, 149.894, -11.076], [5.026, 149.877, -9.511], [6.338, 149.779, -6.931], [6.418, 149.634, -4.07], [5.768, 149.475, -1.29]],
    'rad': [
        [[-5.735, 149.179, -1.348], [-3.175, 151.339, -0.846], [-0.124, 151.477, -0.875]],
        [[-6.453, 149.263, -3.354], [-4.716, 151.037, -3.06], [-2.526, 152.181, -2.831], [-0.104, 152.044, -2.731]],
        [[-6.471, 149.467, -7.359], [-4.828, 151.209, -6.546], [-2.642, 152.281, -5.862], [-0.214, 152.122, -5.546]],
        [[-5.019, 149.634, -9.876], [-3.809, 151.168, -8.771], [-2.046, 152.068, -7.713], [-0.219, 152.102, -6.412]],
        [[-0.107, 149.854, -11.674], [-0.096, 151.022, -10.384], [-0.114, 151.859, -8.865], [-0.292, 152.001, -7.159]],
        [[3.096, 149.895, -10.844], [2.21, 151.144, -9.463], [0.999, 152.017, -8.034], [-0.219, 152.102, -6.412]],
        [[6.348, 149.778, -6.906], [4.503, 151.371, -6.274], [2.234, 152.354, -5.786], [-0.214, 152.122, -5.546]],
        [[6.358, 149.591, -3.248], [4.493, 151.161, -2.743], [2.264, 152.222, -2.39], [-0.144, 151.826, -2.094]],
        [[5.768, 149.475, -1.29], [3.2, 151.35, -0.85], [-0.124, 151.477, -0.875]],
    ],
}
CROWN_MADE = 'Crown created — move the parting line, the lower connector and the crown curves; attach lock roots to the lower connector'
CURVE_ADDED = 'Crown curve added'
CAME_OFF = 'Anchor came off the connector (Ctrl + drag slides along it)'
NOTHING_TO_MIRROR = 'The crown has nothing on that side to mirror'
SYM_OFF = 'Crown symmetry off — both halves are free'
CLICK_HEAD = 'Click on the head (or press Create in the panel)'
STAYS = 'A crown curve stays on its connector'


def sym_text(master):
    return 'left (+X) → right (−X)' if master > 0 else 'right (−X) → left (+X)'


def connectors(P):
    # port of connectors (7368)
    return [c for c in P.curves() if c.conn]


def crownOf(P, it):
    """the crown an item belongs to (the crown itself, its connectors, its curves' group), or None"""
    if it is None:
        return None
    if it.type == 'crown':
        return it
    for k in P.crowns():
        if it.id in (k.part, k.low) or (it.type == 'curve' and it.parent == k.parent):
            return k
    return None


def _head_hits(P, o, d):
    mesh = edit.head_trimesh(P)
    if mesh is None:
        return []
    rc = Raycaster()
    rc.set(o, d)
    # (ours, 4.3: only the triangles whose box meets the box of the half-line o + t d, t >= 0, are tested —
    # Raycaster.intersectObject's seg_box: a hit lies in both boxes, so the hits and their order are the same; the
    # ~80 rays of a crown made on the example's head: 1.2 s -> a few ms)
    inf = float('inf'); eps = 1e-9 * (1.0 + max(abs(o.x), abs(o.y), abs(o.z)))   # (widened a hair: the rounding of origin + dir * t, as rebuildDisc's box)
    lo = tuple(oc - eps if dc >= 0 else -inf for oc, dc in ((o.x, d.x), (o.y, d.y), (o.z, d.z)))
    hi = tuple(oc + eps if dc <= 0 else inf for oc, dc in ((o.x, d.x), (o.y, d.y), (o.z, d.z)))
    return rc.intersectObject(mesh, seg_box=(lo, hi))


def headHit(P, o, d):
    # port of headHit (7648): the nearest hit of a ray on the head, or None
    hits = _head_hits(P, o, d.clone().normalize())
    return hits[0] if hits else None


def headVisible(P, shown=True):
    """S.head && isVisible(S.head): the head's eye and, in Blender, its object shown (`shown`: pick.head_visible — the
    H of the head object; the callers in the core pass it as placePoint's head_visible does)"""
    return shown and P.head is not None and P.head.visible and edit.head_trimesh(P) is not None


def crownFit(P, target, normal, head_shown=True):
    # port of crownFit (hair-modeler.clean.html:7649): fitting the reference to the head — the top of the head (rays
    # from above), its width at the height of the lower connector (rays from the sides) -> scale; the back of the head
    # there (a ray from behind) -> where the back of the lower connector goes. `target` (a clicked point): the whorl goes
    # there instead. None without a head.
    if not headVisible(P, head_shown):
        return None
    S = P.S
    B = Box3().setFromPoints(edit.head_trimesh(P).pos); big = S.scale * 2; R = S.scale * 0.06   # (Box3.setFromObject: the geometry's box, the identity matrix)
    top = None
    for i in range(-3, 4):
        for j in range(-3, 4):
            h = headHit(P, V3(S.center.x + i * R / 3, B.max.y + big * 0.1, S.center.z + j * R / 3), V3(0, -1, 0))
            if h and (top is None or h['point'].y > top.y):
                top = h['point'].clone()
    if top is None:
        return None
    T = CROWN_TMPL; tw = 13.2; depth = 2.6; back0 = V3(0, 149.6, -11.45)   # the reference: width / depth below the top / back point
    s = 1; cx = top.x; y = top.y
    for _it in range(4):
        y = top.y - depth * s
        l = headHit(P, V3(top.x - big, y, top.z), V3(1, 0, 0)); r = headHit(P, V3(top.x + big, y, top.z), V3(-1, 0, 0))
        if not l or not r:
            break
        w = r['point'].x - l['point'].x
        if not (w > 1e-6):
            break
        s = w / tw; cx = (l['point'].x + r['point'].x) / 2
    bk = headHit(P, V3(cx, y, B.min.z - big), V3(0, 0, 1))
    p0 = V3(*T['part'][0])

    def to(p):
        if target:
            return p.clone().sub(p0).multiplyScalar(s).add(target).addScaledVector(normal or V3(0, 1, 0), 0.25 * s)
        return p.clone().sub(back0).multiplyScalar(s).add(V3(cx, y, bk['point'].z if bk else top.z - 6 * s))
    return {'s': s, 'to': to}


def attachAnchor(P, a, conn):
    # port of attachAnchor (7401)
    if len(conn.samples) < 2:
        CM.rebuildCurve(P, conn)
    a.att = Attachment(c=conn.id, t=nearestT(conn, a.pos)); a.attPos = None


def createCrown(P, sel, target=None, normal=None, sym=True, head_shown=True):
    """port of createCrown (hair-modeler.clean.html:7673): a new crown from the reference, fitted on the head's top
    (Create: `target` None), or with its whorl on the clicked point `target` (the face normal `normal`). `sym`: the
    tool's Symmetric (S.crownSym !== false); `head_shown`: the head object shown in Blender. The crown is the
    selection. Returns (the crown, the toast). Like every function here it reads the curves' samples as the frame
    left them: the caller rebuilds the dirty curves first (curves.rebuild_dirty)."""
    from . import groups as G
    T = CROWN_TMPL; fit = crownFit(P, target, normal, head_shown)
    off = target.clone().sub(V3(*T['part'][0])) if not fit and target else V3()

    def place(p):
        return fit['to'](p) if fit else p.clone().add(off)
    src = (next((x for x in G.convTargets(P) if x.conv is not None and not x.guidesGroup and not x.meshesGroup), None)   # (x.conv: an object, {} too, is true in JS)
           or next((x for x in P.groups() if x.conv is not None and not x.guidesGroup and not x.meshesGroup), None))
    g = edit.addItem(P, 'group', None); g.name = f'Crown {P.counters.get("crown", 0) + 1}'
    conv = dict(src.conv) if src else {}
    conv['step'] = G.projectStep(P)
    g.conv = conv; g.guides = 'auto'
    if g.id not in P.settings.convChecked:
        P.settings.convChecked.append(g.id)
    every = []

    def mk(pts, name):
        c = edit.makeCurve(P, g.id); c.name = name
        c.anchors = [edit.newAnchor(P, place(V3(*p))) for p in pts]
        every.extend(c.anchors)
        return c
    part = mk(T['part'], 'Parting line'); low = mk([], 'Lower connector'); part.conn = 'part'; low.conn = 'low'
    k = edit.addItem(P, 'crown', g.id, {'part': part.id, 'low': low.id, 'p': {'rings': 0, 'visV': 1}, 'dirty': True})
    ch = P.childrenOf(g.id); ch.remove(k.id); ch.insert(0, k.id)
    rads = [mk(r, f'Crown curve {i + 1}') for i, r in enumerate(T['rad'])]
    # the lower connector runs through the lower ends of the crown curves (shared anchors) — no anchors of its own between
    low.anchors = [c.anchors[0] for c in rads]; low.dirty = True
    # anchors that went inside the head come out of it
    if headVisible(P, head_shown):
        box = Box3().setFromPoints([a.pos for a in every]); size = box.getSize(V3()).length(); C0 = box.getCenter(V3()); C0.y -= size * 0.6
        for a in every:
            d = a.pos.clone().sub(C0).normalize()
            hits = _head_hits(P, C0, d)
            h = hits[len(hits) - 1] if hits else None               # the outermost hit
            if h and h['distance'] > a.pos.distanceTo(C0):
                a.pos.copy(h['point']).addScaledVector(d, size * 0.015)
    CM.rebuildCurve(P, part); CM.rebuildCurve(P, low)
    for c in rads:
        attachAnchor(P, c.anchors[len(c.anchors) - 1], part)
    if sym:                                                         # symmetric from the start (the tool's option)
        for c in rads:
            CM.rebuildCurve(P, c)
        crownMakeSymmetric(P, sel, k, 1, True)
    sel.anchors = []; sel.items = [k.id]
    P.gen += 1
    return k, CROWN_MADE


def addCrownCurve(P, sel, k):
    """port of addCrownCurve (hair-modeler.clean.html:7705): one more crown curve in the middle of the widest gap
    between the crown curves (shaped like its neighbours); it is the selection. Returns (the curve, the toast), or
    (None, None) when the crown has fewer than two crown curves."""
    part = P.items.get(k.part); low = P.items.get(k.low); rs = crownCurves(P, k)
    if not part or not low or len(rs) < 2:
        return None, None
    bi = 0
    for i in range(1, len(rs) - 1):
        if rs[i + 1]['u'] - rs[i]['u'] > rs[bi + 1]['u'] - rs[bi]['u']:
            bi = i
    A = rs[bi]; B = rs[bi + 1]

    def pts(r):
        p = [s.p for s in r['c'].samples]
        return resample(p[::-1] if r['rev'] else p, 4)
    pa = pts(A); pb = pts(B); c = edit.makeCurve(P, k.parent); c.name = f'Crown curve {len(rs) + 1}'
    c.anchors = [edit.newAnchor(P, p.clone().lerp(pb[i], 0.5)) for i, p in enumerate(pa)]
    arr = P.childrenOf(c.parent); arr.remove(c.id); arr.insert(_index(arr, B['c'].id), c.id)
    # lower end: a new anchor of the connector between the neighbours' ones (shared), else attached
    ea = A['c'].anchors[len(A['c'].anchors) - 1] if A['rev'] else A['c'].anchors[0]
    eb = B['c'].anchors[len(B['c'].anchors) - 1] if B['rev'] else B['c'].anchors[0]
    ia = _index(low.anchors, ea); ib = _index(low.anchors, eb); um = (A['u'] + B['u']) / 2
    if ia >= 0 and ib >= 0 and abs(ia - ib) == 1:
        n = edit.newAnchor(P, pointOnCurve(low, um) or c.anchors[0].pos); c.anchors[0] = n; low.anchors.insert(max(ia, ib), n); low.dirty = True
    else:
        attachAnchor(P, c.anchors[0], low); c.anchors[0].att.t = um
    attachAnchor(P, c.anchors[3], part); c.anchors[3].att.t = (A['s'] + B['s']) / 2
    c.dirty = True; sel.anchors = []; sel.items = [c.id]
    P.gen += 1
    return c, CURVE_ADDED


def _index(lst, x):
    """Array.prototype.indexOf by identity: -1 when missing (a splice at -1 inserts before the last element)"""
    for i, y in enumerate(lst):
        if y is x or y == x:
            return i
    return -1


def _has(lst, x):
    return any(y is x for y in lst)


# ---------------- connectors ----------------

def updateAttachments(P):
    """port of updateAttachments (hair-modeler.clean.html:7383): attached anchors sit on their connector — the
    connector changed: they follow it at the same arc fraction; moved together with it they stay; moved away from it
    (a plain drag) they come off it. Returns (the anchors moved, the toasts)."""
    moved = []; seen = set(); toasts = []
    for c in P.curves():
        for a in c.anchors:
            if a.att is None or id(a) in seen:
                continue
            seen.add(id(a))
            h = P.items.get(a.att.c)
            if h is None or h.type != 'curve' or _has(h.anchors, a):
                a.att = None
                continue
            if len(h.samples) < 2:
                continue
            if a.attPos is not None and a.pos.distanceToSquared(a.attPos) > 1e-14:
                t2 = nearestT(h, a.pos); q2 = pointOnCurve(h, t2)
                if q2 is not None and q2.distanceTo(a.pos) > P.S.scale * 0.002:
                    a.att = None; a.attPos = None; moved.append(a); toasts.append(CAME_OFF)
                    continue
                a.att.t = t2
            q = pointOnCurve(h, a.att.t)
            if q is not None and q.distanceToSquared(a.pos) > 1e-14:
                a.pos.copy(q); moved.append(a)
            a.attPos = a.pos.clone()
    if moved:
        CM.markAnchorsDirty(P, moved)
    return moved, toasts


def attachCandidates(P, a):
    """the first half of checkAttach (hair-modeler.clean.html:7406): the connectors an anchor END dragged near them may
    attach to (the viewport picks the one under the anchor: pickCurve over them), or [] when this anchor cannot attach
    (already attached, a disc's ring, not the end of a curve that is no connector)"""
    if a.att is not None or any(c.ring for c in edit.curvesWith(P, a)):
        return []
    if not any(not c.conn and (c.anchors[0] is a or c.anchors[len(c.anchors) - 1] is a) for c in edit.curvesWith(P, a)):
        return []
    return [c for c in connectors(P) if not _has(c.anchors, a)]


def insertIntoConnector(P, conn, a):
    # port of insertIntoConnector (hair-modeler.clean.html:7439): the anchor goes into the connector's anchors, on it
    if len(conn.samples) < 2:
        CM.rebuildCurve(P, conn)
    t = nearestT(conn, a.pos); p = pointOnCurve(conn, t); L = conn.arc; d = t * L[len(L) - 1]
    i = 0
    while i < len(L) - 2 and L[i + 1] < d:
        i += 1
    seg = conn.samples[i].seg; a.att = None
    if p is not None:
        a.pos.copy(p)
    conn.anchors.insert(seg + 1, a); CM.markAnchorsDirty(P, [a, *conn.anchors])
    P.gen += 1


def connectToConnector(P, a, conn):
    """port of connectToConnector (hair-modeler.clean.html:7416): a curve end on a connector becomes an anchor OF the
    connector (shared, like the crown curves made with the crown). Returns the toast."""
    other = [c.anchors[len(c.anchors) - 1] if c.anchors[0] is a else c.anchors[0] for c in edit.curvesWith(P, a)]
    k = next((x for x in P.crowns() if x.low == conn.id), None); part = P.items.get(k.part) if k else None
    insertIntoConnector(P, conn, a)
    if k and part and any((o.att is not None and o.att.c == part.id) or _has(part.anchors, o) for o in other):
        return 'Crown curve connected to the lower connector'
    return 'Attached — the anchor is part of the connector now: drag it to reshape the connector, Ctrl + drag slides it along, Ctrl + click detaches'


def detachFromConnector(P, sel, a):
    """port of detachFromConnector (hair-modeler.clean.html:7425): Ctrl + click on a curve's anchor on a connector —
    the curve comes off with an anchor of its own at the same spot (the connector keeps its shape; crown curves stay);
    an old-style attached anchor just lets go. Returns the toast, or None when nothing came off (`sel` changed only
    when a curve came off: the new anchor alone)."""
    if a.att is not None:
        a.att = None; a.attPos = None; CM.markAnchorsDirty(P, [a])
        return 'Anchor detached from the connector'
    if not any(c.conn for c in edit.curvesWith(P, a)):
        return None
    crownC = {r['c'].id for k in P.crowns() for r in crownCurves(P, k)}
    users = [c for c in edit.curvesWith(P, a) if not c.conn and not c.ring and c.id not in crownC]
    if not users:
        return None
    n = edit.newAnchor(P, a.pos); n.smooth = a.smooth
    for c in users:
        c.anchors = [n if x is a else x for x in c.anchors]
        o = c.ovr.pop(a.id, None)
        if o is not None:
            c.ovr[n.id] = type(o)(in_=o.in_.clone() if o.in_ else None, out=o.out.clone() if o.out else None)
    CM.markAnchorsDirty(P, [a, n]); sel.anchors = [n.id]; sel.items = []
    P.gen += 1
    return f'{users[0].name if len(users) == 1 else str(len(users)) + " curves"} detached from the connector'


def slideTarget(P, a):
    """the curve Ctrl + drag slides `a` along (5825): its connector when attached, else a connector holding it, or
    None"""
    if a.att is not None:
        h = P.items.get(a.att.c)
        return h if h is not None and h.type == 'curve' else None
    return next((c for c in edit.curvesWith(P, a) if c.conn), None)


def slideStep(P, d, m, screen):
    """port of slideStep (hair-modeler.clean.html:7447): Ctrl + drag of an attached anchor or an anchor of a connector
    — it slides along the connector as it was when the drag began. `d` = {'a': the anchor, 'poly': the connector's
    sample points at the start, 'moved'}; `m` = (x, y) the pointer; `screen(p)` -> (x, y, z) (toScreen)."""
    Q = d['poly']; best = None; bd = float('inf'); bl = 0; acc = 0
    for i in range(len(Q) - 1):
        a = screen(Q[i]); b = screen(Q[i + 1]); sl = Q[i].distanceTo(Q[i + 1])
        if a[2] <= 1 and b[2] <= 1:
            vx = b[0] - a[0]; vy = b[1] - a[1]; l2 = vx * vx + vy * vy
            t = clamp(((m[0] - a[0]) * vx + (m[1] - a[1]) * vy) / l2, 0, 1) if l2 > 0 else 0
            dd = js_hypot(a[0] + vx * t - m[0], a[1] + vy * t - m[1])
            if dd < bd:
                bd = dd; best = Q[i].clone().lerp(Q[i + 1], t); bl = acc + sl * t
        acc += sl
    if best is None:
        return False
    an = d['a']; an.pos.copy(best)
    if an.att is not None:
        an.att.t = bl / (acc or 1)
    an.attPos = best.clone()
    d['moved'] = True; CM.markAnchorsDirty(P, [an])
    return True


# ---------------- symmetry ----------------

def crownMakeSymmetric(P, sel, k, master, silent=False):
    """port of crownMakeSymmetric (hair-modeler.clean.html:7726): the master half (1: the character's left, +X; -1: its
    right) copied onto the other across X = S.center.x — the lower connector's anchors there and the crown curves there
    replaced by mirrored copies (lock curves attached on that side stay: their anchors go onto the new connector), the
    parting line and the anchors near the plane onto it. Returns the toast (None when silent)."""
    part = P.items.get(k.part); low = P.items.get(k.low)
    if not part or not low:
        return None
    for c in (part, low):
        if c.dirty or len(c.samples) < 2:
            CM.rebuildCurve(P, c)
    S = P.S
    cx = S.center.x; tol = js_max(js_or(low.arc[len(low.arc) - 1] if low.arc else None, S.scale) * 0.01, 1e-6)

    def side(a):
        dx = a.pos.x - cx
        return 0 if abs(dx) < tol else (1 if dx > 0 else -1)      # (Math.sign)

    def mir(p):
        q = p.clone(); q.x = 2 * cx - q.x
        return q
    rads = crownCurves(P, k); crownIds = {r['c'].id for r in rads}

    def lowEnd(r):
        return r['c'].anchors[len(r['c'].anchors) - 1] if r['rev'] else r['c'].anchors[0]
    L = list(low.anchors); sides = [side(a) for a in L]; masters = [a for i, a in enumerate(L) if sides[i] == master]
    if not masters and not any(side(lowEnd(r)) == master for r in rads):
        return None if silent else NOTHING_TO_MIRROR
    pairs = []; mid = []; amap = {}

    def mirA(a):
        if id(a) not in amap:
            n = edit.newAnchor(P, mir(a.pos)); n.smooth = a.smooth; amap[id(a)] = n; pairs.append([a.id, n.id])
        return amap[id(a)]

    def toMid(a):
        if a.att is None:
            a.pos.x = cx
        if a.id not in mid:
            mid.append(a.id)
    for a in part.anchors:                                          # the parting line lies on the plane
        toMid(a)
    # the lower connector: the master side, the middle, the master side mirrored — in the connector's order
    foreign = [a for i, a in enumerate(L) if sides[i] == -master and any(not c.conn and not c.ring and c.id not in crownIds for c in edit.curvesWith(P, a))]
    mids = [a for i, a in enumerate(L) if sides[i] == 0]
    for a in mids:
        toMid(a)
    iM = next((i for i in range(len(L)) if sides[i] == master), -1); iS = next((i for i in range(len(L)) if sides[i] == -master), -1)
    # a lock anchor there that already IS a master anchor's mirror (its pair from the last mirroring, or at the
    # mirrored spot) stays as that mirror — else the lock's anchor and a new copy both sat in the connector at one
    # place and it ran there and back (a doubled crown after Mirror again)
    sy0 = k.p.get('sym'); oldPairs = (sy0.get('pairs') if sy0 else None) or []
    reused = set(); reach = js_max(tol, crownStep(P, k) * 1.5)
    for m in masters:
        f = None
        for x, y in oldPairs:
            o = y if x == m.id else (x if y == m.id else None)
            if o is not None and f is None:
                f = next((a for a in foreign if a.id == o and id(a) not in reused), None)
        if f is None:
            bd = reach
            for a in foreign:
                dd = float('inf') if id(a) in reused else a.pos.distanceTo(mir(m.pos))
                if dd < bd:
                    bd = dd; f = a
        if f is not None:
            reused.add(id(f)); f.pos.copy(mir(m.pos)); amap[id(m)] = f; pairs.append([m.id, f.id])
    copies = [mirA(a) for a in masters][::-1]
    low.anchors = [*copies, *mids, *masters] if 0 <= iS < iM else [*masters, *mids, *copies]
    low.dirty = True; CM.rebuildCurve(P, low)
    # crown curves: the master side's are mirrored, the other side's go, the middle ones go onto the plane
    from .model import HandleOverride

    def flipV(v):
        return V3(-v.x, v.y, v.z) if v is not None else None
    for r in rads:
        s = side(lowEnd(r))
        if s == -master:
            edit.deleteItem(P, r['c'].id)
            continue
        if s == 0:
            for a in r['c'].anchors:
                if not _has(part.anchors, a) and not _has(low.anchors, a):
                    toMid(a)
            r['c'].dirty = True
            continue
        n = edit.makeCurve(P, r['c'].parent); n.name = re.sub(r'(\s+mirror)+\Z', '', r['c'].name, flags=re.IGNORECASE) + ' mirror'

        def copyA(a):
            if _has(part.anchors, a):
                return a                                            # on the parting line (the plane) — shared
            if a.att is not None:
                mm = edit.newAnchor(P, mir(a.pos))
                mm.att = Attachment(c=a.att.c, t=nearestT(low, mir(a.pos)) if a.att.c == low.id else a.att.t); mm.attPos = None
                return mm
            return mirA(a)
        n.anchors = [copyA(a) for a in r['c'].anchors]
        for aid, o in r['c'].ovr.items():
            a = next((x for x in r['c'].anchors if x.id == aid), None)
            if a is not None:
                m = n.anchors[_index(r['c'].anchors, a)]
                n.ovr[m.id] = HandleOverride(in_=flipV(o.in_), out=flipV(o.out))
    # the other lock curves on the replaced side stay attached (the connector rebuilt after each: its samples give the place)
    for a in foreign:
        if id(a) not in reused:
            insertIntoConnector(P, low, a); CM.rebuildCurve(P, low)
    CM.markAnchorsDirty(P, [*part.anchors, *low.anchors])
    for c in P.curves():
        if c.parent == k.parent:
            c.dirty = True
    k.p = {**k.p, 'sym': {'on': True, 'master': master, 'pairs': pairs, 'mid': mid}}; k.sym_last = None
    P.gen += 1
    if silent:
        return None
    sel.anchors = []; sel.items = [k.id]
    return f'Crown mirrored: {sym_text(master)} — symmetric now, either side drives the other'


def crownSymOff(P, k):
    """the Symmetric checkbox unticked (7828): k.p.sym = null. Returns the toast."""
    k.p = {**k.p, 'sym': None}
    return SYM_OFF


def crownSymSync(P):
    """port of crownSymSync (hair-modeler.clean.html:7779): symmetric crowns — an anchor moved on either side since the
    last sync moves its pair, the middle ones stay on the plane. The first sync of a crown (after it was made, mirrored
    or read) only records. Returns the anchors it moved."""
    out = []
    for k in P.crowns():
        sy = k.p.get('sym')
        if not sy or not sy.get('on'):
            continue
        cx = P.S.center.x; A = {}; moved = []
        for c in P.curves():
            if c.parent == k.parent or c.id == k.low or c.id == k.part:
                for a in c.anchors:
                    A[a.id] = a
        fresh = k.sym_last is None
        if fresh:
            k.sym_last = {}
        last = k.sym_last
        for ia, ib in sy.get('pairs') or []:
            a = A.get(ia); b = A.get(ib)
            if a is None or b is None:
                continue
            if not fresh and ia in last and ib in last:            # (a pair not recorded yet — its curve came back with an undo — records only: the original would throw in its frame)
                am = not last[ia].equals(a.pos); bm = not last[ib].equals(b.pos)
                if am and not bm:
                    b.pos.set(2 * cx - a.pos.x, a.pos.y, a.pos.z); moved.append(b)
                elif bm and not am:
                    a.pos.set(2 * cx - b.pos.x, b.pos.y, b.pos.z); moved.append(a)
            last[ia] = a.pos.clone(); last[ib] = b.pos.clone()
        for i in sy.get('mid') or []:
            a = A.get(i)
            if a is not None and a.att is None and abs(a.pos.x - cx) > 1e-9:
                a.pos.x = cx; moved.append(a)
        if moved:
            CM.markAnchorsDirty(P, moved)
        out.extend(moved)
    return out


def syncSnapshot(P):
    """(ours, 4.3: the Esc of a Blender drag puts back what frameSync moved while it ran) the position and attachment of
    every anchor attached or on a crown's curves and connectors, and every crown's last synced positions; None when
    there is nothing a frame step could move"""
    crowns = P.crowns()
    if not crowns and not any(a.att is not None for a in P.anchors.values()):
        return None
    keep = {}
    for c in P.curves():
        mine = any(c.parent == k.parent or c.id in (k.part, k.low) for k in crowns)
        for a in c.anchors:
            if (mine or a.att is not None) and id(a) not in keep:
                keep[id(a)] = (a, a.pos.clone(), (a.att.c, a.att.t) if a.att is not None else None, a.attPos.clone() if a.attPos is not None else None)
    return list(keep.values()), [(k, dict(k.sym_last) if k.sym_last is not None else None) for k in crowns]


def syncRestore(P, snap):
    """puts back a syncSnapshot (the anchors' curves resampled by the next frame)"""
    if snap is None:
        return
    anchors, syms = snap
    for a, pos, att, attPos in anchors:
        a.pos.copy(pos); a.att = Attachment(c=att[0], t=att[1]) if att is not None else None; a.attPos = attPos.clone() if attPos is not None else None
    for k, last in syms:
        k.sym_last = dict(last) if last is not None else None
    if anchors:
        CM.markAnchorsDirty(P, [a for a, *_r in anchors])


def recordSym(P):
    """the crowns' last synced positions as they are now (ours, 4.3: a model read again from the text — an undo, a
    load — is the state after a frame of the original: the next sync compares with it)"""
    for k in P.crowns():
        sy = k.p.get('sym')
        if not sy or not sy.get('on'):
            continue
        A = {a.id: a for c in P.curves() if c.parent == k.parent or c.id in (k.low, k.part) for a in c.anchors}
        k.sym_last = {i: A[i].pos.clone() for pr in sy.get('pairs') or [] for i in pr if i in A}


def frameSync(P):
    """The crown part of the original's frame (12060-12063): crownSymSync, the dirty curves rebuilt, updateAttachments,
    the dirty curves rebuilt — D4.8: run at the end of every operator that changes the model and on every move of a
    drag. Without a crown or an attached anchor nothing could move: nothing is done (the dirty curves wait for the
    next rebuild). Returns (the anchors moved, the toasts)."""
    if not P.crowns() and not any(a.att is not None for a in P.anchors.values()):
        return [], []
    moved = crownSymSync(P)
    CM.rebuild_dirty(P)
    m2, toasts = updateAttachments(P)
    CM.rebuild_dirty(P)
    return moved + m2, toasts


# ---------------- the panel (crownProps 7805) ----------------

def setCrownParam(k, key, value):
    """the crown's panel: Rings (k.p.rings = +r.value, 7810), Whorl (k.p = {...k.p, whorl}, 7818)"""
    if key == 'rings':
        k.p['rings'] = value
    else:
        k.p = {**k.p, key: value}
    k.dirty = True


def infoText(k):
    # crownProps (7806): what crownData said about the crown
    i = k.info
    if i:
        return f'Vertical loops: {i["loops"]} · rings: {i["rings"]} · parting vertices: {i["part"]}'
    return 'The crown needs its parting line, lower connector and at least one crown curve.'
