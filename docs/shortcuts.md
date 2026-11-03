# Shortcuts

This page is generated from the add-on's code (`tools/gen_shortcuts_doc.py`). Every key is editable in
Edit > Preferences > Add-ons > HairMesh, where each one can also be restored to its default.

Only **Shift+Alt+H** is global. The rest work only while the HairMesh tool is active. Many of them are contextual: they
act on HairMesh only in the situation the When column gives, and leave the key to Blender otherwise.

## Global

| Key | When | Action | What it does |
|---|---|---|---|
| Shift+Alt+H | Object Mode, tool on or off | Toggle HairMesh Tool | Switch to the HairMesh tool |

## While the tool is active

The Action column is the name in the list of shortcuts in the preferences.

| Key | When | Action | What it does |
|---|---|---|---|
| 1 | Any sub-tool | HairMesh Select Sub-tool | Select: select and move anchors and curves |
| 2 | Any sub-tool | HairMesh Draw Sub-tool | Draw: draw a curve on the head — each click places an anchor, Enter finishes |
| 3 | Any sub-tool | HairMesh Scissors Sub-tool | Scissors: drag a line across curves to cut them, or cut a wall in two |
| C | Any sub-tool | Circle Select | Select anchors and curves with a circle: the circle follows the cursor |
| K | Any sub-tool | HairMesh Scissors Sub-tool | Scissors: drag a line across curves to cut them, or cut a wall in two |
| Esc | Guides > Pencil, a loop being drawn | Cancel the Pencil Loop | Drop the pencil loop |
| Enter | Guides > Pencil, a loop being drawn | Close the Pencil Loop | Close the pencil loop into a NURBS disc |
| Esc | Edges, with edges selected | HairMesh Deselect Edges | Deselect the edges |
| Esc | Any other sub-tool | HairMesh Select Sub-tool | Select: select and move anchors and curves |
| LMB | Draw | Draw HairMesh Curve | Draw a hair curve: in Anchors each click places an anchor on the head, Enter or right click finishes, Esc finishes and goes back to Select |
| LMB | Scissors > Curve | HairMesh Scissors | Drag a line across the selected curves to cut them |
| LMB | Scissors > Wall | HairMesh Wall Cut | Click a wall, then put the cut on it like a curve — from one of its curves, over the wall, to its other curve |
| LMB | Guides > Line or NURBS Disc | HairMesh Guides | Drag a line across the selected curves, click on the hair or draw a loop |
| LMB | Guides > Pencil | HairMesh Guide Pencil | Hold the left button and draw a loop around the hair |
| LMB | Edges | HairMesh Pick Edge | Pick an edge of a converted mesh |
| LMB Double-Click | Select, on a curve | HairMesh Curve Anchors (Double Click) | Double click a curve: select all its anchors |
| LMB | Select | HairMesh Select | Select anchors and curves of the HairMesh project: click an anchor and drag it |
| LMB | Crown | HairMesh Crown | Click on the head where the whorl should be to build a crown there |
| RMB | On a curve | Insert HairMesh Anchor | Insert an anchor where the right button hits a curve and drag it |
| B | Anchors selected | HairMesh Anchor Smoothness | Set the smoothness of the selected anchors: hold B and drag with the left button, 200 px = 100 % |
| Backspace | Something of HairMesh selected | Delete HairMesh Selection | Delete the selected anchors, else the selected curves |
| X | Something of HairMesh selected | Delete HairMesh Selection (Ask) | Delete the HairMesh selection after a short question: the selected anchors, else the selected items |
| Delete | Something of HairMesh selected | Delete HairMesh Selection (Direct) | Delete the HairMesh selection at once: the selected anchors, else the selected items |
| H | Something of HairMesh selected | HairMesh Hide Selected | Hide the selected items, as their eye in the HairMesh Outliner does |
| Shift+H | Something of HairMesh selected | HairMesh Hide Unselected | Hide what is not selected, as the eye in the HairMesh Outliner does |
| Alt+H | Something of HairMesh hidden | HairMesh Reveal Hidden | Show everything hidden with the eye of the HairMesh Outliner, H or Shift+H |
| Alt+A | Something of HairMesh selected | HairMesh Deselect All | Clear the HairMesh selection |
| G | Select, something of HairMesh selected | HairMesh Move Selection | Move the HairMesh selection — anchors, or the curves, walls and groups selected: follows the mouse on the view plane |
| R | Select, something of HairMesh selected | HairMesh Rotate Selection | Turn the HairMesh selection about the last anchor, or the center of the selected items: the angle about it on the screen |
| S | Select, something of HairMesh selected | HairMesh Scale Selection | Scale the HairMesh selection about the last anchor, or the center of the selected items: the distance to it on the screen |
| E | Something of HairMesh selected | HairMesh Move Gizmo | Show the Move gizmo on the selection — anchors, curves, walls or a group |
| No default key | Something of HairMesh selected | HairMesh Rotate Gizmo | Show the Rotate gizmo on the selection — anchors, curves, walls or a group |
| No default key | Something of HairMesh selected | HairMesh Scale Gizmo | Show the Scale gizmo on the selection — anchors, curves, walls or a group |
| Shift+D | Something of HairMesh selected | Duplicate HairMesh Selection | Duplicate the selected curves, walls and groups |
| F | Something of HairMesh selected | HairMesh Frame Selection | Frame the HairMesh selection: the selected anchors, else what is inside the selected curves, walls and groups |
| P | A group selected last | HairMesh Parent to Last Selected Group | Put the selected curves, walls and groups into the group selected last: one undo step |
| Shift+P | Items inside a group selected | HairMesh Unparent to Root | Take the selected curves, walls and groups out of their groups, to the root level: one undo step |
| Shift+RMB | Any sub-tool | HairMesh Pie | Open the pie |
| D | Any sub-tool | HairMesh Menu | Open the pop-up |
| Q | Any sub-tool | HairMesh Sub-tool Pie | Open the pie |
| O | Any sub-tool | HairMesh Outliner | Open the pop-up |
| 4 | Any sub-tool | HairMesh Guides Sub-tool | Guides: guide lines — drag a line across curves, place a NURBS disc or draw one with the pencil |
| 5 | Any sub-tool | HairMesh Crown Sub-tool | Crown: place the whorl on the head, the parting, the connector and the crown curves |
| M | Any sub-tool | HairMesh Brush Sub-tool | Brush: move, smooth, inflate or shrink the anchors under a circle |
| V | Any sub-tool | HairMesh Edges Sub-tool | Edges: pick edges of a converted mesh and turn the path into curves |
| 6 | Any sub-tool | HairMesh Modifiers Tab | Open the HairMesh Menu on the Modifiers tab: Profile, Extra and Topology, with any sub-tool |

