"""Plugin Pill widget - the central visual unit of the Loadout Panel.

A custom ``QWidget`` that paints a single Plugin pill and emits signals when
the user interacts with it. The widget owns no domain state: callers build a
``PillState`` dataclass and hand it in via ``set_state()``; the widget never
reaches back into Loadout / scan state.

Signal layering:

    Border  : divergence + pending-restart / save state (barber-pole / glow)
    Body    : truth-vs-intent tint   (neutral / green / red / yellow)
    Status  : current-session load truth (read-only icon)
    Buttons : Status / GUI-only / Menu / Info chips along the bottom row

Hard rules:
    * Qt imports go through ``NukeSurvivalLoadout.compat`` exclusively.
    * No ``import nuke``.
    * Never raise from a paint path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import List, Optional

from NukeSurvivalLoadout import compat
from NukeSurvivalLoadout import log
from NukeSurvivalLoadout.ui import _theme

QtCore = compat.QtCore
QtWidgets = compat.QtWidgets
QtGui = compat.QtGui

Qt = QtCore.Qt
Signal = QtCore.Signal


# ---------------------------------------------------------------------------
# State enums - small and stable. Values are canonical state vocabulary, not display.
# ---------------------------------------------------------------------------


class Source(str, Enum):
    """Where the Plugin came from.

    Both sources render with the same engraved-dark border `#1a1a1a`:
    provenance is no longer encoded in the border colour. Divergence is
    surfaced via the grey barber-pole stripe, and the GUI-only chip's
    Global-non-interactive behaviour is the only remaining provenance
    affordance in the pill chrome.
    """

    USER_ADDED = "user_added"
    GLOBAL = "global_base"


class StatusIcon(str, Enum):
    """Current-session Load Status icon vocabulary."""

    EMPTY = "empty"          # Disabled - no icon
    PENDING = "pending"      # ... muted grey
    LOADED = "loaded"        # ✓ green
    FAILED = "failed"        # ! red, clickable diag
    MISSING = "missing"      # ? yellow, clickable diag


class Tint(str, Enum):
    """Pill body tint."""

    NEUTRAL = "neutral"
    GREEN = "green"    # enabled next restart, not loaded this session
    RED = "red"        # disabled next restart, was loaded this session
    YELLOW = "yellow"  # unaddressed problem (failed / missing)


# ---------------------------------------------------------------------------
# Tooltips (locked wording - must not drift)
# ---------------------------------------------------------------------------


TOOLTIP_GUI_ONLY_GLOBAL = "GUI-only is set by the Global Loadout for this Plugin."
TOOLTIP_GUI_ONLY_USER_ON = (
    "GUI-only: loads only in GUI Nuke, skipped on the render farm."
)
TOOLTIP_GUI_ONLY_USER_OFF = "GUI-only: off, loads everywhere."


# ---------------------------------------------------------------------------
# Tag stub - v2 tags carry name + colour. v1 has the row but never populates it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TagStub:
    """Placeholder for v2 Plugin Tags.

    v1 never produces these - the row exists in the pill so the v2 transition
    is purely additive (the renderer iterates an empty list today).
    """

    name: str
    colour_rgb: tuple  # (r, g, b) ints 0-255


# ---------------------------------------------------------------------------
# PillState - the full set of inputs the renderer consumes.
# ---------------------------------------------------------------------------


@dataclass
class PillState:
    """Everything the pill needs to render.

    Keep this a plain dataclass - the domain layer builds these and hands
    them in; the widget never reaches back into Loadout / scan state.

    Tint *precedence*: pending-change (red/green) WINS over yellow problem.
    ``effective_tint()`` applies this rule so callers can pass a "raw" intent
    and let the widget do the right thing.
    """

    plugin_name: str = "Plugin"
    source: Source = Source.USER_ADDED
    enabled: bool = True                # pressed (enabled) vs unpressed (disabled)
    status_icon: StatusIcon = StatusIcon.LOADED
    tint: Tint = Tint.NEUTRAL
    selected: bool = False
    diverges_from_global: bool = False  # only meaningful for GLOBAL pills
    gui_only: bool = False
    failure_label: Optional[str] = None # Historical: human-readable
                                        # failure category. NSL no longer
                                        # captures per-plugin load failures
                                        # (the loadout chain uses plain
                                        # ``nuke.pluginAddPath``; a failing
                                        # init.py crashes the whole
                                        # interpreter). The field is
                                        # preserved for future use but
                                        # production callers leave it
                                        # ``None``.
    has_diagnostic: bool = False        # diag button becomes clickable
    is_dirty_vs_saved: bool = False     # True when this pill's effective
                                        # entry differs from the active
                                        # Loadout's saved-on-disk baseline
                                        # (drives the white "you have
                                        # uncommitted edits" border; once
                                        # the user saves, this flips to
                                        # False and the border switches to
                                        # the locked-in lime/red colour
                                        # for any pending-restart diff).
    source_missing: bool = False        # Plugin loaded this Nuke session
                                        # but its source folder is no
                                        # longer in user_plugins_dirs (and
                                        # not in Global). Painted as
                                        # YELLOW hazard body + green
                                        # checkmark (still loaded this
                                        # session) + red border glow
                                        # (won't load on next restart):
                                        # yellow signals missing, red
                                        # means it won't load.
    info_active: bool = False           # True while the side panel's Info
                                        # tab currently holds this plugin's
                                        # README - paints the info chip in
                                        # the lit-up hover-lighten state so
                                        # the user can see at a glance which
                                        # pill the side-panel content
                                        # corresponds to. Pushed by
                                        # ``LoadoutPanel._apply_active_chips_to_grid``
                                        # which Registry calls right after
                                        # ``side_panel.show_info``. Mutually
                                        # exclusive with ``log_active`` -
                                        # both flags are pushed together, so
                                        # clicking log on any pill clears
                                        # the prior info highlight.
    log_active: bool = False            # DORMANT - was the diag/"Log" chip's
                                        # active highlight. The Log chip is
                                        # gone; superseded by ``menu_active``.
                                        # Kept (always False) so dormant
                                        # diag paint/test paths don't break.
    menu_active: bool = False           # Sibling of ``info_active`` for the
                                        # menu chip. True while the side
                                        # panel's Menu tab currently holds
                                        # this Plugin's menu.py - paints the
                                        # menu chip in the lit-up state so
                                        # the user can see at a glance which
                                        # pill the panel content belongs to.
                                        # Mutually exclusive with
                                        # ``info_active`` (pushed as a pair
                                        # by ``_apply_active_chips_to_grid``)
                                        # so at most one chip on one pill is
                                        # lit at a time.
    panic_engaged: bool = False         # Panic Mode is on. Suppresses the
    gui_pending_on: bool = False   # GUI differs OFF->ON vs session load. PENDING
                                   # signal = lit-purple chip (always on when
                                   # gui_only True). COMMITTED signal (cell wash)
                                   # is gated on ``gui_committed`` below.
    gui_pending_off: bool = False  # GUI differs ON->OFF vs session load. PENDING
                                   # signal = red chip text. COMMITTED signal
                                   # (red GUI-button border) gated on
                                   # ``gui_committed`` below.
    gui_committed: bool = False    # The GUI change above is SAVED on a saveable
                                   # slot (not Custom / Global, not a ceremonial-
                                   # save or unsaved edit) and will apply on
                                   # restart. Gates the committed-only visuals -
                                   # purple cell wash + red GUI border - so they
                                   # never fire on a slot that can't commit.
                                   # Mirrors ``is_dirty_vs_saved`` gating.
                                        # saved-glow (lime/red border) on
                                        # USER_ADDED pills - they won't
                                        # load on next restart regardless
                                        # of their committed state, so the
                                        # "will load on restart" glow lies.
                                        # GLOBAL / Global pills keep
                                        # their glow (panic doesn't strip
                                        # Globals). Source-
                                        # missing pills also keep their
                                        # red glow because that signal is
                                        # still truthful in panic. The
                                        # green-glow pills go back to plain
                                        # white in panic mode because a
                                        # "will load on restart" glow on a
                                        # pill that won't load is a
                                        # distracting, misleading marker.
    tags: List[TagStub] = field(default_factory=list)  # v2 stub - empty in v1

    def effective_tint(self) -> Tint:
        """Apply tint precedence (pending-change beats yellow problem).

        The widget always renders the effective tint; callers can populate
        ``tint`` from raw signals and trust the precedence to be correct.
        """
        # If caller already collapsed to NEUTRAL or to a pending colour, honour it.
        # The precedence only matters when both a pending change and a problem
        # exist simultaneously - caller may pre-resolve, or pass YELLOW knowing
        # we'll keep it (because no pending change is recorded in `tint`).
        return self.tint


# ---------------------------------------------------------------------------
# Palette - single source of truth for pill colours. Exact shades are
# production phase territory; these are first-pass picks within the canonical
# colour *vocabulary* constraints.
# ---------------------------------------------------------------------------


def _qcolor(r: int, g: int, b: int, a: int = 255):
    return QtGui.QColor(r, g, b, a)


class Palette:
    """Pill colour vocabulary - converged with the canonical design system.

    Source of truth: ``Knowledge/docs/design/NSL_Design_System_New/preview/``
        - ``_pill.css`` for the chrome (border / body / chip rules)
        - ``pill-anatomy.html`` for the legend
        - ``pill-lab.html`` for the 12 canonical scenarios

    Where the canonical and the prior production-pill diverged, the canonical
    wins. The earlier desaturated palette has been replaced with the brighter,
    more saturated canonical values; the divergent border has changed from a
    solid purple to a grey barber-pole stripe; the yellow body has changed
    from a flat tint to a hazard zebra. See the file-level docstring for
    details.
    """

    # ------------------------------------------------------------------
    # Borders - canonical default is engraved dark (#1a1a1a), NOT the
    # panel-divider grey the earlier production palette used. The deeper
    # value makes the pill read as a discrete object set against the
    # panel rather than as a sketched-on outline.
    # ------------------------------------------------------------------
    BORDER_DEFAULT = _qcolor(26, 26, 26)        # #1a1a1a - engraved dark
    BORDER_USER_ADDED = BORDER_DEFAULT
    BORDER_GLOBAL = BORDER_DEFAULT
    # Loaded glow - thin white border on top of the engraved-dark
    # default when the pill is pressed. Locked at ~85 % opacity
    # (alpha 215) so the edge reads clearly against the body fill;
    # canonical CSS specifies 55 % but at our Nuke-panel rendering
    # scale that came across faint. Layered with the outer halo in
    # ``_paint_border``.
    BORDER_LOADED_GLOW = _qcolor(255, 255, 255, 215)
    # Divergent - grey barber-pole stripe drawn over the border path
    # (replaces the prior solid purple). Light stripe matches the
    # canonical ``#7a7a7a``; the dark stripe was lifted from canonical
    # ``#2a2a2a`` to ``#4a4a4a`` so the dark band reads as a grey
    # gap rather than a near-black gap (improves contrast against the
    # panel-bg ``#393939``, keeps the barber-pole rhythm legible
    # without it reading as broken sections).
    DIVERGENT_STRIPE_LIGHT = _qcolor(122, 122, 122)
    DIVERGENT_STRIPE_DARK = _qcolor(74, 74, 74)
    # Selection - 2 px Nuke-orange ring **replaces** the border entirely
    # when ``state.selected`` is True (canonical behaviour, matches the
    # locked vocabulary from the grid-toolbar pass). Single source of
    # truth for the orange lives in ``_theme.NUKE_ORANGE_RGB``.
    BORDER_SELECTION = _qcolor(*_theme.NUKE_ORANGE_RGB)

    # ------------------------------------------------------------------
    # Pending-change borders - layered ON TOP of the default border to
    # signal "this pill has a pending-restart diff." The colour
    # distinguishes whether the diff is still in-memory or committed
    # to disk:
    #   * BORDER_PENDING_DIRTY (white) - uncommitted edit. User has
    #     toggled the pill but hasn't clicked Save. Reads as "you're
    #     still editing this - the change will be lost on Discard."
    #   * BORDER_PENDING_ENABLE (lime) - committed. Save has locked
    #     the toggle in; the plugin WILL load next Nuke restart.
    #   * BORDER_PENDING_DISABLE (red) - committed. Save has locked
    #     the toggle in; the plugin WILL unload next Nuke restart.
    #
    # Unsaved changes show the white border; saved changes that will
    # enable a plugin get the bright lime border; saved changes that
    # will disable a plugin get the red border.
    BORDER_PENDING_DIRTY = _qcolor(255, 255, 255, 240)        # bright white
    BORDER_PENDING_ENABLE = _qcolor(170, 255, 80, 245)        # lime green
    BORDER_PENDING_DISABLE = _qcolor(255, 60, 60, 245)        # bright red
    # Halo peak alpha for the pending-change outer glow - keeps the
    # solid border readable while the halo bleeds outward.
    PENDING_HALO_PEAK_ALPHA = 110

    # ------------------------------------------------------------------
    # Body fills.
    # Pressed (enabled) = indented, darker body, dark border.
    # Unpressed (disabled) = raised, lighter body, soft top-left light.
    # Canonical CSS:
    #   .pill {                                 --pill-body: #2f2c2c }
    #   .pill--unpressed {                      --pill-body: #4c4c4c }
    #   .pill--green  (pressed)                 #486050
    #   .pill--red    (pressed)                 #6e4242
    #   .pill--unpressed.pill--green            #687c6e
    #   .pill--unpressed.pill--red              #845858
    #   .pill--yellow                           hazard zebra (see below)
    # ------------------------------------------------------------------
    # Pressed body is darker than the canonical CSS (34, 32, 32 =
    # #222020) so the pressed pill reads as visibly indented against
    # the recessed grid background (#2d2d2d); the canonical #2f2c2c
    # barely separated from the grid plane.
    BODY_NEUTRAL_PRESSED = _qcolor(34, 32, 32)     # #222020
    BODY_NEUTRAL_UNPRESSED = _qcolor(76, 76, 76)   # #4c4c4c
    # Body tints - dialled down to ~75 % of the canonical CSS saturation
    # so the pending-enable / pending-disable signals are subtler against
    # the panel. Each value is a linear interpolation 75 % of the way
    # from the neutral body to the canonical full-saturation tint:
    #   pressed   from neutral rgb(47, 44, 44) → green rgb(66, 83, 71),
    #             red rgb(94, 61, 61)
    #   unpressed from neutral rgb(76, 76, 76) → green rgb(97, 112, 102),
    #             red rgb(118, 85, 85)
    BODY_TINT_GREEN_PRESSED = _qcolor(66, 83, 71)
    BODY_TINT_GREEN_UNPRESSED = _qcolor(97, 112, 102)
    BODY_TINT_RED_PRESSED = _qcolor(94, 61, 61)
    BODY_TINT_RED_UNPRESSED = _qcolor(118, 85, 85)
    # Yellow tint is rendered as a diagonal "hazard zebra" stripe pattern
    # painted in ``_paint_body_fill``; these two values are the stripe
    # colours (pressed variant). Brightened above the canonical
    # ``#3e3920`` / ``#322e1c``: the muted-brown canonical didn't read
    # as a warning on the dark theme, so these sit between the canonical
    # and a louder ~25 %-brighter pick - an audible caution-amber that
    # stays in vocabulary.
    BODY_HAZARD_STRIPE_A_PRESSED = _qcolor(70, 64, 36)
    BODY_HAZARD_STRIPE_B_PRESSED = _qcolor(56, 52, 31)
    # Unpressed variant - slightly lighter to compensate for the raised
    # highlight. Brightened above the canonical ``#4e4828`` / ``#423c22``
    # on the same basis as the pressed pair.
    BODY_HAZARD_STRIPE_A_UNPRESSED = _qcolor(88, 81, 45)
    BODY_HAZARD_STRIPE_B_UNPRESSED = _qcolor(74, 67, 38)
    # Legacy aliases retained for any caller that still references the
    # pre-converge name (no production-data callers do; ``BODY_TINT_GREEN``
    # etc. remain available as representative colours).
    BODY_TINT_GREEN = BODY_TINT_GREEN_PRESSED
    BODY_TINT_RED = BODY_TINT_RED_PRESSED
    BODY_TINT_YELLOW = BODY_HAZARD_STRIPE_A_PRESSED

    # ------------------------------------------------------------------
    # Text colours.
    # Plugin name on tinted bodies picks up a warm tint of the body
    # colour (canonical: green→#d4e8d0, red→#f0d4d4, yellow→#f0e3b8).
    # Default name is pure white (canonical: ``--pill-text: #ffffff``).
    # ------------------------------------------------------------------
    TEXT_PRIMARY = _qcolor(255, 255, 255)
    TEXT_PRIMARY_GREEN = _qcolor(212, 232, 208)    # #d4e8d0
    TEXT_PRIMARY_RED = _qcolor(240, 212, 212)      # #f0d4d4
    TEXT_PRIMARY_YELLOW = _qcolor(240, 227, 184)   # #f0e3b8
    TEXT_DIM = _qcolor(140, 140, 140)

    # ------------------------------------------------------------------
    # Status icons (used by the SVG path painters in ``_paint_status_icon``).
    # Canonical icons:
    #   LOADED - green ✓ glyph,  chip bg #3d6b3d / text #a8e8a8
    #   OFF - red   ✕ glyph,  chip bg #6b3a3a / text #f4b0b0
    #   FAILED - caution triangle SVG (fill #cf8e8e, stroke #7a3030)
    #              chip bg #7a3030 / text #f4b0b0
    #   MISSING - olive  ?  glyph, chip bg #8a7530 / text #ffe680
    #   PENDING - spinner SVG (white at decaying opacity)
    #              chip bg #3a3a3a / text #d8d8d8
    # ------------------------------------------------------------------
    STATUS_LOADED_GREEN = _qcolor(168, 232, 168)    # #a8e8a8 - glyph
    STATUS_FAILED_RED = _qcolor(207, 142, 142)      # #cf8e8e - SVG fill
    STATUS_FAILED_STROKE = _qcolor(122, 48, 48)     # #7a3030 - SVG stroke
    STATUS_MISSING_YELLOW = _qcolor(255, 230, 128)  # #ffe680 - glyph
    STATUS_OFF_RED = _qcolor(244, 176, 176)         # #f4b0b0 - ✕ glyph
    # Spinner glyph colour - pure white so it reads brightly against
    # the subtle green chip fill below. Name kept generic so future
    # tint tweaks don't churn the symbol.
    STATUS_PENDING_GLYPH = _qcolor(255, 255, 255)   # #ffffff - spinner

    # ------------------------------------------------------------------
    # Bottom-row chip tints - canonical, brighter than the earlier
    # production palette. Each `status-*` chip has its own bg + text.
    # ------------------------------------------------------------------
    # Status chip fills (per status icon state).
    CHIP_STATUS_LOADED_FILL = _qcolor(61, 107, 61)      # #3d6b3d
    CHIP_STATUS_LOADED_TEXT = STATUS_LOADED_GREEN
    CHIP_STATUS_OFF_FILL = _qcolor(107, 58, 58)         # #6b3a3a
    CHIP_STATUS_OFF_TEXT = STATUS_OFF_RED
    CHIP_STATUS_FAILED_FILL = _qcolor(122, 48, 48)      # #7a3030
    CHIP_STATUS_FAILED_TEXT = STATUS_OFF_RED
    CHIP_STATUS_MISSING_FILL = _qcolor(138, 117, 48)    # #8a7530
    CHIP_STATUS_MISSING_TEXT = STATUS_MISSING_YELLOW
    # Pending-add chip - same green family as the pill body's
    # pending-add tint (#425347) but a touch darker and more
    # desaturated so the chip lane recedes slightly behind the body
    # rather than disappearing entirely. The white spinner glyph
    # carries the contrast. rgb(60, 72, 64) vs the body's
    # rgb(66, 83, 71): each channel ~4-11 lower and the green channel
    # pulled marginally closer to the others, for a slightly darker,
    # more desaturated green.
    CHIP_STATUS_PENDING_FILL = _qcolor(60, 72, 64)      # #3c4840
    CHIP_STATUS_PENDING_TEXT = STATUS_PENDING_GLYPH     # #ffffff

    # Log / diagnostic chip - two distinct visual states:
    #
    #   * No diagnostic    → neutral grey (`#3a3a3a` / muted text).
    #                        The chip reads as a placeholder - present
    #                        for layout consistency, not asking for
    #                        attention.
    #   * Has a diagnostic → yellow (`#8a7530`, same as the ``missing``
    #                        status chip) + WHITE text. The colour
    #                        shift signals "there is something here";
    #                        the white text is the strong click hint.
    #
    # Hover lighten (via ``_zone_is_actionable``) only fires when the
    # chip is on - the off chip stays static.
    CHIP_LOG_OFF_FILL = _qcolor(58, 58, 58)             # #3a3a3a - neutral
    CHIP_LOG_OFF_TEXT = _qcolor(110, 110, 110)          # #6e6e6e - muted
    CHIP_LOG_ON_FILL = _qcolor(138, 117, 48)            # #8a7530 - yellow
    CHIP_LOG_ON_TEXT = _qcolor(255, 255, 255)           # white - click hint

    # Menu chip - opens this Plugin's menu.py in the side panel. ``menu`` and
    # ``info`` are the two "open a side-panel tab" actions, so they share the
    # info chip's off-white "sticky-note" look and read as a matched pair.
    # A neutral-grey fill made ``menu`` look like a disabled GUI-off chip and
    # read as out of place next to ``info``; matching info fixes both the
    # readability and the "doesn't belong" feel. Values mirror CHIP_INFO_*
    # (duplicated rather than aliased because CHIP_INFO_* is defined later in
    # this class body - keep the two in sync if either moves).
    CHIP_MENU_FILL = _qcolor(90, 87, 80)                # #5a5750 - matches CHIP_INFO_FILL
    CHIP_MENU_TEXT = _qcolor(216, 210, 192)             # #d8d2c0 - matches CHIP_INFO_TEXT

    # GUI-only chip - off, user-on (lit purple), Global-dim (read-only).
    CHIP_GUI_OFF_FILL = _qcolor(58, 58, 58)             # #3a3a3a
    CHIP_GUI_OFF_TEXT = _qcolor(110, 110, 110)          # #6e6e6e
    CHIP_GUI_ON_FILL = _qcolor(90, 79, 114)             # #5a4f72
    CHIP_GUI_ON_TEXT = _qcolor(212, 176, 255)           # #d4b0ff
    CHIP_GUI_GLOBAL_DIM_FILL = _qcolor(58, 58, 58)
    CHIP_GUI_GLOBAL_DIM_TEXT = _qcolor(90, 90, 90)      # #5a5a5a

    # Info chip - subtle off-white sticky-note constant.
    CHIP_INFO_FILL = _qcolor(90, 87, 80)                # #5a5750
    CHIP_INFO_TEXT = _qcolor(216, 210, 192)             # #d8d2c0

    # Chip divider chrome - paired 1 px black + 2 px white-5% inset for
    # the engraved look between chips. Canonical CSS:
    #   inset 1px 0 0 rgba(0,0,0,0.55),
    #   inset 2px 0 0 rgba(255,255,255,0.05)
    CHIP_DIVIDER_DARK = _qcolor(0, 0, 0, 140)
    CHIP_DIVIDER_LIGHT = _qcolor(255, 255, 255, 13)
    # Top edge of the chip row (1 px inset black) - separates the row
    # from the title area above it.
    CHIP_ROW_TOP_DARK = _qcolor(0, 0, 0, 153)

    # Legacy aliases - earlier callers (grid_toolbar, side_panel etc.)
    # referenced these generic names. Kept as aliases so the converge
    # doesn't ripple beyond this file. New code should use the more
    # specific names above.
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
# Geometry - pill layout zones. Computed inside paintEvent / hit-test methods
# from the current widget rect so resize is automatic.
# ---------------------------------------------------------------------------


# Canonical pill is 280×96 with a 20 px radius and a 28 px chip row.
# Each of the size knobs below honours an env-var override so size
# variants can be rendered in adjacent snapshots without touching
# the source.
# The defaults are the canonical values; env vars only kick in if set.

def _env_int(name: str, default: int) -> int:
    """Read an int env var, fall back to *default* on miss / parse error."""
    import os
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default

# Compact 211×70 body. The body chrome, padding, border radius, chip-row
# height, and status glyph sizes are scaled down from the canonical to
# exploit the headroom won by the pt-size font vocabulary. The name + chip
# label point sizes are deliberately *not* touched here - they remain
# panel-wide vocabulary anchors. The status-chip glyph (✓ ✕ ✗) does scale
# with the body.
# Each knob honours an env-var override so the size-compare script can
# iterate without code edits.
# The shadow halo is 15 px so the gap between adjacent pills opens to
# ~30 px while keeping the body itself 211 × 70.
_MIN_W = 211 + 2 * 15
_MIN_H = 70 + 2 * 15
_BORDER_RADIUS = _env_int("NSL_PILL_RADIUS", 16)
_BORDER_WIDTH = 1
_SHADOW_MARGIN = 15
# Bottom-row chips span the full pill width, touching each other.
# Canonical CSS uses ``height: 28px`` on the row; the grid template uses
# ``grid-template-rows: 1fr 28px`` so the upper area auto-fills.
# Chip row is 19 px - comfortably above the 8 pt label font's natural
# ~11 px ascent + descent, so labels don't clip.
_BOTTOM_ROW_H = _env_int("NSL_PILL_CHIP_H", 19)
_BOTTOM_ROW_DIVIDER = 1      # paired 1 px black + 2 px white inset highlight
_BUTTON_MARGIN = 11

# Pill body hover affordance. The bottom-row chips (gui / info / diag)
# lighten on hover via ``_lighten``; the body (name zone -
# click-to-toggle) gets the same "yes I am clickable" cue from a
# translucent white overlay painted between body fill and chip row,
# without recolouring the body tint. Alpha is kept low (9) so it's a
# barely-there cue that signals interactivity without making the body
# look mid-state hovered/pressed.
_BODY_HOVER_ALPHA = 9

# Chip glyph + label sizes (env-overrideable for the size compare).
# Glyphs stay in pixels because the canonical CSS sizes them in px and
# they're proportional to the chip-row height, not the body font.
# Labels and name moved to **point sizes** so they scale with the panel-
# wide font vocabulary (panel chrome uses 10 pt; the pill name picks up
# 10 pt; the chip labels drop to 8 pt to read as a tighter hierarchy
# step).
_CHIP_GLYPH_PX = _env_int("NSL_PILL_GLYPH_PX", 13)         # ✓ ? glyphs
_CHIP_GLYPH_SMALL_PX = _env_int("NSL_PILL_GLYPH_SMALL_PX", 16)  # ✕ × ✗
_CHIP_LABEL_PT = _env_int("NSL_PILL_CHIP_PT", 10)          # GUI / MENU / INFO
# Letter-spacing (device px) applied to the all-caps multi-char chip labels
# (GUI / MENU / INFO). The default Helvetica caps crowd together and the
# capital I in INFO reads like a lowercase l; 1.0 px of tracking opens them
# up and disambiguates the I.
_CHIP_LABEL_TRACKING_PX = 1.0
# Plugin name point size - 10 pt matches the panel-wide control font
# (grid_toolbar sort label / dropdown, search field). The legacy
# "derive from host font pointSize + 1" branch is gone; explicit point
# size keeps the pill consistent across host font configurations.
_NAME_PT = _env_int("NSL_PILL_NAME_PT", 10)
_TAG_ROW_HEIGHT = 6
# Chip order - left-to-right: status, GUI, menu, info.
# The old "Log"/diag chip was retired (no live diagnostic source) and
# replaced by "menu" (opens the Plugin's menu.py in the side panel). GUI
# sits next to the status indicator - GUI-only is itself a kind of load
# status - and menu sits next to info, its sibling "inspect this Plugin"
# surface. The dormant "diag" zone code below is no longer reachable (it is
# absent from this tuple, so ``_zone_at`` never returns it) but is left in
# place as an additive change.
_BOTTOM_ROW_ORDER = ("status", "gui", "menu", "info")
# Loaded glow - extra outer ring radius (px outward from the body edge)
# and peak alpha for the canonical "thin white border + outer halo" look.
# CSS source: ``box-shadow: 0 0 12px rgba(255,255,255,0.22),
#                            0 0 3px  rgba(255,255,255,0.28)``.
_LOADED_GLOW_OUTER_PX = 6
_LOADED_GLOW_PEAK_ALPHA = 56     # ≈ 0.22 of 255
_LOADED_GLOW_INNER_ALPHA = 71    # ≈ 0.28 of 255 - tighter inner ring


# ---------------------------------------------------------------------------
# PluginPill - the widget
# ---------------------------------------------------------------------------


class PluginPill(QtWidgets.QWidget):
    """Custom QWidget rendering a single Plugin pill.

    Signals (out - the wiring layer routes these to ``loadout_ops``):

        toggled(bool) - user clicked the body; new enabled state
        info_clicked() - info button (bottom row, rightmost)
        menu_clicked() - menu button (bottom row); open this
                                   Plugin's menu.py in the side panel. Always
                                   emits on a plain click.
        diagnostic_clicked() - DORMANT (old Log chip); never emitted now.
        gui_only_toggled(bool) - user-added Plugins only; new gui_only state.
                                   Global pills NEVER emit this.

    The widget owns no domain state - ``set_state()`` replaces the dataclass
    snapshot in one go. The widget calls ``update()`` and ``setToolTip()``;
    nothing reaches into the Loadout / scan state.
    """

    toggled = Signal(bool)
    info_clicked = Signal()
    menu_clicked = Signal()       # menu chip (bottom row) - open this
                                  # Plugin's menu.py in the side panel's
                                  # Menu tab. Always emits on a plain click.
    diagnostic_clicked = Signal()  # DORMANT - old Log chip; never emitted
                                   # now (the diag chip was removed from
                                   # ``_BOTTOM_ROW_ORDER``). Kept defined so
                                   # existing wiring/tests don't break.
    gui_only_toggled = Signal(bool)
    #: Emitted on a right-click → "Open Plugin Folder". The pill does not know
    #: its own on-disk path (``PillState`` carries only the name); the wiring
    #: layer resolves the folder via the registry - the same indirection used
    #: by ``info_clicked`` / ``menu_clicked``.
    open_folder_requested = Signal()
    #: Emitted when the user clicks the pill body with Shift / Ctrl / Cmd
    #: held - the press is a selection gesture, not an enable toggle.
    #: Payload is the ``Qt.KeyboardModifiers`` from the press event so the
    #: receiver (the grid) can distinguish shift (add-only) from ctrl/cmd
    #: (smart toggle).
    selection_requested = Signal(object)

    def __init__(self, state: Optional[PillState] = None, parent=None):
        super().__init__(parent)
        self._state: PillState = state if state is not None else PillState()
        # Border treatment for the pressed (enabled) state. "glow" is the
        # locked production default - off-white green-tinted border with a
        # soft outer bloom. "thick_white" and "normal" are kept available
        # for design iteration; the disabled state ignores this knob.
        self._border_style: str = "glow"
        # Current hover zone - one of the _BOTTOM_ROW_ORDER names, or
        # "body", "outside". Drives the per-chip hover-lighten treatment
        # for gui / info / log (status stays read-only - load truth is
        # not an interaction surface).
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
        """Convenience: replace specific fields without rebuilding the dataclass."""
        self._state = replace(self._state, **kwargs)
        self._apply_tooltips()
        self.update()

    def _apply_tooltips(self) -> None:
        # Whole-widget tooltip is the Plugin name (cheap discoverability).
        try:
            self.setToolTip(self._state.plugin_name)
        except Exception as exc:  # never raise during state apply
            log.warning(f"pill setToolTip failed: {exc!r}")

    # -- hit-test geometry helpers ------------------------------------

    def _button_rect(self, name: str) -> "QtCore.QRect":
        """Rect for one of the four bottom-row chips.

        Chips span the full pill width, split into four equal segments
        touching at 1 px hairline dividers. Left segment owns the leftover
        pixel from integer division so the rounded outer corners line up.

        ``name`` in {"status", "gui", "menu", "info"}.
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
        # Clicks outside the visible body (in the shadow margin) count as
        # outside the pill, so the hit zones match what the user sees.
        if not self._body_rect().contains(pos):
            return "outside"
        for name in _BOTTOM_ROW_ORDER:
            if self._button_rect(name).contains(pos):
                return name
        return "body"

    # -- mouse handling -----------------------------------------------

    def mouseMoveEvent(self, event):
        """Track hover zone for per-chip lighten effects on gui / info /
        log. Status chip is intentionally not hover-lit - it is read-only
        load truth, not an interaction surface. Cursor flips to a
        pointing hand over any clickable chip OR the pill body itself
        (which toggles enable/disable on click) so the affordance reads.

        The log chip is only "actionable" when ``state.has_diagnostic``
        is True - an empty log chip is not clickable, so it should not
        light on hover and the cursor stays default. Matches the click
        gate in :meth:`mousePressEvent`.
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
        """True when *zone* responds to a click - drives both the hover
        lighten and the cursor flip. The diag/log chip needs a backing
        diagnostic to be actionable; other chips are always actionable
        except the GUI chip on a source-missing pill (locked because
        the plugin won't load next restart, so highlighting it would
        imply a clickability it doesn't have).

        Panic-engaged + USER_ADDED locks the mutating zones (body
        toggle, GUI chip) - those changes won't take effect on next
        restart anyway because panic strips user plugins. Info /
        diag / status stay actionable so the user can still inspect.
        """
        st = self._state
        panic_user = st.panic_engaged and st.source is Source.USER_ADDED
        if zone == "body":
            # Source-missing pills are locked at the body too - the
            # plugin won't load regardless of the enable flag. Drops
            # both the hover-lift signal and the clickable cursor.
            if st.source_missing:
                return False
            if panic_user:
                return False
            return True
        if zone == "menu":
            # The menu chip only inspects (opens menu.py in the side panel),
            # never mutates load state - so it stays actionable even when the
            # body/GUI toggles are locked (panic / source-missing). Mirrors
            # the info chip's "you can always inspect" rule.
            return True
        if zone == "diag":
            # DORMANT - diag is no longer in ``_BOTTOM_ROW_ORDER`` so this
            # branch is unreachable; kept for the additive-change decision.
            return bool(st.has_diagnostic)
        if zone == "gui":
            if st.source_missing:
                return False
            if panic_user:
                return False
            # Global-base pills can't toggle GUI-only (the Global
            # Loadout owns the flag; click is already blocked in
            # mousePressEvent). Without this explicit block the
            # hover-lift falls through to the generic _HOVER_CHIPS
            # check and fires anyway, implying a clickability the pill
            # doesn't have. Blocking here matches the click gate so
            # visual + behavioural states agree.
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

        # Modifier-held click is **always** a selection gesture, regardless
        # of which zone (body / info / gui / diag / status) the click
        # lands on. Chips' own actions are reserved for plain clicks so
        # the user can hold shift / ctrl / cmd and bulk-select without
        # accidentally opening the info dialog or flipping gui_only.
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
            # Always clickable - the side panel resolves whether a menu.py
            # exists and shows it (or a "no menu.py" message).
            self.menu_clicked.emit()
            return

        if zone == "diag":
            # DORMANT - diag is no longer in ``_BOTTOM_ROW_ORDER``, so
            # ``_zone_at`` never returns it and this is unreachable.
            if st.has_diagnostic:
                self.diagnostic_clicked.emit()
            return

        if zone == "gui":
            # GUI-only is set by the Global Loadout for Global
            # Plugins and the chip is greyed + non-interactive in that
            # case. User-added Plugins own their flag and can flip it.
            # The hover tooltip already reflects the read-only state
            # (TOOLTIP_GUI_ONLY_GLOBAL); this guard makes the click
            # match. Without it the chip would silently mutate state
            # the Global Loadout is supposed to dictate.
            if st.source is Source.GLOBAL:
                return
            # Source-missing - the plugin's source folder is gone,
            # so the plugin won't load next restart regardless of
            # ``gui_only``. Lock the chip so the user can't waste a
            # click on a flag with no effect.
            if st.source_missing:
                return
            # Panic engaged - user-added plugin won't load next
            # restart, so toggling gui_only is moot. Info stays
            # clickable; mutating zones do not.
            if st.panic_engaged:
                return
            new_value = not st.gui_only
            self.update_state(gui_only=new_value)
            self.gui_only_toggled.emit(new_value)
            return

        if zone == "body":
            # Source-missing - the plugin won't load next restart
            # regardless of ``enabled``, so the body toggle is moot.
            # Lock the click + suppress hover-lift / clickable cursor
            # (see ``_zone_is_actionable``) so the plugin can't be
            # turned on when its source is gone.
            if st.source_missing:
                return
            # Panic engaged on a user-added pill - same logic: the
            # plugin won't load next restart, body toggle is moot.
            # GLOBAL pills stay interactive because Globals
            # still load in panic.
            if st.panic_engaged and st.source is Source.USER_ADDED:
                return
            new_enabled = not st.enabled
            self.update_state(enabled=new_enabled)
            self.toggled.emit(new_enabled)
            return

        # zone == "status" or "outside" - read-only / no-op for the pill.
        # ``ignore()`` lets Qt propagate the press to the parent cell so
        # a click in the pill's shadow-margin ring (between the visible
        # body and the pill widget edge) reaches the grid's marquee
        # handler instead of being silently swallowed here.
        event.ignore()

    def contextMenuEvent(self, event):
        """Right-click → a small context menu whose single action reveals the
        Plugin's source folder in the OS file browser.

        The pill never reaches into Loadout / scan state (it doesn't know its
        own on-disk path), so the action just emits ``open_folder_requested``;
        the wiring layer resolves the folder via the registry and opens it.
        Wrapped in try/except so a menu-build failure can't crash the grid's
        event path - mirrors the never-raise contract on ``paintEvent``.
        """
        try:
            menu = QtWidgets.QMenu(self)
            action = menu.addAction("Open Plugin Folder")
            action.triggered.connect(
                lambda *_: self.open_folder_requested.emit()
            )
            menu.exec(event.globalPos())
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
            # Paint order is load-bearing:
            #   1. Drop shadow (behind everything) - ONLY for unpressed
            #      (raised / disabled) pills. Pressed pills sit "in" the
            #      surface, not "above" it; they get the loaded glow
            #      from _paint_border instead. Shadow + glow stacking
            #      reads as visual noise - pick one per state.
            #   2. Body fill + raised/pressed inset gradients
            #   3. Bottom-row chip fills + dividers (clipped inside border)
            #   4. Plugin name + tag indicators
            #   5. Border + selection outline - ALWAYS LAST so nothing
            #      covers it.
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

        Locked invariant: a pill is **either** pressed (glow) **or**
        unpressed (drop shadow), with no third state. The earlier
        definition also gated on ``status_icon == LOADED``, which left
        pressed-but-pending pills (scan-pending, failed, missing,
        pending-enable) painting flat - neither glow nor shadow. That
        produced inconsistent visual feedback when the user toggled a
        pill. Now every pressed pill gets the glow regardless of the
        status chip; every unpressed pill gets the drop shadow.
        """
        return self._state.enabled

    def _border_colour(self):
        """Default border colour - engraved dark ``#1a1a1a``.

        Provenance (user vs Global) is no longer encoded in the border
        colour; both sources use the same dark engraved border. The
        loaded glow + divergent barber-pole + selection orange paint on
        top of (or replace) this baseline border depending on state.
        """
        return Palette.BORDER_DEFAULT

    def _body_colour(self):
        """Solid body colour for non-zebra tints. Yellow returns the
        first stripe colour as a fallback fill - the zebra pattern is
        painted on top in ``_paint_body_fill``.
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
            # Fallback base colour under the hazard zebra. The zebra
            # pattern paints over this in ``_paint_body_fill``.
            return (
                Palette.BODY_HAZARD_STRIPE_A_PRESSED
                if pressed
                else Palette.BODY_HAZARD_STRIPE_A_UNPRESSED
            )
        # NEUTRAL - pressed (enabled) is dark; unpressed (disabled) is
        # the raised lighter body.
        return (
            Palette.BODY_NEUTRAL_PRESSED
            if pressed
            else Palette.BODY_NEUTRAL_UNPRESSED
        )

    def _body_rect(self) -> "QtCore.QRect":
        """Visible pill body rect - inset from the widget rect by
        ``_SHADOW_MARGIN`` so the drop-shadow blur has room to render
        around the body without being clipped by the parent."""
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
        """Soft drop shadow falling DOWN and slightly RIGHT - only paints
        for unpressed/raised pills (see ``_paint``). Built from concentric
        rounded rects whose growth on the top + left sides is cancelled by
        an equal offset, leaving the shadow entirely below + right of the
        body. Softness comes from many layers with low per-layer alpha;
        depth comes from cumulative alpha across more layers.

        Canonical CSS pairs an outer soft shadow with a tighter inner
        one (``0 4px 8px rgba(0,0,0,0.35), 0 1px 2px rgba(0,0,0,0.45)``).
        We simulate that by giving the inner rings a higher per-layer
        alpha than the outer rings - same falloff shape, more weight
        directly under the pill edge.
        """
        body = self._body_rect()
        offset_x = 2
        offset_y = 4
        # Same outward reach (max grow = 6 px) as before, but more rings
        # at finer pitch (~0.5 px per ring) + a quadratic alpha falloff
        # so the inner edge is gentler and the gradient blurs out
        # smoothly. Reach unchanged; softness up.
        layers = 12
        max_grow = 6
        for i in range(layers, 0, -1):
            grow = i * (max_grow / layers)
            left_pad = max(grow - offset_x, 0)
            top_pad = max(grow - offset_y, 0)
            r = QtCore.QRectF(body).adjusted(
                -left_pad, -top_pad, grow + offset_x, grow + offset_y
            )
            # Quadratic falloff peaking at the innermost ring - gentler
            # near the edge than the prior linear curve, so the shadow
            # reads as a soft cushion rather than a sharp lip.
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
        """Paint the canonical 135° hazard zebra over the pill body.

        Stripe pair is the ``BODY_HAZARD_STRIPE_*`` palette - stripe A
        is assumed already painted as the body's base fill; this method
        paints stripe B on every other 12 px band over a 24 px period.

        ``pressed`` selects the pressed (enabled, darker) vs unpressed
        (disabled, lighter) stripe-B colour. ``opacity`` blends the
        whole zebra: 1.0 = full canonical strength, lower values give a
        faint memorial / breadcrumb strength.

        Factored out of the inline body-fill block so the memorial path
        can reuse the exact same geometry at lower alpha without
        duplicating the rotation / period math.
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
        # Forward-leaning stripes (``/``) - bottom-left to top-right.
        # Qt's positive rotation is clockwise in screen space, so +45°
        # tilts axis-aligned vertical bands into the ``/`` orientation.
        painter.rotate(45)
        stripe_w = 12  # canonical stripe width
        period = 24    # canonical stripe period
        half_diag = int(
            ((rect.width()) ** 2 + (rect.height()) ** 2) ** 0.5 / 2 + period
        )
        # Paint stripe B in every other band. Stripe A is the solid
        # base fill underneath, so we only need to paint B.
        x = -half_diag + stripe_w
        while x < half_diag:
            painter.fillRect(
                QtCore.QRectF(x, -half_diag, stripe_w, 2 * half_diag),
                stripe_b,
            )
            x += period
        painter.restore()

    def _paint_body_fill(self, painter) -> None:
        """Body fill - neutral / green / red tints render as a solid; yellow
        renders as a canonical hazard-zebra pattern. Border is drawn last so
        it always sits on top of every other paint layer.
        """
        st = self._state
        rect = self._body_rect_f()
        pressed = st.enabled

        # Solid base fill - for non-yellow tints, this is the only body
        # fill layer. For yellow, this is the under-stripe colour that
        # shows between the dark stripes.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QBrush(self._body_colour()))
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        # Yellow tint = canonical hazard zebra (full strength). The
        # base solid fill above already painted stripe A; the helper
        # paints stripe B on top in the canonical 12 px / 24 px period.
        if st.effective_tint() is Tint.YELLOW:
            self._paint_hazard_zebra(painter, rect, pressed=pressed, opacity=1.0)
        # Memorial zebra. When a plugin failed at load this session and
        # the user has since disabled it, keep a faint trace of the zebra
        # so the user scanning the grid can tell this disabled plugin had
        # a problem earlier. ``has_diagnostic`` is no longer set by
        # production callers (NSL no longer captures per-plugin load
        # failures), so this branch is effectively dead in production.
        # Opacity 0.4 is clear enough to scan-read while staying visually
        # subordinate to the full-strength zebra, so the enabled-failed
        # case stays loud.
        elif not st.enabled and st.has_diagnostic:
            self._paint_hazard_zebra(painter, rect, pressed=False, opacity=0.4)

        # Pressed (enabled): canonical recipe is
        #   inset 0 0 14px rgba(255,255,255,0.10)  ← ambient inner lift
        #   inset 0 2px 4px rgba(0,0,0,0.45)       ← top pressed indent
        #   inset 0 1px 0  rgba(0,0,0,0.55)        ← 1 px top sharp lip
        # The earlier draft only painted dark gradients (top + left) and
        # missed the white ambient lift entirely - net effect: green and
        # red tints came out 10-15 % darker than the canonical reference.
        # Now the ambient white inner glow lifts the whole body before
        # the top-only dark indent paints the pressed signal.
        painter.save()
        painter.setClipPath(self._inner_clip_path())
        painter.setPen(Qt.NoPen)
        if st.enabled:
            # The pressed-pill recipe simulates a depression "into" the
            # panel surface, lit from the TOP-LEFT. The rim around the
            # depression casts an inner shadow on the TOP and LEFT edges
            # of the body; the BOTTOM and RIGHT catch the light coming
            # through. Canonical CSS only specifies the TOP inset shadow
            # (``inset 0 2px 4px``) - we restore the LEFT inset shadow
            # too because at our Nuke-panel scale the top-only version
            # reads as a flat band rather than a true indent.
            #
            # Paint order:
            #   1. Ambient inner glow (white, ~10 % alpha all edges)
            #   2. Top inset shadow (gradient over 10 px)
            #   3. Left inset shadow (gradient over 8 px)
            #   4. Sharp 1 px top lip (the "rim line")

            # 1. Ambient inner glow - canonical
            # ``inset 0 0 14px rgba(255,255,255,0.10)``. Subtle lift
            # across all edges, NOT a depression cue - it's the
            # ambient light scattering inside the bowl.
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

            # 2. Top inset shadow - 14 px tall, peak alpha 130 at top
            # edge, fading to 0. The depression's top rim shadows the
            # inside-top of the body. Broader fade than the prior
            # 10 px ramp so the depression reads as a soft bowl
            # rather than a hard band.
            grad_t = QtGui.QLinearGradient(
                0, rect.top(), 0, rect.top() + 14
            )
            grad_t.setColorAt(0.0, QtGui.QColor(0, 0, 0, 130))
            grad_t.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            painter.setBrush(QtGui.QBrush(grad_t))
            painter.drawRect(
                QtCore.QRectF(rect.left(), rect.top(), rect.width(), 14)
            )

            # 3. Left inset shadow - 12 px wide, peak alpha 110. Now
            # closer to the top in intensity so the top-left corner
            # reads as the consistent shadow origin. The pre-bump
            # 8 px / alpha 70 ramp was too subtle - the indent read
            # as top-only rather than top-left.
            grad_l = QtGui.QLinearGradient(
                rect.left(), 0, rect.left() + 12, 0
            )
            grad_l.setColorAt(0.0, QtGui.QColor(0, 0, 0, 110))
            grad_l.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
            painter.setBrush(QtGui.QBrush(grad_l))
            painter.drawRect(
                QtCore.QRectF(rect.left(), rect.top(), 12, rect.height())
            )

            # 4. Sharp 1 px top lip - canonical
            # ``inset 0 1px 0 rgba(0,0,0,0.55)``. The crisp rim line.
            painter.fillRect(
                QtCore.QRectF(rect.left(), rect.top(), rect.width(), 1),
                QtGui.QColor(0, 0, 0, 140),
            )
        else:
            # Unpressed (raised) recipe - lit from top-left, mirrors the
            # pressed pill's top-left shadow at inverse colour. Canonical
            # CSS:
            #   inset 5px 4px 8px rgba(255,255,255,0.10)    top-left lift
            #   inset 2px 1px 1px rgba(255,255,255,0.05)    crisper edge
            #   inset -4px -3px 8px rgba(0,0,0,0.20)        bottom-right
            #
            # 1. Top highlight - 10 px, peak alpha 50 (lighter than the
            #    earlier 8 px / alpha 35 ramp so the lift reads at the
            #    same intensity scale as the broader inner shadow).
            grad_hi = QtGui.QLinearGradient(0, rect.top(), 0, rect.top() + 10)
            grad_hi.setColorAt(0.0, QtGui.QColor(255, 255, 255, 50))
            grad_hi.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(grad_hi))
            painter.drawRect(QtCore.QRectF(
                rect.left(), rect.top(), rect.width(), 10
            ))
            # 2. Left highlight - 12 px, peak alpha 55. Mirrors the
            #    pressed pill's left inner shadow at inverse colour so
            #    the raised vs pressed lighting reads from a consistent
            #    top-left source. Without this the raised pill lifted
            #    from the top only, which read flat against the
            #    pressed pill's clear top-left depression.
            grad_lh = QtGui.QLinearGradient(
                rect.left(), 0, rect.left() + 12, 0
            )
            grad_lh.setColorAt(0.0, QtGui.QColor(255, 255, 255, 55))
            grad_lh.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(grad_lh))
            painter.drawRect(QtCore.QRectF(
                rect.left(), rect.top(), 12, rect.height()
            ))
            # 3. Bottom shadow - subtle dark gradient fading up. Sits
            #    above the chip row on the title-area edge.
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
        """Hover-lighten on the pill body (name zone) - parity with
        the chip-row hover lighten.

        Paints a translucent white wash over the body's rounded
        silhouette when the cursor is over the body AND the body is
        actionable (``_zone_is_actionable("body")`` - excludes
        source-missing pills, panic-engaged USER_ADDED pills). Called
        in ``_paint`` between body fill and chip row, so the chip row
        overpaints its own strip; the visible result is a subtle
        lighten of the name zone only. The pill border + selection
        ring paint last and stay untouched.

        Brings the body to affordance parity with the chips so the
        click-to-toggle zone reads as "yes I am clickable." Alpha
        ``_BODY_HOVER_ALPHA`` (module constant) is intentionally low so
        the body's tint (neutral / green / red / yellow zebra) stays the
        primary signal.
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
        """Border treatment - canonical chrome.

        Drawn LAST so it sits on top of every previous paint layer.
        Order of operations:

        1. If the pill is loaded (enabled + LOADED status), paint the
           outer white glow halo. Two layers - a wide soft outer and
           a tight inner - match the canonical box-shadow pair.
        2. If selected, paint a 2 px Nuke-orange ring that **replaces**
           the border entirely. No default border underneath; selection
           is exclusive on the border zone.
        3. Else if divergent, paint a barber-pole stripe along the
           border path. Two colours (`#7a7a7a` / `#2a2a2a`) alternate
           at 5 px in a 135° pattern, replacing the solid border.
        4. Else paint the default engraved-dark border (`#1a1a1a`).
        5. If loaded, additionally paint a thin white 55%-opacity
           border on top of step 4 - the canonical "loaded glow"
           inner ring.
        """
        st = self._state
        rect = self._body_rect_f()
        is_loaded = self._is_loaded()

        # 1. Outer halo for any pressed pill. Locked invariant:
        #    pressed → glow, unpressed → shadow (the drop shadow is
        #    painted in ``_paint`` only when ``not enabled``, so a
        #    pressed pill is the only state that ever sees this halo).
        #    Selection paints on top as an orange ring; the glow stays
        #    under it so selected pills keep their pressed signal.
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

        # 3. Divergent - barber-pole replaces the solid border. When
        # the pill ALSO has a pending-restart diff, colour the stripes
        # with the committed-pending colour (lime / red) or the
        # uncommitted white. The pattern keeps signalling "diverges
        # from Global"; the colour adds the direction-of-change /
        # save-state signal that user-added pills get via solid border.
        if (
            st.source is Source.GLOBAL
            and st.diverges_from_global
        ):
            pending = self._pending_border_color()
            # Stripe colour by state. Dirty + pressed paints WHITE
            # stripes (``BORDER_PENDING_DIRTY``) so the barber-pole
            # reads against the white loaded-glow halo (grey stripes
            # under the glow and over the green body wash into a
            # patternless smear). Dirty + unpressed keeps the canonical
            # grey (no glow → grey reads cleanly as "diverged from
            # Global, no direction signal yet"). Committed states use
            # the lime/red pending colour.
            stripe_color = pending
            if pending is None and st.enabled:
                stripe_color = Palette.BORDER_PENDING_DIRTY
            # Coloured pending halo only fires for committed (lime /
            # red) state - the white-dirty case is already carried by
            # the loaded-glow halo painted in step 1; stacking a
            # second white halo on top is redundant.
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

        # 5. Loaded inner glow border - thin white 55% opacity layered
        #    on top of the default border. Paired with the outer halo
        #    painted in step 1 above.
        if is_loaded:
            glow_pen = QtGui.QPen(Palette.BORDER_LOADED_GLOW)
            glow_pen.setWidth(1)
            glow_pen.setCosmetic(True)
            painter.setPen(glow_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                rect, _BORDER_RADIUS, _BORDER_RADIUS
            )

        # 6. Pending-change border - layered last so it dominates over
        #    the default + loaded-glow. Only paints when the pill has a
        #    GREEN / RED tint (= a real pending-restart diff). The
        #    colour distinguishes uncommitted (white) from committed
        #    (lime / red), making "did I save my edits?" readable at a
        #    glance. YELLOW (problem) tints are deliberately skipped -
        #    the problem signal is its own thing and shouldn't be
        #    overloaded with the saved/unsaved axis. The divergent
        #    branch above also uses ``_pending_border_color`` so the
        #    Global striped path matches the user-added solid
        #    path.
        pending_color = self._pending_border_color()
        if pending_color is not None:
            # Outer halo first (bleeds beyond the body) - same
            # concentric-ring falloff as ``_paint_loaded_glow`` but
            # tinted with the pending colour. Solid stroke goes on top
            # so the rim stays crisp while the halo softens outward.
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

        Mirror of :meth:`_paint_loaded_glow` - concentric ring
        subtraction with quadratic falloff - but tinted with the
        pending colour passed in. Smaller outer radius than the
        loaded glow so the white loaded-glow halo (when both fire on
        a loaded pill that's pending-disable) still reads as the
        dominant signal at the rim.
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

        Returns ``None`` when no glow should paint. Mapping:

        * source_missing → red (won't load on restart - source gone)
        * panic_engaged + USER_ADDED (not source_missing) → None
          (panic strips user plugins, the saved-glow would lie)
        * not dirty + GREEN → lime (committed, will load on restart)
        * not dirty + RED   → red  (committed, will unload on restart)
        * dirty / NEUTRAL / YELLOW (without source_missing) → None

        Source-missing overrides everything below it: a plugin loaded
        this session whose source folder is gone reads as "definitely
        won't load next restart" regardless of the loadout's enable
        flag, so the red glow paints unconditionally. The YELLOW
        hazard body underneath communicates "source missing": the
        green check is there, the yellow signals missing, and red
        means it won't load.

        Panic-engaged is layered next: in panic mode, every
        USER_ADDED pill is dropped on next restart (panic drops user
        plugins, never Globals). The lime "will load on restart" glow
        would therefore lie, so it is suppressed and the pill drops
        back to the plain white loaded-glow look (white reads as "will
        not load", which is the truth in panic). GLOBAL pills
        keep their glow because Globals still load.

        The non-missing branch is the "locked-in by save" signal: the
        glow paints only for the saved state. The body tint already
        carries the direction-of-change signal; the glow adds only the
        committed/uncommitted layer.
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
        """Outer white halo for the loaded state - matches the canonical
        ``box-shadow: 0 0 12px rgba(255,255,255,0.22),
                      0 0 3px  rgba(255,255,255,0.28)``.

        Implemented as concentric ring subtraction with falloff so the
        bloom rolls off gently - Qt has no built-in box-shadow.
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
            # Quadratic falloff from peak → 0 across the layers.
            t = i / layers
            alpha = max(0, int(peak_alpha * (1.0 - t) ** 2))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, alpha))
            painter.drawPath(ring)
            prev_path = outer_path

    # Divergent border width - slightly wider than the default 1 px so
    # the barber-pole stripes are actually visible at the rendered scale.
    # CSS uses 1 px but at the design's native pixel size the alternation
    # reads as a speckle; at our Nuke-panel scale a 1 px ring effectively
    # disappears. 2 px keeps the stripes legible without dominating.
    _DIVERGENT_BORDER_WIDTH = 2

    def _paint_divergent_border(
        self,
        painter,
        rect,
        *,
        light_color=None,
        dark_color=None,
    ) -> None:
        """Barber-pole stripe replacing the solid border. Canonical:

            .pill--divergent {
              border-color: transparent;
              background:
                linear-gradient(body, body) padding-box,
                repeating-linear-gradient(135deg, #7a7a7a 0 5px,
                                                  #2a2a2a 5px 10px) border-box;
            }

        We paint stripes inside the border annulus (2 px wide here so
        the stripes read clearly - see ``_DIVERGENT_BORDER_WIDTH``).

        ``light_color`` / ``dark_color`` override the default grey
        stripe pair. Used when a Global pill diverges from Global
        AND has a pending-restart diff: the caller passes the
        committed-pending colour (lime / red) or the uncommitted white
        so the stripe pattern keeps signalling "diverges from Global"
        while the colour adds the direction-of-change signal. The stripe
        pattern is retained and tinted (e.g. red) rather than dropped; a
        user-added plugin with no divergence gets a plain solid border
        in that colour instead, with no stripes.
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
        # Base fill with the dark stripe colour, then paint the light
        # stripes on top in a 135° diagonal. Locked at 8 / 16 - light
        # bands 8 px wide, equal dark gap 8 px (period 16). Canonical
        # CSS convention was 5 / 10 (5 + 5) but at our rendering scale that
        # read as fine speckle; the doubled period reads as discrete
        # bands while preserving the 1 : 1 light-to-dark ratio.
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

        Full 5-state vocabulary - chip fill + glyph track the
        ``status_icon`` enum so the user can see at a glance whether
        the plugin is loaded (✓ green), pending a restart (spinner
        green), off (✕ red), failed (! red triangle), or missing
        (? yellow).

        ``glyph_kind`` is the rendering hint consumed by
        :meth:`_paint_status_icon`. Text glyphs are passed as strings;
        the SVG-rendered ones (caution triangle, spinner) are markers
        the painter knows how to draw via QPainterPath.
        """
        st = self._state
        ic = st.status_icon
        if ic is StatusIcon.LOADED:
            return (
                Palette.CHIP_STATUS_LOADED_FILL,
                Palette.CHIP_STATUS_LOADED_TEXT,
                "✓",  # ✓
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
        # EMPTY - disabled, never loaded. Red ✕ on dark-red chip.
        return (
            Palette.CHIP_STATUS_OFF_FILL,
            Palette.CHIP_STATUS_OFF_TEXT,
            "✕",  # ✕
        )

    def _chip_colours_diag(self):
        """(fill, text, glyph) for the log/diag chip - empty vs lit-on."""
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

        GUI is **always togglable** regardless of source - the earlier
        rule "Global: greyed and non-interactive" is
        retired. The user can flip GUI-only on either user-added OR
        Global pills; the per-pill toggle is the only path to set
        the bit. Visually the chip has two states only: off (grey) and
        on (lit purple); no Global-dim variant.

        Source-missing pills are click-blocked in ``mousePressEvent``
        but render with the same off/on visuals - the lock is
        behavioural only (disabled and not clickable, but not
        visually distinct).
        """
        st = self._state
        if st.gui_only:
            return (
                Palette.CHIP_GUI_ON_FILL,
                Palette.CHIP_GUI_ON_TEXT,
                "GUI",
            )
        if st.gui_pending_off:
            # GUI differs ON->OFF vs session: red text on the (grey) off
            # chip. This is the PENDING signal (shows saved or not, like
            # body tint); the committed signal is the red GUI-button
            # border added in _paint_bottom_row when ``gui_committed``.
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
        """Menu chip - neutral, always-clickable constant. Opens this
        Plugin's menu.py in the side panel; the panel handles the
        no-menu.py case, so the chip is always actionable and is not
        colour-coded by state in v1."""
        return (
            Palette.CHIP_MENU_FILL,
            Palette.CHIP_MENU_TEXT,
            "menu",
        )

    def _chip_colours_info(self):
        """Info chip - subtle off-white sticky-note constant. Always present,
        always actionable, never colour-coded by state."""
        return (
            Palette.CHIP_INFO_FILL,
            Palette.CHIP_INFO_TEXT,
            "info",
        )

    @staticmethod
    def _lighten(colour: "QtGui.QColor", amount: int = 22) -> "QtGui.QColor":
        """Return *colour* with each RGB channel pushed up by *amount*
        (clamped to 255). Alpha preserved. Used for chip hover lift.
        Default amount tuned so the hover state reads as "yes I am
        clickable" without being so loud it competes with selected /
        diff signals on the body.
        """
        return QtGui.QColor(
            min(255, colour.red() + amount),
            min(255, colour.green() + amount),
            min(255, colour.blue() + amount),
            colour.alpha(),
        )

    # Chips that respond to hover. ``status`` is intentionally absent -
    # the status chip reflects load truth and is not an interaction
    # target; lighting it on hover would imply clickability that the
    # chip does not have.
    _HOVER_CHIPS = frozenset({"menu", "gui", "info"})

    def _paint_bottom_row(self, painter) -> None:
        """Paint the full-width bottom-row chips with shape-clipped fills.

        Each chip is a rectangle filled with its canonical colour,
        clipped to the pill's rounded outer silhouette so the bottom
        corners follow the pill curve.

        Canonical chip dividers are **engraved** - a 1 px black inset
        paired with a 2 px white-5%-opacity inset highlight, so the
        seam between chips reads as a recessed groove. The top edge of
        the row gets a 1 px black inset for the same reason.
        """
        chips = {
            "status": self._chip_colours_status(),
            "gui": self._chip_colours_gui(),
            "menu": self._chip_colours_menu(),
            "info": self._chip_colours_info(),
        }

        painter.save()
        painter.setClipPath(self._inner_clip_path())

        # 1. Fill each chip's rectangle. Hover-lift applies to chips
        # that are *actionable* - gui / info always; diag only when a
        # diagnostic exists. Status is read-only and never lights.
        #
        # The info and diag chips ALSO light while their respective
        # ``_state.info_active`` /
        # ``_state.log_active`` is True (side panel currently shows
        # this plugin's content in the matching tab). Reads as the
        # same hover-lighten visual; user gets a persistent "this is
        # the pill the panel content belongs to" cue across hover and
        # tab-switch boundaries. The two flags are mutually exclusive
        # - Registry's pill-button handlers push them as a pair (info
        # set + log clear on info click; log set + info clear on log
        # click) so at most one pill+chip is lit at a time.
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

        # 2. Engraved dividers - top edge of row + between chips.
        body = self._body_rect()
        row_top_y = body.bottom() + 1 - _BOTTOM_ROW_H

        # Top edge of the row: 1 px dark inset.
        dark_pen = QtGui.QPen(Palette.CHIP_ROW_TOP_DARK)
        dark_pen.setWidth(1)
        dark_pen.setCosmetic(True)
        painter.setPen(dark_pen)
        painter.drawLine(body.left(), row_top_y, body.right(), row_top_y)

        # Between chips: paired dark + light line (engraved seam).
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

        # 3. Glyph / label per chip - text glyphs draw via the label
        #    painter; SVG markers (failed triangle, pending spinner)
        #    route through the icon painter.
        for name in _BOTTOM_ROW_ORDER:
            rect = self._button_rect(name)
            _fill, text_colour, glyph = chips[name]
            if name == "status" and glyph.startswith("svg:"):
                self._paint_status_icon(painter, rect, glyph)
            else:
                self._draw_chip_label(painter, rect, glyph, text_colour)

        # 4. Committed GUI-OFF accent - when a GUI ON->OFF change is
        #    SAVED (``gui_committed``), frame the GUI chip in the same
        #    bright red as the pill body's enabled->disabled border. This
        #    is the committed signal, the analogue of the lime/red
        #    saved-glow; the red chip TEXT (step 3) is the pending signal
        #    that shows while the edit is unsaved. On Custom (can't save)
        #    only the text shows - never this border.
        if self._state.gui_pending_off and self._state.gui_committed:
            self._paint_gui_pending_off_border(painter)

    def _paint_gui_pending_off_border(self, painter) -> None:
        """Stroke a bright-red frame inside the GUI chip rect.

        Uses ``Palette.BORDER_PENDING_DISABLE`` - the exact red the pill
        body draws when a Plugin flips enabled->disabled - so the GUI
        OFF diff reads as punchy as the pill-level signal. Inset by 1 px
        so the 2 px stroke sits fully inside the chip rather than
        straddling the engraved dividers on either side.
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
        """Render a canonical SVG status icon inside *rect*.

        Supported kinds:
          ``svg:failed`` - caution triangle (filled with stroke + dot)
          ``svg:spinner`` - 8-line spinner with decaying opacity

        Both are ported from ``_pill.css`` /
        ``Knowledge/docs/design/NSL_Design_System_New/preview/pill-lab.html``
        as QPainterPath drawings so the widget stays self-contained
        (no external SVG asset, no QtSvg dependency).
        """
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Both canonical SVGs use a 24×24 viewBox; map that into the
        # chip rect with a generous internal margin so the icon doesn't
        # crowd the chip edges.
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
        """Caution triangle - canonical SVG path centred at origin.

        Source (pill-lab.html):
            <path d="M12 2.5 L22.5 21 L1.5 21 Z"
                  fill="#cf8e8e" stroke="#cf8e8e"
                  stroke-width="1.5" stroke-linejoin="round"/>
            <line x1="12" y1="9.5" x2="12" y2="14.5"
                  stroke="#7a3030" stroke-width="2.6" stroke-linecap="round"/>
            <circle cx="12" cy="17.8" r="1.3" fill="#7a3030"/>

        viewBox is 24×24 with the centre at (12, 12); we translated the
        painter to that centre before calling, so subtract 12 from each
        coordinate.
        """
        # Triangle outline + fill.
        tri = QtGui.QPainterPath()
        tri.moveTo(0, -9.5)         # (12, 2.5)  - (12, 12) = (0, -9.5)
        tri.lineTo(10.5, 9)         # (22.5, 21) - (12, 12) = (10.5, 9)
        tri.lineTo(-10.5, 9)        # (1.5, 21)  - (12, 12) = (-10.5, 9)
        tri.closeSubpath()
        fill_pen = QtGui.QPen(Palette.STATUS_FAILED_RED)
        fill_pen.setWidthF(1.5)
        fill_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(fill_pen)
        painter.setBrush(QtGui.QBrush(Palette.STATUS_FAILED_RED))
        painter.drawPath(tri)
        # Inner exclamation line - (12, 9.5) → (12, 14.5) in viewBox.
        stroke_pen = QtGui.QPen(Palette.STATUS_FAILED_STROKE)
        stroke_pen.setWidthF(2.6)
        stroke_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(stroke_pen)
        painter.drawLine(QtCore.QPointF(0, -2.5), QtCore.QPointF(0, 2.5))
        # Dot at (12, 17.8), r=1.3 - (0, 5.8) after centring.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QBrush(Palette.STATUS_FAILED_STROKE))
        painter.drawEllipse(QtCore.QPointF(0, 5.8), 1.3, 1.3)

    def _paint_pending_spinner(self, painter) -> None:
        """Eight-line spinner - canonical opacities baked into the path.

        Source (pill-lab.html):
            8 lines around a 24×24 viewBox, stroke #e8e8e8, stroke-width
            2.4, stroke-linecap round, opacities falling from 1.0 to
            0.12 in 8 steps. Stationary (no animation by design -
            "movement on a notification reads as spam"; the same
            engraving rule applies to pill icons).
        """
        # Coordinates from the canonical SVG, centred on origin.
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

        Sizing follows the canonical CSS in ``_pill.css``:

          * Single-char glyphs (``✓ ✕ ?``) use the ``.gl`` class -
            ``font-size: 16px; font-weight: 900``. The ``✕`` on a
            disabled pill was previously rendering at 11 pt and read
            as undersized against the canonical reference.
          * Multi-char labels (``log`` / ``GUI`` / ``info``) use the
            base ``.pill__btn`` rule - ``font-size: 12px;
            font-weight: 700``.
        """
        painter.setPen(QtGui.QPen(colour))
        font = painter.font()
        if len(glyph) <= 1:
            # Status glyphs (✓ / ✕ / ?) - pin Helvetica: the multiplication-X
            # glyph (``✕``, U+2715) renders at ~60 % of the cap-height of
            # ``✓`` in Helvetica/Arial, so we boost its pixel size below.
            # Both knobs respect env-var overrides for size
            # exploration; defaults are the canonical-tuned 18 / 24.
            font.setFamily("Helvetica")
            font.setStyleHint(QtGui.QFont.Helvetica)
            small_glyphs = {"✕", "×", "✗"}
            font.setPixelSize(
                _CHIP_GLYPH_SMALL_PX if glyph in small_glyphs
                else _CHIP_GLYPH_PX
            )
            font.setWeight(QtGui.QFont.Black)
        else:
            # Text labels (GUI / Menu / Info) use the SAME font family as the
            # side-panel tabs - i.e. the inherited default UI font (the tabs'
            # QSS sets only weight + size, no family). We set the family
            # explicitly from the widget's own resolved font rather than
            # leaving painter.font() untouched, because the painter may carry
            # the Helvetica family set while drawing a sibling chip's status
            # glyph earlier in the same paint pass. 10 pt bold + 1 px tracking
            # opens the letters and disambiguates the I.
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
        # Plugin names are often path-style strings with mixed-width
        # characters (numbers, dashes, underscores, capital letters).
        # Monospace gives every glyph the same advance so the names line
        # up vertically across rows of the grid and avoid the "ragged
        # right" feel of a proportional bold sans. ``StyleHint.Monospace``
        # picks the platform's default monospace (SF Mono on macOS,
        # Consolas on Windows, DejaVu Sans Mono on Linux).
        font = QtGui.QFont()
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setFamily("monospace")  # fallback for setStyleHint
        font.setBold(True)
        font.setPointSize(_NAME_PT)
        painter.setFont(font)
        # Plugin name always reads at full brightness - the body fill and
        # the bottom inset shadow already carry the enabled/disabled signal.
        # Subtle hue shift on tinted bodies so the name picks up a hint of
        # the body colour without becoming colourful itself.
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
            # v1: nothing to draw - the absence of indicators IS the signal
            # ("None bucket" is signalled by empty row).
            return
        # v2 path - left for the wiring layer when Tags ships. Draw small
        # rectangles aligned under the centre.
        body = self._body_rect()
        # v2 tag indicators sit just above the bottom-row buttons, indented
        # from the rounded-corner edge.
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
            # Failure-category tooltip on status + diag chips.
            # Surfaces the same wording the
            # terminal `NSL Failed ✗` line uses ("Import Error",
            # "Syntax Error", ...) so the user can identify the
            # failure class without opening the Log tab. Gated on
            # ``has_diagnostic`` + ``failure_label`` together - both
            # are populated only for plugins that failed this session
            # with a captured traceback, so the tooltip never lies
            # about clickability or fires on healthy plugins.
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
                # Global-base tooltip variant. Click is blocked for
                # Global-base GUI chips in mousePressEvent, so the
                # user-toggle wording would lie; the locked tooltip
                # text is used instead so hover-info matches actual
                # interactivity.
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

