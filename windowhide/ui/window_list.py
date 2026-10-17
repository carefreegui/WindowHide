"""window_list.py - 窗口列表表格。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView

from ..core.classify import type_label, type_color
from ..core.win32 import WindowInfo, WM_GETICON, SendMessageW, user32

COL_CHECK, COL_APP, COL_TITLE, COL_TYPE, COL_STATUS, COL_PID = range(6)

_STATE_LABELS = {True: "已隐藏", False: "显示中"}


def _icon_for_window(win: WindowInfo) -> QIcon | None:
    """尽量取窗口自身的图标;取不到就用类型色块。"""
    try:
        hicon = SendMessageW(win.hwnd, WM_GETICON, 1, 0) or SendMessageW(win.hwnd, WM_GETICON, 0, 0)
        if hicon:
            pix = QPixmap.fromWinHICON(int(hicon))
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
    row_activated = Signal(int)   # 双击行 -> hwnd

    def __init__(self, parent=None):
        super().__init__(parent)
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

    def refresh(self, windows: list[WindowInfo], hidden_hwnds: set[int],
                our_pid: int) -> None:
        self.setRowCount(0)
        self._hwnds: list[int] = []
        for win in sorted(windows, key=lambda w: (w.process_name.lower(), w.title.lower())):
            row = self.rowCount()
            self.insertRow(row)
            self._hwnds.append(win.hwnd)

            # 勾选
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
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
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def invert_checked(self) -> None:
        for row in range(self.rowCount()):
            item = self.item(row, COL_CHECK)
            if item and (item.flags() & Qt.ItemIsEnabled):
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def selected_hwnds(self) -> list[int]:
        return [self._hwnds[r] for r in sorted({i.row() for i in self.selectedItems()})]

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._hwnds):
            self.row_activated.emit(self._hwnds[row])
