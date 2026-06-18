"""NSL Empty State widget - the panel's welcome surface when no Plugins are visible.

The widget displays a short welcome message, centred horizontally and
vertically, in the panel's default font sized one step larger than body
text. It carries NO buttons, links, or affordances of its own - the panel's
existing ``Add Plugins Folder`` button (in the Plugins Folder management
card) is the one and only path forward.

The same message applies regardless of *why* no Plugins are visible - the
panel deliberately does not distinguish between "Global layer isn't
configured" and "user hasn't added folders yet".
"""

from __future__ import annotations


from nsl import compat


# ---------------------------------------------------------------------------
# Welcome wording
# ---------------------------------------------------------------------------
#
# The grid string acknowledges what the area is without competing with the
# folder card's primary CTA (which names the "Add Plugins Folder" button
# verbatim and is pulled forward visually by the button's nuke-orange
# first-run border).
#
# ``WELCOME_LINE_2`` is retained as an empty string so existing
# imports keep resolving.

WELCOME_LINE_1 = "Plugins will appear here once a Folder is added."
WELCOME_LINE_2 = ""

# Single-line message - what the QLabel actually displays. The
# trailing newline is dropped when LINE_2 is empty so the message
# renders without a phantom blank line under it.
WELCOME_TEXT = WELCOME_LINE_1


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class EmptyStateWidget(compat.QtWidgets.QWidget):
    """Centred welcome message shown when no Plugins are visible.

    Composition: a single ``QLabel`` carrying the welcome wording, parked
    inside a layout that centres it horizontally and vertically across
    whatever space the widget is given. No buttons, links, or affordances
    are added.

    Sizing: the label's font is the widget's default font with the point
    size bumped one step larger than body text. "One step" is implemented
    as ``current_point_size + 1`` so the widget inherits whatever body
    size the host Qt style (Nuke's stylesheet, or any other host
    environment) has settled on.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # ------------------------------------------------------------------
        # Label - the only child widget.
        # ------------------------------------------------------------------
        self._label = compat.QtWidgets.QLabel(WELCOME_TEXT, self)
        self._label.setObjectName("nsl_empty_state_label")
        # No word wrap - the welcome text controls its own line breaks
        # with hard newlines. WordWrap=True caused Qt to wrap the second
        # line at the label's narrower-than-widget intrinsic width and
        # then under-report its needed height, clipping the bottom of
        # the message.
        self._label.setWordWrap(False)
        self._label.setAlignment(compat.QtCore.Qt.AlignCenter)
        # Muted-bright text - matches the design system's `#dcdcdc`
        # secondary copy weight. Reads as informational, not primary.
        self._label.setStyleSheet(
            "QLabel#nsl_empty_state_label {"
            "  color: #c8c8c8;"
            "  background: transparent;"
            "}"
        )

        # One step larger than body text. Inherit the widget's font so the
        # host stylesheet drives the family/weight, and bump the point size
        # by +1.
        font = self.font()
        point_size = font.pointSize()
        if point_size <= 0:
            # Some platforms / styles return -1 for pointSize when only a
            # pixel size is set. Fall back to a reasonable default before
            # bumping so the size knob still works.
            point_size = 10
        font.setPointSize(point_size + 1)
        self._label.setFont(font)

        # ------------------------------------------------------------------
        # Layout - centre the label horizontally + vertically.
        # ------------------------------------------------------------------
        # Centred horizontally and vertically in the grid's empty area.
        # A QVBoxLayout with stretches above and below the label achieves
        # vertical centring; the label's AlignCenter handles the horizontal
        # axis.
        layout = compat.QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(self._label, 0, compat.QtCore.Qt.AlignHCenter)
        layout.addStretch(1)

        self.setLayout(layout)

    # ----------------------------------------------------------------------
    # Convenience accessors - handy for introspection and tooling.
    # ----------------------------------------------------------------------

    def message_text(self) -> str:
        """Return the label's current text, exactly as displayed."""
        return self._label.text()

    def message_label(self) -> "compat.QtWidgets.QLabel":
        """Return the internal label widget (for layout / font inspection)."""
        return self._label

