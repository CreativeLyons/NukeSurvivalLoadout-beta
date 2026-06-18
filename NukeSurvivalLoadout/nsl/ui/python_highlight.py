"""Monokai Python syntax highlighter for the side panel's Menu tab.

The Menu tab renders a Plugin's ``menu.py`` so artists can spot hotkey /
menu assignments at a glance. This module supplies a Sublime-Text-style
*Monokai* :class:`QSyntaxHighlighter` for that surface.

Design constraints (shared with the rest of ``nsl.ui``):

* Qt is reached **only** through :mod:`nsl.compat` - never import
  PySide2 / PySide6 directly.
* The module must stay importable on a build host **without** PySide. A
  :class:`QSyntaxHighlighter` subclass needs the Qt base class at
  *class-definition* time, so the class is built lazily inside
  :func:`attach_python_highlighter` rather than at module scope. The colour
  palette + token lists below are pure Python and import-safe.

Scope (v1): display-only highlighting. Editing / saving ``menu.py`` is a
later phase; nothing here writes to disk.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Monokai palette - the canonical Sublime Text "Monokai" colour vocabulary.
# Pure strings so this block is import-safe without Qt. Hex values are the
# widely-published Monokai set.
# ---------------------------------------------------------------------------

MONOKAI_BACKGROUND = "#272822"   # canonical Monokai canvas - NOT used: the
                                 # Menu view uses a neutral dark grey instead
                                 # (set in side_panel.py) so the code area
                                 # matches the panel rather than Monokai's
                                 # olive tint. Kept here for palette reference.
MONOKAI_FOREGROUND = "#F8F8F2"   # default text (identifiers, punctuation)
MONOKAI_COMMENT = "#75715E"      # comments - muted olive-grey
MONOKAI_STRING = "#E6DB74"       # string + bytes literals - soft yellow
MONOKAI_NUMBER = "#AE81FF"       # numeric literals - purple
MONOKAI_KEYWORD = "#F92672"      # keywords + operators - magenta/pink
MONOKAI_BUILTIN = "#66D9EF"      # builtins / types - cyan (italic)
MONOKAI_DEFNAME = "#A6E22E"      # function / class names + decorators - green
MONOKAI_SELF = "#FD971F"         # ``self`` / ``cls`` - orange

# Python keywords (control flow + declarations). ``self`` / ``cls`` are
# coloured separately (orange) rather than as keywords.
_KEYWORDS = (
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield", "match", "case",
)

# A representative slice of builtins + the names artists most often see in a
# Nuke ``menu.py`` (``nuke`` is the dominant module reference). Not
# exhaustive - this is cosmetic highlighting, not a linter.
_BUILTINS = (
    "abs", "all", "any", "bool", "bytes", "callable", "dict", "dir",
    "enumerate", "filter", "float", "format", "frozenset", "getattr",
    "hasattr", "hash", "id", "input", "int", "isinstance", "issubclass",
    "iter", "len", "list", "map", "max", "min", "next", "object", "open",
    "ord", "print", "property", "range", "repr", "reversed", "round", "set",
    "setattr", "sorted", "staticmethod", "classmethod", "str", "sum",
    "super", "tuple", "type", "vars", "zip", "nuke", "nukescripts",
)


# ---------------------------------------------------------------------------
# Lazy class construction - keeps the module headless-importable.
# ---------------------------------------------------------------------------

_HIGHLIGHTER_CLASS = None  # cached after first build


def _build_highlighter_class():
    """Define (once) and return the :class:`QSyntaxHighlighter` subclass.

    Imports Qt via :mod:`nsl.compat` on first call so the module
    itself stays importable without PySide.
    """
    global _HIGHLIGHTER_CLASS
    if _HIGHLIGHTER_CLASS is not None:
        return _HIGHLIGHTER_CLASS

    from nsl import compat

    QtGui = compat.QtGui
    QtCore = compat.QtCore
    QRegularExpression = QtCore.QRegularExpression

    def _fmt(colour: str, *, italic: bool = False, bold: bool = False):
        f = QtGui.QTextCharFormat()
        f.setForeground(QtGui.QColor(colour))
        if italic:
            f.setFontItalic(True)
        if bold:
            # ``QFont.Bold`` is the weight enum; setFontWeight wants the int.
            weight = getattr(QtGui.QFont, "Bold", 75)
            f.setFontWeight(weight)
        return f

    class MonokaiPythonHighlighter(QtGui.QSyntaxHighlighter):
        """Monokai-themed Python highlighter.

        Applies, per text block, in this order (later passes override
        earlier ones on overlapping ranges, which is the behaviour we
        want - a keyword *inside* a string must read as string):

        1. Code tokens - decorators, ``def`` / ``class`` names, ``self`` /
           ``cls``, builtins, keywords, numbers.
        2. Single-line string literals.
        3. Comments (``#`` … EOL), skipped when the ``#`` sits inside a
           single/double-quoted string on the same line.
        4. Multi-line triple-quoted strings (docstrings) - applied last so
           they win over everything inside their span, across block
           boundaries via block state.
        """

        # Block-state bits for the two triple-quote delimiters. -1 is Qt's
        # "no special state" marker for the previous block.
        _STATE_NONE = 0
        _STATE_IN_TRIPLE_DOUBLE = 1
        _STATE_IN_TRIPLE_SINGLE = 2

        def __init__(self, document):
            super().__init__(document)

            self._fmt_keyword = _fmt(MONOKAI_KEYWORD)
            self._fmt_builtin = _fmt(MONOKAI_BUILTIN, italic=True)
            self._fmt_number = _fmt(MONOKAI_NUMBER)
            self._fmt_string = _fmt(MONOKAI_STRING)
            self._fmt_comment = _fmt(MONOKAI_COMMENT, italic=True)
            self._fmt_defname = _fmt(MONOKAI_DEFNAME)
            self._fmt_decorator = _fmt(MONOKAI_DEFNAME)
            self._fmt_self = _fmt(MONOKAI_SELF, italic=True)

            kw = "|".join(_KEYWORDS)
            bi = "|".join(_BUILTINS)

            # (regex, format, capture-group). Group 0 = whole match unless a
            # capture is needed (def/class name after the keyword).
            self._rules = [
                # Decorators: @something.dotted
                (QRegularExpression(r"@[A-Za-z_][\w.]*"),
                 self._fmt_decorator, 0),
                # Names following def / class.
                (QRegularExpression(r"\b(?:def|class)\s+([A-Za-z_]\w*)"),
                 self._fmt_defname, 1),
                # self / cls.
                (QRegularExpression(r"\b(?:self|cls)\b"), self._fmt_self, 0),
                # Builtins (before keywords is fine - disjoint sets).
                (QRegularExpression(rf"\b(?:{bi})\b"), self._fmt_builtin, 0),
                # Keywords.
                (QRegularExpression(rf"\b(?:{kw})\b"), self._fmt_keyword, 0),
                # Numbers - int / float / hex, with optional sign handled by
                # the surrounding context (we don't try to be exact).
                (QRegularExpression(
                    r"\b(?:0[xX][0-9A-Fa-f]+|\d+\.?\d*(?:[eE][+-]?\d+)?)\b"),
                 self._fmt_number, 0),
            ]

            # Single-line strings - double- and single-quoted, with escape
            # handling so ``"a\"b"`` stays one string.
            self._string_rules = [
                QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"'),
                QRegularExpression(r"'[^'\\]*(?:\\.[^'\\]*)*'"),
            ]

            self._triple_double = QRegularExpression(r'"""')
            self._triple_single = QRegularExpression(r"'''")

        # -- helpers ---------------------------------------------------

        def _apply_rule(self, text, regex, fmt, group):
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                start = m.capturedStart(group)
                length = m.capturedLength(group)
                if start >= 0 and length > 0:
                    self.setFormat(start, length, fmt)

        @staticmethod
        def _comment_index(text) -> int:
            """Index of the first ``#`` that starts a comment, or -1.

            Tracks single/double quote state so a ``#`` inside a string is
            not mistaken for a comment. Triple-quote spans are handled by
            the multiline pass which overrides this anyway.
            """
            in_single = False
            in_double = False
            escaped = False
            for i, ch in enumerate(text):
                if escaped:
                    escaped = False
                    continue
                if ch == "\\" and (in_single or in_double):
                    escaped = True
                    continue
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif ch == "#" and not in_single and not in_double:
                    return i
            return -1

        def _match_multiline(self, text, regex, state_flag) -> bool:
            """Format triple-quoted spans, threading state across blocks.

            Returns True when the block ends *inside* an open triple-quote
            (so the next block starts in-string).
            """
            start = 0
            if self.previousBlockState() == state_flag:
                # Continuing an open string from the prior block.
                add = 0
            else:
                m = regex.match(text, 0)
                if not m.hasMatch():
                    return False
                start = m.capturedStart()
                add = m.capturedLength()

            while start >= 0:
                m = regex.match(text, start + add)
                if m.hasMatch():
                    end = m.capturedStart()
                    length = end - start + m.capturedLength()
                    self.setFormat(start, length, self._fmt_string)
                    # Look for another opening delimiter after this close.
                    nxt = regex.match(text, start + length)
                    if not nxt.hasMatch():
                        self.setCurrentBlockState(self._STATE_NONE)
                        return False
                    start = nxt.capturedStart()
                    add = nxt.capturedLength()
                else:
                    # No closing delimiter on this block - string continues.
                    self.setCurrentBlockState(state_flag)
                    self.setFormat(start, len(text) - start, self._fmt_string)
                    return True
            return False

        # -- the entry point -------------------------------------------

        def highlightBlock(self, text):  # noqa: N802 - Qt override name.
            try:
                # 1. Code tokens.
                for regex, fmt, group in self._rules:
                    self._apply_rule(text, regex, fmt, group)

                # 2. Single-line strings.
                for regex in self._string_rules:
                    self._apply_rule(text, regex, self._fmt_string, 0)

                # 3. Comments (quote-aware).
                idx = self._comment_index(text)
                if idx >= 0:
                    self.setFormat(idx, len(text) - idx, self._fmt_comment)

                # 4. Multi-line triple-quoted strings - override everything
                # in their span. Default state is "no open string".
                self.setCurrentBlockState(self._STATE_NONE)
                if not self._match_multiline(
                    text, self._triple_double, self._STATE_IN_TRIPLE_DOUBLE
                ):
                    self._match_multiline(
                        text, self._triple_single, self._STATE_IN_TRIPLE_SINGLE
                    )
            except Exception:
                # A highlighter must never raise into Qt's paint path.
                pass

    _HIGHLIGHTER_CLASS = MonokaiPythonHighlighter
    return _HIGHLIGHTER_CLASS


def attach_python_highlighter(document) -> Optional[object]:
    """Build a Monokai Python highlighter bound to *document* and return it.

    The caller must keep the returned object alive (store it on the owning
    widget) - Qt does not take ownership of a Python-side highlighter
    reference. Returns ``None`` only if the document is falsy.
    """
    if document is None:
        return None
    cls = _build_highlighter_class()
    return cls(document)


# ---------------------------------------------------------------------------
# Line-numbered code view - the surface the Menu tab renders into.
#
# A read-only ``QPlainTextEdit`` subclass with a left line-number gutter,
# ported from Qt's canonical "Code Editor" example. Built lazily for the same
# headless-import reason as the highlighter (a QWidget subclass needs the Qt
# base class at class-definition time). The gutter + canvas colours are
# neutral dark to match the panel - NOT Monokai's olive; only the syntax
# *token* colours (from the highlighter) are Monokai.
# ---------------------------------------------------------------------------

_CODE_VIEW_CLASS = None  # cached after first build


def _build_code_view_class():
    """Define (once) and return the line-numbered ``QPlainTextEdit`` subclass."""
    global _CODE_VIEW_CLASS
    if _CODE_VIEW_CLASS is not None:
        return _CODE_VIEW_CLASS

    from nsl import compat

    QtWidgets = compat.QtWidgets
    QtGui = compat.QtGui
    QtCore = compat.QtCore

    class _LineNumberArea(QtWidgets.QWidget):
        """Thin gutter painted by its parent CodeView."""

        def __init__(self, editor):
            super().__init__(editor)
            self._editor = editor

        def sizeHint(self):  # noqa: N802 - Qt override.
            return QtCore.QSize(self._editor.line_number_area_width(), 0)

        def paintEvent(self, event):  # noqa: N802 - Qt override.
            self._editor._paint_line_numbers(event)

    class CodeView(QtWidgets.QPlainTextEdit):
        """Read-only Python source view with a left line-number gutter.

        Monokai token colours come from the attached highlighter; the canvas
        and gutter are neutral dark (set here + by the owner's stylesheet) so
        the code area matches the panel rather than Monokai's olive canvas.
        """

        GUTTER_BG = "#1c1c1c"   # gutter strip - a touch darker than the canvas
        GUTTER_FG = "#6f6f6f"   # line-number digits - muted grey

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setReadOnly(True)
            # Soft-wrap long lines at the widget width so code stays visible
            # without horizontal scrolling. Wrap mid-word (WrapAnywhere) since
            # source lines often have no spaces to break on.
            self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
            self.setWordWrapMode(QtGui.QTextOption.WrapAnywhere)
            self._lna = _LineNumberArea(self)
            self.blockCountChanged.connect(self._on_block_count_changed)
            self.updateRequest.connect(self._on_update_request)
            self._update_gutter_width()

        # -- gutter sizing / sync --------------------------------------

        def line_number_area_width(self) -> int:
            digits = len(str(max(1, self.blockCount())))
            return 14 + self.fontMetrics().horizontalAdvance("9") * digits

        def _update_gutter_width(self) -> None:
            self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

        def _on_block_count_changed(self, _new_count) -> None:
            self._update_gutter_width()

        def _on_update_request(self, rect, dy) -> None:
            if dy:
                self._lna.scroll(0, dy)
            else:
                self._lna.update(0, rect.y(), self._lna.width(), rect.height())
            if rect.contains(self.viewport().rect()):
                self._update_gutter_width()

        def resizeEvent(self, event):  # noqa: N802 - Qt override.
            super().resizeEvent(event)
            cr = self.contentsRect()
            self._lna.setGeometry(
                cr.left(), cr.top(), self.line_number_area_width(), cr.height()
            )

        def setFont(self, font):  # noqa: N802 - keep gutter width in sync.
            super().setFont(font)
            self._lna.setFont(font)
            self._update_gutter_width()

        # -- gutter paint ----------------------------------------------

        def _paint_line_numbers(self, event) -> None:
            painter = QtGui.QPainter(self._lna)
            painter.fillRect(event.rect(), QtGui.QColor(self.GUTTER_BG))
            painter.setFont(self.font())
            painter.setPen(QtGui.QColor(self.GUTTER_FG))
            block = self.firstVisibleBlock()
            num = block.blockNumber()
            offset = self.contentOffset()
            top = self.blockBoundingGeometry(block).translated(offset).top()
            bottom = top + self.blockBoundingRect(block).height()
            height = self.fontMetrics().height()
            right_pad = 6
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    painter.drawText(
                        0, int(top), self._lna.width() - right_pad, height,
                        int(QtCore.Qt.AlignRight), str(num + 1),
                    )
                block = block.next()
                top = bottom
                bottom = top + self.blockBoundingRect(block).height()
                num += 1

    _CODE_VIEW_CLASS = CodeView
    return _CODE_VIEW_CLASS


def make_code_view(parent=None):
    """Return a read-only, line-numbered Python code view (``QPlainTextEdit``).

    Built lazily so this module imports without PySide. Raises if Qt isn't
    available or can't build the subclass - callers that must stay
    headless-safe should guard the call and fall back to a plain view.
    """
    cls = _build_code_view_class()
    return cls(parent)
