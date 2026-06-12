"""Pure state-derivation helpers - domain shapes → widget-input shapes.

Qt-free, no I/O on the hot path, no side effects beyond reading the
loadouts directory (a deterministic function of its contents). The
Registry layer calls these so `apply_op_result` can re-emit every
widget's state from one place after a domain mutation.

Each helper produces an existing widget-input type so wiring stays
flat: no parallel hierarchy of "model" objects, no adapter layer
inside the panel - the helpers feed `LoadoutStrip.set_loadouts`,
`FolderCard.set_entries`, `Banner.set_state`, `PluginPill` directly.

No ``import nuke`` and no Qt imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from NukeSurvivalLoadout.boot.dispatcher import DispatcherState
from NukeSurvivalLoadout.constants import (
    GLOBAL_LOADOUT_DIR_NAME,
    RESERVED_LOADOUT_STEM,
)
from NukeSurvivalLoadout.data.loadout_file import LoadoutFile, PluginEntry
from NukeSurvivalLoadout.ui.banner import BannerKind
from NukeSurvivalLoadout.ui.folder_card import FolderEntry, Health
from NukeSurvivalLoadout.ui.loadout_strip import GLOBAL_LOADOUT_NAME, Loadout
from NukeSurvivalLoadout.ui.pill import PillState, Source, StatusIcon, Tint

__all__ = [
    "PendingDiff",
    "loadout_list_from",
    "folder_list_from",
    "pending_diff",
    "pill_state_from",
]


@dataclass(frozen=True)
class PendingDiff:
    """Banner-state carrier returned by :func:`pending_diff`.

    ``count == 0`` means the banner should be hidden; non-zero means
    the panel calls ``banner.set_state(diff.kind, diff.count)`` and
    ``banner.show()``.
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
    """Enumerate per-loadout folders into :class:`Loadout` rows.

    Under the runnable-python-loadout-chain architecture each user
    Loadout is a folder ``<loadouts_dir>/<stem>/`` containing ``init.py``.
    This helper lists those folders, applies dirty / Custom / Global
    rules, and returns the strip-input list.

    Row names are bare stems; the strip widget keys off the
    ``GLOBAL_LOADOUT_NAME`` / ``CUSTOM_LOADOUT_NAME`` constants (bare
    ``"Global"`` / ``"Custom"``). The JSON-era ``.loadout`` display
    suffix is retired.

    Behaviour:
        * Lists subfolders only - no file iteration.
        * A folder counts as a loadout when it contains an ``init.py``;
          empty / stub folders are ignored.
        * Synthesises the ``Global`` row as the last row regardless of
          whether a folder named ``Global`` exists; Global comes from
          the Global resolver, not the user's loadouts dir.
        * Any literal ``Global`` folder on disk is skipped (the
          synthesised row supersedes it).
        * Returns alpha-sorted user Loadouts followed by Global.
        * ``active_is_dirty`` applies the ``(*)`` indicator on the row
          whose stem matches ``state.active``.
        * ``dirty_stems`` applies the ``(*)`` indicator on NON-active
          rows whose stem appears in the set.
        * ``global_loadout_copy_exists`` (case B - a ``Global_Loadout``
          copy lives in the NSL Global folder) hides the user-land
          ``Global_Loadout`` row: that name is then a staging area, not
          an activatable loadout.

    Returns the empty-state list ``[Global]`` when the dir doesn't
    exist yet (first-run before any user Loadout has been saved).
    """
    parked = set(dirty_stems or ())
    from NukeSurvivalLoadout.constants import DEFAULT_CUSTOM_LOADOUT_STEM
    custom_display = DEFAULT_CUSTOM_LOADOUT_STEM

    if not loadouts_dir.exists():
        # The Global row exists iff a Global layer is configured.
        # Without one, Custom is the home slot (first-run rule) and
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
            # Custom is in-memory only - handled in the reserved slot
            # below; ignore any on-disk Custom folder.
            continue
        if (
            global_loadout_copy_exists
            and child.name == GLOBAL_LOADOUT_DIR_NAME
        ):
            # Case B: the staged user-land copy is hidden, never
            # activatable; the Global row already represents it.
            continue
        if not (child / "init.py").is_file():
            continue
        user_stems.append(child.name)

    active_stem = (
        state.active
        if state.active and state.active != RESERVED_LOADOUT_STEM
        else None
    )

    # Visibility: Custom is hidden from the dropdown unless it's
    # currently-active, carries parked edits, or no Global layer is
    # configured.
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

    # Custom carries the (*) suffix when shown - the wildcard slot is
    # unsaved by definition, not by value comparison.
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

    The user's plugin source folders live as ``plugins_A``/``plugins_B``
    vars at the top of the active loadout file. The Registry derives the
    absolute-path list at construction time and threads it through
    ``user_plugin_dirs`` so this pure helper stays free of model-shape
    coupling.

    ``visibility`` and ``health`` are session-only - visibility is the
    eye-toggle state owned by the panel (never persisted), health is
    the latest scan result. Defaults: visible=True, health=HEALTHY.

    When ``global_model`` is non-None AND carries plugins, appends a
    synthetic ``FolderEntry`` for the resolved Global layer at the END
    of the list - pinned-to-bottom so it always sits below user
    folders (Global is the lowest-priority base layer).
    ``global_plugins_dir`` feeds that row's tooltip (friendly label on
    the row, full path on hover).
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
        from NukeSurvivalLoadout.constants import GLOBAL_PLUGINS_FOLDER_SENTINEL
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
    """Count Plugin Names whose state diverges from the saved baseline.

    The diff baseline is the active loadout's last-saved-on-disk state,
    NOT the boot snapshot. When a loadout is
    loaded (panel boot or switch) its on-disk state is the baseline;
    Save advances the baseline; toggling pills in memory diverges from
    it. Toggling on-then-off returns to baseline → count 0.

    Effective state ignores entries with ``enabled=False`` (they
    resolve to "not loaded" - equivalent to absence). A plugin counts
    as a pending change iff its full effective entry (enabled +
    gui_only) differs between baseline and current.
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

    ``pending_add`` - keys in current effective state but absent from
    the baseline (will *load* on next Save). ``pending_del`` - keys in
    the baseline but absent from current (will *unload* on next Save).
    Sum may be < :func:`pending_diff` ``.count``: entries that are
    still enabled but otherwise changed (e.g. ``gui_only`` flipped)
    count toward ``pending_diff`` but neither add nor delete.
    """
    baseline_eff = _effective_plugins(saved_baseline)
    current_eff = _effective_plugins(current_active)
    add = sum(1 for k in current_eff if k not in baseline_eff)
    delete = sum(1 for k in baseline_eff if k not in current_eff)
    return (add, delete)


