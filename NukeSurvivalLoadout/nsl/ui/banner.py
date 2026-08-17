"""Change-detected banner - restart-required notification strip.

One horizontal strip over the top of the Plugins grid, hidden by
default. The wiring layer calls ``set_state(kind, count)`` then
``show()``, and ``hide()`` once the changes are resolved.

The strip is an overlay. :meth:`Banner.attach_to` pins it to the top
edge of a host. It reserves no layout space and never joins the host
layout.
"""

from __future__ import annotations

from enum import Enum

from nsl import compat


# ---------------------------------------------------------------------------
# Variants + message templates
# ---------------------------------------------------------------------------


class BannerKind(Enum):
    """Which trigger drove the banner up.

    The kind picks the fill colour and the message template. The count
    fills the ``{n}`` slot.

    * ``PENDING_CHANGES`` - unsaved in-memory edits. Blue.
    * ``GLOBAL_DRIFT`` - the Global default moved away from the active
      Loadout. Amber.
    * ``SAVED_AWAITING_RESTART`` - saved, but Nuke still runs the old
      set. Green.
    * ``PANIC_ENGAGED`` - the next restart skips every user Plugin. Red.
    """

    PENDING_CHANGES = "pending_changes"
    GLOBAL_DRIFT = "global_drift"
    SAVED_AWAITING_RESTART = "saved_awaiting_restart"
    PANIC_ENGAGED = "panic_engaged"


#: ``{n}`` is the count of pending Loadout changes.
MESSAGE_PENDING_CHANGES = (
    "<b>{n}</b> Pending {noun}, save Loadout to apply changes."
)

#: ``{n}`` is the count of Plugins that diverge from the new default.
MESSAGE_GLOBAL_DRIFT = "Global Loadout updated. {n} {noun} diverge from new default."

#: ``{n}`` is the count of changes the saved Loadout applies on restart.
MESSAGE_SAVED_AWAITING_RESTART = (
    "<b>{n}</b> Saved {noun}, restart Nuke to apply."
)

#: ``{n}`` is unused. Panic is on or off, not a count. The sentence
#: stays true for a user with no Global set.
MESSAGE_PANIC_ENGAGED = (
    "<b>Panic Mode enabled.</b> On next restart, all User Plugins "
    "will be skipped."
)

#: Appended to ``MESSAGE_PANIC_ENGAGED`` only when a Global Loadout is
#: configured. The leading space is on purpose and joins the sentences.
MESSAGE_PANIC_ENGAGED_GLOBAL_SUFFIX = " Only Global Plugins will be loaded."


# ---------------------------------------------------------------------------
# Palette - colours are locked design tokens
# ---------------------------------------------------------------------------

# Each fill is blended toward ``_PANEL_BG_RGB`` so the strip reads as a
# wash, not a bar.

_PANEL_BG_RGB = (57, 57, 57)  # #393939 - the panel body background.


def _blend_with_panel(rgb, t: float = 0.5) -> tuple:
    """Blend ``rgb`` toward the panel background by *t*, from 0 to 1.

    ``t=0`` keeps ``rgb``. ``t=1`` gives the panel background.
    """
    pr, pg, pb = _PANEL_BG_RGB
    r, g, b = rgb
    return (
        int(round(pr * t + r * (1 - t))),
        int(round(pg * t + g * (1 - t))),
        int(round(pb * t + b * (1 - t))),
    )


_BG_PENDING_CHANGES = _blend_with_panel((60, 90, 120))
_BG_GLOBAL_DRIFT = _blend_with_panel((148, 121, 74))
# Blended twice, unlike the other fills. Green reads brighter than the
# blue after one blend, so one blend leaves the two looking unrelated.
_BG_SAVED_AWAITING_RESTART = _blend_with_panel(_blend_with_panel((80, 130, 80)))
# ``(196, 56, 56)`` is ``#c43838``, the engaged red of the toolbar's
# Panic Mode button. Keep the two in step.
_BG_PANIC_ENGAGED = _blend_with_panel((196, 56, 56))

# Alpha on the body fill, so the grid shows through the strip. 220 is
# about 86 %, which stays readable.
_BG_ALPHA = 220

#: ``BannerKind`` to background RGB. A new variant needs this lookup
#: only, not a change in ``paintEvent``.
_KIND_TO_BG = {
    BannerKind.PENDING_CHANGES: _BG_PENDING_CHANGES,
    BannerKind.GLOBAL_DRIFT: _BG_GLOBAL_DRIFT,
    BannerKind.SAVED_AWAITING_RESTART: _BG_SAVED_AWAITING_RESTART,
    BannerKind.PANIC_ENGAGED: _BG_PANIC_ENGAGED,
}

