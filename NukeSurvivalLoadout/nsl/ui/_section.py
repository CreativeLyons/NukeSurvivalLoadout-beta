"""Shared section-bounding chrome.

:class:`SectionBox` paints a 1 px rounded border around any child
widget. The panel wraps each region in one.

The border is painted in :meth:`paintEvent`, not in QSS. QSS on a parent
widget drops child ``QPushButton`` siblings out of native style sizing.
See :class:`HybridTextButton` in :mod:`nsl.ui._buttons`.
"""

from __future__ import annotations

from nsl import compat


class SectionBox(compat.QtWidgets.QFrame):
    """Wraps a child widget in a 1 px rounded panel border.

    Change ``BORDER_COLOR`` or ``RADIUS`` to restyle every section.
    """

    BORDER_COLOR = compat.QtGui.QColor("#2f2f2f")
    RADIUS = 4
    INNER_PADDING = 2  # px gap between the border and the child

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
            # Inset by 0.5 so the 1 px line sits on the pixel grid.
            # Without it the line straddles the edge and looks faded.
            rect = compat.QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        finally:
            painter.end()
