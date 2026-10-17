"""tests/hang_test.py - 无响应窗口不阻塞测试。

背景:枚举窗口时对每个窗口调用 GetWindowTextW / WM_GETICON,这些是
跨线程 SendMessage,遇到"无响应"的进程会无限期阻塞调用方 —— 曾导致
隐藏操作整体卡死(栈停在 get_window_text)。
现已改为 SendMessageTimeoutW(SMTO_ABORTIFHUNG, 100ms)。
本测试启动一个不 pumps 消息的挂起窗口(hang_probe),验证:
  1. 枚举全系统窗口仍快速返回
  2. 挂起窗口能被枚举到(类名可见),标题为空串
  3. 按 pid 过滤的枚举也快速
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowhide.core import win32  # noqa: E402

HANG_CLASS = "WHHangProbeWnd"


def main() -> int:
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "hang_probe.py")],
                            creationflags=subprocess.CREATE_NO_WINDOW)

    # 等挂起窗口出现(按其类名)
    hang = None
    t0 = time.time()
    while time.time() - t0 < 10:
        for w in win32.enum_top_level_windows():
            if w.class_name == HANG_CLASS:
                hang = w
                break
        if hang:
            break
        time.sleep(0.3)

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    if not hang:
        print("FAIL  挂起窗口未出现(探针启动失败?)")
        proc.terminate()
        return 1

    try:
        t1 = time.time()
        wins = win32.enum_top_level_windows()
        dt_full = time.time() - t1
        check(f"挂起窗口存在时全量枚举快速返回({dt_full*1000:.0f}ms < 2s)",
              dt_full < 2.0)

        found = [w for w in wins if w.class_name == HANG_CLASS]
        check("挂起窗口能被枚举到", bool(found))

        t2 = time.time()
        sub = win32.enum_process_windows_all(hang.pid)
        dt_pid = time.time() - t2
        check(f"按 pid 过滤的枚举快速返回({dt_pid*1000:.0f}ms < 2s)", dt_pid < 2.0)

        t3 = time.time()
        txt = win32.get_window_text(hang.hwnd)
        dt_txt = time.time() - t3
        check(f"对挂起窗口取标题快速返回({dt_txt*1000:.0f}ms < 2s)", dt_txt < 2.0)
        check("挂起窗口标题为空串(超时降级,不影响其它窗口)", txt == "")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
