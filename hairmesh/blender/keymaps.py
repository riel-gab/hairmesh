# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The add-on's shortcuts: what they are by default, where they are registered, and how the user's copies are found.

Three keymaps, all in the add-on keyconfig (wm.keyconfigs.addon), so Blender mirrors them into the user keyconfig
(wm.keyconfigs.user), where every edit the user makes lives and is saved with the preferences:
  - '3D View' (always on): Shift+Alt+H toggles the tool.
  - '3D View Tool: Object, HairMesh' (only while the tool is active, with priority over the 3D View keymap): the
    tool's own keys, from the D3 table of docs/fase-3-plano-ciclo-nativo.md (O since the UI review of 15/10/2026).
    bpy.utils.register_tool creates this keymap itself, from the WorkSpaceTool's bl_keymap, in the add-on keyconfig.
  - '3D View Generic' (the viewport and the sidebar; the operator passes the key through while the tool is off):
    F2 renames the selected item (16/10/2026, 1d: over the N panel too).
The tables below are the DEFAULTS; the preferences show the user's copies (editable) and can restore them."""
import bpy

from .props import ready_subtools as _ready_subtools

TOOL_ID = 'hairmesh.tool'
TOOL_LABEL = 'HairMesh'
GLOBAL_KM = '3D View'
TOOL_KM = f'3D View Tool: Object, {TOOL_LABEL}'                     # the name register_tool gives it (ToolSelectPanelHelper._km_action_simple)

