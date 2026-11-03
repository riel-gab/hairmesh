# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Data model of a HairMesh project: the scene the original keeps in `items`, the anchors and `S`
(hair-modeler.clean.html:691-726), as dataclasses.

All dataclasses use eq=False: the original compares anchors and items with `===` and uses them as Map / Set keys,
so identity semantics are required (see docs/js-para-python.md). `items` is a dict in file order, like the
original's Map: several algorithms return "the first curve that matches".
Coordinates are the original's: Y up, head units.
"""
from dataclasses import dataclass, field
from typing import Any

from .vec3 import Vec3

# conversion parameters a group may override (CONV_KEYS of the original)
CONV_KEYS = ('step', 'dev', 'straighten', 'loopOrient', 'tipMode', 'relax', 'mergeTrunks')
# defaults merged into cap / skin items on load (hair-modeler.clean.html:8546, 8679)
CAP_DEFAULTS = {'mode': 'quad', 'start': True, 'end': True, 'size': 0, 'segs': 2, 'round': 1}
SKIN_DEFAULTS = {'open': False, 'bulge': 0}


@dataclass(eq=False, kw_only=True)
class Attachment:
    """a.att = { c, t }: the anchor sits on crown connector curve `c` at arc fraction `t`."""
    c: int
    t: float


@dataclass(eq=False, kw_only=True)
class Anchor:
    # port of class Anchor (hair-modeler.clean.html:712) plus what restore() adds (1232-1233)
    id: int
    pos: Vec3
    smooth: float = 1.0
    pass_: list[int] | None = None       # a.pass: [id, id], the two directions passing smoothly through a junction (passRule, 4885)
    att: Attachment | None = None
    attPos: Vec3 | None = None
    sec: float | None = None             # a.sec (1.0.1, file key `sc`): the cross-section an anchor of a converted modifier's edge lies in (lockSecs, 1435)


@dataclass(eq=False, kw_only=True)
class Item:
    id: int
    type: str
    name: str
    parent: int | None = None
    visible: bool = True
    q: list[float] | None = None         # own rotation (gizmo Local axes)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)   # the item's JSON as read, nothing lost


@dataclass(eq=False, kw_only=True)
class Group(Item):
    children: list[int] = field(default_factory=list)
    color: str | None = None
    expanded: bool = True
    sym: dict | None = None
    guides: str = 'auto'
    conv: dict | None = None             # per-group conversion parameters (groupConv fills defaults lazily, phase 2)
    guidesGroup: bool = False
    meshesGroup: bool = False
    importGroup: bool = False
    labels: dict | None = None
    guidesOff: bool = False


@dataclass(eq=False, kw_only=True)
class HandleOverride:
    """c.ovr entry: frozen handle offsets from the anchor (`in` is a Python keyword, hence in_)."""
    in_: Vec3 | None = None
    out: Vec3 | None = None


@dataclass(eq=False, kw_only=True)
class Sample:
    p: Vec3
    seg: int


@dataclass(eq=False, kw_only=True)
class Curve(Item):
    anchors: list[Anchor] = field(default_factory=list)
    ovr: dict[int, HandleOverride] = field(default_factory=dict)   # Map anchor id -> override, insertion order
    closed: bool = False
    ring: int | None = None              # id of the NURBS disc this ring belongs to
    round: float | None = None           # Roundness 0..1
    fair: float | None = None            # Smoothing 0..1
    asm: float | None = None             # Even out anchors slider value
    asrc: list[Vec3] | None = None       # anchor positions before Even out
    junc: bool = False                   # junction curve (Unite locks)
    conn: str | None = None              # crown connector: 'part' | 'low'
    para: bool = False                   # 1.0.1: an edge of a converted modifier (convertTube, 6758) — the `para` branch of buildRowTables (2601)
    # computed by rebuildCurve (1325)
    samples: list[Sample] = field(default_factory=list)
    arc: list[float] | None = None
    dirty: bool = True
    fair_cache: Any = field(default=None, repr=False)   # c._fair (fairSpline, 936)
    box_cache: Any = field(default=None, repr=False)    # (the samples list, its bounding box) for rebuildDisc's prefilter (ours, 16/10/2026)
    sample_key: Any = field(default=None, repr=False)   # what the samples were made from (curves.sampleKey; ours, lot ux8): the same key keeps them


@dataclass(eq=False, kw_only=True)
class Surface(Item):
    a: int = 0
    b: int = 0
    p: dict = field(default_factory=dict)
    dirty: bool = True
    twin: bool = False                   # the second surface of a wall (twinWall, 2862): back from b to a


@dataclass(eq=False, kw_only=True)
class Guide(Item):
    kind: str = 'line'
    hits: list[dict] = field(default_factory=list)
    p: dict = field(default_factory=dict)
    rings: list[int] | None = None
    off: bool = False
    samples: list = field(default_factory=list)
    dirty: bool = True
    # set by rebuildDisc (1140): where the hair curves pierce the disc
    hitPts: dict | None = None           # curve id -> Vec3 (a Map in the original)
    center: Vec3 | None = None
    nrm: Vec3 | None = None
    segCount: int | None = None


@dataclass(eq=False, kw_only=True)
class Crown(Item):
    part: int | None = None
    low: int | None = None
    p: dict = field(default_factory=dict)
    dirty: bool = True
    info: dict | None = None             # k.info of crownData: { loops, rings, part }
    dbg: dict | None = None              # k._dbg of crownData: vid, e, onCurve, loopsU, idx, paths (for tests)
    sym_last: Any = field(default=None, repr=False)   # k._symLast of crownSymSync (4.3): {anchor id: pos} at the last sync, never in the file


@dataclass(eq=False, kw_only=True)
class Cap(Item):
    p: dict = field(default_factory=dict)
    srcSurfs: list[int] = field(default_factory=list)


@dataclass(eq=False, kw_only=True)
class Skin(Item):
    p: dict = field(default_factory=dict)
    cands: list = field(default_factory=list)


@dataclass(eq=False, kw_only=True)
class Tip(Item):
    """Density modifier: p = { dens, v }, mask = { curve id: [weight per sample] }."""
    p: dict = field(default_factory=dict)
    mask: dict | None = None


@dataclass(eq=False, kw_only=True)
class Tube(Item):
    src: int | None = None
    profile: list[list[float]] = field(default_factory=list)
    profs: list[list[list[float]]] | None = None   # 1.0.1: the three profiles (start, middle, end); `profile` is profs[0], the same list (restore, 1254)
    p: dict = field(default_factory=dict)
    dirty: bool = True
    cl: list | None = field(default=None, repr=False)   # it.cl (tubeRings, 6421): the (displaced) centre line, the mask brush paints along it; never in the file


@dataclass(eq=False, kw_only=True)
class XMod(Item):
    tube: int | None = None
    kind: str | None = None
    p: dict = field(default_factory=dict)
    mask: list | None = None
    mesh: int | None = None


@dataclass(eq=False, kw_only=True)
class IMesh(Item):
    src: Any = None
    dirty: bool = True


@dataclass(eq=False, kw_only=True)
class MeshData:
    pos: list[Vec3] = field(default_factory=list)
    faces: list[list[int]] = field(default_factory=list)
    topo: Any = field(default=None, repr=False)   # ours: edges.edgeTopo's memo (EDGE_TOPO, a WeakMap on this data; never in the file)
    world: Any = field(default=None, repr=False)   # ours: edges.edgeWorld's memo (it._ew; never in the file)


@dataclass(eq=False, kw_only=True)
class MeshItem(Item):
    data: MeshData | None = None
    pending: bool = False
    srcSurfs: list[int] = field(default_factory=list)
    srcCrowns: list[int] = field(default_factory=list)
    symBaked: bool = False
    token: str | None = None             # ours (3.7): names this data; rebuilt data gets a new one (never in the file)
    pathGroup: int | None = None         # the group Path -> curve fills (edgeCurveGroup 3854; as in the original, not in the snapshot: gone after an undo or a reopen)
    bbox: Any = field(default=None, repr=False)   # the original's m.bbox (4586): (data, min, max) of Box3.setFromPoints, cleared by the bake (never in the file)


@dataclass(eq=False, kw_only=True)
class Head:
    """The reference head: src = the imported file itself ({ ext, text } or { ext, b64 })."""
    id: int
    name: str
    visible: bool = True
    showWire: bool = False
    src: dict | None = None


@dataclass(eq=False, kw_only=True)
class Settings:
    """Project-wide conversion defaults (S.step … S.mergeTrunks, hair-modeler.clean.html:697-700) and view prefs."""
    step: float | None = None
    dev: float = 0.3
    straighten: float = 1
    loopOrient: float = 0.8
    tipMode: str = 'point'
    relax: float = 0
    mergeTrunks: bool = True
    convChecked: list[int] = field(default_factory=list)   # a Set in the original; kept in file order
    guideDefaults: dict = field(default_factory=lambda: {'density': 1, 'radius': None, 'blend': 1, 'local': True, 'priority': 0})
    xray: float = 0.2
    guideOpacity: float = 1
    brushMode: str = 'screen'


@dataclass(eq=False, kw_only=True)
class SceneState:
    """The geometric part of S that fitToBox (4184) derives from the head's bounding box.

    The defaults are the start-up frame (4.1): the original starts with fitToBox(Box3((-0.5, 0, -0.5), (0.5, 1, 0.5)))
    (hair-modeler.clean.html:12391, and New project, 10133): scale 1, headH 1, yRef 1 and center (0, 0.5, 0) —
    box.getCenter, (min + max) * 0.5. S's own initial value (center: new V3(), 699) never survives the start-up.
    Until 4.1 the center here was (0, 0, 0): a project with neither a head nor an anchor (a parity script without a
    head, a New project in Blender) oriented its two-curve walls from the wrong point (surfaceData, 2843) and mirrored
    a symmetric group about y = 0 instead of y = 0.5 (mirrorPt, 9824) — the divergence found in 3.8b."""
    scale: float = 1.0
    headH: float = 1.0
    yRef: float = 1.0
    center: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.5, 0.0))


@dataclass(eq=False, kw_only=True)
class Project:
    name: str = ''
    app: str = 'anime-hair-modeler'
    version: int = 1
    roots: list[int] = field(default_factory=list)
    items: dict[int, Item] = field(default_factory=dict)        # file order, like the original Map
    anchors: dict[int, Anchor] = field(default_factory=dict)    # every anchor by id; curves share the instances
    head: Head | None = None
    settings: Settings = field(default_factory=Settings)
    S: SceneState = field(default_factory=SceneState)
    counters: dict = field(default_factory=dict)
    camera: dict | None = None
    credits: list | None = None
    uid: int = 1                                                 # UID: the next free id
    stripped: list[str] = field(default_factory=list)            # top-level keys dropped on read (refs, thumb)
    gen: int = 0                                                 # bumped by every structural change (items, parents, curve anchors, a group's conv.step): memos key on it
    rev: int = 0                                                 # bumped by every change that alters what a viewport shows (positions, samples, every save): drawing caches key on it
    _memo: dict = field(default_factory=dict, repr=False)        # per-project caches (usedStep), keyed on gen
    conv: dict | None = None                                     # S.conv (10024): {entries, hidden} of a pending conversion (+ ours, not in the file: replaced, shown)

    # ---- the original's scene helpers (hair-modeler.clean.html:714-726) ----
    def childrenOf(self, pid):
        # port of childrenOf (714)
        return self.roots if pid is None else self.items[pid].children

    def parentOf(self, it):
        # port of parentOf (715)
        return self.items.get(it.parent) if it.parent is not None else None

    def of_type(self, type_name):
        return [it for it in self.items.values() if it.type == type_name]

    def curves(self): return self.of_type('curve')        # port of curves (717), file order

    def anchor_curves(self):
        """anchor id -> the curves using it, in curves() order, each curve once (a closed curve lists its first
        anchor twice). Curves share the Anchor instances of `anchors`, so "h.anchors includes a" is "h in
        anchor_curves()[a.id]", and scanning this list instead of every curve finds the same first match.
        Memoised on (gen, item count): bump `gen` after adding or removing curves or changing a curve's anchors."""
        key = (self.gen, len(self.items))
        m = self._memo.get('anchor_curves')
        if m is None or m[0] != key:
            out = {}
            for c in self.curves():
                for a in c.anchors:
                    lst = out.setdefault(a.id, [])
                    if not lst or lst[len(lst) - 1] is not c:
                        lst.append(c)
            m = (key, out); self._memo['anchor_curves'] = m
        return m[1]
    def surfaces(self): return self.of_type('surface')    # 718
    def guides(self): return self.of_type('guide')        # 719
    def tubes(self): return self.of_type('tube')          # 723
    def crowns(self): return self.of_type('crown')        # 725
    def caps(self): return self.of_type('cap')            # 726
    def groups(self): return self.of_type('group')
