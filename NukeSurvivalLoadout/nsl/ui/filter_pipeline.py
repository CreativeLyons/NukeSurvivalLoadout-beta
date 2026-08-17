"""NSL Loadout Panel filter pipeline.

Composes search, tags, and the folder-eye toggle into one visible-key
set, and applies it to ``panel.grid`` through
:func:`wire_filter_pipeline`. The selection stays on the grid.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence, Set

from nsl.ui.search_tags import match_query

__all__ = [
    "tag_layer_v1",
    "eye_layer",
    "search_layer",
    "compose_visible_keys",
    "bulk_target_keys",
    "FilterState",
    "FilterPipeline",
    "wire_filter_pipeline",
]


# ---------------------------------------------------------------------------
# Pure-Python decision layers (no Qt)
# ---------------------------------------------------------------------------


def search_layer(query: Optional[str], plugin_name: str) -> bool:
    """Case-insensitive substring match against the Plugin Name."""
    return match_query(query or "", plugin_name)


def tag_layer_v1(
    plugin_name: str,
    selected_tags: Optional[Iterable[str]] = None,
) -> bool:
    """v1 tag layer. Always returns ``True``.

    There is no user tag state to check yet. ``selected_tags`` is the
    seam for a real membership check.
    """
    del plugin_name, selected_tags
    return True


def eye_layer(plugin_name: str, hidden_keys: Iterable[str]) -> bool:
    """True when *plugin_name* is not in *hidden_keys*.

    The wiring layer turns "this Plugins Folder is hidden" into a set of
    Plugin keys, so this predicate knows nothing about folders.
    """
    return plugin_name not in set(hidden_keys)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def compose_visible_keys(
    all_keys: Sequence[str],
    *,
    query: str = "",
    invert: bool = False,
    hidden_keys: Iterable[str] = (),
    selected_tags: Optional[Iterable[str]] = None,
) -> List[str]:
    """Return the ordered list of pill keys currently visible.

    The rule is ``(search AND tag)``, then Invert, then AND eye.

    Invert flips the search × tag set only. Eye is applied after the
    flip, so an eye-hidden Plugin is never visible in either state.
    Order of *all_keys* is preserved.
    """
    hidden_set = set(hidden_keys)
    base = [
        k for k in all_keys
        if search_layer(query, k) and tag_layer_v1(k, selected_tags)
    ]
    base_set = set(base)
    if invert:
        inverted = [k for k in all_keys if k not in base_set]
        candidates = inverted
    else:
        candidates = base
    return [k for k in candidates if eye_layer(k, hidden_set)]


# ---------------------------------------------------------------------------
# Bulk-operation target set - full selection, not visible subset
# ---------------------------------------------------------------------------


def bulk_target_keys(full_selection: Iterable[str]) -> List[str]:
    """Return the keys a bulk action should affect.

    Always the full selection, never the visible subset. Twelve pills
    selected with four visible still disables all twelve.
    """
    return list(full_selection)


# ---------------------------------------------------------------------------
# FilterState - the per-session, panel-local store
# ---------------------------------------------------------------------------


class FilterState:
    """Per-session, panel-local filter inputs.

    Nothing here is persisted, and a Loadout switch does not clear it.
    """

    __slots__ = ("query", "invert", "hidden_keys", "_listeners")

    def __init__(self) -> None:
        self.query: str = ""
        self.invert: bool = False
        self.hidden_keys: Set[str] = set()
        self._listeners: List[Callable[[List[str]], None]] = []

    # -- state mutators --------------------------------------------------

    def set_query(self, query: str) -> None:
        self.query = query or ""

    def set_invert(self, invert: bool) -> None:
        self.invert = bool(invert)

    def set_hidden_keys(self, hidden_keys: Iterable[str]) -> None:
        """Replace the Plugin keys hidden by the folder-card eyes."""
        self.hidden_keys = set(hidden_keys)

    # -- reactivity ------------------------------------------------------

    def add_listener(
        self, listener: Callable[[List[str]], None]
    ) -> None:
        """Register a callable to run after every state change.

        Listeners run in registration order and receive the new
        visible-keys list.
        """
        self._listeners.append(listener)

    def visible_keys(self, all_keys: Sequence[str]) -> List[str]:
        """Return the current visible-keys list against *all_keys*."""
        return compose_visible_keys(
            all_keys,
            query=self.query,
            invert=self.invert,
            hidden_keys=self.hidden_keys,
        )

    def notify(self, all_keys: Sequence[str]) -> List[str]:
        """Recompute visible keys, fire every listener, return the list."""
        visible = self.visible_keys(all_keys)
        for listener in list(self._listeners):
            listener(visible)
        return visible


# ---------------------------------------------------------------------------
# FilterPipeline - the high-level binding object
# ---------------------------------------------------------------------------


class FilterPipeline:
    """Holds the filter state, resolves the visible-keys set, applies it.

    Pure-Python unless ``apply_visibility`` is wired.

    * ``all_keys_getter`` runs on every recompute, so a grid rebuild
      after a Loadout switch is picked up on its own.
    * ``key_to_folder`` returns ``None`` for an unknown origin. Those
      keys are never hidden by the eye layer.
    * ``apply_visibility`` paints the new set onto the grid. Pass
      ``None`` and read ``last_visible`` instead.
    """

    def __init__(
        self,
        all_keys_getter: Callable[[], Sequence[str]],
        key_to_folder: Callable[[str], Optional[str]],
        apply_visibility: Optional[Callable[[List[str]], None]] = None,
    ) -> None:
        self._all_keys_getter = all_keys_getter
        self._key_to_folder = key_to_folder
        self._apply = apply_visibility
        self.state = FilterState()
        # folder_path -> True when the eye is open.
        self._folder_visible: dict[str, bool] = {}
        # ``last_filtered`` is pre-sort, ``last_visible`` is post-sort.
        self.last_filtered: List[str] = []
        self.last_visible: List[str] = []
        # ``None`` means no sort, for panels built without a sort toolbar.
        self._sort_mode = None
        self._sort_state_lookup: Optional[Callable[[str], object]] = None
        # Aligned with ``last_visible``. ``None`` means no divider above
        # that pill, and an alphabetical sort gives ``None`` throughout.
        self.last_group_labels: List[Optional[str]] = []
        self.state.add_listener(self._on_state_recompute)

    # ----- public mutators (called by the Qt wiring helper) ------------

    def on_filter_changed(self, query: str, invert: bool) -> List[str]:
        """Slot for ``SearchTagsStrip.filter_changed(str, bool)``."""
        self.state.set_query(query)
        self.state.set_invert(invert)
        return self._recompute_and_apply()

    def on_folder_visibility_changed(
        self, folder_path: str, visible: bool
    ) -> List[str]:
        """Slot for ``FolderCard.visibility_changed(str, bool)``.

        ``visible=False`` means the eye is closed, so this folder's
        Plugins are hidden in the grid.
        """
        self._folder_visible[folder_path] = bool(visible)
        self._recompute_hidden_keys()
        return self._recompute_and_apply()

    def reset_folder_visibility(self, folder_paths: Iterable[str]) -> List[str]:
        """Seed the folder visibility map from a fresh folder list.

        Called at wire time so every configured folder is known before
        the first eye toggle. New folders default to visible.
        """
        # Keep any hide the user already made on a path that survives.
        seeded = {p: self._folder_visible.get(p, True) for p in folder_paths}
        self._folder_visible = seeded
        self._recompute_hidden_keys()
        return self._recompute_and_apply()

    # ----- sort composition --------------------------------------------

    def set_sort_mode(self, mode) -> List[str]:
        """Slot for the grid-toolbar sort dropdown's ``sort_mode_changed``.

        Dataflow is ``(master → filter → sort → set_keys)``. A sort
        change re-runs the same recompute as a filter change, so the
        active filter is preserved.
        """
        self._sort_mode = mode
        return self._recompute_and_apply()

    def set_sort_state_lookup(self, lookup: Optional[Callable[[str], object]]) -> List[str]:
        """Replace the per-key state lookup used by sort axes."""
        self._sort_state_lookup = lookup
        return self._recompute_and_apply()

    def filter_visible_keys(self) -> List[str]:
        """Return the filter-only output, before sort.

        Installed on the panel as ``panel.filter_visible_keys`` for
        legacy consumers. The grid itself follows ``last_visible``.
        """
        return list(self.last_filtered)

    # ----- bulk-op contract (full selection, never visible) ------------

    def bulk_target_keys(self, full_selection: Iterable[str]) -> List[str]:
        """Bulk ops act on the **full selection**.

        Wraps the module-level function so callers skip a second import.
        """
        return bulk_target_keys(full_selection)

    # ----- read accessors ---------------------------------------------

    def is_folder_visible(self, folder_path: str) -> bool:
        """Return True if *folder_path*'s eye is open (default True)."""
        return bool(self._folder_visible.get(folder_path, True))

    def hidden_folder_paths(self) -> List[str]:
        """Return the list of folder paths currently hidden by eye."""
        return [p for p, v in self._folder_visible.items() if not v]

    # ----- internals ---------------------------------------------------

    def _recompute_hidden_keys(self) -> None:
        """Resolve the set of Plugin keys hidden by the current eye state."""
        hidden_folders = {
            p for p, visible in self._folder_visible.items() if not visible
        }
        if not hidden_folders:
            self.state.set_hidden_keys(set())
            return
        keys = self._all_keys_getter()
        hidden_keys = set()
        for key in keys:
            folder = self._key_to_folder(key)
            if folder is not None and folder in hidden_folders:
                hidden_keys.add(key)
        self.state.set_hidden_keys(hidden_keys)

    def _recompute_and_apply(self) -> List[str]:
        keys = list(self._all_keys_getter())
        return self.state.notify(keys)

    def _on_state_recompute(self, visible: List[str]) -> None:
        self.last_filtered = list(visible)
        if self._sort_mode is not None:
            # Lazy import keeps this module Qt-light.
            from nsl.ui.sort import (  # noqa: PLC0415
                SortableState,
                group_label_for_state,
                sort_keys,
            )

            lookup = self._sort_state_lookup or (lambda k: SortableState(name=k))
            sorted_keys = sort_keys(self.last_filtered, self._sort_mode, lookup)
            self.last_visible = list(sorted_keys)
            # One lookup per visible key. Alphabetical modes return
            # ``None`` for every pill, so the grid draws no dividers.
            self.last_group_labels = [
                group_label_for_state(lookup(k), self._sort_mode)
                for k in self.last_visible
            ]
        else:
            self.last_visible = list(self.last_filtered)
            # An empty list tells the grid to use a uniform layout.
            self.last_group_labels = []
        if self._apply is not None:
            self._apply(self.last_visible)


