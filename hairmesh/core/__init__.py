# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Pure-Python core of HairMesh: no bpy, no numpy required.

Everything here is a faithful port of the original JavaScript (reference/hair-modeler.clean.html). Functions keep
the original identifiers and cite their source line (`# port of name (hair-modeler.clean.html:N)`), and all
geometry stays in the original coordinate space: Y up, head units. See docs/js-para-python.md for the semantic
differences the port has to compensate for.
"""
