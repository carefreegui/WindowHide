"""classify.py - 按进程名/窗口样式把窗口归类为应用类型。

分类**只依赖本地信息**,不访问网络、不加载目标进程:
1. 进程名精确匹配(最常见的那些)
2. 进程名前缀兜底(微信 4.x 叫 Weixin.exe、企业微信叫 WXWork.exe…)
3. 窗口类名/样式启发式(Unity/Unreal 的游戏、无边框全屏置顶窗口)
4. exe 的「文件说明」兜底(国产软件改名的最后一根救命稻草)

另外,**结果按 hwnd 粘住**:见 `classify_window` 的注释 —— 类型是程序的属性,
不应该因为窗口被藏起来就变。
"""
from __future__ import annotations

from . import win32
from .win32 import WindowInfo

WS_CAPTION = 0x00C00000
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008

BROWSER_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "iexplore.exe", "360se.exe", "360chrome.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "liebao.exe", "2345explorer.exe", "maxthon.exe",
    "vivaldi.exe", "yandex.exe", "whale.exe", "centbrowser.exe",
    "chromium.exe", "quarkpc.exe",
}

# 微信 4.x 的进程叫 Weixin.exe(不再是 WeChat.exe),小程序容器叫
# WeChatAppEx.exe —— 只写 wechat.exe 的话新版微信会被判成「其他」。
CHAT_PROCESSES = {
    "wechat.exe", "weixin.exe", "wechatappex.exe", "wechatapp.exe",
    "wxwork.exe", "wework.exe", "wemeetapp.exe", "wemeet.exe",
    "qq.exe", "qqnt.exe", "tim.exe", "qqprotect.exe",
    "dingtalk.exe", "dingtalkhelper.exe",
    "lark.exe", "feishu.exe", "teams.exe", "ms-teams.exe",
    "telegram.exe", "telegramdesktop.exe", "discord.exe",
    "whatsapp.exe", "whatsappdesktop.exe", "line.exe", "skype.exe",
    "zoom.exe", "tencentmeeting.exe",
}

GAME_PROCESSES = {
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
    "battle.net.exe", "heroes.exe", "leagueclient.exe", "league of legends.exe",
    "genshinimpact.exe", "yuanshen.exe", "starrail.exe", "hoyoplay.exe",
    "netease.exe", "qqgame.exe", "wegame.exe", "narakabladepoint.exe",
    "tslgame.exe", "dota2.exe", "cs2.exe", "gta5.exe", "eldenring.exe",
}

MEDIA_PROCESSES = {
    "potplayermini64.exe", "potplayer.exe", "potplayermini.exe",
    "vlc.exe", "mpv.exe", "mpc-hc64.exe", "wmplayer.exe",
    "cloudmusic.exe", "qqmusic.exe", "kugou.exe", "kwmusic.exe",
    "ximalaya.exe",
    "foobar2000.exe", "aimp.exe", "itunes.exe", "spotify.exe",
    "bilibili.exe", "哔哩哔哩.exe", "twitch.exe",
    "qqlive.exe", "iqiyi.exe", "qyclient.exe", "youku.exe",
    "mgtv.exe", "douyin.exe",
}

OFFICE_DEV_PROCESSES = {
    "code.exe", "cursor.exe", "idea64.exe", "idea.exe",
    "pycharm64.exe", "pycharm.exe", "webstorm64.exe", "clion64.exe",
    "devenv.exe", "notepad++.exe", "sublime_text.exe",
    "typora.exe", "obsidian.exe", "winword.exe", "excel.exe",
    "powerpnt.exe", "wps.exe", "et.exe", "wpp.exe", "wpsoffice.exe",
    "notepad.exe", "wordpad.exe", "onenote.exe", "acrobat.exe",
}

