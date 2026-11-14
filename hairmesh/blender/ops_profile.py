# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Step 4.6a (docs/fase-4-plano.md): the Profile page of the D's Modifiers tab (modifierPanel 6915-6973: the preset, the
user's own presets — in the add-on preferences, not in localStorage —, Save / Delete preset, Apply) and the sheet of a
selected Profile modifier (tubeProps 10493: radius and rotation at the start, middle and end, "= Start profile" /
"= Middle profile", Collapse the tip, Tip length, Convert to curves & walls, Auto radius, Copy / Paste modifier).
The modifier's surface shows in its group's live object (D4.5: live.tube_previews). The profile editor (D4.6) is
step 4.6b: until then the panel's profile is its preset's.

The core: tube.applyModifier / ownProfs / ensureTubeRef / convertTube (4.5) and modifier.py (copy / paste, presets)."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from ..core import curves as CM, groups as G, modifier as MOD, tube as TB
from . import deferred, live, owner, prefs, select, session
from .coords import f32, field_same
from .props import add_unit_twins, measure

CLIP = [None]                                                       # modClip (10077): the copied modifier, for the session
EDITED = '__edited__'                                               # the preset menu's "— edited —" (the panel's profile changed by hand: 4.6b; an empty identifier is a separator)
APPLY_TIP = '\n'.join(('Draw a curve (2), select it, press Apply.',
                       'The radius, rotation and tip are on its sheet.',
                       'Edit the curve: the surface follows.',
                       'Convert to Curves & Walls on its sheet: make curves and walls.'))              # (6972)


SAVE_ICON = 'PRESET_NEW'                                            # (lot 1.0.2, item 2: Blender's new-preset icon, not the + an Apply was taken for)
SAVE_TEXT = ''                                                      # (icon only, as Blender's own preset row and item 8: a label made Save the row's widest button)
APPLY_ICON = 'CHECKMARK'
APPLY_FIRST = 'Apply, then edit Start, Middle, End'                  # (the three profiles are the modifier's, on its sheet after Apply)


def _poll(context):
    return owner.visible(context) and session.find_root(context) is not None


def selected_tube(context):
    root, P, sel = select.current(context)
    if P is None:
        return root, P, None
    for iid in reversed(sel.items):
        it = P.items.get(iid)
        if it is not None and it.type == 'tube':
            return root, P, it
    return root, P, None


# ---------------- the panel: the preset (S.modPreset) ----------------

_ITEMS = []                                                         # (kept alive: Blender reads the strings of a dynamic enum later)


def _preset_items(self, context):
    items = [(EDITED, '— Edited —', 'Custom profile edited in the viewport', 'CURVE_BEZCIRCLE', 0)]
    items += [(n, n.title(), 'A built-in preset', 'MESH_DATA', i + 1) for i, (n, _p) in enumerate(TB.BUILTIN_PROFILES)]
    base = len(items)
    items += [(p['name'], p['name'], 'A saved preset (the add-on preferences)', 'USER', base + i) for i, p in enumerate(prefs.user_profiles(context))]
    _ITEMS[:] = items
    return _ITEMS


def _names(context):
    """the identifiers in the menu's order (the enum numbers are their indices) — read without rebuilding _ITEMS, whose
    strings Blender may still hold (the dynamic enum's known crash)"""
    return [EDITED, *(n for n, _p in TB.BUILTIN_PROFILES), *(p['name'] for p in prefs.user_profiles(context))]


def _get_preset(self):
    names = _names(bpy.context)
    name = self.get('preset_name', 'Quad')
    return names.index(name) if name in names else 1                # (a preset deleted meanwhile: Quad)


def _set_preset(self, v):
    names = _names(bpy.context)
    if 0 <= v < len(names):
        self['preset_name'] = names[v]


