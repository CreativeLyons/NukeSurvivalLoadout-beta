"""Plugin Pill widget - the central visual unit of the Loadout Panel.

A ``QWidget`` that paints one Plugin pill and emits signals on user input.
Callers pass a ``PillState`` to ``set_state()``. The widget never reads
Loadout or scan state.

Signal layering:

    Border  : divergence + pending-restart / save state (barber-pole / glow)
    Body    : truth-vs-intent tint   (neutral / green / red / yellow)
    Status  : current-session load truth (read-only icon)
    Buttons : Status / GUI-only / Menu / Info chips along the bottom row

Hard rules:
    * Qt imports go through ``nsl.compat`` exclusively.
    * No ``import nuke``.
    * Never raise from a paint path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional

from nsl import compat
from nsl import log
from nsl.ui import _theme

QtCore = compat.QtCore
QtWidgets = compat.QtWidgets
QtGui = compat.QtGui

Qt = QtCore.Qt
Signal = QtCore.Signal


# ---------------------------------------------------------------------------
# State enums. The values are state vocabulary, not display text.
# ---------------------------------------------------------------------------


class Source(str, Enum):
    """Where the Plugin came from.

    Both sources paint the same engraved-dark border. The only provenance
    signal left in the chrome is the GUI-only chip, which Global pills
    cannot toggle.
    """

    USER_ADDED = "user_added"
    GLOBAL = "global_base"


class StatusIcon(str, Enum):
    """Current-session Load Status icon vocabulary."""

    #   * EMPTY   - no icon. The Plugin is disabled.
    #   * PENDING - grey spinner.
    #   * LOADED  - green check.
    #   * FAILED  - red triangle. Opens the diagnostic.
    #   * MISSING - yellow question mark. Opens the diagnostic.
    EMPTY = "empty"
    PENDING = "pending"
    LOADED = "loaded"
    FAILED = "failed"
    MISSING = "missing"


class Tint(str, Enum):
    """Pill body tint."""

    #   * GREEN  - enabled next restart, not loaded this session.
    #   * RED    - disabled next restart, was loaded this session.
    #   * YELLOW - a problem nobody has dealt with (failed or missing).
    NEUTRAL = "neutral"
    GREEN = "green"
    RED = "red"
    YELLOW = "yellow"


# ---------------------------------------------------------------------------
# Tooltips (locked wording - must not drift)
# ---------------------------------------------------------------------------


TOOLTIP_GUI_ONLY_GLOBAL = "GUI-only is set by the Global Loadout for this Plugin."
TOOLTIP_GUI_ONLY_USER_ON = (
    "GUI-only: loads only in GUI Nuke, skipped on the render farm."
)
TOOLTIP_GUI_ONLY_USER_OFF = "GUI-only: off, loads everywhere."


# ---------------------------------------------------------------------------
# Tag stub - v2 tags carry name + colour. v1 never populates the row.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TagStub:
    """Placeholder for v2 Plugin Tags. v1 never produces these."""

    name: str
    colour_rgb: tuple  # (r, g, b) ints 0-255


# ---------------------------------------------------------------------------
# PillState - the full set of inputs the renderer consumes.
# ---------------------------------------------------------------------------


@dataclass
class PillState:
    """Everything the pill needs to render.

    Keep it a plain dataclass. The domain layer builds these, and the
    widget never reads Loadout or scan state.
    """

    plugin_name: str = "Plugin"
    source: Source = Source.USER_ADDED
    enabled: bool = True                # pressed (enabled) vs unpressed (disabled)
    status_icon: StatusIcon = StatusIcon.LOADED
    tint: Tint = Tint.NEUTRAL
    selected: bool = False
    diverges_from_global: bool = False  # only meaningful for GLOBAL pills
    gui_only: bool = False
    # Human-readable failure category. NSL does not capture per-plugin
    # load failures, so production callers always leave this None.
    failure_label: Optional[str] = None
    has_diagnostic: bool = False        # diag button becomes clickable
    # True when this pill differs from the active Loadout saved on disk.
    # Draws the white border. A Save flips it to False, and the border
    # becomes the lime or red colour for a pending-restart diff.
    is_dirty_vs_saved: bool = False
    # The Plugin loaded this Nuke session, but its folder is gone from
    # ``user_plugins_dirs`` and from Global. Painted as a yellow hazard
    # body with a green check and a red border glow.
    source_missing: bool = False
    # True while the side panel Info tab holds this Plugin's README. Lights
    # the info chip, so the user sees which pill the panel content belongs
    # to. Pushed as a pair with ``menu_active``, so only one chip lights.
    info_active: bool = False
    # DORMANT. Was the Log chip highlight, now replaced by ``menu_active``.
    # Always False, and kept so the dormant diag paths still work.
    log_active: bool = False
    # Sibling of ``info_active`` for the menu chip and the Menu tab.
    menu_active: bool = False
    # Panic Mode is on. Drops the lime or red saved-glow on USER_ADDED
    # pills, because they will not load on restart whatever their state.
    # GLOBAL pills keep the glow, and source-missing pills keep the red
    # one, because both signals stay true in panic.
    panic_engaged: bool = False
    # GUI differs OFF->ON against the session load. The pending signal is
    # the lit-purple chip. The committed signal is the cell wash, gated on
    # ``gui_committed``.
    gui_pending_on: bool = False
    # GUI differs ON->OFF against the session load. The pending signal is
    # red chip text. The committed signal is the red GUI-chip border, gated
    # on ``gui_committed``.
    gui_pending_off: bool = False
    # The GUI change above is saved on a slot that can save. Gates the
    # purple cell wash and the red GUI border. Custom, Global and unsaved
    # edits never light them.
    gui_committed: bool = False
    tags: List[TagStub] = field(default_factory=list)  # v2 stub - empty in v1

    def effective_tint(self) -> Tint:
        """Return ``tint`` unchanged.

        The caller resolves tint precedence before it builds the state.
        This is the seam if the widget ever has to resolve it instead.
        """
        return self.tint


# ---------------------------------------------------------------------------
# Palette - single source of truth for pill colours.
# ---------------------------------------------------------------------------


def _qcolor(r: int, g: int, b: int, a: int = 255):
    return QtGui.QColor(r, g, b, a)


class Palette:
    """Pill colour vocabulary, taken from the canonical design system.

    Source of truth: ``Knowledge/docs/design/NSL_Design_System_New/preview/``
        - ``_pill.css`` for the chrome (border / body / chip rules)
        - ``pill-anatomy.html`` for the legend
        - ``pill-lab.html`` for the 12 canonical scenarios
    """

    # ------------------------------------------------------------------
    # Borders
    # ------------------------------------------------------------------
    BORDER_DEFAULT = _qcolor(26, 26, 26)
    BORDER_USER_ADDED = BORDER_DEFAULT
    BORDER_GLOBAL = BORDER_DEFAULT
    # Thin white border over the default one on a pressed pill. Alpha 215,
    # not the canonical 55 %, which reads faint at panel scale.
    BORDER_LOADED_GLOW = _qcolor(255, 255, 255, 215)
    # Barber-pole stripe over the border path. The dark stripe is lifted
    # from the canonical #2a2a2a to #4a4a4a. At #2a2a2a the dark band
    # reads as a break in the ring against the #393939 panel.
    DIVERGENT_STRIPE_LIGHT = _qcolor(122, 122, 122)
    DIVERGENT_STRIPE_DARK = _qcolor(74, 74, 74)
    # A 2 px orange ring replaces the border when ``selected`` is True.
    BORDER_SELECTION = _qcolor(*_theme.NUKE_ORANGE_RGB)

    # ------------------------------------------------------------------
    # Pending-change borders, layered over the default border:
    #   * DIRTY (white)  - edit not saved yet. Lost on Discard.
    #   * ENABLE (lime)  - saved. The Plugin loads on next restart.
    #   * DISABLE (red)  - saved. The Plugin unloads on next restart.
    BORDER_PENDING_DIRTY = _qcolor(255, 255, 255, 240)
    BORDER_PENDING_ENABLE = _qcolor(170, 255, 80, 245)
    BORDER_PENDING_DISABLE = _qcolor(255, 60, 60, 245)
    # Peak alpha of the pending outer glow. Keeps the solid border
    # readable while the halo bleeds outward.
    PENDING_HALO_PEAK_ALPHA = 110

    # ------------------------------------------------------------------
    # Body fills. The pressed body is darker than the canonical #2f2c2c,
    # which barely separated from the #2d2d2d grid background.
    # ------------------------------------------------------------------
    BODY_NEUTRAL_PRESSED = _qcolor(34, 32, 32)
    BODY_NEUTRAL_UNPRESSED = _qcolor(76, 76, 76)
    # Body tints sit 75 % of the way from the neutral body to the
    # canonical tint. That keeps the pending signals subtle.
    BODY_TINT_GREEN_PRESSED = _qcolor(66, 83, 71)
    BODY_TINT_GREEN_UNPRESSED = _qcolor(97, 112, 102)
    BODY_TINT_RED_PRESSED = _qcolor(94, 61, 61)
    BODY_TINT_RED_UNPRESSED = _qcolor(118, 85, 85)
    # Stripe colours for the yellow hazard zebra painted in
    # ``_paint_body_fill``. Brightened above the canonical #3e3920 and
    # #322e1c, which did not read as a warning on the dark theme.
    BODY_HAZARD_STRIPE_A_PRESSED = _qcolor(70, 64, 36)
    BODY_HAZARD_STRIPE_B_PRESSED = _qcolor(56, 52, 31)
    # Unpressed variant, lighter to match the raised highlight.
    BODY_HAZARD_STRIPE_A_UNPRESSED = _qcolor(88, 81, 45)
    BODY_HAZARD_STRIPE_B_UNPRESSED = _qcolor(74, 67, 38)
    # Legacy aliases. No caller in the tree uses these now.
    BODY_TINT_GREEN = BODY_TINT_GREEN_PRESSED
    BODY_TINT_RED = BODY_TINT_RED_PRESSED
    BODY_TINT_YELLOW = BODY_HAZARD_STRIPE_A_PRESSED

    # ------------------------------------------------------------------
    # Text colours. The Plugin name picks up a hint of the body tint.
    # ------------------------------------------------------------------
    TEXT_PRIMARY = _qcolor(255, 255, 255)
    TEXT_PRIMARY_GREEN = _qcolor(212, 232, 208)
    TEXT_PRIMARY_RED = _qcolor(240, 212, 212)
    TEXT_PRIMARY_YELLOW = _qcolor(240, 227, 184)
    TEXT_DIM = _qcolor(140, 140, 140)

    # ------------------------------------------------------------------
    # Status icon glyph colours, used by ``_paint_status_icon``.
    # ------------------------------------------------------------------
    STATUS_LOADED_GREEN = _qcolor(168, 232, 168)
    STATUS_FAILED_RED = _qcolor(207, 142, 142)
    STATUS_FAILED_STROKE = _qcolor(122, 48, 48)
    STATUS_MISSING_YELLOW = _qcolor(255, 230, 128)
    STATUS_OFF_RED = _qcolor(244, 176, 176)
    # White, so the spinner reads against the muted green chip fill.
    STATUS_PENDING_GLYPH = _qcolor(255, 255, 255)

    # ------------------------------------------------------------------
    # Bottom-row chip tints. Each chip has its own fill and text colour.
    # ------------------------------------------------------------------
    CHIP_STATUS_LOADED_FILL = _qcolor(61, 107, 61)
    CHIP_STATUS_LOADED_TEXT = STATUS_LOADED_GREEN
    CHIP_STATUS_OFF_FILL = _qcolor(107, 58, 58)
    CHIP_STATUS_OFF_TEXT = STATUS_OFF_RED
    CHIP_STATUS_FAILED_FILL = _qcolor(122, 48, 48)
    CHIP_STATUS_FAILED_TEXT = STATUS_OFF_RED
    CHIP_STATUS_MISSING_FILL = _qcolor(138, 117, 48)
    CHIP_STATUS_MISSING_TEXT = STATUS_MISSING_YELLOW
    # Darker and less saturated than the pill body's pending-add green,
    # so the chip lane sits behind the body. The white spinner glyph
    # carries the contrast.
    CHIP_STATUS_PENDING_FILL = _qcolor(60, 72, 64)
    CHIP_STATUS_PENDING_TEXT = STATUS_PENDING_GLYPH

    # Log chip:
    #   * No diagnostic  - grey. Reads as a placeholder.
    #   * A diagnostic   - yellow fill and white text. The white text is
    #                      the click hint.
    # The hover lighten only fires when the chip is on.
    CHIP_LOG_OFF_FILL = _qcolor(58, 58, 58)
    CHIP_LOG_OFF_TEXT = _qcolor(110, 110, 110)
    CHIP_LOG_ON_FILL = _qcolor(138, 117, 48)
    CHIP_LOG_ON_TEXT = _qcolor(255, 255, 255)

    # Menu chip. It matches the info chip, because both open a side-panel
    # tab. The values are copied and not aliased, because CHIP_INFO_* is
    # defined further down. Keep the two pairs in sync.
    CHIP_MENU_FILL = _qcolor(90, 87, 80)
    CHIP_MENU_TEXT = _qcolor(216, 210, 192)

    # GUI-only chip - off, and user-on in lit purple.
    CHIP_GUI_OFF_FILL = _qcolor(58, 58, 58)
    CHIP_GUI_OFF_TEXT = _qcolor(110, 110, 110)
    CHIP_GUI_ON_FILL = _qcolor(90, 79, 114)
    CHIP_GUI_ON_TEXT = _qcolor(212, 176, 255)
    # Nothing paints these two. ``_chip_colours_gui`` gives Global pills
    # the same off and on colours as a user pill.
    CHIP_GUI_GLOBAL_DIM_FILL = _qcolor(58, 58, 58)
    CHIP_GUI_GLOBAL_DIM_TEXT = _qcolor(90, 90, 90)

    CHIP_INFO_FILL = _qcolor(90, 87, 80)
    CHIP_INFO_TEXT = _qcolor(216, 210, 192)

    # Paired dark and light inset lines, for the engraved look between
    # chips.
    CHIP_DIVIDER_DARK = _qcolor(0, 0, 0, 140)
    CHIP_DIVIDER_LIGHT = _qcolor(255, 255, 255, 13)
    # Top edge of the chip row, to separate it from the title area.
    CHIP_ROW_TOP_DARK = _qcolor(0, 0, 0, 153)

    # Legacy aliases. No caller in the tree reads these now. New code
    # uses the specific names above.
    BUTTON_FRAME = _qcolor(90, 90, 95)
    BUTTON_FRAME_DISABLED = _qcolor(70, 70, 72)
    GUI_ONLY_ON = CHIP_GUI_ON_FILL
    GUI_ONLY_OFF = CHIP_GUI_OFF_FILL
    GUI_ONLY_GLOBAL_DIM = CHIP_GUI_GLOBAL_DIM_FILL
    CHIP_GREY_FILL = CHIP_LOG_OFF_FILL
    CHIP_GREY_TEXT = CHIP_LOG_OFF_TEXT
    CHIP_INACTIVE_TEXT = _qcolor(75, 75, 80)
    CHIP_GREEN_FILL = CHIP_STATUS_LOADED_FILL
    CHIP_GREEN_TEXT = CHIP_STATUS_LOADED_TEXT
    CHIP_RED_FILL = CHIP_STATUS_OFF_FILL
    CHIP_RED_TEXT = CHIP_STATUS_OFF_TEXT
    CHIP_ORANGE_FILL = CHIP_LOG_ON_FILL
    CHIP_ORANGE_TEXT = CHIP_LOG_ON_TEXT
    CHIP_YELLOW_FILL = CHIP_STATUS_MISSING_FILL
    CHIP_YELLOW_TEXT = CHIP_STATUS_MISSING_TEXT
    CHIP_PURPLE_FILL = CHIP_GUI_ON_FILL
    CHIP_PURPLE_TEXT = CHIP_GUI_ON_TEXT


# ---------------------------------------------------------------------------
# Geometry - pill layout zones, computed from the current widget rect so
# resize is automatic.
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    """Read an int env var. Falls back to *default* on a miss or a bad value."""
    import os
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default

# Compact 211 x 70 body, scaled down from the canonical 280 x 96. Each
# knob below reads an env var, so the size-compare script needs no code
# edit. The 15 px shadow margin opens a ~30 px gap between pills, and
# the body itself stays 211 x 70.
_MIN_W = 211 + 2 * 15
_MIN_H = 70 + 2 * 15
_BORDER_RADIUS = _env_int("NSL_PILL_RADIUS", 16)
_BORDER_WIDTH = 1
_SHADOW_MARGIN = 15
# Bottom-row chips span the full pill width and touch each other. The
# 19 px row clears the ascent and descent of the label font, so the
# labels do not clip.
_BOTTOM_ROW_H = _env_int("NSL_PILL_CHIP_H", 19)
_BOTTOM_ROW_DIVIDER = 1      # paired 1 px black + 2 px white inset highlight
_BUTTON_MARGIN = 11

# Alpha of the white wash painted over the body on hover. Kept low, so
# the body does not start to look pressed.
_BODY_HOVER_ALPHA = 9

# Chip glyph and label sizes. The glyphs stay in pixels, because they
# scale with the chip row and not with the body font. The labels and the
# name use point sizes, so they follow the panel-wide 10 pt font.
_CHIP_GLYPH_PX = _env_int("NSL_PILL_GLYPH_PX", 13)
_CHIP_GLYPH_SMALL_PX = _env_int("NSL_PILL_GLYPH_SMALL_PX", 16)
_CHIP_LABEL_PT = _env_int("NSL_PILL_CHIP_PT", 10)
# Letter spacing in device px for the multi-char chip labels. Without it
# the capital I in INFO reads like a lowercase l.
_CHIP_LABEL_TRACKING_PX = 1.0
# 10 pt matches the panel-wide control font. An explicit size keeps the
# pill the same across host font settings.
_NAME_PT = _env_int("NSL_PILL_NAME_PT", 10)
_TAG_ROW_HEIGHT = 6
# Left-to-right chip order. The old "diag" chip is absent, so
# ``_zone_at`` never returns it and its code below is unreachable.
_BOTTOM_ROW_ORDER = ("status", "gui", "menu", "info")
# Outer ring radius and alpha for the loaded-glow halo.
_LOADED_GLOW_OUTER_PX = 6
_LOADED_GLOW_PEAK_ALPHA = 56
_LOADED_GLOW_INNER_ALPHA = 71    # tighter inner ring


# ---------------------------------------------------------------------------
# PluginPill - the widget
# ---------------------------------------------------------------------------


class PluginPill(QtWidgets.QWidget):
    """Custom QWidget rendering a single Plugin pill.

    Signals out, which the wiring layer routes to ``loadout_ops``:

        toggled(bool)          - body click. The new enabled state.
        info_clicked()         - info chip.
        menu_clicked()         - menu chip. Opens menu.py in the side panel.
        diagnostic_clicked()   - DORMANT. Never emitted now.
        gui_only_toggled(bool) - user-added Plugins only. Global never emits.

    ``set_state()`` replaces the whole state snapshot in one go. Nothing
    here reads Loadout or scan state.
    """

    toggled = Signal(bool)
    info_clicked = Signal()
    menu_clicked = Signal()
    diagnostic_clicked = Signal()  # kept so old wiring and tests still import it
    gui_only_toggled = Signal(bool)
    #: Emitted on right-click, "Open Plugin Folder". The pill does not know
    #: its own path, so the wiring layer resolves the folder.
    open_folder_requested = Signal()
    #: Emitted on a body click with Shift, Ctrl or Cmd held. That press is a
    #: selection gesture, not an enable toggle. The payload is the
    #: ``Qt.KeyboardModifiers``, so the grid can tell shift from ctrl.
    selection_requested = Signal(object)

    def __init__(self, state: Optional[PillState] = None, parent=None):
        super().__init__(parent)
        self._state: PillState = state if state is not None else PillState()
        # Design iteration knob. No paint path reads it, so setting it has
        # no effect today.
        self._border_style: str = "glow"
        # One of the ``_BOTTOM_ROW_ORDER`` names, or "body", or "outside".
        self._hover_zone: str = "outside"
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._apply_tooltips()

    def set_border_style(self, style: str) -> None:
        """Iteration knob - switch the pressed pill border treatment."""
        self._border_style = style
        self.update()

    # -- state ---------------------------------------------------------

    def state(self) -> PillState:
        return self._state

    def set_state(self, state: PillState) -> None:
        self._state = state
        self._apply_tooltips()
        self.update()

    def update_state(self, **kwargs) -> None:
        """Replace some fields without rebuilding the dataclass."""
        self._state = replace(self._state, **kwargs)
        self._apply_tooltips()
        self.update()

    def _apply_tooltips(self) -> None:
        try:
            self.setToolTip(self._state.plugin_name)
        except Exception as exc:  # never raise during state apply
            log.warning(f"pill setToolTip failed: {exc!r}")

    # -- hit-test geometry helpers ------------------------------------

    def _button_rect(self, name: str) -> "QtCore.QRect":
        """Rect for one of the four bottom-row chips.

        The chips split the pill width into four segments with 1 px
        dividers. The left segment takes the leftover pixel, so the
        rounded outer corners line up.
        """
        if name not in _BOTTOM_ROW_ORDER:
            return QtCore.QRect()
        idx = _BOTTOM_ROW_ORDER.index(name)
        body = self._body_rect()
        w = body.width()
        base = w // 4
        extra = w - base * 4
        widths = [base + (1 if i < extra else 0) for i in range(4)]
        x = body.left() + sum(widths[:idx])
        y = body.bottom() + 1 - _BOTTOM_ROW_H
        return QtCore.QRect(x, y, widths[idx], _BOTTOM_ROW_H)

    def _zone_at(self, pos) -> str:
        """Classify a click position into one of:
        status / gui / menu / info / body / outside."""
        # A click in the shadow margin counts as outside the pill, so the
        # zones match what the user sees.
        if not self._body_rect().contains(pos):
            return "outside"
        for name in _BOTTOM_ROW_ORDER:
            if self._button_rect(name).contains(pos):
                return name
        return "body"

    # -- mouse handling -----------------------------------------------

    def mouseMoveEvent(self, event):
        """Track the hover zone and light the chip under the cursor.

        The cursor becomes a pointing hand over any actionable zone,
        including the pill body. See :meth:`_zone_is_actionable`.
        """
        zone = self._zone_at(event.pos())
        if zone != self._hover_zone:
            self._hover_zone = zone
            actionable = self._zone_is_actionable(zone)
            self.setCursor(
                QtCore.Qt.PointingHandCursor
                if actionable
                else QtCore.Qt.ArrowCursor
            )
            self.update()
        return super().mouseMoveEvent(event)

    def _zone_is_actionable(self, zone: str) -> bool:
        """True when *zone* responds to a click.

        Drives the hover lighten and the cursor. A source-missing pill,
        and panic on a USER_ADDED pill, lock the body and the GUI chip.
        The zones that only inspect stay open.
        """
        st = self._state
        panic_user = st.panic_engaged and st.source is Source.USER_ADDED
        if zone == "body":
            if st.source_missing:
                return False
            if panic_user:
                return False
            return True
        if zone == "menu":
            return True
        if zone == "diag":
            # DORMANT. ``_zone_at`` never returns "diag".
            return bool(st.has_diagnostic)
        if zone == "gui":
            if st.source_missing:
                return False
            if panic_user:
                return False
            # Global pills cannot toggle GUI-only. Without this the
            # generic ``_HOVER_CHIPS`` check below lights the chip anyway.
            if st.source is Source.GLOBAL:
                return False
        return zone in self._HOVER_CHIPS

    def leaveEvent(self, event):
        if self._hover_zone != "outside":
            self._hover_zone = "outside"
            self.unsetCursor()
            self.update()
        return super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        zone = self._zone_at(event.pos())
        st = self._state

        # A click with a modifier held is always a selection gesture, in
        # any zone. Chip actions need a plain click.
        modifiers = event.modifiers()
        if zone != "outside" and modifiers & (
            Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier
        ):
            self.selection_requested.emit(modifiers)
            return

        if zone == "info":
            self.info_clicked.emit()
            return

        if zone == "menu":
            # Always clickable. The side panel handles a missing menu.py.
            self.menu_clicked.emit()
            return

        if zone == "diag":
            # DORMANT. ``_zone_at`` never returns "diag".
            if st.has_diagnostic:
                self.diagnostic_clicked.emit()
            return

        if zone == "gui":
            # The Global Loadout owns GUI-only for Global Plugins, so the
            # click is blocked. The tooltip already says it is read-only.
            if st.source is Source.GLOBAL:
                return
            # The source folder is gone, so ``gui_only`` has no effect.
            if st.source_missing:
                return
            # The Plugin will not load on restart, so ``gui_only`` has no
            # effect.
            if st.panic_engaged:
                return
            new_value = not st.gui_only
            self.update_state(gui_only=new_value)
            self.gui_only_toggled.emit(new_value)
            return

        if zone == "body":
            # The source folder is gone, so ``enabled`` has no effect.
            if st.source_missing:
                return
            # Panic drops user plugins on restart. GLOBAL pills stay
            # interactive, because Globals still load.
            if st.panic_engaged and st.source is Source.USER_ADDED:
                return
            new_enabled = not st.enabled
            self.update_state(enabled=new_enabled)
            self.toggled.emit(new_enabled)
            return

        # "status" and "outside" do nothing here. ``ignore()`` passes the
        # press on, so a click in the shadow margin can start a marquee in
        # the grid.
        event.ignore()

    def contextMenuEvent(self, event):
        """Right-click menu with one action, reveal the Plugin folder.

        The action only emits ``open_folder_requested``, because the pill
        does not know its own path. The try/except keeps a menu failure
        out of the grid's event path.
        """
        try:
            menu = QtWidgets.QMenu(self)
            action = menu.addAction("Open Plugin Folder")
            action.triggered.connect(
                lambda *_: self.open_folder_requested.emit()
            )
            compat.run_modal(menu, event.globalPos())
        except Exception as exc:
            log.warning(f"pill context menu failed: {exc!r}")

    # -- painting ------------------------------------------------------

    def paintEvent(self, event):
        # NEVER raise from paint. Hard contract.
        try:
            self._paint(event)
        except Exception as exc:
            try:
                log.warning(f"PluginPill paint failed: {exc!r}")
            except Exception:
                pass

    def _paint(self, event) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            # The paint order matters:
            #   1. Drop shadow, for unpressed pills only. A pressed pill
            #      gets the loaded glow from ``_paint_border`` instead.
            #   2. Body fill and the pressed or raised gradients.
            #   3. Chip fills and dividers, clipped inside the border.
            #   4. Plugin name and tag indicators.
            #   5. Border and selection outline, always last.
            if not self._state.enabled:
                self._paint_drop_shadow(painter)
            self._paint_body_fill(painter)
            self._paint_body_hover_overlay(painter)
            self._paint_bottom_row(painter)
            self._paint_plugin_name(painter)
            self._paint_tag_indicators(painter)
            self._paint_border(painter)
        finally:
            painter.end()


    # -- paint helpers ------------------------------------------------

    def _is_loaded(self) -> bool:
        """True when the pill is pressed (``enabled``).

        A pill is either pressed with a glow, or unpressed with a drop
        shadow. There is no third state, whatever the status chip shows.
        """
        return self._state.enabled

    def _border_colour(self):
        """Default border colour. The same for both sources."""
        return Palette.BORDER_DEFAULT

    def _body_colour(self):
        """Solid body colour. Yellow returns stripe A, and the zebra is
        painted over it in ``_paint_body_fill``.
        """
        st = self._state
        tint = st.effective_tint()
        pressed = st.enabled
        if tint is Tint.GREEN:
            return (
                Palette.BODY_TINT_GREEN_PRESSED
                if pressed
                else Palette.BODY_TINT_GREEN_UNPRESSED
            )
        if tint is Tint.RED:
            return (
                Palette.BODY_TINT_RED_PRESSED
                if pressed
                else Palette.BODY_TINT_RED_UNPRESSED
            )
        if tint is Tint.YELLOW:
            return (
                Palette.BODY_HAZARD_STRIPE_A_PRESSED
                if pressed
                else Palette.BODY_HAZARD_STRIPE_A_UNPRESSED
            )
        # NEUTRAL. Pressed is dark, unpressed is the lighter raised body.
        return (
            Palette.BODY_NEUTRAL_PRESSED
            if pressed
            else Palette.BODY_NEUTRAL_UNPRESSED
        )

    def _body_rect(self) -> "QtCore.QRect":
        """Visible pill body rect, inset by ``_SHADOW_MARGIN`` so the drop
        shadow has room to paint without being clipped."""
        return self.rect().adjusted(
            _SHADOW_MARGIN, _SHADOW_MARGIN, -_SHADOW_MARGIN, -_SHADOW_MARGIN
        )

    def _body_rect_f(self) -> "QtCore.QRectF":
        """Pill body rect (float) - centred on the border path."""
        return QtCore.QRectF(self._body_rect()).adjusted(
            _BORDER_WIDTH / 2.0,
            _BORDER_WIDTH / 2.0,
            -_BORDER_WIDTH / 2.0,
            -_BORDER_WIDTH / 2.0,
        )

    def _inner_clip_path(self) -> "QtGui.QPainterPath":
        """Path inset fully inside the border line."""
        inner = QtCore.QRectF(self._body_rect()).adjusted(
            _BORDER_WIDTH, _BORDER_WIDTH, -_BORDER_WIDTH, -_BORDER_WIDTH,
        )
        radius = max(_BORDER_RADIUS - _BORDER_WIDTH, 0)
        path = QtGui.QPainterPath()
        path.addRoundedRect(inner, radius, radius)
        return path

    def _paint_drop_shadow(self, painter) -> None:
        """Soft drop shadow below and right of the body.

        Painted for unpressed pills only. Concentric rounded rects, with
        the growth on the top and left cancelled by an equal offset. The
        inner rings carry more alpha than the outer ones.
        """
        body = self._body_rect()
        offset_x = 2
        offset_y = 4
        layers = 12
        max_grow = 6
        for i in range(layers, 0, -1):
            grow = i * (max_grow / layers)
            left_pad = max(grow - offset_x, 0)
            top_pad = max(grow - offset_y, 0)
            r = QtCore.QRectF(body).adjusted(
                -left_pad, -top_pad, grow + offset_x, grow + offset_y
            )
            # Quadratic falloff, peaking at the innermost ring.
            t = i / layers
            alpha = int(20 * (1.0 - t) ** 2 + 6 * t)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                r, _BORDER_RADIUS + grow, _BORDER_RADIUS + grow
            )

    def _paint_hazard_zebra(
        self, painter, rect, *, pressed: bool, opacity: float
    ) -> None:
        """Paint the hazard zebra over the pill body.

        Stripe A is already painted as the base fill, so this paints
        stripe B on every other 12 px band in a 24 px period. An
        ``opacity`` below 1.0 gives the faint trace on a disabled pill.
        """
        stripe_b = (
            Palette.BODY_HAZARD_STRIPE_B_PRESSED
            if pressed
            else Palette.BODY_HAZARD_STRIPE_B_UNPRESSED
        )
        painter.save()
        painter.setClipPath(self._inner_clip_path())
        painter.setOpacity(opacity)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QBrush(stripe_b))
        painter.translate(rect.center())
        # Forward-leaning stripes, bottom-left to top-right. Qt rotates
        # clockwise in screen space, so +45 tilts vertical bands into "/".
        painter.rotate(45)
        stripe_w = 12
        period = 24
        half_diag = int(
            ((rect.width()) ** 2 + (rect.height()) ** 2) ** 0.5 / 2 + period
        )
        x = -half_diag + stripe_w
        while x < half_diag:
            painter.fillRect(
                QtCore.QRectF(x, -half_diag, stripe_w, 2 * half_diag),
                stripe_b,
            )
            x += period
        painter.restore()

    def _paint_body_fill(self, painter) -> None:
        """Body fill. Neutral, green and red paint as a solid. Yellow
        paints as the hazard zebra.
        """
        st = self._state
        rect = self._body_rect_f()
        pressed = st.enabled

        # For yellow this is stripe A, under the zebra.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QBrush(self._body_colour()))
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        if st.effective_tint() is Tint.YELLOW:
            self._paint_hazard_zebra(painter, rect, pressed=pressed, opacity=1.0)
        # A faint zebra on a disabled pill that failed to load this
        # session. ``has_diagnostic`` is never set in production, so this
        # branch is dead there.
        elif not st.enabled and st.has_diagnostic:
            self._paint_hazard_zebra(painter, rect, pressed=False, opacity=0.4)

        painter.save()
        painter.setClipPath(self._inner_clip_path())
        painter.setPen(Qt.NoPen)
        if st.enabled:
            # A depression lit from the top left. The canonical CSS has
            # the top inset shadow only. The left one is added here,
            # because top-only reads as a flat band at panel scale.

            # 1. Ambient inner glow. A subtle lift on all edges, not a
            # depression cue.
            glow_pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 22))
            glow_pen.setWidth(6)
            glow_pen.setCosmetic(True)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(2, 2, -2, -2),
                _BORDER_RADIUS - 2,
                _BORDER_RADIUS - 2,
            )
            painter.setPen(Qt.NoPen)

            # 2. Top inset shadow.
            grad_t = QtGui.QLinearGradient(
                0, rect.top(), 0, rect.top() + 14
            )
            grad_t.setColorAt(0.0, QtGui.QColor(0, 0, 0, 130))
            grad_t.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            painter.setBrush(QtGui.QBrush(grad_t))
            painter.drawRect(
                QtCore.QRectF(rect.left(), rect.top(), rect.width(), 14)
            )

            # 3. Left inset shadow, close to the top one in strength.
            grad_l = QtGui.QLinearGradient(
                rect.left(), 0, rect.left() + 12, 0
            )
            grad_l.setColorAt(0.0, QtGui.QColor(0, 0, 0, 110))
            grad_l.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            painter.setBrush(QtGui.QBrush(grad_l))
            painter.drawRect(
                QtCore.QRectF(rect.left(), rect.top(), 12, rect.height())
            )

            # 4. Sharp 1 px top lip.
            painter.fillRect(
                QtCore.QRectF(rect.left(), rect.top(), rect.width(), 1),
                QtGui.QColor(0, 0, 0, 140),
            )
        else:
            # Raised recipe. Lit from the top left, mirroring the pressed
            # pill's shadow in the opposite colour.
            # 1. Top highlight.
            grad_hi = QtGui.QLinearGradient(0, rect.top(), 0, rect.top() + 10)
            grad_hi.setColorAt(0.0, QtGui.QColor(255, 255, 255, 50))
            grad_hi.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(grad_hi))
            painter.drawRect(QtCore.QRectF(
                rect.left(), rect.top(), rect.width(), 10
            ))
            # 2. Left highlight. Without it the raised pill lifts from
            #    the top only, which reads flat.
            grad_lh = QtGui.QLinearGradient(
                rect.left(), 0, rect.left() + 12, 0
            )
            grad_lh.setColorAt(0.0, QtGui.QColor(255, 255, 255, 55))
            grad_lh.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(grad_lh))
            painter.drawRect(QtCore.QRectF(
                rect.left(), rect.top(), 12, rect.height()
            ))
            # 3. Bottom shadow, on the edge above the chip row.
            row_top = self._body_rect().bottom() + 1 - _BOTTOM_ROW_H
            grad_lo = QtGui.QLinearGradient(0, row_top - 6, 0, row_top)
            grad_lo.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
            grad_lo.setColorAt(1.0, QtGui.QColor(0, 0, 0, 45))
            painter.setBrush(QtGui.QBrush(grad_lo))
            painter.drawRect(QtCore.QRectF(
                rect.left(), row_top - 6, rect.width(), 6
            ))
        painter.restore()

    def _paint_body_hover_overlay(self, painter) -> None:
        """White wash over the body while the cursor is on it.

        Only paints when the body is actionable. The chip row paints
        after this, so only the name zone lightens.
        """
        if self._hover_zone != "body":
            return
        if not self._zone_is_actionable("body"):
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QColor(255, 255, 255, _BODY_HOVER_ALPHA))
        painter.drawRoundedRect(
            self._body_rect_f(), _BORDER_RADIUS, _BORDER_RADIUS
        )
        painter.restore()

    def _paint_border(self, painter) -> None:
        """Border treatment, painted last so it sits on top.

        1. Pressed - the outer white glow halo, in two layers.
        2. Selected - a 2 px orange ring that replaces the border.
        3. Divergent - a barber-pole stripe along the border path.
        4. Otherwise - the default engraved-dark border.
        5. Pressed - a thin white ring over step 4.
        """
        st = self._state
        rect = self._body_rect_f()
        is_loaded = self._is_loaded()

        # 1. Outer halo for a pressed pill. Selection paints over it, so
        #    a selected pill keeps its pressed signal.
        if is_loaded:
            self._paint_loaded_glow(painter, rect)

        # 2. Selection - 2 px orange ring REPLACES the border.
        if st.selected:
            sel_pen = QtGui.QPen(Palette.BORDER_SELECTION)
            sel_pen.setWidth(2)
            sel_pen.setCosmetic(True)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                rect, _BORDER_RADIUS, _BORDER_RADIUS
            )
            return

        # 3. Divergent. The barber-pole replaces the solid border. If the
        # pill also has a pending-restart diff, the stripes take the
        # pending colour, so they carry both signals.
        if (
            st.source is Source.GLOBAL
            and st.diverges_from_global
        ):
            pending = self._pending_border_color()
            # Dirty and pressed paints white stripes. Grey stripes under
            # the white glow wash into a smear. Dirty and unpressed keeps
            # the grey pair.
            stripe_color = pending
            if pending is None and st.enabled:
                stripe_color = Palette.BORDER_PENDING_DIRTY
            # A dirty pill already has the white halo from step 1. Only
            # the committed colours paint a second one.
            if pending is not None:
                self._paint_pending_glow(painter, rect, pending)
            self._paint_divergent_border(painter, rect, light_color=stripe_color)
            return

        # 4. Default engraved-dark border.
        border_pen = QtGui.QPen(self._border_colour())
        border_pen.setWidth(_BORDER_WIDTH)
        border_pen.setCosmetic(True)
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        # 5. Loaded inner glow, over the default border. Paired with the
        #    outer halo from step 1.
        if is_loaded:
            glow_pen = QtGui.QPen(Palette.BORDER_LOADED_GLOW)
            glow_pen.setWidth(1)
            glow_pen.setCosmetic(True)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                rect, _BORDER_RADIUS, _BORDER_RADIUS
            )

        # 6. Pending-change border, last so it dominates. GREEN and RED
        #    tints only. YELLOW is the problem signal and is not
        #    overloaded with the saved or unsaved axis.
        pending_color = self._pending_border_color()
        if pending_color is not None:
            # Halo first, then the solid stroke, so the rim stays crisp.
            self._paint_pending_glow(painter, rect, pending_color)
            pending_pen = QtGui.QPen(pending_color)
            pending_pen.setWidth(2)
            pending_pen.setCosmetic(True)
            painter.setPen(pending_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                rect, _BORDER_RADIUS, _BORDER_RADIUS
            )

    def _paint_pending_glow(self, painter, rect, color) -> None:
        """Coloured outer halo for the pending-change border.

        Like :meth:`_paint_loaded_glow`, but tinted and with a smaller
        radius, so the white loaded glow stays dominant at the rim.
        """
        layers = 24
        prev_path = QtGui.QPainterPath()
        prev_path.addRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)
        peak_alpha = Palette.PENDING_HALO_PEAK_ALPHA
        outer_px = 8.0
        step = outer_px / layers
        base_r = color.red()
        base_g = color.green()
        base_b = color.blue()
        for i in range(1, layers + 1):
            grow = i * step
            outer_rect = rect.adjusted(-grow, -grow, grow, grow)
            outer_path = QtGui.QPainterPath()
            outer_path.addRoundedRect(
                outer_rect,
                _BORDER_RADIUS + grow,
                _BORDER_RADIUS + grow,
            )
            ring = outer_path.subtracted(prev_path)
            t = i / layers
            alpha = max(0, int(peak_alpha * (1.0 - t) ** 2))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(base_r, base_g, base_b, alpha))
            painter.drawPath(ring)
            prev_path = outer_path

    def _pending_border_color(self):
        """Pending-restart border colour for the current pill state.

        Returns ``None`` when no glow paints:

        * source_missing            -> red. The source folder is gone.
        * panic and USER_ADDED      -> None. Panic drops user plugins,
                                      so a saved glow would lie. GLOBAL
                                      pills keep theirs.
        * clean and GREEN           -> lime. Saved, loads on restart.
        * clean and RED             -> red. Saved, unloads on restart.
        * dirty, NEUTRAL or YELLOW  -> None.

        The glow only says "locked in by a Save". The body tint already
        says which way the change goes.
        """
        st = self._state
        if st.source_missing:
            return Palette.BORDER_PENDING_DISABLE
        if st.panic_engaged and st.source is Source.USER_ADDED:
            return None
        if st.tint not in (Tint.GREEN, Tint.RED):
            return None
        if st.is_dirty_vs_saved:
            return None
        if st.tint is Tint.GREEN:
            return Palette.BORDER_PENDING_ENABLE
        return Palette.BORDER_PENDING_DISABLE

    def _paint_loaded_glow(self, painter, rect) -> None:
        """Outer white halo for the loaded state.

        Concentric ring subtraction with falloff, because Qt has no
        box-shadow.
        """
        layers = 32
        prev_path = QtGui.QPainterPath()
        prev_path.addRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)
        peak_alpha = _LOADED_GLOW_PEAK_ALPHA
        outer_px = _LOADED_GLOW_OUTER_PX
        step = outer_px / layers
        for i in range(1, layers + 1):
            grow = i * step
            outer_rect = rect.adjusted(-grow, -grow, grow, grow)
            outer_path = QtGui.QPainterPath()
            outer_path.addRoundedRect(
                outer_rect,
                _BORDER_RADIUS + grow,
                _BORDER_RADIUS + grow,
            )
            ring = outer_path.subtracted(prev_path)
            # Quadratic falloff from the peak to 0 across the layers.
            t = i / layers
            alpha = max(0, int(peak_alpha * (1.0 - t) ** 2))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, alpha))
            painter.drawPath(ring)
            prev_path = outer_path

    # Wider than the default 1 px border. At 1 px the barber-pole
    # stripes disappear at panel scale.
    _DIVERGENT_BORDER_WIDTH = 2

    def _paint_divergent_border(
        self,
        painter,
        rect,
        *,
        light_color=None,
        dark_color=None,
    ) -> None:
        """Barber-pole stripe replacing the solid border.

        The stripes paint inside the border ring, which is
        ``_DIVERGENT_BORDER_WIDTH`` wide. ``light_color`` and
        ``dark_color`` override the grey pair, so a Global pill with a
        pending diff keeps the pattern and adds the pending colour.
        """
        painter.save()
        bw = self._DIVERGENT_BORDER_WIDTH
        outer = QtGui.QPainterPath()
        outer.addRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)
        inner_rect = rect.adjusted(bw, bw, -bw, -bw)
        inner = QtGui.QPainterPath()
        inner.addRoundedRect(
            inner_rect,
            max(_BORDER_RADIUS - bw, 0),
            max(_BORDER_RADIUS - bw, 0),
        )
        border_ring = outer.subtracted(inner)
        painter.setClipPath(border_ring)
        # Dark base fill, then the light stripes on top. Bands are 8 px
        # in a 16 px period. The canonical 5 / 10 reads as speckle at
        # panel scale.
        light = light_color if light_color is not None else Palette.DIVERGENT_STRIPE_LIGHT
        dark = dark_color if dark_color is not None else Palette.DIVERGENT_STRIPE_DARK
        painter.fillRect(rect, dark)
        painter.translate(rect.center())
        painter.rotate(-45)
        stripe_w = 8
        period = 16
        half_diag = int(
            ((rect.width()) ** 2 + (rect.height()) ** 2) ** 0.5 / 2 + period
        )
        x = -half_diag
        while x < half_diag:
            painter.fillRect(
                QtCore.QRectF(x, -half_diag, stripe_w, 2 * half_diag),
                light,
            )
            x += period
        painter.restore()

    # -- bottom-row chip palette --------------------------------------

    def _chip_colours_status(self):
        """(fill, text_colour, glyph_kind) for the status chip.

        ``glyph_kind`` is either a text glyph, or an ``svg:`` marker that
        :meth:`_paint_status_icon` draws as a QPainterPath.
        """
        st = self._state
        ic = st.status_icon
        if ic is StatusIcon.LOADED:
            return (
                Palette.CHIP_STATUS_LOADED_FILL,
                Palette.CHIP_STATUS_LOADED_TEXT,
                "✓",
            )
        if ic is StatusIcon.PENDING:
            return (
                Palette.CHIP_STATUS_PENDING_FILL,
                Palette.CHIP_STATUS_PENDING_TEXT,
                "svg:spinner",
            )
        if ic is StatusIcon.FAILED:
            return (
                Palette.CHIP_STATUS_FAILED_FILL,
                Palette.CHIP_STATUS_FAILED_TEXT,
                "svg:failed",
            )
        if ic is StatusIcon.MISSING:
            return (
                Palette.CHIP_STATUS_MISSING_FILL,
                Palette.CHIP_STATUS_MISSING_TEXT,
                "?",
            )
        # EMPTY. Disabled, never loaded.
        return (
            Palette.CHIP_STATUS_OFF_FILL,
            Palette.CHIP_STATUS_OFF_TEXT,
            "✕",
        )

    def _chip_colours_diag(self):
        """(fill, text, glyph) for the log chip. Empty or lit."""
        st = self._state
        if st.has_diagnostic:
            return (
                Palette.CHIP_LOG_ON_FILL,
                Palette.CHIP_LOG_ON_TEXT,
                "log",
            )
        return (
            Palette.CHIP_LOG_OFF_FILL,
            Palette.CHIP_LOG_OFF_TEXT,
            "log",
        )

    def _chip_colours_gui(self):
        """(fill, text, glyph) for the GUI-only chip.

        Two visual states only, off (grey) and on (lit purple). Global
        and source-missing pills are blocked in ``mousePressEvent`` but
        paint the same, so their lock is behavioural only.
        """
        st = self._state
        if st.gui_only:
            return (
                Palette.CHIP_GUI_ON_FILL,
                Palette.CHIP_GUI_ON_TEXT,
                "GUI",
            )
        if st.gui_pending_off:
            # Red text is the pending signal. The committed one is the
            # red border that ``_paint_bottom_row`` adds.
            return (
                Palette.CHIP_GUI_OFF_FILL,
                Palette.STATUS_FAILED_RED,
                "GUI",
            )
        return (
            Palette.CHIP_GUI_OFF_FILL,
            Palette.CHIP_GUI_OFF_TEXT,
            "GUI",
        )

    def _chip_colours_menu(self):
        """Menu chip. A constant, and never coded by state. The side
        panel handles a missing menu.py, so it is always actionable."""
        return (
            Palette.CHIP_MENU_FILL,
            Palette.CHIP_MENU_TEXT,
            "menu",
        )

    def _chip_colours_info(self):
        """Info chip. A constant, always actionable."""
        return (
            Palette.CHIP_INFO_FILL,
            Palette.CHIP_INFO_TEXT,
            "info",
        )

    @staticmethod
    def _lighten(colour: "QtGui.QColor", amount: int = 22) -> "QtGui.QColor":
        """Return *colour* with each RGB channel raised by *amount*.

        Clamped at 255, and alpha is kept. Used for the chip hover lift.
        """
        return QtGui.QColor(
            min(255, colour.red() + amount),
            min(255, colour.green() + amount),
            min(255, colour.blue() + amount),
            colour.alpha(),
        )

    # ``status`` is absent on purpose. It shows load truth and is not
    # clickable.
    _HOVER_CHIPS = frozenset({"menu", "gui", "info"})

    def _paint_bottom_row(self, painter) -> None:
        """Paint the full-width bottom-row chips.

        Each chip fill is clipped to the pill's rounded silhouette, so
        the bottom corners follow the curve. The dividers are engraved,
        a dark inset line paired with a light one.
        """
        chips = {
            "status": self._chip_colours_status(),
            "gui": self._chip_colours_gui(),
            "menu": self._chip_colours_menu(),
            "info": self._chip_colours_info(),
        }

        painter.save()
        painter.setClipPath(self._inner_clip_path())

        # 1. Fill each chip. An actionable chip lightens on hover. The
        # info and menu chips also stay lit while the side panel holds
        # this Plugin. Those two flags are mutually exclusive.
        for name in _BOTTOM_ROW_ORDER:
            rect = self._button_rect(name)
            fill, _text, _glyph = chips[name]
            hovered = (
                self._hover_zone == name
                and self._zone_is_actionable(name)
            )
            active_lit = (
                (name == "info" and self._state.info_active)
                or (name == "menu" and self._state.menu_active)
            )
            if hovered or active_lit:
                fill = self._lighten(fill)
            painter.fillRect(rect, fill)

        # 2. Engraved dividers, on the top edge and between the chips.
        body = self._body_rect()
        row_top_y = body.bottom() + 1 - _BOTTOM_ROW_H

        dark_pen = QtGui.QPen(Palette.CHIP_ROW_TOP_DARK)
        dark_pen.setWidth(1)
        dark_pen.setCosmetic(True)
        painter.setPen(dark_pen)
        painter.drawLine(body.left(), row_top_y, body.right(), row_top_y)

        for name in _BOTTOM_ROW_ORDER[:-1]:
            rect = self._button_rect(name)
            x_dark = rect.right() + 1
            x_light = x_dark + 1
            painter.setPen(
                QtGui.QPen(Palette.CHIP_DIVIDER_DARK, 1, Qt.SolidLine)
            )
            painter.drawLine(x_dark, row_top_y + 1, x_dark, body.bottom())
            painter.setPen(
                QtGui.QPen(Palette.CHIP_DIVIDER_LIGHT, 1, Qt.SolidLine)
            )
            painter.drawLine(
                x_light, row_top_y + 1, x_light, body.bottom()
            )

        painter.restore()

        # 3. Glyph or label per chip. SVG markers go to the icon painter.
        for name in _BOTTOM_ROW_ORDER:
            rect = self._button_rect(name)
            _fill, text_colour, glyph = chips[name]
            if name == "status" and glyph.startswith("svg:"):
                self._paint_status_icon(painter, rect, glyph)
            else:
                self._draw_chip_label(painter, rect, glyph, text_colour)

        # 4. Committed GUI-OFF accent. A saved ON->OFF change frames the
        #    GUI chip in the same red as the pill body. On a slot that
        #    cannot save, only the red text from step 3 shows.
        if self._state.gui_pending_off and self._state.gui_committed:
            self._paint_gui_pending_off_border(painter)

    def _paint_gui_pending_off_border(self, painter) -> None:
        """Stroke a bright-red frame inside the GUI chip rect.

        The red is the one the pill body uses for enabled to disabled.
        Inset by 1 px, so the stroke clears the engraved dividers.
        """
        rect = self._button_rect("gui").adjusted(1, 1, -1, -1)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        pen = QtGui.QPen(Palette.BORDER_PENDING_DISABLE)
        pen.setWidth(2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        painter.restore()

    def _paint_status_icon(self, painter, rect, kind: str) -> None:
        """Render an SVG status icon inside *rect*.

        ``svg:failed`` is the caution triangle and ``svg:spinner`` the
        8-line spinner. Both are QPainterPath drawings, so the widget
        needs no QtSvg and no asset file.
        """
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Both SVGs use a 24x24 viewBox. Map it into the chip rect with
        # a margin, so the icon does not crowd the edges.
        size = min(rect.width(), rect.height()) - 8
        if size < 8:
            painter.restore()
            return
        cx = rect.center().x()
        cy = rect.center().y()
        scale = size / 24.0
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        if kind == "svg:failed":
            self._paint_failed_triangle(painter)
        elif kind == "svg:spinner":
            self._paint_pending_spinner(painter)
        painter.restore()

    def _paint_failed_triangle(self, painter) -> None:
        """Caution triangle, from the canonical SVG in pill-lab.html.

        The viewBox is 24x24 centred at (12, 12). The painter is already
        translated to that centre, so each coordinate has 12 subtracted.
        """
        tri = QtGui.QPainterPath()
        tri.moveTo(0, -9.5)
        tri.lineTo(10.5, 9)
        tri.lineTo(-10.5, 9)
        tri.closeSubpath()
        fill_pen = QtGui.QPen(Palette.STATUS_FAILED_RED)
        fill_pen.setWidthF(1.5)
        fill_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(fill_pen)
        painter.setBrush(QtGui.QBrush(Palette.STATUS_FAILED_RED))
        painter.drawPath(tri)
        # Inner exclamation line, (12, 9.5) to (12, 14.5) in the viewBox.
        stroke_pen = QtGui.QPen(Palette.STATUS_FAILED_STROKE)
        stroke_pen.setWidthF(2.6)
        stroke_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(stroke_pen)
        painter.drawLine(QtCore.QPointF(0, -2.5), QtCore.QPointF(0, 2.5))
        # Dot at (12, 17.8) with r 1.3, so (0, 5.8) after centring.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QBrush(Palette.STATUS_FAILED_STROKE))
        painter.drawEllipse(QtCore.QPointF(0, 5.8), 1.3, 1.3)

    def _paint_pending_spinner(self, painter) -> None:
        """Eight-line spinner, from the canonical SVG in pill-lab.html.

        The opacities fall from 1.0 to 0.12. It does not animate, by
        design.
        """
        # Coordinates from the canonical SVG, centred on the origin.
        # Each tuple is (x1, y1, x2, y2, opacity).
        lines = (
            ( 0.0, -9.0,  0.0, -6.0, 1.00),
            ( 6.4, -6.4,  4.3, -4.3, 0.80),
            ( 9.0,  0.0,  6.0,  0.0, 0.62),
            ( 6.4,  6.4,  4.3,  4.3, 0.46),
            ( 0.0,  9.0,  0.0,  6.0, 0.32),
            (-6.4,  6.4, -4.3,  4.3, 0.22),
            (-9.0,  0.0, -6.0,  0.0, 0.16),
            (-6.4, -6.4, -4.3, -4.3, 0.12),
        )
        base = Palette.STATUS_PENDING_GLYPH
        for x1, y1, x2, y2, op in lines:
            colour = QtGui.QColor(base)
            colour.setAlpha(int(op * 255))
            pen = QtGui.QPen(colour)
            pen.setWidthF(2.4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2)
            )

    def _draw_chip_label(self, painter, rect, glyph, colour) -> None:
        """Draw a chip label or glyph centred in *rect*.

        A single-char glyph uses a pixel size and Black weight. A
        multi-char label uses ``_CHIP_LABEL_PT`` and Bold.
        """
        painter.setPen(QtGui.QPen(colour))
        font = painter.font()
        if len(glyph) <= 1:
            # Pin Helvetica for the status glyphs. In Helvetica the ✕
            # (U+2715) is about 60 % of the ✓ cap height. It therefore
            # gets the larger pixel size.
            font.setFamily("Helvetica")
            font.setStyleHint(QtGui.QFont.Helvetica)
            small_glyphs = {"✕", "×", "✗"}
            font.setPixelSize(
                _CHIP_GLYPH_SMALL_PX if glyph in small_glyphs
                else _CHIP_GLYPH_PX
            )
            font.setWeight(QtGui.QFont.Black)
        else:
            # Take the family from the widget's own resolved font, not
            # from ``painter.font()``. The painter may still carry the
            # Helvetica family set for a status glyph drawn earlier in
            # the same paint pass.
            font.setFamily(self.font().family())
            font.setStyleHint(QtGui.QFont.AnyStyle)
            font.setPointSize(_CHIP_LABEL_PT)
            font.setWeight(QtGui.QFont.Bold)
            font.setLetterSpacing(
                QtGui.QFont.AbsoluteSpacing, _CHIP_LABEL_TRACKING_PX
            )
        painter.setFont(font)
        painter.drawText(rect, int(Qt.AlignCenter), glyph)

    def _paint_plugin_name(self, painter) -> None:
        st = self._state
        body = self._body_rect()
        side_inset = _BUTTON_MARGIN + _BORDER_RADIUS // 2
        bottom_inset = _BOTTOM_ROW_H + _TAG_ROW_HEIGHT
        text_rect = body.adjusted(
            side_inset,
            _BUTTON_MARGIN,
            -side_inset,
            -bottom_inset,
        )
        # Plugin names often read like paths, with mixed-width
        # characters. Monospace gives every glyph the same advance, so
        # the names line up down the grid.
        font = QtGui.QFont()
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setFamily("monospace")  # fallback for setStyleHint
        font.setBold(True)
        font.setPointSize(_NAME_PT)
        painter.setFont(font)
        # The name always paints at full brightness. On a tinted body it
        # picks up a hint of the body colour.
        tint = st.effective_tint()
        if tint is Tint.GREEN:
            text_colour = Palette.TEXT_PRIMARY_GREEN
        elif tint is Tint.RED:
            text_colour = Palette.TEXT_PRIMARY_RED
        elif tint is Tint.YELLOW:
            text_colour = Palette.TEXT_PRIMARY_YELLOW
        else:
            text_colour = Palette.TEXT_PRIMARY
        painter.setPen(text_colour)
        painter.drawText(text_rect, int(Qt.AlignCenter), st.plugin_name)

    def _paint_tag_indicators(self, painter) -> None:
        st = self._state
        if not st.tags:
            # v1 has no tags, so there is nothing to draw.
            return
        # v2 path. Small rectangles above the chip row, indented from the
        # rounded corner.
        body = self._body_rect()
        y = body.bottom() - _BOTTOM_ROW_H - _TAG_ROW_HEIGHT - 2
        x = body.left() + _BUTTON_MARGIN
        for tag in st.tags:
            r, g, b = tag.colour_rgb
            painter.setBrush(QtGui.QBrush(_qcolor(r, g, b)))
            painter.setPen(Qt.NoPen)
            painter.drawRect(x, y, 14, _TAG_ROW_HEIGHT)
            x += 16

    def _draw_glyph(self, painter, rect, glyph: str, colour) -> None:
        painter.setPen(QtGui.QPen(colour))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(rect, int(Qt.AlignCenter), glyph)

    # -- hover tooltips for GUI-only -----------------------------------

    def event(self, e):
        if e.type() == QtCore.QEvent.ToolTip:
            try:
                pos = e.pos()
            except Exception:
                pos = QtCore.QPoint(0, 0)
            zone = self._zone_at(pos)
            # Failure-category tooltip, in the same wording as the
            # terminal ``NSL Failed`` line. Both flags are set only for a
            # Plugin that failed this session. Production never sets
            # them, so this branch is dead there.
            if zone in ("status", "diag"):
                if self._state.has_diagnostic and self._state.failure_label:
                    try:
                        QtWidgets.QToolTip.showText(
                            e.globalPos(), self._state.failure_label, self
                        )
                    except Exception:  # noqa: BLE001 - tooltip never raises
                        pass
                    return True
            if zone == "gui":
                # The click is blocked for Global GUI chips, so the
                # user-toggle wording would lie.
                if self._state.source is Source.GLOBAL:
                    text = TOOLTIP_GUI_ONLY_GLOBAL
                elif self._state.gui_only:
                    text = TOOLTIP_GUI_ONLY_USER_ON
                else:
                    text = TOOLTIP_GUI_ONLY_USER_OFF
                try:
                    QtWidgets.QToolTip.showText(e.globalPos(), text, self)
                except Exception:
                    pass
                return True
        return super().event(e)

