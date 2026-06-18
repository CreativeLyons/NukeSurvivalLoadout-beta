"""Top-of-panel toolbar widget - Undo / Redo / Reset panel (plus Panic).

The always-visible strip at the top of the panel. It owns the buttons and
emits signals on click; it never owns the undo/redo stack or any layout
state, so it only dispatches user intent.

Signals emitted by :class:`TopToolbar`:
    - ``undo_requested()`` - Undo button clicked.
    - ``redo_requested()`` - Redo button clicked.
    - ``reset_panel_requested()`` - Reset panel button clicked.
    - ``panic_toggled(bool)`` - Panic button toggled.

Inbound state plumbing:
    - ``set_undo_available(bool)`` / ``set_redo_available(bool)`` toggle the
      Undo / Redo enabled state. The domain layer is the source of truth for
      stack state; undo scope is the active Loadout's per-Loadout undo stack.

Behavior notes:
    - Reset panel restores splitter / collapse to defaults; it does NOT touch
      user data (Loadouts, selections, Plugins Folders, filter, sort). The
      button only requests the reset via signal.
    - Buttons render as plain text without icons - the lowest-surprise look
      that works on every Qt theme.
    - Qt is imported only via :mod:`nsl.compat`; no direct
      ``import PySide2`` / ``import PySide6`` and no ``import nuke``.
"""

from __future__ import annotations

from typing import Optional

from nsl import compat
from nsl.ui._buttons import HybridTextButton, install_clickable_cursor


# ---------------------------------------------------------------------------
# Button labels
# ---------------------------------------------------------------------------

BUTTON_LABEL_UNDO = "&Undo"
BUTTON_LABEL_REDO = "&Redo"
BUTTON_LABEL_RESET = "Reset &Panel"

# Panic button - wording is the canonical from comp-buttons.html
# (NSL Design System). Off (armed) names the action; on (engaged)
# names the state. P is the mnemonic in both.
BUTTON_LABEL_PANIC_OFF = "&Panic Mode: Disable All User Plugins"
BUTTON_LABEL_PANIC_ON = "&Panic Mode: Engaged"
BUTTON_TOOLTIP_PANIC = (
    "Panic - Disable All: when engaged, all user-added Plugins are "
    "disabled. Global Plugins are untouched. Click again to restore."
)

# Panic treatment - canonical from comp-buttons.html / _card.css:
#
#   armed  (off)     : dusty brown-red gradient #4a3a3a → #382c2c, dim
#                      label #cccaca, border #1f1414. "Lower saturation,
#                      doesn't shout" - visibly different from sibling
#                      buttons (always reads as a danger control) without
#                      being a constant scream.
#   engaged (on)     : bright red gradient #c43838 → #9a2020, bold WHITE
#                      label, border #4a0e0e. The chrome shouts because
#                      the state is the one that should.
#
# Hover lifts each gradient one stop brighter; HybridStyle paints hover
# natively in-Nuke and this QSS only matters when rendered offscreen.
_PANIC_GRAD_OFF = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a3a3a, stop:1 #382c2c)"
_PANIC_GRAD_OFF_HOVER = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5a4444, stop:1 #443434)"
_PANIC_GRAD_ON = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #c43838, stop:1 #9a2020)"
_PANIC_GRAD_ON_HOVER = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d04848, stop:1 #ac2828)"


