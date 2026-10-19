# 窗口隐藏工具(WindowHide / 老板键)

> A Windows window-hiding tool ("boss key") written in pure Win32 ctypes + PySide6.
> Pick windows from a live list, hide them from the taskbar / Alt+Tab / tray, keep the
> programs running, and bring them back with a global hotkey. No input injection,
> no DLL hooking — only documented Win32 APIs.
> [中文说明见下](#窗口隐藏工具windowhide--老板键)

一个 Windows 桌面窗口管理工具:勾选目标窗口,一键把它们从**任务栏和系统托盘**里
完全隐藏,程序继续在后台正常运行;再用**全局快捷键**或**托盘图标点击**一键恢复。

> 隐藏 = "后台化",不是关闭。目标程序的功能、下载、播放、计算全部不受影响。

![主界面](docs/screenshot.png)

<details>
<summary>设置界面</summary>

![设置](docs/settings.png)

</details>

## 功能

- **按多种方式选目标**
  - 窗口标题 / 进程名实时列表,支持搜索
  - 应用类型分组:浏览器 / 即时通讯 / 游戏 / 媒体播放 / 办公开发 / 系统 / 其他
  - 勾选后可「保存为目标」,重启自动重新识别(按 进程名+窗口类名 匹配)
- **彻底隐藏**
  - 窗口从任务栏按钮和 Alt+Tab 列表消失(`SW_HIDE` + `WS_EX_TOOLWINDOW`)
  - 全屏窗口/游戏先最小化逼出独占全屏,再隐藏(否则画面还在屏幕上)
  - 目标程序的托盘图标也会被移除(`Shell_NotifyIcon(NIM_DELETE)`)
  - 隐藏期间自动压制新弹出的窗口(可关)
  - 藏不掉的窗口会明确报出来(托盘气泡 + 状态栏 + 日志),不静默假装成功
- **快速恢复**
  - 全局快捷键两种模式:单一快捷键反复切换(隐藏 ⇄ 恢复),或「隐藏键 / 恢复键」分离
  - 点击系统托盘图标即切换隐藏/恢复;托盘菜单可显示主界面、以管理员身份重启或退出
- **工具自藏**
  - 「隐藏本工具」快捷键/托盘菜单:把主窗口收进托盘(与点叉号等效),双击托盘图标唤回
- **可选辅助控制**
  - 隐藏时同时静音目标程序(调用系统音量合成器接口,含子孙进程,恢复时自动取消)
- **不打扰**
  - 只改窗口显示状态,不向目标程序发送任何按键/鼠标事件
  - 关闭主界面默认最小化到托盘,不退出
  - 退出时会自动恢复所有被隐藏的窗口

## 运行环境

- Windows 10 / 11(64 位)
- Python 3.10+,依赖:`PySide6>=6.5`(见 `requirements.txt`)
- **目标程序以管理员权限运行时,本工具也必须以管理员身份运行**(否则 UIPI
  会拦掉所有窗口操作)。托盘菜单里有「以管理员身份重启」一键切换。

## 快速开始

```bash
# 1. 建虚拟环境并装依赖(推荐,run.bat 会优先用它)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 国内网络慢的话换镜像
.venv\Scripts\pip install -i https://mirrors.aliyun.com/pypi/simple/ PySide6

# 2. 启动(无控制台窗口)
run.bat
# 或者
.venv\Scripts\python run.pyw
```

`run.bat` 会依次找:项目内 `.venv` → PATH 里的 `pythonw.exe` → `pyw -3`,
并且**要求候选解释器真的能 `import PySide6`**（否则 pythonw 会静默失败,
连个错都不给)。

启动后:

1. 在主界面列表里**勾选**要管理的窗口(可用搜索框/类型筛选快速定位)
2. 点「隐藏勾选」→ 勾选窗口全部后台化,列表状态变为"已隐藏"
3. 按全局快捷键(默认 `Ctrl+Alt+H` 切换)或**单击托盘图标**恢复

「保存为目标」把勾选窗口存成规则,以后启动会自动匹配同名/同类的窗口,
直接用快捷键或托盘图标切换,不用每次手动勾选。

## 目录结构

