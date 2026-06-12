"""Plugins Folder management card - NSL Loadout Panel, left-top region.

Card anatomy (top to bottom):

1. **Button row** - ``Add Plugins Folder`` + ``Rescan Plugins`` side by side.
2. **Priority indicator** - ``↑ priority`` strip; shown only when there are
   ≥2 folders (no priority to communicate with one).
3. **Folder list** - vertical stack of :class:`FolderRow` widgets inside a
   scroll area. Each row carries a path label, eye toggle (visual filter
   only, per-session), Select button, drag handle, ▲ / ▼ reorder arrows,
   and a Remove ✕ button.
4. **Empty-state label** - shown when there are no user-added folders.

The card lists every user-added Plugins Folder plus a synthetic
``.../Global Plugins`` row pinned to the bottom whenever the Global
resolver produces a non-empty layer. The Global row shows a friendly
label rather than the raw path, and its drag handle + ▲ / ▼ / ✕ controls
render permanently disabled (the row cannot be reordered or removed - only
the Global configuration controls that). It still supports the eye
toggle and Select-all so artists can hide / engage the Global pills
like any other folder. The goal is to surface the existence of Global
plugins without cluttering the row with a long path.

The widget never touches the filesystem and never imports ``nuke``. It is a
**view** - it emits intent signals and consumes a list of
:class:`FolderEntry` records. Qt imports go exclusively through
:mod:`NukeSurvivalLoadout.compat`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.ui._buttons import HybridTextButton, install_clickable_cursor

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


__all__ = [
    "Health",
    "FolderEntry",
    "FolderRow",
    "FolderCard",
    "main",
]


# ---------------------------------------------------------------------------
# Shared row geometry
# ---------------------------------------------------------------------------

# Stripe height for the placeholder backdrop of an empty folder list. Sized
# to match the natural :class:`FolderRow` sizeHint so a real row drops into
# exactly one stripe (stripes are sized to fit the rows, not the inverse).
#
# Why 28 px: FolderRow is a QHBoxLayout with 22 px chrome buttons + the
# `(6, 3, 10, 3)` content margins → 22 + 3 + 3 = 28 px natural height. The
# row uses no ``setFixedHeight`` and lets Qt compute the height from
# sizeHint, which lands at 28 px under HybridStyle.
_FOLDER_ROW_HEIGHT_PX = 28


# ---------------------------------------------------------------------------
# Health vocabulary
# ---------------------------------------------------------------------------


class Health(Enum):
    """Folder health states.

    The labels are the strings used when describing each state.
    Glyphs and colours are picked to read well against the typical Nuke
    panel dark background; production styling may tune the exact hexes but
    the glyphs are fixed.
    """

    HEALTHY = ("Healthy", "✓", "#2ecc71")           # ✓ green
    UNREACHABLE = ("Unreachable", "!", "#e74c3c")        # ! red
    PERMISSION_DENIED = ("Permission denied", "\U0001f512", "#f1c40f")  # 🔒 amber
    EMPTY = ("Empty", "∅", "#95a5a6")               # ∅ muted grey

    def __init__(self, label: str, glyph: str, colour: str) -> None:
        self.label = label
        self.glyph = glyph
        self.colour = colour


@dataclass
class FolderEntry:
    """One user-added Plugins Folder as rendered by :class:`FolderRow`.

    Attributes:
        path: Absolute folder path. Identity for the row (signals carry it).
        health: Last-known :class:`Health` state from the most recent scan.
        visible: Eye-toggle state. ``True`` = visible in grid (default).
            Per-session, panel-local - never persisted, never affects
            Plugin scan or enable/disable state.
        is_global: When True, this is the synthetic Global Plugins row
            (the resolved Global layer from the ``<nsl_root>/Global/``
            chain head).
            The row is pinned-to-bottom, renders the friendly
            ``.../Global Plugins`` label, and suppresses the drag handle +
            ▲ / ▼ / ✕ controls. Only the eye toggle and Select-all
            stay active. ``path`` carries
            :data:`NukeSurvivalLoadout.constants.GLOBAL_PLUGINS_FOLDER_SENTINEL` so the
            wiring layer can recognise the row.
        tooltip_path: Resolved Global plugins dir for the row tooltip
            (the friendly label hides the path from the row itself; the
            tooltip carries the full path). Empty for user rows - their
            ``path`` IS the tooltip.
    """

    path: str
    health: Health = Health.HEALTHY
    visible: bool = True
    is_global: bool = False
    tooltip_path: str = ""


# ---------------------------------------------------------------------------
# Remove-confirmation dialog factory
# ---------------------------------------------------------------------------


def _default_confirm_remove_folder(parent: "QtWidgets.QWidget", path: str) -> bool:
    """Confirm-remove dialog used when ``NukeSurvivalLoadout.ui.dialogs`` is absent.

    Returns ``True`` if the user confirmed the remove, ``False`` otherwise
    (including Cancel or dialog-dismissed).

    The card prefers ``NukeSurvivalLoadout.ui.dialogs.confirm_remove_folder``
    when that module exists; this fallback keeps the card usable standalone.
    """
    try:  # pragma: no cover
        from NukeSurvivalLoadout.ui import dialogs as _dialogs  # type: ignore[attr-defined]

        confirm = getattr(_dialogs, "confirm_remove_folder", None)
        if callable(confirm):
            return bool(confirm(parent, path))
    except ImportError:
        pass
    except Exception:  # pragma: no cover - defensive: never crash the panel
        pass

    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle("Remove Plugins Folder")
    box.setText(
        "Remove this Plugins Folder? Plugins inside it will no longer "
        "load on next Nuke restart."
    )
    box.setInformativeText(path)
    box.setStandardButtons(
        QtWidgets.QMessageBox.Cancel | QtWidgets.QMessageBox.Yes
    )
    yes_btn = box.button(QtWidgets.QMessageBox.Yes)
    if yes_btn is not None:
        yes_btn.setText("Remove")
    box.setDefaultButton(QtWidgets.QMessageBox.Cancel)

    # PySide2 (Nuke 13-15) ships only .exec_(); PySide6 ships .exec().
    # compat.run_modal absorbs the difference.
    result = compat.run_modal(box)
    return result == QtWidgets.QMessageBox.Yes


# ---------------------------------------------------------------------------
# FolderRow - one row in the folder list
# ---------------------------------------------------------------------------


class FolderRow(QtWidgets.QFrame):
    """A single folder row inside :class:`FolderCard`.

    Visuals follow the Claude Design ``DirectoryList.jsx`` prototype:
    grip (``⋮⋮``) · two-tone path (parent dim, last segment bold white) ·
    ``Select all`` text · eye-toggle SVG · ``▲ ▼`` · ``✕``. Controls sit
    at 0.6 opacity by default and pop to 1.0 on row hover (no animation -
    instant toggle).

    Emits user-intent signals only; the card owns the model and reacts by
    mutating the entry list and re-laying out the rows.
    """

    visibility_toggled = QtCore.Signal(str, bool)   # (path, visible)
    select_requested = QtCore.Signal(str)           # (path,)
    deselect_requested = QtCore.Signal(str)         # (path,)
    remove_requested = QtCore.Signal(str)           # (path,)
    move_up_requested = QtCore.Signal(str)          # (path,)
    move_down_requested = QtCore.Signal(str)        # (path,)
    open_folder_requested = QtCore.Signal(str)      # (path,) - right-click "Open Folder"
    health_clicked = QtCore.Signal(str)             # (path,) - kept for API stability; not emitted by row chrome (design has no health glyph)
    drag_started = QtCore.Signal(str)               # (path,)
    drag_moved = QtCore.Signal(str, "QPoint")       # (path, global_pos) - fires during drag
    drag_released_over = QtCore.Signal(str, "QPoint")  # (path, global_pos)

    def __init__(
        self,
        entry: FolderEntry,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FolderRow")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setProperty("nslPath", entry.path)
        self.setProperty("rowHover", False)
        self.setProperty("rowEven", False)
        self.setProperty("panicDimmed", False)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        # Row height is the natural sizeHint (Qt's normal computation
        # from the QHBoxLayout's children + content margins). The
        # placeholder stripes are sized to match this height so a real
        # row drops into exactly one stripe - stripes shrink to fit
        # rows, never the inverse.
        self._entry = entry
        # Panic-mode dim flag - set via ``set_panic_dimmed`` from the
        # card. Global rows ignore it (they keep loading on restart
        # during panic). In panic mode every folder that is NOT the
        # Global Plugins row is disabled / struck through so it reads as
        # ignored on next restart, while the Global row remains.
        self._panic_dimmed: bool = False
        self._build()
        self._apply_row_qss()
        self._refresh_from_entry()

    # NOTE: there is no row-level selection - folders are managed via
    # their per-row controls (eye / Select all / arrows / remove);
    # selection lives on the plugin pills elsewhere in the panel.

    def contextMenuEvent(self, event):
        """Right-click → context menu with **Open Folder** (reveal the folder
        in the OS file browser).

        Suppressed on the synthetic Global Plugins row: its ``path`` is the
        :data:`GLOBAL_PLUGINS_FOLDER_SENTINEL`, not a real directory (the
        resolved dir lives in the row tooltip instead). Wrapped in
        try/except so a menu-build failure can't crash the panel.
        """
        try:
            if self._entry.is_global:
                return
            menu = QtWidgets.QMenu(self)
            open_action = menu.addAction("Open Folder")
            open_action.triggered.connect(
                lambda *_: self.open_folder_requested.emit(self._entry.path)
            )
            # Copy Path is handled locally - putting a string on the clipboard
            # needs no registry path-resolution, so the row does it directly
            # (no signal round-trip like Open Folder needs).
            copy_action = menu.addAction("Copy Path")
            copy_action.triggered.connect(lambda *_: self._copy_path_to_clipboard())
            compat.run_modal(menu, event.globalPos())
        except Exception:  # pragma: no cover - defensive: never crash the panel
            pass

    def _copy_path_to_clipboard(self) -> None:
        """Put this row's folder path on the system clipboard. Never raises -
        a clipboard failure must not crash the panel."""
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(self._entry.path)
        except Exception:  # pragma: no cover - defensive
            pass

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        # JSX `.dirrow`: grid 18px / 1fr / auto, gap 8px, padding 8 10 8 6.
        # Vertical padding tightened from 8 → 3 (still on the 2/4/6/8/12/16
        # spacing scale) so the row hugs the 22 px ctrl buttons. The icon
        # buttons used to centre with a 4-5 px gap above/below, making the
        # hover paint read short; now they sit near-flush with the row edges
        # and `Select all`'s hover-paint height matches the icons (paired
        # with `setFixedHeight(22)` on the text-variant Select button below).
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 10, 3)
        layout.setSpacing(8)
        # Enforce a minimum row width so the right-side icon cluster
        # (Select / Eye / ▲ / ▼ / ✕) stays visible when the folder/side
        # splitter narrows the folder card area. Sum: drag handle ~16 +
        # ctrls cluster ~122 (five buttons 22-26px + 2px spacings) +
        # layout spacings 16 + paddings 16 + a small path budget ~20 =
        # ~190. The path label is Expanding with ``setMinimumWidth(0)``
        # so it squeezes all the way down to its leading ellipsis;
        # everything past that minimum protects the icons. The icon
        # buttons must stay visible inside the window and tied to the
        # right side of the folder card area.
        self.setMinimumWidth(190)

        # 1. Grip - vertical-dots glyph, opacity controlled by row hover.
        self._drag_handle = _DragHandle(self)
        layout.addWidget(self._drag_handle)

        # 2. Path label - two-tone rich text (parent dim, last seg bold white).
        # Elide rule: when the row is too narrow to show the full path,
        # the LEFT side of the path is hidden behind a leading ``…`` so
        # the most informative tail (parent folder + leaf) stays visible
        # against the right edge. Tooltip carries the full path
        # verbatim. ``setMinimumWidth(0)`` lets the label shrink below
        # its natural sizeHint; without it Qt refuses to compress it
        # past the full-text width and the row blows past the panel.
        self._path_label = _PathLabel("", self)
        self._path_label.setObjectName("PathLabel")
        self._path_label.setTextFormat(QtCore.Qt.RichText)
        self._path_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        # The label is selectable, so by default it would intercept the
        # right-click with its own Copy / Select-all menu - and the label
        # spans most of the row width, so that would shadow the row's
        # "Open Folder" menu almost everywhere. NoContextMenu makes the
        # label defer context-menu handling to its parent (this FolderRow),
        # so right-clicking anywhere on the row - label included - opens the
        # folder menu. Text stays selectable for drag-select + Cmd+C.
        self._path_label.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        self._path_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
        self._path_label.setMinimumWidth(0)
        font = self._path_label.font()
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setFamily("SF Mono")
        font.setPointSize(10)
        self._path_label.setFont(font)
        layout.addWidget(self._path_label, 1)

        # 3. Controls cluster - Select all · eye · ▲ ▼ · ✕. JSX wraps
        #    these in `.ctrls { opacity: 0.6 }`; we apply the dim/active
        #    treatment per-control via the row-hover property selector.
        #
        # Size policy: ``Fixed`` horizontally so the cluster never gets
        # squeezed off the right edge of the row when the panel narrows.
        # The path label (between drag handle and ctrls) is the only
        # element that shrinks; it elides from the left so the most
        # informative tail ("…/plugins") stays visible. Without this
        # pin the default ``Preferred`` policy let the cluster shrink
        # below the sum of its fixed-size children and clip ▲ ▼ ✕ off
        # the right side of the visible row.
        self._ctrls = QtWidgets.QWidget(self)
        self._ctrls.setObjectName("Ctrls")
        self._ctrls.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed,
        )
        ctrls_layout = QtWidgets.QHBoxLayout(self._ctrls)
        ctrls_layout.setContentsMargins(0, 0, 0, 0)
        ctrls_layout.setSpacing(2)

        # 3a. Select all - icon variant by default so the per-row controls
        #     read as one clean row of icons (drag · select-all · eye · ▲
        #     · ▼ · ✕) instead of one text label sitting beside five
        #     glyphs. Variants:
        #         * ``D`` (default) - classic mouse-cursor arrow. Reads
        #           as "click to select"; the icon visually *is* the
        #           user's pointer.
        #         * ``C`` - checked-box glyph; the most direct visual
        #           read of "select all".
        #         * ``B`` - lines-with-check glyph; an alternative shape.
        #         * ``TEXT`` (or any other value) - original "Select all"
        #           text button (the canonical-JSX treatment).
        self._select_variant = os.environ.get("NSL_SELECT_ICON", "D").upper()
        if self._select_variant in ("B", "C", "D"):
            self._select_button = QtWidgets.QToolButton(self._ctrls)
            self._select_button.setObjectName("SelectButton")
            # autoRaise OFF - we drive the icon's colour from the row's
            # ``rowHover`` property (same model as the eye + ▲ / ▼ / ✕)
            # so the select icon brightens to white the moment the row
            # is hovered, not just on direct cursor-over. autoRaise also
            # paints Qt's own subtle hover background that diverges from
            # the ``rgba(255, 255, 255, 15)`` pad the other ctrls use;
            # leaving it off lets the row-scoped QSS own that pad.
            self._select_button.setAutoRaise(False)
            self._select_button.setCheckable(True)  # stays orange once clicked
            self._select_button.setFixedSize(QtCore.QSize(22, 22))
            self._select_button.setIconSize(QtCore.QSize(14, 14))
            # Initial icon paint - driven by ``_sync_select_visuals``
            # later, but seeded here so the first render has the right
            # colour. ``_sync_select_visuals`` overrides on every
            # row-hover / checked-state change.
            self._select_button.setIcon(self._make_select_icon(_SELECT_REST_COLOR))
            self._select_button.toggled.connect(self._on_select_toggled)
        else:
            self._select_button = QtWidgets.QPushButton("Select all", self._ctrls)
            self._select_button.setObjectName("SelectButton")
            self._select_button.setFlat(True)
            self._select_button.setCursor(QtCore.Qt.ArrowCursor)
            # Pin height to match the 22 px icon ctrls so its hover-paint
            # rect is identical - without this the text button stretches
            # vertically to the layout's full height and the hover bg
            # reads noticeably taller than the eye / ▲ / ▼ / × icons.
            self._select_button.setFixedHeight(22)
        self._select_button.setToolTip(
            "Select every visible Plugin from this folder "
            "(replaces current selection)"
        )
        # Click semantics: the icon variant is checkable, so the
        # button's checked state mirrors a "selection engaged" toggle.
        # Click while unchecked → select. Click while checked → emit a
        # ``deselect_requested`` so the wiring layer can subtract the
        # folder's pills from the current selection (clicking an already-
        # engaged folder deselects it). The text variant is non-checkable,
        # so it always emits ``select_requested``.
        self._select_button.clicked.connect(self._on_select_clicked)
        ctrls_layout.addWidget(self._select_button)

        # 3b. Eye toggle - SVG eye / eye-off painted via QPainter paths.
        self._eye_button = QtWidgets.QToolButton(self._ctrls)
        self._eye_button.setObjectName("EyeToggle")
        self._eye_button.setCheckable(True)
        self._eye_button.setAutoRaise(True)
        self._eye_button.setFixedSize(QtCore.QSize(22, 22))
        self._eye_button.setIconSize(QtCore.QSize(14, 14))
        self._eye_button.toggled.connect(self._on_eye_toggled)
        # Panel-wide interactive cursor - see ``install_clickable_cursor``
        # docstring. Applied to every per-row control so the folder card
        # reads as the same interactive vocabulary as the rest of the
        # panel's affordances.
        install_clickable_cursor(self._eye_button)
        ctrls_layout.addWidget(self._eye_button)

        # 3c. ▲ ▼ - flat text glyphs.
        self._up_button = QtWidgets.QPushButton("▲", self._ctrls)
        self._up_button.setObjectName("MoveUp")
        self._up_button.setFlat(True)
        self._up_button.setFixedSize(QtCore.QSize(22, 22))
        self._up_button.setToolTip("Move up (increase priority)")
        self._up_button.clicked.connect(
            lambda: self.move_up_requested.emit(self._entry.path)
        )
        # ▲ is disabled on the topmost row; ▼ on the bottommost. The
        # filter inside ``install_clickable_cursor`` swaps to arrow on
        # disable, so the boundary rows read as inert instead of inviting.
        install_clickable_cursor(self._up_button)
        ctrls_layout.addWidget(self._up_button)

        self._down_button = QtWidgets.QPushButton("▼", self._ctrls)
        self._down_button.setObjectName("MoveDown")
        self._down_button.setFlat(True)
        self._down_button.setFixedSize(QtCore.QSize(22, 22))
        self._down_button.setToolTip("Move down (decrease priority)")
        self._down_button.clicked.connect(
            lambda: self.move_down_requested.emit(self._entry.path)
        )
        install_clickable_cursor(self._down_button)
        ctrls_layout.addWidget(self._down_button)

        # 3d. ✕ remove - flat text, hover turns red. Slightly wider than
        # the other ctrls so the bumped-up glyph has breathing room.
        self._remove_button = QtWidgets.QPushButton("✕", self._ctrls)
        self._remove_button.setObjectName("RemoveButton")
        self._remove_button.setFlat(True)
        self._remove_button.setFixedSize(QtCore.QSize(26, 22))
        self._remove_button.setToolTip("Remove this Plugins Folder")
        self._remove_button.clicked.connect(
            lambda: self.remove_requested.emit(self._entry.path)
        )
        install_clickable_cursor(self._remove_button)
        ctrls_layout.addWidget(self._remove_button)

        layout.addWidget(self._ctrls)

        # Global Plugins row - pinned-to-bottom synthetic for the
        # resolved Global layer. The icons stay visible
        # for visual symmetry with user rows; only their `enabled`
        # state flips off so the row reads as "same vocabulary, just
        # not actionable here." Affected controls:
        #   * drag handle - Global is always lowest priority, never
        #     reorderable by the user.
        #   * ▲ / ▼ - same reason; can't move it past user rows.
        #     (``_refresh_arrow_enablement`` also disables these via
        #     ``set_can_move_up/down(False)`` on every refresh - the
        #     direct disable here covers the pre-first-refresh window.)
        #   * ✕ - only the Global configuration controls Global
        #     (the ``<nsl_root>/Global/`` folder convention,
        #     ``NSL_GLOBAL_PLUGIN_DIRS``, or both); the panel can't
        #     remove it.
        # The eye toggle and Select-all stay enabled so artists can
        # still hide / engage Global pills like any other folder.
        # Surfacing the row's existence while keeping row-icon symmetry;
        # Qt's disabled-state styling communicates the read-only
        # treatment.
        if self._entry.is_global:
            self._drag_handle.setEnabled(False)
            self._up_button.setEnabled(False)
            self._down_button.setEnabled(False)
            self._remove_button.setEnabled(False)

    # -- public state ----------------------------------------------------

    @property
    def entry(self) -> FolderEntry:
        return self._entry

    def set_entry(self, entry: FolderEntry) -> None:
        """Replace the row's model record and refresh visuals."""
        self._entry = entry
        self.setProperty("nslPath", entry.path)
        self._refresh_from_entry()

    def set_can_move_up(self, can: bool) -> None:
        self._up_button.setEnabled(can)

    def set_can_move_down(self, can: bool) -> None:
        self._down_button.setEnabled(can)

    def set_panic_dimmed(self, dimmed: bool) -> None:
        """Apply or clear the panic-mode dim treatment for this row.

        Global rows ignore this - the resolved Global layer keeps loading
        on next restart even when panic is engaged. Non-Global rows
        render as ``setEnabled(False)`` (Qt's
        standard greyed-out widget tree) plus a strikethrough on the
        path label so the user reads at a glance that those folders
        are *ignored* on next restart, not merely *disabled in the UI*.
        Folders that are NOT the Global Plugins row are disabled / struck
        through so they read as ignored, while the Global row remains.
        """
        if self._entry.is_global:
            # Defensive: callers should already skip Globals, but if
            # they don't, treat it as a no-op so the Global row never
            # accidentally reads as ignored.
            return
        if self._panic_dimmed == dimmed:
            return
        self._panic_dimmed = dimmed
        self.setEnabled(not dimmed)
        self.setProperty("panicDimmed", dimmed)
        # Re-render: the path label picks up the new dim text
        # colours; ``_PathLabel.set_panic_dimmed`` flips its custom
        # paint flag so the bright strikethrough overlay draws on
        # top; the icon syncs repaint the eye + select pixmaps at
        # the panic-dimmed stroke colour (they don't follow Qt's
        # disabled palette because they're hand-rendered pixmaps).
        # ``_repolish_self`` flushes the property-driven QSS.
        self._path_label.setText(self._render_path_html())
        self._path_label.set_panic_dimmed(dimmed)
        self._sync_eye_visuals(self._entry.visible)
        self._sync_select_visuals()
        self._repolish_self()

    def clear_select_engaged(self) -> None:
        """Uncheck this row's Select button + repaint to default colour.

        Called when the wiring layer detects that the grid selection
        has diverged from what the engaged folders predict - the
        icon would otherwise lie about state ("orange but not
        actually selecting those pills"). Signals are blocked so the
        programmatic uncheck does NOT cascade as a deselect request;
        the user's diverging action stays as the source of truth.
        """
        btn = self._select_button
        if not btn.isCheckable() or not btn.isChecked():
            return
        blocked = btn.blockSignals(True)
        try:
            btn.setChecked(False)
        finally:
            btn.blockSignals(blocked)
        self._sync_select_visuals()

    def is_select_engaged(self) -> bool:
        """Return True when this row's Select button is in the "engaged"
        (orange / checked) state.

        Only the icon variant is checkable; the text variant always
        returns False. Used by the wiring layer to coordinate
        additive selection across multiple folder rows: a select
        click on a folder while OTHER folders are already engaged
        adds to the current selection; the first folder engaged
        after a non-folder selection replaces it.
        """
        btn = self._select_button
        if not btn.isCheckable():
            return False
        return btn.isChecked()

    def update_entry(self, entry: FolderEntry) -> None:
        """Apply ``entry``'s field values to this row WITHOUT recreating it.

        Used by :meth:`FolderCard.set_entries` when the entry list has
        the same paths in the same order - only ``visible`` and/or
        ``health`` changed. Mutating in place avoids the
        ``deleteLater`` + ``FolderRow(...)`` rebuild cycle that would
        otherwise visibly flash the folder card on every Reset Panel
        / eye-toggle refresh.
        """
        self._entry = entry
        self._refresh_from_entry()

    # -- internals --------------------------------------------------------

    def _refresh_from_entry(self) -> None:
        e = self._entry
        # Two-tone path: parent dim grey, last segment bold white. Disabled
        # rows render the same string with strikethrough applied. Elide
        # width comes from the label's current geometry - see
        # ``_render_path_html``.
        self._path_label.setText(self._render_path_html())
        if e.is_global:
            # Friendly label on the row; full resolved path in the
            # tooltip (when known) so the dir is discoverable without
            # cluttering the row.
            tooltip_path = getattr(e, "tooltip_path", "") or ""
            if tooltip_path:
                self._path_label.setToolTip(
                    f"{tooltip_path}\nGlobal plugins folder (read-only)."
                )
            else:
                self._path_label.setToolTip(
                    "Global plugins resolved from the NSL Global folder "
                    "(read-only)."
                )
        else:
            self._path_label.setToolTip(e.path)
        self.setProperty("rowDisabled", not e.visible)
        # Eye state (block signals during programmatic restore).
        prev = self._eye_button.blockSignals(True)
        try:
            self._eye_button.setChecked(not e.visible)
        finally:
            self._eye_button.blockSignals(prev)
        self._sync_eye_visuals(e.visible)
        self._repolish_self()

    def _sync_eye_visuals(self, visible: bool) -> None:
        # Eye stroke colour follows the row hover state so the icon
        # matches the ▲ / ▼ / ✕ ctrls (which switch via the QSS
        # `[rowHover="true"]` selector on the row) AND the select-all
        # icon (which uses the same shared tokens). The eye is a
        # painted pixmap, not text - so its colour can't be flipped
        # via QSS; we repaint the icon with the appropriate stroke.
        # Rest token deliberately matches ``_ICON_REST_COLOR`` so the
        # eye doesn't sit visibly brighter than its row neighbours at
        # rest - was previously ``#c8c8c8`` (full-opacity bright grey)
        # which read as "always-active" against the dimmer siblings.
        hovered = bool(self.property("rowHover"))
        if self._panic_dimmed:
            color = _ICON_PANIC_DIMMED_COLOR
        else:
            color = _ICON_HOVER_COLOR if hovered else _ICON_REST_COLOR
        self._eye_button.setIcon(_make_eye_icon(visible, color))
        self._eye_button.setToolTip(
            "Hide this folder's Plugins in the grid (visual filter only)"
            if visible
            else "Show this folder's Plugins in the grid"
        )
        # Select-all disabled when eye is off - nothing visible to select.
        self._select_button.setEnabled(visible)
        # Re-render the select icon too - its colour follows the row
        # hover state by the same model, so an eye flip that toggles
        # the row's enabled-paint should also keep the select icon in
        # sync. ``_sync_select_visuals`` is a no-op for the TEXT variant.
        self._sync_select_visuals()

    # -- select-all icon ------------------------------------------------------

    def _make_select_icon(self, color: str) -> "QtGui.QIcon":
        """Render the active select-all variant in ``color``.

        Single-state icon (one pixmap, no Off/Active matrix). Colour
        decisions live in :meth:`_sync_select_visuals` so the icon
        repaints on row-hover and on toggle, matching the eye / ▲ / ▼ /
        ✕ hover model.
        """
        if self._select_variant == "B":
            pix = _render_select_b_pixmap(color)
        elif self._select_variant == "C":
            pix = _render_select_c_pixmap(color)
        else:  # "D"
            pix = _render_select_d_pixmap(color)
        return QtGui.QIcon(pix)

    def _sync_select_visuals(self) -> None:
        """Repaint the select icon based on row hover + checked state.

        Mirrors :meth:`_sync_eye_visuals`'s contract - the icon is a
        painted pixmap, so colour can't flip via QSS. We render a fresh
        pixmap whenever the row hover or the button's checked state
        changes. No-op on the TEXT variant (it's a QPushButton driven
        by QSS already).
        """
        if self._select_variant not in ("B", "C", "D"):
            return
        if self._panic_dimmed:
            # Panic dims the row; the select icon goes dimmer than its
            # normal rest state so it doesn't pop against the muted
            # path text. Overrides checked/hover because the row is
            # non-interactive while panic is engaged.
            color = _SELECT_PANIC_DIMMED_COLOR
        elif self._select_button.isChecked():
            # Sticky orange - overrides hover; "select-all engaged" is
            # the loudest state and must read as such regardless of
            # whether the row is currently hovered.
            color = _SELECT_CHECKED_COLOR
        elif bool(self.property("rowHover")):
            color = _SELECT_HOVER_COLOR
        else:
            color = _SELECT_REST_COLOR
        self._select_button.setIcon(self._make_select_icon(color))

    def _on_select_toggled(self, _checked: bool) -> None:
        # Repaint immediately so the colour catches up with the new
        # checked state without waiting for the next hover event.
        self._sync_select_visuals()

    def _on_select_clicked(self) -> None:
        """Emit select OR deselect depending on the button's new state.

        Qt has already toggled the checked state before this slot
        fires (Qt's ``QAbstractButton`` flips checked first, then
        emits ``clicked``). For the checkable icon variant: now-
        checked → select; now-unchecked → deselect. For the text
        variant (non-checkable) ``isChecked()`` is always False so
        we always emit ``select_requested``.
        """
        is_checkable = self._select_button.isCheckable()
        if is_checkable and not self._select_button.isChecked():
            self.deselect_requested.emit(self._entry.path)
        else:
            self.select_requested.emit(self._entry.path)

    def _on_eye_toggled(self, hidden: bool) -> None:
        visible = not hidden
        self._entry.visible = visible
        self.setProperty("rowDisabled", hidden)
        self._path_label.setText(self._render_path_html())
        self._sync_eye_visuals(visible)
        self._repolish_self()
        self.visibility_toggled.emit(self._entry.path, visible)

    # -- row-index awareness (zebra parity) -----------------------------------

    def set_row_index(self, index: int) -> None:
        """Toggle the ``rowEven`` property - drives JSX zebra striping."""
        self.setProperty("rowEven", index % 2 == 1)  # 2nd row (index 1) is "even" per JSX :nth-child(even)
        self._repolish_self()

    # -- hover state ----------------------------------------------------------

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("rowHover", True)
        # Re-render the path so hidden rows brighten on hover.
        if not self._entry.visible:
            self._path_label.setText(self._render_path_html())
        # Repaint the eye + select icons white to match the ▲ / ▼ / ✕
        # glyphs that switch via the row's `[rowHover="true"]` QSS
        # selector.
        self._sync_eye_visuals(self._entry.visible)
        self._sync_select_visuals()
        self._repolish_self()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("rowHover", False)
        if not self._entry.visible:
            self._path_label.setText(self._render_path_html())
        # Repaint the eye + select icons grey now that the row is no
        # longer hovered.
        self._sync_eye_visuals(self._entry.visible)
        self._sync_select_visuals()
        self._repolish_self()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        # Defer the re-elide to the next event loop tick so the path
        # label's geometry has settled under the row's new width before
        # we read it. Synchronous re-elide here would still see the
        # pre-resize ``self._path_label.width()``.
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self._refresh_path_label)

    def _refresh_path_label(self) -> None:
        """Re-render the path label against the label's current width.

        Called from resize events and from any state change that flips
        the visible / hovered colour pairing. The actual elide budget
        is read from ``self._path_label.width()`` so the truncation
        matches what the user sees on-screen at the current layout.
        """
        self._path_label.setText(self._render_path_html())

    def _render_path_html(self) -> str:
        """Build the path label HTML at the label's current width.

        Centralises the visible / hovered / elide-width derivation so
        every call site (``_refresh_from_entry``, ``_on_eye_toggled``,
        enter/leave/resize) produces a consistent string.

        Global Plugins row: renders the synthetic
        ``.../Global Plugins`` label verbatim instead of running the
        path through the parent-dim / leaf-bold formatter. The Global
        layer is identified by its friendly name on the row; the full
        path lives in the tooltip.
        Visible / hidden / hover styling still respects the row state
        so the row reads the same way as user rows.
        """
        if self._entry.is_global:
            return self._format_global_label_html(
                self._entry.visible,
                hovered=bool(self.property("rowHover")),
            )
        width = max(0, self._path_label.width())
        html = self._format_path_html(
            self._entry.path,
            self._entry.visible,
            hovered=bool(self.property("rowHover")),
            elide_width=width,
            font=self._path_label.font(),
            panic_dimmed=self._panic_dimmed,
        )
        # Panic mode: the strikethrough line is drawn by ``_PathLabel``'s
        # custom paintEvent, NOT via HTML ``<s>``. Qt's QTextDocument
        # picks the strike line colour from each glyph's inline ``color``,
        # so an HTML ``<s>`` wrapper just produced a line the same dim
        # grey as the text. The custom paint overlays a bright white
        # line independent of the rich-text colours.
        return html

    @staticmethod
    def _format_global_label_html(visible: bool, *, hovered: bool = False) -> str:
        """Render the Global Plugins row label.

        Display string: ``.../Global Plugins`` with the leading
        ``.../`` rendered in the dim parent colour and ``Global Plugins``
        rendered in the same bold-white treatment as a regular row's
        leaf segment. The leading ellipsis communicates "this stands
        in for a real path that's not shown."
        """
        if not visible and not hovered:
            return (
                '<span style="color:#555555;">…/Global Plugins</span>'
            )
        parent_color = "#888888" if not visible else "#888888"
        leaf_color = "#dcdcdc" if not visible else "#ffffff"
        return (
            f'<span style="color:{parent_color};">…/</span>'
            f'<b style="color:{leaf_color};">Global Plugins</b>'
        )

    def _repolish_self(self) -> None:
        """Re-apply QSS after a dynamic property change."""
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        for child in (
            self._drag_handle,
            self._path_label,
            self._ctrls,
            self._select_button,
            self._eye_button,
            self._up_button,
            self._down_button,
            self._remove_button,
        ):
            style.unpolish(child)
            style.polish(child)
        # The drag handle paints itself by reading the row's ``rowHover``
        # property - repolish alone won't redraw it, so kick its paintEvent.
        self._drag_handle.update()
        self.update()

    # -- path rendering -------------------------------------------------------

    @staticmethod
    def _format_path_html(
        path: str,
        visible: bool,
        *,
        hovered: bool = False,
        elide_width: int = 0,
        font: Optional["QtGui.QFont"] = None,
        panic_dimmed: bool = False,
    ) -> str:
        """Render a folder path with parent dim + last segment white.

        - Visible row: dim grey parent + white last segment.
        - Hidden row at rest: whole path in deep grey (recedes).
        - Hidden row hovered: brighten the whole path so the user can
          read what they're about to act on (same dim/white pairing as
          the visible row, but a touch muted so the row still reads as
          "off").
        - Panic-dimmed row: both segments pushed to very-deep grey
          (dimmer than the hidden-rest state). The row's strikethrough
          wrapper is layered on top by ``_render_path_html``. This
          dimness level is what makes the row read as "ignored on
          next restart" rather than "just visually muted".

        When ``elide_width > 0`` (in px) and ``font`` is supplied, the
        parent portion is elided from the LEFT using ``QFontMetrics``
        so the path reads as ``…/parent/leaf`` when the row is too
        narrow. The leaf (last segment) is always rendered in full -
        the row tells the user *which* folder they're looking at via
        the leaf, and the rest is supplementary. When ``elide_width``
        is 0 or the path fits, the full path is rendered.
        """
        idx = path.rfind("/")
        if idx < 0:
            parent, name = "", path
        else:
            parent = path[: idx + 1]
            name = path[idx + 1:]

        # Elide from the LEFT so the deepest folder name stays visible.
        # Common case (row is wide enough for the leaf): elide just the
        # parent portion, leaf renders in full. Tight case (even the
        # leaf alone is wider than the available width): hide the parent
        # entirely and elide the LEAF from the left so the trailing
        # characters of the folder name still read. Both branches keep
        # the ellipsis at the start of the visible string - the final
        # folder(s) are more important to see than the beginning.
        if elide_width > 0 and font is not None:
            metrics = QtGui.QFontMetrics(font)
            name_w = metrics.horizontalAdvance(name)
            if parent and name_w < elide_width:
                parent_budget = elide_width - name_w
                parent = metrics.elidedText(
                    parent, QtCore.Qt.ElideLeft, parent_budget,
                )
            elif name_w > elide_width:
                # Even the leaf overflows - drop the parent prefix and
                # elide the leaf itself from the left.
                parent = ""
                name = metrics.elidedText(
                    name, QtCore.Qt.ElideLeft, elide_width,
                )

        # Two-tone pattern (parent dim, name brighter) stays consistent
        # across all states - only the dimness level shifts. Keeping the
        # last segment a touch brighter than the parent in every state
        # avoids the row feeling "off" when toggled hidden.
        if panic_dimmed:
            # Panic-engaged user row. Slightly dimmer than the visible
            # rest state but still clearly legible against the panel
            # background - the strikethrough wrapper carries the
            # "ignored on next restart" semantic; the colour only
            # needs to read as "muted, not active." The colours stay
            # clear of the panel-bg #393939 so the text is dimmed but
            # still readable.
            #
            # Hover doesn't restore brightness here: the row is
            # non-interactive (``setEnabled(False)``) and the user
            # can't act on it without first disengaging panic.
            dim_color, name_color = "#6c6c6c", "#a0a0a0"
        elif visible:
            dim_color, name_color = "#9a9a9a", "#ffffff"
        elif hovered:
            # Hidden + hovered: legible again so the row can be inspected,
            # but a touch dimmer than a visible row so the "hidden" state
            # still registers.
            dim_color, name_color = "#7a7a7a", "#c8c8c8"
        else:
            # Hidden + at rest: both very dim so the row recedes, but the
            # last segment still reads slightly brighter than the parent
            # so the two-tone vocabulary is preserved.
            dim_color, name_color = "#4a4a4a", "#6a6a6a"
        return (
            f'<span style="color:{dim_color}">{_html_escape(parent)}</span>'
            f'<span style="color:{name_color}">{_html_escape(name)}</span>'
        )

    # -- QSS for the row + every control --------------------------------------

    def _apply_row_qss(self) -> None:
        """Install the row's stylesheet.

        Every selector keys off dynamic properties (``rowHover``, ``rowEven``)
        toggled in :meth:`enterEvent` / :meth:`leaveEvent` /
        :meth:`set_row_index`. QSS doesn't propagate ``:hover`` to children
        the way CSS does, so we drive child appearance via the same
        property on the row's siblings.
        """
        self.setStyleSheet(
            # ---- row container -----------------------------------------------
            # Order matters: equal-specificity QSS rules cascade in source
            # order. Layering goes zebra → disabled → hover so that the
            # orange hover wins on hidden rows too (hidden is a *content*
            # state, not a "no longer interactive" state - the row is
            # still a click target for its ctrls).
            # Row paints transparent so the parent _StripedListContainer's
            # alternating A/B stripe colours show through and define each
            # row's background. No border-bottom: the stripe's own
            # #2c2c2c divider at the bottom of each stripe provides the
            # row separator. Without this, the row rendered at 29 px
            # (28 px content + 1 px border) vs the 28 px stripe height,
            # accumulating 1 px of misalignment per row and producing the
            # "doubling" effect below the last real row. The rowEven
            # overlay is intentionally absent - letting the stripe show
            # through directly gives clean A/B alternation that the
            # empty-area stripes continue in phase.
            'QFrame#FolderRow[rowDisabled="true"] {'
            '   background: rgba(0, 0, 0, 100);'
            '}'
            # Row hover - translucent Nuke orange. Comes last so it
            # overrides both the zebra striping and the hidden-row
            # overlay; matches the rest of the panel's "cursor is here"
            # vocabulary.
            'QFrame#FolderRow[rowHover="true"] { background: rgba(238, 150, 38, 40); }'
            # Hidden + hovered - keep the dark recede tone but tint it
            # with a faint orange so the toggle from hidden→hovered reads
            # as a state change. Low alpha keeps the path text readable.
            'QFrame#FolderRow[rowDisabled="true"][rowHover="true"] {'
            '   background: rgba(238, 150, 38, 18);'
            '}'
            # Dragging - source row fades to a faint outline so the
            # ghost overlay carries the visual weight while live-reorder
            # shifts rows around it. The slot the source occupies still
            # reads as part of the list (faint border still draws).
            'QFrame#FolderRow[rowDragging="true"] {'
            '   background: rgba(255, 255, 255, 8);'
            '}'
            # ---- ctrls cluster (opacity faked via colour stops) --------------
            'QWidget#Ctrls { background: transparent; }'
            # ---- Select all -------------------------------------------------
            'QPushButton#SelectButton {'
            '   background: transparent; border: 1px solid transparent;'
            '   border-radius: 3px; padding: 0 10px;'
            '   font-weight: 700; color: rgba(200, 200, 200, 153);'
            '}'
            'QFrame#FolderRow[rowHover="true"] QPushButton#SelectButton {'
            '   color: #ffffff;'
            '}'
            'QPushButton#SelectButton:hover {'
            '   background: rgba(255, 255, 255, 15); color: #ffffff;'
            '}'
            'QPushButton#SelectButton:disabled {'
            '   color: rgba(150, 150, 150, 102);'
            '}'
            # ---- Eye toggle --------------------------------------------------
            'QToolButton#EyeToggle {'
            '   background: transparent; border: 1px solid transparent;'
            '   border-radius: 3px;'
            '}'
            'QToolButton#EyeToggle:hover {'
            '   background: rgba(255, 255, 255, 15);'
            '}'
            # ---- Select all (icon variants B / C / D - QToolButton) ----------
            # The TEXT variant is a QPushButton#SelectButton and keeps its
            # own QSS above; the icon variants are QToolButton#SelectButton
            # and need their own ruleset to match the eye / ▲ / ▼ / ✕
            # hover-pad vocabulary (transparent bg + 1 px transparent
            # border at rest, ``rgba(255,255,255,15)`` pad on direct
            # cursor-over). Icon colour is driven by ``_sync_select_visuals``
            # so the painted pixmap brightens with the row's hover state;
            # this QSS only owns the background pad on direct hover.
            'QToolButton#SelectButton {'
            '   background: transparent; border: 1px solid transparent;'
            '   border-radius: 3px;'
            '}'
            'QToolButton#SelectButton:hover {'
            '   background: rgba(255, 255, 255, 15);'
            '}'
            # ---- ▲ ▼ - disabled buttons stay dim regardless of row hover --
            'QPushButton#MoveUp, QPushButton#MoveDown {'
            '   background: transparent; border: 1px solid transparent;'
            '   border-radius: 3px; padding: 0;'
            '   color: rgba(200, 200, 200, 153);'
            '   font-size: 10px;'
            '}'
            'QFrame#FolderRow[rowHover="true"] QPushButton#MoveUp:enabled,'
            'QFrame#FolderRow[rowHover="true"] QPushButton#MoveDown:enabled {'
            '   color: #ffffff;'
            '}'
            'QPushButton#MoveUp:enabled:hover, QPushButton#MoveDown:enabled:hover {'
            '   background: rgba(255, 255, 255, 15); color: #ffffff;'
            '}'
            'QPushButton#MoveUp:disabled, QPushButton#MoveDown:disabled {'
            '   color: rgba(150, 150, 150, 60);'
            '}'
            # ---- ✕ remove ----------------------------------------------------
            'QPushButton#RemoveButton {'
            '   background: transparent; border: 1px solid transparent;'
            '   border-radius: 3px; padding: 0;'
            '   color: rgba(200, 200, 200, 153);'
            '   font-size: 14px;'  # sized so the X glyph reads clearly
            '}'
            'QFrame#FolderRow[rowHover="true"] QPushButton#RemoveButton {'
            '   color: #ffffff;'
            '}'
            'QPushButton#RemoveButton:hover {'
            '   background: rgba(255, 255, 255, 15); color: #d96a6a;'
            '}'
            # Disabled ✕ - same faded grey as ▲ / ▼ :disabled so the
            # Global Plugins row's permanently-disabled remove control
            # reads as inert (not clickable). The row-hover rule above
            # lifts colour to #ffffff regardless of enabled state, so
            # both selectors below override that lift when :disabled to
            # keep the disabled X from looking clickable on hover.
            'QPushButton#RemoveButton:disabled {'
            '   color: rgba(150, 150, 150, 60);'
            '}'
            'QFrame#FolderRow[rowHover="true"] QPushButton#RemoveButton:disabled {'
            '   color: rgba(150, 150, 150, 60);'
            '}'
            # ---- Path label --------------------------------------------------
            'QLabel#PathLabel { background: transparent; padding: 0; }'
        )


