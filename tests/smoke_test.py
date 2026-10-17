"""smoke_test.py - 无 GUI 冒烟测试:模块导入、窗口枚举、分类、配置、热键格式。

在任意环境可跑(不需要 PySide6 之外的依赖):
    python tests/smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowhide.core import win32
from windowhide.core.classify import classify_window, type_label
from windowhide.core.hotkeys import hotkey_label
from windowhide.core.targets import TargetRule, resolve_target_hwnds
from windowhide.config import Config, MOD_CONTROL, MOD_ALT


def main() -> int:
    failures = []

    # 1. 窗口枚举
    windows = win32.enum_top_level_windows()
    print(f"[1] 枚举到顶层窗口 {len(windows)} 个")
    if len(windows) < 5:
        failures.append("窗口枚举数量异常")
    for w in windows[:10]:
        w.process_name = win32.process_image_path(w.pid).rsplit("\\", 1)[-1]
        print(f"    - pid={w.pid:<6} {w.process_name:<20} "
              f"class={w.class_name:<24} visible={w.visible} title={w.title[:30]!r}")

    # 2. 分类
    kinds = set()
    for w in windows:
        w.process_name = win32.process_image_path(w.pid).rsplit("\\", 1)[-1]
        kinds.add(classify_window(w))
    print(f"[2] 分类覆盖: {sorted(kinds)}")

    # 3. 规则匹配
    rule = TargetRule(process="explorer.exe")
    matched = resolve_target_hwnds([rule], windows)
    print(f"[3] explorer.exe 规则匹配 {len(matched)} 个窗口")

    # 4. 配置往返
    cfg = Config()
    cfg.targets = [TargetRule(process="notepad.exe", label="记事本")]
    cfg.hotkey_toggle.mods = MOD_CONTROL | MOD_ALT
    cfg.hotkey_toggle.vk = 0x48
    d = cfg.to_dict()
    cfg2 = Config.from_dict(d)
    assert cfg2.hotkey_toggle.mods == (MOD_CONTROL | MOD_ALT)
    assert cfg2.hotkey_toggle.vk == 0x48
    assert cfg2.targets[0].process == "notepad.exe"
    print("[4] 配置序列化/反序列化 OK")

    # 5. 热键显示
    s = hotkey_label(MOD_CONTROL | MOD_ALT, 0x48)
    print(f"[5] 热键格式: {s}")
    if s != "Ctrl + Alt + H":
        failures.append(f"热键格式错误: {s}")

    # 6. 托盘 API 自检(注册→删除→再删)
    from windowhide.core.trayhide import self_test
    ok = self_test()
    print(f"[6] Shell_NotifyIcon 通路自检: {'通过' if ok else '失败'}")
    if not ok:
        failures.append("托盘 API 自检失败")

    if failures:
        print("\n失败:")
        for f in failures:
            print(" -", f)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
