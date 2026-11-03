# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Step 4.7b (docs/fase-4-plano.md): the Extra page of the D's Modifiers tab (extraPanel 6980: "+ Mask", "+ Thickness",
"+ Twist", "+ Noise", "+ Curls" on the selected modifiers, a brush per painted kind, the brush's settings) and the sheet
of a selected Extra (xmodProps 7039: the Mask's "Affects only" list, Ignore, Clear / Fill / Invert; Thickness's and
Twist's Clear and the angle; Noise's and Curls' sliders). The brush is blender/brush.py; the core core/tube.py
(addExtra, xmodProps, maskOp, paintMask, applyExtras).

The original's extras list with "Select" buttons (6992-7010) is the list of the Extras here, as the modifier stack: a
click on a line makes it the active Extra, as the Outliner's (no select buttons), the one the brush paints; its eye; its
X. Nothing reorders it: the order changes nothing but two Curls with different Thin Where Crowded or masks (the second
pass, 6527-6547), and the original has no way to change it. The drag & drop onto "Affects only" (7056-7080) is the "+"
menu of the Mask's list."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup

from ..core import curves as CM, edit, effects as E, groups as G, tube as TB
from . import brush as BRU, deferred, fit, live, owner, select, session, wording
from .coords import f32, field_same

KINDS = (('MASK', 'mask', 'Mask', 'MOD_MASK', 'A mask painted along the lock: where it is 100% the extra modifiers do nothing'),
         ('THICK', 'thick', 'Thickness', 'MOD_THICKNESS', 'Paint where the lock gets fatter or thinner'),
         ('TWIST', 'twist', 'Twist', 'MOD_SIMPLEDEFORM', 'Paint where the profile turns: clockwise or counterclockwise (seen from the root along the lock)'),
         ('NOISE', 'noise', 'Noise', 'MOD_NOISE', 'Sine-like noise along the lock'),
         ('CURL', 'curl', 'Curls', 'FORCE_TURBULENCE', 'Curls: the lock winds in a spiral'))   # (6985-6986: the buttons' titles)
KIND = {k: w for k, w, *_r in KINDS}
ICON = {w: i for _k, w, _n, i, _t in KINDS}


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def selected_xmod(context):
    root, P, sel = select.current(context)
    if P is None:
        return root, P, None
    last = select.last_item(P, sel)
    return root, P, (last if last is not None and last.type == 'xmod' else None)


def tube_name(P, x):
    t = P.items.get(x.tube)
    return t.name if t is not None else '(deleted)'


# ---------------- the sheet's controls: views on the selected extra ----------------

def _x():
    return selected_xmod(bpy.context)


def _set(key, conv=lambda v: v):
    def setter(self, v):
        root, P, x = _x()
        if x is None:
            return
        v = conv(v)
        if field_same(x.p.get(key), v):
            return
        x.p = {**x.p, key: v}                                       # (a new dict: the undo snapshots share p, 7057)
        t = P.items.get(x.tube)
        if t is not None:
            t.dirty = True                                          # (upd, 7043)
        deferred.changed(bpy.context, root)                         # (the modifier's surface follows by its key: live.tube_previews; no lock reads it)
    return setter


def _get(key, default, conv=lambda v: v):
    def getter(self):
        _r, _P, x = _x()
        v = x.p.get(key) if x is not None else None
        return conv(default if v is None else v)
    return getter


def _ref(key_ref, *alts):
    """the slider's reference length (ampRef || amp || S.scale * 0.01, 7117; radRef || radius || r0 || …, 7127)"""
    _r, P, x = _x()
    if x is None:
        return 1.0
    for k in (key_ref, *alts):
        v = x.p.get(k)
        if v:
            return float(v)
    return float(P.S.scale) * 0.01


def _deg(key):
    return IntProperty(name='', min=-180, max=180, get=_get(key, 0, lambda v: int(round(v))), set=_set(key, int))


class HairMeshXmodProps(PropertyGroup):
    """The sheet of a selected Extra (xmodProps 7039): on the WindowManager, views on the model; the modifier's surface
    and the undo step follow when the slider stops (deferred.py). Lengths and counts are fields; fractions sliders."""
    x_ignore: BoolProperty(name='Ignore', description='On: the mask affects every extra modifier of the lock EXCEPT the ones in the list', get=_get('ignore', False, bool), set=_set('ignore', bool))
    x_deg: IntProperty(name='Angle at 100%', description='How far the profile turns where the weight is 100% (↻ or ↺), degrees', min=0, max=720, get=_get('deg', 180, lambda v: int(round(v))), set=_set('deg', int))
    x_amp: FloatProperty(name='Amplitude', description='How far the lock moves from its path', min=0.0, soft_max=1000.0, precision=4, get=_get('amp', 0.0, float), set=_set('amp', f32))
    x_freq: FloatProperty(name='Frequency', description='Waves along the lock', min=0.5, max=20.0, precision=1, step=10, get=_get('freq', 1.0, float), set=_set('freq', f32))
    x_pure: FloatProperty(name='Pure Sine', description='0 irregular noise; 100% one clean sine wave (with both spreads on: a clean spiral)', min=0.0, max=1.0, subtype='FACTOR', get=_get('pure', 0.0, float), set=_set('pure', f32))
    x_sx: FloatProperty(name='Spread Across', description='Share of the noise across the lock', min=0.0, max=1.0, subtype='FACTOR', get=_get('sx', 1.0, float), set=_set('sx', f32))
    x_sy: FloatProperty(name='Spread Outwards', description='Share of the noise outwards (off in Plane mode)', min=0.0, max=1.0, subtype='FACTOR', get=_get('sy', 1.0, float), set=_set('sy', f32))
    x_plane: BoolProperty(name='Plane', description='The noise works in one plane only (across the lock, turned by the rotations below)', get=_get('plane', False, bool), set=_set('plane', bool))
    x_rot0: IntProperty(name='Rotation: Start', description='Turns the noise directions at the root (degrees)', min=-180, max=180, get=_get('rot0', 0, lambda v: int(round(v))), set=_set('rot0', int))
    x_rot1: IntProperty(name='Rotation: Middle', description='Turns the noise directions in the middle (degrees)', min=-180, max=180, get=_get('rot1', 0, lambda v: int(round(v))), set=_set('rot1', int))
    x_rot2: IntProperty(name='Rotation: End', description='Turns the noise directions at the tip (degrees)', min=-180, max=180, get=_get('rot2', 0, lambda v: int(round(v))), set=_set('rot2', int))
    x_seed: IntProperty(name='Seed', description='Another variant of the noise', min=1, max=50, get=_get('seed', 1, lambda v: int(round(v))), set=_set('seed', int))
    x_r0: FloatProperty(name='Root', description='Coil radius at the root: 0 the lock is only twisted here; more a volume curl, as if it wound around something', min=0.0, soft_max=1000.0, precision=4, get=_get('r0', 0.0, float), set=_set('r0', f32))
    x_r1: FloatProperty(name='1/3', description='Coil radius at a third of the lock', min=0.0, soft_max=1000.0, precision=4, get=_get('r1', 0.0, float), set=_set('r1', f32))
    x_r2: FloatProperty(name='2/3', description='Coil radius at two thirds of the lock', min=0.0, soft_max=1000.0, precision=4, get=_get('r2', 0.0, float), set=_set('r2', f32))
    x_r3: FloatProperty(name='Tip', description='Coil radius at the tip', min=0.0, soft_max=1000.0, precision=4, get=_get('r3', 0.0, float), set=_set('r3', f32))
    x_phase: IntProperty(name='Start Angle', description='Where the spiral starts around the lock (degrees)', min=-180, max=180, get=_get('phase', 0, lambda v: int(round(v))), set=_set('phase', int))
    x_d0: FloatProperty(name='1/4', description='Turns over the first quarter of the lock (the root)', min=0.0, max=4.0, step=5, get=_get('d0', 1.0, float), set=_set('d0', f32))
    x_d1: FloatProperty(name='2/4', description='Turns over the second quarter of the lock', min=0.0, max=4.0, step=5, get=_get('d1', 1.0, float), set=_set('d1', f32))
    x_d2: FloatProperty(name='3/4', description='Turns over the third quarter of the lock', min=0.0, max=4.0, step=5, get=_get('d2', 1.0, float), set=_set('d2', f32))
    x_d3: FloatProperty(name='4/4', description='Turns over the last quarter of the lock (the tip)', min=0.0, max=4.0, step=5, get=_get('d3', 1.0, float), set=_set('d3', f32))
    x_thick: FloatProperty(name='Thickness', description='Lock thickness inside the curl (×)', min=0.1, max=3.0, step=1, get=_get('thick', 1.0, float), set=_set('thick', f32))
    x_spin: IntProperty(name='Rotation in Spiral', description='Turns the cross-section inside the spiral (degrees)', min=-180, max=180, get=_get('spin', 0, lambda v: int(round(v))), set=_set('spin', int))
    x_follow: BoolProperty(name='Twist the Profile', description='The cross-section turns with the spiral (a twisted lock); off: it keeps its direction', get=_get('follow', False, bool), set=_set('follow', bool))
    x_smooth: FloatProperty(name='Smooth the Path', description='The spiral winds around a smoothed path, so on the bends of a curved lock the coils do not cut into each other (the lock shape gets softer)', min=0.0, max=1.0, subtype='FACTOR', get=_get('smooth', 0.0, float), set=_set('smooth', f32))
    x_shrink: FloatProperty(name='Shrink on Bends', description='Where the lock bends tighter than the coil, the coil radius smoothly gets smaller (moves the path: the lock may turn)', min=0.0, max=1.0, subtype='FACTOR', get=_get('shrink', 0.0, float), set=_set('shrink', f32))
    x_thin: FloatProperty(name='Thin Where Crowded', description='Where coils or bends of the lock come closer than its thickness, the lock gets thinner there so the surfaces do not cut into each other (only the thickness changes)', min=0.0, max=1.0, subtype='FACTOR', get=_get('thin', 0.0, float), set=_set('thin', f32))


XMOD_ATTRS = frozenset(HairMeshXmodProps.__annotations__)


# ---------------- operators ----------------

def _finish(context, root, P, sel, report, msg):
    select.store(context.scene.hairmesh, sel)
    G.panelsRendered(P, sel.items[len(sel.items) - 1] if sel.items else None)   # onSelChange (the last one's sheet: xmodProps)
    CM.rebuild_dirty(P)
    session.save_project(root, P)
    live.update(context, root)
    select.redraw(context)
    if msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


class HAIRMESH_OT_add_extra(bpy.types.Operator):
    """Add an Extra modifier on the selected Profile modifiers (or their curves)"""
    bl_idname = 'hairmesh.add_extra'
    bl_label = 'Add Extra'
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name='Kind', items=[(k, n, t, i, j) for j, (k, _w, n, i, t) in enumerate(KINDS)], default='MASK', options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        n, t = next((n, t) for k, _w, n, _i, t in KINDS if k == properties.kind)
        return f'Add {n}: {t[len(n) + 2:] if t.startswith(n + ": ") else t}'   # (the name first, once: the Extra tab's buttons are icons, item 8)

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, P, sel = select.current(context)
        if not TB.modTubes(P, sel.items):
            cls.poll_message_set('Select a modifier (or its curve) — apply a profile in the Profile tab first')   # (6566, the modifiers' part)
            return False
        return True

    def execute(self, context):
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)                                         # (newExtra reads autoTubeRadius without rRef: the samples)
        kind = KIND[self.kind]
        made, msg = TB.addExtra(P, sel.items, kind)
        msg = wording.core(msg)
        if not made:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        sel.items = [x.id for x in made]; sel.anchors = []
        res = _finish(context, root, P, sel, self.report, msg)
        if kind in TB.PAINTED:                                      # (6567: S.maskBrush on with this kind)
            BRU.props(context).brush_weight_kind = self.kind
            start_brush(context, self.kind)
        return res


