# CC Port 项目身份改名规格

## 目标

- [KNOWN] 项目版本统一为 `0.5.0`。置信度：HIGH。
- [KNOWN] 产品展示名统一为 `CC Port`，仓库与命令行 slug 统一为 `cc-port`。置信度：HIGH。
- [KNOWN] Python 包名统一为 `cc_port`，代码标识符使用 `CcPort`，环境变量前缀使用 `CC_PORT_`。置信度：HIGH。
- [KNOWN] 公开仓库地址统一为 `https://github.com/Ling-ye/cc-port`。置信度：HIGH。

## 身份映射

| 边界 | 历史标识 | 新标识 |
| --- | --- | --- |
| 产品展示名 | `LPM` / `LingyePluginMarketplace` | `CC Port` |
| GitHub 仓库 slug | `LingyePluginMarketplace` | `cc-port` |
| Python distribution | `lingyepluginmarketplace` | `cc-port` |
| Python import | `lpm` | `cc_port` |
| CLI | `lpm` / `lpm-mcp` / `lpm-desktop-api` | `cc-port` / `cc-port-mcp` / `cc-port-desktop-api` |
| 桌面 crate / 二进制 | `lpm-desktop` | `cc-port-desktop` |
| 桌面 Rust lib | `lpm_desktop_lib` | `cc_port_desktop_lib` |
| Desktop API sidecar | `lpm-desktop-api` | `cc-port-desktop-api` |
| Tauri 产品 / identifier | `LPM Desktop` / `com.lingye.lpm.desktop` | `CC Port` / `com.lingye.cc-port.desktop` |
| PowerShell 标识符 | `Lpm` | `CcPort` |
| 环境变量前缀 | `LPM_` | `CC_PORT_` |
| 配置与状态目录 | `.config/lpm` / `%LOCALAPPDATA%/LPM` / XDG `lpm` | `.config/cc-port` / `%LOCALAPPDATA%/cc-port` / XDG `cc-port` |
| 所有权协议 | `.lpm-managed.json` / `managed_by: lpm` | `.cc-port-managed.json` / `managed_by: cc-port` |
| 项目链接协议 | `.lpm-linked` / `lpm-skills.md` | `.cc-port-linked` / `cc-port-skills.md` |
| 资源 manifest | `lpm.resource.json` / `lpm-resource.json` | `cc-port.resource.json` / `cc-port-resource.json` |
| Skill / MCP key | `lpm` | `cc-port` |
| 组件版本 | Python `0.4.0` / Desktop `0.1.0` | 全部 `0.5.0` |

## 构建与发布要求

- [KNOWN] sidecar 构建入口文件为 `tools/packaging/sidecar/cc_port_desktop_api_entry.py`，并导入 `cc_port.interfaces.desktop_api`。置信度：HIGH。
- [KNOWN] sidecar 输出基础名为 `cc-port-desktop-api`，Tauri 目标三元组后缀规则保持不变。置信度：HIGH。
- [KNOWN] Windows 构建共享模块的项目专用函数和脚本变量使用 `CcPort`，构建缓存与运行时环境变量使用 `CC_PORT_`。置信度：HIGH。
- [KNOWN] 发布目录结构、目标三元组、缓存 schema 与事务发布语义不因项目改名而改变。置信度：HIGH。
- [KNOWN] 文档中的安装命令、模块路径、配置路径、环境变量、可执行文件和仓库 URL 必须与新身份一致。置信度：HIGH。
- [KNOWN] 旧命令、导入、环境变量、状态目录和所有权标记不提供读取、迁移或双写兼容层；旧数据留在原位，由新版本完全忽略。置信度：HIGH。

## 图标要求

- [KNOWN] 图标由 `tools/packaging/icons/generate_icons.py` 程序化生成，不依赖人工编辑位图。置信度：HIGH。
- [KNOWN] 主视觉为蓝青渐变底色上的白色桥接箭头，不包含文字或字母。置信度：HIGH。
- [KNOWN] 输出保持 14 个 PNG 与 1 个 ICO，文件名及尺寸满足现有 Tauri 与 Windows 打包约定。置信度：HIGH。
- [KNOWN] 小尺寸图标必须使用同一几何图形并保持可辨识，不使用字体渲染。置信度：HIGH。

## 迁移顺序

1. [KNOWN] 用户先将 GitHub 仓库改名为 `cc-port`，并把本地 `origin` 更新为新地址；这两个外部动作已在源码迁移前完成。置信度：HIGH。
2. [KNOWN] 再改 manifest、Python 包路径和构建入口，并更新所有源码与文档引用。置信度：HIGH。
3. [KNOWN] 然后由 manifest 重生成锁文件，并由图标生成器重生成全部图标。置信度：HIGH。
4. [KNOWN] 随后运行单元测试、构建脚本检查和全仓身份残留扫描。置信度：HIGH。
5. [KNOWN] 验证通过后再改名本地工作区目录；该文件系统操作不与未验证的源码修改混在同一步。置信度：HIGH。

## 验收标准

- [KNOWN] 除本规格与对应 ADR 的历史映射外，tracked 源码、脚本、配置、测试和文档不再出现历史项目标识。置信度：HIGH。
- [KNOWN] `cc-port`、`cc-port-mcp` 与 `cc-port-desktop-api` 指向 `cc_port` 包中的对应入口。置信度：HIGH。
- [KNOWN] Python、NPM、Cargo、Tauri 与 Skill 元数据的组件版本均为 `0.5.0`。置信度：HIGH。
- [KNOWN] PowerShell 构建逻辑自测使用 `CcPort` 函数名并通过。置信度：HIGH。
- [KNOWN] sidecar 构建命令收集 `cc_port`，产物名为 `cc-port-desktop-api-{target_triple}`。置信度：HIGH。
- [KNOWN] Windows 发布产物名为 `cc-port-desktop.exe`、`cc-port-desktop-api.exe`、`CC Port_0.5.0_x64_en-US.msi` 与 `CC Port_0.5.0_x64-setup.exe`。置信度：HIGH。
- [KNOWN] 14 个 PNG 和 1 个 ICO 均可解码，尺寸与既有清单一致。置信度：HIGH。
- [KNOWN] README、Skill 元数据、领域上下文与发布文档均声明版本 `0.5.0` 和新仓库地址。置信度：HIGH。
