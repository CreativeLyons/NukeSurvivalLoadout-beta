"""Top-of-panel toolbar widget - Undo / Redo / Reset panel (plus Panic).

The always-visible strip at the top of the panel. It owns the buttons
and emits a signal per click. It holds no undo stack and no layout state.

Signals emitted by :class:`TopToolbar`:
    - ``undo_requested()`` - Undo button clicked.
    - ``redo_requested()`` - Redo button clicked.
    - ``reset_panel_requested()`` - Reset panel button clicked.
    - ``panic_toggled(bool)`` - Panic button toggled.

``set_undo_available(bool)`` and ``set_redo_available(bool)`` come from
outside. The domain layer owns the active Loadout's undo stack.

Reset panel restores splitter and collapse defaults. It does not touch
Loadouts, selections, Plugins Folders, filter or sort.
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

# Panic wording is canonical in comp-buttons.html (NSL Design System).
# Off names the action, on names the state. P is the mnemonic in both.
BUTTON_LABEL_PANIC_OFF = "&Panic Mode: Disable All User Plugins"
BUTTON_LABEL_PANIC_ON = "&Panic Mode: Engaged"
BUTTON_TOOLTIP_PANIC = (
    "Panic - Disable All: when engaged, all user-added Plugins are "
    "disabled. Global Plugins are untouched. Click again to restore."
)

# Panic treatment, canonical in comp-buttons.html / _card.css. Armed is
# low-saturation brown-red, so it reads as a danger control without
# shouting. Engaged is bright red. Hover lifts each gradient one stop.
_PANIC_GRAD_OFF = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a3a3a, stop:1 #382c2c)"
_PANIC_GRAD_OFF_HOVER = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5a4444, stop:1 #443434)"
_PANIC_GRAD_ON = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #c43838, stop:1 #9a2020)"
_PANIC_GRAD_ON_HOVER = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d04848, stop:1 #ac2828)"


# Panic-only QSS, applied to the Panic button and never to the
# TopToolbar widget. A stylesheet on the widget re-renders the sibling
# buttons through the QSS path and they stop matching native Fusion.
_PANIC_BTN_QSS = (
    # Armed (off).
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
    # Engaged (on).
    "QPushButton:checked {"
    f"   background-color: {_PANIC_GRAD_ON};"
    "    color: #ffffff;"
    # Bold comes from QFont.setBold() in _apply_panic_label(), so
    # QFontMetrics measures the real width. Do not set font-weight here.
    "    border: 1px solid #4a0e0e;"
    "}"
    "QPushButton:checked:hover {"
    f"   background-color: {_PANIC_GRAD_ON_HOVER};"
    "}"
    # Disabled - the sibling grey gradient, like
    # `.nbtn--primary-disabled` in _card.css.
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
    """Top-of-panel toolbar with Undo, Redo, Reset panel and Panic.

    A thin horizontal strip of :class:`QPushButton` instances that emits
    a signal per click. Undo and Redo enabled state comes from outside,
    via :meth:`set_undo_available` and :meth:`set_redo_available`.
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

        # Outer margins are zero. The parent panel owns the 12px gutter,
        # so the toolbar lines up with the strips below.
        layout = compat.QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._btn_undo = HybridTextButton(BUTTON_LABEL_UNDO, self)
        self._btn_redo = HybridTextButton(BUTTON_LABEL_REDO, self)
        self._btn_reset = HybridTextButton(BUTTON_LABEL_RESET, self)

        # Object names for QSS lookup.
        self._btn_undo.setObjectName("nsl_top_toolbar_undo")
        self._btn_redo.setObjectName("nsl_top_toolbar_redo")
        self._btn_reset.setObjectName("nsl_top_toolbar_reset")

        # The stretch keeps the trio left and pushes Panic to the right.
        layout.addWidget(self._btn_undo)
        layout.addWidget(self._btn_redo)
        layout.addWidget(self._btn_reset)
        layout.addStretch(1)

        # The tooltip carries the full action, so the label stays short.
        self._btn_panic = compat.QtWidgets.QPushButton(self)
        self._btn_panic.setObjectName("nsl_top_toolbar_panic")
        self._btn_panic.setCheckable(True)
        self._btn_panic.setToolTip(BUTTON_TOOLTIP_PANIC)
        # Panic cannot subclass HybridTextButton, because its danger-state
        # QSS is unique to it. This gives it the same cursor behaviour.
        install_clickable_cursor(self._btn_panic)
        self._apply_panic_label()
        self._btn_panic.toggled.connect(self._on_panic_toggled)
        # Pin the minimum width to the wider label, plus the 12px QSS
        # padding each side and a 16px buffer. The engaged label renders
        # bold, so it needs its own QFontMetrics or the text clips.
        font_regular = compat.QtGui.QFont(self._btn_panic.font())
        font_bold = compat.QtGui.QFont(self._btn_panic.font())
        font_bold.setBold(True)
        fm_off = compat.QtGui.QFontMetrics(font_regular)
        fm_on = compat.QtGui.QFontMetrics(font_bold)
        off_w = fm_off.horizontalAdvance(BUTTON_LABEL_PANIC_OFF.replace("&", ""))
        on_w = fm_on.horizontalAdvance(BUTTON_LABEL_PANIC_ON.replace("&", ""))
        self._btn_panic.setMinimumWidth(max(off_w, on_w) + 24 + 16)
        layout.addWidget(self._btn_panic)

        self._btn_panic.setStyleSheet(_PANIC_BTN_QSS)

        # Lock the minimum size to the layout hint, so the window cannot
        # shrink until Qt clips the Panic label. SetMinAndMaxSize would
        # pin the maximum too, and the toolbar must still be able to grow.
        layout.setSizeConstraint(
            compat.QtWidgets.QLayout.SetMinimumSize
        )
        # Activate now so sizeHint() is current before any caller asks.
        layout.activate()
        self.setMinimumWidth(layout.sizeHint().width())

        # The undo stack is empty at startup. The domain layer turns
        # these on through set_undo_available / set_redo_available.
        self._btn_undo.setEnabled(False)
        self._btn_redo.setEnabled(False)
        # Reset panel is always enabled. Clicking it at the defaults is
        # a no-op for the user but still a valid signal.
        self._btn_reset.setEnabled(True)

        # The lambdas drop the checked bool that ``clicked`` carries.
        self._btn_undo.clicked.connect(lambda _checked=False: self.undo_requested.emit())
        self._btn_redo.clicked.connect(lambda _checked=False: self.redo_requested.emit())
        self._btn_reset.clicked.connect(
            lambda _checked=False: self.reset_panel_requested.emit()
        )

    # ----- inbound state plumbing ----------------------------------------------

    def set_undo_available(self, available: bool) -> None:
        """Reflect whether the active Loadout has any undoable history."""
        self._btn_undo.setEnabled(bool(available))

    def set_redo_available(self, available: bool) -> None:
        """Reflect whether the active Loadout has anything to redo."""
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

        Bold is set on the button's ``QFont``, not in QSS, so
        ``QFontMetrics`` and the sizeHint see the bold text.
        """
        engaged = self._btn_panic.isChecked()
        self._btn_panic.setText(
            BUTTON_LABEL_PANIC_ON if engaged else BUTTON_LABEL_PANIC_OFF
        )
        font = self._btn_panic.font()
        font.setBold(engaged)
        self._btn_panic.setFont(font)
        # Re-evaluate the `:checked` QSS and re-measure the size hint.
        self._btn_panic.style().unpolish(self._btn_panic)
        self._btn_panic.style().polish(self._btn_panic)
        self._btn_panic.updateGeometry()

    def _on_panic_toggled(self, checked: bool) -> None:
        self._apply_panic_label()
        self.panic_toggled.emit(bool(checked))

