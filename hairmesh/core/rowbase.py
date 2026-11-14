# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.3 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Types and context shared by the row-table code (rows.py, trunks.py, junction.py, density.py, guides.py).

The original keeps this state in module globals (CP, TIPZ, TRUNK_PTS, TRUNK_KEY, TRUNK_LOCKS, ANCHOR_CURVES,
LOCK_CACHE, DBG; hair-modeler.clean.html:1426-1440, 1513, 2501); here it lives in a RowBuild passed explicitly.

JavaScript optional fields of a row (`r.tm`, `r.free`, `r.fixed`...) are None when absent: an EMPTY Map or Set is
truthy in JS, so every `if (r.tm)` of the original is `if r.tm is not None` here, never `if r.tm`.
"""
from dataclasses import dataclass, field

from .geom import atF
from .hairmesh_io import defaultStep
from .js import clamp, js_or, js_round, truthy
from .model import CONV_KEYS
from .trace import dec as _dec
from .vec3 import V3, Vec3


@dataclass(eq=False, kw_only=True)
class Comp:
    """`comp` of a row table entry: the lock's loop centres and its number of curves (surfaceData, 2740)."""
    cent: list[Vec3] = field(default_factory=list)
    n: int = 0
    # 1.0.3 (buildRowTables 3828-3831): per loop — sh: a proxy shell holds it (weight 0.5+; backfillTrunk puts nothing in
    # between two of them), shSet: whose ring (the set, shellSets), shPl: the ring's loop plane; None on a comp the
    # row tables did not make
    sh: list | None = None
    shSet: list | None = None
    shPl: list | None = None


@dataclass(eq=False, kw_only=True)
class RowTable:
    """T.get(curve id) = { rows, comp }: the loop points along one curve of a lock."""
    rows: list[Vec3] = field(default_factory=list)
    comp: Comp = field(default_factory=Comp)


@dataclass(eq=False)
class Row:
    """One loop of a lock while its position is being decided (spineRows ... unifyCoincident).

    f: fractional index along the spine (0 .. NS-1). The other fields are the original's optional properties."""
    f: float = 0.0
    lvl: object = None
    plane: dict | None = None          # {'p': Vec3, 'n': Vec3}; p's identity keys loopPts (buildRowTables)
    plane0: dict | None = None
    tplane: dict | None = None
    fixed: dict | None = None          # guide hits: curve index -> f on that curve
    jfix: dict | None = None           # Y junction: curve index -> f
    guide: bool = False
    local: bool = False
    gid: int | None = None
    aligned: bool = False
    pts: list | None = None            # the shared loop's points (one list shared by every lock using the loop)
    open: bool = False
    trunk: bool = False
    lg: dict | None = None             # Set of guide ids (dict keys)
    pj: bool = False
    junc: bool = False
    tgt: float | None = None
    prio: float | None = None
    blend: list | None = None          # [{'g': Row (guide row), 'w': weight}]
    tm: dict | None = None             # trunk match: curve index -> f (shared with the level's `at` map)
    free: dict | None = None           # Set of curve indices (dict keys)
    exact: bool = False
    exactC: dict | None = None         # Set of curve indices
    gT: dict | None = None             # curve index -> f of the guide shape
    gW: dict | None = None             # curve index -> weight of that shape
    inserted: dict | None = None       # {'a': Row, 'b': Row, 's': share}
    added: bool = False
    # 1.0.2: the ring the loop belongs to (buildRowTables: L.rowSrc) — `ring` on an aligned loop copied from a neighbour's
    # plane (spineRows), `src` on a loop taken from a trunk level (trunkLevels); compared by identity, like the original
    ring: object = None
    src: object = None
    tkeys: dict | None = None          # the trunk level's neighbour loop keys (Set, dict keys): gapEnd reads their ringB
    tgap: bool = False                 # cut through both ends of a neighbour's gap (trunkLevels: gapEnd)
    forkC: dict | None = None          # Set of curve indices whose point forkSnap moved onto an anchor (dict keys)
    shell: float | None = None         # 1.0.3: the weight of the proxy shell / Even loops ring the loop is (shellAdopt, 3362)
    shellSet: dict | None = None       # 1.0.3: whose ring (the shell's set of this lock, shellSets)
    shellRing: dict | None = None      # 1.0.3: the ring itself (shellAdopt's copy of the set's plane, with f / d)
    shellFree: bool = False            # 1.0.3: a Y junction loop the shell let go of (shellAdopt 3399)
    adopted: bool = False              # 1.0.3: a loop shellAdopt put in at a ring (3460)


class Ring:
    """1.0.2: a loop's ring `{ owner: L.key }` (buildRowTables, hair-modeler.clean.html:2923). Compared by identity (a JS
    object in a Set), never by value: two rings of one lock are two rings."""
    __slots__ = ('owner',)

    def __init__(self, owner):
        self.owner = owner

    def __repr__(self):
        return 'Ring(%r)@%x' % (self.owner, id(self))


