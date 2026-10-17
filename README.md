# 窗口隐藏工具(WindowHide / 老板键)

一个 Windows 桌面窗口管理工具:勾选目标窗口,一键把它们从**任务栏和系统托盘**里
完全隐藏,程序继续在后台正常运行;再用**全局快捷键**或**托盘图标点击**一键恢复。

> 隐藏 = "后台化",不是关闭。目标程序的功能、下载、播放、计算全部不受影响。

## 功能

- **按多种方式选目标**
  - 窗口标题 / 进程名实时列表,支持搜索
  - 应用类型分组:浏览器 / 即时通讯 / 游戏 / 媒体播放 / 办公开发 / 系统 / 其他
  - 勾选后可「保存为目标」,重启自动重新识别(按 进程名+窗口类名 匹配)
- **彻底隐藏**
  - 窗口从任务栏按钮和 Alt+Tab 列表消失(`SW_HIDE` + `WS_EX_TOOLWINDOW`)
  - 目标程序的托盘图标也会被移除(`Shell_NotifyIcon(NIM_DELETE)`)
  - 隐藏期间自动压制新弹出的窗口(可关)
- **快速恢复**
  - 全局快捷键:同一快捷键反复切换,或分别设置"隐藏键/恢复键"
  - 另支持"隐藏当前前台窗口"快捷键(经典老板键手感)
  - 点击系统托盘图标即切换隐藏/恢复;托盘菜单可显示主界面或退出
- **可选辅助控制**
  - 隐藏时同时静音目标程序(调用系统音量合成器接口,恢复时自动取消)
- **不打扰**
  - 只改窗口显示状态,不向目标程序发送任何按键/鼠标事件
  - 关闭主界面默认最小化到托盘,不退出
  - 退出时会自动恢复所有被隐藏的窗口

## 运行环境

- Windows 10 / 11(64 位)
- Python 3.10+,依赖:`PySide6>=6.5`(见 `requirements.txt`)
- 建议以**管理员身份**运行:否则无权操作管理员权限启动的程序窗口

## 快速开始

```bash
# 1. 装依赖(任选其一)
pip install -r requirements.txt
pip install -i https://mirrors.aliyun.com/pypi/simple/ PySide6

# 2. 启动(无控制台窗口)
run.bat
# 或者
python run.pyw
```

启动后:

1. 在主界面列表里**勾选**要管理的窗口(可用搜索框/类型筛选快速定位)
2. 点「隐藏勾选」→ 勾选窗口全部后台化,列表状态变为"已隐藏"
3. 按全局快捷键(默认 `Ctrl+Alt+H` 切换)或**单击托盘图标**恢复

「保存为目标」把勾选窗口存成规则,以后启动会自动匹配同名/同类的窗口,
直接用快捷键或托盘图标切换,不用每次手动勾选。

## 目录结构

```
windowhide/
├── run.pyw / run.bat         # 入口
├── requirements.txt
├── windowhide/
│   ├── app.py                # 引导:QLocale 修复 / 单实例 / 装配
│   ├── config.py             # JSON 配置(%APPDATA%\WindowHide\config.json)
│   ├── core/                 # 与界面无关的核心能力
│   │   ├── win32.py          #   ctypes Win32 封装(枚举/样式/显隐)
│   │   ├── classify.py       #   应用类型分类
│   │   ├── targets.py        #   目标规则与匹配
│   │   ├── hide.py           #   隐藏/恢复控制器(含持续隐藏看门狗)
│   │   ├── trayhide.py       #   托盘图标跨进程移除/恢复
│   │   ├── hotkeys.py        #   全局热键(RegisterHotKey 线程)
│   │   └── audio.py          #   按进程静音(Core Audio)
│   └── ui/                   # PySide6 界面
│       ├── main_window.py    #   主窗口
│       ├── window_list.py    #   窗口表格
│       ├── settings_dialog.py#   设置(快捷键/行为/目标规则)
│       ├── hotkey_edit.py    #   快捷键捕获按钮
│       ├── tray.py           #   系统托盘
│       └── icon.py           #   程序绘制图标(无外部资源)
└── tests/
    ├── smoke_test.py         # 冒烟测试(枚举/分类/配置/托盘通路)
    ├── integration_test.py   # 真实窗口隐藏恢复 + 托盘移除恢复 + 热键模拟
    ├── audio_test.py         # 静音功能测试(播音探针 + 静音/取消/状态查询)
    ├── gui_e2e.py            # GUI 端到端(启动真实程序,按键验证隐藏恢复)
    ├── dummy_app.py          # 测试专用假目标窗口
    ├── tray_probe.py         # 托盘图标探针(测试辅助)
    ├── audio_probe.py        # 播音探针(测试辅助)
    └── screenshot.py         # 纯 ctypes 截屏(全屏/PrintWindow 窗口)
```

