"""tests/hide_state_test.py - 隐藏状态持久化测试。

背景(实测踩坑):隐藏状态只在内存里,进程一重启,被隐藏的窗口就成了
"孤儿" —— 任务栏没有、新实例也不知道它被隐藏过,永远无法恢复。
修复:状态落盘 hidden_state.json,启动时加载并逐项校验。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowhide.config import Config  # noqa: E402
from windowhide.core import win32  # noqa: E402
from windowhide.core.hide import HideController, _state_path  # noqa: E402


def find_dummy():
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith("WH-Test-Target-"):
            return w
    return None


def main() -> int:
    cfg = Config()
    cfg.mute_on_hide = False
    if _state_path().exists():
        _state_path().unlink()

    dummy = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    target = None
    for _ in range(50):
        target = find_dummy()
        if target:
            break
        time.sleep(0.2)
    if not target:
        print("FAIL  dummy 窗口未出现")
        return 1
    print(f"[1] dummy hwnd={target.hwnd} pid={target.pid}")

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    # 实例 1:隐藏
    print("[2] 实例 1 隐藏 dummy ...")
    c1 = HideController(cfg)
    n = c1.hide_pids([target.pid])
    check("隐藏成功", n == 1)
    check("状态文件已写入", _state_path().exists())
    data = json.loads(_state_path().read_text(encoding="utf-8"))
    check("状态文件含该进程", any(h["pid"] == target.pid for h in data["hidden"]))

    # 实例 2:模拟"工具重启"
    print("[3] 实例 2(模拟重启)加载状态 ...")
    c2 = HideController(cfg)
    check("新实例知道该进程被隐藏", c2.is_pid_hidden(target.pid))
    check("新实例知道该窗口被隐藏", c2.is_hwnd_hidden(target.hwnd))

    # 实例 2 恢复(这就是用户重启工具后的恢复路径)
    print("[4] 实例 2 恢复 ...")
    n = c2.restore_pids([target.pid])
    check("新实例恢复成功", n == 1)
    time.sleep(0.3)
    check("窗口恢复可见", bool(win32.IsWindowVisible(target.hwnd)))
    data2 = json.loads(_state_path().read_text(encoding="utf-8"))
    check("状态文件已清空该进程", not any(h["pid"] == target.pid for h in data2["hidden"]))

    # 过期记录(进程已死)应被丢弃
    print("[5] 过期记录丢弃 ...")
    stale = {"version": 1, "hidden": [
        {"pid": 999999, "process_name": "ghost.exe", "muted": False,
         "records": [{"hwnd": 888888, "ex_style": 0, "show_cmd": 9, "title": "x"}]}]}
    _state_path().write_text(json.dumps(stale), encoding="utf-8")
    c3 = HideController(cfg)
    check("已死进程的记录被丢弃", not c3.is_pid_hidden(999999))

    print("[6] 清理 ...")
    win32.PostMessageW(target.hwnd, 0x0010, 0, 0)
    time.sleep(0.3)
    if find_dummy():
        dummy.kill()
    try:
        dummy.wait(timeout=3)
    except subprocess.TimeoutExpired:
        dummy.kill()
    if _state_path().exists():
        _state_path().unlink()

    print()
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
