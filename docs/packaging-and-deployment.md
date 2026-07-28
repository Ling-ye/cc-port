# Windows 桌面打包与发布

本手册适用于 CC Port `0.5.2`。它只保留当前可执行的维护者流程；缓存、
并发、回滚和历史性能实验的完整约束见
[桌面发布编排规格](specs/desktop-release-orchestration.md)。

## 支持边界

- 构建机：Windows 10/11 x64、Windows PowerShell 5.1、WinGet 和网络连接。
- 目标机：Windows 10/11 x64、Git for Windows 和 Git Credential Manager。
- `scripts/setup.ps1` 可安装或准备 Python、Node.js、Rust 和 MSVC；最终用户
  不需要这些开发工具。
- Public Beta 安装器未做 Authenticode 签名。企业策略禁止运行未签名应用的
  电脑不在当前支持范围。
- 发布保持本地构建和人工上传，不从脚本登录 GitHub、签名程序或上传资产。
- 不维护长期 `release` 分支。发布提交通过短期 PR 分支进入受保护的 `main`。

## 准备环境

在仓库根目录运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1
```

常用模式：

```powershell
# 只检查，不安装或同步依赖
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -CheckOnly

# 跳过确认
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -NonInteractive

# 无条件重新同步 Python 与前端依赖
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -ForceSync
```

WinGet 缺失时先安装或修复
[App Installer](https://learn.microsoft.com/windows/package-manager/winget/)。
脚本会检查工具版本、准备仓库 `.venv`、执行锁定的 npm 安装并验证环境；
不兼容的旧 `.venv` 会重命名备份而不是直接删除。

## 版本准备

发布前把以下版本统一为 `X.Y.Z`：

```text
pyproject.toml
src/cc_port/__init__.py
desktop/package.json
desktop/package-lock.json（顶层与 packages[""]）
desktop/src-tauri/Cargo.toml
desktop/src-tauri/Cargo.lock
desktop/src-tauri/tauri.conf.json
SKILL.md
```

同时准备：

```text
docs/releases/vX.Y.Z.md
docs/releases/vX.Y.Z.en.md
```

修改版本后更新锁文件：

```powershell
Push-Location .\desktop
npm install --package-lock-only
cargo check --manifest-path .\src-tauri\Cargo.toml
Pop-Location
```

发布脚本会再次核对所有版本和两份发布说明；不一致时立即停止。

## 构建

从干净、最新的 `main` 运行：

```powershell
git status --short
git pull --ff-only
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
```

需要忽略依赖和 sidecar 缓存时：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1 -Clean
```

脚本依次执行环境验证、PowerShell 自测、pytest、Ruff、Vitest、锁文件审计、
sidecar 构建与冒烟、Tauri MSI/NSIS 构建、产物哈希和事务式目录切换。任一门禁
失败都会停止发布；脚本不会安装生成的安装包，也不会上传 GitHub Release。
Rust 构建期间会把用户目录、Cargo/Rustup 目录和仓库目录重映射为稳定名称，
避免发布 EXE 包含构建用户名或源码绝对路径；调用前的 Rust flags 会在构建后恢复。

## 产物

完整的本地验证产物位于：

```text
release/desktop/x86_64-pc-windows-msvc/
  cc-port-desktop.exe
  cc-port-desktop-api.exe
  msi/
    CC Port_<version>_x64_en-US.msi
  nsis/
    CC Port_<version>_x64-setup.exe
```

唯一允许上传的公开资产位于：

```text
release/publish/v<version>/
  cc-port_<version>_windows_x64_setup.exe
  SHA256SUMS.txt
```

不要上传 MSI、原始桌面 EXE 或 sidecar EXE。`SHA256SUMS.txt` 由脚本从已
验证的 NSIS 安装器生成；每次成功构建都会事务式替换该版本目录，避免混入旧文件。

## 安装冒烟

