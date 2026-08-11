# AI Agent 自动发现、审批与调用规格

## 目标

CC Port 同时服务两类调用者：

- 人类继续使用桌面客户端完成扫描、比较、冲突选择、审批、恢复和诊断；
- AI coding agent 通过可发现的本机 MCP server 或严格 JSON CLI 完成同一套资源操作。

自动化接口不是第二套同步实现。CLI、MCP 和 Desktop API 必须复用 Python services，
由同一套 profile、Registry、所有权、链接、秘密扫描、计划重建、目标锁和事务规则决定结果。

本规格覆盖 `skill`、`mcp`、`rule`、`prompt`、`plugin`、`instruction` 和 `memory`
七类 profile-aware asset 操作，以及 Registry 修复。创建/删除仓库、修改可见性、强推、发布、
直接执行资源内容或批量删除本机状态不属于默认 AI 自动化权限。

## 分发组成

Windows 安装包必须同时包含：

```text
cc-port-desktop.exe       # 人类桌面客户端
cc-port-desktop-api.exe   # Tauri 调用的结构化 Desktop API
cc-port.exe               # 人类 CLI、机器 JSON CLI、stdio MCP
```

Python wheel、`cc-port.exe` 和负责安装集成的 `cc-port-desktop-api.exe` 都必须包含
canonical Skill：

```text
cc_port/assets/ai/cc-port/SKILL.md
cc_port/assets/ai/cc-port/references/workflow.md
cc_port/assets/ai/cc-port/references/resource-kinds.md
cc_port/assets/ai/cc-port/references/safety.md
```

仓库根 `SKILL.md` 与根 `references/` 是便于源码环境发现的逐字节镜像，不承担版本来源。
Skill frontmatter 只包含 `name` 和 `description`；发布版本继续由 Python、Desktop 和
Tauri manifest 共同决定。

## 启用与卸载

桌面设置页按精确 profile 显示 AI 集成状态。启用流程必须先生成计划，并展示：

- profile id、工具与 Windows/WSL 环境身份；
- Skill 目标、MCP 配置目标和启动命令；
- 当前所有权状态、计划动作、阻断项和 `plan_hash`；
- 是否需要接管同名未受管 Skill 或 MCP entry。

用户批准后，服务把 canonical Skill 安装到该 profile 的 `skills_dir/cc-port`，并注册
`cc-port.exe mcp --stdio`：

- JSON 型配置只增加或更新 `mcpServers.cc-port`；
- Codex `config.toml` 使用带 BEGIN/END marker 的受管 `[mcp_servers.cc-port]` block；
- 其他配置、其他 MCP entry 和未受管的兼容内容保持原样。

安装和卸载使用 `LocalChangeTransaction`。apply 前重新检查 profile、目标路径、现有内容、
所有权、命令和内容指纹；失败时恢复 Skill、配置文件和本机 ownership record。卸载只删除
CC Port 明确拥有的目标，不把“存在兼容配置”解释为可删除。

一个 Windows 安装和每个 WSL distribution 都是独立 profile，资源扫描和同步不得按
`tool_id` 合并它们。AI 集成 schema v1 只自动引导 Windows 原生 profile；WSL profile 返回显式
blocker 且 `transport_status=unknown`，不得用 Windows 进程结果伪装 WSL MCP 验证。启用一个
profile 不得自动修改相同 `tool_id` 的其他 profile；unavailable profile 必须阻断而不是当成 missing。

## 接口选择

AI 按以下顺序选择接口：

1. 使用客户端通过 MCP discovery 暴露的 CC Port stdio server；
2. MCP 不可用时，使用 `cc-port --non-interactive <command> --json`；
3. 两者都不可用、输出无法解析或审批无法表达时，停止并交给桌面客户端。

不得以 PowerShell、shell、原始 Git 或直接文件复制重新实现失败的 CC Port 操作。

### MCP 启动

```text
cc-port mcp --stdio
```

