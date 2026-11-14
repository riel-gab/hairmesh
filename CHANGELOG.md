# Changelog

## 1.1.0 (2026-11-14)

What you will notice:

- Measures show in Blender's unit: the loop step, Merge Distance, the radii, the amplitude, the flat lock's width and the cap's Size read in cm or m as set in Scene > Units (3 significant digits), and so do Match Project and the loop step columns; the value saved in the project does not change.
- Updates from Blender: Edit > Preferences > Add-ons > HairMesh > Enable Updates adds the HairMesh repository to Get Extensions (with Allow Online Access on); Blender checks it at start-up, and Refresh Remote (Get Extensions, the drop-down menu at the top right) checks it at once. The Updates box says when a newer version is listed. Blender updates only a copy installed from that repository: a copy installed from the zip needs to be installed again from it once.
- A baked mesh survives Ctrl+Z and Ctrl+Shift+Z: after undo and redo, Live and Baked keep the same mesh instead of rebuilding it (#4).
- A baked crown mesh is no longer rebuilt on every update; it is rebuilt when the crown changes (#5).
- Reveal Hidden (Alt+H) says what the Live / Baked view hides, and where to change it, when that is all that is hidden, instead of "Nothing of the HairMesh is hidden" (#6).
- The eye of the head in the HairMesh Outliner now hides the head object too; before, the head stayed on screen.
- Symmetry block: the switch is a checkbox in the block header, which stays visible when the block is closed; inside, Axis (X Y Z) and Apply Symmetry take only the width they need.
- Project tab: the line with the folder of the file no longer breaks into two lines when the path is long.
- Flat locks and volume branches (Draw > Lock): draw a flat ribbon of hair with a width at every anchor, or start from two anchors of other curves for a lock between them, or from three or more for a branch. Width, rotation, Profile Smoothing and Tip in the curve and anchor sheets; Extrude, Offset and Glue brushes paint the ribbon (shown as colour on it); Convert to Curves & Walls and Plain Curve turn it into ordinary curves. Files that carry flat locks open with their ribbons in place.
- X junctions: Separate Zones at This X (anchor pie, More) and Separate X Junctions (N) (group sheet) cut the walls across an X and run the new walls on edge curves; the walls menu of two curves in an X offers Create Walls Around the X first. Edge curves follow the curves they run along.
- Scissors > Split Ends: click a curve and it splits from there to its tip into two branches; the Spread field and `[` `]` / F (or Ctrl + right drag) set the reach, and a preview shows both branches under the pointer.
- Detect Fill Outline Here (Shift+RMB pie): lights up the outline under the pointer (two curves, or a hole in the walls) and a confirmation pie (W Cancel, E Fill) fills it.
- Drag the right mouse button across empty space to insert anchors where the line crosses the selected curves (a plain right click still opens the context menu on release); Select Lock Curves (N) in the Shift+RMB pies; with a NURBS disc selected, Ctrl + click an item to add or remove its group in the disc's Groups list.
- Duplicate now copies a flat lock; welding skips the anchors of a flat lock; the walls menu follows the order of the 1.0.2; the anchor colour in Draw no longer counts a shared anchor of an edge curve; the Groups list of a guide only takes groups it offers; the right mouse button of the Scissors picks a curve in Split Ends too; Delete (keep shape) on an anchor of three curves keeps all three shapes; a lock with Extrude whose tip is welded to its root no longer fails.
- Files with flat locks or X-junction edge curves are no longer reported as "newer tools, kept": both are edited here now.
- If you had remapped the Shift+RMB pie key, set it again in Preferences > Keymap (the item is now "HairMesh Pie", `hairmesh.pie`).
- Tests: the stress judge for baked meshes checks every previewed mesh against a conversion from scratch, through undo and redo (#8, #9).
- Detail Proxy (Conversion tab, between Relax and Merge Shared Trunks; also in a group's sheet): where a lock has detail on it (a dent, a groove, curves forking inside it), the loops are placed as on the simple tube around the detail and carried over to the detailed curves, so they no longer run in chevrons along the detail. It is on by default, so the mesh of an older project changes when it is rebuilt; untick the box to get the previous loops.
- Files of 1.0.3 with Even Loops and Proxy Shell are read, applied and kept: both shape the loops where they are painted, show in the HairMesh Outliner with an eye and Delete, and their sheet says what they do or why they do nothing. There are no tools to create, paint or sculpt them yet.
- Files of 1.0.3 keep their display settings (the `view` block) and the Detail Proxy switch when saved again.
- Dragging an anchor, changing a group's Conversion values, duplicating or hiding an Even Loops or Proxy Shell now rebuilds every lock the item reaches, so Convert gives the same mesh as a rebuild from scratch.
- The root of a flat lock that follows an anchor of a symmetric crown now moves the opposite root too.
- A paragraph in a sheet that takes two lines is no longer cut with "...".
- Turning the HairMesh tool on takes the HairMesh meshes, live walls and curves out of Blender's selection, so a baked mesh no longer shows two selection outlines; other objects stay selected (#7).
- Object > HairMesh lists actions only (sub-tools, Convert, Confirm, Cancel, Live, Baked, Hide Selected, Hide Unselected, Reveal Hidden, the HairMesh Menu and the Outliner); sliders and sheets stay in the pop-up and the N panel. With no project, Convert, Confirm, Cancel, Live and Baked are dimmed.
- The ribbon of a flat lock is drawn as one flat purple surface, the same on both sides, with no lines across it, and it also shows with the tool off; Width and Rotation (and the other sliders of the lock) move it while you drag.
- The pointer over the circles (brushes, Draw > Lock width, Split Ends, the Merge Distance Pick, Ctrl + drag in Select) is Blender's paint cross, never hidden.
- Junction and border curves show only while selected, as in the original; their anchors stay.
- Deleting one ring of a NURBS disc takes its effect off at once, as when the file is reopened.
- An anchor line that crosses nothing usable no longer leaves an empty undo step.
- Detect Fill Outline's 3 px tie follows the interface scale.
- Merge Shared Trunks is a checkbox, like Detail Proxy.
- Deleting an anchor that leaves a separated curve says that curve was deleted too.
- Sheets of the HairMesh Menu and of the N panel, reviewed as a whole: every field has its name on the left and its value on the right, one value per row; buttons are as wide as their text; buttons of one family are joined and separate actions stand apart; on / off options are checkboxes; measures show 2 decimals (4 in a project of small units; more can still be typed). The pies, the menus and the keys are as they were.
- E now extrudes a curve: with the first or the last anchor of a curve selected (Select sub-tool), E adds a new anchor there that follows the mouse like G (X / Y / Z, typed values, Ctrl, Shift, Alt); click or Enter confirms, Esc or right click removes it. Several selected ends grow together. When the selection cannot be extruded (a middle anchor, a welded end, a service curve, an attached root), the status bar says why. Also in the anchor's Weld / Split / Curves menu and sheet (Extrude Curve). To weld the new anchor to another curve, drag it onto an anchor of that curve and release there: the weld pie opens, as with G. Extending an edge of a lock made by Convert of a modifier makes it an ordinary lock; a line in the status bar says so.
- The Move gizmo key is now W (it was E). If you had remapped either key, check both in Preferences > Keymap.
- Sheets: a row of buttons always has the standard space above and under it when the row next to it is a row of fields, a block header or another row of buttons (the name row of a group sheet no longer touches New Sub-group / Rename... / Delete), in the HairMesh Menu and in the N panel, narrow or wide.
- Flat Lock block: each brush and its Clear line up with the Depth field, in the same columns; the brush button says Brush, beside its name (Extrude, Offset, Glue) in the column of names.
- "X-Ray" is spelled as in Blender everywhere.
- "Loops Horiz ↔ Perp" is now "Loop Direction"; its tooltip says what 0 and 1 do.
- Shortcuts panel (N): instead of a read-only list of the keys, one button, Edit Shortcuts in Preferences..., opens the HairMesh page of the preferences, where the keys are listed and edited.
- The Project panel no longer shows a "Loop step" label over the Conversion values.
- The Hide mode of a group's curves in the HairMesh Outliner (Auto / Show / Hide) shows Blender's closed eye instead of a no-entry sign, which read as Delete.
- One circle on the screen: the Merge Distance Pick started while Split Ends, a brush or the Lock width shows its circle hides that circle until the Pick ends, and the Pick gets the clicks; the circle below comes back after.
- Pick and Auto of Merge Distance, when dimmed (nothing reads the distance yet), only say why when clicked: no eyedropper, no new value, no undo step.
- A project with an FBX head saves the head as the original writes it (base64), so the original opens the file with its head.
- A curve made and selected by a sub-tool shows its sheet: after Draw (a curve, a flat lock), the anchor line (a right drag across curves), Split Ends, a wall cut, Duplicate, Convert to Curves & Walls or Extrude Curve from a sheet, the Selection block under the sub-tool's page in the D opens.
- After a click on the Draw's Mode in the D (and on a tab or sub-tool that ends a brush, the Lock width or Split Ends), the other buttons of the row no longer change the value when the pointer only passes over them.

Known limitations:

- Deleting a project's root with Blender's own delete (for example A, then X in Object Mode) leaves the project's live previews on screen, larger than the head and with no parent. They are removed at the next HairMesh action that updates a preview (for example New Project or Import .hairmesh). This is expected, not a bug.

## 1.0.4

What you will notice:

- Deleting the last confirmed mesh with Blender's own Delete no longer leaves an unused "HairMesh Mesh" material in
  the file; Ctrl+Z brings the meshes back with their material.
- Alt+H while a preview is up no longer reveals the confirmed mesh the preview replaces (when it was already hidden
  before Convert), so a group never shows two meshes at once. The meshes a preview replaces stay hidden until Confirm
  or Cancel, and Alt+H now says so when that is all that is hidden.

## 1.0.3

What you will notice:

- Profile editor: dragging a selected point now moves every selected point of that frame by the same amount, as in
  Blender. Dragging a point that is not selected selects it alone and moves it alone (with Shift, it is added to the
  selection and the whole selection moves). The selection stops at the edge of the frame without changing its shape,
  Shift still snaps the point under the pointer, and one drag is one Ctrl+Z.
- Conversion: switching to Baked while a preview is up and then pressing Cancel no longer leaves walls showing under a
  converted mesh that only partly covered the previewed group (a parent group's mesh, when a sub-group alone was
  converted): the mesh stays shown and its walls stay hidden.

## 1.0.2

What you will notice:

- Profile editor: a click now selects a point (it turns orange and gets a white ring until you click again);
  Shift+click adds it to or takes it out of the selection, a click on nothing inside a frame deselects, and X or Delete
  delete the selected points from the three profiles in one edit. With nothing selected they still delete the point
  under the pointer. Ctrl+Z and Ctrl+Shift+Z give the selection back with each edit. Before, a click did not select
  anything, so X had nothing to delete. (#2)
- Modifiers > Profile: Save Preset has the new-preset icon and Apply a check mark, and the tooltips say what each one
  does: Save Preset keeps the profile in the preferences without applying it, and Apply gives each selected curve a
  Profile modifier whose sheet has its own Start, Middle and End profiles. With no modifier selected, a line says
  "Apply, then edit Start, Middle, End". (#3)
- R on a single anchor no longer starts a rotation that turns nothing: the status bar says "Rotate needs 2 or more
  anchors: one anchor turns about itself". With a frozen handle on the anchor, or two or more anchors, R works as
  before. (#1)

## 1.0.1

What you will notice:

- Scissors > Wall: the double click that deletes an anchor of a cut is now Blender's own double click, so it
  follows Preferences > Input > Double Click Speed and Drag Threshold like every other double click.
- A wall cut in progress ends when you pick another tool in the toolbar or hide HairMesh in the workspace (Filter
  Add-ons). Before, the cut kept taking the clicks of the other tool.
- Scissors > Wall in the sidebar, with one wall selected while another tool is active, now switches to the HairMesh
  tool before the cut starts.
- The add-on's Website link (Edit > Preferences > Add-ons > HairMesh) opens the HairMesh repository.
- HairMesh is installed from the zip (Edit > Preferences > Get Extensions > the drop-down menu at the top right >
  Install from Disk). It is not on extensions.blender.org, so Blender does not update it by itself: install the new
  zip the same way.

Nothing else changes: the profile editor still deletes a point with a double click, the sliders still rebuild once
when you let them go, and the shortcuts, their list in the preferences and Restore Defaults stay as they were.

## 1.0.0

First release, a port of Stylized HairMesh Creator 1.0.1 by catarackta. Requires Blender 5.1 or later.

- Draw curves over a head, freehand or point by point; walls stretch between neighboring curves to form locks.
- Convert locks to clean quad meshes, with the same result as the original, and keep editing the curves afterwards.
- Guides, crowns, Y and X junctions, weld points, symmetry and duplication.
- Modifiers: tube, mask, thickness, twist, noise, curls, fill and density, with brushes for density and twist.
- Three profiles, a profile editor and presets; Extras with a weight brush.
- Brush and Edges tools, scissors, caps, and an Outliner of the project.
- Import of `.hairmesh` files. The project lives in the `.blend` file.
- One global shortcut (Shift+Alt+H); every shortcut can be edited in the preferences.