MOD_TABS = (('PROFILE', 'Profile', 'Sweep a cross-section profile along a curve', 'MOD_SCREW', 0),
            ('EXTRA', 'Extra', 'Mask, Thickness, Twist, Noise, Curls: added on top of a profile modifier', 'MODIFIER', 2),   # (4.7b: 6929)
            ('TOPOLOGY', 'Topology', 'The mesh of a whole group: Cap (close the lock ends), Fill (fill holes), Density', 'MESH_GRID', 1))


class HairMeshProfileProps(PropertyGroup):
    """The Modifiers tab's state on the Scene: the page shown (S.modTab) and the panel's preset (S.modPreset)"""
    mod_tab: EnumProperty(name='Modifiers', items=MOD_TABS, default='PROFILE')
    mod_preset: EnumProperty(name='Profile', description='The profile Apply sweeps along the selected curves: a built-in preset or a saved one', items=_preset_items, get=_get_preset, set=_set_preset)


def panel_profile(context):
    """modProfile (9593): the panel's profile — edited in the profile editor (4.6b: the preset menu then says
    "— Edited —"), else its preset's points, else Quad"""
    import json
    pp = context.scene.hairmesh_profile
    name = pp.get('preset_name', 'Quad')
    if name == EDITED and pp.get('edited'):
        try:
            pts = json.loads(pp['edited'])
            if isinstance(pts, list) and len(pts) >= 3:
                return [[float(q[0]), float(q[1])] for q in pts]
        except (ValueError, TypeError, IndexError):
            pass
    return MOD.profileByName(name, prefs.user_profiles(context)) or MOD.profileByName('Quad')


# ---------------- the sheet's controls: views on the selected modifier ----------------

def _tube():
    return selected_tube(bpy.context)


def _set(key, conv=lambda v: v):
    def setter(self, v):
        root, P, t = _tube()
        if t is None:
            return
        v = conv(v)
        if field_same(t.p.get(key), v):
            return
        t.p = {**t.p, key: v}; t.dirty = True
        deferred.changed(bpy.context, root)                         # (the modifier's surface follows by its key: live.tube_previews; no lock reads it)
    return setter


def _get(key, default, conv=lambda v: v):
    def getter(self):
        _r, _P, t = _tube()
        return conv(t.p.get(key, default) if t is not None and t.p.get(key) is not None else default)
    return getter


def _set_step(v):
    """the modifier's own loop step (lot 4.7b item 4): a value of its own, or 0 — the key goes (it inherits again)"""
    root, P, t = _tube()
    if t is None:
        return
    v = f32(v) if v > 0 else None
    if field_same(t.p.get('step'), v):
        return
    t.p = {**{k: x for k, x in t.p.items() if k != 'step'}, **({'step': v} if v is not None else {})}; t.dirty = True
    deferred.changed(bpy.context, root)


def _r_max(self, context=None):
    _r, _P, t = _tube()
    if t is None:
        return 100.0
    ref = t.p.get('rRef') or max(t.p.get('r0') or 0, t.p.get('r1') or 0, t.p.get('r2') or 0) or 1.0
    return max(8 * ref, 1.05 * max(t.p.get('r0') or 0, t.p.get('r1') or 0, t.p.get('r2') or 0))   # (rmax, 10504)


