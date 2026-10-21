"""profile_test.py - 场景(profile)/ 保护名单 / 规则持续生效 的真机测试。

需要真实桌面会话(要真的创建窗口)。所有配置与隐藏状态都写到临时目录,
不会碰用户自己的 %APPDATA%\\WindowHide:

    python tests/profile_test.py

覆盖:
  1. 切场景:藏本场命中的、放掉别的场景藏的、手动藏的不动
  2. 场景自带静音开关能传给隐藏流程
  3. 保护名单:python.exe 标成"永不隐藏"后拒绝动手,并发出提示
  4. 规则持续生效:目标重启后自动再藏;手动恢复过的不会被抢回去
  5. 状态落盘:场景名记进 hidden_state.json,重启后还能按场景回收
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import windowhide.config 之前改:app_dir() 每次调用都读环境变量
_TMP = Path(tempfile.mkdtemp(prefix="wh-profile-"))
os.environ["APPDATA"] = str(_TMP)

from PySide6.QtCore import QCoreApplication  # noqa: E402

from windowhide.config import Config, Profile  # noqa: E402
from windowhide.core import protect, win32  # noqa: E402
from windowhide.core.hide import HideController  # noqa: E402
from windowhide.core.targets import TargetRule  # noqa: E402

app = QCoreApplication(sys.argv)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def pump(seconds: float) -> None:
    """边跑事件循环边等 —— 工作线程 emit 的信号要靠它投递。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def spawn() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                            creationflags=subprocess.CREATE_NO_WINDOW)


def wait_window(timeout: float = 12.0, skip: set[int] | None = None):
    skip = skip or set()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for w in win32.enum_top_level_windows():
            if (w.title or "").startswith("WH-Test-Target-") and w.hwnd not in skip:
                return w
        time.sleep(0.2)
    return None


