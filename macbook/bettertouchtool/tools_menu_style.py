"""Default Tools action styling, matching the parent Tools button."""

from btt_touchbar_folder_to_floating_submenu import base_menu_config, rtf_label


def tools_button_config(identifier, label):
    config = base_menu_config(identifier, label)
    width = max(100, round(len(label) * 25 * 0.57 + 24))
    config.update(BTTMenuItemMinWidth=width, BTTMenuItemMaxWidth=width,
                  BTTMenuItemMinHeight=40, BTTMenuItemMaxHeight=40,
                  BTTMenuAttributedText=rtf_label(label), BTTMenuItemText=label,
                  BTTMenuTextMinimumScaleFactor=1)
    return config
