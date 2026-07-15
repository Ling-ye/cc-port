# 桌面打包与部署

> [KNOWN] 本文描述当前仓库实际存在的桌面构建链；事实来源是 `scripts/`、`tools/packaging/`、`desktop/package.json`、`desktop/src-tauri/tauri.conf.json` 和 `.github/workflows/ci.yml`。置信度：HIGH。

## 范围与发布边界

- [KNOWN] 桌面发布链由 React/Vite 前端、Rust/Tauri 外壳和 PyInstaller 单文件 sidecar 三部分组成。
- [KNOWN] 普通桌面安装包已经携带 `lpm-desktop-api`，目标电脑不需要安装 Python、Node.js 或 Rust。
- [KNOWN] 目标电脑仍需能够使用 Git，并提供有效的 LPM 配置；私有 GitHub 资源仓库还需要对应 token。
- [KNOWN] 当前仓库没有服务器部署目标、代码签名配置、自动更新器或自动创建 GitHub Release 的工作流。
- [KNOWN] `.github/workflows/ci.yml` 只在 Ubuntu/Windows 的 Python 3.10—3.12 上执行 Ruff 和 pytest，不构建或上传桌面安装包。
- [KNOWN] “环境”页和 `lpm env deploy` 的跨电脑资源恢复不是应用发布；相关流程见 [README 的自动发现与跨电脑部署](../README.md#自动发现与跨电脑部署)。

## 构建入口的职责

| 入口 | 实际行为 | 不会执行 |
| --- | --- | --- |
| `scripts/setup.ps1` / `setup.sh` | [KNOWN] 检查基础工具，安装 `.[dev,desktop]`、npm 依赖并确保图标存在。 | [KNOWN] 不运行测试，不生成安装包。 |
| `scripts/check-release.ps1` / `check-release.sh` | [KNOWN] 依次运行 pytest、Ruff、前端 `npm run build`、sidecar 构建和 Tauri release build，并打印原始产物路径。 | [KNOWN] 不运行 Vitest，不把产物收集到 `release/desktop/`。 |
| `scripts/build-desktop.ps1` / `build-desktop.sh` | [KNOWN] 构建图标、sidecar 和 Tauri 安装包，并收集最终产物。 | [KNOWN] 不运行 Python、前端自动测试或 Ruff。 |
| `npm run build` | [KNOWN] 执行 TypeScript 检查和 Vite 前端构建。 | [KNOWN] 不构建 sidecar、Rust 外壳或安装包。 |
| `npm run tauri build` | [KNOWN] 执行 Tauri release build；Tauri 会先再次调用 `npm run build`。 | [KNOWN] 不构建缺失的 PyInstaller sidecar，不收集 `release/desktop/`。 |

[INFERRED] 发布验收与最终打包必须分开看：某个脚本退出码为 0，不等于所有测试已经运行，也不等于收集目录里的文件是本次新产物。置信度：HIGH。

## 构建环境

### 通用要求

- [KNOWN] Python 3.10+。
- [KNOWN] Node.js 18+ 和 npm。
- [KNOWN] Rust、Cargo 和 `rustc`。
- [KNOWN] Git。
- [KNOWN] Python 桌面 extras：PyInstaller 6+ 与 Pillow 10+，由 `pip install -e ".[dev,desktop]"` 安装。
- [KNOWN] 桌面依赖以 `desktop/package-lock.json` 为准；正式构建优先使用 `npm ci`，避免锁文件外漂移。

[KNOWN] Windows 还需要 Visual Studio 2022 Build Tools 的 **Desktop development with C++** 工作负载。Windows 10 1803+ 和 Windows 11 通常已有 WebView2 Runtime；旧环境需单独验证。

[KNOWN] 安装包按当前主机构建，仓库没有配置跨平台交叉编译。Windows、macOS 和 Linux 的发布产物应分别在对应系统或专用 runner 上构建。

### 首次初始化

Windows PowerShell：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\setup.ps1
```

PowerShell 7：

```powershell
pwsh scripts/setup.ps1
```

macOS / Linux：

```bash
bash scripts/setup.sh
```

[KNOWN] 初始化结束后必须重新确认命令解析正常，尤其是 Windows 上的 `npm` 和 Cargo：

```powershell
Get-Command python, node, npm, cargo, rustc
python --version
node --version
npm --version
cargo --version
rustc --version
```

[KNOWN] 如果 Cargo 已安装但当前会话找不到，可将用户 Cargo 目录加入本次会话的 PATH：

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```

[KNOWN] 如果 Node 安装在用户目录但 `npm` 无法解析，可先确认实际安装位置，再把 Node 目录加入 PATH；不要把某台开发机的绝对路径写入仓库脚本。

## 版本准备

[KNOWN] 桌面版本目前同时存在于以下文件，发布桌面新版本时应保持一致：

```text
desktop/package.json
desktop/package-lock.json
desktop/src-tauri/Cargo.toml
desktop/src-tauri/Cargo.lock
desktop/src-tauri/tauri.conf.json
```

[KNOWN] `pyproject.toml` 的 Python 包版本是独立版本；只发布桌面安装包时不要求与桌面版本相同。

[INFERRED] 更新桌面版本后，建议在 `desktop/` 执行以下命令同步 npm 锁文件，并通过一次 Cargo 命令同步 Rust 锁文件。置信度：HIGH。

```powershell
cd desktop
npm install --package-lock-only
cargo check --manifest-path src-tauri\Cargo.toml
cd ..
```

## 正式打包流程

### 1. 确认源码状态与依赖

```powershell
git status --short
cd desktop
npm ci
cd ..
```

- [KNOWN] 发布前应先确认工作区中的每个修改都属于本次版本。
- [KNOWN] `release/`、`desktop/dist/`、`desktop/src-tauri/target/` 和 sidecar 构建目录均被 Git 忽略，不能用“Git 工作区干净”证明产物是新的。

### 2. 执行完整验收

Windows：

```powershell
python -m pytest -q -s
python -m ruff check src/lpm tests
Push-Location desktop
npm test
npm run build
Pop-Location
```

macOS / Linux：

```bash
python3 -m pytest -q -s
python3 -m ruff check src/lpm tests
(cd desktop && npm test && npm run build)
```

[KNOWN] 也可以运行 `scripts/check-release.*` 做端到端构建验收，但必须在此前单独执行 `npm test`，因为该脚本当前不包含 Vitest。

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\check-release.ps1
```

```bash
bash scripts/check-release.sh
```

### 3. 构建并收集最终产物

Windows：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

macOS / Linux：

```bash
bash scripts/build-desktop.sh
```

[KNOWN] 完整脚本按以下顺序执行：确保图标、PyInstaller sidecar、Tauri release build、安装包生成、产物收集。

[KNOWN] 仅修改前端且已确认当前 target triple 对应的 sidecar 存在时，可以在本地增量验证中跳过 sidecar；正式对外发布仍建议完整重建。

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\build-desktop.ps1 -SkipSidecar -SkipIcons
```

```bash
bash scripts/build-desktop.sh --skip-sidecar --skip-icons
```

[KNOWN] `-SkipSidecar` / `--skip-sidecar` 不会验证旧 sidecar 与当前 Python 源码是否一致；Python、依赖或 Desktop API 有任何变化时禁止使用。

## 产物与验证

### 目录结构

```text
build/sidecar/                                  # PyInstaller 中间目录
desktop/dist/                                   # Vite 前端输出
desktop/src-tauri/binaries/
  lpm-desktop-api-<target-triple>[.exe]          # Tauri externalBin 输入
desktop/src-tauri/target/release/                # Rust/Tauri 原始 release 输出
desktop/src-tauri/target/release/bundle/         # 原始安装包
release/desktop/<target-triple>/                 # build-desktop.* 收集目录
```

[KNOWN] Windows x86_64 完整构建通常包含：

```text
release/desktop/x86_64-pc-windows-msvc/
  lpm-desktop.exe
  lpm-desktop-api.exe
  msi/
    LPM Desktop_<version>_x64_en-US.msi
  nsis/
    LPM Desktop_<version>_x64-setup.exe
```

[KNOWN] macOS 和 Linux 的 bundle 类型由 Tauri 2、当前主机及已安装的系统打包工具决定；应以 `desktop/src-tauri/target/release/bundle/` 的实际文件为准。

### 防止误认旧产物

[KNOWN] 构建完成必须同时检查退出码、输出中的 `Finished` / bundle 路径、文件更新时间和哈希；只看到旧路径或旧文件存在不能算成功。

```powershell
Get-ChildItem release\desktop -Recurse -File |
  Select-Object FullName, Length, LastWriteTime

Get-ChildItem release\desktop -Recurse -File |
  Get-FileHash -Algorithm SHA256
```

```bash
find release/desktop -type f -print0 | xargs -0 sha256sum
```

[KNOWN] 如果运行中的便携版占用了 `release/desktop/.../lpm-desktop.exe`，收集阶段可能无法覆盖旧文件；打包前应关闭从收集目录启动的应用。

[KNOWN] Windows 脚本内部需要解析裸命令 `npm`。如果日志出现 `npm is not recognized`、`cargo metadata ... program not found` 或对应中文错误，构建没有通过；先修复 PATH，再重新执行，不能沿用此前安装包。

## 部署选择

### Windows

| 方式 | 适用场景 | 约束 |
| --- | --- | --- |
| MSI | [COMMON] 企业软件分发、Windows Installer 管理或需要 `msiexec` 的环境。 | [KNOWN] 发布文件位于 `bundle/msi/`；先在目标 Windows 版本上验证安装、升级和卸载。 |
| NSIS setup | [COMMON] 面向普通用户的交互式安装。 | [KNOWN] 发布文件位于 `bundle/nsis/`；不要与同版本 MSI 混装测试。 |
| 便携目录 | [INFERRED] 内部测试、临时验证或不希望安装的环境。 | [KNOWN] 必须把 `lpm-desktop.exe` 与 `lpm-desktop-api.exe` 放在同一发布目录，不能只复制主程序。 |

[KNOWN] 当前未配置 Windows 代码签名，因此外部分发时可能出现发布者未知或 SmartScreen 提示。正式公开发布前应决定是否引入证书签名；文档不能把未签名包描述为“受信任安装包”。

### macOS / Linux

- [KNOWN] macOS 安装包必须在 macOS 主机上构建；当前仓库没有 Apple 签名、公证或自动更新配置。
- [KNOWN] Linux 安装包必须在目标发行版兼容的构建环境中生成，并在声明支持的发行版上做实际安装验证。
- [INFERRED] 在没有签名、公证和发行版矩阵验证之前，macOS/Linux 产物应标记为内部或实验性发布。置信度：HIGH。

## 发布与升级

[KNOWN] 当前发布需要人工上传产物，CI 不会创建 Release、上传 bundle 或生成校验文件。

[INFERRED] 为避免与 Python 包 `0.4.0` 的版本混淆，桌面 tag 推荐使用 `desktop-v<version>`，例如 `desktop-v0.1.0`。置信度：MED。

[COMMON] 每次发布至少应包含：

- 安装包文件名、目标系统和 CPU 架构。
- SHA-256 校验值。
- 用户可见变更与已知限制。
- 是否签名、是否支持自动更新。
- 配置或数据格式是否变化，以及回退限制。

[KNOWN] 升级或回退前应关闭正在运行的 LPM Desktop。运行时配置默认位于 `~/.config/lpm/config.toml`，私有资源数据位于单独资源仓库；它们不属于安装包产物。

[INFERRED] 发布前应备份配置和私有资源仓库。应用安装包回退不会自动回退用户资源数据；若新版本改变了数据内容，必须单独恢复备份。置信度：HIGH。

[KNOWN] 当前没有应用内自动更新器，升级方式是人工获取新安装包并重新安装。

## 部署后验收

[COMMON] 至少在一台没有 Python、Node.js 和 Rust 的干净目标机或虚拟机上执行以下检查：

1. 安装包可完成安装，应用可启动且只出现一个主窗口。
2. “健康检查”能够检查 Git、配置、资源仓库和平台目录。
3. 手动刷新、资源扫描等只读任务能在任务中心显示并允许安全重试。
4. 安装、删除、保存、部署等写操作失败后不出现自动重试入口。
5. sidecar 能随应用启动，不依赖开发机 Python 环境。
6. 从上一个受支持版本升级后，配置仍可读取。
7. 卸载后按发布策略确认是否保留用户配置；不要假设卸载器会删除私有资源仓库。

## 常见失败定位

| 现象 | 检查方向 |
| --- | --- |
| `npm` / `cargo` 找不到 | [KNOWN] 检查当前 shell 的 PATH；安装后重新打开终端。 |
| `link.exe not found` | [KNOWN] 安装 Visual Studio Build Tools 的 C++ 桌面工作负载。 |
| Tauri 报 external binary 不存在 | [KNOWN] 重建 sidecar，并确认文件名包含当前 `rustc -vV` 返回的 host triple。 |
| 前端 build 成功但没有安装包 | [KNOWN] `npm run build` 只生成 `desktop/dist/`；使用 `build-desktop.*` 或 `npm run tauri build`。 |
| 收集目录仍是旧时间 | [KNOWN] 检查 Tauri 原始 bundle 的时间和构建日志；关闭占用收集目录 exe 的进程后重跑。 |
| 安装包在另一平台无法运行 | [KNOWN] 当前构建不是跨平台通用二进制；在对应系统/架构重新构建。 |

## 发布检查清单

- [ ] 桌面版本文件保持一致，锁文件已同步。
- [ ] Git 差异只包含计划内源码、测试和文档。
- [ ] pytest 与 Ruff 通过。
- [ ] `npm test` 与 `npm run build` 通过。
- [ ] sidecar 使用当前 Python 源码完整重建。
- [ ] Tauri release build 成功生成目标平台 bundle。
- [ ] 收集目录的时间、大小与 SHA-256 已核对。
- [ ] 在干净目标机完成安装、启动、健康检查、任务反馈和升级验证。
- [ ] 发布说明明确签名、自动更新、系统/架构和已知限制。
- [ ] 保留上一个可用安装包以及配置/私有资源备份，用于人工回退。
