"""Pure state-derivation helpers - domain shapes to widget inputs.

No Qt and no ``import nuke``. The only I/O is reading the loadouts
directory. The Registry calls these so ``apply_op_result`` can re-emit
every widget's state from one place.

Each helper returns an existing widget-input type, so the panel needs
no adapter layer of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from nsl.boot.dispatcher import DispatcherState
from nsl.constants import (
    GLOBAL_LOADOUT_DIR_NAME,
    RESERVED_LOADOUT_STEM,
)
from nsl.data.loadout_file import LoadoutFile, PluginEntry
from nsl.ui.banner import BannerKind
from nsl.ui.folder_card import FolderEntry, Health
from nsl.ui.loadout_strip import GLOBAL_LOADOUT_NAME, Loadout
from nsl.ui.pill import PillState, Source, StatusIcon, Tint

__all__ = [
    "PendingDiff",
    "loadout_list_from",
    "folder_list_from",
    "pending_diff",
    "pill_state_from",
]


@dataclass(frozen=True)
class PendingDiff:
    """Banner state returned by :func:`pending_diff`.

    ``count == 0`` means hide the banner. Anything else means show it.
    """

    count: int
    kind: BannerKind = BannerKind.PENDING_CHANGES


def loadout_list_from(
    loadouts_dir: Path,
    state: DispatcherState,
    *,
    active_is_dirty: bool = False,
    dirty_stems: Optional[Iterable[str]] = None,
    has_global_layer: bool = True,
    global_loadout_copy_exists: bool = False,
) -> List[Loadout]:
    """Enumerate the per-Loadout folders into :class:`Loadout` rows.

    A subfolder of ``loadouts_dir`` counts as a Loadout when it holds an
    ``init.py``. Row names are bare stems.

    * User Loadouts come first, sorted by name.
    * The ``Global`` row is synthesised last, from the Global resolver.
      Any literal ``Global`` folder on disk is skipped.
    * Custom is in-memory only, so an on-disk Custom folder is skipped
      too. The row appears when Custom is active, has unsaved edits, or
      no Global layer is configured.
    * ``active_is_dirty`` puts ``(*)`` on the row matching
      ``state.active``. ``dirty_stems`` puts it on the other rows.
    * ``global_loadout_copy_exists`` hides the user-land
      ``Global_Loadout`` row. That name is a staging area, not a
      Loadout the user can activate.

    When ``loadouts_dir`` does not exist yet, only the Custom and Global
    rows that apply are returned.
    """
    parked = set(dirty_stems or ())
    from nsl.constants import DEFAULT_CUSTOM_LOADOUT_STEM
    custom_display = DEFAULT_CUSTOM_LOADOUT_STEM

    if not loadouts_dir.exists():
        # With no Global layer, Custom is the first-run home slot and
        # shows alone.
        rows: list[Loadout] = []
        custom_parked = DEFAULT_CUSTOM_LOADOUT_STEM in parked
        active_is_custom_first_run = (
            state.active == DEFAULT_CUSTOM_LOADOUT_STEM
        )
        if (not has_global_layer) or custom_parked or active_is_custom_first_run:
            rows.append(
                Loadout(
                    name=custom_display,
                    is_global=False,
                    is_dirty=True,
                )
            )
        if has_global_layer:
            rows.append(Loadout(name=GLOBAL_LOADOUT_NAME, is_global=True))
        return rows

    user_stems: list[str] = []
    for child in sorted(loadouts_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name == RESERVED_LOADOUT_STEM:
            continue
        if child.name == DEFAULT_CUSTOM_LOADOUT_STEM:
            continue
        if (
            global_loadout_copy_exists
            and child.name == GLOBAL_LOADOUT_DIR_NAME
        ):
            continue
        if not (child / "init.py").is_file():
            continue
        user_stems.append(child.name)

    active_stem = (
        state.active
        if state.active and state.active != RESERVED_LOADOUT_STEM
        else None
    )

    active_is_custom = state.active == DEFAULT_CUSTOM_LOADOUT_STEM
    custom_has_parked_edits = DEFAULT_CUSTOM_LOADOUT_STEM in parked
    show_custom = (
        active_is_custom
        or custom_has_parked_edits
        or (not has_global_layer)
    )

    out: list[Loadout] = []
    for stem in user_stems:
        is_active_row = active_stem is not None and stem == active_stem
        is_parked_dirty = (not is_active_row) and stem in parked
        out.append(
            Loadout(
                name=stem,
                is_global=False,
                is_dirty=(is_active_row and active_is_dirty) or is_parked_dirty,
            )
        )

    # Custom always shows ``(*)``. The slot is unsaved by definition,
    # not by value comparison.
    if show_custom:
        out.append(
            Loadout(
                name=custom_display,
                is_global=False,
                is_dirty=True,
            )
        )

    if has_global_layer:
        out.append(Loadout(name=GLOBAL_LOADOUT_NAME, is_global=True))
    return out


def folder_list_from(
    user_plugin_dirs: Iterable[str],
    *,
    visibility: Optional[Mapping[str, bool]] = None,
    health: Optional[Mapping[str, Health]] = None,
    global_model: Optional[LoadoutFile] = None,
    global_plugins_dir: str = "",
) -> List[FolderEntry]:
    """Build :class:`FolderEntry` rows from ``user_plugin_dirs``.

    ``visibility`` and ``health`` are session-only. Neither is
    persisted. Defaults are visible and ``Health.HEALTHY``.

    A ``global_model`` that carries plugins adds one more row at the
    end, for the Global layer. It stays last, because Global is the
    lowest layer. ``global_plugins_dir`` fills that row's tooltip.
    """
    vis = visibility or {}
    health_map = health or {}
    rows: List[FolderEntry] = [
        FolderEntry(
            path=path,
            visible=vis.get(path, True),
            health=health_map.get(path, Health.HEALTHY),
        )
        for path in user_plugin_dirs
    ]
    if global_model is not None and global_model.plugins:
        from nsl.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
        rows.append(
            FolderEntry(
                path=GLOBAL_PLUGINS_FOLDER_SENTINEL,
                visible=vis.get(GLOBAL_PLUGINS_FOLDER_SENTINEL, True),
                health=health_map.get(
                    GLOBAL_PLUGINS_FOLDER_SENTINEL, Health.HEALTHY
                ),
                is_global=True,
                tooltip_path=str(global_plugins_dir or ""),
            )
        )
    return rows


def pending_diff(
    *,
    current_active: Optional[LoadoutFile],
    saved_baseline: Optional[LoadoutFile],
    kind: BannerKind = BannerKind.PENDING_CHANGES,
) -> PendingDiff:
    """Count Plugin Names that diverge from the saved baseline.

    The baseline is the Loadout's last-saved-on-disk state, not the boot
    snapshot. Save moves the baseline forward, so toggling a pill on and
    off again gives a count of 0.

    ``enabled=False`` counts as absent. A Plugin is a pending change
    when its enabled and gui_only pair differs from the baseline.
    """
    baseline_eff = _effective_plugins(saved_baseline)
    current_eff = _effective_plugins(current_active)

    all_keys = set(baseline_eff) | set(current_eff)
    count = sum(
        1 for k in all_keys if baseline_eff.get(k) != current_eff.get(k)
    )
    return PendingDiff(count=count, kind=kind)


def pending_diff_split(
    *,
    current_active: Optional[LoadoutFile],
    saved_baseline: Optional[LoadoutFile],
) -> tuple:
    """Return ``(pending_add, pending_del)`` against the saved baseline.

    ``pending_add`` will load on the next Save and ``pending_del`` will
    unload. The sum can be less than :func:`pending_diff` ``.count``. An
    entry that stays enabled but changes ``gui_only`` counts there and
    in neither of these.
    """
    baseline_eff = _effective_plugins(saved_baseline)
    current_eff = _effective_plugins(current_active)
    add = sum(1 for k in current_eff if k not in baseline_eff)
    delete = sum(1 for k in baseline_eff if k not in current_eff)
    return (add, delete)


def _effective_plugins(
    model: Optional[LoadoutFile],
) -> Mapping[str, "PluginEntry"]:
    """Drop ``enabled=False`` entries from one Loadout's plugins map.

    For divergence counting, disabled is the same as absent. No Global
    resolution happens here. The caller passes resolved models.
    """
    if model is None:
        return {}
    return {k: v for k, v in model.plugins.items() if v.enabled}


def pill_state_from(
    plugin_name: str,
    *,
    active: Optional[LoadoutFile],
    global_model: Optional[LoadoutFile],
    global_plugin_names: Iterable[str] = (),
    selected: bool = False,
    loaded_in_session: Optional[bool] = None,
    diagnostic_available: bool = False,
    failure_label: Optional[str] = None,
    saved_baseline: Optional[LoadoutFile] = None,
    force_dirty_plugins: Iterable[str] = (),
    source_missing: bool = False,
    panic_engaged: bool = False,
    active_is_custom: bool = False,
    session_gui_only: Optional[bool] = None,
) -> PillState:
    """Compose a :class:`PillState` for ``plugin_name`` from domain state.

    Sparse diff order: the active Loadout entry, then the Global entry,
    then a default. The default is ``enabled=True``, except for a
    user-added Plugin in the Global view, which defaults to off.

    ``diverges_from_global`` fires only for a GLOBAL pill whose active
    Loadout override differs from the Global entry. The renderer turns
    that flag into the purple border.
    """
    entry = None
    if active is not None:
        entry = active.plugins.get(plugin_name)
    if entry is None and global_model is not None:
        entry = global_model.plugins.get(plugin_name)

    global_set = frozenset(global_plugin_names)
    source = (
        Source.GLOBAL if plugin_name in global_set else Source.USER_ADDED
    )

    # Under panic the user chain never runs, so resolve a Global name
    # from the Global model. A user override cannot apply, and showing
    # it makes a live Plugin read as pending disable.
    if panic_engaged and source is Source.GLOBAL:
        g_entry = (
            global_model.plugins.get(plugin_name)
            if global_model is not None
            else None
        )
        entry = g_entry or PluginEntry(enabled=True, gui_only=False)

    if entry is None:
        # No entry in either layer. In the Global view user plugins
        # default to off, because Global is the TD's read-only set. The
        # same rule lives in ``effective_state``, so the panel and the
        # loader agree.
        if active is None and source is Source.USER_ADDED:
            entry = PluginEntry(enabled=False, gui_only=False)
        else:
            entry = PluginEntry(enabled=True, gui_only=False)

    # Suppressed under panic for the same reason as the re-attribution
    # above.
    diverges = False
    if (
        source is Source.GLOBAL
        and active is not None
        and global_model is not None
        and not panic_engaged
    ):
        override = active.plugins.get(plugin_name)
        base = global_model.plugins.get(plugin_name)
        diverges = override is not None and override != base

    status_icon = _derive_status_icon(
        enabled=entry.enabled,
        loaded_in_session=loaded_in_session,
        diagnostic_available=diagnostic_available,
    )

    # Tint compares this session against the next restart, not against
    # the saved file. Driving it from ``saved_baseline`` would paint
    # every freshly-discovered Plugin GREEN. See ``_derive_tint``.
    tint = _derive_tint(enabled=entry.enabled, status_icon=status_icon)
    # The source folder is gone. YELLOW is the Missing body, and the
    # renderer adds a red border from ``PillState.source_missing``.
    if source_missing:
        tint = Tint.YELLOW

    # Compare explicit presence in the active model against the saved
    # baseline, not the resolved values. A folder-add writes an explicit
    # entry with nothing on disk to match it. Resolving both sides
    # through one default would glow that pill green before a Save.
    is_dirty_vs_saved = False
    if active is not None and tint in (Tint.GREEN, Tint.RED):
        if active_is_custom:
            # Custom never persists on its own, so no pill in it can be
            # committed. Force the dirty path so the renderer skips the
            # saved border and halo.
            is_dirty_vs_saved = True
        elif plugin_name in frozenset(force_dirty_plugins):
            # A folder-add marks its own plugins dirty, so the pill
            # matches the ``(*)`` and the enabled Save button. Other
            # pills keep the value comparison below.
            is_dirty_vs_saved = True
        else:
            m_explicit = plugin_name in active.plugins
            d_explicit = (
                saved_baseline is not None
                and plugin_name in saved_baseline.plugins
            )
            if m_explicit != d_explicit:
                is_dirty_vs_saved = True
            elif m_explicit:
                is_dirty_vs_saved = (
                    active.plugins[plugin_name]
                    != saved_baseline.plugins[plugin_name]
                )

    # ``session_gui_only`` is the gui_only flag of what loaded this
    # session. None means the Plugin did not load, so there is no GUI
    # signal. A disabled Plugin's GUI flag does not matter.
    gui_pending_on = False
    gui_pending_off = False
    if entry.enabled and session_gui_only is not None:
        if session_gui_only is False and entry.gui_only is True:
            gui_pending_on = True
        elif session_gui_only is True and entry.gui_only is False:
            gui_pending_off = True

    # ``gui_committed`` is True only when the GUI change is on disk and
    # will apply on the next restart. It gates the committed visuals.
    # The flags above drive the editing visuals. The comparison mirrors
    # ``is_dirty_vs_saved``, on the ``gui_only`` field alone.
    gui_committed = False
    if (
        (gui_pending_on or gui_pending_off)
        and active is not None
        and not active_is_custom
        and plugin_name not in frozenset(force_dirty_plugins)
    ):
        m_explicit = plugin_name in active.plugins
        d_explicit = (
            saved_baseline is not None
            and plugin_name in saved_baseline.plugins
        )
        if m_explicit and d_explicit:
            gui_committed = (
                active.plugins[plugin_name].gui_only
                == saved_baseline.plugins[plugin_name].gui_only
            )
        elif not m_explicit and not d_explicit:
            gui_committed = True
        # Presence differs, so the change is not committed.

    return PillState(
        plugin_name=plugin_name,
        source=source,
        enabled=entry.enabled,
        status_icon=status_icon,
        tint=tint,
        selected=selected,
        diverges_from_global=diverges,
        gui_only=entry.gui_only,
        has_diagnostic=diagnostic_available,
        failure_label=failure_label,
        is_dirty_vs_saved=is_dirty_vs_saved,
        source_missing=source_missing,
        panic_engaged=panic_engaged,
        gui_pending_on=gui_pending_on,
        gui_pending_off=gui_pending_off,
        gui_committed=gui_committed,
    )


def _derive_tint(*, enabled: bool, status_icon: "StatusIcon") -> "Tint":
    """Derive pill body tint from the (enabled, status) pair.

    Tint compares what loaded this Nuke session against what is enabled
    for the next restart.

    ===============  ===========  ===============================
    ``enabled``      ``status``   Tint
    ===============  ===========  ===============================
    True             LOADED       NEUTRAL  (no diff)
    True             other        GREEN    (pending enable)
    False            LOADED       RED      (pending disable)
    False            other        NEUTRAL  (off, was never loaded)
    any              FAILED       YELLOW   (problem - overrides diff)
    any              MISSING      YELLOW   (problem - overrides diff)
    ===============  ===========  ===============================

    YELLOW wins, because a failed or missing Plugin needs attention
    whatever the restart diff says.
    """
    from nsl.ui.pill import Tint

    if status_icon in (StatusIcon.FAILED, StatusIcon.MISSING):
        return Tint.YELLOW
    loaded = status_icon is StatusIcon.LOADED
    if enabled and not loaded:
        return Tint.GREEN
    if loaded and not enabled:
        return Tint.RED
    return Tint.NEUTRAL


def _derive_status_icon(
    *,
    enabled: bool,
    loaded_in_session: Optional[bool],
    diagnostic_available: bool,
) -> StatusIcon:
    """Derive the status icon (see :func:`pill_state_from`).

    The icon reports what is in memory this session, not what the user
    picked for the next restart. So ``loaded_in_session is True`` must
    be checked before the ``not enabled`` shortcut. The other order
    returns EMPTY first, and the RED pending-disable tint never fires.
    """
    if loaded_in_session is True:
        return StatusIcon.LOADED
    if not enabled:
        return StatusIcon.EMPTY
    if loaded_in_session is None:
        return StatusIcon.LOADED
    if diagnostic_available:
        return StatusIcon.FAILED
    return StatusIcon.PENDING
