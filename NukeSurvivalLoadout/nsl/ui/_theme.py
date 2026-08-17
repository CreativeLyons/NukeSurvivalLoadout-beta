"""NSL's Nuke-look colour palette and token definitions.

Widgets import these tokens in their paint code instead of hard-coding
hexes. ``apply_nuke_dark_palette(app)`` is for rendering outside Nuke.
Inside Nuke the panel goes through Nuke's own style chain afterwards, so
the call is harmless there.
"""

from __future__ import annotations

from nsl import compat


# Palette anchors only. Pill body tints, the panic red and the divergent
# stripe still live in widget paint code.
NUKE_ORANGE_HEX = "#ee9626"
NUKE_ORANGE_RGB = (238, 150, 38)
NUKE_ORANGE_RGB_DISABLED = (90, 74, 50)  # `accent/nuke-orange/disabled`
# Row highlight for the active Loadout in the dropdown.
ACTIVE_ROW_BLUE_RGB = (86, 160, 244)
ACTIVE_ROW_BLUE_ALPHA = 71  # ≈ 0.28 * 255

_NUKE_WINDOW_BG = "#393939"
_NUKE_BASE_BG = "#262626"
_NUKE_ALT_BG = "#2e2e2e"
_NUKE_BUTTON_BG = "#525252"
_NUKE_BUTTON_TEXT = "#dcdcdc"
_NUKE_TEXT = "#c8c8c8"
_NUKE_DISABLED_TEXT = "#7a7a7a"
_NUKE_PLACEHOLDER_TEXT = "#8a8a8a"  # readable against the #262626 Base
_NUKE_BRIGHT_TEXT = "#ffffff"
_NUKE_LIGHT = "#5a5a5a"
_NUKE_MID = "#3f3f3f"
_NUKE_MID_LIGHT = "#4a4a4a"
_NUKE_DARK = "#202020"
_NUKE_SHADOW = "#1a1a1a"
_NUKE_HIGHLIGHT = NUKE_ORANGE_HEX
_NUKE_HIGHLIGHTED_TEXT = "#1a1a1a"
_NUKE_LINK = "#56a0f4"
_NUKE_TOOLTIP_BG = "#3a3a3a"


def _q_color(hex_str):
    return compat.QtGui.QColor(hex_str)


def build_nuke_dark_palette():
    """Return a ``QPalette`` tuned to Nuke 16's chrome.

    Call :func:`apply_nuke_dark_palette` instead, so the style and the
    palette stay in sync.
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
    # PlaceholderText only exists as a role in Qt 5.12 and later. Without
    # it a placeholder string renders near-black on the dark ``Base``.
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

    palette.setColor(qt.QPalette.Disabled, qt.QPalette.Text, _q_color(_NUKE_DISABLED_TEXT))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.WindowText, _q_color(_NUKE_DISABLED_TEXT))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.ButtonText, _q_color(_NUKE_DISABLED_TEXT))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.Highlight, _q_color("#5a4a32"))
    palette.setColor(qt.QPalette.Disabled, qt.QPalette.HighlightedText, _q_color(_NUKE_DISABLED_TEXT))

    return palette


def apply_nuke_dark_palette(app):
    """Switch ``app`` to Fusion style and the Nuke-tuned dark palette."""
    if app is None:
        return
    app.setStyle("Fusion")
    app.setPalette(build_nuke_dark_palette())
    # Nuke's UI font is about 10 pt against Qt's 13 pt default. Without
    # this the panel reads too large even with the palette right.
    font = compat.QtGui.QFont()
    font.setPointSize(10)
    app.setFont(font)
