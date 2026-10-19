"""logic_test.py - 纯逻辑单元测试(不需要桌面/不需要真实窗口)。

与其它测试的分工:
  - 这个文件只测**可以脱离 Windows 桌面验证**的纯函数:分类规则、
    目标匹配、配置序列化与旧版兼容、热键文案。
  - 需要真实窗口的(隐藏/恢复/静音/热键注册/全屏判定)在各自的
    integration / audio / fullscreen 测试里,那些必须有真实桌面会话。

之所以单独拆出来,是为了能放进 CI(GitHub Actions 的 windows runner
没有交互式桌面,跑 GUI 测试必然 flaky)。

    python tests/logic_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowhide.config import (  # noqa: E402
    Config, HotkeySpec, MOD_ALT, MOD_CONTROL, MOD_SHIFT,
)
from windowhide.core.classify import classify_window, type_label  # noqa: E402
from windowhide.core.hotkeys import hotkey_label  # noqa: E402
from windowhide.core.targets import TargetRule  # noqa: E402
from windowhide.core.win32 import WindowInfo  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


def win(process: str = "", title: str = "t", cls: str = "Cls",
        style: int = 0, ex_style: int = 0,
        rect: tuple[int, int, int, int] = (0, 0, 800, 600),
        visible: bool = True, pid: int = 1000) -> WindowInfo:
    return WindowInfo(hwnd=1, pid=pid, title=title, class_name=cls,
                      visible=visible, minimized=False,
                      ex_style=ex_style, style=style,
                      process_name=process, rect=rect)


WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080


def test_classify() -> None:
    print("== 分类规则 ==")
    check("浏览器", classify_window(win("msedge.exe")) == "browser")
    check("即时通讯", classify_window(win("WeChat.exe")) == "chat",
          classify_window(win("WeChat.exe")))
    check("游戏(词典)", classify_window(win("GenshinImpact.exe")) == "game")
    check("媒体播放", classify_window(win("vlc.exe")) == "media")
    check("系统", classify_window(win("explorer.exe")) == "system")
    check("未知进程归到其它", classify_window(win("foo.exe")) == "other")
    check("进程名大小写不敏感", classify_window(win("MSEDGE.EXE")) == "browser")

    # 无边框全屏置顶 + 大尺寸 => 启发式判为游戏
    game_like = win("unknown.exe", title="Game", style=WS_POPUP,
                    ex_style=WS_EX_TOPMOST, rect=(0, 0, 1920, 1080))
    check("无边框全屏置顶 => 游戏", classify_window(game_like) == "game")
    # 有标题栏 / 尺寸太小 / 无标题 / 非置顶 都不该判成游戏
    check("有标题栏不算游戏",
          classify_window(win("u.exe", style=WS_POPUP | WS_CAPTION,
                              ex_style=WS_EX_TOPMOST,
                              rect=(0, 0, 1920, 1080))) == "other")
    check("小窗口不算游戏",
          classify_window(win("u.exe", style=WS_POPUP,
                              ex_style=WS_EX_TOPMOST,
                              rect=(0, 0, 400, 300))) == "other")
    check("无标题不算游戏",
          classify_window(win("u.exe", title="", style=WS_POPUP,
                              ex_style=WS_EX_TOPMOST,
                              rect=(0, 0, 1920, 1080))) == "other")
    check("非置顶不算游戏",
          classify_window(win("u.exe", style=WS_POPUP,
                              rect=(0, 0, 1920, 1080))) == "other")
    check("类型中文名", type_label("browser") == "浏览器"
          and type_label("没这个类型") == "其他")


def test_targets() -> None:
    print("== 目标规则匹配 ==")
    w = win("notepad.exe", title="未命名 - 记事本", cls="Notepad")
    r = TargetRule(process="notepad.exe")
    check("按进程名命中", r.matches(w))
    check("进程名不匹配则不命中", not TargetRule(process="other.exe").matches(w))
    check("窗口类名限定命中", TargetRule(process="notepad.exe",
                                        window_class="Notepad").matches(w))
    check("窗口类名不符则不命中", not TargetRule(process="notepad.exe",
                                                window_class="Other").matches(w))
    check("标题正则命中", TargetRule(process="notepad.exe",
                                    title_regex="记事本$").matches(w))
    check("标题正则不符则不命中", not TargetRule(process="notepad.exe",
                                                title_regex="^Chrome").matches(w))
    # 非法正则不能抛异常,按不匹配处理
    check("非法正则不抛异常", not TargetRule(process="notepad.exe",
                                             title_regex="[").matches(w))
    check("进程名为空时不命中", not TargetRule(process="").matches(win("")))
    check("规则可序列化往返",
          TargetRule.from_dict(TargetRule(process="a.exe", label="L").to_dict())
          .process == "a.exe")


def test_config() -> None:
    print("== 配置 ==")
    c = Config()
    check("默认单一快捷键模式", c.hotkey_mode == "toggle")
    check("默认隐藏时静音=关", c.mute_on_hide is False)
    check("默认保持隐藏=开", c.keep_hidden is True)

    c.hotkey_mode = "split"
    c.mute_on_hide = True
    c.hotkey_hide = HotkeySpec(MOD_CONTROL | MOD_ALT, 0x48)
    c.hotkey_restore = HotkeySpec(MOD_CONTROL | MOD_ALT, 0x53)
    c.targets = [TargetRule(process="notepad.exe", label="记事本")]
    d = c.to_dict()
    c2 = Config.from_dict(d)
    check("模式往返", c2.hotkey_mode == "split")
    check("布尔往返", c2.mute_on_hide is True and c2.keep_hidden is True)
    check("隐藏键往返", (c2.hotkey_hide.mods, c2.hotkey_hide.vk)
          == (MOD_CONTROL | MOD_ALT, 0x48))
    check("恢复键往返", (c2.hotkey_restore.mods, c2.hotkey_restore.vk)
          == (MOD_CONTROL | MOD_ALT, 0x53))
    check("规则往返", c2.targets and c2.targets[0].process == "notepad.exe")

    # v1 旧配置:hotkey_foreground -> hotkey_hide_self
    old = {"hotkey_foreground": {"mods": MOD_CONTROL | MOD_ALT, "vk": 0x51}}
    c3 = Config.from_dict(old)
    check("旧版 hotkey_foreground 迁移到 hide_self",
          (c3.hotkey_hide_self.mods, c3.hotkey_hide_self.vk)
          == (MOD_CONTROL | MOD_ALT, 0x51))
    # 脏数据不能崩:类型不对的字段按"不认"处理,不能污染成错误值
    c4 = Config.from_dict({"mute_on_hide": "yes", "hotkey_mode": 123,
                           "targets": "not-a-list"})
    check("脏数据类型不崩也不写坏配置",
          c4.mute_on_hide is False and c4.hotkey_mode == "toggle"
          and c4.targets == [],
          f"mute={c4.mute_on_hide} mode={c4.hotkey_mode!r}")


def test_hotkey_label() -> None:
    print("== 热键文案 ==")
    check("Ctrl+Alt+H", hotkey_label(MOD_CONTROL | MOD_ALT, 0x48)
          == "Ctrl + Alt + H")
    check("Shift+Enter(VK 0x0D)",
          hotkey_label(MOD_SHIFT, 0x0D) == "Shift + Enter",
          hotkey_label(MOD_SHIFT, 0x0D))
    check("F 键", hotkey_label(0, 0x70) == "F1", hotkey_label(0, 0x70))
    check("数字键", hotkey_label(MOD_CONTROL, 0x31) == "Ctrl + 1",
          hotkey_label(MOD_CONTROL, 0x31))


def main() -> int:
    test_classify()
    test_targets()
    test_config()
    test_hotkey_label()
    failed = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(failed)}/{len(results)}")
    for name, _ok, detail in failed:
        print(f"  失败: {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
