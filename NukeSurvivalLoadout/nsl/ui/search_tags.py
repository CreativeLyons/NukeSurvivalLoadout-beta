"""NSL Loadout Panel - Search and Tags strip.

This module ships the visible discovery surface that sits above the pill grid:
a search field plus a row of filter/selection buttons.

What it implements:

* The **search field** (case-insensitive substring against Plugin Name) with
  a custom in-field clear glyph (only visible while text is present).
* **Select filtered** - momentary; emits a request signal carrying an
  ``add_to_selection`` flag (False for plain click, True for shift-click).
* **Deselect filtered** - momentary; removes currently-visible Plugins from
  the selection (the narrowing complement of Select filtered).
* **Clear selection** - momentary; empties the selection entirely
  regardless of filter (the blunt complement).

The ``Reset`` / ``Invert`` filter buttons and the tag-chip row (including the
system-managed ``None`` chip) are gated behind the tag system and hidden until
``_V2_TAGS_ENABLED`` flips True. The hidden widgets are still constructed so
call-sites and signal connections that reference ``strip.reset_btn`` /
``strip.invert_btn`` / ``strip.none_chip`` keep working without a branch
upstream.

What this module does NOT do:

* It does not own the visible pill list. It is a **signal-out** widget - the
  filter pipeline consumes ``filter_changed(query, invert)`` and
  ``select_filtered_requested(add_to_selection)``.
* It does not persist filter state. The search text, chip selection, and
  Invert toggle are per-session and panel-local. Switching Loadouts does not
  clear them; restarting Nuke does.

The widget code imports Qt exclusively via :mod:`nsl.compat` -
no direct ``PySide2`` / ``PySide6`` imports anywhere in this file.
"""

from __future__ import annotations

from typing import Iterable, List


# ---------------------------------------------------------------------------
# Pure-Python filter logic
# ---------------------------------------------------------------------------
#
# The widget below leans on this helper for its search semantics. Pulling the
# rule out as a module-level function keeps the case-insensitive substring
# contract usable without needing a Qt binding installed on the host. The
# widget calls ``match_query`` per plugin name; the production filter pipeline
# is free to call it directly as well.


def match_query(query: str, plugin_name: str) -> bool:
    """Return True if ``plugin_name`` matches the user's search ``query``.

    * Case-insensitive substring match against the Plugin Name.
    * An empty (or whitespace-only) query places no constraint and matches
      every Plugin Name.

    Matching against tag names and description text is handled in the filter
    pipeline, not here.
    """
    if query is None:
        return True
    q = query.strip()
    if not q:
        return True
    return q.casefold() in plugin_name.casefold()


def filter_visible(query: str, plugin_names: Iterable[str]) -> List[str]:
    """Return the subset of ``plugin_names`` that match ``query``.

    Convenience helper around :func:`match_query`. Preserves input order.
    """
    return [name for name in plugin_names if match_query(query, name)]


# ---------------------------------------------------------------------------
# Qt widget
# ---------------------------------------------------------------------------
#
# Qt is imported lazily (at module-import time, but via the compat shim) so
# that ``match_query`` / ``filter_visible`` above remain importable on a host
# without PySide installed. The compat shim itself raises a clear ImportError
# if neither binding is available - that is the right failure mode for the
# widget path, which fundamentally needs Qt.

from nsl import compat  # noqa: E402 - kept after pure helpers on purpose
from nsl.ui._buttons import HybridTextButton  # noqa: E402

QtCore = compat.QtCore
QtGui = compat.QtGui
QtWidgets = compat.QtWidgets


# Gate for the entire tag system. When off we render no tag chips and no chip
# row - the lone None chip is functionally dead with nothing else to point at,
# so it's visual noise. The chip widget is still constructed below so call-sites
# that reference ``strip.none_chip`` keep working without a branch. Flip to True
# when the tag system ships.
_V2_TAGS_ENABLED = False


# Visual tone for the None chip. Grey body + italic label - it should
# read as "system, not user". A neutral mid-grey on a transparent stroke
# distinguishes it from any user-created chip (which is outlined in the tag's
# colour).
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