Over the viewport and the sidebar (N):

| Key | When | Action | What it does |
|---|---|---|---|
| F2 | Any sub-tool | Rename HairMesh Item | Rename the selected item: F2 in the viewport and over the N panel while the tool is active, a double click on its name in the outliner, the pencil of the outliner's bar |

## Sub-tools (Q pie)

| Key | Sub-tool | What it does |
|---|---|---|
| 1 | Select | Select and move anchors and curves |
| 2 | Draw | Draw a curve on the head — each click places an anchor, Enter finishes |
| 3 | Scissors | Drag a line across curves to cut them, or cut a wall in two |
| 4 | Guides | Guide lines — drag a line across curves, place a NURBS disc or draw one with the pencil |
| 5 | Crown | Place the whorl on the head, the parting, the connector and the crown curves |
| M | Brush | Move, smooth, inflate or shrink the anchors under a circle |
| V | Edges | Pick edges of a converted mesh and turn the path into curves |

## In a modal

While a modal runs, the status bar shows its keys. These are the same lists.

### Draw: placing anchors

| Key | Action |
|---|---|
| LMB | Place Anchor |
| Enter / RMB | Finish Curve |
| Esc | Finish, Back to Select |

### Draw: Freehand stroke

| Key | Action |
|---|---|
| LMB | Release: Finish Stroke |
| Esc | Cancel Stroke |
| MMB | Navigate |

### Select: dragging anchors

| Key | Action |
|---|---|
| Shift | Slide Along Curve |
| Alt | Follow Head Surface |
| Esc | Cancel |

### Select: sliding along a connector

| Key | Action |
|---|---|
| LMB Drag | Slide Along the Connector |
| Ctrl | Click: Detach |
| Esc | Cancel |

