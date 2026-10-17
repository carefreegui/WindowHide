"""log.py - 极简文件日志(追加写,%APPDATA%/WindowHide/windowhide.log)。"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()


def _log_path() -> Path:
    import os
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "WindowHide" / "windowhide.log"
    return Path(__file__).resolve().parent.parent / "windowhide.log"


def write(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n"
    with _lock:
        try:
            path = _log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def read_all() -> str:
    try:
        return _log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
