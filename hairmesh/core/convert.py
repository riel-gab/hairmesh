# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Conversion of locks into meshes (hair-modeler.clean.html:8466-9152, 9153-9190, 9312-9360): the lock grid,
gradual tips, caps, seams, Merge shared trunks, the mesh of one group and the Convert button."""
import math
from dataclasses import dataclass, field

from . import model as M
from .curves import juncLine, rebuildCurve
from .fdlibm import cos as _cos, sin as _sin
from .geom import faceCenter, faceNormal
from .hairmesh_io import defaultStep
from .js import at, clamp, includes, js_hypot, js_max, js_min, js_or, js_sign, nullish, to_int32, truthy
from .rowbase import convOf, settings_conv
from .surfaces import concatMeshes, mergeWeld, surfaceData, wallsOf, WeldInfo
from .trace import br as _br, dec as _dec, dec_near
from .vec3 import V3


def levelH_of(P, CP):
    # levelH (1597) for a given parameter set: CP.step || defaultStep()
    return js_or(CP.get('step'), defaultStep(P))


def collect(P, gid, checked, types):
    # port of collect (hair-modeler.clean.html:8466): items of the given types under group gid, not descending into
    # sub-groups that are ticked themselves (they get their own mesh)
    out = []

    def walk(ids):
        for iid in ids:
            it = P.items[iid]
            if it.type in types:
                out.append(it)
            elif it.type == 'group' and iid not in checked:
                walk(it.children)
    walk(P.items[gid].children)
    return out


# walls connected through shared curves = one lock
def lockGroups(surfs):
    # port of lockGroups (hair-modeler.clean.html:8479)
    par = {}

    def find(x):
        while par[x] != x:
            x = par[x]
        return x
    for s in surfs:
        for cid in (s.a, s.b):
            if cid not in par:
                par[cid] = cid
        ra = find(s.a); rb = find(s.b)
        if ra != rb:
            par[ra] = rb
    m = {}
    for s in surfs:
        m.setdefault(find(s.a), []).append(s)
    return list(m.values())


@dataclass(eq=False, kw_only=True)
class Grid:
    """a closed lock as rows x columns (lockGrid, 8486); capGrid adds the bevel rows and the cap faces"""
    pos: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    R: int = 0
    M: int = 0
    flip: bool = False
    area: float = 0.0
    capFaces: list | None = None
    capAdded: int = 0
    loops: list | None = None           # lockQuery's cache
    pairs: list | None = None           # lockQuery's cache: the loop pairs' centres and normals as scalars


# a closed lock (its walls form one ring of curves) as a rows × columns grid; None for open locks
def lockGrid(P, walls, T):
    # port of lockGrid (hair-modeler.clean.html:8486)
    deg = {}
    for s in walls:
        for cid in (s.a, s.b):
            deg[cid] = deg.get(cid, 0) + 1
    if len(walls) < 2 or len(deg) != len(walls) or any(d != 2 for d in deg.values()):   # 2 = a wall and its second surface
        return None
    order = []; used = set(); cur = walls[0].a
    for n in range(len(walls)):
        w = next((s for s in walls if s not in used and (s.a == cur or s.b == cur)), None)
        if w is None:
            return None
        used.add(w); order.append((w, w.a == cur)); cur = w.b if w.a == cur else w.a
    cols = []; R = None
    for w, fwd in order:
        d = surfaceData(P, w, T)
        if not d:
            return None
        Mw = js_max(1, to_int32(nullish(w.p.get('segs'), 0))) + 1; r = len(d.pos) / Mw
        if R is None:
            R = r
        elif R != r:
            return None
        for jj in range(Mw - 1):
            j = jj if fwd else Mw - 1 - jj
            cols.append([d.pos[k * Mw + j] for k in range(math.ceil(R))])
    Mc = len(cols); pos = []
    Ri = math.ceil(R)
    for k in range(Ri):
        for j in range(Mc):
            pos.append(cols[j][k].clone())
    faces = []
    for k in range(Ri - 1):
        for j in range(Mc):
            j1 = (j + 1) % Mc
            faces.append([k * Mc + j, k * Mc + j1, (k + 1) * Mc + j1, (k + 1) * Mc + j])
    vote = 0
    for i, f in enumerate(faces):
        k = i // Mc; c = V3()
        for j in range(Mc):
            c.add(pos[k * Mc + j])
        c.divideScalar(Mc)
        vote += js_sign(faceNormal(pos, f).dot(faceCenter(pos, f).sub(c)))
    flip = _dec('lockGrid.vote', vote, 0) < 0
    if flip:
        for f in faces:
            f.reverse()
    area = 0
    for f in faces:
        area += faceNormal(pos, f).length()
    return Grid(pos=pos, faces=faces, R=int(R) if R == int(R) else R, M=Mc, flip=flip, area=area)


# Gradual tip: towards a closed tip one column disappears per loop. Columns are merged by moving their
# vertices together; welding then turns each such quad into the single triangle seen in hand-made locks.
def taperTip(g):
    # port of taperTip (hair-modeler.clean.html:8516)
    R = g.R; Mc = g.M; pos = g.pos
    if Mc < 4 or R < 4:
        return g

    def spread(r):
        mx = 0
        for j in range(1, Mc):
            mx = js_max(mx, pos[r * Mc + j].distanceTo(pos[r * Mc]))
        return mx
    ref = js_max(spread(R // 2), 1e-9)
    tipRow = R - 1 if _dec('taperTip.endSpread', spread(R - 1), 0.05 * ref) < 0.05 * ref else 0 if _dec('taperTip.startSpread', spread(0), 0.05 * ref) < 0.05 * ref else -1
    if tipRow < 0:
        return g                                                    # open end: nothing to taper
    _br('taperTip.tapered')
    dir_ = -1 if tipRow == R - 1 else 1; ln = min(Mc - 2, R - 2); startRow = tipRow + dir_ * ln

    def gpos(grp, r):
        c = V3()
        for j in grp:
            c.add(pos[r * Mc + j])
        return c.divideScalar(len(grp))
    groups = [[j] for j in range(Mc)]
    snaps = {Mc: groups}
    while len(groups) > 1:                                          # merge the closest neighbours first
        bi = 0; bd = math.inf
        for i in range(len(groups)):
            d = gpos(groups[i], startRow).distanceTo(gpos(groups[(i + 1) % len(groups)], startRow))
            if d < bd:
                bd = d; bi = i
        a = groups[bi]; b = groups[(bi + 1) % len(groups)]
        nxt = [x for x in groups if x is not a and x is not b]; nxt.insert(min(bi, len(nxt)), a + b)
        groups = nxt; snaps[len(groups)] = groups
    np_ = [p.clone() for p in pos]
    for i in range(ln + 1):
        r = tipRow + dir_ * i; s = 1 if i == 0 else min(Mc, i + 2)
        if s >= Mc:
            continue
        for grp in snaps[s]:
            c = gpos(grp, r)
            for j in grp:
                np_[r * Mc + j].copy(c)
    return Grid(pos=np_, faces=g.faces, R=g.R, M=g.M, flip=g.flip, area=g.area)


# ---- Cap modifier: a 'cap' item in a group closes the open ends of every closed lock in it ----
def capOf(P, it):
    # port of capOf (hair-modeler.clean.html:8547): the nearest visible Cap item up the hierarchy
    g = P.parentOf(it)
    while g:
        k = next((x for x in (P.items.get(i) for i in g.children) if x and x.type == 'cap' and x.visible), None) if g.type == 'group' else None
        if k:
            return k
        g = P.parentOf(g)
    return None


def centroidOf(ps):
    # port of centroidOf (8554)
    c = V3()
    for q in ps:
        c.add(q)
    return c.divideScalar(len(ps))


# Bevel of the last row of `rows` (loops of M points): new rings past the end loop, out by `size` and in towards the
# middle (at most 85% of the way); round 0 — a straight chamfer, 1 — a quarter circle. Returns the rows.
def bevelEnd(rows, p):
    # port of bevelEnd (hair-modeler.clean.html:8558)
    R = len(rows); Mc = len(rows[0]); E = rows[R - 1]; n = js_max(1, to_int32(nullish(p.get('segs'), 0))); s = js_or(p.get('size'), 0)
    if not (s > 1e-9) or R < 2:
        return rows
    cen = centroidOf(E); d = cen.clone().sub(centroidOf(rows[R - 2]))   # outwards along the lock
    if d.lengthSq() < 1e-18:
        return rows
    d.normalize()
    inw = []
    for q in E:
        v = cen.clone().sub(q); v.addScaledVector(d, -v.dot(d)); Lv = v.length()
        inw.append(v.multiplyScalar(js_min(s / Lv, 0.85)) if Lv > 1e-12 else v)
    rd = clamp(nullish(p.get('round'), 1), 0, 1); out = list(rows)
    for i in range(1, n + 1):
        f = i / n; th = f * math.pi / 2
        a = rd * _sin(th) + (1 - rd) * f; b = rd * (1 - _cos(th)) + (1 - rd) * f   # along / inwards (fdlibm sin/cos = V8's)
        out.append([q.clone().addScaledVector(d, s * a).addScaledVector(inw[j], b) for j, q in enumerate(E)])
    return out


# Fill a loop of vertex indices L (its order = the winding of the new faces). New vertices are pushed into pos.
def capFill(L, pos, mode):
    # port of capFill (hair-modeler.clean.html:8574)
    n = len(L); Pp = lambda i: pos[i]; faces = []
    if n < 3:
        return faces
    if mode == 'point':
        c = len(pos); pos.append(centroidOf([Pp(i) for i in L]))
        for i in range(n):
            faces.append([L[i], L[(i + 1) % n], c])
        return faces
    if mode == 'tris':                                              # n - 2 triangles, a zigzag strip across the short way
        def rung(r):
            t = 0; i = 0; j = n - 1
            while j - i >= 2:
                t += Pp(L[(r + i + 1) % n]).distanceTo(Pp(L[(r + j) % n])); i += 1; j -= 1
            return t
        best = 0; bl = math.inf
        for r in range(n):
            t = rung(r)
            if t < bl:
                bl = t; best = r
        Q = [L[(best + k) % n] for k in range(n)]
        i = 0; j = n - 1; left = True
        while j - i >= 2:
            if left:
                faces.append([Q[i], Q[i + 1], Q[j]]); i += 1
            else:
                faces.append([Q[i], Q[j - 1], Q[j]]); j -= 1
            left = not left
        return faces
    # quads: an odd loop first loses one triangle (at its shortest diagonal), then a grid patch (Coons) fills the rest
    Q = list(L)
    if n % 2:
        bk = 0; bd = math.inf
        for k in range(n):
            d = Pp(Q[(k + n - 1) % n]).distanceTo(Pp(Q[(k + 1) % n]))
            if d < bd:
                bd = d; bk = k
        faces.append([Q[(bk + n - 1) % n], Q[bk], Q[(bk + 1) % n]]); del Q[bk]
    m = len(Q)
    if m < 4:
        return faces
    if m == 4:
        faces.append(list(Q)); return faces
    a = m // 4; b = m // 2 - a

    def patch(r):                                                   # grid (a+1) × (b+1); boundary = Q from r on
        q = lambda k: Pp(Q[(r + k) % m]).clone(); idx = lambda k: Q[(r + k) % m]
        bnd = lambda u, v: u if v == 0 else a + v if u == a else a + b + (a - u) if v == b else (2 * a + b + (b - v)) % m
        pt = []; ids = []; newP = []
        for v in range(b + 1):
            for u in range(a + 1):
                if u == 0 or v == 0 or u == a or v == b:
                    pt.append(q(bnd(u, v))); ids.append(idx(bnd(u, v))); continue
                s = u / a; t = v / b
                c = q(bnd(u, 0)).multiplyScalar(1 - t).addScaledVector(q(bnd(u, b)), t) \
                    .addScaledVector(q(bnd(0, v)), 1 - s).addScaledVector(q(bnd(a, v)), s) \
                    .addScaledVector(q(bnd(0, 0)), -(1 - s) * (1 - t)).addScaledVector(q(bnd(a, 0)), -s * (1 - t)) \
                    .addScaledVector(q(bnd(0, b)), -(1 - s) * t).addScaledVector(q(bnd(a, b)), -s * t)
                pt.append(c); ids.append(-1 - len(newP)); newP.append(c)
        ln = 0; at_ = lambda u, v: pt[v * (a + 1) + u]
        for v in range(1, b):
            for u in range(a):
                ln += at_(u, v).distanceTo(at_(u + 1, v))
        for u in range(1, a):
            for v in range(b):
                ln += at_(u, v).distanceTo(at_(u, v + 1))
        return {'id': ids, 'newP': newP, 'len': ln}
    # the loop's own normal (Newell) and its long / short extent: a long narrow end gets a ladder of quads
    QP = [Pp(i) for i in Q]; nrm = V3()
    for i in range(m):
        p0 = QP[i]; p1 = QP[(i + 1) % m]
        nrm.x += (p0.y - p1.y) * (p0.z + p1.z); nrm.y += (p0.z - p1.z) * (p0.x + p1.x); nrm.z += (p0.x - p1.x) * (p0.y + p1.y)

    def ladder():
        def rung(r):
            t = 0; i = 0; j = m - 1
            while j - i >= 3:
                t += QP[(r + i + 1) % m].distanceTo(QP[(r + j - 1) % m]); i += 1; j -= 1
            return t
        br_ = 0; bl = math.inf
        for r in range(m):
            t = rung(r)
            if t < bl:
                bl = t; br_ = r
        Z = [Q[(br_ + k) % m] for k in range(m)]; i = 0; j = m - 1
        while j - i >= 3:
            faces.append([Z[i], Z[i + 1], Z[j - 1], Z[j]]); i += 1; j -= 1
        if j - i == 2:
            faces.append([Z[i], Z[i + 1], Z[j]])
        _br('capFill.ladder')
        return faces
    cen = centroidOf(QP); lo = math.inf; hi = 0
    for i in range(m):
        for k in range(i + 1, m):
            hi = js_max(hi, QP[i].distanceTo(QP[k]))
    for q in QP:
        lo = js_min(lo, q.distanceTo(cen))
    if _dec('capFill.narrow', hi, 5 * lo) > 5 * lo:
        return ladder()
    best = None
    for r in range(m):
        p = patch(r)
        if not best or p['len'] < best['len'] - 1e-12:
            best = p
    at2 = lambda u, v: pos[best['id'][v * (a + 1) + u]] if best['id'][v * (a + 1) + u] >= 0 else best['newP'][-1 - best['id'][v * (a + 1) + u]]
    for v in range(b):
        for u in range(a):                                          # a folded quad -> the ladder instead
            f = [at2(u, v), at2(u + 1, v), at2(u + 1, v + 1), at2(u, v + 1)]
            fn = V3().crossVectors(f[2].clone().sub(f[0]), f[3].clone().sub(f[1]))
            if _dec('capFill.fold', fn.dot(nrm), 0) <= 0:
                return ladder()
    base = len(pos)
    for c in best['newP']:
        pos.append(c)
    vi = lambda u, v: best['id'][v * (a + 1) + u] if best['id'][v * (a + 1) + u] >= 0 else base + (-1 - best['id'][v * (a + 1) + u])
    for v in range(b):
        for u in range(a):
            faces.append([vi(u, v), vi(u + 1, v), vi(u + 1, v + 1), vi(u, v + 1)])
    _br('capFill.patch')
    return faces


# A lock grid with its open ends capped: the grid with the bevel rows, faces + cap faces, capFaces; or None
def capGrid(g, p):
    # port of capGrid (hair-modeler.clean.html:8647)
    if not g or g.R < 2 or g.M < 3:
        return None
    R = g.R; Mc = g.M
    rows = [g.pos[k * Mc:(k + 1) * Mc] for k in range(R)]

    def spread(r):
        c = centroidOf(r); return js_max(*[q.distanceTo(c) for q in r])
    ref = js_max(*[spread(r) for r in rows], 1e-9)
    doS = p.get('start') is not False and spread(rows[0]) > 0.05 * ref; doE = p.get('end') is not False and spread(rows[R - 1]) > 0.05 * ref
    if not doS and not doE:
        return None
    R0 = len(rows)
    addS = 0; addE = 0
    if doE:
        r = bevelEnd(rows, p); addE = len(r) - R0; rows = r
    if doS:
        n0 = len(rows); r = bevelEnd(list(reversed(rows)), p)[::-1]; addS = len(r) - n0; rows = r
    R2 = len(rows); pos = [q.clone() for row in rows for q in row]; faces = []; capFaces = []
    bev = js_max(1, to_int32(nullish(p.get('segs'), 0))) if truthy(p.get('size')) and p.get('size') > 0 else 0   # quad rows of a bevel
    bevS = min(R2 - 1, bev) if doS else 0; bevE = min(R2 - 1, bev) if doE else 0
    for k in range(R2 - 1):
        for j in range(Mc):
            j1 = (j + 1) % Mc; f = [k * Mc + j, k * Mc + j1, (k + 1) * Mc + j1, (k + 1) * Mc + j]
            if g.flip:
                f.reverse()
            faces.append(f)
            if k < bevS or k >= R2 - 1 - bevE:
                capFaces.append(f)
    loop = lambda k: [k * Mc + j for j in range(Mc)]
    if doS:
        Lp = loop(0)
        if not g.flip:
            Lp.reverse()
        fs = capFill(Lp, pos, p.get('mode')); faces.extend(fs); capFaces.extend(fs); _br('capGrid.start')
    if doE:
        Lp = loop(R2 - 1)
        if g.flip:
            Lp.reverse()
        fs = capFill(Lp, pos, p.get('mode')); faces.extend(fs); capFaces.extend(fs); _br('capGrid.end')
    return Grid(pos=pos, faces=faces, R=R2, M=Mc, flip=g.flip, area=g.area, capFaces=capFaces, capAdded=addS + addE)


# Union of vertices (from `list_`) closer than tol whose pair passes ok(u, v); clusters move to their average.
# faces (optional): two clusters holding vertices of one face are never merged.
def weldClusters(list_, pos, tol, ok, faces=None):
    # port of weldClusters (hair-modeler.clean.html:9030)
    par = {v: v for v in list_}

    def fd(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    fs = {}
    if faces is not None:
        inL = set(list_)
        for i, f in enumerate(faces):
            for v in f:
                if v in inL:
                    fs.setdefault(v, {})[i] = True

    def disjoint(a, b):
        A = fs.get(a); B = fs.get(b)
        if not A or not B:
            return True
        small, big = (A, B) if len(A) < len(B) else (B, A)
        return not any(x in big for x in small)

    def join(a, b):
        par[a] = b
        if faces is not None:
            A = fs.get(a)
            if A:
                fs.setdefault(b, {}).update(A)
    grid = {}
    cell = lambda p: (math.floor(p.x / tol), math.floor(p.y / tol), math.floor(p.z / tol))
    for v in list_:
        grid.setdefault(cell(pos[v]), []).append(v)
    merged = 0
    for v in list_:
        x, y, z = cell(pos[v])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for u in grid.get((x + dx, y + dy, z + dz), []):
                        if u <= v or dec_near('weldClusters.dist', pos[u].distanceTo(pos[v]), tol) >= tol or not ok(u, v):
                            continue
                        a = fd(u); b = fd(v)
                        if a != b and (faces is None or disjoint(a, b)):
                            join(a, b); merged += 1
    cl = {}
    for v in list_:
        cl.setdefault(fd(v), []).append(v)
    for r, l in cl.items():
        if len(l) > 1:
            c = V3()
            for v in l:
                c.add(pos[v])
            pos[r] = c.divideScalar(len(l))
    return {'rep': lambda v: fd(v) if v in par else v, 'merged': merged}


def _ekey(a, b):
    return (a, b) if a <= b else (b, a)


def sewSeams(d, tol):
    # port of sewSeams (hair-modeler.clean.html:8876): boundary vertices welded within tol, duplicate faces dropped
    ec = {}
    for f in d.faces:
        for k, a in enumerate(f):
            key = _ekey(a, f[(k + 1) % len(f)]); ec[key] = ec.get(key, 0) + 1
    bv = {}
    for key, c in ec.items():
        if c == 1:
            bv[key[0]] = True; bv[key[1]] = True
    pos = [p.clone() for p in d.pos]
    w = weldClusters(list(bv), pos, tol, lambda u, v: True, d.faces)
    seen = set(); faces = []
    for f in d.faces:
        g0 = [w['rep'](i) for i in f]; g = [x for k, x in enumerate(g0) if x != g0[(k + len(g0) - 1) % len(g0)]]
        if len(g) < 3 or len(set(g)) != len(g):
            continue
        key = tuple(sorted(g))
        if key in seen:
            continue
        seen.add(key); faces.append(g)
    mp = {}; out = []
    nf = []
    for f in faces:
        g = []
        for i in f:
            if i not in mp:
                mp[i] = len(out); out.append(pos[i])
            g.append(mp[i])
        nf.append(g)
    _br('sewSeams')
    return M.MeshData(pos=out, faces=nf)


# Close boundary loops (skipping loops that touch `keepOpen` vertices) with a ladder of quads (+1 triangle when
# odd), starting where the rungs come out shortest. Winding follows the surrounding faces.
def fillHoles(faces, pos, keepOpen):
    # port of fillHoles (hair-modeler.clean.html:8918)
    cnt = {}; nxt = {}; bad = set()
    for f in faces:
        for k, a in enumerate(f):
            key = _ekey(a, f[(k + 1) % len(f)]); cnt[key] = cnt.get(key, 0) + 1
    for f in faces:
        for k, a in enumerate(f):
            b = f[(k + 1) % len(f)]
            if cnt.get(_ekey(a, b)) != 1:
                continue
            if b in nxt:
                bad.add(b)
            nxt[b] = a                                              # hole edge runs opposite to the face edge
    out = []; seen = set()
    for s in list(nxt):
        if s in seen:
            continue
        Lp = []; v = s
        while v is not None and v not in seen:
            seen.add(v); Lp.append(v); v = nxt.get(v)
        if v != s or len(Lp) < 3 or len(Lp) > 24 or any(i in keepOpen or i in bad for i in Lp):
            continue
        n = len(Lp)

        def rung(r):
            t = 0; i = 0; j = n - 1
            while j - i >= 3:
                t += pos[Lp[(r + i + 1) % n]].distanceTo(pos[Lp[(r + j - 1) % n]]); i += 1; j -= 1
            return t
        best = 0; bl = math.inf
        for r in range(n):
            t = rung(r)
            if t < bl:
                bl = t; best = r
        Q = [Lp[(best + k) % n] for k in range(n)]
        i = 0; j = n - 1
        while j - i >= 3:
            out.append([Q[i], Q[i + 1], Q[j - 1], Q[j]]); i += 1; j -= 1
        if j - i == 2:
            out.append([Q[i], Q[i + 1], Q[j]])
        _br('fillHoles.filled')
    return out


# Small holes (3…maxN open edges) whose vertices all pass `ok` — also at vertices where several holes meet.
def closeSmallHoles(faces, ok, maxN):
    # port of closeSmallHoles (hair-modeler.clean.html:8943)
    cnt = {}
    for f in faces:
        for k, a in enumerate(f):
            kk = _ekey(a, f[(k + 1) % len(f)]); cnt[kk] = cnt.get(kk, 0) + 1
    adj = {}; dir_ = set()                                          # open edges (undirected) + their face direction
    for f in faces:
        for k, a in enumerate(f):
            b = f[(k + 1) % len(f)]
            if cnt.get(_ekey(a, b)) != 1 or not ok(a) or not ok(b):
                continue
            dir_.add((a, b))
            adj.setdefault(a, []).append(b); adj.setdefault(b, []).append(a)
    used = set(); res = []

    def dfsL(s, path, lim):
        v = path[len(path) - 1]
        for w in adj.get(v, []):
            if _ekey(v, w) in used or (len(path) > 1 and w == path[len(path) - 2]):
                continue
            if w == s and len(path) >= 3:
                return path
            if w in path or len(path) >= lim:
                continue
            r = dfsL(s, path + [w], lim)
            if r:
                return r
        return None

    def dfs(s, path):                                               # shortest cycles first (iterative deepening)
        for lim in range(3, maxN + 1):
            r = dfsL(s, path, lim)
            if r:
                return r
        return None
    for s in list(adj):
        found = dfs(s, [s])
        while found:
            for k, v in enumerate(found):
                used.add(_ekey(v, found[(k + 1) % len(found)]))
            if (found[0], found[1]) in dir_:
                found.reverse()                                     # a hole runs opposite to its neighbours' edges
            n = len(found); i = 0; j = n - 1                        # a ladder of quads (+1 triangle)
            while j - i >= 3:
                res.append([found[i], found[i + 1], found[j - 1], found[j]]); i += 1; j -= 1
            if j - i == 2:
                res.append([found[i], found[i + 1], found[j]])
            found = dfs(s, [s])
    # cracks left at folded corners: an open edge u–v whose ends are both joined to one vertex w -> triangle
    all_ = {}
    for f in faces:
        for k, a in enumerate(f):
            b = f[(k + 1) % len(f)]
            all_.setdefault(a, {})[b] = True; all_.setdefault(b, {})[a] = True
    tri = {tuple(sorted(f)) for f in faces if len(f) == 3}
    for u, v in dir_:
        if _ekey(u, v) in used:
            continue
        best = None; bd = math.inf
        for w in all_.get(u, {}):
            if w != v and v in all_ and w in all_[v] and tuple(sorted((u, v, w))) not in tri:
                s = 0 if len(adj.get(w, [])) else 1
                if s < bd:
                    bd = s; best = w
        if best is None:
            continue
        used.add(_ekey(u, v)); tri.add(tuple(sorted((u, v, best)))); res.append([v, u, best])
        for x in (u, v):
            if x in adj and best in adj and x in adj[best]:
                used.add(_ekey(x, best))
    return res


# Where is p relative to a closed lock (rows × columns grid)? Returns { inside, dist, n }.
def lockQuery(G, p):
    # port of lockQuery (hair-modeler.clean.html:8992)
    pos = G.pos; R = G.R; Mc = G.M
    if G.loops is None:
        G.loops = []
        for k in range(R):
            pts = [pos[k * Mc + j] for j in range(Mc)]
            c = V3()
            for q in pts:
                c.add(q)
            c.divideScalar(Mc)
            n = V3()
            for j in range(Mc):
                a = pts[j]; b = pts[(j + 1) % Mc]
                n.x += (a.y - b.y) * (a.z + b.z); n.y += (a.z - b.z) * (a.x + b.x); n.z += (a.x - b.x) * (a.y + b.y)
            G.loops.append({'pts': pts, 'c': c, 'n': n.normalize() if n.lengthSq() > 1e-16 else None})
        # the (centre, normal) of every loop pair as scalars for the test below, which runs for every pair on every query
        G.pairs = []
        for k in range(R - 1):
            A = G.loops[k]; B = G.loops[k + 1]
            nA = A['n'] or B['n']; nB = B['n'] or A['n']
            G.pairs.append(None if not nA else (A['c'].x, A['c'].y, A['c'].z, nA.x, nA.y, nA.z, B['c'].x, B['c'].y, B['c'].z, nB.x, nB.y, nB.z, nA, nB))
    Ls = G.loops; best = {'inside': False, 'dist': math.inf, 'n': None}
    p_x = p.x; p_y = p.y; p_z = p.z                                 # (px / py are reused below for the 2-D projection)
    for k in range(R - 1):
        pr = G.pairs[k]
        if pr is None:
            continue
        # da = p.clone().sub(A.c).dot(nA), db likewise, on scalars; the loop pair is used only when p lies between its planes
        da = (p_x - pr[0]) * pr[3] + (p_y - pr[1]) * pr[4] + (p_z - pr[2]) * pr[5]
        db = (p_x - pr[6]) * pr[9] + (p_y - pr[7]) * pr[10] + (p_z - pr[8]) * pr[11]
        if da * db > 0:
            continue
        A = Ls[k]; B = Ls[k + 1]; nA = pr[12]; nB = pr[13]
        t = da / (da - db) if abs(da - db) > 1e-12 else 0.5
        n = nA.clone().lerp(nB, t).normalize(); c = A['c'].clone().lerp(B['c'], t)
        u = V3(1, 0, 0)
        if abs(n.x) > 0.9:
            u.set(0, 1, 0)
        u.addScaledVector(n, -u.dot(n)).normalize(); v = V3().crossVectors(n, u)
        Pp = []
        for j, q in enumerate(A['pts']):
            w = q.clone().lerp(B['pts'][j], t).sub(c); Pp.append([w.dot(u), w.dot(v)])
        area = 0; j = Mc - 1
        for i in range(Mc):
            area += Pp[j][0] * Pp[i][1] - Pp[i][0] * Pp[j][1]; j = i
        if abs(area) < 1e-14:
            continue
        x = p.clone().sub(c); px = x.dot(u); py = x.dot(v)
        inside = False; dmin = math.inf; en = None; j = Mc - 1
        for i in range(Mc):
            xi, yi = Pp[i]; xj, yj = Pp[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
            ex = xi - xj; ey = yi - yj; l2 = ex * ex + ey * ey; s = clamp(((px - xj) * ex + (py - yj) * ey) / l2, 0, 1) if l2 > 0 else 0
            d = js_hypot(xj + ex * s - px, yj + ey * s - py)
            if d < dmin and l2 > 0:
                dmin = d; sg = 1 if area > 0 else -1; en = [ey * sg, -ex * sg]   # outward edge normal
            j = i
        n3 = u.clone().multiplyScalar(en[0]).addScaledVector(v, en[1]).normalize() if en else None
        if (inside and not best['inside']) or (inside == best['inside'] and dmin < best['dist']):
            best = {'inside': inside, 'dist': dmin, 'n': n3}
    return best


# Merge shared trunks (only between locks of one converted group that run along welded curves); the original's
# comment block at 9050-9056 describes the three steps.
def mergeTrunks(P, parts, grids, lockCurves, h, dbg=None):
    # port of mergeTrunks (hair-modeler.clean.html:9057)
    dbg = dbg if dbg is not None else {'mergeOpts': {}, 'mergeWhy': {}}
    opts = dbg.setdefault('mergeOpts', {})
    info = WeldInfo()
    m = mergeWeld(parts, h * 0.06, info)
    NP = len(parts)
    why = {'gapWelds': 0, 'identical': 0, 'inside': 0, 'snapped': 0, 'filled': 0}; dbg['mergeWhy'] = why
    # locks connected through welded anchors, and sample points of their welded runs (the trunk)
    aLocks = {}
    for li, cs in enumerate(lockCurves):
        for c in cs:
            for a in c.anchors:
                aLocks.setdefault(a, {})[li] = True
    conn = [{} for _ in range(NP)]; trunk = [[] for _ in range(NP)]
    for li, cs in enumerate(lockCurves):
        for c in cs:
            for k in range(len(c.anchors) - 1):
                A = aLocks[c.anchors[k]]; B = aLocks[c.anchors[k + 1]]
                other = [x for x in A if x != li and x in B]
                if not other:
                    continue
                for o in other:
                    conn[li][o] = True; conn[o][li] = True
                for s in range(0, 25, 4):
                    q = at(c.samples, min(k * 24 + s, len(c.samples) - 1))
                    if q:
                        trunk[li].append(q.p)
    lockRad = []
    for G in grids:
        if not G:
            lockRad.append(h); continue
        r = 0
        for k in range(G.R):
            c = V3()
            for j in range(G.M):
                c.add(G.pos[k * G.M + j])
            c.divideScalar(G.M)
            for j in range(G.M):
                r = js_max(r, G.pos[k * G.M + j].distanceTo(c))
        lockRad.append(r)

    def nearTrunk(li, p):
        Rr = (lockRad[li] + h) * 1.5
        for q in trunk[li]:
            if q.distanceTo(p) < Rr:
                return True
        return False
    linked = lambda a, b: any(any(x != y and y in conn[x] for y in info.vsrc.get(b, {})) for x in info.vsrc.get(a, {}))
    # junction curves (Unite locks): open edges along them are seams between the united locks, not lock ends
    jPts = []
    for cs in lockCurves:
        for c in cs:
            if c.junc:
                jPts.extend(juncLine(P, c))
    # lock ends (roots, tips) stay protected even near a junction: around every curve end, 0.6 × the gap to the next end
    ends = []
    for cs in lockCurves:
        E = [a.pos for c in cs for a in (at(c.anchors, 0), at(c.anchors, len(c.anchors) - 1)) if a]
        for p in E:
            d = math.inf
            for q in E:
                x = p.distanceTo(q)
                if 1e-6 < x < d:
                    d = x
            ends.append((p, 0.6 * d if math.isfinite(d) else h))
    nearJunc = lambda p: any(q.distanceToSquared(p) < 6.25 * h * h for q in jPts) and not any(q.distanceTo(p) < r for q, r in ends)
    # vertices on the open ends of their own lock (roots, open tips) — never welded across or closed
    wasOpen = set()
    for di, d in enumerate(parts):
        ec = {}
        for f in d.faces:
            for k, a in enumerate(f):
                key = _ekey(a, f[(k + 1) % len(f)]); ec[key] = ec.get(key, 0) + 1
        for key, c in ec.items():
            if c == 1:
                for i in key:
                    j = info.vmap[di].get(i)
                    if j is not None and not (jPts and nearJunc(m.pos[j])):
                        wasOpen.add(j)
    F = [{'v': v, 'src': info.fsrc[i]} for i, v in enumerate(m.faces)]

    def remap(rep):
        nonlocal F, wasOpen
        nf = []
        for f in F:
            g0 = [rep(i) for i in f['v']]; g = [x for k, x in enumerate(g0) if x != g0[(k + len(g0) - 1) % len(g0)]]
            if len(g) >= 3 and len(set(g)) == len(g):
                nf.append({'v': g, 'src': f['src']})
        F = nf
        vs = {}
        for v, s in info.vsrc.items():
            if not s:
                continue
            r = rep(v); vs.setdefault(r, {}).update(s)
        info.vsrc = vs
        wasOpen = {rep(v) for v in wasOpen}
    # 1) small gaps along the trunk: vertices of connected locks closer than ¼ step become one
    cand = [v for v, p in enumerate(m.pos) if (s := info.vsrc.get(v)) and any(len(conn[li]) and nearTrunk(li, p) for li in s)]
    gw = weldClusters(cand, m.pos, h * nullish(opts.get('gap'), 0.25), linked, [f['v'] for f in F])
    why['gapWelds'] = gw['merged']; remap(gw['rep'])
    # 2) identical faces, then trunk faces inside a connected lock
    same = {}
    for i, f in enumerate(F):
        same.setdefault(tuple(sorted(f['v'])), []).append(i)
    drop = {}
    for l in same.values():
        if len(l) < 2 or opts.get('identical') is False:
            continue
        ns = [faceNormal(m.pos, F[i]['v']) for i in l]; used = set()
        for x in range(len(l)):
            for y in range(x + 1, len(l)):
                if x not in used and y not in used and ns[x].dot(ns[y]) < 0:
                    used.add(x); used.add(y); drop[l[x]] = True; drop[l[y]] = True
        for i in [i for x, i in enumerate(l) if x not in used][1:]:
            drop[i] = True
    why['identical'] = len(drop)
    shared = lambda i: i in info.vsrc and len(info.vsrc[i]) > 1
    tolIn = h * 0.02
    for fi, f in enumerate(F):
        if opts.get('inside') is False or fi in drop or sum(1 for v in f['v'] if shared(v)) < 2 or not len(conn[f['src']]):
            continue
        c = faceCenter(m.pos, f['v'])
        for lj in conn[f['src']]:
            G = grids[lj]
            if not G:
                continue
            q = lockQuery(G, c)
            if q['inside'] and _dec('mergeTrunks.insideDist', q['dist'], tolIn) > tolIn:
                drop[fi] = True; why['inside'] += 1; break
    # never open the surface: a face whose removal would leave a kept neighbour with a free edge stays
    if opts.get('keepClosed') is not False:
        changed = True
        while changed:
            changed = False
            cnt = {}
            for i, f in enumerate(F):
                if i not in drop:
                    for k, a in enumerate(f['v']):
                        key = _ekey(a, f['v'][(k + 1) % len(f['v'])]); cnt[key] = cnt.get(key, 0) + 1
            for i in list(drop):
                f = F[i]
                if any(cnt.get(_ekey(a, f['v'][(k + 1) % len(f['v'])])) == 1 for k, a in enumerate(f['v'])):
                    del drop[i]; why['kept'] = why.get('kept', 0) + 1; changed = True
    F = [f for i, f in enumerate(F) if i not in drop]
    # 3) open edges across small gaps between connected locks are welded, remaining holes get quads
    ec = {}
    for f in F:
        for k, a in enumerate(f['v']):
            key = _ekey(a, f['v'][(k + 1) % len(f['v'])]); ec[key] = ec.get(key, 0) + 1
    bv = {}
    for key, c in ec.items():
        if c == 1:
            for i in key:
                if i not in wasOpen:
                    bv[i] = True
    if opts.get('snap') is not False:
        sn = weldClusters(list(bv), m.pos, h * 0.35, linked, [f['v'] for f in F]); why['snapped'] = sn['merged']; remap(sn['rep'])
    if jPts and opts.get('snap') is not False:                      # along junction lines the locks' loops land further apart
        ec2 = {}
        for f in F:
            for k, a in enumerate(f['v']):
                key = _ekey(a, f['v'][(k + 1) % len(f['v'])]); ec2[key] = ec2.get(key, 0) + 1
        jv = {}
        for key, c in ec2.items():
            if c == 1:
                for i in key:
                    if i not in wasOpen and nearJunc(m.pos[i]):
                        jv[i] = True
        sn = weldClusters(list(jv), m.pos, h * 0.55, linked, [f['v'] for f in F]); why['snapped'] += sn['merged']; remap(sn['rep'])
    seen = set(); faces = []
    for f in F:
        k = tuple(sorted(f['v']))
        if k in seen:
            continue
        seen.add(k); faces.append(f['v'])
    fill = fillHoles(faces, m.pos, wasOpen); why['filled'] = len(fill)
    faces.extend(fill)
    if jPts:
        sm = closeSmallHoles(faces, lambda i: i not in wasOpen and nearJunc(m.pos[i]), 6); why['filled'] += len(sm); faces.extend(sm)
    mp = {}; pos = []
    nf = []
    for f in faces:
        g = []
        for i in f:
            if i not in mp:
                mp[i] = len(pos); pos.append(m.pos[i])
            g.append(mp[i])
        nf.append(g)
    return M.MeshData(pos=pos, faces=nf)


# Every lock is welded within itself (its walls, its tip). Neighbouring locks are not stitched — they get matching
# loops instead. Exception ("Merge shared trunks"): where locks run along welded curves, their geometry coincides.
def buildGroupMesh(P, surfs, T, dbg=None):
    # port of buildGroupMesh (hair-modeler.clean.html:8898); T must come from buildRowTables (computed once per
    # project, where the original recomputes it per call — same table)
    parts = []; grids = []; lockCurves = []
    for walls in lockGroups(wallsOf(surfs)):
        CP = convOf(P, P.items.get(walls[0].a) or walls[0]); hL = levelH_of(P, CP)
        g = lockGrid(P, walls, T); eps = hL * 0.04
        if g and CP.get('tipMode') == 'gradual':
            g = taperTip(g)
        cap = capOf(P, walls[0]) if g else None                      # Cap modifier of the group: open ends closed
        if cap:
            g = capGrid(g, cap.p) or g
        part = mergeWeld([g], eps) if g else mergeWeld([d for d in (surfaceData(P, s, T) for s in walls) if d], eps)
        if not g and any(s.p.get('jfade') is not None for s in walls):
            part = sewSeams(part, hL * 0.4)                         # a lock with junction walls
        parts.append(part); grids.append(g)
        lockCurves.append([c for c in (P.items.get(i) for i in dict.fromkeys(x for s in walls for x in (s.a, s.b))) if c and len(c.samples)])
    CP = convOf(P, surfs[0]) if surfs else settings_conv(P)
    if truthy(CP.get('mergeTrunks')) and len(parts) > 1:
        _br('buildGroupMesh.mergeTrunks')
        return mergeTrunks(P, parts, grids, lockCurves, levelH_of(P, CP), dbg)
    return concatMeshes(parts)


def mergeUnits(P, surfs, rootId):
    # port of mergeUnits (hair-modeler.clean.html:9312): `container` labelled groups are converted each on its own
    units = {}
    for s in surfs:
        chain = []; p = P.parentOf(s)
        while p:
            chain.insert(0, p)
            if p.id == rootId:
                break
            p = P.parentOf(p)
        key = 'all'
        for i, g in enumerate(chain):
            if g.labels and g.labels.get('container'):
                key = chain[i + 1].id if i + 1 < len(chain) else str(g.id) + ':own'
        # (exclZone is always null since 10.10)
        units.setdefault(key, []).append(s)
    return list(units.values())


def symOf(P, it):
    # port of symOf (hair-modeler.clean.html:9153): nearest group (itself or an ancestor) with symmetry on
    p = it if it.type == 'group' else P.parentOf(it)
    while p:
        if getattr(p, 'sym', None) and p.sym.get('on'):
            return p.sym
        p = P.parentOf(p)
    return None


def mirrorPt(P, p, axis):
    # port of mirrorPt (9157)
    q = p.clone(); setattr(q, axis, 2 * getattr(P.S.center, axis) - getattr(q, axis)); return q


def mirrorData(P, data, axis):
    # port of mirrorData (hair-modeler.clean.html:9184): the mirrored half as a separate piece
    n = len(data.pos)
    return M.MeshData(pos=[*data.pos, *(mirrorPt(P, p, axis) for p in data.pos)], faces=[*data.faces, *([i + n for i in f][::-1] for f in data.faces)])


def buildConvMesh(P, mesh, T, dbg=None, crown_data=None):
    # port of buildConvMesh (hair-modeler.clean.html:9323); crown_data(P, k) -> MeshData | None (crown.py, subphase e)
    surfs = [s for s in (P.items.get(i) for i in mesh.srcSurfs) if s]
    units = mergeUnits(P, surfs, mesh.parent)
    if not surfs:
        mesh.data = M.MeshData()
    elif len(units) > 1:
        _br('buildConvMesh.units')
        mesh.data = concatMeshes([buildGroupMesh(P, u, T, dbg) for u in units])
    else:
        mesh.data = buildGroupMesh(P, surfs, T, dbg)
    crs = [k for k in (P.items.get(i) for i in (mesh.srcCrowns or [])) if k]
    if crs:
        if crown_data is None:
            raise NotImplementedError('crownData is ported in subphase (e)')
        pieces = [d for d in (crown_data(P, k) for k in crs) if d]   # crowns: their own piece each
        if pieces:
            mesh.data = concatMeshes([mesh.data, *pieces])
    g = P.items.get(mesh.parent); sym = symOf(P, g) if g else None
    mesh.symBaked = bool(sym)
    if sym:
        mesh.data = mirrorData(P, mesh.data, sym['axis'])           # mirrored half is a separate piece (no auto stitching)
    return mesh


def fillConvs(P, mesh):
    """ours (3.7): what buildConvMesh (9998) does to the model besides the mesh — convOf (1596) on the first wall of
    every lock of every merge unit and on the unit's first wall, as buildGroupMesh does: groupConv fills the conv of
    those groups (and may bump gen). For a mesh whose data is reused instead of built (conversion.startConversion), so
    the project comes out as the original's."""
    surfs = [s for s in (P.items.get(i) for i in mesh.srcSurfs) if s]
    if not surfs:
        return
    units = mergeUnits(P, surfs, mesh.parent)
    for u in (units if len(units) > 1 else [surfs]):
        for walls in lockGroups(wallsOf(u)):
            convOf(P, P.items.get(walls[0].a) or walls[0])
        convOf(P, u[0])


