# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Ours (lot ux7, part 2 — docs/fase-3-plano-ciclo-nativo.md, "Estado"): text widths in Blender's own UI font, so no
text of the add-on is ever cut with "…" (the rule of the project since 21/10/2026): a paragraph wraps by width, a name
that does not fit goes on as a second line, a row too narrow for its labels takes short ones.

The measure is blf on font 0 — the font the interface draws its widgets with — at the widget style's size (11 points
by default) times the interface scale: what Blender itself measures when it decides to clip (UI_fontstyle_string_width).
A UI unit is 20 pixels times the same scale (UI_UNIT_X), so a width in units hardly changes with the scale; the
measure is taken at the scale in use (the font's hinting differs a little between sizes). blf works in -b too (the
default font is loaded without a window); the scale is 1 there. A custom interface font (Preferences > Interface >
Text Rendering) is not measured: font 0 stays the default one (a known limit).

How a row shares its width — measured in Blender 5.1 with real captures (docs/img/ux7-*.png): in a panel or a pop-up
(a vertical layout with the default EXPAND alignment) every item of a row gets the SAME share, whatever its text
(ui_layout_variable_size is false there: each item counts as ten units, ui_item_fit gives each its part); a split
gives its factor; a layout with ui_units_x set keeps that width. A button's text is clipped when it is wider than the
button less its icon and margins (CLIP_PAD), a slider's label when the label and the value do not fit together
(SLIDER_PAD), a label's text a little later (LABEL_PAD)."""
import bpy

UNIT_PX = 20.0                                                      # UI_UNIT_X at scale 1
ICON = 1.0                                                          # an icon, in units (the icon and its gap)
CLIP_PAD = 0.9                                                      # a button: its two text margins and the room the clipping keeps
LABEL_PAD = 0.5                                                     # a label: its margins
SLIDER_PAD = 2.6                                                    # a slider: the margins of the label, the gap before the value, the value's margins
ENUM_LABEL = 1.0 / 3.0                                              # an enum with a name: the name's share of the item (ui_item_with_label)

_cache = {}


def scale():
    """the interface scale in use (preferences.system.ui_scale: the user's Resolution Scale times the display's);
    1 without a window"""
    try:
        s = bpy.context.preferences.system.ui_scale
    except AttributeError:
        s = 0.0
    return s if s > 0.0 else 1.0


def points():
    try:
        return bpy.context.preferences.ui_styles[0].widget.points
    except (AttributeError, IndexError):
        return 11.0


def units(text, sc=None):
    """the width of `text` in UI units, in the widget font at scale `sc` (the one in use by default)"""
    sc = sc or scale()
    pt = points()
    key = (text, sc, pt)
    w = _cache.get(key)
    if w is None:
        import blf
        blf.size(0, pt * sc)                                        # (sets the size of font 0 for this measure; the interface sets it again before it draws)
        w = blf.dimensions(0, text)[0] / (UNIT_PX * sc)
        if len(_cache) > 20000:
            _cache.clear()
        _cache[key] = w
    return w


# A row that sizes its items by their texts (alignment LEFT / RIGHT, a header, a menu) gives a button its text plus
# the margins of ui_text_pad_default (1.5 units, 0.25 more with an icon) and a label its text plus those of
# ui_text_pad_none (0.25 units, 1.5 more with an icon): the widths the HairMesh Outliner's names take.
BTN_PAD, BTN_ICON = 1.5, 0.25
LBL_PAD, LBL_ICON = 0.25, 1.5


def natural_button(text, icon=True, sc=None):
    """the width Blender gives a button in a row that sizes it by its text"""
    return units(text, sc) + BTN_PAD + (BTN_ICON if icon else 0.0)


def natural_label(text, icon=True, sc=None):
    """the width Blender gives a label in a row that sizes it by its text"""
    return units(text, sc) + LBL_PAD + (LBL_ICON if icon else 0.0)


def label_need(text, icon=True, sc=None):
    """the width a label needs to show `text` whole"""
    return units(text, sc) + (ICON if icon else 0.0) + LABEL_PAD


def button_need(text, icon=True, sc=None):
    """the width an operator button (or a toggle, a menu button) needs to show `text` whole"""
    return units(text, sc) + (ICON if icon else 0.0) + CLIP_PAD


def slider_need(name, value_text, sc=None):
    """the width a slider — or a number field: the same room, measured in Blender 5.1.2 on 30/10/2026 (a field's name
    and value start and end where a slider's do; its arrows show over the margins on hover) — needs to show its name
    and its value whole"""
    return units(name, sc) + units(value_text, sc) + SLIDER_PAD


BREAK_AFTER = '_-./\\'                                               # where a word too long for a line is best broken


