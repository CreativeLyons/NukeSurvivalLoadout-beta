"""NSL Loadout Panel - Search and Tags strip.

The discovery surface above the pill grid: a search field, Select and
Deselect filtered, and Clear selection.

``Reset``, ``Invert``, and the tag-chip row stay hidden until
``_V2_TAGS_ENABLED`` is True. The widgets are still built, so call sites
that reference ``strip.reset_btn`` need no branch.

The strip owns no pill state and emits signals only. Filter state is
per-session and panel-local. A Loadout switch does not clear it, and
restarting Nuke does.

Qt comes from :mod:`nsl.compat`. No direct PySide import in this file.
"""

from __future__ import annotations

from typing import Iterable, List


# ---------------------------------------------------------------------------
# Pure-Python filter logic
# ---------------------------------------------------------------------------


def match_query(query: str, plugin_name: str) -> bool:
    """Return True if ``plugin_name`` matches the search ``query``.

    Case-insensitive substring. An empty query matches every name. Tag
    and description matching belongs in the filter pipeline.
    """
    if query is None:
        return True
    q = query.strip()
    if not q:
        return True
    return q.casefold() in plugin_name.casefold()


def filter_visible(query: str, plugin_names: Iterable[str]) -> List[str]:
    """Return the ``plugin_names`` that match ``query``, in input order."""
    return [name for name in plugin_names if match_query(query, name)]


# ---------------------------------------------------------------------------
# Qt widget
# ---------------------------------------------------------------------------

# The Qt import sits after the pure helpers. ``match_query`` and
# ``filter_visible`` then import on a host with no PySide.
from nsl import compat  # noqa: E402 - kept after pure helpers on purpose
from nsl.ui._buttons import HybridTextButton  # noqa: E402

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# Gate for the whole tag system. The chip widget is still built when
# off, so call sites using ``strip.none_chip`` keep working.
_V2_TAGS_ENABLED = False


# Grey body and italic label, so the None chip reads as system, not as a
# user chip. A user chip is outlined in the tag colour.
_NONE_CHIP_QSS = (
    "QToolButton#NSL_NoneChip {"
    "    background-color: #4a4a4a;"
    "    color: #cfcfcf;"
    "    border: 1px solid #5a5a5a;"
    "    border-radius: 2px;"
    "    padding: 2px 10px;"
    "    font-style: italic;"
    "}"
)


# Search field chrome, from the canonical panel.css ``.search-input``.
# Qt QSS has no ``box-shadow``, so ``_SearchField.paintEvent`` paints
# the orange focus halo outside the dark border, which survives focus.
_SEARCH_QSS = (
    "QLineEdit#NSL_SearchField {"
    "    background-color: #303030;"  # a hair dimmer than canonical #383838
    "    color: #ffffff;"
    # 11 px and 4,8 padding so the field height matches
    # ``HybridTextButton`` under Fusion.
    "    font-size: 11px;"
    "    padding: 4px 8px;"
    "    margin: 1px;"  # reserve 1 px outside the QSS border for the halo
    "    border: 1px solid #1a1a1a;"
    "    border-radius: 4px;"
    "    selection-background-color: #c9a373;"
    "    selection-color: #1a1a1a;"
    "}"
    "QLineEdit#NSL_SearchField:focus {"
    # No body lift on focus. The dark inner border and the orange outer
    # halo carry the focus signal on their own.
    "    background-color: #303030;"
    "    border: 1px solid #2a2a2a;"  # canonical keeps a dark border on focus
    "}"
)


# Outer-halo tint used on focus. A desaturated orange, as low in
# contrast as the canonical ``rgba(86,160,244,0.25)`` blue it replaces.
_FOCUS_HALO_COLOR = QtGui.QColor("#7a5a32")


def _make_clear_glyph_icon(
    size: int = 12,
    disc_color: str = "#9a9a9a",
) -> "QtGui.QIcon":
    """Return a small "circle with × cut out" glyph icon.

    ``CompositionMode_Clear`` punches the × through a filled disc. The
    disc stops the glyph reading as a stray character after the typed
    word. Use ``#9a9a9a`` for ``disc_color`` at rest and ``#ffffff`` on
    hover. Paints at 2x DPR so the cut edges stay clean on Retina.
    """
    scale = 2
    pixmap = QtGui.QPixmap(size * scale, size * scale)
    pixmap.fill(QtCore.Qt.transparent)
    pixmap.setDevicePixelRatio(scale)

    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Inset 0.5 px so the antialiased edge lands inside the bounds.
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(disc_color))
        rect = QtCore.QRectF(0.5, 0.5, size - 1.0, size - 1.0)
        painter.drawEllipse(rect)

        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
        pen = QtGui.QPen(QtCore.Qt.transparent)
        pen.setWidthF(1.4)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        a = 3.5
        b = size - 3.5
        painter.drawLine(QtCore.QPointF(a, a), QtCore.QPointF(b, b))
        painter.drawLine(QtCore.QPointF(b, a), QtCore.QPointF(a, b))
    finally:
        painter.end()
    return QtGui.QIcon(pixmap)


