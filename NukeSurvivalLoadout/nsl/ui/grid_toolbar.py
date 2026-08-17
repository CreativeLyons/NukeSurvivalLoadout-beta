"""Plugins grid toolbar widget.

Sits between the Search/Tags strip and the Plugins pill grid.
Bulk-action buttons on the left, sort-order dropdown on the right.

Signal-out only. The wiring layer pushes counts in with
:meth:`PluginsGridToolbar.set_counts`. The count is the full selection
size, not the subset a filter leaves visible.

All Qt access goes through :mod:`nsl.compat`. Never import PySide2 or
PySide6 directly.
"""

from __future__ import annotations

import enum
from typing import List, Optional, Tuple

from nsl import compat
from nsl.ui._buttons import HybridHoverComboBox, HybridTextButton

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Canonical constants. Do not change the label wording.
# ---------------------------------------------------------------------------


class SortMode(str, enum.Enum):
    """The eight sort options, in canonical order.

    The values are the dropdown's display labels. Default is ``A_TO_Z``.
    """

    A_TO_Z = "A → Z"  # default
    Z_TO_A = "Z → A"
    STATUS = "Status"
    GUI_ONLY = "GUI-only"
    SELECTED = "Selected"
    CHANGED_STATE = "Changed state"
    WARNINGS = "Warnings"
    FOLDER_OF_ORIGIN = "Folder of origin"


#: The one source of sort order, re-exported by :mod:`nsl.ui.sort`. The
#: dropdown in this file builds its rows from ``_SORT_GROUPS`` instead.
SORT_MODE_ORDER: Tuple[SortMode, ...] = (
    SortMode.A_TO_Z,
    SortMode.Z_TO_A,
    SortMode.STATUS,
    SortMode.GUI_ONLY,
    SortMode.SELECTED,
    SortMode.CHANGED_STATE,
    SortMode.WARNINGS,
    SortMode.FOLDER_OF_ORIGIN,
)


#: Bulk-action button labels. ``{n}`` is the current count. Deselect All
#: carries no count. It renders disabled when nothing is selected.
_LABEL_ENABLE = "&Enable Selected ({n})"
_LABEL_DISABLE = "&Disable Selected ({n})"
_LABEL_INVERT = "&Invert Selected ({n})"
_LABEL_SELECT_ALL = "Select &All"
# The mnemonic is L because A, D and E are taken by Select All, Disable
# and Enable.
_LABEL_CLEAR_SELECTION = "Dese&lect All"
_LABEL_SET_GUI_ONLY = "&Set GUI-only ({n})"
_LABEL_CLEAR_GUI_ONLY = "Clear &GUI-only ({n})"
_LABEL_TOGGLE_GUI_ONLY = "&Toggle GUI-only"


# Object names - exposed for testability and to scope styling.
_OBJ_BULK_ENABLE = "nsl_grid_toolbar_enable"
_OBJ_BULK_DISABLE = "nsl_grid_toolbar_disable"
_OBJ_BULK_INVERT = "nsl_grid_toolbar_invert"
_OBJ_BULK_SELECT_ALL = "nsl_grid_toolbar_select_all"
_OBJ_BULK_CLEAR_SELECTION = "nsl_grid_toolbar_clear_selection"
_OBJ_BULK_SET_GUI_ONLY = "nsl_grid_toolbar_set_gui_only"
_OBJ_BULK_CLEAR_GUI_ONLY = "nsl_grid_toolbar_clear_gui_only"
_OBJ_BULK_TOGGLE_GUI_ONLY = "nsl_grid_toolbar_toggle_gui_only"
_OBJ_SORT_DROPDOWN = "nsl_grid_toolbar_sort"
_OBJ_SORT_LABEL = "nsl_grid_toolbar_sort_label"


# Invert Selected and the GUI-only Set/Clear pair are built but never
# shown. Their signals stay alive for nsl/ui/wiring/bulk_ops. The
# counter chips now carry the tallies those buttons used to show.
_V1_INVERT_VISIBLE = False
_V1_GUI_ONLY_VISIBLE = False


