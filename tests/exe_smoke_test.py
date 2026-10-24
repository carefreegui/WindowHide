"""exe_smoke_test.py - 打包产物(单文件 exe)的端到端验收。

跑的是 `dist/WindowHide.exe`(不是源码),验证"打完包还能用":

  1. 启动 exe,主窗口能在合理时间内出现,进程名是 WindowHide.exe;
  2. 窗口/任务栏图标存在(打包漏了 assets 时这里会掉);
  3. 全局热键能注册(日志里有"热键注册成功");
  4. 按一次热键 → 目标窗口隐藏;再按一次 → 恢复;
  5. 收尾:结束 exe、还原真实 config.json、结束测试目标。

没有 dist/WindowHide.exe 时直接 SKIP(退出码 0)—— 源码测试不该因为
"还没打包"而变红。

    python tools/build_exe.py
    python tests/exe_smoke_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ctypes import wintypes  # noqa: E402

from windowhide.core import win32  # noqa: E402
from windowhide.log import read_all  # noqa: E402

USER32 = __import__("ctypes").windll.user32
WM_HOTKEY = 0x0312
HWND_MESSAGE = -3
# 热键 id = 该 action 在 specs 里的序号 + 1。测试配置把 hotkey_hide_self
# 的 vk 写成 0(不注册),所以 toggle 是第 2 项 —— id=2。
TOGGLE_ID = 2

EXE = ROOT / "dist" / "WindowHide.exe"
APPDATA = Path(os.environ["APPDATA"]) / "WindowHide"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def find_app_window():
    return win32.FindWindowW(None, "窗口隐藏工具")


def find_hotkey_window(timeout: float = 10.0) -> int:
    """找到应用那个"热键消息窗口"。

    消息窗口(HWND_MESSAGE 的子窗口)不在 EnumWindows 的遍历范围里,
    必须用 FindWindowEx(HWND_MESSAGE, ...) 逐个翻。
    """
    USER32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR]
    USER32.FindWindowExW.restype = wintypes.HWND
    deadline = time.time() + timeout
    while time.time() < deadline:
        child = 0
        while True:
            child = USER32.FindWindowExW(wintypes.HWND(HWND_MESSAGE),
                                         wintypes.HWND(child), None, None)
            if not child:
                break
            if win32.get_class_name(int(child)).startswith("WHHotkeyMsgWnd"):
                return int(child)
        time.sleep(0.3)      # 窗口可能还没建好,重试
    return 0


def press_hotkey(hwnd: int) -> None:
    """投递一条 WM_HOTKEY,等价于真按了那个热键。

    不用 keybd_event 合成按键:沙箱/远程会话会整体拦截合成输入(本机实测
    收不到),而这条投递路径和真实按键之后 wndproc 拿到的东西一模一样 ——
    被测的正是"消息 → 信号 → 主线程 → 隐藏"这一整条链路。
    """
    USER32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    USER32.PostMessageW(wintypes.HWND(hwnd), WM_HOTKEY, TOGGLE_ID, 0)


def find_dummy():
    for w in win32.enum_top_level_windows():
        if (w.title or "").startswith("WH-Test-Target-"):
            return w
    return None


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def main() -> int:
    if not EXE.exists():
        print(f"SKIP: 没找到 {EXE.relative_to(ROOT)}(先跑 python tools/build_exe.py)")
        return 0
    print(f"被测产物: {EXE}  ({EXE.stat().st_size / 1024 / 1024:.1f} MB)")
    log_file = APPDATA / "windowhide.log"
    if log_file.exists():
        log_file.unlink()      # 不清的话会读到上一次运行的"热键注册成功"

    if find_app_window():
        print("已经有一个窗口隐藏工具在跑,先退出它再测(避免抢热键/单实例互斥)")
        return 1

    # ---- 准备配置:热键 + 指向测试目标 ----
    from gui_e2e import probe_free_hotkey, write_config
    probe = probe_free_hotkey()
    if not probe:
        print("找不到空闲热键组合,测试中止")
        return 1
    mods, vk, hotkey_name = probe
    print(f"  使用热键: {hotkey_name}")

    config_backup = None
    cfg_path = APPDATA / "config.json"
    if cfg_path.exists():
        config_backup = APPDATA / "config.exetest.bak.json"
        config_backup.write_bytes(cfg_path.read_bytes())

    dummy = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    target = None
    exe = None
    # 无论从哪一步失败退出,都必须:杀窗口/杀目标/还原真实配置。
    # 之前 exe_smoke_test 在中途 return 1 时漏了还原,把用户的真实配置
    # 覆盖成了测试配置(和 gui_e2e 当年一个坑)。
    try:
        for _ in range(50):
            target = find_dummy()
            if target:
                break
            time.sleep(0.2)
        if not target:
            check("测试目标窗口已就绪", False)
            return 1

        write_config(mods, vk)

        print("== 1. 启动 exe ==")
        exe = subprocess.Popen([str(EXE)])
        hwnd = 0
        t0 = time.time()
        while time.time() - t0 < 45:
            hwnd = find_app_window()
            if hwnd:
                break
            time.sleep(0.3)
        cost = time.time() - t0
        check("主窗口出现了(单文件解包 + 启动)", bool(hwnd), f"{cost:.1f} 秒")
        if not hwnd:
            return 1

        app_pid = win32.get_window_pid(hwnd)
        exe_name = win32.process_image_path(app_pid).rsplit("\\", 1)[-1]
        check("进程名是 WindowHide.exe(不是 python)",
              exe_name.lower() == "windowhide.exe", exe_name)
        check("窗口有图标(assets 打进去了)", bool(win32.window_icon_handle(hwnd)))

        print("== 2. 热键注册 ==")
        registered = False
        t0 = time.time()
        while time.time() - t0 < 20:
            log = read_all()
            if "热键注册" in log:
                tail = log.split("热键注册", 1)[1][:80]
                registered = "失败" not in tail
                break
            time.sleep(0.4)
        check("日志里热键注册成功", registered)

        print("== 3. 热键切换隐藏 / 恢复 ==")
        hot_hwnd = find_hotkey_window()
        check("找到热键消息窗口", bool(hot_hwnd), hex(hot_hwnd) if hot_hwnd else "")
        if not hot_hwnd:
            return 1

        press_hotkey(hot_hwnd)
        done = False
        t0 = time.time()
        while time.time() - t0 < 6:
            if not win32.IsWindowVisible(target.hwnd):
                done = True
                break
            time.sleep(0.2)
        check("按一次热键后目标被隐藏", done)
        check("目标进程仍存活(后台化,不是关掉)", win32.process_is_alive(target.pid))

        press_hotkey(hot_hwnd)
        back = False
        t0 = time.time()
        while time.time() - t0 < 6:
            if win32.IsWindowVisible(target.hwnd):
                back = True
                break
            time.sleep(0.2)
        check("再按一次恢复显示", back)
        return 0 if all(r[1] for r in results) else 1
    finally:
        print("== 4. 收尾 ==")
        win_hwnd = find_app_window()
        if win_hwnd:
            kill_tree(win32.get_window_pid(win_hwnd))
        if exe is not None and exe.poll() is None:
            kill_tree(exe.pid)          # onefile 的 bootloader 父进程
        time.sleep(0.5)
        check("exe 已退出", not find_app_window())

        if target is not None and win32.process_is_alive(target.pid):
            dummy.kill()
        try:
            dummy.wait(timeout=5)
        except Exception:
            pass
        if config_backup and config_backup.exists():
            cfg_path.write_bytes(config_backup.read_bytes())
            config_backup.unlink()
            print("  已还原 config.json")
        else:
            cfg_path.unlink(missing_ok=True)

    failed = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(failed)}/{len(results)}")
    for name, _ok, detail in failed:
        print(f"  失败: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
