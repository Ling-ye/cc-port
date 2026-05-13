# LPM（LingyePluginMarketplace）

LPM 是一个用于管理个人 AI 编程资源的开源工具。它把常用的 Skills、MCP 配置、Rules、Prompts 和 Plugins 统一登记、同步和恢复到不同的 AI 编程平台上。

LPM 的核心设计是把「工具本身」和「个人资源」分离：

- **LPM 仓库**：公开的工具代码、文档、示例、CLI、MCP Server 和桌面 UI。
- **资源仓库**：你的私有 Git 仓库，用于保存选中的资源、`registry.yaml` 和平台相关配置。

这样可以让 LPM 保持开源，同时避免把个人资源、私有配置或敏感元数据提交到公开仓库。

## 功能特性

- 通过 GitHub URL 收集第三方开源资源，只记录引用，不复制上游项目。
- 上传本地资源到私有资源仓库，便于跨设备同步。
- 支持 `skill`、`mcp`、`rule`、`prompt`、`plugin` 五类资源。
- 支持 Cursor、Claude Code、Windsurf、Codex 等平台的安装目录配置。
- 支持在新设备上通过 `lpm sync` 恢复资源。
- 支持 MCP 环境变量占位符，避免真实密钥进入 Git。
- 提供 CLI、MCP Server、桌面端 JSON API 和 Tauri 桌面 UI。
- 桌面应用以 **Tauri sidecar** 方式自带 `lpm-ui-api`，普通用户无需安装 Python。

## 项目结构

```text
LingyePluginMarketplace/
  lpm/                    # Python 核心逻辑、CLI、MCP Server、JSON API
  desktop/                # Tauri + Rust + React + TypeScript 桌面端
    src-tauri/icons/      # 桌面图标（已 commit）
    src-tauri/binaries/   # 构建期生成的 sidecar exe（被 .gitignore 排除）
  packaging/              # 打包工具
    icons/generate_icons.py    # 占位图标生成器
    sidecar/build_sidecar.py   # PyInstaller sidecar 打包脚本
  scripts/                # 一键脚本：setup / dev / build-desktop（ps1 + sh）
  examples/               # 示例配置
  registry.example.yaml   # registry 示例
```

资源仓库默认结构：

```text
<your-resource-repo>/
  README.md
  registry.yaml
  skills/
  rules/
  prompts/
  mcp/
  plugins/
  .claude-plugin/
    plugin.json
```

如果没有显式指定资源仓库名称，LPM 默认使用 `LingyeAIResources`，本地路径为 `~/<repo_name>`。

---

## 三种使用场景

| 场景 | 适合谁 | 需要安装 |
| --- | --- | --- |
| **桌面应用（推荐）** | 终端用户、想用 GUI | 只要安装包，**无需 Python** |
| **CLI / MCP Server** | 重度命令行用户、自动化 | Python 3.10+，`pip install -e .` |
| **从源码构建桌面** | 开发者、想自己出包 | Python + Node + Rust + 一键脚本 |

---

## 场景 1：使用桌面应用（最简单）