def _effective_plugins(
    model: Optional[LoadoutFile],
) -> Mapping[str, "PluginEntry"]:
    """Drop ``enabled=False`` entries from a single loadout's plugins map.

    ``enabled=False`` represents "not loaded" which is equivalent to
    absence for divergence counting. Compares model-against-model
    (current vs saved baseline) so Global resolution lives at the
    caller - the saved-baseline cache already stores the resolved
    snapshot for the Global pseudo-loadout.
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

    Resolution (sparse diff):
        1. Active user Loadout's entry, if present.
        2. Global Loadout's entry, if present.
        3. Default ``PluginEntry(enabled=True, gui_only=False)``.

    Status icon:
        * ``enabled=False`` → :attr:`StatusIcon.EMPTY` (no icon).
        * Without runtime info (``loaded_in_session is None``) →
          :attr:`StatusIcon.LOADED` (optimistic placeholder; the loader
          refines on attempt).
        * With runtime info: ``True`` → LOADED, ``False`` + diagnostic
          → FAILED, ``False`` without diagnostic → PENDING.

    ``diverges_from_global`` only fires for GLOBAL pills whose
    active Loadout override differs from the Global entry - the
    grey-vs-purple border decision lives in the renderer based on
    this flag.
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

    # Panic re-attribution: while panic is engaged
    # the user chain never runs, so a Global-resident name belongs to
    # the Global layer for the whole session - even when a user folder
    # shadows it or the active loadout overrides it. Resolve the entry
    # from the GLOBAL model (sweep default: enabled) so the pill shows
    # the live panic truth instead of a user override that cannot
    # apply under panic. Observed lie this fixes: a user-disabled
    # shadowed plugin that just loaded from Global rendered RED
    # "pending disable" with a divergence dash while actually live.
    if panic_engaged and source is Source.GLOBAL:
        g_entry = (
            global_model.plugins.get(plugin_name)
            if global_model is not None
            else None
        )
        entry = g_entry or PluginEntry(enabled=True, gui_only=False)

    if entry is None:
        # Global-active honesty.
        # When no user loadout is in play (``active is None`` →
        # Global is the active "loadout"), user-added plugins
        # default to disabled. Global is the read-only TD view;
        # user plugins aren't part of it. Without this, the
        # default-True fallback would graft every discovered user
        # plugin onto the Global view, surface a phantom "+N would
        # load on restart" banner against a slot the user can't
        # save, and on next launch the boot loader (same default-True
        # fallback via ``effective_state``) would actually load them
        # - overriding the user's "I want just the Global view"
        # selection. Global plugins always default to
        # ``PluginEntry(enabled=True, gui_only=False)`` since the
        # Global entry should have been the source of truth above
        # (this branch only fires for Global when Global is
        # somehow missing the entry; defensive).
        if active is None and source is Source.USER_ADDED:
            entry = PluginEntry(enabled=False, gui_only=False)
        else:
            entry = PluginEntry(enabled=True, gui_only=False)

    # Divergence is suppressed under panic for the same reason as the
    # entry re-attribution above: the user override cannot apply while
    # panic holds, so flagging departure from Global would be noise.
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

    # Pill body tint:
    #
    #     enabled=True,  status=LOADED      → NEUTRAL (no diff)
    #     enabled=True,  status=other       → GREEN   (pending enable -
    #                                                  will load next restart)
    #     enabled=False, status=LOADED      → RED     (pending disable -
    #                                                  will unload next restart)
    #     enabled=False, status=other       → NEUTRAL (off, was never loaded)
    #     status=FAILED or MISSING          → YELLOW  (problem state,
    #                                                  overrides the green/red diff)
    #
    # Tint is the *session-truth-vs-restart-intent* signal: what was
    # loaded this Nuke session vs what is enabled for the next restart.
    # This is distinct from the change-detected banner's signal, which
    # compares in-memory loadout edits against the loadout's last-saved-
    # on-disk state (the "save your edits" signal). The two must NOT be
    # collapsed: driving tint from saved_baseline instead would produce
    # false-positive GREEN on every freshly-discovered plugin (no saved
    # entry yet → reads as pending-enable) even when the status chip
    # honestly says LOADED. Tint is *derived*, not stored: the wiring
    # layer computes it at render time from the pair (enabled,
    # status_icon).
    tint = _derive_tint(enabled=entry.enabled, status_icon=status_icon)
    # Source-missing override - a plugin loaded this session whose
    # source folder is no longer configured reads as "source gone."
    # YELLOW hazard body is the canonical Missing signal; the pill
    # renderer also paints a red border glow on top (via
    # ``PillState.source_missing``) so the user reads "still loaded
    # right now (green check), but the source is gone (yellow) and
    # this won't load on next restart (red border)."
    if source_missing:
        tint = Tint.YELLOW

    # Dirty-vs-saved-on-disk - drives the "no glow" plain look while
    # the user is toggling vs the colour-locked "committed
    # pending-restart" glow once they save. Compare the pill's
    # presence + value in M (active model) against its presence +
    # value in D (saved-on-disk baseline).
    #
    # CRITICAL: compare EXPLICIT presence, not fallback-resolved
    # values. A freshly-reconciled plugin (folder-add auto-enable)
    # has an explicit ``(True, False)`` entry in M but no entry in D.
    # If we resolved both sides through the same ``enabled=True``
    # default, the explicit M would compare equal to the absent D and
    # the pill would falsely glow green pre-save. The explicit/implicit
    # distinction is the dirty signal itself: importing a folder marks
    # the loadout dirty, but until the user saves, the newly-added pills
    # must read as white-border unsaved, not green committed-pending.
    #
    # Global active (``active is None``) means there's nothing to be
    # dirty about - Global is read-only.
    is_dirty_vs_saved = False
    if active is not None and tint in (Tint.GREEN, Tint.RED):
        if active_is_custom:
            # Custom-active honesty:
            # Custom is the in-memory wildcard scratch slot - it cannot
            # be saved to disk (Save redirects to Save As → named
            # loadout). The committed-pending vocabulary (lime/red
            # border + halo + "Saved Change" banner) would lie about
            # a slot that can't commit. Force the dirty path for
            # every pill while Custom is active so
            # ``_pending_border_color()`` returns None across the
            # board: divergent pills fall back to white barber-pole;
            # solid-border pills skip the coloured border / halo in
            # ``_paint_border`` step 6. The white pressed-glow still
            # paints for any enabled pill (canonical pressed
            # affordance - independent of save state).
            is_dirty_vs_saved = True
        elif plugin_name in frozenset(force_dirty_plugins):
            # Ceremonial-save set - this specific plugin is part of a
            # re-confirm gesture (folder-add today). Treat as
            # uncommitted so the visual matches the loadout's (*)
            # and the enabled Save button. Other plugins not in the
            # set still use value comparison below - so a folder-add
            # doesn't blow away the saved-glow on unrelated pills.
            is_dirty_vs_saved = True
        else:
            m_explicit = plugin_name in active.plugins
            d_explicit = (
                saved_baseline is not None
                and plugin_name in saved_baseline.plugins
            )
            if m_explicit != d_explicit:
                is_dirty_vs_saved = True
            elif m_explicit:  # both explicit - compare values
                is_dirty_vs_saved = (
                    active.plugins[plugin_name]
                    != saved_baseline.plugins[plugin_name]
                )

    # GUI-only direction-of-change vs what loaded this session.
    # ``session_gui_only`` = the plugin's GUI-only
    # state in ``session_loaded_baseline`` (None = not loaded this
    # session -> no GUI signal; the load wash owns that case).
    # Enabled-only: a disabled plugin's GUI flag is moot. The load
    # wash (enable/disable) takes precedence at the cell level (see
    # panel refresh + grid CELL_DIFF_BG_GUI_ON_RGBA).
    gui_pending_on = False
    gui_pending_off = False
    if entry.enabled and session_gui_only is not None:
        if session_gui_only is False and entry.gui_only is True:
            gui_pending_on = True
        elif session_gui_only is True and entry.gui_only is False:
            gui_pending_off = True

    # GUI-only commit state. ``gui_pending_on/off``
    # above are the GUI analogue of body tint - they fire the moment the
    # user toggles, saved or not. ``gui_committed`` is the GUI analogue
    # of the lime/red saved-glow: True only when the GUI change is
    # persisted to disk on a saveable slot, so it WILL apply on restart.
    # It gates the committed-only visuals (purple cell wash off->on; red
    # GUI-button border on->off); the pending visuals (lit-purple chip;
    # red chip text) stay on the flags above so the user still sees the
    # change while editing. Mirrors the ``is_dirty_vs_saved`` comparison
    # - explicit presence + value, gui_only field only - so it never
    # fires on Custom (can't save), Global (read-only, ``active is
    # None``), a ceremonial-save pill, or an unsaved edit.
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
        # presence differs (m_explicit != d_explicit) → uncommitted → False

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

    Tint is the **session-truth-vs-restart-intent** diff: what loaded
    this Nuke session (status_icon) vs what is enabled for the next
    restart (``entry.enabled`` resolved through sparse-diff).

    Matrix:

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

    YELLOW dominates because a failed / missing plugin is a "needs the
    user's attention" signal regardless of the pending-restart diff.
    """
    from NukeSurvivalLoadout.ui.pill import Tint  # lazy - keeps import graph clean

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
    """Status-icon derivation matrix (see :func:`pill_state_from`).

    Order matters here: ``loaded_in_session is True`` must be checked
    **before** the ``not enabled`` shortcut. The pending-disable
    case (``enabled=False`` + ``status=LOADED`` → RED tint) depends on
    the icon honestly reporting "this plugin is currently loaded in
    memory" even after the user has toggled it OFF for the next restart.
    With the old ordering, ``not enabled`` returned EMPTY first and the
    RED tint row was unreachable - every pending-disable read as
    NEUTRAL. The ``_derive_tint`` step downstream computes the actual
    GREEN / RED / NEUTRAL diff from the ``(enabled, status_icon)``
    pair; this function's job is to make sure the status_icon carries
    real session truth into that decision.
    """
    # Session truth wins - if the plugin is in memory this session,
    # the icon says LOADED regardless of the user's pending enable
    # toggle. The tint derivation then renders RED when enabled=False
    # against a LOADED status (pending-disable signal).
    if loaded_in_session is True:
        return StatusIcon.LOADED
    if not enabled:
        return StatusIcon.EMPTY
    if loaded_in_session is None:
        return StatusIcon.LOADED
    if diagnostic_available:
        return StatusIcon.FAILED
    return StatusIcon.PENDING
