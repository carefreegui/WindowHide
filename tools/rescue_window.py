"""急救:把被最小化卡住的全屏游戏窗口拉回来。

背景:永劫无间以管理员权限运行,本工具(普通权限)受 UIPI 限制:
能发出的最小化生效了,但 ShowWindow/SetWindowPlacement 全被挡,
于是窗口卡在"最小化且没有任务栏按钮"的状态。必须以管理员身份运行本脚本。

用法:
    rescue_window.bat            # 双击,会自己弹 UAC
    python tools/rescue_window.py [pid]
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowhide.core import win32  # noqa: E402
from windowhide.core.audio import set_process_mute  # noqa: E402

user32 = win32.user32
kernel32 = ctypes.windll.kernel32

HINTS = ("naraka", "bladepoint", "narakabladepoint")


def _pause() -> None:
    """等用户看一眼结果;非交互环境下(重定向/管道)直接跳过。"""
    try:
        input("按回车退出…")
    except (EOFError, KeyboardInterrupt):
        pass


def find_pid() -> int:
    """按进程名找游戏。找不到返回 0。"""
    snap = win32.CreateToolhelp32Snapshot(win32.TH32CS_SNAPPROCESS, 0)
    if not snap or snap == -1:
        return 0
    entry = win32.PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(win32.PROCESSENTRY32W)
    found = 0
    try:
        ok = win32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if any(h in entry.szExeFile.lower() for h in HINTS):
                found = int(entry.th32ProcessID)
            ok = win32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        win32.CloseHandle(snap)
    return found


def find_main(pid: int):
    best = None
    for w in win32.enum_process_windows_all(pid):
        if w.class_name == "UnityWndClass" or (w.title or "").startswith("Naraka"):
            if best is None or (w.title or "").startswith("Naraka"):
                best = w
    return best


def state(hwnd: int) -> str:
    style, ex_style = win32.get_window_styles(hwnd)
    return (f"visible={bool(style & win32.WS_VISIBLE)} "
            f"iconic={bool(win32.IsIconic(hwnd))} "
            f"rect={win32.get_window_rect(hwnd)} ex=0x{ex_style:08x}")


def fg_force(hwnd: int) -> None:
    fg = user32.GetForegroundWindow()
    t_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    t_self = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if t_fg and t_fg != t_self:
            attached = bool(user32.AttachThreadInput(t_self, t_fg, True))
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(t_self, t_fg, False)


def main() -> int:
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_pid()
    if not pid:
        print("没找到正在运行的永劫无间进程。")
        _pause()
        return 1

    # 权限自检:UIPI 会静默拦掉所有窗口操作,先确认自己够格
    h = win32.OpenProcess(win32.PROCESS_VM_READ, False, pid)
    if not h:
        err = win32.GetLastError()
        print(f"无法操作游戏进程(pid={pid},错误码 {err})。")
        print("这通常意味着本脚本不是以管理员身份运行的 —— "
              "请右键 rescue_window.bat →「以管理员身份运行」。")
        _pause()
        return 1
    win32.CloseHandle(h)

    w = find_main(pid)
    if not w:
        print(f"进程 pid={pid} 存在,但找不到任何主窗口。")
        print("这种情况不是'最小化点不回来'(那种一定还有窗口),"
              "而是游戏本身没正常起来。")
        print(f"建议:任务管理器里结束 NarakaBladepoint.exe(pid={pid}),再启动游戏。")
        _pause()
        return 1

    hwnd = w.hwnd
    print(f"目标: hwnd={hwnd} class={w.class_name} title={w.title!r}")
    print("  初始  ", state(hwnd))

    win32.set_ex_style(hwnd, remove=win32.WS_EX_TOOLWINDOW)

    # 1) 直接改窗口放置状态(最可靠,不依赖程序处理消息)
    wp = win32.WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(win32.WINDOWPLACEMENT)
    if win32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        wp.showCmd = win32.SW_SHOWNORMAL
        ok = win32.SetWindowPlacement(hwnd, ctypes.byref(wp))
        time.sleep(0.4)
        print(f"  SetWindowPlacement ret={ok}", state(hwnd))

    # 2) 异步还原 + 系统命令还原
    win32.ShowWindowAsync(hwnd, win32.SW_RESTORE)
    time.sleep(0.3)
    win32.PostMessageW(hwnd, win32.WM_SYSCOMMAND, 0xF120, 0)   # SC_RESTORE
    time.sleep(0.3)
    print("  SW_RESTORE + SC_RESTORE", state(hwnd))

    # 3) 抢前台(游戏拿到焦点才会重新进全屏)
    fg_force(hwnd)
    time.sleep(0.6)
    print("  抢前台  ", state(hwnd), "fg=", win32.GetForegroundWindow() == hwnd)

    n = set_process_mute(pid, False)
    print(f"  取消静音影响 {n} 个会话")
    print("最终    ", state(hwnd))

    if not win32.IsIconic(hwnd):
        print("\n已恢复。切回游戏看一下画面。")
    else:
        print("\n还是最小化状态 —— 如果本脚本已经是管理员身份,可能游戏主线程"
              "自身卡住,只能结束进程重开。")
    _pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
