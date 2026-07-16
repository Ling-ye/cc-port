# 资产同步 API / CLI 迁移指南

## 新入口

桌面端和 CLI 统一使用资产级三段式流程：

1. 读取 `AssetInventory`，定位 `kind + name + platform` 对应的平台行。
2. 创建 `AssetActionPlan`，记录远端提交、目标断言、本地源指纹和用户选择。
3. 使用 `operation_id` 执行计划；执行前重新抓取并重新计算所有路径和指纹。

Desktop API：

```text
asset_inventory
asset_action_plan
asset_action_apply
```

CLI：

```bash
lpm asset list
lpm asset list --scan-local
lpm asset plan download --kind skill --name demo --platform cursor
lpm asset apply <operation-id>
```

机器调用可在三个 CLI 子命令上使用 `--json`。

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

新写接口必须同时传入 `kind`、`name` 和 `platform`。出现多个本地实例时还必须传入 `local_instance_id`。

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
