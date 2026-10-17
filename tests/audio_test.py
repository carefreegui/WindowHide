"""tests/audio_test.py - 静音功能自动化测试。

流程:启动播音探针 → 等它写出 pid(且确认进程存活) → 验证静音/取消/状态查询 → 收尾。
注意:GUID 必须是 {BCDE0395-E52F-467C-...}(467C,不是 467E),
否则 CoCreateInstance 返回 REGDB_E_CLASSNOTREG(0x80040154)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowhide.core.audio import is_process_muted, set_process_mute  # noqa: E402
from windowhide.core.win32 import process_is_alive  # noqa: E402

STATE = ROOT / "tests" / "audio_probe_state.json"
PROBE = ROOT / "tests" / "audio_probe.py"
PYTHON = sys.executable


def main() -> int:
    if STATE.exists():
        STATE.unlink()

    proc = subprocess.Popen([PYTHON, str(PROBE)],
                            creationflags=subprocess.CREATE_NO_WINDOW)

    # 等探针写出 pid;轮询直到文件里的 pid 对应存活进程(规避写入竞态)
    pid = None
    deadline = time.time() + 15
    while time.time() < deadline:
        if STATE.exists():
            try:
                candidate = json.loads(STATE.read_text(encoding="utf-8"))["pid"]
                if process_is_alive(candidate):
                    pid = candidate
                    break
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is not None:
            print("FAIL  探针提前退出")
            return 1
        time.sleep(0.2)

    if pid is None:
        print("FAIL  等待探针 pid 超时")
        proc.terminate()
        return 1

    print(f"探针 pid: {pid}")
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    try:
        st0 = is_process_muted(pid)
        check("初始状态可查询(False)", st0 is False)

        n = set_process_mute(pid, True)
        check("静音影响 1 个会话", n == 1)
        check("静音后状态为 True", is_process_muted(pid) is True)

        n = set_process_mute(pid, False)
        check("取消静音影响 1 个会话", n == 1)
        check("取消后状态为 False", is_process_muted(pid) is False)
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