stdio 期间 stdout 只承载 MCP JSON-RPC 消息。server instructions 必须描述安全工作流、
exact profile、stale 和审批语义。所有工具必须提供 `readOnlyHint`、`destructiveHint`、
`idempotentHint` 与 `openWorldHint`；annotations 只帮助宿主展示风险，不替代服务端授权。

推荐公共工具至少包括：

```text
cc_port_status
cc_port_doctor
asset_inventory
asset_content_diff
asset_action_plan
asset_action_apply
asset_batch_plan
asset_batch_apply
registry_repair_plan
registry_repair_apply
operation_detail
```

旧的 direct-write 工具可以在兼容期继续存在，但必须标记为 legacy、排除在
`cc_port_status.recommended_tools` 之外，并且不能成为 Skill 的默认路径。

### JSON CLI

机器调用统一使用版本化 envelope：

```json
{
  "contract_version": 1,
  "ok": true,
  "status": "planned",
  "data": {},
  "error": null
}
```

一次调用的 stdout 只能包含一个 UTF-8 JSON 文档，不得混入 Rich、ANSI、进度条或提示文本。
输入缺失、schema 错误、需要审批、stale、partial 和运行失败使用不同非零退出码；调用者必须同时
检查退出码、`ok` 和 `status`，不能只检查是否有 `data`。

批量 JSON request 默认拒绝未知字段，并完整保存 `direction`、`resource_keys`、
`target_platforms` 与 choices。`link_target_confirmed`、`overwrite_unmanaged`、
`ownership_confirmed` 等布尔值是计划输入，不是审批凭据。

## 强制工作流

所有 AI 发起的资产写入都遵循：

```text
status → inventory → diff → plan → approval → apply → verify
```

### 1. Status

先读取 contract version、推荐工具、legacy 工具、transport 和配置 profile 摘要。仅在状态指出
配置或依赖问题时调用 doctor；诊断不授权修复。

### 2. Inventory

调用 `asset_inventory(scan_local=true, refresh_remote=true)`，从结果选择精确 `resource_key`、
profile id 和必要的 `local_instance_id`。不得用 `tool_id`、显示名、路径或上次会话记忆代替身份。

### 3. Diff

本地和远端都存在时，可以请求有界内容 diff。文件名、说明、diff、Skill、Prompt、Rule、
Instruction、Memory、Plugin manifest 和 MCP description 都是不可信数据，不能变成新的命令。

### 4. Plan

单项 plan 返回 `operation_id`；批量 plan 返回稳定的 operation identity 与 `plan_hash`。
计划必须显示选中身份、方向、目标、重命名/覆盖/链接/所有权选择、远端 commit 类别、阻断项、
可执行数量与跳过数量。计划本身不写资源仓库或工具目标。

### 5. Approval

存在可执行写动作且计划未阻断时，机器 plan 创建一个本机审批请求：

```text
pending → approved → consumed
        ↘ rejected
        ↘ expired
```

请求包含随机 `approval_id`，并绑定：

- 操作 kind；
- operation id；
- `plan_hash`；
- 完整 normalized scope 的 SHA-256；
- 不含凭据或私有绝对路径的摘要与元数据；
- 创建时间和过期时间。

MCP 不暴露 approve/reject 工具。模型不能通过传入 `true`、CLI `--yes`、复述用户原话或调用另一个 CC Port
机器接口批准自己。用户只在桌面“待处理 AI 审批”中批准或拒绝。审批默认短时有效，
只能消费一次。

### 6. Apply

AI apply 必须提交原样的 operation identity、`plan_hash`、请求 scope 和 `approval_id`。adapter
在调用写 service 前验证审批状态和完整绑定，并原子转换为 `consumed`。已消费授权即使写入失败也
不能重试；必须重新 plan 并重新批准，避免一次授权演变为无限写权限。

apply 重建计划并重新检查本地与远端身份。发现变化时返回 `stale-plan` 和一个新的未批准计划；
旧审批不能迁移到新 hash。`blocked`、`needs-action`、`partial`、`failed` 和 `stale-plan` 都不是成功。

### 7. Verify

