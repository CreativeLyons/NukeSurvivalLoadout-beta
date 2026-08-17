"""Plugins Folder management card - NSL Loadout Panel, left-top region.

Card anatomy, top to bottom:

1. Button row - ``Add Plugins Folder`` and ``Rescan Plugins``.
2. Priority indicator - ``↑ priority``, shown only with two or more folders.
3. Folder list - one :class:`FolderRow` per folder, inside a scroll area.
4. Empty-state label - shown when there are no user-added folders.

A synthetic ``.../Global Plugins`` row is pinned to the bottom whenever the
Global layer is not empty. It cannot be reordered or removed. Only the eye
toggle and Select-all stay live on it.

The widget never touches the filesystem and never imports ``nuke``. It emits
intent signals and consumes :class:`FolderEntry` records. Qt imports go
through :mod:`nsl.compat`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

from nsl import compat
from nsl.ui._buttons import HybridTextButton, install_clickable_cursor

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

# Stripe height for the placeholder backdrop, matched to the FolderRow
# sizeHint so a real row fills one stripe. 28 px is the 22 px chrome
# buttons plus the `(6, 3, 10, 3)` content margins.
_FOLDER_ROW_HEIGHT_PX = 28


# ---------------------------------------------------------------------------
# Health vocabulary
# ---------------------------------------------------------------------------


class Health(Enum):
    """Folder health states. No row chrome renders them yet."""

    HEALTHY = ("Healthy", "✓", "#2ecc71")
    UNREACHABLE = ("Unreachable", "!", "#e74c3c")
    PERMISSION_DENIED = ("Permission denied", "\U0001f512", "#f1c40f")
    EMPTY = ("Empty", "∅", "#95a5a6")

    def __init__(self, label: str, glyph: str, colour: str) -> None:
        self.label = label
        self.glyph = glyph
        self.colour = colour


@dataclass
class FolderEntry:
    """One user-added Plugins Folder as rendered by :class:`FolderRow`.

    Attributes:
        path: Absolute folder path. The row identity carried by signals.
        health: Last :class:`Health` state from the most recent scan.
        visible: Eye-toggle state. Per-session and panel-local. It never
            persists and never changes the Plugin scan or enable state.
        is_global: Marks the synthetic Global Plugins row. ``path`` then
            carries :data:`nsl.constants.GLOBAL_PLUGINS_FOLDER_SENTINEL`
            so the wiring layer can recognise it.
        tooltip_path: Resolved Global plugins dir for the row tooltip.
            Empty for user rows, whose ``path`` is the tooltip.
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
    """Confirm-remove dialog used when ``nsl.ui.dialogs`` is absent.

    Returns True only when the user confirms.
    """
    try:  # pragma: no cover
        from nsl.ui import dialogs as _dialogs  # type: ignore[attr-defined]

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

    result = compat.run_modal(box)
    return result == QtWidgets.QMessageBox.Yes


# ---------------------------------------------------------------------------
# FolderRow - one row in the folder list
# ---------------------------------------------------------------------------