在干净的 Windows 10/11 x64 消费者环境完成：

1. 对照 `SHA256SUMS.txt` 验证安装器 SHA-256。
2. 通过 SmartScreen 的“更多信息”继续运行未签名安装器。
3. 首次启动并检查 Git 与 Git Credential Manager 诊断。
4. 连接由测试账号控制的私有仓库并完成读写验证。
5. 扫描资源，完成一次上传和一次安装。
6. 使用同版本或候选升级安装器完成覆盖升级。
7. 卸载应用，确认远端仓库、工具资源和预期保留的本机状态未被删除。

不要把真实 Token、私有仓库内容或未脱敏的 MCP 环境变量放入日志和截图。

## 人工发布

以下顺序是发布门禁，不得跳步：

1. 通过 PR 把版本提交合并到 `main`，确认工作区干净且 CI 全绿。
2. 在该提交完成构建和安装冒烟。
3. 使用已经配置好的 SSH 或 GPG 签名创建 annotated tag：

   ```powershell
   git tag -s -a vX.Y.Z -m "CC Port vX.Y.Z"
   git tag -v vX.Y.Z
   git push origin vX.Y.Z
   ```

4. 从该标签创建 GitHub prerelease。
5. 粘贴 `docs/releases/vX.Y.Z.md` 和 `.en.md` 的中英文发布说明。
6. 只上传 `release/publish/vX.Y.Z/` 中的两个文件。
7. 从 GitHub 重新下载两个资产，重新计算 SHA-256，并与校验文件及 GitHub
   资产摘要核对。

已公开的标签和安装器不可移动、重建或替换；需要改变产物时发布新版本。已发布的
`v0.5.0` 保持原标签与原安装器，只允许修正文案和链接。

## 常见失败

### 找不到 WinGet、Git、MSVC 或 Rust target

先运行 `scripts/setup.ps1 -CheckOnly`。缺少 WinGet 时修复 App Installer；
其他缺失工具运行默认 `setup.ps1` 补齐。若 `VsDevCmd.bat` 报“输入行太长”，
新开未加载 Conda 或 Visual Studio 环境的 PowerShell 后重试。

### `Locked frontend dependency audit` 失败

在 `desktop/` 中运行 `npm explain <包名>` 确认依赖来源，只更新满足上游
语义版本范围的安全锁定版本，然后依次运行：

```powershell
npm audit --package-lock-only --audit-level=moderate
npm test
npm run build
```

### sidecar 冒烟或哈希失败

使用 `release-desktop.ps1 -Clean` 重建。仍失败时检查本次
`build/metrics/*.logs/`，不要复制旧 sidecar 绕过校验。

### 正式目录无法替换

关闭从 `release/desktop/` 启动的便携程序、安装器或资源管理器预览后重试。
不要手工删除 `.backup-*`；先确认没有发布进程运行并核对正式目录与备份内容。

### 安装程序要求重启

重启 Windows 后执行原命令。环境准备脚本是幂等的，只会补齐仍缺失的组件。

## 发布检查清单

- [ ] 所有版本和中英文 Release notes 一致。
- [ ] PowerShell 自测、pytest、Ruff、Vitest、npm audit 和 Vite build 通过。
- [ ] sidecar 冒烟、MSI/NSIS 检查和四类本地产物哈希通过。
- [ ] 解包后的桌面 EXE 和 sidecar 不包含构建用户名或绝对源码路径。
- [ ] `release/publish/vX.Y.Z/` 恰好包含两个规定文件，安装器哈希一致。
- [ ] 干净 Windows 10/11 消费者环境完成安装、同步、覆盖升级和卸载。
- [ ] 签名 annotated tag 指向已验证的 `main` 提交。
- [ ] GitHub prerelease 只包含安装器和 `SHA256SUMS.txt`。
- [ ] 重新下载后的 SHA-256 与校验文件和 GitHub 资产摘要一致。
