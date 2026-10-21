"""make_icon.py - 导出应用图标资源。

画法在 `windowhide/ui/icondraw.py`(和运行时兜底共用一套代码),
这里只负责按多个尺寸导出:

  windowhide/assets/icon-<尺寸>.png          正常态(蓝紫)
  windowhide/assets/icon-hidden-<尺寸>.png   已隐藏态(橙)
  windowhide/assets/icon.ico                 exe / 资源管理器用,多尺寸
  windowhide/assets/icon-hidden.ico
  docs/icon.png                              README 顶部用

改图标只改 `icondraw.py`,然后跑:

    python tools/make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from windowhide.ui.icondraw import PALETTES, render, write_ico  # noqa: E402

# 托盘/窗口按需挑最近的尺寸,所以这里把常见尺寸都导出
PNG_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def main() -> int:
    QApplication.instance() or QApplication(sys.argv)   # QImage 绘制需要 GUI 环境
    assets = ROOT / "windowhide" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)

    for key in PALETTES:
        ico = assets / f"{key}.ico"
        write_ico(ico, key)
        count = 0
        for size in PNG_SIZES:
            render(size, key).save(str(assets / f"{key}-{size}.png"), "PNG")
            count += 1
        rel = str(ico.relative_to(ROOT))
        print(f"  {rel:<34} {ico.stat().st_size:>7} 字节  (+{count} 张 png)")

    render(256, "icon").save(str(docs / "icon.png"), "PNG")
    print("  docs/icon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