直接下载 [Releases](https://github.com/Ling-ye/LingyePluginMarketplace/releases) 中的安装包：

- Windows：`LPM Desktop_<version>_x64-setup.exe`（NSIS）或 `LPM Desktop_<version>_x64_en-US.msi`
- macOS：`LPM Desktop_<version>_x64.dmg`
- Linux：`lpm-desktop_<version>_amd64.AppImage` / `.deb`

安装后启动 **LPM Desktop**，里面包含：

- **总览**：配置、资源仓库状态、资源数量、常用操作。
- **资源库**：查看 `registry.yaml` 中的资源、安装状态、来源和详情。
- **添加资源**：通过 GitHub URL 收集，或上传本地路径。
- **同步**：把资源同步到 Cursor / Claude Code / Windsurf / Codex 等平台。
- **检查**：环境检查（Git、Token、资源仓库、平台目录）。
- **平台**：查看各平台目录配置。

> 桌面应用以 sidecar 方式自带 `lpm-ui-api.exe`，**不需要预先安装 Python 或 LPM CLI**。
> 第一次启动会要求你登记 GitHub Token 和资源仓库地址。

---

## 场景 2：使用 CLI / MCP Server

适合习惯命令行的用户，或要在 Cursor / Claude Code 里把 LPM 接成 MCP Server。

### 基础依赖

- Python 3.10+
- Git
- 推荐：GitHub Token，用于创建、拉取或推送私有资源仓库

### 安装

```bash
git clone https://github.com/Ling-ye/LingyePluginMarketplace.git
cd LingyePluginMarketplace
pip install -e .
```

强烈建议在虚拟环境里安装：

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

### 初始化

```bash
lpm init
```

配置 GitHub Token。可以写入 `~/.config/lpm/config.toml`，也可以通过环境变量提供：

```bash
# macOS / Linux
export LPM_GITHUB_TOKEN=ghp_xxxxx
```

```powershell
# Windows PowerShell
$env:LPM_GITHUB_TOKEN = "ghp_xxxxx"
```

确认 CLI 可用：

```bash
lpm doctor
lpm platforms
```

### 常用命令

资源仓库管理：

```bash
lpm resource init --name MyAIResources
lpm resource use <path-or-git-url>
lpm resource status
lpm resource pull
lpm resource push
```

资源管理：

```bash
lpm collect <github-url-or-tree-url>
lpm upload <local-path>
lpm list
lpm status
lpm check
lpm remove <name>
```

同步和平台：

```bash
lpm sync                  # 默认只同步 skill
lpm sync --include-mcp
lpm sync --include-rule
lpm sync --include-prompt
lpm sync --include-plugin
lpm sync --all-kinds
lpm update <name>
lpm platforms
```

项目链接：

```bash
lpm link --project <project-root>
lpm unlink --project <project-root>
```

### 在新设备上恢复

```bash
git clone https://github.com/Ling-ye/LingyePluginMarketplace.git
cd LingyePluginMarketplace
pip install -e .
lpm init
lpm resource use https://github.com/<you>/MyAIResources.git
lpm resource pull
lpm sync
```

### MCP Server

LPM 同时提供 MCP Server：

```bash
lpm-mcp
```

Cursor 配置示例：

```json
{
  "mcpServers": {
    "lpm": {
      "command": "lpm-mcp"
    }
  }
}
```

Claude Code：

```bash
claude mcp add lpm -- lpm-mcp
```

---

## 场景 3：从源码构建桌面应用（开发者）

适合想修桌面 UI、出自己的安装包、或在新平台上做适配的开发者。

### 系统依赖

| 工具 | 最低版本 | 安装建议 |
| --- | --- | --- |
| Python | 3.10+ | 系统包管理器 / `winget install Python.Python.3.12` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) / `winget install OpenJS.NodeJS.LTS` |
| Rust / Cargo | 任意稳定版 | `winget install Rustlang.Rustup` 然后 `rustup default stable` |
| Git | 任意 | 系统包管理器 |
| Windows 专属 | Visual Studio 2022 含 `Desktop development with C++` + Windows 10/11 SDK |

> 安装 Rust 后请重开终端，确认 `cargo --version` 可用；
> 否则在脚本里临时补充：`$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"`。

### 一键准备环境

