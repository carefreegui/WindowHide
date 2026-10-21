"""config.py - 配置持久化(JSON,存于 %APPDATA%/WindowHide/)。

配置格式版本:
  v1  一次性平铺的 `targets` 列表
  v2  场景(profiles):一批规则打包成一个可命名的场景("上班"/"游戏"…),
      可以一键切换。v1 读进来会自动迁移成 v2,原文件备份成
      `config.v1.bak.json` —— 用户手写过的配置不能因为我们升级就丢了。
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field

from pathlib import Path

from .core.targets import TargetRule

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

CONFIG_VERSION = 2
DEFAULT_PROFILE = "默认"


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


@dataclass(slots=True)
class Profile:
    """一个场景:一组目标规则 + 该场景是否顺带静音。"""
    name: str = DEFAULT_PROFILE
    rules: list[TargetRule] = field(default_factory=list)
    mute: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name,
                "rules": [r.to_dict() for r in self.rules],
                "mute": self.mute}

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        name = d.get("name")
        rules = d.get("rules")
        mute = d.get("mute")
        return cls(
            name=name.strip() if isinstance(name, str) and name.strip() else DEFAULT_PROFILE,
            rules=[TargetRule.from_dict(r) for r in rules if isinstance(r, dict)]
            if isinstance(rules, list) else [],
            mute=mute if isinstance(mute, bool) else False,
        )


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
    show_balloon: bool = True          # 隐藏/恢复时的气泡确认
    # 关窗口时"已最小化到托盘"的提示。单独一个开关:很多人只想关掉这条
    # 打扰,但保留隐藏/恢复的确认(绑在一起的话就得二选一)。
    show_minimize_hint: bool = True
    autostart: bool = False
    # 强制隐藏全屏游戏:先把它最小化(逼出独占全屏)再隐藏。
    # **默认关闭** —— 实测永劫无间被最小化后主线程不再处理消息,
    # 游戏自己回不来、工具也跟着卡死。默认行为是"藏不掉就如实说",
    # 至少不会把用户的对局搞没。
    force_fullscreen_hide: bool = False
    # 规则持续生效:目标程序重启后自动按当前场景再藏一次。
    # 默认关闭 —— "打开就被藏"对没预期的人很吓人
    auto_apply_rules: bool = False

    # 场景
    profiles: list[Profile] = field(default_factory=list)
    active_profile: str = ""

    # 永不隐藏的额外进程(内置名单在 core/protect.py,这里只放用户自定义)
    protected_processes: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- 场景
    def profile_names(self) -> list[str]:
        return [p.name for p in self.profiles]

    def profile(self, name: str | None = None) -> Profile | None:
        """按名字找场景;不传名字就取当前场景。找不到返回 None。"""
        want = self.active_profile if name is None else name
        for p in self.profiles:
            if p.name == want:
                return p
        return self.profiles[0] if (name is None and self.profiles) else None

    def active(self) -> Profile | None:
        return self.profile(None)

    def ensure_profile(self, name: str = DEFAULT_PROFILE) -> Profile:
        """取场景,不存在就建一个并设为当前。"""
        p = self.profile(name)
        if p is None:
            p = Profile(name=name)
            self.profiles.append(p)
        self.active_profile = p.name
        return p

    def set_active(self, name: str) -> bool:
        for p in self.profiles:
            if p.name == name:
                self.active_profile = p.name
                return True
        return False

    @property
    def targets(self) -> list[TargetRule]:
        """当前场景的规则(兼容旧的 `config.targets` 用法)。"""
        p = self.active()
        return p.rules if p else []

    @targets.setter
    def targets(self, rules: list[TargetRule]) -> None:
        """给当前场景整批换规则;没有场景就建一个"默认"场景。"""
        p = self.ensure_profile(self.active_profile or DEFAULT_PROFILE)
        p.rules = list(rules)

    # ---------------------------------------------------------------- 持久化
    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
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
            "show_minimize_hint": self.show_minimize_hint,
            "autostart": self.autostart,
            "force_fullscreen_hide": self.force_fullscreen_hide,
            "auto_apply_rules": self.auto_apply_rules,
            "profiles": [p.to_dict() for p in self.profiles],
            "active_profile": self.active_profile,
            "protected_processes": list(self.protected_processes),
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
                    "minimize_to_tray", "show_balloon", "show_minimize_hint",
                    "autostart", "force_fullscreen_hide", "auto_apply_rules"):
            # 只认真正的布尔值:bool("yes") 会静默变成 True,把用户
            # 手改坏的配置读成"开",排查起来极其费劲
            if isinstance(d.get(key), bool):
                setattr(c, key, d[key])

        profiles = d.get("profiles")
        if isinstance(profiles, list):
            # 同名场景会让"按名字找场景"变成掷骰子,直接去重(保留第一个)
            uniq: list[Profile] = []
            seen: set[str] = set()
            for p in profiles:
                if not isinstance(p, dict):
                    continue
                prof = Profile.from_dict(p)
                if prof.name in seen:
                    continue
                seen.add(prof.name)
                uniq.append(prof)
            c.profiles = uniq
        if not c.profiles:
            # v1 迁移:平铺的 targets 收进一个叫"默认"的场景
            old = d.get("targets")
            rules = [TargetRule.from_dict(t) for t in old
                     if isinstance(t, dict)] if isinstance(old, list) else []
            if rules:
                c.profiles = [Profile(name=DEFAULT_PROFILE, rules=rules)]

        active = d.get("active_profile")
        c.active_profile = active if isinstance(active, str) else ""
        if c.profiles and not any(p.name == c.active_profile for p in c.profiles):
            c.active_profile = c.profiles[0].name

        extra = d.get("protected_processes")
        c.protected_processes = [x for x in extra if isinstance(x, str) and x.strip()] \
            if isinstance(extra, list) else []
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
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
        except (OSError, json.JSONDecodeError, ValueError):
            return cls()
        cfg = cls.from_dict(raw)
        try:
            version = int(raw.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        if version < CONFIG_VERSION:
            # 备份再升级 —— 用户手写过的配置丢了没法找回
            try:
                backup = path.with_name(f"config.v{version}.bak.json")
                if not backup.exists():
                    shutil.copy2(path, backup)
            except OSError:
                pass
            cfg.save()
        return cfg

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
