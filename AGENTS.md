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
- 本地资产扫描必须包含所有已启用平台配置的 `skills_dir`、`mcp_json` 和 `plugins_dir`；自定义目录、UNC 路径和 WSL UNC 路径使用同一套资源发现、去重与链接安全规则。

## Registry v1 约束

- `registry.yaml` 是工具中立清单，只保存 `version: 1`、资源 `(kind, name)` 以及互斥的 `path` 或 `source`；不得写入派生元数据、健康缓存、删除历史、MCP 配置或 CC Port 专属设置。
- 实体资源内容或外部 `source` 是事实；已登记内容内部变化且仍有效时不得产生 Registry 修复项。
- MCP 配置必须脱敏后写入 `mcp/<name>/mcp.json|yaml|yml`，Registry 只保存路径。
- CC Port 专属平台和插件意图只能进入可选 `cc-port.yaml`；其他工具无需理解它。
- 每次远端刷新必须审计同一个 commit，但不得自动修改远端；Registry 不可用时远端仍可标记连接成功，本地扫描继续，依赖远端清单的动作全部阻断。
- Registry 修复必须重新 fetch 并校验 `plan_hash`；只允许暂存、提交和普通推送 `registry.yaml`，不得修改资源内容、`cc-port.yaml` 或其他文件，不得强推或自动合并竞态。
- Registry 缺失、YAML 损坏、不是普通文件或为链接时只报告且不可修复；普通加载器不兼容 v5/v6/v7。可解析 v7 只允许用户确认后从当前实体资源覆盖为 v1。
- 未知安全 `kind` 和 `source.type` 必须原样保留并只读展示；已知类型拼错字段必须报告 schema 错误。
- 凭据或疑似秘密不得出现在 Registry、diff、结构化错误、日志或提交中。

## 快速验证

- 后端：`.venv\Scripts\python.exe -m pytest tests/test_asset_sync.py -q`
- Registry：`.venv\Scripts\python.exe -m pytest tests/test_registry_v1.py tests/test_registry_audit.py tests/test_registry_interfaces.py -q`
- 链接探测：`.venv\Scripts\python.exe -m pytest tests/test_local_path_probe.py -q`
- 前端：在 `desktop` 目录执行 `npm.cmd exec vitest run -- src/features/resources/ResourcesView.test.tsx`
- 构建：在 `desktop` 目录执行 `npm.cmd run build`
- Rust 桥接：在 `desktop/src-tauri` 目录执行 `cargo test --lib`
- 不要创建 Git 提交；提交由维护者完成。

## 开发环境安装脚本

- `scripts/setup.ps1` 打印计划后必须直接执行，不得增加 `y/n` 或其他二次确认。
- `-CheckOnly` 必须保持只读；`-NonInteractive` 继续用于控制 WinGet 的静默安装参数。
