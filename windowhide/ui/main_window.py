"""main_window.py - 主窗口。"""
from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QStatusBar, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..core import protect, win32
from ..core.classify import TYPE_LABELS, classify_window
from ..core.hide import HideController
from ..core.hotkeys import HotkeyManager
from ..core.targets import TargetRule, resolve_target_hwnds
from ..core.win32 import WindowInfo
from .settings_dialog import SettingsDialog
from .tray import SystemTray
from .window_list import WindowTable

SHOW_ME_MSG = "WindowHideShowMeMessage"


class MainWindow(QMainWindow):
    def __init__(self, config: Config, controller: HideController,
                 hotkeys: HotkeyManager, tray: SystemTray):
        super().__init__()
        self._config = config
        self._controller = controller
        self._hotkeys = hotkeys
        self._tray = tray
        self._windows: list[WindowInfo] = []

        self.setWindowTitle("窗口隐藏工具")
        self.resize(980, 620)
        self._build_ui()
        self._wire()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()

        self.refresh()
        self._update_status()

    # ---------------------------------------------------------------- UI 搭建
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # ---- 顶部工具行 ----
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索窗口标题 / 进程名…")
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(260)
        top.addWidget(self._search)

        self._type_filter = QComboBox()
        self._type_filter.addItem("全部类型", "")
        for key, label in TYPE_LABELS.items():
            self._type_filter.addItem(label, key)
        top.addWidget(self._type_filter)

        top.addStretch(1)

        self._btn_hide = QPushButton("隐藏勾选")
        # 注意:不要设 setDefault(True) —— 默认按钮会在搜索框按回车时
        # 触发"隐藏勾选",误隐藏后果严重(曾按用户反馈踩坑)
        self._btn_hide.setToolTip(
            "只隐藏勾中的窗口。\n"
            "同一个程序开了多个窗口(浏览器多开)时,没勾的窗口会原样留着。\n"
            "「声音」列决定这个程序被藏起来后还要不要出声。")
        self._btn_restore = QPushButton("恢复")
        self._btn_profile = QPushButton("场景")
        self._btn_profile_menu = QMenu(self._btn_profile)
        self._btn_profile.setMenu(self._btn_profile_menu)
        self._btn_profile_menu.aboutToShow.connect(self._rebuild_profile_menu)
        self._btn_settings = QPushButton("设置")
        for b in (self._btn_hide, self._btn_restore,
                  self._btn_profile, self._btn_settings):
            top.addWidget(b)
        v.addLayout(top)

        # ---- 窗口表格 ----
        self._table = WindowTable()
        v.addWidget(self._table, 1)

        # ---- 底部行 ----
        bottom = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_all.setToolTip("点击全选;再次点击取消全选")
        self._btn_invert = QPushButton("反选")
        bottom.addWidget(self._btn_all)
        bottom.addWidget(self._btn_invert)
        bottom.addStretch(1)
        v.addLayout(bottom)

        self.setStatusBar(QStatusBar())

    def _wire(self) -> None:
        self._btn_hide.clicked.connect(self._hide_checked)
        self._btn_restore.clicked.connect(self._restore_all)
        self._btn_settings.clicked.connect(self._open_settings)
        self._btn_all.clicked.connect(self._table.toggle_all_checked)
        self._btn_invert.clicked.connect(self._table.invert_checked)
        self._search.textChanged.connect(self._apply_filter)
        self._type_filter.currentIndexChanged.connect(self._apply_filter)
        self._table.row_activated.connect(self._toggle_hwnd)
        self._table.sound_toggled.connect(self._on_sound_toggled)

        self._controller.state_changed.connect(self.refresh)
        self._controller.action_done.connect(self._on_action)
        self._controller.hide_incomplete.connect(self._on_hide_incomplete)
        self._controller.privilege_blocked.connect(self._on_privilege_blocked)
        self._controller.protected_skipped.connect(self._on_protected_skipped)
        self._controller.profile_changed.connect(self._on_profile_changed)
        self._hotkeys.triggered.connect(self._on_hotkey)
        self._hotkeys.register_failed.connect(self._on_hotkey_failed)

        self._tray.act_toggle.triggered.connect(self._toggle_targets)
        self._tray.act_hide_self.triggered.connect(self._hide_self)
        self._tray.act_show.triggered.connect(self._show_window)
        self._tray.act_elevate.triggered.connect(self._restart_as_admin)
        self._tray.act_quit.triggered.connect(self._quit)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.profile_menu.aboutToShow.connect(self._rebuild_tray_profiles)

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        our_pid = win32.get_window_pid(int(self.winId()))
        hidden = self._controller.hidden_hwnds()
        # 在途的窗口也要算"留着":异步隐藏的真空期里它既不可见、也还没进
        # 隐藏记录,不兜这一下,列表会把它删掉,连勾选一起没了。
        alive = hidden | self._controller.pending_hwnds()
        try:
            windows = win32.enum_top_level_windows()
        except Exception:
            windows = []
        # 可见窗口 + 被本工具隐藏的窗口(后者要在列表里显示"已隐藏")
        self._windows = [w for w in windows
                         if w.pid != our_pid
                         and (w.visible or w.hwnd in alive)
                         # 桌面/任务栏(explorer 的 Progman/Shell_TrayWnd)
                         # 一律不列 —— 它们混进来就等于给了用户一个
                         # "一键把桌面藏了"的按钮
                         and not win32.is_shell_window(w.hwnd)
                         # 无标题但铺满屏幕的(全屏游戏常见)也要列出来,
                         # 否则用户根本没地方勾选它
                         and (w.title or win32.is_fullscreen_window(w.hwnd))]
        for w in self._windows:
            w.process_name = w.process_name or win32.process_image_path(w.pid).rsplit("\\", 1)[-1]
            if not w.process_path:
                w.process_path = win32.process_image_path(w.pid)
        self._protected = protect.protected_set(self._config.protected_processes)
        self._apply_filter()
        self._update_status()

    def _filtered_windows(self) -> list[WindowInfo]:
        text = self._search.text().strip().lower()
        tkey = self._type_filter.currentData()
        out = []
        for w in self._windows:
            if text and text not in (w.title or "").lower() \
                    and text not in (w.process_name or "").lower():
                continue
            if tkey and classify_window(w) != tkey:
                continue
            out.append(w)
        return out

    def _apply_filter(self) -> None:
        our_pid = win32.get_window_pid(int(self.winId()))
        self._table.refresh(self._filtered_windows(),
                            self._controller.hidden_hwnds(), our_pid,
                            getattr(self, "_protected", set()),
                            keep_audio=set(self._config.keep_audio_processes),
                            mute_global=self._config.mute_on_hide)

    def _update_status(self) -> None:
        n_proc, n_win = self._controller.hidden_summary()
        if n_proc:
            self._tray.set_hiding(True)
            self._tray.set_state_text(f"已隐藏 {n_win} 个窗口 / {n_proc} 个程序")
        else:
            self._tray.set_hiding(False)
            self._tray.set_state_text("运行中")
        scene = self._config.active_profile
        scene_txt = f"场景「{scene}」·" if scene else ""
        if n_proc:
            self.statusBar().showMessage(
                f"{scene_txt}后台运行中:已隐藏 {n_win} 个窗口({n_proc} 个程序),程序未关闭")
        else:
            self.statusBar().showMessage(
                f"{scene_txt}就绪:勾选窗口后点击「隐藏勾选」,或按全局快捷键")

    # ---------------------------------------------------------------- 动作
    def _current_targets(self) -> list[int]:
        """当前该被"切换"的窗口:勾选/选中优先,否则用已保存的场景规则。

        规则解析必须能容忍**快照过期**:恢复是异步的,刷新恰好赶在"窗口已经
        不隐藏、但还没重新显示"的那一瞬,这个窗口会既不在可见列表、也不在
        隐藏集合里,于是被刷出 `self._windows` —— 这时同一次点击就会变成
        "没目标,什么都不做"(用户体感:"点了没反应")。所以解析不到就重新
        枚举一次(18ms,只在没目标时发生)。
        """
        checked = self._table.checked_hwnds()
        if checked:
            return checked
        selected = self._table.selected_hwnds()
        if selected:
            return selected
        hwnds = self._resolve_rules(self._windows)
        if hwnds:
            return hwnds
        try:
            fresh = win32.enum_top_level_windows()
        except Exception:
            return []
        return self._resolve_rules(fresh)

    def _resolve_rules(self, windows) -> list[int]:
        """按已保存的规则挑窗口(含"已隐藏/正在切换"的)。"""
        matched = resolve_target_hwnds(
            self._config.targets, windows,
            include_hidden=self._controller.hidden_hwnds())
        out = [w.hwnd for w in matched]
        # 正在隐藏途中(还没写进隐藏记录)的窗口也要算进来,否则连点第二下
        # 会被判成"还没藏"从而又藏一次。只收命中规则的,别把用户手动藏的
        # 窗口卷进来。
        for w in windows:
            if w.hwnd in out:
                continue
            if not self._controller.is_pid_hidden_or_pending(w.pid):
                continue
            if any(rule.matches(w) for rule in self._config.targets):
                out.append(w.hwnd)
        return out

    def _hide_checked(self) -> None:
        hwnds = self._table.checked_hwnds() or self._table.selected_hwnds()
        if not hwnds:
            QMessageBox.information(self, "提示", "请先勾选要隐藏的窗口。")
            return
        self._controller.hide_hwnds(hwnds)

    def _restore_all(self) -> None:
        if not self._controller.hidden_pids():
            QMessageBox.information(self, "提示", "当前没有已隐藏的窗口。")
            return
        self._controller.restore_all()

    def _toggle_targets(self) -> None:
        self._controller.toggle(self._current_targets())

    def _toggle_hwnd(self, hwnd: int) -> None:
        # 用"已隐藏或正在隐藏"判断,别用纯状态:隐藏是异步的,双击的
        # 第二下若赶在落地之前,会看到"还没藏"从而再藏一次(点了恢复没反应)
        if self._controller.is_hwnd_hidden_or_pending(hwnd):
            self._controller.restore_hwnd(hwnd)
        else:
            self._controller.hide_hwnds([hwnd])

    def _on_sound_toggled(self, process_name: str, keep: bool) -> None:
        """「声音」列:这个程序被隐藏时要不要继续出声。

        为什么只能按程序设:系统音量合成器的会话是挂在**进程**上的,
        同一个程序的多个窗口共用一路声音 —— 想让"这一个窗口静音、另一个
        继续响"在 Windows 上做不到。所以这一列是程序级开关,同一程序的
        所有行联动。
        """
        if self._config.set_keeps_audio(process_name, keep):
            self._config.save()
        n = self._controller.set_audio_policy(process_name, keep)
        msg = (f"{process_name}:隐藏时继续播放声音"
               if keep else f"{process_name}:隐藏时跟随全局静音设置")
        if n:
            msg += f"(已对隐藏中的 {n} 个窗口立即生效)"
        self.statusBar().showMessage(msg)
        self._update_status()

    def _hide_self(self) -> None:
        """把本工具主窗口收进托盘(与点叉号等效)。

        曾经的"隐藏前台窗口"是个语义陷阱:从托盘菜单触发时,
        GetForegroundWindow 拿到的是任务栏/桌面(explorer),
        一键把桌面都藏了(用户实测踩坑);在主界面点又会藏掉自己。
        用户真正想要的就是这个 —— 工具自藏到托盘。
        """
        self.hide()
        if self._config.show_balloon:
            self._tray.showMessage("窗口隐藏工具", "已收进托盘,双击图标唤回主界面")

    # ---------------------------------------------------------------- 场景
    def _rebuild_profile_menu(self) -> None:
        m = self._btn_profile_menu
        m.clear()
        active = self._config.active_profile
        if self._config.profiles:
            for p in self._config.profiles:
                act = m.addAction(p.name)
                act.setCheckable(True)
                act.setChecked(p.name == active)
                act.triggered.connect(
                    lambda _checked=False, n=p.name: self._switch_profile(n))
            m.addSeparator()
        else:
            act = m.addAction("(还没有场景)")
            act.setEnabled(False)
            m.addSeparator()
        m.addAction("把勾选的窗口加入当前场景", self._add_checked_to_profile)
        m.addAction("新建场景…(用勾选的窗口)", self._new_profile_with_checked)
        m.addAction("在设置里管理场景…", self._open_settings)

    def _rebuild_tray_profiles(self) -> None:
        m = self._tray.profile_menu
        m.clear()
        active = self._config.active_profile
        if not self._config.profiles:
            act = m.addAction("(还没有场景)")
            act.setEnabled(False)
            return
        for p in self._config.profiles:
            act = m.addAction(p.name)
            act.setCheckable(True)
            act.setChecked(p.name == active)
            act.triggered.connect(
                lambda _checked=False, n=p.name: self._switch_profile(n))

    def _switch_profile(self, name: str) -> None:
        self._controller.activate_profile(name)
        # 场景名要立刻反映到状态栏;真正的隐藏结果由 state_changed 驱动刷新
        self._config.active_profile = name
        self._update_status()

    def _scene_name(self) -> str:
        p = self._config.active()
        return p.name if p else "默认"

    def _add_rules_to(self, profile, hwnds: list[int]) -> int:
        by_hwnd = {w.hwnd: w for w in self._windows}
        added = 0
        for hwnd in hwnds:
            w = by_hwnd.get(hwnd)
            if not w:
                continue
            if protect.is_protected(w.process_name,
                                    self._config.protected_processes):
                continue
            rule = TargetRule.from_window(w)
            if rule not in profile.rules:
                profile.rules.append(rule)
                added += 1
        return added

    def _add_checked_to_profile(self) -> None:
        hwnds = self._table.checked_hwnds()
        if not hwnds:
            QMessageBox.information(
                self, "提示",
                "请先勾选窗口,再把它们加入场景。\n"
                "场景 = 一批窗口的集合,切到该场景时这些窗口会被一起隐藏。")
            return
        profile = self._config.ensure_profile(self._config.active_profile or "默认")
        added = self._add_rules_to(profile, hwnds)
        self._config.save()
        self._update_status()
        QMessageBox.information(
            self, "已保存",
            f"向场景「{profile.name}」新增 {added} 条规则"
            + ("(重复的已跳过)" if added < len(hwnds) else "") + "。")

    def _new_profile_with_checked(self) -> None:
        name, ok = QInputDialog.getText(
            self, "新建场景", "场景名称(例如:上班 / 摸鱼 / 游戏):")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self._config.profile_names():
            QMessageBox.warning(self, "重名", f"已经有叫「{name}」的场景了。")
            return
        from ..config import Profile
        profile = Profile(name=name)
        self._config.profiles.append(profile)
        self._config.set_active(name)
        added = self._add_rules_to(profile, self._table.checked_hwnds())
        self._config.save()
        self.refresh()
        QMessageBox.information(
            self, "场景已创建",
            f"场景「{name}」已创建,包含 {added} 条规则。\n"
            "以后点托盘菜单里的场景名就能一键切换。")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec():
            dlg.apply_to(self._config)
            self._config.save()
            self._apply_hotkeys()
            self._config.apply_autostart()

    def _apply_hotkeys(self) -> None:
        c = self._config
        # toggle 模式:一个键反复切换;split 模式:隐藏键 + 恢复键分开。
        # 另有一个"隐藏本工具"(收进托盘),与模式无关,始终注册。
        specs = {"hide_self": (c.hotkey_hide_self.mods, c.hotkey_hide_self.vk)}
        if c.hotkey_mode == "split":
            specs["hide"] = (c.hotkey_hide.mods, c.hotkey_hide.vk)
            specs["restore"] = (c.hotkey_restore.mods, c.hotkey_restore.vk)
        else:
            specs["toggle"] = (c.hotkey_toggle.mods, c.hotkey_toggle.vk)
        self._hotkeys.set_hotkeys(specs)

    # ---------------------------------------------------------------- 信号
    def _on_hotkey(self, action: str) -> None:
        if action == "toggle":
            self._toggle_targets()
        elif action == "hide":
            hwnds = self._current_targets()
            if hwnds:
                self._controller.hide_hwnds(hwnds)
        elif action == "restore":
            self._controller.restore_all()
        elif action == "hide_self":
            self._hide_self()

    def _on_hotkey_failed(self, actions: str) -> None:
        # 状态栏消息一闪而过,用户根本注意不到(曾因此以为"快捷键坏了")。
        # 气泡提示 + 日志双管齐下。
        msg = (f"以下热键注册失败(可能被其它软件占用),请在设置中更换组合: {actions}")
        self.statusBar().showMessage("警告:" + msg)
        self._tray.showMessage("快捷键注册失败", msg)
        from ..log import write
        write("界面提示: " + msg)

    def _on_action(self, action: str, count: int) -> None:
        from ..log import write
        if action == "profile_empty":
            msg = ("当前场景里没有匹配的窗口 —— 可能是相关程序还没打开,"
                   "或者规则里写的进程名对不上。")
            self.statusBar().showMessage("提示:" + msg)
            if self._config.show_balloon:
                self._tray.showMessage("场景没匹配到窗口", msg)
            return
        self.refresh()
        if action == "hidden":
            # 计数改成**窗口**数:隐藏现在是可以只藏同一个程序里的一部分
            # 窗口的,"N 个程序"会让用户以为整个程序都被收起来了
            n_win, n_proc = self._controller.hidden_summary()
            write(f"隐藏 {count} 处(当前共 {n_win} 个窗口 / {n_proc} 个程序)")
            if self._config.show_balloon:
                self._tray.showMessage(
                    "已隐藏",
                    f"已隐藏 {n_win} 个窗口(涉及 {n_proc} 个程序),"
                    "任务栏与托盘图标已隐藏")
            # 只藏了一部分窗口却把整个程序静音了 —— 这类"意料之外的连带
            # 影响"必须说出来,不然用户只会觉得工具坏了
            muted = self._controller.partially_muted()
            if muted:
                self.statusBar().showMessage(
                    "提示:" + "、".join(muted) + " 还有窗口在显示,"
                    "但声音被整个程序静音了(声音只能按程序关)—— "
                    "不想这样就到「声音」列勾上它")
        elif action == "restored":
            n_win, n_proc = self._controller.hidden_summary()
            write(f"恢复 {count} 处(剩余 {n_win} 个窗口 / {n_proc} 个程序)")
            if self._config.show_balloon:
                self._tray.showMessage(
                    "已恢复",
                    "窗口已全部恢复显示" if n_win == 0
                    else f"已恢复;仍有 {n_win} 个窗口处于隐藏状态")

    def _on_profile_changed(self, name: str) -> None:
        self.refresh()
        from ..log import write
        write(f"切换场景:{name}")

    def _restart_as_admin(self) -> None:
        """以管理员身份重启自己。

        目标程序以管理员权限运行时,普通权限进程对它的窗口操作会被 UIPI
        静默拦掉(连 SetWindowLongPtr 都不报错),只能整套搬到提权进程里。
        """
        import sys
        from pathlib import Path

        if getattr(sys, "frozen", False):
            # 打包成 exe 后 sys.executable 就是自己:参数只能给 --elevated,
            # 不能再把 exe 自己当脚本传进去(否则命令行变成
            # `WindowHide.exe WindowHide.exe --elevated`)
            target, params = sys.executable, "--elevated"
        else:
            # 入口脚本路径:优先用启动时的 argv[0],取不到就按包位置推 run.pyw
            script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
            if not script or not os.path.exists(script):
                script = str(Path(__file__).resolve().parents[2] / "run.pyw")
            target, params = sys.executable, f'"{script}" --elevated'
        # --elevated:跳过单实例检查 —— 此刻旧实例还在退出过程中,
        # 互斥体没释放,不跳过的话新进程会以为"已有一个实例"而直接退出。
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, params, None, 1)
        if int(ret) <= 32:
            self._tray.showMessage("提权失败", "管理员授权被取消,本次未重启")
            return
        self._hotkeys.stop()
        self._tray.hide()
        # 直接退进程:隐藏状态已落盘,新实例启动时会重新接管,
        # 走 QApplication.quit() 反而会先 restore_all 把窗口全放出来。
        os._exit(0)

    def _on_hide_incomplete(self, count: int, hint: str) -> None:
        """有窗口没藏掉时必须说出来 —— 静默失败最坑,用户以为藏好了。"""
        self.statusBar().showMessage("警告:" + hint)
        self._tray.showMessage("部分窗口未能隐藏", hint)
        from ..log import write
        write("界面提示: " + hint)

    def _on_privilege_blocked(self, msg: str) -> None:
        """目标权限比我们高:说清原因,别让用户以为工具坏了。"""
        self.statusBar().showMessage("警告:" + msg)
        self._tray.showMessage("需要管理员权限", msg)
        from ..log import write
        write("界面提示: " + msg)

    def _on_protected_skipped(self, msg: str) -> None:
        """命中了保护名单:同样要说出来,否则用户以为工具没反应。"""
        self.statusBar().showMessage("已跳过:" + msg)
        self._tray.showMessage("已跳过受保护的进程", msg)
        from ..log import write
        write("界面提示: " + msg)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:      # 左键点击 = 切换
            self._toggle_targets()
        elif reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        # 退出前必须**同步**把所有窗口放出来(必须是 wait=True:异步提交
        # 后进程立刻就没了,窗口会永远留在隐藏状态)
        self._controller.restore_all(wait=True)
        self._hotkeys.stop()
        self._tray.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    # ---------------------------------------------------------------- 事件
    def closeEvent(self, event) -> None:
        if self._config.minimize_to_tray:
            event.ignore()
            self.hide()
            # 这条提示曾经是无条件的 —— 用户每次关窗口都被弹一下。
            # 现在有自己的开关:很多人只想关掉这一条,还想留着隐藏/恢复的确认。
            if self._config.show_minimize_hint:
                self._tray.showMessage(
                    "窗口隐藏工具", "已最小化到托盘,点击图标可切换隐藏状态")
        else:
            self._quit()
            event.accept()

    def nativeEvent(self, eventType, message):
        # 单实例唤醒消息
        try:
            if eventType == "windows_generic_MSG":
                import ctypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == self._show_me_msg_id:
                    self._show_window()
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def register_show_me(self) -> None:
        self._show_me_msg_id = win32.RegisterWindowMessageW(SHOW_ME_MSG)