# Search field chrome - lifted from canonical panel.css ``.search-input``:
#
#   bg #383838 · color #fff · 13 px · 7 12 padding · 4 px radius
#   1 px #1a1a1a border · faint inset top shadow (not modelled here)
#   focus: bg #404040 · border #2a2a2a (still dark!) PLUS a 1 px outer halo
#
# Locked palette rule for this project: reserved orange is the only accent -
# so the canonical's blue focus halo translates to an equally-subtle orange.
# The crucial property is that **the dark border must survive on focus** -
# the orange is an *outer* ring around the still-dark stroke, reading like a
# soft shadow on the brand colour. Qt QSS cannot do
# CSS ``box-shadow``, so the halo is painted in ``_SearchField.paintEvent``
# below, and this stylesheet only handles the body + border layers.
_SEARCH_QSS = (
    "QLineEdit#NSL_SearchField {"
    "    background-color: #303030;"  # a hair dimmer than canonical #383838
    "    color: #ffffff;"
    # Font + padding dropped from 13/6,10 → 11/4,8 so the field height
    # matches the rest of the panel's button vocabulary (HybridTextButton
    # under Fusion). Prior values overflowed every other strip's scale.
    "    font-size: 11px;"
    "    padding: 4px 8px;"
    "    margin: 1px;"  # reserve 1 px outside the QSS border for the halo
    "    border: 1px solid #1a1a1a;"
    "    border-radius: 4px;"
    "    selection-background-color: #c9a373;"
    "    selection-color: #1a1a1a;"
    "}"
    "QLineEdit#NSL_SearchField:focus {"
    # No body lift on focus - the dark inner border + orange outer halo
    # carry the "you're typing here" signal. A brighter body would fight
    # the deliberately dimmer-than-canonical idle state.
    "    background-color: #303030;"
    "    border: 1px solid #2a2a2a;"  # canonical keeps a dark border on focus
    "}"
)


# Outer-halo tint used on focus. Picked to read as a desaturated nuke-orange
# at low contrast - equivalent in subtlety to the canonical's
# ``rgba(86,160,244,0.25)`` blue halo, just in the brand accent. Sitting
# *outside* the QLineEdit's dark border lets both layers stay visible:
# the dark stroke reads as the input edge, the orange as a soft surround.
_FOCUS_HALO_COLOR = QtGui.QColor("#7a5a32")