# Canonical .btn chrome - matches panel.css `.btn`:
#   Fusion vertical gradient #5e5e5e → #464646, 5px radius, 1px #2a2a2a
#   outer border, 1px #6a6a6a faux inset top highlight, 24px height,
#   12px font, 0 12px padding. Pressed/disabled match the canonical
#   variants. Mirrors the .btn-small recipe used in grid_toolbar /
#   search_tags so every chrome button in the panel reads as one
#   vocabulary. .btn (24px) here because the top toolbar uses canonical
#   `.btn`, not `.btn-small` (which is the grid-toolbar / search-tags
#   scale).
# Panic-only QSS, applied directly to the Panic button (NOT to the
# TopToolbar widget). Reason: setting any stylesheet on the TopToolbar
# widget causes Qt to re-render its sibling QPushButtons through the
# QSS resolution path even when no rule matches them, which produces
# different border + padding than pure native Fusion. That made
# Undo/Redo/Reset Panel look visibly different from FolderCard's
# Add/Rescan buttons (which inherit pure native because FolderCard
# has no widget-level setStyleSheet). Applying this QSS directly to
# the Panic button keeps the cascade clean and lets every other
# HybridTextButton render identically across the panel.
_PANIC_BTN_QSS = (
    # Armed (off) - dusty brown-red gradient, dim label. Always visibly
    # different from sibling buttons so the danger control reads
    # immediately, without shouting.
    "QPushButton {"
    "    padding: 4px 12px;"
    "    color: #cccaca;"
    f"   background-color: {_PANIC_GRAD_OFF};"
    "    border: 1px solid #1f1414;"
    "    border-radius: 3px;"
    "}"
    "QPushButton:hover {"
    f"   background-color: {_PANIC_GRAD_OFF_HOVER};"
    "    color: #ffffff;"
    "}"
    # Engaged (on) - bright red gradient, bold WHITE. Chrome shouts
    # because the state is the one that should.
    "QPushButton:checked {"
    f"   background-color: {_PANIC_GRAD_ON};"
    "    color: #ffffff;"
    # Bold is applied via QFont.setBold() in _apply_panic_label() so
    # QFontMetrics returns the correct rendered width; setting it in
    # QSS here would double-apply it without helping the size hint.
    "    border: 1px solid #4a0e0e;"
    "}"
    "QPushButton:checked:hover {"
    f"   background-color: {_PANIC_GRAD_ON_HOVER};"
    "}"
    # Disabled - falls back to the sibling .btn grey gradient with dim
    # text, exactly like `.nbtn--primary-disabled` in _card.css.
    "QPushButton:disabled {"
    "    color: #7a7a7a;"
    "    font-weight: normal;"
    "    background-color: qlineargradient("
    "        x1:0, y1:0, x2:0, y2:1,"
    "        stop:0 #5e5e5e, stop:1 #464646);"
    "    border: 1px solid #2a2a2a;"
    "    border-top: 1px solid #5a5a5a;"
    "}"
)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class TopToolbar(compat.QtWidgets.QWidget):
    """Top-of-panel toolbar with Undo, Redo, Reset panel.

    The widget is a thin horizontal strip. It owns three :class:`QPushButton`
    instances and emits signals on click. Enabled state for Undo / Redo is
    driven from outside via :meth:`set_undo_available` /
    :meth:`set_redo_available` - the domain layer is the source of truth
    for stack state (the per-Loadout undo stack).
    """

    # Signal-out only.
    undo_requested = compat.QtCore.Signal()
    redo_requested = compat.QtCore.Signal()
    reset_panel_requested = compat.QtCore.Signal()
    panic_toggled = compat.QtCore.Signal(bool)

    def __init__(
        self,
        parent: Optional[compat.QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)

        # The toolbar is the smallest persistent UI element in the panel.
        # We use plain QPushButtons inside a QHBoxLayout: a QWidget host with
        # QPushButtons keeps the widget self-contained (no QMainWindow context
        # needed) and makes it trivial to embed inside the larger panel layout.
        # Outer margins are zero - parent panel owns the 12px gutter so
        # the toolbar aligns flush with the strips below.
        layout = compat.QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # HybridTextButton - canonical Nuke-hybrid basic-text vocabulary
        # shared with FolderCard's Add/Rescan buttons (and any future
        # plain-text action). Edit `nsl/ui/_buttons.py` to change the
        # whole family in one place.
        self._btn_undo = HybridTextButton(BUTTON_LABEL_UNDO, self)
        self._btn_redo = HybridTextButton(BUTTON_LABEL_REDO, self)
        self._btn_reset = HybridTextButton(BUTTON_LABEL_RESET, self)

        # Object names - helpful for QSS lookup.
        self._btn_undo.setObjectName("nsl_top_toolbar_undo")
        self._btn_redo.setObjectName("nsl_top_toolbar_redo")
        self._btn_reset.setObjectName("nsl_top_toolbar_reset")

        # Undo, Redo, Reset panel - left-aligned, in order.
        # Append a stretch so the trio hugs the left edge of the toolbar, and
        # the panic button sits right-aligned in the remaining space.
        layout.addWidget(self._btn_undo)
        layout.addWidget(self._btn_redo)
        layout.addWidget(self._btn_reset)
        layout.addStretch(1)

        # Panic button - right-aligned. Default chrome when off (just looks
        # like any other button under HybridStyle); red treatment only when
        # the :checked pseudo-state is active. Tooltip carries the full
        # action description so the label can stay short.
        self._btn_panic = compat.QtWidgets.QPushButton(self)
        self._btn_panic.setObjectName("nsl_top_toolbar_panic")
        self._btn_panic.setCheckable(True)
        self._btn_panic.setToolTip(BUTTON_TOOLTIP_PANIC)
        # Panel-wide interactive cursor vocabulary - pointing hand when
        # the button is armed, arrow when it's disabled (no user-added
        # Plugins / Global active). Mirrors the HybridTextButton sibling
        # contract; the Panic button can't inherit from that class
        # because its danger-state QSS pipeline (engaged-red gradient,
        # bold white label) is unique to it.
        install_clickable_cursor(self._btn_panic)
        self._apply_panic_label()
        self._btn_panic.toggled.connect(self._on_panic_toggled)
        # Pin minimum width to the max of (regular OFF label, bold ON
        # label) plus QSS padding (12px each side) plus a 16px safety
        # buffer. We measure both labels at their actual rendered
        # weight using two QFontMetrics - QSS's `font-weight: 700` is
        # applied via Qt's stylesheet but is NOT reflected in
        # ``QFontMetrics(button.font())``, so a single regular-weight
        # measurement underestimates the bold engaged width and the
        # text clips. Two metrics give us the truth on both states.
        font_regular = compat.QtGui.QFont(self._btn_panic.font())
        font_bold = compat.QtGui.QFont(self._btn_panic.font())
        font_bold.setBold(True)
        fm_off = compat.QtGui.QFontMetrics(font_regular)
        fm_on = compat.QtGui.QFontMetrics(font_bold)
        off_w = fm_off.horizontalAdvance(BUTTON_LABEL_PANIC_OFF.replace("&", ""))
        on_w = fm_on.horizontalAdvance(BUTTON_LABEL_PANIC_ON.replace("&", ""))
        self._btn_panic.setMinimumWidth(max(off_w, on_w) + 24 + 16)
        layout.addWidget(self._btn_panic)

        # Apply Panic's danger-state QSS directly to the Panic button
        # (NOT to the TopToolbar widget). See `_PANIC_BTN_QSS` comment
        # for the cascade-pollution reason. With the QSS pinned to the
        # button itself, the TopToolbar widget has no stylesheet, so
        # Undo / Redo / Reset Panel render pure-native Fusion, exactly
        # matching FolderCard's Add / Rescan buttons.
        self._btn_panic.setStyleSheet(_PANIC_BTN_QSS)

        # Lock the toolbar's minimum size to its layout's hint so the
        # parent window cannot shrink below what the buttons need to
        # render readably. The panic button has the longest text and
        # owns most of this budget. Without this, Qt happily clips the
        # button as the window narrows; with it, the window itself
        # refuses to shrink past the toolbar's natural minimum width.
        # SetMinAndMaxSize would also pin the maximum - we only want
        # to pin the minimum so the toolbar can still grow horizontally
        # when the window is wide.
        layout.setSizeConstraint(
            compat.QtWidgets.QLayout.SetMinimumSize
        )
        # Also force layout activation now so sizeHint() is current
        # before any caller asks (e.g. a host container's resize).
        layout.activate()
        self.setMinimumWidth(layout.sizeHint().width())

        # Default-disabled Undo / Redo: at startup no actions have been
        # taken yet, so the per-Loadout undo stack is empty. The domain
        # layer flips these on as the user does undoable things.
        self._btn_undo.setEnabled(False)
        self._btn_redo.setEnabled(False)
        # Reset panel is always enabled - there is no "nothing to reset"
        # state for layout (it's idempotent: clicking when already at
        # defaults is a no-op for the user but a valid signal).
        self._btn_reset.setEnabled(True)

        # Wire button clicks to outbound signals. Using lambdas keeps the
        # signature explicit; ``clicked`` carries a checked bool we ignore.
        self._btn_undo.clicked.connect(lambda _checked=False: self.undo_requested.emit())
        self._btn_redo.clicked.connect(lambda _checked=False: self.redo_requested.emit())
        self._btn_reset.clicked.connect(
            lambda _checked=False: self.reset_panel_requested.emit()
        )

    # ----- inbound state plumbing ----------------------------------------------

    def set_undo_available(self, available: bool) -> None:
        """Reflect whether the active Loadout has any undoable history.

        Hook this up to the domain layer's
        ``undo_available_changed(bool)`` signal.
        """
        self._btn_undo.setEnabled(bool(available))

    def set_redo_available(self, available: bool) -> None:
        """Reflect whether the active Loadout has anything to redo.

        Hook this up to the domain layer's
        ``redo_available_changed(bool)`` signal.
        """
        self._btn_redo.setEnabled(bool(available))

    # ----- accessors (for the wiring layer) ------------------------------------

    @property
    def undo_button(self) -> compat.QtWidgets.QPushButton:
        """Expose the Undo ``QPushButton`` for the wiring layer."""
        return self._btn_undo

    @property
    def redo_button(self) -> compat.QtWidgets.QPushButton:
        """Expose the Redo ``QPushButton`` for the wiring layer."""
        return self._btn_redo

    @property
    def reset_button(self) -> compat.QtWidgets.QPushButton:
        """Expose the Reset panel ``QPushButton`` for the wiring layer."""
        return self._btn_reset

    @property
    def panic_button(self) -> compat.QtWidgets.QPushButton:
        """Expose the panic ``QPushButton`` for the wiring layer."""
        return self._btn_panic

    def set_panic_engaged(self, engaged: bool) -> None:
        """Programmatically reflect panic state without re-emitting."""
        engaged = bool(engaged)
        if self._btn_panic.isChecked() == engaged:
            self._apply_panic_label()
            return
        blocked = self._btn_panic.blockSignals(True)
        try:
            self._btn_panic.setChecked(engaged)
        finally:
            self._btn_panic.blockSignals(blocked)
        self._apply_panic_label()

    def _apply_panic_label(self) -> None:
        """Swap the Panic button label and font weight to match state.

        Wording:
          * off (armed)   → "Panic Mode: Disable All User Plugins"
                            (names mode + action)
          * on  (engaged) → "Panic Mode: Engaged"
                            (state-only; the OFF label already told the
                            user what's about to happen, the ON label
                            just confirms it's done)

        Engaged state is also bold. We set the bold weight on the
        button's ``QFont`` directly (not via QSS) so Qt's
        ``QFontMetrics`` returns the correct rendered width and the
        sizeHint / layout reflects the bold text accurately.
        """
        engaged = self._btn_panic.isChecked()
        self._btn_panic.setText(
            BUTTON_LABEL_PANIC_ON if engaged else BUTTON_LABEL_PANIC_OFF
        )
        font = self._btn_panic.font()
        font.setBold(engaged)
        self._btn_panic.setFont(font)
        # Re-evaluate QSS (the `:checked` state-coloured background and
        # border swap on toggle) and re-measure the size hint.
        self._btn_panic.style().unpolish(self._btn_panic)
        self._btn_panic.style().polish(self._btn_panic)
        self._btn_panic.updateGeometry()

    def _on_panic_toggled(self, checked: bool) -> None:
        self._apply_panic_label()
        self.panic_toggled.emit(bool(checked))

