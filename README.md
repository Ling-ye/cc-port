# CC Port

> 把分散在 Codex、Claude Code、Cursor、Windsurf 和 OpenCode 的 Skill、MCP、Rule、Prompt、Plugin、用户指令和 Claude auto memory，安全同步到你控制的通用 Git 资源仓库。

[English](README.en.md) · [下载 Windows 安装器](https://github.com/Ling-ye/cc-port/releases/tag/v0.6.0) · [快速开始](docs/getting-started.md) · [报告问题](https://github.com/Ling-ye/cc-port/issues)

[![CI](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml/badge.svg)](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?logo=windows)
![Public Beta](https://img.shields.io/badge/status-public_beta-orange)

CC Port 是一个面向多 AI coding 工具的本地资源管理器。人类可以继续使用桌面客户端，AI 则可通过随安装包提供的 Skill、严格 JSON CLI 和 stdio MCP 自动发现并调用同一套能力。它扫描每个工具的原生目录，以你控制的 Git 仓库作为跨设备事实源，并在真正写入前生成明确的操作计划。资源仓库和 `registry.yaml` 是开放格式，不要求其他消费者安装或理解 CC Port。

## 一眼看懂

| 资源清单与平台状态 | 写入前的操作计划 | 设置与环境诊断 |
| --- | --- | --- |
| ![资源清单与平台状态](docs/assets/screenshots/resources-overview.png) | ![写入前的操作计划](docs/assets/screenshots/operation-plan.png) | ![设置与环境诊断](docs/assets/screenshots/settings-diagnostics.png) |

## 它解决什么问题

- **配置散落**：Skill、MCP、Rule、Prompt、Plugin、用户指令和 Claude auto memory 分别藏在多个工具目录中，难以盘点。
- **多设备漂移**：同一资源在不同电脑和工具中逐渐变成不同版本。
- **同步不安全**：复制目录容易覆盖手工配置，删除和失败操作也难以恢复。
- **凭据风险**：MCP 配置可能混入 Token 或环境变量字面值，不适合直接提交。

CC Port 将远端仓库快照与每个平台的本地实例逐项比较。上传、安装、另存副本和设置安装别名都先生成计划；写入使用备份、目标锁、结果校验和失败回滚。

### 上传到仓库

在资源页勾选资产并点击“上传到仓库”后，CC Port 会立即刷新远端快照并重新扫描本地实例。检查期间只显示进度和取消入口，不会提前显示冲突选项或执行按钮；检查完成后才展示本次得到的本地、远端和整体状态。确认后点击“上传到远端仓库”即可执行并推送。

“本地存在、远端不存在”表示将在远端新增资产，不属于冲突，也不会生成空的资源编辑卡或显示“用远端资产替换本地目标”；该替换确认只用于下载/安装。只有本地与远端都存在且内容或元数据不同时，上传界面才显示覆盖或重命名等冲突处理选项。

根级 Windows 符号链接和目录联接可以作为本地资源来源；上传时 CC Port 会保留逻辑安装路径用于识别资源，解引用目标后生成普通文件快照，仓库中不会写入链接。指向已知 `.agents/skills` 规范目录的链接自动信任，其他目标会在最新扫描结果中显示链接类型、目标和 reparse tag，并要求单独确认。资源目录内部的嵌套链接、悬空链接、循环链接和不可读取重解析点都会阻断该资源，但不会中断其他资源的扫描。

WSL 创建的 LX 符号链接与 Windows 原生符号链接不是同一种 reparse point，Windows 桌面服务不会尝试经由 WSL 桥接读取它。遇到此类阻断时，请在 Windows 中重建原生链接，或用资源安装器的复制模式（例如 `npx skills add ... --copy`）重新安装。

### Windows 与 WSL 独立运行环境

Windows 原生安装和每个 WSL 发行版都是独立 profile；Codex 与 Claude Code 都遵循这条规则。`[platforms.<profile-id>]` 中的 `<profile-id>` 是稳定且唯一的 `name`，用于发现、选择、上传和下载计划；`tool_id` 只描述工具的原生资源语义。环境由 `environment_kind`、`environment_name`、`display_name` 和 `home_dir` 显式描述，CC Port 不会从 `name` 文案反推工具或环境，也不会因为两个 profile 具有相同 `tool_id` 而合并写入目标。

Profile id 必须匹配 `[a-z0-9][a-z0-9._-]{0,127}`，在整份配置中唯一，且不能包含路径分隔符、控制字符或本机私有路径。id 中包含 `.` 时必须写成带引号的 TOML 表键，例如 `[platforms."claude.wsl"]`；CC Port 对非法或重复 id 直接拒绝加载，不会自动改名或合并。

```toml
[platforms.claude-windows]
tool_id = "claude-code"
environment_kind = "windows"
display_name = "Claude Code"
home_dir = "C:/Users/example"

[platforms.claude-wsl-ubuntu]
tool_id = "claude-code"
environment_kind = "wsl"
environment_name = "Ubuntu-24.04"
display_name = "Claude Code"
home_dir = '\\wsl.localhost\Ubuntu-24.04\home\example'
```

每个启用 profile 会独立扫描其 `skills_dir`、`mcp_json`、`rules_dir`、`prompts_dir`、`plugins_dir`、`instructions_path`、`memories_dir` 和 `settings_path`。`settings_path` 指向工具原生的用户级配置文件，例如 Claude Code 的 `settings.json` 或 Codex 的 `config.toml`；当前每个 profile 只解析这一个显式 user-level/native config 输入。CC Port 不自动合并 Claude managed policy、工作区受信任后才生效的 project/local settings 或 `--settings` 临时来源，也不宣称已完整推导 Claude 运行时最终配置。若这些更高或项目作用域的来源覆盖 `autoMemoryDirectory`，必须另建显式 direct profile/path。WSL profile 可用 `home_dir` 把 `~` 展开到对应发行版的 UNC 用户目录；Codex 的 Windows/WSL profile 也应使用不同 `name`。普通 UNC 内容可以进入计划，目录中的 Linux 符号链接仍按 WSL LX 链接单项阻断。WSL 发行版未运行或 UNC 不可达时，该 profile 显示为 unavailable 并阻断写入，不能把不可达误判为资源缺失或删除信号。完整示例见[配置示例](config/config.example.toml)。

Claude Code 的普通 Skill 与 Plugin 共用 `skills_dir`，但不是同一格式：`<name>/SKILL.md` 且没有 manifest 时是普通 Skill；存在 `<name>/.claude-plugin/plugin.json` 时是 `<manifest-name>@skills-dir` Plugin，内部 `skills/*/SKILL.md` 不再作为顶层 Skill 重复发现。Claude Skill 的命令名来自目录名，frontmatter 的 `name` 和 `description` 不强制必填。skills-directory Plugin 以普通文件快照同步到用户 `skills_dir` 或项目 `.claude/skills`；Marketplace Plugin 只同步可移植引用，并由目标 profile 同一运行环境的 `claude plugin marketplace add/install` 原生安装。桌面端分别标识 Skills 目录插件和 Marketplace 插件，并把 Marketplace 注册名与可移植来源分开显示和编辑。`~/.claude/plugins`、Plugin cache 和 Marketplace checkout 只用于状态观察，不能作为上传源码。Codex 的 `.codex-plugin/plugin.json`、TOML 状态和安装流程保持独立，绝不转换成 Claude 格式。完整合同见 [Claude Code Plugin 与 Skill 安装规格](docs/specs/claude-plugin-and-skill-installation.md)。

资源仓库必须与 CC Port 配置文件、本机状态/备份目录、legacy install target，以及所有 profile 的资源目标和 `settings_path` 完全分离；任一方等于、包含或位于另一方之内都属于重叠。配置保存和 asset 扫描、计划、应用会 fail closed，必须先移动冲突目录，不能把机器状态或工具原生配置混入 Git 仓库。

Claude Code 用户指令 `~/.claude/CLAUDE.md` 作为 `instruction` 迁移，但个人 `instruction` 与 `memory` 只由配置 profile 的 environment-aware asset inventory 识别和迁移。通用 global/directory discover 不会把全局用户指令或 auto memory 暴露为可上传候选；项目指令仍只读展示。配置的用户 `rules_dir`（默认 `~/.claude/rules/`）会在 profile-aware 全局用户扫描中递归发现 Markdown；当前只有该目录根级 Markdown 可直接迁移。嵌套用户规则使用 `claude-rule-<relative-path-hash>` 生成不含相对路径明文的唯一候选名，但保持阻断，用户必须先整理成明确的可移植 rule 目录或布局；该哈希只用于区分条目，不是可还原的路径编码。项目 `.claude/rules/**/*.md` 与用户 rules 作用域不同；directory-scope 扫描中的项目规则保持只读和阻断，因为当前没有 project target identity，不能把它们提升或下载到用户全局 `rules_dir`。项目级 `CLAUDE.md`、`.claude/CLAUDE.md` 和 `CLAUDE.local.md` 同样不会被误当成全局指令。默认 auto memory 只扫描 `~/.claude/projects/<project-key>/memory/`；如果可信的 `settings.json` 声明 `autoMemoryDirectory`，其值就是最终 memory 目录，不能再附加 `<project-key>/memory`。memory 的所有权 marker 写在目录旁，不会进入 memory 内容树。

Memory 目录是精确快照：只要内容符合普通 UTF-8 Markdown 契约，名为 `build/`、`cache/`、`tmp/` 的 topic 目录也会原样上传和恢复，不套用 Skill 等资源的通用排除规则。上传前和应用时会扫描目录内全部 Markdown 的疑似秘密；命中时整体阻断，并且错误不回显秘密值。

Claude project slot 可能包含本机绝对路径或用户名，因此 projects memory 的默认发现候选名是 `claude-memory-<slot-hash>`，不包含 slot 明文；哈希同时纳入 profile id，两个未绑定 profile 即使 slot 文本相同也不会自动聚合。direct memory 也以 profile id 与本机路径生成不透明候选名。确切 slot 只保留在本机发现结果的 `install_name_hint` 和 profile 的 `memory_install_names` 中；用户上传时可以把候选项重命名为有意义的远端逻辑名，例如 `cc-port-memory`。

Windows 与 WSL 的 slot 即使对应同一 Git 仓库，也可能不同；CC Port 不会根据路径、内容相同或 hash 候选名自动认定并聚合它们。要把两边绑定到同一远端 Memory，用户应选择同一个远端逻辑名，并在每个目标 profile 的本机 `memory_install_names` 中分别映射到 `projects/` 下确切的现有 slot。目标尚不存在且缺少映射时，计划会阻断，不会猜路径。direct memory 布局不需要此映射。候选 slot 明文和 `memory_install_names` 都不得写入 Registry 或 `cc-port.yaml`。

`cc-port publish` 子命令和 MCP 的 `publish_local_skill` 是 dedicated-repository 发布入口，会拒绝 `instruction` 与 `memory`；legacy `sync`、`check` 和安装计划也不会处理这两个 kind。受支持的个人资源入口只有 profile-aware asset workflow：桌面端资产批量流程、CLI 的 `cc-port asset ...`，或 MCP 的 `asset_inventory`、`asset_action_plan`/`asset_action_apply`、`asset_batch_plan`/`asset_batch_apply`。MCP 发现本机实例时调用 `asset_inventory(scan_local=true)`；`platform`/`target_platforms` 参数使用精确 profile id，plan/apply 两阶段继续校验 operation id 或 `plan_hash`，不能绕过重新扫描和 stale-plan 检查。

CC Port 不整体迁移 `~/.claude.json`、Claude `settings.json` 或 Codex `config.toml`；`~/.claude.json` 只用于脱敏 MCP 投影，settings/config 文件只用于原生路径和能力识别。Claude/Codex 的认证、session、聊天历史、file-history、plans、todos、日志、遥测、plugin cache，以及精确 memory 目录之外的运行时 cache 都不进入资源仓库。只有已经由用户绑定为同一 `kind:name` 的多 profile 实例才按指纹显示 identical copies 或 variants；不同 Claude project slot 不会仅凭内容相同自动合并。完整的官方语义、边界和验收规则见 [Claude Code 指令、记忆与多运行环境规格](docs/specs/claude-memory-and-runtime-environments.md)。

### 通用 Registry v1 与仓库检查

仓库中的资源文件或外部 `source` 是事实，`registry.yaml` 只是可移植成员清单。它只保存 `(kind, name)` 稳定身份，以及互斥的仓库相对 `path` 或外部 `source`：

```yaml
version: 1
resources:
  - kind: skill
    name: code-review
    path: skills/code-review
  - kind: plugin
    name: browser-tools
    source:
      type: marketplace
      locator: openai-bundled/browser-tools
      revision: latest
```

描述、版本、作者、许可证、标签、哈希和检查时间不写入 Registry；MCP 配置保存在 `mcp/<name>/mcp.json|yaml|yml`。profile id、`tool_id`、Windows/WSL 环境、用户目录和本机目标路径同样不得进入 Registry。平台/工具白名单、安装别名和插件启用意图可以进入可选的 `cc-port.yaml`，其他工具无需读取它。`instruction` 与 `memory` 只增加已知 kind 和 `instructions/`、`memories/` 约定目录，不改变 Registry v1 schema。完整字段见 [Registry v1 规格](docs/specs/registry-v1.md)。

`cc-port.yaml` 虽然可选，但一旦存在就必须是普通非链接文件，并完整通过 YAML 与 portable overlay 语义校验。损坏、语义非法绑定或包含本机 memory slot/install alias 的 overlay 会使远端清单 fail closed；CC Port 不把它当成空配置继续上传或下载，也不会通过 Registry 修复静默改写它。

每次远端刷新都会对同一个 commit 只读检查 Registry，但不会自动修改仓库。资源页的“检查仓库”会预览待新增、待移除、人工处理项和最终 YAML diff；用户确认后，CC Port 才以一次只包含 `registry.yaml` 的提交修复并普通推送。修复绝不改写、移动或删除资源内容，也不修改 `cc-port.yaml`。

Registry 缺失、YAML 损坏或本身是链接时只显示诊断，不显示应用按钮；仓库仍显示已连接，本地扫描仍可使用，但上传和安装等依赖远端清单的动作会阻断。CLI 使用：

```text
cc-port resource registry-check --json
cc-port resource registry-repair --dry-run
```

命令行的 `registry-repair` 只生成和展示计划，`--yes` 不具有授权或写入语义。实际修复需要在桌面端审阅，或使用带一次性审批的 MCP `registry_repair_plan` / `registry_repair_apply` 流程。

## 五步开始

1. 安装 [Git for Windows](https://git-scm.com/download/win)，并确认 Git Credential Manager 可用。
2. 从 [v0.6.0 Public Beta](https://github.com/Ling-ye/cc-port/releases/tag/v0.6.0) 下载 `cc-port_0.6.0_windows_x64_setup.exe`。
3. 在 GitHub 创建一个空的私有仓库，作为你自己的资源仓库。
4. 启动 CC Port，在“设置”中粘贴仓库的 HTTPS 地址并完成验证。
5. 扫描本机资源，在资源页逐项选择上传到仓库或安装到目标工具。

v0.6.0 Windows 安装包同时包含桌面程序、Desktop API sidecar 和独立的 `cc-port.exe` CLI/MCP agent；普通用户不需要安装 Python、Node.js 或 Rust。完整流程、首次配置、AI 集成和卸载方式见[快速开始](docs/getting-started.md)。

开发环境可运行 `Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1` 自动检查并安装所需工具和依赖。脚本列出操作后会直接执行，不再要求输入 `y/n`；`-CheckOnly` 仍只检查而不修改环境。

## 安全边界

- **仓库归你所有**：CC Port 不提供托管云服务；资源保存在你指定的 Git 仓库。
- **凭据交给系统**：桌面端通过 Git Credential Manager 使用系统凭据，不读取或保存 GitHub Token。
- **先计划再写入**：桌面端和 CLI 在写操作前展示目标、动作与阻断原因。
- **机器接口不提供自批准**：推荐的 MCP 与非交互 CLI 写入必须提交绑定 operation、plan hash 和完整 scope 的一次性本机审批；用户在桌面端批准，stale 后必须重新审阅。
- **不覆盖未接管内容**：所有权标记用于区分 CC Port 管理项与手工维护项。
- **悬空链接只替换链接本身**：下载目标是根级 Windows 原生悬空符号链接时，只有明确确认覆盖未接管目标后才会删除链接本身并写入普通内容，不会跟随或修改链接指向的位置。
- **可恢复写入**：安装、卸载、部署和恢复使用持久化事务、备份与失败回滚。
- **MCP 密钥占位**：采集 MCP 配置时，环境变量字面值会替换为 `${SECRET_NAME}` 占位符。
- **本机目录与资源仓库隔离**：配置、状态/备份和任何 profile 原生目标不得与 Git 资源仓库互为父子目录。
- **缺失不等于删除**：远端缺少某项资源不会触发隐式删除。

发现安全问题时，请不要创建公开 Issue；按照[安全策略](SECURITY.md)使用 GitHub 的私密漏洞报告。

## 支持范围

### 资源类型

| 类型 | 扫描与登记 | 资源仓库同步 | 安装到工具 |
| --- | :---: | :---: | :---: |
| Skill | ✓ | ✓ | ✓ |
| MCP Server | ✓ | ✓ | ✓ |
| Rule | ✓ | ✓ | ✓ |
| Prompt | ✓ | ✓ | ✓ |
| Plugin | ✓ | ✓ | ✓ |
| Instruction | ✓ | ✓ | ✓ |
| Memory | ✓ | ✓ | ✓ |

Instruction 与 Memory 的“扫描与登记”仅指已配置 profile 的 environment-aware asset inventory；通用 global/directory discover 不提供这两类个人资源的上传入口。

### Instruction 与 Memory 兼容性

| 类型 | Codex | Claude Code | 跨工具规则 |
| --- | --- | --- | --- |
| Instruction | 用户级 `AGENTS.override.md` 或 `AGENTS.md` | 用户级 `~/.claude/CLAUDE.md` | 只写回来源工具语义，不在两种格式间自动转换 |
| Memory | 不支持 Claude auto memory 契约 | 默认 project memory 或 `autoMemoryDirectory` 指定的最终目录 | 只安装到 Claude Code profile，不改名为 Codex 指令 |

### AI coding 工具

| 工具 | 状态 | 默认可写资源 |
| --- | --- | --- |
| Codex | 稳定 | Skill、Instruction |
| Claude Code | 稳定 | Skill、MCP、Rule、Plugin、Instruction、Memory |
| Cursor | 稳定 | Skill、MCP、Prompt |
| Windsurf | 实验性 | Skill、MCP |
| OpenCode | 实验性 | Skill、MCP、Rule、Prompt、Plugin |
| Cline、Gemini CLI | 仅发现 | 暂无完整可写平台预设 |

### Cursor Prompt 命令

Cursor 预设把 Prompt `<name>` 安装为全局自定义命令
`~/.cursor/commands/<name>.md`；设置平台安装别名后，文件名改用该别名。资源仓库
仍以 `prompts/<name>/` 保存可移植内容。下载到这个文件式目标时，远端 Prompt
必须是一个 Markdown 文件，或目录根级恰好包含一个非符号链接 `.md` 文件；零个或
多个根级 Markdown 文件都会阻断计划，不会任意选择。

已有自定义平台若没有设置 `prompts_dir`，仍按旧行为使用
`rules_dir/<install-name>`，避免现有配置被静默迁移。完整规则见
[Cursor Prompt 命令安装规格](docs/specs/cursor-prompt-commands.md)。

高级用户可以在 `config.toml` 中添加自定义平台路径。具体字段见[配置示例](config/config.example.toml)。

## 三种入口

- **桌面 GUI**：日常扫描、比较、上传、安装、AI 集成、审批和环境诊断；人类界面不会删除。
- **CLI**：人类脚本、严格 `--non-interactive --json` 机器调用、批量操作、历史恢复和状态维护。
- **MCP Server**：`cc-port mcp --stdio` 向支持 MCP 的 AI coding 工具暴露 typed plan/apply 能力。

三种入口共享同一套 Python 核心逻辑。架构边界和同步状态机见[架构文档](docs/architecture.md)。

### 让 AI 自动使用 CC Port

AI 自动化让 Codex、Claude Code、Cursor 等 AI 工具调用本机 CC Port，帮你扫描资源、比较本地与远端、上传本地资源或把远端资源安装到指定工具。它不是无人值守的后台同步：AI 可以自动读取和生成计划，真正写入前仍由你在 CC Port 桌面端批准。

CC Port 不调用大模型，不需要你另外填写 OpenAI、Anthropic 或其他模型 API Key。AI 工具本身需要已经正常登录；私有 GitHub 仓库由 Git Credential Manager 完成登录，第三方 MCP 的真实密钥继续留在本机。

最短使用方式：

1. 打开“设置 → AI 自动化”，为需要使用 CC Port 的 Windows profile 点击“审阅启用计划”，然后“批准并启用”。
2. 在 AI 对话中输入：`使用 CC Port 扫描所有已配置 profile，只读比较本地和远端，不要修改。`
3. 决定方向后再输入：`把 codex-windows 中的 skill:example 上传到仓库，只处理这一项，先展示计划。`
4. 在“设置 → AI 自动化 → 待处理 AI 审批”中核对并批准，再回到对话说：`我已在 CC Port 桌面端批准，请继续执行并重新扫描验证。`

如果不知道 profile id 或资源 key，先让 AI 从最新扫描结果中列出来。尽量使用“上传到仓库”或“安装到某个 profile”明确方向，不要只说含义不确定的“同步”。每次审批只绑定当前 operation、`plan_hash` 和完整范围，会自动过期且只能使用一次；状态变化后必须审阅新计划。

启用时，CC Port 只把内置 `cc-port` Skill 安装到选定 profile，并在该工具的原生配置中注册本机 `cc-port.exe mcp --stdio`；不会删除桌面客户端或改写其他 MCP Server。当前 schema v1 只自动引导 Windows 原生 profile，WSL profile 仍可参与已有的资源扫描和 plan/apply。完整的新手步骤和可复制话术见[快速开始：让 AI 帮你管理资源](docs/getting-started.md#5-让-ai-帮你管理资源ai-自动化)，底层命令、schema 和安全边界见 [AI Agent 自动发现、审批与调用规格](docs/specs/ai-agent-interface.md)。

#### 可选：只读 Advisor

普通用户不需要安装 Advisor。只有希望“先让 AI 汇总全部差异并给出建议、但绝不创建写入计划”的用户，
才需要外部
[`cc-port-advisor`](https://github.com/Ling-ye/LingyeAIResources/tree/main/skills/cc-port-advisor)
Skill。它只检查 CC Port 已配置的 profile 与保存项目，不创建计划、审批或传输。用户决定执行后，
仍由上面的内置 `cc-port` Skill 重新扫描、生成计划并等待桌面审批。完整接口和安全边界见
[AI Agent 自动发现、审批与调用规格](docs/specs/ai-agent-interface.md)。

桌面审批是应用层安全护栏，不是 Windows 上的独立安全主体：AI 宿主仍需限制 agent 直接改写 CC Port 本机 state 目录或伪造桌面 sidecar 调用。对与人类用户拥有同等、不受限制文件与进程权限的代码执行者，当前 v1 不声称提供操作系统级“人类在场”证明。

## 当前限制

- Public Beta 仅正式支持 Windows 10/11 x64。
- v0.6.0 安装器尚未代码签名，Windows SmartScreen 可能显示“未知发布者”。
- 目标电脑必须安装 Git for Windows，并配置 Git Credential Manager。
- 桌面端不会替你创建、删除仓库或修改仓库可见性。
- 当前没有自动更新；升级时请从 Releases 下载新版本。
- 桌面端聚焦资源管理；部分历史恢复和维护能力仍只在 CLI/Desktop API 提供。

遇到安装、登录或同步问题，请先查看[故障排查](docs/troubleshooting.md)。

## 文档

- [快速开始](docs/getting-started.md)
- [故障排查](docs/troubleshooting.md)
- [开发指南](docs/development.md)
- [架构](docs/architecture.md)
- [Registry v1 规格](docs/specs/registry-v1.md)
- [Claude Code 指令、记忆与多运行环境规格](docs/specs/claude-memory-and-runtime-environments.md)
- [Claude Code Plugin 与 Skill 安装规格](docs/specs/claude-plugin-and-skill-installation.md)
- [AI Agent 自动发现、审批与调用规格](docs/specs/ai-agent-interface.md)
- [桌面打包与发布](docs/packaging-and-deployment.md)
- [v0.6.0 发布说明](docs/releases/v0.6.0.md)
- [功能规格](docs/specs/)
- [变更记录](CHANGELOG.md)

## 参与贡献

Bug、文档修正和小改动可以直接提交 PR。较大的功能或行为变化请先创建 Issue，确认问题和范围后再实现。详细规则见[贡献指南](CONTRIBUTING.md)。

## License

[MIT](LICENSE) © 2026 Lingye · [第三方软件声明](THIRD_PARTY_NOTICES.md)