@dataclass(eq=False, kw_only=True)
class Lock:
    """The lock descriptor L of buildRowTables (2531; 2620 in 1.0.1): the curves, their resampled polylines and the spine."""
    cs: list = field(default_factory=list)
    Ps: list = field(default_factory=list)
    sp: list = field(default_factory=list)
    len: float = 0.0
    rad: float = 0.0
    topo: object = None
    key: str = ''
    para: bool = False                 # a converted modifier's lock (1.0.1, 2601): its curves resampled by section, loops index to index
    planes: list | None = None
    rowGid: list | None = None
    allPts: list | None = None
    trunkSpans: dict | None = None
    segSet: set | None = None          # cache of trunkLevels' segsOf
    Pidx: list | None = None           # polyIndex of every curve of Ps (nearestF's stretch boxes), None when unknown
    spIdx: list | None = None          # polyIndex of sp
    plen: list | None = None           # polyLen of every curve of Ps (the loop passes divide it by N - 1 for `unit`)
    spLen: float | None = None         # polyLen of sp
    grids: dict = field(default_factory=dict)   # 1.0.3: nearestWithin's grids of this lock's polylines (POLY_GRID, 2537), id(P) -> (P, grid)
    rowSrc: list | None = None         # 1.0.2: the ring (Ring) of each loop (buildRowTables 2923)
    ringB: list | None = None          # 1.0.2: each loop's points on the lock's open edges (buildRowTables 2938; gapEnd)
    shellSpan: list | None = None      # 1.0.3: [f, f] from the loop before the first ring a proxy shell holds to the one after its last (buildRowTables 3776)


def settings_conv(P):
    """S as a conversion-parameter dict (CP = S for curves outside groups)."""
    return {k: getattr(P.settings, k) for k in CONV_KEYS}


def proxyOn(v):
    """the Detail proxy of a conv value as buildRowTables reads it (1.0.3, 3729: `CP.proxy !== false`): only false
    switches it off — a missing key, null and 0 leave it on; the box shows this (the review of phase 2, finding b)"""
    return v is not False


def groupConv(P, g):
    # port of groupConv (hair-modeler.clean.html:1589): a group's conv, defaults filled in from S
    if g.conv is None:
        g.conv = {}
    # projects from before the reference cube stored a density (loops per head height) instead of a step
    if g.conv.get('step') is None:
        g.conv['step'] = P.S.headH / g.conv['density'] if truthy(g.conv.get('density')) else defaultStep(P)
        P.gen += 1                                                  # (usedStep reads the groups' steps: its memo is stale now)
    for k in CONV_KEYS:
        if k not in g.conv:                                         # (=== undefined: a null stays null)
            # (so a group's mergeDist: null is Auto even when the project has a number: mergeTrunks reads
            # CP.mergeDist > 0 ? CP.mergeDist : null, hair-modeler.clean.html:12353; convert.mergeTrunks)
            g.conv[k] = getattr(P.settings, k)
    return g.conv


def convOf(P, it):
    # port of convOf (hair-modeler.clean.html:1596): the nearest group's conv, else S
    p = P.parentOf(it) if it is not None else None
    while p:
        if p.type == 'group':
            return groupConv(P, p)
        p = P.parentOf(p)
    return settings_conv(P)


class RowBuild:
    """The state of one buildRowTables run (the original's globals)."""

    def __init__(self, P):
        self.P = P
        self.CP = settings_conv(P)                                  # the parameter set of the lock being built
        self.TIPZ = None                                            # density zone of the lock being built
        self.DETZ: dict | None = None                               # 1.0.3: lockDetail of the lock being built (spineRows; DETZ, 2786)
        self.TRUNK_PTS = None                                       # loop points of the welded locks built so far
        self.TRUNK_KEY = {}                                         # loop point (by identity) -> 'lock:row'
        self.TRUNK_LOCKS = None                                     # the locks built so far (the `done` list)
        self.ANCHOR_CURVES = None                                   # anchor id -> curves using it
        self.LOCK_CACHE = {}                                        # lock key -> {'L', 'T', 'rowOf', 'dbg'}
        self.LOCKS_REBUILT = None
        self.LOCKS_RESTORED = set()                                 # ours: the curves whose locks came back from HISTORY (rowhist.py)
        self.HISTORY = None                                         # ours, opt-in: {lock key: {fingerprint: LOCK_CACHE entry}} (rowhist.py)
        self.HIST_SAMPLES = {}                                      # ours: the shape digests by curve (rowhist.py)
        self.LOCK_FP = {}                                           # ours (lot 1.1e): lock key -> the fingerprint (rowhist.component_fp) its entry was built or taken back under; none: not known
        self.ROWS_V = 0
        self.MODEL_CHANGED = False                                  # ours: the last build changed the model itself (tipConvert 3843, the sections written 2604): the caller saves it
        self.STALE_CURVES = set()                                   # ours: the curves of the locks the last build built from an item it changed afterwards (tipConvert)
        self.DBG = {'locks': [], 'mergeOpts': {}, 'mergeWhy': {}, 'rowKinds': {}, 'trace': None, 'traceOut': {}}
        self.T = None
        self.locks = []


def levelH(ctx) -> float:
    # port of levelH (hair-modeler.clean.html:1597): CP.step || defaultStep()
    return js_or(ctx.CP.get('step'), defaultStep(ctx.P))


# Plane that cuts one loop: through the spine point, normal = blend of vertical (horizontal loops) and the
# spine tangent (loops perpendicular to the lock), set by "Loop orientation"
def rowPlane(CP, sp, f):
    # port of rowPlane (hair-modeler.clean.html:1618); CP = the conversion parameters in use (loopOrient)
    N = len(sp); i0 = clamp(js_round(f), 1, N - 2)
    t = sp[i0 + 1].clone().sub(sp[i0 - 1])
    if _dec('rowPlane.tLengthSq', t.lengthSq(), 1e-16) < 1e-16:
        return None
    t.normalize()
    w = CP['loopOrient']
    n = V3(0, 1 if t.y >= 0 else -1, 0).multiplyScalar(1 - w).addScaledVector(t, w)
    return None if _dec('rowPlane.nLengthSq', n.lengthSq(), 1e-10) < 1e-10 else {'p': atF(sp, f), 'n': n.normalize()}
