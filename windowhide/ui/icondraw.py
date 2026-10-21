"""icondraw.py - 应用图标的**画法**(矢量绘制,不依赖任何外部素材)。

图标语义:一只**被划掉的眼睛** —— 看不见,也就是隐藏。

为什么不是"窗口 + 眼睛":窗口框 + 标题栏横线 + 闭眼弧线,在小尺寸下会被
读成一张笑脸(第一版就栽在这儿)。单个符号没有歧义,16px 也认得出。

三种用法:
  - `render(size, hidden)` 直接拿 QImage(托盘/窗口图标的兜底);
  - `write_ico(path, hidden)` 生成多尺寸 .ico(exe 图标、资源管理器预览);
  - `tools/make_icon.py` 批量导出 assets/ 下的 png/ico。
"""
from __future__ import annotations

import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
)

# 各尺寸单独画,不是缩放:小图标不这么做边缘必糊
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

# (渐变亮色, 渐变暗色, 前景色)
PALETTES = {
    # 正常态:蓝紫,浅色/深色任务栏上都够亮
    "icon": ("#6366f1", "#4338ca", "#eef2ff"),
    # 隐藏态:暖橙,和界面上"已隐藏"的琥珀色是同一套语汇
    "icon-hidden": ("#fb923c", "#c2410c", "#fff7ed"),
}


def _rounded_square(size: float) -> QRectF:
    """Windows 11 风格的贴边圆角方(留一点呼吸边)。"""
    inset = size * 0.03
    return QRectF(inset, inset, size - inset * 2, size - inset * 2)


def paint_icon(painter: QPainter, size: float, key: str = "icon") -> None:
    top, bottom, ink = PALETTES[key]
    painter.setRenderHint(QPainter.Antialiasing, True)

    # ---- 底:圆角方形 + 对角渐变 ----
    rect = _rounded_square(size)
    grad = QLinearGradient(rect.topLeft(), QPointF(rect.right(), rect.bottom()))
    grad.setColorAt(0.0, QColor(top))
    grad.setColorAt(1.0, QColor(bottom))
    painter.setPen(Qt.NoPen)
    painter.setBrush(grad)
    painter.drawRoundedRect(rect, size * 0.22, size * 0.22)

    ink_c = QColor(ink)
    # 斜杠的"镂空描边":用底色近似,视觉上等于把眼睛切开一道缝
    rim = QColor(bottom).lighter(112)

    # ---- 眼睛:实心杏仁形 ----
    # 三次贝塞尔 + 控制点靠两端 = "眼角尖、上下弧饱满";换成二次曲线就是
    # 一片叶子(试过,不像眼睛)。小尺寸下必须实心,空心轮廓会糊成一团。
    l, r = size * 0.155, size * 0.845
    cy = size * 0.500
    top_y, bot_y = size * 0.255, size * 0.745
    eye = QPainterPath(QPointF(l, cy))
    eye.cubicTo(QPointF(l + (r - l) * 0.33, top_y),
                QPointF(r - (r - l) * 0.33, top_y), QPointF(r, cy))
    eye.cubicTo(QPointF(r - (r - l) * 0.33, bot_y),
                QPointF(l + (r - l) * 0.33, bot_y), QPointF(l, cy))
    eye.closeSubpath()

    painter.setBrush(ink_c)
    painter.setPen(QPen(ink_c, size * 0.030, Qt.SolidLine, Qt.RoundCap,
                        Qt.RoundJoin))
    painter.drawPath(eye)

    # ---- 斜杠:先画一道底色描边,再压一条亮线 ----
    # 不描边的话斜杠和眼睛糊在一起,16px 下就是一团白(也和图标库里的
    # eye-slash 同款处理)。
    p1 = QPointF(size * 0.215, size * 0.775)
    p2 = QPointF(size * 0.785, size * 0.225)
    painter.setPen(QPen(rim, size * 0.140, Qt.SolidLine, Qt.FlatCap))
    painter.drawLine(p1, p2)
    painter.setPen(QPen(ink_c, size * 0.074, Qt.SolidLine, Qt.FlatCap))
    painter.drawLine(p1, p2)


def render(size: int, key: str = "icon") -> QImage:
    """渲染一张指定尺寸的图标(带超采样抗锯齿)。"""
    ss = 8 if size <= 64 else 4
    img = QImage(size * ss, size * ss, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    paint_icon(p, float(size * ss), key)
    p.end()
    return img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(path, key: str = "icon", sizes=SIZES) -> None:
    """写多尺寸 ICO(PNG 分片,Vista 起原生支持)。

    手写容器 —— 为了一张图标去引入 Pillow 不值当。
    """
    images = [(s, png_bytes(render(s, key))) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII",
                               0 if size >= 256 else size,
                               0 if size >= 256 else size,
                               0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + blobs)
