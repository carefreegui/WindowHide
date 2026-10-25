# 从零发布到 GitHub(维护者手册)

> 这份文档写给"想自己把这个项目发出去/发新版本"的人。
> 命令都能直接复制,但**先看第 1 节的概念** —— 概念不清的时候,命令越敲越慌。

## 1. 先把名词对齐

| 名词 | 一句话解释 | 类比 |
| --- | --- | --- |
| **repo(仓库)** | GitHub 上的一个项目空间,存代码 + 历史 | 一个带时间机器的文件夹 |
| **commit** | 一次改动快照,有唯一 hash | 存档点 |
| **branch(分支)** | 一条独立的提交线,`main` 是主线 | 存档的一个平行世界 |
| **remote(远程)** | 本地仓库认识的"远方的仓库",通常叫 `origin` | 本地和 GitHub 之间的电话线 |
| **push** | 把本地提交传到远程 | 上传存档 |
| **tag(标签)** | 钉在某个 commit 上的固定名字,如 `v1.0.0` | 给存档贴"1.0 版"的封条 |
| **release(发行版)** | 基于某个 tag 的发布页:说明 + **可下载的二进制** | 封条 + 说明书 + 安装包 |
| **CI** | 每次 push 后,GitHub 自动开一台干净机器跑测试 | 自动质检员 |

关键区别:**tag 只是给 commit 起个名字,release 才是"给人下载的东西"**。
`git push` 只传代码;`dist/WindowHide.exe` 这类二进制**不进 git 历史**,
而是挂在 release 上 —— 所以别人 clone 下来不会拖一个 46MB 的 exe。

## 2. CI 是什么(以及我们为什么要它)

**CI = Continuous Integration,持续集成**。说人话:

> 你 push 之后,GitHub 自动开一台**全新的、干净的**电脑,
> 装上依赖、跑你的测试,然后把结果贴在 commit 旁边(绿勾 / 红叉)。

它解决的痛点:你机器上能跑,不代表别人 clone 下来能跑(少了依赖、
路径写死了、换 Python 版本就崩)。CI 那台机器就是"别人"。

我们这份 CI 在 `.github/workflows/tests.yml`,逐行解释:

```yaml
on:                       # 什么时候触发
  push:
    branches: [main]      # 往 main 推就触发
  pull_request:           # 有人提 PR 也触发
  workflow_dispatch:      # 也允许在网页上手动点一下跑

jobs:
  logic:
    runs-on: windows-latest        # 开一台 Windows 机器(项目只支持 Windows)
    strategy:
      matrix:
        python-version: ["3.10", "3.12"]   # 同一套测试跑两个 Python 版本
    steps:
      - uses: actions/checkout@v4          # 把仓库拉下来
      - uses: actions/setup-python@v5      # 装指定版本的 Python
        with: { python-version: ... }
      - run: python -m pip install -r requirements.txt   # 装依赖(PySide6)
      - run: python -m compileall -q windowhide tools tests # 语法检查全部文件
      - run: python tests/logic_test.py                    # 跑纯逻辑测试
```

**为什么只跑 `logic_test`?** 因为其余测试要"真实的交互式桌面"——
要真的创建窗口、真的隐藏、真的播音。云端的 Windows 机器没有交互式桌面会话,
那些测试在 CI 上必然 flaky(时好时坏)。硬塞进去的后果是 CI 常年红灯,
然后所有人都学会无视它 —— 那还不如不装。这是**测试分层**:

- `tests/logic_test.py` → 纯逻辑,无窗口,进 CI
- 其余(隐藏/恢复/静音/全屏/GUI)→ 本机手动跑,README 里列了清单

**免费额度**(2026 年核对):**公开仓库用标准 runner 完全免费**,不限分钟数;
私有仓库 Free 套餐每月 2,000 分钟。所以开源项目的 CI 基本不用考虑钱。

## 3. 这次发布,具体敲了什么

按真实顺序。`$` 开头的是命令。

### 3.1 发布前体检(别跳过)

```bash
git remote -v                    # 有没有已配置的远程
git status --short               # 有没有没提交的改动
git log --oneline | head -20     # 提交历史干不干净
git log --all --name-only --pretty=format: | sort -u | grep -i -E "config|log|.env|token"
                                 # 历史里有没有混进过敏感文件
git grep -n -i -E "Users[/\\]gui|D:[/\\]file"   # 代码里有没有写死个人路径
```

这一步查出了三件必须处理的事:旧截图里有个人目录名、LICENSE 署名是占位符、
配置文件在 `.gitignore` 里(没进仓库)。**发布是一次性的,发出去就收不回。**

### 3.2 认证:让 git 有权限往你的账号推

四种方式,选一种:

| 方式 | 适合 | 说明 |
| --- | --- | --- |
| **Git Credential Manager(GCM)** | Windows 上最省事 | 装 Git for Windows 时自带。第一次 push 会弹浏览器登录,之后**存在 Windows 凭据管理器里**,不再问 |
| **Personal Access Token(PAT)** | 脚本化 | 网页 Settings → Developer settings → Tokens 生成,当密码用。**只勾需要的权限**,别用 classic 全权限 |
| **`gh` CLI** | 习惯命令行 | `winget install GitHub.cli` → `gh auth login`,之后 `gh repo create` / `gh release create` 一条命令搞定 |
| **SSH key** | 长期开发 | `ssh-keygen` 生成密钥,公钥贴到 GitHub。之后用 `git@github.com:...` 地址 |

