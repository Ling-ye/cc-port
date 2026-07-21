# Windows 桌面发布编排规格

## 目标

- [KNOWN] Windows x64 构建用户只执行 PowerShell 命令，不需要手工安装或调用 Python。置信度：HIGH。
- [KNOWN] 构建内部继续使用仓库 `.venv` 和现有 PyInstaller sidecar。置信度：HIGH。
- [KNOWN] 环境准备与完整发布分别只有一个公开入口。置信度：HIGH。

## 公开接口

```powershell
# 检查、安装并准备环境
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1

# 只检查，零写入
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -CheckOnly

# 自动准备环境并执行完整发布
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
```

- [KNOWN] 两个入口支持 `-NonInteractive`；默认模式汇总操作后只确认一次。置信度：HIGH。
- [KNOWN] `-CheckOnly` 不安装系统包、不创建或修改 `.venv`、不运行 pip/npm 安装，并以退出码表示环境是否完整。置信度：HIGH。
- [KNOWN] `release-desktop.ps1` 必须先调用 `setup.ps1`，不能复制第二套环境安装逻辑。置信度：HIGH。

## 平台与工具链

- [KNOWN] 正式支持 Windows 10/11 x64、Windows PowerShell 5.1 和 `x86_64-pc-windows-msvc`。置信度：HIGH。
- [KNOWN] 环境脚本检测 Python 3.10–3.12 x64、受支持的 Node.js、npm、Git、Rustup/Cargo/rustc 和 Visual Studio C++ Build Tools。置信度：HIGH。
- [KNOWN] 构建测试必须使用当前机器动态发现的 Git 可执行文件，不得包含开发机专属绝对路径。置信度：HIGH。
- [KNOWN] 缺失的系统工具通过精确 WinGet 包 ID 安装；WinGet 缺失时必须在系统修改前停止。置信度：HIGH。
- [KNOWN] 安装完成后必须刷新当前进程 PATH，并通过 `vswhere.exe` 与 `VsDevCmd.bat` 导入和验证 MSVC linker 环境；`VsDevCmd.bat` 必须在清除 `__VSCMD_*` / `EXTERNAL_INCLUDE` 等瞬时变量后的最小 Windows PATH 下执行，再将 MSVC 目录合并回原 PATH，且不得把 `__VSCMD_*` 写回父进程，避免过长 PATH 或同会话二次导入触发 cmd「输入行太长」。置信度：HIGH。
- [KNOWN] 不兼容的仓库 `.venv` 必须重命名备份，不得直接删除。置信度：HIGH。

## Rust 目标解析

1. 先解析 Cargo 路径。
2. 优先使用 Cargo 同目录的 rustc proxy，再尝试 `rustup which rustc`，最后才使用其他 PATH/fallback 候选。
3. 优先解析 `rustc --print host-tuple`。
4. 回退解析 `rustc -vV`，并容忍 BOM、ANSI 控制码、大小写和前导空白。
5. 目标不是 `x86_64-pc-windows-msvc` 时，选择或安装 `stable-x86_64-pc-windows-msvc`。
6. 两种解析都失败时，错误必须包含 rustc 绝对路径、退出码和截断输出。

## 发布门禁

[KNOWN] 发布按以下顺序执行，任一步失败立即停止：置信度：HIGH。

1. 环境检查、系统工具补齐、`.venv` 与锁定 npm 依赖同步。
2. 无外部测试框架的 PowerShell 构建逻辑自测。
3. 完整 pytest 和 Ruff。
4. Vitest、`npm audit --package-lock-only --audit-level=moderate` 与前端生产构建。
5. 缺失图标生成和 PyInstaller sidecar 完整重建。
6. 删除本次已知 Tauri 输出并执行 MSI/NSIS release build。
7. 将桌面 exe、sidecar、MSI 和 NSIS 复制到同级临时发布目录。
8. 在隔离状态目录运行临时 sidecar，并验证 JSON `ok` 响应。
9. 验证成功后替换正式目录并输出绝对路径、大小和 SHA-256。

## 产物与失败语义

- [KNOWN] staging 目录必须在移动现有正式产物前通过安全路径检查并确认为真实目录；缺失时抛出与操作系统语言无关的项目错误，正式产物保持不变。置信度：HIGH。
- [KNOWN] 正式目录固定为 `release/desktop/x86_64-pc-windows-msvc/`。置信度：HIGH。
- [KNOWN] Windows 发布必须同时存在 MSI 与名称以 `-setup.exe` 结尾的 NSIS。置信度：HIGH。
- [KNOWN] 测试、构建、安装包验证或 sidecar 冒烟失败不得覆盖上一次已验证目录。置信度：HIGH。
- [KNOWN] 正式目录替换失败时必须恢复旧目录。置信度：HIGH。
- [KNOWN] 删除和移动只能操作经过验证的预期父目录直接子项。置信度：HIGH。
- [KNOWN] 发布脚本不得读取、写入或输出 Git 凭据，不执行安装、代码签名或上传。置信度：HIGH。

## 验收标准

- [KNOWN] Windows PowerShell 5.1 解析两个入口、共享模块和自测文件时没有语法错误。置信度：HIGH。
- [KNOWN] 自测覆盖版本规则、Rust 装饰输出、可执行文件 fallback、安全目录、MSI/NSIS 判断、staging 缺失预检以及发布替换与回滚。置信度：HIGH。
- [KNOWN] `setup.ps1 -CheckOnly` 在环境完整时返回 0，并保持 `.venv` 与 `node_modules` 不变。置信度：HIGH。
- [KNOWN] 一条 `release-desktop.ps1` 命令完成所有门禁，生成并哈希桌面 exe、sidecar、MSI 和 NSIS，且 sidecar 冒烟通过。置信度：HIGH。
