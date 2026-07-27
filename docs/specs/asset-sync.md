# 资产级双向同步规格

> 当前桌面端与新批量接口以 [统一资源清单与批量双向同步规格](unified-resource-inventory.md)
> 为准。本文件保留单资产比较、并发和安全规则；平台级清单展示与“一次远端写入只包含一个
> 资产”的旧限制已被逻辑资源清单和显式批量计划替代。

## 范围

资产同步比较私有资源仓库配置分支的最新远端提交与各 AI 工具平台上的本地实例。Git 仅用于远端快照、历史、并发检测和普通推送，不向用户暴露分支合并流程。

## 清单

- 每一行代表一个“资源身份 × 平台 × 本地实例”。
- 状态为 `remote-only`、`local-only`、`same`、`content-different`、`metadata-only`、`read-only-reference`、`target-conflict` 或 `uncomparable`。
- 普通加载读取已配置平台；显式扫描还包含已检测但未配置的平台。
- external 或 owned 且没有私库 `path` 的项目为只读引用。
- 缺失只产生上传或下载候选，绝不产生隐式删除。

## 动作

- `download`：把远端私库资产安装到单个平台。
- `upload`：以单个本地实例整体替换或创建远端私库资产。
- `copy-to-local`：保持远端资源不变，以用户输入的新名称安装本地副本。
- `copy-to-remote`：保持原远端资源不变，以用户输入的新名称创建私库资产。
- `set-platform-install-name`：只修改远端元数据；成功推送后用户才能执行下载。

单项接口保留为兼容封装；桌面主流程和 CLI 批量命令使用逻辑资源级计划，不再维护第二套安全语义。

## API 与 CLI

- Desktop API 主入口为 `asset_inventory`、`asset_batch_plan`、`asset_batch_apply`；`asset_action_plan`、`asset_action_apply` 作为单项兼容封装。
- CLI 主入口为 `cc-port asset list`、`cc-port asset upload`、`cc-port asset download`；单项 `plan/apply` 暂时保留，并提供 `--json` 机器输出。
- 所有机器 JSON 输出必须是可直接解析的 UTF-8 JSON，不得包含 ANSI 颜色码、Rich 样式或终端相关转义序列；终端是否支持颜色不得改变输出字节语义。
- 批量接口携带逻辑资源键、方向和用户决策；下载携带目标平台，多本地版本上传通过 `local_instance_id` 选择来源。
- `AssetPlatformRow.available_actions` 由服务端计算，前端不得自行推导可写性。
- 旧 `resource_sync_*`、`resource_commit_*` Desktop API 和 `cc-port resource pull/push/sync-*` 只保留一个发布版本，并返回或输出弃用警告。

## 安全

- 下载覆盖使用路径锁、备份、验证和失败回滚。
- 未管理目标只有在计划中显式确认后才能覆盖。
- 内容复制遵循统一资源排除策略，不复制符号链接、真实环境文件、依赖或构建产物。
- MCP 指纹和写入值使用脱敏、规范化配置。
- 上传只安全改写成功派生出的 `description`、`version`、`author`、`license`、`mcp_config`；缺失字段不清空远端值。
- 同类型、不同名称但内容相同只产生警告，显式确认后允许创建。

## 计划与并发

- 写计划记录远端提交、目标资产存在性和指纹、本地源指纹、解析出的目标路径以及用户选择。
- apply 必须重新计算路径与指纹，不信任持久化值。
- 若分支提交变化但目标资产断言仍成立，在最新提交上重放操作并普通推送。
- 若目标资产已新增、删除或改变，返回 `stale-target`。
- 推送竞态允许重新抓取并重放一次；第二次失败返回用户重试。
- 同一批上传的所有有效资源合并为一次提交；最终推送失败时所有已准备项统一失败，禁止 force push。

## 旧模型阻断

旧本地资源仓库存在 dirty、ahead、diverged、wrong-branch 或待处理旧同步计划时，允许读取和扫描，但阻断新资产模型的远端写入。用户必须通过保留一版的弃用命令处理旧状态。
