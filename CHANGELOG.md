# Changelog

本项目记录用户可见的功能、修复和兼容性变化。版本格式遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

## [0.5.4] - 2026-07-30

### Added

- 本地资产扫描覆盖所有已启用平台配置的 `skills_dir`、`mcp_json` 和
  `plugins_dir`，可通过 `\\wsl.localhost\...` 路径发现 Claude Code
  的普通 WSL Skill、MCP 配置和内容型 Plugin。
- 根级 Windows 原生符号链接和目录联接可以作为上传来源；逻辑安装路径与
  解引用后的内容路径分开处理，远端只接收普通文件快照。

### Changed

- 打开批量上传对话框时刷新远端快照并重新扫描本地实例；检查完成前只显示进度
  和取消入口，检查结果随计划返回，不再用旧资源清单推断冲突。
- 上传方向只在本地和远端都存在且内容或元数据不同时显示覆盖或重命名；
  本地新增资源不再误报为冲突，也不再显示下载方向的替换确认。

### Fixed

- 修复下载目标为根级原生悬空符号链接时，即使明确确认覆盖未接管目标仍被通用
  路径安全 blocker 阻断的问题；下载只删除链接本身，上传仍拒绝无安全指纹的来源。

### Security

- 上传计划及执行阶段同时校验链接类型、目标、reparse tag、内容指纹和
  `plan_hash`，链接重定向后拒绝旧计划。
- 资源内部的嵌套链接、悬空链接、循环链接、未知重解析点和 WSL LX
  符号链接按单个资源阻断；远端仓库快照继续拒绝任何符号链接。

## [0.5.3] - 2026-07-29

### Added

- 资源详情为 `content-different` 项增加按需内容 diff：以远端为基准，按文件展示本地新增、缺失和修改，支持在多个本地实例间切换，并对二进制或过大的结果给出提示。

## [0.5.2] - 2026-07-28

### Added

- Cursor Prompt 使用原生全局命令目标
  `~/.cursor/commands/<install-name>.md`，并可通过 `prompts_dir` 配置。

### Changed

- 文件式 Prompt 下载只接受 Markdown 文件，或根级恰好包含一个非符号链接
  `.md` 文件的目录；歧义载荷会在计划阶段阻断。
- 未设置 `prompts_dir` 的既有自定义平台继续使用
  `rules_dir/<install-name>`，保持旧配置兼容。

### Fixed

- 修复 Git for Windows `core.autocrlf` 污染内部资源快照、导致相同 Cursor Prompt
  被误报为 `content-different`；仓库自身 `.gitattributes` 与 clean/smudge filter
  仍保持 Git 原生优先级，不声明 object-level blob 精确 checkout。
- 修复自定义 Cursor `prompts_dir` 不在默认工具根目录时无法被本地扫描和上传的问题。
- 修复文件式 Prompt 目标暂时为同名目录时 ownership sidecar 路径漂移、失败回滚
  可能遗留邻接 sidecar 的问题。
- ownership marker 改为同目录原子替换，并在文件式 Prompt 下载完成前重新验证
  `resource_key` ownership；marker 未持久化时会回滚内容与 sidecar。

### Security

- 远端资源快照不再复制或跟随内部格式标记符号链接，并原子写入格式标记；同时要求
  `registry.yaml` 是快照内普通文件，拒绝任何包含符号链接组件或规范化后越界的
  资源路径。

## [0.5.1] - 2026-07-27

### Changed

- 新配置的默认资源仓库名改为 `cc-port-resources`；已有配置保持旧默认值。
- 桌面发布增加版本一致性校验，并生成只含 NSIS 安装器和校验文件的人工上传目录。
- 增加英文快速开始、故障排查和发布说明，修正 GitHub Release 可用的绝对链接。

### Security

- CI 增加完整 Git 历史的 Gitleaks 检查和 Markdown 链接检查。
- 将 `serde_with` 锁定版本升级至 3.21.0，修复其集合分配上限安全公告。
- Rust 发布构建重映射用户、工具链与仓库路径，避免桌面 EXE 泄露构建用户名。
- CI 验证 `glib 0.18.5` 不进入正式支持的 Windows 目标依赖图；该依赖仅由
  未正式发行的 Linux GTK/WebKit 目标引用。

## [0.5.0] - 2026-07-27

### Added

- Windows 10/11 x64 桌面应用，使用 React、Tauri 和 Python sidecar。
- Skill、MCP Server、Rule、Prompt 和 Plugin 的统一资源清单。
- Codex、Claude Code、Cursor、Windsurf 和 OpenCode 平台预设。
- 私有 Git 资源仓库绑定、远端快照和跨设备同步。
- 资源级上传、安装、另存副本和平台安装别名。
- 本地资源扫描、GitHub 引用收集和插件管理。
- 持久化写操作记录、备份、目标锁、结果校验和失败回滚。
- CLI、MCP Server 和桌面 JSON API。
- 简体中文与英文桌面界面。

### Security

- 桌面端使用 Git Credential Manager，不保存 GitHub Token。
- MCP 环境变量字面值在收集时替换为占位符。
- 未由 CC Port 管理的同名目标默认阻断覆盖和卸载。

### Documentation

- 重写中英文项目首页、快速开始、故障排查和开发指南。
- 增加开源治理、Issue 和 Pull Request 模板。

### Known limitations

- 安装器尚未代码签名，Windows SmartScreen 可能显示未知发布者。
- 仅正式支持 Windows x64 桌面发行。
- 需要外部 Git for Windows 和 Git Credential Manager。
- 没有自动更新。

[Unreleased]: https://github.com/Ling-ye/cc-port/compare/v0.5.4...HEAD
[0.5.4]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.4
[0.5.3]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.3
[0.5.2]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.2
[0.5.1]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.1
[0.5.0]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.0
