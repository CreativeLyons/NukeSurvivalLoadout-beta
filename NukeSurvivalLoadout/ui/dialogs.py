"""Confirmation dialog factories for destructive panel actions.

Each ``QMessageBox`` factory returns ``True`` when the user accepts the
destructive action and ``False`` when they cancel (or, for
:func:`confirm_close_with_unsaved_changes`, a :class:`CloseUnsavedChoice`).
No side effects beyond constructing and exec-ing the dialog.

* :func:`confirm_remove_folder` - remove-folder confirmation.
* :func:`confirm_delete_loadout` - delete-Loadout confirmation.
* :func:`confirm_revert_loadout` - discard unsaved edits and reload from disk.
* :func:`confirm_reset_global_to_default` - bulk Reset Global Plugins to
  Default confirmation (toolbar action).
* :func:`confirm_close_with_unsaved_changes` - the panel-close "save before
  discarding?" surface (the only such prompt; Nuke's quit path can't be
  reliably intercepted and Plugin toggles are trivially reproducible).

All Qt imports go through :mod:`NukeSurvivalLoadout.compat` per the project Qt boundary.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from NukeSurvivalLoadout import compat

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Locked dialog strings - single source of truth
# ---------------------------------------------------------------------------
#
# Kept as module-level constants so every caller renders the same text
# the QMessageBox would show.

REMOVE_FOLDER_TEXT = (
    "Remove this Plugins Folder? Plugins inside it will no longer load on "
    "next Nuke restart."
)
"""Single string, no interpolation."""

# The wording is open ("with confirmation"); we mirror the remove-folder
# shape rather than invent unvetted prose.
DELETE_LOADOUT_TEXT_TEMPLATE = (
    "Delete the Loadout {name!r}? Its file will be removed from disk and "
    "cannot be recovered."
)

# Reset Global Plugins to Default restores all Global Plugins to
# whatever state NSL_GLOBAL_LOADOUTS resolves to. It is scoped strictly to
# Global Plugins: it does not touch user-added Plugins or their state,
# nor the Global Loadout itself. We pin a short body that names what gets reset
# and what stays untouched.
RESET_GLOBAL_TEXT_TEMPLATE = (
    "Reset {n} Global Plugin{plural} in {loadout!r} Loadout to Global "
    "defaults? Your user-added Plugins won't be affected, and the "
    "Global Loadout itself is not modified."
)

# Quit-with-unsaved-changes - fired when the user closes Nuke (or the panel
# host with multiple dirty loadouts) and at least one Loadout has unsaved
# edits. Distinct surface from the per-Loadout close dialog
# (:func:`confirm_close_with_unsaved_changes`).
QUIT_TEXT_TEMPLATE = "You have unsaved changes in {names}. Quit anyway?"

# Revert discards in-memory edits on the active Loadout, reloading its
# on-disk state. Destructive (unsaved work is lost) so the dialog is short
# and explicit. No locked wording to pin. A tight named-action + consequence
# pair. ``{name!r}`` renders the Loadout name in single quotes (Python repr
# style) so the target stays visually distinct from the surrounding
# sentence.
REVERT_LOADOUT_TEXT_TEMPLATE = (
    "Revert {name!r} Loadout? Your unsaved edits will be discarded."
)

# Case-B Global_Loadout staging save (a Global Loadout copy already lives
# in the NSL Global folder, so the user-land save is a staging step, not
# an activatable loadout). Plain info, one OK button.
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
    """Two-button prompt fired when Nuke is quitting with dirty loadouts.

    Returns ``True`` if the user clicks "Quit anyway", ``False`` on Cancel
    or window dismiss. Cancel is both the default + escape binding so an
    inadvertent Enter / Esc never discards work.
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

    Delegates to :func:`compat.run_modal`, the panel-wide shim (PySide2
    ships only ``exec_``; PySide6 ships ``exec``).
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

    Body text:
        *"Remove this Plugins Folder? Plugins inside it will no longer load
        on next Nuke restart."*

    Buttons:
        * **Remove** - accept role; returns ``True``.
        * **Cancel** - reject role; returns ``False`` (default button).

    The folder path is surfaced in the dialog's informative text so the
    user sees which folder they are about to remove without bloating the
    locked primary body.
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

    The wording is not pinned, so we mirror the remove-folder shape: short
    question body, Cancel / Delete buttons, default Cancel.

    Buttons:
        * **Delete** - accept role; returns ``True``.
        * **Cancel** - reject role; returns ``False`` (default button).
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
    """Info box after a case-B ``Global_Loadout`` staging save.

    Names where the file landed and where to copy it; no choice to make,
    so a single OK button.
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

    Revert discards unsaved in-memory edits and reloads the Loadout from
    disk. Destructive (work is lost), so the same Cancel / accept-role
    pattern as the delete dialog applies, with the action button labelled
    ``Revert`` so the user reads the consequence before clicking.

    Buttons:
        * **Revert** accept role; returns ``True``.
        * **Cancel** reject role; returns ``False`` (default button).
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

    Bulk granularity only: the per-Plugin (right-click) reset path has no
    confirmation dialog (the right-click menu is the confirmation surface
    itself).

    The body names the affected count, the active Loadout, and the two
    invariants: user-added Plugins are untouched, and the Global Loadout itself
    is read-only. The dialog does not enumerate which Global Plugins will be
    reset - for a small count that's redundant with the grid, and for a
    large count the list would bloat the dialog past usefulness.

    Buttons:
        * **Reset** - accept role; returns ``True``.
        * **Cancel** - reject role; returns ``False`` (default button).

    Default and Escape both bound to Cancel so an inadvertent Enter or
    Esc cannot fire the reset.
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
    """Three-button prompt fired when the panel's Close button is clicked
    against a dirty active Loadout.

    Body wording depends on the slot kind:

    * **User Loadout** - *"Save changes to ``<name>`` before closing?"*
      Save / Don't Save / Cancel. Save commits to the existing file.
    * **Custom**: warns that Custom is an in-session-only wildcard that
      will NOT load any plugins on the next Nuke restart, and that the user
      should Save As a named Loadout (or Cancel and switch to one) to have
      plugins load. Save As… / Don't Save / Cancel. Custom is in-memory
      only; the only commit path is a new named file. The consequence that
      matters is "leaving it on Custom loads nothing next launch", not
      merely "changes lost": Custom never persists, so there are no on-disk
      changes to lose; what's at stake is whether anything loads at all.

    The consequence line is included so the user reads the cost of *Don't
    Save* before clicking.

    Default + Escape both bind to Cancel so an inadvertent Enter / Esc
    never discards work.
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

