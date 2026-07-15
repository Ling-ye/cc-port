# LPM（LingyePluginMarketplace）

LPM 是一个面向 AI coding 资源的桌面管理工具。它以 **桌面 GUI** 作为主要使用入口，同时保留 **CLI** 和 **MCP Server**，方便自动化、脚本化和接入 AI 开发环境。

项目的核心设计是：**Python 负责核心能力，Rust/Tauri 只负责桌面外壳和系统集成，React 负责前端界面**。GUI、CLI、MCP Server 都调用同一套 Python 核心逻辑，避免业务逻辑分叉。

## 能做什么

LPM 用来管理和同步 AI coding 相关资源：

- 管理 `skill`、`mcp`、`rule`、`prompt`、`plugin` 等资源类型。
- 从 GitHub 收集第三方资源，只记录引用，不复制无关内容。
- 上传本地资源到你的私有资源仓库。
- 维护私有 `registry.yaml`，用于跨设备同步资源。
- 自动发现本机 Codex、Claude Code、Cursor、Windsurf、opencode、Gemini CLI 等 AI 工具的非敏配置资源。
- 采集 `skill`、`prompt`、`rule`、`plugin` 和 MCP server 配置，并把 MCP `env` 字面值替换为 `${SECRET_NAME}` 占位符。
- 把资源同步安装到 Cursor、Claude Code 等配置的平台目录。
- 检查 Git、GitHub token、配置文件、私有资源仓库和平台安装状态。
- 通过 CLI 执行自动化任务。
- 通过 MCP Server 让 AI coding 工具调用 LPM 能力。
- 通过桌面 GUI 管理资源、同步安装、查看平台状态和运行健康检查。
- 在桌面 GUI 的任务中心统一查看会话内异步操作的运行、成功和失败状态。

### 桌面任务反馈

桌面端会用 Toast 提示异步任务结果，并在顶栏任务中心保留本次应用会话最近的任务记录。任务历史不会跨应用重启保存；运行中的任务始终保留，已完成记录最多保留 50 条。

手动刷新、扫描、发现、计划生成和检查等只读操作失败后，可以从任务中心安全重试。安装、卸载、删除、上传、导入、应用、部署和配置保存等写操作不会提供自动重试；写操作失败后必须由用户检查当前状态并重新确认，再从原操作入口执行，避免重复产生副作用。

页面初始加载和资源变更后的自动刷新保持静默，仅在失败时显示持久错误。已进入任务中心的操作不会再重复显示顶部成功或错误横幅。

## 架构

```text
React UI
  -> Tauri invoke
  -> Rust command bridge
  -> lpm-desktop-api sidecar
  -> Python interfaces.desktop_api
  -> Python services/core

CLI
  -> Python interfaces.cli
  -> Python services/core

MCP Server
  -> Python interfaces.mcp_server
  -> Python services/core
```

Rust/Tauri 不实现业务逻辑，只负责：

- 创建桌面窗口。
- 调用随应用打包的 `lpm-desktop-api` sidecar。
- 把 JSON 请求和响应转发给前端。

Python 分层：

- `core`：模型、配置、registry、平台定义、资源识别、校验和安全处理。
- `services`：资源收集、上传、发布、同步、安装、卸载、私有资源仓库管理。
- `infrastructure`：Git、GitHub、外部命令等基础设施适配。
- `interfaces`：CLI、MCP Server、Desktop JSON API。

## 项目结构

```text
config/
  config.example.toml           # 运行时配置模板
  registry.example.yaml         # 私有资源 registry 示例

docs/
  packaging-and-deployment.md   # 桌面打包、分发、升级与回退
  specs/                        # 功能规格与验收标准

src/lpm/
  core/                         # 领域模型、配置、registry、校验、资源识别
  services/                     # 业务用例：安装、同步、发布、资源仓库等
  infrastructure/               # Git / GitHub / 外部命令适配
  interfaces/                   # CLI、MCP Server、Desktop JSON API

desktop/
  src/                          # React 前端
    app/
    api/
    components/
    features/
    styles/
    types/
  src-tauri/                    # Tauri/Rust 桌面外壳
    binaries/                   # 构建期生成的 sidecar，可忽略
    target/                     # Cargo/Tauri 构建输出，可忽略

tools/packaging/
  icons/                        # 桌面图标生成工具
  sidecar/                      # PyInstaller sidecar 打包工具

scripts/
  setup.*                       # 初始化开发/构建环境
  dev.*                         # 启动 Tauri 桌面调试环境
  build-desktop.*               # 构建桌面发布产物

desktop/dist/                   # Vite 前端静态资源，中间产物，可忽略
release/desktop/                # 对外发布的最终 exe/installer，可忽略
build/sidecar/                  # PyInstaller 中间产物，可忽略
```

