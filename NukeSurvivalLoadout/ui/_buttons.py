"""Shared button vocabularies for the NSL panel.

Each class in this module is one button vocabulary used in multiple
places across the panel. Edit the class once and every site updates
together - the whole point is that we never let two "same kind of
button" sites drift visually.

Current vocabularies:

* :class:`HybridTextButton` - Nuke-hybrid-styled basic-text action
  button. Used for every plain text action label: Undo / Redo / Reset
  Panel / Add Plugins Folder / Rescan Plugins / Save / Save As /
  Import / Export / etc.

* :class:`HybridHoverComboBox` - ``QComboBox`` carrying the same
  hover-wash overlay + pointing-hand cursor as :class:`HybridTextButton`.
  Used for dropdowns that read as siblings of the action buttons in the
  same row (currently: the Plugins grid toolbar's Sort selector).

  **Not** used for: the Panic button (danger control with its own
  red-state vocabulary), icon-only buttons (eye / ▲ / ▼ / ×),
  specialised pill triggers, or tab-bar labels.
"""

from __future__ import annotations

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout.ui._theme import NUKE_ORANGE_HEX


class HybridTextButton(compat.QtWidgets.QPushButton):
    """Canonical Nuke-hybrid basic-text action button.

    Renders via the underlying Qt style:

    * Inside Nuke - Nuke's ``HybridStyle`` paints the button natively,
      same chrome as every other Nuke panel button.
    * Outside Nuke (standalone / offscreen rendering) - Qt
      Fusion paints it via the dark palette applied in
      :mod:`NukeSurvivalLoadout.ui._theme`. Visually adjacent to HybridStyle without
      being identical.

    No per-instance ``setStyleSheet``: applying QSS to a ``QPushButton``
    silently drops it out of native style sizing and collapses the
    button to text-content height, which is the wrong default for this
    vocabulary. Stay bare; let the style engine size and paint.

    **Hover boost.** Native Fusion hover is too subtle for NSL - siblings
    like the disc-shaped clear glyph and the loadout-strip icon buttons
    light up clearly while a HybridTextButton barely changes. Paint a
    light translucent-white wash on top of the native paint while
    hovered (and not pressed) so all sibling buttons read at comparable
    hover intensity. The wash is intentionally subtle - bump
    ``_HOVER_WASH_ALPHA`` if more lift is wanted.

    The vertical size policy is pinned to ``Fixed`` so the button always
    renders at its ``sizeHint`` height regardless of the parent layout.
    Without this, a permissive parent layout (e.g. a row that gives its
    children expanding vertical space) stretches the button taller than
    its sibling instances elsewhere on the panel, breaking the
    "every button of this class looks identical" contract.

    Caller responsibilities are unchanged from ``QPushButton``: pass
    the label and ``parent``, connect ``clicked``, set ``objectName``
    if needed for wiring.
    """

    # Uniform text-to-button-edge padding (px each side) used to override
    # the underlying QStyle's CT_PushButton minimum-width floor (~75 px on
    # QCommonStyle / 80 px on Fusion). Without this override, short-label
    # buttons like "Undo" get inflated to the floor while long-label ones
    # like "Add Plugins Folder" hug the text, producing visibly different
    # chrome padding across what is meant to be one button family.
    _TEXT_PADDING = 12
    _FRAME = 1

    # Alpha (0-255) of the translucent-white wash painted on top of the
    # native button during hover. ~10 % feels deliberately subtle next
    # to the icon-button hover lifts (which use 0.18 alpha) - bump if
    # the hierarchy needs the action buttons to read louder.
    _HOVER_WASH_ALPHA = 26
    # Radius of the wash overlay. Matches the Fusion / HybridStyle native
    # button radius closely enough that the wash hugs the button shape
    # without leaking past corners.
    _HOVER_WASH_RADIUS = 3

    # First-run highlight - a continuous nuke-orange border drawn on
    # top of the native chrome to point the user at the first action
    # they need to take (currently only used on Add Plugins Folder
    # when the panel is in the empty-state). Set via
    # :meth:`set_first_run_highlight`; off by default.
    # 1.5 px stroke is heavy enough to read as an intentional accent
    # on the dark panel without competing with the button's own text.
    _FIRST_RUN_BORDER_WIDTH = 1.5

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setSizePolicy(
            compat.QtWidgets.QSizePolicy.Preferred,
            compat.QtWidgets.QSizePolicy.Fixed,
        )
        # WA_Hover ensures enter/leaveEvent fire reliably under both
        # styles. Without it Fusion would still hover-repaint but our
        # custom paintEvent's _hover flag would never flip.
        self.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._hover = False
        self._first_run_highlight = False
        # Cursor: every action button in the panel reads as clickable, so
        # the cursor should always flip to the pointing hand when an
        # enabled button is under the pointer. Disabled buttons fall
        # back to the default arrow (handled in ``changeEvent`` below).
        # Setting this on the base class keeps the cursor behaviour
        # consistent across every site that uses HybridTextButton (Save,
        # Save As, Import, Export, Undo, Redo, Reset Panel, Add Plugins
        # Folder, Rescan Plugins, the bulk-action toolbar, Select /
        # Deselect filtered, Clear selection, Reset Global Plugins to
        # Default, etc.) without each call site having to remember.
        self.setCursor(compat.QtCore.Qt.PointingHandCursor)

    def sizeHint(self) -> "compat.QtCore.QSize":  # noqa: N802 - Qt override
        natural = super().sizeHint()
        text = self.text().replace("&", "")
        text_width = self.fontMetrics().horizontalAdvance(text)
        uniform = text_width + 2 * self._TEXT_PADDING + 2 * self._FRAME
        return compat.QtCore.QSize(uniform, natural.height())

    def minimumSizeHint(self) -> "compat.QtCore.QSize":  # noqa: N802 - Qt override
        # Mirror sizeHint so layouts that consult minimumSizeHint (e.g.
        # under tight horizontal space) honour the same uniform padding
        # rather than dropping back to the QStyle's intrinsic min-width
        # floor, which would re-introduce the inconsistency.
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
        # When the button flips between enabled and disabled, swap the
        # cursor so a disabled button reads as inert under the pointer.
        # Without this, a disabled button (e.g. ``Deselect All (0)`` at
        # rest) still showed the pointing hand on hover, which read as
        # "clickable but doing nothing" - a confusing affordance signal.
        if event.type() == compat.QtCore.QEvent.EnabledChange:
            self.setCursor(
                compat.QtCore.Qt.PointingHandCursor
                if self.isEnabled()
                else compat.QtCore.Qt.ArrowCursor
            )
        super().changeEvent(event)

    def set_first_run_highlight(self, enabled: bool) -> None:
        """Toggle the nuke-orange first-run affordance border.

        Set ON when this button represents the first action the user
        should take (currently only Add Plugins Folder while the panel
        is in the empty-state). Set OFF the moment that state ends so
        the affordance doesn't linger.
        Idempotent - no repaint when the value isn't changing.
        """
        if self._first_run_highlight == enabled:
            return
        self._first_run_highlight = enabled
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)

        # First-run highlight border. Drawn before the hover wash so a
        # hover lifts the affordance rather than competing with it
        # (semitransparent white over orange reads as a brighter orange).
        # No border while disabled - there's no first action to take if
        # the button can't be clicked.
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
                # Inset by half the stroke so the centreline sits on
                # the outer edge of the visible button rect (no
                # clipping at the widget boundary).
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
            # No wash while pressed - native pressed-state darkening is
            # already the correct down-state read; layering wash on top
            # would lift the depressed button visually back up.
            #
            # No wash while disabled - a disabled button is inert; the
            # hover wash would otherwise read as "still clickable" and
            # contradict the dim text + disabled chrome native style
            # paints underneath.
            return
        painter = compat.QtGui.QPainter(self)
        try:
            painter.setRenderHint(compat.QtGui.QPainter.Antialiasing, True)
            painter.setPen(compat.QtCore.Qt.NoPen)
            painter.setBrush(
                compat.QtGui.QColor(255, 255, 255, self._HOVER_WASH_ALPHA)
            )
            # Inset 1 px so the wash sits inside the native border -
            # otherwise the overlay's corners can paint over the dark
            # 1 px outline native style draws.
            rect = compat.QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(
                rect, self._HOVER_WASH_RADIUS, self._HOVER_WASH_RADIUS
            )
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Clickable-cursor helper - apply to any QWidget that can't (or shouldn't)
# subclass HybridTextButton but still reads as interactive.
# ---------------------------------------------------------------------------