# 10 pt keeps the label the same size as the buttons next to it.
_SORT_LABEL_QSS = (
    "QLabel#nsl_grid_toolbar_sort_label {"
    "    color: #7a7a7a;"
    "    font-size: 10pt;"
    "    padding-right: 2px;"
    "}"
)


# Font size only, to match the label. Heavier QComboBox QSS fights
# HybridStyle's hover and pressed paint inside Nuke.
_SORT_COMBO_QSS = (
    "QComboBox#nsl_grid_toolbar_sort {"
    "    font-size: 10pt;"
    "}"
)


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


class PluginsGridToolbar(QtWidgets.QWidget):
    """Plugins grid toolbar.

    Every signal below is a bare click notice with no payload. The one
    exception is ``sort_mode_changed(str)``, which carries the
    :class:`SortMode` value.
    """

    bulk_enable_requested = QtCore.Signal()
    bulk_disable_requested = QtCore.Signal()
    bulk_invert_requested = QtCore.Signal()
    bulk_set_gui_only_requested = QtCore.Signal()
    bulk_clear_gui_only_requested = QtCore.Signal()
    bulk_toggle_gui_only_requested = QtCore.Signal()
    select_all_requested = QtCore.Signal()
    clear_selection_requested = QtCore.Signal()
    sort_mode_changed = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self._selection_count: int = 0
        self._gui_only_count: int = 0

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        # Margins stay at zero. The parent panel owns the gutter, so the
        # strips line up with their neighbours.
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- Bulk-action buttons (left side) -----------------------------

        self._btn_enable = HybridTextButton("", self)
        self._btn_enable.setObjectName(_OBJ_BULK_ENABLE)
        self._btn_enable.clicked.connect(self.bulk_enable_requested)

        self._btn_disable = HybridTextButton("", self)
        self._btn_disable.setObjectName(_OBJ_BULK_DISABLE)
        self._btn_disable.clicked.connect(self.bulk_disable_requested)

        self._btn_invert = HybridTextButton("", self)
        self._btn_invert.setObjectName(_OBJ_BULK_INVERT)
        self._btn_invert.clicked.connect(self.bulk_invert_requested)

        # Stays enabled even when nothing is selected.
        self._btn_select_all = HybridTextButton(_LABEL_SELECT_ALL, self)
        self._btn_select_all.setObjectName(_OBJ_BULK_SELECT_ALL)
        self._btn_select_all.setToolTip(
            "Select every Plugin currently visible in the grid"
        )
        self._btn_select_all.clicked.connect(self.select_all_requested)

        self._btn_clear_selection = HybridTextButton(
            _LABEL_CLEAR_SELECTION, self
        )
        self._btn_clear_selection.setObjectName(_OBJ_BULK_CLEAR_SELECTION)
        self._btn_clear_selection.clicked.connect(self.clear_selection_requested)

        self._btn_set_gui_only = HybridTextButton("", self)
        self._btn_set_gui_only.setObjectName(_OBJ_BULK_SET_GUI_ONLY)
        self._btn_set_gui_only.setToolTip(
            "Set GUI-only on all selected user-added Plugins. "
            "Global Plugins in the selection are skipped silently."
        )
        self._btn_set_gui_only.clicked.connect(self.bulk_set_gui_only_requested)

        self._btn_clear_gui_only = HybridTextButton("", self)
        self._btn_clear_gui_only.setObjectName(_OBJ_BULK_CLEAR_GUI_ONLY)
        self._btn_clear_gui_only.setToolTip(
            "Clear GUI-only on all selected user-added Plugins. "
            "Global Plugins in the selection are skipped silently."
        )
        self._btn_clear_gui_only.clicked.connect(
            self.bulk_clear_gui_only_requested
        )

        # The one visible GUI-only action. The wiring layer picks the
        # direction.
        self._btn_toggle_gui_only = HybridTextButton(
            _LABEL_TOGGLE_GUI_ONLY, self
        )
        self._btn_toggle_gui_only.setObjectName(_OBJ_BULK_TOGGLE_GUI_ONLY)
        self._btn_toggle_gui_only.setToolTip(
            "Sync GUI-only across the selected Plugins: turns it on "
            "unless every selected Plugin already has it on, in which "
            "case it turns them all off. Global Plugins in the "
            "selection are skipped silently."
        )
        self._btn_toggle_gui_only.clicked.connect(
            self.bulk_toggle_gui_only_requested
        )

        _visible_buttons = [
            self._btn_enable,
            self._btn_disable,
            self._btn_toggle_gui_only,
            self._btn_select_all,
            self._btn_clear_selection,
        ]
        if _V1_INVERT_VISIBLE:
            _visible_buttons.insert(2, self._btn_invert)
        else:
            self._btn_invert.setVisible(False)
        if _V1_GUI_ONLY_VISIBLE:
            _visible_buttons.extend(
                [self._btn_set_gui_only, self._btn_clear_gui_only]
            )
        else:
            self._btn_set_gui_only.setVisible(False)
            self._btn_clear_gui_only.setVisible(False)
        for btn in _visible_buttons:
            layout.addWidget(btn)

        layout.addStretch(1)

        # --- Sort-order dropdown (right side) ----------------------------

        sort_label = QtWidgets.QLabel("Sort:", self)
        sort_label.setObjectName(_OBJ_SORT_LABEL)
        layout.addWidget(sort_label)

        # HybridHoverComboBox matches the hover wash of the buttons.
        self._sort = HybridHoverComboBox(self)
        self._sort.setObjectName(_OBJ_SORT_DROPDOWN)
        # Separator lines split the popup into three groups: alphabetical,
        # what the pill is, and how the user got there. GUI-only sits with
        # Status because both say how the plugin loads at next restart.
        _SORT_GROUPS: Tuple[Tuple[SortMode, ...], ...] = (
            (SortMode.A_TO_Z, SortMode.Z_TO_A),
            (
                SortMode.STATUS,
                SortMode.GUI_ONLY,
                SortMode.CHANGED_STATE,
                SortMode.WARNINGS,
            ),
            (SortMode.SELECTED, SortMode.FOLDER_OF_ORIGIN),
        )
        for group_idx, group in enumerate(_SORT_GROUPS):
            if group_idx > 0:
                self._sort.insertSeparator(self._sort.count())
            for mode in group:
                self._sort.addItem(mode.value, userData=mode)
        self._sort.setCurrentText(SortMode.A_TO_Z.value)
        self._sort.currentTextChanged.connect(self._on_sort_text_changed)
        layout.addWidget(self._sort)

        self.setStyleSheet(self.styleSheet() + _SORT_LABEL_QSS + _SORT_COMBO_QSS)

        self._refresh_buttons()

    # ------------------------------------------------------------------
    # Public API consumed by the wiring layer
    # ------------------------------------------------------------------

    def set_counts(
        self,
        selection_count: int,
        gui_only_count: Optional[int] = None,
    ) -> None:
        """Update the counts shown on the bulk buttons.

        ``selection_count`` must be the full selection size, not the
        subset a filter leaves visible. It also gates Deselect All.

        ``gui_only_count`` counts user-added Plugins only. ``None``
        falls back to ``selection_count``.
        """

        if selection_count < 0:
            raise ValueError(
                f"selection_count must be >= 0; got {selection_count}"
            )
        if gui_only_count is not None and gui_only_count < 0:
            raise ValueError(
                f"gui_only_count must be >= 0; got {gui_only_count}"
            )

        self._selection_count = selection_count
        self._gui_only_count = (
            gui_only_count if gui_only_count is not None else selection_count
        )
        self._refresh_buttons()

    def selection_count(self) -> int:
        """Return the full selection count now on display."""
        return self._selection_count

    def gui_only_count(self) -> int:
        """Return the GUI-only bulk count now on display."""
        return self._gui_only_count

    def current_sort_mode(self) -> SortMode:
        """Return the dropdown's current :class:`SortMode`."""
        data = self._sort.currentData()
        if isinstance(data, SortMode):
            return data
        # Fall back to the visible text if userData was lost.
        return SortMode(self._sort.currentText())

    def set_sort_mode(self, mode: SortMode) -> None:
        """Set the dropdown's current sort mode.

        Qt emits :attr:`sort_mode_changed` only when the value changes.
        Resolves by label, not index. Separator rows mean an index no
        longer matches ``SORT_MODE_ORDER``.
        """
        self._sort.setCurrentText(mode.value)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_buttons(self) -> None:
        """Re-format the button labels and disabled states from the counts."""
        n = self._selection_count
        g = self._gui_only_count

        self._btn_enable.setText(_LABEL_ENABLE.format(n=n))
        self._btn_disable.setText(_LABEL_DISABLE.format(n=n))
        self._btn_invert.setText(_LABEL_INVERT.format(n=n))
        self._btn_set_gui_only.setText(_LABEL_SET_GUI_ONLY.format(n=g))
        self._btn_clear_gui_only.setText(_LABEL_CLEAR_GUI_ONLY.format(n=g))

        enable_state = n > 0
        gui_state = g > 0
        for btn in (self._btn_enable, self._btn_disable, self._btn_invert):
            btn.setEnabled(enable_state)
        self._btn_clear_selection.setEnabled(enable_state)
        # Keyed off the selection count, not ``gui_only_count``. Toggle
        # picks its own direction, so that tally must not gate it.
        self._btn_toggle_gui_only.setEnabled(enable_state)
        self._btn_set_gui_only.setEnabled(gui_state)
        self._btn_clear_gui_only.setEnabled(gui_state)

    def _on_sort_text_changed(self, text: str) -> None:
        """Forward the dropdown's text change as a typed signal.

        The string is the stable public identifier. Listeners do not
        need the :class:`SortMode` enum.
        """
        # Separator rows arrive as empty text on some Qt versions. That
        # is not a mode change.
        if not text:
            return
        # Raise on any label that is not canonical. The rows come from
        # ``_SORT_GROUPS``, so this should never fire.
        _ = SortMode(text)
        self.sort_mode_changed.emit(text)