## 用户使用方式

### 使用编译好的安装包

适合普通用户。

安装包内包含桌面程序和 `lpm-desktop-api` sidecar，因此用户不需要单独安装 Python，也不需要安装 LPM CLI。

用户机器需要：

- Git 可用。
- LPM 运行时配置可用。
- 如果使用 GitHub 私有资源仓库，需要 GitHub token。

安装后：

1. 确认 `git` 在系统 PATH 中可用。
2. 创建或编辑配置文件：`~/.config/lpm/config.toml`。
3. 可参考仓库中的 `config/config.example.toml`。
4. 启动桌面应用。
5. 在桌面 GUI 中运行健康检查，确认配置、Git、资源仓库和平台目录正常。

### 从源码构建最终工具

适合开发者，或希望自己编译 exe/installer 的用户。

需要安装：

- Python 3.10+
- Node.js 18+
- Rust / Cargo
- Git
- Windows 构建桌面端时还需要 Microsoft C++ Build Tools（Tauri / Rust MSVC 链接器依赖）。

Windows Tauri 构建依赖：

- Tauri 官方前置依赖说明：<https://v2.tauri.app/start/prerequisites/>
- 需要安装 Visual Studio Build Tools，并选择 **Desktop development with C++** 工作负载。
- 如果本机可用 `winget`，可以运行：

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--passive --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

- 如果构建时报 `linker link.exe not found`，说明 MSVC 链接器不可用；安装上面的 C++ Build Tools 后重新打开 PowerShell，再运行构建命令。
- Windows 10 1803 及以上 / Windows 11 通常已包含 WebView2；旧系统可能还需要按 Tauri 文档安装 WebView2 Runtime。

Rust / Cargo 安装指引：

- 官方安装页：<https://www.rust-lang.org/tools/install>
- Windows 推荐使用 `rustup` 安装器；如果本机可用 `winget`，可以运行：

```powershell
winget install --id Rustlang.Rustup -e
```

- macOS / Linux / WSL 可以运行：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

安装完成后请重新打开终端，并确认 `rustc` 和 `cargo` 可用：

```bash
rustc --version
cargo --version
```

如果命令仍然找不到，通常是 PATH 尚未生效；Windows 下检查 `%USERPROFILE%\.cargo\bin`，macOS / Linux 下检查 `~/.cargo/bin` 是否在 PATH 中。

克隆仓库：

```bash
git clone https://github.com/Ling-ye/LingyePluginMarketplace.git
cd LingyePluginMarketplace
```

Windows 初始化：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\setup.ps1
```

如果安装了 PowerShell 7，也可以使用：

```powershell
pwsh scripts/setup.ps1
```

macOS / Linux 初始化：

```bash
bash scripts/setup.sh
```

初始化脚本会做这些事：

- 检查 Python、Node.js、npm、Cargo。
- 安装 Python 包：`pip install -e ".[dev,desktop]"`。
- 安装桌面端 npm 依赖。
- 确保 Tauri 图标存在。

启动 Tauri 桌面调试环境：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\dev.ps1
```

或：

```bash
bash scripts/dev.sh
```

该脚本会先构建 `lpm-desktop-api` sidecar，再启动 Tauri dev shell。Tauri 会在内部启动 Vite 前端开发服务器、编译 Rust 桌面外壳，并打开桌面窗口。这个入口适合日常开发、联调和问题复现，不会生成最终安装包。

`scripts/dev.*` 生成和使用的主要产物路径：

```text
desktop/src-tauri/binaries/lpm-desktop-api-<target-triple>[.exe]
```

Windows x86_64 默认路径通常是：

```text
desktop/src-tauri/binaries/lpm-desktop-api-x86_64-pc-windows-msvc.exe
```

同时会保留这些调试/中间输出：

```text
build/sidecar/dist/lpm-desktop-api[.exe]       # PyInstaller 中间输出
build/sidecar/work/                            # PyInstaller 工作目录
desktop/src-tauri/target/debug/                # Tauri/Cargo dev 调试输出
```

构建桌面发布产物：

完整的环境要求、版本同步、验收矩阵、产物校验、安装方式和回退边界见 [桌面打包与部署](docs/packaging-and-deployment.md)。

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