# ---------------------------------------------------------------------------
# Qt wiring helper - the stitch point
# ---------------------------------------------------------------------------


def wire_filter_pipeline(
    panel,
    *,
    key_to_folder: Optional[Callable[[str], Optional[str]]] = None,
) -> FilterPipeline:
    """Stitch a :class:`FilterPipeline` into *panel*'s existing widgets.

    Connects ``panel.search_tags.filter_changed`` and
    ``panel.folder_card.visibility_changed``, and installs an
    ``apply_visibility`` callable that rebuilds the grid in the new key
    order.

    ``key_to_folder`` maps a Plugin key to its source folder path.
    Without it every key resolves to ``None``, the eye layer hides
    nothing, and the helper still runs against a minimal fixture.

    Returns the pipeline so callers can read state or add listeners.
    """
    if key_to_folder is None:
        # No key has an origin folder, so the eye layer is inert.
        def key_to_folder(_key: str) -> Optional[str]:
            return None

    def _all_keys() -> Sequence[str]:
        # The MASTER list, not ``panel.grid.keys()``, which holds the
        # filtered subset once ``_apply_visibility`` rebuilds the grid.
        master = getattr(panel, "_all_plugin_keys", None)
        if master is not None:
            return list(master)
        return panel.grid.keys()

    def _apply_visibility(visible: List[str]) -> None:
        # ``visible`` is already filtered and sorted, so it is the grid
        # order. Capture the selection before ``set_keys`` and restore
        # it after. ``set_keys`` clears ``grid._selected`` and emits
        # ``selection_changed([])``, which wipes the bulk-action model.
        try:
            preserved_selection = list(panel.grid.selected_keys())
        except Exception:  # noqa: BLE001 - selection capture must not break the pipeline
            preserved_selection = []
        if panel.grid.set_keys(list(visible)):
            # A real rebuild happened. Lazy import keeps this Qt-light.
            from nsl.ui.wiring.events import rewire_grid_pills
            rewire_grid_pills(panel)
            # ``set_keys`` tore down the pills and cells. The factory
            # re-mints the PillStates, but the cell diff tint and the
            # panic dim come only from this push. A registry refresh
            # does the same push itself, so skip it there.
            if not getattr(panel, "_in_registry_refresh", False):
                push = getattr(panel, "_set_pills_from_registry", None)
                if callable(push):
                    try:
                        push()
                    except Exception:  # noqa: BLE001 - state push must not break the pipeline
                        pass
        if preserved_selection:
            try:
                panel.grid.select_keys(preserved_selection)
            except Exception:  # noqa: BLE001 - restore must not break the pipeline
                pass
        # After ``set_keys`` so the labels line up with the new key
        # order. An empty list clears any stale divider.
        labels = list(pipeline.last_group_labels)
        set_labels = getattr(panel.grid, "set_group_labels", None)
        if callable(set_labels):
            try:
                set_labels(labels)
            except Exception:  # noqa: BLE001 - dividers must not break the filter path
                pass
        # Refresh the counter strip so its chips follow the visible set
        # as the user types. The selection handler does a full refresh.
        try:
            selected_keys_fn = getattr(panel.grid, "selected_keys", None)
            sel = selected_keys_fn() if selected_keys_fn else []
            panel._on_grid_selection_changed(sel)
        except Exception:  # noqa: BLE001 - counter refresh must not break the filter path
            pass

    pipeline = FilterPipeline(
        all_keys_getter=_all_keys,
        key_to_folder=key_to_folder,
        apply_visibility=_apply_visibility,
    )

    # Seed the folder map before the first user interaction.
    folder_card = getattr(panel, "folder_card", None)
    if folder_card is not None and hasattr(folder_card, "entries"):
        pipeline.reset_folder_visibility(
            [e.path for e in folder_card.entries() if e.visible]
            + [e.path for e in folder_card.entries() if not e.visible]
        )
        # The seed above only records which folders exist. Apply each
        # entry's real visible flag now.
        for entry in folder_card.entries():
            pipeline._folder_visible[entry.path] = bool(entry.visible)
        pipeline._recompute_hidden_keys()
        pipeline._recompute_and_apply()

    # Tolerate missing signals so the helper runs against a stub panel.
    search_tags = getattr(panel, "search_tags", None)
    if search_tags is not None and hasattr(search_tags, "filter_changed"):
        search_tags.filter_changed.connect(pipeline.on_filter_changed)

    if folder_card is not None and hasattr(folder_card, "visibility_changed"):
        folder_card.visibility_changed.connect(
            pipeline.on_folder_visibility_changed
        )

    # Stable attribute, so the other wiring helpers find the pipeline.
    panel.filter_pipeline = pipeline
    # ``wire_sort``'s legacy fallback reads this. The sort path itself
    # routes through the pipeline when it sees ``panel.filter_pipeline``.
    panel.filter_visible_keys = pipeline.filter_visible_keys
    return pipeline
