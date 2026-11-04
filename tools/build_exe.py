"""build_exe.py - 打包成免 Python 的单文件 exe。

产物:`dist/WindowHide.exe`(双击就能用,不需要装 Python / PySide6)。

    python tools/build_exe.py              # 打包
    python tools/build_exe.py --clean      # 先清掉 build/ dist/ 再打包
    python tools/build_exe.py --no-trim    # 不做体积裁剪(排查疑难杂症时用)

为什么是 **onefile** 而不是目录版:
  给朋友传的时候只有一个文件,不用解释"这一堆都得带上"。代价是启动时
  要先解包到 %TEMP%(约 1 秒),而且个别杀软会把 PyInstaller 的
  bootloader 误报成病毒 —— README 里写清了。

体积裁剪:PySide6 完整装下来 150MB+,这个工具只用到 QtCore/QtGui/QtWidgets。
下面的 EXCLUDES 把明显用不到的 Qt 模块排除掉(实测能省一半)。
**排除项改动后必须真跑一次 exe 验证**,某些模块被 Qt 插件反向依赖,
排掉之后启动就崩,而打包过程是不会有任何提示的。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "windowhide" / "assets"
MANIFEST = ROOT / "tools" / "WindowHide.manifest"
ICON = ASSETS / "icon.ico"
ENTRY = ROOT / "run.pyw"
NAME = "WindowHide"

# 排除表和二进制过滤都在 WindowHide.spec 里 —— 命令行参数没法过滤 DLL,
# 而 Pyside6 的 hook 会白塞 40+ MB 用不到的 Qt 组件进来(详见 spec 顶部注释)。
SPEC = ROOT / "WindowHide.spec"


def check_env() -> None:
    """构建前的硬检查:缺一样都别开始(打包要几分钟,失败在最后才发现很烦)。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("缺 PyInstaller:.venv\\Scripts\\pip install pyinstaller")
    for path, what in ((ENTRY, "入口脚本"), (ICON, "图标"),
                       (MANIFEST, "清单"), (SPEC, "打包描述")):
        if not path.exists():
            hint = "(先跑 python tools/make_icon.py)" if path is ICON else ""
            sys.exit(f"缺{what}:{path.name} {hint}")
    # PySide6 必须在**当前解释器**里
    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.exit("当前解释器没有 PySide6,请用项目 .venv 里的 python 跑")


def build(clean: bool = False, trim: bool = True) -> int:
    if clean:
        for d in (ROOT / "build", ROOT / "dist"):
            if d.exists():
                print(f"  删除 {d.name}/")
                shutil.rmtree(d, ignore_errors=True)

    # 打包参数(onefile / windowed / 图标 / manifest / 资源)全在 spec 里
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)]
    env = dict(os.environ, WH_TRIM="1" if trim else "0")

    print("  执行:", " ".join(args[2:]), "(trim=%s)" % ("on" if trim else "off"))
    started = time.time()
    result = subprocess.run(args, cwd=str(ROOT), env=env)
    cost = time.time() - started
    if result.returncode != 0:
        print(f"\n打包失败(退出码 {result.returncode},{cost:.0f} 秒)")
        return result.returncode

    exe = ROOT / "dist" / f"{NAME}.exe"
    if not exe.exists():
        print("\n打包命令成功但没找到产物:", exe)
        return 1
    print(f"\n完成:{exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB,{cost:.0f} 秒)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把 WindowHide 打成单文件 exe")
    ap.add_argument("--clean", action="store_true", help="先删除 build/ 和 dist/")
    ap.add_argument("--no-trim", action="store_true", help="不裁剪 Qt 模块(体积大一倍)")
    ns = ap.parse_args()

    check_env()
    rc = build(clean=ns.clean, trim=not ns.no_trim)
    if rc == 0:
        print("\n下一步:直接双击 dist\\WindowHide.exe 试一次")
        print("  - 首次启动约 1~2 秒(单文件要先解包到 %TEMP%)")
        print("  - 发布到 GitHub Release 时上传这个 exe 即可")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