```
windowhide/
├── run.pyw / run.bat         # 入口(run.bat 自动探测带 PySide6 的解释器)
├── requirements.txt
├── LICENSE                   # MIT
├── docs/                     # 界面截图 + 路线图 + 需要真人配合的测试清单
├── .github/workflows/        # CI:纯逻辑测试(需真实桌面的不进去)
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
│   │   └── audio.py          #   按进程树静音(Core Audio,含子孙进程)
│   └── ui/                   # PySide6 界面
│       ├── main_window.py    #   主窗口
│       ├── window_list.py    #   窗口表格
│       ├── settings_dialog.py#   设置(快捷键/行为/目标规则)
│       ├── hotkey_edit.py    #   快捷键捕获按钮
│       ├── tray.py           #   系统托盘
│       └── icon.py           #   程序绘制图标(无外部资源)
└── tests/
    ├── logic_test.py         # 纯逻辑单元测试(分类/匹配/配置/热键文案,可进 CI)
    ├── smoke_test.py         # 冒烟测试(枚举/分类/配置/托盘通路)
    ├── integration_test.py   # 真实窗口隐藏恢复 + 托盘移除恢复 + 热键模拟
    ├── audio_test.py         # 静音功能测试(播音探针 + 静音/取消/状态查询)
    ├── audio_tree_test.py    # 进程树静音(父进程无会话、子进程播音,模拟浏览器)
    ├── tray_click_test.py    # 托盘点击切换链路(进程内真实 MainWindow)
    ├── checkbox_test.py      # 勾选行为回归(跨刷新保留/双击不误隐藏)
    ├── hang_test.py          # 无响应窗口不阻塞测试(挂起探针)
    ├── hotkey_rereg_test.py  # 热键重复注册回归(曾静默死亡,现有回归测试)
    ├── hide_state_test.py    # 隐藏状态持久化回归(重启/多实例不丢)
    ├── foreground_test.py    # 前台窗口隐藏 core 回归(桌面/自藏保护)
    ├── fullscreen_test.py    # 全屏窗口回归(判定/先最小化再隐藏/失败上报)
    ├── gui_e2e.py            # GUI 端到端(启动真实程序,按键验证隐藏恢复)
    ├── dummy_app.py          # 测试专用假目标窗口(含 --fullscreen 全屏模式)
    ├── hang_probe.py         # 无响应窗口探针(测试辅助)
    ├── tray_probe.py         # 托盘图标探针(测试辅助)
    ├── audio_probe.py        # 播音探针(测试辅助)
    └── screenshot.py         # 纯 ctypes 截屏(全屏/PrintWindow 窗口)
└── tools/
    ├── rescue_window.bat     # 急救:把卡在最小化的游戏窗口拉回来(自动提权)
    └── rescue_window.py      #   同上,纯 Python 实现
```

## 实现要点

**隐藏为什么任务栏会消失?** 两个动作叠加:`ShowWindow(SW_HIDE)` 让窗口不可见,
再给窗口加 `WS_EX_TOOLWINDOW` 扩展样式——带该样式的顶层窗口不出现在任务栏和
Alt+Tab 中。恢复时两者都会还原。即使目标程序自己调 `ShowWindow(SW_SHOW)`,
工具窗口样式也能保证它不会回到任务栏(看门狗每秒复查,可关)。

**全屏游戏藏不掉?独占全屏根本不看窗口可见性** 全屏游戏按隐藏,
声音静了、画面还在屏幕上。原因:独占全屏的游戏由 D3D/DXGI 直接把画面翻转
输出到显示器,`WS_VISIBLE` 与最终画面是两条路 —— 把窗口 `SW_HIDE` 掉,
窗口"看不见了",游戏却照旧渲染。
*修复:全屏窗口(无标题栏 + 铺满所在显示器)改走 **先最小化、再隐藏**:
`WM_SYSCOMMAND/SC_MINIMIZE`(和按最小化键同一条路径)*+*
`ShowWindowAsync(SW_MINIMIZE)`,等它掉出独占全屏后再 `SW_HIDE`。
判据也改成 `is_offscreen`(隐藏**或**最小化),否则全屏游戏最小化后
`IsWindowVisible` 仍是 True,会被误记成"失败"。*
*判定必须排除三类"铺满屏幕但不是游戏"的窗口,否则会误走最小化路径:
带 `WS_CAPTION` 的最大化窗口、`WS_EX_NOACTIVATE/LAYERED/NOREDIRECTIONBITMAP`
的覆盖层(实测有 `Windows 输入体验`、`NVIDIA GeForce Overlay`)、
`WS_DISABLED` 的桌面 `Progman`。附带:桌面/任务栏窗口不再出现在列表里
(`win32.is_shell_window`),断了"一键把桌面藏了"的第二次机会。*
*另外隐藏失败现在会**说出来**(托盘气泡 + 状态栏 + 日志),以前是静默
假装成功 —— 静默失败比失败本身更坑。*

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