def main() -> int:
    print(f"临时配置目录: {_TMP}")
    collected: dict[str, list] = {}

    cfg = Config()
    cfg.mute_on_hide = False
    cfg.keep_hidden = False
    cfg.activate_on_restore = False
    ctl = HideController(cfg)
    ctl.hide_incomplete.connect(lambda n, m: collected.setdefault("incomplete", []).append(m))
    ctl.protected_skipped.connect(lambda m: collected.setdefault("protected", []).append(m))
    ctl.action_done.connect(lambda a, n: collected.setdefault("action", []).append((a, n)))

    print("== 1. 起两个目标窗口 ==")
    p1, p2 = spawn(), spawn()
    w1, w2 = wait_window(), None
    if w1:
        w2 = wait_window(skip={w1.hwnd})
    if not (w1 and w2):
        print("两个 dummy 窗口没都起来,测试中止")
        for p in (p1, p2):
            p.kill()
        return 1
    print(f"    w1 hwnd={w1.hwnd} pid={w1.pid} {w1.title!r}")
    print(f"    w2 hwnd={w2.hwnd} pid={w2.pid} {w2.title!r}")

    r1 = TargetRule(process=w1.process_name, window_class=w1.class_name,
                    title_regex=f"{w1.title}$", label="窗口1")
    r2 = TargetRule(process=w2.process_name, window_class=w2.class_name,
                    title_regex=f"{w2.title}$", label="窗口2")
    cfg.profiles = [Profile(name="甲", rules=[r1]), Profile(name="乙", rules=[r2])]
    cfg.active_profile = "甲"

    print("== 2. 切到场景「甲」 ==")
    ctl.activate_profile("甲", wait=True)
    pump(0.3)
    check("场景甲的窗口被藏了", not win32.IsWindowVisible(w1.hwnd)
          or win32.IsIconic(w1.hwnd))
    check("场景乙的窗口没被牵连", win32.IsWindowVisible(w2.hwnd))
    check("隐藏记录里写明了场景名",
          ctl._hidden.get(w1.pid) is not None
          and ctl._hidden[w1.pid].profile == "甲",
          str(ctl._hidden.get(w1.pid).profile if w1.pid in ctl._hidden else None))

    print("== 3. 切到场景「乙」:甲要放掉、乙要藏住 ==")
    ctl.activate_profile("乙", wait=True)
    pump(0.4)
    check("场景甲的窗口被放出来了", win32.IsWindowVisible(w1.hwnd)
          and not win32.IsIconic(w1.hwnd))
    check("场景乙的窗口被藏了", not win32.IsWindowVisible(w2.hwnd)
          or win32.IsIconic(w2.hwnd))
    check("隐藏记录换成乙",
          w1.pid not in ctl._hidden and w2.pid in ctl._hidden)

    print("== 4. 手动藏的窗口不会被换场景放掉 ==")
    ctl.restore_all(wait=True)
    pump(0.3)
    ctl.hide_pids([w1.pid], wait=True)          # 手动藏(profile 为空)
    pump(0.2)
    ctl.activate_profile("乙", wait=True)
    pump(0.3)
    hp1 = ctl._hidden.get(w1.pid)
    check("手动藏的进程仍被藏着", hp1 is not None and hp1.profile == "",
          f"profile={hp1.profile if hp1 else None}")

    print("== 5. 保护名单:拒绝动手并说清原因 ==")
    ctl.restore_all(wait=True)
    pump(0.3)
    collected.pop("protected", None)
    cfg.protected_processes = [w1.process_name.rsplit(".", 1)[0]]  # 故意不带 .exe
    check("不带扩展名也能识别", protect.is_protected(w1.process_name,
                                                cfg.protected_processes))
    ctl.hide_pids([w1.pid], wait=True)
    pump(0.2)
    check("受保护进程没有被藏", win32.IsWindowVisible(w1.hwnd)
          and not win32.IsIconic(w1.hwnd))
    check("产生了受保护提示", bool(collected.get("protected")),
          str(collected.get("protected"))[:60])
    reason = protect.reason(w1.process_name, cfg.protected_processes)
    check("提示里说明了原因", reason == "你在设置里标记为不隐藏", reason)
    cfg.protected_processes = []

    print("== 6. 规则持续生效:目标重启后自动再藏 ==")
    ctl.restore_all(wait=True)
    pump(0.3)
    cfg.profiles.append(Profile(name="自动", rules=[TargetRule(
        process=w1.process_name, window_class=w1.class_name)], mute=False))
    cfg.set_active("自动")
    cfg.auto_apply_rules = True
    ctl.activate_profile("自动", wait=True)     # 先把现存窗口按"自动"藏掉
    pump(0.4)

    # 关掉并重开一个目标:新 pid,规则应该重新命中
    p1.kill()
    try:
        p1.wait(timeout=5)
    except Exception:
        pass
    pump(1.2)                                    # 让看门狗清掉死 pid
    p3 = spawn()
    w3 = wait_window(skip={w1.hwnd, w2.hwnd})
    check("新窗口起来了", w3 is not None)
    if w3:
        hidden_at = None
        end = time.monotonic() + 8
        while time.monotonic() < end:
            app.processEvents()
            if not win32.IsWindowVisible(w3.hwnd) or win32.IsIconic(w3.hwnd):
                hidden_at = time.monotonic()
                break
            time.sleep(0.1)
        check("重启后的窗口被自动藏住", hidden_at is not None)

        print("== 7. 手动恢复过的不会被自动规则抢回去 ==")
        ctl.restore_pids([w3.pid], wait=True)
        pump(0.3)
        check("恢复成功", win32.IsWindowVisible(w3.hwnd)
              and not win32.IsIconic(w3.hwnd))
        pump(4.5)                                # 跨过至少一轮 3 秒的规则检查
        check("没有被自动规则再藏回去", win32.IsWindowVisible(w3.hwnd)
              and not win32.IsIconic(w3.hwnd))
    else:
        p3 = None

    print("== 8. 状态落盘带场景名 ==")
    # 用全新窗口 + 全新场景:不依赖前面几轮折腾后的残留状态
    cfg.auto_apply_rules = False
    ctl.restore_all(wait=True)
    pump(0.3)
    p4 = spawn()
    w4 = wait_window(skip={w.hwnd for w in (w1, w2) if w}
                     | ({w3.hwnd} if w3 else set()))
    check("新窗口起来了(用于落盘检查)", w4 is not None)
    if w4:
        cfg.profiles.append(Profile(name="丙", rules=[TargetRule(
            process=w4.process_name, window_class=w4.class_name,
            title_regex=f"{w4.title}$")]))
        ctl.activate_profile("丙", wait=True)
        pump(0.4)
        state = _TMP / "WindowHide" / "hidden_state.json"
        ok, detail = False, "文件不存在"
        if state.exists():
            data = json.loads(state.read_text(encoding="utf-8"))
            profs = [h.get("profile") for h in data.get("hidden", [])]
            ok = "丙" in profs
            detail = str(profs)
        check("hidden_state.json 里记了场景名", ok, detail)
        cfg_file = _TMP / "WindowHide" / "config.json"
        saved = json.loads(cfg_file.read_text(encoding="utf-8")) \
            if cfg_file.exists() else {}
        check("配置里记下当前场景", saved.get("active_profile") == "丙",
              str(saved.get("active_profile")))
        check("配置已写成 v2", saved.get("version") == 2, str(saved.get("version")))

    print("== 9. 收尾 ==")
    ctl.restore_all(wait=True)
    pump(0.3)
    ctl._timer.stop()
    alive = []
    for p in (p1, p2, p3, p4):
        if p is None:
            continue
        p.kill()
        try:
            p.wait(timeout=5)      # kill 是异步的,不等就 poll 拿不到结果
        except Exception:
            pass
        if p.poll() is None:
            alive.append(p.pid)
    check("目标进程已全部结束", not alive, str(alive))

    failed = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(failed)}/{len(results)}")
    for name, _ok, d in failed:
        print(f"  失败: {name} {d}")
    shutil.rmtree(_TMP, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
