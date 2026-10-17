"""targets.py - 目标窗口规则:按进程名/窗口类名/标题正则匹配窗口。"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from .win32 import WindowInfo


@dataclass(slots=True)
class TargetRule:
    """一条目标规则。process 必填,其余可选(空 = 不限制)。"""
    process: str                      # 进程名,如 "notepad.exe"
    window_class: str = ""            # 窗口类名,如 "Chrome_WidgetWin_1"
    title_regex: str = ""             # 标题正则
    label: str = ""                   # 备注名(界面显示用)

    @classmethod
    def from_window(cls, win: WindowInfo) -> "TargetRule":
        return cls(
            process=win.process_name or f"pid:{win.pid}",
            window_class=win.class_name if win.class_name else "",
            title_regex="",
            label=(win.title or win.process_name)[:60],
        )

    def matches(self, win: WindowInfo) -> bool:
        if not win.process_name or win.process_name.lower() != self.process.lower():
            return False
        if self.window_class and win.class_name != self.window_class:
            return False
        if self.title_regex:
            try:
                if not re.search(self.title_regex, win.title or "", re.IGNORECASE):
                    return False
            except re.error:
                return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TargetRule":
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in data.items() if k in known})


def resolve_target_hwnds(rules: list[TargetRule],
                         windows: list[WindowInfo] | None = None,
                         include_hidden: set[int] | None = None) -> list[WindowInfo]:
    """把规则解析成当前匹配的窗口列表。

    默认只返回可见窗口;include_hidden 里的 hwnd(被本工具隐藏的窗口)
    即使当前不可见也参与匹配,这样"隐藏后再按一次热键"能恢复它们。
    """
    if windows is None:
        from .win32 import enum_top_level_windows
        windows = enum_top_level_windows()
    hidden = include_hidden or set()
    seen: set[int] = set()
    out: list[WindowInfo] = []
    for rule in rules:
        for w in windows:
            if w.hwnd in seen:
                continue
            if not w.visible and w.hwnd not in hidden:
                continue
            if rule.matches(w):
                seen.add(w.hwnd)
                out.append(w)
    return out
