# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Lot 4.7b (D4.4, approved): the brush — a session over the 3D View that paints instead of selecting, shared by the
weight brush of the Extras (4.7b: Mask, Thickness, Twist, paintMask 6618), the Density brush (4.8: paintTipMask 6585)
and the anchor brush (4.9). The rules are core/brush.py (brushWeight, the dab amount, the radius keys and drag, the
press's operation) and the painting functions of the core; here: the circle (gpu), the keys, one undo step per stroke.

  LMB drags a stroke (the press's op: Shift smooths; Mask / Density: Ctrl erases; Thickness / Twist: Ctrl inverts,
      Ctrl+Alt erases; Alt alone is Blender's, as the original's navigation): a dab on the press (amount 0.2), one per
      move (more for a longer move); the live object follows when the cursor pauses, the project text and the undo
      step when the stroke ends (the original's commit on pointerup). The Density's loops are rebuilt there too, before
      that undo step, so it carries them and a Ctrl+Z takes both back (lot 4.8c, the owner's request)
  [ / ]  the radius x0.85 / x1.15 (8018-8019)
  F / Shift+F  the radius / the hardness by dragging (Blender's paint modes: the circle stays, LMB or Enter keeps it,
      RMB or Esc puts it back)
  Ctrl + RMB drag  right = bigger, up = harder (5774, 5932-5933)
  Esc  ends the brush (as its toggle button); Shift+RMB is the pie; navigation and every other key pass through

The keys exist only while the session runs (D4.4: outside it `[` `]` are Blender's / MACHIN3tools', F the
contextual frame). The session ends by itself when its sub-tool or tab is left, or what it paints is gone.

The weight brush's colors (rebuildTube 6689-6703) are on the live mesh itself, only while it runs (lot 4.8b, item 5:
live.PAINT_ATTR); Solid shows a color attribute only with the view's Color on Attribute, so the session sets that on its
own view and gives it back on every way out (hide_colors). An object without a color attribute keeps its look there
(Blender 5.1 falls back to the Material color). Material Preview and Rendered show no color: the mesh has no material, and
one made during a session would be a datablock outside the undo steps."""
import gc
import math
import time
import traceback

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

from ..core import anchorbrush as AB, brush as BR, curves as CM, edit, effects as E, groups as G, tube as TB
from . import errorlog, live, modal_keys, owner, pick, prefs, select, session, slow
from .coords import f32

MODIFIER_KEYS = {'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY'}
STATE = {'op': None, 'shade': None}                                 # the running session (the draw handlers read it); 4.8b: the view's Color it replaced (its space's pointer, the old value)
FORESEE = 0.008                                                     # seconds of a mouse move spent on what the next dab will ask: _foresee
FORESEE_AROUND = 1.15                                               # (a little past the circle: the press lands near where the cursor was)
TIMES = {'dab': [], 'stroke': []}                                   # the last dabs' / strokes' cost, seconds (tools/sim_c47_shots.py, sim_c49_brush_perf.py read it)
WEIGHT_KINDS = (('MASK', 'mask'), ('THICK', 'thick'), ('TWIST', 'twist'))
WEIGHT_OF = dict(WEIGHT_KINDS)
KIND_OF = {v: k for k, v in WEIGHT_KINDS}
NAMES = {'MASK': 'Mask', 'THICK': 'Thickness', 'TWIST': 'Twist'}
HINTS = {
    'MASK': 'LMB paints the mask (black), Ctrl erases, Shift smooths',
    'THICK': 'LMB paints the intensity (orange fatter, blue thinner), Ctrl inverted, Ctrl+Alt erases, Shift smooths',
    'TWIST': 'LMB paints the twist (green clockwise, pink counterclockwise), Ctrl the other way, Ctrl+Alt erases, Shift smooths',
    'TIP': 'LMB paints where the density works, Ctrl erases, Shift smooths',
    'ANCHOR': 'LMB drags the anchors under the circle, Shift smooths, Ctrl inflates the lock, Ctrl+Alt shrinks it; with curves or a NURBS disc selected, only their anchors'}
UNDO = {'ANCHOR': 'HairMesh Brush'}
KEYS_HINT = '[ ] or F radius · Shift+F hardness · Ctrl + right drag both · Esc ends'
PAINT_KEYS = {'MASK': ((('LMB',), 'Paint the Mask'), (('Ctrl',), 'Erase'), (('Shift',), 'Smooth')),
              'THICK': ((('LMB',), 'Paint the Intensity'), (('Ctrl',), 'Inverted'), (('Ctrl', 'Alt'), 'Erase'), (('Shift',), 'Smooth')),
              'TWIST': ((('LMB',), 'Paint the Twist'), (('Ctrl',), 'The Other Way'), (('Ctrl', 'Alt'), 'Erase'), (('Shift',), 'Smooth')),
              'TIP': ((('LMB',), 'Paint the Density'), (('Ctrl',), 'Erase'), (('Shift',), 'Smooth')),
              'ANCHOR': ((('LMB',), 'Drag the Anchors'), (('Shift',), 'Smooth'), (('Ctrl',), 'Inflate'), (('Ctrl', 'Alt'), 'Shrink'))}
BRUSH_KEYS = ((('[', ']'), 'Radius'), (('F',), 'Radius'), (('Shift', 'F'), 'Hardness'), (('Ctrl', 'RMB Drag'), 'Both'), (('Esc',), 'Exit'))
RADIAL_KEYS = ((('Ctrl',), 'Snap'), (('Shift',), 'Precision'), (('LMB', 'Enter'), 'Keep It'), (('RMB', 'Esc'), 'Put It Back'))
ADJUST_KEYS = {'radius': ((('Move',), 'Set the Radius'), *RADIAL_KEYS),
               'hardness': ((('Move',), 'Set the Hardness'), *RADIAL_KEYS),
               'drag': ((('Move',), 'Set Radius and Hardness'), (('RMB',), 'Release: Keep Them'), (('Esc',), 'Put Them Back'))}


class HairMeshBrushProps(PropertyGroup):
    """The brush's settings (S.brushR, S.brushHard, S.maskBrush, S.tipBrush): on the WindowManager — no undo step
    for a radius, kept for the session (the original keeps them in S, not in the file)"""
    brush_radius: IntProperty(name='Radius', description='The brush circle in pixels ([ / ] or F in the viewport)', min=BR.R_MIN, max=BR.R_MAX, soft_max=400, default=BR.BRUSH_R)
    brush_hardness: FloatProperty(name='Hardness', description='The share of the radius painting at full strength (Shift+F in the viewport); softer outside', min=0.0, max=1.0, default=BR.BRUSH_HARD, subtype='FACTOR')
    brush_weight_kind: EnumProperty(name='Paints', items=[(k, NAMES[k], f'The {NAMES[k]} weights of the modifiers', i) for i, (k, _w) in enumerate(WEIGHT_KINDS)], default='MASK')
    brush_weight_mode: EnumProperty(name='Mode', items=(('ADD', 'Add', 'Every stroke adds up', 0), ('ABS', 'Absolute', 'The weight is set to the brush value', 1)), default='ADD')
    brush_mask_strength: FloatProperty(name='Strength', description='How much a stroke paints the mask (100%: masked)', min=0.0, max=1.0, default=1.0, subtype='FACTOR')
    brush_thick_intensity: FloatProperty(name='Intensity', description='-100% thinner (to a point) … +100% twice as thick; 0 paints nothing', min=-1.0, max=1.0, default=0.0, subtype='FACTOR')
    brush_twist_strength: FloatProperty(name='Strength', description='-100% counterclockwise … +100% clockwise (by the twist angle at 100%)', min=-1.0, max=1.0, default=0.5, subtype='FACTOR')
    brush_tip_strength: FloatProperty(name='Strength', description='How much a stroke paints where the density works', min=0.0, max=1.0, default=1.0, subtype='FACTOR')
    brush_anchor_mode: EnumProperty(name='Mode', update=lambda self, context: _mode_changed(context), items=(('SCREEN', 'Screen', 'Move everything inside the circle on screen, whatever its depth', 0),
                                                        ('SURFACE', 'Surface', 'Move only what is near the touched point in 3D (the front-most hair under the cursor)', 1)), default='SCREEN')


BRUSH_ATTRS = frozenset(HairMeshBrushProps.__annotations__)


def props(context=None):
    return (context or bpy.context).window_manager.hairmesh_brush


def _mode_changed(context):
    op = STATE.get('op')
    if op is not None and op.kind == 'ANCHOR':
        op.header(context)


def running(kind=None, weight=None):
    """the session running (of this kind / weight), or None"""
    op = STATE.get('op')
    if op is None or (kind is not None and op.kind != kind):
        return None
    if weight is not None and props().brush_weight_kind != weight:
        return None
    return op


def mask_brush(bp):
    """S.maskBrush as core/tube.paintMask reads it"""
    return {'kind': WEIGHT_OF[bp.brush_weight_kind], 'mode': 'abs' if bp.brush_weight_mode == 'ABS' else 'add',
            'strength': f32(bp.brush_mask_strength), 'intensity': f32(bp.brush_thick_intensity), 'twist': f32(bp.brush_twist_strength)}


def paint_ids(P, sel, kind):
    """the Extras a dab of this kind paints: the selected ones of the kind — the active one alone after a click in the
    list; several after a Shift + click, or after adding to several modifiers at once"""
    w = WEIGHT_OF[kind]
    return [i for i in sel.items if (x := P.items.get(i)) is not None and x.type == 'xmod' and x.kind == w]


def follow_active(context):
    """the running weight brush paints the active Extra: its kind follows it (a click in the list or the Outliner), and
    on one it cannot paint it ends here, not at its next event: the D redrawn with the change has to show it off"""
    op = STATE.get('op')
    if op is None or op.kind != 'WEIGHT' or op.stroke is not None:
        return
    _r, P, sel = select.current(context)
    x = E.activeExtra(P, sel.items) if P is not None else None
    if E.extraPaintState(x) != 'ok':
        stop(context)
        return
    bp = props(context)
    if bp.brush_weight_kind != KIND_OF[x.kind]:
        bp.brush_weight_kind = KIND_OF[x.kind]
        op.header(context)


def why_off(context, kind, weight=None):
    """why the brush can't run now (None: it can) — nothing to paint; the weight brush paints the active Extra (`weight`:
    of this kind)"""
    root, P, sel = select.current(context)
    if P is None:
        return 'No HairMesh project'
    if kind == 'WEIGHT':
        if not context.scene.hairmesh.live_preview:
            return 'Turn on Live Preview to paint: the brush paints the surface on screen (Viewport tab)'
        x = E.activeExtra(P, sel.items)
        state = E.extraPaintState(x)
        if state == 'none':
            if not any(y.kind in TB.PAINTED for y in P.of_type('xmod')):
                return 'Add a Mask, Thickness or Twist first (Modifiers tab > Extra)'
            return 'Select a Mask, Thickness or Twist in the list (Modifiers tab > Extra)'
        if state == 'no_weights':
            return f'Select a Mask, Thickness or Twist in the list: {x.name} has no weights to paint'
        if state == 'hidden':
            return f'Show {x.name} with its eye in the list: a hidden Extra is not painted'
        if weight is not None and WEIGHT_OF[weight] != x.kind:
            return f'Select a {NAMES[weight]} in the list: the brush paints the active Extra'
        return None
    if kind == 'TIP':
        if not P.of_type('tip'):
            return 'Add a Density first (Modifiers tab > Topology > Density)'
        return None
    if kind == 'ANCHOR':
        return None
    return 'Unknown brush'


def _still_on(op, context):
    from . import tool
    if STATE.get('op') is not op or context.scene.hairmesh.subtool != op.subtool_start:
        return False                                                # (ours: the brushes work from the D's Modifiers tab with any sub-tool; picking another sub-tool ends them)
    p = prefs.get(bpy.context)
    if op.kind != 'ANCHOR' and p is not None and p.menu_tab != 'MODIFIERS':
        return False                                                # (the tab stays in the preferences with the D closed: only moving to another tab ends the brush; the anchor brush is its sub-tool, whatever the tab)
    if context.mode != 'OBJECT' or not tool.is_active(context) or not owner.visible(context):
        return False                                                # (Tab, another tool, the add-on filtered out: the clicks are Blender's again)
    if context.area is None or context.area.type != 'VIEW_3D':
        return False                                                # (its area is not on this screen — another workspace, the tool on there too —, or another editor now)
    pp = context.scene.hairmesh_profile
    if op.kind == 'WEIGHT' and (pp.mod_tab != 'EXTRA' or not context.scene.hairmesh.live_preview):
        return False                                                # (the preview off: no surface on screen to paint, paintMask's t.obj.visible)
    if op.kind == 'TIP' and (pp.mod_tab != 'TOPOLOGY' or getattr(context.scene.hairmesh_topo, 'topo_tab', 'DENSITY') != 'DENSITY'):
        return False
    if bpy.data.objects.get(op.root_name) is None:
        return False
    if op.kind == 'ANCHOR':
        root = session.find_root(context)
        if root is None or root.name != op.root_name:
            return False                                            # (another project selected: _arm starts one on it)
    return why_off(context, op.kind) is None


def _release_area(area):
    """Blender's header back on the session's area. A closed area is freed and the modal only sees context.area None
    (as on another workspace, where it lives on): the reference is compared (that reads the pointer alone), never
    touched, unless the area is still on a screen"""
    if any(a == area for s in bpy.data.screens for a in s.areas):
        live.header(area, None); area.tag_redraw()
    else:
        modal_keys.clear(bpy.context)


RADIAL_WIDTH = 165                                                  # px at 100 %: the pointer's way from hardness 0 to 1 (Blender's scale of a strength)
SNAP_RADIUS = 5                                                     # Ctrl: Blender's radial control snaps a size to 10 px of diameter
SNAP_HARDNESS = 0.1
SLOW = 0.1                                                          # Shift: precision


def radial_radius(r0, dx, snap=False):
    """pure: the radius `dx` pixels of the pointer (at 100 %) after the press, as Blender's radial control sets a size: the
    horizontal way alone, right grows it, one pixel of radius a pixel"""
    r = r0 + dx
    if snap:
        r = round(r / SNAP_RADIUS) * SNAP_RADIUS
    return int(round(min(BR.R_MAX, max(BR.R_MIN, r))))


def radial_hardness(h0, dx, snap=False):
    """pure: the hardness `dx` pixels of the pointer after the press, as Blender's radial control sets a strength"""
    h = h0 + dx / RADIAL_WIDTH
    if snap:
        h = round(h / SNAP_HARDNESS) * SNAP_HARDNESS
    return min(1.0, max(0.0, h))


def over_ui(context, event):
    """the cursor is not over the viewport's own region (outside it, or over a region overlapping it: the toolbar, the
    N panel, the headers with Region Overlap) — the event is the interface's"""
    r = context.region
    if r is None:
        return True
    mx, my = event.mouse_x, event.mouse_y
    if not (r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height):
        return True
    for o in context.area.regions if context.area is not None else ():
        if o.type in ('TOOLS', 'UI', 'HEADER', 'TOOL_HEADER', 'ASSET_SHELF', 'ASSET_SHELF_HEADER') and o.width > 1 and o.height > 1 \
                and o.x <= mx < o.x + o.width and o.y <= my < o.y + o.height:
            return True
    return False


def popup_rect(context, region):
    """the rectangle of a pop-up as it is now, None once it closed. A modal's context has no region_popup, and a closed
    pop-up's region is freed: temp_override looks it up among the screen's regions before anything reads it"""
    try:
        with context.temp_override(region=region):
            return region.x, region.y, region.width, region.height
    except TypeError:
        return None


NAV = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM', 'NDOF_MOTION'}


class HAIRMESH_OT_brush(bpy.types.Operator):
    """Paint with the brush in the viewport (the button again, or Esc, ends it): LMB paints, Shift smooths, Ctrl erases (thickness / twist: Ctrl inverts, Ctrl+Alt erases); [ ] or F the radius, Shift+F the hardness, Ctrl + right drag both"""
    bl_idname = 'hairmesh.brush'
    bl_label = 'Brush'
    bl_options = {'REGISTER'}                                       # (each stroke pushes its own undo step: _end_stroke)

    kind: EnumProperty(name='Brush', items=(('WEIGHT', 'Weight', 'The Extras: Mask, Thickness, Twist', 0), ('TIP', 'Density', 'Where a Density works', 1), ('ANCHOR', 'Anchors', 'The anchors under the circle', 2)), default='WEIGHT', options={'SKIP_SAVE'})
    weight: EnumProperty(name='Paints', items=[(k, NAMES[k], '', i) for i, (k, _w) in enumerate(WEIGHT_KINDS)], default='MASK', options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if properties.kind == 'ANCHOR':
            return 'Shape the curves with the Brush: ' + HINTS['ANCHOR'] + '. ' + KEYS_HINT
        if properties.kind == 'TIP':
            return 'Paint with the Density Brush: ' + HINTS['TIP'] + '. With Density modifiers selected only their locks are painted. ' + KEYS_HINT
        why = None if running('WEIGHT') is not None else why_off(context, 'WEIGHT', properties.weight)
        return why or f'Paint the active Extra with the {NAMES[properties.weight]} Brush: ' + HINTS[properties.weight] + '. ' + KEYS_HINT

    @classmethod
    def poll(cls, context):
        if not (owner.visible(context) and session.find_root(context) is not None):
            return False
        if context.area is None or context.area.type != 'VIEW_3D':
            cls.poll_message_set('Over the 3D View')
            return False
        return True

    def invoke(self, context, event):
        bp = props(context)
        cur = STATE.get('op')
        if self.kind == 'ANCHOR' and cur is not None and cur.kind == 'ANCHOR':
            return {'FINISHED'}                                     # (its sub-tool turns it on and off, not a toggle)
        if cur is not None and cur.kind == self.kind and (self.kind != 'WEIGHT' or bp.brush_weight_kind == self.weight):
            stop(context)                                           # (the toggle again: off)
            return {'FINISHED'}
        why = why_off(context, self.kind, self.weight if self.kind == 'WEIGHT' else None)
        if why:
            self.report({'INFO'}, why)
            return {'CANCELLED'}
        from . import tool
        if not tool.activate(context, self.report):                 # (the button is reachable with another tool on — the N panel's popover, Object > HairMesh —: a session
            return {'CANCELLED'}                                    # _still_on would end at its first event; outside Object Mode it says so)
        if self.kind == 'WEIGHT':
            bp.brush_weight_kind = self.weight
        if self.kind != 'ANCHOR':
            p = prefs.get(context)
            if p is not None:
                p.menu_tab = 'MODIFIERS'                            # (brushFor 6979: S.modTab, pickTool('modifier'); here the D's tab, the sub-tool stays)
            context.scene.hairmesh_profile.mod_tab = 'EXTRA' if self.kind == 'WEIGHT' else 'TOPOLOGY'
        if self.kind == 'TIP':
            context.scene.hairmesh_topo.topo_tab = 'DENSITY'         # (tipBrushFor 7316: S.modTab = 'tip')
        if cur is not None and cur.kind == self.kind:
            cur.header(context)                                     # (another kind of the same brush: the running one paints it)
            select.redraw(context)
            return {'FINISHED'}
        if cur is not None:
            stop(context)
        self.root_name = session.find_root(context).name
        if self.kind == 'ANCHOR':
            P = session.get_project(session.find_root(context))
            if STATE.get('mode_of') is not P:                       # (the file keeps S.brushMode, 10061; a choice not yet stroked stays when the session comes back)
                STATE['mode_of'] = P
                bp.brush_anchor_mode = 'SURFACE' if P.settings.brushMode == 'surface' else 'SCREEN'
        self.subtool_start = context.scene.hairmesh.subtool
        self.ui = pick.ui_scale(context)
        self.stroke = None                                          # {'op', 'last', 'changed', 't0'} while LMB is down
        self.adjust = None                                          # F / Shift+F / Ctrl+RMB: {'what', ...}
        self.mouse = None                                           # the circle's centre (region pixels), None off the viewport
        self.pointer_hidden = False                                 # _pointer
        self.area = context.area
        self.popup = context.region_popup                           # the pop-up of the button that started it, read only through popup_rect
        self.shaded = False                                         # (the view's Color set once a session: the owner's own change stays)
        self.warm_key = None; self.occ = None; self.locks = []      # _warm
        STATE['op'] = self
        if self.kind == 'WEIGHT':
            _colors_on(self, context)
        ensure_handlers()
        if self.kind == 'ANCHOR':
            gc.collect()                                            # (here, with nothing waiting: Python's full collection of a model just read took ~50 ms from the first move of a first stroke)
        try:
            self._warm(context)
        except Exception:                                           # (it only goes ahead of the stroke, which has its own way: the session must start, or STATE keeps a dead operator)
            errorlog.record('brush: warming')
            traceback.print_exc()
        # (the popover of the header, Object > HairMesh, the N panel: a modal keeps the region it started in, and it paints
        # and draws its circle in the viewport's own)
        view = context.region if context.region is not None and context.region.type == 'WINDOW' else next(r for r in context.area.regions if r.type == 'WINDOW')
        with context.temp_override(region=view):
            context.window_manager.modal_handler_add(self)
        self.header(context)
        select.redraw(context)
        return {'RUNNING_MODAL'}

    def header(self, context):
        k = self.kind if self.kind in ('TIP', 'ANCHOR') else props(context).brush_weight_kind
        name = {'TIP': 'Density ', 'ANCHOR': ''}.get(k, f'{NAMES.get(k)} ')
        live.header(context.area or self.area, f'HairMesh {name}Brush ({self.values(context)})', PAINT_KEYS[k] + BRUSH_KEYS)

    def values(self, context):
        bp = props(context)
        mode = f'{"Surface" if bp.brush_anchor_mode == "SURFACE" else "Screen"}, ' if self.kind == 'ANCHOR' else ''
        return f'{mode}Radius: {bp.brush_radius} px, Hardness: {round(bp.brush_hardness * 100)}%'

    def adjust_header(self, context):
        live.header(context.area, f'HairMesh Brush ({self.values(context)})', ADJUST_KEYS[self.adjust['what']])

    def radius_px(self, context):
        return props(context).brush_radius * self.ui

    def _pointer(self, context, hide):
        """the system's pointer, while the circle stands for it (the owner's request, as in Sculpt): hidden over the viewport,
        back over the interface, before anything that may open a menu and at every exit. A window has one modal cursor and
        no stack: whoever restores it (a rebuild's wait cursor) takes this too, so every move asks for it again"""
        win = getattr(context, 'window', None)
        if win is None:
            return
        if hide:
            win.cursor_modal_set('NONE')
        elif self.pointer_hidden:
            win.cursor_modal_restore()
        self.pointer_hidden = hide

    def over_popup(self, context, event):
        """a button of the D started this session, so this modal sits above that pop-up, which stays open: what the
        cursor is over there is the pop-up's (a line of the list, a slider), never a stroke under it"""
        if self.popup is None:
            return False
        r = popup_rect(context, self.popup)
        if r is None:
            self.popup = None
            return False
        x, y, w, h = r
        return x <= event.mouse_x < x + w and y <= event.mouse_y < y + h

    # ---- the modal ----
    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            self._end(context)
            raise

    def _modal(self, context, event):
        if not _still_on(self, context):
            if self.stroke is not None:
                self._end_stroke(context, closing=True)
            return self._end(context)
        self.area = context.area                                    # (Blender moves a modal to the new area when its own is replaced: the header's)
        if self.kind == 'WEIGHT':
            show_colors(self, context.area)                         # (the view turned to Solid while it runs)
            if live.paint_follow(self.root_name):
                context.area.tag_redraw()
        t, v = event.type, event.value
        if self.adjust is not None:
            return self._adjust(context, event)
        if self.stroke is None and self.over_popup(context, event):
            self._pointer(context, False)
            if self.mouse is not None:
                self.mouse = None; context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if t == 'MOUSEMOVE' or t == 'INBETWEEN_MOUSEMOVE':
            if over_ui(context, event):
                self._pointer(context, False)
                if self.mouse is not None:
                    self.mouse = None; context.area.tag_redraw()
                return {'PASS_THROUGH'}
            self._pointer(context, True)
            self.mouse = (event.mouse_region_x, event.mouse_region_y)
            if self.stroke is None:
                self._warm(context)                                 # (a Ctrl+Z parsed another model: ready before the next press)
                self._foresee(context)
            if self.stroke is not None:
                x, y = self.mouse; lx, ly = self.stroke['last']
                dd = math.hypot(x - lx, y - ly)
                self.stroke['last'] = (x, y)
                self._dab(context, x, y, BR.dabAmount(dd))
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if t == 'LEFTMOUSE':
            if v == 'RELEASE':
                if self.stroke is not None:
                    self._end_stroke(context)
                    return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}
            if v in {'PRESS', 'DOUBLE_CLICK'}:
                if over_ui(context, event) or event.oskey:
                    return {'PASS_THROUGH'}
                follow_active(context)                              # (Ctrl+Z may have brought another Extra back)
                if not self.can_press(event):
                    return {'PASS_THROUGH'}                         # (Alt alone: Blender's — the original's navigation)
                self.mouse = (event.mouse_region_x, event.mouse_region_y)
                self._warm(context)
                self.stroke = {'op': self.press_op(event), 'last': self.mouse, 'changed': False, 't0': time.perf_counter(), 'cache': {}}   # (cache: what a painter keeps through the stroke)
                self._dab(context, *self.mouse, BR.FIRST_DAB)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
        if self.stroke is not None:
            if t == 'ESC' and v == 'PRESS':
                self._end_stroke(context)                           # (the stroke kept, as the original's release: one undo step)
            return {'RUNNING_MODAL'}                                # (nothing else while the button is down)
        if v == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
            if t in {'LEFT_BRACKET', 'RIGHT_BRACKET'} and not event.shift:
                bp = props(context)
                bp.brush_radius = int(round(BR.smaller(bp.brush_radius) if t == 'LEFT_BRACKET' else BR.bigger(bp.brush_radius)))
                self.header(context)
                context.area.tag_redraw(); select.redraw(context)
                return {'RUNNING_MODAL'}
            if t == 'F' and self.mouse is not None:
                bp = props(context); x, y = self.mouse
                # (Blender's radial control: the circle stays where the pointer was, the pointer's way sets the value)
                self.adjust = {'what': 'hardness' if event.shift else 'radius', 'centre': (x, y), 'x': x, 'dx': 0.0, 'slow': not event.shift, 'r0': bp.brush_radius, 'h0': bp.brush_hardness}
                self.adjust_header(context)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
        if t == 'RIGHTMOUSE' and v == 'PRESS' and event.ctrl and not event.shift and not over_ui(context, event):
            bp = props(context)
            self.adjust = {'what': 'drag', 'x0': event.mouse_region_x, 'y0': event.mouse_region_y, 'r0': bp.brush_radius, 'h0': bp.brush_hardness}
            self.mouse = (event.mouse_region_x, event.mouse_region_y)
            self.adjust_header(context)
            return {'RUNNING_MODAL'}
        if t == 'ESC' and v == 'PRESS' and not over_ui(context, event):
            if self.kind == 'ANCHOR':
                context.scene.hairmesh.subtool = 'SELECT'           # (Esc comes back to Select, as from the other sub-tools)
            return self._end(context)
        if v == 'PRESS' and t not in NAV and t not in MODIFIER_KEYS:
            self._pointer(context, False)                           # (a pie or a menu may open: Blender gives the modal no event until it closes)
        return {'PASS_THROUGH'}                                     # (navigation, Shift+RMB's pie, Ctrl+Z, every other key)

    def _adjust(self, context, event):
        bp = props(context); a = self.adjust; t, v = event.type, event.value
        x, y = event.mouse_region_x, event.mouse_region_y
        if a['what'] == 'drag':
            if t == 'MOUSEMOVE':
                r, h = BR.adjust(a['r0'], a['h0'], (x - a['x0']) / self.ui, -(y - a['y0']))   # (the original's y grows downwards; the radius in its own pixels)
                bp.brush_radius = int(round(r)); bp.brush_hardness = h
                self.adjust_header(context)                         # (5935)
                context.area.tag_redraw()
            elif t == 'RIGHTMOUSE' and v == 'RELEASE':
                self.adjust = None; self.header(context); select.redraw(context)
            elif t == 'ESC' and v == 'PRESS':
                bp.brush_radius = a['r0']; bp.brush_hardness = a['h0']; self.adjust = None; self.header(context)
            return {'RUNNING_MODAL'}
        if t == 'MOUSEMOVE':
            self._pointer(context, True)
            a['slow'] = a['slow'] or not event.shift                # (the Shift of Shift+F still held is not Precision)
            a['dx'] += (x - a['x']) * (SLOW if event.shift and a['slow'] else 1.0) / self.ui
            a['x'] = x
            if a['what'] == 'radius':
                bp.brush_radius = radial_radius(a['r0'], a['dx'], event.ctrl)
            else:
                bp.brush_hardness = radial_hardness(a['h0'], a['dx'], event.ctrl)
            self.adjust_header(context)
            context.area.tag_redraw()
        elif (t == 'LEFTMOUSE' and v == 'PRESS') or (t in {'RET', 'NUMPAD_ENTER'} and v == 'PRESS'):
            self.adjust = None; self.header(context); select.redraw(context)
        elif (t == 'RIGHTMOUSE' and v == 'PRESS') or (t == 'ESC' and v == 'PRESS'):
            bp.brush_radius = a['r0']; bp.brush_hardness = a['h0']; self.adjust = None; self.header(context)
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def can_press(self, event):
        if self.kind == 'ANCHOR':
            return AB.anchorOp(event.shift, event.ctrl, event.alt) is not None   # (Ctrl+Alt shrinks; Alt alone is Blender's)
        if self.kind == 'TIP':
            return not event.alt
        return BR.weightPress(WEIGHT_OF[props().brush_weight_kind], event.ctrl, event.alt)

    def press_op(self, event):
        if self.kind == 'ANCHOR':
            return AB.anchorOp(event.shift, event.ctrl, event.alt)
        if self.kind == 'TIP':
            return BR.tipOp(event.shift, event.ctrl)
        return BR.weightOp(WEIGHT_OF[props().brush_weight_kind], event.shift, event.ctrl, event.alt)

    # ---- painting ----
    def _warm(self, context, keep_seen=False):
        """the Density brush: what a stroke reads built now, never at the press: pick.Occluder (the walls' soup, the previews'
        triangles) and the locks, again only when the model, the grids, the selection or the head changed. keep_seen: the
        release of a stroke; its rebuild moved the loops, not the surfaces, so what was seen from this view stays seen"""
        if self.kind == 'ANCHOR':
            _warm_anchor(self, context)
        if self.kind != 'TIP':
            return
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return
        P = session.get_project(root)

        def state():
            hm = context.scene.hairmesh
            return (id(P), P.gen, P.rev, live.revision(root), hm.sel_curves, hm.sel_anchors, pick.head_visible(root), hm.live_preview)
        if state() == self.warm_key:
            return
        view = next(r for r in self.area.regions if r.type == 'WINDOW')
        rv3d = self.area.spaces.active.region_3d
        with slow.part('the brush made ready'), context.temp_override(region=view), live.as_they_are(root, P):
            occ = pick.Occluder(bpy.context, root, P, pick.region_projector(view, rv3d, root.matrix_world))
            T = live.row_tables(root, P)
        if keep_seen and self.occ is not None and self.occ.P is P:
            occ.memo = self.occ.memo; occ.view = self.occ.view
        self.occ = occ
        self.locks = [e['L'] for e in T.LOCK_CACHE.values()] if T is not None and T.LOCK_CACHE else []
        self.warm_key = state()

    def _foresee(self, context):
        """the Density brush with the button up: what is seen under the circle answered ahead, a slice per move (a press from
        a new view would ask ~90 rays at once, ~120 ms on the example). A dab that paints nothing: paintTipMask itself says
        which points it would ask, and the answer is kept for the press"""
        occ = self.occ
        root = bpy.data.objects.get(self.root_name)
        if self.kind != 'TIP' or occ is None or root is None or self.mouse is None:
            return
        from ..core import density as DN
        P = session.get_project(root)
        if occ.P is not P:
            return
        project = pick.region_projector(context.region, context.region_data, root.matrix_world)
        occ.aim(context, project)
        sc = occ.screen
        until = time.perf_counter() + FORESEE

        def screen(p):
            s = sc.get(id(p))
            if s is None or s[0] is not p:
                s = sc[id(p)] = (p, project(p))
            return s[1]

        def ask(L, c, i, p):
            if time.perf_counter() < until:
                occ.seen(L, c, i, p)
            return False
        bp = props(context)
        sel = select.load(context.scene.hairmesh).prune(P)
        DN.paintTipMask(P, self.locks, sel.items, self.mouse, 'paint', 0, 1, self.radius_px(context) * FORESEE_AROUND, f32(bp.brush_hardness), screen, ask, mask_points)

    def _dab(self, context, x, y, amt):
        root = bpy.data.objects.get(self.root_name)
        if root is None:
            return
        t0 = time.perf_counter()
        P = session.get_project(root)
        sel = select.load(context.scene.hairmesh).prune(P)
        project = pick.region_projector(context.region, context.region_data, root.matrix_world)
        bp = props(context)
        changed = PAINTERS[self.kind](self, context, root, P, sel, project, (x, y), self.stroke['op'], amt, bp)
        if changed:
            self.stroke['changed'] = True
            if self.kind == 'WEIGHT':
                live.schedule(root.name)                            # (the live object follows when the cursor pauses; the release updates it — the Density's loops: once per stroke, 6002)
        TIMES['dab'].append(time.perf_counter() - t0)
        del TIMES['dab'][:-400]

    def _end_stroke(self, context, closing=False):
        s = self.stroke; self.stroke = None
        live.hold(self.root_name, False)                            # (the anchor brush held the root through the stroke: _paint_anchor)
        root = bpy.data.objects.get(self.root_name)
        if s is None or root is None:
            return
        if s['changed']:
            t0 = time.perf_counter()
            P = session.get_project(root)
            END[self.kind](self, context, root, P, s)
            session.save_project(root, P)
            live.update(context, root)                              # (the Density's loops too, 6002-6004, here and not later: this undo step has to carry them)
            bpy.ops.ed.undo_push(message=UNDO.get(self.kind, 'HairMesh Paint'))
            TIMES['stroke'].append(time.perf_counter() - t0)
            del TIMES['stroke'][:-50]
            if not closing:                                         # (the session died: its area may be gone)
                self._warm(context, keep_seen=True)
                if context.area is not None:
                    self.header(context)                            # (a long rebuild just said so in the headers, and took this one)
        else:
            live.cancel(root.name)
        if self.pointer_hidden:
            self._pointer(context, True)                            # (a rebuild's wait cursor took it)
        select.redraw(context)

    def _end(self, context):
        live.hold(self.root_name, False)                            # (a stroke cut short by an error)
        if STATE.get('op') is self:
            STATE['op'] = None
            _colors_off(self)
        alone = STATE.get('op') is None                             # (a session started meanwhile owns the area, the pointer and the pending rebuild)
        if alone:
            self._pointer(context, False)
        if alone:
            live.cancel(self.root_name)
            try:
                _release_area(self.area)
            except Exception:
                pass
        elif STATE['op'].area == context.area:
            STATE['op'].header(context)                             # (a long rebuild just said so in the headers, and took the new session's)
        try:
            select.redraw(context)
        except Exception:
            pass
        return {'FINISHED'}

    def cancel(self, context):
        self._end(context)                                          # (a file loaded, Blender quitting)


def stop(context=None):
    """ends the running session (its toggle button again, a tab left): it returns at its next event"""
    op = STATE.get('op')
    STATE['op'] = None
    if op is not None:
        _colors_off(op)
        try:
            _release_area(op.area)
        except Exception:
            pass
        if context is not None:
            op._pointer(context, False)
    if context is not None:
        select.redraw(context)


# ---- the weight brush's colors in the view (4.8b) ----

def _space_at(ptr):
    """the 3D View's space with this pointer, on any screen, else None. Numbers compared, as _release_area: a closed area
    is freed with its spaces and nothing of it may be touched; another editor on top keeps the space in the area's list,
    another workspace keeps its screen, Ctrl+Space hands the space to the maximized area"""
    for s in bpy.data.screens:
        for a in s.areas:
            for sp in a.spaces:
                if sp.as_pointer() == ptr:
                    return sp
    return None


def show_colors(op, area):
    """the view's Color on Attribute, on the brush's own view and in Solid only (the other modes don't read it), once a
    session; the value it had kept to go back"""
    if op.shaded:
        return
    sp = area.spaces.active
    if sp.shading.type != 'SOLID':
        return
    op.shaded = True
    if sp.shading.color_type != 'VERTEX':
        STATE['shade'] = (sp.as_pointer(), sp.shading.color_type)
        sp.shading.color_type = 'VERTEX'


def hide_colors():
    """the view's Color back, unless the owner changed it meanwhile"""
    s = STATE.get('shade')
    STATE['shade'] = None
    sp = _space_at(s[0]) if s is not None else None
    if sp is not None and sp.shading.color_type == 'VERTEX':
        sp.shading.color_type = s[1]


def _colors_on(op, context):
    try:
        root = session.find_root(context)
        live.paint_begin(root, session.get_project(root))
        show_colors(op, context.area)
    except Exception as e:                                          # (what is shown must not cost the session)
        errorlog.record('brush: colors on')
        print('HairMesh brush:', repr(e))


def _colors_off(op):
    """the session is over: the view's Color back, the mesh without colors"""
    try:
        hide_colors()
        if op.kind == 'WEIGHT':
            live.paint_end(op.root_name)
    except Exception as e:                                          # (its end never raises: _end runs from the modal's own except)
        errorlog.record('brush: colors off')
        print('HairMesh brush:', repr(e))


# ---- what each brush paints (4.7b: the weights; 4.8: the Density) ----

def _paint_weight(op, context, root, P, sel, project, m, how, amt, bp):
    cache = op.stroke['cache']
    if 'shown' not in cache:                                        # (the modifiers' surfaces of this model: after an undo the parsed model has none until an update)
        live.tube_previews(root, P)
        cache['shown'] = live.tubes_under_curves(root, P)
        cache['live'] = {gid: ob.name for gid, ob in live.live_objects(root).items()}
    shown = cache['shown']
    changed = TB.paintMask(P, sel.items, m, how, amt, mask_brush(bp), op.radius_px(context), f32(bp.brush_hardness), project, lambda t: t.id in shown,
                           paint_ids(P, sel, bp.brush_weight_kind))
    if changed:
        live.paint_tubes(cache['live'], P, {t.id: live.tube_colors(root, P, t) for t in P.tubes() if t.dirty and t.id in shown})   # (the painted weights at once, where the mesh's rings are; the shape when the cursor pauses)
    return changed


def _end_weight(op, context, root, P, stroke):
    pass                                                            # (the modifiers are dirty: live.update rebuilds their displays)


_MASK_PTS = {}                                                      # curve id -> (its samples list, the MASK_N points along it)


def mask_points(c):
    """the MASK_N points pointOnCurve(c, i / (MASK_N - 1)) the masks are painted at, kept while the curve keeps its
    samples (the same points: lot 4.8 — the Density brush and its preview asked for them on every dab and redraw)"""
    from ..core.density import MASK_N
    from ..core.geom import pointOnCurve
    e = _MASK_PTS.get(c.id)
    if e is None or e[0] is not c.samples:
        e = (c.samples, [pointOnCurve(c, i / (MASK_N - 1)) for i in range(MASK_N)])
        _MASK_PTS[c.id] = e
        if len(_MASK_PTS) > 4096:
            _MASK_PTS.pop(next(iter(_MASK_PTS)))
    return e[1]


def _paint_tip(op, context, root, P, sel, project, m, how, amt, bp):
    """4.8: a dab of the Density brush (paintTipMask 6585) over the locks of the row tables, a point painted only where it
    is seen (pick.Occluder: built by the session's _warm, aimed at the view by the stroke's first dab)"""
    from ..core import density as DN
    cache = op.stroke['cache']
    if 'occ' not in cache:
        occ = getattr(op, 'occ', None)
        if occ is not None and occ.P is P:
            cache['locks'] = op.locks
        else:
            with live.as_they_are(root, P):
                occ = pick.Occluder(context, root, P, project)
                T = live.row_tables(root, P)
            cache['locks'] = [e['L'] for e in T.LOCK_CACHE.values()] if T is not None and T.LOCK_CACHE else []
        occ.aim(context, project)
        cache['occ'] = occ
        cache['tips'] = set()
    sc = cache.setdefault('screen', {})

    def screen(p):                                                  # (the view stays through the stroke: each point projected once)
        s = sc.get(id(p))
        if s is None or s[0] is not p:
            s = sc[id(p)] = (p, project(p))
        return s[1]
    before = {k.id: {c: list(W) for c, W in (k.mask or {}).items()} for k in P.of_type('tip')}
    changed = DN.paintTipMask(P, cache['locks'], sel.items, m, how, amt, f32(bp.brush_tip_strength), op.radius_px(context), f32(bp.brush_hardness), screen, cache['occ'].seen, mask_points)
    if changed:
        cache['tips'].update(k.id for k in P.of_type('tip') if {c: list(W) for c, W in (k.mask or {}).items()} != before.get(k.id))
    return changed


def _end_tip(op, context, root, P, stroke):
    """the release (6002): S.surfDirty on the painted Densities' locks; _end_stroke rebuilds them"""
    from ..core import groups as G
    ids = []
    for kid in stroke['cache'].get('tips', ()):
        k = P.items.get(kid); g = P.items.get(k.parent) if k is not None else None
        if g is not None:
            ids += [c.id for c in G.curvesIn(P, g)]
    if ids:
        live.touch(root, ids)


# ---- 4.9: the anchor brush (core/anchorbrush.py) ----

ANCHOR_HELP = '\n'.join(('LMB drag: the anchors under the circle follow, softer towards its rim.',
                         'Shift + LMB drag: smooth them.',
                         'Ctrl + LMB drag: inflate the lock. Ctrl + Alt + LMB drag: shrink it.',
                         'With curves or a NURBS disc selected, only their anchors.',
                         '[ ] or F: the radius. Shift + F: the hardness. Ctrl + RMB drag: both.',
                         'Esc: back to Select.'))


def anchor_brush_rows(context):
    """the Brush's panel (9004-9011): Screen / Surface, Radius, Hardness"""
    from .tool import enum
    return [[enum('brush_anchor_mode', 'Mode')], [('prop', 'brush_radius', 'Radius'), ('prop', 'brush_hardness', 'Hardness')]]


def _arm(ctx):
    """the sub-tool is Brush with no session on this view (an undo brought it back, a file opened with it, the tool or Object
    Mode came back, the sub-tool was set by the header's own buttons): one starts after this draw. A draw cannot start a
    modal, a timer can, with this area in its context"""
    hm = ctx.scene.hairmesh
    if hm.subtool != 'BRUSH' or STATE.get('armed') is not None or STATE.get('arm_failed') or ctx.window is None or ctx.mode != 'OBJECT':
        return
    from . import tool
    if not tool.is_active(ctx) or not owner.visible(ctx) or session.find_root(ctx) is None:
        return
    STATE['armed'] = (ctx.window.as_pointer(), ctx.area.as_pointer())
    bpy.app.timers.register(_start_armed, first_interval=0.0)


def _start_armed():
    armed = STATE.pop('armed', None)
    if armed is None or STATE.get('op') is not None:
        return None
    try:
        for w in bpy.context.window_manager.windows:
            if w.as_pointer() != armed[0]:
                continue
            for a in w.screen.areas:
                if a.as_pointer() == armed[1] and a.type == 'VIEW_3D':
                    r = next(x for x in a.regions if x.type == 'WINDOW')
                    with bpy.context.temp_override(window=w, area=a, region=r):
                        bpy.ops.hairmesh.brush('INVOKE_DEFAULT', kind='ANCHOR')
    except Exception:                                               # (a timer must never raise, and no redraw tries again)
        errorlog.record('timer: brush armed')
        traceback.print_exc()
        STATE['arm_failed'] = True
    return None


def follow_subtool(context):
    """the sub-tool was just chosen: the anchor brush on with it, here and not at the next draw (a button of the D: the
    session has to know that pop-up), off with any other. Its keys and buttons call this"""
    op = STATE.get('op')
    if context.scene.hairmesh.subtool != 'BRUSH':
        if op is not None and op.kind == 'ANCHOR':
            stop(context)
        return
    from . import tool
    if (op is None or op.kind != 'ANCHOR') and tool.is_active(context) and context.area is not None and context.area.type == 'VIEW_3D' and session.find_root(context) is not None:
        bpy.ops.hairmesh.brush('INVOKE_DEFAULT', kind='ANCHOR')


def _warm_anchor(op, context):
    """Surface: what the press reads ready before it, the walls' arrays, the head's and the crowns' (a first press paid
    them, ~40 ms on the example; a Ctrl+Z parsed another model, and the next one paid them again)"""
    root = bpy.data.objects.get(op.root_name)
    if root is None or props(context).brush_anchor_mode != 'SURFACE':
        return
    P = session.get_project(root)
    key = (id(P), P.gen, P.rev, live.revision(root))
    if key != op.warm_key:
        pick.warm_hits(context, root, P)
        op.warm_key = key


def _brush_view(context, root, P, project, sel):
    """the BrushView of a 3D View (6081-6090, 5656): the model's space, the region's pixels. The view stays through a
    stroke: what reads it is made once; `hit` sees the walls as the press found them (the root is held: Surface + Shift
    asks at every move; the original's frame loop had rebuilt them, ours does at the release)"""
    from mathutils import Vector
    from .coords import from_blender
    from .pick import model_cam, model_ray
    region = context.region; rv3d = context.region_data
    cam_dir, _piv = model_cam(root, rv3d)
    inv = root.matrix_world.inverted()
    c = inv @ rv3d.view_matrix.inverted().translation
    cam_pos = AB.V3(*from_blender(c.x, c.y, c.z))
    m11 = rv3d.window_matrix[1][1]
    ortho = None; tan_half = None
    if rv3d.is_perspective:
        tan_half = 1 / m11
    else:
        ortho = 2 / m11 / (sum(root.matrix_world.to_scale()) / 3)
    discs = [g for g in P.guides() if g.kind == 'disc' and edit.isVisible(P, g) and g.id in _shown_guides(P, sel)]
    shown_curves = None
    st_ = session.state(root, P)

    def hit(m):
        kept = st_.get('wall_soup')
        if kept is not None:
            st_['wall_soup'] = ((P.gen, P.rev, live.revision(root)), kept[1])   # (see above: wall_soup would gather the arrays again after every move)
        ray = model_ray(root, region, rv3d, m)
        p = pick.brush_hit(context, root, P, ray, discs)
        return (p, p.distanceTo(cam_pos)) if p is not None else None

    def near(m, r):
        nonlocal shown_curves
        if shown_curves is None:
            shown_curves = [x for x in P.curves() if x.id in view.shown]
        x, y = m
        for c_ in shown_curves:
            b = pick.screen_box(c_, project)
            if b is not None and b[0] - r <= x <= b[2] + r and b[1] - r <= y <= b[3] + r:
                yield from c_.samples

    view = AB.BrushView(project, lambda m: model_ray(root, region, rv3d, m), cam_dir, cam_pos, region.height, tan_half, ortho, hit, near)
    return view


def _shown_guides(P, sel):
    return {g.id for g in G.shownGuides(P, *G.shownCurves(P, sel.items, sel.anchors)[1:])}


def _paint_anchor(op, context, root, P, sel, project, m, how, amt, bp):
    """the press and the moves of a stroke (6123-6143, 5938-5953): on the anchors and in memory alone; the draw handler
    resamples the dirty curves it draws, and the release (_end_stroke) saves, rebuilds the walls and pushes the undo step"""
    cache = op.stroke['cache']
    st = cache.get('st')
    if st is None:
        CM.rebuild_dirty(P)
        mode = AB.SURFACE if bp.brush_anchor_mode == 'SURFACE' else AB.SCREEN
        if mode == AB.SURFACE:
            pick.wall_soup(root, P)
        live.hold(root.name)                                        # (nothing rebuilds the locks until the release: a redraw that reads the walls keeps them as they are)
        shown = AB.brushVisible(P, sel.items, sel.anchors)
        hair = {c.id for c in G.shownCurves(P, sel.items, sel.anchors)[0]}
        view = cache['view'] = _brush_view(context, root, P, project, sel)
        view.shown = shown
        anchors = AB.brushAnchors(P, sel.items, edit.anchorList(P, sel.items, sel.anchors, shown=hair))
        cache['st'] = st = AB.startStroke(P, view, m, how, op.radius_px(context), f32(bp.brush_hardness), mode, anchors, shown, P.S.scale, op.ui)
        cache['mode'] = mode
        if st.type == 'inflate' and not st.cap:
            live.notify(context, AB.NO_LOCK)
        changed = st.moved
    else:
        changed = AB.moveStroke(P, cache['view'], m, st)
    if changed:
        live._drag_sync(root.name)                                  # (the frame's sync: a symmetric crown, the anchors attached to a moved connector or to a curve)
    return changed


def _end_anchor(op, context, root, P, stroke):
    """the release (6001-6002): nothing to mark, the curves resampled count as changed (live.row_tables)"""
    P.settings.brushMode = stroke['cache'].get('mode', P.settings.brushMode)
    CM.rebuild_dirty(P)


PAINTERS = {'WEIGHT': _paint_weight, 'TIP': _paint_tip, 'ANCHOR': _paint_anchor}
END = {'WEIGHT': _end_weight, 'TIP': _end_tip, 'ANCHOR': _end_anchor}


# ---------------- drawing ----------------

_handles = {}


def ensure_handlers():
    if not _handles:
        _handles['px'] = bpy.types.SpaceView3D.draw_handler_add(draw_circle, (), 'WINDOW', 'POST_PIXEL')
        _handles['tips'] = bpy.types.SpaceView3D.draw_handler_add(draw_tips, (), 'WINDOW', 'POST_VIEW')


def remove_handlers():
    for k in list(_handles):
        bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(k), 'WINDOW')


