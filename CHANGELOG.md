# Changelog

All notable changes to Nuke Survival Loadout are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- File IO no longer depends on the host locale. Loadout and dispatcher
  writes are pinned to UTF-8 with LF newlines, and the boot-time
  dispatcher read is pinned to UTF-8. This fixes save failures and
  unparseable loadout files on Linux sessions with a non-UTF-8 locale
  (for example `LANG=C` render-farm launches).
- Terminal logging no longer raises when stdout cannot encode the
  status glyphs or a non-ASCII plugin name. Log lines degrade
  gracefully instead of aborting the Global layer load at boot.
- Right-click context menus (plugin pills, folder cards) and the
  remove-folder confirmation dialog now work on PySide2-based Nuke
  versions (13 to 15). These code paths called Qt's `exec()` alias,
  which the PySide2 builds bundled with Nuke do not provide; all modal
  calls now route through a PySide2/PySide6 compatibility shim.

## [0.1.0] - 2026-06-12

### Added

- Initial release: Loadout Panel for saving, naming, and switching
  between loadouts of enabled Plugins, with plain-Python loadout files,
  panic mode, and a Global layer for studio/TD shared baselines.
