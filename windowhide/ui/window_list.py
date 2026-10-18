"""window_list.py - 窗口列表表格。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView

from ..core.classify import type_label, type_color
from ..core.win32 import (
    WindowInfo, WM_GETICON, SendMessageTimeoutW, SMTO_ABORTIFHUNG,
    SEND_MSG_TIMEOUT_MS,
)

COL_CHECK, COL_APP, COL_TITLE, COL_TYPE, COL_STATUS, COL_PID = range(6)

_STATE_LABELS = {True: "已隐藏", False: "显示中"}


def _get_icon_handle(hwnd: int, size: int) -> int:
    """带超时地取窗口图标句柄(WM_GETICON 也是跨线程发送,会阻塞)。"""
    result = wintypes.ULONG(0)
    return int(SendMessageTimeoutW(hwnd, WM_GETICON, size, 0,
                                   SMTO_ABORTIFHUNG, SEND_MSG_TIMEOUT_MS,
                                   ctypes.byref(result)) or 0)


def _icon_for_window(win: WindowInfo) -> QIcon | None:
    """尽量取窗口自身的图标;取不到就用类型色块。"""
    try:
        hicon = _get_icon_handle(win.hwnd, 1) or _get_icon_handle(win.hwnd, 0)
        if hicon:
            pix = QPixmap.fromWinHICON(hicon)
            if not pix.isNull():
                return QIcon(pix)
    except Exception:
        pass
    return None


def _badge_icon(color: str) -> QIcon:
    pix = QPixmap(16, 16)
    pix.fill(Qt.transparent)
    from PySide6.QtGui import QPainter
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 12, 12, 3, 3)
    p.end()
    return QIcon(pix)


class WindowTable(QTableWidget):
    row_activated = Signal(int)   # 双击行(复选框列除外) -> hwnd

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hwnds: list[int] = []
        self._checked: set[int] = set()   # 勾选状态按 hwnd 保存,跨刷新保留
        self._syncing = False             # 重建表格时为真,避免 itemChanged 干扰
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels(["", "应用", "窗口标题", "类型", "状态", "PID"])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_APP, QHeaderView.Interactive)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_PID, QHeaderView.Fixed)
        self.setColumnWidth(COL_CHECK, 28)
        self.setColumnWidth(COL_APP, 150)
        self.setColumnWidth(COL_TYPE, 90)
        self.setColumnWidth(COL_STATUS, 70)
        self.setColumnWidth(COL_PID, 64)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item) -> None:
        """用户点击复选框 -> 同步进 _checked,刷新后才能恢复。"""
        if self._syncing or item.column() != COL_CHECK:
            return
        row = item.row()
        if not (0 <= row < len(self._hwnds)):
            return
        hwnd = self._hwnds[row]
        if item.checkState() == Qt.Checked:
            self._checked.add(hwnd)
        else:
            self._checked.discard(hwnd)

    def refresh(self, windows: list[WindowInfo], hidden_hwnds: set[int],
                our_pid: int) -> None:
        # 定时器每 1.5s 会整体重建表格;勾选状态和滚动位置必须保留,
        # 否则用户刚勾的窗口下一秒就被取消,根本没法累积选择。
        old_checked = self._checked
        scroll = self.verticalScrollBar().value()

        self._syncing = True
        try:
            self._rebuild_rows(windows, hidden_hwnds, our_pid, old_checked)
        finally:
            self._syncing = False
        self.verticalScrollBar().setValue(scroll)

    def _rebuild_rows(self, windows, hidden_hwnds, our_pid, old_checked) -> None:
        self.setRowCount(0)
        self._hwnds = []
        self._checked = set()
        for win in sorted(windows, key=lambda w: (w.process_name.lower(), w.title.lower())):
            row = self.rowCount()
            self.insertRow(row)
            self._hwnds.append(win.hwnd)
            checked = win.hwnd in old_checked
            if checked:
                self._checked.add(win.hwnd)

            # 勾选
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            chk.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, COL_CHECK, chk)

            # 应用
            app_item = QTableWidgetItem(win.process_name or f"PID {win.pid}")
            icon = _icon_for_window(win)
            if icon is None:
                icon = _badge_icon(type_color(self._win_type(win)))
            app_item.setIcon(icon)
            self.setItem(row, COL_APP, app_item)

            # 标题
            self.setItem(row, COL_TITLE, QTableWidgetItem(win.title or "(无标题)"))

            # 类型
            tkey = self._win_type(win)
            type_item = QTableWidgetItem(type_label(tkey))
            type_item.setForeground(QColor(type_color(tkey)))
            self.setItem(row, COL_TYPE, type_item)

            # 状态
            hidden = win.hwnd in hidden_hwnds
            status_item = QTableWidgetItem(_STATE_LABELS[hidden])
            if hidden:
                status_item.setForeground(QColor("#b45309"))
            self.setItem(row, COL_STATUS, status_item)

            # PID
            self.setItem(row, COL_PID, QTableWidgetItem(str(win.pid)))

            if win.pid == our_pid:
                for col in range(6):
                    item = self.item(row, col)
                    if item:
                        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                        if col == COL_CHECK and win.hwnd in self._checked:
                            # 本工具自己的窗口不允许勾选
                            self._checked.discard(win.hwnd)
                            item.setCheckState(Qt.Unchecked)

    @staticmethod
    def _win_type(win: WindowInfo) -> str:
        from ..core.classify import classify_window
        return classify_window(win)

    def checked_hwnds(self) -> list[int]:
        out = []
        for row in range(self.rowCount()):
            item = self.item(row, COL_CHECK)
            if item and item.checkState() == Qt.Checked:
                out.append(self._hwnds[row])
        return out

    def set_all_checked(self, checked: bool) -> None:
        for row in range(self.rowCount()):
            item = self.item(row, COL_CHECK)
            if item and (item.flags() & Qt.ItemIsEnabled):
                hwnd = self._hwnds[row]
                if checked:
                    self._checked.add(hwnd)
                else:
                    self._checked.discard(hwnd)
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def toggle_all_checked(self) -> None:
        """全选按钮:已全勾则取消全选,否则全选。

        曾经的 bug:按钮直接连 set_all_checked,而 QPushButton.clicked
        带的 bool 参数对不可勾选按钮恒为 False —— 点"全选"实际执行的是
        "全部取消",用户表现为"点全选没反应、点反选反而全选"。
        """
        enabled = [r for r in range(self.rowCount())
                   if self.item(r, COL_CHECK)
                   and (self.item(r, COL_CHECK).flags() & Qt.ItemIsEnabled)]
        if not enabled:
            return
        all_checked = all(self.item(r, COL_CHECK).checkState() == Qt.Checked
                          for r in enabled)
        self.set_all_checked(not all_checked)

    def invert_checked(self) -> None:
        for row in range(self.rowCount()):
            item = self.item(row, COL_CHECK)
            if item and (item.flags() & Qt.ItemIsEnabled):
                hwnd = self._hwnds[row]
                if item.checkState() == Qt.Checked:
                    self._checked.discard(hwnd)
                    item.setCheckState(Qt.Unchecked)
                else:
                    self._checked.add(hwnd)
                    item.setCheckState(Qt.Checked)

    def selected_hwnds(self) -> list[int]:
        return [self._hwnds[r] for r in sorted({i.row() for i in self.selectedItems()})]

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        # 复选框列上的双击就是勾选操作本身,不能再触发"切换隐藏"——
        # 否则快速点击两下复选框会先把窗口隐藏掉,误触严重。
        if item.column() == COL_CHECK:
            return
        row = item.row()
        if 0 <= row < len(self._hwnds):
            self.row_activated.emit(self._hwnds[row])
