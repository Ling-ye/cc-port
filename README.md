# LPM（LingyePluginMarketplace）

LPM 是一个面向 AI coding 资源的桌面管理工具。它以 **桌面 GUI** 作为主要使用入口，同时保留 **CLI** 和 **MCP Server**，方便自动化、脚本化和接入 AI 开发环境。

项目的核心设计是：**Python 负责核心能力，Rust/Tauri 只负责桌面外壳和系统集成，React 负责前端界面**。GUI、CLI、MCP Server 都调用同一套 Python 核心逻辑，避免业务逻辑分叉。

## 能做什么

LPM 用来管理和同步 AI coding 相关资源：

- 管理 `skill`、`mcp`、`rule`、`prompt`、`plugin` 等资源类型。
- 从 GitHub 收集第三方资源，只记录引用，不复制无关内容。
- 上传本地资源到你的私有资源仓库。
- 维护私有 `registry.yaml`，用于跨设备同步资源。
- 把资源同步安装到 Cursor、Claude Code 等配置的平台目录。
- 检查 Git、GitHub token、配置文件、私有资源仓库和平台安装状态。
- 通过 CLI 执行自动化任务。
- 通过 MCP Server 让 AI coding 工具调用 LPM 能力。
- 通过桌面 GUI 管理资源、同步安装、查看平台状态和运行健康检查。

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
  dev.*                         # 启动桌面开发模式
  build-desktop.*               # 构建最终桌面工具

dist/desktop/                   # 收集后的最终 exe/installer，可忽略
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
- Windows 构建安装包时需要可用的 Tauri Windows 打包环境。

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

启动开发模式：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\dev.ps1
```

或：

```bash
bash scripts/dev.sh
```

构建最终桌面工具：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

或：

```bash
bash scripts/build-desktop.sh
```

构建脚本会按顺序执行：

1. 检查/生成桌面图标。
2. 使用 PyInstaller 构建 `lpm-desktop-api` sidecar。
3. 执行 Tauri release build。
4. 生成平台安装包。
5. 把最终产物收集到 `dist/desktop/<target-triple>/`。

Windows 上最终通常会得到：

```text
dist/desktop/x86_64-pc-windows-msvc/
  lpm-desktop.exe
  msi/
    LPM Desktop_*.msi
  nsis/
    LPM Desktop_*-setup.exe
```

Tauri 原始输出仍保留在：

```text
desktop/src-tauri/target/release/
desktop/src-tauri/target/release/bundle/
```

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

## CLI

CLI 是自动化入口，适合脚本、CI 或高级用户使用。

```bash
lpm --help
lpm init
lpm resource init
lpm resource status
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

Python 检查：

```bash
python -m compileall -q src/lpm
ruff check src/lpm tests
pytest -q
```

前端检查：

```bash
cd desktop
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
