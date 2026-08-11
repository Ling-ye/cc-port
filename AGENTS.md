# AGENTS.md

## 资源上传流程约束

- 打开“上传到仓库”批量对话框时，必须通过批量计划刷新远端快照并重新扫描本地实例。
- 检查进行中只能显示进度和取消入口；不得提前渲染资源编辑卡、冲突选项、重新检查按钮或上传按钮。
- 批量计划必须返回 `checked_resources`，前端展示本次检查得到的本地、远端和整体状态，不能使用打开对话框前的旧清单推断冲突。
- “本地存在、远端不存在”是新增，不是冲突；此时不得显示“冲突处理”。
- “用远端资产替换本地目标”只属于下载/安装方向；上传计划不得显示该确认项。
- 只有本地与远端都存在，并且整体状态为 `content-different` 或 `metadata-only` 时，上传流程才显示覆盖或重命名选项。
- 没有需要用户选择的资源时，不得渲染空的资源编辑卡；计划存在阻断或没有可执行项时，不得显示上传按钮。
- 应用批量计划前必须继续校验 `plan_hash`；状态变化时返回新计划，不得直接写入旧计划。

## Windows 链接资源约束

- 根级 Windows 原生符号链接和目录联接可以作为本地资源；逻辑安装路径与解引用后的内容路径必须分开保存。
- 上传链接资源时只能写入普通文件快照，不能把链接或 reparse point 写入远端仓库。
- 指向已知 `.agents/skills` 规范目录的根级链接可自动信任；其他链接目标必须在上传计划中显示并由用户明确确认。
- 上传计划和应用阶段都必须校验逻辑路径、内容路径、链接类型、原始目标、reparse tag 与内容指纹，链接被重定向后必须返回 stale plan。
- 下载方向遇到根级 Windows 原生悬空符号链接时，只能在用户明确确认覆盖 unmanaged 目标后删除链接本身并写入普通内容；不得跟随或写入链接目标。
- WSL LX 符号链接必须阻断单个资源并给出 Windows 原生链接或复制模式指引；不得自动调用 WSL 桥接读取。
- 资源内部的嵌套链接、悬空链接、循环链接、不可读取或未知 reparse point 必须 fail closed，但单个异常条目不得中断整次本地扫描。
- 远端仓库快照继续拒绝符号链接，不得复用本地根级链接的放行逻辑。
- 本地资产扫描必须包含所有已启用 profile 配置的 `skills_dir`、`mcp_json`、`rules_dir`、`prompts_dir`、`plugins_dir`、`instructions_path`、`memories_dir` 和 `settings_path`；自定义目录、UNC 路径和 WSL UNC 路径使用同一套资源发现、去重与链接安全规则。

## Claude 指令、记忆与多运行环境约束