# Not white. White would make the red and amber banners read as alarms.
_FG = "#dcdcdc"

# The × rests below full opacity so it reads as secondary to the message.
_DISMISS_OPACITY_RESTING = 0.7
_DISMISS_OPACITY_HOVER = 1.0


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

# Width of the × slot at the right end. The left spacer uses the same
# width, so the message label sits centred on the strip.
_DISMISS_SLOT_WIDTH = 28

# Vertical padding stays at 0. The counter chips are pinned at 18 px and
# any padding grows the row. Horizontal 10 keeps the text away from the
# rounded corners.
_STRIP_V_PADDING = 0
_STRIP_H_PADDING = 10


class Banner(compat.QtWidgets.QWidget):
    """Single-line restart-required strip with an ``×`` dismiss button.

    Hidden by default and reserves no layout space. Call
    :meth:`set_state` to pick the :class:`BannerKind` and the count.
    """

    # Re-exported so callers reach the enum and templates from the class.
    BannerKind = BannerKind
    MESSAGE_PENDING_CHANGES = MESSAGE_PENDING_CHANGES
    MESSAGE_GLOBAL_DRIFT = MESSAGE_GLOBAL_DRIFT

    dismissed = compat.QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Zero layout space when hidden. Only matters for a caller that
        # still adds the banner to a layout instead of using attach_to.
        policy = compat.QtWidgets.QSizePolicy(
            compat.QtWidgets.QSizePolicy.Expanding,
            compat.QtWidgets.QSizePolicy.Fixed,
        )
        policy.setRetainSizeWhenHidden(False)
        self.setSizePolicy(policy)

        self.setObjectName("NslChangeDetectedBanner")
        # Translucent background, so the ``paintEvent`` alpha fill blends
        # over the grid instead of an opaque widget background.
        self.setAttribute(
            compat.QtCore.Qt.WA_TranslucentBackground, True
        )
        # Set by ``attach_to``. None means no host tracking yet.
        self._overlay_host = None

        layout = compat.QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(
            _STRIP_H_PADDING,
            _STRIP_V_PADDING,
            _STRIP_H_PADDING,
            _STRIP_V_PADDING,
        )
        layout.setSpacing(0)

        left_spacer = compat.QtWidgets.QWidget(self)
        left_spacer.setFixedWidth(_DISMISS_SLOT_WIDTH)
        left_spacer.setAttribute(
            compat.QtCore.Qt.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(left_spacer)

        self._label = compat.QtWidgets.QLabel("", self)
        self._label.setObjectName("NslChangeDetectedBannerLabel")
        self._label.setAlignment(compat.QtCore.Qt.AlignCenter)
        self._label.setStyleSheet(
            # The panel caps the banner height at 18 px, so the font
            # size only drives readability, not the band height.
            "color: " + _FG + "; background: transparent; font-size: 10pt;"
        )
        layout.addWidget(self._label, stretch=1)

        self._dismiss_button = compat.QtWidgets.QToolButton(self)
        self._dismiss_button.setObjectName("NslChangeDetectedBannerDismiss")
        self._dismiss_button.setText("×")
        self._dismiss_button.setToolTip("Dismiss")
        self._dismiss_button.setCursor(compat.QtCore.Qt.PointingHandCursor)
        # Do not call ``setAutoRaise(True)``. Qt's auto-raise owns the
        # hover repaint and blocks the QSS ``:hover`` background, so the
        # hover lift never appears.
        self._dismiss_button.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._dismiss_button.setFixedWidth(_DISMISS_SLOT_WIDTH)
        self._dismiss_button.setStyleSheet(
            "QToolButton#NslChangeDetectedBannerDismiss {"
            "  color: " + _FG + ";"
            "  background: transparent;"
            "  border: none;"
            # 15 pt against the label's 10 pt, so the × reads as a
            # button and not as punctuation.
            "  font-size: 15pt;"
            f"  opacity: {_DISMISS_OPACITY_RESTING};"
            "  padding: 0;"
            "}"
            "QToolButton#NslChangeDetectedBannerDismiss:hover {"
            f"  opacity: {_DISMISS_OPACITY_HOVER};"
            "  color: #ffffff;"
            "  background-color: rgba(255,255,255,0.18);"
            "  border-radius: 3px;"
            "}"
        )
        self._dismiss_button.clicked.connect(self._on_dismiss_clicked)
        layout.addWidget(self._dismiss_button)

        # The wiring layer overrides this before each show().
        self._kind: BannerKind = BannerKind.PENDING_CHANGES
        self._count: int = 0
        # PANIC_ENGAGED only. True appends the Global sentence.
        self._globals_present: bool = False
        self._apply_state()

        super().setVisible(False)

    # ---- public API -----------------------------------------------------

    def set_state(
        self, kind: BannerKind, count: int = 0, *, globals_present: bool = False
    ) -> None:
        """Set the variant and the count used in the message template.

        Calling it again with the same values does nothing.
        ``globals_present`` affects PANIC_ENGAGED only. True appends the
        Global sentence.
        """
        if not isinstance(kind, BannerKind):
            kind = BannerKind(kind)
        count = max(0, int(count))
        globals_present = bool(globals_present)
        if (
            kind is self._kind
            and count == self._count
            and globals_present == self._globals_present
        ):
            return
        self._kind = kind
        self._count = count
        self._globals_present = globals_present
        self._apply_state()

    def attach_to(self, host) -> None:
        """Install the overlay positioner on *host*.

        The banner reparents to *host*, pins to the top edge, and re-pins
        on every host resize. The host lays out its other children as if
        the banner were not there.
        """
        if self._overlay_host is host:
            return
        if self._overlay_host is not None:
            self._overlay_host.removeEventFilter(self)
        self._overlay_host = host
        if host is None:
            return
        self.setParent(host)
        host.installEventFilter(self)
        self._reposition_overlay()
        self.raise_()

    def _reposition_overlay(self) -> None:
        host = self._overlay_host
        if host is None:
            return
        self.setGeometry(0, 0, host.width(), self.sizeHint().height())

    def eventFilter(self, watched, event):
        if (
            watched is self._overlay_host
            and event.type() == compat.QtCore.QEvent.Resize
        ):
            self._reposition_overlay()
        return super().eventFilter(watched, event)

    def kind(self) -> BannerKind:
        """Current variant."""
        return self._kind

    def count(self) -> int:
        """Current count."""
        return self._count

    def message(self) -> str:
        """Currently rendered message text."""
        return self._label.text()

    def dismiss_button(self):
        """Return the dismiss ``QToolButton``."""
        return self._dismiss_button

    # ---- internals ------------------------------------------------------

    def _apply_state(self) -> None:
        """Recompute the body fill and the formatted message text.

        The fill goes through ``paintEvent``, not ``autoFillBackground``.
        Qt on macOS ignores the alpha in a ``QPalette.Window`` colour.
        """
        if self._kind is BannerKind.PENDING_CHANGES:
            template = MESSAGE_PENDING_CHANGES
            noun = "Change" if self._count == 1 else "Changes"
        elif self._kind is BannerKind.GLOBAL_DRIFT:
            template = MESSAGE_GLOBAL_DRIFT
            noun = "Plugin" if self._count == 1 else "Plugins"
        elif self._kind is BannerKind.SAVED_AWAITING_RESTART:
            template = MESSAGE_SAVED_AWAITING_RESTART
            noun = "Change" if self._count == 1 else "Changes"
        else:  # PANIC_ENGAGED
            template = MESSAGE_PANIC_ENGAGED
            if self._globals_present:
                template = template + MESSAGE_PANIC_ENGAGED_GLOBAL_SUFFIX
            # The panic template has no ``{n}`` or ``{noun}`` slot.
            # ``format`` accepts the extra arguments anyway.
            noun = ""
        # The copy uses ``<b>`` around the count. Without RichText the
        # tag shows as literal characters under Fusion.
        self._label.setTextFormat(compat.QtCore.Qt.RichText)
        try:
            text = template.format(n=self._count, noun=noun)
        except (KeyError, IndexError):
            # Only fires if a future template adds another placeholder.
            # PANIC_ENGAGED has no slots and formats fine.
            text = template
        self._label.setText(text)
        self.update()

    def paintEvent(self, event):
        """Draw the semi-transparent body fill behind the message."""
        rgb = _KIND_TO_BG.get(self._kind, _BG_PENDING_CHANGES)
        r, g, b = rgb
        painter = compat.QtGui.QPainter(self)
        try:
            painter.setRenderHint(
                compat.QtGui.QPainter.Antialiasing, False
            )
            painter.fillRect(
                self.rect(), compat.QtGui.QColor(r, g, b, _BG_ALPHA)
            )
        finally:
            painter.end()

    def _on_dismiss_clicked(self) -> None:
        # Hide first and emit second, so a listener that checks
        # visibility sees the banner already hidden.
        self.hide()
        self.dismissed.emit()