或：

```bash
bash scripts/build-desktop.sh
```

该脚本用于生成可分发的桌面程序和安装包。它会按顺序执行：

1. 检查/生成桌面图标。
2. 使用 PyInstaller 构建 `lpm-desktop-api` sidecar。
3. 执行 Tauri release build。
4. 生成平台安装包。
5. 把最终产物收集到 `release/desktop/<target-triple>/`。

简单区分：

- `scripts/dev.*`：本地调试入口，启动的是带热更新能力的桌面开发环境。
- `scripts/check-release.*`：端到端构建验收入口，串联 Python 测试、Ruff、前端 build、sidecar build 和 Tauri build，并打印原始产物路径；当前不运行 Vitest，也不收集到 `release/desktop/`。
- `scripts/build-desktop.*`：发布构建与收集入口，输出 exe、sidecar 和安装包等最终产物；当前不运行 Python 或前端测试。

Windows 上最终通常会得到：

```text
release/desktop/x86_64-pc-windows-msvc/
  lpm-desktop.exe
  msi/
    LPM Desktop_*.msi
  nsis/
    LPM Desktop_*-setup.exe
```

中间产物和 Tauri 原始输出仍保留在：

```text
desktop/dist/                         # Vite 前端静态资源
desktop/src-tauri/target/release/     # Tauri/Cargo release 输出
desktop/src-tauri/target/release/bundle/  # Tauri 原始安装包输出
```

### 命令分层说明

源码构建和发布时，对外推荐只使用仓库根目录下的脚本：

- `scripts/setup.*`：初始化 Python、Node.js、Rust/Tauri 相关依赖。
- `scripts/dev.*`：启动完整的 Tauri 桌面调试环境，用于开发和联调。
- `scripts/check-release.*`：执行端到端构建验收：`pytest`、`ruff`、`npm run build`、sidecar build 和 `tauri build`；Vitest 需另外运行。
- `scripts/build-desktop.*`：构建并收集最终桌面可执行文件、sidecar 和安装包，不替代测试命令。

`desktop/package.json` 中的 npm 脚本属于内部步骤或专项检查：Tauri 会调用 `npm run build` 构建前端，根目录脚本会调用 `npm run tauri dev/build` 启动或打包桌面外壳，`npm run sidecar` 和 `npm run icons` 主要用于维护单个打包环节。完整构建请使用根目录脚本，不需要手动拼接这些内部命令。

## 配置

示例配置：

```text
config/config.example.toml
config/registry.example.yaml
```

真实运行时配置默认位置：

```text
~/.config/lpm/config.toml
```

Windows 下等价路径通常是：

```text
%USERPROFILE%\.config\lpm\config.toml
```

资源仓库地址格式：

- 推荐填写 GitHub HTTPS 仓库地址：`https://github.com/<owner>/<repo>`。
- 也可以填写：`https://github.com/<owner>/<repo>.git` 或 `git@github.com:<owner>/<repo>.git`。
- 桌面设置页里的检查、创建和连接资源仓库功能目前只支持 `github.com` 仓库。

常用环境变量：

- `LPM_CONFIG`：指定配置文件路径。
- `LPM_GITHUB_TOKEN`：覆盖配置文件中的 GitHub token。
- `LPM_RESOURCE_HOME`：覆盖私有资源仓库本地路径。
- `LPM_DESKTOP_API_BIN`：指定桌面 GUI 使用的 `lpm-desktop-api` 可执行文件，主要用于调试。

初始化 CLI 配置：

```bash
lpm init
```

初始化或绑定私有资源仓库：

```bash
lpm resource init
```

检查环境：

```bash
lpm doctor
```

真实 `registry.yaml` 属于用户的私有资源仓库，不属于这个工具仓库。公开仓库默认忽略真实 `registry.yaml`、`skills/`、`rules/`、`prompts/`、`mcp/`、`plugins/` 和 `.claude-plugin/`。


## 自动发现与跨电脑部署

LPM 的环境迁移主流程是：`发现 -> 预览 -> 脱敏 -> 保存 -> 同步/导出 -> 另一台电脑恢复`。

桌面端新增 **环境** 页面，用于执行这些动作：

- 发现本机已安装或已有配置目录的 AI 工具。
- 采集用户确认保存的 skills、prompts、rules、plugins 和 MCP server 配置。
- 导出离线 zip 快照。
- 推送前预览本地和远端差异，并按资源选择 local 或 incoming。
- 拉取前预览远端和本地差异，并按资源选择 local 或 incoming。
- 导入 zip 快照前预览快照和本地差异，并按资源选择 local 或 incoming。
- dry-run 预览部署计划。
- 部署到当前电脑已启用的平台目录。

