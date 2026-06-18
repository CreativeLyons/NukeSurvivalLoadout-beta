"""Change-detected banner - restart-required notification strip.

A single horizontal strip that surfaces "something changed, restart to
apply" notices over the top of the Plugins grid. Hidden by default;
the wiring layer calls ``set_state(kind, count)`` + ``show()`` on a
trigger and ``hide()`` once changes resolve.

Key behaviour:

* Variants are selected by :class:`BannerKind`, each with its own fill
  colour and message template (blue = pending, amber = Global drift,
  green = saved/awaiting restart, red = panic). Colours are kept low in
  saturation so the strip reads as informational, not alarm-red.
* **Overlay, not layout participant** - the banner floats on top of the
  grid region rather than pushing it down. Call :meth:`Banner.attach_to`
  to parent it to a host, pin it to the host's top edge, and re-position
  on host resize. It reserves zero layout space and never participates
  in the host's layout.
* **Semi-transparent** so the grid shows through the strip.
* Message text is centred; the ``×`` dismiss glyph sits at the right
  end, balanced by a left spacer of equal width. Clicking × hides the
  banner and emits :attr:`Banner.dismissed`.
* No animation - snaps in / out.

Public API: :class:`BannerKind` (variant enum), :class:`Banner` (the
widget), :attr:`Banner.dismissed` (signal emitted on × click).
"""

from __future__ import annotations

from enum import Enum

from nsl import compat


# ---------------------------------------------------------------------------
# Variants + message templates
# ---------------------------------------------------------------------------


class BannerKind(Enum):
    """Which trigger class drove the banner up.

    The kind picks the body fill colour and the message template; the
    count fills the ``{n}`` slot. All variants share the same chrome
    (height, alignment, dismiss button) - only the fill colour and
    message change.

    Variants:

    * ``PENDING_CHANGES`` - in-memory edits not yet saved. Blue.
    * ``GLOBAL_DRIFT`` - Global default drifted from the active
      Loadout's expectations. Amber.
    * ``SAVED_AWAITING_RESTART`` - edits have been saved to disk, but
      Nuke still has the old set loaded; a restart is required to
      pick up the new state. Green.
    * ``PANIC_ENGAGED`` - Panic Mode is on; next restart will skip
      every user-added Plugin. Red.
    """

    PENDING_CHANGES = "pending_changes"
    GLOBAL_DRIFT = "global_drift"
    SAVED_AWAITING_RESTART = "saved_awaiting_restart"
    PANIC_ENGAGED = "panic_engaged"


#: Message template for the PENDING_CHANGES variant. ``{n}`` is the count
#: of pending Loadout changes (enabled/disabled diffs plus Added/Missing).
#: Kept short to match the green SAVED_AWAITING_RESTART banner's length.
#: The two banners form a two-step flow - blue says what to do NOW (Save),
#: green takes over after Save and says what's left (restart Nuke). Each
#: banner stays tight and scannable instead of duplicating the full flow.
MESSAGE_PENDING_CHANGES = (
    "<b>{n}</b> Pending {noun}, save Loadout to apply changes."
)

#: Message template for the GLOBAL_DRIFT variant. ``{n}`` is the
#: count of Plugins whose state diverges from the new Global default.
MESSAGE_GLOBAL_DRIFT = "Global Loadout updated. {n} {noun} diverge from new default."

#: Message template for the SAVED_AWAITING_RESTART variant. ``{n}`` is
#: the count of plugin changes the saved Loadout will apply on next
#: restart. Mirrors the PENDING_CHANGES format with the count first so
#: the banner scans the same way (the bold number leads the eye in both
#: the blue "Pending" state and the green "Saved" state).
MESSAGE_SAVED_AWAITING_RESTART = (
    "<b>{n}</b> Saved {noun}, restart Nuke to apply."
)

#: Message template for the PANIC_ENGAGED variant. ``{n}`` is unused -
#: panic mode is binary, not count-driven. The banner leads with the
#: plain, universally-true statement (all User Plugins skipped) so users
#: with no Global set aren't confused by a mention of "Global Plugins"
#: they never configured. The Global sentence is appended ONLY when a
#: Global is actually present (see ``MESSAGE_PANIC_ENGAGED_GLOBAL_SUFFIX``),
#: which simultaneously answers the Global-using crowd's question "does
#: panic skip my Globals too?" (no, Globals still load).
MESSAGE_PANIC_ENGAGED = (
    "<b>Panic Mode enabled.</b> On next restart, all User Plugins "
    "will be skipped."
)

