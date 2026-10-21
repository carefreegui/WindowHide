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

from . import protect
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
    profile: str = ""   # 由哪个场景藏的("" = 手动勾选)

    @property
    def window_count(self) -> int:
        return len(self.records)


class HideController(QObject):
    """管理"哪些进程被隐藏了"以及隐藏/恢复动作。"""

    state_changed = Signal()          # 隐藏状态变化(UI 刷新用)
    action_done = Signal(str, int)    # (action, count) action: hidden/restored/failed
    hide_incomplete = Signal(int, str)  # (藏不掉的窗口数, 原因提示)
    privilege_blocked = Signal(str)   # 目标进程权限比我们高(UIPI 拦死)
    protected_skipped = Signal(str)   # 命中了"永不隐藏"名单,已跳过
    profile_changed = Signal(str)     # 当前场景变了(托盘菜单要更新勾选)

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
        self._queued: tuple | None = None  # 忙时排队的最后一次请求
        # 用户手动恢复过的进程:自动应用规则时不要马上又把它藏回去
        # ("点恢复 → 3 秒后又没了"是我最讨厌的那种 bug)
        self._opt_out: set[int] = set()
        self._rule_tick = 0
        # 自动应用规则时"藏不掉"的进程:重试 3 次就放弃,别每 3 秒撞一次墙
        self._rule_fail: dict[int, int] = {}
        # 在途意图:pid -> "hide" / "restore"。
        # 异步隐藏有个几毫秒的真空期:窗口已经不可见了,但 _hidden 还没写进
        # 去 —— 这期间用户再点一下,旧逻辑会判"还没藏",于是又藏一次(表现为
        # "点了恢复没反应")。用这个表把"意图"也算进状态判断。
        self._pending: dict[int, str] = {}
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
                 "profile": hp.profile,
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
                               muted=bool(entry.get("muted", False)),
                               profile=str(entry.get("profile", "")))
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

    def is_pid_hidden_or_pending(self, pid: int) -> bool:
        """已隐藏 **或** 正在隐藏 —— 用来判"这次点击该藏还是该放"。

        只看 `_hidden` 会漏掉异步隐藏落地前的那几毫秒,用户连点两下就会
        变成"藏两次",而"恢复"根本没被执行。
        """
        with self._lock:
            return pid in self._hidden or self._pending.get(pid) == "hide"

    def hidden_summary(self) -> tuple[int, int]:
        """(隐藏的进程数, 隐藏的窗口数)"""
        with self._lock:
            return (len(self._hidden),
                    sum(hp.window_count for hp in self._hidden.values()))

    # ------------------------------------------------------------ 工作线程
    def _submit(self, fn, *args) -> bool:
        """把跨进程动作丢到工作线程去做,主线程立刻返回。

        同一时刻只跑一个后台任务(两次隐藏叠在一起会互相踩状态)。
        但**不丢请求**:忙的时候记住最后一次,忙完接着做。
        以前是直接丢掉,表现就是"点了恢复毫无反应" —— 隐藏一个大程序
        要几百毫秒,用户很容易在这个窗口里点第二下。
        """
        with self._busy_lock:
            if self._busy:
                self._queued = (fn, args)
                from ..log import write
                write("上一个隐藏/恢复还没结束,本次请求已排队")
                return True
            self._busy = True
        self._start(fn, args)
        return True

    def _start(self, fn, args) -> None:
        def run():
            try:
                fn(*args)
            except Exception:
                from ..log import write
                write("后台隐藏/恢复出现异常:\n" + traceback.format_exc())
            finally:
                with self._busy_lock:
                    self._busy = False
                    nxt = self._queued
                    self._queued = None
                # 收尾刷新一次:即便中途失败,界面也该反映真实状态
                self.state_changed.emit()
                if nxt is not None:
                    self._start(nxt[0], nxt[1])

        threading.Thread(target=run, name="hide-worker", daemon=True).start()

    def _wait_idle(self, timeout: float = 5.0) -> None:
        """等后台任务做完(同步调用前先用,避免和正在跑的隐藏/恢复打架)。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._busy_lock:
                if not self._busy:
                    self._queued = None      # 自己要做的事优先,排队的不做了
                    return
            time.sleep(0.02)

    # ------------------------------------------------------------ 隐藏
    def hide_pids(self, pids: list[int], wait: bool = False,
                  mute: bool | None = None, profile: str = "") -> int:
        """隐藏指定进程的所有可见顶层窗口。

        `wait=False`(默认)时丢到工作线程去做、立即返回 0 —— 界面调用一律
        用这个,主线程绝不碰跨进程窗口调用(目标卡住会把 UI 一起冻死)。
        `wait=True` 是同步版本,给测试和"退出前必须做完"的路径用。
        `mute=True` 用于场景自带的"顺带静音";None 表示只看全局设置。
        `profile` 记录"是哪个场景藏的",切换场景时要据此回收。
        """
        with self._lock:
            # 用户主动藏 = 撤销"别再自动藏我"。必须在这里做,不能放在
            # 工作线程收尾里 —— 否则"自动藏→用户点恢复"的交错会把恢复请求
            # 擦掉,窗口 3 秒后又被藏回去(实测踩过)。
            self._opt_out.difference_update(pids)
            for pid in pids:
                self._pending[pid] = "hide"
        if wait:
            self._wait_idle()
            return self._hide_pids_blocking(pids, mute=mute, profile=profile)
        self._submit(self._hide_pids_blocking, list(pids), mute, profile)
        return 0

    def _hide_pids_blocking(self, pids: list[int], mute: bool | None = None,
                            profile: str = "", quiet: bool = False) -> int:
        done = 0
        failed = 0
        failed_names: list[str] = []
        blocked_names: list[str] = []
        hung_names: list[str] = []
        protected_names: list[str] = []
        extra = self._settings.protected_processes
        for pid in dict.fromkeys(pids):
            with self._lock:
                if pid in self._hidden:
                    self._pending.pop(pid, None)
                    continue
                if self._pending.get(pid) == "restore":
                    # 藏的过程中用户又点了恢复:别藏了,让恢复请求去做它的事
                    self._pending.pop(pid, None)
                    continue
            # 权限不够就别动 —— UIPI 会把窗口操作静默吃掉,只留下一个
            # "最小化了但点不回来"的烂摊子(永劫无间实测事故)。
            name = win32.process_image_path(pid).rsplit("\\", 1)[-1] or f"pid {pid}"
            if protect.is_protected(name, extra):
                protected_names.append(f"{name}({protect.reason(name, extra)})")
                continue
            if win32.process_is_higher_integrity(pid):
                blocked_names.append(name)
                continue
            # 单个进程出任何意外都不能拖垮整批(以前一个异常就让剩下
            # 勾选的窗口一个都不藏)
            try:
                hp = self._hide_one(pid, mute=mute, profile=profile)
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
                    if self._pending.get(pid) == "hide":
                        self._pending.pop(pid, None)
                done += 1
                if hp.failed:
                    failed += hp.failed
                    failed_names.append(hp.process_name or name)
        if done:
            self._save_state()
            self.state_changed.emit()
            self.action_done.emit("hidden", done)
        if quiet:
            # 自动应用规则时憋住气泡:每 3 秒弹一次"没藏掉"比不藏还烦。
            # 只写日志,让愿意排查的人查得到。
            if protected_names or blocked_names or hung_names or failed:
                from ..log import write
                write("自动应用规则时跳过:"
                      + " ".join(filter(None, [
                          ("受保护:" + "、".join(protected_names)) if protected_names else "",
                          ("权限不足:" + "、".join(blocked_names)) if blocked_names else "",
                          ("无响应:" + "、".join(hung_names)) if hung_names else "",
                          ("藏不掉:" + "、".join(failed_names)) if failed else "",
                      ])))
            return done
        if protected_names:
            names = "、".join(dict.fromkeys(protected_names))
            self.protected_skipped.emit(
                f"{names} 属于系统/输入关键进程,已跳过 —— 藏掉它可能导致"
                "打不出字、桌面异常。可在设置里调整自定义名单。")
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

    def hide_hwnds(self, hwnds: list[int], wait: bool = False,
                   mute: bool | None = None, profile: str = "") -> int:
        """隐藏窗口列表(按进程归组)。`wait=True` 供测试同步断言。"""
        pids: list[int] = []
        for hwnd in hwnds:
            if win32.IsWindow(hwnd):
                pids.append(win32.get_window_pid(hwnd))
        return self.hide_pids(pids, wait=wait, mute=mute, profile=profile)

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

    def _hide_one(self, pid: int, mute: bool | None = None,
                  profile: str = "") -> HiddenProcess | None:
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
            # 必须等一小会儿:异步 SW_HIDE 刚投递时窗口还是可见的。
            if not win32.wait_offscreen(w.hwnd, 0.3):
                failed += 1
        if not records:
            return HiddenProcess(pid=pid, process_name=name, hung=hung) \
                if hung else None
        if not name:
            # 调用方(如测试/命令行)不一定填过 process_name,报警文案要能
            # 指名道姓,不然用户只看到一个 pid
            name = win32.process_image_path(pid).rsplit("\\", 1)[-1]

        hp = HiddenProcess(pid=pid, process_name=name, records=records,
                           failed=failed, hung=hung, profile=profile)
        if failed:
            from ..log import write
            write(f"未能隐藏的窗口:{name}(pid={pid}) 失败 {failed}/{len(records)} 个")
        # 托盘图标
        delete_process_tray_icons(pid)
        # 静音(场景自带的静音优先,否则看全局设置)
        if mute or self._settings.mute_on_hide:
            n = set_process_mute(pid, True)
            hp.muted = n > 0
        return hp

    # ------------------------------------------------------------ 恢复
    def restore_all(self, wait: bool = False) -> int:
        with self._lock:
            pids = list(self._hidden.keys())
        return self.restore_pids(pids, wait=wait)

    def restore_pids(self, pids: list[int], wait: bool = False) -> int:
        # 手动恢复 = 用户明确表示"这个不要再藏了" —— 记下来,免得
        # 「规则持续生效」三秒后又把它藏回去
        with self._lock:
            self._opt_out.update(pids)
            for pid in pids:
                self._pending[pid] = "restore"
        if wait:
            self._wait_idle()
            return self._restore_pids_blocking(pids)
        self._submit(self._restore_pids_blocking, list(pids))
        return 0

    def _restore_pids_blocking(self, pids: list[int]) -> int:
        done = 0
        for pid in pids:
            with self._lock:
                hp = self._hidden.pop(pid, None)
                if self._pending.get(pid) == "restore":
                    self._pending.pop(pid, None)
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
        pids = {win32.get_window_pid(h) for h in hwnds}
        pids.discard(0)
        hidden_pids = {p for p in pids if self.is_pid_hidden_or_pending(p)}
        if hidden_pids:
            self.restore_pids(list(hidden_pids))
            return "restored"
        self.hide_hwnds(hwnds)
        return "hidden"

    # ------------------------------------------------------------ 场景
    def activate_profile(self, name: str, wait: bool = False) -> bool:
        """切换到某个场景:藏该场景命中的窗口,放掉别的场景藏的窗口。

        语义写清楚,免得用户以为"点了一下没反应":
        - **藏**:当前可见、且命中该场景规则的窗口(跳过本工具、桌面/任务栏、
          受保护进程、无响应窗口);
        - **放**:之前被**另一个场景**藏起来的进程 —— 换场景就该换一批;
          手动勾选藏的不动(那是用户自己的选择);
        - 没有命中任何窗口时会如实提示,不会假装切过去了。
        """
        profile = self._settings.profile(name)
        if profile is None:
            return False
        self._settings.set_active(profile.name)

        def run():
            self._apply_profile(profile)
            # 场景是用户点出来的,把结果落盘(下次启动还是这个场景)
            try:
                self._settings.save()
            except Exception:
                pass
            self.profile_changed.emit(profile.name)

        if wait:
            self._wait_idle()
            run()
            return True
        return self._submit(run)

    def _apply_profile(self, profile) -> None:
        # 先给在途的异步显示/隐藏一点时间落地。切场景常常发生在"刚点过恢复"
        # 之后,而恢复走的是 ShowWindowAsync/SetWindowPlacement —— 目标线程
        # 还没处理完的时候枚举,会看到窗口"还没显示"从而漏掉它。
        # 这里跑在工作线程,200ms 不影响界面。
        time.sleep(0.2)
        # 1) 放掉其它场景藏的(只动场景藏的,手动的不碰)
        with self._lock:
            release = [hp.pid for hp in self._hidden.values()
                       if hp.profile and hp.profile != profile.name]
        if release:
            self._restore_pids_blocking(release)

        # 2) 藏本场景命中的
        target_pids = self._profile_targets(profile)
        if not target_pids:
            # 刚放出来的窗口可能还没真正"可见"(恢复也是异步落地的),
            # 这时候扫一遍会是空的。等一小会儿再扫,最多约 1 秒 ——
            # 否则用户"恢复完立刻切场景"就会漏掉窗口,以为场景坏了。
            for _ in range(5):
                time.sleep(0.2)
                target_pids = self._profile_targets(profile)
                if target_pids:
                    break
        if not target_pids:
            self.action_done.emit("profile_empty", 0)
            return
        self._hide_pids_blocking(target_pids, mute=profile.mute,
                                 profile=profile.name)

    def _profile_targets(self, profile) -> list[int]:
        """当前可见、命中该场景规则、且不该被跳过的进程。"""
        extra = self._settings.protected_processes
        our_pid = os.getpid()
        with self._lock:
            already = set(self._hidden.keys())
        out: list[int] = []
        for w in win32.enum_top_level_windows():
            # 注意:这里**不看** opt_out —— 用户主动点了场景,就该按场景办;
            # opt_out 只用来约束"自动应用规则"那种后台行为。
            if not w.visible or w.pid in already:
                continue
            if w.pid == our_pid or win32.is_shell_window(w.hwnd):
                continue
            if protect.is_protected(w.process_name, extra):
                continue
            if any(rule.matches(w) for rule in profile.rules):
                out.append(w.pid)
        return out

    # ------------------------------------------------------------ 持续隐藏
    def _watch(self) -> None:
        """看门狗(主线程每 1 秒跑一次)。

        这里只做"读"和"判断",真正的窗口操作丢给工作线程 —— 主线程
        绝不允许出现任何可能阻塞的跨进程调用。
        """
        self._rule_tick += 1
        if self._rule_tick % 3 == 0:
            self._watch_rules()
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

    def _watch_rules(self) -> None:
        """「规则持续生效」:目标程序重启后,按当前场景再自动藏一次。

        默认关闭(设置里叫「规则持续生效」)。以前规则只在启动时匹配一次,
        目标一重启就永远不管了 —— 那就不是"生效",只是"记住"。
        """
        if not self._settings.auto_apply_rules:
            return
        profile = self._settings.active()
        if profile is None or not profile.rules:
            return
        # 清掉已经退出的 opt_out:pid 会被系统回收,留着会误放新进程一马
        with self._lock:
            self._opt_out = {p for p in self._opt_out
                             if win32.process_is_alive(p)}
            self._rule_fail = {p: n for p, n in self._rule_fail.items()
                               if win32.process_is_alive(p)}
        self._submit(self._apply_rules_worker, profile.name)

    def _apply_rules_worker(self, profile_name: str) -> None:
        """工作线程:按场景规则找新出现的窗口并隐藏。"""
        profile = self._settings.profile(profile_name)
        if profile is None or not profile.rules:
            return
        extra = self._settings.protected_processes
        our_pid = os.getpid()
        with self._lock:
            already = set(self._hidden.keys())
            opt_out = set(self._opt_out)
            hopeless = {p for p, n in self._rule_fail.items() if n >= 3}
        target_pids: list[int] = []
        for w in win32.enum_top_level_windows():
            if not w.visible or w.minimized:
                continue
            if w.pid == our_pid or w.pid in already \
                    or w.pid in opt_out or w.pid in hopeless:
                continue
            if win32.is_shell_window(w.hwnd):
                continue
            if protect.is_protected(w.process_name, extra):
                continue
            if any(rule.matches(w) for rule in profile.rules):
                target_pids.append(w.pid)
        if not target_pids:
            return
        before = self.hidden_summary()[0]
        self._hide_pids_blocking(target_pids, mute=profile.mute,
                                 profile=profile.name, quiet=True)
        after = self.hidden_summary()[0]
        # 藏不掉的别每 3 秒重试一遍(白耗 CPU,日志也会淹)
        with self._lock:
            for pid in target_pids:
                if pid in self._hidden:
                    continue
                self._rule_fail[pid] = self._rule_fail.get(pid, 0) + 1
        if after > before:
            from ..log import write
            write(f"规则持续生效:按场景「{profile.name}」自动隐藏 "
                  f"{after - before} 个程序")

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
            if win32.wait_offscreen(hwnd, 0.3):
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