CLI 对应命令：

```bash
lpm env discover
lpm env capture
lpm env capture --push
lpm env export --out ~/lpm-env-snapshot.zip
lpm env push --dry-run
lpm env push --choices choices.yaml
lpm env pull --dry-run
lpm env pull --choices choices.yaml
lpm env import ~/lpm-env-snapshot.zip --dry-run
lpm env import ~/lpm-env-snapshot.zip --choices choices.yaml
lpm env deploy --dry-run
lpm env deploy
```

choices 文件格式：

```yaml
operation: pull
source: remote
items:
  resource:cursor-skill-demo-skill: incoming
  meta:profiles/default.yaml: local
```

采集后的私人环境仓库包含：

```text
registry.yaml              # 资源索引
profiles/default.yaml      # 当前采集到的工具、路径和资源统计
secrets.example.yaml       # 需要用户自行补齐的密钥名和用途
resources/
  skills/
  prompts/
  rules/
  plugins/
  mcp/
```

安全边界：

- API key、token、cookie、OAuth session、账号缓存不应进入 `registry.yaml`、`profiles/default.yaml`、`resources/` 或 zip 快照。
- MCP `env` 的非空字面值保存为 `${ENV_NAME}` 占位符，缺失项写入 `secrets.example.yaml`。
- push、pull、snapshot import 的 apply 前会扫描选定数据源中的疑似 token-like 内容；命中时阻断写入或上传。
- zip 快照导入拒绝绝对路径、`..`、`.git/` 和 Windows drive-like 路径。
- 恢复部署先生成 plan；目标已有同名资源且没有 LPM 管理标记时进入 `conflict`，不会静默覆盖。
- 部署前会在私有资源仓库下创建 `.lpm-backups/<timestamp>/`，用于保存被更新的原文件。

## CLI

CLI 是自动化入口，适合脚本、CI 或高级用户使用。

```bash
lpm --help
lpm init
lpm resource init
lpm resource status
lpm env discover
lpm env capture
lpm env capture --push
lpm env deploy --dry-run
lpm collect <github-url>
lpm upload <local-path>
lpm list
lpm sync
lpm doctor
```

Desktop API smoke test：

```bash
lpm-desktop-api platforms {}
lpm-desktop-api summary {}
```

MCP Server：

```bash
lpm-mcp
```

## 开发检查

下面的命令用于单独检查某一层是否正常，不会收集最终桌面产物，也不是完整桌面打包入口。完整开发和发布流程仍以根目录脚本为准。

Python 检查：

```bash
python -m compileall -q src/lpm
ruff check src/lpm tests
pytest -q
```

前端检查：

```bash
cd desktop
npm test
npm run build
```

Rust/Tauri 检查：

```bash
cd desktop/src-tauri
cargo check
```

sidecar 打包检查：

```bash
python tools/packaging/sidecar/build_sidecar.py
```

运行生成的 sidecar：

```powershell
desktop\src-tauri\binaries\lpm-desktop-api-x86_64-pc-windows-msvc.exe platforms "{}"
```

## 发布说明

完整发布流程见 [桌面打包与部署](docs/packaging-and-deployment.md)。该文档包含：

- 构建入口职责和完整验收顺序。
- Windows MSI、NSIS 与便携目录的选择。
- 版本同步、产物时间与 SHA-256 校验。
- 干净目标机验收、人工升级和回退边界。
- 当前未配置代码签名、自动更新器和自动发布工作流的限制。

PyInstaller 在本项目中只是 **发布手段**，不是业务架构核心。

开发和业务代码仍然以 Python package 的形式组织在 `src/lpm` 中。发布桌面应用时，PyInstaller 负责把 `lpm.interfaces.desktop_api` 打包为 Tauri 可携带的 sidecar：

```text
desktop/src-tauri/binaries/lpm-desktop-api-<target-triple>[.exe]
```

Tauri 再把该 sidecar 打进最终安装包，使普通用户可以直接使用编译好的桌面工具。

## 安全说明

- 不要提交真实 GitHub token。
- 不要提交真实 `registry.yaml`。
- 不要提交私有资源目录。
- 私有资源仓库可能包含个人资源、内部路径、MCP 配置或敏感 metadata，默认应保持私有。

## License

MIT
