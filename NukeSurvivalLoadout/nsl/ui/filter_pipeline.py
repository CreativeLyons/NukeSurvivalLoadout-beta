"""NSL Loadout Panel filter pipeline.

Composes the three visibility-narrowing layers (search, tags, folder-eye)
into a single decision function and wires it to the panel's widgets via
:func:`wire_filter_pipeline`.

What the FilterPipeline owns:

* The visible-key set, recomputed reactively whenever one of the three
  input layers changes (search query, Invert toggle, folder-eye toggle).
* Applying that visible set to ``panel.grid`` so non-matching pills
  collapse out of the layout and matches reflow to the top-left.

What the FilterPipeline deliberately does **not** own:

* The selection set. Selection lives on the grid; the pipeline reads
  ``panel.grid.selected_keys()`` only when callers ask for the
  bulk-operation target set.
* The Plugin → folder mapping. The wiring layer feeds in a ``key_to_folder``
  callable so the pipeline never has to know how a key resolves to a
  Plugins Folder.
* Tag membership. The v1 tag layer is identity by construction; the seam
  for a future real tag check is the ``selected_tags`` argument on
  :func:`compose_visible_keys`.

Key contracts:

* **AND composition** for the visible set (search × tag × eye), with
  Invert applied as the final post-composition flip.
* **Full-selection bulk operations**: :func:`bulk_target_keys` always
  returns the full selection regardless of current visibility, keeping
  "discovery" (filter) separate from "action" (bulk ops on selection).
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Sequence, Set

# We share the search rule with :mod:`nsl.ui.search_tags`
# rather than re-encoding it, so the case-insensitive substring rule
# stays in exactly one place.
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
#
# Each layer is a predicate over a single Plugin key. The composition
# function ANDs them. Keeping the three layers as standalone functions
# means the AND composition is a one-line ``all(...)`` that's obviously
# correct on inspection.


def search_layer(query: Optional[str], plugin_name: str) -> bool:
    """Search rule: case-insensitive substring against the Plugin Name.

    Delegates to :func:`nsl.ui.search_tags.match_query` so the rule lives
    in exactly one place. An empty / whitespace-only / ``None`` query
    matches every Plugin.
    """
    return match_query(query or "", plugin_name)


def tag_layer_v1(
    plugin_name: str,
    selected_tags: Optional[Iterable[str]] = None,
) -> bool:
    """v1 tag layer - **identity**, always returns ``True``.

    Plugin tags are parked for v2. The v1 tag-chip row in
    :class:`nsl.ui.search_tags.SearchTagsStrip` ships the system
    ``None`` chip only - there is no user-chip state for this layer to
    consult. The tags filter combines via AND, so contributing an
    identity predicate leaves the AND composition unchanged when v2
    swaps in a real tag membership check here.

    The ``selected_tags`` argument is the v2 seam - v1 passes ``None``
    or an empty iterable and the result is the same.
    """
    # Touch the arguments so static analysers don't flag them as unused;
    # the v2 implementation will branch on ``selected_tags`` membership.
    del plugin_name, selected_tags
    return True


def eye_layer(plugin_name: str, hidden_keys: Iterable[str]) -> bool:
    """Eye-toggle layer - True when *plugin_name* is **not** hidden.

    The folder-card's eye toggle hides every Plugin from a given
    Plugins Folder. The wiring layer resolves "folder X is hidden" to
    "the set of Plugin keys originating from folder X is hidden" and
    feeds that key-set into ``hidden_keys`` here.

    Doing the resolution at the wire layer keeps this predicate
    folder-agnostic, which keeps the composition function reusable for
    any "blanket hide these keys" feature v2 might add (e.g. an
    enabled-only filter or a single-plugin mute).
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

    Composition rule (search and tag filter combine via AND):

        visible = search(key) AND tag(key) AND eye(key)

    The Invert toggle flips the resulting *base* set: the pill grid shows
    the inverse of what the current selection would normally show. With
    v1 tags-identity the inversion still operates correctly because the
    base set is well-defined (it just happens to equal the all-keys set
    when the tag layer is identity). When v2 adds real tag-chip state,
    the inversion semantics stay correct because they always flip the
    AND-composed base set.

    Order of *all_keys* is preserved in the output. Hidden keys are
    excluded from the *base* set computation, so they are correctly
    excluded from the Invert result too - eye-hidden Plugins are treated
    as *not present* for filtering purposes, and are never surfaced
    regardless of filter state.

    Inversion targets the search × tag AND set (just the search-narrowed
    set in v1), then flips it, and continues to AND with eye after the
    flip so eye-hidden Plugins stay hidden.
    """
    hidden_set = set(hidden_keys)
    # Step 1: compute the search × tag base set (no eye yet).
    base = [
        k for k in all_keys
        if search_layer(query, k) and tag_layer_v1(k, selected_tags)
    ]
    base_set = set(base)
    # Step 2: apply Invert against the *base* set, preserving order.
    if invert:
        inverted = [k for k in all_keys if k not in base_set]
        candidates = inverted
    else:
        candidates = base
    # Step 3: AND with eye - eye-hidden keys are NEVER visible,
    # regardless of search/invert state.
    return [k for k in candidates if eye_layer(k, hidden_set)]


# ---------------------------------------------------------------------------
# Bulk-operation target set - full selection, not visible subset
# ---------------------------------------------------------------------------


def bulk_target_keys(full_selection: Iterable[str]) -> List[str]:
    """Return the keys a bulk action should affect.

    Bulk actions act on the full selection, not the visible subset: a
    user with 12 pills selected and a search filter narrowing visible
    pills to 4 still sees ``Disable Selected (12)`` on the toolbar, and
    clicking the button disables all 12, including the 8 currently hidden
    by filter.

    The pipeline therefore takes the *full* selection in and passes it
    through unchanged. It does not intersect with the visible set; it
    does not partition by source. The wiring layer is free to compose
    this output with a separate "skip Global" gate - that is a
    different concern enforced at the bulk-op execution layer.

    Wrapping this in a named helper makes the contract explicit at the
    call site: every bulk-op handler in the wiring layer should call
    ``bulk_target_keys(panel.grid.selected_keys())`` rather than reading
    the visible set, even though the implementation today is a passthrough.
    The name documents intent and survives future refactors of the
    selection model without losing the contract.
    """
    return list(full_selection)


# ---------------------------------------------------------------------------
# FilterState - the per-session, panel-local store
# ---------------------------------------------------------------------------


class FilterState:
    """Per-session, panel-local filter inputs.

    Holds the search text, the Invert toggle, and the set of
    currently-hidden Plugin keys (resolved by the wiring layer from the
    folder-card eye toggles). The state is **not** persisted; switching
    Loadouts does not clear it.

    Tag-chip selection is intentionally absent. v2 adds a
    ``selected_tags`` attribute here; until then keeping it out
    prevents anyone from accidentally relying on a v2 surface.
    """

    __slots__ = ("query", "invert", "hidden_keys", "_listeners")

    def __init__(self) -> None:
        self.query: str = ""
        self.invert: bool = False
        self.hidden_keys: Set[str] = set()
        # Listeners receive a single argument: the new visible-keys list.
        # They are invoked synchronously, in registration order, after
        # any state mutator runs. Used by the Qt wiring helper to repaint
        # the grid.
        self._listeners: List[Callable[[List[str]], None]] = []

    # -- state mutators --------------------------------------------------

    def set_query(self, query: str) -> None:
        self.query = query or ""

    def set_invert(self, invert: bool) -> None:
        self.invert = bool(invert)

    def set_hidden_keys(self, hidden_keys: Iterable[str]) -> None:
        """Replace the set of currently-hidden Plugin keys.

        The wiring helper computes this set every time a folder-card eye
        toggle fires: for each folder whose eye is closed, collect every
        Plugin key whose origin is that folder, union them all.
        """
        self.hidden_keys = set(hidden_keys)

    # -- reactivity ------------------------------------------------------

    def add_listener(
        self, listener: Callable[[List[str]], None]
    ) -> None:
        """Register a callable to be invoked after every state change.

        Listeners receive the freshly-computed visible-keys list. This is
        the reactivity hook the Qt wiring layer uses to repaint the grid.
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
        """Recompute visible keys and fire every listener with them.

        Returns the freshly-computed list so callers that want both the
        recomputation and the side effect get a single result.
        """
        visible = self.visible_keys(all_keys)
        for listener in list(self._listeners):
            listener(visible)
        return visible