**浏览器静不了音?因为音频不在主进程放** 现象:浏览器能隐藏但静不了音,
游戏却能静。原因:Chromium 系(Edge/Chrome)的声音由 **audio service 子进程**
播放(实测 Edge 主 pid 零会话,2 个会话全在子进程上),按主窗口 pid 找会话
当然静不了;游戏是主进程直接放音频,所以旧逻辑对游戏有效。
*修复:`set_process_mute` / `is_process_muted` 的目标从单个 pid 扩展为
**pid + 全部子孙进程**(`win32.process_descendants`,toolhelp 快照建父子表
再 BFS)。用进程树而非进程名匹配,不会误伤同名的其它浏览器实例。
回归测试 `audio_tree_test.py` 用"父进程无会话、子进程播音"确定性复现。*

**窗口枚举必须防挂起** `GetWindowTextW` / `WM_GETICON` 都是跨线程
`SendMessage`,遇到"无响应"进程的窗口会**无限期阻塞调用方** —— 曾导致
点击隐藏后整个工具卡死(栈停在 `get_window_text`)。现在统一改用
`SendMessageTimeoutW`(`SMTO_ABORTIFHUNG`,100ms 超时),挂起窗口的标题
降级为空串,不影响其它窗口;枚举时也先按 pid 过滤再取详情,
避免对全系统窗口逐个发消息。

**热键** 独立线程建消息窗口 + `RegisterHotKey`,`MOD_NOREPEAT` 防连发;收到 `WM_HOTKEY` 后通过 Qt 信号转主线程执行,响应延迟在毫秒级。
*踩坑记录(实测"改设置后快捷键没反应"):窗口类名必须按线程唯一化 ——
同进程内第二次 `RegisterClassW` 同名类必然失败,旧线程还没退出完时新线程
起不来,旧热键已注销、新热键没注册,热键整个静默死掉。现在类名带线程序号,
且线程起不来/注册失败必须写日志 + 发信号(托盘气泡提示),绝不允许静默;
`RegisterHotKey` 另带 6 次重试兜底旧线程退出超时的竞态。*

**隐藏状态持久化** 隐藏记录(pid + hwnd + 原样式)落在
`%APPDATA%\WindowHide\hidden_state.json`,每次变更即写盘;启动时逐条校验
(进程存活 / IsWindow / pid 匹配 / 当前不可见 / 带 TOOLWINDOW 样式),
不合格的丢弃。否则工具重启后"还藏着的窗口"会变成孤儿 —— 界面不显示、
热键恢复找不到目标,只能手动重启目标程序。

**`GetForegroundWindow` 在托盘菜单场景是个陷阱** 现象:从托盘菜单点
"隐藏当前前台窗口",一键把**桌面、任务栏连同所有资源管理器窗口**全藏了。
原因:菜单弹出的瞬间前台窗口变成 explorer 的 Progman / Shell_TrayWnd,
按 pid 藏进程 = 藏整个 explorer。同理,在主界面点按钮时前台是工具自己,
会把自个儿藏掉。*结论:"隐藏当前前台窗口"这个交互从托盘/主界面/快捷键
全部撤掉,改为用户真正想要的「隐藏本工具(收进托盘)」;core 的
`hide_foreground` 保留但加了桌面/任务栏/自身三重保护备用。*

