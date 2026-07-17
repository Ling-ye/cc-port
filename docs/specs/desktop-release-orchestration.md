# 桌面发布编排规格

## 目标

桌面发布使用一个基于 Python 标准库的权威入口，统一完成依赖安装、质量门禁、sidecar 构建、Tauri 打包、产物收集、摘要计算和冒烟验证。

## 命令与兼容性

- 权威命令为 `python scripts/release_desktop.py`。
- `scripts/release-desktop.ps1` 仅作为旧命令的兼容转发层，不再承载发布逻辑。
- Python 脚本只能为当前宿主操作系统生成安装包；跨平台表示同一编排代码可在 Windows、macOS 和 Linux 上运行，不表示在任一系统上交叉生成其他系统的安装包。
- Windows 生成 MSI 和 NSIS，macOS 至少生成 DMG，Linux 至少生成 DEB、AppImage 或 RPM 之一。

## 工具链发现

- Python 始终使用启动脚本的当前解释器。
- Node.js、npm、Git、Cargo 和 rustc 优先从 `PATH` 发现；Windows 可回退到用户级、系统级和当前工作盘的标准安装位置。
- 所有外部命令使用参数数组执行，不通过 shell 拼接命令字符串。
- Node.js 必须满足 Vite 的最低版本：20.19+ 或 22.12+。

## 发布门禁

发布必须按以下顺序执行，任一步失败立即返回非零退出码：

1. 安装项目声明的 Python 依赖和锁文件约束的前端依赖。
2. 运行完整 Python 测试和 Ruff。
3. 运行前端测试、全依赖安全审计和生产构建。
4. 生成缺失图标并构建 PyInstaller sidecar。
5. 清理本次 Tauri 的已知输出后执行当前平台 release bundle。
6. 将产物复制到临时发布目录，验证当前平台必需安装包。
7. 对打包后的 sidecar 执行隔离状态目录冒烟测试。
8. 验证成功后替换正式发布目录并输出 SHA-256。

## 失败与产物完整性

- 测试失败不得继续打包；不得通过迁移脚本绕过失败门禁。
- 打包失败不得覆盖上一次已验证的正式发布目录。
- 正式发布目录不得混入上一次构建遗留的安装包。
- 发布脚本不得写入或输出 Git 凭据。

## 验收标准

- 旧 PowerShell 命令和新的 Python 命令进入同一个 Python 发布流程。
- Python 编排的纯逻辑具备单元测试，至少覆盖 Rust 目标解析、Node.js 版本门禁和各平台安装包判断。
- 当前 Windows 环境完成 Python/前端测试、MSI/NSIS 打包、SHA-256 输出和 sidecar 冒烟测试。
