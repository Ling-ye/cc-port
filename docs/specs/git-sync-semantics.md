# Git 同步语义规格

## 目标

资源仓库遵循标准 Git 历史模型，支持多台电脑的本地提交、远端提交、三方合并和冲突解决，不以硬重置代替同步。

## 状态

同步状态包含：`clean`、`dirty`、`ahead`、`behind`、`diverged`、`unborn`、`no-remote`、`wrong-branch`。

状态计算基于本地 HEAD、`origin/<branch>` 和 merge-base。`fetch` 只更新远端跟踪引用，不修改工作区。

## 同步流程

1. dirty 工作区先生成资源级提交计划；仅 LPM 管理路径且敏感内容扫描通过后才允许用户确认提交。
2. `fetch` 后生成同步计划。
3. 仅 behind 时允许快进。
4. diverged 时在临时 worktree 中执行三方合并。
5. `registry.yaml` 按资源名称语义合并；资源内容冲突按整个资源选择 local 或 incoming。
6. 用户确认后将临时结果应用到正式分支。
7. push 只允许普通非强制推送；远端抢先更新时重新 fetch 和规划。

## 禁止行为

- 禁止用 `reset --hard origin/<branch>` 实现普通 pull。
- 禁止 force push。
- 禁止用通用 `git add -A` 自动提交资源仓库全部内容。
- 非管理路径、真实环境文件和疑似凭据内容必须阻断 commit/push。
- dirty 工作区可以查看状态和 fetch，但不能应用 merge。
- 当前分支与配置分支不一致时进入 `wrong-branch`，不得在错误分支上生成或应用合并。
- “全部使用本地/远端”仅是批量冲突选择，最终仍保留双方提交历史。
- 同一资源仓库的 clone/connect、plan、resolve、apply、cleanup、commit 和 push 必须持有仓库写锁。

## 临时工作树恢复

- 三方合并使用本机状态目录中的 detached worktree。
- 同步计划记录创建时间和最后更新时间。
- `conflict` 或 `ready` 状态超过 24 小时后可被列为陈旧计划。
- 陈旧计划只允许用户显式清理，不在应用启动或普通同步时自动删除。
- 清理同时移除工作树目录、Git worktree 注册并把计划标记为 `abandoned`。
- worktree 路径只能由 operation id 推导；持久化计划不能指定任意删除路径。

## 验收标准

- 两个 clone 可完成快进、非冲突分叉合并和同资源冲突选边。
- 合并后的历史同时包含双方提交。
- push race 不覆盖远端历史。
- 非管理文件不会被自动提交，待推送提交中的敏感内容会阻断 push。
- 两个 LPM 进程不能同时修改同一个资源仓库。
