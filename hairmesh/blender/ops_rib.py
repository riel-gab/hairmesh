# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""1.1f: the flat locks' sheets and actions (1.0.2: ribCurveProps 7077, ribAnchorProps 7055, Convert 6150-6204,
Plain curve 6147) on the Blender side; the data is core/rib.py.

The curve sheet of a flat lock gets a block of its own — Flat Lock, or Volume Branch (N Anchors) —: Profile Smoothing
and Tip; on a flat lock (not a branch) the three brush sections, Extrude (Invert, Depth, Fade to Tip), Offset (Fade to
Tip) and Glue, each with its brush button (on while it runs) and Clear, the brush's Strength / Intensity while it runs;
then Convert to Curves & Walls, the main action, and Plain Curve. The anchor sheet gets Flat Lock at the Anchor: Width
(the whole width, a length) and Rotation (degrees); at the root of a lock started from anchors both are dimmed with
the original's reason (its shape follows them, 6093). The sliders are views on the model on the WindowManager, as the
other sheets' (deferred.py: one undo step per change, when the interaction ends; lot 1.2a, item 8: the ribbon itself
follows every step of a drag, reshape — only a display, and cheap); what would change
nothing now is dimmed with the reason (core/effects.py).

The original's sliders edit the lock of the panel alone (renderProps shows the last selected item): here the last
selected curve with a lock; the anchor sliders every selected anchor of every lock through it (ribAnchorSet)."""
import math

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

from ..core import effects as E, rib as RB
from ..core.js import truthy
from ..core.selection import Selection
from . import live, owner, select, session
from .coords import f32, field_same
from .props import add_unit_twins

KINDS = (('EXT', 'ext'), ('OFF', 'off'), ('GLUE', 'glue'))
KIND_OF = dict(KINDS)                                               # the operators' enum -> rib's kind
ENUM_OF = {v: k for k, v in KINDS}
NAMES = {'ext': 'Extrude', 'off': 'Offset', 'glue': 'Glue'}
BRUSH_TIPS = {'ext': 'LMB extrudes, Ctrl erases, Shift smooths',
              'off': 'LMB moves the extruded edge nearest the brush in (orange; at 100% onto the other edge), Ctrl out (blue; up to one more width), Ctrl+Alt erases, Shift smooths. The two edges are independent',
              'glue': 'LMB closes the top face of the extrusion (green; at 100% its two edges become one: a Y profile), Ctrl erases, Shift smooths'}
NEEDS_EXTRUDE = 'Extrude must be painted first: this moves the extruded edges'
ROOT_WHY = 'The root spans the {n} anchors it started from: its shape follows them'   # (6093)


# ---------------- what the sheets read ----------------

def last_rib(P, sel):
    """the last selected curve with a lock (the panel's curve), or None"""
    for i in reversed(sel.items):
        c = P.items.get(i)
        if c is not None and c.type == 'curve' and truthy(c.rib):
            return c
    return None


def _current():
    root, P, sel = select.current(bpy.context)
    return root, P, sel, (last_rib(P, sel) if P is not None else None)


def _after(root, P=None, curves=()):
    """the end of a setter: lot 1.2a, item 8 — the ribbons of `curves` reshaped at once (reshape), then the undo step
    and the update when the interaction ends, as every slider of the sheets (deferred.py)"""
    from .props import _after_slide
    if P is not None:
        reshape(P, curves)
    _after_slide(root)


def reshape(P, curves):
    """lot 1.2a, item 8: the ribbons of these flat locks made again now, in memory (rebuildRib: c.rib_geom, which the
    overlay draws; no data of Blender's touched, nothing written) — so Width, Rotation and the lock's sliders follow a
    drag at every step, as the original's frame redraws the ribbon (14823). A ribbon is only a display (no conversion
    reads it) and costs a few ms (docs/interface.md): the exception to "heavy things wait for the release". The
    camera is the settle's own (none: a lock's `view`, or rib.DEFAULT_CAM_DIR), so the release's update finds this very
    ribbon (its ribKey) and makes nothing. A root that followed its anchors (never by these sliders alone) leaves its
    curves dirty and the ribbon to the release's update, as the original's next frame: nothing sampled behind the
    row tables' back.
    Only while it is cheap: a lock whose last reshape in this drag took more than RESHAPE_BUDGET (the smoothing is
    quadratic in the samples: 12 ms at 5 anchors, 130 ms at 15 on the example, docs/interface.md) waits for the
    release, as before; the costs last one interaction (deferred.ended), so the next drag measures again"""
    import time
    import weakref
    from . import deferred
    if _COST_OF[0] != deferred.ended() or _COST_OF[1] is None or _COST_OF[1]() is not P or len(_COST) > 512:
        _COST.clear()                                               # (a new interaction or another model: measured anew)
        _COST_OF[:] = [deferred.ended(), weakref.ref(P)]
    seen = set()
    for c in curves:
        if c.id in seen or not truthy(c.rib) or not c.ribDirty:
            continue
        seen.add(c.id)
        key = (c.id, len(c.samples or ()))                          # (another length: measured anew)
        if key in _COST and _COST[key] > RESHAPE_BUDGET:
            continue                                                # (still dirty: the release's update makes it)
        t0 = time.perf_counter()
        if RB.rebuildRib(P, c) is not None:                         # (the root moved: its curves dirty, the ribbon the next frame's)
            c.ribDirty = True
        _COST[key] = time.perf_counter() - t0


RESHAPE_BUDGET = 0.030                                              # seconds a step of a drag may spend on a ribbon (the owner's 30 ms)
_COST = {}                                                          # (curve id, samples) -> the seconds its last reshape took, this interaction
_COST_OF = [None, None]                                             # (deferred.ended() when _COST began, a weak reference to its model)


def _curve_prop(key):
    def get(self):
        _r, _P, _s, c = _current()
        return float(RB.ribPropValue(c, key)) if c is not None else float(RB.PROP_DEFAULTS[key])

    def set(self, v):
        root, _P, _s, c = _current()
        if c is None or field_same(RB.ribPropValue(c, key), f32(v)):
            return
        RB.ribProp(c, key, f32(v))
        _after(root, _P, [c])
    return get, set


def _get_inv(self):
    _r, _P, _s, c = _current()
    return truthy(c.rib.get('extInv')) if c is not None else False


def _set_inv(self, v):
    root, P, _s, c = _current()
    if c is None or bool(v) == truthy(c.rib.get('extInv')):
        return
    RB.ribInvert(c)
    _after(root, P, [c])


def _anchor_panel():
    root, P, sel = select.current(bpy.context)
    return root, P, sel, (RB.ribAnchorPanel(P, sel.anchors) if P is not None else None)


def _get_aw(self):
    _r, _P, _s, d = _anchor_panel()
    return float(d['w'] * 2) if d is not None else 0.0


def _set_aw(self, v):
    root, P, sel, d = _anchor_panel()
    if d is None or d['at_root'] or field_same(d['w'] * 2, f32(v)):
        return
    RB.ribAnchorSet(P, sel.anchors, 'w', f32(v) / 2)                # (the field shows the whole width, the lock keeps its half: fmtLen(x * 2), 6095)
    _after(root, P, [c for _a, c in RB.ribAnchorList(P, sel.anchors)])


def _get_ar(self):
    _r, _P, _s, d = _anchor_panel()
    return math.radians(d['r']) if d is not None else 0.0


def _set_ar(self, v):
    root, P, sel, d = _anchor_panel()
    deg = f32(math.degrees(v))
    if d is None or d['at_root'] or field_same(d['r'], deg):
        return
    RB.ribAnchorSet(P, sel.anchors, 'r', deg)                      # (the original keeps degrees, -180..180, 6096)
    _after(root, P, [c for _a, c in RB.ribAnchorList(P, sel.anchors)])


class HairMeshRibProps(PropertyGroup):
    """The flat lock sheets' sliders: views on the model on the WindowManager (deferred.py), nothing stored here"""
    rib_smooth: FloatProperty(name='Profile Smoothing', description='Even out the width and the rotation along the lock: 0 keeps them exactly as set at the anchors, more makes smoother changes between them', min=0.0, max=1.0, subtype='FACTOR', get=_curve_prop('smooth')[0], set=_curve_prop('smooth')[1])
    rib_tip: FloatProperty(name='Tip', description='Set the share of the length over which the tip narrows to a point (0: a cut end)', min=0.0, max=0.8, subtype='FACTOR', get=_curve_prop('tip')[0], set=_curve_prop('tip')[1])
    rib_ext_inv: BoolProperty(name='Invert', description='Push the extrusion out to the other side of the ribbon', get=_get_inv, set=_set_inv)
    rib_ext_h: FloatProperty(name='Depth', description='Set how far 100% of the Extrude weight pushes the ribbon out, in half widths of the lock', min=0.0, max=3.0, precision=2, step=1, get=_curve_prop('extH')[0], set=_curve_prop('extH')[1])
    rib_ext_fade: FloatProperty(name='Fade to Tip', description='Set the share of the length at the tip over which the extrusion dies out', min=0.0, max=1.0, subtype='FACTOR', get=_curve_prop('extFade')[0], set=_curve_prop('extFade')[1])
    rib_off_fade: FloatProperty(name='Fade to Tip', description='Set the share of the length at the tip over which the offset dies out', min=0.0, max=1.0, subtype='FACTOR', get=_curve_prop('offFade')[0], set=_curve_prop('offFade')[1])
    rib_anchor_w: FloatProperty(name='Width', description='Set the width of the lock at the selected anchors (Profile Smoothing evens it out along the lock)', min=0.0, soft_max=10000.0, precision=2, get=_get_aw, set=_set_aw)
    # (an angle in Blender's own unit: whole degrees by its step; a finer value typed shows the decimals it has)
    rib_anchor_r: FloatProperty(name='Rotation', description='Turn the lock around its middle line at the selected anchors (in between it turns gradually)', min=-math.pi, max=math.pi, subtype='ANGLE', precision=0, step=100, get=_get_ar, set=_set_ar)


add_unit_twins(HairMeshRibProps, 'rib_anchor_w')
RIB_ATTRS = frozenset(HairMeshRibProps.__annotations__)


# ---------------- the operators ----------------

def _poll(cls, context):
    if not (owner.visible(context) and session.find_root(context) is not None):
        return False
    return True


def _target(context, cid):
    root, P, sel = select.current(context)
    if P is None:
        return root, P, sel, None
    c = P.items.get(cid) if cid >= 0 else last_rib(P, sel)
    if c is None or c.type != 'curve' or not truthy(c.rib):
        return root, P, sel, None
    return root, P, sel, c


def convert_text(P, c):
    """what Convert makes of this lock (its tooltip): a flat lock, two curves and a wall; extruded, four and four; a
    volume branch, a curve per anchor it started from and as many walls (6142-6144)"""
    e = RB.ribEnds(P, c)
    if e and len(e) > 2:
        return (f'Make {len(e)} curves and {len(e)} walls round them (closed): a curve from every anchor the branch started on, '
                'along the branch and on back along that anchor\'s curve (a common trunk). The middle curve is deleted')
    if E.ribExtruded(c):
        return ('Make 4 curves and 4 walls round them (the base, two sides and the top): the edges of the ribbon and of '
                'its extrusion; where two of them meet (a glued top, a pointed tip) they share their anchors. The middle curve is deleted')
    return ('Make 2 curves along the edges and a flat wall between them' + ('; started from two anchors, the curves start on them' if e else '')
            + '. The middle curve is deleted')


class HAIRMESH_OT_rib_convert(bpy.types.Operator):
    """Turn the flat lock into curves and walls (the middle curve is deleted)"""
    bl_idname = 'hairmesh.rib_convert'
    bl_label = 'Convert to Curves & Walls'
    bl_options = {'REGISTER', 'UNDO'}

    curve: IntProperty(name='Curve', description='The flat lock (-1: the last selected one)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    @classmethod
    def description(cls, context, properties):
        _r, P, _s, c = _target(context, properties.curve)
        if c is None:
            return 'Select a flat lock: its curve is the middle line of the ribbon'
        if not E.ribConvertActs(c):
            return RB.NO_BRANCH if c.ribRings else RB.NO_SHAPE
        return convert_text(P, c)

    def execute(self, context):
        root, P, _sel, c = _target(context, self.curve)
        if c is None:
            self.report({'INFO'}, 'Select a flat lock')
            return {'CANCELLED'}
        assert root is not None and P is not None                   # (_target finds a lock only in a model)
        from .pick import model_cam
        cam = model_cam(root, context.region_data)[0] if getattr(context, 'region_data', None) is not None else None
        name = c.name
        made, msg = RB.ribConvert(P, c, cam)
        if not made:
            self.report({'WARNING'}, msg or RB.NO_SHAPE)
            session.forget_model(root.name)                         # (ribConvert may have rebuilt the ribbon in memory: the text holds the state)
            return {'CANCELLED'}
        select.store(context.scene.hairmesh, Selection([k.id for k in made], []))   # (S.asel = [], S.sel = them, 6203)
        from . import tool
        tool.show_selection(context)                                # (their sheet in sight under a sub-tool's page)
        walls = sum(1 for s in P.surfaces() if s.a in {k.id for k in made} or s.b in {k.id for k in made})
        session.save_project(root, P)
        live.touch(root, [k.id for k in made])
        live.update(context, root)
        select.redraw(context)
        self.report({'INFO'}, msg or f'{name}: {len(made)} curves and {walls} wall{"s" if walls != 1 else ""}')
        return {'FINISHED'}


class HAIRMESH_OT_rib_plain(bpy.types.Operator):
    """Drop the flat lock and keep its curve as a plain curve"""
    bl_idname = 'hairmesh.rib_plain'
    bl_label = 'Plain Curve'
    bl_options = {'REGISTER', 'UNDO'}

    curve: IntProperty(name='Curve', description='The flat lock (-1: the last selected one)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def execute(self, context):
        root, P, _sel, c = _target(context, self.curve)
        if c is None:
            self.report({'INFO'}, 'Select a flat lock')
            return {'CANCELLED'}
        assert root is not None and P is not None                   # (_target finds a lock only in a model)
        RB.ribPlain(c)
        P.rev += 1
        session.save_project(root, P)
        live.update(context, root)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_rib_clear(bpy.types.Operator):
    """Remove the weights painted in this section of the flat lock"""
    bl_idname = 'hairmesh.rib_clear'
    bl_label = 'Clear'
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name='Section', items=[(k, NAMES[v], '', i) for i, (k, v) in enumerate(KINDS)], default='EXT')
    curve: IntProperty(name='Curve', description='The flat lock (-1: the last selected one)', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    @classmethod
    def description(cls, context, properties):
        _r, _P, _s, c = _target(context, properties.curve)
        name = NAMES[KIND_OF[properties.kind]]
        if c is not None and not E.ribClearActs(c, KIND_OF[properties.kind]):
            return f'Nothing to clear: no {name} weights are painted on {c.name}'
        return f'Remove the {name} weights painted on the flat lock'

    def execute(self, context):
        root, P, _sel, c = _target(context, self.curve)
        kind = KIND_OF[self.kind]
        if c is None or not E.ribClearActs(c, kind):
            self.report({'INFO'}, f'No {NAMES[kind]} weights to clear')
            return {'CANCELLED'}
        RB.ribClear(c, kind)
        session.save_project(root, P)
        live.update(context, root)
        select.redraw(context)
        return {'FINISHED'}


# ---------------- the sheets' rows ----------------

def curve_block(context, P, c):
    """the block of a flat lock's curve sheet (ribCurveProps 7077): a 'block' row"""
    from . import brush, fit
    from .tool import GAP, block, buttons, dim, dim_op, field, item_need
    d = RB.ribCurvePanel(P, c)
    labels = ['Profile Smoothing', 'Tip'] if d['vol'] else ['Profile Smoothing', 'Tip', 'Extrude', 'Depth', 'Fade to Tip', 'Offset', 'Glue', 'Strength', 'Intensity']
    lw = max(fit.label_need(f'{t}:', False) for t in labels)    # (every name the block can show: a brush switched on moves nothing)

    def _field(label, item, lw, *more):
        return field(label, item, *more, lw=lw)
    rows = [[_field('Profile Smoothing', ('prop', 'rib_smooth', ''), lw)], [_field('Tip', ('prop', 'rib_tip', ''), lw)]]
    whys = []
    if not d['vol']:
        on = brush.running('RIB')
        on_kind = brush.RIB_KIND_OF[brush.props(context).brush_rib_kind] if on is not None else None
        ext = E.ribExtruded(c)
        inv = ('prop', 'rib_ext_inv', 'Invert', *dim(ext))
        tail = item_need(context, inv)                              # (the Invert after the Depth: the brushes end where its field does)

        def section(kind):
            acts = E.ribBrushActs(c, kind)
            b = ('op', 'hairmesh.brush', 'Brush', 'BRUSH_DATA', {'kind': 'RIB', 'rib': ENUM_OF[kind]}, {'depress': on_kind == kind, **({} if acts else {'active': False})})
            cl = dim_op(('op', 'hairmesh.rib_clear', 'Clear', 'X', {'kind': ENUM_OF[kind]}), E.ribClearActs(c, kind))
            out = [[_cols_brush(NAMES[kind], b, cl, lw, tail)]]
            if on_kind == kind:
                key = 'brush_rib_intensity' if kind == 'off' else 'brush_rib_strength'
                out.append([_field('Intensity' if kind == 'off' else 'Strength', ('prop', key, ''), lw)])
            return out
        rows += [[GAP], *section('ext'),
                 [_field('Depth', ('prop', 'rib_ext_h', '', *dim(ext)), lw, (item_need(context, inv), [inv]))],
                 [_field('Fade to Tip', ('prop', 'rib_ext_fade', '', *dim(ext)), lw)],
                 [GAP], *section('off'),
                 [_field('Fade to Tip', ('prop', 'rib_off_fade', '', *dim(E.ribOffFadeActs(c))), lw)],
                 [GAP], *section('glue')]
        if not ext:
            whys.append('Depth, Invert, the Fades, Offset and Glue shape an extruded lock: paint Extrude first.')
        elif not E.ribOffFadeActs(c):
            whys.append('Offset\'s Fade to Tip: paint an Offset first.')
    conv_ok = E.ribConvertActs(c)
    conv = dim_op(('op', 'hairmesh.rib_convert', 'Convert to Curves & Walls', 'CHECKMARK', {'curve': c.id}), conv_ok)
    plain = ('op', 'hairmesh.rib_plain', 'Plain Curve', 'CURVE_DATA', {'curve': c.id})
    rows += [[GAP], [buttons(context, conv, plain)]]             # (G12: both at the width of their texts; the whole row is for the panel's one main action, Convert)
    if not conv_ok:
        whys.append(RB.NO_BRANCH if c.ribRings else RB.NO_SHAPE)
    title = f'Volume Branch ({d["n"]} Anchors)' if d['vol'] else 'Flat Lock'
    return block('curve_rib', title, 'MOD_SOLIDIFY', rows, True, ' '.join(whys) or None)


def _cols_brush(name, b, cl, lw, tail):
    """a brush joined to its Clear alone (a family), its name in the column of names. Lot 1.2c, item 9.2 (the owner's
    call): the three pairs as wide as the column of the fields, the Depth's — the brush over the free column, the Clear
    at its text's width, then the empty place of the Depth's Invert (`tail`), so every pair starts and ends where the
    Depth field does. The button says Brush: its row's name says which one, and "Extrude Brush" and its Clear did not
    fit in the Depth's width (its tooltip names it)"""
    from .tool import field, item_need
    return field(name, b, (item_need(bpy.context, cl), [cl]), (tail, []), lw=lw)


def anchor_block(context, P, sel):
    """Flat Lock at the Anchor(s) (ribAnchorProps 7055) for the anchor sheet, or None without a flat lock's anchor"""
    from .props import measure
    from .tool import block, dim, field, why_item
    d = RB.ribAnchorPanel(P, sel.anchors)
    if d is None:
        return None
    ok = not d['at_root']
    rows: list[list] = [[field('Width', ('prop', measure(P, 'rib_anchor_w'), '', *dim(ok)))], [field('Rotation', ('prop', 'rib_anchor_r', '', *dim(ok)))]]
    why = None if ok else ROOT_WHY.format(n=d['ends']) + '.'
    if not ok:
        rows.append([why_item(why)])
    title = 'Flat Lock at the Anchor' + (f's ({d["n"]})' if d['n'] > 1 else '')
    return block('anchor_rib', title, 'MOD_SOLIDIFY', rows, True, why)


def menu_items(context):
    """the flat lock's items of the curve pie's Walls / Lock menu: Convert and Plain Curve, with a flat lock the last
    selected curve; else nothing"""
    from .tool import dim_op
    _r, P, sel = select.current(context)
    c = last_rib(P, sel) if P is not None else None
    if c is None:
        return []
    return [('sep',), ('label', c.name, 'MOD_SOLIDIFY'),
            dim_op(('op', 'hairmesh.rib_convert', 'Convert to Curves & Walls', 'CHECKMARK', {'curve': c.id}), E.ribConvertActs(c)),
            ('op', 'hairmesh.rib_plain', 'Plain Curve', 'CURVE_DATA', {'curve': c.id})]


CLASSES = (HairMeshRibProps, HAIRMESH_OT_rib_convert, HAIRMESH_OT_rib_plain, HAIRMESH_OT_rib_clear)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.hairmesh_rib = bpy.props.PointerProperty(type=HairMeshRibProps)


def unregister():
    del bpy.types.WindowManager.hairmesh_rib
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
