# Windows 桌面打包与部署

本发布文档适用于 CC Port `0.5.0`，源码仓库为 <https://github.com/Ling-ye/cc-port>。

## 支持边界

- 桌面发布构建正式支持 Windows 10/11 x64 和 Windows PowerShell 5.1。
- 构建机需要 WinGet、网络连接和本仓库源码；Python、Node.js、Git、Rust 与 MSVC 可以由环境脚本安装。
- 用户只执行 PowerShell 入口；PyInstaller sidecar 仍由脚本管理的仓库 `.venv` 构建。
- 安装生成的 MSI/NSIS 后，目标电脑不需要 Python、Node.js 或 Rust。

## 一键准备环境

在仓库根目录执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1
```

默认流程先显示所有系统安装与仓库环境操作，并只确认一次。

只检查、不安装且不修改 `.venv`、`desktop/node_modules` 或 `build/cache/` 记录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -CheckOnly
```

显式非交互模式会跳过确认，并向 WinGet 传递静默安装与协议接受参数：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -NonInteractive
```

默认准备流程会验证依赖缓存，验证通过时复用现有 `.venv` 与 `desktop/node_modules`；需要无条件重新执行 pip 与 npm 同步时使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -ForceSync
```

环境脚本执行以下行为：

1. 验证 Windows x64 和 Windows PowerShell 5.1。
2. 检测 Python 3.10–3.12、Node.js 20.19+/22.12+、npm、Git、Rustup/Cargo/rustc、Visual Studio Build Tools 与 C++ workload。
3. 使用精确 WinGet 包 ID 安装缺失的 Python 3.12、Node.js LTS、Git、Rustup 和 Visual Studio 2022 Build Tools。
4. 刷新当前进程 PATH，并通过 `vswhere.exe` 与 `VsDevCmd.bat` 导入 MSVC/Windows SDK 环境（`VsDevCmd` 在最小 Windows PATH 下执行，再合并回原 PATH，避免 Conda 等过长 PATH 触发 cmd「输入行太长」）。
   如果 Build Tools 错选旧版 `winv6.3` 占位环境，脚本会从已安装的 Windows 10/11 SDK 中选择包含 `kernel32.lib` 与 UCRT 的最新 x64 版本，只修复当前构建进程。
5. 选择 `stable-x86_64-pc-windows-msvc` Rust toolchain。
6. 创建仓库 `.venv`；不兼容的旧环境会先重命名为 `.venv.backup-<timestamp>`。
7. 验证 `build/cache/dependencies.json`；缓存缺失、损坏、失效或指定 `-ForceSync` 时，执行 `.venv\Scripts\python.exe -m pip install -e ".[dev,desktop]"`。
8. 同一次依赖同步中执行 `npm ci --ignore-scripts --no-audit --no-fund`，并且只在两套依赖与后置探针全部成功后刷新缓存记录。

`.venv` 备份不会自动删除；如果新环境创建或依赖同步随后失败，修复根因后应优先重跑 `setup.ps1`。确需回退时，先确认没有 Python/发布进程使用仓库环境，再删除失败的新 `.venv` 并把所需 `.venv.backup-<timestamp>` 手工改回 `.venv`。

如果 WinGet 不存在，脚本会在系统安装前停止；先按微软文档安装或修复 App Installer，再重新执行同一命令：<https://learn.microsoft.com/windows/package-manager/winget/>。

## 一键打包

桌面应用不依赖 GitHub App、OAuth App、broker、Worker、自有 OAuth 域名或应用侧 Token 存储。

正式发布目标仍为 Windows x64；Git for Windows 与 Git Credential Manager 是目标机运行前提，不嵌入安装包。设置页在运行时只读检查 Git、GCM 与 `credential.helper`，不会修改用户全局 Git 配置。