# (operator, event, operator properties). Always on, in the 3D View keymap.
GLOBAL_ITEMS = (
    ('hairmesh.toggle_tool', {'type': 'H', 'value': 'PRESS', 'shift': True, 'alt': True}, {}),
)
# Blender runs these items in the REVERSE of this order: the keymap in use takes an add-on's items to its head one at a
# time (wm_keymap_addon_add; lot 4.9b, conferred with keyconfigs.user and with real events). So no item may count on the
# order: two items on one key tell each other apart by themselves (the sub-tool, a poll, what is under the cursor).
# Only while the tool is active (the WorkSpaceTool's bl_keymap). Keys of the original where they do not collide with
# the untouchable list (Tab, Ctrl+Z, A, N/T, Shift+A, Ctrl+S, F3, numpad, MMB, wheel). G/R/S are contextual since
# 24/10/2026 (the owner's decision): HairMesh's with the tool in Select and anything of the HairMesh selected (3.8d:
# anchors, curves, walls, groups), Blender's with nothing of the HairMesh selected; Alt+A since 4.3, X / Delete since 4.4,
# H / Shift+H with something of the HairMesh selected, Alt+H with something of the HairMesh hidden.
TOOL_ITEMS = (
    # the sub-tool keys are one operator each (hairmesh.subtool_<name>), so Preferences > Keymap names them apart.
    # 1 / 2 / 3 (15/10/2026) cover Blender's "show / hide collection" while the tool is active; pressed again on the
    # sub-tool they already show they do nothing (pass_through False), so the collections never toggle. W is free: the
    # Draw key from 15/10/2026 (D before), it fell through to Blender's W (the select-tool cycle) when pressed in Draw.
    ('hairmesh.subtool_select', {'type': 'ONE', 'value': 'PRESS'}, {'pass_through': False}),   # 1: select
    ('hairmesh.subtool_draw', {'type': 'TWO', 'value': 'PRESS'}, {'pass_through': False}),     # 2: draw a curve (KeyC in the original; C is the Circle Select below)
    ('hairmesh.subtool_scissors', {'type': 'THREE', 'value': 'PRESS'}, {'pass_through': False}),   # 3: scissors
    ('hairmesh.select_circle', {'type': 'C', 'value': 'PRESS'}, {}),                           # C: Circle Select of anchors and curves (ops_select, 3.3)
    ('hairmesh.subtool_scissors', {'type': 'K', 'value': 'PRESS'}, {'pass_through': False}),   # K: scissors too (the original's key)
    ('hairmesh.guide_pencil_cancel', {'type': 'ESC', 'value': 'PRESS'}, {}),                   # Esc with a pencil loop being drawn (4.2): drop it (passes through otherwise)
    ('hairmesh.guide_pencil_finish', {'type': 'RET', 'value': 'PRESS'}, {}),                   # Enter with a pencil loop being drawn (4.2): close it into a disc (passes through otherwise)
    ('hairmesh.edges_deselect', {'type': 'ESC', 'value': 'PRESS'}, {}),                        # Esc in Edges with edges selected (4.9, 8055): deselect them (its poll fails otherwise: the key goes on)
    ('hairmesh.subtool_select', {'type': 'ESC', 'value': 'PRESS'}, {'pass_through': True}),    # Esc: back to select (passes through when already there: Blender's Esc)
    ('hairmesh.draw_curve', {'type': 'LEFTMOUSE', 'value': 'PRESS'}, {}),                      # LMB in the Draw sub-tool: place an anchor (passes through otherwise)
    ('hairmesh.scissors', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),           # LMB in Scissors > Curve: drag the cut line (a click picks a curve; passes through otherwise)
    ('hairmesh.wall_cut', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),           # LMB in Scissors > NURBS Surface: pick the wall and cut it (passes through otherwise)
    ('hairmesh.guide_tool', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),         # LMB in Guides > Line / NURBS Disc (4.2): the line, the disc's click; a click on a guide picks it (passes through otherwise)
    ('hairmesh.guide_pencil', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),       # LMB in Guides > Pencil (4.2): draw the loop, a stroke per press (passes through otherwise)
    ('hairmesh.edge_click', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),         # LMB in Edges (4.9): an edge, twice its loop, Shift adds, Ctrl takes off, Ctrl + drag a box, Ctrl + Shift the path (passes through otherwise)
    ('hairmesh.edge_hover', {'type': 'MOUSEMOVE', 'value': 'ANY', 'any': True}, {}),           # the edge under the cursor in Edges (4.9; its poll fails in the other sub-tools, and it always passes the move on)
    ('hairmesh.select_curve_anchors', {'type': 'LEFTMOUSE', 'value': 'DOUBLE_CLICK', 'any': True}, {}),   # double LMB on a curve (3.8d): its anchors, again the curve; Shift adds (passes through elsewhere: the press goes to hairmesh.select)
    ('hairmesh.select', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),             # LMB in Select (and on an anchor in Crown, 4.3): pick / drag anchors (Shift adds, Ctrl toggles), pick curves, Ctrl + drag paints curves, drag in the empty boxes anchors (Alt passes through: Blender's own)
    ('hairmesh.crown_tool', {'type': 'LEFTMOUSE', 'value': 'PRESS', 'any': True}, {}),         # LMB in Crown (4.3; an anchor under the cursor passes on to Select's): a click on the head puts a crown's whorl there (passes through otherwise)
    ('hairmesh.insert_anchor', {'type': 'RIGHTMOUSE', 'value': 'PRESS'}, {}),                 # RMB on a curve: insert an anchor and drag it (passes through elsewhere: Blender's context menu)
    ('hairmesh.smooth_drag', {'type': 'B', 'value': 'PRESS'}, {}),                             # B held + LMB drag: smoothness of the selected anchors (B is taken from Blender's box select while the tool is active)
    ('hairmesh.delete_selected', {'type': 'BACK_SPACE', 'value': 'PRESS'}, {}),               # Backspace: delete the selected anchors, else the selected items
    ('hairmesh.delete_ask', {'type': 'X', 'value': 'PRESS'}, {}),                              # X (4.4, the owner's request): the same after a short question "Delete N items?" — contextual: with nothing of the HairMesh selected, Blender's X
    ('hairmesh.delete_now', {'type': 'DEL', 'value': 'PRESS'}, {}),                            # Delete (4.4): the same at once — contextual: with nothing of the HairMesh selected, Blender's Delete
    ('hairmesh.hide_selected', {'type': 'H', 'value': 'PRESS'}, {}),                           # H, Shift+H, Alt+H (the owner's request): the eye of the Outliner on the selection, on the rest, off all — contextual
    ('hairmesh.hide_unselected', {'type': 'H', 'value': 'PRESS', 'shift': True}, {}),
    ('hairmesh.reveal_hidden', {'type': 'H', 'value': 'PRESS', 'alt': True}, {}),
    ('hairmesh.deselect_all', {'type': 'A', 'value': 'PRESS', 'alt': True}, {}),             # Alt+A (lot 4.3, the owner's request): clear the HairMesh selection — contextual: with nothing of it selected, Blender's
    # 3.8b: the gizmo modes (the original's E / R / T; R and T are Blender's rotate and toolbar: no key until the owner
    # picks one — the items are here, so Preferences > Keymap lists them, ready to be given a key) and Shift+D
    # 3.8c (the owner's decision of 24/10/2026): G / R / S move, turn and scale the selection — contextual: the
    # operators fail their poll without the tool in Select with something of the HairMesh selected (3.8d), and the keys
    # reach Blender's own
    ('hairmesh.grab_anchors', {'type': 'G', 'value': 'PRESS'}, {}),                          # G: HairMesh Move Selection (else Blender's G)
    ('hairmesh.rotate_anchors', {'type': 'R', 'value': 'PRESS'}, {}),                        # R: HairMesh Rotate Selection (else Blender's R)
    ('hairmesh.scale_anchors', {'type': 'S', 'value': 'PRESS'}, {}),                         # S: HairMesh Scale Selection (else Blender's S)
    ('hairmesh.gizmo_move', {'type': 'E', 'value': 'PRESS'}, {}),                             # E: the Move gizmo on the selection (again: off)
    ('hairmesh.gizmo_rotate', {'type': 'NONE', 'value': 'PRESS'}, {}),                        # (no key yet: R is Blender's rotate)
    ('hairmesh.gizmo_scale', {'type': 'NONE', 'value': 'PRESS'}, {}),                         # (no key yet: T is Blender's toolbar)
    ('hairmesh.duplicate', {'type': 'D', 'value': 'PRESS', 'shift': True}, {}),               # Shift+D: duplicate the selection (the original's D; Blender's Shift+D while the tool is off)
    # 4.0 (D4.3): the original's F and P / Shift+P, contextual like G / R / S: their poll fails without something of the
    # HairMesh selected that serves (P: the last selected a group; Shift+P: an item inside a group), and the key goes on
    # (F: the Fluent pie of the 3D View Generic keymap; P: blocksurfaces's Bs_parent_Col_Objects; Shift+P: RetopoFlow)
    ('hairmesh.frame_selection', {'type': 'F', 'value': 'PRESS'}, {}),                        # F: frame the HairMesh selection (frameAll, 4390)
    ('hairmesh.parent_to_last', {'type': 'P', 'value': 'PRESS'}, {}),                         # P: into the group selected last (parentSelection, 8179)
    ('hairmesh.unparent', {'type': 'P', 'value': 'PRESS', 'shift': True}, {}),                # Shift+P: out of the groups (unparentSelection, 8187)
    ('wm.call_menu_pie', {'type': 'RIGHTMOUSE', 'value': 'PRESS', 'shift': True}, {'name': 'HAIRMESH_MT_pie'}),   # Shift+RMB: the context pie ("HairMesh Pie")
    ('wm.call_panel', {'type': 'D', 'value': 'PRESS'}, {'name': 'HAIRMESH_PT_popup', 'keep_open': True}),   # D: the HairMesh Menu pop-up (the key HardOps / BoxCutter open their main menu with; "HairMesh Menu" in the keymap list)
    ('wm.call_menu_pie', {'type': 'Q', 'value': 'PRESS'}, {'name': 'HAIRMESH_MT_subtools'}),  # Q: the sub-tool pie (over HardOps's Q, which lives in the 3D View keymap: the tool keymap comes first)
    ('wm.call_panel', {'type': 'O', 'value': 'PRESS'}, {'name': 'HAIRMESH_PT_outliner', 'keep_open': True}),   # O: the HairMesh Outliner pop-up (U1, 15/10/2026; over Blender's proportional-editing toggle while the tool is active; "HairMesh Outliner" in the keymap list)
)
# Only while the tool is active, in every region of the 3D View (the viewport AND the sidebar with the N panel): the
# "3D View Generic" keymap, which the sidebar region consults too, where the tool keymap is the viewport's alone. The
# operator passes the key through (tool_only) when the tool is off, so Blender's own F2 (rename the active object)
# still works then. 1d of 16/10/2026: F2 over the N panel renames the selected item. (A pop-up swallows every key
# the mouse is over, so no keymap can act inside the O pop-up: there, a double click on the name or the name
# field of the bar.)
# 4.0 (D4.2): the keys 4 / 5 of Guides / Crown, each only once its step makes it work (props.READY_STEPS), and 6, the
# D on its Modifiers tab (the owner's decision: Modifiers is a tab, not a sub-tool): like 1 / 2 / 3 they cover Blender's
# "show / hide collection" while the tool is active
TOOL_ITEMS = TOOL_ITEMS + tuple((f'hairmesh.subtool_{t[0].lower()}', {'type': t[6], 'value': 'PRESS'}, {'pass_through': False})
                                for t in _ready_subtools() if t[5] != '3') + (('hairmesh.open_modifiers', {'type': 'SIX', 'value': 'PRESS'}, {}),)
