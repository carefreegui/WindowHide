"""audio_probe.py - 播音探针(测试辅助)。

循环播放系统提示音,供静音功能测试。把 PID 写到 audio_probe_state.json。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import winsound

STATE = Path(__file__).resolve().parent / "audio_probe_state.json"


def main() -> None:
    pid = __import__("os").getpid()
    STATE.write_text(json.dumps({"pid": pid}), encoding="utf-8")
    winsound.PlaySound("SystemExit", winsound.SND_ASYNC | winsound.SND_LOOP)
    t0 = time.time()
    while time.time() - t0 < 60:
        time.sleep(0.5)
    winsound.PlaySound(None, winsound.SND_PURGE)


if __name__ == "__main__":
    main()