class _ClickableCursorFilter(compat.QtCore.QObject):
    """Event filter that swaps a watched widget's cursor on enable change.

    Lives as a child of the watched widget (so its lifetime is bound)
    and listens for ``QEvent.EnabledChange`` - when the widget toggles
    enabled / disabled, the cursor flips between PointingHand and Arrow.
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

    Sets the pointing-hand cursor while ``widget`` is enabled and reverts
    to the arrow cursor when disabled. Use this on any widget that reads
    as clickable but can't (or shouldn't) subclass :class:`HybridTextButton`
    / :class:`HybridHoverComboBox` - e.g. the top-toolbar Panic button
    (a danger-state ``QPushButton`` with its own red-state QSS), the
    loadout-strip glyph icon buttons (rename / duplicate / delete), the
    folder card per-row controls (eye / ▲ / ▼ / ✕), and the side panel's
    Summary / Info / Log tab bar.

    The helper installs an event filter parented to the widget so the
    filter dies when the widget does. Calling twice is harmless - the
    initial cursor set is idempotent and Qt deduplicates event-filter
    installs by (watcher, target) pair.
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

    Why a subclass instead of QSS ``:hover`` rules?
        QComboBox QSS is famously sticky - adding ``:hover`` background
        rules to a combo's QSS forces the whole control off the native
        style sizing path (HybridStyle inside Nuke, Fusion outside
        Nuke) and the dropdown chrome collapses to QSS box
        rendering, which doesn't match the rest of the panel. Painting
        the wash ON TOP of the native paint keeps the body chrome native
        AND lifts the hover read to match the sibling action buttons.

    Visual contract: same wash alpha, same radius, same enabled / disabled
    cursor flip as :class:`HybridTextButton`. The dropdown arrow keeps
    its native rendering - only the body rect gets the overlay.

    Hover behaviour while the popup is open: Qt's native paint already
    holds the "control is active" tint, and our wash sits on top - the
    combined effect reads as "pressed + lit" which is the intended
    interactive state.
    """

    _HOVER_WASH_ALPHA = HybridTextButton._HOVER_WASH_ALPHA
    _HOVER_WASH_RADIUS = HybridTextButton._HOVER_WASH_RADIUS

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._hover = False
        # Pointing-hand cursor on enable; arrow on disable. Mirrors
        # HybridTextButton's contract so dropdowns and buttons read as
        # the same interactive vocabulary across the panel.
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
            # Same 1 px inset as HybridTextButton so the wash respects
            # the native border.
            rect = compat.QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(
                rect, self._HOVER_WASH_RADIUS, self._HOVER_WASH_RADIUS
            )
        finally:
            painter.end()
