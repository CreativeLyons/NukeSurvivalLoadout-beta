# NSL Global chain head. See README.md in this folder.

# ─── Global folders ────────────────────────
global_plugins = "./plugins"
global_loadout = "./Global_Loadout"
# import os
# global_plugins = os.environ.get("MY_STUDIO_PLUGINS", "./plugins")

# ─── Custom boot code ──────────────────────


# ─── NSL loader ────────────────────────────
try:
    from NukeSurvivalLoadout.boot.global_loader import nsl_load_global
    nsl_load_global(global_plugins, global_loadout, base=__file__)
except ImportError:
    import os
    import nuke
    _base = os.path.dirname(os.path.abspath(__file__))
    _plugins = os.path.expanduser(global_plugins)
    if not os.path.isabs(_plugins):
        _plugins = os.path.join(_base, _plugins)
    try:
        _names = sorted(os.listdir(_plugins))
    except OSError:
        _names = []
    for _name in _names:
        if _name.startswith(("_", ".")):
            continue
        _path = os.path.join(_plugins, _name)
        if os.path.isdir(_path):
            nuke.pluginAddPath(_path)
