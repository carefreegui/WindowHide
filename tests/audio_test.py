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


def wait_session(pid: int, timeout: float = 10.0):
    """等探针的音频会话出现。

    会话是异步创建的:探针先写 pid 再播音,紧接着查会拿到 None
    (找不到会话),不能当作故障。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = is_process_muted(pid)
        if st is not None:
            return st
        time.sleep(0.3)
    return None


def set_mute_retry(pid: int, mute: bool, tries: int = 10) -> int:
    """带重试地静音/取消静音。

    Windows 会临时回收空闲会话再重建(播音循环重启时尤其明显),
    瞬间查会话数会是 0 —— 重试几次给它时间,别一竿子打成故障。
    """
    for _ in range(tries):
        n = set_process_mute(pid, mute)
        if n > 0:
            return n
        time.sleep(0.3)
    return 0


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
        st0 = wait_session(pid)
        check("探针音频会话出现", st0 is not None)

        # Windows 音量合成器按"应用"记住静音状态:上一次测试若取消静音
        # 失败,会给 python.exe 留下静音,新探针生来就是静音的 ——
        # 先复位,保证从"未静音"开始,测试才可重复。
        n0 = set_mute_retry(pid, False)
        check("复位为未静音", n0 > 0 and is_process_muted(pid) is False)

        n = set_mute_retry(pid, True)
        check("静音影响 1 个会话", n == 1)
        check("静音后状态为 True", is_process_muted(pid) is True)

        n = set_mute_retry(pid, False)
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
