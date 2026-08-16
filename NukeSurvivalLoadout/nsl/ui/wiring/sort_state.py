"""Plugins-grid sort dropdown - production state-lookup wiring.

Installs the ``key -> SortableState`` callable on the panel and on the
filter pipeline. Run it after the dropdown-to-grid wiring and after
``panel.filter_pipeline`` exists, so every recompute path uses it.

No ``import nuke``, no Qt imports.
"""

from __future__ import annotations

import logging

from nsl.ui.sort import build_key_to_folder, build_sort_state_lookup

__all__ = ["wire_sort_state_lookup"]


_log = logging.getLogger(__name__)


def wire_sort_state_lookup(panel) -> None:
    """Install the production sort state-lookup on *panel*.

    Idempotent. Calling twice replaces the previous lookup. The lookup
    goes on the panel and on the filter pipeline, which is the path
    every recompute uses.

    The pipeline install triggers a recompute. That is a no-op at panel
    construction, because ``_sort_mode`` starts as ``None``.
    """
    lookup = build_sort_state_lookup(panel)
    panel.sort_state_lookup = lookup

    pipeline = getattr(panel, "filter_pipeline", None)
    if pipeline is None:
        # Sort still works. ``wire_sort`` falls back to the lookup set
        # on the panel above.
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
