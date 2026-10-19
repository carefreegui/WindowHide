"""config.py - 配置持久化(JSON,存于 %APPDATA%/WindowHide/)。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .core.targets import TargetRule

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


@dataclass(slots=True)
class HotkeySpec:
    mods: int = 0
    vk: int = 0

    @property
    def valid(self) -> bool:
        return self.vk != 0 and (self.mods & (MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN)) != 0

    def to_dict(self) -> dict:
        return {"mods": self.mods, "vk": self.vk}

    @classmethod
    def from_dict(cls, d: dict) -> "HotkeySpec":
        return cls(mods=int(d.get("mods", 0)), vk=int(d.get("vk", 0)))


def app_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        p = Path(base) / "WindowHide"
    else:  # 兜底:放脚本旁边
        p = Path(__file__).resolve().parent.parent
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


@dataclass(slots=True)
class Config:
    # 快捷键
    hotkey_mode: str = "toggle"                       # toggle | split
    hotkey_toggle: HotkeySpec = field(default_factory=lambda: HotkeySpec(MOD_CONTROL | MOD_ALT, 0x48))  # Ctrl+Alt+H
    hotkey_hide: HotkeySpec = field(default_factory=lambda: HotkeySpec(MOD_CONTROL | MOD_ALT, 0x48))    # Ctrl+Alt+H
    hotkey_restore: HotkeySpec = field(default_factory=lambda: HotkeySpec(MOD_CONTROL | MOD_ALT, 0x53)) # Ctrl+Alt+S
    # "隐藏本工具":把主窗口收进托盘(不是藏别人窗口 —— 曾按"隐藏前台窗口"
    # 实现,那个语义只会误伤,已按用户反馈改为工具自藏)
    hotkey_hide_self: HotkeySpec = field(default_factory=lambda: HotkeySpec(MOD_CONTROL | MOD_ALT, 0x51))  # Ctrl+Alt+Q

    # 行为
    mute_on_hide: bool = False
    keep_hidden: bool = True
    activate_on_restore: bool = True
    minimize_to_tray: bool = True
    show_balloon: bool = True
    autostart: bool = False
    # 强制隐藏全屏游戏:先把它最小化(逼出独占全屏)再隐藏。
    # **默认关闭** —— 实测永劫无间被最小化后主线程不再处理消息,
    # 游戏自己回不来、工具也跟着卡死。默认行为是"藏不掉就如实说",
    # 至少不会把用户的对局搞没。
    force_fullscreen_hide: bool = False

    # 目标规则
    targets: list[TargetRule] = field(default_factory=list)

    # ---------------------------------------------------------------- 持久化
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "hotkey_mode": self.hotkey_mode,
            "hotkey_toggle": self.hotkey_toggle.to_dict(),
            "hotkey_hide": self.hotkey_hide.to_dict(),
            "hotkey_restore": self.hotkey_restore.to_dict(),
            "hotkey_hide_self": self.hotkey_hide_self.to_dict(),
            "mute_on_hide": self.mute_on_hide,
            "keep_hidden": self.keep_hidden,
            "activate_on_restore": self.activate_on_restore,
            "minimize_to_tray": self.minimize_to_tray,
            "show_balloon": self.show_balloon,
            "autostart": self.autostart,
            "force_fullscreen_hide": self.force_fullscreen_hide,
            "targets": [t.to_dict() for t in self.targets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        c = cls()
        if not isinstance(d, dict):
            return c
        mode = d.get("hotkey_mode", "toggle")
        # 类型不对就当没设置:脏值(数字/None)会让按模式显隐输入行
        # 之类的地方拿到意料之外的值
        c.hotkey_mode = mode if isinstance(mode, str) else "toggle"
        for key in ("hotkey_toggle", "hotkey_hide", "hotkey_restore",
                    "hotkey_hide_self"):
            if key in d and isinstance(d[key], dict):
                setattr(c, key, HotkeySpec.from_dict(d[key]))
        # 兼容:v1 的"隐藏前台窗口"快捷键改名为"隐藏本工具"
        # (旧键读进来,语义从藏别人窗口变为工具自藏到托盘)
        if "hotkey_foreground" in d and isinstance(d["hotkey_foreground"], dict):
            c.hotkey_hide_self = HotkeySpec.from_dict(d["hotkey_foreground"])
        for key in ("mute_on_hide", "keep_hidden", "activate_on_restore",
                    "minimize_to_tray", "show_balloon", "autostart",
                    "force_fullscreen_hide"):
            # 只认真正的布尔值:bool("yes") 会静默变成 True,把用户
            # 手改坏的配置读成"开",排查起来极其费劲
            if isinstance(d.get(key), bool):
                setattr(c, key, d[key])
        c.targets = [TargetRule.from_dict(t) for t in d.get("targets", [])
                     if isinstance(t, dict)] if isinstance(d.get("targets"), list) else []
        return c

    def save(self) -> None:
        path = app_dir() / "config.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    @classmethod
    def load(cls) -> "Config":
        path = app_dir() / "config.json"
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            return cls()

    # ---------------------------------------------------------------- 自启动
    def apply_autostart(self) -> None:
        """按当前开关状态增删 HKCU\\...\\Run 自启动项。"""
        try:
            import sys
            import winreg
            if getattr(sys, "frozen", False):
                # PyInstaller 打包后直接启动 exe
                cmd = f'"{sys.executable}"'
            else:
                script = Path(__file__).resolve().parent.parent / "run.pyw"
                cmd = f'"{sys.executable}" "{script}"'
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE)
            with key:
                if self.autostart:
                    winreg.SetValueEx(key, "WindowHide", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, "WindowHide")
                    except FileNotFoundError:
                        pass
        except OSError:
            pass
