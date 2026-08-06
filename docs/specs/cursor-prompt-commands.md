# Cursor Prompt 命令安装规格

## 问题

平台适配器声明 Cursor 支持 `prompt`，但平台配置没有 Prompt 目标目录，
`PlatformProfile` 又把 `prompt` 与 `rule` 一起解析到 `rules_dir/<name>`。
默认配置中的 `rules_dir` 为空，因此 Cursor Prompt 没有可安装目标；即使手工填写
`rules_dir`，目录式目标也不是 Cursor 可通过 `/name` 调用的自定义命令文件。

## 范围

- 本规格只新增 Cursor 全局 Prompt 命令的扫描、上传、下载和覆盖语义。
- Codex 继续只声明并安装 Skill，不把 Prompt 伪装成 Codex 支持项。
- 资源仓库继续使用 `prompts/<name>/` 作为 Prompt 的可移植存储目录。
- 不增加第二套同步协议；单项和批量操作继续复用资产计划、事务、锁、备份、
  校验、回滚和普通 Git push。

## 平台目标

- `PlatformProfile` 增加 `prompts_dir`。
- Cursor 预设的 `prompts_dir` 是 `~/.cursor/commands`。
- 配置了 `prompts_dir` 的平台把 Prompt `<name>` 解析为
  `<prompts_dir>/<install-name>.md`。
- 未配置 `prompts_dir` 的既有自定义平台保留 `rules_dir/<install-name>` 的旧行为，
  避免破坏已有配置。
- `config.toml`、CLI 平台输出、Desktop API 和前端类型必须保留并显示同一个
  `prompts_dir` 值；桌面端切换平台开关不得丢失该值。
- 本地扫描必须包含已启用平台配置的 `prompts_dir`，即使目录不在默认
  `~/.cursor` 根下或目录名不是 `commands`；与默认扫描重叠时按平台、类型和路径去重。

## Prompt 内容

- 文件式 Prompt 目标只接受一个 Markdown 载荷。
- 远端目录只有一个根级 `.md` 文件时，该文件是安装载荷。
- 远端目录包含零个或多个根级 `.md` 文件时，文件式安装计划必须阻断并解释原因，
  不得任意选择文件。
- 从本地 Cursor 命令文件上传时，远端仍写入 `prompts/<name>/`，并保留原 Markdown
  文件名。
- 比较文件式目标时，远端指纹使用选中的 Markdown 载荷，因而相同内容显示
  `same`；远端资产指纹仍包含完整 registry 元数据和仓库内容，用于并发检测。
- CC Port 内部 asset transport 与临时写入 clone 必须通过 repo-local 配置关闭宿主
  `core.autocrlf` 并重写 checkout；同一 commit 的旧快照缺少当前格式标记时从
  transport 安全重建，比较仍使用逐字节哈希。
- 该设置不宣称从 Git object database 逐 blob 物化：
  仓库自己的 `.gitattributes`、`eol` 和 clean/smudge filter 继续优先并可能改变
  checkout 字节。格式版本名必须描述“关闭宿主 autocrlf”，不得使用
  `blob-exact` 等更强保证。
- `.cc-port-snapshot-format` 是 CC Port 保留的内部控制文件，不得从远端仓库复制；
  格式标记必须通过同目录临时文件原子替换，不得跟随远端或并发创建的符号链接。
- 快照的 `registry.yaml` 必须是快照内的普通非符号链接文件；registry 中的每个
  `entry.path` 必须规范化后仍位于快照内，且从快照根到目标的任一组件都不得是
  符号链接。违反任一条件时远端快照 fail closed，不得读取或安装越界内容。

## 所有权与事务

- CC Port 管理目录继续使用目录内的 `.cc-port-managed.json`。
- CC Port 管理文件使用同目录隐藏 sidecar
  `.<filename>.cc-port-managed.json`，其中保存完整 `resource_key` 和平台。
- 文件式 Prompt 的 sidecar 路径由预期目标类型决定，不得随目标当时是文件、目录或
  缺失而变化；同名目录被确认覆盖后，事务仍必须备份并回滚邻接 sidecar。
- ownership marker 必须经同目录临时文件 `flush`、`fsync` 后原子替换，避免跟随
  hardlink 别名或检查后的 symlink 竞态；下载成功前必须重新验证完整
  `resource_key` 与文件目标 ownership。
- 文件、sidecar 与任何状态文件都必须包含在同一本地事务的锁、备份、校验和回滚
  范围内。
- 未管理的同名命令文件仍要求用户显式确认覆盖。
- 下载后必须逐字节验证远端 Markdown 载荷与本地命令文件；失败时回滚文件和
  sidecar。

## 真实端到端验收

使用唯一的 `cc-port-e2e-*` 资源名和隔离的 CC Port 配置、状态目录及资源仓库工作
副本，但把安装目标指向真实 Windows 用户目录。

1. 刷新 `Ling-ye/LingyeAIResources` 的 `main`，随后使用缓存快照扫描本地，证明
   “刷新远端”与“扫描本地”是两个独立动作。
2. 通过 CC Port 把一个远端 Skill 安装到 Codex 与 Cursor，把一个远端 Prompt
   安装到 Cursor `commands`；Codex Prompt 必须显示为不支持且不得产生文件。
3. 使用固定 `CODEX_HOME` 的 `codex exec` 显式调用 Skill，并验证只存在于
   `SKILL.md` 的随机标记。
4. 使用 Cursor Agent CLI 显式调用同一 Skill 和 `/prompt-name`，分别验证只存在于
   Skill 与 Prompt 载荷中的随机标记。
5. 扫描真实目录后，通过 CC Port 在一次批量上传中提交一个本地 Skill 和 Prompt；
   执行结果的 commit 必须与 `refs/heads/main` 一致，独立 clone 中的 registry、
   文件和 SHA-256 必须匹配。
6. 修改本地 Skill 的 `description` 后，清单必须显示 `content-different` 且
   `metadata_differences` 包含 `description`；上传后远端覆盖成功并回到 `same`。
7. 从独立 clone 修改同一远端 Skill 的 `description` 并推送；刷新后必须再次显示
   `content-different`，下载后远端覆盖本地，文件哈希一致并回到 `same`。
8. 保留 E2E 资源与提交作为审计证据，不清理用户原有资源或现有 dirty 工作副本。