def split_word(word, room, sc=None):
    """a word too long for a line: the longest head that fits and the rest — broken after a '_', '-', '.' or a path
    separator when one lies in the second half of that head (a name reads better cut there)"""
    for n in range(len(word) - 1, 0, -1):
        if units(word[:n], sc) <= room:
            for k in range(n, max(1, n // 2) - 1, -1):
                if word[k - 1] in BREAK_AFTER:
                    return word[:k], word[k:]
            return word[:n], word[n:]
    return word[:1], word[1:]


def wrap_parts(parts, room, sc=None, sep='  '):
    """parts of a text (a name, then what is said about it: "(4 walls)", "(via Bang Main)"...) as lines of at most
    `room` units: a part goes whole to the next line when it does not fit the current one, and is wrapped by words
    only when it does not fit a line of its own"""
    lines = []; cur = ''
    for p in parts:
        if not p:
            continue
        cand = f'{cur}{sep}{p}' if cur else p
        if units(cand, sc) <= room:
            cur = cand
            continue
        if cur:
            lines.append(cur); cur = ''
        if units(p, sc) <= room:
            cur = p
            continue
        ws = wrap(p, room, sc)
        lines += ws[:-1]; cur = ws[-1]
    if cur:
        lines.append(cur)
    return lines or ['']


def wrap_path(text, room, sc=None):
    """a text holding a file path as lines of at most `room` units, broken after the path's separators (a folder name
    is split only when it is longer than a line)"""
    import re
    tokens = [t for t in re.split(r'(?<=[\\/ ])', text) if t]
    lines = []; cur = ''
    for t in tokens:
        if units(cur + t, sc) <= room:
            cur += t
            continue
        if cur:
            lines.append(cur.rstrip()); cur = ''
        while units(t, sc) > room and len(t) > 1:
            head, t = split_word(t, room, sc)
            lines.append(head)
        cur = t
    if cur:
        lines.append(cur.rstrip())
    return lines or ['']


def wrap(text, room, sc=None):
    """`text` as lines of at most `room` units (greedy, by words; a word longer than a line is split)"""
    lines = []; cur = ''
    for w in text.split(' '):
        if not w:
            continue
        cand = f'{cur} {w}' if cur else w
        if units(cand, sc) <= room:
            cur = cand
            continue
        if cur:
            lines.append(cur); cur = ''
        while units(w, sc) > room and len(w) > 1:
            head, w = split_word(w, room, sc)
            lines.append(head)
        cur = w
    if cur:
        lines.append(cur)
    return lines or ['']


# A split puts the style's column space between its columns whether it is aligned or not (uiLayoutSplit: 8 px at scale
# 1, 0.4 units), taken out of the width it shares: nested, every column after the first starts later and the last one
# loses what all the others took (measured in the lot 1.2b: "Merge" got 49 of the 71 px it asked for and was cut,
# with free room at the end of the row). An aligned group then draws its buttons joined over that space; buttons that
# are not in one group keep it as the gap between them.
SPLIT_SPACE = 0.4
SPLIT_SLACK = 0.05                                                  # a column's width is cut to whole pixels: a pixel more is asked for each


def split_exact(layout, runs, total):
    """Sub-layouts side by side, each as wide as asked. `runs`: [[(key, units or None), ...], ...] — the columns of a
    row in runs; the columns of one run are a family, joined (one aligned group); two runs are apart, with the split's
    own space between them (each run an aligned group of its own, under layouts that are not aligned). None: the
    free column (one at most), which gets what the others and the spaces leave of `total` units. Returns {key: layout}."""
    runs = [r for r in runs if r]
    n = sum(len(r) for r in runs)
    fixed = sum(u + SPLIT_SLACK for r in runs for _k, u in r if u)
    free = max(0.5, total - fixed - (n - 1) * SPLIT_SPACE)
    alloc = [[(k, u + SPLIT_SLACK if u else free) for k, u in r] for r in runs]
    out = {}

    def chain(lay, parts, aligned, leaf):
        room = sum(u for _p, u in parts) + (len(parts) - 1) * SPLIT_SPACE
        for i, (p, u) in enumerate(parts):
            if i == len(parts) - 1:
                leaf(lay.row(align=aligned), p)
                return
            w = room - SPLIT_SPACE                                  # (what the split shares: its width less its space)
            sp = lay.split(factor=max(0.001, min(0.999, u / w)), align=aligned)
            leaf(sp.row(align=aligned), p)
            lay = sp.row(align=aligned)
            room = w - u

    def run_into(lay, run):
        chain(lay, run, True, lambda sub, key: out.__setitem__(key, sub))

    if len(alloc) == 1:
        run_into(layout, alloc[0])
    elif alloc:
        parts = [(run, sum(u for _k, u in run) + (len(run) - 1) * SPLIT_SPACE) for run in alloc]
        chain(layout, parts, False, lambda sub, run: run_into(sub.row(align=True), run))
    return out


def split_cols(layout, widths, align=True):
    """sub-layouts of the given widths (units) side by side, made with nested splits: in every row drawn this way
    the columns start at the same place whatever their contents (a plain row shares its width in proportion to its
    items' texts, so a longer name moves the columns after it: part 3 of the lot ux7). A width of 0 gets no
    sub-layout (None in its place)."""
    out = []; lay = layout
    live = [i for i, w in enumerate(widths) if w > 0]
    total = sum(widths[i] for i in live)
    for n, i in enumerate(live):
        if n == len(live) - 1:
            out.append((i, lay.row(align=align)))
            break
        sp = lay.split(factor=max(0.001, min(0.999, widths[i] / total)), align=align)
        out.append((i, sp.row(align=align)))
        lay = sp.row(align=align)
        total -= widths[i]
    got = dict(out)
    return [got.get(i) for i in range(len(widths))]
