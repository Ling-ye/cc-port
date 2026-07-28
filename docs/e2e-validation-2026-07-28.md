# 真实端到端验证记录（2026-07-28）

## 范围

- Run ID：`e2e-20260728T041712Z-029049c9`
- 资源仓库：`git@github.com:Ling-ye/LingyeAIResources.git`
- 分支：`main`
- CC Port 基线提交：`64c228601de7e7d44a7e8984a8824c86a6cdedce`
- 隔离配置、状态、操作记录、快照和测试 clone：
  `D:\Temp\cc-port-e2e-20260728T041712Z-029049c9`
- 真实安装目标：
  `C:\Users\Lingye\.codex\skills`、
  `C:\Users\Lingye\.cursor\skills`、
  `C:\Users\Lingye\.cursor\commands`

测试保留远端提交与 `cc-port-e2e-*-029049c9` 资源。用户原有
`C:\Users\Lingye\LingyeAIResources` dirty clone 未参与写入；测试前后仍为原 HEAD
`49f940f8a7b752c51a7a8d48ffeefd8c7be359dd` 和 11 条工作区变更。

## 1. 拉取远端，然后扫描本地

第一次成功基线使用 `asset list --scan-local --refresh-remote --global --json`：

- `remote_available = true`
- `scanned_local = true`
- `remote_commit = 49f940f8a7b752c51a7a8d48ffeefd8c7be359dd`
- 30 个逻辑资源、5 个远端资源、33 个本地实例

随后又显式执行 `resource pull`，确认隔离 legacy workspace 是 clean Git 仓库；紧接着
使用 `asset list --scan-local --cached-remote --global --json` 扫描，得到：

- `remote_commit = 361ac27fe843d54dbb514e3b3d5fc43ac7004c3b`
- `remote_available = true`
- `remote_warning = ""`
- `scanned_local = true`
- `legacy_write_blocker = ""`

## 2. 远端 Skill/Prompt 下载与真实调用

测试前置提交 `06efb1b369e5121e2e85b22e1d0145b57148d2c6` 加入：

- `skill:cc-port-e2e-remote-skill-029049c9`
- `prompt:cc-port-e2e-remote-prompt-029049c9`

CC Port 下载 dry-run 的结果是 3 个可执行项、0 个 blocker、1 个预期 skip：

- Codex Skill：create
- Cursor Skill：create
- Cursor Prompt：create 到
  `C:\Users\Lingye\.cursor\commands\cc-port-e2e-remote-prompt-029049c9.md`
- Codex Prompt：skip，因为 Codex 未声明 Prompt 支持

实际批量下载成功，三个写入操作的 `operation_status` 都是 `succeeded`，且 Skill
目录 marker 与 Prompt 邻接 sidecar 都记录了完整 `resource_key`。

最终本地文件与远端 Git blob 逐字节 SHA-256 一致：

| 文件 | SHA-256 |
| --- | --- |
| Codex/Cursor 远端 Skill `SKILL.md` | `482e375dcc1d48e1554958744625df870fc7cfd3e3312d6599eb595b7f452d58` |
| Cursor 远端 Prompt 命令 | `811c58d4ee96cc7607d09c664766d6e611424544aa73a2f7c0670947ec730565` |

Codex CLI `0.144.2` 真实调用：

```text
CODEX_HOME=/mnt/c/Users/Lingye/.codex /home/lingye/.local/bin/codex exec \
  --ephemeral --sandbox read-only \
  --skip-git-repo-check --json \
  'Use $cc-port-e2e-remote-skill-029049c9 and follow it exactly.'
```

模型返回：

```text
REMOTE_SKILL_NONCE_029049C9
```

Cursor Agent CLI `2026.07.23-e383d2b` 已通过官方安装器安装并完成浏览器登录。
第一次非交互调用被 workspace trust 门禁阻断；确认专用 workspace 为空后，只加入
`--trust`，未使用 `--force` 或 `--yolo`。

Cursor Prompt 真实调用：

```text
HOME=/mnt/c/Users/Lingye /home/lingye/.local/bin/cursor-agent \
  --print --output-format stream-json --mode ask --sandbox enabled --trust \
  --workspace /mnt/d/Temp/cc-port-e2e-20260728T041712Z-029049c9/cursor-invoke-workspace \
  '/cc-port-e2e-remote-prompt-029049c9'
```

结果为：

```text
REMOTE_PROMPT_NONCE_029049C9
```

Cursor Skill 的第一次标准 `$Skill` 调用命中了同名 Codex 副本，因此不计为 Cursor
目录验证。随后把该单个 Codex 测试目录临时原子移出 `skills`，并用 shell trap 保证
恢复，再执行：

```text
HOME=/mnt/c/Users/Lingye /home/lingye/.local/bin/cursor-agent \
  --print --output-format stream-json --mode ask --sandbox enabled --trust \
  --workspace /mnt/d/Temp/cc-port-e2e-20260728T041712Z-029049c9/cursor-invoke-workspace \
  'Use $cc-port-e2e-remote-skill-029049c9 and follow it exactly.'
```

`stream-json` 的 read tool 路径明确为
`/mnt/c/Users/Lingye/.cursor/skills/cc-port-e2e-remote-skill-029049c9/SKILL.md`，
最终 assistant 消息为：

```text
REMOTE_SKILL_NONCE_029049C9
```

Cursor 在 read tool 前另发出一条进度消息，因此聚合 `result` 还包含该进度文本；
终态 assistant 内容与 Skill 要求的单一 nonce 一致。trap 已恢复 Codex Skill，
Codex/Cursor 两份 `SKILL.md` 的 SHA-256 仍相同，专用 workspace 仍为空。

