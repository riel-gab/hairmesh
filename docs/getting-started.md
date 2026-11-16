# Getting Started

This page takes you from a fresh install to your first converted lock: two curves on a head, a wall between them,
and a clean quad mesh.

HairMesh is a port of [Stylized HairMesh Creator](https://catarackta.netlify.app/) by catarackta, made with the
author's permission. It needs Blender 5.1 or later.

## 1. Install

1. Download `hairmesh-<version>.zip` from the [Releases](https://github.com/riel-gab/hairmesh/releases) page.
2. In Blender, open Edit > Preferences > Get Extensions, open the drop-down menu at the top right, choose
   **Install from Disk** and pick the zip. You can also drag the zip into the Blender window and confirm.

Blender does not update a zip installed from disk: to update it, download the new zip and install it the same way.

To have Blender look for new versions instead:

1. In Edit > Preferences > System, turn on **Allow Online Access**.
2. In Edit > Preferences > Add-ons > HairMesh, click **Enable Updates** (in the Updates box). The HairMesh repository
   is added to Get Extensions and Blender checks it at every start-up. To check it now, open Get Extensions, the
   drop-down menu at the top right, and choose **Refresh Remote**. When it lists a newer version, the Updates box says
   so, with a button to Get Extensions.
3. Blender updates an extension only from the repository it was installed from. If you installed the zip, uninstall
   HairMesh once in Get Extensions and install it again from the HairMesh repository listed there; from then on,
   Get Extensions updates it.

## 2. Turn the tool on

1. Make sure you are in **Object Mode**.
2. Press **Shift+Alt+H**. The HairMesh tool becomes the active tool in the toolbar.
3. Press **Shift+Alt+H** again at any time to go back to the tool you used before.

This is the only global shortcut. Every other HairMesh key works only while the tool is active. See
[Shortcuts](shortcuts.md) for the full list.

Three keys open everything else:

- **D** opens the HairMesh Menu, a pop-up with tabs.
- **Q** opens the sub-tool pie (Select, Draw, Scissors, and more).
- **O** opens the HairMesh Outliner.

### Try the example project

To see a finished model first, open the example project of the original app. It does not ship with the add-on: it
comes with the browser version of the original app, as a free download from its author.

1. Open the author's post [Stylized HairMesh Creator 1.0.2 — update](https://boosty.to/catarackta/posts/37965bca-46bf-4e65-8ef5-3e460e858675)
   and download the attachment `Stylized HairMesh Creator HTML V1.0.2.zip`. The download is free.
2. Extract the zip. The example is `Examples/ExampleProject-Wolfcut.hairmesh`.
3. In Blender, choose File > Import > HairMesh Project (.hairmesh) and pick that file. The defaults are fine:
   **Scale** Automatic sizes the head to between 0.1 m and 1 m tall, and **Import Head** brings the head with the
   hair.
4. The head appears with the hair's walls on it. Press **Shift+Alt+H** to turn the tool on: **O** lists the groups in
   the HairMesh Outliner, and **Convert**, in the Conversion tab of the HairMesh Menu (**D**), turns them into meshes.

Example model by catarackta, hair concept by TKY_6a. More from the author: <https://boosty.to/catarackta>.

## 3. Start a project with a head

HairMesh draws hair on a mesh you choose as the head.

1. Add or open a head mesh and click it so it is the active object.
2. In the tool header, click **New Project...** (it is also in the **Project** tab of the HairMesh Menu).
3. Give the project a name. Leave **Head** checked so the active mesh becomes the head.
4. Click OK.

The project is stored inside your `.blend` file. Save the `.blend` and the hair comes with it.

If you skipped the head, select the head mesh later and click **Set Head** in the Project tab (once a head is set, the
button shows **Head:** and the head's name). Click it again after you move or edit the head.

## 4. Make a group

Walls are converted group by group, so start with a group.

1. With nothing selected, press **Shift+RMB** to open the pie.
2. Click **New Group**, at the bottom of the pie (or press **2** on the numpad while the pie is open).

![The Shift+RMB pie with nothing selected, New Group at the bottom](img/gs-pie.png)
<!-- claims gs-pie: groups=0 curves=0 pie.S="New Group" -->

The new group is selected and ticked for conversion. New curves go into it.

## 5. Draw two curves

1. Press **2** to switch to the Draw sub-tool. The keys **1** to **5** switch sub-tools directly, without opening a
   pie; the **Q** pie has them too (Draw is on its right).
2. Click on the head where the lock starts, then keep clicking along its path. Each click places an anchor.
3. Press **Enter** (or right-click) to finish the curve.
4. Draw a second curve beside the first one, a short distance away.

![Two curves drawn on the head with the Draw sub-tool](img/gs-draw.png)
<!-- claims gs-draw: curves=2 walls=0 subtool=DRAW -->

A curve needs at least two anchors. A click off the head places the anchor on a plane facing the view.

Prefer to sketch? The Draw page of the HairMesh Menu has a **Freehand** mode: drag a stroke and it becomes a curve. See
[Tools](tools.md#draw).

## 6. Stretch a wall between them

1. Press **Esc** to go back to Select.
2. Click the first curve, then **Shift+click** the second one.
3. Press **Shift+RMB**. **Walls / Lock** is at the bottom left of the pie.

![The Shift+RMB pie with the two curves selected, Walls / Lock at the bottom left](img/gs-wall-pie.png)
<!-- claims gs-wall-pie: selected_curves=2 subtool=SELECT pie.SW="Walls / Lock" -->

4. Open **Walls / Lock** and click **Create Wall**.

![One wall stretched between the two curves](img/gs-wall.png)
<!-- claims gs-wall: curves=2 walls=1 walls_shown=1 -->

A wall now joins the two curves. It is selected: its grid shows in the orange of the selection. Neighboring walls form
a lock. Click the wall to see its settings in the Selection
tab of the HairMesh Menu (D): Bulge, Width Segments, Clean Topology, and more. The HairMesh Outliner and the sheets
list each wall by its default name, **Surface 1**, **Surface 2**…, the name the original application gives it.

## 7. Convert

1. Press **D** and open the **Conversion** tab. Your group is in the list, ticked.
2. Click **Convert**.
3. The new mesh appears, solid orange, with the **CONVERSION PREVIEW** banner at the top of the view. This is a
   preview: nothing is kept yet. The view is **Baked**: the wall and the curves are hidden while the mesh shows. Adjust
   the sliders (Loop Step, Deviation, Tips…) if you like: the preview follows.

![The preview of the conversion, in orange](img/gs-preview.png)
<!-- claims gs-preview: pending=1 confirmed=0 meshes_shown=1 color=orange view=baked walls_shown=0 -->

4. Click **Confirm** (the check mark next to Convert) to keep it, or **Cancel** (the X) to drop it. The confirmed mesh
   turns a light hair color, and the view stays **Baked**.

![The confirmed mesh in the Baked view](img/gs-confirmed.png)
<!-- claims gs-confirmed: pending=0 confirmed=1 meshes_shown=1 color=light view=baked walls_shown=0 -->

Convert, Confirm and Cancel are also in the tool header. While the preview is pending, they are in the Shift+RMB pie
with nothing selected too.

## 8. Live and Baked

Once there is a converted mesh (a pending preview counts), two buttons switch the view of the whole project:

- **Live** shows the walls and curves and hides the converted meshes. Use it to keep editing.
- **Baked** shows the converted meshes and hides the walls and curves they came from.

![The Live view: the wall and the curves again, the converted mesh hidden](img/gs-live.png)
<!-- claims gs-live: view=live meshes_shown=0 walls_shown=1 curves=2 -->

You find them in the tool header, as icons next to Convert in the Conversion tab, and in the Shift+RMB pie with
nothing selected.

The confirmed mesh does not change when you edit the curves. After an edit, the Conversion tab tells you how many
meshes changed. Click **Convert** again: only those are rebuilt.

## Next steps

- [Workflow](workflow.md): groups, guides, crowns, symmetry, modifiers and more, task by task.
- [Tools](tools.md): every sub-tool, menu and pie.
- [Shortcuts](shortcuts.md): every key.
- [Troubleshooting](troubleshooting.md): when something does not respond, and how to report a bug.

Want to study a finished model? See [Try the example project](#try-the-example-project).