def start_brush(context, weight):
    """the weight brush on with this kind (the running one switches; outside a 3D View: nothing — its button stays)"""
    if BRU.running('WEIGHT') is not None:
        BRU.props(context).brush_weight_kind = weight
        BRU.running('WEIGHT').header(context)
        return
    if context.area is None or context.area.type != 'VIEW_3D' or bpy.app.background:
        return
    try:
        bpy.ops.hairmesh.brush('INVOKE_DEFAULT', kind='WEIGHT', weight=weight)
    except RuntimeError:
        pass


def selection_after(P, sel, x):
    """the selection without the Extra x; left empty, the next line of the list (else the line before it, else its
    modifier): the list stays open on the same modifier, the next X a click away"""
    items = [i for i in sel.items if i != x.id]
    if items or x.id not in sel.items:
        return items
    t = P.items.get(x.tube)
    ex = TB.extrasOf(P, t) if t is not None else [x]
    i = ex.index(x)
    near = [*ex[i + 1:], *reversed(ex[:i])]
    return [near[0].id] if near else ([t.id] if t is not None else [])


class HAIRMESH_OT_delete_extra(bpy.types.Operator):
    """Delete this Extra and nothing else (the X of its line in the list)"""
    bl_idname = 'hairmesh.delete_extra'
    bl_label = 'Delete Extra'
    bl_options = {'REGISTER', 'UNDO'}

    item: IntProperty(name='Extra', default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        _r, P, _s = select.current(context)
        x = P.items.get(properties.item) if P is not None else None
        return f'Delete {x.name} and nothing else' if x is not None else cls.__doc__

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        root, P, sel = select.current(context)
        x = P.items.get(self.item)
        if x is None or x.type != 'xmod':
            self.report({'ERROR'}, f'No Extra {self.item} in the project')
            return {'CANCELLED'}
        name = x.name
        sel.items = selection_after(P, sel, x)
        edit.deleteItem(P, x.id)                                    # (a mask's "Affects only" keeps the id, as the original's: onlyList drops what is gone, 6445)
        res = _finish(context, root, P, sel, self.report, f'HairMesh: {name} deleted')
        BRU.follow_active(context)
        return res


class HAIRMESH_OT_extra_mask_op(bpy.types.Operator):
    """Change the extra's painted weights: Clear (all to 0), Fill (all to 100%), Invert (100% - w)"""
    bl_idname = 'hairmesh.extra_mask_op'
    bl_label = 'Weights'
    bl_options = {'REGISTER', 'UNDO'}

    op: EnumProperty(items=(('CLEAR', 'Clear', 'Every weight to 0: the extra does nothing until painted again', 0),
                            ('FILL', 'Fill', 'Every weight to 100%: masked all along', 1),
                            ('INVERT', 'Invert', 'Every weight w becomes 100% - w', 2)), options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return {'CLEAR': 'Clear: every weight to 0 (the extra does nothing until painted again)', 'FILL': 'Fill: every weight to 100% (masked all along)',
                'INVERT': 'Invert: every weight w becomes 100% - w'}[properties.op]

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, _P, x = selected_xmod(context)
        if x is None or x.mask is None:
            cls.poll_message_set('Select a painted Extra (Mask, Thickness or Twist)')
            return False
        return True

    def execute(self, context):
        root, P, x = selected_xmod(context)
        if not TB.maskOp(P, x, self.op.lower()):
            self.report({'INFO'}, 'Only a Mask has Fill and Invert')
            return {'CANCELLED'}
        _r, _P, sel = select.current(context)
        return _finish(context, root, P, sel, self.report, None)


def _candidates(P, x):
    """the extras of the mask's modifier that can go in its list (7067: not a mask, not already there)"""
    only = TB.onlyList(P, x)
    t = P.items.get(x.tube)
    return [y for y in (TB.extrasOf(P, t) if t is not None else []) if y is not x and y.kind != 'mask' and y.id not in only]


class HAIRMESH_OT_mask_only(bpy.types.Operator):
    """Edit the Mask's "Affects only" list: add an extra of its modifier (the mask then works on the listed ones only), or take one out"""
    bl_idname = 'hairmesh.mask_only'
    bl_label = 'Affects Only'
    bl_options = {'REGISTER', 'UNDO'}

    extra: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    remove: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return 'Take it out of the list' if properties.remove else 'Add it to the list: the mask then works on the listed extras only'   # (7062, 7068)

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        _r, _P, x = selected_xmod(context)
        return x is not None and x.kind == 'mask'

    def execute(self, context):
        root, P, x = selected_xmod(context)
        only = TB.onlyList(P, x)
        if self.remove:
            lst = [z for z in only if z != self.extra]
        else:
            y = P.items.get(self.extra)
            if y is None or y not in _candidates(P, x):
                return {'CANCELLED'}
            lst = [*only, y.id]
        x.p = {**x.p, 'only': lst}                                  # (setOnly 7057: always a new array)
        t = P.items.get(x.tube)
        if t is not None:
            t.dirty = True
        _r, _P, sel = select.current(context)
        return _finish(context, root, P, sel, self.report, None)


class HAIRMESH_MT_mask_only(bpy.types.Menu):
    """Extra modifiers on this lock that can be added to the mask list"""
    bl_idname = 'HAIRMESH_MT_mask_only'
    bl_label = 'Add to the List'

    def draw(self, context):
        _r, P, x = selected_xmod(context)
        cs = _candidates(P, x) if x is not None else []
        if not cs:
            self.layout.label(text='No other extra on this modifier', icon='INFO')
        for y in cs:
            op = self.layout.operator(HAIRMESH_OT_mask_only.bl_idname, text=y.name, icon=ICON.get(y.kind, 'MODIFIER'))
            op.extra = y.id


# ---------------- the D and the N: rows ----------------

EXTRA_HELP = '\n'.join(('Select a modifier (or its curve), then add an extra: Mask, Thickness, Twist, Noise, Curls.',
                        'The list shows the extras of the modifier: click one to make it active, its eye switches it off, its X deletes it.',
                        'Paint the active extra: turn on the brush, paint on the surface with LMB.',
                        'Shift + LMB: smooth.',
                        'Mask: Ctrl + LMB erases. Thickness, Twist: Ctrl + LMB paints the other way, Ctrl + Alt + LMB brings the value back to 0 (or Absolute with the value 0).',
                        'Clear on its sheet: erase it all.'))                                           # (7024, 7042)


def brush_toggle(kind, P, context):
    on = BRU.running('WEIGHT') is not None and BRU.props(context).brush_weight_kind == kind
    return ('op', 'hairmesh.brush', f'{BRU.NAMES[kind]} Brush', 'BRUSH_DATA', {'kind': 'WEIGHT', 'weight': kind}, {'depress': on})


def brush_button(context, P):
    """the one brush: the active Extra's, dimmed when it can't be painted (the reason: hairmesh.brush's tooltip, why_off)"""
    from .tool import dim_op
    _r, _P, x = selected_xmod(context)
    k = BRU.KIND_OF.get(x.kind) if x is not None else None
    item = brush_toggle(k or BRU.props(context).brush_weight_kind, P, context)
    if k is None:
        item = (*item[:2], 'Brush', *item[3:])
    return dim_op(item, BRU.running('WEIGHT') is not None or BRU.why_off(context, 'WEIGHT', k) is None)


def brush_settings_rows(context):
    """the running weight brush's settings (extraPanel 7000-7023): Add / Absolute, its strength (or intensity), radius,
    hardness, and what Ctrl+Alt does"""
    from .tool import enum, text
    bp = BRU.props(context); k = bp.brush_weight_kind
    key = {'MASK': 'brush_mask_strength', 'THICK': 'brush_thick_intensity', 'TWIST': 'brush_twist_strength'}[k]
    name = {'MASK': 'Strength', 'THICK': 'Intensity', 'TWIST': 'Strength'}[k]
    rows = [[enum('brush_weight_mode', 'Mode')], [('prop', key, name)], [('prop', 'brush_radius', 'Radius'), ('prop', 'brush_hardness', 'Hardness')]]
    if k == 'THICK' and bp.brush_thick_intensity == 0:
        rows.append([text('Intensity 0: the brush paints nothing — set it first.', 'ERROR')])   # (the toast of addExtra: "set the intensity, then paint")
    return rows


# ---- the list of the Extras (ours, the owner's request: docs/features-adicionadas.md) ----

LIST_UL = ('HAIRMESH_UL_extras', 'xrows', 'hairmesh_extra_active')   # the list's class, its lines on Scene.hairmesh, its active line on the WindowManager
LIST_MIN = 3                                                        # the fewest lines it shows (the wheel scrolls past them) when the tab and the sheet fill the D
LINE_BTN = 1.0                                                      # the column of its eye, of its X


def listed_tube(P, sel):
    """the modifier the list shows: the active Extra's, else the first of the selection (modTubes)"""
    x = E.activeExtra(P, sel.items)
    return P.items.get(x.tube) if x is not None else next(iter(TB.modTubes(P, sel.items)), None)


def extra_row(P, x, room):
    """a line of the list, as the modifier stack's: the kind's icon and the name (a click makes it the active Extra, as
    the Outliner's: Shift adds, a double click renames), the eye, the X. Hidden: the eye closed, the name dimmed"""
    from .tool import LIST_PAD, cols
    shown = x.visible is not False
    said = x.name
    if fit.button_need(said) > room - LIST_PAD - 2 * LINE_BTN:
        said = said.partition(' (')[0] or said                      # (its curve: the same on every line)
    t = P.items.get(x.tube)
    twins = [y for y in TB.extrasOf(P, t) if y.name == x.name] if t is not None else [x]
    if twins[0] is not x:
        said += f' ({twins.index(x) + 1})'                          # (the original numbers no Extra of a kind: two Masks of a curve are both "Mask 1 (Curve 1)")
    name = ('op', 'hairmesh.select_item', said, ICON.get(x.kind, 'MODIFIER'), {'item': x.id}, {'emboss': False, 'active': shown})
    eye = ('op', 'hairmesh.toggle_visible', '', 'HIDE_OFF' if shown else 'HIDE_ON', {'item': x.id}, {'emboss': False, 'list_text': f'{"Hide" if shown else "Show"} {x.name}'})
    delete = ('op', 'hairmesh.delete_extra', '', 'X', {'item': x.id}, {'emboss': False, 'list_text': f'Delete {x.name}'})
    return cols((None, [name], {'align': 'LEFT'}), (LINE_BTN, [eye]), (LINE_BTN, [delete]))


def list_rows(context, P, t, around, sheet, room):
    """the list of the Extras of the modifier t: as many lines as the 18 rows leave room for after `around` (the tab's
    other rows) and the `sheet` below; the wheel scrolls the rest. Its lines come along for the list menu"""
    from .tool import MAX_ROWS, visible_rows
    ex = TB.extrasOf(P, t) if t is not None else []
    lines = [[extra_row(P, x, room)] for x in ex]
    have = {e.xid for e in context.scene.hairmesh.xrows}
    if not all(x.id in have for x in ex):
        return lines                                                # (a .blend saved before the list: its collection catches up at the next save)
    if not ex:
        return []
    used = 2 + visible_rows(context, around, room) + (visible_rows(context, sheet, room) if sheet else 0)   # (the row of the tabs, the list's grip)
    return [[('list', min(len(ex), max(LIST_MIN, MAX_ROWS - used)), room, LIST_UL, lines)]]


class HAIRMESH_UL_extras(bpy.types.UIList):
    """The Extras of a modifier: a list, so the wheel scrolls it in the pop-up (Scene.hairmesh.xrows has one line per
    Extra of the project, the filter leaves the modifier's)"""
    bl_idname = 'HAIRMESH_UL_extras'

    def filter_items(self, context, data, propname):
        from .tool import TREE
        rows = getattr(data, propname)
        _r, P, sel = select.current(context)
        TREE['P'] = P
        t = listed_tube(P, sel) if P is not None else None
        ids = {x.id for x in TB.extrasOf(P, t)} if t is not None else set()
        name = self.filter_name.lower()                             # (the list's own search field)
        return [self.bitflag_filter_item if r.xid in ids and name in P.items[r.xid].name.lower() else 0 for r in rows], []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        from . import tool
        P = tool.TREE['P']
        x = P.items.get(item.xid) if P is not None else None
        if x is not None:
            tool.draw_item(layout, data, extra_row(P, x, tool.TREE['room']), tool.TREE['room'] - tool.LIST_PAD)


def _active_line(_self):
    """the line the list highlights: the active Extra's (a view on the selection; a click on a line is its name's button)"""
    _r, _P, x = selected_xmod(bpy.context)
    return next((i for i, e in enumerate(bpy.context.scene.hairmesh.xrows) if e.xid == x.id), -1) if x is not None else -1


def extra_tab_rows(context, room, sheet=None):
    """the Extra tab (extraPanel 6980): what the buttons add to, + Mask / Thickness / Twist / Noise / Curls — in a closed
    block while a sheet follows (`sheet`: its rows) —, the list of the Extras of the modifier, the one brush, the running
    brush's settings"""
    from .tool import block, text
    _r, P, sel = select.current(context)
    ts = TB.modTubes(P, sel.items) if P is not None else []
    head = [text(f'Add to {ts[0].name}:' if len(ts) == 1 else f'Add to {len(ts)} modifiers:' if ts else 'Select a modifier (or its curve), then add:', 'INFO')]   # (6982)
    # (item 8 of the lot after 4.8, the owner's request: the five "+ Kind" buttons of three rows as one row of icons, each
    # named by its tooltip — add_extra's description — and by the list menu)
    adds = [('op', 'hairmesh.add_extra', '', i, {'kind': k}, {'list_text': f'Add {n}'}) for k, _w, n, i, _t in KINDS]
    t = listed_tube(P, sel) if P is not None else None
    top = [block('extra_add', 'Add an Extra', 'MODIFIER', [head, adds], False)] if sheet else [head, adds]
    if len(ts) > 1 and t is not None:
        top.append([text(f'Extras of {t.name}:', 'INFO')])          # (the first of the modifiers the buttons add to)
    under = [[brush_button(context, P)]]
    if BRU.running('WEIGHT') is not None:
        under += brush_settings_rows(context)
    return [*top, *list_rows(context, P, t, [*top, *under], sheet, room), *under]


def xmod_rows(context, P, x, room, brush=True):
    """the sheet of an Extra (xmodProps 7039) by kind; `brush`: with its brush's button (the Extra tab above it has one)"""
    from .tool import block, group_label, label_and, text, why_item
    rows = [[('label', f'{x.name} (on {tube_name(P, x)})', ICON.get(x.kind, 'MODIFIER'))],
            *label_and(group_label(P, x), ('op', 'hairmesh.delete_selected', 'Delete', 'X', {'keep_shape': False}), room)]   # (7042)
    k = x.kind
    if k == 'mask':
        only = TB.onlyList(P, x)
        lst = [[('label', P.items[i].name, ICON.get(P.items[i].kind, 'MODIFIER')), ('op', 'hairmesh.mask_only', '', 'X', {'extra': i, 'remove': True}, {'list_text': f'Remove {P.items[i].name}'})] for i in only]
        if not lst:
            lst = [[text('All extras of this modifier — add one with + to limit it.', 'BLANK1')]]   # (7049)
        rows.append(block('xmod_only', 'Affects Only', 'FILTER', [*lst, [('menu', 'HAIRMESH_MT_mask_only', 'Add to the List', 'ADD', {'active': bool(_candidates(P, x))}), ('prop', 'x_ignore', 'Ignore')]]))
        rows.append([('op', 'hairmesh.extra_mask_op', 'Clear', 'X', {'op': 'CLEAR'}), ('op', 'hairmesh.extra_mask_op', 'Fill', 'FACESEL', {'op': 'FILL'}),
                     ('op', 'hairmesh.extra_mask_op', 'Invert', 'ARROW_LEFTRIGHT', {'op': 'INVERT'})])
        if brush:
            rows.append([brush_button(context, P)])
    elif k in ('thick', 'twist'):
        if k == 'twist':
            rows.append([('prop', 'x_deg', 'Angle at 100%')])
        rows.append([('op', 'hairmesh.extra_mask_op', 'Clear', 'X', {'op': 'CLEAR'}), *([brush_button(context, P)] if brush else []),
                     why_item('Painted thickness along the lock: orange fatter (up to ×2), blue thinner (down to a point). A mask can limit it.' if k == 'thick'
                              else 'Painted twist along the lock: green the profile turns clockwise, pink counterclockwise (seen from the root along the lock). A mask can limit it.')])   # (7094, 7102)
    elif k == 'noise':
        rows += [[('prop', 'x_amp', 'Amplitude'), ('prop', 'x_freq', 'Frequency')], [('prop', 'x_pure', 'Pure Sine')],
                 [('prop', 'x_sx', 'Spread Across'), ('prop', 'x_sy', 'Spread Outwards', {'active': not x.p.get('plane')})], [('prop', 'x_plane', 'Plane'), ('prop', 'x_seed', 'Seed')],
                 block('xmod_rot', 'Rotation', 'DRIVER_ROTATIONAL_DIFFERENCE', [[('prop', 'x_rot0', 'Start')], [('prop', 'x_rot1', 'Middle')], [('prop', 'x_rot2', 'End')]], False)]
    elif k == 'curl':
        rows += [block('xmod_coil', 'Coil Radius', 'FORCE_VORTEX', [[('prop', 'x_r0', 'Root'), ('prop', 'x_r1', '1/3')], [('prop', 'x_r2', '2/3'), ('prop', 'x_r3', 'Tip')], [('prop', 'x_phase', 'Start Angle')]], True),
                 block('xmod_turns', 'Density (Turns)', 'MOD_SCREW', [[('prop', 'x_d0', '1/4'), ('prop', 'x_d1', '2/4')], [('prop', 'x_d2', '3/4'), ('prop', 'x_d3', '4/4')]], True),
                 block('xmod_shape', 'Shape', 'MOD_THICKNESS', [[('prop', 'x_thick', 'Thickness'), ('prop', 'x_spin', 'Rotation in Spiral')], [('prop', 'x_follow', 'Twist the Profile')]], False),
                 block('xmod_clip', 'Anti-clipping', 'MOD_PHYSICS', [[('prop', 'x_smooth', 'Smooth the Path')], [('prop', 'x_shrink', 'Shrink on Bends')], [('prop', 'x_thin', 'Thin Where Crowded')]], False)]
    return rows


CLASSES = (HairMeshXmodProps, HAIRMESH_OT_add_extra, HAIRMESH_OT_delete_extra, HAIRMESH_OT_extra_mask_op, HAIRMESH_OT_mask_only, HAIRMESH_MT_mask_only, HAIRMESH_UL_extras)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.hairmesh_xmod = PointerProperty(type=HairMeshXmodProps)
    bpy.types.WindowManager.hairmesh_extra_active = IntProperty(name='Active Extra', get=_active_line, set=lambda _s, _v: None)   # (a list needs an active line)


def unregister():
    del bpy.types.WindowManager.hairmesh_extra_active
    del bpy.types.WindowManager.hairmesh_xmod
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