class HairMeshTubeProps(PropertyGroup):
    """The sheet of a selected Profile modifier (tubeProps 10493): on the WindowManager, views on the model; the
    modifier's surface and the undo step follow when the slider stops (deferred.py)."""
    tube_r0: FloatProperty(name='Radius', description='The radius at the start of the lock', min=0.0, soft_max=1000.0, precision=2, get=_get('r0', 0.0, float), set=_set('r0', f32))
    tube_r1: FloatProperty(name='Radius', description='The radius in the middle of the lock', min=0.0, soft_max=1000.0, precision=2, get=_get('r1', 0.0, float), set=_set('r1', f32))
    tube_r2: FloatProperty(name='Radius', description='The radius at the end of the lock', min=0.0, soft_max=1000.0, precision=2, get=_get('r2', 0.0, float), set=_set('r2', f32))
    tube_rot0: IntProperty(name='Rotation', description='The profile turned about the curve at the start (degrees)', min=-180, max=180, get=_get('rot0', 0, lambda v: int(round(v))), set=_set('rot0', int))
    tube_rot1: IntProperty(name='Rotation', description='The profile turned about the curve in the middle (degrees)', min=-180, max=180, get=_get('rot1', 0, lambda v: int(round(v))), set=_set('rot1', int))
    tube_rot2: IntProperty(name='Rotation', description='The profile turned about the curve at the end (degrees)', min=-180, max=180, get=_get('rot2', 0, lambda v: int(round(v))), set=_set('rot2', int))
    tube_collapse: BoolProperty(name='Collapse the Tip', description='The end of the lock narrows to one point (on by default)', get=_get('collapse', False, bool), set=_set('collapse', bool))   # (!!it.p.collapse, 7184)
    tube_tip_len: FloatProperty(name='Tip Length', description='Share of the length over which the tip narrows', min=0.02, max=0.8, step=1,
                                get=_get('tipLen', 0.25, float), set=_set('tipLen', f32))
    tube_step: FloatProperty(name='Loop Step', description="This modifier's own loop step: the loops of its lock after Convert, and of its preview now. 0: its group's step (the lock gets it on Convert)",
                             min=0.0, soft_max=10000.0, precision=2, get=_get('step', 0.0, float), set=lambda self, v: _set_step(v))


add_unit_twins(HairMeshTubeProps, 'tube_r0', 'tube_r1', 'tube_r2', 'tube_step')
TUBE_ATTRS = frozenset(HairMeshTubeProps.__annotations__)
PROFILE_ATTRS = frozenset(HairMeshProfileProps.__annotations__)


# ---------------- operators ----------------

def _finish(context, root, P, sel, report, msg):
    select.store(context.scene.hairmesh, sel)
    G.panelsRendered(P, sel.items[len(sel.items) - 1] if sel.items else None)   # onSelChange (the modifier's sheet: ensureTubeRef, ownProfs)
    CM.rebuild_dirty(P)
    session.save_project(root, P)
    live.update(context, root)
    select.redraw(context)
    if msg:
        report({'INFO'}, msg)
    return {'FINISHED'}


def _targets(context):
    _r, P, sel = select.current(context)
    return [c for c in TB.modTargets(P, sel.items) if len(c.samples) >= 2] if P is not None else []


class HAIRMESH_OT_apply_modifier(bpy.types.Operator):
    """Sweep this profile along the selected curve(s): each gets a Profile modifier, whose sheet then has its own Start, Middle and End profiles to edit (a curve that has one gets the new profile, its settings kept)"""
    bl_idname = 'hairmesh.apply_modifier'
    bl_label = 'Apply'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        if not _targets(context):
            cls.poll_message_set('Select a curve in the viewport, then press Apply')   # (6716)
            return False
        return True

    def execute(self, context):
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)
        made, msg = TB.applyModifier(P, sel.items, panel_profile(context))
        if not made:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        sel.items = [t.id for t in made]; sel.anchors = []
        return _finish(context, root, P, sel, self.report, msg)


