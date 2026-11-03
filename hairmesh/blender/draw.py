# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""What the tool draws over the 3D View while it is active: the curves (polylines of the core's samples, 24 per
segment) and the anchors (a circle; a diamond for the first anchor of an open curve) in the original's colors,
in two passes like its renderer (renderViewport, 12022): depth-tested first, then again without depth test at
the project's x-ray opacity (S.xray) — or, with Blender's own X-ray of that 3D View on (Alt+Z; 3.8c), in one pass
without depth test. The selection: selected curves thicker and drawn over the others, selected anchors red and the
size of the others, the last one ringed (3.8c, 3.8e; in the colors of the preferences: sel_colors; the original's
are orange and red, rebuildAnchors 1403-1406, syncVisuals 11958); the curve an anchor slides along cyan. The curves are
pulled in front of the wall edges they lie on (CURVE_OFFSET, 3.8e). The guides (part 3 of the lot of 21/10/2026) as the original shows them: a line guide's green line,
a disc's annulus between its two rings, its rings and its cut lines across the walls (guide_geometry), each by its
eye, its groups' eyes and its group's mode — the ⏻ never hides one. A second, 2D callback draws the box of a marquee
and the circle of the Circle Select. Nothing here is a Blender object. Every thickness and point size goes through
pick.line_scale (G11): the UI scale once and the Line Width preference; the sizes below are at 100 %.

