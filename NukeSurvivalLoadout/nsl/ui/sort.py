"""Per-session sort comparators for the Plugins grid.

Seven sort modes; A -> Z is the universal secondary sort. Sort selection
is panel-local and per-session and resets on Nuke quit.

This module provides:

* :class:`SortableState` - a tiny dataclass collecting every attribute the
  seven primary sort axes consult (enabled, selected, pending change,
  warning, folder priority). The wiring layer (:func:`wire_sort`) builds
  one of these per pill key by calling the ``state_lookup`` callable
  installed on the panel.
* :data:`COMPARATORS` - a mode -> key-function table. Each key function
  returns a tuple whose first element is the mode's *primary* sort axis
  and whose final element is the A -> Z secondary axis. ``Z -> A`` is the
  one exception (the entire ordering *is* the alpha axis) and gets a
  ``reverse=True`` flag.
* :func:`sort_keys` - the public comparator dispatcher. Pure function:
  given ``keys``, a :class:`SortMode`, and a ``state_lookup`` callable,
  returns a new list in the requested order. Stable. Never mutates the
  input.
* :func:`wire_sort` - connects the grid toolbar's ``sort_mode_changed``
  signal to the grid, re-rendering by composing filter then sort then
  rebuild and preserving selection state across the swap. Sort scope is
  panel-local and per-session: this helper writes nothing to disk and
  reads nothing back, so a fresh Nuke session always opens with the
  default ``A -> Z`` order.

Qt imports go only via :mod:`nsl.compat`. The
:class:`SortMode` enum and :data:`SORT_MODE_ORDER` are imported from
:mod:`nsl.ui.grid_toolbar` (the toolbar widget is the
canonical source of the labels; re-exporting from one place avoids drift).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from nsl import compat
from nsl.ui.grid_toolbar import SORT_MODE_ORDER, SortMode

QtCore = compat.QtCore

__all__ = [
    "SortMode",
    "SORT_MODE_ORDER",
    "SortableState",
    "StateLookup",
    "sort_keys",
    "wire_sort",
    "build_sort_state_lookup",
    "build_key_to_folder",
    "group_label_for_state",
]


# ---------------------------------------------------------------------------
# Sortable state - what each primary axis consults
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SortableState:
    """The minimum data each pill exposes to the sort axes.

    Six independent axes plus the always-secondary ``name``:

    * ``name``             - Plugin Name. Drives ``A -> Z`` / ``Z -> A``
      directly and the secondary sort in every other mode. Across all
      sort modes, the secondary sort is always alphabetical A -> Z.
    * ``enabled``          - next-restart enabled state. Drives
      ``Status``: enabled Plugins first.
    * ``selected``         - current selection membership. Drives
      ``Selected``: selected pills first.
    * ``pending``          - ``"green"`` (will-add) / ``"red"`` (will-
      remove) / ``None`` (no pending change). Drives ``Changed state``:
      green first, then red, then unchanged.
    * ``warning``          - ``True`` if the pill is in the load-
      failed problem state (status icon FAILED). Drives the middle
      bucket of ``Warnings``.
    * ``missing``          - ``True`` if the pill is in the Plugin-
      Scan-missing problem state (status icon MISSING). Drives the
      top bucket of ``Warnings``: pills grouped into Missing above
      Warnings (load-failed) above Clean.
    * ``folder_priority``  - integer index into the Plugins Folder list
      (lower = higher priority, matching the *Plugins Folder
      Management* ordering). Drives ``Folder of origin``: folders
      group in priority order.

    The wiring layer builds one of these per pill key via the
    ``state_lookup`` callable. The dataclass is frozen so a single
    instance can be safely cached/shared between sort calls if the
    caller wishes.
    """

    name: str
    enabled: bool = True
    selected: bool = False
    pending: Optional[str] = None  # "green" | "red" | None
    warning: bool = False
    missing: bool = False
    folder_priority: int = 0
    folder_label: Optional[str] = None  # human-readable folder name for the
    # ``Folder of origin`` divider header (basename of the folder path).
    # Optional; ``None`` falls back to ``"Folder · ?"`` so callers that never
    # set this field keep rendering predictably.


#: Type of the ``state_lookup`` callable passed to :func:`sort_keys`.
#: Given a pill key, return its :class:`SortableState`.
StateLookup = Callable[[str], SortableState]


# ---------------------------------------------------------------------------
# Primary-axis key functions - one per mode, A → Z always secondary
# ---------------------------------------------------------------------------
#
# Each function returns a tuple whose final element is the alphabetical
# secondary axis (``name.lower()``). Booleans use the convention
# ``not flag`` so that ``True`` sorts before ``False`` under ascending
# sort. Strings already sort ascending without inversion.
#
# These functions consume *only* a :class:`SortableState`. The
# dispatcher :func:`sort_keys` is the only place the lookup callable is
# invoked, so each comparator stays trivially analysable in isolation.


def _key_a_to_z(s: SortableState) -> Tuple:
    """Primary axis: alphabetical ascending. No secondary (alpha IS the axis)."""
    return (s.name.lower(),)


def _key_z_to_a(s: SortableState) -> Tuple:
    """Primary axis: alphabetical descending.

    Implemented in the comparator (via ``reverse=True`` in
    :func:`sort_keys`) rather than by inverting the key here, so the
    *value* of the key remains the alphabetic axis. This keeps the
    secondary-sort contract simple: when there is no secondary axis,
    ``sort_keys`` simply reverses the alpha-ascending result.
    """
    return (s.name.lower(),)


def _key_status(s: SortableState) -> Tuple:
    """Primary axis: enabled first, then disabled. Secondary A → Z."""
    return (not s.enabled, s.name.lower())


def _key_selected(s: SortableState) -> Tuple:
    """Primary axis: selected first, then unselected. Secondary A → Z."""
    return (not s.selected, s.name.lower())


def _pending_bucket(pending: Optional[str]) -> int:
    """Map pending state to a sort bucket.

    Pending-change pills first: green (will be added) at top, red (will be
    removed) below green, then unchanged pills below those. So green=0,
    red=1, none=2.
    """
    if pending == "green":
        return 0
    if pending == "red":
        return 1
    return 2


def _key_changed_state(s: SortableState) -> Tuple:
    """Primary axis: green → red → unchanged. Secondary A → Z."""
    return (_pending_bucket(s.pending), s.name.lower())


def _key_warnings(s: SortableState) -> Tuple:
    """Primary axis: Missing -> Failed -> Clean. Secondary A -> Z.

    Three buckets: pills grouped into ``Missing`` above ``Warnings``
    (load-failed) above ``Clean``. The triage view for "what needs my
    attention this session." Missing wins over Failed because a Plugin
    that disappeared from disk between snapshot and scan is a louder
    signal than one that failed to load: the user's first move is
    typically to confirm the folder is still mounted.
    """
    if s.missing:
        bucket = 0
    elif s.warning:
        bucket = 1
    else:
        bucket = 2
    return (bucket, s.name.lower())


def _key_folder_of_origin(s: SortableState) -> Tuple:
    """Primary axis: folder priority (highest first). Secondary A → Z.

    Lower ``folder_priority`` integer = higher priority, matching the
    *Plugins Folder Management* list order (top of list = highest
    priority).
    """
    return (s.folder_priority, s.name.lower())


#: Mode -> primary key function. The dispatcher :func:`sort_keys` reads
#: from here. Exporting the table (not just the dispatcher) makes each
#: per-mode comparator usable on its own without going through the
#: lookup-callable boilerplate.
COMPARATORS: Dict[SortMode, Callable[[SortableState], Tuple]] = {
    SortMode.A_TO_Z: _key_a_to_z,
    SortMode.Z_TO_A: _key_z_to_a,
    SortMode.STATUS: _key_status,
    SortMode.SELECTED: _key_selected,
    SortMode.CHANGED_STATE: _key_changed_state,
    SortMode.WARNINGS: _key_warnings,
    SortMode.FOLDER_OF_ORIGIN: _key_folder_of_origin,
}


# ---------------------------------------------------------------------------
# The public dispatcher
# ---------------------------------------------------------------------------


def sort_keys(
    keys: Sequence[str],
    mode: SortMode,
    state_lookup: StateLookup,
) -> List[str]:
    """Return ``keys`` sorted under ``mode``.

    Pure function. Never mutates ``keys`` or anything ``state_lookup``
    returns. Stable: Python's Timsort preserves the input order for
    ties beyond the comparator's tuple, which honours the rule that
    across all sort modes the secondary sort is always alphabetical
    A -> Z once the secondary axis is encoded in the tuple itself.

    Args:
        keys: Iterable of pill keys (typically the grid's current
            full key list, or a filtered subset).
        mode: One of the seven :class:`SortMode` members.
        state_lookup: Callable mapping a key to its
            :class:`SortableState`. Called exactly once per key.

    Raises:
        ValueError: ``mode`` is not a known sort mode.
    """
    if mode not in COMPARATORS:
        raise ValueError(f"unknown sort mode: {mode!r}")

    keys_list = list(keys)
    key_fn = COMPARATORS[mode]

    # Pre-resolve states so the lookup is consulted exactly once per
    # key. Ties on the primary axis fall back to the alpha secondary
    # encoded in the tuple, and ties beyond that are broken by
    # Timsort's stable ordering.
    decorated = [(key_fn(state_lookup(k)), k) for k in keys_list]

    reverse = mode is SortMode.Z_TO_A
    decorated.sort(key=lambda pair: pair[0], reverse=reverse)
    return [k for _, k in decorated]


# ---------------------------------------------------------------------------
# Wiring helper - the orchestrator's single integration point
# ---------------------------------------------------------------------------


def wire_sort(panel) -> None:
    """Connect the grid-toolbar sort dropdown to the grid.

    Looks at ``panel.grid_toolbar.sort_mode_changed`` and on each change
    re-renders the grid in the new order, composed after the filter
    pipeline and preserving the current selection.

    Composition contract:

    1. Resolve the *visible* keys - call
       ``panel.filter_visible_keys()`` if installed, otherwise fall back
       to ``panel.grid.keys()``.
    2. Apply :func:`sort_keys` to the visible keys using
       ``panel.sort_state_lookup`` if installed, otherwise a permissive
       default that returns a vanilla :class:`SortableState` (so the
       only consulted axis is the alpha secondary - safe for the
       initial wire-up before the real state lookup is installed).
    3. Capture the current selection from ``panel.grid``.
    4. Call ``panel.rebuild_grid(new_keys)`` to swap the grid.
    5. Re-apply the captured selection on the rebuilt grid.

    Sort state is per-session and panel-local. This helper deliberately
    does not read or write any persistence surface: a fresh Nuke session
    opens with the toolbar's default ``A -> Z`` and nothing here changes
    that.

    The helper also stashes ``panel._current_sort_mode`` so other
    wiring helpers (filter, selection) can re-trigger a sort by simply
    calling ``panel._resort_grid()`` without having to query the
    dropdown directly. This is the only state the helper introduces
    on the panel; it is panel-local and reset every Nuke session
    because the panel is destroyed and recreated each session.
    """

    toolbar = panel.grid_toolbar

    # Initial mode - the toolbar's current value (default ``A → Z``).
    panel._current_sort_mode = toolbar.current_sort_mode()

    def _default_state_lookup(key: str) -> SortableState:
        """Permissive lookup used until the real one is installed.

        Returns a vanilla :class:`SortableState` whose only non-default
        axis is the name. Under any of the seven modes, the result is
        the same alpha-ascending or alpha-descending order - which is
        exactly the right thing before domain wiring: the toolbar still
        re-orders the grid visibly, proving the wire is alive without
        requiring domain state to exist.
        """
        return SortableState(name=key)

    def _resolve_state_lookup() -> StateLookup:
        return getattr(panel, "sort_state_lookup", None) or _default_state_lookup

    def _resolve_visible_keys() -> List[str]:
        """Return the post-filter visible key set.

        The filter pipeline is expected to install
        ``panel.filter_visible_keys`` as a no-argument callable
        returning the keys the filter currently lets through. Until
        that lands, we fall back to the grid's full key list: sort
        applied to "everything" is the correct default for an
        unfiltered grid.
        """
        getter = getattr(panel, "filter_visible_keys", None)
        if callable(getter):
            try:
                visible = getter()
                return list(visible)
            except Exception:
                # Filter pipeline is the wrong place to crash a sort;
                # fall back rather than propagate. The exception path
                # cannot block the panel.
                pass
        return panel.grid.keys()

    def _resort_grid(*_args) -> None:
        """Compose filter → sort → rebuild, preserve selection.

        Connected to ``sort_mode_changed`` (which emits the new mode's
        text label) and exposed as ``panel._resort_grid`` so peer
        wiring helpers can re-trigger a sort after their own work.

        When ``panel.filter_pipeline`` exists, the sort is routed
        through the pipeline so the dataflow stays
        ``(master -> filter -> sort -> set_keys)``. Without that route, a
        sort change would call ``rebuild_grid(master_sorted)`` and
        clobber any active filter. The direct path is preserved for
        panels constructed without a pipeline.

        A re-entrancy guard (``panel._sort_in_progress``) keeps a
        re-sort triggered by a :attr:`selection_changed` emit (Selected
        sort mode) from looping when :meth:`grid.set_keys` emits its own
        empty selection mid-recompute. The recompute also collapses to a
        single pass when the lookup closure is already installed on the
        pipeline: a second recompute would run against a freshly-emptied
        selection (``set_keys`` clears ``grid._selected``), producing
        alpha order even when pills are selected. Skipping it preserves
        the correct selected-first ordering for the Selected sort mode.
        """
        # Re-entrancy guard. Selection-driven re-sorts (see
        # ``_on_selection_changed_for_sort`` below) fire from inside
        # ``grid.set_keys``'s own ``selection_changed.emit([])``; without
        # this guard, the inner sort would re-enter the outer sort and
        # the lookup would consult a cleared selection.
        if getattr(panel, "_sort_in_progress", False):
            return
        panel._sort_in_progress = True
        try:
            mode = toolbar.current_sort_mode()
            panel._current_sort_mode = mode

            pipeline = getattr(panel, "filter_pipeline", None)
            lookup = _resolve_state_lookup()
            if pipeline is not None:
                # Capture the selection before the rebuild and restore
                # it after. ``grid.set_keys`` (called inside the
                # pipeline's apply path) unconditionally clears
                # ``_selected`` AND emits ``selection_changed.emit([])``
                # - that empty signal propagates through the selection
                # bridge, replaces the selection model with an empty
                # list, and disables the bulk-action buttons. The
                # restore below MUST emit so the bridge sees the
                # recovered selection and re-enables the toolbar
                # buttons. Without the emitting restore, changing the
                # sort mode leaves the bulk-action buttons disabled.
                selected = list(panel.grid.selected_keys())
                # Avoid the redundant ``set_sort_state_lookup`` call
                # when the pipeline already has this exact closure
                # installed (the common case - ``wire_sort_state_lookup``
                # installs it once at panel construction and it never
                # changes). A redundant call triggers a full pipeline
                # recompute, which calls ``grid.set_keys`` and clears
                # the selection - then the ``set_sort_mode`` recompute
                # right after runs against the cleared selection and
                # produces alpha order instead of selected-first for
                # the Selected mode. Skipping the redundant call lets
                # the single ``set_sort_mode`` recompute see the
                # captured selection live and order accordingly.
                if pipeline._sort_state_lookup is not lookup:
                    pipeline.set_sort_state_lookup(lookup)
                pipeline.set_sort_mode(mode)
                if selected:
                    panel.grid.select_keys(selected)
                return

            # Pipeline-less path (panels constructed without a pipeline).
            visible_keys = _resolve_visible_keys()
            new_keys = sort_keys(visible_keys, mode, lookup)

            # Selection preservation: capture before the grid is swapped,
            # restore after. The new grid only honours keys it contains.
            # See the pipeline branch above for the emit-True rationale -
            # ``rebuild_grid`` also routes through ``grid.set_keys`` which
            # emits an empty selection that we must overwrite to keep the
            # toolbar buttons enabled.
            selected = list(panel.grid.selected_keys())

            panel.rebuild_grid(new_keys)

            # Push group-divider labels onto the freshly-rebuilt grid so
            # the pipeline-less path matches the pipeline path's visual
            # output. ``set_group_labels`` is a no-op on grids that
            # don't implement it.
            set_labels = getattr(panel.grid, "set_group_labels", None)
            if callable(set_labels):
                labels = [
                    group_label_for_state(lookup(k), mode) for k in new_keys
                ]
                try:
                    set_labels(labels)
                except Exception:  # noqa: BLE001 - dividers never break sort
                    pass

            if selected:
                panel.grid.select_keys(selected)
        finally:
            panel._sort_in_progress = False

    # Stash the re-sort entry point so the filter/selection helpers can
    # poke it without re-importing this module.
    panel._resort_grid = _resort_grid

    # The toolbar emits the verbatim label string; the slot ignores
    # the payload and consults ``current_sort_mode()`` so it remains
    # the single source of truth.
    toolbar.sort_mode_changed.connect(_resort_grid)

    # Selection-change re-sort for ``Selected`` mode.
    #
    # The ``Selected`` sort axis groups currently-selected pills above
    # the rest. Selection state changes via marquee, click, or
    # ``select_keys`` - none of which fire the sort dropdown's
    # ``sort_mode_changed``, and none of which trigger a filter
    # pipeline recompute on their own. Without this connection, the
    # user would have to re-pick ``Selected`` from the dropdown each
    # time they changed which pills were selected for the grid to
    # actually re-order.
    #
    # Gated on ``mode is SortMode.SELECTED`` so the four other grouping
    # modes don't pay a recompute on every click in the grid (their
    # primary axis doesn't depend on selection). The re-entrancy guard
    # inside ``_resort_grid`` squelches the inner emit that
    # ``grid.set_keys`` fires during the sort itself.
    def _on_selection_changed_for_sort(*_args) -> None:
        if panel._current_sort_mode is not SortMode.SELECTED:
            return
        _resort_grid()

    try:
        panel.grid.selection_changed.connect(_on_selection_changed_for_sort)
    except AttributeError:
        # Grids that don't expose the signal simply don't activate the
        # selection-re-sort feature.
        pass


# ---------------------------------------------------------------------------
# Production state-lookup builders
# ---------------------------------------------------------------------------
#
# The wire helper above (``wire_sort``) connects the toolbar to the grid
# but reads the per-key state through whatever callable the wiring layer
# has installed on ``panel.sort_state_lookup``. With no such callable
# installed, every non-alpha sort mode collapses to the alpha secondary
# because the permissive default lookup leaves every axis at its
# dataclass default.
#
# ``build_sort_state_lookup`` returns a closure that queries the live
# registry, grid selection, and folder-card order. The wiring layer
# (``nsl.ui.wiring.sort_state.wire_sort_state_lookup``) installs the
# closure on the panel and on the filter pipeline so every recompute
# uses production data.
#
# Each axis-specific helper is small and pure (no Qt) so it can be
# reasoned about independently with stub registries.


def _pending_for_key(
    key: str,
    *,
    current,
    baseline,
) -> Optional[str]:
    """Return the per-key ``Changed state`` bucket: ``"green"`` /
    ``"red"`` / ``None``.

    Matches the formula used by the pill body so the sort and the
    pill-body wash never disagree:

    * ``pending_enabled and not loaded_enabled`` -> ``"green"`` (will-add)
    * ``loaded_enabled and not pending_enabled`` -> ``"red"`` (will-remove)
    * otherwise                                  -> ``None``

    Effective-state rule: an entry with ``enabled=False`` collapses to
    absence - equivalent to "not loaded / not pending." Same convention
    the banner uses, so the sort and the banner never disagree about the
    count of changed plugins.
    """
    pending_enabled = _key_is_effective(key, current)
    loaded_enabled = _key_is_effective(key, baseline)
    if pending_enabled and not loaded_enabled:
        return "green"
    if loaded_enabled and not pending_enabled:
        return "red"
    return None


def _key_is_effective(key: str, model) -> bool:
    """``True`` iff *key* is present in *model* with ``enabled=True``.

    Returns ``False`` when *model* is ``None`` (degraded contexts), the
    key is absent, or the entry is explicitly ``enabled=False``.
    """
    if model is None:
        return False
    entry = model.plugins.get(key)
    if entry is None:
        return False
    return bool(entry.enabled)


def _problem_state_for_key(
    key: str,
    *,
    panel,
    registry,
) -> Tuple[bool, bool]:
    """Return ``(warning, missing)`` matching the pill's status icon.

    Replays the same derivation
    :func:`nsl.ui.state._derive_status_icon` runs from
    :meth:`nsl.ui.panel.LoadoutPanel._set_pills_from_registry` so the
    ``Warnings`` sort and the YELLOW pill body wash always agree about
    which pills are "needs my attention this session."

    Returns ``(warning, missing)`` where ``warning`` is always ``False``
    under the runnable-python-loadout-chain architecture: there is no
    per-pill "failed to load" state any more; Nuke's walker either
    succeeded or crashed the interpreter, so if the panel is open every
    pill is either Enabled, Disabled, or Missing on disk. ``missing`` is
    ``True`` when the plugin's source folder doesn't resolve on disk
    (registry has no discovery record for it and Global doesn't carry it
    either).

    Best-effort: any failure walking the registry surfaces returns
    ``(False, False)`` rather than raising. The sort must never crash
    on a degraded registry - falling back to ``Clean`` keeps the pill
    in the bottom bucket, which is the correct fallback for an unknown
    problem state.
    """
    if registry is None:
        return (False, False)

    discovered = getattr(registry, "discovered_plugins", None) or {}
    global_model = getattr(registry, "global_model", None)
    in_discovery = key in discovered
    in_global = (
        global_model is not None and key in global_model.plugins
    )
    missing = not (in_discovery or in_global)
    return (False, missing)


def _folder_for_key(
    key: str,
    *,
    panel,
    registry,
) -> Tuple[int, Optional[str]]:
    """Return ``(priority, label)`` for *key*'s source Plugins Folder.

    Lower priority integer = higher priority, matching the *Plugins
    Folder Management* list order (top = highest priority). ``label``
    is the human-readable display name for the ``Folder of origin``
    divider header - the basename of the folder path so it fits a
    one-line divider without wrapping.

    Lookup chain:

    1. ``registry.discovered_plugins[key].source`` - absolute folder
       path the scanner recorded.
    2. ``panel.folder_card.entries()`` - user-facing folder list in
       priority order.

    Fallbacks:

    * Plugin without a discovery record / unknown source folder →
      ``(len(entries), None)`` - sorts to the bottom of ``Folder of
      origin``; the wiring layer renders the label as ``"Folder · ?"``.
    * Folder card missing entirely (degraded fixtures) → ``(0, None)``
      so the legacy "everything ties on priority 0 → A→Z fallback"
      behaviour is preserved.
    """
    folder_card = getattr(panel, "folder_card", None)
    if folder_card is None or not hasattr(folder_card, "entries"):
        return (0, None)
    try:
        entries = list(folder_card.entries())
    except Exception:  # noqa: BLE001 - folder card must not break sort
        return (0, None)
    unknown = len(entries)

    if registry is None:
        return (unknown, None)
    discovered = getattr(registry, "discovered_plugins", None) or {}
    discovery = discovered.get(key)
    if discovery is None:
        return (unknown, None)
    source = getattr(discovery, "source", None)
    if not source:
        return (unknown, None)
    for index, entry in enumerate(entries):
        if entry.path == source:
            return (index, _folder_basename(source))
    return (unknown, None)


def _folder_priority_for_key(
    key: str,
    *,
    panel,
    registry,
) -> int:
    """Back-compat shim. Returns priority only.

    Retained so any external caller that reaches for this helper by name
    keeps working. New call sites should use :func:`_folder_for_key` so
    they also pick up the divider label.
    """
    priority, _label = _folder_for_key(key, panel=panel, registry=registry)
    return priority


def _folder_basename(path: str) -> str:
    """Return *path*'s last non-empty path segment.

    Trailing-slash tolerant (``"/foo/bar/"`` → ``"bar"``). Falls back
    to the original string when it has no path separator (e.g. the
    user configured a bare folder name on a relative search root).
    """
    if not path:
        return ""
    # Trim trailing separators (POSIX or Windows) without depending on
    # ``os.path`` - keeps this helper independent of the filesystem.
    trimmed = path.rstrip("/\\")
    if not trimmed:
        return path  # path was all separators - return as-is
    # Find the last separator of either flavour and slice past it.
    last_sep = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    if last_sep < 0:
        return trimmed
    return trimmed[last_sep + 1:]


def build_sort_state_lookup(panel) -> StateLookup:
    """Return a ``key → SortableState`` callable closed over *panel*.

    Used by :func:`nsl.ui.wiring.sort_state.wire_sort_state_lookup` to
    install the production lookup on the panel (and on the filter
    pipeline). Each call to the returned closure queries the live
    panel state - domain mutations between sort runs (pill toggles,
    selection changes, loadout switches, folder reorders) are
    therefore reflected in the next sort without an explicit refresh.

    Per-key cost is small (a handful of dict lookups + a frozenset
    membership test on the selection). For typical NSL deployments
    (< 200 plugins) the total sort cost stays well under one frame
    even with the per-key resolved-active rebuild.
    """

    def lookup(key: str) -> SortableState:
        registry = getattr(panel, "registry", None)

        # enabled - next-restart intent under sparse-diff resolution.
        resolved = (
            getattr(registry, "resolved_active_for_diff", None)
            if registry is not None
            else None
        )
        entry = resolved.plugins.get(key) if resolved is not None else None
        enabled = bool(entry.enabled) if entry is not None else True

        # selected - live from the grid. ``selected_keys()`` returns a
        # list copy each call; wrap once in a set for O(1) membership.
        try:
            selected_set = set(panel.grid.selected_keys())
        except Exception:  # noqa: BLE001 - grid must not break sort
            selected_set = set()
        selected = key in selected_set

        # pending - compare current effective state vs session-loaded
        # baseline; same diff math the banner/counters use.
        baseline = (
            getattr(registry, "session_loaded_baseline", None)
            if registry is not None
            else None
        )
        pending = _pending_for_key(key, current=resolved, baseline=baseline)

        # warning / missing - replay the pill status-icon matrix.
        warning, missing = _problem_state_for_key(
            key, panel=panel, registry=registry
        )

        # folder_priority + folder_label - one folder-card walk yields
        # both, so the ``Folder of origin`` divider renders the friendly
        # basename (e.g. ``Folder · plugins_testA``) instead of the
        # full absolute path.
        folder_priority, folder_label = _folder_for_key(
            key, panel=panel, registry=registry
        )

        return SortableState(
            name=key,
            enabled=enabled,
            selected=selected,
            pending=pending,
            warning=warning,
            missing=missing,
            folder_priority=folder_priority,
            folder_label=folder_label,
        )

    return lookup


def build_key_to_folder(panel) -> Callable[[str], Optional[str]]:
    """Return a ``key → folder_path`` callable closed over *panel*.

    Companion to :func:`build_sort_state_lookup`. The filter pipeline
    consumes this for the folder-eye-toggle layer
    (:meth:`FilterPipeline.on_folder_visibility_changed`). Without a
    real mapping the eye toggles record state but never hide any
    pills (see :func:`nsl.ui.filter_pipeline.wire_filter_pipeline`'s
    default ``key_to_folder=None`` branch).

    The mapping is queried per-key; uses ``registry.discovered_plugins``
    as the source of truth. Returns ``None`` for any unknown key
    (degraded contexts, plugin not in the current scan) - the
    pipeline treats ``None`` as "doesn't belong to any hidden folder"
    which keeps the pill visible.
    """

    def key_to_folder(key: str) -> Optional[str]:
        registry = getattr(panel, "registry", None)
        if registry is None:
            return None
        # Global plugins map to the synthetic Global folder
        # marker so the filter pipeline's folder-visibility map
        # can hide them via the Global Plugins row's eye toggle
        # (parallel to the events.py path; the two handlers must
        # hold the same invariant).
        global_base = (
            getattr(registry, "global_plugin_names", None) or frozenset()
        )
        if key in global_base:
            from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
            return GLOBAL_PLUGINS_FOLDER_SENTINEL
        discovered = getattr(registry, "discovered_plugins", None) or {}
        discovery = discovered.get(key)
        if discovery is None:
            return None
        return getattr(discovery, "source", None) or None

    return key_to_folder


# ---------------------------------------------------------------------------
# Group dividers in the pill grid
# ---------------------------------------------------------------------------
#
# When the active sort mode groups pills (every mode except A->Z / Z->A),
# the pill grid renders a thin group divider between buckets: a small
# uppercase group label on the left followed by a 1px hairline
# stretching to the right edge of the grid.
#
# ``group_label_for_state`` returns the per-pill divider label given
# the pill's :class:`SortableState` and the active :class:`SortMode`.
# The grid layout walks its key list in sort order and inserts a
# divider whenever the label changes from the previous pill.
#
# A->Z and Z->A return ``None`` for every pill so the grid renders a
# tight 3-column pack with no dividers.


def group_label_for_state(state: SortableState, mode: SortMode) -> Optional[str]:
    """Return the divider label for *state* under *mode*, or ``None``.

    A ``None`` return signals "no divider before this pill" - used for
    the alphabetical modes and as the fall-through bucket on every
    pill before the first group transition (the grid does not insert
    a leading divider above the very first bucket either).

    Vocabulary:

    * ``Status``           -> ``"On"`` (enabled) / ``"Off"`` (disabled).
    * ``Selected``         -> ``"Selected"`` / ``"Unselected"``.
    * ``Changed state``    -> ``"Pending add"`` / ``"Pending remove"`` /
      ``"Unchanged"``.
    * ``Warnings``         -> ``"Missing"`` / ``"Warnings"`` / ``"Clean"``
      (matches the three-bucket comparator).
    * ``Folder of origin`` -> ``"Folder · <folder_label>"`` when
      ``state.folder_label`` is set; otherwise ``"Folder · ?"`` -
      the wiring layer populates ``folder_label`` via
      :func:`build_sort_state_lookup`.
    """
    if mode is SortMode.STATUS:
        return "On" if state.enabled else "Off"
    if mode is SortMode.SELECTED:
        return "Selected" if state.selected else "Unselected"
    if mode is SortMode.CHANGED_STATE:
        if state.pending == "green":
            return "Pending add"
        if state.pending == "red":
            return "Pending remove"
        return "Unchanged"
    if mode is SortMode.WARNINGS:
        if state.missing:
            return "Missing"
        if state.warning:
            return "Warnings"
        return "Clean"
    if mode is SortMode.FOLDER_OF_ORIGIN:
        if state.folder_label:
            return f"Folder · {state.folder_label}"
        return "Folder · ?"
    # A_TO_Z / Z_TO_A - no grouping.
    return None
