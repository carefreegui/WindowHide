"""WindowHide 入口(供 pythonw 无控制台启动)。"""
from windowhide.app import main

if __name__ == "__main__":
    raise SystemExit(main())