The geometry assembly (`geometry`) is pure and cached per root; gpu is touched only inside the draw callbacks,
because it is unavailable in background mode. Anchors use the original's point shader (anchorMat, 1344): the dot
is pushed 10 px towards the camera along the view ray so its own wall does not hide it."""
from types import SimpleNamespace

import bpy
from mathutils import Vector

from ..core import edit, groups as G
from ..core.curves import rebuild_dirty
from . import errorlog, live, owner, pick, select, session, slow, tool
from .coords import to_blender

# the selection (3.8c, the owner's request — ours, docs/features-adicionadas.md): the original's orange selected curve
# (0xff9a2e) and orange / red anchors (1404-1405) were lost among the others; a strong lime green instead, apart from
# the guides' green (0x3fe07a) and the gizmo's Y (0x8adb00): a selected curve thicker and drawn over the others, every
# selected anchor bigger with a thicker dark rim (until 3.8e), the last one with a white ring around it. Editable in the add-on's
# preferences (sel_colors); these are the defaults. 3.8e (the owner's request): the selected anchors pure red (0xff0000)
# and the size of the others — the red is enough —, apart from the gizmo's pink X (0xff3352) and the Scissors' salmon
# (0xff5a5a); the curve stays lime.
SEL_DEFAULT = {'curve': (0xb4 / 255.0, 1.0, 0x1e / 255.0), 'anchor': (1.0, 0.0, 0.0), 'mark': (1.0, 1.0, 1.0)}   # 0xb4ff1e, 0xff0000, the white ring
# the original's colours: anchors CA (1370), curve lines (syncVisuals, 11958), x-ray (S.xray); 'sel' / 'last': ours (3.8c)
CA = {'normal': (1.0, 0.82, 0.29), 'shared': (0.23, 0.84, 1.0), 'sel': SEL_DEFAULT['anchor'], 'last': SEL_DEFAULT['anchor'],
      'weld': (1.0, 0.2, 1.0), 'ring': (0.45, 0.95, 0.6), 'att': (0.75, 0.5, 1.0)}
CURVE = (0xdd / 255.0, 0xdd / 255.0, 0xdd / 255.0)                  # 0xdddddd
CURVE_SEL = SEL_DEFAULT['curve']
SEL_LINE_PX = 3.5                                                   # a selected curve (ours, 3.8c; the others: LINE_PX)
SEL_THROUGH = 0.85                                                  # the selected curves and anchors behind a wall or the head: drawn again at this opacity (3.8c)
CURVE_SNAP = (0x4c / 255.0, 0xc9 / 255.0, 0xf0 / 255.0)             # 0x4cc9f0: the curve an anchor slides along (S.snapCurve)
WHITE = (1.0, 1.0, 1.0)
ANCHOR_PX = 9.0                                                     # dot diameter in CSS px (rebuildAnchors: s = 9; a start is 1.35 x)
SEL_PX = ANCHOR_PX                                                  # a selected anchor (1404: 11; 3.8c: 13 with its rim; 3.8e: the size of the others)
LAST_PX = ANCHOR_PX / 0.66                                          # the last selected one: its dot and rim as big as the others' (r < 0.33 in the shader), the white ring outside them (~2 px)
START_SCALE = 1.35
LINE_PX = 2.0                                                       # newLineObj(0xdddddd, 2, 10)
OUTLINE = 0.06                                                      # the dark rim of a dot (fragment shader)
# a selected wall (3.5): its grid tinted in the selection orange over the live mesh (syncVisuals tints the surface
# material of a selected item the same way, 11957), the quad edges on top, WALL_LINE_PX wide (times pick.line_scale).
# Both are drawn with the built-in shaders the curves use (UNIFORM_COLOR, POLYLINE_UNIFORM_COLOR: a width on every
# backend, smoothed edges) under a projection pulled towards the camera by Blender's own polygon offset
# (offset_projection, the port of GPU_polygon_offset_calc): the wireframe overlay of the live mesh pulls its 1 px
# wire by half a unit of that offset, the walls get WALL_OFFSET units, so the tint and the edges read over the mesh
# and over its wire wherever the wall is in front (no z-fighting, no grey wire inside the orange lines: 1b of the
# manual tests of 15/10/2026), and the head still hides a wall behind it. The custom wide-line shader of the first
# attempt (quads built in the vertex shader) had no smoothing and a smaller push, so it serrated and lost to the wire.
WALL_FILL = (1.0, 0x9a / 255.0, 0x2e / 255.0, 0.28)
WALL_LINE = (1.0, 0x9a / 255.0, 0x2e / 255.0, 0.95)
WALL_LINE_PX = 3.0
WALL_OFFSET = 2.0                                                   # units of polygon offset (the wire overlay: 1.0, halved in its shader)
# 3.8e (the owner's print: Bang_01): a wall's edges along a curve — the live mesh's grey wire, a selected wall's orange
# edges — are pulled in front of the curve by their offsets and fought with it (a thin flickering line beside it); the
# curves get more (CURVE_OFFSET > WALL_OFFSET), so in the depth-tested pass they are always over the edges they lie on.
# Only the drawing: no mesh, no wall and no bit of the model changes.
CURVE_OFFSET = WALL_OFFSET + 1.0
# the guides (syncVisuals 11946-11979; attachGuideObj 1061, rebuildGuide 1134, rebuildDisc 1147)
GUIDE = (0x3f / 255.0, 0xe0 / 255.0, 0x7a / 255.0)                  # 0x3fe07a: a guide line, a disc's cut lines
GUIDE_SEL = (1.0, 0xe2 / 255.0, 0x5a / 255.0)                       # 0xffe25a: selected (the rings of a selected disc too)
RING = (0x8f / 255.0, 0xe8 / 255.0, 0xb0 / 255.0)                   # 0x8fe8b0: the two rings of a disc
DISC_FILL = (0x6f / 255.0, 0xdc / 255.0, 0x9a / 255.0)              # 0x6fdc9a, opacity 0.16 (selected: 0xffe27a, 0.3) x the guides' opacity
DISC_FILL_SEL = (1.0, 0xe2 / 255.0, 0x7a / 255.0)
GUIDE_PX = {'line': (3.0, 4.0), 'cut': (4.0, 5.0), 'ring': (1.5, 2.5)}   # linewidth: plain, selected
DISC_NR = 96                                                        # rebuildDisc's resampling of the rings (1149)


def hex_rgb(s):
    """'#rrggbb' -> (r, g, b) in 0..1; anything else -> None"""
    if isinstance(s, str) and len(s) == 7 and s[0] == '#':
        try:
            return tuple(int(s[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        except ValueError:
            return None
    return None


def lerp3(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def curve_color(P, c):
    # syncVisuals (11228): the group's tint mixed 35 % towards white, else light grey (selection colours: step 3.3)
    t = hex_rgb(edit.tintOf(P, c))
    return lerp3(t, WHITE, 0.35) if t else CURVE


def wall_geometry(grids, matrix):
    """The selected walls' grids (MeshData, the model space) as world-space triangles and edges (pure):
    {'tris': flat list of points, 3 per triangle; 'lines': flat list of points, 2 per edge}."""
    tris = []; lines = []
    for d in grids:
        if d is None:
            continue
        pts = [tuple(matrix @ Vector(to_blender(p))) for p in d.pos]
        for f in d.faces:
            for k in range(1, len(f) - 1):
                tris.extend((pts[f[0]], pts[f[k]], pts[f[k + 1]]))
            for k in range(len(f)):
                lines.extend((pts[f[k]], pts[f[(k + 1) % len(f)]]))
    return {'tris': tris, 'lines': lines}


def polygon_offset(proj, view_distance, dist=1.0):
    """port of GPU_polygon_offset_calc (gpu_matrix.cc): the clip-space z Blender takes off a projection matrix's
    translation term (proj[2][3] in mathutils' row-major indexing, winmat[3][2] in C) to pull geometry `dist` units
    of polygon offset towards the camera; what the overlay engine puts in ndc_offset_factor (dist 1.0) and the
    wireframe shader halves. `proj` is indexed [row][col]; orthographic when proj[3][3] > 0.5 (then the offset
    scales with the view distance)."""
    dist *= 0.5                                                     # "a factor of 2 more offset than 2.79": corrected there
    if proj[3][3] > 0.5:
        return 0.00001 * dist * view_distance
    return proj[2][3] * -0.0025 * dist                              # about 0.25 % of the depth per unit, as Blender's comment says


def offset_projection(proj, rv3d, dist=1.0):
    """A copy of the projection matrix with the polygon offset applied, for a region's view (View::OffsetData): the
    view distance is rv3d.view_distance, or 1 / the camera zoom in an orthographic camera view."""
    vd = rv3d.view_distance
    if rv3d.view_perspective == 'CAMERA' and not rv3d.is_perspective:
        vd = 1.0 / max(abs(proj[0][0]), abs(proj[1][1]))
    m = proj.copy()
    m[2][3] -= polygon_offset(proj, vd, dist)
    return m


def pull_projection(proj, d, depth):
    """A copy of a projection matrix that draws anything `d` world units nearer the camera, for a thing `depth` away from
    it (a perspective's depth is not linear: the translation term that gives that pull at `depth`; an orthographic's is, and
    `depth` does not count). Pure; `proj` is indexed [row][col]."""
    m = proj.copy()
    if proj[3][3] > 0.5:
        m[2][3] += proj[2][2] * d
    else:
        d = min(d, 0.9 * depth)                                     # (the camera inside the tube: never past the near plane)
        m[2][3] += proj[2][3] * d / (depth - d)
    return m


def tube_reach(pos, samples):
    """Pure (numpy): how far a surface gets from the curve it follows: the largest, over its vertices `pos`, of the distance
    to the nearest of `samples`"""
    import numpy as np
    V = np.asarray(pos, dtype=np.float64).reshape(-1, 3); S = np.asarray(samples, dtype=np.float64).reshape(-1, 3)
    if not len(V) or not len(S):
        return 0.0
    near = np.full(len(V), np.inf)
    for s in S:
        d = V - s
        near = np.minimum(near, (d * d).sum(axis=1))
    return float(np.sqrt(near.max()))


def tube_pulls(P, tubes, matrix):
    """{curve id: the world distance that brings the curve out of its own Profile modifier's surface}, of `tubes`
    (live.tube_shown). Ours (lot 4.8b, the owner's decision): a curve inside its modifier was out of sight and out of reach;
    drawn that much nearer the camera it shows over that surface, and the head further away than that still covers it."""
    scale = max(abs(v) for v in matrix.to_scale())
    out = {}
    for tid, data in tubes.items():
        c = P.items.get(P.items[tid].src)
        if c is None:
            continue
        key = (id(data['pos']), c.sample_key if c.sample_key is not None else id(c.samples))
        r = _reach_cache.get(tid)
        if r is None or r[0] != key:
            r = (key, tube_reach([to_blender(q) for q in data['pos']], [to_blender(s.p) for s in c.samples]), data['pos'])   # (the list kept: its id stays its own)
            _reach_cache[tid] = r
        out[c.id] = max(out.get(c.id, 0.0), r[1] * scale * OVER_MARGIN)
    for tid in [i for i in _reach_cache if i not in tubes]:
        del _reach_cache[tid]
    return out


def guide_geometry(P, matrix, guides, sel_items=()):
    """The guides the viewport shows, in Blender world space (pure). `guides` = groups.shownGuides (visible and shown
    by the group's mode; the ⏻ hides nothing). A line guide: the polyline through its hits (pointOnCurve of each,
    rebuildGuide 1134). A disc: its two rings (the ring curves follow their disc, 11946), the annulus between them
    resampled at DISC_NR + 1 points (rebuildDisc 1147), and the cut line across every wall whose two curves both
    pierce it (the hitPts of the last rebuildDisc; none before it). Colors and widths of syncVisuals (11946, 11970):
    selected yellow. Returns {'strokes': {(width px, (r, g, b, a)): [points, 2 per segment]},
    'fills': [((r, g, b, a), [points, 3 per triangle])]}."""
    from ..core.geom import pointOnCurve, resample
    from ..core.guides import discRings
    sel = set(sel_items)
    op = P.settings.guideOpacity if P.settings.guideOpacity is not None else 1.0
    strokes = {}; fills = []

    def w(p):
        return tuple(matrix @ Vector(to_blender(p)))

    def stroke(kind, selected, colour, pts):
        key = (GUIDE_PX[kind][1 if selected else 0], (*(GUIDE_SEL if selected else colour), op))
        strokes.setdefault(key, []).extend(pts)

    def polyline(pts):
        out = []
        for i in range(len(pts) - 1):
            out.append(pts[i]); out.append(pts[i + 1])
        return out
    for g in guides:
        s = g.id in sel
        if g.kind != 'disc':
            pts = [q for q in (pointOnCurve(P.items.get(h.get('c')), h.get('t', 0)) for h in g.hits) if q is not None]
            if len(pts) >= 2:
                stroke('line', s, GUIDE, polyline([w(q) for q in pts]))
            continue
        rs = discRings(P, g)
        for c in rs:
            if len(c.samples) >= 2:                                 # (4.2c, item 5, ours: a ring selected by itself is yellow too)
                stroke('ring', s or c.id in sel, RING, polyline([w(q.p) for q in c.samples]))
        if len(rs) >= 2 and all(len(c.samples) >= 4 for c in rs):
            A = [w(q) for q in resample([x.p for x in rs[0].samples], DISC_NR + 1)]
            B = [w(q) for q in resample([x.p for x in rs[1].samples], DISC_NR + 1)]
            tris = []
            for k in range(DISC_NR):
                tris += [A[k], A[k + 1], B[k], B[k], A[k + 1], B[k + 1]]
            fills.append(((*(DISC_FILL_SEL if s else DISC_FILL), (0.3 if s else 0.16) * op), tris))
        hp = g.hitPts or {}
        cut = []
        for sf in P.surfaces():
            if sf.a in hp and sf.b in hp:
                cut += [w(hp[sf.a]), w(hp[sf.b])]
        if cut:
            stroke('cut', s, GUIDE, cut)
    return {'strokes': strokes, 'fills': fills}


def sel_colors(context=None):
    """the selection colors (3.8c): the add-on's preferences, else the defaults (the add-on registered by hand: tests)"""
    from . import prefs
    p = prefs.get(context)
    if p is None:
        return dict(SEL_DEFAULT)
    return {'curve': tuple(p.sel_curve_color), 'anchor': tuple(p.sel_anchor_color), 'mark': tuple(p.last_anchor_mark)}


def geometry(P, matrix, sel=None, snap=None, weld=None, walls=None, shown=None, guides=None, colors=None, over=None):
    """Pure assembly, in Blender world space (`matrix` = the root's matrix_world):
    lines: flat list of (x, y, z) pairs (one pair per sample segment), line_colors: one (r, g, b) per line vertex;
    anchors: pos (x, y, z), col (r, g, b), size (CSS px), shape (1 = diamond, 0 = circle), one entry each per anchor;
    walls: wall_geometry of `walls` (the grids of the selected walls), when given.
    Follows rebuildAnchors (1363): hidden curves and their anchors are skipped, ring (disc) curves too, every
    anchor once; shared anchors cyan, attached ones purple; the first anchor of an open curve is a diamond.
    `sel` (a core Selection) colors the selected anchors (orange, 11 px), the last one (red, 13 px) and the
    selected curves (orange); `snap` is the id of the curve an anchor slides along (cyan), as syncVisuals does; `weld`
    is the id of the anchor the dragged one would weld into (magenta, 16 px: S.weldCand, 1409). `shown` (3.6): the
    ids of the curves their group's mode shows (groups.shownCurves, guidesShown 11914); None draws every visible
    curve. A curve left out takes its anchors with it (rebuildAnchors, 1381). `guides` (21/10/2026): the guides to
    draw (groups.shownGuides), as guide_geometry; none when None. `colors` (3.8c): the selection colors (sel_colors;
    the defaults when None); sel_lines: the selected curves again (drawn over the others, thicker); anchors' kind:
    0 plain, 1 selected (3.8e: drawn as a plain one, in its color), 2 the last selected (a ring of the mark color around it).
    `over` (lot 4.8b): {curve id: world distance} of tube_pulls; 'over' holds those curves again, each whole, with every
    anchor it has, the distance and its bounds (_draw_over)."""
    colors = colors or SEL_DEFAULT
    rebuild_dirty(P)
    curves = P.curves()
    count = {}
    for c in curves:
        for a in c.anchors:
            count[a.id] = count.get(a.id, 0) + 1
    starts = {c.anchors[0].id for c in curves if not c.ring and not c.closed and len(c.anchors) > 1}
    sel_items = set(sel.items) if sel is not None else set()
    asel = set(sel.anchors) if sel is not None else set()
    last = sel.last_anchor if sel is not None else None
    lines = []; line_colors = []; sel_lines = []; sel_line_colors = []
    a_pos = []; a_col = []; a_size = []; a_shape = []; a_kind = []
    seen = set()
    pulled = []

    def anchor_of(a):
        """(position, color, size, shape, kind) of an anchor"""
        col = CA['att'] if a.att else CA['shared'] if count[a.id] > 1 else CA['normal']; s = ANCHOR_PX; kind = 0
        if a.id in asel:
            col = colors['anchor']; s = SEL_PX; kind = 1
        if a.id == last:
            col = colors['anchor']; s = LAST_PX; kind = 2
        if a.id == weld:
            col = CA['weld']; s = WELD_PX; kind = 0
        dia = a.id in starts
        return tuple(matrix @ Vector(to_blender(a.pos))), col, s * START_SCALE if dia else s, 1.0 if dia else 0.0, float(kind)

    for c in curves:
        if c.ring or len(c.anchors) < 2 or not edit.isVisible(P, c):
            continue
        if shown is not None and c.id not in shown:
            continue
        picked = c.id in sel_items and snap != c.id
        col = CURVE_SNAP if snap == c.id else colors['curve'] if picked else curve_color(P, c)
        pts = [tuple(matrix @ Vector(to_blender(s.p))) for s in c.samples]
        for i in range(len(pts) - 1):
            lines.append(pts[i]); lines.append(pts[i + 1])
            line_colors.append(col); line_colors.append(col)
            if picked:
                sel_lines.append(pts[i]); sel_lines.append(pts[i + 1])
                sel_line_colors.append(col); sel_line_colors.append(col)
        for a in c.anchors:
            if a.id in seen:
                continue
            seen.add(a.id)
            q, tint, s, shape, kind = anchor_of(a)
            a_pos.append(q); a_col.append(tint); a_size.append(s); a_shape.append(shape); a_kind.append(kind)
        if over and c.id in over:
            A = [anchor_of(a) for a in c.anchors]
            mid = tuple(sum(q[k] for q in pts) / len(pts) for k in range(3))
            pulled.append({'lines': [q for i in range(len(pts) - 1) for q in (pts[i], pts[i + 1])], 'color': col, 'picked': picked, 'dist': over[c.id], 'mid': mid,
                           'size': max((Vector(q) - Vector(mid)).length for q in pts),
                           'anchors': {'pos': [x[0] for x in A], 'col': [x[1] for x in A], 'size': [x[2] for x in A], 'shape': [x[3] for x in A], 'kind': [x[4] for x in A]}})
    return {'lines': lines, 'line_colors': line_colors, 'sel_lines': sel_lines, 'sel_line_colors': sel_line_colors,
            'anchors': {'pos': a_pos, 'col': a_col, 'size': a_size, 'shape': a_shape, 'kind': a_kind}, 'over': pulled,
            'walls': wall_geometry(walls, matrix) if walls else {'tris': [], 'lines': []},
            'guides': guide_geometry(P, matrix, guides, sel_items) if guides else {'strokes': {}, 'fills': []}}


# ---------------- gpu side (only inside the draw callback) ----------------
_handle = None
_handle_2d = None
_cache = {}                                                         # root name -> (key, geometry, batches)
_reach_cache = {}                                                   # Profile modifier id -> (key, tube_reach, its surface's points)
OVER_MARGIN = 1.05                                                  # tube_pulls: a little past the surface's farthest vertex
_shaders = {}
ghost = None                                                        # the Draw tool's ghost line (4645): (last anchor, cursor point) in world space, or None
drawing = None                                                      # S.drawing: the id of the curve being drawn (its group counts as active, 11911), or None
GHOST = (1.0, 0xaa / 255.0, 0x33 / 255.0, 0.7)                      # LineBasicMaterial 0xffaa33, opacity 0.7, no depth test
pencil = None                                                       # 4.2: the Pencil's loop being drawn, world points (pencilObj, 6300: 0xffb347, 2.5 px), or None
zone_flash = None                                                   # 4.2 (D4.7): (guide id, until) — the zone shown 1.5 s after one of its sliders (showZone 6210)
snap_curve = None                                                   # S.snapCurve: the id of the curve an anchor slides along (Shift drag), or None
connectors_on = False                                               # 4.4: every crown's connectors shown while a curve end is dragged (ops_gizmo.attach_preview)
attach_dot = None                                                   # 4.4: where the dragged end would attach (a world point), or None
weld_cand = None                                                    # S.weldCand: the id of the anchor a dragged anchor would weld into (findWeldCandidate during the drag), or None
WELD_PX = 16.0                                                      # the candidate's dot (1409)
overlay = None                                                      # the Select tool's 2D overlay in region pixels: ('box', x0, y0, x1, y1) | ('circle', x, y, r) | ('paint', x, y, r) | ('line', x0, y0, x1, y1) (the Scissors' stroke) | None
wall_cut = None                                                     # the wall cut being placed (3.8a, drawSurfCut 5344): ([line points], [(point, kind)]) in world space, or None
CUT_LINE = (1.0, 0x5a / 255.0, 0x5a / 255.0, 1.0)                   # scutLine: 0xff5a5a, 3 px, drawn over the wall (depthTest false)
CUT_DOTS = {'end': ((1.0, 0.25, 0.25), 13, 1), 'ctl': ((1.0, 0.45, 0.45), 11, 0), 'auto': ((1.0, 0.75, 0.75), 6, 0), 'hover': ((1.0, 0.6, 0.6), 9, 0),   # drawSurfCut's look: colour, size, diamond
            'lead': ((1.0, 0x5a / 255.0, 0x5a / 255.0), 11, 0)}                                                                                          # ours (3.8f): where the first click would start the cut, the cut's colour
STROKE = (1.0, 0x5a / 255.0, 0x5a / 255.0, 0.95)                    # the Scissors' stroke (#guideStroke)
OVERLAY_LINE = (1.0, 1.0, 1.0, 0.9)
OVERLAY_SHADOW = (0.0, 0.0, 0.0, 0.6)
OVERLAY_FILL = (1.0, 1.0, 1.0, 0.06)
# the Ctrl + drag paint cursor (an addition of this port, docs/features-adicionadas.md): a filled blue disc of the pick radius
PAINT_FILL = (0.25, 0.55, 1.0, 0.35)
PAINT_LINE = (0.4, 0.7, 1.0, 0.9)


def _anchor_mats(sh):
    """the anchors shader's matrices (the view's model-view and projection) into its uniform buffer: std140 mat4s,
    column by column"""
    import gpu
    vals = []
    for m in (gpu.matrix.get_model_view_matrix(), gpu.matrix.get_projection_matrix()):
        vals += [m[r][c] for c in range(4) for r in range(4)]
    ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', 32, vals))
    sh.uniform_block('mats', ubo)
    return ubo                                                      # (kept alive by the caller until the draw)


def _line_shader():
    import gpu
    sh = _shaders.get('line')
    if sh is None:
        sh = _shaders['line'] = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    return sh


def _anchor_shader():
    """port of anchorMat (1344): a point sprite per anchor, circle or diamond, dark rim, pushed 10 px towards the
    camera along the view ray (orthographic: along z) so the wall it sits on does not hide it."""
    import gpu
    if 'anchor' in _shaders:
        return _shaders['anchor']
    from gpu.types import GPUShaderCreateInfo, GPUStageInterfaceInfo
    iface = GPUStageInterfaceInfo('hairmesh_anchor_iface')
    iface.smooth('VEC4', 'vC')
    iface.flat('FLOAT', 'vS')
    iface.flat('FLOAT', 'vK')
    info = GPUShaderCreateInfo()
    # (lot 4.4, item 3b: the two matrices in a uniform buffer — as push constants with the rest they took 160 bytes, over
    # the 128 every GPU backend guarantees: Blender's "Push constants have a minimum supported size of 128 bytes")
    info.typedef_source('struct HairMeshAnchorMats { mat4 mv; mat4 proj; };')
    info.uniform_buf(0, 'HairMeshAnchorMats', 'mats')
    info.push_constant('FLOAT', 'opacity')
    info.push_constant('FLOAT', 'vh')
    info.push_constant('FLOAT', 'pr')
    info.push_constant('VEC4', 'mark')
    info.push_constant('FLOAT', 'selonly')
    info.vertex_in(0, 'VEC3', 'pos')
    info.vertex_in(1, 'VEC4', 'col')
    info.vertex_in(2, 'FLOAT', 'size')
    info.vertex_in(3, 'FLOAT', 'shape')
    info.vertex_in(4, 'FLOAT', 'kind')
    info.vertex_out(iface)
    info.fragment_out(0, 'VEC4', 'fragColor')
    info.vertex_source('''
void main() {
  vC = col; vS = shape; vK = kind;
  mat4 ModelViewMatrix = mats.mv, ProjectionMatrix = mats.proj;
  vec4 mv = ModelViewMatrix * vec4(pos, 1.0);
  float push = 10.0;
  if (ProjectionMatrix[2][3] < -0.5) {
    float l = length(mv.xyz), b = push * 2.0 * (-mv.z) / (ProjectionMatrix[1][1] * vh);
    mv.xyz *= 1.0 - min(b, 0.5 * l) / l;
  } else {
    mv.z += push * 2.0 / (ProjectionMatrix[1][1] * vh);
  }
  gl_Position = ProjectionMatrix * mv;
  gl_PointSize = size * pr;
}''')
    info.fragment_source('''
void main() {
  vec2 d = gl_PointCoord - 0.5;
  float r = vS > 0.5 ? abs(d.x) + abs(d.y) : length(d);
  if (r > 0.5 || (selonly > 0.5 && vK < 0.5)) discard;
  // the dark rim of every dot (3.8e: a selected one, kind 1, as the others); the last one (kind 2) a ring of the mark color outside it
  vec3 c = vC.rgb;
  if (vK > 1.5) { if (r > 0.33) c = mark.rgb; else if (r > 0.24) c = vec3(0.06); }
  else if (r > 0.34) c = vec3(0.06);
  fragColor = vec4(c, vC.a * opacity * smoothstep(0.5, 0.42, r));
}''')
    sh = _shaders['anchor'] = gpu.shader.create_from_info(info)
    return sh


def _batches(root, P, matrix, sel, snap, weld, active_group=None, drawing_id=None):
    import gpu
    from gpu_extras.batch import batch_for_shader
    mt = tuple(tuple(row) for row in matrix)
    # the selected walls' grids (3.5): from the live preview's cache, computed when the wall is dirty or unbuilt
    # (a selected wall hidden by its eye, or by its group's, has no outline: syncVisuals leaves its object invisible, 12005)
    wall_ids = [i for i in sel.items if (it := P.items.get(i)) is not None and it.type == 'surface' and edit.isVisible(P, it)]
    walls = [live.grid_of(root, P, i) for i in wall_ids] if wall_ids else None
    # the curves the groups' modes show (3.6): a selected group is part of the selection (U1), the curve being drawn counts
    cs, act, loose = G.shownCurves(P, sel.items, sel.anchors, P.items.get(drawing_id) if drawing_id is not None else None, active_group, snap)
    if connectors_on:                                               # (4.4: every crown's connectors while an end is dragged)
        have = {c.id for c in cs} | G.hiddenCrownCurves(P)
        cs = cs + [c for c in P.curves() if c.conn and c.id not in have and len(c.anchors) >= 2 and edit.isVisible(P, c)]
    shown = frozenset(c.id for c in cs)
    over = {i: d for i, d in tube_pulls(P, live.tubes_under_curves(root, P), matrix).items() if i in shown}
    # the guides shown (part 3 of 21/10/2026); a disc's cut lines follow its last rebuild (hitPts is a new dict each
    # time: the dict itself is in the key, so it is kept alive and never mistaken for a newer one at the same address)
    guides = G.shownGuides(P, act, loose)
    gkey = tuple((g.id, g.hitPts) for g in guides)
    colors = sel_colors()
    key = (id(P), P.gen, P.rev, mt, tuple(sel.items), tuple(sel.anchors), snap, weld, live.revision(root) if wall_ids else 0, shown, gkey,
           tuple(colors[k] for k in ('curve', 'anchor', 'mark')), tuple(sorted(over.items())))
    c = _cache.get(root.name)
    if c is not None and c[0] == key:
        return c[1], c[2]
    g = geometry(P, matrix, sel, snap, weld, walls, shown, guides, colors, over)
    b = {}
    if g['guides']['fills'] or g['guides']['strokes']:
        u = gpu.shader.from_builtin('UNIFORM_COLOR'); pl = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        b['guide_fills'] = [(rgba, batch_for_shader(u, 'TRIS', {'pos': tris})) for rgba, tris in g['guides']['fills']]
        b['guide_strokes'] = [(px, rgba, batch_for_shader(pl, 'LINES', {'pos': pts})) for (px, rgba), pts in g['guides']['strokes'].items()]
    if g['lines']:
        sh = _line_shader()
        b['lines'] = batch_for_shader(sh, 'LINES', {'pos': g['lines'], 'color': [(*col, 1.0) for col in g['line_colors']]})
    if g['sel_lines']:
        b['sel_lines'] = batch_for_shader(_line_shader(), 'LINES', {'pos': g['sel_lines'], 'color': [(*col, 1.0) for col in g['sel_line_colors']]})
    b['mark'] = (*colors['mark'], 1.0)
    if g['walls']['tris']:
        b['wall_tris'] = batch_for_shader(gpu.shader.from_builtin('UNIFORM_COLOR'), 'TRIS', {'pos': g['walls']['tris']})
        b['wall_lines'] = batch_for_shader(gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR'), 'LINES', {'pos': g['walls']['lines']})
    _anchor_batches(b, g['anchors'])
    if g['over']:
        b['over'] = []
        for o in g['over']:
            e = {'dist': o['dist'], 'mid': o['mid'], 'size': o['size'], 'mark': b['mark']}
            e['sel_lines' if o['picked'] else 'lines'] = batch_for_shader(_line_shader(), 'LINES', {'pos': o['lines'], 'color': [(*o['color'], 1.0)] * len(o['lines'])})
            _anchor_batches(e, o['anchors'])
            b['over'].append(e)
    _cache[root.name] = (key, g, b)
    return g, b


def _anchor_batches(b, A):
    """the anchors `A` (geometry's) as batches into `b`"""
    import gpu
    from gpu_extras.batch import batch_for_shader
    if not A['pos']:
        return
    try:
        sh = _anchor_shader()
        # the selected anchors last (3.8c): an anchor of another curve in the very same place (coincident, not welded:
        # the example has many) drawn after one hid it — the depth is equal, the later one wins
        o = sorted(range(len(A['pos'])), key=lambda i: A['kind'][i])
        b['any_sel'] = any(k > 0.5 for k in A['kind'])
        b['anchors'] = batch_for_shader(sh, 'POINTS', {'pos': [A['pos'][i] for i in o], 'col': [(*A['col'][i], 1.0) for i in o], 'size': [A['size'][i] for i in o],
                                                      'shape': [A['shape'][i] for i in o], 'kind': [A['kind'][i] for i in o]})
    except Exception as e:                                          # a backend that refuses the shader: plain round points
        _shaders['anchor_error'] = repr(e)
        sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
        groups = {}
        for p, col, s in zip(A['pos'], A['col'], A['size']):
            groups.setdefault((col, s), []).append(p)
        b['anchors_plain'] = [((col, s), batch_for_shader(sh, 'POINTS', {'pos': pts})) for (col, s), pts in groups.items()]


# ---- lot 4.2 ∥ 4.5, item 1 (ours, the owner's request): a selected converted mesh's outline ----
# A converted mesh selected by a click (or by the HairMesh Outliner) gets an outline in the theme's colour of a selected
# object (object_selected_color; the curves' lime until the outside review of 30/10/2026: a mesh is an object, not a
# curve), like Blender's selection outline — the add-on's selection, never Blender's: the object is neither selected
# nor active there. Drawn as lines along the mesh's silhouette for the current view (an edge between a face turned to
# the eye and one turned away), its open borders and its non-manifold edges (a Y junction), MESH_LINE_PX wide, pulled
# towards the camera like a selected wall's edges, in the depth-tested pass (Blender's X-ray: the one pass).
MESH_LINE_PX = 3.0
_outline_topo = {}                                                  # data token -> mesh_outline_topology
_outline_lines = {}                                                 # (mesh item id, the 3D View's region_data) -> (view key, batch)


def object_selected_color(context):
    """the color of a selected object in Blender's theme (Preferences > Themes > 3D Viewport > Object Selected)"""
    return tuple(context.preferences.themes[0].view_3d.object_selected)


def mesh_outline_topology(pos, faces):
    """Pure (numpy): the arrays the outline needs from a mesh's data — the vertices in Blender axes (`pos`, already
    there), one normal and one point per face, the edges between two faces with their faces, and the edges that are
    always drawn (an open border: one face; a junction: more than two)."""
    import numpy as np
    V = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    nv = len(V)
    faces = [f for f in faces if len(f) >= 3 and all(0 <= i < nv for i in f)]   # (a broken index never takes the outline down)
    f0 = np.array([f[0] for f in faces], dtype=np.int64); f1 = np.array([f[1] for f in faces], dtype=np.int64)
    f2 = np.array([f[2] for f in faces], dtype=np.int64); fl = np.array([f[-1] for f in faces], dtype=np.int64)
    # the diagonals' cross product: the normal of a quad, and of a triangle (f[-1] = f[2]) the usual one
    n = np.cross(V[f2] - V[f0], V[fl] - V[f1]) if len(faces) else np.zeros((0, 3))
    of = {}
    for i, f in enumerate(faces):
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            of.setdefault((a, b) if a < b else (b, a), []).append(i)
    pairs = []; pf = []; always = []
    for e, fs in of.items():
        if len(fs) == 2:
            pairs.append(e); pf.append(fs)
        else:
            always.append(e)
    return {'pos': V, 'n': n, 'c': V[f0] if len(faces) else np.zeros((0, 3)),
            'pairs': np.array(pairs, dtype=np.int64).reshape(-1, 2), 'pf': np.array(pf, dtype=np.int64).reshape(-1, 2),
            'always': np.array(always, dtype=np.int64).reshape(-1, 2)}


def mesh_outline_segments(topo, eye=None, view_dir=None):
    """Pure (numpy): the outline's segments (2 points each, the mesh's own space) for an eye at `eye` (perspective)
    or looking along `view_dir` (orthographic): every edge whose two faces are turned differently, plus the
    always-drawn edges."""
    import numpy as np
    n = topo['n']
    if eye is not None:
        s = np.einsum('ij,ij->i', n, topo['c'] - np.asarray(eye, dtype=np.float64)) > 0.0
    else:
        s = n @ np.asarray(view_dir, dtype=np.float64) > 0.0
    pf = topo['pf']
    e = topo['pairs'][s[pf[:, 0]] != s[pf[:, 1]]] if len(pf) else topo['pairs']
    e = np.concatenate([e, topo['always']]) if len(topo['always']) else e
    seg = topo['pos'][e.reshape(-1)].reshape(-1, 2, 3)
    return seg[np.all(np.isfinite(seg), axis=(1, 2))].reshape(-1, 3)


def _outline_batches(ctx, root, P, sel):
    """(object matrix, batch) of every selected converted mesh shown, its segments recomputed when the view, the
    object's matrix or the data change (per 3D View: two views never undo each other's). Only looks at bpy.data."""
    import numpy as np
    from gpu_extras.batch import batch_for_shader
    import gpu
    from . import meshes as M
    ids = [i for i in sel.items if (it := P.items.get(i)) is not None and it.type == 'mesh' and it.data is not None and edit.isVisible(P, it)]
    rv3d = ctx.region_data
    if not ids or rv3d is None:
        _outline_lines.clear()
        return []
    vk = rv3d.as_pointer()
    obs = M.mesh_objects(root, clean=False)
    out = []
    for iid in ids:
        m = P.items[iid]; ob = obs.get(iid)
        if ob is None:
            continue
        try:
            if not ob.visible_get():
                continue
        except Exception:
            continue
        mw = ob.matrix_world
        key = (m.token, tuple(map(tuple, rv3d.view_matrix)), rv3d.is_perspective, tuple(map(tuple, mw)))
        c = _outline_lines.get((iid, vk))
        if c is None or c[0] != key:
            topo = _outline_topo.get(m.token) if m.token else None
            if topo is None:
                with slow.part('mesh outline', m.name):
                    topo = mesh_outline_topology([to_blender(p) for p in m.data.pos], m.data.faces)
                if m.token:
                    _outline_topo[m.token] = topo
                    while len(_outline_topo) > 64:
                        del _outline_topo[next(iter(_outline_topo))]
            inv = mw.inverted()
            vi = rv3d.view_matrix.inverted()
            if rv3d.is_perspective:
                seg = mesh_outline_segments(topo, eye=tuple(inv @ vi.translation))
            else:
                seg = mesh_outline_segments(topo, view_dir=tuple((inv.to_3x3() @ (vi.to_3x3() @ Vector((0.0, 0.0, -1.0)))).normalized()))
            batch = batch_for_shader(gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR'), 'LINES', {'pos': np.ascontiguousarray(seg, dtype=np.float32)}) if len(seg) else None
            c = (key, batch)
            _outline_lines[(iid, vk)] = c
        if c[1] is not None:
            out.append((mw.copy(), c[1]))
    for k in [k for k in _outline_lines if k[0] not in ids]:
        del _outline_lines[k]
    return out


def clear_outlines():
    _outline_topo.clear(); _outline_lines.clear(); _topo_cache.clear()


_outline_error = []


def _draw_mesh_outlines(ctx, root, P, sel, region, lw, alpha):
    import gpu
    try:
        lines = _outline_batches(ctx, root, P, sel)
    except Exception as e:                                          # the outline must never take the curves and anchors down
        errorlog.record('draw: mesh outline')
        if not _outline_error:
            _outline_error.append(repr(e)); print('HairMesh mesh outline:', repr(e))
        return
    if not lines:
        return
    col = object_selected_color(ctx)
    gpu.matrix.push_projection()
    gpu.state.depth_mask_set(False)
    try:
        if ctx.region_data is not None:
            gpu.matrix.load_projection_matrix(offset_projection(gpu.matrix.get_projection_matrix(), ctx.region_data, WALL_OFFSET))
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_float('lineWidth', MESH_LINE_PX * lw)
        sh.uniform_bool('lineSmooth', True)
        sh.uniform_float('color', (*col, alpha))
        for mw, batch in lines:
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(mw)
                batch.draw(sh)
    finally:
        gpu.state.depth_mask_set(True)
        gpu.matrix.pop_projection()


# ---- the Caps' and Fill holes' previews (ours, the outside review of 30/10/2026, docs/features-adicionadas.md) ----
# Their faces are in their group's live object (live.rebuild), grey among the grey walls; the original tints them
# (cap 0x9b7bff, skin 0x5fd08a: syncVisuals 11991, 11983). Over that mesh, drawn as a selected wall is: a translucent
# fill and the quad edges wider than the wire, each kind in a hue near the original's but away from the anchors (yellow,
# cyan, purple, red), the curves (grey, lime) and the guides' green. Selected: a stronger fill and wider lines, not only
# another colour (G11). What a click hits is what shows: live.shown_previews, as pick.topo_hits.
TOPO_COLOR = {'cap': (0x5f / 255.0, 0x73 / 255.0, 1.0), 'skin': (0x1f / 255.0, 0xd6 / 255.0, 0xb0 / 255.0)}
TOPO_FILL = (0.30, 0.50)                                            # opacity: plain, selected
TOPO_LINE_PX = (2.5, 4.0)                                           # width: plain, selected
TOPO_LINE_ALPHA = 0.95
_topo_cache = {}                                                    # item id -> (matrix, preview, tris batch, lines batch)


def preview_geometry(data, matrix):
    """a Cap's / Fill's preview ({pos, faces}, the model space) as wall_geometry: world-space triangles and edges (pure)"""
    return wall_geometry([SimpleNamespace(pos=data['pos'], faces=data['faces'])], matrix)


def topo_style(kind, selected):
    """pure: (fill rgba, line rgba, line width px at 100 %) of a preview of `kind` ('cap', 'skin')"""
    c = TOPO_COLOR[kind]; i = 1 if selected else 0
    return (*c, TOPO_FILL[i]), (*c, TOPO_LINE_ALPHA), TOPO_LINE_PX[i]


def topo_previews_shown(P, prev):
    """[(item, preview)] of the Caps and Fill holes among `prev` (live.shown_previews) that show now: as the live object
    (the eye, its groups', a cap only over walls that show) and with faces; a Profile modifier is drawn by its object"""
    out = []
    for kid, (_key, data, _info) in prev.items():
        k = P.items.get(kid)
        if data is not None and k is not None and k.type in TOPO_COLOR and data['faces'] and edit.isVisible(P, k) and live.topo_shown(P, k):
            out.append((k, data))
    return out


def _topo_batches(root, P, sel):
    """[(kind, selected, tris batch, lines batch)] of the previews shown; a batch made again only when its preview or
    the root's matrix changed"""
    if not (P.caps() or P.of_type('skin')):
        _topo_cache.clear()
        return []
    st = session.runtime(root)
    if st is None or st.get('P') is not P:
        return []                                                   # (a file just loaded: nothing yet, the first update brings the previews)
    import gpu
    from gpu_extras.batch import batch_for_shader
    prev = (st.get('topo_prev') or {}) if bpy.context.scene.hairmesh.live_preview else {}   # (read only: never built inside a draw; live.update and the Ctrl+Z build them)
    mt = tuple(tuple(row) for row in root.matrix_world)
    picked = set(sel.items)
    out = []
    for k, data in topo_previews_shown(P, prev):
        c = _topo_cache.get(k.id)
        if c is None or c[0] != mt or c[1] is not data:
            g = preview_geometry(data, root.matrix_world)
            c = (mt, data, batch_for_shader(gpu.shader.from_builtin('UNIFORM_COLOR'), 'TRIS', {'pos': g['tris']}),
                 batch_for_shader(gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR'), 'LINES', {'pos': g['lines']}))
            _topo_cache[k.id] = c
        out.append((k.type, k.id in picked, c[2], c[3]))
    for kid in [i for i in _topo_cache if i not in prev]:
        del _topo_cache[kid]
    return out


def _draw_topo(batches, region, rv3d, lw, alpha):
    """the previews, under the projection pulled off the live mesh as a selected wall's (_draw_walls), writing no depth"""
    import gpu
    gpu.matrix.push_projection()
    gpu.state.depth_mask_set(False)
    try:
        if rv3d is not None:
            gpu.matrix.load_projection_matrix(offset_projection(gpu.matrix.get_projection_matrix(), rv3d, WALL_OFFSET))
        sh = gpu.shader.from_builtin('UNIFORM_COLOR')
        sh.bind()
        for kind, picked, tris, _lines in batches:
            rgba = topo_style(kind, picked)[0]
            sh.uniform_float('color', (*rgba[:3], rgba[3] * alpha))
            tris.draw(sh)
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_bool('lineSmooth', True)
        for kind, picked, _tris, lines in batches:
            _f, rgba, px = topo_style(kind, picked)
            sh.uniform_float('lineWidth', px * lw)
            sh.uniform_float('color', (*rgba[:3], rgba[3] * alpha))
            lines.draw(sh)
    finally:
        gpu.state.depth_mask_set(True)
        gpu.matrix.pop_projection()


# ---- 4.2: the guides' influence zone (updateZoneViz 6216, D4.7) and the Pencil's loop ----
GUIDE_STROKE = (0x5f / 255.0, 0xe3 / 255.0, 0x9a / 255.0, 0.95)      # #guideStroke's green (466): the Line mode's dashed stroke
GUIDE_DASH = (6.0, 4.0)                                             # stroke-dasharray="6 4"
PENCIL_COLOR = (1.0, 0xb3 / 255.0, 0x47 / 255.0, 1.0)              # pencilObj 0xffb347
PENCIL_PX = 2.5
ZONE_PX = 4.0                                                       # zoneObj: newLineObj(0xffffff, 4, 12, true), vertex colours
_zone_cache = {}


def flash_zone(gid):
    """showZone (6210): the guide's zone for 1.5 s (after one of its sliders), then a redraw takes it away"""
    global zone_flash
    import time
    from ..core.guidetool import ZONE_SECONDS
    zone_flash = (gid, time.monotonic() + ZONE_SECONDS)

    def later():
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type == 'VIEW_3D':
                    a.tag_redraw()
        return None
    try:
        bpy.app.timers.register(later, first_interval=ZONE_SECONDS + 0.1)
    except Exception:
        pass


def zone_guides(P, sel):
    """the guides whose zone shows (D4.7): the selected ones, and the one a slider flashed for 1.5 s"""
    import time
    global zone_flash
    out = [it for it in (P.items.get(i) for i in sel.items) if it is not None and it.type == 'guide']
    if zone_flash is not None and time.monotonic() < zone_flash[1]:
        g = P.items.get(zone_flash[0])
        if g is not None and g.type == 'guide' and g not in out:
            out.append(g)
    elif zone_flash is not None:
        zone_flash = None                                           # (over: forgotten)
    return out


def zone_geometry(P, matrix, guides):
    """pure: the zones of `guides` in Blender world space — (points, colors), two of each per segment"""
    from ..core.guidetool import zoneSegments
    pts = []; cols = []
    for g in guides:
        seg, col = zoneSegments(P, g)
        pts += [tuple(matrix @ Vector(to_blender(p))) for p in seg]
        cols += [(*c, 1.0) for c in col]
    return pts, cols


def _draw_zone(root, P, sel, region, lw):
    import json
    import gpu
    from gpu_extras.batch import batch_for_shader
    gs = zone_guides(P, sel)
    if not gs:
        return
    key = (id(P), P.gen, P.rev, tuple(tuple(row) for row in root.matrix_world), tuple((g.id, json.dumps(g.p, sort_keys=True, default=str), id(g.hits)) for g in gs))
    c = _zone_cache.get(root.name)
    if c is None or c[0] != key:
        pts, cols = zone_geometry(P, root.matrix_world, gs)
        c = (key, batch_for_shader(_line_shader(), 'LINES', {'pos': pts, 'color': cols}) if pts else None)
        _zone_cache[root.name] = c
    if c[1] is None:
        return
    sh = _line_shader()
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', ZONE_PX * lw)
    sh.uniform_bool('lineSmooth', True)
    c[1].draw(sh)


def _draw_pencil(region, lw):
    import gpu
    from gpu_extras.batch import batch_for_shader
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', PENCIL_PX * lw)
    sh.uniform_bool('lineSmooth', True)
    sh.uniform_float('color', PENCIL_COLOR)
    batch_for_shader(sh, 'LINE_STRIP', {'pos': pencil}).draw(sh)


def dash_segments(x0, y0, x1, y1, on, off):
    """pure: the dashes of a line from (x0, y0) to (x1, y1), `on` px drawn and `off` px skipped (stroke-dasharray):
    a flat list of points, two per dash"""
    import math
    L = math.hypot(x1 - x0, y1 - y0)
    if L <= 0:
        return []
    ux = (x1 - x0) / L; uy = (y1 - y0) / L; out = []; s = 0.0
    while s < L:
        e = min(s + on, L)
        out += [(x0 + ux * s, y0 + uy * s), (x0 + ux * e, y0 + uy * e)]
        s = e + off
    return out


def polyline2d(region, pts, rgba, width, mode='LINE_STRIP', smooth=True):
    """a line over the region, in its pixels, never under 1 px: POLYLINE_UNIFORM_COLOR, the built-in shader with a width
    on every GPU backend (gpu.state.line_width_set is 1 px on some). `smooth` off for lines along the pixels: smoothed,
    a thin line across two rows of pixels fades"""
    import gpu
    from gpu_extras.batch import batch_for_shader
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (region.width, region.height))
    sh.uniform_float('lineWidth', max(1.0, width))
    sh.uniform_bool('lineSmooth', smooth)
    sh.uniform_float('color', rgba)
    batch_for_shader(sh, mode, {'pos': [(p[0], p[1], 0.0) for p in pts]}).draw(sh)


def invalidate(root_name=None):
    if root_name is None:
        _cache.clear()
    else:
        _cache.pop(root_name, None)


def overlays_on(ctx):
    """4.0 (ours, the owner's request): the 3D View being drawn shows its overlays (its Show Overlays button). Off,
    nothing the HairMesh draws over that view shows — curves, anchors, the selection, the Scissors' line and points,
    the dot, the Draw stroke, the box, guides, bands, the banner texts; the meshes (live walls, converted meshes) are
    objects and stay, as Blender's. Only the drawing: the tool goes on working. Per 3D View (another one with its
    overlays on keeps showing them)."""
    ov = getattr(getattr(ctx, 'space_data', None), 'overlay', None)
    return getattr(ov, 'show_overlays', True)


@slow.timed('the viewport drawn (the curves and anchors of the tool)')
def _draw():
    ctx = bpy.context
    if not owner.visible(ctx):                                      # the workspace filters the add-on out: nothing drawn (the tool may stay active, inert)
        return
    if not tool.is_active(ctx) or not overlays_on(ctx):
        return
    root = session.find_root(ctx)
    if root is None:
        return
    try:
        P = session.get_project(root)
    except Exception:
        errorlog.record('draw: the project of the overlay')
        return
    import gpu
    sel_now = select.load(ctx.scene.hairmesh)
    g, b = _batches(root, P, root.matrix_world, sel_now, snap_curve, weld_cand, None, drawing)
    edge_lines = None
    if ctx.scene.hairmesh.subtool == 'EDGES':                       # 4.9: the selected edges and the one under the cursor
        from . import ops_edges
        try:
            edge_lines = ops_edges.overlay(ctx.scene.hairmesh, P, root)
        except Exception as e:                                      # (they must never take the curves and anchors down)
            errorlog.record('draw: edges overlay')
            if not _outline_error:
                _outline_error.append(repr(e)); print('HairMesh edges overlay:', repr(e))
    if not b and ghost is None and wall_cut is None and pencil is None and zone_flash is None and not edge_lines:
        return
    region = ctx.region
    lw = pick.line_scale(ctx)
    xray = P.settings.xray
    try:
        topo = _topo_batches(root, P, sel_now)
    except Exception as e:                                          # (the previews must never take the curves and anchors down)
        errorlog.record('draw: Cap / Fill preview')
        topo = []
        if not _outline_error:
            _outline_error.append(repr(e)); print('HairMesh Cap / Fill preview:', repr(e))
    gpu.state.blend_set('ALPHA')
    passes = [('LESS_EQUAL', 1.0)]
    if pick.blender_xray(ctx):
        passes = [('NONE', 1.0)]                                    # Blender's X-ray (Alt+Z): the curves and anchors behind the head show (3.8c)
    elif xray > 0.001:
        passes.append(('NONE', xray))
    for depth, alpha in passes:
        gpu.state.depth_test_set(depth)
        if 'guide_fills' in b:
            _draw_guides(b, region, lw, alpha, fills=depth != 'NONE' or len(passes) == 1)   # (Blender's X-ray: the one pass draws the discs' annuli too)
        if 'wall_tris' in b:                                        # the selected walls (3.5): tinted fill, then the quad edges, pulled off the live mesh
            _draw_walls(b, region, ctx.region_data, lw, alpha)
        if topo:
            try:
                _draw_topo(topo, region, ctx.region_data, lw, alpha)
            except Exception as e:                                  # (the previews must never take the curves and anchors down)
                errorlog.record('draw: Cap / Fill preview')
                topo = []
                if not _outline_error:
                    _outline_error.append(repr(e)); print('HairMesh Cap / Fill preview:', repr(e))
        if alpha >= 1.0:                                            # the selected converted meshes' outline (4.2 ∥ 4.5, item 1): not in the x-ray pass
            _draw_mesh_outlines(ctx, root, P, sel_now, region, lw, alpha)
        # the curves, then the selected ones, then the anchors; 3.8e: in the depth-tested pass, under the projection
        # pulled CURVE_OFFSET towards the camera — the curves in front of the wall edges they lie on, and the anchors'
        # own 10 px push counted from there, so a curve never covers its anchors (the pull of an orthographic view
        # grows with the clip range and could pass the 10 px)
        pushed = depth != 'NONE' and ctx.region_data is not None
        if pushed:
            gpu.matrix.push_projection()
        try:
            if pushed:
                gpu.matrix.load_projection_matrix(offset_projection(gpu.matrix.get_projection_matrix(), ctx.region_data, CURVE_OFFSET))
            _draw_curves(b, g, region, lw, alpha)
            _draw_anchors(b, region, lw, alpha)
        finally:
            if pushed:
                gpu.matrix.pop_projection()
        if pushed and alpha >= 1.0 and 'over' in b:
            _draw_over(b['over'], ctx.region_data, region, lw)
    gpu.state.depth_test_set('NONE')
    if passes[0][0] != 'NONE':
        # 3.8c: the selection never hides — the selected curves and anchors again without depth test, at SEL_THROUGH (a
        # curve on the back layer of a lock, behind its front walls, kept its anchors out of sight: the owner's report)
        if 'sel_lines' in b:
            sh = _line_shader()
            sh.bind()
            sh.uniform_float('viewportSize', (region.width, region.height))
            sh.uniform_float('lineWidth', SEL_LINE_PX * lw)
            sh.uniform_bool('lineSmooth', True)
            _draw_lines_faded(b, g, sh, SEL_THROUGH, 'sel_lines')
        if 'anchors' in b and b.get('any_sel'):
            sh = _anchor_shader()
            sh.bind()
            _ubo = _anchor_mats(sh)
            sh.uniform_float('opacity', SEL_THROUGH)
            sh.uniform_float('vh', float(region.height))
            sh.uniform_float('pr', lw)
            sh.uniform_float('mark', b['mark'])
            sh.uniform_float('selonly', 1.0)
            gpu.state.program_point_size_set(True)
            b['anchors'].draw(sh)
            gpu.state.program_point_size_set(False)
    if ghost is not None:
        from gpu_extras.batch import batch_for_shader
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_float('lineWidth', 1.0 * lw)
        sh.uniform_bool('lineSmooth', True)
        sh.uniform_float('color', GHOST)
        batch_for_shader(sh, 'LINES', {'pos': [ghost[0], ghost[1]]}).draw(sh)
    if wall_cut is not None:
        _draw_wall_cut(region, lw)
    if attach_dot is not None:
        try:
            _draw_dots(region, lw, [(attach_dot, ATTACH_DOT)])
        except Exception:                                           # (a backend without the anchors' sprite shader: no dot, the rest drawn)
            pass
    if edge_lines:
        _draw_edges(edge_lines, root, ctx.region_data, region, lw)
    try:                                                            # 4.2: the guides' zones and the Pencil's loop, on top
        _draw_zone(root, P, sel_now, region, lw)
        if pencil:
            _draw_pencil(region, lw)
    except Exception as e:
        errorlog.record('draw: guides')
        if not _outline_error:
            _outline_error.append(repr(e)); print('HairMesh guides drawing:', repr(e))
    gpu.state.blend_set('NONE')


def _draw_edges(lines, root, rv3d, region, lw):
    """4.9: [(batch, color, width)] in the root's local space, over the mesh they lie on (the curves' pull), and again
    through what hides them at SEL_THROUGH, as the selected curves"""
    import gpu
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    gpu.matrix.push()
    gpu.matrix.push_projection()
    try:
        gpu.matrix.multiply_matrix(root.matrix_world)
        if rv3d is not None:
            gpu.matrix.load_projection_matrix(offset_projection(gpu.matrix.get_projection_matrix(), rv3d, CURVE_OFFSET))
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_bool('lineSmooth', True)
        for depth, alpha in (('NONE', SEL_THROUGH), ('LESS_EQUAL', 1.0)):
            gpu.state.depth_test_set(depth)
            for batch, color, width in lines:
                sh.uniform_float('lineWidth', width * lw)
                sh.uniform_float('color', (color[0], color[1], color[2], color[3] * alpha))
                batch.draw(sh)
    finally:
        gpu.state.depth_test_set('NONE')
        gpu.matrix.pop_projection()
        gpu.matrix.pop()


def _draw_over(pulled, rv3d, region, lw):
    """the curves with a Profile modifier on screen and their anchors, again, under the projection that brings each out of
    its own surface (tube_pulls): depth-tested as the others, so the head further away than that still covers them. A click
    needs nothing: an anchor's pick has no occlusion and comes first (ops_select)"""
    import gpu
    gpu.matrix.push_projection()
    try:
        base = offset_projection(gpu.matrix.get_projection_matrix(), rv3d, CURVE_OFFSET)
        view = gpu.matrix.get_model_view_matrix()
        for e in pulled:
            near = -(view @ Vector(e['mid'])).z - e['size']         # (the curve's nearest end sets the pull: never less than `dist`)
            gpu.matrix.load_projection_matrix(pull_projection(base, e['dist'], max(near, 1e-6)))
            _draw_curves(e, None, region, lw, 1.0)
            _draw_anchors(e, region, lw, 1.0)
    finally:
        gpu.matrix.pop_projection()


def _draw_anchors(b, region, lw, alpha):
    """the anchors (the point shader of anchorMat), or the plain round points of a backend that refused it"""
    import gpu
    if 'anchors' in b:
        sh = _anchor_shader()
        sh.bind()
        _ubo = _anchor_mats(sh)
        sh.uniform_float('opacity', alpha)
        sh.uniform_float('vh', float(region.height))
        sh.uniform_float('pr', lw)
        sh.uniform_float('mark', b['mark'])
        sh.uniform_float('selonly', 0.0)
        gpu.state.program_point_size_set(True)
        b['anchors'].draw(sh)
        gpu.state.program_point_size_set(False)
    elif 'anchors_plain' in b:
        sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
        sh.bind()
        for (col, s), batch in b['anchors_plain']:
            gpu.state.point_size_set(s * lw)
            sh.uniform_float('color', (*col, alpha))
            batch.draw(sh)


def _draw_curves(b, g, region, lw, alpha):
    """the curves, then the selected ones again, thicker, over the others (3.8c: equal depth passes LESS_EQUAL); in the
    x-ray pass (alpha < 1) a faded copy of each batch (POLYLINE_SMOOTH_COLOR has no opacity uniform)"""
    for which, px in (('lines', LINE_PX), ('sel_lines', SEL_LINE_PX)):
        if which not in b:
            continue
        sh = _line_shader()
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_float('lineWidth', px * lw)
        sh.uniform_bool('lineSmooth', True)
        b[which].draw(sh) if alpha >= 1.0 else _draw_lines_faded(b, g, sh, alpha, which)


ATTACH_DOT = ((0.35, 1.0, 0.55), 12.0, 0.0)                        # 4.4: the Attach preview's dot (colour, size, circle)


def _draw_dots(region, lw, dots):
    """dots [(world point, (color, size, shape))] with the anchors' sprite shader (4.4: the Attach preview)"""
    import gpu
    from gpu_extras.batch import batch_for_shader
    sh = _anchor_shader()
    b = batch_for_shader(sh, 'POINTS', {'pos': [p for p, _l in dots], 'col': [(*l[0], 1.0) for _p, l in dots], 'size': [float(l[1]) for _p, l in dots],
                                       'shape': [float(l[2]) for _p, l in dots], 'kind': [0.0] * len(dots)})   # (kind: 3.8c's anchors shader)
    sh.bind()
    _ubo = _anchor_mats(sh)
    sh.uniform_float('opacity', 1.0); sh.uniform_float('vh', float(region.height)); sh.uniform_float('pr', lw)
    sh.uniform_float('mark', (1.0, 1.0, 1.0, 1.0)); sh.uniform_float('selonly', 0.0)
    gpu.state.program_point_size_set(True)
    b.draw(sh)
    gpu.state.program_point_size_set(False)


def _draw_wall_cut(region, lw):
    """the cut being placed on a wall (scutLine, scutPts): a red line over the wall, the anchors as dots"""
    import gpu
    from gpu_extras.batch import batch_for_shader
    line, dots = wall_cut
    if len(line) >= 2:
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_float('lineWidth', 3.0 * lw)
        sh.uniform_bool('lineSmooth', True)
        sh.uniform_float('color', CUT_LINE)
        batch_for_shader(sh, 'LINE_STRIP', {'pos': line}).draw(sh)
    if dots:
        _draw_dots(region, lw, [(p, CUT_DOTS[k]) for p, k in dots])


def _draw_guides(b, region, lw, alpha, fills=True):
    """the guides, writing no depth (every line material of the original has depthWrite false, newLineObj 1019; the
    disc's MeshBasicMaterial too): the discs' annuli (transparent, double-sided) in the depth-tested pass only — the
    original's x-ray pass draws layer 1, where only the lines are (1022) —, then the lines (the guide lines, the cut
    lines, the rings) at their widths. The depth mask is given back whatever happens."""
    import gpu
    gpu.state.depth_mask_set(False)
    try:
        if fills:
            sh = gpu.shader.from_builtin('UNIFORM_COLOR')
            sh.bind()
            gpu.state.face_culling_set('NONE')
            for rgba, batch in b['guide_fills']:
                sh.uniform_float('color', (*rgba[:3], rgba[3] * alpha))
                batch.draw(sh)
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_bool('lineSmooth', True)
        for px, rgba, batch in b['guide_strokes']:
            sh.uniform_float('lineWidth', px * lw)
            sh.uniform_float('color', (*rgba[:3], rgba[3] * alpha))
            batch.draw(sh)
    finally:
        gpu.state.depth_mask_set(True)


def _draw_walls(b, region, rv3d, lw, alpha):
    """The selected walls: the tinted fill, then the quad edges WALL_LINE_PX wide and smoothed, both under the
    projection pulled WALL_OFFSET units of polygon offset towards the camera (offset_projection), so in the
    depth-tested pass they read over the live mesh and over its wire; in the x-ray pass (no depth test) the pull
    changes nothing. The projection stack is restored whatever happens."""
    import gpu
    gpu.matrix.push_projection()
    try:
        if rv3d is not None:
            gpu.matrix.load_projection_matrix(offset_projection(gpu.matrix.get_projection_matrix(), rv3d, WALL_OFFSET))
        sh = gpu.shader.from_builtin('UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('color', (*WALL_FILL[:3], WALL_FILL[3] * alpha))
        b['wall_tris'].draw(sh)
        sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        sh.bind()
        sh.uniform_float('viewportSize', (region.width, region.height))
        sh.uniform_float('lineWidth', WALL_LINE_PX * lw)
        sh.uniform_bool('lineSmooth', True)
        sh.uniform_float('color', (*WALL_LINE[:3], WALL_LINE[3] * alpha))
        b['wall_lines'].draw(sh)
    finally:
        gpu.matrix.pop_projection()


def _draw_lines_faded(b, g, sh, alpha, which='lines'):
    """the x-ray pass of the curves: a batch with the faded colors, cached beside the main one"""
    from gpu_extras.batch import batch_for_shader
    key = ('faded', which, alpha)
    if key not in b:
        cols = g['line_colors'] if which == 'lines' else g['sel_line_colors']
        b[key] = batch_for_shader(sh, 'LINES', {'pos': g[which], 'color': [(*col, alpha) for col in cols]})
    b[key].draw(sh)


def overlay_points(shape):
    """The outline of the Select tool's overlay as 2D region points (pure): a box's four corners, or 48 points of a
    circle; None for nothing."""
    if shape is None:
        return None
    if shape[0] == 'box':
        _k, x0, y0, x1, y1 = shape
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if shape[0] in ('line', 'gline'):
        _k, x0, y0, x1, y1 = shape
        return [(x0, y0), (x1, y1)]
    if shape[0] in ('circle', 'paint'):
        import math
        _k, x, y, r = shape
        n = 48
        return [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return None


BANNER_COLORS = {'preview': (1.0, 0.55, 0.18, 1.0), 'baked': (0.92, 0.86, 0.76, 1.0), 'live': (0.55, 0.80, 1.0, 1.0)}
BANNER_BACK = (0.08, 0.08, 0.08, 0.85)                              # (dark enough to read the orange text over the orange preview)


def _draw_banner(ctx):
    """3.7: the line at the top of the viewport about the converted meshes (meshes.banner): a pending preview in
    orange, the Baked or Live view — so a Convert never looks as if nothing happened"""
    root = session.find_root(ctx)
    if root is None:
        return
    try:
        from . import meshes
        b = meshes.banner(session.get_project(root))
    except Exception:
        errorlog.record('draw: the banner')
        return
    if b is None:
        return
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    text, kind = b
    ui = pick.ui_scale(ctx)
    region = ctx.region
    blf.size(0, 13 * ui)
    w, h = blf.dimensions(0, text)
    pad = 8 * ui
    x = max(pad, (region.width - w) / 2)
    # under the header and the tool header when they overlap the viewport (Region Overlap: the WINDOW region runs
    # under them), else just under the top edge
    top = region.y + region.height
    over = [r for r in getattr(ctx.area, 'regions', ()) if r.type in ('HEADER', 'TOOL_HEADER') and r.height > 1 and r.y < top and r.y + r.height > top - 200]
    cover = max([top - r.y for r in over], default=0)
    y = region.height - cover - 18 * ui - h
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    sh.bind()
    sh.uniform_float('color', BANNER_BACK)
    x0, y0, x1, y1 = x - pad, y - pad * 0.75, x + w + pad, y + h + pad * 0.75
    batch_for_shader(sh, 'TRIS', {'pos': [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)]}).draw(sh)
    blf.color(0, *BANNER_COLORS.get(kind, (1.0, 1.0, 1.0, 1.0)))
    blf.position(0, x, y, 0)
    blf.draw(0, text)
    gpu.state.blend_set('NONE')


@slow.timed('the viewport drawn (the banner)')
def _draw_2d():
    """POST_PIXEL: the banner about the converted meshes (3.7), and the marquee box or the Circle Select circle, in
    region pixels (while the tool is active)."""
    ctx = bpy.context
    if not owner.visible(ctx) or not tool.is_active(ctx) or not overlays_on(ctx):
        return
    _draw_banner(ctx)
    if overlay is None:
        return
    pts = overlay_points(overlay)
    if not pts:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    pts = [(x, y, 0.0) for x, y in pts]                             # UNIFORM_COLOR's `pos` is a vec3 (2D pairs are padded, but be explicit)
    region = ctx.region
    lw = pick.line_scale(ctx)
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    sh.bind()
    if overlay[0] == 'box':
        sh.uniform_float('color', OVERLAY_FILL)
        batch_for_shader(sh, 'TRIS', {'pos': [pts[0], pts[1], pts[2], pts[0], pts[2], pts[3]]}).draw(sh)
    elif overlay[0] == 'line':
        # the Scissors' stroke: a red line (2 px) over a dark shadow, not closed
        for col, off, w in ((OVERLAY_SHADOW, 1.0, 3.0), (STROKE, 0.0, 2.0)):
            polyline2d(region, [(x + off * lw, y - off * lw) for x, y, _z in pts], col, w * lw, 'LINES')
        gpu.state.blend_set('NONE')
        return
    elif overlay[0] == 'gline':
        # 4.2: the Guides' Line stroke, the original's dashed green line (#guideStroke, 466: 2 px, dashes 6 4)
        ui = pick.ui_scale(ctx)
        dashes = dash_segments(overlay[1], overlay[2], overlay[3], overlay[4], GUIDE_DASH[0] * ui, GUIDE_DASH[1] * ui)
        if dashes:
            for col, off, w in ((OVERLAY_SHADOW, 1.0, 3.0), (GUIDE_STROKE, 0.0, 2.0)):
                polyline2d(region, [(x + off * lw, y - off * lw) for x, y in dashes], col, w * lw, 'LINES')
        gpu.state.blend_set('NONE')
        return
    elif overlay[0] == 'paint':
        # the paint cursor: a filled, semi-transparent blue disc with a blue rim, no shadow
        cx, cy = overlay[1], overlay[2]
        sh.uniform_float('color', PAINT_FILL)
        batch_for_shader(sh, 'TRI_FAN', {'pos': [(cx, cy, 0.0), *pts, pts[0]]}).draw(sh)
        polyline2d(region, [*pts, pts[0]], PAINT_LINE, lw)
        gpu.state.blend_set('NONE')
        return
    # the outline twice, a dark line one line width off under a white one, so it reads on a light head as on the dark background
    straight = overlay[0] == 'box'
    polyline2d(region, [(x + lw, y - lw) for x, y, _z in [*pts, pts[0]]], OVERLAY_SHADOW, lw, smooth=not straight)
    polyline2d(region, [*pts, pts[0]], OVERLAY_LINE, lw, smooth=not straight)
    gpu.state.blend_set('NONE')


def register():
    global _handle, _handle_2d
    _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    _handle_2d = bpy.types.SpaceView3D.draw_handler_add(_draw_2d, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle, _handle_2d, overlay, snap_curve, ghost, weld_cand, drawing, wall_cut
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    if _handle_2d is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle_2d, 'WINDOW')
        _handle_2d = None
    overlay = None; snap_curve = None; ghost = None; weld_cand = None; drawing = None; wall_cut = None
    global pencil, zone_flash
    pencil = None; zone_flash = None; _zone_cache.clear()
    _cache.clear()
    _shaders.clear()
    clear_outlines()