### Select: smoothness (B)

| Key | Action |
|---|---|
| B + LMB Drag | Smoothness |
| B | Release: Confirm |

### Circle Select (C)

| Key | Action |
|---|---|
| LMB | Select |
| Shift / MMB | Deselect |
| Wheel / + / - | Radius |
| RMB / Esc | Exit |

### Move, Rotate, Scale (G, R, S)

| Key | Action |
|---|---|
| LMB / Enter | Confirm |
| RMB / Esc | Cancel |
| X / Y / Z | Axis (Twice: Local) |
| Shift+X / Y / Z | Plane |
| Digits | Type a Value |
| Ctrl | Snap |
| Shift | Precision |

### Gizmo handles

| Key | Action |
|---|---|
| LMB Drag | Move the Handle |
| Ctrl | Uniform Scale |
| Shift | 0.1 Steps / Slide Along Curve |
| RMB / Esc | Cancel |

### Scissors: Curve

| Key | Action |
|---|---|
| LMB Drag | Cut the Selected Curves |
| RMB | Pick a Curve |
| Shift | Add |
| Esc | Cancel |

### Scissors: Wall

| Key | Action |
|---|---|
| LMB | Place the Cut |
| Enter | Cut |
| Esc | Cancel |

### Guides: Line

| Key | Action |
|---|---|
| LMB Drag | Make a Guide Line |
| LMB | Select a Guide |
| Esc | Cancel |

### Guides: NURBS Disc

| Key | Action |
|---|---|
| LMB | Place a NURBS Disc |
| LMB | On a Guide: Select It |
| Esc | Cancel |

### Guides: Pencil

| Key | Action |
|---|---|
| LMB Drag | Draw the Loop |
| Enter | Close the Loop |
| Esc | Cancel |

### Crown

| Key | Action |
|---|---|
| LMB | Release on the Head: Create the Crown |
| Esc | Cancel |

### Brush

| Key | Action |
|---|---|
| [ / ] | Radius |
| F | Radius |
| Shift+F | Hardness |
| Ctrl+RMB Drag | Both |
| Esc | Exit |

### Brush: painting: Mask

| Key | Action |
|---|---|
| LMB | Paint the Mask |
| Ctrl | Erase |
| Shift | Smooth |

### Brush: painting: Thickness

| Key | Action |
|---|---|
| LMB | Paint the Intensity |
| Ctrl | Inverted |
| Ctrl+Alt | Erase |
| Shift | Smooth |

### Brush: painting: Twist

| Key | Action |
|---|---|
| LMB | Paint the Twist |
| Ctrl | The Other Way |
| Ctrl+Alt | Erase |
| Shift | Smooth |

### Brush: painting: Density

| Key | Action |
|---|---|
| LMB | Paint the Density |
| Ctrl | Erase |
| Shift | Smooth |

### Brush: painting: Anchors

| Key | Action |
|---|---|
| LMB | Drag the Anchors |
| Shift | Smooth |
| Ctrl | Inflate |
| Ctrl+Alt | Shrink |

### Brush: radius and hardness: Radius (F)

| Key | Action |
|---|---|
| Move | Set the Radius |
| Ctrl | Snap |
| Shift | Precision |
| LMB / Enter | Keep It |
| RMB / Esc | Put It Back |

### Brush: radius and hardness: Hardness (Shift+F)

| Key | Action |
|---|---|
| Move | Set the Hardness |
| Ctrl | Snap |
| Shift | Precision |
| LMB / Enter | Keep It |
| RMB / Esc | Put It Back |

### Brush: radius and hardness: Both (Ctrl + right drag)

| Key | Action |
|---|---|
| Move | Set Radius and Hardness |
| RMB | Release: Keep Them |
| Esc | Put Them Back |

### Edges: box

| Key | Action |
|---|---|
| Release | Deselect the Edges in the Box |
| Esc | Cancel |

### Profile editor

| Key | Action |
|---|---|
| LMB Drag | Move a Point |
| Shift | Snap |
| Ctrl | Pull a Glued One Apart |
| RMB | Add |
| Double-Click | Delete |
| X | Delete |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z | Redo |
| Enter / Esc | Exit |
