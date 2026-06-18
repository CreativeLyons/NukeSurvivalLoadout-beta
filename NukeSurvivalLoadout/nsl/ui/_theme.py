"""NSL's Nuke-look colour palette and token definitions.

Defines the colour tokens that give NSL its Nuke-native appearance. Widgets
import these tokens directly in their paint code (e.g. ``NUKE_ORANGE_RGB``,
``NUKE_ORANGE_HEX``, ``ACTIVE_ROW_BLUE_*``) instead of hard-coding hexes, so
the palette is the single source of truth for the package's accent colours.

``apply_nuke_dark_palette(app)`` is for rendering outside Nuke (offline /
headless): it switches the ``QApplication`` to ``Fusion`` and installs a dark
palette tuned to Nuke 16's chrome, so the UI looks Nuke-shaped even without a
host. Inside real Nuke the docked panel goes through Nuke's own style chain
afterwards, so the same call is harmless there.
"""

from __future__ import annotations

from nsl import compat


# Colour vocabulary derived from observation of Nuke 16's panel chrome.
# These are the palette anchors only - pill body tints, panic button red, and
# divergence purple still live in widget paint code.

# Public role exports. Widgets that need a token in paint code (e.g. pill
# selection outline, loadout dropdown hover) import these instead of
# hard-coding hexes - keeps the palette the single source of truth across
# the package.
NUKE_ORANGE_HEX = "#ee9626"
NUKE_ORANGE_RGB = (238, 150, 38)  # = NUKE_ORANGE_HEX as (R, G, B)
NUKE_ORANGE_RGB_DISABLED = (90, 74, 50)  # `accent/nuke-orange/disabled`
# Loadout dropdown "you are here" anchor (translucent baby blue per JSX).
ACTIVE_ROW_BLUE_RGB = (86, 160, 244)
ACTIVE_ROW_BLUE_ALPHA = 71  # ≈ 0.28 * 255

_NUKE_WINDOW_BG = "#393939"
_NUKE_BASE_BG = "#262626"          # input fields, list backgrounds
_NUKE_ALT_BG = "#2e2e2e"           # alternating rows
_NUKE_BUTTON_BG = "#525252"
_NUKE_BUTTON_TEXT = "#dcdcdc"
_NUKE_TEXT = "#c8c8c8"
_NUKE_DISABLED_TEXT = "#7a7a7a"
_NUKE_PLACEHOLDER_TEXT = "#8a8a8a"  # mid-grey - readable against #262626 Base
_NUKE_BRIGHT_TEXT = "#ffffff"
_NUKE_LIGHT = "#5a5a5a"             # widget light edge
_NUKE_MID = "#3f3f3f"
_NUKE_MID_LIGHT = "#4a4a4a"
_NUKE_DARK = "#202020"
_NUKE_SHADOW = "#1a1a1a"
_NUKE_HIGHLIGHT = NUKE_ORANGE_HEX   # Nuke's signature yellow-orange selection
_NUKE_HIGHLIGHTED_TEXT = "#1a1a1a"
_NUKE_LINK = "#56a0f4"
_NUKE_TOOLTIP_BG = "#3a3a3a"


def _q_color(hex_str):
    return compat.QtGui.QColor(hex_str)


def build_nuke_dark_palette():
    """Return a ``QPalette`` tuned to Nuke 16's chrome.

    Useful in isolation - production callers should use
    :func:`apply_nuke_dark_palette` so style + palette stay in sync.
    """
    qt = compat.QtGui
    palette = qt.QPalette()

    palette.setColor(qt.QPalette.Window, _q_color(_NUKE_WINDOW_BG))
    palette.setColor(qt.QPalette.WindowText, _q_color(_NUKE_TEXT))
    palette.setColor(qt.QPalette.Base, _q_color(_NUKE_BASE_BG))
    palette.setColor(qt.QPalette.AlternateBase, _q_color(_NUKE_ALT_BG))
    palette.setColor(qt.QPalette.ToolTipBase, _q_color(_NUKE_TOOLTIP_BG))
    palette.setColor(qt.QPalette.ToolTipText, _q_color(_NUKE_TEXT))
    palette.setColor(qt.QPalette.Text, _q_color(_NUKE_TEXT))
    # PlaceholderText is a separate palette role in Qt 5.12+/6 - required so
    # ``QLineEdit`` placeholder strings (e.g. "Search plugins…") render
    # against the dark ``Base`` background instead of the default near-black.
    if hasattr(qt.QPalette, "PlaceholderText"):
        palette.setColor(qt.QPalette.PlaceholderText, _q_color(_NUKE_PLACEHOLDER_TEXT))
    palette.setColor(qt.QPalette.Button, _q_color(_NUKE_BUTTON_BG))
    palette.setColor(qt.QPalette.ButtonText, _q_color(_NUKE_BUTTON_TEXT))
    palette.setColor(qt.QPalette.BrightText, _q_color(_NUKE_BRIGHT_TEXT))
    palette.setColor(qt.QPalette.Highlight, _q_color(_NUKE_HIGHLIGHT))
    palette.setColor(qt.QPalette.HighlightedText, _q_color(_NUKE_HIGHLIGHTED_TEXT))
    palette.setColor(qt.QPalette.Link, _q_color(_NUKE_LINK))
    palette.setColor(qt.QPalette.LinkVisited, _q_color(_NUKE_LINK))

    palette.setColor(qt.QPalette.Light, _q_color(_NUKE_LIGHT))
    palette.setColor(qt.QPalette.Midlight, _q_color(_NUKE_MID_LIGHT))
    palette.setColor(qt.QPalette.Mid, _q_color(_NUKE_MID))
    palette.setColor(qt.QPalette.Dark, _q_color(_NUKE_DARK))
    palette.setColor(qt.QPalette.Shadow, _q_color(_NUKE_SHADOW))

    # Disabled-group overrides - Nuke greys text and dims buttons in this group.
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.Text, _q_color(_NUKE_DISABLED_TEXT))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.WindowText, _q_color(_NUKE_DISABLED_TEXT))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.ButtonText, _q_color(_NUKE_DISABLED_TEXT))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.Highlight, _q_color("#5a4a32"))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.HighlightedText, _q_color(_NUKE_DISABLED_TEXT))

    return palette


def apply_nuke_dark_palette(app):
    """Switch ``app`` to Fusion style + Nuke-tuned dark palette.

    Safe to call once when rendering outside Nuke (offline / headless).
    Inside real Nuke the docked panel goes through Nuke's own style chain
    anyway, so this call is a no-op visually.
    """
    if app is None:
        return
    app.setStyle("Fusion")
    app.setPalette(build_nuke_dark_palette())
    # Nuke panels read denser than Qt's default - its UI font sits around
    # 10 pt vs Qt's 13 pt. Without this the UI reads as "chunky Qt"
    # even with the palette right. Inside real Nuke this is a no-op
    # because Nuke's own style re-applies its font afterwards.
    font = compat.QtGui.QFont()
    font.setPointSize(10)
    app.setFont(font)