- `PlatformProfile.name` 是稳定且唯一的 profile id，也是发现、计划、选择、所有权和本地实例的键；`tool_id`、`environment_kind`、`environment_name`、`display_name` 和 `home_dir` 必须显式保存，不得从 `name` 文案反推。
- profile id 必须匹配 `[a-z0-9][a-z0-9._-]{0,127}` 并在整份配置中唯一；包含 `.` 时写配置必须使用带引号的 TOML 表键。路径、控制字符、非法或重复 id 必须 fail closed，不得自动改名、覆盖或聚合。
- Windows 原生安装和每个 WSL 发行版必须建成独立 profile；Codex 与 Claude Code 均不得因 `tool_id` 相同而在发现、批量选择、上传或下载目标中相互覆盖。`home_dir` 用于把该 profile 的 `~` 展开到正确的 Windows 用户目录或 WSL UNC 用户目录。
- WSL 发行版未运行或 UNC 不可达时必须标记该 profile 为 unavailable 并阻断写入；不得把不可达实例推断为资源 missing、删除请求或空目录。
- `instruction` 与 `memory` 是独立已知资源类型，`rule` 继续表示规则文件或目录。Claude `CLAUDE.md` 与 Codex `AGENTS.md` 只按各自工具的原生语义安装，不得自动互译；Claude memory 不得安装为 Codex 指令。
- Claude 用户指令只识别配置的 `instructions_path`；项目级 `CLAUDE.md`、`.claude/CLAUDE.md` 和 `CLAUDE.local.md` 不得当作用户全局指令。默认 memory 布局只扫描 `projects/*/memory/` 且目录根必须有普通 UTF-8 `MEMORY.md`。
- 个人 `instruction` 与 `memory` 只允许 profile-aware、environment-aware asset inventory 和 plan/apply workflow 发现、上传或下载；通用 global/directory discover 不得把全局用户指令或 auto memory 暴露为可上传候选。directory-scope 项目指令继续只读展示。
- Claude 用户 rules 只从配置的用户 `rules_dir` 参与全局用户扫描，并必须递归发现全部普通 Markdown；当前仅该目录根级文件可直接迁移。嵌套项用 `claude-rule-<relative-path-hash>` 生成不含相对路径明文的唯一候选名后保持阻断，必须先整理为明确可移植的 rule 目录或布局；候选哈希只用于区分，不得解释为可还原路径。项目 `.claude/rules/**/*.md` 与用户 rules 作用域不同；当前没有 project target identity，directory-scope 项目规则必须只读和阻断，不得提升或下载到用户全局 `rules_dir`。
- `settings_path` 只指向并解析该 profile 的一个显式工具原生用户级配置输入；不得自动合并 Claude managed policy、workspace trust 后生效的 project/local settings 或 `--settings` 临时来源，也不得宣称完整推导运行时最终配置。当该用户级 `settings_path` 指向可信 Claude `settings.json` 时，其中的 `autoMemoryDirectory` 是最终 memory 目录本身，必须切换为 direct 布局，不得继续附加 project key 或 `memory/`；若更高或项目作用域来源覆盖该值，必须另建显式 direct profile/path。Codex profile 的 `settings_path` 指向 `config.toml`。
- Claude project slot 可能编码本机绝对路径或用户名，projects memory 默认候选名必须使用 `claude-memory-<slot-hash>`，不得包含 slot 明文；确切 slot 只保留在本机 `install_name_hint` 和 `memory_install_names`。
- projects memory 的远端逻辑名不得用于猜测本地 Claude project slot；每个 profile 必须以本机 `memory_install_names` 显式映射到 `projects/` 下确切 slot。Win/WSL 的不同 slot 不得按路径或内容自动聚合；用户可以为两边选择同一远端逻辑名，再分别映射。目标不存在且缺映射时阻断下载；direct 布局不需要映射。slot 明文和映射不得进入 Registry 或 `cc-port.yaml`。
- `~/.claude.json` 只能用于脱敏 MCP 投影，Claude `settings.json` 与 Codex `config.toml` 只能用于原生路径和能力识别；不得整体迁移这些文件，也不得迁移认证、token、API key、session、聊天历史、file-history、plans、todos、日志、遥测、plugin cache 或精确 memory 目录之外的运行时 cache。
- memory 是精确 Markdown 目录快照；`build/`、`cache/`、`tmp/` 等合法 topic 目录不得套用其他资源的通用排除规则。上传计划和应用阶段都必须扫描树内全部 Markdown 的疑似秘密，命中时整体阻断且不得回显值。
- instruction 的所有权 marker 放在目标文件旁；memory 的 marker 必须放在 memory 目录旁，不得写入内容树。只有已绑定到同一 `kind:name` 的多 profile 实例才可按指纹折叠为 identical copies 或保留为 variants；不同 project slot 不得仅凭内容相同自动合并。
- dedicated-repository 的 `cc-port publish` 和 MCP `publish_local_skill`，以及 legacy `sync`、`check`、安装计划必须拒绝或跳过 `instruction` 与 `memory`。这两类资源只能走 profile-aware asset workflow。
- MCP 的 `asset_inventory`、`asset_action_plan`/`asset_action_apply`、`asset_batch_plan`/`asset_batch_apply` 必须与桌面端和 CLI 共用 asset 核心；本机发现要求 `scan_local=true`，平台参数是精确 profile id，apply 必须按 operation id 或 `plan_hash` 重新校验本地/远端身份并返回 stale plan，而不是信任调用方提交的资源字段。