```powershell
# Windows
pwsh scripts\setup.ps1
# 如果没有 pwsh，用 powershell.exe 也行：
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

```bash
# macOS / Linux
bash scripts/setup.sh
```

`setup` 脚本会做：

1. 检查 Python / Node / npm / Cargo 是否就绪。
2. `pip install -e ".[dev,desktop]"`（含 PyInstaller 和 Pillow）。
3. 如果 `desktop/src-tauri/icons/` 缺少图标，自动生成占位图标。
4. `cd desktop && npm install`。

只想用 CLI、不需要桌面端：`scripts/setup.ps1 -SkipDesktop` / `bash scripts/setup.sh --skip-desktop`。

### 一键启动开发模式

```powershell
pwsh scripts\dev.ps1
```

```bash
bash scripts/dev.sh
```

它会：

1. 用 PyInstaller 构建一次 `lpm-ui-api` sidecar（按当前平台命名，写到 `desktop/src-tauri/binaries/`）。
2. 执行 `npm run tauri dev`，启动 Vite + 编译 Rust 壳，打开桌面窗口。

如果 sidecar 已经构建过，加 `-SkipSidecar` / `--skip-sidecar` 跳过这一步。

### 一键打包发布版

```powershell
pwsh scripts\build-desktop.ps1
```

```bash
bash scripts/build-desktop.sh
```

它会：

1. 检查并按需生成桌面图标。
2. 用 PyInstaller 打 sidecar 单文件 exe（约 25 MB），写入 `desktop/src-tauri/binaries/`。
3. 执行 `npm run tauri build`，Tauri 自动把 sidecar 复制到 `target/release/lpm-ui-api(.exe)`，并打到最终安装包里。

构建完成后：

```text
desktop/src-tauri/target/release/
  lpm-desktop.exe
  lpm-ui-api.exe                                 # sidecar，由 Tauri 自动 rename
  bundle/
    msi/LPM Desktop_<version>_x64_en-US.msi      # Windows MSI 安装包
    nsis/LPM Desktop_<version>_x64-setup.exe     # Windows NSIS 安装器
    dmg/LPM Desktop_<version>_x64.dmg            # macOS（在 macOS 上构建）
    appimage/lpm-desktop_<version>_amd64.AppImage  # Linux（在 Linux 上构建）
    deb/lpm-desktop_<version>_amd64.deb            # Linux（在 Linux 上构建）
```

跳过 sidecar 重建：`-SkipSidecar` / `--skip-sidecar`。

### 想细分步骤手动跑

```bash
# 只生成图标
python packaging/icons/generate_icons.py

# 只打 sidecar exe
python packaging/sidecar/build_sidecar.py
# 指定目标平台
python packaging/sidecar/build_sidecar.py --target x86_64-pc-windows-msvc

# 在 desktop/ 下用 npm 直接调
cd desktop
npm run icons          # 调 generate_icons.py
npm run sidecar        # 调 build_sidecar.py
npm run app:dev        # = sidecar + tauri dev
npm run app:build      # = sidecar + tauri build
```

### 桌面工具页面

当前桌面工具包含这些页面：

- **总览**：查看配置、资源仓库状态、资源数量和常用操作入口。
- **资源库**：查看 `registry.yaml` 中的资源、安装状态、来源和详情。
- **添加资源**：通过 GitHub URL 收集资源，或上传本地路径。
- **同步**：同步资源到启用的平台。默认只同步 `skill`，可选择同步全部类型。
- **检查**：运行环境检查，确认 Git、配置、Token、资源仓库和平台状态。
- **平台**：查看 Cursor、Claude Code、Windsurf、Codex 等平台目录配置。

### 桌面 JSON API

桌面端不会解析 CLI 表格输出，而是调用结构化 JSON API：

```bash
python -m lpm.ui_api summary
python -m lpm.ui_api list_items
python -m lpm.ui_api doctor
```

成功格式：

```json
{ "ok": true, "data": {} }
```

错误格式：

```json
{ "ok": false, "error": { "code": "ErrorName", "message": "Readable error message" } }
```

调用链：

```text
React UI
  -> Tauri invoke("lpm_action")
  -> Rust command bridge (desktop/src-tauri/src/lib.rs)
  -> lpm-ui-api sidecar (PyInstaller bundled exe)
  -> Python LPM core
```

### 桥接层 sidecar 解析顺序

Rust 桥接层按下列顺序尝试调用 `lpm-ui-api`，找到第一个能跑的为止：

1. `LPM_UI_API_BIN` 环境变量（绝对路径）。
2. 与 `lpm-desktop(.exe)` 同目录下的 `lpm-ui-api(.exe)` 或 `lpm-ui-api-{target_triple}(.exe)` —— **打包发布的安装包走这条**。
3. PATH 上的 `lpm-ui-api`。
4. `LPM_PYTHON -m lpm.ui_api`（指定 Python 解释器）。
5. PATH 上的 `python -m lpm.ui_api`。
6. PATH 上的 `python3 -m lpm.ui_api`。
7. Windows 上的 `py -3 -m lpm.ui_api`。

任何一步成功即返回；全部失败时会把每个候选的错误信息一起返回，方便定位。

---

## 资源类型识别

LPM 会尽量自动识别资源类型：

| 类型 | 识别规则 |
| --- | --- |
| `skill` | 目录包含 `SKILL.md` |
| `plugin` | 目录包含 `.claude-plugin/plugin.json` 或 `.codex-plugin/plugin.json` |
| `mcp` | 文件或目录包含 `mcp.yaml`、`mcp.yml` 或 `mcp.json` |
| `rule` | Markdown 文件，或目录名包含 `rule` / `rules` |
| `prompt` | 未识别为 rule 的 Markdown 文件 |

无法安全推断时，请使用 `--type` 显式指定。

## 配置

LPM 默认读取 `~/.config/lpm/config.toml`。常用配置如下：

```toml
[github]
token = ""
owner = ""
repo_prefix = "cursor-skill-"
default_private = false

