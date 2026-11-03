# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Phase 4.2: the Guides tool of the original (hair-modeler.clean.html:6187-6351, 1097-1131, 8843-8945, 9015-9024) —
the data part, pure and bit for bit with the original (tests/test_guidetool_parity.py, harness --script ops
guideStroke, guideProp, placeDisc, createDisc, discRadius, resetDisc, pencil).

  Line    strokeHit / createGuide: a screen-space stroke across the selected curves (or every shown one) makes a guide
          line through the crossings; pickGuide: a click on a guide makes it the active one.
  Zone    zoneWeight / zoneSegments: the influence zone the original draws while its sliders are used (updateZoneViz).
  Disc    surfacePoint's choice, placeDisc, createDiscButton, setDiscRadius, resetDisc.
  Pencil  Pencil / pencilAdd / finishPencil: a loop drawn on a plane closes into a 6-point disc.

What needs the viewport (the projector of a screen point, the ray and what it hits) comes in as arguments: `screen(p)`
-> (x, y, z) | None (toScreen 5656), rays in the model's space, hit points already found. The selection is a
core.selection.Selection (S.sel / S.asel)."""
from .duplicate import makeDisc, makeGuide
from .fdlibm import cos, sin
from .geom import pointOnCurve, resample
from .guides import discBasis, discFrame, discRings, guideExcludes, hairCurves
from .js import clamp, js_hypot, js_max, js_min, js_or, nullish
from .raycast import Box3, Plane
from .vec3 import V3

PI = 3.141592653589793


# ---------------- the line ----------------

def strokeHit(c, A, B, screen):
    # port of strokeHit (hair-modeler.clean.html:6189): where a screen-space stroke A -> B crosses curve c —
    # {u: position along the stroke, t: arc fraction on c}, the crossing nearest the stroke's start
    if not c.arc or len(c.samples) < 2:
        return None
    sp = [screen(s.p) for s in c.samples]; best = None
    rx = B[0] - A[0]; ry = B[1] - A[1]
    for i in range(len(sp) - 1):
        a = sp[i]; b = sp[i + 1]
        if a is None or b is None or a[2] > 1 or b[2] > 1:
            continue
        sx = b[0] - a[0]; sy = b[1] - a[1]; den = rx * sy - ry * sx
        if abs(den) < 1e-9:
            continue
        qx = a[0] - A[0]; qy = a[1] - A[1]; u = (qx * sy - qy * sx) / den; s = (qx * ry - qy * rx) / den
        if u < 0 or u > 1 or s < 0 or s > 1:
            continue
        if not best or u < best['u']:
            best = {'u': u, 'i': i, 's': s}
    if not best:
        return None
    L = c.arc; i = best['i']
    return {'u': best['u'], 't': (L[i] + best['s'] * (L[i + 1] - L[i])) / js_or(L[len(L) - 1], 1)}


LINE_NONE_ANY = 'Select curves, then drag a line across at least two of them'
LINE_NONE_SEL = 'The line must cross at least two selected curves'


def selectedCurves(P, sel):
    # port of selectedCurves (7843): the selected items that are curves, in selection order
    return [it for it in (P.items.get(i) for i in sel.items) if it is not None and it.type == 'curve']


def createGuide(P, sel, A, B, screen, visible):
    """port of createGuide (hair-modeler.clean.html:6336): the selected curves (or, with none selected, every visible
    hair curve) crossed by the stroke -> a new guide line; the curves stay selected and the guide becomes the active
    one. `visible(c)`: c.obj.visible. Returns (the guide or None, the toast); `sel` is changed in place (S.sel,
    S.asel). The caller marks the locks dirty (S.surfDirty: the curves guides.guideCurves names)."""
    cand = [c for c in selectedCurves(P, sel) if visible(c) and not c.ring]
    any_ = not cand
    if any_:
        cand = [c for c in hairCurves(P) if visible(c)]
    hits = []
    for c in cand:
        h = strokeHit(c, A, B, screen)
        if h:
            hits.append({'c': c.id, 't': h['t'], 'u': h['u']})
    hits.sort(key=lambda h: h['u'])                                 # (Array.prototype.sort is stable, as sorted)
    if len(hits) < 2:
        return None, LINE_NONE_ANY if any_ else LINE_NONE_SEL
    g = makeGuide(P, P.items[hits[0]['c']].parent, [{'c': h['c'], 't': h['t']} for h in hits])
    sel.anchors = []
    sel.items = [i for i in sel.items if (it := P.items.get(i)) is not None and it.type == 'curve']
    if any_:
        for h in hits:
            if h['c'] not in sel.items:
                sel.items.append(h['c'])
    if g.id in sel.items:
        sel.items.remove(g.id)
    sel.items.append(g.id)
    return g, f'Guide line across {len(hits)} curves'


def pickGuide(P, guides, x, y, screen, radius=7):
    """port of pickGuide (5672): pickCurve over the guides shown (their samples: a line's points through its hits;
    a disc has none, it is picked by its surface), the nearest within `radius` px, or None"""
    best = None; bd = radius
    for g in guides:
        sp = [screen(s['p']) for s in g.samples]
        for i in range(len(sp) - 1):
            a = sp[i]; b = sp[i + 1]
            if a is None or b is None or a[2] > 1 or b[2] > 1:
                continue
            vx = b[0] - a[0]; vy = b[1] - a[1]; l2 = vx * vx + vy * vy
            t = clamp(((x - a[0]) * vx + (y - a[1]) * vy) / l2, 0, 1) if l2 > 0 else 0
            d = js_hypot(a[0] + vx * t - x, a[1] + vy * t - y)
            if d < bd:
                bd = d; best = g
    return best


def activateGuide(P, sel, g):
    """a click on a guide / disc makes it the active one (6031): the anchors and the items that are not curves leave
    the selection, the guide goes in last (the curve selection stays)"""
    sel.anchors = []
    sel.items = [i for i in sel.items if (it := P.items.get(i)) is not None and it.type == 'curve' and i != g.id]
    sel.items.append(g.id)


# ---------------- the influence zone (updateZoneViz, 6205-6239) ----------------

ZONE_FULL = (0xa6 / 255.0, 0xff / 255.0, 0xc4 / 255.0)               # 0xa6ffc4: loops fully follow the guide
ZONE_NONE = (0x17 / 255.0, 0x4f / 255.0, 0x2e / 255.0)               # 0x174f2e: towards the edge where Blend mixes them back
ZONE_N = 48
ZONE_SECONDS = 1.5                                                  # showZone (6210): the zone stays this long after a slider


def zoneWeight(rad, blend, x):
    # port of zoneWeight (hair-modeler.clean.html:6211): the guide's weight at distance x along the lock (as spineRows)
    if rad <= 0 or x >= rad:
        return 0
    x0 = rad * (1 - clamp(blend, 0, 1))
    if x <= x0:
        return 1
    t = (x - x0) / js_max(rad - x0, 1e-9)
    return 1 - t * t * (3 - 2 * t)


def zoneSegments(P, g):
    """port of updateZoneViz's geometry (6216): the part of every crossed curve inside the radius, coloured by the
    weight there (light green: loops fully follow the guide, darker towards the edge), plus the two boundary loops.
    Returns (points, colours): two of each per segment (colours as (r, g, b) in the sRGB of the original's hex; the
    lerp of THREE.Color runs on its linear values — here on these, a drawing-only difference). Empty without hits."""
    if g is None or not g.hits:
        return [], []
    seg = []; col = []; rad = g.p.get('radius', 0) * P.S.headH; blend = nullish(g.p.get('blend'), 1); lo = []; hi = []; N = ZONE_N

    def cw(x):
        w = zoneWeight(rad, blend, js_min(x, rad * 0.995))
        return tuple(a + (b - a) * w for a, b in zip(ZONE_NONE, ZONE_FULL))
    c0 = cw(rad)
    for h in g.hits:
        c = P.items.get(h.get('c'))
        if not c or not c.arc or guideExcludes(P, g, c):
            continue
        tot = js_or(c.arc[len(c.arc) - 1], 1); t0 = clamp(h['t'] - rad / tot, 0, 1); t1 = clamp(h['t'] + rad / tot, 0, 1)
        prev = None; pc = None
        for q in range(N + 1):
            t = t0 + (t1 - t0) * q / N; p = pointOnCurve(c, t); pcol = cw(abs(t - h['t']) * tot)
            if prev is not None:
                seg += [prev, p]; col += [pc, pcol]
            prev = p; pc = pcol
        lo.append(pointOnCurve(c, t0)); hi.append(pointOnCurve(c, t1))
    for side in (lo, hi):
        for i in range(len(side) - 1 + (1 if g.kind == 'disc' else 0)):
            p = side[i]; q = side[(i + 1) % len(side)]
            if p is not None and q is not None and p is not q:
                seg += [p, q]; col += [c0, c0]
    return seg, col


# ---------------- the NURBS disc ----------------

DISC_PLACED = 'NURBS disc created — E / R / T move, rotate, transform it, the Brush shapes it'


def surfacePointChoice(hit_point, curve_point, plane_point):
    """the order of surfacePoint (6271): what the click hits among pickables() (not discs, not the reference cube),
    else the curve under it (pickCurve over the hair curves), else the plane through the pivot facing the camera"""
    if hit_point is not None:
        return hit_point.clone()
    if curve_point is not None:
        return curve_point
    return plane_point


def placeDisc(P, sel, Pt):
    """port of placeDisc (6277): a horizontal ring around the head axis at the clicked height, sized so the clicked
    hair lies between the inner and the outer ring. `Pt` = surfacePoint(m). Returns the disc (None without a point);
    the disc is the selection. The caller marks the locks dirty (S.surfDirty)."""
    if not Pt:
        return None
    S = P.S
    C = V3(S.center.x, Pt.y, S.center.z); r = js_max(js_hypot(Pt.x - C.x, Pt.z - C.z), S.scale * 0.05)
    g = makeDisc(P, C, V3(0, 1, 0), r * 1.6, r * 0.5)
    sel.anchors = []; sel.items = [g.id]
    return g


def createDiscButton(P, sel, visible):
    """port of createDiscButton (6286): a disc around the selected curves (or all the visible hair, or the top of the
    model) at their middle height. `visible(c)`: isVisible. The disc is the selection."""
    S = P.S
    cs = [c for c in selectedCurves(P, sel) if not c.ring and c.samples]
    if not cs:
        cs = [c for c in hairCurves(P) if c.samples and visible(c)]
    box = Box3()
    for c in cs:
        for s in c.samples:
            box.expandByPoint(s.p)
    if not (box.max.x < box.min.x or box.max.y < box.min.y or box.max.z < box.min.z):   # (!box.isEmpty())
        C = box.getCenter(V3()); C.x = S.center.x; C.z = S.center.z; sz = box.getSize(V3()); r = js_max(sz.x, sz.z) * 0.5
    else:
        C = V3(S.center.x, S.yRef - 0.06 * S.headH, S.center.z); r = 0.06 * S.headH
    r = js_max(r, S.scale * 0.02)
    g = makeDisc(P, C, V3(0, 1, 0), r * 1.25, r * 0.35)
    sel.anchors = []; sel.items = [g.id]
    return g


def discMinR(P):
    # port of discMinR (1096)
    return P.S.scale * 0.002


def setDiscRadius(P, g, which, r):
    """port of setDiscRadius (hair-modeler.clean.html:1097): every point of the ring moves radially by the change, so
    hand deformations are kept; a ring never shrinks to nothing (a point there has no direction to grow back in), and
    one that already did takes the directions of the other ring's points. Returns the ring's anchors moved (the
    caller's markAnchorsDirty), or []."""
    rs = discRings(P, g)
    ring = rs[0] if which == 'out' else (rs[1] if len(rs) > 1 else None)
    other = (rs[1] if len(rs) > 1 else None) if which == 'out' else (rs[0] if rs else None)
    if not ring:
        return []
    eps = discMinR(P); r = js_max(r, eps)
    key = 'rOut' if which == 'out' else 'rIn'; dr = r - g.p[key]; g.p = {**g.p, key: r}
    fr = discFrame(P, g); C = fr['C']; N = fr['N']; u, v = discBasis(N)

    def radial(p):
        d = p.clone().sub(C)
        return d.addScaledVector(N, -d.dot(N))
    for k, a in enumerate(ring.anchors):
        d = radial(a.pos); ln = d.length()
        if ln < eps * 0.5:                                          # collapsed point: back to the centre, then out
            o = radial(other.anchors[k].pos) if other is not None and k < len(other.anchors) else None
            if o is not None and o.lengthSq() > eps * eps * 0.25:
                d.copy(o)
            else:
                d.copy(u).multiplyScalar(cos(k * PI / 3)).addScaledVector(v, sin(k * PI / 3))
            a.pos.addScaledVector(radial(a.pos), -1); ln = 0
        d.normalize()
        a.pos.addScaledVector(d, js_max(ln + dr, eps) - ln)
    return list(ring.anchors)


def clampedRadius(P, g, which, x):
    """the panel's clamp (8926-8927): the outer ring stays a step outside the inner one and the other way round"""
    mn = discMinR(P)
    return js_max(x, g.p['rIn'] + mn) if which == 'out' else js_min(x, g.p['rOut'] - mn)


def resetDisc(P, g):
    """port of resetDisc (hair-modeler.clean.html:1115): both rings back to circles of their radii in the disc's
    plane, the first anchor's direction kept; smoothness 1, the frozen handles cleared. Returns the anchors moved."""
    t = resetTargets(P, g)
    if t is None:
        return []
    rs, targets = t
    for a, q in targets:
        a.pos.copy(q); a.smooth = 1
    for c in rs:
        c.ovr.clear()
    return [a for a, _q in targets]


def resetTargets(P, g):
    """where resetDisc puts every ring anchor, without moving any: (the rings, [(anchor, position)]), None with fewer
    than two rings (the same operations in the same order: the positions are resetDisc's to the bit)"""
    fr = discFrame(P, g); C = fr['C']; N = fr['N']; rs = discRings(P, g)
    if len(rs) < 2:
        return None
    d0 = rs[0].anchors[0].pos.clone().sub(C); d0.addScaledVector(N, -d0.dot(N))
    u = d0.normalize() if d0.lengthSq() > 1e-12 else discBasis(N)[0]; v = V3().crossVectors(N, u)
    out = []
    for ri, c in enumerate(rs):
        r = g.p['rOut'] if ri == 0 else g.p['rIn']
        for k, a in enumerate(c.anchors):
            out.append((a, V3().copy(C).addScaledVector(u, r * cos(k * PI / 3)).addScaledVector(v, r * sin(k * PI / 3))))
    return rs, out


# ---------------- the pencil ----------------

PENCIL_SHORT = 'Draw a loop around the hair (close it or press Enter)'
PENCIL_DONE = 'Pencil loop turned into a NURBS disc'


class Pencil:
    """S.pencil (6302): the loop's points on its plane and their screen positions; the plane is fixed by the first
    point (`anchor`, `flat`)"""
    def __init__(self):
        self.pts = []; self.scr = []; self.anchor = None; self.flat = None


def pencilAdd(pc, m, ray, cam_dir, pivot, hit_point, step_px=4, close_len=120, close_px=14):
    """port of pencilAdd (hair-modeler.clean.html:6301), the data part. `m` the screen point (x, y), `ray` the
    cursor's ray (rayAt) in the model's space, `hit_point` what pickables() minus the discs hit under it (only read
    for the first point; a callable is called only then), `cam_dir` / `pivot` the view's. The stroke lies on one
    plane: horizontal at the height where it first touched the hair (a ring drawn around the head); when that plane
    is seen edge-on, the screen plane at that depth. Returns 'close' when the loop came back to its start
    (finishPencil follows), True when a point was added, else False. The pixel thresholds are the original's CSS px
    (the caller scales them)."""
    last = pc.scr[len(pc.scr) - 1] if pc.scr else None
    if last and js_hypot(m[0] - last[0], m[1] - last[1]) < step_px:
        return False
    if not pc.anchor:
        hp = hit_point() if callable(hit_point) else hit_point
        pc.anchor = hp.clone() if hp is not None else pivot.clone(); pc.flat = abs(ray.direction.y) > 0.2
    p = ray.intersectPlane(Plane().setFromNormalAndCoplanarPoint(V3(0, 1, 0) if pc.flat else cam_dir, pc.anchor), V3())
    if not p:
        return False
    pc.pts.append(p); pc.scr.append((m[0], m[1]))
    if len(pc.scr) > 12:
        f = pc.scr[0]; ln = 0
        for i in range(1, len(pc.scr)):
            q = pc.scr[i]; r = pc.scr[i - 1]
            ln = ln + js_hypot(q[0] - r[0], q[1] - r[1])
        if ln > close_len and js_hypot(m[0] - f[0], m[1] - f[1]) < close_px:
            return 'close'
    return True


def finishPencil(P, sel, pc, cam_dir):
    """port of finishPencil (hair-modeler.clean.html:6318), after cancelPencil: a loop of 6 or more points -> a NURBS
    disc in the loop's best plane (the drawn loop a little beyond its outer edge, a small hole); the disc is the
    selection. Returns (the disc or None, the toast)."""
    if not pc or len(pc.pts) < 6:
        return None, PENCIL_SHORT
    loop = [*pc.pts, pc.pts[0]]; mid = resample(loop, 7)[:6]
    C = V3()
    for p in pc.pts:
        C.add(p)
    C.divideScalar(len(pc.pts))
    N = V3()
    n = len(pc.pts)
    for i in range(n):
        a = pc.pts[i]; b = pc.pts[(i + 1) % n]
        N.x += (a.y - b.y) * (a.z + b.z); N.y += (a.z - b.z) * (a.x + b.x); N.z += (a.x - b.x) * (a.y + b.y)
    if N.lengthSq() < 1e-16:
        N.copy(cam_dir)
    N.normalize()
    for p in mid:
        p.addScaledVector(N, -p.clone().sub(C).dot(N))             # a flat disc in the loop's best plane
    # the drawn loop is the outer edge (a little beyond it), the hole is small — room for the head
    out = [C.clone().add(p.clone().sub(C).multiplyScalar(1.1)) for p in mid]
    inn = [C.clone().add(p.clone().sub(C).multiplyScalar(0.3)) for p in mid]
    rm = 0
    for p in mid:
        rm = rm + p.distanceTo(C)
    rm = rm / 6
    g = makeDisc(P, C, N.normalize(), rm * 1.1, rm * 0.3, out, inn)
    sel.anchors = []; sel.items = [g.id]
    return g, PENCIL_DONE


# ---------------- the guide's panel (guideControls, 8843) ----------------

GUIDE_KEYS = ('radius', 'blend', 'density', 'priority', 'local')


def setGuideParam(g, key, value):
    """a slider / checkbox of guideControls (8856-8877): tgt[key] = value on the guide's own p (the defaults' when
    `g` is the dict of S.guideDefaults). The caller marks the locks dirty (upd: S.surfDirty)."""
    if isinstance(g, dict):
        g[key] = value
    else:
        g.p = {**g.p, key: value}


def setExclusions(g, lst):
    """setExcl (8884): the list is replaced, never edited in place (undo snapshots share the p object's arrays)"""
    g.p = {**g.p, 'excl': list(lst)}


def setExclMode(g, mode):
    """the list's mode (8893): 'exclude' — the listed groups are left alone; 'only' — the guide works only on them"""
    g.p = {**g.p, 'exclMode': mode}


def exclusionCandidates(P, g):
    """the choice of the list (8905): the groups holding curves the guide crosses, with their parent groups (not the
    guides' own group), in the order the hits meet them, minus the ones listed. (group, depth) pairs."""
    ex = [i for i in (g.p.get('excl') or []) if (it := P.items.get(i)) is not None and it.type == 'group']
    cand = []
    for h in g.hits or []:
        q = P.parentOf(P.items.get(h.get('c'))) if P.items.get(h.get('c')) is not None else None
        while q:
            if q.type == 'group' and not q.guidesGroup and q.id not in cand:
                cand.append(q.id)
            q = P.parentOf(q)
    out = []
    for iid in cand:
        if iid in ex:
            continue
        it = P.items[iid]; depth = 0; q = P.parentOf(it)
        while q:
            depth += 1; q = P.parentOf(q)
        out.append((it, depth))
    return out


def crossedCurves(P, g):
    """"Select crossed curves" (8942): the curves of the line's hits that still exist, then the guide"""
    return [h['c'] for h in g.hits if h.get('c') in P.items] + [g.id]
