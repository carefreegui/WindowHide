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


def find_pid(name: str | None = None) -> int:
    """按进程名找目标(默认找永劫无间)。找不到返回 0。"""
    want = (name or "").lower()
    snap = win32.CreateToolhelp32Snapshot(win32.TH32CS_SNAPPROCESS, 0)
    if not snap or snap == -1:
        return 0
    entry = win32.PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(win32.PROCESSENTRY32W)
    found = 0
    try:
        ok = win32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            exe = entry.szExeFile.lower()
            if (want and want in exe) or (not want and any(h in exe for h in HINTS)):
                found = int(entry.th32ProcessID)
            ok = win32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        win32.CloseHandle(snap)
    return found


def find_main(pid: int):
    """找主窗口:优先认得出的大作(Unity / Naraka),否则取面积最大的可见窗口。

    不能只认固定类名 —— 别的游戏也想用这个脚本救命。
    """
    best = None
    for w in win32.enum_process_windows_all(pid):
        if w.class_name == "UnityWndClass" or (w.title or "").startswith("Naraka"):
            return w
        if w.visible and (best is None or w.size > best.size):
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
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    pid = int(arg) if arg.isdigit() else find_pid(arg or None)
    if not pid:
        print("没找到正在运行的目标进程。")
        _pause()
        return 1

    # 权限自检:UIPI 会静默拦掉所有窗口操作,先确认自己够格
    h = win32.OpenProcess(win32.PROCESS_VM_READ, False, pid)
    if not h:
        err = win32.GetLastError()
        print(f"无法操作目标进程(pid={pid},错误码 {err})。")
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
        print(f"建议:任务管理器里结束该进程(pid={pid}),再启动游戏。")
        _pause()
        return 1

    hwnd = w.hwnd
    print(f"目标: hwnd={hwnd} class={w.class_name} title={w.title!r}")
    print("  初始  ", state(hwnd))

    # 关键:目标无响应时,同步窗口调用(SetWindowLongPtr / SetWindowPlacement)
    # 会**一直等下去** —— 老版本就是卡在这一步。先判定,只用异步手段。
    hung = win32.is_hung(hwnd)
    if hung:
        print("  ⚠ 目标窗口当前无响应(和任务管理器里显示的一致)。")
        print("    只用异步调用(ShowWindowAsync / PostMessage),不会有阻塞风险,")
        print("    但**能不能救回来取决于它自己什么时候恢复**。")

    if not hung:
        win32.set_ex_style(hwnd, remove=win32.WS_EX_TOOLWINDOW)
        print("  去掉 TOOLWINDOW", state(hwnd))

    # 1) 系统命令还原(投递到队列,不等)
    win32.PostMessageW(hwnd, win32.WM_SYSCOMMAND, 0xF120, 0)   # SC_RESTORE
    # 2) 异步显示
    win32.ShowWindowAsync(hwnd, win32.SW_RESTORE)
    time.sleep(0.4)
    print("  SC_RESTORE + SW_RESTORE", state(hwnd))

    # 3) 直接改窗口放置状态 —— 只在目标响应时做(它会同步发消息)
    if not hung:
        ok = win32.set_placement_cmd(hwnd, win32.SW_SHOWNORMAL)
        time.sleep(0.4)
        print(f"  SetWindowPlacement ret={ok}", state(hwnd))
    else:
        print("  跳过 SetWindowPlacement(目标无响应,会阻塞)")

    # 4) 再补两轮异步唤醒(有些游戏要连续几次)
    for i in range(3):
        win32.ShowWindowAsync(hwnd, win32.SW_SHOW)
        win32.PostMessageW(hwnd, win32.WM_SYSCOMMAND, 0xF120, 0)
        time.sleep(0.35)
    print("  连续异步唤醒后", state(hwnd), "hung=", win32.is_hung(hwnd))

    # 5) 抢前台(目标响应时才做)
    if not win32.is_hung(hwnd):
        fg_force(hwnd)
        time.sleep(0.4)
        print("  抢前台  ", state(hwnd), "fg=", win32.GetForegroundWindow() == hwnd)

    n = set_process_mute(pid, False)
    print(f"  取消静音影响 {n} 个会话")
    print("最终    ", state(hwnd))

    if win32.is_hung(w.hwnd) or win32.IsIconic(w.hwnd):
        print("\n没救回来。原因和目标程序的状态有关:")
        print("  · hung=True 表示它主线程已经不理消息了 —— 任何外部手段都进不去,")
        print("    这种只能结束进程重开(对局会丢,但窗口确实回不来)。")
        print("  · 用任务管理器结束它即可,不影响本工具。")
    else:
        print("\n已恢复。切回去看一下画面。")
    _pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
