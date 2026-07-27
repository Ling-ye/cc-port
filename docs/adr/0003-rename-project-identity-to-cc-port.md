# 将项目身份统一改名为 CC Port

- [KNOWN] 状态：Accepted。置信度：HIGH。
- [KNOWN] 决策版本：`0.5.0`。置信度：HIGH。

## Context

- [KNOWN] 历史身份同时使用 `LingyePluginMarketplace`、`LPM`、`lpm`、`Lpm` 与 `LPM_`，不同生态边界的命名规则没有被明确区分。置信度：HIGH。
- [INFERRED] 继续保留多套历史简称会让仓库 URL、安装命令、Python 导入、桌面产物和构建脚本持续漂移。置信度：HIGH。

## Decision

- [KNOWN] 产品展示名使用 `CC Port`，GitHub 仓库和外部 slug 使用 `cc-port`。置信度：HIGH。
- [KNOWN] Python 包使用 `cc_port`，PowerShell 与类型标识符使用 `CcPort`，环境变量前缀使用 `CC_PORT_`。置信度：HIGH。
- [KNOWN] 桌面程序与 sidecar 分别使用 `cc-port-desktop` 和 `cc-port-desktop-api`。置信度：HIGH。
- [KNOWN] Tauri 产品名与 identifier 分别使用 `CC Port` 和 `com.lingye.cc-port.desktop`；这是独立的应用与 WebView 身份。置信度：HIGH。
- [KNOWN] 配置、状态、所有权与项目链接协议只读取 `cc-port` 新身份；旧路径和标记留在原位但不迁移、不删除也不兼容读取。置信度：HIGH。
- [KNOWN] 项目图标改为无文字的蓝青渐变白色桥接箭头，以避免把可变简称固化进位图。置信度：HIGH。
- [KNOWN] 历史身份只保留在本 ADR 和项目身份改名规格的迁移映射中。置信度：HIGH。

## Consequences

- [KNOWN] 仓库内导入路径、命令、环境变量、缓存输入、构建产物、文档和测试必须在同一版本迁移。置信度：HIGH。
- [KNOWN] 现有脚本或用户配置引用历史命令、环境变量、路径或所有权标记时需要显式更新；本次决策不提供别名、迁移器或双写。置信度：HIGH。
- [KNOWN] 用户已在源码迁移前完成 GitHub 仓库改名和 `origin` 更新；本地目录改名仍属于源码验证后的外部操作。置信度：HIGH。
- [INFERRED] 无文字图标能减少未来名称调整导致的位图迁移，但仍需维持稳定的图形资产和颜色规范。置信度：HIGH。

## Rejected Alternatives

- [KNOWN] 拒绝只改展示名：该方案会保留旧包名、命令和产物名，无法形成单一身份。置信度：HIGH。
- [KNOWN] 拒绝在 Python 包名中使用连字符：Python 导入标识符不接受连字符。置信度：HIGH。
- [KNOWN] 拒绝长期保留历史命令别名：该方案扩大测试矩阵，并与清除历史标识的验收标准冲突。置信度：HIGH。
