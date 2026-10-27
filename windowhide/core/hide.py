"""hide.py - 隐藏/恢复控制器。

隐藏 = 窗口从任务栏和 Alt+Tab 消失(SW_HIDE + WS_EX_TOOLWINDOW),
      程序继续在后台运行;同时尽力移除其托盘图标。
      全屏窗口(游戏)走另一条路:先最小化让它掉出独占全屏,再 SW_HIDE
      —— 详见 win32.hide_plan。
恢复 = 还原样式与显示状态,取消静音,广播 TaskbarCreated 让托盘图标回来。

**隐藏的单位是窗口(HWND),不是进程**。Chromium 系浏览器多开时,几个
窗口往往同属一个进程 —— 早期实现按 pid 归组(`hide_hwnds` 折叠成 pid
再枚举整个进程),勾一个窗口会把同进程的所有窗口一起藏掉。现在:
- 手动勾选走 `hide_hwnds()`,只动勾中的那几个 HWND,兄弟窗口原样保留;
- 场景/规则走 `hide_pids()`,语义本来就是"整个程序",保持不变;
- 同一进程只藏了一部分时(`HiddenProcess.partial`)**不删托盘图标**(图标
  不像音量能自己恢复,半藏状态下删掉就成了"浏览器还在、图标没了");
  静音**照做** —— 用户要的是"藏了就别响",代价是同程序其它窗口一起没声,
  这一点在「声音」列和状态栏里说清楚。
- 要不要静音**只有一处判定**:`should_mute()`(声音列 > 场景三态 > 全局)。

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

from ..config import MUTE_ALWAYS, MUTE_INHERIT, MUTE_NEVER
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
    partial: bool = False  # 只藏了这个进程的一部分窗口(同程序多开)

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
        # 窗口级的在途意图:hwnd -> "hide" / "restore"。
        # `_pending` 是进程级的,同一进程多开时不够用 —— 藏了 A 再点 B,
        # 进程级会误判成"B 也藏过"。所以手动勾选/双击切换这条路单开一张表。
        self._pending_hwnd: dict[int, str] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._watch)
        self._timer.start()
        self._load_state()

    # ------------------------------------------------------------ 状态持久化
    def _save_state(self) -> None:
        with self._lock:
            data = {"version": 2, "hidden": [
                {"pid": hp.pid, "process_name": hp.process_name, "muted": hp.muted,
                 "profile": hp.profile, "partial": hp.partial,
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
                               profile=str(entry.get("profile", "")),
                               partial=bool(entry.get("partial", False)))
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

    def pending_hwnds(self) -> set[int]:
        """正在隐藏/恢复途中、还没落地的窗口。

        界面刷新要靠它兜一下:异步隐藏投递出去之后,窗口已经"不可见"了,
        但还没写进 `_hidden` —— 这一小段里枚举不到、隐藏记录里也没有,
        列表会把它当成"窗口消失了"直接从表里删掉,连带勾选状态一起丢
        (用户实测:"多按几次快捷键,勾选自己就没了")。
        """
        with self._lock:
            return set(self._pending_hwnd.keys())

    def is_pid_hidden(self, pid: int) -> bool:
        with self._lock:
            return pid in self._hidden

    def is_hwnd_hidden(self, hwnd: int) -> bool:
        with self._lock:
            return any(hwnd in hp.records for hp in self._hidden.values())

    def is_hwnd_hidden_or_pending(self, hwnd: int) -> bool:
        """这个窗口已隐藏 **或** 正在隐藏 —— 判"该藏还是该放"用。

        只看记录会漏掉异步落地前那几毫秒:连点两下会变成"藏两次",
        "恢复"根本没被执行(pid 版本的同一个坑,见 is_pid_hidden_or_pending)。
        """
        with self._lock:
            if any(hwnd in hp.records for hp in self._hidden.values()):
                return True
            return self._pending_hwnd.get(hwnd) == "hide"

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

    def partially_muted(self) -> list[str]:
        """"只藏了一部分窗口、却把整个程序静音了"的程序名。

        这种情况用户几乎一定会踩:他只是想藏一个浏览器窗口,结果同程序还
        开着的那几个也没声了。静音是整进程级的,没有别的做法,所以至少在
        状态栏说一句,告诉他在哪豁免。
        """
        with self._lock:
            names = [hp.process_name for hp in self._hidden.values()
                     if hp.partial and hp.muted]
        return list(dict.fromkeys(names))

    def mute_reason_for(self, process_name: str) -> tuple[bool, str]:
        """界面上问"这个程序现在藏了会不会静音"用。

        已经藏着的,按它当初被哪个场景藏来判(否则界面上显示的原因和实际
        听到的声音对不上);没藏的,按"手动勾选隐藏"算 —— 手动隐藏不带场景。
        """
        key = (process_name or "").strip().lower()
        if not key:
            return False, "没开静音"
        with self._lock:
            prof = next((hp.profile for hp in self._hidden.values()
                         if (hp.process_name or "").lower() == key), "")
        return self.mute_reason(process_name, prof)

    # ------------------------------------------------------------ 静音判定
    def should_mute(self, process_name: str, profile_name: str = "") -> bool:
        """隐藏这个程序时到底要不要静音 —— **全程序唯一判定入口**。

        以前这件事被写在三个地方(`_hide_one`、`set_audio_policy`、
        场景的 `profile.mute`),而且场景那个是跟全局 **OR** 起来的:
        用户关掉行为页的开关、切到场景照样被静音,看着就是"两个开关打架"。
        现在只有一个函数、一条明确的优先级,谁也别再各自算一遍。

        优先级(高 → 低):
          1. 主界面「声音」列勾了「藏了继续响」→ **不静音**(绝对豁免)
          2. 场景的静音模式:总是静音 / 从不静音
          3. 跟随全局(设置 → 行为 →「隐藏时同时静音目标程序」)
        """
        if self._settings.keeps_audio(process_name):
            return False
        prof = self._settings.profile(profile_name) if profile_name else None
        mode = getattr(prof, "mute_mode", MUTE_INHERIT) if prof else MUTE_INHERIT
        if mode == MUTE_ALWAYS:
            return True
        if mode == MUTE_NEVER:
            return False
        return bool(self._settings.mute_on_hide)

    def mute_reason(self, process_name: str,
                    profile_name: str = "") -> tuple[bool, str]:
        """(要不要静音, 为什么) —— 界面上直接把理由摆出来。

        三个开关各自都能改结果,只显示一个勾选框的话,用户永远看不出
        "我明明关掉了静音,为什么它还在静音"。原因文案就是为这个存在的。
        """
        if self._settings.keeps_audio(process_name):
            return False, "「声音」列勾了「藏了继续响」"
        prof = self._settings.profile(profile_name) if profile_name else None
        mode = getattr(prof, "mute_mode", MUTE_INHERIT) if prof else MUTE_INHERIT
        if mode == MUTE_ALWAYS:
            return True, f"场景「{prof.name}」设了「总是静音」"
        if mode == MUTE_NEVER:
            return False, f"场景「{prof.name}」设了「从不静音」"
        if self._settings.mute_on_hide:
            return True, "全局设了「隐藏时同时静音」"
        return False, "没开静音"

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
                  profile: str = "") -> int:
        """隐藏指定进程的所有可见顶层窗口。

        `wait=False`(默认)时丢到工作线程去做、立即返回 0 —— 界面调用一律
        用这个,主线程绝不碰跨进程窗口调用(目标卡住会把 UI 一起冻死)。
        `wait=True` 是同步版本,给测试和"退出前必须做完"的路径用。
        `profile` 记录"是哪个场景藏的":切换场景时要据此回收,同时也是
        **静音判定的输入之一**(见 should_mute)。
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
            return self._hide_pids_blocking(pids, profile=profile)
        self._submit(self._hide_pids_blocking, list(pids), profile)
        return 0

    def _hide_pids_blocking(self, pids: list[int], profile: str = "",
                            quiet: bool = False,
                            only: dict[int, list[int]] | None = None) -> int:
        """`only` 给定时只藏其中列出的窗口(手动勾选),否则藏整个进程。"""
        done = 0
        failed = 0
        failed_names: list[str] = []
        blocked_names: list[str] = []
        hung_names: list[str] = []
        protected_names: list[str] = []
        extra = self._settings.protected_processes
        for pid in dict.fromkeys(pids):
            want = None if only is None else list(only.get(pid) or ())
            with self._lock:
                existing = self._hidden.get(pid)
                if existing is not None:
                    if want is None:
                        if not existing.partial:
                            self._pending.pop(pid, None)
                            continue  # 整个进程已经藏过了
                        # 之前只藏了一部分(手动勾的),现在要整个藏起来:
                        # 继续往下走,按进程把剩下的窗口也藏了
                    else:
                        # 同一进程第二次藏(先藏窗口 A,再藏 B):只做新增的那些
                        want = [h for h in want if h not in existing.records]
                        if not want:
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
                hp = self._hide_one(pid, profile=profile, only=want)
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
                    cur = self._hidden.get(pid)
                    if cur is None:
                        self._hidden[pid] = hp
                    else:
                        # 合并:partial 以最新一次为准(藏满全部窗口后就该
                        # 认领托盘图标/静音这些整进程级动作了)
                        cur.records.update(hp.records)
                        cur.partial = hp.partial
                        cur.muted = cur.muted or hp.muted
                        cur.failed += hp.failed
                        cur.profile = cur.profile or hp.profile
                        cur.hung = cur.hung and hp.hung
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
                   profile: str = "") -> int:
        """隐藏**这几个窗口**——不看同一进程还有什么别的窗口。

        和 `hide_pids` 的唯一区别就在这里,也是"勾一个浏览器窗口、其它
        浏览器窗口跟着消失"那个 bug 的修复点:以前这里把 hwnd 列表折叠成
        pid 再交给 `hide_pids`,于是同进程的多开窗口被一锅端。Win32 的
        隐藏其实是窗口级的(每个 HWND 各有自己的 WS_VISIBLE / 扩展样式),
        实测同一个 Edge 进程里藏掉一个窗口,另一个照样正常显示、进程也不受影响。

        `wait=True` 供测试同步断言。
        """
        groups: dict[int, list[int]] = {}
        for hwnd in hwnds:
            try:
                hwnd = int(hwnd)
            except (TypeError, ValueError):
                continue
            if not win32.IsWindow(hwnd):
                continue
            pid = win32.get_window_pid(hwnd)
            if not pid:
                continue
            groups.setdefault(pid, []).append(hwnd)
        if not groups:
            return 0
        with self._lock:
            # 用户主动藏 = 撤销"别再自动藏我"(与 hide_pids 一致)
            self._opt_out.difference_update(groups.keys())
            for pid in groups:
                self._pending[pid] = "hide"
            for hs in groups.values():
                for hwnd in hs:
                    self._pending_hwnd[hwnd] = "hide"
        if wait:
            self._wait_idle()
            return self._hide_hwnds_worker(groups, profile)
        self._submit(self._hide_hwnds_worker, groups, profile)
        return 0

    def _hide_hwnds_worker(self, groups: dict[int, list[int]],
                           profile: str) -> int:
        try:
            return self._hide_pids_blocking(list(groups),
                                            profile=profile, only=groups)
        finally:
            with self._lock:
                for hs in groups.values():
                    for hwnd in hs:
                        if self._pending_hwnd.get(hwnd) == "hide":
                            self._pending_hwnd.pop(hwnd, None)

    def hide_foreground(self, wait: bool = False) -> int:
        """隐藏当前前台**窗口**(只这一个窗口,同程序的其它窗口不动)。

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

    def _hide_one(self, pid: int,
                  profile: str = "",
                  only: list[int] | None = None) -> HiddenProcess | None:
        """藏一个进程的窗口。`only` 给定时只藏这些 hwnd(逐个藏)。

        `only` 为 None = 老语义:这个进程所有在屏幕上的窗口都藏。
        `only` 为列表 = 只藏勾中的那几个,兄弟窗口一律不碰 —— 同程序多开
        (浏览器开好几个窗口)时用户要的就是这个。
        """
        name = ""
        records: dict[int, WindowRecord] = {}
        failed = 0
        hung = False
        # 先把候选和目标分清楚,再决定用哪条隐藏策略 ——
        # "先最小化再隐藏"是整进程级的动作(永劫无间最小化后引擎主线程
        # 死掉、窗口再也回不来),同进程还有窗口留在屏幕上时坚决不用。
        on_screen = [w for w in win32.enum_process_windows(pid)
                     if w.visible or w.minimized]
        if only is None:
            targets = on_screen
            others = 0
        else:
            wanted = set(only)
            targets = [w for w in on_screen if w.hwnd in wanted]
            others = sum(1 for w in on_screen
                         if w.hwnd not in wanted and w.visible and not w.minimized)
        partial = others > 0
        allow_minimize = self._settings.force_fullscreen_hide and not partial
        for w in targets:
            name = name or w.process_name
            if win32.is_hung(w.hwnd):
                # 未响应的窗口一个都别碰:任何同步窗口调用都会连带把
                # 本工具卡死(实测"界面未响应,只能强杀进程")。
                hung = True
                continue
            fullscreen = win32.is_fullscreen_window(w.hwnd)
            ex_style, show_cmd = win32.hide_window(w.hwnd,
                                                   allow_minimize=allow_minimize)
            records[w.hwnd] = WindowRecord(w.hwnd, ex_style, show_cmd, w.title,
                                           fullscreen)
            # 判据是"真的不在屏幕上了"(隐藏或最小化),不是 IsWindowVisible ——
            # 全屏游戏被最小化时 IsWindowVisible 依然是 True。
            # 必须等一小会儿:异步 SW_HIDE 刚投递时窗口还是可见的。
            if not win32.wait_offscreen(w.hwnd, 0.3):
                failed += 1
        if not records:
            return HiddenProcess(pid=pid, process_name=name, hung=hung,
                                 partial=partial) \
                if hung else None
        if not name:
            # 调用方(如测试/命令行)不一定填过 process_name,报警文案要能
            # 指名道姓,不然用户只看到一个 pid
            name = win32.process_image_path(pid).rsplit("\\", 1)[-1]

        hp = HiddenProcess(pid=pid, process_name=name, records=records,
                           failed=failed, hung=hung, profile=profile,
                           partial=partial)
        if failed:
            from ..log import write
            write(f"未能隐藏的窗口:{name}(pid={pid}) 失败 {failed}/{len(records)} 个")
        # 托盘图标和静音都是**整进程级**的,处置方式不一样:
        # - 托盘图标只在整个进程都藏干净时删。只藏一半还删图标,用户看到的是
        #   "浏览器明明还在,托盘图标没了" —— 而且不给恢复指令就回不来;
        # - 要不要静音由 should_mute 一处说了算(声音列 > 场景模式 > 全局)。
        want_mute, why = self.mute_reason(name, profile)
        if not partial:
            delete_process_tray_icons(pid)
        if want_mute:
            n = set_process_mute(pid, True)
            hp.muted = n > 0
            if partial:
                from ..log import write
                write(f"{name} 只藏了一部分窗口,静音连带未隐藏的窗口一起没声"
                      "(想保留声音:主界面「声音」列勾「藏了继续响」)")
        elif why != "没开静音":
            from ..log import write
            write(f"{name} 隐藏时不静音({why})")
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
                if hp is not None:
                    for hwnd in hp.records:
                        self._pending_hwnd.pop(hwnd, None)
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
        """整进程恢复:把记录里的窗口全放回来。"""
        main_hwnd = self._restore_records(hp, None)
        self._finish_release(hp, main_hwnd)

    def _restore_records(self, hp: HiddenProcess,
                         hwnds: set[int] | None) -> int:
        """恢复 hp 记录里的窗口(`hwnds` 给定时只恢复其中一部分)。

        返回面积最大的那个窗口的 hwnd(整进程释放后拉前台用)。
        """
        main_hwnd = 0
        main_size = -1
        todo = list(hp.records) if hwnds is None else list(hwnds)
        for hwnd in todo:
            rec = hp.records.pop(hwnd, None)
            if rec is None:
                continue
            self._retry.pop(hwnd, None)
            if not win32.IsWindow(hwnd):
                continue
            win32.restore_window(hwnd, rec.ex_style, rec.show_cmd)
            size = win32.get_window_rect(hwnd)
            area = max(0, size[2] - size[0]) * max(0, size[3] - size[1])
            if area > main_size:
                main_size, main_hwnd = area, hwnd
        return main_hwnd

    def _finish_release(self, hp: HiddenProcess, main_hwnd: int = 0) -> None:
        """整进程放回后的收尾:取消静音、叫回托盘图标、拉前台。"""
        self._unmute(hp)
        request_tray_icon_restore(hp.pid)
        if main_hwnd and self._settings.activate_on_restore:
            # 全屏游戏光 SetForegroundWindow 常常抢不到前台(前台锁),
            # 必须走 AttachThreadInput 那条路,否则游戏回来了但停在后台
            win32.force_foreground(main_hwnd)

    def _unmute(self, hp: HiddenProcess) -> None:
        """取消这个程序(整个进程树)的静音。

        静音是整进程级的,所以**放出任何一个窗口**就该把声音还回来 ——
        否则用户把正在听的那个窗口点回来,却是没声的。
        """
        if not hp.muted:
            return
        # 音频会话可能被系统临时回收后再重建(播音循环重启时尤其明显),
        # 瞬间取消静音会影响 0 个会话 —— 重试几次,否则用户恢复窗口后
        # 应用一直处于静音状态(实测踩坑)。
        for _ in range(5):
            if set_process_mute(hp.pid, False) > 0:
                break
            time.sleep(0.2)
        hp.muted = False

    def restore_hwnds(self, hwnds: list[int], wait: bool = False) -> int:
        """只恢复这几个窗口 —— 同一进程的其它隐藏窗口继续保持隐藏。

        返回"被完全放回的进程数"在异步模式下拿不到,这里返回提交的窗口数,
        调用方只关心"有没有做"(界面靠 state_changed 刷新)。
        """
        wanted: list[int] = []
        with self._lock:
            recorded = {h for hp in self._hidden.values() for h in hp.records}
        for hwnd in hwnds:
            try:
                hwnd = int(hwnd)
            except (TypeError, ValueError):
                continue
            if hwnd not in recorded:
                # 清掉可能残留的"正在隐藏"标记:藏/恢复是异步的,请求被队列
                # 丢弃时标记会留在这儿,让这个窗口永远被判成"正在藏",
                # 于是一个切换动作只会空转(点了没反应)。
                with self._lock:
                    self._pending_hwnd.pop(hwnd, None)
                continue
            wanted.append(hwnd)
            # 手动恢复 = 用户明确表示"这个别再藏了":记到 pid 上,免得
            # 「规则持续生效」三秒后又把它藏回去
            pid = win32.get_window_pid(hwnd)
            if pid:
                with self._lock:
                    self._opt_out.add(pid)
                    self._pending_hwnd[hwnd] = "restore"
        if not wanted:
            return 0
        if wait:
            self._wait_idle()
            return self._restore_hwnds_worker(wanted)
        self._submit(self._restore_hwnds_worker, wanted)
        return len(wanted)

    def _restore_hwnds_worker(self, hwnds: list[int]) -> int:
        done = 0
        try:
            for hwnd in hwnds:
                with self._lock:
                    hp = next((h for h in self._hidden.values()
                               if hwnd in h.records), None)
                if hp is None:
                    continue
                try:
                    main_hwnd = self._restore_records(hp, {hwnd})
                except Exception:
                    from ..log import write
                    write(f"恢复 hwnd={hwnd} 时出现异常:\n{traceback.format_exc()}")
                    continue
                done += 1
                with self._lock:
                    released = not hp.records
                    if released:
                        self._hidden.pop(hp.pid, None)
                # 静音是整进程级的:放出任何一个窗口,声音就该回来
                self._unmute(hp)
                # 只有整个进程都放回来时才动托盘图标 ——
                # 还有窗口藏着的时候恢复托盘图标等于自己暴露了
                if released:
                    self._finish_release(hp, main_hwnd)
        finally:
            with self._lock:
                for hwnd in hwnds:
                    if self._pending_hwnd.get(hwnd) == "restore":
                        self._pending_hwnd.pop(hwnd, None)
        if done:
            self._save_state()
            self.state_changed.emit()
            self.action_done.emit("restored", done)
        return done

    def restore_hwnd(self, hwnd: int, wait: bool = False) -> bool:
        """恢复单个窗口(同进程其它隐藏窗口不受影响)。"""
        return self.restore_hwnds([hwnd], wait=wait) > 0

    def set_audio_policy(self, process_name: str, keep_audio: bool) -> int:
        """「藏了继续响」改了之后,对**已经在隐藏中**的程序立刻生效。

        否则用户勾上"继续响"却发现音乐还是没声,只能先恢复再藏一次 ——
        而"恢复再藏"会把窗口弹到前台,正是他想避免的事。
        返回受影响(正在隐藏中)的程序数。

        `keep_audio` 调用方必须**先**写进 config(`set_keeps_audio`);这里
        不直接用它,而是统一走 `should_mute()`,免得又出现"第二套算法"。
        """
        name = (process_name or "").strip().lower()
        if not name:
            return 0
        with self._lock:
            targets = [hp.pid for hp in self._hidden.values()
                       if (hp.process_name or "").lower() == name]
        if not targets:
            return 0

        def run():
            for pid in targets:
                with self._lock:
                    hp = self._hidden.get(pid)
                    prof_name = hp.profile if hp else ""
                # 判定走同一个 should_mute —— 以前这里自己算了一遍
                # (而且漏掉了场景的"从不静音"),于是界面上刚取消勾选、
                # 下一秒又被别处的规则按回去。
                target = self.should_mute(name, prof_name)
                if target:
                    n = set_process_mute(pid, True)
                    with self._lock:
                        hp = self._hidden.get(pid)
                        if hp is not None:
                            hp.muted = hp.muted or n > 0
                else:
                    set_process_mute(pid, False)
                    with self._lock:
                        hp = self._hidden.get(pid)
                        if hp is not None:
                            hp.muted = False
            self._save_state()
            self.state_changed.emit()

        self._submit(run)
        return len(targets)

    # ------------------------------------------------------------ 切换
    def toggle(self, hwnds: list[int]) -> str:
        """切换给定窗口的隐藏状态,返回**意图**:'hidden'/'restored'/'none'。

        粒度是**窗口**:勾了两个浏览器窗口、其中一个已经藏了,再按一次热键
        就把那个放回来(而不是把整个浏览器进程都放回来)。

        动作是异步的,所以这里返回的是"我要隐藏/恢复",不保证已完成 ——
        调用方(托盘切换)只需要知道方向,界面靠 state_changed 刷新。
        """
        valid = []
        for hwnd in hwnds or ():
            try:
                hwnd = int(hwnd)
            except (TypeError, ValueError):
                continue
            if win32.IsWindow(hwnd):
                valid.append(hwnd)
        if not valid:
            return "none"
        hidden = [h for h in valid if self.is_hwnd_hidden_or_pending(h)]
        if hidden:
            self.restore_hwnds(hidden)
            return "restored"
        self.hide_hwnds(valid)
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
        self._hide_pids_blocking(target_pids, profile=profile.name)

    def _profile_targets(self, profile) -> list[int]:
        """当前可见、命中该场景规则、且不该被跳过的进程。"""
        extra = self._settings.protected_processes
        our_pid = os.getpid()
        with self._lock:
            # 只跳过"整个进程都藏干净了"的;partial(手动只藏了一部分)要让
            # 场景把它剩下的窗口也收走,否则用户会觉得"切了场景没反应"
            already = {p for p, hp in self._hidden.items() if not hp.partial}
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
                    if hp.partial and w.hwnd not in hp.records:
                        # 同程序多开、只藏了一部分:没藏过的窗口是用户留着
                        # 要看的,补刀不能碰它(否则"别忘了藏"变成"全都藏")
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
            # 同上:partial 的进程还要继续收(场景语义是"整个程序")
            already = {p for p, hp in self._hidden.items() if not hp.partial}
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
        self._hide_pids_blocking(target_pids,
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
                hwnd,
                # 同程序还有窗口留着时不能"先最小化"(那是整进程的动作)
                allow_minimize=self._settings.force_fullscreen_hide
                and not hp.partial)
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
