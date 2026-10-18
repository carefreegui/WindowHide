"""hide.py - 隐藏/恢复控制器。

隐藏 = 窗口从任务栏和 Alt+Tab 消失(SW_HIDE + WS_EX_TOOLWINDOW),
      程序继续在后台运行;同时尽力移除其托盘图标。
      全屏窗口(游戏)走另一条路:先最小化让它掉出独占全屏,再 SW_HIDE
      —— 详见 win32.hide_plan。
恢复 = 还原样式与显示状态,取消静音,广播 TaskbarCreated 让托盘图标回来。

隐藏状态会持久化到 %APPDATA%/WindowHide/hidden_state.json:
本工具重启/崩溃后,被隐藏的窗口不会变成"孤儿"(任务栏没有、界面里也
恢复不了),启动时自动恢复状态,用户照常点恢复即可。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import win32
from .audio import set_process_mute
from .targets import TargetRule
from .trayhide import delete_process_tray_icons, request_tray_icon_restore


def _state_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) / "WindowHide" if base else Path(__file__).resolve().parent.parent.parent
    return root / "hidden_state.json"


@dataclass(slots=True)
class WindowRecord:
    hwnd: int
    ex_style: int
    show_cmd: int
    title: str = ""
    fullscreen: bool = False   # 隐藏时是否全屏(走的是"先最小化再隐藏")


@dataclass(slots=True)
class HiddenProcess:
    pid: int
    process_name: str
    records: dict[int, WindowRecord] = field(default_factory=dict)
    muted: bool = False
    failed: int = 0  # 权限不足等原因未能隐藏的窗口数

    @property
    def window_count(self) -> int:
        return len(self.records)


class HideController(QObject):
    """管理"哪些进程被隐藏了"以及隐藏/恢复动作。"""

    state_changed = Signal()          # 隐藏状态变化(UI 刷新用)
    action_done = Signal(str, int)    # (action, count) action: hidden/restored/failed
    hide_incomplete = Signal(int, str)  # (藏不掉的窗口数, 原因提示)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings          # Config 对象(读 mute/keep_hidden 等)
        self._hidden: dict[int, HiddenProcess] = {}
        self._last_fg_pid = 0              # 最近一次「隐藏前台窗口」藏掉的进程
        self._retry: dict[int, int] = {}   # hwnd -> 已重试次数(藏不掉的窗口别无限刷)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._watch)
        self._timer.start()
        self._load_state()

    # ------------------------------------------------------------ 状态持久化
    def _save_state(self) -> None:
        data = {"version": 1, "hidden": [
            {"pid": hp.pid, "process_name": hp.process_name, "muted": hp.muted,
             "records": [{"hwnd": r.hwnd, "ex_style": r.ex_style,
                          "show_cmd": r.show_cmd, "title": r.title,
                          "fullscreen": r.fullscreen}
                         for r in hp.records.values()]}
            for hp in self._hidden.values()
        ]}
        try:
            _state_path().write_text(json.dumps(data, ensure_ascii=False),
                                     encoding="utf-8")
        except OSError:
            pass

    def _load_state(self) -> None:
        """从磁盘恢复隐藏状态;逐项校验,过期/已被恢复的一律丢弃。"""
        try:
            data = json.loads(_state_path().read_text(encoding="utf-8"))
        except Exception:
            return
        loaded = 0
        for entry in data.get("hidden", []):
            try:
                pid = int(entry["pid"])
            except (KeyError, TypeError, ValueError):
                continue
            if not win32.process_is_alive(pid):
                continue
            hp = HiddenProcess(pid=pid,
                               process_name=str(entry.get("process_name", "")),
                               muted=bool(entry.get("muted", False)))
            for rec in entry.get("records", []):
                try:
                    hwnd = int(rec["hwnd"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not win32.IsWindow(hwnd):
                    continue
                if win32.get_window_pid(hwnd) != pid:
                    continue          # 句柄已被回收给别的窗口
                if win32.IsWindowVisible(hwnd) and not win32.IsIconic(hwnd):
                    continue          # 已经重新显示了,不是我们隐藏的状态
                if not (win32.get_window_styles(hwnd)[1] & win32.WS_EX_TOOLWINDOW):
                    continue          # 没有我们加的工具窗样式
                hp.records[hwnd] = WindowRecord(
                    hwnd, int(rec.get("ex_style", 0)),
                    int(rec.get("show_cmd", 9)), str(rec.get("title", "")),
                    bool(rec.get("fullscreen", False)))
            if hp.records:
                self._hidden[pid] = hp
                loaded += 1
        if loaded:
            self.state_changed.emit()

    # ------------------------------------------------------------ 查询
    def hidden_pids(self) -> list[int]:
        return list(self._hidden.keys())

    def hidden_hwnds(self) -> set[int]:
        out: set[int] = set()
        for hp in self._hidden.values():
            out.update(hp.records.keys())
        return out

    def is_pid_hidden(self, pid: int) -> bool:
        return pid in self._hidden

    def is_hwnd_hidden(self, hwnd: int) -> bool:
        return any(hwnd in hp.records for hp in self._hidden.values())

    def hidden_summary(self) -> tuple[int, int]:
        """(隐藏的进程数, 隐藏的窗口数)"""
        return len(self._hidden), sum(hp.window_count for hp in self._hidden.values())

    # ------------------------------------------------------------ 隐藏
    def hide_pids(self, pids: list[int]) -> int:
        """隐藏指定进程的所有可见顶层窗口。返回成功隐藏的进程数。"""
        done = 0
        failed = 0
        failed_names: list[str] = []
        for pid in dict.fromkeys(pids):
            if pid in self._hidden:
                continue
            hp = self._hide_one(pid)
            if hp and hp.window_count > 0:
                self._hidden[pid] = hp
                done += 1
                if hp.failed:
                    failed += hp.failed
                    failed_names.append(hp.process_name or f"pid {pid}")
        if done:
            self._save_state()
            self.state_changed.emit()
            self.action_done.emit("hidden", done)
        if failed:
            # 静默失败最坑:用户以为藏好了,其实窗口还在屏幕上。
            names = "、".join(dict.fromkeys(failed_names))
            self.hide_incomplete.emit(
                failed, f"{names} 有 {failed} 个窗口没藏掉,"
                        "通常是程序以管理员权限运行(需以管理员身份启动本工具),"
                        "或是它不接受系统的最小化/隐藏指令。")
        return done

    def hide_hwnds(self, hwnds: list[int]) -> int:
        """隐藏窗口列表(按进程归组)。"""
        pids: list[int] = []
        for hwnd in hwnds:
            if win32.IsWindow(hwnd):
                pids.append(win32.get_window_pid(hwnd))
        return self.hide_pids(pids)

    def hide_foreground(self) -> int:
        """隐藏当前前台窗口所属的进程。

        两道保护(用户实测"点一下连桌面都藏了"):
        1. **桌面/任务栏不藏** —— 从托盘菜单触发时,GetForegroundWindow
           拿到的是 Progman/WorkerW/Shell_TrayWnd(全是 explorer 的),
           不拦这一下就会把桌面、任务栏连同所有资源管理器窗口一起藏掉;
        2. **本工具自己不藏** —— 在主界面点按钮的瞬间,前台就是工具自己。

        注意:本方法目前没有 UI 入口(主界面按钮/托盘菜单/快捷键都已
        按用户反馈改为"工具自藏到托盘"),保留为 core 能力备用。
        """
        hwnd = win32.GetForegroundWindow()
        if hwnd:
            if win32.is_shell_window(hwnd):
                return 0
            pid = win32.get_window_pid(hwnd)
            if pid == os.getpid():
                return 0
            n = self.hide_hwnds([hwnd])
            if n:
                self._last_fg_pid = pid
            return n
        return 0

    def restore_last_foreground(self) -> bool:
        """恢复最近一次「隐藏前台窗口」藏掉的进程。没有则返回 False。"""
        pid = self._last_fg_pid
        if pid and pid in self._hidden:
            self._last_fg_pid = 0
            self.restore_pids([pid])
            return True
        return False

    def _hide_one(self, pid: int) -> HiddenProcess | None:
        name = ""
        records: dict[int, WindowRecord] = {}
        failed = 0
        for w in win32.enum_process_windows(pid):
            if not w.visible and not w.minimized:
                continue  # 本来就隐藏的窗口不动,恢复时也不会把它弹出来
            name = name or w.process_name
            fullscreen = win32.is_fullscreen_window(w.hwnd)
            ex_style, show_cmd = win32.hide_window(w.hwnd)
            records[w.hwnd] = WindowRecord(w.hwnd, ex_style, show_cmd, w.title,
                                           fullscreen)
            # 判据是"真的不在屏幕上了"(隐藏或最小化),不是 IsWindowVisible ——
            # 全屏游戏被最小化时 IsWindowVisible 依然是 True。
            if not win32.is_offscreen(w.hwnd):
                failed += 1
        if not records:
            return None
        if not name:
            # 调用方(如测试/命令行)不一定填过 process_name,报警文案要能
            # 指名道姓,不然用户只看到一个 pid
            name = win32.process_image_path(pid).rsplit("\\", 1)[-1]

        hp = HiddenProcess(pid=pid, process_name=name, records=records, failed=failed)
        if failed:
            from ..log import write
            write(f"未能隐藏的窗口:{name}(pid={pid}) 失败 {failed}/{len(records)} 个")
        # 托盘图标
        delete_process_tray_icons(pid)
        # 静音
        if self._settings.mute_on_hide:
            n = set_process_mute(pid, True)
            hp.muted = n > 0
        return hp

    # ------------------------------------------------------------ 恢复
    def restore_all(self) -> int:
        return self.restore_pids(list(self._hidden.keys()))

    def restore_pids(self, pids: list[int]) -> int:
        done = 0
        for pid in pids:
            hp = self._hidden.pop(pid, None)
            if not hp:
                continue
            self._restore_one(hp)
            done += 1
        if done:
            self._save_state()
            self.state_changed.emit()
            self.action_done.emit("restored", done)
        return done

    def _restore_one(self, hp: HiddenProcess) -> None:
        main_hwnd = 0
        main_size = -1
        for hwnd, rec in hp.records.items():
            self._retry.pop(hwnd, None)
            if not win32.IsWindow(hwnd):
                continue
            win32.restore_window(hwnd, rec.ex_style, rec.show_cmd)
            size = win32.get_window_rect(hwnd)
            area = max(0, size[2] - size[0]) * max(0, size[3] - size[1])
            if area > main_size:
                main_size, main_hwnd = area, hwnd
        if hp.muted:
            # 音频会话可能被系统临时回收后再重建(播音循环重启时尤其明显),
            # 瞬间取消静音会影响 0 个会话 —— 重试几次,否则用户恢复窗口后
            # 应用一直处于静音状态(实测踩坑)。
            for _ in range(5):
                if set_process_mute(hp.pid, False) > 0:
                    break
                time.sleep(0.2)
        request_tray_icon_restore(hp.pid)
        if main_hwnd and self._settings.activate_on_restore:
            win32.SetForegroundWindow(main_hwnd)

    def restore_hwnd(self, hwnd: int) -> bool:
        """恢复单个窗口(把它所属的整个进程恢复)。"""
        pid = win32.get_window_pid(hwnd)
        if pid in self._hidden:
            self.restore_pids([pid])
            return True
        return False

    # ------------------------------------------------------------ 切换
    def toggle(self, hwnds: list[int]) -> str:
        """切换给定窗口的隐藏状态,返回 'hidden'/'restored'/'none'。"""
        if not hwnds:
            return "none"
        any_hidden = any(self.is_hwnd_hidden(h) for h in hwnds)
        if any_hidden:
            pids = {win32.get_window_pid(h) for h in hwnds if self.is_hwnd_hidden(h)}
            self.restore_pids([p for p in pids if p])
            return "restored"
        n = self.hide_hwnds(hwnds)
        return "hidden" if n else "none"

    # ------------------------------------------------------------ 持续隐藏
    def _watch(self) -> None:
        if not self._hidden:
            return
        dirty = False
        for pid in list(self._hidden.keys()):
            hp = self._hidden[pid]
            if not win32.process_is_alive(pid):
                del self._hidden[pid]
                dirty = True
                continue
            if not self._settings.keep_hidden:
                continue
            for w in win32.enum_process_windows(pid):
                # 只在窗口"又回到了屏幕上"时补刀。最小化/隐藏中的窗口是
                # 我们想要的状态,别每秒重刷一遍(全屏游戏会因此反复被
                # 最小化,画面闪)。
                if not w.visible or w.minimized:
                    continue
                tries = self._retry.get(w.hwnd, 0)
                if tries >= 3:
                    continue  # 藏不掉的窗口(如管理员权限)别无限重试
                fullscreen = win32.is_fullscreen_window(w.hwnd)
                ex_style, show_cmd = win32.hide_window(w.hwnd)
                if win32.is_offscreen(w.hwnd):
                    self._retry.pop(w.hwnd, None)
                else:
                    self._retry[w.hwnd] = tries + 1
                if w.hwnd in hp.records:
                    # 应用自己又把窗口显示出来了,重新隐藏
                    hp.records[w.hwnd].ex_style = ex_style
                    hp.records[w.hwnd].show_cmd = show_cmd
                    hp.records[w.hwnd].fullscreen = fullscreen
                else:
                    hp.records[w.hwnd] = WindowRecord(w.hwnd, ex_style, show_cmd,
                                                      w.title, fullscreen)
                dirty = True
        if dirty:
            self._save_state()
            self.state_changed.emit()