def conversionPlan(P, gids=None):
    """ours (U7 A, docs/fase-3-ux-revisao.md): the meshes Convert makes, without building any — the loop of
    startConversion (10010) on ids alone: [(group, walls, crowns)] for each ticked group whose collect (9133) finds a
    wall or a crown, in the order convert_groups makes them (tests: the same names)"""
    gids = [i for i in (gids if gids is not None else P.settings.convChecked) if i in P.items]
    checked = set(gids)
    out = []
    for gid in gids:
        surfs = collect(P, gid, checked, ['surface']); crs = collect(P, gid, checked, ['crown'])
        if surfs or crs:
            out.append((P.items[gid], len(surfs), len(crs)))
    return out


def meshPieces(P, g):
    """ours (U7 A): how the walls of g's mesh come out, with the current ticks — (pieces, carried): the units
    mergeUnits (9987) makes of what collect (9133) finds (1: one piece; more: a Container on the way), and whether
    some of those walls lie in a sub-group (they came with g). Ids only, nothing built."""
    checked = {i for i in P.settings.convChecked if i in P.items}
    surfs = collect(P, g.id, checked, ['surface'])
    if not surfs:
        return 0, False
    return len(mergeUnits(P, surfs, g.id)), any(P.parentOf(s) is not g for s in surfs)


