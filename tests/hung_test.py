"""hung_test.py - 目标无响应时的行为回归(用户实测事故的护栏)。

事故经过:隐藏一个卡死的全屏游戏,工具界面直接变成"未响应",用户只能
强杀进程;游戏窗口也永远回不来。根因两条:

1. 主线程上做跨进程同步窗口调用 —— 目标不处理消息,调用方就一起卡死;
2. 没有"目标是否无响应"的前置检查。

这个测试用 hang_probe 造一个真的不 pump 消息的窗口,验证:
  1. `is_hung` 能认出来(注意 Windows 要 5 秒才把线程标成 not responding);
  2. `hide_pids(wait=False)` 立刻返回,不阻塞调用线程(实测 < 100ms);
  3. 那个进程完全不被碰(窗口仍可见、无隐藏记录);
  4. 会发出"目标无响应"的提示,而不是假装成功。

    python tests/hung_test.py
"""
from __future__ import annotations

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


def wait_for(fn, timeout=15.0, interval=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def main() -> int:
    app = QCoreApplication(sys.argv)  # noqa: F841  信号/QTimer 需要

    print("== 1. 造一个真正无响应的窗口 ==")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "hang_probe.py")],
        creationflags=subprocess.CREATE_NO_WINDOW)

    def find():
        # 按**窗口类名**找,不按 pid:`.venv\Scripts\python.exe` 是个中转进程,
        # Popen.pid 和真正跑脚本的 python 进程不是同一个(实测 16256 vs 28840)。
        # 也不能按标题找 —— 无响应窗口的标题读出来是空串。
        for w in win32.enum_top_level_windows():
            if w.class_name == "WHHangProbeWnd":
                return w
        return None

    win = wait_for(find)
    if not win:
        print("挂起探针没起窗口,测试中止")
        proc.kill()
        return 1
    probe_pid = win.pid
    print(f"    hwnd={win.hwnd} pid={probe_pid} class={win.class_name!r}")

    # Windows 要等线程 5 秒不理消息才把它标成 "not responding"
    time.sleep(6.0)
    check("is_hung 认出了无响应窗口", win32.is_hung(win.hwnd))

    print("== 2. 隐藏调用必须立刻返回(不阻塞界面) ==")
    cfg = Config()
    cfg.mute_on_hide = False
    cfg.keep_hidden = False
    controller = HideController(cfg)
    hints: list[tuple[int, str]] = []
    controller.hide_incomplete.connect(lambda n, h: hints.append((n, h)))

    t0 = time.perf_counter()
    controller.hide_pids([probe_pid])          # wait=False:界面路径
    elapsed_ms = (time.perf_counter() - t0) * 1000
    check("hide_pids 立即返回(主线程不阻塞)", elapsed_ms < 100,
          f"{elapsed_ms:.1f} ms")

    print("== 3. 那个进程完全不能被碰 ==")
    # 后台线程 emit 的信号是队列投递的 —— 没有事件循环就永远收不到,
    # 所以要边等边 processEvents(真实运行时主线程在跑 exec(),天然满足)
    for _ in range(30):
        app.processEvents()
        time.sleep(0.05)
    check("没有产生隐藏记录", not controller.is_pid_hidden(probe_pid))
    check("窗口仍然可见(没被藏、也没被最小化)",
          bool(win32.IsWindowVisible(win.hwnd))
          and not bool(win32.IsIconic(win.hwnd)))
    check("进程还活着", win32.process_is_alive(probe_pid))
    check("发出了「目标无响应」提示", len(hints) >= 1, str(hints))
    check("提示文案点明了无响应",
          bool(hints) and "无响应" in hints[0][1])

    print("== 4. 慢窗口不再拖慢列表刷新(慢窗口缓存) ==")
    # 实测过:3 个挂起窗口能把一次全量刷新从 31ms 拉到 349ms ——
    # 取标题要等满 100ms 超时,而且每轮都重新等一遍。
    # 用 --slow 探针(每条消息睡 250ms:活着但很忙)稳定复现"取标题慢"。
    slow_proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "hang_probe.py"), "--slow"],
        creationflags=subprocess.CREATE_NO_WINDOW)
    slow = wait_for(lambda: next(
        (w for w in win32.enum_top_level_windows()
         if w.class_name == "WHHangProbeWnd" and w.hwnd != win.hwnd), None))
    if not slow:
        check("慢窗口探针起来了", False)
        slow_proc.kill()
    else:
        win32.clear_slow_cache()
        t0 = time.perf_counter()
        ws = win32.enum_top_level_windows()
        first = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        for _ in range(3):
            win32.enum_top_level_windows()
        later = (time.perf_counter() - t0) / 3 * 1000
        check("第二轮明显更快(不再重复等超时)", later < first - 50,
              f"首次 {first:.0f}ms → 之后 {later:.0f}ms")
        check("慢窗口被记住", win32.is_known_slow(slow.hwnd))
        check("记住后不再返回标题(省掉每次 100ms 等待)",
              not win32.get_window_text(slow.hwnd))
        check("慢窗口被标记 hung(界面显示「无响应」)",
              any(w.hung for w in ws if w.hwnd == slow.hwnd))
        slow_proc.kill()
        try:
            slow_proc.wait(timeout=5)
        except Exception:
            pass

    print("== 5. 对照组:同一个调用路径,响应正常的窗口照样能藏 ==")
    dummy = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "dummy_app.py")],
        creationflags=subprocess.CREATE_NO_WINDOW)

    def find_dummy():
        for w in win32.enum_top_level_windows():
            if (w.title or "").startswith("WH-Test-Target-"):
                return w
        return None

    d = wait_for(find_dummy)
    if not d:
        check("对照组窗口起来了", False)
    else:
        check("正常窗口不被误判为无响应", not win32.is_hung(d.hwnd))
        controller.hide_pids([d.pid], wait=True)
        time.sleep(0.2)
        check("正常窗口被正常隐藏", not bool(win32.IsWindowVisible(d.hwnd)))
        controller.restore_all(wait=True)

    print("== 6. 收尾 ==")
    controller._timer.stop()
    for p in (proc, dummy, slow_proc):
        p.kill()
        try:
            p.wait(timeout=5)
        except Exception:
            pass
    check("测试进程已全部结束", all(p.poll() is not None for p in (proc, dummy)))

    failed = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(failed)}/{len(results)}")
    for name, _ok, detail in failed:
        print(f"  失败: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