def _make_clear_glyph_icon(
    size: int = 12,
    disc_color: str = "#9a9a9a",
) -> "QtGui.QIcon":
    """Return a small "circle with × cut out" glyph icon.

    Renders as a filled disc with an × hollowed straight through it (the
    × lines are punched transparent via ``CompositionMode_Clear``). The
    disc shape disambiguates the affordance from typed text - without it
    a stroked × would read as a stray glyph hanging off the end of the
    user's word. The hollow × inside keeps it within the engraved-line
    vocabulary used elsewhere on the panel.

    ``disc_color`` controls the disc fill. Use ``#9a9a9a`` for rest and
    ``#ffffff`` for hover so the icon visibly brightens when the cursor
    is over the click target (paired with a stronger hover background in
    the button's stylesheet).

    Paints into a 2× DPR pixmap so the disc + cut-out edges stay clean
    on Retina without the pen-width math going subpixel.
    """
    scale = 2
    pixmap = QtGui.QPixmap(size * scale, size * scale)
    pixmap.fill(QtCore.Qt.transparent)
    pixmap.setDevicePixelRatio(scale)

    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # 1. Filled disc. Insets 0.5 px so the antialiased edge lands
        # inside the icon bounds.
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(disc_color))
        rect = QtCore.QRectF(0.5, 0.5, size - 1.0, size - 1.0)
        painter.drawEllipse(rect)

        # 2. Punch the × out of the disc. CompositionMode_Clear sets
        # painted pixels to transparent - the × strokes become hollow
        # gaps in the disc rather than overpainted darker strokes.
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

    QSS ``:hover`` cannot change ``setIcon``, only background / border /
    colour properties - same constraint :class:`_HoverIconButton` in
    :mod:`nsl.ui.loadout_strip` works around. Pre-render both icons at
    construction and swap them in :meth:`enterEvent` / :meth:`leaveEvent`.
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

    The halo sits *outside* the QSS-drawn dark border (which is inset 1 px
    via ``margin: 1px`` in ``_SEARCH_QSS``) so both layers remain visible
    on focus - the dark stroke reads as the input edge, the orange as a
    soft surround. No animation; the ring is on or off.

    The field also owns an in-field clear glyph (custom × in the engraved
    vocabulary) that only appears while text is present. Built by hand
    rather than via :meth:`QLineEdit.setClearButtonEnabled` because that
    API's icon is a generic dark-circle × that doesn't sit cleanly in our
    quiet-chrome system.
    """

    _CLEAR_GLYPH_SIZE = 12
    _CLEAR_RIGHT_PAD = 6
    # Gap between the typed text and the × glyph when the glyph follows
    # the text. Generous spacing so the disc-shaped affordance reads as a
    # distinct control, not a punctuation mark trailing the word.
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
        # Stronger hover lift than the prior rgba(255,255,255,0.06) - the
        # 12 px disc needs a more visible background pad to read as a
        # real click target. Pressed state darkens slightly so the button
        # confirms the click. Radius covers the whole hit area.
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
        # Hit area = glyph + 6 px halo so the hover-lift background reads
        # as a real button pad around the disc rather than a tight crop.
        self._clear_btn.setFixedSize(
            QtCore.QSize(self._CLEAR_GLYPH_SIZE + 8, self._CLEAR_GLYPH_SIZE + 8)
        )
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self.clear)

        # Reserve right-side text margin so typed text never slides under
        # the clear glyph when the field overflows and the glyph snaps to
        # the right edge as a fallback. Recomputed if the glyph size
        # changes.
        self.setTextMargins(
            0, 0, self._clear_btn.width() + self._CLEAR_RIGHT_PAD, 0
        )

        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text: str) -> None:
        self._clear_btn.setVisible(bool(text))
        self._reposition_clear_glyph()

    def _reposition_clear_glyph(self) -> None:
        """Place the × at the end of the text, clamped to the right edge.

        Until the typed text gets close to the right edge, the glyph
        sits right after the last character so the user can clear in a
        short mouse move. Once the text would push the glyph past the
        right edge, the glyph clamps to ``right_edge - pad`` so it
        never disappears off-canvas.

        Uses :meth:`QStyle.subElementRect(SE_LineEditContents)` to find
        the text body rect (honours QSS padding, textMargins, and the
        QStyle frame in one call) and :meth:`QFontMetrics.horizontalAdvance`
        for the text width. Avoids touching ``cursorPosition``, which
        would jump the glyph around as the user navigates the caret.
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
        # Inset by 0.5 so the 1 px stroke sits cleanly on integer pixels.
        # Outer radius is one step larger than the inner field's 4 px
        # radius to keep the ring concentric with the dark border.
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, 5.0, 5.0)


