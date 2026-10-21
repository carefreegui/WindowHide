"""icon.py - 应用图标的加载(托盘 / 窗口 / exe)。

图标本体是 `windowhide/assets/` 下的多尺寸 PNG(由 `tools/make_icon.py`
生成,画法在 `icondraw.py`)。这里只负责"按尺寸挑文件、挑不到就现画一个",
所以即使打包漏了资源,托盘也不会变成一个空白方块。
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap

from .icondraw import render

# 这些尺寸覆盖了 Windows 常见的图标场景:任务栏 24/32、Alt+Tab 32/48、
# 大图标视图 96/128、资源管理器超大图标 256
_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def asset_dir() -> Path:
    """图标资源目录;兼容 PyInstaller 解包后的 `_MEIPASS`。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for candidate in (Path(base) / "windowhide" / "assets",
                          Path(base) / "assets"):
            if candidate.exists():
                return candidate
    return Path(__file__).resolve().parent.parent / "assets"


@lru_cache(maxsize=4)
def load_icon(hidden: bool = False) -> QIcon:
    """取应用图标。hidden=True 为"已隐藏"态(橙色)。"""
    key = "icon-hidden" if hidden else "icon"
    d = asset_dir()
    icon = QIcon()
    for size in _ICON_SIZES:
        f = d / f"{key}-{size}.png"
        if f.exists():
            icon.addFile(str(f), QSize(size, size))
    if icon.isNull() or not icon.availableSizes():
        # 资源丢了(打包漏文件/用户误删)也别让托盘变空白:现画一个
        icon = QIcon(QPixmap.fromImage(render(64, key)))
    return icon