**目标程序以管理员权限运行时,我们能做的极其有限(UIPI)** 现象:永劫无间
(全屏,管理员权限运行)点隐藏后"只最小化到桌面、还点不回来"。
排查结论:`OpenProcess(PROCESS_VM_READ)` 对它返回 **错误 5**,
而工具自身是普通权限 —— UIPI 把 `SetWindowLongPtrW`(加 TOOLWINDOW)、
`SetWindowPlacement`、`SetForegroundWindow` **全部静默拦掉**,只有
`ShowWindowAsync(SW_MINIMIZE)` 生效。更糟的是游戏窗口是 `WS_POPUP` 且没有
`WS_EX_APPWINDOW`:最小化之后**没有任务栏按钮、也不在 Alt+Tab 里**,
用户根本没有入口点回去。
*修复三层:*
*① **动手前先问权限**(`win32.process_is_higher_integrity`,用
`PROCESS_VM_READ` 能不能拿到判断)—— 权限不够就**完全不碰**,弹气泡说明
"请以管理员身份重启";*
*② **全有或全无**:全屏窗口"最小化成功、隐藏失败"时必须**回滚**
(`_undo_hide` 拉回原位),绝不能把窗口丢在"最小化且点不回来"的死角;*
*③ 托盘菜单加「**以管理员身份重启**」(`ShellExecuteW runas`,新进程带
`--elevated` 跳过单实例检查,旧进程 `os._exit` 直接退出以让出互斥体)。*
*恢复路径也一起加固:`SetWindowPlacement` 直接改窗口状态(游戏对
`ShowWindow(SW_SHOWNORMAL)` 可能完全没反应),抢前台改用
`AttachThreadInput` 那条路,且全程只用异步 API —— 同步 `ShowWindow`
跨进程要等目标线程,游戏主循环一卡就把工具的 UI 冻住。*

## 急救:窗口卡在"最小化、点不回来"

`tools/rescue_window.bat` 双击即可(会自己弹 UAC 提权),自动找到
游戏进程 → `SetWindowPlacement` 拉回 → `SC_RESTORE` → 抢前台 → 取消静音。
适用于本工具(或任何同类工具)把全屏游戏最小化后没恢复的场景。

## 测试

分两类。**需要真实 Windows 桌面会话的**(隐藏/恢复/静音/热键注入/全屏)在
CI 里跑不了 —— headless runner 没有交互式桌面,硬塞进去只会常年红灯 ——
所以 CI 只跑纯逻辑那部分,其余请在本机手动跑。

```bash
# 纯逻辑(无窗口、无桌面,CI 会跑):分类/匹配/配置/热键文案
python tests/logic_test.py

# 以下都需要真实桌面
python tests/smoke_test.py        # 冒烟:枚举/分类/配置/托盘通路
python tests/integration_test.py  # 集成:测试窗口隐藏恢复 + 托盘移除恢复 + 热键
python tests/audio_test.py        # 静音:播音探针静音/取消/状态查询
python tests/audio_tree_test.py   # 进程树静音:父进程无会话、子进程播音(模拟浏览器)
python tests/tray_click_test.py   # 托盘点击切换链路(进程内真实界面)
python tests/checkbox_test.py     # 勾选行为:跨刷新保留 + 双击不误隐藏 + 全选切换
python tests/hang_test.py         # 无响应窗口不阻塞(挂起探针)
python tests/hotkey_rereg_test.py # 热键重复注册:5 连改不静默死亡
python tests/hide_state_test.py   # 隐藏状态持久化:重启/多实例/过期清理
python tests/foreground_test.py   # 前台窗口隐藏 core:桌面/任务栏/自身保护
python tests/fullscreen_test.py   # 全屏窗口:判定/先最小化再隐藏/恢复/失败上报/权限预检
python tests/gui_e2e.py           # GUI 端到端:启动真实程序,自动探测空闲热键按键验证
```

实测结果(Win11,Python 3.13 + PySide6 6.12,12 个测试全绿):

| 测试 | 结果 |
| --- | --- |
| 纯逻辑(分类/匹配/配置往返/旧版迁移/脏数据/热键文案) | 36/36 通过 |
| 冒烟(枚举/分类/配置/托盘通路自检) | 全部通过 |
| 集成(隐藏/恢复/样式还原/托盘删除与重注册/热键链路) | 12/12 通过 |
| 静音(静音→取消→状态查询,连跑 3 次稳定) | 全部通过 |
| 进程树静音(父无会话/子播音,浏览器架构) | 全部通过 |
| 托盘点击切换(曾因漏导入失效,现有回归测试) | 全部通过 |
| 勾选行为(跨刷新保留/双击不误隐藏/全选切换/消失清理) | 全部通过 |
| 无响应窗口不阻塞 | 全部通过 |
| 热键重复注册(5 连改设置) | 全部通过 |
| 隐藏状态持久化(重启/多实例/死进程清理) | 全部通过 |
| 前台窗口隐藏 core(桌面/任务栏/自身保护/定向恢复) | 12/12 通过 |
| 全屏窗口(判定/先最小化再隐藏/恢复回原尺寸/失败上报/权限预检/回滚) | 25/25 通过 |
| GUI 端到端(热键 Ctrl+Alt+F8 切换隐藏/恢复) | 通过 |

