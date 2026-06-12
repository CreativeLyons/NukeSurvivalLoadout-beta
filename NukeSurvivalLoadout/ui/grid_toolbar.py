"""Plugins grid toolbar widget.

The toolbar sits between the Search/Tags strip above and the Plugins
pill grid below. It carries bulk-action buttons on the left, each
labelled with its live selection count (e.g. ``Enable Selected (N)``,
``Disable Selected (N)``), and a sort-order dropdown on the right.

Signal-out only: the toolbar does not own the selection model or the
grid's sort state. The wiring layer pushes counts in via
:meth:`PluginsGridToolbar.set_counts` and listens to the emitted
signals.

Key behaviour:

* Always visible - never hidden by selection state. When the selection
  is empty the bulk buttons render disabled and their count reads ``0``.
* The count surfaced on the bulk buttons is **the full selection
  size**, not the visible-after-filter subset: a user with 12 pills
  selected and a search filter narrowing visible pills to 4 still sees
  ``Disable Selected (12)``.
* The two GUI-only buttons emit signals carrying the full selection.
  Global Plugins inside that selection are skipped at the wiring
  layer; this widget does not know about provenance.
* The sort-order selection is **panel-local and per-session**. The
  toolbar emits ``sort_mode_changed(mode)`` so the grid can re-sort;
  no persistence happens here.

All Qt access goes through :mod:`NukeSurvivalLoadout.compat` - never
``import PySide2`` or ``import PySide6`` directly.
"""

from __future__ import annotations

import enum
from typing import List, Optional, Tuple

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.ui._buttons import HybridHoverComboBox, HybridTextButton

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# ---------------------------------------------------------------------------
# Canonical constants (wording is locked - do not edit casually)
# ---------------------------------------------------------------------------


class SortMode(str, enum.Enum):
    """The seven sort options, in their **canonical order**.

    Default is ``A_TO_Z``. The dropdown's display labels are the enum
    *values* (the wording is locked - do not edit casually).
    """

    A_TO_Z = "A → Z"  # default
    Z_TO_A = "Z → A"
    STATUS = "Status"
    SELECTED = "Selected"
    CHANGED_STATE = "Changed state"
    WARNINGS = "Warnings"
    FOLDER_OF_ORIGIN = "Folder of origin"


#: Ordered tuple of sort options in canonical order - the dropdown populates
#: from this. Keeping the list as a module constant means downstream
#: readers (and the sort module wired to the grid) can import a single
#: source of truth.
SORT_MODE_ORDER: Tuple[SortMode, ...] = (
    SortMode.A_TO_Z,
    SortMode.Z_TO_A,
    SortMode.STATUS,
    SortMode.SELECTED,
    SortMode.CHANGED_STATE,
    SortMode.WARNINGS,
    SortMode.FOLDER_OF_ORIGIN,
)


#: Bulk-action button labels - formatted with the current count where a
#: count applies. ``Clear Selection`` carries no count by design: it
#: renders disabled when no Plugins are selected - the disabled state is
#: the signal, not a "(0)" suffix.
_LABEL_ENABLE = "&Enable Selected ({n})"
_LABEL_DISABLE = "&Disable Selected ({n})"
_LABEL_INVERT = "&Invert Selected ({n})"
_LABEL_SELECT_ALL = "Select &All"
# "Deselect All" reads as the explicit complement of "Select All".
# Mnemonic uses L because A is taken by Select All, D by Disable, and
# E by Enable.
_LABEL_CLEAR_SELECTION = "Dese&lect All"
_LABEL_SET_GUI_ONLY = "&Set GUI-only ({n})"
_LABEL_CLEAR_GUI_ONLY = "Clear &GUI-only ({n})"


# Object names - exposed for testability and to scope styling.
_OBJ_BULK_ENABLE = "nsl_grid_toolbar_enable"
_OBJ_BULK_DISABLE = "nsl_grid_toolbar_disable"
_OBJ_BULK_INVERT = "nsl_grid_toolbar_invert"
_OBJ_BULK_SELECT_ALL = "nsl_grid_toolbar_select_all"
_OBJ_BULK_CLEAR_SELECTION = "nsl_grid_toolbar_clear_selection"
_OBJ_BULK_SET_GUI_ONLY = "nsl_grid_toolbar_set_gui_only"
_OBJ_BULK_CLEAR_GUI_ONLY = "nsl_grid_toolbar_clear_gui_only"
_OBJ_SORT_DROPDOWN = "nsl_grid_toolbar_sort"
_OBJ_SORT_LABEL = "nsl_grid_toolbar_sort_label"