## 3. 扫描本地 Skill/Prompt，并上传远端

本地创建两项，其中 Skill 通过 Skill validator 校验：

- `skill:cc-port-e2e-local-skill-029049c9`
- `prompt:cc-port-e2e-local-prompt-029049c9`

扫描结果对两项均为：

- `status = local-only`
- `remote_status = missing`
- `remote.exists = false`
- `available_actions` 包含 `upload`

上传 dry-run：2 个 create、2 个 executable、0 个 blocker。实际 `asset upload`
把两项放入同一个提交：

`980bcca47b978d7bda0115ff3fc87ec290446314`

`git ls-remote` 返回同一个 SHA；独立 clone 确认提交标题为
`cc-port: batch upload 2 assets`，registry、Skill、Prompt 都存在。Prompt 本地文件
与该提交中的 Git blob SHA-256 均为：

`b8c2878edd06c574ebaa15ce1e80241fc85a6173d57bf633e91e4dc0cfbbc858`

## 4. description 差异识别与双向覆盖

本地 Skill description 从 version one 改成 `version two from local`，并把正文验收
nonce 更新为 version two 后，扫描返回：

- `status = content-different`
- `metadata_differences = ["description"]`
- 本地实例 description 是 version two，远端 description 仍是 version one

上传 dry-run 为一个 update、0 个 blocker；实际上传提交：

`439d5e59fd703cbf21b5a47b1aa77e32af925f93`

`git ls-remote` 和独立 clone 都确认远端 `SKILL.md` 与 registry description 已变为
version two。

再从独立 clone 把远端 description 改成 `version three from remote`，同时把正文
验收 nonce 更新为 version three 并推送：

`361ac27fe843d54dbb514e3b3d5fc43ac7004c3b`

刷新后再次得到 `content-different` 与 `metadata_differences = ["description"]`。
未显式确认时下载计划因 unmanaged target 阻断；choices 文件加入
`overwrite_unmanaged: true` 后计划变成一个 update、0 个 blocker。实际下载成功，
最终扫描返回：

- `status = same`
- `metadata_differences = []`
- 本地 description 是 version three
- 本地 `SKILL.md` 与远端 Git blob 逐字节相同

最终本地 Skill 主载荷与远端 Git blob SHA-256 均为：

`908df740ea5500b23578a52983898636398e877392b0c66687c033b6b32b03fb`

## 5. 真实测试发现并修复的问题

1. CC Port 此前没有 Cursor Prompt 的文件式安装目标。新增 `prompts_dir`，Cursor
   默认映射到 `~/.cursor/commands/<install-name>.md`，Codex 仍只支持 Skill。
2. 文件式 Prompt 需要邻接 ownership sidecar，并与内容在同一事务中锁定、备份和
   回滚；marker 经同目录临时文件原子替换，写后重新验证 `resource_key` ownership；
   歧义 Markdown 载荷、dangling symlink 和 sidecar symlink 均 fail closed。
3. Windows Git 的全局 `core.autocrlf` 曾使刚上传的 LF Prompt 在内部 CRLF snapshot
   中被误报为 `content-different`。现在只有 CC Port 内部 asset transport/write
   clone 使用 repo-local 配置关闭宿主 `core.autocrlf` 并重写 checkout；旧 snapshot
   会在格式标记文件 `.cc-port-snapshot-format` 的内容不等于
   `host-autocrlf-disabled-v1` 时安全重建。用户 clone、全局 Git 与逐字节
   `resource_hash_path` 语义没有改变。本次绑定仓库没有 `.gitattributes`，五个
   主载荷与 Git blob 哈希相同是下方记录的实测事实；该设置不覆盖仓库
   `.gitattributes` 中的 `eol` 属性或 clean/smudge filter，也不声明从 object
   database 逐 blob 物化。
4. 自定义 Cursor `prompts_dir` 若位于默认工具根目录以外，原扫描器不会发现其中
   的命令。现在已启用平台的配置目录会参与扫描，并与默认目录结果去重。
5. 内部 transport/snapshot 路径现在校验 canonical parent，末级 symlink 只 unlink；
   snapshot 先在同一 cache root 完整构建临时副本再替换，复制失败保留旧 cache。
6. 远端 snapshot 不再复制保留的格式标记，`registry.yaml` 必须是 snapshot 内普通
   文件；registry 资源路径只要越界或任一组件是 symlink 就 fail closed。
7. 文件式 Prompt 的邻接 sidecar 路径由预期文件目标决定，不随目标当前是目录、
   文件或缺失而漂移；ownership marker 原子替换，写后校验失败会连同内容一起回滚。

修复后对四个 E2E 资源的最终扫描全部为 `same`，所有
`metadata_differences` 都为空；五个本地主载荷文件与相应 Git blob 的 SHA-256
全部一致。

## 6. 回归

- Python：`299 passed, 10 skipped`
- Ruff：通过
- Desktop i18n gate：通过
- Desktop Vitest：`74 passed`
- Desktop TypeScript + production Vite build：通过
- `git diff --check`：通过

10 个 Python skip 都是当前 Windows 环境没有创建测试 symlink 的权限；对应
symlink 测试在支持该权限的 Linux CI 上可执行。

## 远端提交链

```text
361ac27 test: change e2e skill remotely 029049c9
439d5e5 cc-port: batch upload 1 assets
980bcca cc-port: batch upload 2 assets
06efb1b test: seed cc-port e2e remote fixtures 029049c9
49f940f Merge pull request #1 from Ling-ye/agent/fix-caveman-skill-format
```