# A double click is a press to the sub-tools that take every press (4.9b). Blender tries the DOUBLE_CLICK over all the keymaps
# of the region, and the same event as a PRESS only when nobody took it, so a PRESS item here loses that second press to a
# DOUBLE_CLICK item of another add-on in "Object Mode" (DECALmachine's Select Decals took the loop of the Edges). The tool
# keymap comes before "Object Mode" (ed_default_handlers before view3d_main_region_init), so these win; each passes through
# in the other sub-tools. Select has none: what select_curve_anchors leaves goes on to the add-on, as ever.
DOUBLE_PRESS = ('hairmesh.draw_curve', 'hairmesh.scissors', 'hairmesh.wall_cut', 'hairmesh.guide_tool', 'hairmesh.guide_pencil', 'hairmesh.edge_click', 'hairmesh.crown_tool')
TOOL_ITEMS = TOOL_ITEMS + tuple((i, {**e, 'value': 'DOUBLE_CLICK'}, p) for i, e, p in TOOL_ITEMS if i in DOUBLE_PRESS and e['value'] == 'PRESS')
GENERIC_KM = '3D View Generic'
GENERIC_ITEMS = (
    ('hairmesh.rename_item', {'type': 'F2', 'value': 'PRESS'}, {'tool_only': True}),         # F2: rename the selected item (the original's F2 / double click in its outliner)
)


