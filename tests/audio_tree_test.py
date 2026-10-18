"""tests/audio_tree_test.py - 进程树静音回归测试。

背景(用户实测):浏览器能隐藏但静不了音,游戏却能静。
根因:Chromium 系浏览器(Edge/Chrome)的音频不在主进程放,而在
audio service 子进程放 —— 按主窗口 pid 找音频会话什么都静不了
(实测:Edge 主 pid 零会话,2 个会话全在子进程上);游戏是主进程
直接放音频,所以旧逻辑对游戏有效。

修复:set_process_mute / is_process_muted 的目标从"单个 pid"
扩展为"pid + 所有子孙进程"(win32.process_descendants,toolhelp
快照)。对单进程放音频的程序(游戏、探针)行为不变。

本测试用父子探针确定性地复现场景:父进程(测试自身)无音频会话,
子进程播音 —— 按父 pid 应能静音子进程。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowhide.core import audio  # noqa: E402
from windowhide.core.win32 import process_is_alive  # noqa: E402

STATE = ROOT / "tests" / "audio_probe_state.json"
PROBE = ROOT / "tests" / "audio_probe.py"


def main() -> int:
    if STATE.exists():
        STATE.unlink()

    proc = subprocess.Popen([sys.executable, str(PROBE)],
                            creationflags=subprocess.CREATE_NO_WINDOW)
    child_pid = None
    deadline = time.time() + 15
    while time.time() < deadline:
        if STATE.exists():
            try:
                candidate = json.loads(STATE.read_text(encoding="utf-8"))["pid"]
                if process_is_alive(candidate):
                    child_pid = candidate
                    break
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is not None:
            print("FAIL  探针提前退出")
            return 1
        time.sleep(0.2)
    if child_pid is None:
        print("FAIL  等待探针 pid 超时")
        proc.terminate()
        return 1

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    try:
        my_pid = os.getpid()
        print(f"父进程(测试)pid={my_pid},子探针 pid={child_pid}")

        # 等子探针的音频会话出现(异步创建)
        t0 = time.time()
        while time.time() - t0 < 10:
            if audio.is_process_muted(my_pid) is not None:
                break
            time.sleep(0.3)

        # 场景校验:父进程自身没有任何音频会话(模拟浏览器主进程),
        # 声音全在子进程 —— 这正是旧代码静不了音的原因
        own_sessions = sum(1 for _ in audio._iter_session_volumes(my_pid))
        check(f"父进程自身 0 个音频会话(实际 {own_sessions}),声音在子进程",
              own_sessions == 0)

        # 修复验证:按父 pid 静音 -> 子进程的会话被静
        n = audio.set_process_mute(my_pid, True)
        check(f"按父 pid 静音影响了 {n} 个会话(子进程的)", n > 0)
        check("按父 pid 查询为已静音", audio.is_process_muted(my_pid) is True)
        check("子探针自身查询也是已静音", audio.is_process_muted(child_pid) is True)

        # 恢复
        n = audio.set_process_mute(my_pid, False)
        check(f"按父 pid 取消静音影响了 {n} 个会话", n > 0)
        check("取消后按父 pid 查询为未静音", audio.is_process_muted(my_pid) is False)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if STATE.exists():
            STATE.unlink()

    print()
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
