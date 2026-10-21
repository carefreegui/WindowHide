"""log.py - 极简文件日志(追加写,%APPDATA%/WindowHide/windowhide.log)。

带大小轮转:单个文件到 1MB 就改名为 `.log.1`,老的 `.log.1` 变 `.log.2`。
最多留 3 份,总占用有上限 —— 这个工具是要长期挂在后台的,不轮转的话
日志能长到几十 MB,用户磁盘被吃掉还以为是别的东西干的。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1024 * 1024        # 1 MB
KEEP_FILES = 3                 # windowhide.log / .1 / .2

_lock = threading.Lock()


def _log_path() -> Path:
    import os
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "WindowHide" / "windowhide.log"
    return Path(__file__).resolve().parent.parent / "windowhide.log"


def _rotate(path: Path) -> None:
    """把当前日志挪成 .1,依次把旧的后移,超出 KEEP_FILES 的删掉。"""
    import os
    try:
        if not path.exists() or path.stat().st_size < MAX_BYTES:
            return
        oldest = path.with_name(f"{path.name}.{KEEP_FILES - 1}")
        if oldest.exists():
            oldest.unlink()
        for i in range(KEEP_FILES - 2, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        pass


def write(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n"
    with _lock:
        try:
            path = _log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            # 每次写之前 stat 一下(几微秒),换来"永远不会有超大日志";
            # 日志量本身很小,这点开销可以忽略
            _rotate(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def read_all() -> str:
    try:
        return _log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def log_files() -> list[Path]:
    """当前存在的日志文件(含轮转出来的),按新旧排序。"""
    path = _log_path()
    out = [path]
    out += [path.with_name(f"{path.name}.{i}") for i in range(1, KEEP_FILES)]
    return [p for p in out if p.exists()]
