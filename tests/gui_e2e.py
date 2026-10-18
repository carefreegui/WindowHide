"""gui_e2e.py - GUI 端到端实测。

流程:
  1. 探测一个当前空闲的全局热键组合(很多组合会被浏览器/播放器占用)
  2. 启动 dummy_app 作为目标窗口,预置 config.json(目标规则锁定 dummy)
  3. 启动 GUI(run.pyw),从日志确认热键注册成功
  4. 按热键 -> 目标窗口应被隐藏(任务栏消失、进程存活)
  5. 再按一次 -> 恢复
  6. 全程只操作 dummy 窗口,不碰用户程序
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowhide.core import win32  # noqa: E402
from windowhide.log import read_all  # noqa: E402

USER32 = ctypes.windll.user32
APPDATA = Path(os.environ["APPDATA"]) / "WindowHide"

# 候选组合(依次尝试,取第一个能注册成功的)
CANDIDATES = [
    (6, 0x77, "Ctrl+Alt+F8"),
    (6, 0x7C, "Ctrl+Alt+F13"),
    (5, 0x77, "Ctrl+Shift+F8"),
    (6, 0xC0, "Ctrl+Alt+~"),
    (2, 0x77, "Alt+F8"),
]


def key(vk, flags):
    USER32.keybd_event(vk, 0, flags, 0)


def press_combo(mods: int, vk: int) -> None:
    if mods & 2:
        key(0x11, 0)
    if mods & 1:
        key(0x12, 0)
    if mods & 4:
        key(0x10, 0)
    key(vk, 0)
    time.sleep(0.08)
    key(vk, 0x0002)
    if mods & 4:
        key(0x10, 0x0002)
    if mods & 1:
        key(0x12, 0x0002)
    if mods & 2:
        key(0x11, 0x0002)


def probe_free_hotkey():
    """临时注册各个候选组合,返回第一个空闲的。"""
    from windowhide.core.win32 import (
        CreateWindowExW, DefWindowProcW, RegisterClassW, RegisterHotKey,
        UnregisterHotKey, DestroyWindow, GetModuleHandleW, HWND_MESSAGE,
    )
    from ctypes import wintypes

    class WC(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    WP = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                            wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    @WP
    def wp(h, m, w, l):
        return DefWindowProcW(h, m, w, l)

    wc = WC()
    wc.lpfnWndProc = ctypes.cast(wp, ctypes.c_void_p)
    wc.hInstance = GetModuleHandleW(None)
    wc.lpszClassName = "WHProbeHotkeyWnd"
    RegisterClassW(ctypes.byref(wc))
    hwnd = CreateWindowExW(0, "WHProbeHotkeyWnd", None, 0, 0, 0, 0, 0,
                           wintypes.HWND(HWND_MESSAGE), None,
                           GetModuleHandleW(None), None)
    try:
        for mods, vk, name in CANDIDATES:
            if RegisterHotKey(hwnd, 999, mods, vk):
                UnregisterHotKey(hwnd, 999)
                return mods, vk, name
        return None
    finally:
        DestroyWindow(hwnd)


def find_dummy():
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith("WH-Test-Target-"):
            return w
    return None


def write_config(mods: int, vk: int) -> None:
    APPDATA.mkdir(parents=True, exist_ok=True)
    cfg = {
        "version": 1,
        "hotkey_toggle": {"mods": mods, "vk": vk},
        "hotkey_foreground": {"mods": 0, "vk": 0},
        "mute_on_hide": False,
        "keep_hidden": True,
        "activate_on_restore": False,
        "minimize_to_tray": True,
        "show_balloon": True,
        "autostart": False,
        "targets": [
            {"process": "python.exe", "window_class": "WHDummyAppWnd",
             "title_regex": "", "label": "测试目标窗口"}
        ],
    }
    (APPDATA / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def main() -> int:
    probe = probe_free_hotkey()
    if not probe:
        print("找不到空闲的热键组合,测试中止")
        return 1
    mods, vk, name = probe
    print(f"[0] 选用热键: {name}")
    write_config(mods, vk)

    print("[1] 启动 dummy_app ...")
    dummy = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    win = None
    for _ in range(50):
        win = find_dummy()
        if win:
            break
        time.sleep(0.2)
    if not win:
        print("dummy 窗口未出现")
        return 1
    print(f"    dummy hwnd={win.hwnd}")

    # 清空旧日志,便于断言
    log_file = APPDATA / "windowhide.log"
    if log_file.exists():
        log_file.unlink()

    print("[2] 启动 GUI ...")
    gui = subprocess.Popen([sys.executable, str(ROOT / "run.pyw")],
                           creationflags=subprocess.CREATE_NO_WINDOW)
    gui_hwnd = 0
    t0 = time.time()
    while time.time() - t0 < 30:
        gui_hwnd = USER32.FindWindowW(None, "窗口隐藏工具")
        if gui_hwnd:
            break
        time.sleep(0.5)
    if not gui_hwnd:
        print("    !! GUI 窗口 30s 内未出现")
        return 1
    print(f"    GUI hwnd={gui_hwnd}")

    # 等热键注册(日志出现注册记录)
    registered = False
    t0 = time.time()
    while time.time() - t0 < 15:
        log = read_all()
        if "热键注册" in log:
            registered = "失败" not in log.split("热键注册", 1)[1][:80]
            break
        time.sleep(0.5)
    print(f"    日志确认热键注册: {registered}")

    print(f"[3] 第一次按 {name}(应隐藏目标) ...")
    press_combo(mods, vk)
    time.sleep(1.5)
    hidden = not win32.IsWindowVisible(win.hwnd)
    alive = win32.process_is_alive(win.pid)
    print(f"    dummy visible={not hidden}, alive={alive}")
    if not (hidden and alive):
        print("    !! 隐藏未生效(若日志显示注册成功,可能是第三方软件拦截了该组合键)")

    print(f"[4] 第二次按 {name}(应恢复) ...")
    press_combo(mods, vk)
    time.sleep(1.5)
    visible = win32.IsWindowVisible(win.hwnd)
    print(f"    dummy visible={visible}")
    if not visible:
        print("    !! 恢复未生效")

    print("[5] 清理 ...")
    USER32.PostMessageW(win.hwnd, 0x0010, 0, 0)   # WM_CLOSE dummy
    time.sleep(0.3)
    USER32.PostMessageW(gui_hwnd, 0x0010, 0, 0)   # WM_CLOSE GUI(进托盘)
    time.sleep(0.5)
    gui.terminate()   # 结束 wrapper;真身可能残留,交给系统回收
    if find_dummy():
        dummy.kill()

    ok = registered and hidden and alive and visible
    print("E2E 结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
