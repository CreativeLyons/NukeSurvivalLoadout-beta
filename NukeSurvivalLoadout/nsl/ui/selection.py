"""Shared selection model for the Loadout Panel.

This module owns the canonical selection set. :class:`PluginsGrid` keeps
a mirror of it for cell paint only.

* The set lives in memory for the session. Nothing is written to disk.
* A selected Plugin stays selected when a later search or invert hides it.
* Bulk actions and the toolbar count use the whole set, not the
  visible-after-filter subset.
* Each mutation call emits ``changed`` once, so the action layer pushes
  one undo entry per call.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence

from nsl import compat

QtCore = compat.QtCore

__all__ = ["SelectionModel", "wire_selection"]


# ---------------------------------------------------------------------------
# SelectionModel
# ---------------------------------------------------------------------------


class SelectionModel(QtCore.QObject):
    """Canonical per-session selection set for the Loadout Panel.

    The set is unordered inside. :meth:`selected_keys` and the
    ``changed`` payload both return a sorted list, so the toolbar count,
    the grid paint, and the tests see a stable order.

    ``changed(list)`` fires only on a real change. Replacing the set
    with the same members does not emit.

    Every mutation method returns True when the set changed.
    """

    changed = QtCore.Signal(list)

    def __init__(self, parent: Optional["QtCore.QObject"] = None) -> None:
        super().__init__(parent)
        self._selected: set = set()

    # -- read accessors -------------------------------------------------

    def selected_keys(self) -> List[str]:
        """Return the current selection as a deterministic sorted list."""
        return sorted(self._selected)

    def size(self) -> int:
        """Return the number of selected keys."""
        return len(self._selected)

    def __len__(self) -> int:
        return len(self._selected)

    def __contains__(self, key: object) -> bool:
        return key in self._selected

    def contains(self, key: str) -> bool:
        """Return True if ``key`` is selected. Same result as ``in``."""
        return key in self._selected

    # -- mutations ------------------------------------------------------

    def add(self, key: str) -> bool:
        """Add a single key. Returns True if the set changed."""
        if key in self._selected:
            return False
        self._selected.add(key)
        self._emit()
        return True

    def remove(self, key: str) -> bool:
        """Remove a single key. Returns True if the set changed."""
        if key not in self._selected:
            return False
        self._selected.discard(key)
        self._emit()
        return True

    def toggle(self, key: str) -> bool:
        """Flip one key in or out of the set. This is ctrl-click."""
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        self._emit()
        return True

    def replace(self, keys: Iterable[str]) -> bool:
        """Replace the selection with *keys*.

        Marquee release, plain ``Select filtered``, and folder
        ``Select`` all replace instead of adding.
        """
        new = set(keys)
        if new == self._selected:
            return False
        self._selected = new
        self._emit()
        return True

    def add_many(self, keys: Iterable[str]) -> bool:
        """Union *keys* into the current selection.

        Used by shift-click ``Select filtered``, which builds a
        selection across several filter passes.
        """
        new = self._selected | set(keys)
        if new == self._selected:
            return False
        self._selected = new
        self._emit()
        return True

    def clear(self) -> bool:
        """Empty the selection. Returns True if anything was selected."""
        if not self._selected:
            return False
        self._selected.clear()
        self._emit()
        return True

    # -- internal -------------------------------------------------------

    def _emit(self) -> None:
        self.changed.emit(sorted(self._selected))


# ---------------------------------------------------------------------------
# Filter helpers - local copy of the search-strip's matching rule
# ---------------------------------------------------------------------------

# ``nsl.ui.search_tags.match_query`` is copied here instead of imported,
# because the import can make a Qt-import cycle. The rule is the same.
# Case-insensitive substring on the Plugin name, empty query matches all.


def _matches_query(query: str, plugin_name: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    return q in plugin_name.lower()


def _visible_after_filter(
    keys: Sequence[str],
    query: str,
    invert: bool,
) -> List[str]:
    """Return *keys* filtered by ``query``, with optional invert.

    Tag chips are deferred to v2, so only the query and the invert
    toggle apply here.
    """
    matched = {k for k in keys if _matches_query(query, k)}
    if invert:
        matched = set(keys) - matched
    # Preserve the input order so the result is stable.
    return [k for k in keys if k in matched]


# ---------------------------------------------------------------------------
# wire_selection - the single public entry point
# ---------------------------------------------------------------------------


def wire_selection(
    panel,
    *,
    folder_keys_for_path: Optional[Callable[[str], Sequence[str]]] = None,
) -> None:
    """Install a shared :class:`SelectionModel` on *panel* and bridge widgets.

    Called from ``panel._wire_signals()``. This module must not edit
    ``panel.py`` directly.

    The bridge:

    * Creates ``panel.selection_model`` unless the caller installed one.
    * grid ``selection_changed(list)`` to ``model.replace``.
    * search/tags ``select_filtered_requested(add)`` to ``replace``, or
      ``add_many`` on shift-click. The visible set comes from
      ``grid.keys()``, ``query()`` and ``is_inverted()``.
    * grid_toolbar ``clear_selection_requested`` to ``model.clear``.
    * ``model.changed`` back to the grid for paint and to the toolbar
      for the count.

    Folder Select is not wired here. ``events.py`` owns it, so
    ``folder_keys_for_path`` is currently unused.

    Calling this twice is a no-op.
    """
    if getattr(panel, "_selection_wired", False):
        return

    model: SelectionModel = getattr(panel, "selection_model", None)
    if model is None:
        model = SelectionModel(panel)
        panel.selection_model = model

    grid = getattr(panel, "grid", None)
    search_tags = getattr(panel, "search_tags", None)
    grid_toolbar = getattr(panel, "grid_toolbar", None)
    folder_card = getattr(panel, "folder_card", None)

    # ---- grid → model (marquee + ctrl-click) ----------------------------
    # The grid emits the full selection on every change. The flag below
    # blocks the return path, so model to grid does not bounce back.
    _bridge_state = {"applying_from_model": False}

    def _on_grid_selection_changed(keys: list) -> None:
        if _bridge_state["applying_from_model"]:
            return
        model.replace(list(keys))

    if grid is not None and hasattr(grid, "selection_changed"):
        grid.selection_changed.connect(_on_grid_selection_changed)

    # ---- search/tags → model (Select filtered, shift = additive) --------
    def _on_select_filtered(add_to_selection: bool) -> None:
        if grid is None:
            return
        keys = list(grid.keys()) if hasattr(grid, "keys") else []
        query = search_tags.query() if search_tags is not None else ""
        invert = (
            search_tags.is_inverted() if search_tags is not None else False
        )
        visible = _visible_after_filter(keys, query, invert)
        if add_to_selection:
            model.add_many(visible)
        else:
            model.replace(visible)

    if search_tags is not None and hasattr(search_tags, "select_filtered_requested"):
        search_tags.select_filtered_requested.connect(_on_select_filtered)

    # ---- folder_card → model (per-folder Select) ----
    # Do not connect this to ``folder_card.select_requested``. It
    # replaces, and ``events.py`` owns folder Select and adds instead.
    def _on_folder_select(path: str) -> None:  # noqa: F841
        if grid is None:
            return
        all_keys = list(grid.keys()) if hasattr(grid, "keys") else []
        query = search_tags.query() if search_tags is not None else ""
        invert = (
            search_tags.is_inverted() if search_tags is not None else False
        )
        visible = _visible_after_filter(all_keys, query, invert)
        if folder_keys_for_path is not None:
            folder_keys = set(folder_keys_for_path(path) or ())
            picked = [k for k in visible if k in folder_keys]
        else:
            picked = list(visible)
        model.replace(picked)

    # ---- grid_toolbar → model (Clear Selection) -------------------------
    def _on_clear_selection() -> None:
        model.clear()

    if grid_toolbar is not None and hasattr(
        grid_toolbar, "clear_selection_requested"
    ):
        grid_toolbar.clear_selection_requested.connect(_on_clear_selection)

    # ---- model → grid + toolbar (paint + count) -------------------------
    def _on_model_changed(keys: list) -> None:
        if grid is not None and hasattr(grid, "select_keys"):
            _bridge_state["applying_from_model"] = True
            try:
                grid.select_keys(list(keys), emit=False)
            finally:
                _bridge_state["applying_from_model"] = False
        # ``gui_only_count`` is left None, so the toolbar mirrors the
        # full count. Downstream wiring sets a provenance-aware count.
        if grid_toolbar is not None and hasattr(grid_toolbar, "set_counts"):
            grid_toolbar.set_counts(len(keys))

    model.changed.connect(_on_model_changed)

    panel._selection_wired = True
