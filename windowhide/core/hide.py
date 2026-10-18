"""hide.py - 隐藏/恢复控制器。

隐藏 = 窗口从任务栏和 Alt+Tab 消失(SW_HIDE + WS_EX_TOOLWINDOW),
      程序继续在后台运行;同时尽力移除其托盘图标。
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

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings          # Config 对象(读 mute/keep_hidden 等)
        self._hidden: dict[int, HiddenProcess] = {}
        self._last_fg_pid = 0              # 最近一次「隐藏前台窗口」藏掉的进程
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
                          "show_cmd": r.show_cmd, "title": r.title}
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
                if win32.IsWindowVisible(hwnd):
                    continue          # 已经重新显示了,不是我们隐藏的状态
                if not (win32.get_window_styles(hwnd)[1] & win32.WS_EX_TOOLWINDOW):
                    continue          # 没有我们加的工具窗样式
                hp.records[hwnd] = WindowRecord(
                    hwnd, int(rec.get("ex_style", 0)),
                    int(rec.get("show_cmd", 9)), str(rec.get("title", "")))
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
        for pid in dict.fromkeys(pids):
            if pid in self._hidden:
                continue
            hp = self._hide_one(pid)
            if hp and hp.window_count > 0:
                self._hidden[pid] = hp
                done += 1
        if done:
            self._save_state()
            self.state_changed.emit()
            self.action_done.emit("hidden", done)
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

        防自藏:在主界面点「隐藏前台窗口」按钮的瞬间,前台窗口就是
        本工具自己 —— 不拦这一下的话,工具会把自个儿藏掉,界面直接
        消失,用户完全不知道去哪恢复(用户实测踩坑)。
        """
        hwnd = win32.GetForegroundWindow()
        if hwnd:
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
            ex_style, show_cmd = win32.hide_window(w.hwnd)
            records[w.hwnd] = WindowRecord(w.hwnd, ex_style, show_cmd, w.title)
            if win32.IsWindowVisible(w.hwnd):
                failed += 1  # 隐藏后仍然可见 = 失败(通常是权限不足)
        if not records:
            return None

        hp = HiddenProcess(pid=pid, process_name=name, records=records, failed=failed)
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
                if not w.visible and not w.minimized:
                    continue
                if w.hwnd in hp.records:
                    # 应用自己又把窗口显示出来了,重新隐藏
                    ex_style, show_cmd = win32.hide_window(w.hwnd)
                    hp.records[w.hwnd].ex_style = ex_style
                    hp.records[w.hwnd].show_cmd = show_cmd
                else:
                    ex_style, show_cmd = win32.hide_window(w.hwnd)
                    hp.records[w.hwnd] = WindowRecord(w.hwnd, ex_style, show_cmd, w.title)
                dirty = True
        if dirty:
            self._save_state()
            self.state_changed.emit()
