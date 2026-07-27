# Changelog

本项目记录用户可见的功能、修复和兼容性变化。版本格式遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Documentation

- 重写中英文项目首页、快速开始、故障排查和开发指南。
- 增加开源治理、Issue 和 Pull Request 模板。

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

### Known limitations

- 安装器尚未代码签名，Windows SmartScreen 可能显示未知发布者。
- 仅正式支持 Windows x64 桌面发行。
- 需要外部 Git for Windows 和 Git Credential Manager。
- 没有自动更新。

[Unreleased]: https://github.com/Ling-ye/cc-port/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Ling-ye/cc-port/releases/tag/v0.5.0
