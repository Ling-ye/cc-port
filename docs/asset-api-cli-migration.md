# 资产同步 API / CLI 迁移指南

## 新入口

桌面端和 CLI 以逻辑资源清单与批量计划为主流程：

1. 读取 `AssetInventory.resources[]`，每个 `kind:name` 只出现一次；平台级比较行仅存在于服务内部，不出现在 Desktop API 或 CLI JSON 中。
2. 创建 `AssetBatchPlan`，输入资源键、方向、目标平台与冲突决策，得到规范化 `plan_hash`。
3. 使用同样的选择和 `plan_hash` 执行；服务端重新扫描、刷新远端并重建计划，哈希变化时拒绝写入。

Desktop API：

```text
asset_inventory
asset_batch_plan
asset_batch_apply
asset_action_plan
asset_action_apply
```

CLI：

```bash
lpm asset list
lpm asset list --scan-local
lpm asset upload --resource skill:demo --dry-run
lpm asset upload --all --yes
lpm asset download --resource skill:demo --platform cursor --dry-run
lpm asset download --all --platform cursor --platform codex --yes
lpm asset plan download --kind skill --name demo --platform cursor
lpm asset apply <operation-id>
```

机器调用可使用 `--json`；批量命令支持重复 `--resource`、`--all`、`--dry-run`、`--yes` 和 YAML `--choices`，下载额外支持重复 `--platform`。旧 `lpm env` 命令已删除。

环境采集、ZIP 导出/导入、仓库级环境 push/pull 差异和环境部署服务也已删除，不提供隐藏兼容入口。资产扫描只复用只读工具发现与 MCP 安全占位符处理。

## Desktop API 参数

`asset_inventory`：

```json
{
  "scan_local": true,
  "refresh_remote": true
}
```

`asset_action_plan`：

```json
{
  "action": "copy-to-remote",
  "kind": "skill",
  "name": "demo",
  "platform": "cursor",
  "local_instance_id": "optional-instance-id",
  "new_name": "demo-copy",
  "new_install_name": "",
  "overwrite_unmanaged": false
}
```

`asset_action_apply`：

```json
{
  "operation_id": "plan-operation-id"
}
```

`asset_batch_plan`：

```json
{
  "direction": "download",
  "resource_keys": ["skill:demo", "mcp:server"],
  "target_platforms": ["cursor", "codex"],
  "choices": [
    {
      "resource_key": "skill:demo",
      "platform": "cursor",
      "resolution": "overwrite",
      "overwrite_unmanaged": true
    }
  ]
}
```

`asset_batch_apply` 使用相同字段，并增加计划阶段返回的 `plan_hash`。批次不接受前端路径、指纹或可写性断言。

旧单项接口继续接受 `kind`、`name` 和 `platform`，内部复用相同安全语义。新批量接口只接受逻辑资源键；出现多个不同本地版本时，通过 choices 中的 `local_instance_id` 选择来源。

## 动作映射

| 新动作 | 语义 |
| --- | --- |
| `download` | 远端私库资产覆盖或创建单个平台本地目标 |
| `upload` | 单个平台本地实例整体覆盖或创建远端资产 |
| `copy-to-local` | 远端版本以用户输入的新名称安装为本地副本 |
| `copy-to-remote` | 本地版本以用户输入的新名称创建远端副本 |
| `set-platform-install-name` | 单独提交平台安装别名，不组合本地写入 |

缺失状态不映射为删除。卸载和删除继续使用独立入口。

## 一版兼容层

以下 Desktop API 保留一个发布版本，维持旧行为并在响应中返回 `deprecated: true` 和弃用警告：

```text
resource_commit_plan
resource_commit_push
resource_sync_status
resource_sync_plan
resource_sync_resolve
resource_sync_apply
resource_sync_cancel
resource_sync_push
resource_sync_stale
resource_sync_cleanup
```

以下 CLI 命令保留一个发布版本，执行前输出弃用警告：

```text
lpm resource pull
lpm resource push
lpm resource sync-*
```

旧工作区存在 dirty、ahead、diverged、wrong-branch 或未处理旧同步计划时，新资产模型仍允许读取和扫描，但阻断远端写入。先使用兼容命令提交、取消或清理旧状态。

## Registry v6 兼容

资源身份从名称升级为 `kind:name`。旧名称查询只在同名资源唯一时继续工作；如果 `skill:demo` 和 `prompt:demo` 同时存在，调用方必须传入 `kind`。

平台目标名称按以下优先级解析：

1. `platform_install_dirs[platform]`
2. 旧 `install_dir`
3. 资源 `name`

迁移不会自动删除旧同步计划、临时 worktree 或本地资源仓库内容。