# The visible action set is the three mutators only:
#   Enable Selected (N) | Disable Selected (N) | Clear Selection
# Invert Selected and the GUI-only Set/Clear pair are constructed but
# kept hidden - the signals stay alive so wiring
# (NukeSurvivalLoadout/ui/wiring/bulk_ops) keeps working, and the buttons
# can be re-shown cheaply if/when the design changes. Counters
# (Pending +X / -Y, GUI, Errors, Missing) replace the count semantics
# those parked buttons used to surface.
_V1_INVERT_VISIBLE = False
_V1_GUI_ONLY_VISIBLE = False


# Sort label: muted per canonical (`.sort-label { color: #7a7a7a; }`).
# Font-size 10 pt so the label scales with the panel-wide control
# vocabulary instead of reading larger than the buttons next to it.
_SORT_LABEL_QSS = (
    "QLabel#nsl_grid_toolbar_sort_label {"
    "    color: #7a7a7a;"
    "    font-size: 10pt;"
    "    padding-right: 2px;"
    "}"
)


# Sort dropdown: only set font-size - body chrome left to HybridStyle /
# Fusion (heavy QComboBox QSS fights HybridStyle's hover/pressed paint
# inside Nuke, so we touch nothing else). 10 pt matches the label so the
# combo reads in scale with the rest of the panel's controls.
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

    Always-visible horizontal strip with six bulk-action buttons on the
    left and the sort-order dropdown on the right. Signal-out only - the
    toolbar does not own the selection model or the sort state of the
    grid; the wiring layer pushes counts in via :meth:`set_counts` and
    listens to the emitted signals.

    Signals:
        bulk_enable_requested(): the user clicked ``Enable Selected``.
        bulk_disable_requested(): the user clicked ``Disable Selected``.
        bulk_invert_requested(): the user clicked ``Invert Selected``.
        bulk_set_gui_only_requested(): the user clicked ``Set GUI-only``.
        bulk_clear_gui_only_requested(): the user clicked ``Clear GUI-only``.
        select_all_requested(): the user clicked ``Select All``.
        clear_selection_requested(): the user clicked ``Clear Selection``.
        sort_mode_changed(str): the dropdown's value changed. The
            emitted string is :class:`SortMode` value (the verbatim
            label, e.g. ``"A → Z"``).
    """

    bulk_enable_requested = QtCore.Signal()
    bulk_disable_requested = QtCore.Signal()
    bulk_invert_requested = QtCore.Signal()
    bulk_set_gui_only_requested = QtCore.Signal()
    bulk_clear_gui_only_requested = QtCore.Signal()
    select_all_requested = QtCore.Signal()
    clear_selection_requested = QtCore.Signal()
    sort_mode_changed = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # Current counts - start at zero (no selection).
        self._selection_count: int = 0
        self._gui_only_count: int = 0

        # Sized to one row, expanding horizontally, fixed vertically.
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )

        # Outer margins are zero - parent panel owns the 12px gutter so
        # strips align flush vertically with their neighbours.
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

        # Select All - selects every Plugin currently visible in the
        # grid. Always enabled regardless of selection state (the
        # action is meaningful even when nothing is selected; it's the
        # complement of Clear Selection).
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

        # Visible action set: Enable / Disable / Clear Selection only.
        # Invert and the GUI-only pair are constructed above (signals
        # alive for wiring) but never added to the layout. The counter
        # strip elsewhere surfaces the diff / GUI / error / missing
        # tallies those parked buttons used to carry.
        _visible_buttons = [
            self._btn_enable,
            self._btn_disable,
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

        # HybridHoverComboBox carries the same translucent-white hover wash
        # + pointing-hand cursor as the sibling HybridTextButton instances,
        # so the Sort dropdown reads at the same hover intensity as the
        # bulk-action buttons next to it on the toolbar row.
        self._sort = HybridHoverComboBox(self)
        self._sort.setObjectName(_OBJ_SORT_DROPDOWN)
        # Left unstyled on purpose - HybridStyle paints the combo
        # natively in-Nuke. Stylesheet-painted combos lose hover/press
        # feedback when HybridStyle takes over.
        #
        # Visual grouping: divider lines separate semantic clusters in
        # the popup. Alphabetical sits on its own; the rest splits into
        # "Plugin state" (Status / Changed state / Warnings - what the
        # pill is) and "User / origin" (Selected / Folder of origin -
        # how the user got there).
        _SORT_GROUPS: Tuple[Tuple[SortMode, ...], ...] = (
            (SortMode.A_TO_Z, SortMode.Z_TO_A),
            (SortMode.STATUS, SortMode.CHANGED_STATE, SortMode.WARNINGS),
            (SortMode.SELECTED, SortMode.FOLDER_OF_ORIGIN),
        )
        for group_idx, group in enumerate(_SORT_GROUPS):
            if group_idx > 0:
                self._sort.insertSeparator(self._sort.count())
            for mode in group:
                # Store the enum *value* (the verbatim label) as the
                # visible text; userData carries the enum for future-
                # proofing.
                self._sort.addItem(mode.value, userData=mode)
        # Default to A -> Z.
        self._sort.setCurrentText(SortMode.A_TO_Z.value)
        self._sort.currentTextChanged.connect(self._on_sort_text_changed)
        layout.addWidget(self._sort)

        # Bulk action buttons are HybridTextButton - bare native-style
        # chrome plus the panel-wide hover wash, matching every other
        # action button on the panel. The Sort label keeps its muted
        # 10 pt label QSS; the combo body is left unstyled (HybridStyle
        # paints it natively in-Nuke; Fusion paints it when running
        # outside Nuke).
        self.setStyleSheet(self.styleSheet() + _SORT_LABEL_QSS + _SORT_COMBO_QSS)

        # Apply the initial labels (count = 0) and disabled states.
        self._refresh_buttons()

    # ------------------------------------------------------------------
    # Public API consumed by the wiring layer
    # ------------------------------------------------------------------

    def set_counts(
        self,
        selection_count: int,
        gui_only_count: Optional[int] = None,
    ) -> None:
        """Update the selection count surfaced on the bulk buttons.

        ``selection_count`` drives ``Enable Selected (N)``,
        ``Disable Selected (N)``, ``Invert Selected (N)``, and the
        enabled / disabled state of ``Clear Selection``. The
        count is the **full selection size**, not the visible-after-
        filter subset - the wiring layer is responsible for passing the
        full count.

        ``gui_only_count`` drives ``Set GUI-only (N)`` and
        ``Clear GUI-only (N)``. These bulk actions apply to
        all selected Plugins; the wiring layer skips Global
        Plugins silently (signal-out from this widget carries no
        provenance). If ``gui_only_count`` is ``None``, falls back to
        ``selection_count`` so the wiring layer can call
        ``set_counts(N)`` for the simple case where every selected
        Plugin is user-added.
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
        """Return the currently-displayed full selection count."""
        return self._selection_count

    def gui_only_count(self) -> int:
        """Return the currently-displayed GUI-only bulk count."""
        return self._gui_only_count

    def current_sort_mode(self) -> SortMode:
        """Return the dropdown's current :class:`SortMode`."""
        data = self._sort.currentData()
        if isinstance(data, SortMode):
            return data
        # Belt-and-braces: resolve by text if userData was somehow lost.
        return SortMode(self._sort.currentText())

    def set_sort_mode(self, mode: SortMode) -> None:
        """Programmatically set the dropdown's current sort mode.

        Emits :attr:`sort_mode_changed` if the value changes (Qt's
        ``currentTextChanged`` signal handles emission); does not emit
        if the mode is already current.

        Resolves by label rather than by index - the popup includes
        separator rows between semantic groups, so the index of any
        given mode no longer matches ``SORT_MODE_ORDER``.
        """
        self._sort.setCurrentText(mode.value)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_buttons(self) -> None:
        """Re-format the button labels and disabled states from counts.

        Empty selection greys out every bulk button - including
        ``Clear Selection`` which has no count suffix but still
        renders disabled when no Plugins are selected.
        """
        n = self._selection_count
        g = self._gui_only_count

        self._btn_enable.setText(_LABEL_ENABLE.format(n=n))
        self._btn_disable.setText(_LABEL_DISABLE.format(n=n))
        self._btn_invert.setText(_LABEL_INVERT.format(n=n))
        self._btn_set_gui_only.setText(_LABEL_SET_GUI_ONLY.format(n=g))
        self._btn_clear_gui_only.setText(_LABEL_CLEAR_GUI_ONLY.format(n=g))
        # Clear Selection has no count suffix.

        enable_state = n > 0
        gui_state = g > 0
        for btn in (self._btn_enable, self._btn_disable, self._btn_invert):
            btn.setEnabled(enable_state)
        self._btn_clear_selection.setEnabled(enable_state)
        self._btn_set_gui_only.setEnabled(gui_state)
        self._btn_clear_gui_only.setEnabled(gui_state)

    def _on_sort_text_changed(self, text: str) -> None:
        """Forward the dropdown's text change as a typed signal.

        Re-emits the canonical string so listeners do not need to
        depend on the :class:`SortMode` enum (the string IS the
        public, stable identifier - the enum is a convenience).
        """
        # Separator rows surface as empty strings on some Qt versions
        # when the popup is opened; never treat that as a mode change.
        if not text:
            return
        # Don't lose typos to the enum's coercion - if the text isn't
        # one of the canonical labels, raise. The dropdown is
        # populated from SORT_MODE_ORDER, so this should never fire.
        _ = SortMode(text)  # validation
        self.sort_mode_changed.emit(text)


