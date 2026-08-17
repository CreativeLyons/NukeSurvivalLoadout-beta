"""Per-session sort comparators for the Plugins grid.

Eight sort modes. A -> Z is the secondary sort in every other mode.
The sort choice is panel-local and resets when Nuke quits.

:class:`SortMode` and :data:`SORT_MODE_ORDER` are re-exported from
:mod:`nsl.ui.grid_toolbar`, which owns the labels. Qt imports go
through :mod:`nsl.compat`.
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
    """The per-pill data the sort axes read.

    * ``name``            - drives ``A -> Z`` and ``Z -> A``, and the
      secondary sort in every other mode.
    * ``enabled``         - next-restart state. Drives ``Status``.
    * ``gui_only``        - drives ``GUI-only``. Read off the
      sparse-diff-resolved entry, so it matches the pill's GUI chip.
    * ``selected``        - drives ``Selected``.
    * ``pending``         - ``"green"``, ``"red"`` or ``None``. Drives
      ``Changed state``.
    * ``warning``         - load failed. Middle bucket of ``Warnings``.
    * ``missing``         - not in the Plugin scan. Top bucket of
      ``Warnings``.
    * ``folder_priority`` - index into the Plugins Folder list. Lower
      is higher priority. Drives ``Folder of origin``.
    """

    name: str
    enabled: bool = True
    gui_only: bool = False
    selected: bool = False
    pending: Optional[str] = None  # "green" | "red" | None
    warning: bool = False
    missing: bool = False
    folder_priority: int = 0
    # Basename of the folder path, for the ``Folder of origin`` divider.
    # ``None`` renders as ``"Folder · ?"``.
    folder_label: Optional[str] = None


StateLookup = Callable[[str], SortableState]


# ---------------------------------------------------------------------------
# Primary-axis key functions - one per mode, A → Z always secondary
# ---------------------------------------------------------------------------


def _key_a_to_z(s: SortableState) -> Tuple:
    """Alphabetical ascending. There is no secondary axis."""
    return (s.name.lower(),)


def _key_z_to_a(s: SortableState) -> Tuple:
    """Alphabetical descending.

    Returns the ascending key. :func:`sort_keys` passes
    ``reverse=True`` for this mode.
    """
    return (s.name.lower(),)


def _key_status(s: SortableState) -> Tuple:
    """Primary axis: enabled first, then disabled. Secondary A → Z."""
    return (not s.enabled, s.name.lower())


def _key_gui_only(s: SortableState) -> Tuple:
    """Primary axis: loads-everywhere first, then GUI-only. Secondary A → Z.

    This axis passes the bare flag so the unflagged pills lead. Every
    other boolean axis in this module uses ``not flag``.

    The split ignores ``enabled``, so a disabled GUI-only Plugin still
    groups with its own kind. ``Status`` is the mode for slicing by
    enabled.
    """
    return (s.gui_only, s.name.lower())


def _key_selected(s: SortableState) -> Tuple:
    """Primary axis: selected first, then unselected. Secondary A → Z."""
    return (not s.selected, s.name.lower())


def _pending_bucket(pending: Optional[str]) -> int:
    """Map pending state to a sort bucket.

    Green (will be added) is 0, red (will be removed) is 1, unchanged
    is 2.
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

    Missing ranks above Failed. A Plugin that left the disk needs the
    user to check the folder first.
    """
    if s.missing:
        bucket = 0
    elif s.warning:
        bucket = 1
    else:
        bucket = 2
    return (bucket, s.name.lower())


def _key_folder_of_origin(s: SortableState) -> Tuple:
    """Primary axis: folder priority (highest first). Secondary A → Z."""
    return (s.folder_priority, s.name.lower())


#: Mode -> primary key function. :func:`sort_keys` reads from here.
COMPARATORS: Dict[SortMode, Callable[[SortableState], Tuple]] = {
    SortMode.A_TO_Z: _key_a_to_z,
    SortMode.Z_TO_A: _key_z_to_a,
    SortMode.STATUS: _key_status,
    SortMode.GUI_ONLY: _key_gui_only,
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

    Pure and stable. Never mutates ``keys`` or anything
    ``state_lookup`` returns.

    Args:
        keys: Pill keys, usually the grid's full list or a filtered
            subset.
        mode: One of the eight :class:`SortMode` members.
        state_lookup: Maps a key to its :class:`SortableState`. Called
            exactly once per key.

    Raises:
        ValueError: ``mode`` is not a known sort mode.
    """
    if mode not in COMPARATORS:
        raise ValueError(f"unknown sort mode: {mode!r}")

    keys_list = list(keys)
    key_fn = COMPARATORS[mode]

    decorated = [(key_fn(state_lookup(k)), k) for k in keys_list]

    reverse = mode is SortMode.Z_TO_A
    decorated.sort(key=lambda pair: pair[0], reverse=reverse)
    return [k for _, k in decorated]


