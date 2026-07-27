# CC Port

> 把分散在 Codex、Claude Code、Cursor、Windsurf 和 OpenCode 的 Skill、MCP、Rule、Prompt、Plugin，安全同步到你自己的私有 Git 仓库。

[English](README.en.md) · [下载 Windows 安装器](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.1) · [快速开始](docs/getting-started.md) · [报告问题](https://github.com/Ling-ye/cc-port/issues)

[![CI](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml/badge.svg)](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?logo=windows)
![Public Beta](https://img.shields.io/badge/status-public_beta-orange)

CC Port 是一个面向多 AI coding 工具的本地桌面资源管理器。它扫描每个工具的原生目录，以你控制的私有 Git 仓库作为跨设备事实源，并在真正写入前生成明确的操作计划。

## 一眼看懂

| 资源清单与平台状态 | 写入前的操作计划 | 设置与环境诊断 |
| --- | --- | --- |
| ![资源清单与平台状态](docs/assets/screenshots/resources-overview.png) | ![写入前的操作计划](docs/assets/screenshots/operation-plan.png) | ![设置与环境诊断](docs/assets/screenshots/settings-diagnostics.png) |

## 它解决什么问题

- **配置散落**：Skill、MCP、Rule、Prompt 和 Plugin 分别藏在多个工具目录中，难以盘点。
- **多设备漂移**：同一资源在不同电脑和工具中逐渐变成不同版本。
- **同步不安全**：复制目录容易覆盖手工配置，删除和失败操作也难以恢复。
- **凭据风险**：MCP 配置可能混入 Token 或环境变量字面值，不适合直接提交。

CC Port 将远端仓库快照与每个平台的本地实例逐项比较。上传、安装、另存副本和设置安装别名都先生成计划；写入使用备份、目标锁、结果校验和失败回滚。

## 五步开始

1. 安装 [Git for Windows](https://git-scm.com/download/win)，并确认 Git Credential Manager 可用。
2. 从 [v0.5.1 Public Beta](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.1) 下载 `cc-port_0.5.1_windows_x64_setup.exe`。
3. 在 GitHub 创建一个空的私有仓库，作为你自己的资源仓库。
4. 启动 CC Port，在“设置”中粘贴仓库的 HTTPS 地址并完成验证。
5. 扫描本机资源，在资源页逐项选择上传到仓库或安装到目标工具。

安装包已经包含桌面程序和 Python sidecar；普通用户不需要安装 Python、Node.js 或 Rust。完整流程、首次配置和卸载方式见[快速开始](docs/getting-started.md)。

## 安全边界

- **仓库归你所有**：CC Port 不提供托管云服务；资源保存在你指定的 Git 仓库。
- **凭据交给系统**：桌面端通过 Git Credential Manager 使用系统凭据，不读取或保存 GitHub Token。
- **先计划再写入**：桌面端和 CLI 在写操作前展示目标、动作与阻断原因。
- **不覆盖未接管内容**：所有权标记用于区分 CC Port 管理项与手工维护项。
- **可恢复写入**：安装、卸载、部署和恢复使用持久化事务、备份与失败回滚。
- **MCP 密钥占位**：采集 MCP 配置时，环境变量字面值会替换为 `${SECRET_NAME}` 占位符。
- **缺失不等于删除**：远端缺少某项资源不会触发隐式删除。

发现安全问题时，请不要创建公开 Issue；按照[安全策略](SECURITY.md)使用 GitHub 的私密漏洞报告。

## 支持范围

### 资源类型

| 类型 | 扫描与登记 | 私有仓库同步 | 安装到工具 |
| --- | :---: | :---: | :---: |
| Skill | ✓ | ✓ | ✓ |
| MCP Server | ✓ | ✓ | ✓ |
| Rule | ✓ | ✓ | ✓ |
| Prompt | ✓ | ✓ | ✓ |
| Plugin | ✓ | ✓ | ✓ |

### AI coding 工具

| 工具 | 状态 | 默认可写资源 |
| --- | --- | --- |
| Codex | 稳定 | Skill |
| Claude Code | 稳定 | Skill、MCP、Plugin |
| Cursor | 稳定 | Skill、MCP |
| Windsurf | 实验性 | Skill、MCP |
| OpenCode | 实验性 | Skill、MCP、Rule、Prompt、Plugin |
| Cline、Gemini CLI | 仅发现 | 暂无完整可写平台预设 |

高级用户可以在 `config.toml` 中添加自定义平台路径。具体字段见[配置示例](config/config.example.toml)。

## 三种入口

- **桌面 GUI**：日常扫描、比较、上传、安装和环境诊断。
- **CLI**：脚本化、批量操作、历史恢复和状态维护。
- **MCP Server**：让支持 MCP 的 AI coding 工具调用 CC Port 能力。

三种入口共享同一套 Python 核心逻辑。架构边界和同步状态机见[架构文档](docs/architecture.md)。

## 当前限制

- Public Beta 仅正式支持 Windows 10/11 x64。
- v0.5.1 安装器尚未代码签名，Windows SmartScreen 可能显示“未知发布者”。
- 目标电脑必须安装 Git for Windows，并配置 Git Credential Manager。
- 桌面端不会替你创建、删除仓库或修改仓库可见性。
- 当前没有自动更新；升级时请从 Releases 下载新版本。
- 桌面端聚焦资源管理；部分历史恢复和维护能力仍只在 CLI/Desktop API 提供。

遇到安装、登录或同步问题，请先查看[故障排查](docs/troubleshooting.md)。

## 文档

- [快速开始](docs/getting-started.md)
- [故障排查](docs/troubleshooting.md)
- [开发指南](docs/development.md)
- [架构](docs/architecture.md)
- [桌面打包与发布](docs/packaging-and-deployment.md)
- [v0.5.1 发布说明](docs/releases/v0.5.1.md)
- [功能规格](docs/specs/)
- [变更记录](CHANGELOG.md)

## 参与贡献

Bug、文档修正和小改动可以直接提交 PR。较大的功能或行为变化请先创建 Issue，确认问题和范围后再实现。详细规则见[贡献指南](CONTRIBUTING.md)。

## License

[MIT](LICENSE) © 2026 Lingye · [第三方软件声明](THIRD_PARTY_NOTICES.md)
