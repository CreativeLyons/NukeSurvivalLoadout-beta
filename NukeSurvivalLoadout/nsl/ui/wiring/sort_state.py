"""Plugins-grid sort dropdown - production state-lookup wiring.

Installs the production ``key → SortableState`` callable on the panel and
on the filter pipeline (when present). It does not touch the comparator
algebra, the toolbar widget, or the grid layout - those remain
:mod:`nsl.ui.sort` and
:mod:`nsl.ui.grid_toolbar` concerns.

Order matters: run this after the dropdown → grid plumbing and after
``panel.filter_pipeline`` exists, so the installed lookup flows through
every recompute path.

The lookup closure re-queries panel state on every call, so domain
mutations between sort runs (toggle a pill, change selection, switch
loadouts) are reflected in the next sort without an explicit refresh.
Nothing here reads or writes persistence: sort selection is panel-local
and per-session, so each new session opens with the default ``A → Z``.

When ``panel.registry`` is ``None`` (the snapshot-render path), every
helper inside the lookup degrades to the dataclass defaults rather than
raising: the lookup still installs cleanly and sort re-orders by name only.

No ``import nuke``, no Qt imports.
"""

from __future__ import annotations

import logging

from nsl.ui.sort import build_key_to_folder, build_sort_state_lookup

__all__ = ["wire_sort_state_lookup"]


_log = logging.getLogger(__name__)


def wire_sort_state_lookup(panel) -> None:
    """Install the production sort state-lookup on *panel*.

    Idempotent - calling twice replaces the previous lookup with a
    freshly-built one. (Each closure captures ``panel`` by reference,
    so re-building has no behaviour difference; we still do it so the
    surface mirrors ``wire_filter_pipeline``'s replace-on-call shape.)

    Two install targets:

    1. ``panel.sort_state_lookup`` - the legacy direct path read by
       :func:`nsl.ui.sort.wire_sort._resolve_state_lookup` when no
       filter pipeline is attached.
    2. ``panel.filter_pipeline._sort_state_lookup`` (via
       :meth:`FilterPipeline.set_sort_state_lookup`) - the production
       path: every pipeline recompute (filter change, folder eye
       toggle, refresh) re-runs sort with this lookup.

    The second install triggers ``_recompute_and_apply`` inside the
    pipeline. That call is a no-op for the initial render
    (``_sort_mode`` starts as ``None`` so sort is skipped entirely
    and master-A→Z order passes through unchanged), so wiring this
    helper at panel construction has zero visible effect until the
    user first opens the dropdown. From the first dropdown click
    onward, every sort sees production data.
    """
    lookup = build_sort_state_lookup(panel)
    panel.sort_state_lookup = lookup

    pipeline = getattr(panel, "filter_pipeline", None)
    if pipeline is None:
        # No pipeline attached. The legacy direct path in
        # ``wire_sort._resolve_state_lookup`` reads
        # ``panel.sort_state_lookup`` - already set above - so sort
        # still works through the rebuild_grid fallback.
        _log.debug(
            "wire_sort_state_lookup: no filter_pipeline attached; "
            "lookup installed on panel only."
        )
        return

    try:
        pipeline.set_sort_state_lookup(lookup)
    except Exception:  # noqa: BLE001 - wiring helpers never crash the panel
        _log.warning(
            "wire_sort_state_lookup: pipeline.set_sort_state_lookup failed",
            exc_info=True,
        )
