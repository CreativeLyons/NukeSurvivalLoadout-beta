"""Shared selection model for the Loadout Panel.

This module owns the canonical selection set for the panel. The
:class:`PluginsGrid` keeps an internal mirror for cell paint only; the
search/tags strip, the folder card's *Select* button, and the grid
toolbar's *Clear Selection* button all mutate the *same*
:class:`SelectionModel` via the bridge installed by
:func:`wire_selection`.

Key behavior:

* **Per-session** - never persisted to disk. The model is plain
  in-memory state; no JSON, no autosave.
* **Selection survives filter changes** - a Plugin selected via marquee
  / ctrl-click / ``Select filtered`` / folder ``Select`` stays selected
  even if a subsequent search or invert hides it from view.
* **Bulk actions act on the full selection** - the grid toolbar's count
  reflects ``model.size()``, not the visible-after-filter subset.
* This model emits exactly one ``changed`` signal per mutation call, so
  the action layer needs only one undo-stack push per call.
* ``Select filtered`` reads the visible set from the grid keys + the
  search query + the Invert toggle.

The pure model is Qt-free (imported via :mod:`NukeSurvivalLoadout.compat`);
a thin Qt subclass adds the ``changed`` signal for widget consumption.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence

from NukeSurvivalLoadout import compat

QtCore = compat.QtCore

__all__ = ["SelectionModel", "wire_selection"]


# ---------------------------------------------------------------------------
# SelectionModel - Qt-bridged but core logic is pure
# ---------------------------------------------------------------------------


class SelectionModel(QtCore.QObject):
    """Canonical per-session selection set for the Loadout Panel.

    The set is unordered internally; the ``changed`` signal and the
    :meth:`selected_keys` accessor return a deterministic *sorted* list
    so downstream consumers (toolbar count, grid paint, snapshot
    assertions) see a stable order.

    Signals:

    * ``changed(list)`` - emitted whenever the selection set changes.
      Payload is the new ``selected_keys()`` (sorted list of strings).
      Identity replacements (``replace`` with the same set) do **not**
      emit; only genuine state changes do.

    Mutation methods all return ``True`` if the set changed, ``False``
    if the call was a no-op. The return value is convenient for tests
    and for any action-layer logic that wants to skip a redundant
    undo-stack push.
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

    def __len__(self) -> int:  # convenience for ``len(model)``
        return len(self._selected)

    def __contains__(self, key: object) -> bool:
        return key in self._selected

    def contains(self, key: str) -> bool:
        """Explicit membership predicate (mirrors ``in`` for clarity)."""
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
        """Ctrl-click semantics - flip a single key's membership."""
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        self._emit()
        return True

    def replace(self, keys: Iterable[str]) -> bool:
        """Replace the selection with *keys* (set semantics).

        Used by marquee release, plain ``Select filtered``, and folder
        ``Select`` (each of those replaces the prior selection rather
        than adding to it, by design).
        """
        new = set(keys)
        if new == self._selected:
            return False
        self._selected = new
        self._emit()
        return True

    def add_many(self, keys: Iterable[str]) -> bool:
        """Union *keys* into the current selection.

        Used by shift-click ``Select filtered`` (the power-user shortcut
        for building a selection across multiple filter passes).
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
#
# We re-implement the search-match predicate inline so this module can
# compute the visible-after-filter set without importing
# :mod:`NukeSurvivalLoadout.ui.search_tags` (which would create a Qt-import
# cycle in some environments where the strip is not yet built). The
# behaviour matches ``NukeSurvivalLoadout.ui.search_tags.match_query``
# exactly: case-insensitive substring match against the Plugin name.
# Empty query matches all.


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
    """Return *keys* filtered by ``query`` (with optional invert).

    Mirrors the search/tags strip's filter semantics. Tag chips are
    deferred to v2, so this v1 implementation considers only the search
    query and the invert toggle. The strip's filter pipeline composes
    via AND across these axes.
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

    Called from ``panel._wire_signals()``; this module must not edit
    ``panel.py`` directly.

    The bridge:

    * Creates ``panel.selection_model`` if absent (callers can install a
      pre-built model first to share across panels).
    * Routes grid ``selection_changed(list)`` → ``model.replace(list)``
      (marquee + ctrl-click in the grid drive the canonical model).
    * Routes search/tags ``select_filtered_requested(add_to_selection)``
      → compute the visible-after-filter set from
      ``panel.grid.keys()`` + the strip's current ``query()`` and
      ``is_inverted()``; ``replace`` it (plain click) or ``add_many``
      (shift-click).
    * Routes folder_card ``select_requested(path)`` → ask
      ``folder_keys_for_path(path)`` for the Plugin keys belonging to
      that folder (downstream data layer owns the mapping), intersect
      with the visible-after-filter set, then ``replace`` it. The folder
      ``Select`` button **replaces** the selection rather than adding to
      it.
    * Routes grid_toolbar ``clear_selection_requested`` → ``model.clear()``.
    * Pushes every ``model.changed`` payload back to the grid (paint)
      and the grid_toolbar (count), passing ``emit=False`` to the grid
      so the bridge does not loop.

    ``folder_keys_for_path`` may be ``None``; in that case the
    folder-row Select bridge falls back to "select every visible key in
    the grid" so the affordance still functions end-to-end at the panel
    composition layer. Production wiring installs a real mapping driven
    by the discovered-Plugins data.

    Idempotent - calling twice does not double-connect; the second call
    is a no-op.
    """
    # Idempotency guard. The orchestrator's stitch is conservative and
    # may end up calling this twice in some refactor scenarios; we keep
    # the bridge stable in that case.
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
    # The grid keeps an internal mirror set for cell paint and emits the
    # full selection list on every mutation. We treat its emission as the
    # canonical "replace" source. ``select_keys(..., emit=False)`` is used
    # on the return path (model → grid) so we do not bounce.
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
    # The events.py wiring layer is the sole owner of folder
    # Select/Deselect, including the additive-across-folders semantic
    # ("folder list select icon wins; subsequent folder clicks add").
    # This helper must NOT connect to ``folder_card.select_requested``:
    # its filter-aware ``model.replace`` would compete with events.py and
    # collapse the additive behaviour into a replace. It is kept as a
    # backwards-compatible internal but is intentionally left unconnected.
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
        # Push to grid for paint, suppressing its re-emission so we don't
        # bounce back into _on_grid_selection_changed.
        if grid is not None and hasattr(grid, "select_keys"):
            _bridge_state["applying_from_model"] = True
            try:
                grid.select_keys(list(keys), emit=False)
            finally:
                _bridge_state["applying_from_model"] = False
        # Push the **full** size to the toolbar. The toolbar's count is
        # the full selection size, not the visible-after-filter subset.
        # The gui_only_count argument is left None so the toolbar mirrors
        # the full count; downstream wiring overrides this with a
        # provenance-aware count once the Global filter is in place.
        if grid_toolbar is not None and hasattr(grid_toolbar, "set_counts"):
            grid_toolbar.set_counts(len(keys))

    model.changed.connect(_on_model_changed)

    panel._selection_wired = True