def _html_escape(text: str) -> str:
    """Escape ``&``, ``<``, ``>`` for safe inclusion in QLabel rich text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Shared per-row icon colour tokens. Used by both the select-all icon
# variants AND the eye toggle so every per-row icon reads at the same
# brightness at rest and brightens to the same white on row hover.
# Previously the eye used ``#c8c8c8`` (full-opacity bright grey) while
# the select / move / remove icons used ~60 %-alpha or a dimmer hex,
# making the eye visually pop against its neighbours.
_ICON_REST_COLOR = "#7a7a7a"          # dim grey - rest state, reads "clickable"
_ICON_HOVER_COLOR = "#ffffff"         # white - row hovered, all icons brighten together
_SELECT_CHECKED_COLOR = "#ee9626"     # Nuke orange - checked / "select-all engaged"
# Panic-mode rest colour - deliberately DARKER than ``_ICON_REST_COLOR``
# so the eye / select / arrow / remove icons read as suppressed when
# the row is dimmed. Qt's ``setEnabled(False)`` greys the QPushButton
# chrome but the icon pixmaps are painted at the row's literal stroke
# colour and don't follow the disabled palette - without this fork
# the icons would appear brighter than the path text, so the panic-
# mode icons are forced slightly darker than their rest state.
_ICON_PANIC_DIMMED_COLOR = "#4d4d4d"

# Legacy aliases for the select-specific call sites; identical to the
# icon-vocabulary tokens. Kept so any caller importing the old names
# (snapshot scripts, external tools, design notes) continues to work
# without churn.
_SELECT_REST_COLOR = _ICON_REST_COLOR
_SELECT_HOVER_COLOR = _ICON_HOVER_COLOR
_SELECT_PANIC_DIMMED_COLOR = _ICON_PANIC_DIMMED_COLOR


def _render_select_b_pixmap(color: str, size: int = 14) -> "QtGui.QPixmap":
    """Paint variant B (lines + leftmost checkmark) in the given colour."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(1.4 * size / 16.0)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.setPen(pen)

    s = size / 16.0
    painter.drawLine(QtCore.QPointF(7 * s, 4.5 * s), QtCore.QPointF(14 * s, 4.5 * s))
    painter.drawLine(QtCore.QPointF(7 * s, 8 * s), QtCore.QPointF(14 * s, 8 * s))
    painter.drawLine(QtCore.QPointF(7 * s, 11.5 * s), QtCore.QPointF(14 * s, 11.5 * s))

    path = QtGui.QPainterPath()
    path.moveTo(2 * s, 8 * s)
    path.lineTo(3.5 * s, 9.5 * s)
    path.lineTo(5.5 * s, 6 * s)
    painter.drawPath(path)
    painter.end()
    return pixmap


