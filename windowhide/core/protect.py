"""protect.py - "永远不要藏"的进程名单。

按进程隐藏是**进程级**操作:藏掉一个窗口 = 藏掉这个进程的所有窗口。
所以有些进程一旦碰上就不是"藏了一个窗口",而是把系统界面拆了一块:

- `dwm.exe`(桌面窗口管理器):藏了它,整个桌面合成都没了;
- `TextInputHost.exe`(输入法):藏了它,中文打不出字,而且用户根本
  想不到是这个工具干的;
- `ApplicationFrameHost.exe`(UWP 宿主):计算器、设置、照片这些 UWP
  应用共用它一个进程,藏掉一个窗口会连累其它应用。

这份名单是"硬护栏",不是建议。用户可以自己在设置里追加(比如公司电脑上
的某个监控客户端),但**不能把内置项移除** —— 那些项没有合理的隐藏场景。
"""
from __future__ import annotations

from typing import Iterable

# 进程名(小写) -> 界面上的解释
BUILTIN: dict[str, str] = {
    "dwm.exe": "桌面窗口管理器",
    "winlogon.exe": "登录进程",
    "logonui.exe": "登录界面",
    "csrss.exe": "系统关键进程",
    "wininit.exe": "系统关键进程",
    "services.exe": "服务控制管理器",
    "lsass.exe": "安全认证服务",
    "smss.exe": "会话管理",
    "textinputhost.exe": "输入法(藏了就打不出中文)",
    "shellexperiencehost.exe": "系统外壳",
    "startmenuexperiencehost.exe": "开始菜单",
    "searchhost.exe": "系统搜索",
    "searchapp.exe": "系统搜索",
    "applicationframehost.exe": "UWP 窗口宿主(藏一个会连累其它 UWP 应用)",
}


def normalize(names: Iterable[str]) -> set[str]:
    """把用户填的名字规整成小写进程名;不带扩展名的补 `.exe`。"""
    out: set[str] = set()
    for raw in names or ():
        if not isinstance(raw, str):
            continue
        name = raw.strip().lower()
        if not name:
            continue
        if "." not in name.rsplit("\\", 1)[-1]:
            name += ".exe"
        out.add(name)
    return out


def is_protected(process_name: str, extra: Iterable[str] = ()) -> bool:
    """这个进程是否属于"永不隐藏"。"""
    name = (process_name or "").strip().lower()
    if not name:
        return False
    if name in BUILTIN:
        return True
    return name in normalize(extra)


def reason(process_name: str, extra: Iterable[str] = ()) -> str:
    """给界面看的理由;不受保护返回空串。"""
    name = (process_name or "").strip().lower()
    if not name:
        return ""
    if name in BUILTIN:
        return BUILTIN[name]
    if name in normalize(extra):
        return "你在设置里标记为不隐藏"
    return ""


def protected_set(extra: Iterable[str] = ()) -> set[str]:
    """内置 + 用户自定义的完整名单,给界面批量判断用。"""
    return set(BUILTIN) | normalize(extra)