def convert_groups(P, T, gids=None, dbg_per_mesh=None, crown_data=None, register=False):
    """port of startConversion (hair-modeler.clean.html:9335), minus UI: one mesh per ticked group (default: the
    project's convChecked), groups whose own content has no surface or crown skipped. Returns the MeshItems; with
    register=True they are added to the project like addItem does (pending until confirmed)."""
    gids = [i for i in (gids if gids is not None else P.settings.convChecked) if i in P.items]
    checked = set(gids)
    for c in P.curves():
        if c.dirty:
            rebuildCurve(P, c)
    meshes = []
    for gid in gids:
        surfs = collect(P, gid, checked, ['surface']); crs = collect(P, gid, checked, ['crown'])
        if not surfs and not crs:
            continue
        mesh = M.MeshItem(id=P.uid, type='mesh', name=P.items[gid].name + ' mesh', parent=gid, pending=True, srcSurfs=[s.id for s in surfs], srcCrowns=[k.id for k in crs])
        P.uid += 1
        dbg = {'mergeOpts': {}, 'mergeWhy': {}}
        buildConvMesh(P, mesh, T, dbg, crown_data)
        if dbg_per_mesh is not None:
            dbg_per_mesh[mesh.id] = dbg
        if register:
            P.items[mesh.id] = mesh; P.childrenOf(mesh.parent).append(mesh.id)
        meshes.append(mesh)
    return meshes