CIRCLE_PX = 1.5                                                     # the brush's circles (at 100 %: pick.line_scale)


def circle_points(x, y, r, n=48):
    """pure: a closed loop of n + 1 points around (x, y)"""
    return [(x + r * math.cos(2 * math.pi * j / n), y + r * math.sin(2 * math.pi * j / n)) for j in range(n + 1)]


def draw_circle():
    op = STATE.get('op')
    ctx = bpy.context
    if op is None and ctx.area is not None:
        _arm(ctx)
    if op is None or ctx.area is None or ctx.area != op.area:
        return
    import gpu
    from .draw import polyline2d
    bp = props(ctx)
    if op.adjust is not None and op.adjust['what'] != 'drag':
        x, y = op.adjust['centre']
    elif op.mouse is not None:
        x, y = op.mouse
    else:
        return
    r = op.radius_px(ctx)
    w = CIRCLE_PX * pick.line_scale(ctx)
    gpu.state.blend_set('ALPHA')
    painting = op.stroke is not None
    polyline2d(ctx.region, circle_points(x, y, r), (1.0, 1.0, 1.0, 0.95 if painting else 0.75), w)
    polyline2d(ctx.region, circle_points(x, y, max(2.0, r * bp.brush_hardness)), (1.0, 1.0, 1.0, 0.35), w)   # (the inner circle: full strength, 6155)
    what = op.adjust['what'] if op.adjust is not None else None
    if what == 'radius':
        polyline2d(ctx.region, circle_points(x, y, op.adjust['r0'] * op.ui), (1.0, 1.0, 1.0, 0.5), max(1.0, w / 2))   # (Blender's thin ring: where the value started)
    elif what == 'hardness':
        import blf
        text = f'{round(bp.brush_hardness * 100)}%'
        blf.size(0, 18 * op.ui); blf.color(0, 1.0, 1.0, 1.0, 0.95)
        tw, th = blf.dimensions(0, text)
        blf.position(0, x - tw / 2, y - th / 2, 0); blf.draw(0, text)
    gpu.state.blend_set('NONE')