## 实现要点

**隐藏为什么任务栏会消失?** 两个动作叠加:`ShowWindow(SW_HIDE)` 让窗口不可见,
再给窗口加 `WS_EX_TOOLWINDOW` 扩展样式——带该样式的顶层窗口不出现在任务栏和
Alt+Tab 中。恢复时两者都会还原。即使目标程序自己调 `ShowWindow(SW_SHOW)`,
工具窗口样式也能保证它不会回到任务栏(看门狗每秒复查,可关)。

**托盘图标为什么能删掉?** 托盘图标由 `Shell_NotifyIcon(NIM_DELETE)` 删除,
该 API 内部向 explorer 的托盘工具栏发消息,不校验发送方进程,因此可以跨进程删除。
uID 未知时遍历 0..64 试探。恢复时向目标进程广播 `TaskbarCreated` 消息,
应用会按微软文档推荐的做法重新注册自己的图标。
*已知限制:以 GUID 方式注册图标的程序(部分新版 Electron/国产软件)无法从外部
获知 GUID,其托盘图标会保留,但窗口本身照样隐藏。*

**静音** 用的是系统音量合成器同一套接口(Core Audio `ISimpleAudioVolume`),
纯 ctypes 实现,只影响目标程序的音频会话。
*踩坑记录:`CLSID_MMDeviceEnumerator` 是 `{BCDE0395-E52F-467C-8E3D-C4579291692E}`,
第三段是 **467C**(见 `mmdeviceapi.h`),误记成 467E 会让 `CoCreateInstance`
返回 `REGDB_E_CLASSNOTREG`;另外 `IAudioSessionManager2::GetSessionEnumerator`
在 vtable 索引 5(前面有两个方法),`IAudioSessionControl2::GetProcessId`
在索引 14(前面有 11 个方法)。*

**热键** 独立线程建消息窗口 + `RegisterHotKey`,`MOD_NOREPEAT` 防连发;
收到 `WM_HOTKEY` 后通过 Qt 信号转主线程执行,响应延迟在毫秒级。

## 测试

```bash
python tests/smoke_test.py        # 冒烟:枚举/分类/配置/托盘通路
python tests/integration_test.py  # 集成:测试窗口隐藏恢复 + 托盘移除恢复 + 热键
python tests/audio_test.py        # 静音:播音探针静音/取消/状态查询
python tests/gui_e2e.py           # GUI 端到端:启动真实程序,自动探测空闲热键按键验证
```

实测结果(Win11,Python 3.13 + PySide6 6.12):

| 测试 | 结果 |
| --- | --- |
| 冒烟(枚举/分类/配置/托盘通路自检) | 全部通过 |
| 集成(隐藏/恢复/样式还原/托盘删除与重注册/热键触发) | 11/11 通过 |
| 静音(静音→取消→状态查询) | 全部通过 |
| GUI 端到端(热键 Ctrl+Alt+F8 切换隐藏/恢复) | 通过 |

集成测试会真实操作桌面(自动启动/关闭测试窗口与探针、模拟按键),
结束时会恢复原状,不影响正在使用的程序。

## 常见问题

- **隐藏没反应/部分窗口失败?** 目标是以管理员身份启动的程序,请用管理员身份
  运行本工具。
- **快捷键没反应?** 组合键可能被其它软件占用,在设置里换一个组合。
- **恢复后托盘图标没回来?** 少数程序不处理 `TaskbarCreated` 消息,手动重启
  该程序即可,不影响窗口隐藏/恢复本身。
- **想开机自启?** 设置 → 行为 → 勾选"开机自动启动"(写当前用户注册表 Run 项,
  不需要管理员)。
