"""Shared button vocabularies for the NSL panel.

Each class here is one button vocabulary used in several places. Edit
the class once and every site updates together, so two sites of the
same kind never drift apart visually.

* :class:`HybridTextButton` - Nuke-hybrid basic-text action button. Used
  for every plain text label: Undo, Redo, Reset Panel, Add Plugins
  Folder, Rescan Plugins, Save, Save As, Import, Export.
* :class:`HybridHoverComboBox` - ``QComboBox`` with the same hover wash
  and pointing-hand cursor, for dropdowns that sit beside the action
  buttons in one row. Currently the Plugins grid toolbar Sort selector.

Not for the Panic button, icon-only buttons, pill triggers, or tab-bar
labels. Those carry their own vocabulary.
"""

from __future__ import annotations

from nsl import compat
from nsl.ui._theme import NUKE_ORANGE_HEX


class HybridTextButton(compat.QtWidgets.QPushButton):
    """Canonical Nuke-hybrid basic-text action button.

    Painting is left to the underlying Qt style. That is Nuke's
    ``HybridStyle`` inside Nuke, and Fusion with the dark palette from
    :mod:`nsl.ui._theme` outside it.

    Do not call ``setStyleSheet`` on an instance. QSS drops a
    ``QPushButton`` out of native style sizing and collapses it to
    text-content height.

    Native Fusion hover is too weak next to the icon buttons, so
    :meth:`paintEvent` adds a translucent white wash while hovered and
    not pressed. Raise ``_HOVER_WASH_ALPHA`` for more lift.

    The vertical size policy is ``Fixed``. Without it a parent layout
    that gives out expanding vertical space stretches this button
    taller than its siblings elsewhere on the panel.
    """

    # Padding each side. It overrides the QStyle CT_PushButton
    # minimum-width floor, about 75px. Without it a short label like
    # "Undo" is inflated to the floor and a long label hugs the text.
    _TEXT_PADDING = 12
    _FRAME = 1

    # Alpha 0-255 of the white wash painted over the native button on
    # hover. About 10%, deliberately subtle. Raise it for more lift.
    _HOVER_WASH_ALPHA = 26
    # Wash corner radius. Close enough to the native button radius that
    # the wash does not leak past the corners.
    _HOVER_WASH_RADIUS = 3

    # Stroke width of the first-run orange border. 1.5px reads as an
    # accent on the dark panel without competing with the label.
    _FIRST_RUN_BORDER_WIDTH = 1.5

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setSizePolicy(
            compat.QtWidgets.QSizePolicy.Preferred,
            compat.QtWidgets.QSizePolicy.Fixed,
        )
        # Without WA_Hover the enter and leave events do not fire, so
        # the ``_hover`` flag that paintEvent reads never flips.
        self.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._hover = False
        self._first_run_highlight = False
        # Every action button in the panel reads as clickable. Disabled
        # buttons fall back to the arrow, in ``changeEvent`` below.
        self.setCursor(compat.QtCore.Qt.PointingHandCursor)

    def sizeHint(self) -> "compat.QtCore.QSize":  # noqa: N802 - Qt override
        natural = super().sizeHint()
        text = self.text().replace("&", "")
        text_width = self.fontMetrics().horizontalAdvance(text)
        uniform = text_width + 2 * self._TEXT_PADDING + 2 * self._FRAME
        return compat.QtCore.QSize(uniform, natural.height())

    def minimumSizeHint(self) -> "compat.QtCore.QSize":  # noqa: N802 - Qt override
        # Mirror sizeHint. A layout under tight width would otherwise
        # fall back to the QStyle min-width floor.
        return self.sizeHint()

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        # Swap the cursor on enable change. A disabled button kept the
        # pointing hand otherwise, and still read as clickable.
        if event.type() == compat.QtCore.QEvent.EnabledChange:
            self.setCursor(
                compat.QtCore.Qt.PointingHandCursor
                if self.isEnabled()
                else compat.QtCore.Qt.ArrowCursor
            )
        super().changeEvent(event)

    def set_first_run_highlight(self, enabled: bool) -> None:
        """Toggle the nuke-orange first-run affordance border.

        Set it ON when this button is the first action the user should
        take. Currently only Add Plugins Folder in the empty state. Set
        it OFF as soon as that state ends. Idempotent.
        """
        if self._first_run_highlight == enabled:
            return
        self._first_run_highlight = enabled
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)

        # Drawn before the hover wash, so a hover brightens the orange
        # instead of competing with it. No border while disabled.
        if self._first_run_highlight and self.isEnabled():
            painter = compat.QtGui.QPainter(self)
            try:
                painter.setRenderHint(
                    compat.QtGui.QPainter.Antialiasing, True
                )
                pen = compat.QtGui.QPen(
                    compat.QtGui.QColor(NUKE_ORANGE_HEX)
                )
                pen.setWidthF(self._FIRST_RUN_BORDER_WIDTH)
                painter.setPen(pen)
                painter.setBrush(compat.QtCore.Qt.NoBrush)
                # Inset by half the stroke, so the line is not clipped
                # at the widget edge.
                inset = self._FIRST_RUN_BORDER_WIDTH / 2.0
                rect = compat.QtCore.QRectF(self.rect()).adjusted(
                    inset, inset, -inset, -inset
                )
                painter.drawRoundedRect(
                    rect,
                    self._HOVER_WASH_RADIUS,
                    self._HOVER_WASH_RADIUS,
                )
            finally:
                painter.end()

        if not self._hover or self.isDown() or not self.isEnabled():
            # No wash while pressed. The native darkening is the right
            # down-state, and a wash would lift it back up. No wash
            # while disabled either, it would read as clickable.
            return
        painter = compat.QtGui.QPainter(self)
        try:
            painter.setRenderHint(compat.QtGui.QPainter.Antialiasing, True)
            painter.setPen(compat.QtCore.Qt.NoPen)
            painter.setBrush(
                compat.QtGui.QColor(255, 255, 255, self._HOVER_WASH_ALPHA)
            )
            # Inset 1px so the wash stays inside the native border.
            rect = compat.QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(
                rect, self._HOVER_WASH_RADIUS, self._HOVER_WASH_RADIUS
            )
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Clickable-cursor helper
# ---------------------------------------------------------------------------


