# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""The sentences of the core a user reads, in the interface's words (the writing rules G1 and G7, docs/interface.md):
the core stays the port of the original, so they change here, at the border. tests/test_ui_texts.py checks that every
key is still in the core. {n}: the number the sentence carries."""
import re

CORE_TEXTS = {
    # edit.gizmoBlocked
    'The selection holds imported meshes, which the original moves with the curves: they come with the import of meshes (phase 5). '
    'Select the curves, the walls or the converted meshes instead (box, or Shift+click)':
    'Imported meshes cannot be moved directly: select curves, walls, or converted meshes instead',
    # tube.addExtra
    'Twist added — paint it: ↻ clockwise, Ctrl — ↺ anticlockwise, Ctrl+Alt — erase, Shift — smooth':
    'Twist added — paint it: ↻ clockwise, Ctrl — ↺ counterclockwise, Ctrl+Alt — erase, Shift — smooth',
    # walls.segmentsToCurves
    'Select a NURBS surface with width segments (Width segments ≥ 1)': 'Select a wall with Width Segments ≥ 1',
    # walls.surfaceMenuChoices
    'Create NURBS surface (wall)': 'Create Wall',
    'Wall between the two trunks (facing branches)': 'Wall Between the Two Trunks (Facing Branches)',
    'Wall between exactly these two curves': 'Wall Between Exactly These Two Curves',
    'Walls between {n} curves (open)': 'Walls Between {n} Curves (Open)',
    'Walls between exactly these {n} curves': 'Walls Between Exactly These {n} Curves',
    'Volume lock from {n} curves (closed)': 'Volume Lock from {n} Curves (Closed)',
    'Fill the hole between these curves': 'Fill the Hole Between These Curves',
    'Create average curve': 'Create Average Curve',
    'Merge anchors at identical places': 'Merge Anchors at Identical Places',
}


def core(text):
    """a sentence of the core as the interface says it; any other one as it is"""
    if text in CORE_TEXTS:
        return CORE_TEXTS[text]
    n = re.search(r'\d+', text or '')                               # (the core says None where there is no sentence)
    said = CORE_TEXTS.get(text.replace(n[0], '{n}', 1)) if n else None
    return text if said is None else said.replace('{n}', n[0])
