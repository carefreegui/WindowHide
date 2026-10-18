"""tests/foreground_test.py - 前台窗口隐藏 core 能力回归测试。

背景(用户实测"点一下连桌面都藏了"):从托盘菜单触发"隐藏当前前台窗口"时,
GetForegroundWindow 拿到的是 Progman/WorkerW/Shell_TrayWnd(全是 explorer
的),旧代码会把桌面、任务栏连同所有资源管理器窗口一起藏掉。
修复:hide_foreground 对桌面/任务栏窗口、本工具自身窗口直接跳过。

注意:该能力目前**没有 UI 入口**(主界面按钮/托盘菜单/快捷键已按用户
反馈改为"工具自藏到托盘"),本测试保住 core 的正确性备用。

安全约定:只在确认前台窗口确实是预期目标时才调用 hide_foreground,
绝不盲目藏"当前前台"(那可能是用户的 IDE/浏览器)。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from windowhide.config import Config  # noqa: E402
from windowhide.core import win32  # noqa: E402
from windowhide.core.hide import HideController  # noqa: E402

USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32
USER32.AttachThreadInput.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_bool]
USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
USER32.FindWindowW.restype = ctypes.c_void_p
KERNEL32.GetCurrentThreadId.restype = ctypes.c_uint

results = []


def check(name: str, cond: bool) -> None:
    results.append((name, cond))
    print(("PASS  " if cond else "FAIL  ") + name)


def force_foreground(hwnd: int) -> bool:
    """把指定窗口设为真正的前台(AttachThreadInput 技巧,测试专用)。"""
    our = KERNEL32.GetCurrentThreadId()
    fg = USER32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    fg_thread = USER32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    if fg_thread and fg_thread != our:
        USER32.AttachThreadInput(fg_thread, our, True)
    ok = bool(USER32.SetForegroundWindow(hwnd))
    if fg_thread and fg_thread != our:
        USER32.AttachThreadInput(fg_thread, our, False)
    time.sleep(0.2)
    return ok and USER32.GetForegroundWindow() == hwnd


def find_dummy():
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith("WH-Test-Target-"):
            return w
    return None


def main() -> int:
    app = QApplication(sys.argv)
    config = Config()
    config.mute_on_hide = False
    controller = HideController(config)

    print("[1] 启动 dummy_app ...")
    dummy = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    target = None
    for _ in range(50):
        target = find_dummy()
        if target:
            break
        app.processEvents()
        time.sleep(0.2)
    if not target:
        print("找不到 dummy 窗口,测试中止")
        dummy.kill()
        return 1
    print(f"    dummy hwnd={target.hwnd} pid={target.pid}")

    print("[2] 前台=dummy,隐藏前台窗口 ...")
    if not force_foreground(target.hwnd):
        print("SKIP  无法把 dummy 设为前台(环境限制),跳过真实前台部分")
    else:
        n = controller.hide_foreground()
        time.sleep(0.3)
        check("hide_foreground 隐藏了 dummy", n > 0)
        check("dummy 窗口不可见", not win32.IsWindowVisible(target.hwnd))
        check("记录了 _last_fg_pid", controller._last_fg_pid == target.pid)

        print("[3] 恢复前台窗口 ...")
        ok = controller.restore_last_foreground()
        time.sleep(0.3)
        check("restore_last_foreground 返回 True", ok)
        check("dummy 恢复可见", bool(win32.IsWindowVisible(target.hwnd)))
        check("重复调用返回 False(无重复恢复)",
              not controller.restore_last_foreground())

    print("[4] 防自藏:前台=本工具自己的窗口 ...")
    own = QWidget()
    own.setWindowTitle("WH-Foreground-Test-Own")
    own.resize(300, 200)
    own.show()
    app.processEvents()
    own_hwnd = int(own.winId())
    if not force_foreground(own_hwnd):
        print("SKIP  无法把自己的窗口设为前台(环境限制),跳过防自藏验证")
    else:
        n = controller.hide_foreground()
        time.sleep(0.3)
        check("前台是自己时 hide_foreground 返回 0(不藏自己)", n == 0)
        check("自己的窗口仍然可见", bool(win32.IsWindowVisible(own_hwnd)))
        check("没有任何进程被误藏", controller.hidden_pids() == [])

    print("[5] 桌面/任务栏保护(用户实测'一点连桌面都藏了') ...")
    progman = USER32.FindWindowW("Progman", None)
    if not progman:
        print("SKIP  找不到 Progman(桌面)窗口")
    elif not force_foreground(progman):
        print("SKIP  无法把桌面设为前台(环境限制),跳过桌面保护验证")
    else:
        n = controller.hide_foreground()
        time.sleep(0.3)
        check("前台是桌面时 hide_foreground 返回 0(不藏桌面)", n == 0)
        check("explorer 进程未被误藏", controller.hidden_pids() == [])
        check("桌面窗口仍然可见", bool(win32.IsWindowVisible(progman)))

    print("[6] 清理 ...")
    controller.restore_all()
    own.close()
    if find_dummy():
        dummy.kill()
    try:
        dummy.wait(timeout=3)
    except subprocess.TimeoutExpired:
        dummy.kill()

    failed = [r for r in results if not r[1]]
    print()
    print(f"通过 {len(results) - len(failed)}/{len(results)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
