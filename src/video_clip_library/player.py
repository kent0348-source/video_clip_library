from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from aqt.qt import QDesktopServices, QFrame, QUrl, QWidget


class ClipPlayer(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("clipLibraryPlayer")
        self.setMinimumHeight(220)
        self.setStyleSheet("#clipLibraryPlayer { background: #111; }")
        self._process: subprocess.Popen[Any] | None = None
        self._mpv_handle: Any = None
        self._mpv_path = "mpv"
        self._current_path = ""

    def set_mpv_path(self, path: str) -> None:
        self._mpv_path = path.strip() or "mpv"

    def stop(self) -> None:
        if self._mpv_handle is not None:
            try:
                self._mpv_handle.terminate()
            except Exception:
                pass
            self._mpv_handle = None
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            try:
                self._process.wait(timeout=1.5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        self._current_path = ""

    def play(self, path: str) -> str:
        self.stop()
        if not path or not os.path.isfile(path):
            return "missing"
        self._current_path = path
        if self._try_libmpv(path):
            return "libmpv"
        if self._try_embed(path):
            return "embed"
        self._open_external(path)
        return "external"

    def _try_libmpv(self, path: str) -> bool:
        try:
            import mpv
        except Exception:
            return False
        try:
            player = mpv.MPV(wid=str(int(self.winId())), osc=True, keep_open="yes")
            player.play(path)
            self._mpv_handle = player
            return True
        except Exception:
            self._mpv_handle = None
            return False

    def _resolved_mpv(self) -> str:
        configured = self._mpv_path
        if configured and os.path.isfile(configured):
            return configured
        found = shutil.which(configured) or shutil.which("mpv")
        return found or configured

    def _try_embed(self, path: str) -> bool:
        mpv_path = self._resolved_mpv()
        if not mpv_path:
            return False
        try:
            wid = int(self.winId())
            args = [
                mpv_path,
                f"--wid={wid}",
                "--force-window=yes",
                "--keep-open=yes",
                "--osc=yes",
                "--no-terminal",
                "--idle=no",
                path,
            ]
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(args, **kwargs)
            return True
        except Exception:
            self._process = None
            return False

    def _open_external(self, path: str) -> None:
        mpv_path = self._resolved_mpv()
        if mpv_path:
            try:
                subprocess.Popen([mpv_path, path])
                return
            except Exception:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.stop()
        super().closeEvent(event)