[resources]
repo_name = "LingyeAIResources"
repo_url = ""
local_path = ""
branch = "main"

[platforms.cursor]
enabled = true
skills_dir = "~/.cursor/skills"
mcp_json = "~/.cursor/mcp.json"
rules_dir = ""
```

环境变量：

- `LPM_GITHUB_TOKEN`：覆盖配置文件中的 GitHub Token。
- `LPM_CONFIG`：指定配置文件路径。
- `LPM_RESOURCE_HOME`：覆盖资源仓库本地路径。
- `LPM_UI_API_BIN`：桌面端指定 `lpm-ui-api` 可执行文件路径。
- `LPM_PYTHON`：桌面端 sidecar 解析失败时回退使用的 Python 解释器。

---

## 常见问题

### 桌面应用启动后报 `No module named 'lpm'`

通常是从源码构建时漏跑了 sidecar 步骤，或者你直接拷了一个 `lpm-desktop.exe` 但没拷同目录的 `lpm-ui-api.exe`。

修复：

- 用 `scripts/build-desktop.ps1` / `.sh` 重新构建一次（会自动把 sidecar 一起打包）。
- 或者在桌面 exe 同目录放一个 `lpm-ui-api.exe`。
- 或者设置 `$env:LPM_UI_API_BIN` / `LPM_PYTHON` 指向可用的 sidecar / Python 解释器。

### Tauri 构建提示 `'icons/icon.ico' not found`

仓库自带占位图标；如果你删了或要换自己的图标：

```bash
python packaging/icons/generate_icons.py
```

### Cargo 提示找不到

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```

或者重开终端让 PATH 生效。

### PowerShell 5 把 PyInstaller 的 WARNING 标红

`scripts/*.ps1` 已经把 stderr 合并到 stdout 显示，不会把 WARNING 误报为脚本失败。判断成功的依据是脚本最后是否打印 `Build complete` 或 `Setup complete`。

### sidecar exe 体积有点大

约 25 MB（启用 `--exclude-module fastmcp / mcp` 后）。这是 PyInstaller 单文件包的常态：包含 Python 解释器、标准库、加密库、`PyGithub`、`PyYAML`、`pydantic`、`typer` 等依赖。如果不需要某些功能，可以在 `packaging/sidecar/build_sidecar.py` 里继续加 `--exclude-module`。

---

## 开发检查

Python 检查：

```bash
ruff check lpm
python -m compileall -q lpm
```

桌面端前端检查：

```bash
cd desktop
npm run build
```

桌面端完整构建：

```bash
pwsh scripts/build-desktop.ps1
# 或
bash scripts/build-desktop.sh
```

---

## 安全说明

- 如果资源仓库包含个人资源或元数据，请保持私有。
- 不要提交真实 Token、API Key 或其他密钥。
- MCP `env` 字段应使用 `${API_KEY}` 这类占位符。
- GitHub 认证通过 `GIT_ASKPASS` 注入 Token，避免写入 `.git/config`。
- 公开 LPM 仓库默认忽略 `registry.yaml`、`skills/`、`rules/`、`prompts/`、`mcp/`、`plugins/`、`.claude-plugin/`，以及构建产物目录 `desktop/dist/`、`desktop/src-tauri/target/`、`desktop/src-tauri/binaries/` 和 `build_sidecar/`。

## 许可证

本项目使用 MIT License。详见 [LICENSE](LICENSE)。
