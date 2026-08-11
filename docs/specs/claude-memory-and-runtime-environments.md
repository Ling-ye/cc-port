# Claude Code 指令、记忆与多运行环境规格

## 目标

CC Port 必须识别 Claude Code 的用户指令、用户规则和 auto memory，并迁移当前安装模型
能够无损恢复的部分，同时把同一台电脑上的 Windows 原生安装与 WSL 安装视为两个独立
运行环境。Codex、Claude Code 等工具继续使用各自的原生文件语义，不做隐式格式转换。

本规格依据以下官方资料：

- [Claude Code memory](https://code.claude.com/docs/en/memory)
- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code installation](https://code.claude.com/docs/en/installation)
- [Claude Code sessions](https://code.claude.com/docs/en/sessions)
- [Codex AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [Codex configuration](https://developers.openai.com/codex/config-basic)
- [Codex on Windows and WSL](https://developers.openai.com/codex/app/windows)

## 官方语义与范围

### Claude Code

- 用户指令位于 `~/.claude/CLAUDE.md`；用户规则位于
  `~/.claude/rules/**/*.md`，规则目录递归发现 Markdown 文件。
- 项目规则位于项目 `.claude/rules/**/*.md`，其作用域和目标身份不同于用户
  `~/.claude/rules/**/*.md`，不得把项目规则当作用户全局规则安装。
- 项目指令位于项目根的 `CLAUDE.md`、`.claude/CLAUDE.md`，本地项目指令位于
  `CLAUDE.local.md`。这些文件依赖项目路径和 Git 工作区身份，不得当作用户全局指令
  安装。
- 默认 auto memory 位于
  `~/.claude/projects/<project-key>/memory/`，其中 `MEMORY.md` 是入口，其余 topic
  Markdown 文件按需读取。同一 Git 仓库的工作树共享该 memory，但它默认只存在于
  当前机器。
- `autoMemoryDirectory` 指向最终 memory 目录本身；该目录根直接包含 `MEMORY.md`，
  不能再拼接 `<project-key>/memory`。
- Windows 原生 Claude Code 和 WSL 内的 Claude Code 是两个安装。即使管理员启用了
  `wslInheritsWindowsSettings`，该设置也只影响 Windows managed settings 的继承，
  不会把两个用户目录、用户指令或 auto memory 合并成一个本地实例。

### Codex

- 用户级指令为 Codex home 下的 `AGENTS.override.md` 或 `AGENTS.md`；项目指令按 Git
  根到当前目录的层级发现。
- 用户配置位于 `~/.codex/config.toml`；Windows 原生 Codex 与 WSL 内的 Codex 默认
  使用不同 home。用户可以显式共享 `CODEX_HOME`，但 CC Port 不得根据相似路径推断
  两者已经共享。
- Codex 没有与 Claude Code auto memory 相同的可移植目录契约。Claude memory 不得
  自动改名或安装为 Codex 指令。

## 资源模型

新增两个已知资源类型：

| kind | 内容 | 远端默认目录 | 安装语义 |
| --- | --- | --- | --- |
| `instruction` | 一个工具原生的用户指令 Markdown 文件 | `instructions/<name>/` | 写入该 profile 的固定 instruction 文件 |
| `memory` | 一个精确的 Claude Code memory 目录 | `memories/<name>/` | 写入默认 project slot 或显式 direct memory 目录 |

`rule` 继续表示规则文件或规则目录。Claude Code 的 `~/.claude/rules/` 不得与
`CLAUDE.md` 合并，因为二者的加载顺序、作用域和目标路径不同。

### Registry 与 CC Port overlay

- `registry.yaml` 继续只保存 `version: 1`、`(kind, name)` 以及互斥的 `path` 或
  `source`。Windows 路径、UNC 路径、WSL 发行版、用户目录和 profile id 都不得进入
  Registry。
- 工具兼容性和安装别名继续存放在可选 `cc-port.yaml`。`instruction` 和 `memory`
  首次从本地上传时必须写入来源工具 allowlist；Claude 指令只能回到 Claude Code，
  Codex 指令只能回到 Codex。
- `cc-port.yaml` 一旦存在就必须是普通非链接文件，并完整通过 YAML 与 portable overlay
  语义校验。损坏、非法工具绑定、本机 memory slot/install alias 或未知非法字段必须使
  Registry-backed 远端动作 fail closed；不得把无效 overlay 当成空配置继续上传或下载，
  Registry 修复也不得改写它。
- Claude project slot 明文、`install_name_hint` 和 `memory_install_names` 是本机 profile
  状态，不属于通用安装别名，不得进入 Registry 或 `cc-port.yaml`。
- 同一工具的多个运行环境使用相同 tool allowlist，但计划目标使用唯一 profile id。
  因此一个资源可以在 Windows 和 WSL 间迁移，又不会把写入目标混为同一路径。

## 运行环境身份

`PlatformProfile.name` 是稳定且唯一的 profile id，也是计划、选择、所有权与本地实例
使用的键。不得从该字符串解析工具或环境。

Profile id 必须匹配 `[a-z0-9][a-z0-9._-]{0,127}`，在整份配置中唯一，且不得包含路径
分隔符、控制字符或本机私有路径。包含 `.` 的 id 写入 TOML 时必须使用带引号的表键，
例如 `[platforms."claude.wsl"]`；非法或重复 id 直接阻断配置加载，不自动改名或合并。

每个 profile 另外保存：

- `tool_id`：`codex`、`claude-code` 等工具类型；
- `environment_kind`：`windows`、`wsl`、`linux`、`macos` 或 `unknown`；
- `environment_name`：WSL 发行版等同类环境内的可读名称；
- `display_name`：用户界面文案；
- `home_dir`：该运行环境独立的用户目录，用于展开 profile 内的 `~`；
- `memory_install_names`：本机远端逻辑名到确切 Claude project slot 的映射；
- 各资源类型的原生目标路径。

后端可以派生 `tool_id@environment_kind[:environment_name]` 作为诊断命名空间，但所有
写入计划仍以 profile id 和完整规范化目标路径为准。两个 profile 即使具有相同
`tool_id`，也不得在发现阶段、平台上下文、批量选择或插件目标解析时相互覆盖。

示例：

```toml
[platforms.claude-windows]
tool_id = "claude-code"
environment_kind = "windows"
display_name = "Claude Code"
enabled = true
skills_dir = "C:/Users/example/.claude/skills"
mcp_json = "C:/Users/example/.claude.json"
rules_dir = "C:/Users/example/.claude/rules"
plugins_dir = "C:/Users/example/.claude/plugins"
instructions_path = "C:/Users/example/.claude/CLAUDE.md"
memories_dir = "C:/Users/example/.claude/projects"
memory_layout = "projects"
settings_path = "C:/Users/example/.claude/settings.json"
memory_install_names = { "cc-port-memory" = "replace-with-windows-project-slot" }

[platforms.claude-wsl-ubuntu]
tool_id = "claude-code"
environment_kind = "wsl"
environment_name = "Ubuntu"
display_name = "Claude Code"
enabled = true
skills_dir = '\\wsl.localhost\Ubuntu\home\example\.claude\skills'
mcp_json = '\\wsl.localhost\Ubuntu\home\example\.claude.json'
rules_dir = '\\wsl.localhost\Ubuntu\home\example\.claude\rules'
plugins_dir = '\\wsl.localhost\Ubuntu\home\example\.claude\plugins'
instructions_path = '\\wsl.localhost\Ubuntu\home\example\.claude\CLAUDE.md'
memories_dir = '\\wsl.localhost\Ubuntu\home\example\.claude\projects'
memory_layout = "projects"
settings_path = '\\wsl.localhost\Ubuntu\home\example\.claude\settings.json'
memory_install_names = { "cc-port-memory" = "replace-with-wsl-project-slot" }
```

CC Port 只通过配置路径和普通文件系统 API 读取上述目标，不为发现资源自动启动
`wsl.exe`、shell 或 Claude/Codex CLI。UNC 与 WSL UNC 路径继续使用相同的链接探测、
去重和 fail-closed 规则。

### 本机目录与资源仓库边界

- Git 资源仓库不得与 CC Port 配置文件、本机 state/backup 根、legacy install target，
  或任一 profile 的 `skills_dir`、`mcp_json`、`rules_dir`、`prompts_dir`、`plugins_dir`、
  `instructions_path`、`memories_dir`、`settings_path` 相等或互为父子目录。
- 配置写入以及 asset inventory、plan、apply 都必须重新验证该边界并 fail closed。错误只
  返回冲突类别，不回显用户名、WSL 用户路径或 Claude project slot。

## 发现规则

- `settings_path` 当前只自动解析每个 profile 的一个显式 user-level/native config 输入。
  本实现不合并 Claude managed policy、workspace trust 后生效的 project/local settings 或
  `--settings` 临时来源，也不把解析结果宣称为完整的运行时最终配置。
- 若更高或项目作用域来源覆盖 `autoMemoryDirectory`，必须另建显式 direct profile/path，
  不能根据该 profile 的用户级 `settings_path` 猜测最终目录。
- 个人 `instruction` 与 `memory` 只由配置 profile 的 environment-aware asset inventory
  发现和迁移。通用 global/directory discover 不得把全局用户指令或 auto memory 暴露为
  可上传候选；directory-scope 项目指令只可观察并保持阻断。

### Claude 用户规则

- 只有配置的用户 `rules_dir` 参与全局用户规则迁移；递归发现其中全部普通 Markdown
  文件，不因嵌套目录、隐藏目录或通用扫描深度限制而漏掉官方可加载的规则。
- 当前安装模型只能无损恢复 `rules_dir` 根级 Markdown。嵌套项仍显示为独立候选，但必须
  保持阻断，并提示用户先整理成明确的可移植 rule 目录或布局。
- 嵌套项使用 `claude-rule-<relative-path-hash>` 生成不含相对路径明文的唯一候选名；该哈希
  只用于区分候选，不是可逆的路径编码，上传、下载或安装不得据此重建原目录层级。
- directory-scope 扫描发现的项目 `.claude/rules/**/*.md` 必须只读并阻断。当前模型没有
  project target identity，不得把项目规则提升、上传为用户规则或下载到用户全局
  `rules_dir`。

### 用户指令

- Claude profile 只把配置的 `instructions_path` 识别为用户级 `instruction`；默认名称
  为 `claude-code-user-instructions`。
- Codex profile 只把配置的用户 `AGENTS.md` 识别为 Codex `instruction`；不得把
  Claude 的 `CLAUDE.md` 转换为 `AGENTS.md`，反向同理。
- `CLAUDE.md` 的 `@path` import 只作为文本保留。CC Port 不自动跟随或打包 import，
  因为绝对路径和工作区外路径可能越界或泄露私有文件；发现结果必须提示用户单独迁移
  依赖。
- managed policy 指令和项目 `CLAUDE.local.md` 不进入用户级自动迁移。

### Claude auto memory

- 默认布局只扫描 `projects/*/memory/`，候选目录必须直接包含普通、UTF-8 的
  `MEMORY.md`；topic 文件必须是普通 Markdown 文件。
- memory 作为精确目录快照处理。只要满足上述契约，`build/`、`cache/`、`tmp/` 等 topic
  目录及其 Markdown 必须原样进入指纹、上传快照和下载恢复，不得套用 Skill、Plugin 等
  资源的通用目录排除规则。
- 上传计划和 apply 必须扫描 memory 树中全部 Markdown，包括通用名称目录里的 topic；
  命中疑似秘密时阻断整个资源，结构化错误、diff 和日志不得回显秘密值。
- 不扫描或上传精确 memory 目录之外的 session transcripts、history、file-history、plans、
  todos、shell snapshots、telemetry、runtime cache 或 plugin cache；memory 目录内部名为
  `cache/` 的合法 Markdown topic 仍属于上一条定义的精确快照。
- 当 `settings_path` 指向可信 Claude `settings.json` 且设置了 `autoMemoryDirectory` 时，将该值作为 direct memory
  目录；不得把它当作 projects 根。
- Claude project slot 可能编码本机绝对路径或用户名，因此 projects memory 的默认候选名
  必须为 `claude-memory-<slot-hash>`；`<slot-hash>` 由 profile id 与完整 slot 共同计算，
  因而两个未绑定 profile 即使 slot 文本相同也不会自动聚合。候选名不得包含 slot 明文，
  确切 slot 只保留在本机发现结果的 `install_name_hint`。
- direct memory 的默认候选名同样使用 profile id 与本机 direct path 的截断哈希，不得用固定
  名称把 Windows 与 WSL 中两个未经用户绑定的目录自动聚合。
- 默认 hash 候选名只用于本机发现。上传时用户可以将其重命名为有意义的远端逻辑名；
  Registry 的 `name`、仓库路径和远端元数据均不得包含 project slot 明文。
- Windows 与 WSL 的不同 slot 不得根据路径、内容指纹或 hash 候选名自动聚合，也不得反向
  猜测它们是否指向同一 Git 仓库。用户若要把两边视为同一 Memory，必须选择同一个远端
  逻辑名，并在各 profile 的本机 `memory_install_names` 中分别绑定确切 slot。

### 去重

- profile id、kind、逻辑路径共同标识一个本地实例；不同 profile 的相同路径文本不能
  仅按字符串合并。
- 同一 profile 中由默认目录和显式配置重复发现的相同规范化路径只显示一次。
- 逻辑资源仍按 `kind:name` 聚合；只有已经由用户绑定到同一 `kind:name` 的多 profile 实例
  才能按指纹显示 identical copies 或 variants。不同 project slot 的默认 hash 候选项保持
  独立，即使内容指纹相同也不得自动合并。
- 本地实例保留 profile id、tool id、环境字段和本机 `install_name_hint`；后者不得进入
  Registry、`cc-port.yaml` 或仓库资源元数据。

## 上传、下载与安全

- 批量计划必须重新扫描所有已启用 profile，并把环境身份、逻辑路径、内容路径、链接
  属性、指纹和远端 commit 纳入断言；apply 继续重算并校验 `plan_hash`。
- 上传 `instruction` 时远端保存普通 Markdown 快照；上传 `memory` 时远端保存普通文件
  目录快照。符号链接、junction、未知 reparse point 和嵌套链接仍按现有规则处理。
- dedicated-repository 的 `cc-port publish` 子命令和 MCP `publish_local_skill` 拒绝
  `instruction` 与 `memory`；legacy `sync`、`check` 和安装计划也必须拒绝或跳过这两个
  kind。这两类资源只能走 profile-aware asset workflow。
- 自动化调用使用与桌面端、CLI 相同的 MCP asset API：`asset_inventory`、
  `asset_action_plan`/`asset_action_apply`、`asset_batch_plan`/`asset_batch_apply`。平台参数
  必须是精确 profile id；本机发现必须显式设置 `asset_inventory(scan_local=true)`；action
  apply 使用持久化 operation id，batch apply 使用原 `plan_hash` 和相同输入重建计划。
  任何身份、路径、指纹或远端 commit 变化都返回 stale plan，不得信任调用方篡改的资源
  字段。
- instruction 的 ownership marker 放在目标文件旁；memory 的 marker 放在 memory 目录
  旁，不得写入 memory 内容树并改变 Claude 的实际记忆内容。
- 下载只写用户在计划中选择的 profile。选择 Windows profile 不得写 WSL，选择 WSL
  profile 不得写 Windows。
- projects memory 下载必须用目标 profile 的本机 `memory_install_names` 将远端逻辑名映射
  到确切 project slot；同一逻辑名可以在 Windows 与 WSL 映射到不同 slot。目标不存在且
  缺少映射时必须阻断，不得从资源名、路径或其他 profile 推断。direct 布局不需要映射。
- 确切 project slot 源值只保存在本机 `install_name_hint` 和 `memory_install_names`；操作计划
  可以由此计算本机目标路径，但不得把 slot 或映射写入 Registry、`cc-port.yaml`、
  ownership marker 或远端资源内容。
- 未管理目标必须显式确认覆盖。指令文件或 memory 目录在计划后发生变化时返回 stale
  plan，不得合并或覆盖。
- 全文件秘密扫描继续适用于 instruction 和 memory；命中疑似凭据时阻断上传且不回显
  值。

## 明确不迁移的内容

- 不整体迁移 `~/.claude.json`。官方说明该文件混合 OAuth session、用户/本地 MCP、
  per-project allowed tools、trust 状态和缓存；CC Port 只继续读取并脱敏投影 MCP server
  项。
- 不整体迁移 `settings.json` 或 Codex `config.toml`。它们用于识别原生路径和能力，
  其中的 env、hooks、权限、sandbox、provider、认证和机器绝对路径不是可移植资源。
- 不迁移任何认证文件、token、API key、Claude session、聊天历史、Codex session、日志、
  cache 或遥测。
- 不把一种工具的 instruction 或 memory 自动翻译为另一种工具的格式。

## 验收标准

- 同时配置 Windows Claude Code 与 WSL Claude Code 时，两者拥有不同 profile id，清单
  显示环境标签，批量计划可精确选择其中一个或两个目标。
- projects memory 的默认候选名符合 `claude-memory-<slot-hash>`，不包含可能编码绝对路径
  或用户名的 slot 明文；未显式绑定的相同 slot 文本跨 profile 仍得到不同候选名，确切
  slot 只出现在本机 `install_name_hint` 和配置映射中。
- Windows 与 WSL 的不同 slot 默认保持两个独立候选项，即使内容相同也不自动聚合。用户
  可以将两者绑定到同一远端逻辑名；绑定后同内容显示 identical copies，内容不同显示
  variants，上传未选择来源时阻断。
- projects memory 下载时，同一远端逻辑名通过各 profile 的 `memory_install_names` 精确写入
  不同 slot；目标不存在且缺映射时阻断。direct 布局不要求该映射。
- Claude `~/.claude/CLAUDE.md`、全部递归用户 rules 和每个默认 auto-memory 目录均可
  由配置 profile 的 asset inventory 发现；根级 rule、instruction 和 memory 可上传、从
  远端恢复并通过内容指纹验证。通用 global/directory discover 不产生个人 instruction 或
  memory 上传候选。
- 嵌套 Claude rule 使用唯一候选名显示并保持阻断，整理为明确可移植布局前不能上传；
  `claude-rule-<relative-path-hash>` 不会被当作路径还原信息。
- directory-scope 项目 `.claude/rules/**/*.md` 保持只读和阻断，不会进入用户全局
  `rules_dir`；只有 configured user `rules_dir` 的 global user scan 候选可参与迁移。
- direct `autoMemoryDirectory` 不附加 project key 或 `memory` 子目录。
- memory 中合法的 `build/`、`cache/`、`tmp/` Markdown topic 在上传和下载后逐文件、逐字节
  保留，并全部参与疑似秘密扫描；session/cache 等 memory 目录外状态仍不进入仓库。
- `settings_path` 自动解析只读取 profile 的一个显式用户级配置输入，不合并 managed
  policy、Claude 项目级/本地 settings 或 `--settings`；工作区未受信任时不得把项目
  override 当作已生效配置，存在更高作用域覆盖时使用单独的显式 direct profile/path。
- `cc-port publish` 与 MCP dedicated-repository 发布对 `instruction`、`memory` 返回拒绝，
  legacy sync/check/install 同样不处理它们；桌面、CLI 和 MCP 的 profile-aware asset
  plan/apply 仍可处理，并继续校验 operation id 或 `plan_hash`。
- Claude instruction 不会出现在 Codex 下载目标中，Claude memory 对 Codex 始终不支持。
- Windows 与 WSL 的 MCP、Skill、Plugin、Instruction 和 Memory 都以 profile id 参与
  计划，不再因相同 tool id 相互覆盖。
- Registry 中不出现本机路径、环境身份、project slot 明文或 `memory_install_names`；
  `~/.claude.json`、settings、认证、历史和缓存不进入资源仓库。
- 非法或重复 profile id、资源仓库与任一本机配置/state/profile target 重叠、无效
  `cc-port.yaml` 都在写入前 fail closed，不得降级为默认 profile、空 overlay 或普通资源。
- 任一目标变化、链接重定向或远端 commit 变化都会得到 stale plan，而不是静默覆盖。