class _ClickableCursorFilter(compat.QtCore.QObject):
    """Event filter that swaps a watched widget's cursor on enable change.

    It lives as a child of the watched widget, so it dies with it.
    """

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == compat.QtCore.QEvent.EnabledChange:
            watched.setCursor(
                compat.QtCore.Qt.PointingHandCursor
                if watched.isEnabled()
                else compat.QtCore.Qt.ArrowCursor
            )
        return False  # never consume - let the widget keep handling.


def install_clickable_cursor(widget) -> None:
    """Make ``widget`` carry the panel-wide clickable cursor vocabulary.

    Pointing hand while ``widget`` is enabled, arrow when disabled. Use
    it on any widget that reads as clickable but cannot subclass
    :class:`HybridTextButton` or :class:`HybridHoverComboBox`. For
    example the Panic button, the loadout-strip glyph buttons, the
    folder card row controls, and the side panel tab bar.

    The event filter is parented to the widget, so it dies with it.
    Calling this twice is harmless.
    """
    initial = (
        compat.QtCore.Qt.PointingHandCursor
        if widget.isEnabled()
        else compat.QtCore.Qt.ArrowCursor
    )
    widget.setCursor(initial)
    filt = _ClickableCursorFilter(widget)
    widget.installEventFilter(filt)


class HybridHoverComboBox(compat.QtWidgets.QComboBox):
    """``QComboBox`` carrying the HybridTextButton hover-wash vocabulary.

    A subclass, not QSS. Adding ``:hover`` background rules to a combo
    forces the whole control off native style sizing, and the dropdown
    chrome collapses to QSS box rendering. Painting the wash on top of
    the native paint keeps the chrome native.

    Same wash alpha, radius, and cursor flip as
    :class:`HybridTextButton`. Only the body rect gets the overlay, so
    the dropdown arrow stays native.
    """

    _HOVER_WASH_ALPHA = HybridTextButton._HOVER_WASH_ALPHA
    _HOVER_WASH_RADIUS = HybridTextButton._HOVER_WASH_RADIUS

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._hover = False
        self.setCursor(compat.QtCore.Qt.PointingHandCursor)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        if event.type() == compat.QtCore.QEvent.EnabledChange:
            self.setCursor(
                compat.QtCore.Qt.PointingHandCursor
                if self.isEnabled()
                else compat.QtCore.Qt.ArrowCursor
            )
        super().changeEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        if not self._hover or not self.isEnabled():
            return
        painter = compat.QtGui.QPainter(self)
        try:
            painter.setRenderHint(compat.QtGui.QPainter.Antialiasing, True)
            painter.setPen(compat.QtCore.Qt.NoPen)
            painter.setBrush(
                compat.QtGui.QColor(255, 255, 255, self._HOVER_WASH_ALPHA)
            )
            # Same 1px inset as HybridTextButton.
            rect = compat.QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(
                rect, self._HOVER_WASH_RADIUS, self._HOVER_WASH_RADIUS
            )
        finally:
            painter.end()
