"""Confirmation dialog factories for destructive panel actions.

Each factory builds a ``QMessageBox``, runs it, and returns the answer.
Most return ``True`` on accept and ``False`` on cancel.
:func:`confirm_close_with_unsaved_changes` returns a
:class:`CloseUnsavedChoice`. Nothing else happens.

All Qt imports go through :mod:`nsl.compat` per the project Qt boundary.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from nsl import compat

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Locked dialog strings - single source of truth
# ---------------------------------------------------------------------------

REMOVE_FOLDER_TEXT = (
    "Remove this Plugins Folder? Plugins inside it will no longer load on "
    "next Nuke restart."
)
"""Single string, no interpolation."""

DELETE_LOADOUT_TEXT_TEMPLATE = (
    "Delete the Loadout {name!r}? Its file will be removed from disk and "
    "cannot be recovered."
)

# Reset returns the Global Plugins to the state ``NSL_GLOBAL_LOADOUTS``
# resolves to. User-added Plugins and the Global Loadout stay untouched.
RESET_GLOBAL_TEXT_TEMPLATE = (
    "Reset {n} Global Plugin{plural} in {loadout!r} Loadout to Global "
    "defaults? Your user-added Plugins won't be affected, and the "
    "Global Loadout itself is not modified."
)

# For quitting Nuke with more than one dirty Loadout. A separate surface
# from the per-Loadout close dialog.
QUIT_TEXT_TEMPLATE = "You have unsaved changes in {names}. Quit anyway?"

REVERT_LOADOUT_TEXT_TEMPLATE = (
    "Revert {name!r} Loadout? Your unsaved edits will be discarded."
)

# For a save while a Global Loadout copy already exists in the NSL Global
# folder. The save only stages the folder, so the text explains the copy.
GLOBAL_LOADOUT_STAGED_TEXT = (
    "Your Global Loadout was staged. Copy the staged folder into the NSL "
    "Global folder to take effect on the next launch. You may delete the "
    "staged copy afterwards, or keep it for future edits."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_loadout_names(names: List[str]) -> str:
    """Render a list of loadout names for inline use in dialog text.

    * ``[]`` → ``""``
    * ``["A"]`` → ``"'A'"``
    * ``["A", "B"]`` → ``"'A' and 'B'"``
    * ``["A", "B", "C"]`` → ``"'A', 'B', and 'C'"`` (Oxford comma)
    """
    if not names:
        return ""
    quoted = [f"'{n}'" for n in names]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} and {quoted[1]}"
    return ", ".join(quoted[:-1]) + f", and {quoted[-1]}"


def confirm_quit_with_unsaved_changes(
    parent: Optional[QtWidgets.QWidget],
    loadout_names: List[str],
) -> bool:
    """Two-button prompt for quitting Nuke with dirty loadouts.

    Returns ``True`` on "Quit anyway". Cancel takes the default and the
    escape binding, so a stray Enter or Esc never discards work.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslQuitUnsavedChanges")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Unsaved changes")
    box.setText(QUIT_TEXT_TEMPLATE.format(names=_format_loadout_names(loadout_names)))

    quit_button = box.addButton("Quit anyway", QtWidgets.QMessageBox.DestructiveRole)
    cancel_button = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_button)
    box.setEscapeButton(cancel_button)

    _exec_message_box(box)
    return box.clickedButton() is quit_button


def _exec_message_box(box: QtWidgets.QMessageBox) -> int:
    """Call ``exec`` on a ``QMessageBox`` across PySide2 and PySide6.

    PySide2 ships only ``exec_``, so this goes through
    :func:`compat.run_modal`.
    """
    return int(compat.run_modal(box))


# ---------------------------------------------------------------------------
# Public factory: remove Plugins Folder
# ---------------------------------------------------------------------------


def confirm_remove_folder(
    parent: Optional[QtWidgets.QWidget],
    folder_path: str,
) -> bool:
    """Show the remove-Plugins-Folder confirmation.

    Returns ``True`` on Remove. Cancel is the default button. The folder
    path goes in the informative text, so the locked body stays short.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslRemovePluginsFolder")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Remove Plugins Folder")
    box.setText(REMOVE_FOLDER_TEXT)
    if folder_path:
        box.setInformativeText(folder_path)

    remove_button = box.addButton("Remove", QtWidgets.QMessageBox.AcceptRole)
    cancel_button = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_button)
    box.setEscapeButton(cancel_button)

    _exec_message_box(box)
    return box.clickedButton() is remove_button


# ---------------------------------------------------------------------------
# Public factory: delete Loadout
# ---------------------------------------------------------------------------


def confirm_delete_loadout(
    parent: Optional[QtWidgets.QWidget],
    loadout_name: str,
) -> bool:
    """Show the delete-Loadout confirmation.

    Returns ``True`` on Delete. Cancel is the default button.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslDeleteLoadout")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Delete Loadout")
    box.setText(DELETE_LOADOUT_TEXT_TEMPLATE.format(name=loadout_name))

    delete_button = box.addButton("Delete", QtWidgets.QMessageBox.AcceptRole)
    cancel_button = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_button)
    box.setEscapeButton(cancel_button)

    _exec_message_box(box)
    return box.clickedButton() is delete_button


