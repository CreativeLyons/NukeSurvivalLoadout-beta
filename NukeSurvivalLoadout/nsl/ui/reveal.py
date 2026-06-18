"""Open a folder in the OS-default file browser (cross-platform).

Backs the right-click **Open Folder** actions on folder-card rows and on
plugin pills. *Which* folder belongs to a given row / pill is resolved by
:class:`nsl.ui.registry.Registry` (it owns the path data); this
module owns only the mechanics of asking the OS to reveal a path.

Two layers, tried in order:

1. **Qt** - ``QDesktopServices.openUrl(QUrl.fromLocalFile(path))``. The
   canonical cross-platform reveal (Finder on macOS, Explorer on Windows,
   the default file manager on Linux). Qt access goes through
   :mod:`nsl.compat` per the panel-wide convention.
2. **stdlib fallback** - a per-OS shell opener (``open`` / ``os.startfile`` /
   ``xdg-open``). Covers hosts where the Qt platform-integration plugin that
   backs ``QDesktopServices`` is missing (it has been seen absent in some
   bundled DCC Qt builds). No third-party dependency is introduced.

Never raises - every entry point sits on a Qt signal path, so a missing
folder or an unavailable opener must degrade to a logged no-op rather than
crash the panel.
"""

from __future__ import annotations

import os
import platform
import subprocess

from nsl import log


def open_in_file_browser(path: str) -> bool:
    """Reveal *path* in the OS-default file browser.

    Returns ``True`` on a best-effort success, ``False`` otherwise. A
    non-existent / non-directory *path* is rejected up front (the OS openers
    behave inconsistently on a bad path - some spawn an empty window, some
    error), so callers get a clean ``False`` and a warning instead.
    """
    if not path or not os.path.isdir(path):
        log.warning(f"open folder: not a directory: {path!r}")
        return False

    # 1. Qt's cross-platform service (primary path).
    try:
        from nsl import compat

        url = compat.QtCore.QUrl.fromLocalFile(path)
        if compat.QtGui.QDesktopServices.openUrl(url):
            return True
    except Exception as exc:  # pragma: no cover - host-dependent
        log.warning(f"open folder: QDesktopServices unavailable ({exc!r}); falling back")

    # 2. Per-OS shell opener (stdlib only).
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]  # Windows-only
        else:  # Linux / other X-desktop hosts
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception as exc:
        log.warning(f"open folder: fallback opener failed for {path!r}: {exc!r}")
        return False
