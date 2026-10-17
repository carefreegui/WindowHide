"""icon.py - 用 QPainter 程序绘制应用图标(窗口 + 隐藏之眼)。

不依赖任何外部图片资源;普通态为蓝色,隐藏态为橙红色。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF


def _paint(painter: QPainter, size: float, accent: QColor, accent_dark: QColor) -> None:
    painter.setRenderHint(QPainter.Antialiasing)

    # ---- 窗口主体(圆角矩形)----
    win = QRectF(size * 0.14, size * 0.16, size * 0.72, size * 0.56)
    painter.setPen(QPen(accent, size * 0.045))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(win, size * 0.08, size * 0.08)

    # ---- 标题栏 ----
    bar = QRectF(win.left(), win.top(), win.width(), size * 0.13)
    painter.setPen(Qt.NoPen)
    painter.setBrush(accent)
    painter.drawRoundedRect(bar, size * 0.08, size * 0.08)
    # 遮掉标题栏下半圆的方角
    painter.drawRect(QRectF(bar.left(), bar.top() + bar.height() / 2,
                            bar.width(), bar.height() / 2))
    # 三个窗口按钮点
    for i in range(3):
        cx = bar.right() - size * (0.06 + i * 0.055)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(cx, bar.center().y()), size * 0.014, size * 0.014)

    # ---- 眼睛(表示"被盯着")----
    eye_c = QPointF(win.center().x(), win.center().y() + size * 0.05)
    rx, ry = size * 0.16, size * 0.085
    painter.setPen(QPen(accent_dark, size * 0.032))
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(eye_c, rx, ry)
    painter.setPen(Qt.NoPen)
    painter.setBrush(accent_dark)
    painter.drawEllipse(eye_c, size * 0.038, size * 0.038)

    # ---- 斜杠(隐藏)----
    slash = QPolygonF([
        QPointF(size * 0.80, size * 0.10),
        QPointF(size * 0.92, size * 0.10),
        QPointF(size * 0.22, size * 0.88),
        QPointF(size * 0.10, size * 0.88),
    ])
    painter.setBrush(QColor("#dc2626"))
    painter.drawPolygon(slash)


def make_icon(size: int = 64, hidden: bool = False) -> QPixmap:
    """生成应用图标。hidden=True 时为隐藏态(橙红)。"""
    ss = 4  # 超采样抗锯齿
    pix = QPixmap(size * ss, size * ss)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    s = float(size * ss)
    if hidden:
        _paint(p, s, QColor("#f97316"), QColor("#c2410c"))
    else:
        _paint(p, s, QColor("#2563eb"), QColor("#1e40af"))
    p.end()
    return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def save_icon_png(path, size: int = 256) -> None:
    make_icon(size).save(str(path), "PNG")