本项目这次用的是 **GCM**(机器上已有缓存,没有新建 token、没有安装任何东西)。
检查缓存里有没有凭据:

```bash
printf 'protocol=https\nhost=github.com\n\n' | git -c credential.helper=manager credential fill
# 返回 username=... 和 password=...(就是 token)
```

### 3.3 建仓库

**方式 A(推荐给你):网页点。** github.com → 右上 `+` → New repository →
填名字 → 选 Public → **不要**勾 "Add a README"(本地已经有内容,勾了会冲突)→ Create。

**方式 B(我用的):API 一条 POST。** 适合脚本化,也方便你理解"网页按钮背后是什么":

```bash
curl -X POST https://api.github.com/user/repos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d '{"name":"WindowHide","description":"...","private":false}'
```

返回 JSON 里的 `html_url` 就是新仓库地址。

### 3.4 推送代码

```bash
git remote add origin https://github.com/carefreegui/WindowHide.git
git push -u origin main
```

- `origin` 只是个**约定的名字**,你要叫 `github` 也行
- `-u`(=`--set-upstream`):把本地 `main` 和 `origin/main` **绑定**起来。
  绑定之后,以后只要敲 `git push` / `git pull`,不用再写后半截。

### 3.5 打 tag

```bash
git tag -a v1.0.0 -m "v1.0.0:首个可用版本"
git push origin v1.0.0
```

- `-a` 是"带注释的标签"(存创建者、日期、说明),轻量标签 `git tag v1.0.0` 只有个名字。
  发布用带注释的。
- **tag 不会跟着 `git push` 走**,必须单独推(或者 `git push --tags`)。
  这是新手最常踩的坑:本地打了 tag,网页上死活看不到。

### 3.6 建 Release + 传 exe

**方式 A(推荐给你):网页点。** 仓库右侧 `Releases` → `Draft a new release` →
选刚推的 tag `v1.0.0` → 填标题和说明 → **把 `dist/WindowHide.exe` 拖进附件框** →
`Publish release`。

**方式 B(我用的):API 两步。**

```bash
# 第一步:建 release(绑定 tag)
curl -X POST https://api.github.com/repos/OWNER/REPO/releases \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"tag_name":"v1.0.0","name":"v1.0.0","body":"更新说明…"}'
# 返回里有 "id" 和 "upload_url"

# 第二步:上传二进制到 uploads.github.com(注意不是 api.github.com)
curl -X POST "https://uploads.github.com/repos/OWNER/REPO/releases/<id>/assets?name=WindowHide.exe" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @dist/WindowHide.exe
```

限制(2026 年核对):**单个附件 < 2 GiB**,一个 release 最多 1000 个附件,
总大小不限。我们的 exe 46MB,一次就过。

### 3.7 验证

```bash
gh run list                       # 有 gh 就这么看
# 没有 gh:直接开 https://github.com/OWNER/REPO/actions
```

看 `Release` 页附件能不能下、README 图片是否显示、CI 是否绿。

## 4. 以后发新版本

```bash
# 1. 改代码、测试、提交
git add -A && git commit -m "修 xxx"
git push                                   # 有 -u 过一次就不用再写 origin main

# 2. 重新打包
.venv\Scripts\python tools\build_exe.py --clean
.venv\Scripts\python tests\exe_smoke_test.py

# 3. 打新 tag + 推
git tag -a v1.0.1 -m "v1.0.1:修复 xxx"
git push origin v1.0.1

# 4. 网页 Release 里选 v1.0.1、拖新的 exe 上去
```

版本号用**语义化版本**:`主.次.修` ——
不兼容的改动升主版本,加功能升次版本,只修 bug 升修版本。

## 5. 发布前检查清单

- [ ] `.gitignore` 覆盖 `dist/ build/ *.spec .venv/ .workbuddy/`(二进制和本地数据不进库)
- [ ] 历史里没有敏感文件:`git log --all --name-only | grep -i config`
- [ ] 截图/示例里没有真实姓名、目录名、公司名、token
- [ ] LICENSE 署名是你自己(不是占位符)
- [ ] README 里的图片路径能显示、链接不是死链
- [ ] CI 绿
- [ ] tag 已经**单独推送**过
- [ ] Release 附件能下下来、能运行

## 6. 常见报错对照

| 现象 | 原因 / 处理 |
| --- | --- |
| `failed to push some refs` / `rejected` | 远程有你本地没有的提交。先 `git pull --rebase`,别直接 `--force` |
| 网页上看不到 tag | tag 没推:`git push origin v1.0.0` |
| `Authentication failed` | 凭据过期。GCM 用户:清掉 Windows 凭据后重推;PAT 用户:重新生成 |
| CI 报 `UnicodeEncodeError: charmap` | Windows runner 控制台是 cp1252,打印中文会崩。设 `PYTHONUTF8=1`(本项目已设) |
| CI 报 `ModuleNotFoundError` | 忘了把依赖写进 `requirements.txt`,或 workflow 里没装 |
| Release 上传 404 | 用错了 host:`api.github.com` 建 release,`uploads.github.com` 传附件 |
| 附件超 25MB 传不上 | 那是**网页拖拽代码文件**的限制;Release 附件上限是 2 GiB,换到 Release 传 |

## 7. 一句话总结

`commit` 存档 → `push` 上传 → `tag` 贴封条 → `release` 挂安装包 → `CI` 每次自动质检。
网页点几下就能完成,命令行只是把同样的动作写下来,好处是**可重复、可脚本化**。