# Counter-strip chip colours. The glyph-only chips
# (``counter_pending_add`` / ``counter_pending_del``) colour the whole
# label from QSS. The labelled chips colour the trailing number only,
# through rich text built in Python.
_COUNTER_LABEL_BASE = "#8a8a8a"
_COUNTER_MUTED      = "#6a6a6a"   # zero state for every number
_COUNTER_VALUE_HOT  = "#c8c8c8"
_COUNTER_GREEN      = "#5fa869"   # Loaded and pending-add
_COUNTER_RED        = "#c46a6a"
_COUNTER_YELLOW     = "#d4a14a"
# Brighter than the design-system purple #827396, which reads grey at
# the 11 px chip size. The per-pill GUI badge uses the same value.
_COUNTER_PURPLE     = "#a78cc9"


_COUNTER_STRIP_QSS = (
    "QFrame#nsl_grid_counters_strip {"
    "    background: transparent;"
    "}"
    "QLabel.nsl_counter_label {"
    "    color: " + _COUNTER_LABEL_BASE + "; font-size: 11px;"
    "    padding: 2px 6px;"
    "    border: 1px solid #2a2a2a;"
    "    border-radius: 3px;"
    "    background: #2e2e2e;"
    "}"
    "QLabel#counter_pending_add[active=\"false\"],"
    "QLabel#counter_pending_del[active=\"false\"] {"
    "    color: " + _COUNTER_MUTED + "; font-weight: 400;"
    "}"
    "QLabel#counter_pending_add[active=\"true\"] {"
    "    color: " + _COUNTER_GREEN + ";"
    "}"
    "QLabel#counter_pending_del[active=\"true\"] {"
    "    color: " + _COUNTER_RED + ";"
    "}"
    # The Logs chip has no hover rule. Its cursor and tooltip say it is
    # clickable.
    "QLabel#counter_logs {"
    "    border-color: #3a3a3a;"
    "}"
)


