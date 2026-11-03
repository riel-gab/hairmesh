# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""HairMesh for Blender: a port of Stylized HairMesh Creator by catarackta (https://catarackta.netlify.app/), reference version 1.0.1.

Layout:
  hairmesh.core     pure Python (no bpy): data model, curve math, surface -> quad topology, conversion.
                    Works in the original's coordinate space (Y up, head units) so results compare 1:1 with
                    the JavaScript reference (tools/js-harness).
  hairmesh.blender  operators, panels and the conversion to / from Blender objects. Axis swap and scale
                    happen only here.
"""


def register():
    from .blender import register as _register
    _register()


def unregister():
    from .blender import unregister as _unregister
    _unregister()