def show_global_loadout_staged(
    parent: Optional[QtWidgets.QWidget],
    staged_path: str,
    global_dir: str,
) -> None:
    """Info box after a ``Global_Loadout`` staging save.

    Names where the file landed and where to copy it. There is no choice
    to make, so it has one OK button.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslGlobalLoadoutStaged")
    box.setIcon(QtWidgets.QMessageBox.Information)
    box.setWindowTitle("Global Loadout staged")
    box.setText(GLOBAL_LOADOUT_STAGED_TEXT)
    box.setInformativeText(
        f"Staged at: {staged_path}\nCopy into: {global_dir}"
    )
    box.addButton("OK", QtWidgets.QMessageBox.AcceptRole)
    _exec_message_box(box)


def confirm_revert_loadout(
    parent: Optional[QtWidgets.QWidget],
    loadout_name: str,
) -> bool:
    """Show the revert-Loadout confirmation.

    Revert drops unsaved edits and reloads the Loadout from disk.
    Returns ``True`` on Revert. Cancel is the default button.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslRevertLoadout")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Revert Loadout")
    box.setText(REVERT_LOADOUT_TEXT_TEMPLATE.format(name=loadout_name))

    revert_button = box.addButton("Revert", QtWidgets.QMessageBox.AcceptRole)
    cancel_button = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_button)
    box.setEscapeButton(cancel_button)

    _exec_message_box(box)
    return box.clickedButton() is revert_button


# ---------------------------------------------------------------------------
# Public factory: Reset Global Plugins to Default (bulk)
# ---------------------------------------------------------------------------


def confirm_reset_global_to_default(
    parent: Optional[QtWidgets.QWidget],
    affected_count: int,
    loadout_name: str,
) -> bool:
    """Show the Reset Global Plugins to Default (bulk) confirmation.

    Bulk only. The per-Plugin right-click reset has no dialog, because
    the right-click menu is itself the confirmation.

    The body gives a count and never lists the Plugins. Returns ``True``
    on Reset. Cancel takes the default and the escape binding.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslResetGlobalToDefault")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Reset Global Plugins to Default")
    plural = "" if affected_count == 1 else "s"
    box.setText(
        RESET_GLOBAL_TEXT_TEMPLATE.format(
            n=affected_count, plural=plural, loadout=loadout_name,
        )
    )

    reset_button = box.addButton("Reset", QtWidgets.QMessageBox.AcceptRole)
    cancel_button = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_button)
    box.setEscapeButton(cancel_button)

    _exec_message_box(box)
    return box.clickedButton() is reset_button


# ---------------------------------------------------------------------------
# Public factory: Close panel with unsaved changes
# ---------------------------------------------------------------------------


class CloseUnsavedChoice(Enum):
    """User's choice from :func:`confirm_close_with_unsaved_changes`."""

    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


def confirm_close_with_unsaved_changes(
    parent: Optional[QtWidgets.QWidget],
    loadout_name: str,
    *,
    is_custom: bool = False,
) -> CloseUnsavedChoice:
    """Three-button prompt for closing the panel with a dirty Loadout.

    A user Loadout offers Save, Don't Save and Cancel, and Save writes to
    the existing file. Custom offers Save As instead.

    Custom is in-memory only, so no on-disk change is at risk. What is at
    stake is that a Loadout left on Custom loads no plugins on the next
    Nuke start.

    Default and Escape both bind to Cancel.
    """
    box = QtWidgets.QMessageBox(parent)
    box.setObjectName("nslCloseUnsavedChanges")
    box.setIcon(QtWidgets.QMessageBox.Warning)
    if is_custom:
        box.setWindowTitle("Leaving Custom")
        box.setText("Custom is never saved to disk.")
        box.setInformativeText(
            "Save As or Select a Loadout to load changes on restart."
        )
        save_label = "Save As…"
    else:
        box.setWindowTitle("Unsaved changes")
        box.setText(
            f"Save changes to {loadout_name} before closing?"
        )
        box.setInformativeText("Any unsaved changes will be lost.")
        save_label = "Save"
    save_button = box.addButton(save_label, QtWidgets.QMessageBox.AcceptRole)
    discard_button = box.addButton(
        "Don't Save", QtWidgets.QMessageBox.DestructiveRole
    )
    cancel_button = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(cancel_button)
    box.setEscapeButton(cancel_button)

    _exec_message_box(box)
    clicked = box.clickedButton()
    if clicked is save_button:
        return CloseUnsavedChoice.SAVE
    if clicked is discard_button:
        return CloseUnsavedChoice.DISCARD
    return CloseUnsavedChoice.CANCEL

