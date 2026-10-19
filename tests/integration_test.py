"""integration_test.py - 真实环境集成测试。

流程:
  1. 启动测试专用窗口(dummy_app),等待其窗口出现
  2. 通过 HideController 隐藏 -> 断言窗口不可见、进程仍存活、样式已改
  3. 恢复 -> 断言窗口重新可见、样式还原
  4. 启动托盘探针 -> 跨进程删除其托盘图标 -> 广播 TaskbarCreated 恢复
  5. 注册全局热键并用 SendInput 模拟按键 -> 断言热键触发

只操作自己启动的测试程序,不碰用户窗口。测试结束恢复原状。
    python tests/integration_test.py
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowhide.config import Config
from windowhide.core import win32
from windowhide.core.hide import HideController
from windowhide.core.hotkeys import HotkeyManager
from windowhide.core.targets import resolve_target_hwnds

USER32 = ctypes.windll.user32
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def wait_for(fn, timeout=10.0, interval=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def find_target_window(title_prefix: str = "WH-Test-Target-"):
    """按标题前缀找测试窗口(沙箱环境下 Popen 的 pid 与真实进程不一致,
    不能按 proc.pid 匹配,改用唯一标题)。"""
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith(title_prefix):
            return w
    return None


def main() -> int:
    print("== 1. 启动测试窗口 ==")
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                            creationflags=subprocess.CREATE_NO_WINDOW)
    win = wait_for(find_target_window)
    if not win:
        print("找不到测试窗口,测试中止")
        proc.kill()
        return 1
    win.process_name = "dummy_app.py"
    print(f"    hwnd={win.hwnd} pid={win.pid} title={win.title!r}")

    print("== 2. 隐藏 ==")
    cfg = Config()
    cfg.mute_on_hide = False
    controller = HideController(cfg)
    controller.hide_hwnds([win.hwnd], wait=True)
    time.sleep(0.3)

    hidden = not win32.IsWindowVisible(win.hwnd)
    alive = win32.process_is_alive(win.pid)
    ex_style = win32.GetWindowLongPtrW(win.hwnd, win32.GWL_EXSTYLE)
    toolwindow = bool(ex_style & win32.WS_EX_TOOLWINDOW)
    check("隐藏后窗口不可见", hidden)
    check("隐藏后进程仍存活", alive)
    check("扩展样式含 WS_EX_TOOLWINDOW(移出任务栏)", toolwindow)
    check("控制器记录了隐藏状态", controller.is_pid_hidden(win.pid))

    print("== 3. 恢复 ==")
    controller.restore_all(wait=True)
    time.sleep(0.3)
    restored = win32.IsWindowVisible(win.hwnd)
    ex_style2 = win32.GetWindowLongPtrW(win.hwnd, win32.GWL_EXSTYLE)
    check("恢复后窗口可见", restored)
    check("恢复后 WS_EX_TOOLWINDOW 已移除", not (ex_style2 & win32.WS_EX_TOOLWINDOW))
    controller._timer.stop()

    print("== 4. 托盘图标跨进程移除/恢复 ==")
    probe_script = ROOT / "tests" / "tray_probe.py"
    state_file = ROOT / "tests" / "probe_state.json"
    if state_file.exists():
        state_file.unlink()
    probe = subprocess.Popen([sys.executable, str(probe_script)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    state = wait_for(lambda: json.loads(state_file.read_text()) if state_file.exists() else None)
    if not state:
        check("探针启动", False, "未生成状态文件")
    else:
        time.sleep(1.0)
        from windowhide.core.trayhide import delete_process_tray_icons, request_tray_icon_restore
        deleted = delete_process_tray_icons(state["pid"])
        check("跨进程删除探针托盘图标", bool(deleted), f"deleted={deleted}")

        request_tray_icon_restore(state["pid"])
        time.sleep(2.0)
        state2 = json.loads(state_file.read_text())
        check("广播 TaskbarCreated 后探针重新注册图标",
              state2.get("taskbar_created", 0) > 0,
              f"received={state2.get('taskbar_created')}")

        USER32.PostMessageW(state["hwnd"], 0x0012, 0, 0)
        probe.wait(timeout=5)
        check("探针进程退出", probe.poll() is not None)

    print("== 5. 全局热键 ==")
    from PySide6.QtCore import QCoreApplication
    qtapp = QCoreApplication(sys.argv)   # 信号跨线程是排队的,需要事件循环
    hotkeys = HotkeyManager()
    fired = []
    hotkeys.triggered.connect(fired.append)
    from windowhide.config import MOD_CONTROL, MOD_ALT
    WM_HOTKEY = 0x0312
    hotkeys.set_hotkeys({"toggle": (MOD_CONTROL | MOD_ALT, 0x48)})  # Ctrl+Alt+H
    time.sleep(0.5)
    check("热键注册成功", bool(hotkeys.registered_actions()),
          f"registered={hotkeys.registered_actions()}")

    # 5a. 直接向热键消息窗口 post 一条伪造的 WM_HOTKEY(wParam=1 即第一个
    #     注册的热键 id),确定性地验证整条 Python 侧链路:
    #     消息循环 -> wndproc -> local_map 查表 -> triggered 信号 -> 主线程。
    #     真实按键注入在某些环境(如沙箱)会被整体拦截,见 5b。
    USER32.PostMessageW(hotkeys._hwnd, WM_HOTKEY, 1, 0)
    ok = None
    for _ in range(50):   # 轮询时跑 Qt 事件循环,让排队信号有机会投递
        qtapp.processEvents()
        if fired:
            ok = fired
            break
        time.sleep(0.1)
    check("伪造 WM_HOTKEY 走通链路(消息循环->wndproc->信号)", bool(ok),
          f"fired={fired}")

    # 5b. 真实注入(keybd_event;SendInput 在本环境被沙箱拦截)。
    #     判定规则(借 LL 键盘钩子做 OS 层旁证):
    #       钩子看到注入按键 且 热键没触发 -> 真回归,FAIL
    #       钩子什么都没看到(注入被环境整体拦截)-> SKIP,链路已由 5a 验证
    fired.clear()

    WH_KEYBOARD_LL, LLKHF_INJECTED = 13, 0x10
    injected_seen: list[int] = []

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", ctypes.c_uint), ("scanCode", ctypes.c_uint),
                    ("flags", ctypes.c_uint), ("time", ctypes.c_uint),
                    ("dwExtraInfo", ctypes.c_void_p)]

    HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                ctypes.c_ssize_t, ctypes.c_ssize_t)

    @HOOKPROC
    def _ll_hook(nCode, wParam, lParam):
        try:
            if nCode >= 0 and lParam:
                kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if kb.flags & LLKHF_INJECTED:
                    injected_seen.append(int(kb.vkCode))
        except Exception:
            pass
        return USER32.CallNextHookEx(None, nCode, wParam, lParam)

    kernel = ctypes.windll.kernel32
    USER32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                         ctypes.c_void_p, ctypes.c_uint]
    USER32.SetWindowsHookExW.restype = ctypes.c_void_p
    USER32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    USER32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.c_ssize_t, ctypes.c_ssize_t]
    KERNEL_GetModuleHandleW = kernel.GetModuleHandleW
    KERNEL_GetModuleHandleW.restype = ctypes.c_void_p
    hook = USER32.SetWindowsHookExW(WH_KEYBOARD_LL, _ll_hook,
                                    KERNEL_GetModuleHandleW(None), 0)

    KEYEVENTF_KEYUP = 0x0002
    def key(vk, flags):
        USER32.keybd_event(vk, 0, flags, 0)

    VK_CONTROL, VK_MENU, VK_H = 0x11, 0x12, 0x48
    key(VK_CONTROL, 0)
    key(VK_MENU, 0)
    key(VK_H, 0)
    time.sleep(0.05)
    key(VK_H, KEYEVENTF_KEYUP)
    key(VK_MENU, KEYEVENTF_KEYUP)
    key(VK_CONTROL, KEYEVENTF_KEYUP)

    ok2 = None
    for _ in range(50):
        qtapp.processEvents()
        if fired:
            ok2 = fired
            break
        time.sleep(0.1)
    if hook:
        USER32.UnhookWindowsHookEx(hook)

    if ok2:
        check("模拟 Ctrl+Alt+H 触发热键", True, f"fired={fired}")
    elif injected_seen:
        check("模拟 Ctrl+Alt+H 触发热键", False,
              f"fired={fired}(注入已到达 OS 但热键未触发,是真回归)")
    else:
        print("  SKIP  模拟 Ctrl+Alt+H 触发热键"
              "(本环境拦截合成输入:LL 键盘钩子未收到注入事件;"
              "消息循环->wndproc->信号链路已由 5a 验证)")
    hotkeys.stop()

    # 收尾:关闭测试窗口(沙箱下 proc 可能只是 wrapper,以关窗口为主)
    USER32.PostMessageW(win.hwnd, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(0.5)
    if find_target_window():
        proc.kill()

    print("\n== 汇总 ==")
    failed = [r for r in results if not r[1]]
    print(f"通过 {len(results) - len(failed)}/{len(results)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
