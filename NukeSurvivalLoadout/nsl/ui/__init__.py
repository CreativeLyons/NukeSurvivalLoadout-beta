"""NSL UI layer - PySide widgets composing the Loadout Panel.

Every widget submodule imports Qt through ``nsl.compat`` and therefore
triggers PySide resolution at import time. This package does NOT
auto-import its submodules - that would pull Qt in on every ``import
nsl`` (the headless boot path forbids Qt). Consumers import each widget
explicitly:

    from nsl.ui.pill import Pill
    from nsl.ui.grid import Grid
    from nsl.ui.side_panel import SidePanel
    ... and so on.
"""
