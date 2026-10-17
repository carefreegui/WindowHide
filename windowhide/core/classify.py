"""classify.py - 按进程名/窗口样式把窗口归类为应用类型。

分类只依赖进程名词典 + 窗口样式启发式,不访问网络、不加载目标进程。
"""
from __future__ import annotations

from .win32 import WindowInfo

WS_CAPTION = 0x00C00000
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008

BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "iexplore.exe", "360se.exe", "360chrome.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "liebao.exe", "2345explorer.exe", "maxthon.exe",
    "vivaldi.exe", "yandex.exe", "whale.exe", "centbrowser.exe",
}

CHAT_PROCESSES = {
    "wechat.exe", "qq.exe", "qqnt.exe", "tim.exe", "dingtalk.exe",
    "lark.exe", "feishu.exe", "wemeet.exe", "teams.exe", "telegram.exe",
    "discord.exe", "whatsapp.exe", "line.exe", "钉钉.exe",
}

GAME_PROCESSES = {
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
    "battle.net.exe", "heroes.exe", "leagueclient.exe", "league of legends.exe",
    "genshinimpact.exe", "yuanshen.exe", "starrail.exe", "hoyoplay.exe",
    "netease.exe", "launcher.exe", "qqgame.exe", "wegame.exe",
}

MEDIA_PROCESSES = {
    "potplayermini64.exe", "potplayer.exe", "potplayermini.exe",
    "vlc.exe", "mpv.exe", "cloudmusic.exe", "qqmusic.exe", "kugou.exe",
    "kwmusic.exe", "ximalaya.exe", "foobar2000.exe", "aimp.exe",
    "itunes.exe", "spotify.exe", "bilibili.exe", "twitch.exe",
}

OFFICE_DEV_PROCESSES = {
    "code.exe", "idea64.exe", "idea.exe", "pycharm64.exe", "pycharm.exe",
    "webstorm64.exe", "clion64.exe", "devenv.exe", "notepad++.exe",
    "sublime_text.exe", "typora.exe", "obsidian.exe", "winword.exe",
    "excel.exe", "powerpnt.exe", "wps.exe", "et.exe", "wpp.exe",
    "notepad.exe", "wordpad.exe", "feishu.exe",
}

SYSTEM_PROCESSES = {
    "explorer.exe", "taskmgr.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "windowsterminal.exe", "calc.exe", "mstsc.exe",
    "regedit.exe", "services.exe", "mmc.exe", "control.exe",
    "systemsettings.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "runtimebroker.exe", "textinputhost.exe",
    "applicationframehost.exe", "sihost.exe", "ctfmon.exe",
}

TYPE_LABELS = {
    "browser": "浏览器",
    "chat": "即时通讯",
    "game": "游戏",
    "media": "媒体播放",
    "office": "办公/开发",
    "system": "系统",
    "other": "其他",
}

# 界面徽章配色(浅色主题)
TYPE_COLORS = {
    "browser": "#2563eb",
    "chat": "#16a34a",
    "game": "#dc2626",
    "media": "#9333ea",
    "office": "#ea580c",
    "system": "#64748b",
    "other": "#475569",
}


def classify_window(win: WindowInfo) -> str:
    """返回窗口的应用类型 key(见 TYPE_LABELS)。"""
    name = (win.process_name or "").lower()

    if name in BROWSER_PROCESSES:
        return "browser"
    if name in CHAT_PROCESSES:
        return "chat"
    if name in GAME_PROCESSES:
        return "game"
    if name in MEDIA_PROCESSES:
        return "media"
    if name in OFFICE_DEV_PROCESSES:
        return "office"
    if name in SYSTEM_PROCESSES:
        return "system"

    # 启发式:无边框全屏置顶窗口大概率是游戏
    if _looks_like_fullscreen_game(win):
        return "game"
    return "other"


def _looks_like_fullscreen_game(win: WindowInfo) -> bool:
    if not win.visible or not win.title:
        return False
    if not (win.style & WS_POPUP):          # 弹出式窗口
        return False
    if win.style & WS_CAPTION:              # 有标题栏,通常是普通软件
        return False
    if not (win.ex_style & WS_EX_TOPMOST):  # 游戏一般置顶
        return False
    l, t, r, b = win.rect
    if r - l < 800 or b - t < 600:          # 面积够大
        return False
    return True


def type_label(type_key: str) -> str:
    return TYPE_LABELS.get(type_key, "其他")


def type_color(type_key: str) -> str:
    return TYPE_COLORS.get(type_key, "#475569")
