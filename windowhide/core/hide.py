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
import threading
import time
import traceback
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
    hung: bool = False  # 目标已无响应,整个进程都没碰

    @property
    def window_count(self) -> int:
        return len(self.records)


class HideController(QObject):
    """管理"哪些进程被隐藏了"以及隐藏/恢复动作。"""

    state_changed = Signal()          # 隐藏状态变化(UI 刷新用)
    action_done = Signal(str, int)    # (action, count) action: hidden/restored/failed
    hide_incomplete = Signal(int, str)  # (藏不掉的窗口数, 原因提示)
    privilege_blocked = Signal(str)   # 目标进程权限比我们高(UIPI 拦死)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings          # Config 对象(读 mute/keep_hidden 等)
        self._hidden: dict[int, HiddenProcess] = {}
        self._last_fg_pid = 0              # 最近一次「隐藏前台窗口」藏掉的进程
        self._retry: dict[int, int] = {}   # hwnd -> 已重试次数(藏不掉的窗口别无限刷)
        # 隐藏/恢复是跨进程操作,可能卡在任何一次窗口调用上 —— 必须在
        # 工作线程里做,主线程只管画界面(用户实测:一次隐藏把整个 UI
        # 冻成"未响应",最后只能强杀进程)。
        self._lock = threading.RLock()     # 保护 _hidden / _retry
        self._busy_lock = threading.Lock()
        self._busy = False                 # 有任务在跑,别叠加
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._watch)
        self._timer.start()
        self._load_state()

    # ------------------------------------------------------------ 状态持久化
    def _save_state(self) -> None:
        with self._lock:
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
        with self._lock:
            return list(self._hidden.keys())

    def hidden_hwnds(self) -> set[int]:
        out: set[int] = set()
        with self._lock:
            for hp in self._hidden.values():
                out.update(hp.records.keys())
        return out

    def is_pid_hidden(self, pid: int) -> bool:
        with self._lock:
            return pid in self._hidden

    def is_hwnd_hidden(self, hwnd: int) -> bool:
        with self._lock:
            return any(hwnd in hp.records for hp in self._hidden.values())

    def hidden_summary(self) -> tuple[int, int]:
        """(隐藏的进程数, 隐藏的窗口数)"""
        with self._lock:
            return (len(self._hidden),
                    sum(hp.window_count for hp in self._hidden.values()))

    # ------------------------------------------------------------ 工作线程
    def _submit(self, fn, *args) -> bool:
        """把跨进程动作丢到工作线程去做,主线程立刻返回。

        同一时刻只允许一个后台任务:两次隐藏叠在一起会互相踩状态,
        而且用户连点按钮时第二次本来也没意义。
        """
        with self._busy_lock:
            if self._busy:
                from ..log import write
                write("上一次隐藏/恢复还没结束,本次请求已忽略")
                return False
            self._busy = True

        def run():
            try:
                fn(*args)
            except Exception:
                from ..log import write
                write("后台隐藏/恢复出现异常:\n" + traceback.format_exc())
            finally:
                with self._busy_lock:
                    self._busy = False
                # 收尾刷新一次:即便中途失败,界面也该反映真实状态
                self.state_changed.emit()

        threading.Thread(target=run, name="hide-worker", daemon=True).start()
        return True

    # ------------------------------------------------------------ 隐藏
    def hide_pids(self, pids: list[int], wait: bool = False) -> int:
        """隐藏指定进程的所有可见顶层窗口。

        `wait=False`(默认)时丢到工作线程去做、立即返回 0 —— 界面调用一律
        用这个,主线程绝不碰跨进程窗口调用(目标卡住会把 UI 一起冻死)。
        `wait=True` 是同步版本,给测试和"退出前必须做完"的路径用。
        """
        if wait:
            return self._hide_pids_blocking(pids)
        self._submit(self._hide_pids_blocking, list(pids))
        return 0

    def _hide_pids_blocking(self, pids: list[int]) -> int:
        done = 0
        failed = 0
        failed_names: list[str] = []
        blocked_names: list[str] = []
        hung_names: list[str] = []
        for pid in dict.fromkeys(pids):
            with self._lock:
                if pid in self._hidden:
                    continue
            # 权限不够就别动 —— UIPI 会把窗口操作静默吃掉,只留下一个
            # "最小化了但点不回来"的烂摊子(永劫无间实测事故)。
            name = win32.process_image_path(pid).rsplit("\\", 1)[-1] or f"pid {pid}"
            if win32.process_is_higher_integrity(pid):
                blocked_names.append(name)
                continue
            # 单个进程出任何意外都不能拖垮整批(以前一个异常就让剩下
            # 勾选的窗口一个都不藏)
            try:
                hp = self._hide_one(pid)
            except Exception:
                from ..log import write
                write(f"隐藏 {name} 时出现异常:\n{traceback.format_exc()}")
                failed += 1
                failed_names.append(name)
                continue
            if hp and hp.hung:
                # 一个窗口都没碰(或者是部分窗口没碰) —— 单独报,别混进
                # "藏不掉",因为原因和处置方式完全不同
                hung_names.append(hp.process_name or name)
                if hp.window_count == 0:
                    continue
            if hp and hp.window_count > 0:
                with self._lock:
                    self._hidden[pid] = hp
                done += 1
                if hp.failed:
                    failed += hp.failed
                    failed_names.append(hp.process_name or name)
        if done:
            self._save_state()
            self.state_changed.emit()
            self.action_done.emit("hidden", done)
        if blocked_names:
            names = "、".join(dict.fromkeys(blocked_names))
            self.privilege_blocked.emit(
                f"{names} 以管理员权限运行,本工具没有权限操作它。"
                "请用托盘菜单的「以管理员身份重启」后再隐藏。")
        if hung_names:
            names = "、".join(dict.fromkeys(hung_names))
            self.hide_incomplete.emit(
                len(hung_names), f"{names} 已经无响应(未响应状态),没有动它 —— "
                                  "对无响应窗口做窗口操作会把本工具一起卡死。"
                                  "请先让它恢复响应,或直接结束该进程。")
        if failed:
            # 静默失败最坑:用户以为藏好了,其实窗口还在屏幕上。
            names = "、".join(dict.fromkeys(failed_names))
            self.hide_incomplete.emit(
                failed, f"{names} 有 {failed} 个窗口没藏掉(已还原到原样),"
                        "通常是它以独占全屏渲染,不接受隐藏指令。")
        return done

    def hide_hwnds(self, hwnds: list[int], wait: bool = False) -> int:
        """隐藏窗口列表(按进程归组)。`wait=True` 供测试同步断言。"""
        pids: list[int] = []
        for hwnd in hwnds:
            if win32.IsWindow(hwnd):
                pids.append(win32.get_window_pid(hwnd))
        return self.hide_pids(pids, wait=wait)

    def hide_foreground(self, wait: bool = False) -> int:
        """隐藏当前前台窗口所属的进程。

        两道保护(用户实测"点一下连桌面都藏了"):
        1. **桌面/任务栏不藏** —— 从托盘菜单触发时,GetForegroundWindow
           拿到的是 Progman/WorkerW/Shell_TrayWnd(全是 explorer 的),
           不拦这一下就会把桌面、任务栏连同所有资源管理器窗口一起藏掉;
        2. **本工具自己不藏** —— 在主界面点按钮的瞬间,前台就是工具自己。

        注意:本方法目前没有 UI 入口(主界面按钮/托盘菜单/快捷键都已
        按用户反馈改为"工具自藏到托盘"),保留为 core 能力备用。
        `wait=True` 供测试同步断言用(异步模式下返回值代表"已提交")。
        """
        hwnd = win32.GetForegroundWindow()
        if hwnd:
            if win32.is_shell_window(hwnd):
                return 0
            pid = win32.get_window_pid(hwnd)
            if pid == os.getpid():
                return 0
            n = self.hide_hwnds([hwnd], wait=wait)
            if n or not wait:
                # 异步时 n 恒为 0,提交即算"这次藏的就是它"
                self._last_fg_pid = pid
            return n if wait else (1 if self._last_fg_pid == pid else 0)
        return 0

    def restore_last_foreground(self, wait: bool = False) -> bool:
        """恢复最近一次「隐藏前台窗口」藏掉的进程。没有则返回 False。"""
        pid = self._last_fg_pid
        if pid and pid in self._hidden:
            self._last_fg_pid = 0
            self.restore_pids([pid], wait=wait)
            return True
        return False

    def _hide_one(self, pid: int) -> HiddenProcess | None:
        name = ""
        records: dict[int, WindowRecord] = {}
        failed = 0
        hung = False
        for w in win32.enum_process_windows(pid):
            if not w.visible and not w.minimized:
                continue  # 本来就隐藏的窗口不动,恢复时也不会把它弹出来
            name = name or w.process_name
            if win32.is_hung(w.hwnd):
                # 未响应的窗口一个都别碰:任何同步窗口调用都会连带把
                # 本工具卡死(实测"界面未响应,只能强杀进程")。
                hung = True
                continue
            fullscreen = win32.is_fullscreen_window(w.hwnd)
            ex_style, show_cmd = win32.hide_window(
                w.hwnd, allow_minimize=self._settings.force_fullscreen_hide)
            records[w.hwnd] = WindowRecord(w.hwnd, ex_style, show_cmd, w.title,
                                           fullscreen)
            # 判据是"真的不在屏幕上了"(隐藏或最小化),不是 IsWindowVisible ——
            # 全屏游戏被最小化时 IsWindowVisible 依然是 True。
            if not win32.is_offscreen(w.hwnd):
                failed += 1
        if not records:
            return HiddenProcess(pid=pid, process_name=name, hung=hung) \
                if hung else None
        if not name:
            # 调用方(如测试/命令行)不一定填过 process_name,报警文案要能
            # 指名道姓,不然用户只看到一个 pid
            name = win32.process_image_path(pid).rsplit("\\", 1)[-1]

        hp = HiddenProcess(pid=pid, process_name=name, records=records,
                           failed=failed, hung=hung)
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
    def restore_all(self, wait: bool = False) -> int:
        with self._lock:
            pids = list(self._hidden.keys())
        return self.restore_pids(pids, wait=wait)

    def restore_pids(self, pids: list[int], wait: bool = False) -> int:
        if wait:
            return self._restore_pids_blocking(pids)
        self._submit(self._restore_pids_blocking, list(pids))
        return 0

    def _restore_pids_blocking(self, pids: list[int]) -> int:
        done = 0
        for pid in pids:
            with self._lock:
                hp = self._hidden.pop(pid, None)
            if not hp:
                continue
            try:
                self._restore_one(hp)
            except Exception:
                from ..log import write
                write(f"恢复 pid={pid} 时出现异常:\n{traceback.format_exc()}")
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
            # 全屏游戏光 SetForegroundWindow 常常抢不到前台(前台锁),
            # 必须走 AttachThreadInput 那条路,否则游戏回来了但停在后台
            win32.force_foreground(main_hwnd)

    def restore_hwnd(self, hwnd: int) -> bool:
        """恢复单个窗口(把它所属的整个进程恢复)。"""
        pid = win32.get_window_pid(hwnd)
        if pid in self._hidden:
            self.restore_pids([pid])
            return True
        return False

    # ------------------------------------------------------------ 切换
    def toggle(self, hwnds: list[int]) -> str:
        """切换给定窗口的隐藏状态,返回**意图**:'hidden'/'restored'/'none'。

        动作是异步的,所以这里返回的是"我要隐藏/恢复",不保证已完成 ——
        调用方(托盘切换)只需要知道方向,界面靠 state_changed 刷新。
        """
        if not hwnds:
            return "none"
        any_hidden = any(self.is_hwnd_hidden(h) for h in hwnds)
        if any_hidden:
            pids = {win32.get_window_pid(h) for h in hwnds if self.is_hwnd_hidden(h)}
            self.restore_pids([p for p in pids if p])
            return "restored"
        self.hide_hwnds(hwnds)
        return "hidden"

    # ------------------------------------------------------------ 持续隐藏
    def _watch(self) -> None:
        """看门狗(主线程每 1 秒跑一次)。

        这里只做"读"和"判断",真正的窗口操作丢给工作线程 —— 主线程
        绝不允许出现任何可能阻塞的跨进程调用。
        """
        if not self._hidden:
            return
        dirty = False
        targets: list[int] = []
        with self._lock:
            for pid in list(self._hidden.keys()):
                hp = self._hidden[pid]
                if not win32.process_is_alive(pid):
                    del self._hidden[pid]
                    dirty = True
                    continue
                if not self._settings.keep_hidden:
                    continue
                for w in win32.enum_process_windows(pid):
                    # 只在窗口"又回到了屏幕上"时补刀。最小化/隐藏中的窗口
                    # 是我们想要的状态,别每秒重刷一遍。
                    if not w.visible or w.minimized:
                        continue
                    if self._retry.get(w.hwnd, 0) >= 3:
                        continue  # 藏不掉的窗口别无限重试
                    if win32.is_hung(w.hwnd):
                        continue  # 未响应的一律不碰
                    targets.append(w.hwnd)
        if dirty:
            self._save_state()
            self.state_changed.emit()
        if targets:
            self._submit(self._rehide, targets)

    def _rehide(self, hwnds: list[int]) -> None:
        """补刀:目标程序自己又把窗口弹出来了,再藏一次(工作线程里跑)。"""
        for hwnd in hwnds:
            if not win32.IsWindow(hwnd) or win32.is_hung(hwnd):
                continue
            with self._lock:
                hp = next((h for h in self._hidden.values()
                           if hwnd in h.records), None)
            if hp is None:
                continue
            fullscreen = win32.is_fullscreen_window(hwnd)
            ex_style, show_cmd = win32.hide_window(
                hwnd, allow_minimize=self._settings.force_fullscreen_hide)
            if win32.is_offscreen(hwnd):
                self._retry.pop(hwnd, None)
            else:
                self._retry[hwnd] = self._retry.get(hwnd, 0) + 1
            with self._lock:
                rec = hp.records.get(hwnd)
                if rec is None:
                    hp.records[hwnd] = WindowRecord(hwnd, ex_style, show_cmd,
                                                    "", fullscreen)
                else:
                    rec.ex_style = ex_style
                    rec.show_cmd = show_cmd
                    rec.fullscreen = fullscreen
            self._save_state()
        self.state_changed.emit()
