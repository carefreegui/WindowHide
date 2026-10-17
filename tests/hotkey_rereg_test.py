"""tests/hotkey_rereg_test.py - 热键重复注册回归测试。

背景(用户实测"快捷键没反应"):在设置里改热键保存后,热注册曾经
静默失败 —— 旧线程未退出完时,新线程 RegisterClassW 同名窗口类
("WHHotkeyMsgWnd")失败并直接 return,不写日志不发信号;旧热键已被
注销,新热键没注册上,热键整个死掉。
修复:类名按线程唯一化;线程起不来必须写日志 + 发 register_failed。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from windowhide.core.hotkeys import HotkeyManager  # noqa: E402
from windowhide.log import read_all  # noqa: E402


def main() -> int:
    app = QCoreApplication(sys.argv)

    before = read_all()
    hk = HotkeyManager()

    # 连续快速改 5 次热键(不等旧线程退出,刻意制造竞态)
    combos = [(3, 0x48), (6, 0x77), (6, 0x74), (3, 0x71), (6, 0x48)]
    for mods, vk in combos:
        hk.set_hotkeys({"toggle": (mods, vk)})
        time.sleep(0.05)
    time.sleep(1.5)  # 等最后一个线程跑完

    added = read_all()[len(before):]
    n_reg = added.count("热键注册:")
    n_fail = added.count("热键注册失败")
    n_thread_fail = added.count("热键线程启动失败")

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    check(f"5 次注册全部有日志记录(实际 {n_reg} 条)", n_reg == 5)
    check(f"无线程静默死亡(启动失败 {n_thread_fail} 条)", n_thread_fail == 0)
    check("无冲突导致的注册失败", n_fail == 0)
    check("registered_actions 反映当前注册", hk.registered_actions() == ["toggle"])

    hk.stop()
    time.sleep(0.3)
    check("stop 后 registered_actions 清空(不再返回陈旧状态)",
          hk.registered_actions() == [])

    print()
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