class GridCounterStrip(QtWidgets.QFrame):
    """Read-only counter chips under the toolbar action row.

    Shows the loaded, selected, GUI-only and pending add/remove tallies.
    The Logs chip is the one interactive chip. A click emits
    :attr:`logs_clicked`. It is hidden, see :meth:`__init__`.
    """

    logs_clicked = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("nsl_grid_counters_strip")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        self._lbl_loaded = self._chip("counter_loaded")
        self._lbl_selected = self._chip("counter_selected")
        self._lbl_pending_add = self._chip("counter_pending_add")
        self._lbl_pending_del = self._chip("counter_pending_del")
        self._lbl_gui = self._chip("counter_gui")
        # The Logs chip is hidden. The failure surface it counts does
        # not exist yet, so it would always read "Logs: 0". To restore
        # it, add ``self._lbl_logs`` back to the layout loop below.
        self._lbl_logs = self._chip("counter_logs")
        self._lbl_logs.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self._lbl_logs.setToolTip(
            "Click to view session logs for problematic Plugins"
        )
        self._lbl_logs.installEventFilter(self)
        self._lbl_logs.hide()

        for lbl in (
            self._lbl_loaded,
            self._lbl_selected,
            self._lbl_gui,
            self._lbl_pending_add,
            self._lbl_pending_del,
        ):
            layout.addWidget(lbl)
        layout.addStretch(1)
        self.setStyleSheet(_COUNTER_STRIP_QSS)
        self.set_counters(0, 0, 0, 0, 0, 0, 0)

    def eventFilter(self, obj, ev):  # noqa: N802 - Qt API name
        # A mouse release inside the Logs chip counts as a click. An
        # event filter avoids subclassing QLabel for one event.
        if obj is self._lbl_logs and ev.type() == QtCore.QEvent.MouseButtonRelease:
            if ev.button() == QtCore.Qt.LeftButton and self._lbl_logs.rect().contains(ev.pos()):
                self.logs_clicked.emit()
        return super().eventFilter(obj, ev)

    def _chip(self, object_name: str) -> "QtWidgets.QLabel":
        lbl = QtWidgets.QLabel("", self)
        lbl.setObjectName(object_name)
        lbl.setProperty("class", "nsl_counter_label")
        # RichText turns on the inline colour spans. PlainText would show
        # the markup as literal angle brackets.
        lbl.setTextFormat(QtCore.Qt.RichText)
        return lbl

    @staticmethod
    def _split_chip(label: str, number_text: str, number_colour: str) -> str:
        """Compose a labelled chip's rich-text body.

        Only ``number_text`` takes ``number_colour``. The weight is the
        same for both, so colour alone carries the meaning.
        """
        return (
            f"{label} "
            f"<span style='color:{number_colour};'>"
            f"{number_text}</span>"
        )

    def set_counters(
        self,
        selected: int,
        total: int,
        pending_add: int,
        pending_del: int,
        gui_only: int,
        logs: int,
        loaded: int = 0,
    ) -> None:
        # Loaded is the boot-time manifest total for this Nuke session.
        # It ignores grid filtering and folder deletes. A loaded plugin
        # stays counted after its folder is gone.
        loaded_colour = _COUNTER_GREEN if loaded > 0 else _COUNTER_MUTED
        self._lbl_loaded.setText(
            self._split_chip("Loaded:", str(loaded), loaded_colour)
        )
        selected_colour = (
            _COUNTER_VALUE_HOT if selected > 0 else _COUNTER_MUTED
        )
        self._lbl_selected.setText(
            self._split_chip(
                "Selected:", f"{selected} / {total}", selected_colour
            )
        )
        gui_colour = _COUNTER_PURPLE if gui_only > 0 else _COUNTER_MUTED
        self._lbl_gui.setText(
            self._split_chip("GUI:", str(gui_only), gui_colour)
        )
        # Logs collapses errors and missing into one count.
        logs_colour = _COUNTER_YELLOW if logs > 0 else _COUNTER_MUTED
        self._lbl_logs.setText(
            self._split_chip("Logs:", str(logs), logs_colour)
        )

        self._lbl_pending_add.setText(f"+{pending_add}")
        self._lbl_pending_add.setToolTip(
            f"{pending_add} Plugin(s) will be loaded on next Save"
        )
        self._lbl_pending_del.setText(f"−{pending_del}")
        self._lbl_pending_del.setToolTip(
            f"{pending_del} Plugin(s) will be unloaded on next Save"
        )

        for lbl, value in (
            (self._lbl_pending_add, pending_add),
            (self._lbl_pending_del, pending_del),
        ):
            lbl.setProperty("active", "true" if value > 0 else "false")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