# Counter-strip chrome - quiet inline chips. Two kinds of chips:
#
# 1. Glyph-only chips (``counter_pending_add`` / ``counter_pending_del``)
#    are pure numeric badges (``+3``, ``−2``). The whole label takes
#    the meaning colour when active; muted grey when zero.
#
# 2. Labelled chips (``counter_selected`` / ``counter_gui`` /
#    ``counter_errors`` / ``counter_missing``) render the label
#    (``Errors:``) in the normal chip text colour and only the
#    trailing number takes the meaning colour. Done with rich-text
#    QLabel + per-span <font color> so QSS doesn't have to fight
#    char-format inheritance. The colours below are referenced from
#    Python (not QSS) for the labelled chips.
_COUNTER_LABEL_BASE = "#8a8a8a"   # normal label / neutral chip text
_COUNTER_MUTED      = "#6a6a6a"   # zero-state number
# Active selected-count number - matches design-system --text-primary
# (canonical body text brightness; lit but not flash-white). Sourced
# from NSL_Design_System_New/colors_and_type.css.
_COUNTER_VALUE_HOT  = "#c8c8c8"
_COUNTER_GREEN      = "#5fa869"   # pending-add
_COUNTER_RED        = "#c46a6a"   # pending-remove
_COUNTER_YELLOW     = "#d4a14a"   # logs (problematic plugins) number
# GUI-only colour - the canonical design-system purple
# (#827396 = rgb(130,115,150), "muted desaturated") reads too grey at
# the 11px chip size next to the other coloured chips, so we lift it
# toward the same hue but ~30 units brighter. Still desaturated enough
# to not slip back into the magenta zone (rejected #c97fd0). Mirrored
# on the per-pill GUI badge so the chip + badge agree on tone.
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
    # Glyph-only diff chips colour the whole label.
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
    # Logs chip - quiet by default, no hover state. The clickability
    # signal lives in the cursor change + tooltip; the chip itself
    # doesn't visually react to mouse movement so it reads as ambient
    # like the other counter chips.
    "QLabel#counter_logs {"
    "    border-color: #3a3a3a;"
    "}"
)


