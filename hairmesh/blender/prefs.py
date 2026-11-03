# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Add-on preferences: the credit to the original's author, the shortcuts, editable in place (the user's copies in
wm.keyconfigs.user, drawn with rna_keymap_ui like HardOps does), two switches to turn them off, and a button back to
the defaults."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, StringProperty
from bpy.types import AddonPreferences

from . import icons, keymaps

PACKAGE = __package__.rsplit('.', 1)[0]                             # 'hairmesh' in the repo, 'bl_ext.<repo>.hairmesh' installed
_syncing = False
# the original this add-on ports (also the tagline of blender_manifest.toml)
CREDIT = 'Port of Stylized HairMesh Creator by catarackta'
CREDIT_URL = 'https://catarackta.netlify.app/'


def get(context=None):
    """The preferences, or None when the add-on is not enabled as an add-on (tests)."""
    a = (context or bpy.context).preferences.addons.get(PACKAGE)
    return a.preferences if a is not None else None


def _on_global(self, context):
    if not _syncing:
        keymaps.set_active('global', self.global_shortcut)


def _on_tool(self, context):
    if not _syncing:
        keymaps.set_active('tool', self.tool_shortcuts)


class HAIRMESH_OT_keymap_restore(bpy.types.Operator):
    """Restore every HairMesh shortcut to its default key and turn it back on"""
    bl_idname = 'hairmesh.keymap_restore'
    bl_label = 'Restore Default Shortcuts'
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        global _syncing
        missing = keymaps.restore_defaults()
        prefs = get(context)
        if prefs is not None:
            _syncing = True
            try:
                prefs.global_shortcut = True
                prefs.tool_shortcuts = True
            finally:
                _syncing = False
        if missing:
            self.report({'WARNING'}, 'Could not bring back: ' + ', '.join(f'{idname} ({keymaps.event_label(ev)})' for idname, ev, _p in missing)
                        + '. Preferences > Keymap > 3D View > Restore puts the whole keymap back')
        else:
            self.report({'INFO'}, 'HairMesh shortcuts restored')
        return {'FINISHED'}


def _redraw_colors(self, context):
    from . import draw
    draw.invalidate()
    for w in context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()


class HAIRMESH_OT_selection_colors_reset(bpy.types.Operator):
    """Restore the selection colors to their defaults (a lime green curve, red anchors, a white ring on the last anchor)"""
    bl_idname = 'hairmesh.selection_colors_reset'
    bl_label = 'Reset Selection Colors'
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        p = get(context)
        if p is not None:
            for k in SEL_COLOR_PROPS:
                p.property_unset(k)
        _redraw_colors(None, context)
        return {'FINISHED'}


TABS = (('SELECTION', 'Selection', 'Show the properties and actions of the selection, and the settings of the sub-tool', 'PROPERTIES', 0),
        ('MODIFIERS', 'Modifiers', 'Show the modifiers: Profile, Extra and Topology, with their brushes', 'MODIFIER', 4),
        ('CONVERSION', 'Conversion', 'Show the ticked groups, the loop step, the sliders and Convert', 'MOD_REMESH', 1),
        ('OPTIONS', 'Viewport', 'Show what the viewport shows and how it reacts: the live preview, Drag on Head, the x-ray, the guides', 'VIEW3D', 3),
        ('PROJECT', 'Project', 'Show the head and the save, import and new actions', 'FILE', 2))   # Project last (1a of the manual test of the lot ux5; the numbers kept: saved preferences stay valid)
_tabs = {}


def tab_items(_self=None, _context=None):
    """the items of menu_tab: a function, because an image has its icon number only after icons.register. They stay
    in _tabs (Blender keeps the strings of a dynamic enum by pointer)"""
    v = icons.get('selection')
    if v not in _tabs:
        _tabs[v] = [(k, n, d, v if k == 'SELECTION' and v else i, nr) for k, n, d, i, nr in TABS]
    return _tabs[v]


class HairMeshPreferences(AddonPreferences):
    bl_idname = PACKAGE

    global_shortcut: BoolProperty(name='Global Shortcut (Shift+Alt+H)', description='Toggle the HairMesh tool from anywhere in the 3D View', default=True, update=_on_global)
    tool_shortcuts: BoolProperty(name='Tool Shortcuts', description='Turn on the keys that work only while the HairMesh tool is active (D, O, 1 / 2 / 3, K, C, Q, Shift+RMB...)', default=True, update=_on_tool)
    # the tab the HairMesh Menu (D) shows, remembered between openings and sessions (U5, 15/10/2026: as BoxCutter's
    # Box Helper keeps its shape type in its preferences); the items are tool.TABS
    menu_tab: EnumProperty(name='HairMesh Menu Tab', description='Set the tab the HairMesh Menu opens on (the last one used)', items=tab_items, default=0)
    # U2 (15/10/2026): the Curves & anchors mode of the groups the add-on creates (the original: Auto, the curves of a
    # lock shown only while the group is worked on; Show keeps every curve on screen)
    new_group_guides: EnumProperty(name='Curves of New Groups', description="Set the Curves & Anchors mode a new group starts with: Auto (a lock's curves show only while something inside the group is selected) or Show (always shown)",
                                   items=(('auto', 'Auto', 'Show the curves only while something inside the group is selected'), ('show', 'Show', 'Show the curves all the time')), default='auto')
    # U1 part 2 (16/10/2026): the original opens the groups above the active item on F and on a drop only; this keeps
    # them open while the item is selected (an addition of the port)
    outliner_open_on_select: BoolProperty(name='Outliner: Open Groups on Select', description='Keep the groups above the selected item open in the HairMesh Outliner while it is selected (the Reveal button does it once)', default=False)
    # part 2 of the lot of 16/10/2026: the blocks of the sheets (group / curve / wall / anchors) the user opened or
    # closed, remembered between openings of the HairMesh Menu and between sessions (tool.block_open)
    # 3.8c (the owner's request): the selection colors (draw.SEL_DEFAULT; 3.8e: the anchors red)
    sel_curve_color: FloatVectorProperty(name='Selected Curve', description='Set the color of a selected curve (drawn thicker, over the others)', subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, default=(0xb4 / 255.0, 1.0, 0x1e / 255.0), update=_redraw_colors)
    sel_anchor_color: FloatVectorProperty(name='Selected Anchors', description='Set the color of every selected anchor (the size of the others)', subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, default=(1.0, 0.0, 0.0), update=_redraw_colors)
    last_anchor_mark: FloatVectorProperty(name='Last Anchor Ring', description='Set the color of the ring around the last selected anchor (the gizmo\'s pivot)', subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, default=(1.0, 1.0, 1.0), update=_redraw_colors)
    sheet_blocks: StringProperty(name='Sheet Blocks', description='Remember the blocks of the Selection sheets the user opened or closed (JSON {block key: open})', default='{}', options={'HIDDEN'})
    # 4.6 (D4.1's table: the user's presets in the preferences, not in localStorage): the Profile tab's own presets
    profile_presets: StringProperty(name='Profile Presets', description="Keep the user's own profile presets, from Save Preset in the Profile tab (JSON [{name, pts}])", default='[]', options={'HIDDEN'})

    def draw(self, context):
        draw_preferences(self, self.layout, context)


