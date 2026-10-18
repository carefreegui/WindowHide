"""settings_dialog.py - 设置对话框(快捷键 / 行为 / 目标规则)。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QRadioButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..config import Config
from ..core.targets import TargetRule
from .hotkey_edit import HotkeyCaptureButton


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)
        self._build()

    # ---------------------------------------------------------------- UI
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._hotkey_tab(), "快捷键")
        tabs.addTab(self._behavior_tab(), "行为")
        tabs.addTab(self._targets_tab(), "目标规则")
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
        self._cb_autostart = QCheckBox("开机自动启动(写入当前用户注册表 Run 项)")

        self._cb_mute.setChecked(self._config.mute_on_hide)
        self._cb_keep.setChecked(self._config.keep_hidden)
        self._cb_activate.setChecked(self._config.activate_on_restore)
        self._cb_tray.setChecked(self._config.minimize_to_tray)
        self._cb_balloon.setChecked(self._config.show_balloon)
        self._cb_autostart.setChecked(self._config.autostart)

        for cb in (self._cb_mute, self._cb_keep, self._cb_activate,
                   self._cb_tray, self._cb_balloon, self._cb_autostart):
            v.addWidget(cb)

        note = QLabel("静音功能调用系统音量合成器接口,只影响目标程序的声音,"
                      "不影响其它程序和系统音量。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748b;")
        v.addWidget(note)
        v.addStretch(1)
        return w

    def _targets_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        info = QLabel("已保存的目标规则(重启后自动重新匹配):")
        v.addWidget(info)
        self._rules: list[TargetRule] = list(self._config.targets)
        self._rules_list = QListWidget()
        for t in self._rules:
            self._rules_list.addItem(self._rule_text(t))
        v.addWidget(self._rules_list)

        row = QHBoxLayout()
        del_btn = QPushButton("删除选中规则")
        del_btn.clicked.connect(self._delete_rule)
        row.addWidget(del_btn)
        row.addStretch(1)
        v.addLayout(row)

        tip = QLabel("在主界面勾选窗口后点「保存为目标」,即可把勾选的窗口"
                     "存成规则,下次启动自动识别。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #64748b;")
        v.addWidget(tip)
        return w

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
        for item in self._rules_list.selectedItems():
            row = self._rules_list.row(item)
            self._rules_list.takeItem(row)
            if 0 <= row < len(self._rules):
                self._rules.pop(row)

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
        config.autostart = self._cb_autostart.isChecked()
        config.targets = list(self._rules)
