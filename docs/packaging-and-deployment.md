# 桌面打包与部署

> [KNOWN] 权威发布入口使用 Python 标准库编排，可在 Windows、macOS 和 Linux 的仓库根目录运行；安装包仍只能为当前宿主系统生成。置信度：HIGH。

## 第一次构建

需要 Python 3.10+、Node.js 20.19+（或 22.12+）、Rust、Git，以及 Visual Studio Build Tools 的 **Desktop development with C++** 工作负载。

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1
```

检查工具是否可用：

```powershell
Get-Command python, node, npm, cargo, rustc
python --version
node --version
npm --version
cargo --version
rustc --version
```

## 更新代码后打包

每次更新代码后只需要执行这一条命令：

```bash
python scripts/release_desktop.py
```

Windows 旧命令仍然兼容，但它仅转发到上述 Python 脚本：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
```

[KNOWN] `release_desktop.py` 固定执行以下步骤：

1. 检查 Python、npm、Git、Cargo 和 Rust。
2. 安装 `.[dev,desktop]` Python 依赖，并使用 `desktop/package-lock.json` 以 `npm ci --ignore-scripts` 安装前端依赖；当前依赖使用预构建平台包，发布流程不执行第三方安装脚本。
3. 运行 pytest、Ruff 和 Vitest。
4. 运行全依赖 `npm audit --audit-level=moderate` 和前端生产构建，运行时或开发工具依赖存在中高危漏洞时阻断发布。
5. 完整重建 Python sidecar。
6. 执行当前宿主系统的 Tauri release build；Windows 生成 MSI 和 NSIS，macOS 至少生成 DMG，Linux 至少生成 DEB、AppImage 或 RPM 之一。
7. 在 `release/desktop/` 下的隔离临时目录收集并检查产物。
8. 直接运行临时目录中的 sidecar，并验证 `operation_history_page` JSON API。
9. 全部验证成功后替换 `release/desktop/<target-triple>/`，并打印 SHA-256；失败时保留上一次已验证产物。

[KNOWN] 该命令只生成可部署产物，不自动安装、不上传 Release，也不修改用户配置。

## 版本更新

桌面版本需要同步修改：

```text
desktop/package.json
desktop/src-tauri/Cargo.toml
desktop/src-tauri/tauri.conf.json
```

修改版本后同步锁文件：

```powershell
Push-Location .\desktop
npm install --package-lock-only
cargo check --manifest-path .\src-tauri\Cargo.toml
Pop-Location
```

[KNOWN] `pyproject.toml` 是 Python 包版本，只发布桌面端时不要求与桌面版本相同。

## 产物位置

Windows x86_64 的收集目录通常是：

```text
release/desktop/x86_64-pc-windows-msvc/
  lpm-desktop.exe
  lpm-desktop-api.exe
  msi/
    LPM Desktop_<version>_x64_en-US.msi
  nsis/
    LPM Desktop_<version>_x64-setup.exe
```

Tauri 原始安装包位于：

```text
desktop/src-tauri/target/release/bundle/
```

查看本次产物的时间、大小和 SHA-256：

```powershell
$TargetTriple = ((rustc -vV | Select-String '^host:').Line -split ':', 2)[1].Trim()
$ReleaseDir = Join-Path (Resolve-Path .) "release\desktop\$TargetTriple"

Get-ChildItem $ReleaseDir -Recurse -File |
  Select-Object FullName, Length, LastWriteTime

Get-ChildItem $ReleaseDir -Recurse -File |
  Get-FileHash -Algorithm SHA256
```

[KNOWN] 不要仅凭文件存在判断打包成功；必须确认 Python 发布命令退出码为 0，并保存该命令打印的正式产物路径和哈希。

## 部署方式

请选择一种方式：

- [COMMON] **NSIS setup**：普通用户安装，默认推荐。
- [COMMON] **MSI**：企业分发或需要 Windows Installer 管理时使用。
- [INFERRED] **便携目录**：内部测试使用，必须同时保留 `lpm-desktop.exe` 和 `lpm-desktop-api.exe`。

打开最新 NSIS 安装程序：

```powershell
$Installer = Get-ChildItem .\release\desktop -Recurse -Filter '*-setup.exe' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $Installer) { throw '未找到 NSIS 安装程序，请先完成打包。' }
Start-Process -FilePath $Installer.FullName
```

安装最新 MSI：

```powershell
$Installer = Get-ChildItem .\release\desktop -Recurse -Filter '*.msi' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $Installer) { throw '未找到 MSI，请先完成打包。' }
Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', "`"$($Installer.FullName)`"") -Wait
```

运行便携版：

```powershell
$TargetTriple = ((rustc -vV | Select-String '^host:').Line -split ':', 2)[1].Trim()
$ReleaseDir = Join-Path (Resolve-Path .) "release\desktop\$TargetTriple"

if (-not (Test-Path "$ReleaseDir\lpm-desktop-api.exe")) { throw '便携版缺少 lpm-desktop-api.exe。' }
Start-Process -FilePath "$ReleaseDir\lpm-desktop.exe"
```

## 发布限制

- [KNOWN] 当前 CI 只运行 Python Ruff 和 pytest，不构建或上传桌面安装包。
- [KNOWN] 当前没有代码签名、自动更新器或自动创建 GitHub Release 的流程。
- [KNOWN] 目标电脑不需要 Python、Node.js 或 Rust，但仍需要 Git 和有效的 LPM 配置；运行时会搜索配置路径、PATH 和常见安装目录。
- [INFERRED] 升级或回退前应备份 `~/.config/lpm/config.toml` 和私有资源仓库；回退安装包不会自动回退用户数据。

## 部署后检查

1. 在没有 Python、Node.js 和 Rust 的目标机上安装并启动应用。
2. 运行“健康检查”，确认 Git 的实际路径/来源、配置、资源仓库和平台目录正常。
3. 执行一次手动刷新，确认 Toast 和任务中心正常更新。
4. 验证 sidecar 能启动，不依赖开发机环境。
5. 从上一个版本升级一次，确认原配置仍可读取。

## 常见错误

### `npm` 或 Cargo 找不到

```powershell
Get-Command node, npm, cargo, rustc
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```

[KNOWN] 如果 `npm` 仍无法解析，重新打开 PowerShell，并确认 Node.js 安装目录已经加入 PATH。

### `link.exe not found`

[KNOWN] 安装 Visual Studio Build Tools，并选择 **Desktop development with C++** 工作负载，然后重新打开 PowerShell。

### 找不到 sidecar

```powershell
python .\tools\packaging\sidecar\build_sidecar.py
rustc -vV | Select-String '^host:'
Get-ChildItem .\desktop\src-tauri\binaries
```

### 收集目录仍是旧文件

[KNOWN] 先关闭从 `release/desktop/` 启动的便携版，再重新执行完整打包；运行中的 exe 可能阻止覆盖旧产物。

## 发布检查清单

- [ ] 桌面版本和锁文件已同步。
- [ ] pytest、Ruff、`npm test`、`npm run build` 全部通过。
- [ ] `npm audit --audit-level=moderate` 为 0。
- [ ] sidecar 使用当前 Python 源码重新构建。
- [ ] 收集后的 sidecar JSON API 烟雾测试通过。
- [ ] MSI/NSIS 的时间、大小和 SHA-256 已核对。
- [ ] 已在干净目标机完成安装、启动和升级验证。
- [ ] 已备份配置、私有资源仓库和上一个可用安装包。
