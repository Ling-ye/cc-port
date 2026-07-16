# 同步计划完整性规格

## 目标

资源同步计划是机器本地的可恢复状态，不是可信指令。读取、应用、取消或清理计划时，任何持久化字段都不得绕过当前仓库和状态目录的安全边界。

## 计划格式

- 计划必须包含 `schema_version`、`operation_id`、仓库规范路径、Git common-dir、远端 URL、分支、提交引用和状态。
- `operation_id` 必须与 `sync/<operation-id>/plan.json` 的目录名一致。
- worktree 的实际路径只允许由 `operation_id` 推导为 `sync/<operation-id>/worktree`。
- `plan.json` 中不得保存或信任任意可执行的清理路径。
- 未知版本、缺失字段、非法状态或损坏 JSON 进入 `invalid`，不得 apply、resolve、cancel 或 cleanup。

## 仓库绑定

- 创建计划时记录规范化仓库路径、Git common-dir、远端 URL 和分支。
- 修改仓库内容前重新计算绑定信息。
- 仓库被替换、远端变化、common-dir 变化或计划指向其他仓库时必须拒绝。
- apply 仍需验证工作区干净且 HEAD 等于计划记录的本地提交。

## 删除边界

- worktree 删除目标必须位于当前 LPM 状态目录的 `sync/<operation-id>/` 内。
- 删除前必须验证目标规范路径、父目录、符号链接和 Windows reparse point。
- 边界验证失败时不调用 Git worktree remove，也不执行递归删除。

## 验收标准

- 篡改 `worktree_path` 为项目目录、用户目录、绝对路径或 `..` 不会删除目标。
- 计划文件中的 operation id 与目录不一致时拒绝加载。
- 同一路径被另一个 Git 仓库替换后，旧计划不能应用。
- 旧版或损坏计划可以被列为无效状态，但不能执行写操作。