# ---------------------------------------------------------------------------
# Wiring helper - the orchestrator's single integration point
# ---------------------------------------------------------------------------


def wire_sort(panel) -> None:
    """Connect the grid-toolbar sort dropdown to the grid.

    On each ``sort_mode_changed`` the grid re-renders in the new order,
    after the filter and with the selection preserved.

    1. Visible keys come from ``panel.filter_visible_keys()`` if it is
       installed, otherwise from ``panel.grid.keys()``.
    2. Sort with ``panel.sort_state_lookup`` if it is installed,
       otherwise with a default that sorts on name alone.
    3. Capture the selection, call ``panel.rebuild_grid(new_keys)``,
       then re-apply the selection.

    Writes ``panel._current_sort_mode`` and ``panel._resort_grid`` so
    the filter and selection helpers can re-trigger a sort. Nothing is
    persisted, so a new Nuke session opens on ``A -> Z``.
    """

    toolbar = panel.grid_toolbar

    panel._current_sort_mode = toolbar.current_sort_mode()

    def _default_state_lookup(key: str) -> SortableState:
        """Permissive lookup used until the real one is installed.

        Every axis stays at its default, so all eight modes fall back
        to alphabetical order. The toolbar still visibly re-orders the
        grid before the domain wiring lands.
        """
        return SortableState(name=key)

    def _resolve_state_lookup() -> StateLookup:
        return getattr(panel, "sort_state_lookup", None) or _default_state_lookup

    def _resolve_visible_keys() -> List[str]:
        """Return the post-filter visible key set.

        The filter pipeline installs ``panel.filter_visible_keys`` as a
        no-argument callable. Without it, fall back to the grid's full
        key list.
        """
        getter = getattr(panel, "filter_visible_keys", None)
        if callable(getter):
            try:
                visible = getter()
                return list(visible)
            except Exception:
                # A broken filter must not crash the sort.
                pass
        return panel.grid.keys()

    def _resort_grid(*_args) -> None:
        """Compose filter → sort → rebuild, and keep the selection.

        Exposed as ``panel._resort_grid`` so peer wiring helpers can
        re-trigger a sort after their own work.

        With ``panel.filter_pipeline`` present the sort goes through
        the pipeline, so the dataflow stays
        ``master -> filter -> sort -> set_keys``. The direct path calls
        ``rebuild_grid(master_sorted)`` and would drop an active
        filter.
        """
        # Re-entrancy guard. ``grid.set_keys`` emits an empty
        # ``selection_changed`` during the sort. The inner sort would
        # then read a cleared selection.
        if getattr(panel, "_sort_in_progress", False):
            return
        panel._sort_in_progress = True
        try:
            mode = toolbar.current_sort_mode()
            panel._current_sort_mode = mode

            pipeline = getattr(panel, "filter_pipeline", None)
            lookup = _resolve_state_lookup()
            if pipeline is not None:
                # ``grid.set_keys`` clears the selection and emits an
                # empty ``selection_changed``, which disables the bulk
                # buttons. The restore below must emit, so the bridge
                # re-enables them.
                selected = list(panel.grid.selected_keys())
                # Skip the redundant install. It forces a second
                # pipeline recompute. That one runs after ``set_keys``
                # cleared the selection, so the Selected mode falls
                # back to alpha order.
                if pipeline._sort_state_lookup is not lookup:
                    pipeline.set_sort_state_lookup(lookup)
                pipeline.set_sort_mode(mode)
                if selected:
                    panel.grid.select_keys(selected)
                return

            # Pipeline-less path.
            visible_keys = _resolve_visible_keys()
            new_keys = sort_keys(visible_keys, mode, lookup)

            # Capture the selection before the swap and restore it
            # after. The new grid only honours keys it contains.
            selected = list(panel.grid.selected_keys())

            panel.rebuild_grid(new_keys)

            # Match the pipeline path's dividers. ``set_group_labels``
            # is a no-op on grids that do not implement it.
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

    panel._resort_grid = _resort_grid

    # The toolbar emits the label string. The slot ignores it and
    # reads ``current_sort_mode()``.
    toolbar.sort_mode_changed.connect(_resort_grid)

    # Selection changes arrive by marquee, click, or ``select_keys``,
    # and none of them fire ``sort_mode_changed``. Without this
    # connection the user must re-pick ``Selected`` after every change
    # to see the grid re-order.
    def _on_selection_changed_for_sort(*_args) -> None:
        if panel._current_sort_mode is not SortMode.SELECTED:
            return
        _resort_grid()

    try:
        panel.grid.selection_changed.connect(_on_selection_changed_for_sort)
    except AttributeError:
        # Grids without the signal do not get the selection re-sort.
        pass