def tool_keymap():
    """bl_keymap of the WorkSpaceTool, in the shape register_tool expects."""
    return tuple((idname, dict(event), {'properties': list(props.items())}) for idname, event, props in TOOL_ITEMS)


_global = []                                                        # (keymap, item) registered in the add-on keyconfig


def register():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    for km_name, items in ((GLOBAL_KM, GLOBAL_ITEMS), (GENERIC_KM, GENERIC_ITEMS)):
        km = kc.keymaps.new(name=km_name, space_type='VIEW_3D')
        for idname, event, props in items:
            kmi = km.keymap_items.new(idname, **event)
            for k, v in props.items():
                setattr(kmi.properties, k, v)
            _global.append((km, kmi))
    # the user keyconfig takes the new keymaps in now, and every item that pointed at an operator type that was
    # just unregistered and registered again (reload, disable / enable) is relinked: see _find
    wm.keyconfigs.update()


def unregister():
    for km, kmi in _global:
        try:
            km.keymap_items.remove(kmi)
        except ReferenceError:
            pass
    _global.clear()


def _matches(kmi, idname, props):
    if kmi.idname != idname:
        return False
    for k, v in props.items():
        try:
            if getattr(kmi.properties, k) != v:
                return False
        except AttributeError:
            return False
    return True


def _find_all(km, idname, props):
    """The items calling `idname` with these operator properties, whatever keys they have now, in keymap order.
    Position in the keymap is not usable to tell items apart: a restore_to_default reorders them. Reading
    kmi.properties is safe only because register() ends with keyconfigs.update(): right after the operators are
    (re)registered, the user keyconfig's items still point at the old operator types, and reading their properties
    before that update crashes Blender (seen live with a hot reload, 12/10/2026)."""
    return [kmi for kmi in km.keymap_items if _matches(kmi, idname, props)]


def _event_matches(kmi, event):
    """the item still has the default key (type and modifiers)"""
    if kmi.type != event['type'] or kmi.value != event.get('value', 'PRESS'):
        return False
    if event.get('any'):
        return bool(kmi.any)
    return all(bool(getattr(kmi, m)) == bool(event.get(m)) for m in ('ctrl', 'shift', 'alt', 'oskey'))


