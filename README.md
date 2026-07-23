# LPM（LingyePluginMarketplace）

LPM 是一个面向 AI coding 资源的桌面管理工具。它以 **桌面 GUI** 作为主要使用入口，同时保留 **CLI** 和 **MCP Server**，方便自动化、脚本化和接入 AI 开发环境。

桌面端固定文案支持简体中文与英文；资源描述、路径、版本和外部诊断保持原值。固定消息使用稳定语义代码与前端词典解耦，具体边界见 [桌面端国际化规格](docs/specs/desktop-i18n.md)。

项目的核心设计是：**Python 负责核心能力，Rust/Tauri 只负责桌面外壳和系统集成，React 负责前端界面**。GUI、CLI、MCP Server 都调用同一套 Python 核心逻辑，避免业务逻辑分叉。

## 能做什么

LPM 用来管理和同步 AI coding 相关资源：

- 管理 `skill`、`mcp`、`rule`、`prompt`、`plugin` 等资源类型。
- 从 GitHub 收集第三方资源，只记录引用，不复制无关内容。
- 上传本地资源到你的私有资源仓库。
- 以私有资源仓库中的 `registry.yaml` 和资源内容作为跨设备事实源。
- 在本机隐藏维护可重建的受管远端镜像，并从明确 commit 生成只读快照；AI 工具不直接链接该镜像。
- 按“资产 × 平台”比较远端快照与 AI 工具原生目录中的本地实例，逐行安装、上传、另存副本或设置平台安装别名。
- Git 隐藏承担远端快照、历史、目标并发检测和普通非强制推送；缺失资产绝不触发隐式删除。
- 为单个资源配置可选的 `platforms` 白名单，避免平台专用资源被安装到不兼容的 AI 工具。
- 自动发现本机 Codex、Claude Code、Cursor、Windsurf、opencode、Gemini CLI 等 AI 工具的非敏配置资源。
- 采集 `skill`、`prompt`、`rule`、`plugin` 和 MCP server 配置，并把 MCP `env` 字面值替换为 `${SECRET_NAME}` 占位符。
- 把资源同步安装到 Cursor、Claude Code 等配置的平台目录。
- 为目录资源和 MCP server entry 记录 LPM 所有权，避免覆盖或卸载用户手工维护的同名配置。
- 安装、卸载和环境部署统一使用持久化事务记录、集中备份、结果校验和失败回滚。
- 对相同本地目标使用跨进程路径锁，避免桌面端和 CLI 同时写入时互相覆盖快照或回滚结果。
- 分页查看跨重启保存的操作历史，按需加载目标详情，并把成功操作显式恢复到执行前状态；目标发生后续修改时默认阻断恢复。
- 预览操作历史与备份的保留策略，并在用户显式选择后批量清理；运行中操作、最近恢复点和孤立备份不会被自动删除。
- 检查孤立备份，将其导出为 ZIP 或先移入隔离区；只有隔离批次可以被再次确认后永久删除。
- 统一查看状态清理、孤立备份隔离和永久删除产生的维护审计。
- 检查 Git、GitHub token、配置文件、私有资源仓库和平台安装状态。
- 通过 CLI 执行自动化任务。
- 通过 MCP Server 让 AI coding 工具调用 LPM 能力。
- 通过桌面 GUI 管理资源并执行资产级双向同步、部署环境，并在设置页按需运行环境诊断；操作历史、恢复与本机维护能力暂时通过 CLI 或 Desktop API 使用。
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

- `core`：模型、配置、registry、内部工具适配器、资源所有权、资源识别、校验和安全处理。
- `services`：资源收集、上传、发布、Git 同步计划、事务部署、安装、卸载、私有资源仓库管理。
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
  setup.ps1                     # Windows 一键检查并安装桌面构建环境
  desktop-build.psm1            # 两个公开入口共享的 PowerShell 内部模块
  release-desktop.ps1           # Windows 桌面发布唯一入口
  setup.sh                      # 非 Windows 开发环境初始化
  dev.*                         # 启动 Tauri 桌面调试环境

