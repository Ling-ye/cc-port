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
- WSL LX 符号链接必须阻断单个资源并给出 Windows 原生链接或复制模式指引；不得自动调用 WSL 桥接读取。
- 资源内部的嵌套链接、悬空链接、循环链接、不可读取或未知 reparse point 必须 fail closed，但单个异常条目不得中断整次本地扫描。
- 远端仓库快照继续拒绝符号链接，不得复用本地根级链接的放行逻辑。

## 快速验证

- 后端：`.venv\Scripts\python.exe -m pytest tests/test_asset_sync.py -q`
- 链接探测：`.venv\Scripts\python.exe -m pytest tests/test_local_path_probe.py -q`
- 前端：在 `desktop` 目录执行 `npm.cmd exec vitest run -- src/features/resources/ResourcesView.test.tsx`
- 构建：在 `desktop` 目录执行 `npm.cmd run build`
- Rust 桥接：在 `desktop/src-tauri` 目录执行 `cargo test --lib`
- 不要创建 Git 提交；提交由维护者完成。

## 开发环境安装脚本

- `scripts/setup.ps1` 打印计划后必须直接执行，不得增加 `y/n` 或其他二次确认。
- `-CheckOnly` 必须保持只读；`-NonInteractive` 继续用于控制 WinGet 的静默安装参数。