def _srgb(c):
    return [(12.92 * v if v <= 0.0031308 else 1.055 * math.pow(v, 1 / 2.4) - 0.055) for v in c]


TIPV = ((0x7a / 255, 0x30 / 255, 0x06 / 255), (0xff / 255, 0xe0 / 255, 0x4a / 255))   # TIPV (6244): dark orange (weak) … yellow (full), as sRGB
_tip_cache = {}                                                     # 'key' -> (key, batch)


def _lin(c):
    return [v / 12.92 if v <= 0.04045 else math.pow((v + 0.055) / 1.055, 2.4) for v in c]


def tips_shown(context, P):
    """updateTipViz (6247-6249): the selected Density items, else all of them while the Density brush runs (the original
    also on its Modifiers' Density tab; here the tab stays in the preferences with the D closed: the lines would never
    go away)"""
    sel = select.load(context.scene.hairmesh)
    ks = [P.items[i] for i in sel.items if i in P.items and P.items[i].type == 'tip']
    if ks:
        return ks
    if running('TIP') is not None:
        return list(P.of_type('tip'))
    return []


def tips_drawn(context, P):
    """what draw_tips draws: tips_shown, nothing while a conversion is pending (the Convert preview shows the meshes alone;
    a cancel brings them back)"""
    return [] if P.conv else tips_shown(context, P)