desktop/dist/                   # Vite 前端静态资源，中间产物，可忽略
release/desktop/                # 对外发布的最终 exe/installer，可忽略
build/sidecar/                  # PyInstaller 中间产物，可忽略
```

本机状态与用户的私有资源 Git 仓库分离。Windows 默认使用 `%LOCALAPPDATA%\LPM`，其他系统使用用户状态目录；可通过 `LPM_STATE_HOME` 覆盖。该目录保存：

```text
backups/                       # 安装、卸载、部署和恢复备份
backups/mcp/<path-hash>/       # mcp.json 写入前备份：<UTC时间戳>-<4位序号>-<原文件名>
exports/orphans/               # 用户显式导出的孤立备份 ZIP
locks/                         # 跨进程目标路径锁载体
maintenance/*.json             # 状态清理和孤立备份维护审计
maintenance/orphans/           # 等待二次确认的孤立备份隔离批次
maintenance/trash/             # 状态清理失败暂存
operations/                    # 持久化本地写操作历史
ownership/mcp.json             # MCP server entry 所有权
assets/remotes/                # 隐藏的远端传输缓存
assets/snapshots/              # 按提交生成的只读远端快照
asset-plans/<operation-id>/    # 资产级写计划与结果
sync/<operation-id>/           # 弃用兼容：旧 Git 同步计划与临时 worktree
```

更完整的模块边界、同步状态机和后续范围见 [项目架构](docs/architecture.md)；行为规格位于 [docs/specs](docs/specs)。

## 用户使用方式

### 使用编译好的安装包

适合普通用户。

安装包内包含桌面程序和 `lpm-desktop-api` sidecar，因此用户不需要单独安装 Python，也不需要安装 LPM CLI。

用户机器需要：

- Git for Windows 与 Git Credential Manager 可用；LPM 会搜索配置路径、系统 PATH 和常见安装目录。
- LPM 运行时配置可用。
- 桌面仓库绑定要求 GCM 已配置为 `credential.helper`；CLI/MCP 仍可使用 SSH Key 或 GitHub Token。

安装后：

1. 安装包含 GCM 的 Git for Windows；通常无需手工配置 PATH，非标准位置通过 `config.toml` 的 `[git].executable` 或 `LPM_GIT_EXECUTABLE` 指定。
2. 用户先在 GitHub 创建仓库，再启动桌面应用，在设置页粘贴完整 HTTPS 仓库地址并点击“连接并验证仓库”；首次需要凭据时由 GCM 打开登录。
3. 高级值需要时直接编辑 `~/.config/lpm/config.toml`；可参考 `config/config.example.toml`。
4. 需要排障时，在桌面设置页展开“诊断”并运行检查，确认配置、Git、资源仓库和平台目录正常。

### 从源码构建最终工具

[KNOWN] 桌面发布构建正式支持 Windows 10/11 x64 和 Windows PowerShell 5.1；构建机只需预装 WinGet 并能够联网。置信度：HIGH。

[KNOWN] Python、Node.js、Git、Rust 和 Visual Studio C++ Build Tools 由 PowerShell 环境脚本检查并按需安装；用户不需要手工执行 Python 命令。置信度：HIGH。

[KNOWN] 如果 `winget` 不存在，先按微软文档安装或修复 App Installer：<https://learn.microsoft.com/windows/package-manager/winget/>。置信度：HIGH。

克隆仓库：

```bash
git clone https://github.com/Ling-ye/LingyePluginMarketplace.git
cd LingyePluginMarketplace
```

[KNOWN] 一键检查并准备完整构建环境：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1
```

[KNOWN] 默认准备流程会验证 `build/cache/dependencies.json` 中的 schema、输入指纹、工具版本与已安装依赖；全部匹配时复用现有 `.venv` 和 `desktop/node_modules`，任一验证失败时重新执行 pip 与 npm 同步。置信度：HIGH。

[KNOWN] 需要无条件重新同步 Python 与前端依赖时使用：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -ForceSync
```

[KNOWN] 只检查、不安装、不修改 `.venv`、`desktop/node_modules` 或构建缓存记录：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -CheckOnly
```

[KNOWN] 默认安装模式先汇总所有操作并确认一次；显式传入 `-NonInteractive` 才跳过确认。置信度：HIGH。

[KNOWN] 启动 Tauri 桌面调试环境：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\dev.ps1
```

[KNOWN] 调试脚本会先构建 `lpm-desktop-api` sidecar，再启动 Tauri dev shell；它不会生成最终安装包。置信度：HIGH。

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

[KNOWN] 一键构建并验证桌面发布产物；该命令会自动执行环境准备，不要求先单独运行 `setup.ps1`。置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
```

[KNOWN] 该默认发布命令始终以非交互模式准备环境，不读取 `y/n` 输入；缺失工具安装、依赖同步及协议接受按已展示的操作自动授权。`-NonInteractive` 参数仅为旧命令兼容而保留。置信度：HIGH。

[KNOWN] 默认发布先验证依赖与 sidecar 缓存；只有缓存记录、输入指纹、工具链身份和产物验证全部匹配时才复用，缺失、损坏或过期时自动重建。需要忽略两类缓存并强制重新同步或重建时使用：置信度：HIGH。

[KNOWN] 默认入口先做廉价依赖缓存预判，真正复用前再重新计算输入并执行唯一一次完整 Python/npm 环境探针；`setup.ps1 -CheckOnly` 直接执行完整探针。置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1 -Clean
```

[KNOWN] `-Clean` 会强制同步依赖并完整重建 PyInstaller sidecar，但不会执行 `cargo clean`，因此仍保留 Cargo/Tauri 编译缓存。置信度：HIGH。

[KNOWN] 依赖缓存会在依赖同步及后置探针成功后刷新，sidecar 缓存会在 clean 重建及隔离冒烟成功后刷新；两者都不等待整个 Tauri 发布成功。置信度：HIGH。

[KNOWN] 唯一一次 Vite 生产构建先写入隔离目录；生成内容未变化时保留 `desktop/dist` 的现有文件与时间戳，避免无修改暖构建无谓触发 Cargo 主程序重新链接。置信度：HIGH。

[KNOWN] 发布命令按顺序执行：置信度：HIGH。

1. 检查或安装 Windows 构建工具，验证依赖缓存；缓存失效或指定 `-Clean` 时同步锁定依赖。
2. 无论是否命中缓存，都运行 PowerShell 自测、pytest、Ruff、Vitest、锁文件安全审计和一次前端生产构建。
3. 验证并按需复用或重建 PyInstaller sidecar，把验证通过的源 sidecar 显式复制到 Tauri `target/release` 并校验 SHA-256。
4. 构建 Tauri MSI 和 NSIS 安装包，并再次验证 Tauri 目标 sidecar 与源 sidecar 的 SHA-256。
5. 在临时目录验证产物、sidecar JSON API 与全部 SHA-256，全部成功后才事务式切换上一次正式产物。
6. 输出每一步的耗时与缓存命中状态，并写入 `build/metrics/` 下的 JSON 指标文件。
7. 输出正式产物的绝对路径、大小和 SHA-256。

[KNOWN] Windows x64 最终产物结构为：置信度：HIGH。

```text
release/desktop/x86_64-pc-windows-msvc/
  lpm-desktop.exe
  lpm-desktop-api.exe
  msi/
    LPM Desktop_*.msi
  nsis/
    LPM Desktop_*-setup.exe
```

[KNOWN] 中间产物和 Tauri 原始输出保留在：置信度：HIGH。

```text
desktop/dist/                         # Vite 前端静态资源
desktop/src-tauri/target/release/     # Tauri/Cargo release 输出
desktop/src-tauri/target/release/bundle/  # Tauri 原始安装包输出
```

[KNOWN] 构建缓存与指标文件保留在：置信度：HIGH。

```text
build/cache/dependencies.json             # schemaVersion=1，依赖验证缓存
build/cache/sidecar.json                  # schemaVersion=1，sidecar 验证缓存
build/cache/release.lock                  # 同一仓库发布进程的独占锁载体
build/metrics/release-<UTC时间>-<8位runId>.json
```

[KNOWN] 同一仓库同一时间只允许一个 `release-desktop.ps1` 进程；冲突发布立即失败而不等待。锁文件会保留，进程退出时释放的是独占句柄。置信度：HIGH。

[KNOWN] 正式发布锁不覆盖手工执行的 `npm run build`；不要在发布期间另行运行它，也不要并发运行多个独立前端生产构建。置信度：HIGH。

[KNOWN] 发布指标 JSON 的顶层结构为 `{ "schemaVersion": 1, "value": { ... } }`；发布状态、错误、阶段和产物字段路径分别为 `value.success`、`value.error`、`value.phases` 和 `value.artifacts`。置信度：HIGH。

[KNOWN] 30% 性能验收在同一提交、机器和工具版本下，对优化前与优化后各执行三次成功的无修改默认暖构建，取 `value.durationMs` 中位数，并要求 `candidateMedian <= baselineMedian * 0.70`；其他修改与 `-Clean` 场景只单独记录。置信度：HIGH。

[KNOWN] `desktop/package.json` 中的 npm 命令和 `tools/packaging/` 下的 Python 文件都是内部构建步骤；完整发布不要手工拼接这些命令。置信度：HIGH。

[KNOWN] 更完整的环境规则、验收门禁、产物校验和故障处理见 [桌面打包与部署](docs/packaging-and-deployment.md)。置信度：HIGH。

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

- 桌面端必须填写完整 GitHub HTTPS 仓库根地址：`https://github.com/<owner>/<repo>`；`.git` 后缀可选。
- `https://github.com/<owner>` 用户/组织主页、SSH 地址、tree/issue/文件子路径和带凭据地址会被桌面端拒绝。
- CLI/MCP 继续兼容 HTTPS、SSH 与环境 Token。

桌面端直接在“设置 -> 连接资源仓库”粘贴链接并点击“连接并验证仓库”：

- 设置页会先检查 Git、Git Credential Manager（GCM）和 `credential.helper`；缺失时显示原因与 [Git for Windows](https://git-scm.com/download/win) / [GCM 安装说明](https://github.com/git-ecosystem/git-credential-manager/blob/main/docs/install.md)入口，不修改全局 Git 配置。
- HTTPS 缺少凭据时，GCM 可以打开一次 GitHub 浏览器登录，并把凭据保存到系统凭据库。
- 绑定只执行远端引用读取和 `push --dry-run` 权限探测，不 clone、pull、fetch、commit 或实际 push。
- 绑定成功后显示“下一次远端刷新生效”；绑定本身不下载资源，也不创建用户需要维护的本地仓库。
- 后续“刷新远端”只更新 LPM 隐藏维护的受管镜像和 commit 只读快照，不调用旧 `resource_pull`，也不写 AI 工具目录。
- `[resources].credential_mode` 可选 `native`、`auto` 或 `token`；一键绑定使用 `native`，旧配置默认按 `auto` 兼容。

设置页只编辑资源仓库绑定和五个目标工具开关；不显示 GitHub 账号、OAuth、Token、仓库创建、整仓删除或可见性修改。Owner、分支、凭据模式、Git 可执行文件、仓库前缀与状态保留策略继续由内部规则、`config.toml` 和 CLI 管理。资源仓库 URL 中的 Owner 在绑定后优先于旧 `[github].owner`；未绑定时旧字段继续兼容。旧 `local_path` 与用户工作区只保留兼容，不会因设置页保存而被重置，也不进入新的桌面资源流程。

桌面侧栏将资源类型、桌面功能和项目信息统一放在单一“说明”页，不再提供独立“关于”页。

桌面端的 GitHub 边界：

- 用户自行在 GitHub 创建仓库、删除仓库和修改可见性。
- 应用只提交和同步仓库内容，不调用 GitHub 仓库管理 API，也不保存 GCM 凭据。
- 首次绑定允许 GCM 交互；后台刷新与 pull/push 禁止突然弹出登录窗口。
- 凭据失效时后台操作返回需要重新登录；回到设置页再次点击“连接并验证仓库”即可重新触发 GCM。
- 用户取消登录、无写权限、Git/GCM 缺失或未配置以及网络超时都会显示独立错误，且失败不会保存绑定。

桌面端不创建仓库；用户应先在 GitHub 创建仓库，再粘贴完整 HTTPS 地址。全新配置使用 `main` 分支、本机 Git/GCM 凭据，并启用 Codex、Claude Code、Cursor、Windsurf、opencode 五个完整平台预设。绑定后以资源仓库 URL 中的 Owner 为准；未绑定的现有配置继续保留原 Owner、平台开关、目录和高级值。Cline 与 Gemini CLI 目前不提供完整可写平台预设。

常用环境变量：

- `LPM_CONFIG`：指定配置文件路径。
- `LPM_GITHUB_TOKEN`：覆盖配置文件中的 GitHub token。
- `LPM_GIT_EXECUTABLE`：覆盖 `[git].executable`，指定 Git 可执行文件。
- 桌面后台 Git 操作复用已缓存的 GCM 凭据并保持非交互；只有用户显式点击绑定时允许浏览器登录。
- `LPM_RESOURCE_HOME`：覆盖旧兼容工作区路径；新的桌面资源流程不把该路径作为资源中枢。
- `LPM_DESKTOP_API_BIN`：指定桌面 GUI 使用的 `lpm-desktop-api` 可执行文件，主要用于调试。

`[git].executable` 可填写 Git 的绝对路径或命令名；留空时按 PATH、常见系统安装目录和应用邻近目录自动发现。

`[state]` 配置控制本机写锁和清理默认值：

- `lock_timeout_seconds`：等待另一个 LPM 进程释放相同目标的秒数，默认 `10`。
- `retention_days`：已结束操作的保留期，默认 `90` 天。
- `keep_latest_operations`：无论年龄都保护的最近操作数量，默认 `20`。
- `max_backup_mb`：备份容量软上限，默认 `2048` MiB；`0` 表示不按容量选择候选。
- 清理不会自动执行；桌面端和 CLI 都必须先预览，再显式确认候选操作。

初始化 CLI 配置：

```bash
lpm init
```

`lpm init` 对全新配置启用五个完整平台预设；旧配置缺少 `[platforms]` 时仍按历史语义只启用 Cursor。

初始化或绑定私有资源仓库：

```bash
lpm resource init
```

检查环境：

```bash
lpm doctor
```

真实 `registry.yaml` 属于用户的私有资源仓库，不属于这个工具仓库。公开仓库默认忽略真实 `registry.yaml`、`skills/`、`rules/`、`prompts/`、`mcp/`、`plugins/` 和 `.claude-plugin/`。

当前 registry 版本为 v7。资源唯一键仍为 `kind:name`；插件新增 content/reference 双轨规格。v6 插件条目不会被猜测或重分类，仍按旧内容语义兼容读取。


## 统一资源清单与批量同步

桌面端默认打开 **资源** 页面。侧栏不再提供独立“概览”和“添加资源”页；资源页是收集、
导入、本地发现、双端比较与同步的唯一入口：

- 顶栏显示当前页面名称，只保留任务中心和语言切换；资源页不再使用总括性的“添加资源”按钮。
- 桌面窗口默认使用 `1360×820`，最小尺寸为 `1280×720`；侧栏固定为 `220px`，资源清单吸收主要的宽屏新增空间，详情栏保持在 `320px` 至 `420px`。
- 资源表和详情在视口剩余高度内独立滚动，窗口外层不再出现重复滚动条；设置页和说明页保留各自的单一内容滚动区。
- 资源页并排状态卡分别展示远端仓库、分支、短 commit、上次检查时间与在线/缓存状态，以及本地扫描发现的工具数和实例数。
- 两张来源状态卡下方提供紧凑的“收集与导入”操作区，以“从 GitHub 收集”为主入口、“导入本地目录”为次入口；两者分别打开专用弹窗，不再切换模式。
- 应用启动静默刷新远端一次，停留期间不轮询；手动“刷新远端”会报告已是最新、获取到新版本或正在显示只读缓存。
- “刷新远端”只更新受管镜像和远端快照；“扫描本地”只观察用户选择的全局环境与项目，不 fetch 或写远端。
- 本次会话扫描过本地后，再刷新远端会复用完全相同的全局与项目范围重扫，避免本地独有资源从清单消失；扫描范围不会跨应用重启保存。
- 支持按名称、`kind:name` 和描述搜索；类型与总体状态筛选常驻，本地状态、远端状态和工具位于“更多筛选”。
- GitHub 收集保存锁定到当前提交的外部引用；本地导入保存任意本地目录的内容，目录既可手工粘贴，也可通过系统目录选择器填写。
- 桌面端不提供手工插件引用表单；扫描后的上传计划仍可选择“保存引用”，后端 API 与 CLI 的手工登记能力继续保留。
- 仓库未配置、远端不可用、存在遗留写入阻断、清单尚未加载或正在刷新时，收集与导入入口保持可见但禁用，并显示配置或刷新引导。
- 收集和导入默认推送私有资源仓库，用户可在提交前关闭“完成后推送”；成功后清单刷新并定位新增资源。
- 每一行表示一个 `kind:name` 逻辑资源，清单是 commit 只读远端快照和 AI 工具原生本地实例的并集。
- “扫描本地”弹窗显式选择全局环境和已保存项目；项目目录由用户添加，无 Git remote 的项目只观察、不上传。
- 插件采用双轨管理：用户确认拥有的源码上传内容；Codex/Claude marketplace、opencode npm 和 managed 插件只保存引用、版本策略、作用域与启用状态。
- Codex 的 `openai-bundled/chrome` 等 marketplace 插件从配置与版本清单识别为外部引用；扫描到的 cache 路径只用于观测，绝不展示或上传为源码。
- Codex 和 Claude 的版本化 cache 永不作为上传源；第三方引用在目标机器缺失时给出安全安装指引，不复制缓存。
- 清单使用“选择、资源、描述、状态”四列，状态保留精确总体结论，描述最多显示两行；完整描述与双端状态在详情中展示。
- 详情栏优先显示警告和差异，再列出远端提交、路径和每个本地实例的工具、安装名、路径、所有权、内容状态与阻断原因；危险操作位于底部。
- 同一资源的相同本地副本折叠为一个逻辑资源；内容不同的多个实例保留，并在上传计划中要求选择来源。
- 表头全选只选择当前可见资源，跨筛选选择继续保留；上下文操作栏显示总选中数和被筛选隐藏数。
- 只有勾选资源后才显示“上传到仓库”和“安装到工具”；安装前选择一个或多个已启用 AI 工具。
- 上传和安装都先生成服务端差异计划，按新增、覆盖、重命名、不变、跳过和阻断展示，冲突未解决时不能执行。
- 安装使用路径锁、备份、验证和失败回滚；未管理目标必须显式确认覆盖。
- 批量安装以“资源 × 工具”为独立本地事务，单项失败不回滚其他成功项；批量上传把有效项合并为一次远端提交。
- 远端提交变化但目标资产未变化时，操作会重放到最新提交；目标新增、删除或改变时返回 `stale-target`。
- external 和无私库 `path` 的 owned 引用在同步页只读；已有平台内容可以显式另存到私库。
- 远端不可用时最近成功的缓存只供查看，依赖最新远端验证的写入继续阻断。
- 一端缺失只表示可上传或安装；卸载和删除始终使用独立入口。资源页不提供仓库级“一键更新全部”，也不展示本地仓库路径、dirty、ahead、diverged 或 worktree。

桌面侧栏暂不提供独立的 **操作历史** 入口。持久化操作记录、恢复、状态清理、
维护审计及已有本机数据保持不变，相关能力暂时通过 CLI 或 Desktop API 使用；
前端页面实现仍保留，后续可以按需重新接入。

CLI 对应命令：

```bash
lpm asset list --scan-local
lpm asset upload --all --dry-run
lpm asset upload --resource skill:demo --yes
lpm asset download --all --platform cursor --platform codex --dry-run
lpm asset download --resource skill:demo --platform cursor --yes
lpm plugin project add D:\Code\demo
lpm plugin project list
lpm plugin reference add --platform codex --origin marketplace --marketplace openai-bundled --plugin-id chrome
lpm plugin delete plugin:codex-marketplace-chrome-openai-bundled --dry-run
```

批量 choices 文件格式：

```yaml
items:
  - resource_key: skill:demo
    platform: cursor
    local_instance_id: expected-cursor-demo
    resolution: rename
    new_name: demo-cursor
  - resource_key: skill:demo
    platform: codex
    local_instance_id: expected-codex-demo
    resolution: rename
    new_name: demo-codex
```

`items` 也可使用以资源键为 key 的映射表示单一决策；需要把同一逻辑资源的不同本地版本分别重命名上传时，必须使用上面的列表形式。

安全边界：

- API key、token、cookie、OAuth session、账号缓存不应进入 `registry.yaml` 或 `resources/`。
- 上传和复制资源时默认排除 `.env`、`.env.local` 等真实环境文件；`.env.example`、`.env.sample`、`.env.template` 可作为无密钥模板保留。
- MCP `env` 的非空字面值在比较和上传前转换为 `${ENV_NAME}` 占位符；资源流程不采集或保存真实值，也不生成机器级密钥清单。
- 批量 apply 前会重新扫描本地、刷新远端并重建计划；`plan_hash` 变化时拒绝写入并返回最新计划。
- 前端提交的路径、指纹、兼容性和可写性都不可信，必须由服务端重新计算。
- 下载先生成 plan；目标已有同名资源且没有 LPM 管理标记时进入阻断，不会静默覆盖。
- 目录资源使用 `.lpm-managed.json`，MCP 使用本机 entry 级所有权记录；未归 LPM 管理的目标不会被普通覆盖或卸载。
- 下载备份写入本机状态目录的 `backups/<operation-id>/`，不会让私有资源 Git 仓库产生备份脏文件。
- 普通资源安装和卸载也使用相同事务边界；批量同步由多个可独立恢复的资源事务组成。

## CLI

CLI 是自动化入口，适合脚本、CI 或高级用户使用。

```bash
lpm --help
lpm init
lpm resource init
lpm resource status
lpm asset list
lpm asset list --scan-local
lpm asset plan download --kind skill --name demo --platform cursor
lpm asset plan upload --kind skill --name demo --platform cursor
lpm asset plan copy-to-local --kind skill --name demo --platform cursor --new-name demo-copy
lpm asset plan copy-to-remote --kind skill --name demo --platform cursor --new-name demo-copy
lpm asset plan set-platform-install-name --kind skill --name demo --platform cursor --new-install-name demo-cursor
lpm asset apply <operation-id>
lpm asset upload --resource skill:demo --dry-run
lpm asset upload --all --yes
lpm asset download --resource skill:demo --platform cursor --dry-run
lpm asset download --all --platform cursor --platform codex --yes
lpm resource commit-plan
lpm resource sync-status --fetch
lpm resource sync-plan
lpm resource sync-resolve <operation-id> --choices choices.yaml
lpm resource sync-apply <operation-id>
lpm resource sync-cancel <operation-id>
lpm resource sync-stale
lpm resource sync-cleanup <operation-id>
lpm resource push
lpm operations list
lpm operations show <operation-id>
lpm operations restore <operation-id>
lpm operations retention-plan
lpm operations prune
lpm operations orphans
lpm operations orphan-export <name>
lpm operations orphan-quarantine --name <name>
lpm operations quarantines
lpm operations quarantine-delete <quarantine-id>
lpm operations audits
lpm operations audit <audit-id>
lpm collect <github-url-or-tree-url> [--platform cursor]
lpm upload <local-path> [--platform cursor]
lpm import-local <local-path> [--platform cursor]
lpm export-plugin
lpm list
lpm sync
lpm doctor
```

`lpm asset list/plan/apply/upload/download` 支持 `--json`。`upload` 和 `download` 支持重复 `--resource`、`--all`、`--dry-run`、`--yes` 与选择文件；`download` 还支持重复 `--platform`。批量命令在执行前重新生成计划并校验 `plan_hash`，不再提供 `lpm env` 迁移命令。

`lpm resource pull`、`lpm resource push` 和 `lpm resource sync-*` 仅保留一个发布版本处理旧工作区状态，执行时会输出弃用警告。旧工作区处于 dirty、ahead、diverged、wrong-branch 或存在待处理旧计划时，新资产模型允许读取和扫描，但阻断远端写入。

`--platform` 可重复使用。资源未设置平台白名单时沿用旧行为，安装到所有已启用且支持该资源类型的平台；设置后只安装到列出的平台：

```yaml
- name: create-hook
  kind: skill
  source: local
  path: skills/create-hook
  platforms:
  - cursor
```

`lpm export-plugin` 会从兼容 `claude-code` 的 active local skills 重新生成 `.claude-plugin/plugin.json`，并把默认插件名规范化为 kebab-case。

Desktop API smoke test：

```bash
lpm-desktop-api platforms {}
lpm-desktop-api summary {}
lpm-desktop-api asset_inventory "{\"scan_local\":true,\"refresh_remote\":true}"
```

接口迁移、动作参数和兼容期说明见 [资产同步 API / CLI 迁移指南](docs/asset-api-cli-migration.md)。核心状态、比较与并发语义见 [资产级双向同步规格](docs/specs/asset-sync.md)，registry 结构见 [Registry v7 规格](docs/specs/registry-v7.md)。

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
npm run check:i18n
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

- 更新代码后唯一需要复制的 PowerShell 命令。
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
- 桌面端不提供 Token 查看或 OAuth 接口；Git 凭据不应进入 Tauri 响应、进程参数、任务中心、错误或日志。

## License

MIT