class SearchTagsStrip(QtWidgets.QWidget):
    """The Search and Tags strip.

    Signals (the only outward contract):

    * ``filter_changed(query: str, invert: bool)`` - fires whenever the
      search text or the Invert toggle changes. The receiver re-applies its
      visibility filter using both arguments.
    * ``select_filtered_requested(add_to_selection: bool)`` - fires when the
      user clicks ``Select filtered``. ``add_to_selection`` is True if the
      click was a Shift-click, False otherwise. The receiver decides what
      "currently visible" means in its own coordinate system.
    * ``deselect_filtered_requested()`` - fires when the user clicks
      ``Deselect filtered``. The receiver removes everything currently
      visible from its selection (selected-but-filtered-out items keep
      their selection).
    * ``clear_selection_requested()`` - fires when the user clicks
      ``Clear selection``. The receiver empties its selection entirely
      regardless of filter.

    The widget owns no state about the pills themselves. It owns only:

    * the search query string (``QLineEdit``);
    * the Invert toggle state (``QToolButton`` with ``setCheckable(True)``).

    The None chip is rendered but is **not interactive** while the tag system
    is gated off - it is a placeholder for the future chip row.
    """

    # NB: define signals at class scope so PySide picks them up.
    filter_changed = QtCore.Signal(str, bool)
    select_filtered_requested = QtCore.Signal(bool)
    deselect_filtered_requested = QtCore.Signal()
    clear_selection_requested = QtCore.Signal()
    # Reset Global Plugins to Default, bulk granularity. The strip is
    # signal-out only; the wiring layer owns the confirmation dialog and the
    # call into ``nsl.domain.panic.reset_global_to_default``.
    reset_global_requested = QtCore.Signal()

    def __init__(self, parent: "QtWidgets.QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("NSL_SearchTagsStrip")

        # ---- Search field --------------------------------------------------
        # _SearchField owns its own custom × clear glyph (engraved vocabulary)
        # so the native Qt clear button is never enabled - it doesn't match
        # the rest of NSL's quiet chrome.
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

        # Invert is the only stateful control in the strip. The
        # button itself is the only signal that invert is active - the chips
        # do not change state. ``setCheckable`` + Qt's default pressed
        # styling carries the visual feedback automatically.
        self._invert_btn = HybridTextButton("In&vert", self)
        self._invert_btn.setObjectName("NSL_InvertButton")
        self._invert_btn.setCheckable(True)
        self._invert_btn.setToolTip(
            "Invert: when on, the grid shows the inverse of the current chip "
            "selection. Stays pressed while active."
        )
        self._invert_btn.toggled.connect(self._on_invert_toggled)

        # Select filtered is a button that needs to distinguish plain click
        # vs shift-click. ``QPushButton.clicked`` does not surface modifiers
        # directly, so we wire its press through a small handler that reads
        # ``QApplication.keyboardModifiers`` at click time.
        # Canonical (App.jsx) leaves "Select filtered" without an underlined
        # letter - only Reset and Invert carry mnemonics. Match that exactly.
        self._select_filtered_btn = HybridTextButton("Select filtered", self)
        self._select_filtered_btn.setObjectName("NSL_SelectFilteredButton")
        self._select_filtered_btn.setToolTip(
            "Select every Plugin currently visible after filtering. "
            "Shift-click to add to the existing selection instead of replacing it."
        )
        self._select_filtered_btn.clicked.connect(self._on_select_filtered_clicked)

        # Deselect filtered - the narrowing complement of Select filtered.
        # Removes the currently-visible (filtered-in) Plugins from the
        # selection. Leaves any selected-but-filtered-out Plugins alone, so
        # the user can use the filter as a precision deselect tool ("show
        # me only the DMP ones, drop them from the selection").
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

        # Clear selection - the blunt complement. Empties the selection
        # entirely, regardless of what the filter is showing. Same wording
        # the canonical Grid toolbar uses (App.jsx ``Clear Selection``);
        # surfaced here so the full select / narrow / nuke triad lives on
        # one row without hunting in another strip.
        # Reset Global Plugins to Default - right-aligned on the controls
        # row, hidden by default. The wiring layer flips ``set_global_layer_active``
        # on once the Registry resolves a non-empty Global; until then
        # the affordance does not appear at all (it is scoped strictly to
        # Global Plugins - there is nothing to reset against without a
        # Global layer).
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
        # Rendered as a small button-shaped pill with italic 'None' label and
        # a grey body. Not interactive yet: the chip exists so the row's
        # layout reads correctly and so users understand what will be here.
        self._none_chip = QtWidgets.QToolButton(self)
        self._none_chip.setObjectName("NSL_NoneChip")
        self._none_chip.setText("None")
        self._none_chip.setEnabled(False)  # placeholder; not clickable yet
        self._none_chip.setFocusPolicy(QtCore.Qt.NoFocus)
        self._none_chip.setStyleSheet(_NONE_CHIP_QSS)

        # ---- Layout --------------------------------------------------------
        # By default the strip is two rows: search field + a single filter
        # button (Select filtered). Reset and Invert are tag-related controls -
        # they only manipulate chip selection, which has nothing to manipulate
        # without tags - so they're hidden until ``_V2_TAGS_ENABLED`` flips.
        # The widgets stay constructed so upstream wiring keeps working.
        #
        # Outer margins are zero: the parent panel owns the 12 px gutter so all
        # strips line up vertically. Row gap matches the canonical
        # ``gap: 6px`` between filter buttons.
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
        # Reset Global Plugins to Default sits at the right edge of the
        # controls row - hidden by default; surfaced via
        # ``set_global_layer_active(True)`` from the wiring layer. The
        # preceding stretch absorbs the gap so the button right-aligns
        # against the strip's edge. When hidden the layout collapses
        # naturally and the strip reads as before.
        controls_row.addWidget(self._reset_global_btn)
        root.addLayout(controls_row)

        # Search field gets its canonical QSS (dimmer body + dark inner
        # border + painted orange focus halo). Filter buttons are now
        # ``HybridTextButton`` instances - bare native-style chrome,
        # matching the rest of the panel's action-button vocabulary
        # (Save / Save As / Import / Export / Undo / Redo / Reset Panel /
        # Add Plugins Folder / Rescan Plugins) for panel-wide button
        # consistency.
        self.setStyleSheet(self.styleSheet() + _SEARCH_QSS)

        # Tag-chip row is part of the tag system. While it's off the row is
        # empty - the None chip alone would be a no-op control, so we skip the
        # layout row entirely and hide the chip widget. When the tag system
        # ships, flip _V2_TAGS_ENABLED and the row reappears.
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
    # Public read accessors - handy for reading state without poking at
    # private attributes.
    # -----------------------------------------------------------------------

    def query(self) -> str:
        """Current search-field text. Never None."""
        return self._search.text()

    def is_inverted(self) -> bool:
        """Whether the Invert toggle is currently on."""
        return self._invert_btn.isChecked()

    def clear_filter(self) -> None:
        """Clear the search field and turn off the invert toggle.

        Wired by the top toolbar's Reset Panel button, which clears the
        filters as part of resetting the panel. The two mutations emit
        ``textChanged`` / ``toggled`` which fan out to
        ``filter_changed`` through the existing slot handlers, so the
        filter pipeline picks up the cleared state on its own. No-op
        when both are already at defaults.
        """
        if self._search.text():
            self._search.clear()
        if self._invert_btn.isChecked():
            self._invert_btn.setChecked(False)

    def set_global_layer_active(self, active: bool) -> None:
        """Toggle the ``Reset Global Plugins to Default`` button's visibility.

        The bulk-reset affordance only makes sense when a Global layer is currently
        active for this session (i.e. the Global resolver - either
        the ``<nsl_root>/Global/`` folder convention, or
        ``NSL_GLOBAL_PLUGIN_DIRS`` + ``NSL_GLOBAL_LOADOUTS``, or both -
        produced a non-empty set of Global Plugins). When inactive, the
        button is hidden **entirely**, not merely disabled - there is
        nothing to reset against, so the affordance should not appear at
        all. The wiring layer calls this from registry attach + on every
        registry-change refresh.
        """
        self._reset_global_btn.setVisible(bool(active))

    def is_reset_global_visible(self) -> bool:
        """Return whether the Reset Global button is currently visible.

        Useful for the wiring layer's idempotency checks.
        """
        return self._reset_global_btn.isVisible()

    def set_reset_global_enabled(self, enabled: bool) -> None:
        """Toggle the Reset Global button's enabled state.

        The button is only meaningful when at least one Global
        Plugin in the active Loadout has drifted from its
        Global default. When no drift exists, clicking the button
        would either be a no-op or produce a spurious diff (since
        Custom mirrors Global on auto-create, removing entries that
        already match Global would empty the model without any
        effective-state change). With no drift there is nothing to
        revert, so the button stays disabled.
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
        # Chip selection is just the None chip placeholder - there is no
        # user chip state to clear. We still emit ``filter_changed`` so the
        # downstream pipeline can observe the no-op cleanly (and so the
        # signal contract is exercised end-to-end). Search text is NOT
        # touched.
        self.filter_changed.emit(self._search.text(), self._invert_btn.isChecked())

    def _on_select_filtered_clicked(self) -> None:
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        add_to_selection = bool(modifiers & QtCore.Qt.ShiftModifier)
        self.select_filtered_requested.emit(add_to_selection)

