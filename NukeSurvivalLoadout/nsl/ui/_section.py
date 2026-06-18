"""Shared section-bounding chrome.

:class:`SectionBox` wraps any child widget and paints a 1 px rounded
bounding line around it. Used at the panel-composition level to give
every region (top toolbar, folder card, loadout strip, side panel,
search/tags, grid, banner) a clearly visible boundary so the user can
see at a glance where each section starts and ends.

The border is painted in a custom :meth:`paintEvent` - **not** via QSS
 - because applying QSS to a parent widget pollutes child rendering
(``QPushButton`` siblings drop out of native style sizing). See the
:class:`HybridTextButton` lessons in :mod:`nsl.ui._buttons` for the
prior incident this guards against.
"""

from __future__ import annotations

from nsl import compat


class SectionBox(compat.QtWidgets.QFrame):
    """Wraps a child widget in a 1 px rounded panel border.

    Construction injects the child into a margin-zero layout so the
    border hugs the child exactly. The colour and radius are class
    constants - change them in one place and every section updates.
    """

    BORDER_COLOR = compat.QtGui.QColor("#2f2f2f")
    RADIUS = 4
    INNER_PADDING = 2  # px breathing room between border and child content

    def __init__(
        self,
        child: "compat.QtWidgets.QWidget",
        parent: "compat.QtWidgets.QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(compat.QtWidgets.QFrame.NoFrame)
        layout = compat.QtWidgets.QVBoxLayout(self)
        p = self.INNER_PADDING
        layout.setContentsMargins(p, p, p, p)
        layout.setSpacing(0)
        layout.addWidget(child)
        self._child = child

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = compat.QtGui.QPainter(self)
        try:
            painter.setRenderHint(compat.QtGui.QPainter.Antialiasing, True)
            pen = compat.QtGui.QPen(self.BORDER_COLOR, 1)
            painter.setPen(pen)
            painter.setBrush(compat.QtCore.Qt.NoBrush)
            # Inset by 0.5 so the 1 px line draws inside our rect cleanly
            # (centred on the pixel grid). Without this the line straddles
            # the edge and renders at half-opacity on alternating pixels.
            rect = compat.QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        finally:
            painter.end()