#: Appended to ``MESSAGE_PANIC_ENGAGED`` only when a Global Loadout is
#: configured (resolved ``global_model`` is non-empty). Leading space is
#: intentional, it joins onto the base sentence.
MESSAGE_PANIC_ENGAGED_GLOBAL_SUFFIX = " Only Global Plugins will be loaded."


# ---------------------------------------------------------------------------
# Palette - colours are locked design tokens
# ---------------------------------------------------------------------------
#
# All banner fills are pulled to a 50 % blend with the panel background
# (``PANEL_BG_RGB`` = ``(57, 57, 57)``) so the strip reads as a subtle wash
# rather than a saturated bar competing with the pill chrome below. Formula:
# ``mix(panel_bg, source, 0.5)`` per channel.

_PANEL_BG_RGB = (57, 57, 57)  # #393939 - the panel body background.


def _blend_with_panel(rgb, t: float = 0.5) -> tuple:
    """Linear-interp ``rgb`` toward the panel background by *t* (0-1).

    ``t=0`` returns ``rgb`` unchanged; ``t=1`` collapses to the panel
    background; ``t=0.5`` is the canonical 50 % blend.
    """
    pr, pg, pb = _PANEL_BG_RGB
    r, g, b = rgb
    return (
        int(round(pr * t + r * (1 - t))),
        int(round(pg * t + g * (1 - t))),
        int(round(pb * t + b * (1 - t))),
    )


# Pending changes - dusty blue. Pre-blend: ``(60, 90, 120)``.
_BG_PENDING_CHANGES = _blend_with_panel((60, 90, 120))
# Global drift - dusty amber. Pre-blend: ``(148, 121, 74)``.
_BG_GLOBAL_DRIFT = _blend_with_panel((148, 121, 74))
# Saved-awaiting-restart - dusty green. Source is double-blended
# (effectively 75 % toward gray) rather than the single 50 % blend the
# other fills use: at one blend stage green reads more vivid than the
# blue because its value channel tracks higher than the blue's blue
# channel. The extra blend lands the strip at the same perceived
# intensity as the pending-changes blue, so the two notifications read
# as siblings in one vocabulary.
_BG_SAVED_AWAITING_RESTART = _blend_with_panel(_blend_with_panel((80, 130, 80)))
# Panic engaged - dusty red. Source is the canonical panic-button
# engaged red ``#c43838`` so the banner reads as part of the same
# vocabulary as the toolbar's Panic Mode button. Pre-blend source
# ``(196, 56, 56)``.
_BG_PANIC_ENGAGED = _blend_with_panel((196, 56, 56))

# Alpha applied to the body fill so the banner reads as overlaid on the
# grid rather than as a solid strip. ~86 % stays readable while letting
# a hint of pill chrome show through behind the strip.
_BG_ALPHA = 220

#: ``BannerKind`` → background RGB tuple. Centralised so adding a new
#: variant requires touching only one lookup, not the ``paintEvent``
#: chain. Bound below the class definition (see the bottom of this
#: module) once ``BannerKind`` is in scope.
_KIND_TO_BG = {
    BannerKind.PENDING_CHANGES: _BG_PENDING_CHANGES,
    BannerKind.GLOBAL_DRIFT: _BG_GLOBAL_DRIFT,
    BannerKind.SAVED_AWAITING_RESTART: _BG_SAVED_AWAITING_RESTART,
    BannerKind.PANIC_ENGAGED: _BG_PANIC_ENGAGED,
}

# Foreground text + dismiss glyph base colour. `#dcdcdc` reads as
# "muted bright" against both reds and ambers - not flash-white, which
# would over-amplify the alarm signal.
_FG = "#dcdcdc"

# Dismiss × resting opacity - 70 % so the glyph feels secondary to the
# message text. Hover bumps to 100 %; press matches resting.
_DISMISS_OPACITY_RESTING = 0.7
_DISMISS_OPACITY_HOVER = 1.0


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

# The dismiss glyph occupies a fixed-width slot at the right end. The
# same width is reserved on the left as an empty spacer so the message
# label sits visually centred against the strip rather than pulled
# towards the × side.
_DISMISS_SLOT_WIDTH = 28

# Strip padding. Vertical at 0 so the banner sits inline with the grid
# counter row and does not push other rows above it. The counter chips
# are pinned at 18 px; banner internal padding has to be tight enough
# that the row doesn't grow. Horizontal stays at 10 to keep edge text
# away from rounded corners.
_STRIP_V_PADDING = 0
_STRIP_H_PADDING = 10


