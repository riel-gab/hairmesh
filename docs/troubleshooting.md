# Troubleshooting

Common problems and how to fix them. If yours is not here, [report a bug](#reporting-a-bug).

## The tool does nothing in this workspace

The HairMesh icon is always in the toolbar, but a workspace with **Filter Add-ons** turned on can hide the add-on. Then
its clicks, keys, panels and menus are off there, and the tool header says:

> HairMesh is hidden in this workspace (Filter Add-ons): clicks and keys are off

Click **Show HairMesh Here** in the tool header. It adds HairMesh to that workspace's add-on list.

## A key does not do what this page says

- **Is the tool active?** Every key except **Shift+Alt+H** works only while the HairMesh tool is the active tool, in
  Object Mode. Press **Shift+Alt+H** to turn it on.
- **Is something of HairMesh selected?** G, R, S, X, Delete, H, Shift+H, Alt+A and F act on HairMesh only when something
  of it is selected. Otherwise they are Blender's own keys. Alt+H needs something hidden by HairMesh.
- **Is it the right sub-tool?** Some keys belong to one sub-tool. For example, a left click draws in Draw and cuts in
  Scissors. [Shortcuts](shortcuts.md) says when each key works.
- **Is a pop-up open?** The HairMesh Menu (D) and the Outliner (O) take the keyboard while open, so Ctrl+Z does not
  work there. Use their **Undo** and **Redo** buttons, or close the pop-up first.
- **Did you change the key?** Every shortcut is editable in Edit > Preferences > Add-ons > HairMesh. Each one can be
  restored to its default there. The same page can turn off the global shortcut or all tool shortcuts.
- **Another add-on on the same key?** The tool's keys only apply while the tool is active. If another add-on still wins
  a key, change one of them in the preferences.

## A button is grayed out

HairMesh dims a control when it would have no effect right now. Hover over it: the tooltip says why and where to go.
For sliders, the reason is also in the **i** at the right of the block's title. Two common ones:

- **Bulge** does nothing on a wall with Width Segments 0.
- **Project On** (Freehand) needs a visible head.

## Convert makes nothing

- **"Tick the groups to convert"**: open the Conversion tab (D) and tick at least one group.
- **"The ticked groups contain no walls"**: the curves have no walls yet. Select two curves, then Shift+RMB > **Walls /
  Lock** > **Create Wall**. Walls at the root level are not converted: put them in a group.

## Curves or walls disappeared

- **Baked view.** Baked hides the walls and curves that a confirmed mesh came from. Click **Live**.
- **Auto mode.** A group whose curves mode is **Auto** shows its curves and anchors only while you work inside it. Set
  it to **Show** in the Outliner (O) or in the group's **Display** block.
- **Hidden.** Something was hidden with the eye, H or Shift+H. Press **Alt+H**, or open the Outliner and turn the eye
  back on.
- **Overlays off.** HairMesh draws its curves, anchors and guides as viewport overlays. Turn **Show Overlays** back on
  in the 3D View header.

## The mesh did not change after I edited the curves

That is by design: a confirmed mesh stays as it is. The Conversion tab tells you how many meshes changed since. Click
**Convert** again, then **Confirm**.

## Conversion or editing is slow on a big project

- **Convert** shows its progress in the status bar, one mesh at a time. Press **Esc** to stop it without changing
  anything.
- The first conversion after opening a `.blend` builds internal tables first, so it takes longer than the next ones.
- After an edit, **Convert** rebuilds only the meshes that changed.
- Heavy work waits until you finish: sliders rebuild when you release them, and the Density brush rebuilds the loops
  when you release the button. Before a long rebuild, the status bar says how many locks it will rebuild and roughly
  how long it will take.
- Slow actions print a line starting with **HairMesh slow** in the system console (see below). Include these lines in a
  bug report about speed.

## I cannot find the example file

The example does not ship with the add-on. Download it from the author's page, <https://boosty.to/catarackta>, and
import it with File > Import > HairMesh Project (.hairmesh).

## Reporting a bug

Open an issue at <https://github.com/riel-gab/hairmesh/issues>. Please include:

1. **Blender version** (as shown on the splash screen).
2. **Operating system.**
3. **HairMesh version** (Edit > Preferences > Add-ons > HairMesh).
4. **Steps to reproduce**, starting from a new file if you can.
5. **What happened**, and **what you expected**.
6. **The console output.**

### Getting the console output

On Windows, open **Window > Toggle System Console**. On macOS and Linux, start Blender from a terminal and read the
output there.

Then do the steps again and copy what HairMesh printed:

- lines starting with **HairMesh slow**, for slow actions;
- any error with a **traceback** (lines starting with `Traceback (most recent call last):`).

Paste them into the issue as text, inside a code block. A small `.blend` or `.hairmesh` that shows the problem helps a
lot too.
