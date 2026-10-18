"""fullscreen_test.py - 全屏窗口隐藏测试。

背景(用户实测反馈):全屏游戏按"隐藏"只静了音,画面还在屏幕上。
根因是独占全屏的游戏由 D3D/DXGI 直接翻转输出到显示器,不看窗口的
WS_VISIBLE —— SW_HIDE 掉窗口也没用。修复办法是对全屏窗口改走
"先最小化(让它掉出独占全屏)、再隐藏"。

这个测试用真实 Win32 窗口验证:
  1. 全屏判定不会误伤普通窗口(带标题栏的最大化窗口不能被当成全屏);
  2. 全屏窗口走 minimize_then_hide 策略,隐藏后确实不在屏幕上;
  3. 恢复后回到原来的全屏尺寸;
  4. "藏不掉的窗口"能被识别并如实上报(不静默失败)。

    python tests/fullscreen_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from windowhide.config import Config  # noqa: E402
from windowhide.core import win32  # noqa: E402
from windowhide.core.hide import HideController  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def wait_for(fn, timeout=10.0, interval=0.15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def find_window(prefix: str):
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith(prefix):
            return w
    return None


def spawn(flag: str = "") -> subprocess.Popen:
    args = [sys.executable, str(ROOT / "tests" / "dummy_app.py")]
    if flag:
        args.append(flag)
    return subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)


def main() -> int:
    app = QCoreApplication(sys.argv)  # noqa: F841  QTimer/信号需要

    print("== 1. 全屏窗口判定 ==")
    fs_proc = spawn("--fullscreen")
    fs = wait_for(lambda: find_window("WH-Test-Fullscreen-"))
    if not fs:
        print("全屏测试窗口没起来,中止")
        fs_proc.kill()
        return 1
    l, t, r, b = win32.get_window_rect(fs.hwnd)
    ml, mt, mr, mb = win32.monitor_rect(fs.hwnd)
    print(f"    hwnd={fs.hwnd} pid={fs.pid} rect={(l, t, r, b)} "
          f"monitor={(ml, mt, mr, mb)}")
    check("全屏窗口被识别为全屏", win32.is_fullscreen_window(fs.hwnd))
    check("全屏窗口走 minimize_then_hide 策略",
          win32.hide_plan(fs.hwnd) == "minimize_then_hide",
          win32.hide_plan(fs.hwnd))
    check("窗口尺寸覆盖整块显示器", (r - l) >= (mr - ml) * 0.95
          and (b - t) >= (mb - mt) * 0.95)

    normal_proc = spawn()
    normal = wait_for(lambda: find_window("WH-Test-Target-"))
    if not normal:
        print("普通测试窗口没起来,中止")
        fs_proc.kill()
        return 1
    check("普通窗口不被误判为全屏", not win32.is_fullscreen_window(normal.hwnd))
    check("普通窗口走直接隐藏策略", win32.hide_plan(normal.hwnd) == "hide",
          win32.hide_plan(normal.hwnd))

    print("== 2. 隐藏全屏窗口 ==")
    cfg = Config()
    cfg.mute_on_hide = False          # 别把测试进程自己(或别的)静音
    cfg.keep_hidden = False           # 测试期间不要后台反复补刀
    controller = HideController(cfg)
    incomplete: list[tuple[int, str]] = []
    controller.hide_incomplete.connect(lambda n, h: incomplete.append((n, h)))

    controller.hide_pids([fs.pid])
    time.sleep(0.2)
    hp = controller._hidden.get(fs.pid)
    check("控制器记录了全屏进程", hp is not None)
    check("全屏窗口已不在屏幕上", win32.is_offscreen(fs.hwnd),
          f"visible={bool(win32.IsWindowVisible(fs.hwnd))} "
          f"iconic={bool(win32.IsIconic(fs.hwnd))}")
    check("全屏窗口没有产生「藏不掉」报警", not incomplete, str(incomplete))
    check("记录里标记了 fullscreen", bool(hp and hp.records[fs.hwnd].fullscreen))
    check("隐藏后进程仍存活", win32.process_is_alive(fs.pid))

    print("== 3. 恢复正常 ==")
    controller.restore_pids([fs.pid])
    time.sleep(0.4)
    check("恢复后窗口重新可见", bool(win32.IsWindowVisible(fs.hwnd)))
    check("恢复后不再最小化", not win32.IsIconic(fs.hwnd))
    l2, t2, r2, b2 = win32.get_window_rect(fs.hwnd)
    check("恢复后仍铺满整块显示器", (r2 - l2) >= (mr - ml) * 0.95
          and (b2 - t2) >= (mb - mt) * 0.95, f"rect={(l2, t2, r2, b2)}")

    print("== 4. 藏不掉的窗口要如实上报 ==")
    # "藏不掉"在真机上有两种成因:程序以管理员权限运行(窗口句柄被 UIPI
    # 挡住)、或者窗口被程序自己顶回来。这里用打桩确定性复现 —— 把
    # hide_window 换成什么都不做的空操作,窗口自然还留在屏幕上。
    real_hide = win32.hide_window
    win32.hide_window = lambda hwnd: (
        win32.GetWindowLongPtrW(hwnd, win32.GWL_EXSTYLE),
        win32.get_window_placement_cmd(hwnd))
    try:
        incomplete.clear()
        controller.hide_pids([normal.pid])
        time.sleep(0.2)
        hp2 = controller._hidden.get(normal.pid)
        check("顽固窗口被记录(状态没丢)", hp2 is not None)
        check("统计出 1 个藏不掉的窗口", bool(hp2 and hp2.failed == 1),
              f"failed={hp2.failed if hp2 else None}")
        check("发出了 hide_incomplete 报警", len(incomplete) == 1, str(incomplete))
        check("报警文案非空", bool(incomplete and incomplete[0][1]))
        check("窗口确实还在屏幕上(没有假装成功)",
              bool(win32.IsWindowVisible(normal.hwnd)))
    finally:
        win32.hide_window = real_hide
        controller.restore_all()

    print("== 5. 权限预检(UIPI)与半残回滚 ==")
    # 目标进程权限比我们高时,UIPI 会把窗口操作静默吃掉。System(pid=4)
    # 是高完整性进程,用它验证检测器能认出来;然后确认自己不会被误判。
    check("自己能正常操作(不被误判为高权限)",
          not win32.process_is_higher_integrity(os.getpid()))
    check("System(pid=4) 被判为高权限", win32.process_is_higher_integrity(4))

    # 回滚:模拟"最小化成功、隐藏失败"的半残状态,必须能撤回原样
    win32.set_ex_style(fs.hwnd, add=win32.WS_EX_TOOLWINDOW)
    win32.minimize_window(fs.hwnd)
    time.sleep(0.3)
    check("先真的最小化", bool(win32.IsIconic(fs.hwnd)))
    win32._undo_hide(fs.hwnd, 0x00040008, win32.SW_SHOWNORMAL)
    time.sleep(0.5)
    check("回滚后不再最小化", not win32.IsIconic(fs.hwnd))
    check("回滚后重新可见", bool(win32.IsWindowVisible(fs.hwnd)))
    check("回滚后 TOOLWINDOW 已移除",
          not (win32.get_window_styles(fs.hwnd)[1] & win32.WS_EX_TOOLWINDOW))

    print("== 6. 收尾 ==")
    controller.restore_all()
    controller._timer.stop()
    for p in (fs_proc, normal_proc):
        p.kill()
        try:
            p.wait(timeout=5)
        except Exception:
            pass
    check("测试进程已全部结束",
          all(p.poll() is not None for p in (fs_proc, normal_proc)))

    failed = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        for name, _ok, detail in failed:
            print(f"  失败: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