def _render_select_c_pixmap(color: str, size: int = 14) -> "QtGui.QPixmap":
    """Paint variant C (checked box) in the given colour."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(1.4 * size / 16.0)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.setPen(pen)

    s = size / 16.0
    painter.drawRect(QtCore.QRectF(2 * s, 2 * s, 12 * s, 12 * s))

    path = QtGui.QPainterPath()
    path.moveTo(5 * s, 8.5 * s)
    path.lineTo(7.5 * s, 11 * s)
    path.lineTo(11.5 * s, 5.5 * s)
    painter.drawPath(path)
    painter.end()
    return pixmap


def _render_select_d_pixmap(color: str, size: int = 14) -> "QtGui.QPixmap":
    """Paint variant D (classic mouse-cursor arrow) in the given colour.

    Filled polygon - the canonical "default arrow" cursor shape that
    every desktop OS draws for mouse pointer rest state. Reads as
    "click to select" because the icon visually *is* the user's
    pointer. Coordinates fit inside a 16 × 16 grid with 2 px margin so
    the shape never crowds the 22 × 22 button's hit area.
    """
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))

    s = size / 16.0
    poly = QtGui.QPolygonF([
        QtCore.QPointF(2.5 * s, 2.0 * s),    # tip (top-left)
        QtCore.QPointF(2.5 * s, 12.0 * s),   # bottom-left of main body
        QtCore.QPointF(5.5 * s, 10.0 * s),   # inner bend (body → tail)
        QtCore.QPointF(7.0 * s, 13.5 * s),   # bottom-left of tail
        QtCore.QPointF(8.5 * s, 12.8 * s),   # bottom-right of tail
        QtCore.QPointF(6.7 * s, 9.5 * s),    # top of tail (joins body)
        QtCore.QPointF(10.8 * s, 9.5 * s),   # right shoulder (body widest)
    ])
    painter.drawPolygon(poly)
    painter.end()
    return pixmap


def _make_eye_icon(visible: bool, color: str = _ICON_REST_COLOR) -> "QtGui.QIcon":
    """Paint the JSX eye / eye-off glyph via ``QPainter`` paths.

    Geometry mirrors ``DirectoryList.jsx`` exactly: 16-unit viewBox with
    a horizontal almond outline and a 2-unit pupil; the off variant adds
    a diagonal slash from (2, 14) to (14, 2). Stroked outlines only; no
    fill. Rendered at 14×14 to match the design's icon size.

    ``color`` lets the caller swap the stroke colour for the row-hover
    state - the folder row brightens the eye to white on hover so the
    icon matches the ▲ / ▼ / ✕ ctrls (which switch via QSS).
    """
    size = 14
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(1.4 * size / 16.0)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.setPen(pen)

    s = size / 16.0

    # Almond - JSX: M1.5 8c1.7-3 4-4.5 6.5-4.5S12.8 5 14.5 8c-1.7 3-4 4.5-6.5 4.5S3.2 11 1.5 8z
    almond = QtGui.QPainterPath()
    almond.moveTo(1.5 * s, 8 * s)
    almond.cubicTo(3.2 * s, 5 * s, 5.5 * s, 3.5 * s, 8 * s, 3.5 * s)
    almond.cubicTo(10.5 * s, 3.5 * s, 12.8 * s, 5 * s, 14.5 * s, 8 * s)
    almond.cubicTo(12.8 * s, 11 * s, 10.5 * s, 12.5 * s, 8 * s, 12.5 * s)
    almond.cubicTo(5.5 * s, 12.5 * s, 3.2 * s, 11 * s, 1.5 * s, 8 * s)
    almond.closeSubpath()
    painter.drawPath(almond)

    # Pupil - circle cx=8 cy=8 r=2 on a 16-unit viewBox.
    painter.drawEllipse(QtCore.QRectF((8 - 2) * s, (8 - 2) * s, 4 * s, 4 * s))

    if not visible:
        # Diagonal slash - JSX: line x1=2 y1=14 x2=14 y2=2.
        # JSX uses currentColor which inherits `.ctrl-eye-off { color: #7a7a7a }`
        # - apply the same dim grey to the slash so the off-state reads muted.
        slash_pen = QtGui.QPen(QtGui.QColor(color))
        slash_pen.setWidthF(1.6 * s)
        slash_pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(slash_pen)
        painter.drawLine(
            QtCore.QPointF(2 * s, 14 * s),
            QtCore.QPointF(14 * s, 2 * s),
        )

    painter.end()
    return QtGui.QIcon(pixmap)


class _PathLabel(QtWidgets.QLabel):
    """QLabel that paints its own bright strikethrough overlay.

    Qt's QTextDocument renders the HTML ``<s>`` decoration in the
    inline ``color`` of the text being struck, so wrapping
    dim-coloured spans in an outer ``<s>`` doesn't give a brighter
    strike line - the inner-span colours win. This subclass paints a
    1.5 px horizontal line on top of the rendered text in a fixed
    bright colour, independent of the HTML, so the strike reads
    clearly against the dim path text.
    """

    # A small step dimmer than the canonical Nuke body-text grey
    # (``_theme._NUKE_TEXT = #c8c8c8``) - keeps the strike legible
    # without competing with the live body text elsewhere on the
    # panel.
    _STRIKE_COLOR = QtGui.QColor("#b8b8b8")
    _STRIKE_WIDTH = 1.5

    def __init__(self, text: str = "", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(text, parent)
        self._panic_dimmed: bool = False

    def set_panic_dimmed(self, dimmed: bool) -> None:
        if self._panic_dimmed == dimmed:
            return
        self._panic_dimmed = bool(dimmed)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._panic_dimmed:
            return
        # Compute the rendered text width so the strike line ends at
        # the last glyph rather than running across empty label space.
        # The label content is rich-text HTML; strip the tags so
        # QFontMetrics measures the plain-text width. Cheap regex -
        # the strings are short (paths) and the paint frequency is
        # low (only when the row's panic flag flips or the path text
        # itself changes).
        import re as _re  # noqa: PLC0415

        plain = _re.sub(r"<[^>]+>", "", self.text())
        plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text_width = self.fontMetrics().horizontalAdvance(plain)
        rect = self.contentsRect()
        text_width = min(text_width, rect.width())
        if text_width <= 0:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        pen = QtGui.QPen(self._STRIKE_COLOR)
        pen.setWidthF(self._STRIKE_WIDTH)
        painter.setPen(pen)
        # Vertical centre of the contents rect, plus 0.5 so a 1.5-wide
        # line straddles a pixel cleanly.
        y = rect.center().y() + 0.5
        painter.drawLine(
            QtCore.QPointF(rect.left(), y),
            QtCore.QPointF(rect.left() + text_width, y),
        )
        painter.end()


class _BorderOverlay(QtWidgets.QWidget):
    """Transparent overlay that paints a rounded 1 px border on top.

    Sits as the last child of ``_RoundedListBox`` so its paintEvent runs
    after every sibling (the scroll area, the row container). Mouse
    events pass straight through to the underlying widgets via
    ``WA_TransparentForMouseEvents``.
    """

    _RADIUS = 4
    # Edge-light hairline (`#5a5a5a`) - the design-system highlight token.
    # Reads as a clean delineation against the `#393939` panel without
    # the hardness of true black.
    _BORDER_COLOR = QtGui.QColor("#5a5a5a")

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Inset by 0.5 so the 1 px stroke sits on whole pixels.
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QtGui.QPen(self._BORDER_COLOR, 1.0)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)
        painter.end()


class _RoundedListBox(QtWidgets.QFrame):
    """1 px rounded-rect container with AA'd outline that survives children.

    Two cooperating mechanisms make the rounded corners clean:

    - ``paintEvent`` paints the rounded fill so the inside reads as a
      single surface even where children leave gaps.
    - A ``_BorderOverlay`` child sits above every sibling and paints
      the rounded 1 px outline last - guaranteeing the border can't be
      covered by the ``QScrollArea`` viewport's square fill.

    ``resizeEvent`` installs a region mask so the corner pixels outside
    the rounded shape are clipped away entirely; the panel background
    behind shows through. The mask is hard-edged (Qt requires integer
    polygons) but the overlay's AA stroke hides the jaggies.
    """

    _RADIUS = 4
    _FILL_COLOR = QtGui.QColor("#393939")

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self._overlay = _BorderOverlay(self)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(self._FILL_COLOR)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)
        painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Keep the overlay tracking the wrapper's full rect + on top.
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        # Mask the wrapper so corner notches don't paint the panel bg.
        path = QtGui.QPainterPath()
        path.addRoundedRect(
            QtCore.QRectF(self.rect()),
            float(self._RADIUS),
            float(self._RADIUS),
        )
        polygon = path.toFillPolygon().toPolygon()
        self.setMask(QtGui.QRegion(polygon))


class _StripedListContainer(QtWidgets.QWidget):
    """Folder-list container that paints striped placeholder rows.

    When the folder list has 0-N real rows, the big empty area below
    feels boring and unclear. Painting alternating
    horizontal stripes the same height as a real :class:`FolderRow`
    gives the section visible structure even when sparse or empty.
    Real folder rows paint over the stripes via the normal Qt
    composition order; the empty-state overlay (centered label) is
    parented to this container and positioned in :meth:`resizeEvent`.

    Stripes use a 2-tone dark grey palette tuned to read as
    "placeholder for a row" without competing with real row chrome.
    """

    # Stripe height is locked to
    # :data:`_FOLDER_ROW_HEIGHT_PX` so a real :class:`FolderRow` slots
    # into exactly one stripe - each folder row fits one stripe
    # perfectly. Drawing a
    # 1-px bottom hairline at the stripe boundary makes each band read
    # as "a row that could be here" rather than just a background
    # gradient.
    _STRIPE_HEIGHT_PX = _FOLDER_ROW_HEIGHT_PX
    _STRIPE_COLOUR_A = QtGui.QColor("#383838")
    _STRIPE_COLOUR_B = QtGui.QColor("#3e3e3e")
    _STRIPE_DIVIDER = QtGui.QColor("#2c2c2c")

    def __init__(self, parent=None):
        super().__init__(parent)
        # Empty-state overlay label - positioned in resizeEvent. Set
        # by the FolderCard after construction.
        self._overlay_label: "Optional[QtWidgets.QLabel]" = None

    def set_overlay_label(self, label: "QtWidgets.QLabel") -> None:
        """Register the empty-state label so it stays centered."""
        self._overlay_label = label
        self._reposition_overlay()

    def paintEvent(self, event) -> None:
        """Paint horizontal stripes filling the full container area.

        Each stripe is the height of a real folder row + a 1-px
        divider line at the bottom to read as a "card slot."
        """
        painter = QtGui.QPainter(self)
        try:
            rect = self.rect()
            divider_pen = QtGui.QPen(self._STRIPE_DIVIDER, 1)
            y = 0
            i = 0
            while y < rect.height():
                colour = (
                    self._STRIPE_COLOUR_A if i % 2 == 0 else self._STRIPE_COLOUR_B
                )
                painter.fillRect(
                    0, y, rect.width(), self._STRIPE_HEIGHT_PX, colour
                )
                # Divider line at the bottom of each stripe - matches
                # FolderRow's own bottom-hairline so real rows and
                # placeholders share the same row rhythm.
                painter.setPen(divider_pen)
                painter.drawLine(
                    0,
                    y + self._STRIPE_HEIGHT_PX - 1,
                    rect.width(),
                    y + self._STRIPE_HEIGHT_PX - 1,
                )
                y += self._STRIPE_HEIGHT_PX
                i += 1
        finally:
            painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlay()

    def _reposition_overlay(self) -> None:
        """Fill the entire container with the label - the label's own
        ``AlignCenter`` flag handles both horizontal and vertical
        centering of the wrapped text. Much simpler and more reliable
        than computing a precise centred rect when the wrapped label's
        ``sizeHint()`` returns size-hint values that don't account
        for word wrap width."""
        if self._overlay_label is None:
            return
        if self.width() < 50 or self.height() < 50:
            # Not laid out yet; resizeEvent will fire again with a
            # real size and re-run this.
            return
        # Inset 16px so the text doesn't kiss the container edges.
        self._overlay_label.setGeometry(
            16, 16, self.width() - 32, self.height() - 32
        )
        self._overlay_label.raise_()


class _DragGhost(QtWidgets.QWidget):
    """Translucent floating copy of a row, follows the cursor during drag.

    Painted with ``QPainter.setOpacity()`` so the row chrome reads as a
    "shadow of itself" without needing a ``QGraphicsOpacityEffect``
    (effects on complex widgets can flicker or fail to composite cleanly
    inside scroll areas).
    """

    _OPACITY = 0.75

    def __init__(self, source_pixmap: "QtGui.QPixmap", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._pixmap = source_pixmap
        self.resize(source_pixmap.size())

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(self._OPACITY)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.end()


class _DragHandle(QtWidgets.QWidget):
    """Two-column dot-grip matching the JSX `.grip` (`⋮⋮`).

    Painted with ``QPainter`` rather than relying on the Unicode glyph
    because that codepoint (U+22EE) renders as ``: :`` in most Qt fonts.
    Geometry mirrors the JSX target: two columns of three dots, tight
    vertical stacking, larger and more clearly visible than the previous
    iteration.
    """

    _DOT_W = 2          # dot width (px)
    _DOT_H = 2          # dot height (px) - square dot reads sharper at small sizes
    _COL_GAP = 3        # horizontal gap between the two dot columns
    _ROW_GAP = 3        # vertical gap between dots in a column

    def __init__(self, row: FolderRow) -> None:
        super().__init__(row)
        self._row = row
        self.setObjectName("DragHandle")
        self.setToolTip("Drag to reorder")
        self.setCursor(QtCore.Qt.SizeVerCursor)
        self.setFixedWidth(14)
        self._press_pos: Optional[QtCore.QPoint] = None

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)  # crisp 2×2 dots
        painter.setPen(QtCore.Qt.NoPen)

        # JSX `.grip` rest colour: #5a5a5a at opacity 0.5 → effective ≈ rgb(45,45,45).
        # Hover: opacity 1.0 → rgb(90,90,90). We mirror the brightness ramp here.
        hovered = bool(self._row.property("rowHover"))
        if hovered:
            painter.setBrush(QtGui.QColor(200, 200, 200))
        else:
            painter.setBrush(QtGui.QColor(120, 120, 120))

        w = self._DOT_W
        h = self._DOT_H
        col_gap = self._COL_GAP
        row_gap = self._ROW_GAP
        total_w = 2 * w + col_gap
        total_h = 3 * h + 2 * row_gap
        x0 = (self.width() - total_w) // 2
        y0 = (self.height() - total_h) // 2

        for col in (0, 1):
            cx = x0 + col * (w + col_gap)
            for row_idx in (0, 1, 2):
                cy = y0 + row_idx * (h + row_gap)
                painter.drawRect(QtCore.QRect(cx, cy, w, h))

        painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.pos()
            # Grab the mouse so we keep receiving move + release events
            # even when the cursor leaves the small grip widget. Without
            # this, the drag breaks the moment the cursor moves off the
            # 14 px wide handle.
            self.grabMouse()
            self._row.drag_started.emit(self._row.entry.path)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._press_pos is not None:
            self._row.drag_moved.emit(
                self._row.entry.path, event.globalPos()
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if (
            self._press_pos is not None
            and event.button() == QtCore.Qt.LeftButton
        ):
            self.releaseMouse()
            self._row.drag_released_over.emit(
                self._row.entry.path,
                event.globalPos(),
            )
            self._press_pos = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# FolderCard - the card widget
# ---------------------------------------------------------------------------


class FolderCard(QtWidgets.QFrame):
    """Plugins Folder management card.

    Owns the list of :class:`FolderEntry` records and renders one
    :class:`FolderRow` per entry inside a vertical scroll area. The card
    is the single point of integration for the panel: callers wire its
    signals to the domain layer and consume the resulting model changes.

    **Public signals:**

    * ``add_folder_requested()`` - user clicked **Add Plugins Folder**.
      The card does **not** open a folder picker itself; callers wire
      this to ``QFileDialog`` (kept outside the widget so it can run
      offscreen / headless without a Qt file-dialog event loop).
    * ``rescan_requested()`` - user clicked **Rescan Plugins**.
    * ``reorder_requested(list[str])`` - user reordered the list (via
      drag-and-drop or an arrow button). The list payload is the new
      path order (top = highest priority).
    * ``remove_confirmed(str)`` - user clicked ``×`` and confirmed the
      removal dialog. Carries the removed path.
    * ``visibility_changed(str, bool)`` - eye toggled. Carries
      ``(path, visible)``. Visual filter only - does not change scan
      or enable/disable state.
    * ``select_requested(str)`` - user clicked **Select** on a row.
    * ``health_inspected(str)`` - user clicked a row's health indicator.

    Construction parameters:

    * ``confirm_remove``: optional callable
      ``(parent, path) -> bool`` overriding the default confirm dialog.
      The panel composition wires
      :func:`NukeSurvivalLoadout.ui.dialogs.confirm_remove_folder` here.
    """

    add_folder_requested = QtCore.Signal()
    rescan_requested = QtCore.Signal()
    reorder_requested = QtCore.Signal(list)
    remove_confirmed = QtCore.Signal(str)
    visibility_changed = QtCore.Signal(str, bool)
    select_requested = QtCore.Signal(str)
    deselect_requested = QtCore.Signal(str)
    open_folder_requested = QtCore.Signal(str)   # (path,) - row right-click "Open Folder"
    health_inspected = QtCore.Signal(str)

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        confirm_remove: Optional[Callable[..., bool]] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FolderCard")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)

        self._confirm_remove = confirm_remove or _default_confirm_remove_folder
        self._entries: List[FolderEntry] = []
        self._rows: List[FolderRow] = []
        self._drag_source: Optional[str] = None
        self._drag_ghost: Optional[_DragGhost] = None
        self._drag_press_offset: QtCore.QPoint = QtCore.QPoint(0, 0)
        # Panic-mode state. Owned here so a rebuild
        # (``set_entries`` Branch 4 - add/remove) re-applies the dim
        # treatment to newly minted rows without the panel having to
        # re-poke the card after every folder mutation.
        self._panic_engaged: bool = False

        self._build()

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # 1. Button row.
        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(6)
        # HybridTextButton - same Nuke-hybrid basic-text vocabulary as
        # the top toolbar's Undo / Redo / Reset Panel. Single source of
        # truth in `NukeSurvivalLoadout/ui/_buttons.py`; edit once, update everywhere.
        self._add_button = HybridTextButton("&Add Plugins Folder", self)
        self._add_button.setObjectName("AddPluginsFolder")
        self._add_button.clicked.connect(self.add_folder_requested.emit)
        button_row.addWidget(self._add_button)

        self._rescan_button = HybridTextButton("&Rescan Plugins", self)
        self._rescan_button.setObjectName("RescanPlugins")
        self._rescan_button.clicked.connect(self.rescan_requested.emit)
        button_row.addWidget(self._rescan_button)

        button_row.addStretch(1)
        outer.addLayout(button_row)

        # 2. Priority indicator strip (shown only when ≥2 folders).
        #    JSX prototype paints the ↑ arrow in Nuke orange (`#ee9626`) and
        #    the word "priority" in muted grey (`#8a8a8a`).
        self._priority_strip = QtWidgets.QLabel(self)
        self._priority_strip.setObjectName("PriorityIndicator")
        self._priority_strip.setTextFormat(QtCore.Qt.RichText)
        self._priority_strip.setText(
            '<span style="color:#ee9626">↑</span> '
            '<span style="color:#8a8a8a">priority</span>'
        )
        self._priority_strip.setAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
        )
        self._priority_strip.setVisible(False)
        outer.addWidget(self._priority_strip)

        # 3. Scroll area with the row stack inside. Wrapped in a custom
        #    rounded-rect QFrame that paints its own AA'd border (Qt's
        #    QSS `border-radius` doesn't anti-alias at 1 px width and the
        #    rounded outline collides with the square child viewport).
        #    `_RoundedListBox.paintEvent` draws the border last so the
        #    child can't paint over it; `resizeEvent` installs a region
        #    mask so children also clip to the rounded shape.
        self._list_box = _RoundedListBox(self)
        self._list_box.setObjectName("FolderListBox")
        box_layout = QtWidgets.QVBoxLayout(self._list_box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(0)

        self._scroll = QtWidgets.QScrollArea(self._list_box)
        self._scroll.setObjectName("FolderListScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff
        )
        self._scroll.setStyleSheet(
            'QScrollArea#FolderListScroll {'
            '   background: #393939; border: none;'
            '}'
            'QScrollArea#FolderListScroll > QWidget > QWidget {'
            '   background: #393939;'
            '}'
        )
        box_layout.addWidget(self._scroll)

        # Use the striped container subclass so the empty area renders
        # placeholder rows. Real folder rows paint over the stripes via
        # normal Qt composition.
        self._list_container = _StripedListContainer(self._scroll)
        self._list_container.setObjectName("FolderListContainer")
        self._list_layout = QtWidgets.QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)  # rows draw their own dividers
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_container)
        outer.addWidget(self._list_box, 1)

        # 4. Empty-state label.
        #
        # The canonical empty-state message lives CENTERED inside the
        # big folder-list area. An earlier attempt inserted the label at
        # the top of the list layout, which (a) didn't render visibly
        # because the layout already had a single stretch at the end and
        # the empty label collapsed to its natural height at the top, and
        # (b) it should sit in the big empty area, vertically centered.
        #
        # The fix is to overlay the label on the list container's
        # paint area via absolute positioning in a resize hook, instead
        # of fighting the QVBoxLayout stretches that the striped
        # placeholders need to render correctly.
        # The copy names the Add Plugins Folder button verbatim and
        # anchors with "above"; the button's nuke-orange first-run
        # border does the visual pull.
        self._empty_label = QtWidgets.QLabel(
            "No Plugins Folder added yet.<br>"
            "Click <b>Add Plugins Folder</b> above to choose one.",
            self._list_container,
        )
        self._empty_label.setObjectName("EmptyState")
        self._empty_label.setTextFormat(QtCore.Qt.RichText)
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self._empty_label.setStyleSheet(
            "QLabel#EmptyState {"
            "  color: #b8b8b8;"
            "  background: transparent;"
            "  font-size: 11pt;"
            "}"
        )
        # Hand the overlay label to the striped container so it can
        # reposition it on resize.
        self._list_container.set_overlay_label(self._empty_label)

        # Enforce a card-level minimum so the parent splitter
        # (folder card ↔ side panel) cannot shrink this pane below the
        # width the per-row icon cluster needs. ``FolderRow`` sets a
        # 190 px row minimum; add the 8 px outer paddings on both
        # sides so the splitter respects the same envelope at the
        # card boundary. The icons must stay visible inside the window
        # and tied to the right side of the folder card area.
        self.setMinimumWidth(190 + 16)

        self._refresh_empty_state()

    # -- public API -------------------------------------------------------

    def set_entries(self, entries: List[FolderEntry]) -> None:
        """Replace the folder list (top = highest priority).

        Four branches, fastest to slowest:

        1. **Identical entries** - early exit; no UI work.
        2. **Same paths in the same order, only field values differ**
           (e.g. an eye-toggle visibility flip, or a health update).
           Mutate the existing rows in place via
           :meth:`FolderRow.update_entry` - no widget rebuild, no
           flash.
        3. **Same paths in a DIFFERENT order** (▲ / ▼ reorder, or a
           reorder_and_save round-trip). Keep the existing row
           widgets alive and only move them within the layout -
           ``layout.removeWidget`` + ``layout.insertWidget`` does
           NOT destroy the widget, so signal connections stay
           intact and the path label doesn't repaint from scratch.
           This avoids the flicker / reload that a rebuild causes
           when reordering via the move up / move down icons.
        4. **Path set changed** (folder added / removed) - full
           rebuild via :meth:`_rebuild_rows`.
        """
        new_entries = [
            FolderEntry(
                path=e.path,
                health=e.health,
                visible=e.visible,
                is_global=e.is_global,
            )
            for e in entries
        ]
        if new_entries == self._entries:
            return

        old_paths = [e.path for e in self._entries]
        new_paths = [e.path for e in new_entries]

        # Branch 2 - same paths, same order, just field updates.
        if old_paths == new_paths and len(self._rows) == len(new_entries):
            self._entries = new_entries
            for row, entry in zip(self._rows, new_entries):
                row.update_entry(entry)
            return

        # Branch 3 - same paths, different order.
        if (
            set(old_paths) == set(new_paths)
            and len(self._rows) == len(new_entries)
        ):
            # Pair each existing row to its path so we can pluck it
            # out and re-insert in the new order. Widget identity
            # survives the move; signal connections stay live.
            path_to_row = {
                e.path: row
                for e, row in zip(self._entries, self._rows)
            }
            for row in self._rows:
                self._list_layout.removeWidget(row)
            self._entries = new_entries
            self._rows = []
            for idx, entry in enumerate(new_entries):
                row = path_to_row[entry.path]
                row.update_entry(entry)
                row.set_row_index(idx)
                # Insert before the trailing stretch (last layout item).
                self._list_layout.insertWidget(
                    self._list_layout.count() - 1, row
                )
                self._rows.append(row)
            # Re-evaluate ▲ / ▼ enable state - the row at position 0
            # can no longer move up, the row at the last position can
            # no longer move down. Without this refresh the arrows go
            # stale after a reorder and can't be used again.
            self._refresh_arrow_enablement()
            return

        # Branch 4 - path set changed (add / remove).
        self._entries = new_entries
        self._rebuild_rows()

    def set_panic_engaged(self, engaged: bool) -> None:
        """Reflect panic-mode state across the rows.

        When panic is engaged, every user-added folder row dims +
        strikes through (Qt ``setEnabled(False)`` + strikethrough path
        label). The pinned Global Plugins row stays fully active
        because the Global layer keeps loading on next restart (panic
        drops user-added plugins only, never Globals). The Add /
        Rescan buttons also stay enabled so the
        user can keep configuring while panic is on.

        Every folder that is NOT the Global Plugins row is disabled /
        struck through so it reads as ignored on next restart, while
        the Global row remains active.
        """
        self._panic_engaged = bool(engaged)
        for row in self._rows:
            row.set_panic_dimmed(self._panic_engaged)

    def set_first_run_affordance(self, enabled: bool) -> None:
        """Toggle the nuke-orange first-run border on Add Plugins Folder.

        Called by the panel's grid-stack refresh: ON whenever the
        grid is showing the empty-state page (no plugins from any
        source), OFF the moment any plugin appears. The folder card
        owns the button instance, so it owns the affordance hook.

        Re-appears mid-session if the user removes their last folder
        and the Global layer contributes nothing - same trigger drives
        both the initial first-run and the mid-session return-to-empty.
        """
        self._add_button.set_first_run_highlight(bool(enabled))

    def entries(self) -> List[FolderEntry]:
        """Return a defensive copy of the current entry list."""
        return [
            FolderEntry(
                path=e.path,
                health=e.health,
                visible=e.visible,
                is_global=e.is_global,
            )
            for e in self._entries
        ]

    def paths(self) -> List[str]:
        """Current path order (top = highest priority)."""
        return [e.path for e in self._entries]

    def clear_engaged_select(self) -> None:
        """Reset every row's Select icon to its default (gray) state.

        Called by the wiring layer when the grid selection diverges
        from what the engaged folder icons predict (user manually
        selected something in the grid, ran Clear Selection, etc.).
        Each row's :meth:`FolderRow.clear_select_engaged` blocks
        signals so the visual reset does NOT trigger a deselect
        cascade - the user's diverging action stays sovereign.
        """
        for row in self._rows:
            row.clear_select_engaged()

    def engaged_select_paths(self) -> List[str]:
        """Folder paths whose Select icon is currently engaged (orange).

        Read by the wiring layer to decide whether a fresh Select
        click should REPLACE the current selection (no other folder
        engaged) or ADD to it (one or more other folders already
        engaged). Folder selections are additive with each other: the
        first folder select after a non-folder selection clears the
        previous selection and selects only that folder's pills;
        subsequent folder selects add to it.
        """
        return [
            row._entry.path
            for row in self._rows
            if row.is_select_engaged()
        ]

    def row_count(self) -> int:
        return len(self._rows)

    # -- internals --------------------------------------------------------

    def _rebuild_rows(self) -> None:
        # Remove existing FolderRow widgets from the layout (keep the
        # trailing stretch item that pushes rows to the top).
        while self._rows:
            row = self._rows.pop()
            self._list_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()

        for idx, entry in enumerate(self._entries):
            row = FolderRow(entry, self._list_container)
            row.set_row_index(idx)
            # Insert before the trailing stretch.
            self._list_layout.insertWidget(
                self._list_layout.count() - 1, row
            )
            self._rows.append(row)
            row.visibility_toggled.connect(self._on_visibility_toggled)
            row.select_requested.connect(self._on_select_requested)
            row.deselect_requested.connect(self._on_deselect_requested)
            row.remove_requested.connect(self._on_remove_requested)
            row.move_up_requested.connect(self._on_move_up_requested)
            row.move_down_requested.connect(self._on_move_down_requested)
            row.open_folder_requested.connect(self.open_folder_requested.emit)
            row.health_clicked.connect(self.health_inspected.emit)
            row.drag_started.connect(self._on_drag_started)
            row.drag_moved.connect(self._on_drag_moved)
            row.drag_released_over.connect(self._on_drag_released_over)

        # Last row keeps its bottom hairline so the final card reads
        # with a clean bottom edge inside the box (rather than the JSX
        # `:last-child { border-bottom: none }` rule).

        self._refresh_arrow_enablement()
        self._refresh_priority_indicator()
        self._refresh_empty_state()
        # Re-apply panic-mode dim to the freshly-minted rows. Without
        # this, a folder add / remove while panic is engaged would
        # leave the new row brightly-painted while its siblings stay
        # dimmed.
        if self._panic_engaged:
            for row in self._rows:
                row.set_panic_dimmed(True)

    def _refresh_arrow_enablement(self) -> None:
        n = len(self._rows)
        # Global Plugins row (when present) is always at the bottom
        # and never moves. The row immediately above it must NOT be
        # able to move-down, otherwise a swap would displace Global
        # off the bottom into a user-row slot.
        last_movable_idx = n - 1
        if n > 0 and self._entries[-1].is_global:
            last_movable_idx = n - 2
        for index, row in enumerate(self._rows):
            if self._entries[index].is_global:
                row.set_can_move_up(False)
                row.set_can_move_down(False)
                continue
            row.set_can_move_up(index > 0)
            row.set_can_move_down(index < last_movable_idx)

    def _refresh_priority_indicator(self) -> None:
        self._priority_strip.setVisible(len(self._entries) >= 2)

    def _refresh_empty_state(self) -> None:
        """Scroll area is ALWAYS visible
        because its container paints the striped placeholder rows.
        Only the empty-state overlay toggles based on entries count.
        After visibility changes, reposition the overlay so it stays
        centered against the current container size.
        """
        empty = not self._entries
        self._empty_label.setVisible(empty)
        # Scroll area always visible - the striped container is the
        # visual identity of this section regardless of entry count.
        self._scroll.setVisible(True)
        # Reposition the overlay against the current container size.
        reposition = getattr(self._list_container, "_reposition_overlay", None)
        if reposition is not None:
            reposition()

    def _index_of(self, path: str) -> int:
        for i, e in enumerate(self._entries):
            if e.path == path:
                return i
        return -1

    def _swap(self, a: int, b: int) -> None:
        self._entries[a], self._entries[b] = self._entries[b], self._entries[a]

    # -- drag-and-drop ----------------------------------------------------

    def _on_drag_started(self, path: str) -> None:
        """Begin a row drag: dim the source row, spawn the ghost overlay."""
        idx = self._index_of(path)
        if idx < 0:
            return
        self._drag_source = path
        source_row = self._rows[idx]

        # Mark the source row so its QSS dims to the "dragging" state.
        source_row.setProperty("rowDragging", True)
        source_row._repolish_self()  # type: ignore[attr-defined]

        # Capture the row's current pixel appearance and float it on top.
        # Parenting to the list container clips the ghost to the list box
        # - the drag stays inside the panel.
        pixmap = source_row.grab()
        self._drag_ghost = _DragGhost(pixmap, self._list_container)
        # Position so the cursor sits roughly over the grip (left edge).
        # We approximate by offsetting the ghost so its top-left aligns
        # with the source row's current top-left in container coords.
        ghost_pos = source_row.mapTo(self._list_container, QtCore.QPoint(0, 0))
        self._drag_ghost.move(ghost_pos)
        self._drag_ghost.show()
        self._drag_ghost.raise_()
        # Remember where in the row the cursor pressed, so the ghost
        # tracks the cursor consistently as it moves.
        cursor_global = QtGui.QCursor.pos()
        self._drag_press_offset = cursor_global - source_row.mapToGlobal(
            QtCore.QPoint(0, 0)
        )

    def _on_drag_moved(
        self,
        source_path: str,
        global_pos: "QtCore.QPoint",
    ) -> None:
        """Drag is in progress - move ghost, live-reorder rows under cursor."""
        if self._drag_ghost is None or self._drag_source != source_path:
            return

        # Ghost follows the cursor, preserving the press-time offset so
        # the row visually "sticks" to where the user grabbed it.
        target_global = global_pos - self._drag_press_offset
        target_local = self._list_container.mapFromGlobal(target_global)
        self._drag_ghost.move(target_local)

        # Determine which row the cursor is over and live-swap if needed.
        cursor_local = self._list_container.mapFromGlobal(global_pos)
        source_idx = self._index_of(source_path)
        target_idx = self._row_index_at(cursor_local)
        if source_idx < 0 or target_idx < 0:
            return
        if source_idx == target_idx:
            return

        # Live reorder: swap source to target position without rebuilding
        # widgets (cheap - just layout re-parenting). Updates entries,
        # rows, zebra parity, and arrow enablement to match.
        entry = self._entries.pop(source_idx)
        self._entries.insert(target_idx, entry)
        row = self._rows.pop(source_idx)
        self._rows.insert(target_idx, row)
        self._list_layout.removeWidget(row)
        # Layout count includes the trailing stretch at the end, so the
        # new layout index for the row is just `target_idx`.
        self._list_layout.insertWidget(target_idx, row)
        for i, r in enumerate(self._rows):
            r.set_row_index(i)
        self._refresh_arrow_enablement()

    def _on_drag_released_over(
        self,
        source_path: str,
        global_pos: "QtCore.QPoint",
    ) -> None:
        """End the drag: tear down ghost, restore source row, emit reorder."""
        if self._drag_source is None or self._drag_source != source_path:
            self._cleanup_drag()
            return
        idx = self._index_of(source_path)
        if idx >= 0:
            row = self._rows[idx]
            row.setProperty("rowDragging", False)
            row._repolish_self()  # type: ignore[attr-defined]
        self._cleanup_drag()
        self.reorder_requested.emit(self.paths())

    def _cleanup_drag(self) -> None:
        if self._drag_ghost is not None:
            self._drag_ghost.hide()
            self._drag_ghost.deleteLater()
            self._drag_ghost = None
        self._drag_source = None
        self._drag_press_offset = QtCore.QPoint(0, 0)

    def _row_index_at(self, local_point: "QtCore.QPoint") -> int:
        """Return the entry index for the row under ``local_point``.

        Falls back to the last row when the point is below all rows
        (allows dropping at the end), and to the first when above.
        """
        if not self._rows:
            return -1
        y = local_point.y()
        first_top = self._rows[0].geometry().top()
        if y <= first_top:
            return 0
        for index, row in enumerate(self._rows):
            geom = row.geometry()
            if geom.top() <= y <= geom.bottom():
                return index
        return len(self._rows) - 1

    # -- per-row slots ----------------------------------------------------

    def _on_visibility_toggled(self, path: str, visible: bool) -> None:
        idx = self._index_of(path)
        if idx >= 0:
            self._entries[idx].visible = visible
        self.visibility_changed.emit(path, visible)

    def _on_select_requested(self, path: str) -> None:
        self.select_requested.emit(path)

    def _on_deselect_requested(self, path: str) -> None:
        self.deselect_requested.emit(path)

    def _on_remove_requested(self, path: str) -> None:
        if self._confirm_remove(self, path):
            idx = self._index_of(path)
            if idx >= 0:
                del self._entries[idx]
                self._rebuild_rows()
            self.remove_confirmed.emit(path)

    def _on_move_up_requested(self, path: str) -> None:
        idx = self._index_of(path)
        if idx > 0:
            # Never swap a Global row OR with a Global neighbour
            # (defensive - ``_refresh_arrow_enablement`` already
            # disables the arrow, but keep the invariant local).
            if self._entries[idx].is_global or self._entries[idx - 1].is_global:
                return
            new_entries = list(self._entries)
            new_entries[idx - 1], new_entries[idx] = (
                new_entries[idx], new_entries[idx - 1],
            )
            # Route through ``set_entries`` so the reorder hits the
            # in-place branch (widget identity preserved, no path-
            # label flash) instead of the local ``_rebuild_rows``
            # tear-down + recreate. A rebuild flashes the path label
            # for a split second because new FolderRows render their
            # elided path before reaching their final width.
            self.set_entries(new_entries)
            self.reorder_requested.emit(self.paths())

    def _on_move_down_requested(self, path: str) -> None:
        idx = self._index_of(path)
        if 0 <= idx < len(self._entries) - 1:
            if self._entries[idx].is_global or self._entries[idx + 1].is_global:
                return
            new_entries = list(self._entries)
            new_entries[idx], new_entries[idx + 1] = (
                new_entries[idx + 1], new_entries[idx],
            )
            self.set_entries(new_entries)
            self.reorder_requested.emit(self.paths())