def tip_segments(P, ks):
    """the painted mask of the Density items along their curves (updateTipViz 6253-6265): segments where a weight is
    above 0.02, colored dark orange (weak) … yellow (full) — lerped in three.js's linear space, given as sRGB"""
    from ..core.density import MASK_N
    from ..core.edit import isVisible
    lo = _lin(TIPV[0]); hi = _lin(TIPV[1])
    seg = []; col = []
    for k in ks:
        for cid, W in (k.mask or {}).items():
            c = P.items.get(cid)
            if c is None or c.arc is None or len(c.samples) < 2 or not isVisible(P, c):
                continue
            prev = None; pw = 0
            pts = mask_points(c)
            for i in range(MASK_N):
                p = pts[i]; w = W[i] or 0
                if prev is not None and (w > 0.02 or pw > 0.02):
                    seg += [prev, p]
                    col += [_srgb([lo[j] + (hi[j] - lo[j]) * pw for j in range(3)]), _srgb([lo[j] + (hi[j] - lo[j]) * w for j in range(3)])]
                prev = p; pw = w
    return seg, col


def draw_tips():
    """the Density preview (updateTipViz 6246): the painted masks along the curves, depth-tested, over the curves"""
    ctx = bpy.context
    if not owner.visible(ctx) or ctx.region_data is None:
        return
    root = session.find_root(ctx)
    if root is None:
        return
    try:
        P = session.get_project(root)
    except Exception:
        return
    if not P.of_type('tip'):
        return
    ks = tips_drawn(ctx, P)
    if not ks:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    from mathutils import Vector
    from .coords import to_blender
    from .draw import offset_projection
    key = (root.name, tuple(root.matrix_world.col[3]), tuple((k.id, k.visible, tuple((c, tuple(W)) for c, W in (k.mask or {}).items())) for k in ks),
           tuple((c.id, c.sample_key if c.sample_key is not None else id(c.samples)) for k in ks for c in (P.items.get(i) for i in (k.mask or {})) if c is not None))
    c = _tip_cache.get('k')
    if c is None or c[0] != key:
        seg, col = tip_segments(P, ks)
        M = root.matrix_world
        batch = None
        if seg:
            sh = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
            batch = batch_for_shader(sh, 'LINES', {'pos': [tuple(M @ Vector(to_blender(p))) for p in seg], 'color': [(*rgb, 1.0) for rgb in col]})
        c = (key, batch); _tip_cache['k'] = c
    if c[1] is None:
        return
    sh = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    sh.bind()
    sh.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
    sh.uniform_float('lineWidth', 4.0 * pick.line_scale(ctx))
    gpu.state.blend_set('ALPHA'); gpu.state.depth_test_set('LESS_EQUAL'); gpu.state.depth_mask_set(False)
    gpu.matrix.push_projection()
    try:
        gpu.matrix.load_projection_matrix(offset_projection(gpu.matrix.get_projection_matrix(), ctx.region_data, 2.0))
        c[1].draw(sh)
    finally:
        gpu.matrix.pop_projection()
        gpu.state.depth_test_set('NONE'); gpu.state.blend_set('NONE')


