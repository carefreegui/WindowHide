# -*- mode: python ; coding: utf-8 -*-
"""WindowHide.spec - 打包描述(手工维护)。

不要直接用 `pyinstaller WindowHide.spec`,走 `python tools/build_exe.py`
(它会设好环境变量、清目录、跑验收)。

为什么要有这个 spec 文件:命令行参数**没法过滤二进制**。PySide6 的 hook 会把
Qt 安装目录里的一堆 DLL 一股脑塞进来,其中好几样这个程序一行代码都没碰过:

    opengl32sw.dll   19.7 MB   软件 OpenGL 兜底(Mesa),只有强制软件渲染才加载
    Qt6Quick/Qml     12.8 MB   QML 运行时 —— 我们只用 Widgets
    Qt6Pdf            4.4 MB   不生成 PDF
    Qt6Network        2.7 MB   本工具不联网
    Qt6OpenGL         1.9 MB   Windows 上 Widgets 走 D3D/Raster,用不到
    图片格式插件       ~2 MB    只加载 png / ico

这几项加起来 40+ MB(压缩后约 20 MB),全是白带的。`--exclude-module` 只排
Python 模块,排不掉这些 DLL —— 所以只能在 spec 里过滤 `a.binaries`。

**改这里的过滤表之后必须真跑一次 exe。** 某些 DLL 是被 Qt 插件反向依赖的,
排掉之后启动就崩,而打包过程一句提示都没有(踩过)。
"""
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
TRIM = os.environ.get("WH_TRIM", "1") != "0"

ASSETS = ROOT / "windowhide" / "assets"
ICON = ASSETS / "icon.ico"
MANIFEST = ROOT / "tools" / "WindowHide.manifest"
ENTRY = ROOT / "run.pyw"

# 用不到的 Qt 模块(Python 绑定层)。这个工具只用 QtCore/QtGui/QtWidgets。
EXCLUDES = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtNfc", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets", "PySide6.QtXml",
    # 本项目全程没用到的第三方/标准库大件
    "tkinter", "unittest", "pydoc_data", "test",
]

# 按文件名丢弃的 DLL(小写、不含路径)
DROP_BINARIES = {
    "opengl32sw.dll",          # 19.7 MB 软件 OpenGL 兜底
    "qt6pdf.dll",              # 4.4 MB
    "qt6opengl.dll",           # 1.9 MB
    "qt6network.dll",          # 2.7 MB(连同 qtnetwork.pyd)
    "qtnetwork.pyd",
    "qt6sql.dll", "qtsql.pyd",
    "qt6xml.dll", "qtxml.pyd",
    "qt6test.dll", "qttest.pyd",
    # QML / Quick 运行时(Widgets 应用不需要)
    "qt6quick.dll", "qtquick.pyd",
    "qt6quickwidgets.dll", "qtquickwidgets.pyd",
    "qt6qml.dll", "qtqml.pyd",
    "qt6qmlmodels.dll", "qt6qmlworkerscript.dll",
    "qt6qmlmeta.dll", "qt6qmllocalstorage.dll", "qt6qmlxmllistmodel.dll",
    "qt6quickcontrols2.dll", "qt6quicktemplates2.dll", "qt6quicklayouts.dll",
    "qt6quickparticles.dll", "qt6quickshapes.dll", "qt6quickdialogs2.dll",
    # TLS / 网络相关插件:本工具不联网
    "qopensslbackend.dll", "qschannelbackend.dll", "qcertonlybackend.dll",
    "qnetworklistmanager.dll", "qgenericbearer.dll", "qnativewifibearer.dll",
    # 备选平台插件(默认用 qwindows.dll)
    "qdirect2d.dll", "qoffscreen.dll", "qminimal.dll", "qtuiotouchplugin.dll",
}

# 图片格式插件只留这两个:程序里只会加载 png(界面图标)和 ico(应用图标)
KEEP_IMAGE_PLUGINS = {"qico.dll", "qpng.dll"}


def keep_binary(dest: str) -> bool:
    name = Path(dest).name.lower()
    if name in DROP_BINARIES:
        return False
    if "/imageformats/" in dest.replace("\\", "/").lower():
        return name in KEEP_IMAGE_PLUGINS
    return True


a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ASSETS), "windowhide/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES if TRIM else [],
    noarchive=False,
    optimize=0,
)

if TRIM:
    before = len(a.binaries)
    a.binaries = [b for b in a.binaries if keep_binary(b[0])]
    print(f"[trim] 二进制 {before} -> {len(a.binaries)} 个")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WindowHide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 不启用 UPX:压得下去(47MB → 约 20MB),但 UPX 壳是杀软误报的重灾区。
    # 这个工具第一次运行就被 Defender 拦下,等于劝退,省这 10MB 不值得。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON)],
    manifest=str(MANIFEST),
)