class Banner(compat.QtWidgets.QWidget):
    """Single-line restart-required strip with ``×`` dismiss.

    Hidden by default. Reserves zero layout space when hidden. Snaps in
    and out - no animation. Two variants selected by
    :class:`BannerKind`; call :meth:`set_state` to switch kind / count.
    """

    # Re-exported so callers can ``from nsl.ui.banner import Banner`` and
    # access the templates / enum from the widget class.
    BannerKind = BannerKind
    MESSAGE_PENDING_CHANGES = MESSAGE_PENDING_CHANGES
    MESSAGE_GLOBAL_DRIFT = MESSAGE_GLOBAL_DRIFT

    dismissed = compat.QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Zero layout space when hidden - also true when the banner is
        # used as an overlay (it never participates in a layout), but
        # set explicitly so callers using addWidget for legacy reasons
        # still get the right collapsing behaviour.
        policy = compat.QtWidgets.QSizePolicy(
            compat.QtWidgets.QSizePolicy.Expanding,
            compat.QtWidgets.QSizePolicy.Fixed,
        )
        policy.setRetainSizeWhenHidden(False)
        self.setSizePolicy(policy)

        self.setObjectName("NslChangeDetectedBanner")
        # Translucent background so the paintEvent's alpha fill blends
        # over the grid content underneath rather than rendering against
        # an opaque widget bg.
        self.setAttribute(
            compat.QtCore.Qt.WA_TranslucentBackground, True
        )
        # Overlay tracking - set by ``attach_to`` so resizeEvent on the
        # host widget can pin our geometry. ``None`` means "no host
        # tracking installed yet".
        self._overlay_host = None

        layout = compat.QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(
            _STRIP_H_PADDING,
            _STRIP_V_PADDING,
            _STRIP_H_PADDING,
            _STRIP_V_PADDING,
        )
        layout.setSpacing(0)

        # Left spacer balances the × width so the message reads true-centred.
        left_spacer = compat.QtWidgets.QWidget(self)
        left_spacer.setFixedWidth(_DISMISS_SLOT_WIDTH)
        left_spacer.setAttribute(
            compat.QtCore.Qt.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(left_spacer)

        # Message label - centred, single line.
        self._label = compat.QtWidgets.QLabel("", self)
        self._label.setObjectName("NslChangeDetectedBannerLabel")
        self._label.setAlignment(compat.QtCore.Qt.AlignCenter)
        self._label.setStyleSheet(
            # 10 pt for legibility. The panel hard-caps banner height to
            # 18 px via ``setFixedHeight``, so the font size only drives
            # readability, not band height - kept comfortable to read.
            "color: " + _FG + "; background: transparent; font-size: 10pt;"
        )
        layout.addWidget(self._label, stretch=1)

        # × dismiss glyph - plain QToolButton with no border / no bg, just
        # the glyph at low opacity. Hover bumps the opacity, matching the
        # canonical chrome. Width matches the left spacer so the centre
        # axis is preserved.
        self._dismiss_button = compat.QtWidgets.QToolButton(self)
        self._dismiss_button.setObjectName("NslChangeDetectedBannerDismiss")
        self._dismiss_button.setText("×")
        self._dismiss_button.setToolTip("Dismiss")
        self._dismiss_button.setCursor(compat.QtCore.Qt.PointingHandCursor)
        # Do NOT setAutoRaise(True) - Qt's auto-raise mode owns the
        # hover repaint and short-circuits QSS ``:hover`` background,
        # so the hover lift never appears. Same lesson as the
        # search-field disc-clear button which deliberately leaves
        # auto-raise off.
        self._dismiss_button.setAttribute(compat.QtCore.Qt.WA_Hover, True)
        self._dismiss_button.setFixedWidth(_DISMISS_SLOT_WIDTH)
        self._dismiss_button.setStyleSheet(
            "QToolButton#NslChangeDetectedBannerDismiss {"
            "  color: " + _FG + ";"
            "  background: transparent;"
            "  border: none;"
            # × is a typographic glyph at 15 pt - clearly larger than
            # the banner's 10 pt message label so the dismiss target
            # reads as a distinct affordance rather than punctuation.
            "  font-size: 15pt;"
            f"  opacity: {_DISMISS_OPACITY_RESTING};"
            "  padding: 0;"
            "}"
            # Hover: brighten the glyph + paint a translucent-white
            # background pad with a 3 px radius so the click target
            # reads as a real button under the cursor. Same vocabulary
            # as the search field's disc-clear hover lift.
            "QToolButton#NslChangeDetectedBannerDismiss:hover {"
            f"  opacity: {_DISMISS_OPACITY_HOVER};"
            "  color: #ffffff;"
            "  background-color: rgba(255,255,255,0.18);"
            "  border-radius: 3px;"
            "}"
        )
        self._dismiss_button.clicked.connect(self._on_dismiss_clicked)
        layout.addWidget(self._dismiss_button)

        # Default state - kind = PENDING_CHANGES, count = 0. The wiring
        # layer overrides this before each show().
        self._kind: BannerKind = BannerKind.PENDING_CHANGES
        self._count: int = 0
        # Only consulted for the PANIC_ENGAGED variant - appends the
        # "Only Global Plugins will be loaded." sentence when True.
        self._globals_present: bool = False
        self._apply_state()

        # Hidden by default.
        super().setVisible(False)

    # ---- public API -----------------------------------------------------

    def set_state(
        self, kind: BannerKind, count: int = 0, *, globals_present: bool = False
    ) -> None:
        """Update which variant is shown and the count plugged into the
        message template. Idempotent - re-running with the same args is a
        no-op for layout / repaint cost.

        ``globals_present`` only affects the PANIC_ENGAGED variant: when
        True (a Global Loadout is configured), the panic banner appends
        the "Only Global Plugins will be loaded." sentence. Ignored for
        every other kind.
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

        The banner reparents to *host*, pins to the top edge, spans the
        host's full width, and re-pins on every host resize via an
        eventFilter. The host's existing children continue to be laid
        out as if the banner did not exist - the banner overlays them.

        Typical wiring: ``banner.attach_to(grid_region_widget)``; then
        ``banner.set_state(...)`` + ``banner.show()`` to surface.
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
        # Match host width, pin to top, keep our own preferred height.
        self.setGeometry(0, 0, host.width(), self.sizeHint().height())

    def eventFilter(self, watched, event):
        # Track the host's size changes so we re-pin our geometry.
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
        """Currently rendered text - template formatted with the count."""
        return self._label.text()

    def dismiss_button(self):
        """Return the dismiss ``QToolButton``."""
        return self._dismiss_button

    # ---- internals ------------------------------------------------------

    def _apply_state(self) -> None:
        """Recompute the body fill + the formatted message text.

        Body fill goes via a custom ``paintEvent`` (not QPalette /
        autoFillBackground) so we control the alpha component. A
        ``QPalette.Window`` colour with alpha is ignored by Qt's default
        background fill on macOS; painting it ourselves with
        ``WA_TranslucentBackground`` lets the semi-transparent strip
        blend over the grid content underneath.
        """
        if self._kind is BannerKind.PENDING_CHANGES:
            template = MESSAGE_PENDING_CHANGES
            noun = "Change" if self._count == 1 else "Changes"
        elif self._kind is BannerKind.GLOBAL_DRIFT:
            template = MESSAGE_GLOBAL_DRIFT
            noun = "Plugin" if self._count == 1 else "Plugins"
        elif self._kind is BannerKind.SAVED_AWAITING_RESTART:
            template = MESSAGE_SAVED_AWAITING_RESTART
            # Capitalised to match the blue PENDING_CHANGES banner's
            # ``Change`` / ``Changes`` vocabulary - keeps the two
            # variants visually parallel beyond just the leading count.
            noun = "Change" if self._count == 1 else "Changes"
        else:  # PANIC_ENGAGED
            template = MESSAGE_PANIC_ENGAGED
            # Append the Global sentence only when a Global Loadout is
            # actually configured - keeps the base message true for the
            # no-Global majority and answers "are my Globals skipped too?"
            # for the Global crowd (they aren't).
            if self._globals_present:
                template = template + MESSAGE_PANIC_ENGAGED_GLOBAL_SUFFIX
            # Panic message is count-independent - the noun + count
            # slots are unused in the template, but ``format`` still
            # tolerates extras.
            noun = ""
        # Banner copy uses rich text (<b> around the count). Force
        # RichText format so the tag renders instead of being shown as
        # literal characters in headless / Fusion rendering paths.
        self._label.setTextFormat(compat.QtCore.Qt.RichText)
        try:
            text = template.format(n=self._count, noun=noun)
        except (KeyError, IndexError):
            # Defensive - a template without ``{n}``/``{noun}`` (e.g.
            # PANIC_ENGAGED) should still render. ``.format`` on a
            # static string is a no-op so this branch only fires if a
            # future template introduces a custom placeholder.
            text = template
        self._label.setText(text)
        # No autoFillBackground - paintEvent draws the alpha fill itself.
        self.update()

    def paintEvent(self, event):
        """Draw the semi-transparent body fill behind the message."""
        # Per-kind colour lookup. Centralised in a tiny dict so adding
        # a new ``BannerKind`` requires touching only one place.
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
        # Snap out - no animation. Hide first, emit signal second, so a
        # listener that re-queries visibility sees the dismissed state.
        self.hide()
        self.dismissed.emit()

