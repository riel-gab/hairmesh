# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Lot 4.4 ∥ step 4.6a: the Profile tab's data — the presets (BUILTIN_PROFILES, 6358; the user's own, which the
original keeps in localStorage and the port in the add-on's preferences: passed in), the panel's profile (modProfile,
6371), and Copy / Paste modifier (copyModifier 6736, pasteModifier 6737). Apply is tube.applyModifier (4.5)."""
from .js import index_of, truthy
from .tube import builtinProfile, modTargets

COPIED = 'Modifier copied — select a curve and press Paste modifier'          # (6736)
NOTHING_COPIED = 'Copy a modifier first'                                       # (6738)
NO_CURVES = 'Select the curve(s) to paste the modifier onto'                   # (6740)


def profileByName(name, user=()):
    """port of profileByName (6367): a built-in preset, else one of the user's ({'name', 'pts'}), as a fresh list of
    points; None when there is none of that name"""
    b = builtinProfile(name)
    if b is not None:
        return [list(q) for q in b]
    u = next((p for p in user if p.get('name') == name), None)
    return [list(q) for q in u['pts']] if u is not None else None


def modProfile(preset, edited=None, user=()):
    """port of modProfile (6371): the panel's profile — the one edited in the panel, else the preset's, else Quad"""
    if edited:
        return edited
    return profileByName(preset, user) or profileByName('Quad', user)


def copyModifier(t):
    """port of copyModifier (6736): the clipboard (the profile, the three profiles if any, every setting). Returns
    (the clipboard, the toast)"""
    clip = {'profile': [list(q) for q in t.profile], 'profs': [[list(q) for q in Pp] for Pp in t.profs] if truthy(t.profs) else None, 'p': {**t.p}}
    return clip, COPIED


def pasteModifier(P, sel_ids, clip):
    """port of pasteModifier (6737): the clipboard's profile(s) and settings onto the selected curves (and the curves of
    selected modifiers) — a curve without a modifier gets one, named and placed as Apply places it. Returns (the
    modifiers, the toast); made None when nothing was pasted"""
    from .edit import addItem
    if not clip:
        return None, NOTHING_COPIED
    cs = [c for c in modTargets(P, sel_ids) if len(c.samples) >= 2]
    if not cs:
        return None, NO_CURVES
    made = []
    for c in cs:
        t = next((x for x in P.tubes() if x.src == c.id), None)
        if t is None:
            t = addItem(P, 'tube', c.parent, {'src': c.id, 'profile': [], 'p': {}, 'dirty': True})
            t.name = f'Modifier {P.counters["tube"]} ({c.name})'
            arr = P.childrenOf(t.parent)
            arr.remove(t.id); arr.insert(index_of(arr, c.id) + 1, t.id)
        t.profile = [list(q) for q in clip['profile']]; t.p = {**clip['p']}; t.dirty = True; made.append(t)
        t.profs = [[list(q) for q in Pp] for Pp in clip['profs']] if truthy(clip['profs']) else None
        if truthy(t.profs):
            t.profile = t.profs[0] if t.profs else None   # ([] is true in JS: profs[0] undefined)
    P.gen += 1
    return made, f'Modifier pasted onto {len(made)} curve{"s" if len(made) > 1 else ""}'