CLASSES = (HairMeshBrushProps, HAIRMESH_OT_brush)


@bpy.app.handlers.persistent
def _on_load(_dummy):
    STATE['op'] = None; STATE['shade'] = None; STATE['armed'] = None; STATE['arm_failed'] = False


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    hide_colors()                                                   # (a file opened without its UI keeps this very view)


@bpy.app.handlers.persistent
def _on_save_pre(*_args):
    """the .blend gets the view's Color of the owner, not the brush's"""
    s = STATE.get('shade')
    sp = _space_at(s[0]) if s is not None else None
    if sp is not None and sp.shading.color_type == 'VERTEX':
        sp.shading.color_type = s[1]
        STATE['saving'] = s[0]


@bpy.app.handlers.persistent
def _on_save_post(*_args):
    ptr = STATE.pop('saving', None)
    sp = _space_at(ptr) if ptr is not None and STATE.get('shade') is not None else None
    if sp is not None:
        sp.shading.color_type = 'VERTEX'


HANDLERS = (('load_post', _on_load), ('load_pre', _on_load_pre), ('save_pre', _on_save_pre), ('save_post', _on_save_post), ('save_post_fail', _on_save_post))


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.hairmesh_brush = bpy.props.PointerProperty(type=HairMeshBrushProps)
    for name, fn in HANDLERS:
        getattr(bpy.app.handlers, name).append(fn)
    ensure_handlers()


def unregister():
    STATE['op'] = None
    hide_colors()
    remove_handlers()
    for name, fn in HANDLERS:
        if fn in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).remove(fn)
    del bpy.types.WindowManager.hairmesh_brush
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