class _ClearGlyphButton(QtWidgets.QToolButton):
    """Tool button that swaps its icon between rest and hover.

    QSS ``:hover`` cannot change ``setIcon``, only background, border,
    and colour. Both icons are rendered at construction and swapped in
    :meth:`enterEvent` and :meth:`leaveEvent`.
    """

    def __init__(
        self,
        glyph_size: int,
        parent: "QtWidgets.QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self._rest_icon = _make_clear_glyph_icon(glyph_size, "#9a9a9a")
        self._hover_icon = _make_clear_glyph_icon(glyph_size, "#ffffff")
        self.setIcon(self._rest_icon)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.setIcon(self._rest_icon)
        super().leaveEvent(event)


class _SearchField(QtWidgets.QLineEdit):
    """QLineEdit that paints a 1 px outer orange halo when focused.

    ``margin: 1px`` in ``_SEARCH_QSS`` insets the dark QSS border, so
    the halo sits outside it and both layers stay visible.

    The field owns its own × clear glyph, shown only while text is
    present. :meth:`QLineEdit.setClearButtonEnabled` is not used because
    its generic dark-circle × does not match the panel chrome.
    """

    _CLEAR_GLYPH_SIZE = 12
    _CLEAR_RIGHT_PAD = 6
    # Gap between the typed text and the × glyph. Wide enough that the
    # disc reads as a control, not as punctuation.
    _CLEAR_TEXT_GAP = 10

    def __init__(self, parent: "QtWidgets.QWidget | None" = None) -> None:
        super().__init__(parent)

        self._clear_btn = _ClearGlyphButton(self._CLEAR_GLYPH_SIZE, self)
        self._clear_btn.setObjectName("NSL_SearchClearButton")
        self._clear_btn.setIconSize(
            QtCore.QSize(self._CLEAR_GLYPH_SIZE, self._CLEAR_GLYPH_SIZE)
        )
        self._clear_btn.setCursor(QtCore.Qt.ArrowCursor)
        self._clear_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._clear_btn.setToolTip("Clear search")
        # The 12 px disc needs a visible background pad to read as a
        # click target. Pressed darkens to confirm the click.
        self._clear_btn.setStyleSheet(
            "QToolButton#NSL_SearchClearButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "}"
            "QToolButton#NSL_SearchClearButton:hover {"
            "  background-color: rgba(255,255,255,0.18);"
            "  border-radius: 4px;"
            "}"
            "QToolButton#NSL_SearchClearButton:pressed {"
            "  background-color: rgba(255,255,255,0.10);"
            "  border-radius: 4px;"
            "}"
        )
        # Hit area is the glyph plus 8 px. The hover pad then reads as a
        # button around the disc, not as a tight crop.
        self._clear_btn.setFixedSize(
            QtCore.QSize(self._CLEAR_GLYPH_SIZE + 8, self._CLEAR_GLYPH_SIZE + 8)
        )
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self.clear)

        # Right-side text margin, so the typed text never slides under
        # the glyph once it clamps to the right edge.
        self.setTextMargins(
            0, 0, self._clear_btn.width() + self._CLEAR_RIGHT_PAD, 0
        )

        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text: str) -> None:
        self._clear_btn.setVisible(bool(text))
        self._reposition_clear_glyph()

    def _reposition_clear_glyph(self) -> None:
        """Place the × after the text, clamped to the right edge.

        The glyph follows the last character, so the user can clear in a
        short mouse move. It clamps at ``right_edge - pad`` and never
        leaves the field. ``cursorPosition`` is avoided because it would
        move the glyph with the caret.
        """
        text = self.text()
        max_x = (
            self.rect().right()
            - self._clear_btn.width()
            - self._CLEAR_RIGHT_PAD
        )
        if not text:
            # Hidden anyway, but keep position sane for the next show.
            x = max_x
        else:
            opt = QtWidgets.QStyleOptionFrame()
            self.initStyleOption(opt)
            text_rect = self.style().subElementRect(
                QtWidgets.QStyle.SE_LineEditContents, opt, self
            )
            text_width = self.fontMetrics().horizontalAdvance(text)
            ideal = text_rect.left() + text_width + self._CLEAR_TEXT_GAP
            x = min(ideal, max_x)
        y = (self.rect().height() - self._clear_btn.height()) // 2
        self._clear_btn.move(x, y)

    def resizeEvent(self, event: "QtGui.QResizeEvent") -> None:  # noqa: D401
        super().resizeEvent(event)
        self._reposition_clear_glyph()

    def paintEvent(self, event: "QtGui.QPaintEvent") -> None:  # noqa: D401
        super().paintEvent(event)
        if not self.hasFocus():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(_FOCUS_HALO_COLOR)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        # Inset 0.5 so the 1 px stroke lands on integer pixels. The
        # radius is one step above the field's 4 px, to stay concentric.
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, 5.0, 5.0)