## 本机与资源仓库边界

- Git 资源仓库不得与 CC Port 配置文件、本机 state/backup 根、legacy install target 或任何 profile 的 `skills_dir`、`mcp_json`、`rules_dir`、`prompts_dir`、`plugins_dir`、`instructions_path`、`memories_dir`、`settings_path` 相等或互为父子目录。
- 保存配置以及 asset inventory、plan、apply 都必须重新校验上述边界并 fail closed；错误只报告冲突类别，不得把用户名、WSL 路径或 Claude project slot 写入结构化错误或日志。

## Registry v1 约束

- `registry.yaml` 是工具中立清单，只保存 `version: 1`、资源 `(kind, name)` 以及互斥的 `path` 或 `source`；不得写入派生元数据、健康缓存、删除历史、MCP 配置或 CC Port 专属设置。
- 实体资源内容或外部 `source` 是事实；已登记内容内部变化且仍有效时不得产生 Registry 修复项。
- MCP 配置必须脱敏后写入 `mcp/<name>/mcp.json|yaml|yml`，Registry 只保存路径。
- `instruction` 与 `memory` 只扩充已知 kind 和 `instructions/`、`memories/` 约定根目录，不得给 Registry v1 schema 增加字段。
- 本机 profile id、`tool_id`、Windows/WSL 环境、用户目录和目标路径不得进入 Registry；CC Port 专属工具 allowlist、安装别名和插件意图只能进入可选 `cc-port.yaml`，其他工具无需理解它。
- `cc-port.yaml` 可选但一旦存在就必须是普通非链接文件，并完整通过 YAML 与 portable overlay 语义校验；损坏、非法工具绑定、本机 memory slot/install alias 或其他无效字段必须使 Registry-backed 远端动作 fail closed，不得按空 overlay 继续，也不得由 Registry 修复自动改写。
- 每次远端刷新必须审计同一个 commit，但不得自动修改远端；Registry 不可用时远端仍可标记连接成功，本地扫描继续，依赖远端清单的动作全部阻断。
- Registry 修复必须重新 fetch 并校验 `plan_hash`；只允许暂存、提交和普通推送 `registry.yaml`，不得修改资源内容、`cc-port.yaml` 或其他文件，不得强推或自动合并竞态。
- Registry 缺失、YAML 损坏、不是普通文件或为链接时只报告且不可修复；普通加载器不兼容 v5/v6/v7。可解析 v7 只允许用户确认后从当前实体资源覆盖为 v1。
- 未知安全 `kind` 和 `source.type` 必须原样保留并只读展示；已知类型拼错字段必须报告 schema 错误。
- 凭据或疑似秘密不得出现在 Registry、diff、结构化错误、日志或提交中。

## 快速验证

- 后端：`.venv\Scripts\python.exe -m pytest tests/test_asset_sync.py -q`
- Registry：`.venv\Scripts\python.exe -m pytest tests/test_registry_v1.py tests/test_registry_audit.py tests/test_registry_interfaces.py -q`
- 链接探测：`.venv\Scripts\python.exe -m pytest tests/test_local_path_probe.py -q`
- Claude/环境：`.venv\Scripts\python.exe -m pytest tests/test_claude_memory_runtime_profiles.py -q`
- 配置与路径边界：`.venv\Scripts\python.exe -m pytest tests/test_config.py -q`
- MCP asset API：`.venv\Scripts\python.exe -m pytest tests/test_mcp_asset_api.py -q`
- 前端：在 `desktop` 目录执行 `npm.cmd exec vitest run -- src/features/resources/ResourcesView.test.tsx src/features/guide/GuideView.test.tsx`
- 构建：在 `desktop` 目录执行 `npm.cmd run build`
- Rust 桥接：在 `desktop/src-tauri` 目录执行 `cargo test --lib`
- 不要创建 Git 提交；提交由维护者完成。

## 开发环境安装脚本

- `scripts/setup.ps1` 打印计划后必须直接执行，不得增加 `y/n` 或其他二次确认。
- `-CheckOnly` 必须保持只读；`-NonInteractive` 继续用于控制 WinGet 的静默安装参数。