Git for Windows 默认包含 GCM；如果目标机缺失或安装不完整，设置页提供 [Git for Windows](https://git-scm.com/download/win) 与 [GCM](https://github.com/git-ecosystem/git-credential-manager/blob/main/docs/install.md) 官方安装入口。

更新代码后只执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1
```

发布入口会自动调用环境准备流程，不要求提前单独运行 `setup.ps1`。

默认发布入口始终以非交互模式准备环境，不读取 `y/n` 输入；列出的环境操作会被自动授权，WinGet 安装会使用静默安装与协议接受参数。

默认发布会先验证依赖与 sidecar 缓存；验证通过才复用，缓存缺失、损坏或失效时自动同步依赖或重建 sidecar。

发布入口继续接受 `-NonInteractive` 以兼容旧命令，但它与默认发布行为等价，不再需要显式传入。独立运行 `setup.ps1` 时仍保留默认确认。

需要忽略依赖与 sidecar 缓存、完成一次干净的依赖同步和 PyInstaller sidecar 重建时使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1 -Clean
```

`-Clean` 下的依赖缓存会在依赖同步及后置探针成功后刷新，sidecar 缓存会在 clean 重建及隔离冒烟成功后刷新；两者都不等待整个 Tauri 发布成功。该模式不会执行 `cargo clean`，也不会删除 Cargo/Tauri 的依赖编译缓存。

发布门禁固定为：

1. 验证环境与依赖缓存；失效或指定 `-Clean` 时同步 Python 与锁定 npm 依赖。
2. 无论是否命中缓存，都运行 PowerShell 构建逻辑自测、完整 pytest 与 Ruff。
3. 无论是否命中缓存，都运行 Vitest、`npm audit --package-lock-only --audit-level=moderate` 和一次 Vite 生产构建。
4. 验证 sidecar 缓存；命中后先直接冒烟，未命中或冒烟失败时执行 clean 重建并再次验证。
5. 删除已知 Tauri 输出后，把验证通过的源 sidecar 显式复制到 `desktop/src-tauri/target/release/cc-port-desktop-api.exe`，并在构建前校验源、目标 SHA-256 一致。
6. 执行 Tauri release build，并要求同时生成 MSI 和 NSIS；构建后再次校验目标 sidecar 与源 sidecar 的 SHA-256。
7. 在 `release/desktop/` 同级临时目录收集 exe 与安装包。
8. 使用隔离 `CC_PORT_STATE_HOME` 运行打包后的 sidecar，并验证 JSON `ok` 响应。
9. 在临时目录计算并输出绝对发布路径、大小和 SHA-256；全部成功后事务式切换上一次正式发布目录。
10. 在终端汇总总耗时、各阶段耗时与缓存状态，并写入本次发布的 JSON 指标。

五项质量门禁最多四路并行，每项有独立日志与 900 秒硬超时；超时任务会被终止并记录退出码 `124`，其余任务仍等待并统一汇报。

Tauri 的 `beforeBuildCommand` 仍执行唯一一次 TypeScript/Vite 生产构建；Vite 先输出到隔离目录，文件树内容未变化时保留既有 `desktop/dist` 及时间戳，只有内容变化时才替换，从而避免无修改暖构建触发 Cargo 的 `rerun-if-changed`。

`build/cache/release.lock` 只串行同一仓库中的 `release-desktop.ps1`；冲突发布立即失败而不等待，锁文件保留但独占句柄在进程退出时释放。单独执行的 `setup.ps1` 不获取此锁。

`desktop/scripts/stable-vite-build.mjs` 依赖正式发布锁提供跨进程序列化；不得在正式发布运行期间另行执行 `npm run build`，也不得并发执行多个独立 `npm run build`。

- 每个 Tauri 插件的 npm 包与 Rust crate 必须锁在相同 major/minor；例如 `@tauri-apps/plugin-dialog` 与 `tauri-plugin-dialog` 应一起升级并更新两套锁文件。

- 依赖、质量、构建、冒烟或哈希任一发布前门禁失败，都不会覆盖上一次已验证的正式产物；目录切换自身发生可捕获失败时会尝试回滚。
- 正式发布目录不会混入上一次构建遗留的安装包。
- 发布命令不自动安装生成的安装包、不上传 Release、不执行代码签名。

## 缓存与失效规则

- 依赖缓存固定为 `build/cache/dependencies.json`，其 `schemaVersion` 为 `1`。
- `pyproject.toml`、`desktop/package.json`、`desktop/package-lock.json` 的内容，Python/Node/npm 的路径或版本，以及操作系统、架构或 Rust target 任一变化，都会使依赖缓存失效。
- 即使输入指纹匹配，Python import/Ruff 探针、`pip check`、`pip list --format=json` 清单哈希、`npm ls --all --json` 或 `node_modules/.package-lock.json` 哈希任一验证失败，依赖缓存仍然失效并重新同步。
- 默认发布的首次依赖判断只做输入与缓存记录预判；真正复用前只执行一次完整环境探针，并重新计算输入指纹，避免交互等待期间的状态变化和重复探针开销。`setup.ps1 -CheckOnly` 直接执行完整探针。
- sidecar 缓存固定为 `build/cache/sidecar.json`，其 `schemaVersion` 为 `1`；实际 PyInstaller 工作目录与中间产物仍位于 `build/sidecar/`。
- `src/cc_port` 下任一运行时文件、`tools/packaging/sidecar/*.py`、`pyproject.toml`、Python ABI 或路径、PyInstaller 版本、pip 安装清单、target triple 或已缓存产物哈希任一变化，都会使 sidecar 缓存失效。
- sidecar 缓存命中后仍必须直接执行隔离冒烟；冒烟失败会废弃该命中并执行 clean 重建，不能发布旧二进制。
- 缓存 sidecar 冒烟失败时，指标先记录 `recoveryAttempted = true` 且保留原失败；只有 clean 重建、重建后冒烟与缓存刷新全部成功，才把该阶段标成 `recovered = true`。
- 缓存文件缺失、JSON 损坏、schema 不支持或记录不完整时按缓存未命中处理；只有相应同步、构建与验证全部成功后才刷新记录。
- 一旦决定重新同步依赖，旧依赖缓存会在调用 pip/npm 前先原子写成失效记录；同步中途失败时，下次运行不能把旧记录误判为命中。

## 构建指标

- 每次发布尝试都会写入 `build/metrics/release-<UTC yyyyMMdd-HHmmss>-<8位runId>.json`，成功与失败运行都保留指标。
- 指标 JSON 的顶层结构为 `{ "schemaVersion": 1, "value": { ... } }`；发布状态、错误、阶段和产物字段路径固定为 `value.success`、`value.error`、`value.phases` 和 `value.artifacts`，运行模式与总耗时位于 `value.mode` 和 `value.durationMs`。`value.phases` 记录各阶段的退出状态、耗时、缓存状态和日志路径，`value.artifacts` 在成功发布时记录产物信息。
- 终端结束摘要展示总耗时、逐阶段耗时和缓存命中或重建状态，便于比较默认模式与 `-Clean`。
- 指标文件写入失败只输出警告，不得掩盖或改变原始发布结果与退出状态。

## 性能验收

- 优化前基线与优化后候选必须使用同一提交、同一机器和同一组 Python、Node、npm、Rust、PyInstaller 工具版本；测试前必须关闭正在运行的便携桌面程序，确保正式目录切换可以成功。
- 基线与候选各连续执行三次无源码修改的默认暖构建，且纳入统计的指标必须满足 `value.success = true`；分别取 `value.durationMs` 的中位数。
- 第一阶段按子目标验收，不以总体 30% 作为本阶段硬门槛；总体 30% 仍是后续优化目标。依赖与 sidecar 缓存状态必须记录并在两组样本间保持可比。
- Rust 脏构建基线与候选各执行三次“清理本包 release 产物后执行 `tauri build --no-bundle`”，取 Rust 构建阶段中位数，并要求 `candidateMedian <= baselineMedian * 0.80`。
- 质量门禁基线与候选各执行三次默认暖发布，按第一项门禁最早开始至 pytest 结束计算墙钟中位数，并要求 `candidateMedian <= baselineMedian * 0.80`。
- 候选完整暖发布中位数不得超过基线的 105%；若某项未达到对应 20% 子目标、破坏产物或引入行为回归，只回退该项优化，不自动加入 bundle 缓存、pytest 进程并行或并行安装器。
- `-Clean`、前端修改、Python 修改和 Rust 修改场景分别记录耗时与缓存状态，但不套用无修改暖构建的 30% 硬门槛。

### 第一阶段实测结果（2026-07-24）

- Rust 脏构建基线为 47.96、48.70、49.66 秒，中位数 48.70 秒；仅生成 `rlib` 的候选为 89.00、52.62、54.49 秒，中位数 54.49 秒，比基线慢 11.89%，未达到不高于 38.96 秒的门槛。该候选已回退，`Cargo.toml` 继续保留 `staticlib`、`cdylib` 与 `rlib`。
- 原调度的三次质量门禁墙钟为 51.285、51.368、51.034 秒，中位数 51.285 秒；“四项并行后 pytest 独占”的候选为 62.350、62.660、60.367 秒，中位数 62.350 秒，比原调度慢 21.58%，未达到不高于 41.028 秒的门槛。该候选已回退，五项门禁继续使用最多四路并行。
- 原调度的三次完整暖发布为 102.814、102.882、102.256 秒，中位数 102.814 秒；两波候选为 116.561、115.701、111.597 秒，中位数 115.701 秒，比原调度慢 12.53%，同时超过 5% 非回退上限。
- 六次 A/B 暖发布均通过完整 pytest、Vitest、Ruff、锁文件审计、PowerShell 自测、sidecar 冒烟与哈希校验，并生成应用 EXE、sidecar EXE、MSI 和 NSIS。候选指标文件为 `release-20260724-074239-adcf6a2f.json`、`release-20260724-074441-31415d8f.json`、`release-20260724-074639-73f00440.json`；回退后基线复测为 `release-20260724-075018-b99a47b1.json`、`release-20260724-075204-5fe11bbd.json`、`release-20260724-075354-21ffb321.json`。
- 因两个候选都已触发各自的强制回退条件，本轮未继续执行不会影响保留决策的 `-Clean`、前端修改和 Python 修改附加场景；Rust 修改场景已由三次 crate-type 候选脏构建覆盖。

### 第二阶段 pytest-xdist 规格

- 第二阶段只为 Windows 正式发布的完整 pytest 门禁增加 `pytest-xdist==3.8.0`，不修改测试集合、其他门禁的顺序与并发上限、Tauri、Cargo、安装器或运行时接口；CI 继续串行运行 pytest。
- 正式发布使用 `pytest -q -s -n <workers> --dist load`，其中 `workers = max(1, min(4, [Environment]::ProcessorCount))`；当前 8 核、16 逻辑处理器的验收机使用 4 worker。
- `pyproject.toml` 仍参与依赖缓存指纹，增加 xdist 后旧依赖缓存必须失效并重新同步；环境完整性探针必须显式导入 `xdist`。插件缺失或并行测试失败时发布直接失败，不得静默退回串行。
- 保留 Python 门禁的阶段名称、独立日志、900 秒超时、退出码与指标 JSON schema；五项质量门禁继续由现有执行器最多四路并行。
- 第二阶段质量门禁基线中位数为 51.285 秒，候选三次暖发布的门禁墙钟中位数必须不高于 41.028 秒。
- 第二阶段完整暖发布基线中位数为 102.814 秒，候选三次暖发布的总耗时中位数必须不高于 87.392 秒，即至少减少 15%。
- 实现后先执行一次不计样本的暖机发布，使依赖与 sidecar 缓存稳定；随后连续执行三次依赖和 sidecar 缓存均命中的默认暖发布。任一功能失败、并行不稳定或性能门槛未满足时，回退本阶段全部 xdist 改动。

### 第二阶段实测结果（2026-07-24）

- 完整串行 pytest 为 42.28 秒，4-worker `--dist load` 为 20.25 秒；两次均为 257 passed、1 skipped，单独运行的测试墙钟减少 52.1%。
- 三次正式候选的质量门禁墙钟为 27.163、27.377、28.470 秒，中位数 27.377 秒；相对 51.285 秒基线减少 46.62%，低于 41.028 秒门槛。
- 三次正式候选的完整暖发布为 75.565、74.927、76.653 秒，中位数 75.565 秒；相对 102.814 秒基线减少 26.50%，低于 87.392 秒门槛。
- 三次候选的依赖与 sidecar 缓存均命中，完整 pytest、Vitest、Ruff、锁文件审计、PowerShell 自测、sidecar 冒烟与哈希校验全部通过，并生成应用 EXE、sidecar EXE、MSI 和 NSIS；指标文件为 `release-20260724-131755-7b2a837f.json`、`release-20260724-131916-b4089575.json` 和 `release-20260724-132036-7de24734.json`。
- 第二阶段通过功能与性能验收，保留 `pytest-xdist==3.8.0`、动态最多 4 worker 和 `--dist load`；安装器、Cargo、门禁执行器、指标 schema 与运行时接口未改变。

## Rust 目标检测

- 发布流程先解析 Cargo，再优先选择与 Cargo 同目录的 `rustc.exe` 或 `rustup which rustc`，避免 Conda 等 PATH shim 抢占。
- 目标优先通过 `rustc --print host-tuple` 获取，再回退到容忍 BOM、ANSI 控制码和前导空白的 `rustc -vV`。
- Windows x64 发布必须得到 `x86_64-pc-windows-msvc`。
- 检测失败时错误会包含实际 rustc 路径、两个命令的退出码和截断输出。

## 产物位置

验证后的正式目录为：

```text
release/desktop/x86_64-pc-windows-msvc/
  cc-port-desktop.exe
  cc-port-desktop-api.exe
  msi/
    CC Port_<version>_x64_en-US.msi
  nsis/
    CC Port_<version>_x64-setup.exe
```

Tauri 原始输出保留在 `desktop/src-tauri/target/release/`，PyInstaller 中间输出保留在 `build/sidecar/`，缓存记录保留在 `build/cache/`，发布指标保留在 `build/metrics/`。

## 版本更新

桌面版本需要同步修改以下三个文件：

```text
desktop/package.json
desktop/src-tauri/Cargo.toml
desktop/src-tauri/tauri.conf.json
```

修改版本后更新锁文件，再执行一键发布：

```powershell
Push-Location .\desktop
npm install --package-lock-only
cargo check --manifest-path .\src-tauri\Cargo.toml
Pop-Location
```

## 常见失败

### `Locked frontend dependency audit` 失败

- 在 `desktop/` 运行 `npm explain <包名>` 确认直接或传递依赖来源，再根据公告中的首个修复版本更新 `desktop/package-lock.json`。
- 如果首个修复版本已满足上游依赖的语义版本范围，只更新锁文件，不升级无关直接依赖，也不增加 `overrides`。
- GHSA-r28c-9q8g-f849 要求 `postcss >= 8.5.18`；更新后必须依次通过 `npm audit --package-lock-only --audit-level=moderate`、`npm test` 和 `npm run build`。

### WinGet 不存在

- 安装或修复 App Installer 后，重新执行原命令；脚本不会自行下载或旁路安装 WinGet。

### `link.exe` 不存在

- 重新运行 `setup.ps1`；脚本会检查 Visual Studio C++ workload、导入 `VsDevCmd.bat` 并验证 `link.exe`。

### `Visual Studio developer environment failed` / `输入行太长`

- 根因是 cmd.exe 8191 字符限制。常见触发：Conda `base` 等把 PATH 撑得很长；或同一 PowerShell 会话里上次导入留下的 `__VSCMD_PREINIT_PATH` / `EXTERNAL_INCLUDE` / `LIBPATH` 在再次调用 `VsDevCmd.bat` 时被再次拼接。脚本会在最小 Windows PATH 下清除这些瞬时变量后调用 `VsDevCmd.bat`，合并 MSVC 目录回原 PATH，且不把 `__VSCMD_*` 写回父进程。
- 若仍失败，新开一个未加载过 VS 环境的 PowerShell 窗口后重试同一命令。

### Python 或 npm 首次安装很慢

- 首次准备或依赖缓存失效时会下载并同步完整开发、测试和 PyInstaller 依赖；后续运行只有在输入指纹与全部环境探针均通过时才复用，不能为追求速度手工伪造缓存记录。

### Python 测试 `test_mcp_injection_uses_state_backups_*` 偶发失败

- 根因是 Windows 上 `datetime.now()` 分辨率过粗，两次紧挨的 MCP 写入会生成同名备份并互相覆盖。当前实现已用 `timestamp-序列号-文件名` 保证唯一。

### 正式目录无法替换

- 关闭从 `release/desktop/` 启动的便携版或安装程序后重试；运行中的 exe 可能阻止目录移动。

### 发布切换被强制中断

- 正式目录切换使用 `final -> .backup -> final` 的事务式目录移动；脚本捕获到移动失败时会尝试回滚，但断电或强制终止进程可能在 `release/desktop/` 留下 `.x86_64-pc-windows-msvc.backup-<GUID>`。如果正式目录缺失，先停止所有发布进程，确认备份内同时存在桌面 exe、sidecar、MSI 与 NSIS，再把唯一正确的隐藏备份目录手工改回 `x86_64-pc-windows-msvc`；若正式目录存在，先验证其哈希与冒烟结果，再决定是否删除旧备份。

### 安装程序要求重启

- 重启 Windows 后重新执行同一条 PowerShell 命令；环境脚本是幂等的，只补齐仍缺失的系统工具。

## 发布检查清单

基础安装包：

- [ ] PowerShell 自测、pytest、Ruff、Vitest、npm audit 和 Vite build 全部通过。
- [ ] sidecar 缓存已按当前源码、Python/PyInstaller 环境、依赖清单、target triple 与产物哈希验证；缓存未命中时已 clean 重建。
- [ ] MSI 与 NSIS 同时存在。
- [ ] 收集后的 sidecar JSON 冒烟通过。
- [ ] 已在干净 Windows x64 目标机安装 Git for Windows/GCM，设置页显示 Git/GCM 已就绪。
- [ ] 已用无缓存凭据的私有 HTTPS 仓库完成 GCM 登录、读写验证和绑定；后续刷新与 pull/push 不重复登录。
- [ ] 已验证只有读取权限的账号绑定失败，且原绑定不变。
- [ ] 正式目录打印了四类产物的 SHA-256。
- [ ] `build/metrics/` 中存在本次运行的 JSON，终端摘要与其成功/失败状态一致。
- [ ] 已在干净 Windows x64 目标机完成安装、启动和升级验证。
- [ ] 代码签名、上传和发布说明由独立发布步骤处理。