def user_items(kc=None):
    """The user's copies of every shortcut, as (keymap, item or None, default) triples: the keymap name, operator,
    event and properties of the default, and the item as it is now in the user keyconfig (None when the user
    removed it)."""
    wm = bpy.context.window_manager
    kc = kc or wm.keyconfigs.user
    out = []
    for km_name, items in ((GLOBAL_KM, GLOBAL_ITEMS), (TOOL_KM, TOOL_ITEMS), (GENERIC_KM, GENERIC_ITEMS)):
        km = kc.keymaps.get(km_name)
        # defaults with the same operator and properties (3 and K both call hairmesh.subtool_scissors) share the
        # candidates: the ones still on their default key are paired first, the rest in keymap order
        groups = {}
        for i, (idname, event, props) in enumerate(items):
            groups.setdefault((idname, tuple(sorted(props.items()))), []).append(i)
        found = [None] * len(items)
        for (idname, _key), idxs in groups.items():
            cands = _find_all(km, idname, items[idxs[0]][2]) if km is not None else []
            for i in idxs:
                kmi = next((k for k in cands if _event_matches(k, items[i][1])), None)
                if kmi is not None:
                    found[i] = kmi; cands.remove(kmi)
            for i in idxs:
                if found[i] is None and cands:
                    found[i] = cands.pop(0)
        for i, (idname, event, props) in enumerate(items):
            out.append((km, found[i], (km_name, idname, event, props)))
    return out


def set_active(which, on):
    """Enables / disables the user's copies: which = 'global' | 'tool' (the tool keymap and the sidebar's F2)."""
    names = {GLOBAL_KM} if which == 'global' else {TOOL_KM, GENERIC_KM}
    for km, kmi, (name, *_rest) in user_items():
        if kmi is not None and name in names:
            kmi.active = bool(on)


def restore_defaults():
    """Every shortcut back to the table above: the tool keymap holds only ours, so it is restored whole (that also
    brings back removed items); in the shared 3D View keymap only our items are touched, item by item. Returns the
    defaults that could not be restored (removed from the 3D View keymap by the user)."""
    wm = bpy.context.window_manager
    wm.keyconfigs.update()                                          # pending edits are diffed first (is_user_modified is computed there)
    kc = wm.keyconfigs.user
    km = kc.keymaps.get(TOOL_KM)
    if km is not None:
        km.restore_to_default()
    missing = []
    for shared in (GLOBAL_KM, GENERIC_KM):                          # the shared keymaps: only our items are touched, item by item
        km = kc.keymaps.get(shared)
        for _km, kmi, (name, idname, event, props) in user_items(kc):
            if name != shared:
                continue
            if kmi is None:
                missing.append((shared, idname, event, props))
            elif kmi.is_user_modified or not kmi.active:
                km.restore_item_to_default(kmi)
                kmi.active = True
    # an item the user removed stays removed in the user keyconfig (its diff is "deleted"): it comes back as a
    # user-defined item with the default key, found again by operator + properties like the others
    for shared, idname, event, props in missing:
        km = kc.keymaps.get(shared)
        if km is None:
            continue
        kmi = km.keymap_items.new(idname, **event)
        for k, v in props.items():
            setattr(kmi.properties, k, v)
    wm.keyconfigs.update()                                          # diffs recomputed: restored items read as unmodified right away
    return [d for km_, kmi, d in user_items(kc) if kmi is None and d[0] in (GLOBAL_KM, GENERIC_KM)]


def event_label(event):
    """'Shift Alt H' for the preferences and the panel hints."""
    mods = ['Any'] if event.get('any') else [m.title() for m in ('ctrl', 'shift', 'alt', 'oskey') if event.get(m)]
    key = event['type'].replace('_', ' ').title()
    key = {'Rightmouse': 'RMB', 'Leftmouse': 'LMB', 'Middlemouse': 'MMB', 'Esc': 'Esc', 'Back Space': 'Backspace', 'Del': 'Delete', 'Ret': 'Enter'}.get(key, key)
    return ' '.join(mods + [key] + (['Double Click'] if event.get('value') == 'DOUBLE_CLICK' else []))