_presets_fallback = ['[]']                                         # (the add-on registered by hand, as in the tests: no preferences)


def user_profiles(context=None):
    """port of userProfiles (6365): the user's own profile presets, [{'name', 'pts'}] (only well-formed ones)"""
    import json
    p = get(context)
    try:
        lst = json.loads((p.profile_presets if p is not None else _presets_fallback[0]) or '[]')
    except ValueError:
        return []
    return [q for q in lst if isinstance(q, dict) and q.get('name') and isinstance(q.get('pts'), list)] if isinstance(lst, list) else []


def save_user_profiles(lst, context=None):
    """port of saveUserProfiles (6366): into the preferences (saved with them)"""
    import json
    s = json.dumps(lst, separators=(',', ':'))
    p = get(context)
    if p is not None:
        p.profile_presets = s
    else:
        _presets_fallback[0] = s


def open_on_select(context=None):
    """the "open groups on select" preference (off when the add-on is registered by hand, as in the tests)"""
    p = get(context)
    return bool(p.outliner_open_on_select) if p is not None else False


SEL_COLOR_PROPS = ('sel_curve_color', 'sel_anchor_color', 'last_anchor_mark')


def draw_preferences(p, layout, context):
    """the add-on's page; the three selection colors on one line"""
    draw_credit(layout)
    col = layout.column()
    col.prop(p, 'global_shortcut')
    col.prop(p, 'tool_shortcuts')
    row = col.row()
    row.label(text='Curves of New Groups:')
    row.row(align=True).prop(p, 'new_group_guides', expand=True)
    col.prop(p, 'outliner_open_on_select')
    box = layout.box()
    row = box.row()
    row.label(text='Selection Colors', icon='RESTRICT_SELECT_OFF')
    row.operator(HAIRMESH_OT_selection_colors_reset.bl_idname, text='Reset to Defaults', icon='LOOP_BACK')
    row = box.row()
    for k in SEL_COLOR_PROPS:
        row.prop(p, k)
    draw_shortcuts(layout, context)


def draw_credit(layout):
    row = layout.box().row()
    row.label(text=CREDIT, icon='INFO')
    row.operator('wm.url_open', text='catarackta.netlify.app', icon='URL').url = CREDIT_URL


def draw_shortcuts(layout, context):
    """The editable list of the add-on's shortcuts (the user's copies), as HardOps draws its own: one
    rna_keymap_ui.draw_kmi row per item, plus the Restore Defaults button."""
    import rna_keymap_ui
    box = layout.box()
    row = box.row()
    row.label(text='Shortcuts', icon='KEYINGSET')
    row.operator(HAIRMESH_OT_keymap_restore.bl_idname, text='Restore Defaults', icon='LOOP_BACK')
    box.label(text='Edit them here or in Preferences > Keymap; changes are saved with the preferences.')
    box.label(text='The tool keys apply only while the HairMesh tool is active.')
    kc = context.window_manager.keyconfigs.user
    last_km = None
    for km, kmi, (km_name, idname, event, props) in keymaps.user_items(kc):
        if km_name != last_km:
            box.separator(factor=0.5)
            heading = {keymaps.GLOBAL_KM: 'Always', keymaps.TOOL_KM: 'While the Tool Is Active', keymaps.GENERIC_KM: 'While the Tool Is Active, Over the Sidebar Too'}.get(km_name, km_name)
            box.label(text=heading, icon='TOOL_SETTINGS')
            last_km = km_name
        if km is None or kmi is None:
            row = box.row()
            row.alert = True
            row.label(text=f'{idname} ({keymaps.event_label(event)}): removed; Restore Defaults brings it back', icon='ERROR')
            continue
        box.context_pointer_set('keymap', km)
        rna_keymap_ui.draw_kmi([], kc, km, kmi, box, 0)


_CLASSES = (HAIRMESH_OT_keymap_restore, HAIRMESH_OT_selection_colors_reset, HairMeshPreferences)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