# ---------------------------------------------------------------------------
# Production state-lookup builders
# ---------------------------------------------------------------------------


def _pending_for_key(
    key: str,
    *,
    current,
    baseline,
) -> Optional[str]:
    """Return the ``Changed state`` bucket for *key*.

    ``"green"`` when the pending state is enabled and the baseline is
    not. ``"red"`` for the reverse. ``None`` otherwise.

    An entry with ``enabled=False`` counts as absent. The pill body and
    the banner use the same rule, so the three never disagree.
    """
    pending_enabled = _key_is_effective(key, current)
    loaded_enabled = _key_is_effective(key, baseline)
    if pending_enabled and not loaded_enabled:
        return "green"
    if loaded_enabled and not pending_enabled:
        return "red"
    return None


def _key_is_effective(key: str, model) -> bool:
    """``True`` when *key* is in *model* with ``enabled=True``.

    A ``None`` model returns ``False``.
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

    ``warning`` is always ``False``. The loadout chain is runnable
    Python, so there is no per-pill load-failed state any more.
    ``missing`` is ``True`` when neither the scan nor Global carries
    the plugin.

    Any failure walking the registry returns ``(False, False)``. The
    sort must not crash on a degraded registry.
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

    ``label`` is the folder basename, for the ``Folder of origin``
    divider header. The priority comes from
    ``panel.folder_card.entries()`` matched against
    ``registry.discovered_plugins[key].source``.

    * Unknown source folder -> ``(len(entries), None)``, which sorts to
      the bottom.
    * No folder card at all -> ``(0, None)``, so everything ties and
      falls back to A -> Z.
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
    """Back-compat shim for external callers. Priority only.

    New call sites should use :func:`_folder_for_key`, which also
    returns the divider label.
    """
    priority, _label = _folder_for_key(key, panel=panel, registry=registry)
    return priority


def _folder_basename(path: str) -> str:
    """Return *path*'s last non-empty segment.

    Tolerates trailing separators, so ``"/foo/bar/"`` gives ``"bar"``.
    """
    if not path:
        return ""
    # Trim trailing separators without using ``os.path``.
    trimmed = path.rstrip("/\\")
    if not trimmed:
        return path
    last_sep = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    if last_sep < 0:
        return trimmed
    return trimmed[last_sep + 1:]


def build_sort_state_lookup(panel) -> StateLookup:
    """Return a ``key → SortableState`` callable closed over *panel*.

    :func:`nsl.ui.wiring.sort_state.wire_sort_state_lookup` installs it
    on the panel and on the filter pipeline. The closure reads live
    panel state on every call, so pill toggles, selection changes and
    folder reorders reach the next sort with no explicit refresh.
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

        # gui_only - the same resolved entry, so the sort bucket and
        # the pill's GUI chip read one result. An absent entry falls
        # back to False.
        gui_only = (
            bool(getattr(entry, "gui_only", False))
            if entry is not None
            else False
        )

        # selected - live from the grid. ``selected_keys()`` returns a
        # new list each call, so wrap it once.
        try:
            selected_set = set(panel.grid.selected_keys())
        except Exception:  # noqa: BLE001 - grid must not break sort
            selected_set = set()
        selected = key in selected_set

        # pending - current effective state against the session-loaded
        # baseline. Same diff math the banner uses.
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

        # One folder-card walk gives both the priority and the divider
        # label.
        folder_priority, folder_label = _folder_for_key(
            key, panel=panel, registry=registry
        )

        return SortableState(
            name=key,
            enabled=enabled,
            gui_only=gui_only,
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

    The filter pipeline uses it for the folder eye toggles. Without a
    real mapping the toggles record state but hide no pills.

    The source of truth is ``registry.discovered_plugins``. An unknown
    key returns ``None``, which the pipeline reads as "not in a hidden
    folder" and keeps the pill visible.
    """

    def key_to_folder(key: str) -> Optional[str]:
        registry = getattr(panel, "registry", None)
        if registry is None:
            return None
        # Global plugins map to a sentinel folder so the Global Plugins
        # row's eye toggle can hide them. ``events.py`` holds the same
        # invariant.
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


def group_label_for_state(state: SortableState, mode: SortMode) -> Optional[str]:
    """Return the divider label for *state* under *mode*, or ``None``.

    ``None`` means no divider before this pill, which is every pill in
    the alphabetical modes. The grid walks its keys in sort order and
    inserts a divider only where the label changes.
    """
    if mode is SortMode.STATUS:
        return "On" if state.enabled else "Off"
    if mode is SortMode.GUI_ONLY:
        return "GUI-only" if state.gui_only else "Loads everywhere"
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
