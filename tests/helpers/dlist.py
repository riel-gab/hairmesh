"""The D (the HairMesh Menu pop-up) as one flat list: its sections as the D draws them (tool.popup_sections), one
after the other, each a title and its rows through tool._list_items (icon-only buttons named, toggles as checkboxes),
then Hide Selected / Hide Unselected / Reveal Hidden (the D's Visibility pie). Since lot 1.2a Object > HairMesh lists actions only (the owner's
decision; tests/test_blender_object_menu.py): the tests that check an action or a text is reachable in the D read it
here."""


def d_items(tool, context):
    items = []
    for title, icon, rows in tool.popup_sections(context):
        if items:
            items.append(('sep',))
        items.append(('label', title, icon))
        items.extend(tool._list_items(rows))
    items += [('sep',), ('label', 'Visibility', 'HIDE_OFF'), ('op', 'hairmesh.hide_selected', 'Hide Selected', 'HIDE_ON', {}),
              ('op', 'hairmesh.hide_unselected', 'Hide Unselected', 'HIDE_ON', {}), ('op', 'hairmesh.reveal_hidden', 'Reveal Hidden', 'HIDE_OFF', {})]
    return items
