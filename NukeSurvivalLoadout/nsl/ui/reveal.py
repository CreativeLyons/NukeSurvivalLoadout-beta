"""Open a folder in the OS-default file browser.

Backs the right-click **Open Folder** actions on folder-card rows and
plugin pills. :class:`nsl.ui.registry.Registry` owns the path data.

``QDesktopServices`` is tried first, then a per-OS shell opener. Some
bundled DCC Qt builds ship without the platform plugin it needs. Nothing
here raises, because every caller sits on a Qt signal path.
"""

from __future__ import annotations

import os
import platform
import subprocess

from nsl import log


def open_in_file_browser(path: str) -> bool:
    """Reveal *path* in the OS-default file browser.

    A path that is not a directory is rejected here, because the OS
    openers behave inconsistently on a bad one.
    """
    if not path or not os.path.isdir(path):
        log.warning(f"open folder: not a directory: {path!r}")
        return False

    try:
        from nsl import compat

        url = compat.QtCore.QUrl.fromLocalFile(path)
        if compat.QtGui.QDesktopServices.openUrl(url):
            return True
    except Exception as exc:  # pragma: no cover - host-dependent
        log.warning(f"open folder: QDesktopServices unavailable ({exc!r}); falling back")

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
