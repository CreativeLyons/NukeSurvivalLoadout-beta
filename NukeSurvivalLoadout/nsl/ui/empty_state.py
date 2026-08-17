"""NSL Empty State widget - the welcome surface when no Plugins show.

A centred message and nothing else. The ``Add Plugins Folder`` button on
the folder card is the only way forward, so add no buttons or links here.
The wording is the same whatever the reason, so the panel does not tell a
missing Global layer apart from a user who has added no folders.
"""

from __future__ import annotations


from nsl import compat


# ---------------------------------------------------------------------------
# Welcome wording
# ---------------------------------------------------------------------------

# ``WELCOME_LINE_2`` is empty and nothing in the package reads it. It
# stays so an outside import still resolves.
WELCOME_LINE_1 = "Plugins will appear here once a Folder is added."
WELCOME_LINE_2 = ""

WELCOME_TEXT = WELCOME_LINE_1


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class EmptyStateWidget(compat.QtWidgets.QWidget):
    """Centred welcome message shown when no Plugins are visible.

    One ``QLabel`` in a layout that centres it. The font is the widget's
    own font at one point larger, so it follows whatever body size the
    host Qt style has settled on.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._label = compat.QtWidgets.QLabel(WELCOME_TEXT, self)
        self._label.setObjectName("nsl_empty_state_label")
        # Word wrap makes Qt under-report the label height and clip the
        # bottom of the message.
        self._label.setWordWrap(False)
        self._label.setAlignment(compat.QtCore.Qt.AlignCenter)
        self._label.setStyleSheet(
            "QLabel#nsl_empty_state_label {"
            "  color: #c8c8c8;"
            "  background: transparent;"
            "}"
        )

        font = self.font()
        point_size = font.pointSize()
        if point_size <= 0:
            # pointSize returns -1 when the font carries a pixel size only.
            point_size = 10
        font.setPointSize(point_size + 1)
        self._label.setFont(font)

        layout = compat.QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(self._label, 0, compat.QtCore.Qt.AlignHCenter)
        layout.addStretch(1)

        self.setLayout(layout)

    def message_text(self) -> str:
        """Return the label's current text."""
        return self._label.text()

    def message_label(self) -> "compat.QtWidgets.QLabel":
        """Return the internal label widget."""
        return self._label

