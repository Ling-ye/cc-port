# Windows 桌面打包与部署

## 支持边界

- [KNOWN] 桌面发布构建正式支持 Windows 10/11 x64 和 Windows PowerShell 5.1。置信度：HIGH。
- [KNOWN] 构建机需要 WinGet、网络连接和本仓库源码；Python、Node.js、Git、Rust 与 MSVC 可以由环境脚本安装。置信度：HIGH。
- [KNOWN] 用户只执行 PowerShell 入口；PyInstaller sidecar 仍由脚本管理的仓库 `.venv` 构建。置信度：HIGH。
- [KNOWN] 安装生成的 MSI/NSIS 后，目标电脑不需要 Python、Node.js 或 Rust。置信度：HIGH。

## 一键准备环境

[KNOWN] 在仓库根目录执行：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1
```

[KNOWN] 默认流程先显示所有系统安装与仓库环境操作，并只确认一次。置信度：HIGH。

[KNOWN] 只检查、不安装且不修改 `.venv` 或 `node_modules`：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -CheckOnly
```

[KNOWN] 显式非交互模式会跳过确认，并向 WinGet 传递静默安装与协议接受参数：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -NonInteractive
```

[KNOWN] 环境脚本执行以下行为：置信度：HIGH。

1. 验证 Windows x64 和 Windows PowerShell 5.1。
2. 检测 Python 3.10–3.12、Node.js 20.19+/22.12+、npm、Git、Rustup/Cargo/rustc、Visual Studio Build Tools 与 C++ workload。
3. 使用精确 WinGet 包 ID 安装缺失的 Python 3.12、Node.js LTS、Git、Rustup 和 Visual Studio 2022 Build Tools。
4. 刷新当前进程 PATH，并通过 `vswhere.exe` 与 `VsDevCmd.bat` 导入 MSVC/Windows SDK 环境。
5. 选择 `stable-x86_64-pc-windows-msvc` Rust toolchain。
6. 创建仓库 `.venv`；不兼容的旧环境会先重命名为 `.venv.backup-<timestamp>`。
7. 执行 `.venv\Scripts\python.exe -m pip install -e ".[dev,desktop]"`。
8. 执行 `npm ci --ignore-scripts --no-audit --no-fund`。

[KNOWN] 如果 WinGet 不存在，脚本会在系统安装前停止；先按微软文档安装或修复 App Installer，再重新执行同一命令：<https://learn.microsoft.com/windows/package-manager/winget/>。置信度：HIGH。

## 一键打包

[KNOWN] 更新代码后只执行：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
```

[KNOWN] 发布入口会自动调用环境准备流程，不要求提前单独运行 `setup.ps1`。置信度：HIGH。

[KNOWN] CI 或已明确接受自动安装行为的机器可以使用：置信度：HIGH。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1 -NonInteractive
```

[KNOWN] 发布门禁固定为：置信度：HIGH。

1. PowerShell 构建逻辑自测。
2. 完整 pytest 与 Ruff。
3. Vitest、`npm audit --package-lock-only --audit-level=moderate` 和 Vite 生产构建。
4. PyInstaller sidecar 完整重建。
5. Tauri release build，并要求同时生成 MSI 和 NSIS。
6. 在 `release/desktop/` 同级临时目录收集 exe 与安装包。
7. 使用隔离 `LPM_STATE_HOME` 运行打包后的 sidecar，并验证 JSON `ok` 响应。
8. 全部成功后替换上一次正式发布目录，并输出绝对路径、大小和 SHA-256。

- [KNOWN] 任一门禁失败都不会覆盖上一次已验证的正式产物。置信度：HIGH。
- [KNOWN] 正式发布目录不会混入上一次构建遗留的安装包。置信度：HIGH。
- [KNOWN] 发布命令不自动安装生成的安装包、不上传 Release、不执行代码签名。置信度：HIGH。

## Rust 目标检测

- [KNOWN] 发布流程先解析 Cargo，再优先选择与 Cargo 同目录的 `rustc.exe` 或 `rustup which rustc`，避免 Conda 等 PATH shim 抢占。置信度：HIGH。
- [KNOWN] 目标优先通过 `rustc --print host-tuple` 获取，再回退到容忍 BOM、ANSI 控制码和前导空白的 `rustc -vV`。置信度：HIGH。
- [KNOWN] Windows x64 发布必须得到 `x86_64-pc-windows-msvc`。置信度：HIGH。
- [KNOWN] 检测失败时错误会包含实际 rustc 路径、两个命令的退出码和截断输出。置信度：HIGH。

## 产物位置

[KNOWN] 验证后的正式目录为：置信度：HIGH。

```text
release/desktop/x86_64-pc-windows-msvc/
  lpm-desktop.exe
  lpm-desktop-api.exe
  msi/
    LPM Desktop_<version>_x64_en-US.msi
  nsis/
    LPM Desktop_<version>_x64-setup.exe
```

[KNOWN] Tauri 原始输出保留在 `desktop/src-tauri/target/release/`，PyInstaller 中间输出保留在 `build/sidecar/`。置信度：HIGH。

## 版本更新

[KNOWN] 桌面版本需要同步修改以下三个文件：置信度：HIGH。

```text
desktop/package.json
desktop/src-tauri/Cargo.toml
desktop/src-tauri/tauri.conf.json
```

[KNOWN] 修改版本后更新锁文件，再执行一键发布：置信度：HIGH。

```powershell
Push-Location .\desktop
npm install --package-lock-only
cargo check --manifest-path .\src-tauri\Cargo.toml
Pop-Location
```

## 常见失败

### WinGet 不存在

- [KNOWN] 安装或修复 App Installer 后，重新执行原命令；脚本不会自行下载或旁路安装 WinGet。置信度：HIGH。

### `link.exe` 不存在

- [KNOWN] 重新运行 `setup.ps1`；脚本会检查 Visual Studio C++ workload、导入 `VsDevCmd.bat` 并验证 `link.exe`。置信度：HIGH。

### Python 或 npm 首次安装很慢

- [KNOWN] 首次准备会下载完整开发、测试和 PyInstaller 依赖；后续仍会校验依赖，但通常复用本机缓存。置信度：HIGH。

### 正式目录无法替换

- [KNOWN] 关闭从 `release/desktop/` 启动的便携版或安装程序后重试；运行中的 exe 可能阻止目录移动。置信度：HIGH。

### 安装程序要求重启

- [KNOWN] 重启 Windows 后重新执行同一条 PowerShell 命令；环境脚本是幂等的，只补齐仍缺失的系统工具。置信度：HIGH。

## 发布检查清单

- [ ] PowerShell 自测、pytest、Ruff、Vitest、npm audit 和 Vite build 全部通过。
- [ ] sidecar 使用当前源码和仓库 `.venv` 重建。
- [ ] MSI 与 NSIS 同时存在。
- [ ] 收集后的 sidecar JSON 冒烟通过。
- [ ] 正式目录打印了四类产物的 SHA-256。
- [ ] 已在干净 Windows x64 目标机完成安装、启动和升级验证。
- [ ] 代码签名、上传和发布说明由独立发布步骤处理。