SYSTEM_PROCESSES = {
    "explorer.exe", "taskmgr.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "windowsterminal.exe", "calc.exe", "mstsc.exe",
    "regedit.exe", "services.exe", "mmc.exe", "control.exe",
    "systemsettings.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "runtimebroker.exe",
    "textinputhost.exe", "applicationframehost.exe", "sihost.exe",
    "ctfmon.exe",
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

# 进程名前缀兜底。**顺序有讲究**:QQ 系共用 "qq" 前缀,所以
# qqmusic / qqbrowser / qqgame 必须排在 "qq" 前面,否则 QQ 音乐会被认成聊天。
_PREFIX_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("qqmusic", "cloudmusic", "kugou", "kuwo", "xiami", "qqlive",
      "iqiyi", "qiyi", "youku", "mgtv", "bilibili", "douyin",
      "potplayer", "vlc", "mpv", "wmplayer", "spotify", "foobar",
      "ximalaya", "neteasecloudmusic"), "media"),
    (("qqbrowser", "360se", "360chrome", "sogouexplorer", "liebao",
      "centbrowser", "chromium", "msedge", "chrome", "firefox"), "browser"),
    (("qqgame", "wegame", "steam", "epicgames", "battle.net"), "game"),
    (("weixin", "wechat", "wxwork", "wework", "wemeet", "dingtalk",
      "feishu", "lark", "telegram", "discord", "whatsapp",
      "tim"), "chat"),
    # "qq" 放最后:上面已经把 qq 开头但别类的都摘走了
    (("qq",), "chat"),
)

# 窗口类名里的引擎特征 —— 不看可见性,被藏起来也还是游戏
_GAME_CLASS_HINTS = ("unitywndclass", "unrealwindow", "sdl_app", "tgcgamewnd")

# exe「文件说明」的关键词。顺序同上:先摘走"看着像但其实是别的类"的。
_DESC_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("音乐", "播放器", "影音", "视频", "直播", "电台", "music", "player",
      "media", "video", "potplayer", "vlc", "spotify"), "media"),
    (("浏览器", "browser", "chrome", "firefox", "edge"), "browser"),
    (("游戏", "启动器", "对战平台", "game", "launcher", "steam"), "game"),
    (("记事本", "笔记", "文档", "表格", "演示", "代码", "编辑器", "notepad",
      "word", "excel", "powerpoint", "code", "editor", "studio"), "office"),
    (("微信", "企业微信", "钉钉", "飞书", "会议", "聊天", "通讯", "wechat",
      "weixin", "qq", "tim", "chat", "meeting", "team"), "chat"),
)

# hwnd -> 类型。类型是**程序的属性**,不是窗口当前状态的函数 —— 一旦认出
# 是"游戏",窗口被藏起来之后还得是"游戏"。
_CACHE: dict[tuple[int, str], str] = {}
_CACHE_MAX = 8192


def classify_window(win: WindowInfo) -> str:
    """返回窗口的应用类型 key(见 TYPE_LABELS)。

    结果**按 hwnd 粘住**:以前每帧都重新算,而"无边框全屏置顶"这条启发式
    要求窗口当时可见 —— 于是用户把游戏藏起来之后,类型当场从「游戏」掉回
    「其他」,看着像数据被改坏了。现在只要认出来过一次就不再改口。
    `IsWindow(hwnd)` 变成假之后,下一次枚举自然不会再遇到它。
    """
    key = (int(win.hwnd), (win.process_name or "").lower())
    remembered = _CACHE.get(key)
    if remembered:
        return remembered
    kind = _classify(win)
    if kind != "other":
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = kind
    return kind


def clear_cache() -> None:
    """清掉 hwnd 缓存(测试用;正常运行不需要)。"""
    _CACHE.clear()


def _classify(win: WindowInfo) -> str:
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

    for prefixes, kind in _PREFIX_RULES:
        if name.startswith(prefixes):
            return kind

    if _looks_like_game_class(win):
        return "game"
    if _looks_like_fullscreen_game(win):
        return "game"

    return _type_by_description(win)


def _looks_like_game_class(win: WindowInfo) -> bool:
    """按窗口类名认引擎。**不看可见性** —— 被藏起来的游戏照样是游戏。"""
    cls = (win.class_name or "").lower()
    return bool(cls) and any(h in cls for h in _GAME_CLASS_HINTS)


def _type_by_description(win: WindowInfo) -> str:
    """最后一招:读 exe 的「文件说明」按关键词归类。

    国产软件太爱改进程名了(微信 4.x → Weixin.exe),穷举进程名永远会漏。
    文件说明是给人看的,而且同厂商基本一致,兜底效果好得多。
    """
    desc = win32.file_description(win.process_path or "").lower()
    if not desc:
        return "other"
    for words, kind in _DESC_RULES:
        if any(w in desc for w in words):
            return kind
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
