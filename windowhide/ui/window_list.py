"""window_list.py - 窗口列表表格。"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView

from ..core import win32
from ..core.classify import type_label, type_color
from ..core.win32 import WindowInfo

COL_CHECK, COL_APP, COL_TITLE, COL_TYPE, COL_STATUS, COL_SOUND, COL_PID = range(7)

_STATE_LABELS = {True: "已隐藏", False: "显示中"}


@lru_cache(maxsize=512)
def _icon_by_exe(path: str) -> QIcon | None:
    """按 exe 取系统文件图标(最后一道兜底,几乎所有程序都有)。"""
    hicon = win32.file_icon_handle(path, big=True)
    if not hicon:
        return None
    try:
        image = QImage.fromHICON(int(hicon))
        return None if image.isNull() else QIcon(QPixmap.fromImage(image))
    finally:
        # 文件图标的句柄归调用方,不还就是每次刷新泄漏一个 GDI 对象
        win32.DestroyIcon(hicon)


@lru_cache(maxsize=512)
def _icon_by_hwnd(hwnd: int) -> QIcon | None:
    hicon = win32.window_icon_handle(hwnd, big=True)
    if not hicon:
        return None
    image = QImage.fromHICON(int(hicon))
    return None if image.isNull() else QIcon(QPixmap.fromImage(image))


def _icon_for_window(win: WindowInfo) -> QIcon | None:
    """尽量取窗口自身的图标;取不到就用 exe 的文件图标,再不行用类型色块。

    两级兜底是必要的:Chromium 系只设 `WM_GETICON`,老 Win32 程序只在窗口类
    上挂图标,还有些(控制台/UWP 宿主)两样都没有,只能问 shell 要文件图标。
    都取不到才画色块。
    """
    if win.hung:
        return None      # 无响应的窗口问一次要等 100ms,不值得
    try:
        icon = _icon_by_hwnd(win.hwnd)
        if icon is not None:
            return icon
        if win.process_path:
            return _icon_by_exe(win.process_path)
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
    sound_toggled = Signal(str, bool)   # (进程名, 藏了也继续响)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hwnds: list[int] = []
        self._procs: list[str] = []       # 与 _hwnds 平行:每行的进程名
        self._checked: set[int] = set()   # 勾选状态按 hwnd 保存,跨刷新保留
        self._keep_audio: set[str] = set()  # 小写进程名:藏了继续响
        # 进程名 -> (会不会静音, 为什么)。只有提示文案用,但很关键:
        # 三处设置都能改结果,不写"为什么"用户永远看不出是谁在起作用。
        self._mute_reasons: dict[str, tuple[bool, str]] = {}
        self._syncing = False             # 重建表格时为真,避免 itemChanged 干扰
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels(
            ["", "应用", "窗口标题", "类型", "状态", "声音", "PID"])
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
        header.setSectionResizeMode(COL_SOUND, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_PID, QHeaderView.Fixed)
        self.setColumnWidth(COL_CHECK, 28)
        self.setColumnWidth(COL_APP, 150)
        self.setColumnWidth(COL_TYPE, 90)
        self.setColumnWidth(COL_STATUS, 70)
        self.setColumnWidth(COL_SOUND, 110)
        self.setColumnWidth(COL_PID, 64)
        # 表头文字放不下说明,提示挂在这里
        self.horizontalHeaderItem(COL_SOUND).setToolTip(
            "勾选 = 这个程序被隐藏时不静音,声音继续放;\n"
            "不勾 = 按设置里的「隐藏时静音」决定。\n"
            "按程序生效:同一程序多开的窗口共享这一项(系统音量是按程序算的)。")
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item) -> None:
        """用户点击复选框 -> 同步进 _checked / _keep_audio,刷新后才能恢复。"""
        if self._syncing:
            return
        row = item.row()
        if not (0 <= row < len(self._hwnds)):
            return
        if item.column() == COL_CHECK:
            hwnd = self._hwnds[row]
            if item.checkState() == Qt.Checked:
                self._checked.add(hwnd)
            else:
                self._checked.discard(hwnd)
            return
        if item.column() == COL_SOUND:
            proc = (self._procs[row] if row < len(self._procs) else "").lower()
            if not proc:
                return
            keep = item.checkState() == Qt.Checked
            if keep:
                self._keep_audio.add(proc)
            else:
                self._keep_audio.discard(proc)
            item.setToolTip(self._sound_tip(proc, keep))
            # 同一程序的其它行跟着变(声音是按程序算的,不是按窗口)
            for r in range(self.rowCount()):
                if r == row or (r < len(self._procs)
                                and self._procs[r].lower() != proc):
                    continue
                other = self.item(r, COL_SOUND)
                if other is None:
                    continue
                self._syncing = True
                try:
                    other.setCheckState(Qt.Checked if keep else Qt.Unchecked)
                    other.setToolTip(self._sound_tip(proc, keep))
                finally:
                    self._syncing = False
            self.sound_toggled.emit(proc, keep)

    def _sound_tip(self, proc: str, keep: bool) -> str:
        will_mute, why = self._mute_reasons.get(
            proc.lower(), (False, "没开静音"))
        head = ("现在隐藏它:会静音" if will_mute else "现在隐藏它:不静音")
        if keep:
            return (f"{proc}\n\n{head}(「声音」列勾了「藏了继续响」)\n"
                    "按程序生效:同程序的其它窗口也一样。")
        return (f"{proc}\n\n{head}\n原因:{why}\n\n"
                "想让它在后台继续响,勾上这一格(优先级最高)。")

    def refresh(self, windows: list[WindowInfo], hidden_hwnds: set[int],
                our_pid: int, protected: set[str] | None = None,
                keep_audio: set[str] | None = None,
                mute_reasons: dict[str, tuple[bool, str]] | None = None) -> None:
        # 定时器每 1.5s 会整体重建表格;勾选状态和滚动位置必须保留,
        # 否则用户刚勾的窗口下一秒就被取消,根本没法累积选择。
        old_checked = self._checked
        scroll = self.verticalScrollBar().value()
        if keep_audio is not None:
            self._keep_audio = {p.lower() for p in keep_audio}
        if mute_reasons is not None:
            self._mute_reasons = {k.lower(): v for k, v in mute_reasons.items()}
        # 只清理**真的没了**的窗口(进程退出/窗口销毁)。窗口暂时不在列表里
        # (被搜索框过滤、异步隐藏还没落地)不算消失,勾选要留着 ——
        # 用户实测:"多按几次隐藏快捷键,勾选自己就没了,然后再按就认不出来"。
        self._checked = {h for h in self._checked if win32.IsWindow(h)}

        self._syncing = True
        try:
            self._rebuild_rows(windows, hidden_hwnds, our_pid, old_checked,
                               protected or set())
        finally:
            self._syncing = False
        self.verticalScrollBar().setValue(scroll)

    def _rebuild_rows(self, windows, hidden_hwnds, our_pid, old_checked,
                      protected: set[str]) -> None:
        self.setRowCount(0)
        self._hwnds = []
        self._procs = []
        # 注意:**不要**在这里把 _checked 重建一遍。它按 hwnd 保存,是勾选
        # 状态的唯一真相;照界面行去推导的话,"这一刻不在列表里的窗口"的
        # 勾选会被静默抹掉(异步隐藏的空窗期、搜索框过滤都会命中)。
        for win in sorted(windows, key=lambda w: (w.process_name.lower(), w.title.lower())):
            row = self.rowCount()
            self.insertRow(row)
            self._hwnds.append(win.hwnd)
            self._procs.append(win.process_name or "")
            checked = win.hwnd in self._checked

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

            # 标题(无响应的要写明,否则用户只看到标题空着,以为工具没刷新)
            if win.title:
                title_item = QTableWidgetItem(win.title)
            elif win.hung:
                title_item = QTableWidgetItem("(无响应)")
                title_item.setForeground(QColor("#94a3b8"))
            else:
                title_item = QTableWidgetItem("(无标题)")
            self.setItem(row, COL_TITLE, title_item)

            # 类型
            tkey = self._win_type(win)
            type_item = QTableWidgetItem(type_label(tkey))
            type_item.setForeground(QColor(type_color(tkey)))
            self.setItem(row, COL_TYPE, type_item)

            # 状态:已隐藏 / 显示中 / 受保护
            hidden = win.hwnd in hidden_hwnds
            is_protected = (win.process_name or "").lower() in protected
            if is_protected:
                status_item = QTableWidgetItem("受保护")
                status_item.setForeground(QColor("#94a3b8"))
                status_item.setToolTip("系统/输入关键进程,隐藏它会连带影响桌面或输入法")
            else:
                status_item = QTableWidgetItem(_STATE_LABELS[hidden])
                if hidden:
                    status_item.setForeground(QColor("#b45309"))
            self.setItem(row, COL_STATUS, status_item)

            # 声音:勾选 = 这个程序藏了也继续响(按程序,不按窗口)
            proc = (win.process_name or "").lower()
            keep = bool(proc) and proc in self._keep_audio
            snd = QTableWidgetItem()
            snd.setTextAlignment(Qt.AlignCenter)
            snd.setToolTip(self._sound_tip(proc or "(未知程序)", keep))
            if proc:
                snd.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                snd.setCheckState(Qt.Checked if keep else Qt.Unchecked)
            else:
                snd.setFlags(Qt.ItemIsEnabled)
            self.setItem(row, COL_SOUND, snd)

            # PID
            self.setItem(row, COL_PID, QTableWidgetItem(str(win.pid)))

            if is_protected:
                # 复选框保留但不可勾:让用户看见"我知道这个窗口,但不动它",
                # 比直接从列表里消失(以为工具坏了)好
                chk.setFlags(Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
                self._checked.discard(win.hwnd)

            if win.pid == our_pid:
                for col in range(7):
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
        """勾中的窗口。

        以 `_checked` 为准(而不是只看当前可见的行):窗口可能因为异步隐藏
        还没落地、或者被搜索框过滤掉而不在表里,行没了不代表用户没勾它。
        已经销毁的窗口在这里顺手剔掉。
        """
        out: list[int] = []
        seen: set[int] = set()
        for row in range(self.rowCount()):
            item = self.item(row, COL_CHECK)
            if item and item.checkState() == Qt.Checked:
                hwnd = self._hwnds[row]
                if hwnd not in seen and win32.IsWindow(hwnd):
                    seen.add(hwnd)
                    out.append(hwnd)
        for hwnd in sorted(self._checked):
            if hwnd not in seen and win32.IsWindow(hwnd):
                seen.add(hwnd)
                out.append(hwnd)
        return out

    def set_all_checked(self, checked: bool) -> None:
        if not checked:
            # "取消全选"就是一律不选 —— 连当前不在列表里的勾选一起清掉,
            # 否则会出现"点了取消全选,按隐藏还是藏了一批"的怪事
            self._checked.clear()
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