class FolderRow(QtWidgets.QFrame):
    """A single folder row inside :class:`FolderCard`.

    Layout follows the ``DirectoryList.jsx`` prototype: grip, two-tone
    path, Select all, eye toggle, ``▲ ▼`` and ``✕``. Controls sit at 0.6
    alpha and brighten to white on row hover.

    Every signal carries the row path first. The row emits intent only,
    and the card owns the entry list.
    """

    visibility_toggled = QtCore.Signal(str, bool)
    select_requested = QtCore.Signal(str)
    deselect_requested = QtCore.Signal(str)
    remove_requested = QtCore.Signal(str)
    move_up_requested = QtCore.Signal(str)
    move_down_requested = QtCore.Signal(str)
    open_folder_requested = QtCore.Signal(str)
    # Never emitted. The row chrome has no health control.
    health_clicked = QtCore.Signal(str)
    drag_started = QtCore.Signal(str)
    # The drag signals carry the cursor position in global coordinates.
    drag_moved = QtCore.Signal(str, "QPoint")
    drag_released_over = QtCore.Signal(str, "QPoint")

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
        self._entry = entry
        self._panic_dimmed: bool = False
        self._build()
        self._apply_row_qss()
        self._refresh_from_entry()

    # There is no row-level selection. Folders are managed from the
    # per-row controls, and selection lives on the plugin pills.

    def contextMenuEvent(self, event):
        """Right-click menu with Open Folder and Copy Path.

        Suppressed on the Global Plugins row. Its ``path`` is the
        :data:`GLOBAL_PLUGINS_FOLDER_SENTINEL`, not a real directory.
        """
        try:
            if self._entry.is_global:
                return
            menu = QtWidgets.QMenu(self)
            open_action = menu.addAction("Open Folder")
            open_action.triggered.connect(
                lambda *_: self.open_folder_requested.emit(self._entry.path)
            )
            # Copy Path needs no path resolution, so the row does it
            # here instead of emitting a signal like Open Folder.
            copy_action = menu.addAction("Copy Path")
            copy_action.triggered.connect(lambda *_: self._copy_path_to_clipboard())
            compat.run_modal(menu, event.globalPos())
        except Exception:  # pragma: no cover - defensive: never crash the panel
            pass

    def _copy_path_to_clipboard(self) -> None:
        """Put this row's folder path on the clipboard. Never raises."""
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(self._entry.path)
        except Exception:  # pragma: no cover - defensive
            pass

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        # JSX `.dirrow`: gap 8px, padding 8 10 8 6. The vertical padding
        # is 3 here so the row hugs the 22 px ctrl buttons.
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 10, 3)
        layout.setSpacing(8)
        # Minimum row width, so the right-side icon cluster stays visible
        # when the splitter narrows the card. 190 is the drag handle, the
        # five ctrl buttons, the spacings and a small path budget.
        self.setMinimumWidth(190)

        # 1. Grip - vertical-dots glyph, opacity controlled by row hover.
        self._drag_handle = _DragHandle(self)
        layout.addWidget(self._drag_handle)

        # 2. Path label - two-tone rich text, elided from the left.
        # ``setMinimumWidth(0)`` lets it shrink below its sizeHint.
        # Without it Qt keeps the full-text width and the row overflows.
        self._path_label = _PathLabel("", self)
        self._path_label.setObjectName("PathLabel")
        self._path_label.setTextFormat(QtCore.Qt.RichText)
        self._path_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse
        )
        # A selectable label brings its own right-click menu, and it
        # covers most of the row. NoContextMenu makes the label defer to
        # the FolderRow so the folder menu opens anywhere on the row.
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

        # 3. Controls cluster - Select all, eye, ▲ ▼, ✕. The size policy
        # is ``Fixed`` so the cluster is never squeezed off the right
        # edge. ``Preferred`` let it shrink and clipped ▲ ▼ ✕.
        self._ctrls = QtWidgets.QWidget(self)
        self._ctrls.setObjectName("Ctrls")
        self._ctrls.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed,
        )
        ctrls_layout = QtWidgets.QHBoxLayout(self._ctrls)
        ctrls_layout.setContentsMargins(0, 0, 0, 0)
        ctrls_layout.setSpacing(2)

        # 3a. Select all. ``NSL_SELECT_ICON`` picks the glyph:
        #     * ``D`` (default) - mouse-cursor arrow.
        #     * ``C`` - checked box.
        #     * ``B`` - lines with a check.
        #     * ``TEXT`` or any other value - "Select all" text button.
        self._select_variant = os.environ.get("NSL_SELECT_ICON", "D").upper()
        if self._select_variant in ("B", "C", "D"):
            self._select_button = QtWidgets.QToolButton(self._ctrls)
            self._select_button.setObjectName("SelectButton")
            # autoRaise off. The icon colour follows the row's
            # ``rowHover`` property, and the row QSS owns the hover pad.
            self._select_button.setAutoRaise(False)
            self._select_button.setCheckable(True)  # stays orange once clicked
            self._select_button.setFixedSize(QtCore.QSize(22, 22))
            self._select_button.setIconSize(QtCore.QSize(14, 14))
            self._select_button.setIcon(self._make_select_icon(_SELECT_REST_COLOR))
            self._select_button.toggled.connect(self._on_select_toggled)
        else:
            self._select_button = QtWidgets.QPushButton("Select all", self._ctrls)
            self._select_button.setObjectName("SelectButton")
            self._select_button.setFlat(True)
            self._select_button.setCursor(QtCore.Qt.ArrowCursor)
            # Match the 22 px icon ctrls. Without this the text button
            # fills the layout height and its hover pad reads taller.
            self._select_button.setFixedHeight(22)
        self._select_button.setToolTip(
            "Select every visible Plugin from this folder "
            "(replaces current selection)"
        )
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
        # ▲ is disabled on the top row and ▼ on the bottom one. The
        # cursor filter swaps to an arrow, so those rows read as inert.
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

        # The Global row keeps its icons for symmetry with user rows and
        # only disables them. ``_refresh_arrow_enablement`` disables the
        # arrows too, so this covers the first paint before that runs.
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

        A dimmed row is disabled and its path is struck through, so it
        reads as ignored on the next restart. Global rows ignore the
        call, because the Global layer keeps loading during panic.
        """
        if self._entry.is_global:
            return
        if self._panic_dimmed == dimmed:
            return
        self._panic_dimmed = dimmed
        self.setEnabled(not dimmed)
        self.setProperty("panicDimmed", dimmed)
        # The eye and select icons are hand-painted pixmaps, so they do
        # not follow Qt's disabled palette. Repaint them by hand.
        self._path_label.setText(self._render_path_html())
        self._path_label.set_panic_dimmed(dimmed)
        self._sync_eye_visuals(self._entry.visible)
        self._sync_select_visuals()
        self._repolish_self()

    def clear_select_engaged(self) -> None:
        """Uncheck this row's Select button and repaint it grey.

        Signals are blocked, so the uncheck does not go back out as a
        deselect request.
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
        """Return True when this row's Select button is engaged.

        Only the icon variant is checkable. The text variant always
        returns False.
        """
        btn = self._select_button
        if not btn.isCheckable():
            return False
        return btn.isChecked()

    def update_entry(self, entry: FolderEntry) -> None:
        """Apply ``entry`` to this row without recreating it.

        A rebuild would run ``deleteLater`` and make the folder card
        flash on every refresh.
        """
        self._entry = entry
        self._refresh_from_entry()

    # -- internals --------------------------------------------------------

    def _refresh_from_entry(self) -> None:
        e = self._entry
        self._path_label.setText(self._render_path_html())
        if e.is_global:
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
        prev = self._eye_button.blockSignals(True)
        try:
            self._eye_button.setChecked(not e.visible)
        finally:
            self._eye_button.blockSignals(prev)
        self._sync_eye_visuals(e.visible)
        self._repolish_self()

    def _sync_eye_visuals(self, visible: bool) -> None:
        # The eye is a painted pixmap, so QSS cannot colour it. Repaint
        # it so it follows the row hover state like the ▲ / ▼ / ✕ ctrls.
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
        self._sync_select_visuals()

    # -- select-all icon ------------------------------------------------------

    def _make_select_icon(self, color: str) -> "QtGui.QIcon":
        """Render the active select-all variant in ``color``.

        The colour choice lives in :meth:`_sync_select_visuals`.
        """
        if self._select_variant == "B":
            pix = _render_select_b_pixmap(color)
        elif self._select_variant == "C":
            pix = _render_select_c_pixmap(color)
        else:  # "D"
            pix = _render_select_d_pixmap(color)
        return QtGui.QIcon(pix)

    def _sync_select_visuals(self) -> None:
        """Repaint the select icon for the row hover and checked state.

        The icon is a painted pixmap, so QSS cannot colour it. No-op on
        the TEXT variant, which QSS already drives.
        """
        if self._select_variant not in ("B", "C", "D"):
            return
        if self._panic_dimmed:
            # Panic wins over checked and hover. The row is not
            # interactive while panic is engaged.
            color = _SELECT_PANIC_DIMMED_COLOR
        elif self._select_button.isChecked():
            # Checked wins over hover. Engaged select is the loudest state.
            color = _SELECT_CHECKED_COLOR
        elif bool(self.property("rowHover")):
            color = _SELECT_HOVER_COLOR
        else:
            color = _SELECT_REST_COLOR
        self._select_button.setIcon(self._make_select_icon(color))

    def _on_select_toggled(self, _checked: bool) -> None:
        self._sync_select_visuals()

    def _on_select_clicked(self) -> None:
        """Emit select or deselect from the button's new state.

        Qt flips the checked state before ``clicked`` fires. The text
        variant is not checkable, so it always emits select.
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
        self.setProperty("rowEven", index % 2 == 1)  # index 1 is JSX :nth-child(even)
        self._repolish_self()

    # -- hover state ----------------------------------------------------------

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("rowHover", True)
        # Re-render the path so hidden rows brighten on hover.
        if not self._entry.visible:
            self._path_label.setText(self._render_path_html())
        self._sync_eye_visuals(self._entry.visible)
        self._sync_select_visuals()
        self._repolish_self()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("rowHover", False)
        if not self._entry.visible:
            self._path_label.setText(self._render_path_html())
        self._sync_eye_visuals(self._entry.visible)
        self._sync_select_visuals()
        self._repolish_self()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        # Defer the re-elide by one tick. A synchronous call still reads
        # the pre-resize ``self._path_label.width()``.
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self._refresh_path_label)

    def _refresh_path_label(self) -> None:
        """Re-render the path label against the label's current width."""
        self._path_label.setText(self._render_path_html())

    def _render_path_html(self) -> str:
        """Build the path label HTML at the label's current width.

        The Global Plugins row gets its synthetic label instead of the
        path formatter. Row state still drives the colours.
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
        return html

    @staticmethod
    def _format_global_label_html(visible: bool, *, hovered: bool = False) -> str:
        """Render the Global Plugins row label.

        The leading ``…/`` stands in for the real path, which only the
        tooltip shows.
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
        # The drag handle reads ``rowHover`` in its own paintEvent, so a
        # repolish does not redraw it.
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
        """Render a folder path with a dim parent and a bright leaf.

        - Visible row: dim grey parent, white leaf.
        - Hidden row at rest: the whole path in deep grey.
        - Hidden row hovered: brighter, but under a visible row.
        - Panic-dimmed row: dimmer than hidden at rest.

        With ``elide_width`` in px and a ``font``, the parent is elided
        from the left so the path reads as ``…/parent/leaf``.
        """
        idx = path.rfind("/")
        if idx < 0:
            parent, name = "", path
        else:
            parent = path[: idx + 1]
            name = path[idx + 1:]

        # Elide from the left so the deepest folder name stays visible.
        # When even the leaf is too wide, the parent goes and the leaf
        # is elided from the left instead.
        if elide_width > 0 and font is not None:
            metrics = QtGui.QFontMetrics(font)
            name_w = metrics.horizontalAdvance(name)
            if parent and name_w < elide_width:
                parent_budget = elide_width - name_w
                parent = metrics.elidedText(
                    parent, QtCore.Qt.ElideLeft, parent_budget,
                )
            elif name_w > elide_width:
                parent = ""
                name = metrics.elidedText(
                    name, QtCore.Qt.ElideLeft, elide_width,
                )

        if panic_dimmed:
            # Hover does not brighten a panic row. It is disabled, and
            # the user must disengage panic first.
            dim_color, name_color = "#6c6c6c", "#a0a0a0"
        elif visible:
            dim_color, name_color = "#9a9a9a", "#ffffff"
        elif hovered:
            dim_color, name_color = "#7a7a7a", "#c8c8c8"
        else:
            dim_color, name_color = "#4a4a4a", "#6a6a6a"
        return (
            f'<span style="color:{dim_color}">{_html_escape(parent)}</span>'
            f'<span style="color:{name_color}">{_html_escape(name)}</span>'
        )

    # -- QSS for the row + every control --------------------------------------

    def _apply_row_qss(self) -> None:
        """Install the row's stylesheet.

        QSS does not pass ``:hover`` down to children, so the child
        rules key off the row's ``rowHover`` and ``rowEven`` properties.
        """
        self.setStyleSheet(
            # ---- row container -----------------------------------------------
            # Hover comes last so it wins on hidden rows too. The row
            # paints transparent and takes the stripe colour behind it.
            # A border-bottom would make it 29 px against a 28 px stripe.
            'QFrame#FolderRow[rowDisabled="true"] {'
            '   background: rgba(0, 0, 0, 100);'
            '}'
            # Row hover - translucent Nuke orange. Last, so it overrides
            # the zebra striping and the hidden-row overlay.
            'QFrame#FolderRow[rowHover="true"] { background: rgba(238, 150, 38, 40); }'
            # Hidden and hovered - a faint orange tint so the change of
            # state still reads. Low alpha keeps the path readable.
            'QFrame#FolderRow[rowDisabled="true"][rowHover="true"] {'
            '   background: rgba(238, 150, 38, 18);'
            '}'
            # Dragging - the source row fades so the ghost carries the
            # weight. The slot still reads as part of the list.
            'QFrame#FolderRow[rowDragging="true"] {'
            '   background: rgba(255, 255, 255, 8);'
            '}'
            # ---- ctrls cluster -----------------------------------------------
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
            # ---- Select all, icon variants ----
            # The TEXT variant is a QPushButton and uses the rules above.
            # These rules own the hover pad. ``_sync_select_visuals``
            # owns the icon colour.
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
            # The row-hover rule above lifts the colour whatever the
            # enabled state. Both rules below undo that lift, so the
            # Global row's disabled ✕ never looks clickable.
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


# Shared icon colour tokens. The select-all icon and the eye toggle use
# the same values, so every per-row icon reads at one brightness.
_ICON_REST_COLOR = "#7a7a7a"
_ICON_HOVER_COLOR = "#ffffff"
_SELECT_CHECKED_COLOR = "#ee9626"     # Nuke orange
# Darker than the rest colour on purpose. ``setEnabled(False)`` greys
# the button chrome but not the painted pixmaps. Without this the icons
# would sit brighter than the dimmed path text.
_ICON_PANIC_DIMMED_COLOR = "#4d4d4d"

# Aliases for the select-specific call sites. Kept so an outside caller
# using the old names still works.
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

    The coordinates fit a 16 × 16 grid with a 2 px margin, so the shape
    never crowds the 22 × 22 button.
    """
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))

    s = size / 16.0
    # Arrow outline in order: tip, bottom-left of the body, inner bend.
    # Then bottom-left, bottom-right and top of the tail, and the right
    # shoulder.
    poly = QtGui.QPolygonF([
        QtCore.QPointF(2.5 * s, 2.0 * s),
        QtCore.QPointF(2.5 * s, 12.0 * s),
        QtCore.QPointF(5.5 * s, 10.0 * s),
        QtCore.QPointF(7.0 * s, 13.5 * s),
        QtCore.QPointF(8.5 * s, 12.8 * s),
        QtCore.QPointF(6.7 * s, 9.5 * s),
        QtCore.QPointF(10.8 * s, 9.5 * s),
    ])
    painter.drawPolygon(poly)
    painter.end()
    return pixmap


def _make_eye_icon(visible: bool, color: str = _ICON_REST_COLOR) -> "QtGui.QIcon":
    """Paint the JSX eye / eye-off glyph via ``QPainter`` paths.

    Geometry mirrors ``DirectoryList.jsx``: a 16-unit viewBox, an almond
    outline and a 2-unit pupil. The off variant adds a diagonal slash.
    ``color`` is the stroke colour, which the row brightens on hover.
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
    """QLabel that paints its own strikethrough overlay.

    Qt draws the HTML ``<s>`` line in the colour of the text it strikes,
    so a dim path gives a dim line. This paints a 1.5 px line on top in
    a fixed colour instead.
    """

    # One step dimmer than the Nuke body-text grey ``#c8c8c8``, so the
    # strike does not compete with live text elsewhere.
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
        # Strip the HTML tags so QFontMetrics measures the plain text.
        # The line then stops at the last glyph, not at the label edge.
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
        # Plus 0.5 so the 1.5 px line straddles a pixel cleanly.
        y = rect.center().y() + 0.5
        painter.drawLine(
            QtCore.QPointF(rect.left(), y),
            QtCore.QPointF(rect.left() + text_width, y),
        )
        painter.end()


class _BorderOverlay(QtWidgets.QWidget):
    """Transparent overlay that paints a rounded 1 px border on top.

    It is the last child of ``_RoundedListBox``, so it paints after the
    scroll area and the row container. Mouse events pass through.
    """

    _RADIUS = 4
    # The edge-light hairline token. It reads as a clean edge against
    # the `#393939` panel, without the hardness of black.
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
    """1 px rounded-rect container whose outline survives its children.

    - ``paintEvent`` paints the rounded fill.
    - A ``_BorderOverlay`` child paints the outline last, so the square
      ``QScrollArea`` viewport cannot cover it.
    - ``resizeEvent`` masks the corners away.

    The mask is hard-edged, because Qt needs integer polygons. The
    overlay stroke is anti-aliased and hides the jagged edge.
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

    The stripes give the empty area below the rows a visible structure.
    Real rows paint over them in the normal Qt order. The empty-state
    label is a child here and :meth:`resizeEvent` positions it.
    """

    _STRIPE_HEIGHT_PX = _FOLDER_ROW_HEIGHT_PX
    _STRIPE_COLOUR_A = QtGui.QColor("#383838")
    _STRIPE_COLOUR_B = QtGui.QColor("#3e3e3e")
    _STRIPE_DIVIDER = QtGui.QColor("#2c2c2c")

    def __init__(self, parent=None):
        super().__init__(parent)
        # The FolderCard sets this after construction.
        self._overlay_label: "Optional[QtWidgets.QLabel]" = None

    def set_overlay_label(self, label: "QtWidgets.QLabel") -> None:
        """Register the empty-state label so it stays centered."""
        self._overlay_label = label
        self._reposition_overlay()

    def paintEvent(self, event) -> None:
        """Paint horizontal stripes over the full container area.

        Each stripe is one folder row high, with a 1 px divider at the
        bottom.
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
        """Give the label the whole container and let it centre itself.

        Its ``AlignCenter`` flag is more reliable than a computed rect,
        because ``sizeHint()`` ignores the word-wrap width.
        """
        if self._overlay_label is None:
            return
        if self.width() < 50 or self.height() < 50:
            # Not laid out yet. resizeEvent runs this again with a real size.
            return
        # Inset 16 px so the text does not touch the container edges.
        self._overlay_label.setGeometry(
            16, 16, self.width() - 32, self.height() - 32
        )
        self._overlay_label.raise_()


class _DragGhost(QtWidgets.QWidget):
    """Translucent floating copy of a row that follows the cursor.

    It uses ``QPainter.setOpacity()``, not ``QGraphicsOpacityEffect``.
    The effect can flicker inside a scroll area.
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

    Painted with ``QPainter``, because U+22EE renders as ``: :`` in most
    Qt fonts. Two columns of three dots, in px.
    """

    _DOT_W = 2
    _DOT_H = 2
    _COL_GAP = 3
    _ROW_GAP = 3

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

        # The grip is dim at rest and bright on row hover. It follows
        # the row's ``rowHover`` property, not its own hover state.
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
            # Grab the mouse, or the drag stops as soon as the cursor
            # leaves the 14 px handle.
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

    Owns the :class:`FolderEntry` list and renders one
    :class:`FolderRow` per entry inside a scroll area.

    Signals that carry more than their name:

    * ``add_folder_requested()`` - the card never opens the picker. The
      caller wires it to ``QFileDialog``, so the card runs offscreen.
    * ``reorder_requested(list[str])`` - the new path order, top first.
    * ``remove_confirmed(str)`` - the user confirmed the remove dialog.
    * ``visibility_changed(str, bool)`` - a visual filter only. It does
      not change the scan or the enable state.
    * ``health_inspected(str)`` - never emitted. No row control feeds it.

    ``confirm_remove`` replaces the default confirm dialog. The panel
    wires :func:`nsl.ui.dialogs.confirm_remove_folder` there.
    """

    add_folder_requested = QtCore.Signal()
    rescan_requested = QtCore.Signal()
    reorder_requested = QtCore.Signal(list)
    remove_confirmed = QtCore.Signal(str)
    visibility_changed = QtCore.Signal(str, bool)
    select_requested = QtCore.Signal(str)
    deselect_requested = QtCore.Signal(str)
    open_folder_requested = QtCore.Signal(str)
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

        # 2. Priority indicator, shown only with two or more folders.
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

        # 3. Scroll area with the row stack inside. The wrapper paints
        # its own border. QSS `border-radius` does not anti-alias at
        # 1 px, and it collides with the square viewport.
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

        self._list_container = _StripedListContainer(self._scroll)
        self._list_container.setObjectName("FolderListContainer")
        self._list_layout = QtWidgets.QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)  # rows draw their own dividers
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_container)
        outer.addWidget(self._list_box, 1)

        # 4. Empty-state label. It is positioned by a resize hook, not
        # by the layout. Inside the layout it collapses to its natural
        # height at the top, behind the stretch the stripes need.
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
        self._list_container.set_overlay_label(self._empty_label)

        # The 190 px ``FolderRow`` minimum plus the 8 px paddings on
        # both sides. It stops the splitter from clipping the row icons.
        self.setMinimumWidth(190 + 16)

        self._refresh_empty_state()

    # -- public API -------------------------------------------------------

    def set_entries(self, entries: List[FolderEntry]) -> None:
        """Replace the folder list (top = highest priority).

        Four branches, cheapest first:

        1. Identical entries - return, no UI work.
        2. Same paths in the same order - update the rows in place.
        3. Same paths in another order - move the row widgets in the
           layout. They survive the move with their signals connected.
        4. Path set changed - full rebuild.
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
                self._list_layout.insertWidget(
                    self._list_layout.count() - 1, row
                )
                self._rows.append(row)
            # Without this the arrows stay stale after a reorder.
            self._refresh_arrow_enablement()
            return

        # Branch 4 - path set changed (add / remove).
        self._entries = new_entries
        self._rebuild_rows()

    def set_panic_engaged(self, engaged: bool) -> None:
        """Reflect panic-mode state across the rows.

        Every user row dims and strikes through. The Global row stays
        active, because panic drops user plugins only. The Add and
        Rescan buttons also stay enabled during panic.
        """
        self._panic_engaged = bool(engaged)
        for row in self._rows:
            row.set_panic_dimmed(self._panic_engaged)

    def set_first_run_affordance(self, enabled: bool) -> None:
        """Toggle the nuke-orange first-run border on Add Plugins Folder.

        The panel turns it on while the grid shows its empty state, so
        it also returns mid-session when the last folder is removed.
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
        """Reset every row's Select icon to its default grey state.

        The wiring layer calls it when the grid selection no longer
        matches the engaged folders. No deselect signal goes out.
        """
        for row in self._rows:
            row.clear_select_engaged()

    def engaged_select_paths(self) -> List[str]:
        """Folder paths whose Select icon is engaged (orange).

        The wiring layer reads it to decide if the next Select click
        replaces the selection or adds to it. Folder selects are
        additive with each other.
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
        # Keep the trailing stretch that pushes the rows to the top.
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

        self._refresh_arrow_enablement()
        self._refresh_priority_indicator()
        self._refresh_empty_state()
        # Dim the new rows. Without this a folder add during panic
        # leaves one bright row among dimmed siblings.
        if self._panic_engaged:
            for row in self._rows:
                row.set_panic_dimmed(True)

    def _refresh_arrow_enablement(self) -> None:
        n = len(self._rows)
        # The Global row sits at the bottom and never moves. The row
        # above it cannot move down, or the swap would displace Global.
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
        """Show or hide the empty-state label.

        The scroll area stays visible at any entry count, because its
        container paints the placeholder stripes.
        """
        empty = not self._entries
        self._empty_label.setVisible(empty)
        self._scroll.setVisible(True)
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

        source_row.setProperty("rowDragging", True)
        source_row._repolish_self()  # type: ignore[attr-defined]

        # The list container is the parent. The ghost is then clipped
        # to the list box, so the drag stays inside the panel.
        pixmap = source_row.grab()
        self._drag_ghost = _DragGhost(pixmap, self._list_container)
        ghost_pos = source_row.mapTo(self._list_container, QtCore.QPoint(0, 0))
        self._drag_ghost.move(ghost_pos)
        self._drag_ghost.show()
        self._drag_ghost.raise_()
        # Where in the row the cursor pressed. The ghost keeps that
        # offset while it moves.
        cursor_global = QtGui.QCursor.pos()
        self._drag_press_offset = cursor_global - source_row.mapToGlobal(
            QtCore.QPoint(0, 0)
        )

    def _on_drag_moved(
        self,
        source_path: str,
        global_pos: "QtCore.QPoint",
    ) -> None:
        """Move the ghost and reorder the rows under the cursor."""
        if self._drag_ghost is None or self._drag_source != source_path:
            return

        target_global = global_pos - self._drag_press_offset
        target_local = self._list_container.mapFromGlobal(target_global)
        self._drag_ghost.move(target_local)

        cursor_local = self._list_container.mapFromGlobal(global_pos)
        source_idx = self._index_of(source_path)
        target_idx = self._row_index_at(cursor_local)
        if source_idx < 0 or target_idx < 0:
            return
        if source_idx == target_idx:
            return

        # Move the row in the layout instead of rebuilding the widgets.
        entry = self._entries.pop(source_idx)
        self._entries.insert(target_idx, entry)
        row = self._rows.pop(source_idx)
        self._rows.insert(target_idx, row)
        self._list_layout.removeWidget(row)
        # The trailing stretch is last, so the layout index is target_idx.
        self._list_layout.insertWidget(target_idx, row)
        for i, r in enumerate(self._rows):
            r.set_row_index(i)
        self._refresh_arrow_enablement()

    def _on_drag_released_over(
        self,
        source_path: str,
        global_pos: "QtCore.QPoint",
    ) -> None:
        """End the drag, restore the source row and emit the reorder."""
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

        A point below all rows returns the last one, so a drop at the
        end works. A point above them returns the first.
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
            # Never swap a Global row or a Global neighbour. The arrow
            # is already disabled, but keep the rule local too.
            if self._entries[idx].is_global or self._entries[idx - 1].is_global:
                return
            new_entries = list(self._entries)
            new_entries[idx - 1], new_entries[idx] = (
                new_entries[idx], new_entries[idx - 1],
            )
            # ``set_entries`` takes the in-place branch and keeps the
            # widgets. A rebuild flashes the path label, because a new
            # row elides its path before it reaches its final width.
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