集成测试会真实操作桌面(自动启动/关闭测试窗口与探针),结束时会恢复原状,
不影响正在使用的程序。热键一节分两步验证:先向热键消息窗口 post 一条
**伪造的 WM_HOTKEY** 确定性地走通"消息循环→wndproc→信号→主线程"链路;
再尝试 keybd_event 真实注入 —— 部分环境(如沙箱)会整体拦截合成输入,
此时用临时装的 **LL 键盘钩子**做 OS 层旁证:钩子没看到注入事件则记 SKIP
(环境限制,非代码回归),看到了却没触发才判 FAIL。

静音测试的两个环境坑(都已处理,非代码问题):① 探针先写 pid 再播音,
音频会话异步创建,紧接着查会拿到"0 个会话" —— 先轮询等会话出现;
② Windows 音量合成器**按应用记住静音状态**,上次测试若取消静音失败,
会给 python.exe 留下静音、新探针生来就是静音的 —— 测试先复位。
产品侧对应加固:恢复时取消静音带重试(会话可能被系统临时回收),
否则用户恢复窗口后应用会一直静着。

## 常见问题

- **隐藏没反应/部分窗口失败?** 工具会弹气泡告诉你是哪几个窗口没藏掉。
  最常见的原因是以管理员身份启动的程序(UIPI 会挡住对它的窗口操作),
  请用托盘菜单的「以管理员身份重启」。**权限不够时本工具不会动手** ——
  因为"最小化成功但隐藏失败"会把全屏游戏丢在点不回来的状态里。
- **全屏游戏还是藏不掉?** 现在的做法是先把全屏窗口最小化(让它掉出独占
  全屏)再隐藏。极少数游戏会拒绝最小化、或用自己的全屏层继续渲染 ——
  这种情况会在气泡里报"没藏掉"(并且已经把窗口还原回去),把程序名告诉我再想办法。
- **窗口卡在最小化、任务栏也找不到?** 直接双击 `tools/rescue_window.bat`
  (会弹 UAC 提权)把窗口拉回来。
- **快捷键没反应?** 组合键可能被其它软件占用,在设置里换一个组合。
- **恢复后托盘图标没回来?** 少数程序不处理 `TaskbarCreated` 消息,手动重启
  该程序即可,不影响窗口隐藏/恢复本身。
- **想开机自启?** 设置 → 行为 → 勾选"开机自动启动"(写当前用户注册表 Run 项,
  不需要管理员)。

## 开发状态与参与

- 优化路线(含实测数据与验收标准):[`docs/ROADMAP.md`](docs/ROADMAP.md)
- 需要真人配合的测试清单(游戏全屏、管理员权限、多显示器、长时间挂机):
  [`docs/MANUAL_TESTS.md`](docs/MANUAL_TESTS.md)

欢迎按测试清单里的步骤回报结果。**报问题时请附** `%APPDATA%\WindowHide\windowhide.log`
的最后几十行 + 程序名 + 工具当时是否以管理员身份运行 —— 这三样基本就能定位。

## 边界与免责

这是一个**本机窗口管理工具**,只做三件事:改目标窗口的显示状态、按进程移除
托盘图标、调系统音量合成器的静音开关。它**不注入 DLL、不挂钩子、不模拟
键盘鼠标**去操控目标程序,也不碰任何网络。

- 因为不注入,它对"独占全屏游戏""管理员权限程序"这类目标只能尽力而为,
  做不到的会在气泡里如实说,不会假装成功。
- 请只在**自己有权操作的设备**上使用。拿它偷窥、规避监护或规避审计不在
  支持范围内,也不被作者认可。
- 隐藏不等于隐身:目标程序仍在运行,任务管理器、资源监视器照样看得见。

## 许可证

[MIT](LICENSE)