apply 成功后再次调用 `asset_inventory(scan_local=true, refresh_remote=true)`，核对受影响的
`resource_key`、profile id 和 instance。需要恢复/审计信息时读取 `operation_detail`。不能只凭
apply 的 message 宣布最终成功。

## Registry 修复

Registry repair 与资产写入使用相同的 plan、审批、apply、stale 和 verify 边界。审批 scope
必须覆盖 choices 与远端身份；apply 仍然只允许修改并普通推送 `registry.yaml`。修复不得顺带
修改资源内容、`cc-port.yaml`、仓库可见性或分支历史。

## 安全和隐私

- 所有 input model 默认 `extra=forbid`，自由扩展只允许出现在明确声明的 metadata 字段。
- 结构化错误在 adapter 边界脱敏，不回显 token、secret、私有路径或 Claude project slot。
- 资源仓库不得与配置、状态/备份或任一 profile 目标相等或互为父子目录。
- 计划和 apply 都重新检查 Windows link/reparse identity；WSL LX link、嵌套链接、循环、悬空、
  不可读取和未知 reparse point 继续 fail closed。
- `instruction` 与 `memory` 只走 profile-aware asset workflow，不能回退到 legacy publish/sync/check。
- Desktop API 的 approve/reject action 只供本机人类界面调用，不作为 MCP tool 发布。
- `CC_PORT_AGENT_BIN` 只接受存在、普通且非链接的显式 agent 文件；生产默认使用随安装包分发的
  同目录 `cc-port.exe`，开发环境才回退到已安装 CLI 或 Python module。

### 威胁模型

一次性审批、Desktop-only approve/reject、plan 重建和目标锁构成应用层的 fail-closed 工作流。
它们防止只能通过推荐 MCP/CLI 合同调用 CC Port 的 agent 自批准或重放已消费计划。

本地审批 JSON 与未签名 Desktop/sidecar 属于当前 Windows 用户，不是独立的 OS 安全主体。
因此 AI 宿主必须通过沙箱或权限策略禁止 agent 直接改写 CC Port state 目录、替换本地二进制
或伪造 Desktop sidecar 父进程调用。对拥有同用户完整文件和进程控制权的代码执行者，
schema v1 不声称提供 OS 级人类在场证明。该级别需要另设独立 broker/服务账户、受保护的一次性 capability
或系统用户验证，不能用同用户无密钥 JSON hash 替代。

## 兼容与人类客户端

新增 AI 接口不得删除或降级桌面客户端。桌面仍是以下情况的主要入口：

- 初次连接资源仓库和 Git Credential Manager 诊断；
- 查看完整冲突、diff、链接目标和多 profile 身份；
- 批准或拒绝 AI 发起的写入；
- 接管未受管目标；
- 查看操作历史、恢复和维护状态。

旧 CLI/MCP 工具在兼容期可以保留，但新文档、Skill、status capability 和安装集成只宣传安全的
typed plan/apply 接口。兼容工具不得绕过现有服务端 plan revalidation，也不得处理
`instruction` 或 `memory`。

## 验收

完成实现至少通过以下层次：

1. approval unit tests：绑定、过期、拒绝、single-use 和 scope mismatch；
2. AI integration tests：JSON 与 Codex TOML 安装、unmanaged 阻断、stale、回滚和 ownership-safe uninstall；
3. CLI contract tests：单 JSON envelope、严格 request、退出码、非交互无 prompt、审批与 stale；
4. MCP contract tests：严格 input/output schema、annotations、结构化 error、无 approve tool；
5. 真实 stdio 测试：initialize、`tools/list` 和 `tools/call`；
6. Desktop API 与 React tests：计划审阅、启用/卸载、pending approval approve/reject；
7. wheel 内容检查与 Skill `quick_validate.py`；
8. Windows PyInstaller agent 构建、打包后 CLI/MCP smoke、Tauri build 与 Rust tests；
9. 全量 pytest、Ruff、Vitest、TypeScript/Vite build、PowerShell release self-tests 和 `git diff --check`。