class GridCounterStrip(QtWidgets.QFrame):
    """Read-only counter chips that sit under the toolbar action row.

    Shows at-a-glance state of the whole grid: how many pills are
    selected, how many will be added / removed on next Save (the diff
    counts - the toolbar's three action buttons produce these), how
    many are GUI-only, and how many problematic Plugins exist (the
    *Logs* chip collapses error + missing into one click-to-open
    affordance for the session-log preview).

    Mostly informational. The Logs chip is interactive - clicking it
    emits :attr:`logs_clicked`, which the panel can wire to the
    log-viewer pane.
    """

    logs_clicked = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("nsl_grid_counters_strip")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        # Loaded - count of pills loaded at the start of this Nuke
        # session.
        self._lbl_loaded = self._chip("counter_loaded")
        self._lbl_selected = self._chip("counter_selected")
        self._lbl_pending_add = self._chip("counter_pending_add")
        self._lbl_pending_del = self._chip("counter_pending_del")
        self._lbl_gui = self._chip("counter_gui")
        # Logs chip - hidden from the panel. The per-plugin
        # failure/missing surface it summarises does not exist, so it
        # would always read "Logs: 0" and be stale noise. The widget,
        # the logs_clicked signal, the event filter, and the
        # set_counters plumbing are all retained so the chip can be
        # re-enabled cheaply once a live failure surface exists - it is
        # simply not added to the strip layout below and is explicitly
        # hidden. To restore it, re-add self._lbl_logs to the layout loop.
        self._lbl_logs = self._chip("counter_logs")
        self._lbl_logs.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self._lbl_logs.setToolTip(
            "Click to view session logs for problematic Plugins"
        )
        self._lbl_logs.installEventFilter(self)
        self._lbl_logs.hide()

        # Chip order - Loaded on the far left, then Selected, GUI, and the
        # diff chips (+N / -N). The Logs chip is hidden (see above), so the
        # diff chips follow GUI directly. The banner sits to the right of
        # the whole strip in counters_row.
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
        # Treat a mouse-release inside the Logs chip as a click. Using
        # an event filter avoids subclassing QLabel for the sole purpose
        # of catching one event.
        if obj is self._lbl_logs and ev.type() == QtCore.QEvent.MouseButtonRelease:
            if ev.button() == QtCore.Qt.LeftButton and self._lbl_logs.rect().contains(ev.pos()):
                self.logs_clicked.emit()
        return super().eventFilter(obj, ev)

    def _chip(self, object_name: str) -> "QtWidgets.QLabel":
        lbl = QtWidgets.QLabel("", self)
        lbl.setObjectName(object_name)
        # Class name routes through `.nsl_counter_label` selector for
        # the shared chip chrome; per-object QSS overrides colour.
        lbl.setProperty("class", "nsl_counter_label")
        # Labelled chips paint their number colour via inline <font>
        # spans (so only the digit takes the meaning colour, not the
        # word "Errors:"). RichText turns those spans on; PlainText
        # would render the markup as literal angle-brackets.
        lbl.setTextFormat(QtCore.Qt.RichText)
        return lbl

    @staticmethod
    def _split_chip(label: str, number_text: str, number_colour: str) -> str:
        """Compose a labelled chip's rich-text body.

        ``label`` ("Selected:", "GUI:", "Logs:") renders in the chip's
        normal text colour - inherited from the QSS ``QLabel.nsl_counter_label``
        rule. Only the trailing ``number_text`` takes ``number_colour``.
        Both label and number render at the chip's default weight; the
        meaning is carried entirely by colour, not weight.
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
        # Loaded - fixed count of plugins NSL loaded into this Nuke session
        # (the panel passes the boot-time manifest total). Green when > 0;
        # muted at 0. Does NOT adjust with grid filtering or a mid-session
        # folder delete - it is session-total truth, so a loaded plugin
        # stays counted even after its folder is gone.
        loaded_colour = _COUNTER_GREEN if loaded > 0 else _COUNTER_MUTED
        self._lbl_loaded.setText(
            self._split_chip("Loaded:", str(loaded), loaded_colour)
        )
        # Selected - number is white when any pill is selected, muted
        # grey otherwise. Label stays neutral.
        selected_colour = (
            _COUNTER_VALUE_HOT if selected > 0 else _COUNTER_MUTED
        )
        self._lbl_selected.setText(
            self._split_chip(
                "Selected:", f"{selected} / {total}", selected_colour
            )
        )
        # GUI - relabelled "GUI:"; number is purple when > 0, muted at 0.
        gui_colour = _COUNTER_PURPLE if gui_only > 0 else _COUNTER_MUTED
        self._lbl_gui.setText(
            self._split_chip("GUI:", str(gui_only), gui_colour)
        )
        # Logs - collapses "errors" + "missing" into one click-to-open
        # affordance. Yellow when > 0 (something to look at), muted
        # grey at zero.
        logs_colour = _COUNTER_YELLOW if logs > 0 else _COUNTER_MUTED
        self._lbl_logs.setText(
            self._split_chip("Logs:", str(logs), logs_colour)
        )

        # Glyph-only diff chips - whole-label colouring via QSS [active] state.
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