class SearchTagsStrip(QtWidgets.QWidget):
    """The Search and Tags strip.

    Signal-out only. The receiver decides what "currently visible"
    means, and ``select_filtered_requested`` carries True on a
    Shift-click.

    The widget owns no pill state, only the search text and the Invert
    toggle.
    """

    # NB: define signals at class scope so PySide picks them up.
    filter_changed = QtCore.Signal(str, bool)
    select_filtered_requested = QtCore.Signal(bool)
    deselect_filtered_requested = QtCore.Signal()
    clear_selection_requested = QtCore.Signal()
    # The wiring layer owns the confirmation dialog and the call into
    # ``nsl.domain.panic.reset_global_to_default``.
    reset_global_requested = QtCore.Signal()

    def __init__(self, parent: "QtWidgets.QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("NSL_SearchTagsStrip")

        # ---- Search field --------------------------------------------------
        self._search = _SearchField(self)
        self._search.setObjectName("NSL_SearchField")
        self._search.setPlaceholderText("Search plugins…")
        self._search.textChanged.connect(self._on_search_text_changed)

        # ---- Filter controls -----------------------------------------------
        self._reset_btn = HybridTextButton("Rese&t", self)
        self._reset_btn.setObjectName("NSL_ResetButton")
        self._reset_btn.setToolTip(
            "Clear tag chip selection. Leaves the search field untouched."
        )
        self._reset_btn.clicked.connect(self._on_reset_clicked)

        # The only stateful control. Qt's pressed styling shows it is on.
        self._invert_btn = HybridTextButton("In&vert", self)
        self._invert_btn.setObjectName("NSL_InvertButton")
        self._invert_btn.setCheckable(True)
        self._invert_btn.setToolTip(
            "Invert: when on, the grid shows the inverse of the current chip "
            "selection. Stays pressed while active."
        )
        self._invert_btn.toggled.connect(self._on_invert_toggled)

        # ``clicked`` does not carry the modifiers, so the handler reads
        # ``QApplication.keyboardModifiers`` at click time. No mnemonic
        # here. Canonical gives one to Reset and Invert only.
        self._select_filtered_btn = HybridTextButton("Select filtered", self)
        self._select_filtered_btn.setObjectName("NSL_SelectFilteredButton")
        self._select_filtered_btn.setToolTip(
            "Select every Plugin currently visible after filtering. "
            "Shift-click to add to the existing selection instead of replacing it."
        )
        self._select_filtered_btn.clicked.connect(self._on_select_filtered_clicked)

        self._deselect_filtered_btn = HybridTextButton(
            "Deselect filtered", self
        )
        self._deselect_filtered_btn.setObjectName("NSL_DeselectFilteredButton")
        self._deselect_filtered_btn.setToolTip(
            "Remove every Plugin currently visible after filtering from the "
            "selection. Leaves selected-but-filtered-out Plugins alone."
        )
        self._deselect_filtered_btn.clicked.connect(
            self.deselect_filtered_requested
        )

        # Right-aligned on the controls row and hidden by default. The
        # wiring layer calls ``set_global_layer_active`` once the
        # Registry resolves a non-empty Global.
        self._reset_global_btn = HybridTextButton(
            "Reset Global Plugins to Default", self
        )
        self._reset_global_btn.setObjectName("NSL_ResetGlobalButton")
        self._reset_global_btn.setToolTip(
            "Reset every Global Plugin to its Global default in the "
            "active Loadout. Your user-added Plugins are not affected. "
            "The Global Loadout is read-only and untouched."
        )
        self._reset_global_btn.clicked.connect(self.reset_global_requested)
        self._reset_global_btn.setVisible(False)

        self._clear_selection_btn = HybridTextButton("Clear selection", self)
        self._clear_selection_btn.setObjectName("NSL_ClearSelectionButton")
        self._clear_selection_btn.setToolTip(
            "Empty the selection entirely, regardless of the current filter."
        )
        self._clear_selection_btn.clicked.connect(self.clear_selection_requested)

        # ---- None chip (stub) ----------------------------------------------
        # Not interactive yet. The chip exists so the row layout reads
        # correctly once the tag system ships.
        self._none_chip = QtWidgets.QToolButton(self)
        self._none_chip.setObjectName("NSL_NoneChip")
        self._none_chip.setText("None")
        self._none_chip.setEnabled(False)
        self._none_chip.setFocusPolicy(QtCore.Qt.NoFocus)
        self._none_chip.setStyleSheet(_NONE_CHIP_QSS)

        # ---- Layout --------------------------------------------------------
        # Reset and Invert only touch chip selection, so they stay hidden
        # until ``_V2_TAGS_ENABLED``. Outer margins are zero because the
        # parent panel owns the 12 px gutter that lines the strips up.
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.addWidget(self._search)

        controls_row = QtWidgets.QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)
        if _V2_TAGS_ENABLED:
            controls_row.addWidget(self._reset_btn)
            controls_row.addWidget(self._invert_btn)
        else:
            self._reset_btn.setVisible(False)
            self._invert_btn.setVisible(False)
        controls_row.addWidget(self._select_filtered_btn)
        controls_row.addWidget(self._deselect_filtered_btn)
        controls_row.addWidget(self._clear_selection_btn)
        controls_row.addStretch(1)
        controls_row.addWidget(self._reset_global_btn)
        root.addLayout(controls_row)

        self.setStyleSheet(self.styleSheet() + _SEARCH_QSS)

        # With the tag system off the row would hold only the None chip,
        # which does nothing. Skip the row and hide the chip.
        if _V2_TAGS_ENABLED:
            chips_row = QtWidgets.QHBoxLayout()
            chips_row.setContentsMargins(0, 0, 0, 0)
            chips_row.setSpacing(6)
            chips_row.addWidget(self._none_chip)
            chips_row.addStretch(1)
            root.addLayout(chips_row)
        else:
            self._none_chip.setVisible(False)

    # -----------------------------------------------------------------------
    # Public read accessors
    # -----------------------------------------------------------------------

    def query(self) -> str:
        """Current search-field text. Never None."""
        return self._search.text()

    def is_inverted(self) -> bool:
        """Whether the Invert toggle is currently on."""
        return self._invert_btn.isChecked()

    def clear_filter(self) -> None:
        """Clear the search field and turn off the Invert toggle.

        Wired to the top toolbar's Reset Panel button. Both mutations
        fan out to ``filter_changed``, so the filter pipeline picks up
        the cleared state on its own.
        """
        if self._search.text():
            self._search.clear()
        if self._invert_btn.isChecked():
            self._invert_btn.setChecked(False)

    def set_global_layer_active(self, active: bool) -> None:
        """Show or hide the ``Reset Global Plugins to Default`` button.

        Active means the Global resolver produced a non-empty set of
        Global Plugins. Without one the button is hidden, not disabled,
        because there is nothing to reset against. The wiring layer
        calls this on registry attach and on every refresh.
        """
        self._reset_global_btn.setVisible(bool(active))

    def is_reset_global_visible(self) -> bool:
        """Return whether the Reset Global button is currently visible."""
        return self._reset_global_btn.isVisible()

    def set_reset_global_enabled(self, enabled: bool) -> None:
        """Enable or disable the Reset Global button.

        Enabled only when a Global Plugin in the active Loadout has
        drifted from its Global default. With no drift there is nothing
        to revert, and a click would empty the model with no change in
        effective state.
        """
        self._reset_global_btn.setEnabled(bool(enabled))

    @property
    def reset_global_button(self) -> QtWidgets.QPushButton:
        """Expose the Reset Global button for the wiring layer."""
        return self._reset_global_btn

    # -----------------------------------------------------------------------
    # Slot handlers (private) - every slot fans out via the public signals.
    # -----------------------------------------------------------------------

    def _on_search_text_changed(self, text: str) -> None:
        self.filter_changed.emit(text, self._invert_btn.isChecked())

    def _on_invert_toggled(self, checked: bool) -> None:
        self.filter_changed.emit(self._search.text(), checked)

    def _on_reset_clicked(self) -> None:
        # There is no user chip state to clear yet. The emit keeps the
        # signal contract live. Search text is not touched.
        self.filter_changed.emit(self._search.text(), self._invert_btn.isChecked())

    def _on_select_filtered_clicked(self) -> None:
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        add_to_selection = bool(modifiers & QtCore.Qt.ShiftModifier)
        self.select_filtered_requested.emit(add_to_selection)