class HAIRMESH_OT_save_profile_preset(bpy.types.Operator):
    """Save this profile as a user preset, kept in the add-on preferences (it is not applied to any curve: Apply does that)"""
    bl_idname = 'hairmesh.save_profile_preset'
    bl_label = 'Save Preset'
    bl_options = {'REGISTER', 'INTERNAL'}

    name: StringProperty(name='Name', default='My profile')

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def invoke(self, context, event):
        cur = context.scene.hairmesh_profile.get('preset_name', '')
        self.name = cur if cur and TB.builtinProfile(cur) is None else 'My profile'
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip()
        if not name:
            return {'CANCELLED'}
        if TB.builtinProfile(name) is not None:
            self.report({'WARNING'}, 'That name belongs to a built-in preset')   # (6950)
            return {'CANCELLED'}
        lst = [p for p in prefs.user_profiles(context) if p['name'] != name]
        lst.append({'name': name, 'pts': [list(q) for q in panel_profile(context)]})
        prefs.save_user_profiles(lst, context)
        context.scene.hairmesh_profile['preset_name'] = name
        self.report({'INFO'}, f'Preset "{name}" saved')
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_delete_profile_preset(bpy.types.Operator):
    """Delete the selected saved preset (the built-in presets stay)"""
    bl_idname = 'hairmesh.delete_profile_preset'
    bl_label = 'Delete Preset'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        if not owner.visible(context):
            return False
        name = context.scene.hairmesh_profile.get('preset_name', 'Quad')
        if not any(p['name'] == name for p in prefs.user_profiles(context)):
            cls.poll_message_set('Pick a saved preset to delete (the built-in presets stay)')   # (6957)
            return False
        return True

    def execute(self, context):
        name = context.scene.hairmesh_profile.get('preset_name', 'Quad')
        import json
        pts = panel_profile(context)
        prefs.save_user_profiles([p for p in prefs.user_profiles(context) if p['name'] != name], context)
        context.scene.hairmesh_profile['edited'] = json.dumps(pts)    # (6965: the profile stays, the menu says "— edited —")
        context.scene.hairmesh_profile['preset_name'] = EDITED
        self.report({'INFO'}, f'Preset "{name}" deleted')
        select.redraw(context)
        return {'FINISHED'}


def _tube_poll(cls, context):
    if not _poll(context):
        return False
    if selected_tube(context)[2] is None:
        cls.poll_message_set('Select a Profile modifier (in the Outliner, O, or a click on its surface)')
        return False
    return True


def _curve_poll(cls, context):
    if not _tube_poll(cls, context):
        return False
    _r, P, t = selected_tube(context)
    c = P.items.get(t.src)
    if c is None or len(c.samples) < 2:
        cls.poll_message_set('The source curve is missing')       # (6760, 7195)
        return False
    return True


class HAIRMESH_OT_convert_tube(bpy.types.Operator):
    """Make a curve along every profile corner and walls between them (a closed volume lock); the modifier and its source curve are deleted"""
    bl_idname = 'hairmesh.convert_tube'
    bl_label = 'Convert to Curves & Walls'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _curve_poll(cls, context)

    def execute(self, context):
        root, P, t = selected_tube(context)
        CM.rebuild_dirty(P)
        before = set(P.items)
        made, msg = TB.convertTube(P, t)
        if not made:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        _r, _P, sel = select.current(context)
        sel.items = [e.id for e in made]; sel.anchors = []         # (6808: S.sel = the edges)
        live.touch(root, [i for i in P.items if i not in before and P.items[i].type == 'curve'])   # (S.surfDirty: the new lock)
        return _finish(context, root, P, sel, self.report, msg)


class HAIRMESH_OT_tube_auto_radius(bpy.types.Operator):
    """Set the radius from the width of the existing locks (or the curve length); the sliders are re-centered on it"""
    bl_idname = 'hairmesh.tube_auto_radius'
    bl_label = 'Auto Radius'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _curve_poll(cls, context)

    def execute(self, context):
        root, P, t = selected_tube(context)
        CM.rebuild_dirty(P)
        t.p = {**t.p, 'rRef': 0}; TB.ensureTubeRef(P, t); t.dirty = True
        _r, _P, sel = select.current(context)
        return _finish(context, root, P, sel, self.report, None)


