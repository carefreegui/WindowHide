"""settings_dialog.py - 设置对话框(快捷键 / 行为 / 场景)。"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QRadioButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import Config, Profile
from ..core.protect import BUILTIN
from ..core.targets import TargetRule
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
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._hotkey_tab(), "快捷键")
        tabs.addTab(self._behavior_tab(), "行为")
        tabs.addTab(self._targets_tab(), "场景")
        layout.addWidget(tabs)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton("保存")
        ok.setDefault(True)
        cancel = QPushButton("取消")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

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

        form = QFormLayout()
        self._cap_toggle = HotkeyCaptureButton(self._config.hotkey_toggle)
        self._cap_hide = HotkeyCaptureButton(self._config.hotkey_hide)
        self._cap_restore = HotkeyCaptureButton(self._config.hotkey_restore)
        form.addRow("切换键:", self._cap_toggle)
        form.addRow("隐藏键:", self._cap_hide)
        form.addRow("恢复键:", self._cap_restore)
        self._hotkey_form = form
        v.addLayout(form)
        # 模式切换时显隐输入行:选"单一"只显示切换键,
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
                     "提示:全局快捷键在系统任意界面生效;若被其它软件占用,换一个组合即可。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #64748b;")
        ev.addWidget(tip)
        v.addWidget(extra)
        v.addStretch(1)
        return w

    def _sync_hotkey_rows(self) -> None:
        """按模式显隐输入行:单一快捷键只显示切换键,两个快捷键才显示隐藏/恢复键。"""
        split = self._mode_split.isChecked()
        self._hotkey_form.setRowVisible(self._cap_toggle, not split)
        self._hotkey_form.setRowVisible(self._cap_hide, split)
        self._hotkey_form.setRowVisible(self._cap_restore, split)

    def _behavior_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._cb_mute = QCheckBox("隐藏时同时静音目标程序(恢复时取消静音)")
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

        note = QLabel("静音功能调用系统音量合成器接口,只影响目标程序的声音,"
                      "不影响其它程序和系统音量。")
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

        self._cb_profile_mute = QCheckBox("启用该场景时顺带静音其中的程序")
        self._cb_profile_mute.toggled.connect(self._sync_profile_mute)
        v.addWidget(self._cb_profile_mute)

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
            self._cb_profile_mute.setChecked(False)
            return
        for t in p.rules:
            self._rules_list.addItem(self._rule_text(t))
        self._cb_profile_mute.setChecked(p.mute)

    def _sync_profile_mute(self, checked: bool) -> None:
        p = self._current_profile()
        if p is not None:
            p.mute = bool(checked)

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
