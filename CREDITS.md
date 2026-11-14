# Credits

HairMesh is a port of **Stylized HairMesh Creator** by **catarackta** to Blender:
<https://catarackta.netlify.app/>

The core of this add-on (curves, walls, the quad topology, the conversion, guides, crown and modifiers) is ported from
version 1.0.3 of the original, a free web application. Everything specific to Blender (tools, menus, overlays, objects)
was written for this add-on by Riel.

## Permission from the original's author

Given in writing to Riel, who keeps the messages.

12/10/2026:

> I don't mind at all — if that works best for you, go ahead ... if you get it working and want to release it as an
> addon, let me know! I'll be happy to add a link to your work.

01/11/2026:

> Yes, I'm fine with GPL. Feel free to release the add-on with a link to me: catarackta.netlify.app/. The code of
> versions 1.0.0 to 1.0.2 is free to use for add-ons and plugins for any software.

06/11/2026, for version 1.0.3:

> Riel: Hi! I saw you released v1.0.3 for add-ons based on V1. Do you authorize me to port 1.0.3 into the HairMesh
> add-on too, under the same terms as 1.0.0–1.0.2 (GPL, with credit and the link to catarackta.netlify.app)?
>
> catarackta: yep

So the code of versions 1.0.0 to 1.0.3 is used here under these terms.

## Third-party code

`core/fdlibm.py` ports the elementary functions of fdlibm 5.3 and of FreeBSD's msun:

> Copyright (C) 1993 by Sun Microsystems, Inc. All rights reserved.
>
> Developed at SunSoft, a Sun Microsystems, Inc. business.
> Permission to use, copy, modify, and distribute this software is freely granted, provided that this notice is
> preserved.

## Licence

The code is under GPL-3.0-or-later (`LICENSE`). The icons are by Riel, released under CC0-1.0
(`blender/icons/LICENSE-ICONS`).
