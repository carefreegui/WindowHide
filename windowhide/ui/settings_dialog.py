"""settings_dialog.py - 设置对话框(快捷键 / 行为 / 场景)。"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..config import (Config, MUTE_ALWAYS, MUTE_INHERIT,
                      MUTE_MODE_LABELS, MUTE_MODES, Profile)
from ..core.protect import BUILTIN
from ..core.targets import TargetRule
from ..core.hotkeys import reset_capture
from ..core.win32 import ReleaseCapture
from .hotkey_edit import HotkeyCaptureButton


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config
        # 改一份副本:用户点「取消」时不能留下半套改动
        self._profiles: list[Profile] = copy.deepcopy(config.profiles)
        self._current = config.active_profile
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self._build()

    # ---------------------------------------------------------------- UI
    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """把一页塞进滚动区。

        不是洁癖:说明文字一多,窗口高度不够时 QLabel(wordWrap)会被压到
        低于自身需要的高度,几行字直接**叠在一起**。滚动区让内容永远拿到
        它需要的高度,窗口再小也只是出滚动条。
        """
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return area

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._scrollable(self._hotkey_tab()), "快捷键")
        tabs.addTab(self._scrollable(self._behavior_tab()), "行为")
        tabs.addTab(self._scrollable(self._targets_tab()), "场景")
        layout.addWidget(tabs)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton("保存")
        ok.setDefault(True)      # Enter 保存是本来的习惯,留着
        cancel = QPushButton("取消")
        # 取消按钮不能"一有焦点就被 Enter 点掉":老 Qt 的 autoDefault 默认
        # 开启,用户在设置页里随手按个回车,整份改动就没了。
        cancel.setAutoDefault(False)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _capture_buttons(self) -> tuple:
        """所有"等待按键"的按钮。用 getattr 兜底:_build 中途失败时也得能收尾。"""
        return tuple(
            b for b in (getattr(self, name, None) for name in (
                "_cap_toggle", "_cap_hide", "_cap_restore",
                "_cap_toggle_mouse", "_cap_hide_mouse", "_cap_restore_mouse",
                "_cap_self")) if b is not None)

    def done(self, result: int) -> None:
        """对话框关掉的唯一出口,在这里无条件把捕获态收干净。

        🔴 这是全项目最重要的一次收尾。用户踩过:录入到一半按 Esc / 点取消,
        对话框直接 reject,按钮的 `_end_capture()` 根本没机会跑,全局捕获
        标志就永久停在 True —— 之后所有鼠标侧键热键全部失灵(钩子以为还在
        录入,只往界面发信号、不触发动作),而用户完全看不出这是"设置窗口
        的残留状态"。他重启程序才好。

        `done()` 是所有路径的必经之路:保存(accept)、取消(reject)、Esc、
        右上角叉、程序主动关。放在这里才谈得上"保证"。
        """
        for cap in self._capture_buttons():
            cap._end_capture()
        reset_capture()
        # 兜底:把鼠标捕获放干净。
        #
        # 两件事分开做,因为它们是两层机制:
        # · `releaseMouse()` 只放 Qt 自己的**显式**捕获(grabMouse);
        # · 真正卡住用户鼠标的是平台层的**隐式** SetCapture,只认 Win32 的
        #   `ReleaseCapture()`。
        # 漏了后者的表现是:鼠标点桌面上任何东西都没反应,只有本工具的
        # 窗口还能点,连任务管理器都点不动 —— 用户会以为电脑坏了。
        try:
            self.releaseMouse()
        except Exception:
            pass
        try:
            ReleaseCapture()
        except Exception:
            pass
        super().done(result)

    def _hotkey_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        mode_box = QGroupBox("模式")
        mv = QVBoxLayout(mode_box)
        self._mode_toggle = QRadioButton("单一快捷键:反复按同一个键,隐藏 / 恢复切换")
        self._mode_split = QRadioButton("两个快捷键:分别设置「隐藏键」和「恢复键」")
        if self._config.hotkey_mode == "split":
            self._mode_split.setChecked(True)
        else:
            self._mode_toggle.setChecked(True)
        mv.addWidget(self._mode_toggle)
        mv.addWidget(self._mode_split)
        v.addWidget(mode_box)

        # 每个动作两格:键盘组合 + 可选的鼠标侧键。它们**同时生效** ——
        # 老板键讲究的是"手现在在哪边",不是"你更喜欢哪个"。
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        def col_hint(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setStyleSheet("color: #64748b;")
            return lab

        grid.addWidget(QWidget(), 0, 0)                  # 占位,让表头对齐
        grid.addWidget(col_hint("键盘组合键"), 0, 1)
        grid.addWidget(col_hint("鼠标侧键(可选,同时生效)"), 0, 2)

        self._cap_toggle = HotkeyCaptureButton(self._config.hotkey_toggle)
        self._cap_hide = HotkeyCaptureButton(self._config.hotkey_hide)
        self._cap_restore = HotkeyCaptureButton(self._config.hotkey_restore)
        self._cap_toggle_mouse = HotkeyCaptureButton(
            self._config.hotkey_toggle_mouse, mouse_only=True)
        self._cap_hide_mouse = HotkeyCaptureButton(
            self._config.hotkey_hide_mouse, mouse_only=True)
        self._cap_restore_mouse = HotkeyCaptureButton(
            self._config.hotkey_restore_mouse, mouse_only=True)

        for cap in (self._cap_toggle, self._cap_hide, self._cap_restore):
            cap.setToolTip("点一下进入录入,然后按键盘组合键。\n"
                           "手在键盘上时用它;想在鼠标上也能触发,"
                           "就给右边那一格也设个侧键。")

        self._hotkey_rows: list[tuple[QWidget, ...]] = []
        rows = [("切换:", self._cap_toggle, self._cap_toggle_mouse),
                ("隐藏:", self._cap_hide, self._cap_hide_mouse),
                ("恢复:", self._cap_restore, self._cap_restore_mouse)]
        for i, (label, kb, ms) in enumerate(rows, start=1):
            lab = QLabel(label)
            grid.addWidget(lab, i, 0)
            grid.addWidget(kb, i, 1)
            grid.addWidget(ms, i, 2)
            self._hotkey_rows.append((lab, kb, ms))
        v.addLayout(grid)
        # 模式切换时显隐行:选"单一"只显示切换键,
        # 选"两个"才出现隐藏键/恢复键(旧版三个框并列,填了不生效,纯误导)
        self._mode_toggle.toggled.connect(self._sync_hotkey_rows)
        self._mode_split.toggled.connect(self._sync_hotkey_rows)
        self._sync_hotkey_rows()

        extra = QGroupBox("附加")
        ev = QVBoxLayout(extra)
        self._cap_self = HotkeyCaptureButton(self._config.hotkey_hide_self)
        row = QHBoxLayout()
        row.addWidget(QLabel("隐藏本工具(收进托盘):"))
        row.addWidget(self._cap_self)
        row.addStretch(1)
        ev.addLayout(row)
        tip = QLabel("「隐藏本工具」= 把本工具主窗口收进系统托盘(不是藏别人窗口),"
                     "与点右上角叉号等效;双击托盘图标可唤回。\n"
                     "全局快捷键在系统任意界面生效;若被其它软件占用,换一个组合即可。\n\n"
                     "为什么必须带 Ctrl / Alt / Win:全局快捷键在系统里是"
                     "「独占」的 —— 一旦生效,这个组合在别的程序里就再也不能用了。"
                     "Shift+Enter 是聊天换行、Shift+字母是大写、Shift+数字是符号,"
                     "这类组合本工具会直接拒绝(不是坏了),否则整台电脑的键盘会像坏了一样。\n\n"
                     "鼠标侧键:点右边那一格,然后直接按侧键。键盘组合键和侧键"
                     "各一个,同时生效 —— 手在哪边就用哪边。\n"
                     "注意:设成快捷键的那个侧键,以后在浏览器里不再「前进/后退」"
                     "—— 事件被本工具接管了;没设的那一个照常可用。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #64748b;")
        ev.addWidget(tip)
        v.addWidget(extra)
        v.addStretch(1)
        return w

    def _sync_hotkey_rows(self) -> None:
        """按模式显隐输入行:单一快捷键只显示切换键,两个快捷键才显示隐藏/恢复键。"""
        if not getattr(self, "_hotkey_rows", None):
            return
        split = self._mode_split.isChecked()
        for i, widgets in enumerate(self._hotkey_rows):
            visible = split if i else not split   # 第 0 行是"切换"
            for w in widgets:
                w.setVisible(visible)

    def _behavior_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._cb_mute = QCheckBox("隐藏时同时静音目标程序(默认行为)")
        self._cb_mute.setToolTip(
            "按程序(整条音轨)静音,恢复时取消。这一项是「默认值」:\n"
            "· 场景页可以给某个场景单独设「总是静音 / 从不静音」,压过它;\n"
            "· 主界面「声音」列勾了「藏了继续响」的程序,优先级最高。\n"
            "三处的关系就是这三行,不会再有别的规则。")
        self._cb_mute.toggled.connect(self._refresh_mute_hint)
        self._cb_keep = QCheckBox("隐藏期间自动隐藏新弹出的窗口(持续隐藏)")
        self._cb_activate = QCheckBox("恢复时把主窗口带到前台")
        self._cb_tray = QCheckBox("关闭主界面时最小化到系统托盘(不退出)")
        self._cb_balloon = QCheckBox("隐藏 / 恢复时弹出托盘气泡提示")
        self._cb_min_hint = QCheckBox(
            "关闭主界面时提示「已最小化到托盘」(不喜欢被打扰可以关掉)")
        self._cb_autostart = QCheckBox("开机自动启动(写入当前用户注册表 Run 项)")
        self._cb_auto_rules = QCheckBox(
            "规则持续生效:目标程序重启后,按当前场景自动再隐藏一次")
        self._cb_force_fs = QCheckBox(
            "强制隐藏独占全屏游戏(先最小化再隐藏,可能让游戏失去响应)")

        self._cb_mute.setChecked(self._config.mute_on_hide)
        self._cb_keep.setChecked(self._config.keep_hidden)
        self._cb_activate.setChecked(self._config.activate_on_restore)
        self._cb_tray.setChecked(self._config.minimize_to_tray)
        self._cb_balloon.setChecked(self._config.show_balloon)
        self._cb_min_hint.setChecked(self._config.show_minimize_hint)
        self._cb_autostart.setChecked(self._config.autostart)
        self._cb_auto_rules.setChecked(self._config.auto_apply_rules)
        self._cb_force_fs.setChecked(self._config.force_fullscreen_hide)
        self._cb_auto_rules.setToolTip(
            "默认关闭:开着的时候,只要你打开属于当前场景的程序,它会被自动藏起来。\n"
            "手动点「恢复」的窗口不会被它抢回去(直到那个程序退出)。")
        self._cb_force_fs.setToolTip(
            "独占全屏(DirectX 独占模式)的游戏不看窗口可见性,只能先最小化逼它\n"
            "掉出全屏再隐藏。但部分游戏最小化后就不再处理消息:游戏自己回不来,\n"
            "本工具也可能卡住。默认关闭 —— 关着的时候遇到这种游戏只会提示\n"
            "「没藏掉」,不会去动它。")

        for cb in (self._cb_mute, self._cb_keep, self._cb_activate,
                   self._cb_auto_rules):
            v.addWidget(cb)
        for cb in (self._cb_tray, self._cb_balloon, self._cb_min_hint):
            v.addWidget(cb)
        for cb in (self._cb_autostart,):
            v.addWidget(cb)
        v.addWidget(self._cb_force_fs)

        # ---- 永不隐藏 ----
        guard = QGroupBox("永不隐藏的进程")
        gv = QVBoxLayout(guard)
        self._inp_protect = QLineEdit(", ".join(self._config.protected_processes))
        self._inp_protect.setPlaceholderText("例如: steam.exe, qq.exe(逗号分隔)")
        gv.addWidget(self._inp_protect)
        builtin = "、".join(sorted(BUILTIN))
        gtip = QLabel(f"内置保护(不可关闭):{builtin}。\n"
                      "按进程隐藏是进程级的,藏掉这些会连带把输入法、桌面或"
                      "其它同类窗口一起干掉。")
        gtip.setWordWrap(True)
        gtip.setStyleSheet("color: #64748b;")
        gv.addWidget(gtip)
        v.addWidget(guard)

        note = QLabel("要不要静音,只按这个顺序判一次(上面的项目优先级最高):\n"
                      "  1. 主界面「声音」列勾了「藏了继续响」→ 不静音\n"
                      "  2. 场景页的「总是静音 / 从不静音」→ 听场景的\n"
                      "  3. 都没说 → 听上面这个「隐藏时同时静音」\n\n"
                      "静音走系统音量合成器,只影响目标程序,不影响其它程序和"
                      "系统音量。但声音是按程序算的:同一个程序开多个窗口"
                      "只有一路声音,藏其中一个窗口并静音,同程序其它窗口"
                      "也会一起没声 —— 想让它继续响,就在「声音」列勾上它。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748b;")
        v.addWidget(note)
        v.addStretch(1)
        return w

    def _targets_tab(self) -> QWidget:
        """场景页:一个场景 = 一批规则 + 是否顺带静音。"""
        w = QWidget()
        v = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("场景:"))
        self._profile_box = QComboBox()
        for p in self._profiles:
            self._profile_box.addItem(p.name)
        if self._current:
            idx = self._profile_box.findText(self._current)
            if idx >= 0:
                self._profile_box.setCurrentIndex(idx)
        self._profile_box.currentIndexChanged.connect(self._on_profile_switch)
        top.addWidget(self._profile_box, 1)
        btn_new = QPushButton("新建…")
        btn_new.clicked.connect(self._new_profile)
        btn_del = QPushButton("删除")
        btn_del.clicked.connect(self._delete_profile)
        top.addWidget(btn_new)
        top.addWidget(btn_del)
        v.addLayout(top)

        # 静音模式用三态下拉,不用 checkbox —— checkbox 只有"静音/不静音",
        # 而这里第三态"跟随全局"才是默认,也是唯一能让两处设置不打架的写法。
        mute_row = QHBoxLayout()
        mute_row.addWidget(QLabel("启用该场景时,声音"))
        self._cb_profile_mute = QComboBox()
        for mode in MUTE_MODES:
            self._cb_profile_mute.addItem(MUTE_MODE_LABELS[mode], mode)
        self._cb_profile_mute.setToolTip(
            "跟随全局设置 = 按 设置→行为 里的「隐藏时同时静音目标程序」来。\n"
            "总是静音 / 从不静音 = 只对这个场景生效,压过全局那一项。\n\n"
            "注意:主界面「声音」列勾了「藏了继续响」的程序优先级最高,"
            "这里选什么都不影响它。")
        self._cb_profile_mute.currentIndexChanged.connect(self._sync_profile_mute)
        mute_row.addWidget(self._cb_profile_mute, 1)
        v.addLayout(mute_row)

        self._lbl_mute_hint = QLabel()
        self._lbl_mute_hint.setWordWrap(True)
        self._lbl_mute_hint.setStyleSheet("color: #64748b;")
        v.addWidget(self._lbl_mute_hint)

        v.addWidget(QLabel("该场景的规则(切到该场景时,命中规则的窗口会被隐藏):"))
        self._rules_list = QListWidget()
        v.addWidget(self._rules_list, 1)

        row = QHBoxLayout()
        del_btn = QPushButton("删除选中规则")
        del_btn.clicked.connect(self._delete_rule)
        row.addWidget(del_btn)
        row.addStretch(1)
        v.addLayout(row)

        tip = QLabel("在主界面勾选窗口 →「场景 ▾」→「把勾选的窗口加入当前场景」,"
                     "下次点托盘菜单里的场景名就能一键隐藏这一批。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #64748b;")
        v.addWidget(tip)

        self._on_profile_switch()
        return w

    # ---------------------------------------------------------------- 场景
    def _current_profile(self) -> Profile | None:
        idx = self._profile_box.currentIndex()
        if 0 <= idx < len(self._profiles):
            return self._profiles[idx]
        return None

    def _on_profile_switch(self) -> None:
        p = self._current_profile()
        self._rules_list.clear()
        self._cb_profile_mute.setEnabled(p is not None)
        if p is None:
            self._cb_profile_mute.setCurrentIndex(0)
            self._lbl_mute_hint.setText("")
            return
        for t in p.rules:
            self._rules_list.addItem(self._rule_text(t))
        idx = self._cb_profile_mute.findData(p.mute_mode)
        self._cb_profile_mute.setCurrentIndex(idx if idx >= 0 else 0)
        self._refresh_mute_hint()

    def _refresh_mute_hint(self) -> None:
        """把"这一项现在到底起不起作用"直接写出来。

        用户报的就是这个:两个地方都能"静音",改了一处没反应,不知道谁说了算。
        """
        # 行为页先建、场景页后建,而 _cb_mute.setChecked() 会提前触发 toggled
        if not hasattr(self, "_lbl_mute_hint") or not hasattr(self, "_profile_box"):
            return
        p = self._current_profile()
        if p is None:
            self._lbl_mute_hint.setText("")
            return
        global_on = self._cb_mute.isChecked() if hasattr(self, "_cb_mute") else False
        g = "开" if global_on else "关"
        if p.mute_mode == MUTE_INHERIT:
            self._lbl_mute_hint.setText(
                f"全局「隐藏时同时静音」现在是「{g}」—— 这个场景跟着它,"
                f"所以切过来之后{'会' if global_on else '不会'}静音。")
        elif p.mute_mode == MUTE_ALWAYS:
            self._lbl_mute_hint.setText(
                f"全局那个开关现在是「{g}」,但本场景选了「总是静音」,"
                f"所以切过来照样静音。")
        else:
            self._lbl_mute_hint.setText(
                f"全局那个开关现在是「{g}」,但本场景选了「从不静音」,"
                f"所以切过来不会静音。")

    def _sync_profile_mute(self, *_args) -> None:
        p = self._current_profile()
        if p is not None:
            p.mute_mode = self._cb_profile_mute.currentData() or MUTE_INHERIT
        self._refresh_mute_hint()

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "新建场景", "场景名称:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(p.name == name for p in self._profiles):
            QMessageBox.warning(self, "重名", f"已经有叫「{name}」的场景了。")
            return
        self._profiles.append(Profile(name=name))
        self._profile_box.addItem(name)
        self._profile_box.setCurrentIndex(self._profile_box.count() - 1)

    def _delete_profile(self) -> None:
        idx = self._profile_box.currentIndex()
        if not (0 <= idx < len(self._profiles)):
            return
        name = self._profiles[idx].name
        if QMessageBox.question(self, "删除场景",
                                f"确定删除场景「{name}」及其全部规则?") \
                != QMessageBox.Yes:
            return
        self._profiles.pop(idx)
        self._profile_box.removeItem(idx)
        self._on_profile_switch()

    @staticmethod
    def _rule_text(t: TargetRule) -> str:
        parts = [t.process]
        if t.window_class:
            parts.append(f"类名={t.window_class}")
        if t.title_regex:
            parts.append(f"标题~{t.title_regex}")
        label = f" [{t.label}]" if t.label else ""
        return " ".join(parts) + label

    def _delete_rule(self) -> None:
        p = self._current_profile()
        if p is None:
            return
        for item in self._rules_list.selectedItems():
            row = self._rules_list.row(item)
            self._rules_list.takeItem(row)
            if 0 <= row < len(p.rules):
                p.rules.pop(row)

    # ---------------------------------------------------------------- 导出
    def apply_to(self, config: Config) -> None:
        config.hotkey_mode = "split" if self._mode_split.isChecked() else "toggle"
        config.hotkey_toggle = self._cap_toggle.spec()
        config.hotkey_hide = self._cap_hide.spec()
        config.hotkey_restore = self._cap_restore.spec()
        config.hotkey_toggle_mouse = self._cap_toggle_mouse.spec()
        config.hotkey_hide_mouse = self._cap_hide_mouse.spec()
        config.hotkey_restore_mouse = self._cap_restore_mouse.spec()
        config.hotkey_hide_self = self._cap_self.spec()
        config.mute_on_hide = self._cb_mute.isChecked()
        config.keep_hidden = self._cb_keep.isChecked()
        config.activate_on_restore = self._cb_activate.isChecked()
        config.minimize_to_tray = self._cb_tray.isChecked()
        config.show_balloon = self._cb_balloon.isChecked()
        config.show_minimize_hint = self._cb_min_hint.isChecked()
        config.autostart = self._cb_autostart.isChecked()
        config.auto_apply_rules = self._cb_auto_rules.isChecked()
        config.force_fullscreen_hide = self._cb_force_fs.isChecked()
        config.protected_processes = [s.strip() for s in
                                      self._inp_protect.text().split(",") if s.strip()]
        config.profiles = self._profiles
        cur = self._current_profile()
        config.active_profile = cur.name if cur else ""
