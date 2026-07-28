# Changelog

本项目记录用户可见的功能、修复和兼容性变化。版本格式遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

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

[Unreleased]: https://github.com/Ling-ye/cc-port/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.1
[0.5.0]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.0