class HAIRMESH_OT_tube_same_profile(bpy.types.Operator):
    """Make this part's profile the same as the one before it (the middle as the start, the end as the middle)"""
    bl_idname = 'hairmesh.tube_same_profile'
    bl_label = 'Same Profile'
    bl_options = {'REGISTER', 'UNDO'}

    k: IntProperty(name='Part', min=1, max=2, default=1, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return f'Make this profile the same as the {"start" if properties.k == 1 else "middle"} one'   # (7189)

    @classmethod
    def poll(cls, context):
        if not _tube_poll(cls, context):
            return False
        _r, _P, t = selected_tube(context)
        if t.profs and len(t.profs) == 3 and t.profs[1] == t.profs[0] and t.profs[2] == t.profs[1]:
            cls.poll_message_set('The three profiles are the same already')
            return False
        return True

    def execute(self, context):
        root, P, t = selected_tube(context)
        Pp = TB.ownProfs(t)
        if Pp[self.k] == Pp[self.k - 1]:
            self.report({'INFO'}, 'The same profile already')
            return {'CANCELLED'}
        Pp[self.k][:] = [list(q) for q in Pp[self.k - 1]]; t.dirty = True
        _r, _P, sel = select.current(context)
        return _finish(context, root, P, sel, self.report, None)


class HAIRMESH_OT_copy_modifier(bpy.types.Operator):
    """Copy the profile and all settings of this modifier, to paste onto other curves"""
    bl_idname = 'hairmesh.copy_modifier'
    bl_label = 'Copy Modifier'
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return _tube_poll(cls, context)

    def execute(self, context):
        _root, _P, t = selected_tube(context)
        CLIP[0], msg = MOD.copyModifier(t)
        self.report({'INFO'}, msg)
        select.redraw(context)
        return {'FINISHED'}


class HAIRMESH_OT_paste_modifier(bpy.types.Operator):
    """Replace the profile and settings of the selected curves' modifiers with the copied ones (a curve without one gets one)"""
    bl_idname = 'hairmesh.paste_modifier'
    bl_label = 'Paste Modifier'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _poll(context):
            return False
        if CLIP[0] is None:
            cls.poll_message_set(MOD.NOTHING_COPIED)
            return False
        if not _targets(context):
            cls.poll_message_set(MOD.NO_CURVES)
            return False
        return True

    def execute(self, context):
        root, P, sel = select.current(context)
        CM.rebuild_dirty(P)
        made, msg = MOD.pasteModifier(P, sel.items, CLIP[0])
        if made is None:
            self.report({'INFO'}, msg)
            return {'CANCELLED'}
        sel.items = [t.id for t in made]; sel.anchors = []
        return _finish(context, root, P, sel, self.report, msg)


class HAIRMESH_OT_set_mod_tab(bpy.types.Operator):
    """Show this page of the Modifiers tab"""
    bl_idname = 'hairmesh.set_mod_tab'
    bl_label = 'Modifiers Tab'
    bl_options = {'INTERNAL'}

    tab: EnumProperty(items=MOD_TABS, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return next(t[2] for t in MOD_TABS if t[0] == properties.tab)

    @classmethod
    def poll(cls, context):
        return owner.visible(context)

    def execute(self, context):
        context.scene.hairmesh_profile.mod_tab = self.tab
        select.redraw(context)
        return {'FINISHED'}


# ---------------- the D and the N: rows ----------------

def page_widths(context):
    """the widths of the three columns the two rows of pages share — Profile / Extra / Topology and, under Topology,
    Cap / Fill / Density —: each as wide as the wider of its two texts, so the second row lines up under the first"""
    from .ops_topology import TOPO_TABS
    from .tool import item_need

    def need(tab):
        return item_need(context, ('op', 'hairmesh.set_mod_tab', tab[1], tab[3], {}))
    return [max(need(a), need(b)) for a, b in zip(MOD_TABS, TOPO_TABS)]


def pages_row(context, items):
    """a row of pages: a family of modes, joined (G14), in the columns of page_widths, the rest of the row empty (G12)"""
    from .tool import cols
    return cols(*((w, [it]) for w, it in zip(page_widths(context), items)), (None, []))


def mod_tab_row(context):
    """Profile, Extra, Topology (6929): the pages"""
    t = context.scene.hairmesh_profile.mod_tab
    return [pages_row(context, [('op', 'hairmesh.set_mod_tab', lab, icon, {'tab': k}, {'depress': t == k}) for k, lab, _d, icon, _n in MOD_TABS])]


def profile_tab_rows(context, room, sheet=False):
    """the Profile tab (modifierPanel 6943-6973): the preset, Edit (the profile editor, 4.6b), Save / Delete preset,
    Apply — in a closed block while a modifier's sheet follows (the D within 18 rows); the tip (APPLY_TIP) is the "?" of
    the D's tab column (item 9 of the lot after 4.8: tool.how_to)"""
    from .tool import GAP, ICON_BTN, align_fields, block, field
    n = len(_targets(context))
    # (item 8 of the lot after 4.8, the owner's request: Edit, Save Preset and Delete Preset as icons on the preset's row,
    # Blender's own preset row — the menu, +, − —, their names in the tooltips and in the list menu. Lot 1.0.2, item 2:
    # Save Preset taken for Apply — Save is Blender's new-preset icon, Apply a check mark, and
    # before any modifier is selected a line says where Start, Middle and End come from. Lot 1.2b: the menu's name
    # outside it, the icons joined to it as Blender's preset row, Apply at the width of its text)
    icons = (('op', 'hairmesh.profile_editor', '', 'CURVE_BEZCIRCLE', {'target': 'PANEL'}, {'list_text': 'Edit Profile'}),
             ('op', 'hairmesh.save_profile_preset', SAVE_TEXT, SAVE_ICON, {}, {'list_text': 'Save Preset...'}),
             ('op', 'hairmesh.delete_profile_preset', '', 'REMOVE', {}, {'list_text': 'Delete Preset'}))
    # (the owner's call on the capture: Apply under the three icons of the preset's row and as wide as the three
    # together, ending where they end; wider only when its text asks for more, "Apply to N Curves")
    from . import fit
    from .tool import cols, item_need
    apply_ = ('op', 'hairmesh.apply_modifier', f'Apply to {n} Curves' if n > 1 else 'Apply', APPLY_ICON, {})
    under = len(icons) * ICON_BTN + (len(icons) - 1) * (fit.SPLIT_SPACE + fit.SPLIT_SLACK)
    rows = [[field('Profile', ('prop', 'mod_preset', ''), *((ICON_BTN, [it]) for it in icons))], [GAP],
            [cols((None, []), (max(under, item_need(context, apply_)), [apply_]))]]
    if not sheet:
        rows.append([('label', APPLY_FIRST, 'INFO')])
    return align_fields([block('profile_apply', 'Apply a Profile', APPLY_ICON, rows, False)] if sheet else rows)   # (block(): a row)


def tube_rows(context, P, t, room):
    """the sheet of a Profile modifier (tubeProps 10493): its curve, the three parts (radius, rotation; the profile
    editor is 4.6b), Collapse the tip and its length, Convert, Auto radius, Copy / Paste"""
    from .props import measure
    from .tool import GAP, align_fields, block, buttons, field, group_label, why_item
    c = P.items.get(t.src)
    from .tool import named_on
    rows = [[('label', named_on(t.name, c.name if c else 'a deleted curve'), 'MOD_SCREW')],
            [buttons(context, group_label(P, t), ('op', 'hairmesh.delete_selected', 'Delete Modifier', 'X', {'keep_shape': False}))]]
    for k, name in enumerate(('Start', 'Middle', 'End')):
        inner = [[field('Radius', ('prop', measure(P, f'tube_r{k}'), ''))], [field('Rotation', ('prop', f'tube_rot{k}', ''))]]
        if k > 0:
            inner += [[GAP], [buttons(context, ('op', 'hairmesh.tube_same_profile', f'= {"Start" if k == 1 else "Middle"} Profile', 'LINKED', {'k': k}))]]
        rows.append(block(f'tube_part_{k}', name, 'DOT', inner, k == 0))
    own = t.p.get('step')
    inh = TB.tubeStep(P, TB_inherit(t)) if not own else None
    why = (f"Its own: only this modifier's lock (a group of its own on Convert). 0 inherits {_inherit_from(P, t)}." if own
           else f'Inherits {_inherit_from(P, t)}: {inh:.4g}. The preview shows the loops Convert will make.')
    rows += [[GAP], [field('Loop Step', ('prop', measure(P, 'tube_step'), ''), (1.0, [why_item(why)]))],   # (lot 4.7b item 4, ours: the loops it will have; the preview shows them)
             [('prop', 'tube_collapse', 'Collapse the Tip')]]
    if t.p.get('collapse'):
        rows.append([field('Tip Length', ('prop', 'tube_tip_len', ''))])
    # (item 8 of the lot: the rest as icons, their names in the tooltips and the list menu; lot 1.2b: Convert at the
    # width of its text, the icons apart from it, Copy and Paste a family)
    rows += [[GAP], [buttons(context, ('op', 'hairmesh.convert_tube', 'Convert to Curves & Walls', 'OUTLINER_OB_SURFACE', {}),
                             ('op', 'hairmesh.profile_editor', '', 'CURVE_BEZCIRCLE', {'target': 'TUBE'}, {'list_text': 'Edit Profiles'}),
                             ('op', 'hairmesh.tube_auto_radius', '', 'FULLSCREEN_ENTER', {}, {'list_text': 'Auto Radius'}),
                             [('op', 'hairmesh.copy_modifier', '', 'COPYDOWN', {}, {'list_text': 'Copy Modifier'}),
                              ('op', 'hairmesh.paste_modifier', '', 'PASTEDOWN', {}, {'list_text': 'Paste Modifier'})])]]
    return align_fields(rows)


class _NoStep:
    """the modifier seen without its own step (what it inherits)"""
    def __init__(self, t):
        self.src = t.src; self.p = {k: v for k, v in t.p.items() if k != 'step'}


def TB_inherit(t):
    return _NoStep(t)


def _inherit_from(P, t):
    c = P.items.get(t.src)
    g = P.parentOf(c) if c is not None else None
    return f"the loop step of {g.name}" if g is not None else "the project's loop step (its new group's)"


def tube_pie_items(context, P, t):
    """the Shift+RMB pie with a Profile modifier the last selected item: its curve (derived: a selection no click on
    the surface gives), Convert, Delete, the radii in the box, Edit Profiles, Copy / Paste, the HairMesh Menu"""
    from .tool import MENU_OP, pie_box
    return [('op', 'hairmesh.select', 'Its Curve', 'CURVE_DATA', {'curve': t.src}), ('op', 'hairmesh.convert_tube', 'Convert to Curves & Walls', 'OUTLINER_OB_SURFACE', {}),
            ('op', 'hairmesh.delete_selected', 'Delete Modifier', 'X', {'keep_shape': False}),
            pie_box(('prop', measure(P, 'tube_r0'), 'Start Radius'), ('prop', measure(P, 'tube_r1'), 'Middle Radius'), ('prop', measure(P, 'tube_r2'), 'End Radius')),
            ('op', 'hairmesh.profile_editor', 'Edit Profiles', 'CURVE_BEZCIRCLE', {'target': 'TUBE'}), MENU_OP,
            ('op', 'hairmesh.copy_modifier', 'Copy Modifier', 'COPYDOWN', {}), ('op', 'hairmesh.paste_modifier', 'Paste Modifier', 'PASTEDOWN', {})]


CLASSES = (HairMeshProfileProps, HairMeshTubeProps, HAIRMESH_OT_apply_modifier, HAIRMESH_OT_save_profile_preset, HAIRMESH_OT_delete_profile_preset,
           HAIRMESH_OT_convert_tube, HAIRMESH_OT_tube_auto_radius, HAIRMESH_OT_tube_same_profile, HAIRMESH_OT_copy_modifier, HAIRMESH_OT_paste_modifier,
           HAIRMESH_OT_set_mod_tab)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.hairmesh_profile = PointerProperty(type=HairMeshProfileProps)
    bpy.types.WindowManager.hairmesh_tube = PointerProperty(type=HairMeshTubeProps)


def unregister():
    del bpy.types.WindowManager.hairmesh_tube
    del bpy.types.Scene.hairmesh_profile
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