# ---------------------------------------------------------------------------
# FilterPipeline - the high-level binding object
# ---------------------------------------------------------------------------


class FilterPipeline:
    """Holds the state, resolves the visible-keys set, applies it.

    Pure-Python at construction; Qt is only touched when the optional
    ``apply_visibility`` callable is wired (which the Qt helper below
    does for a ``panel.grid``).

    Construction:

    * ``all_keys_getter``: callable returning the current full key list
      (typically ``lambda: panel.grid.keys()``). The pipeline calls it
      every time it recomputes, so a grid rebuild after a Loadout
      switch is automatically picked up.
    * ``key_to_folder``: callable ``str -> Optional[str]`` mapping each
      Plugin key to its source folder path. Returns ``None`` for keys
      with unknown origin (those keys are NEVER hidden by eye - an
      unknown-folder Plugin can't match any folder-eye state).
    * ``apply_visibility``: optional callable ``list[str] -> None`` that
      paints the new visible-keys set onto the grid. The Qt wiring
      helper supplies one; pass ``None`` and read ``pipeline.last_visible``
      instead when no grid is wired.
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
        # Track which folders are currently hidden via the eye toggle.
        # Mapping folder_path -> visible (True = visible, False = hidden).
        self._folder_visible: dict[str, bool] = {}
        # Cache the last computed filter-only output (pre-sort) and the
        # last visible-keys list (post-sort) for the listener API.
        self.last_filtered: List[str] = []
        self.last_visible: List[str] = []
        # Sort composes AFTER filter: (master → filter → sort → set_keys).
        # ``None`` means "no sort applied" - used by panels constructed
        # without a sort toolbar.
        self._sort_mode = None
        self._sort_state_lookup: Optional[Callable[[str], object]] = None
        # Group dividers - computed alongside sort. Aligned with
        # ``last_visible``; ``None`` entries mean "no divider above this
        # pill." For alphabetical sort modes (no grouping) the whole
        # list is ``None`` so the grid renders no dividers.
        self.last_group_labels: List[Optional[str]] = []
        # Wire the state's listener loop into our apply step.
        self.state.add_listener(self._on_state_recompute)

    # ----- public mutators (called by the Qt wiring helper) ------------

    def on_filter_changed(self, query: str, invert: bool) -> List[str]:
        """Slot for ``SearchTagsStrip.filter_changed(str, bool)``.

        Returns the new visible-keys list (the Qt wiring helper ignores
        the return).
        """
        self.state.set_query(query)
        self.state.set_invert(invert)
        return self._recompute_and_apply()

    def on_folder_visibility_changed(
        self, folder_path: str, visible: bool
    ) -> List[str]:
        """Slot for ``FolderCard.visibility_changed(str, bool)``.

        ``visible=True`` ⇒ folder eye is open (Plugins from this folder
        are visible). ``visible=False`` ⇒ folder eye is closed (Plugins
        from this folder are hidden in the grid).
        """
        self._folder_visible[folder_path] = bool(visible)
        self._recompute_hidden_keys()
        return self._recompute_and_apply()

    def reset_folder_visibility(self, folder_paths: Iterable[str]) -> List[str]:
        """Seed the folder visibility map from a fresh folder list.

        Called once at wire time so the pipeline knows about every
        configured folder before any eye toggles fire. Folders default
        to visible. ``hidden_keys`` is recomputed after seeding.
        """
        # Preserve any explicit hide the user has already done on a
        # path that survives the seed (the wiring layer reseeds when
        # the folder list itself changes).
        seeded = {p: self._folder_visible.get(p, True) for p in folder_paths}
        self._folder_visible = seeded
        self._recompute_hidden_keys()
        return self._recompute_and_apply()

    # ----- sort composition --------------------------------------------

    def set_sort_mode(self, mode) -> List[str]:
        """Slot for the grid-toolbar sort dropdown's ``sort_mode_changed``.

        Dataflow: ``(master → filter → sort → set_keys)``. The pipeline
        owns the recompute; sort changes re-run the same compute-and-apply
        path as a filter change, so the active filter is preserved.
        """
        self._sort_mode = mode
        return self._recompute_and_apply()

    def set_sort_state_lookup(self, lookup: Optional[Callable[[str], object]]) -> List[str]:
        """Replace the per-key state lookup used by sort axes."""
        self._sort_state_lookup = lookup
        return self._recompute_and_apply()

    def filter_visible_keys(self) -> List[str]:
        """Return the filter-only output (pre-sort).

        Installed on the panel as ``panel.filter_visible_keys`` by
        :func:`wire_filter_pipeline`. Any legacy consumer that wants
        the filter result without the sort composition can read it
        here; the pipeline itself drives the grid via ``last_visible``.
        """
        return list(self.last_filtered)

    # ----- bulk-op contract (full selection, never visible) ------------

    def bulk_target_keys(self, full_selection: Iterable[str]) -> List[str]:
        """Bulk ops act on the **full selection**.

        Wraps the module-level :func:`bulk_target_keys` so callers can
        reach it through the pipeline object without a separate import.
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
            # Lazy import to keep this module Qt-light; sort lives in
            # ``nsl.ui.sort`` and re-imports the toolbar enum.
            from nsl.ui.sort import (  # noqa: PLC0415
                SortableState,
                group_label_for_state,
                sort_keys,
            )

            lookup = self._sort_state_lookup or (lambda k: SortableState(name=k))
            sorted_keys = sort_keys(self.last_filtered, self._sort_mode, lookup)
            self.last_visible = list(sorted_keys)
            # Compute per-pill divider labels in the same pass - one
            # lookup per visible key. Alphabetical modes return ``None``
            # for every pill (``group_label_for_state`` short-circuits)
            # so the grid renders no dividers.
            self.last_group_labels = [
                group_label_for_state(lookup(k), self._sort_mode)
                for k in self.last_visible
            ]
        else:
            self.last_visible = list(self.last_filtered)
            # No sort → no grouping → empty list signals "use uniform
            # layout" to the grid.
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

    What this helper wires:

    * ``panel.search_tags.filter_changed(str, bool)`` →
      :meth:`FilterPipeline.on_filter_changed`.
    * ``panel.folder_card.visibility_changed(str, bool)`` →
      :meth:`FilterPipeline.on_folder_visibility_changed`.
    * The pipeline's ``apply_visibility`` callable rebuilds the grid with
      the visible-keys order so non-matching pills collapse out of the
      layout and matches reflow to the top-left. Grid selection is
      captured before the rebuild and restored after so it survives a
      visibility cycle.

    The optional ``key_to_folder`` callable maps each Plugin key to its
    source folder path (the wiring layer typically composes this from
    the active Loadout's resolved Plugin records). When omitted, every
    key resolves to ``None`` and the eye layer therefore never matches
 - folder-eye toggles still record state but do not hide anything
    until the wiring layer supplies a real mapping. This keeps the
    helper callable from minimal fixtures without an installed Plugin
    model.

    Returns the constructed :class:`FilterPipeline` so callers can read
    state, drive the bulk-op contract, or extend the pipeline with
    additional listeners.
    """
    if key_to_folder is None:
        # Default: no key carries an origin folder, so the eye layer is
        # inert. The folder-card's eye-toggle clicks still update the
        # pipeline's state - they just don't change visible_keys.
        def key_to_folder(_key: str) -> Optional[str]:
            return None

    def _all_keys() -> Sequence[str]:
        # Must return the MASTER key list, not panel.grid.keys() (which
        # is now potentially the filtered subset because
        # _apply_visibility rebuilds the grid). Panel stores the master
        # list on ``_all_plugin_keys``; if not set yet (early bootstrap),
        # fall back to grid.keys().
        master = getattr(panel, "_all_plugin_keys", None)
        if master is not None:
            return list(master)
        return panel.grid.keys()

    def _apply_visibility(visible: List[str]) -> None:
        # Rebuild the grid so non-matching pills collapse out of the
        # layout and matches reflow to the top-left. Pass ``visible``
        # straight through as the new grid order; the pipeline already
        # composed (filter → sort), so the order encoded in ``visible``
        # is the order the grid should display.
        #
        # Capture grid selection BEFORE ``grid.set_keys`` and restore it
        # AFTER, so the pill selection survives every view-level recompute
        # (folder eye toggle, search filter change, sort change, refresh).
        # Without this, ``set_keys`` unconditionally clears ``grid._selected``
        # and emits ``selection_changed([])`` - that empty signal
        # propagates through the selection bridge and wipes the bulk-
        # action model. ``select_keys`` filters automatically to keys
        # actually present in the grid, so a previously-selected pill
        # that's now hidden simply drops from the restored set without
        # raising.
        try:
            preserved_selection = list(panel.grid.selected_keys())
        except Exception:  # noqa: BLE001 - selection capture must not break the pipeline
            preserved_selection = []
        if panel.grid.set_keys(list(visible)):
            # Rewire pills if a real rebuild happened (set_keys
            # returns True). Lazy import - keeps this module Qt-light.
            from nsl.ui.wiring.events import rewire_grid_pills
            rewire_grid_pills(panel)
        if preserved_selection:
            try:
                panel.grid.select_keys(preserved_selection)
            except Exception:  # noqa: BLE001 - restore must not break the pipeline
                pass
        # Push group-divider labels onto the grid AFTER set_keys so
        # they line up with the freshly-installed key order. The grid
        # tolerates a missing ``set_group_labels`` for backwards-compat
        # snapshot fixtures. An empty ``last_group_labels`` clears any
        # stale dividers (e.g. switching back to A→Z after a grouping
        # mode).
        labels = list(pipeline.last_group_labels)
        set_labels = getattr(panel.grid, "set_group_labels", None)
        if callable(set_labels):
            try:
                set_labels(labels)
            except Exception:  # noqa: BLE001 - dividers must not break the filter path
                pass
        # Recompute the counter strip so the "Loaded"
        # chip (and others sensitive to the visible set) update as
        # the user types in the search field. Reuse the selection-
        # change handler, which does a full strip refresh against
        # the current pipeline.last_visible without a banner reflow.
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

    # Seed the folder-visibility map from the panel's current folder
    # list so the pipeline's hidden-keys cache is consistent before the
    # first user interaction.
    folder_card = getattr(panel, "folder_card", None)
    if folder_card is not None and hasattr(folder_card, "entries"):
        pipeline.reset_folder_visibility(
            [e.path for e in folder_card.entries() if e.visible]
            + [e.path for e in folder_card.entries() if not e.visible]
        )
        # Honour any folder that came in already hidden - the seed above
        # only records "this folder is known"; we now apply the actual
        # visible flag from each entry.
        for entry in folder_card.entries():
            pipeline._folder_visible[entry.path] = bool(entry.visible)
        pipeline._recompute_hidden_keys()
        pipeline._recompute_and_apply()

    # Connect the panel's existing signals. We tolerate missing
    # attributes / signals so the helper can be called against a stub
    # panel; the production panel always has both.
    search_tags = getattr(panel, "search_tags", None)
    if search_tags is not None and hasattr(search_tags, "filter_changed"):
        search_tags.filter_changed.connect(pipeline.on_filter_changed)

    if folder_card is not None and hasattr(folder_card, "visibility_changed"):
        folder_card.visibility_changed.connect(
            pipeline.on_folder_visibility_changed
        )

    # Stash the pipeline on the panel under a stable attribute so the
    # other wiring helpers (bulk-op handlers, select-filtered) can reach
    # it without re-stitching.
    panel.filter_pipeline = pipeline
    # Expose the filter-only output as a panel attribute so ``wire_sort``'s
    # legacy fallback can compose against it. The sort path itself routes
    # through the pipeline when it detects ``panel.filter_pipeline``.
    panel.filter_visible_keys = pipeline.filter_visible_keys
    return pipeline
