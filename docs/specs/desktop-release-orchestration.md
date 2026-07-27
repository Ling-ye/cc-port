# Windows 桌面发布编排规格

## 目标

- Windows x64 构建用户只执行 PowerShell 命令，不需要手工安装或调用 Python。
- 构建内部继续使用仓库 `.venv` 和现有 PyInstaller sidecar。
- 环境准备与完整发布分别只有一个公开入口。
- 本地发布完成后同时生成一个只含最终用户下载资产的确定性目录，供维护者手工上传 GitHub Release。
- 项目采用短期分支、受保护的 `main`、版本标签和 GitHub Release，不维护长期 `release` 分支。

## 公开接口

```powershell
# 检查、安装并准备环境
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1

# 只检查，零写入
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -CheckOnly

# 无条件重新同步 Python 与前端依赖
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -ForceSync

# 自动准备环境并执行完整发布
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1

# 强制依赖同步、sidecar clean build 与 Cargo 主程序重新链接
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1 -Clean
```

- `setup.ps1` 默认汇总操作后确认一次，并支持 `-NonInteractive` 跳过确认；`release-desktop.ps1` 必须始终以非交互模式调用它，因此默认发布命令不读取 `y/n` 输入。发布入口继续接受 `-NonInteractive` 以兼容旧调用，但该参数不再改变行为。
- `-CheckOnly` 不安装系统包、不创建或修改 `.venv`、不运行 pip/npm 安装，并以退出码表示环境是否完整。
- `release-desktop.ps1` 必须先调用 `setup.ps1`，不能复制第二套环境安装逻辑。
- 默认入口必须复用验证通过的依赖、sidecar 与 Cargo 产物；`-ForceSync`/`-Clean` 是显式绕过路径，均不得执行 `cargo clean`。

## 平台与工具链

- 正式支持 Windows 10/11 x64、Windows PowerShell 5.1 和 `x86_64-pc-windows-msvc`。
- 环境脚本检测 Python 3.10–3.12 x64、受支持的 Node.js、npm、Git、Rustup/Cargo/rustc 和 Visual Studio C++ Build Tools。
- 构建测试必须使用当前机器动态发现的 Git 可执行文件，不得包含开发机专属绝对路径。
- 缺失的系统工具通过精确 WinGet 包 ID 安装；WinGet 缺失时必须在系统修改前停止。
- 安装完成后必须刷新当前进程 PATH，并通过 `vswhere.exe` 与 `VsDevCmd.bat` 导入和验证 MSVC linker 环境；`VsDevCmd.bat` 必须在清除 `__VSCMD_*` / `EXTERNAL_INCLUDE` 等瞬时变量后的最小 Windows PATH 下执行，再将 MSVC 目录合并回原 PATH，且不得把 `__VSCMD_*` 写回父进程，避免过长 PATH 或同会话二次导入触发 cmd「输入行太长」。
- 不兼容的仓库 `.venv` 必须重命名备份，不得直接删除。

## Rust 目标解析

1. 先解析 Cargo 路径。
2. 优先使用 Cargo 同目录的 rustc proxy，再尝试 `rustup which rustc`，最后才使用其他 PATH/fallback 候选。
3. 优先解析 `rustc --print host-tuple`。
4. 回退解析 `rustc -vV`，并容忍 BOM、ANSI 控制码、大小写和前导空白。
5. 目标不是 `x86_64-pc-windows-msvc` 时，选择或安装 `stable-x86_64-pc-windows-msvc`。
6. 两种解析都失败时，错误必须包含 rustc 绝对路径、退出码和截断输出。

## 发布门禁

发布按以下顺序执行；质量门禁组最多四路并行并等待全部结束后统一报告，组内任一失败都禁止进入后续打包，其他阶段失败时立即停止：

1. 环境检查、系统工具补齐，并验证依赖缓存；输入、工具或完整环境探针失效时才同步 `.venv` 与锁定 npm 依赖。发布入口必须在调用 PowerShell 环境脚本前显式初始化 native exit code，确保全新严格模式会话不会因未定义的 `$LASTEXITCODE` 误判失败。
2. 最多四路并行执行 PowerShell 构建逻辑自测、完整 pytest、Ruff、Vitest 与 `npm audit --package-lock-only --audit-level=moderate`；每项保留独立日志和真实退出码，900 秒超时记为 `124`。
3. 生成缺失图标，并验证 sidecar 的输入指纹、缓存产物哈希和隔离冒烟；未命中或冒烟失败时 clean 重建一次。
4. 默认只删除 Tauri bundle 与目标 sidecar，保留 Cargo 管理的主程序；`-Clean` 额外删除顶层主程序以强制重新链接，但不清理依赖编译产物。
5. 把验证通过的源 sidecar 显式复制到 Tauri `target/release/cc-port-desktop-api.exe`，并在构建前校验源、目标 SHA-256 一致。
6. 为 Rust release build 注入当前用户目录、Cargo/Rustup 目录和仓库目录的
   `--remap-path-prefix`，同时覆盖 Windows 路径的反斜杠与正斜杠形式；更具体的
   映射必须后置，且构建结束后恢复调用前环境变量。
7. 执行一次 Tauri release build；前端生产构建只由 `beforeBuildCommand` 触发一次，先在隔离目录生成 Vite 输出，内容未变化时保留现有 `desktop/dist` 及时间戳，内容变化时才事务式替换，再同时生成 MSI 与 NSIS。
8. 构建后再次验证 Tauri 目标 sidecar 与源 sidecar 的 SHA-256 一致，并以
   UTF-8、UTF-16LE 和 UTF-16BE 检查桌面 EXE 与 sidecar 不包含重映射前的
   主机路径，再把桌面 exe、sidecar、MSI 和 NSIS 复制到同级临时发布目录。
9. 在隔离状态目录运行临时 sidecar，验证 JSON `ok` 响应，并在临时目录计算四类产物 SHA-256。
10. 全部验证成功后事务式切换正式目录。
11. 验证 Python、NPM、Cargo、Tauri、运行时包和 Skill 元数据版本完全一致。
12. 从已验证的 NSIS 安装器事务式生成 `release/publish/v<version>/`，并写入安装器及其 `SHA256SUMS.txt`。
13. 在终端输出逐阶段耗时、缓存状态与总耗时，并原子写入本次 JSON 指标。

### 锁定前端依赖安全性

- `desktop/package-lock.json` 是桌面前端发布使用的锁定依赖图；根目录的空锁文件不参与桌面依赖审计。
- `npm audit --package-lock-only --audit-level=moderate` 必须返回 0；任何中危、高危或严重漏洞都阻止发布。
- 当存在满足上游语义版本范围的首个安全补丁时，只更新对应的传递依赖锁定项，不修改直接依赖声明，也不增加 `overrides`。
- GHSA-r28c-9q8g-f849 的修复必须使 `postcss` 锁定版本不低于 `8.5.18`。

### Rust 目标依赖安全性

- 正式发行目标仅为 `x86_64-pc-windows-msvc`；Rust 依赖公告必须区分锁文件中
  的全平台依赖与实际进入 Windows 目标图的依赖。
- `serde_with` 必须锁定为 `3.21.0` 或更高版本，以修复
  GHSA-7gcf-g7xr-8hxj。
- `glib 0.18.5` 的 GHSA-wrw7-89jp-8q8g 只存在于 Linux GTK/WebKit 目标图；
  Windows CI 必须用 `cargo tree --locked --target
  x86_64-pc-windows-msvc` 证明 Windows 目标不包含该版本。
- 只要项目仍不发布 Linux 桌面版，可以在 GitHub Dependabot 中以
  `not_used` 处置该目标专属公告，并在处置说明中引用本规格。正式支持 Linux
  前必须升级到 `glib >= 0.20.0`，不得继续沿用该例外。

## 产物与失败语义

- staging 目录必须在移动现有正式产物前通过安全路径检查并确认为真实目录；缺失时抛出与操作系统语言无关的项目错误，正式产物保持不变。
- 正式目录固定为 `release/desktop/x86_64-pc-windows-msvc/`。
- Windows 发布必须同时存在 MSI 与名称以 `-setup.exe` 结尾的 NSIS。
- 手工上传目录固定为 `release/publish/v<version>/`，并且只能包含：
  - `cc-port_<version>_windows_x64_setup.exe`
  - `SHA256SUMS.txt`
- `SHA256SUMS.txt` 使用小写 SHA-256、两个空格和安装器文件名，并以 LF 结尾。
- MSI、桌面 EXE 和 sidecar EXE 只作为本地验证产物，不进入手工上传目录。
- 手工上传目录与正式目录分别事务式替换；公开目录生成失败不得损坏已验证的正式目录或上一次公开目录。
- 测试、构建、安装包验证或 sidecar 冒烟失败不得覆盖上一次已验证目录。
- 正式目录事务切换发生可捕获移动失败时必须先尝试恢复旧目录；回滚本身失败时必须报告保留的 backup 绝对路径，不得掩盖原始错误。
- 同一仓库只允许一个发布入口持有 `build/cache/release.lock` 独占句柄；并发发布立即失败，独立执行的 `setup.ps1` 不受此锁保护。
- 稳定 Vite 输出切换只由正式发布锁序列化；正式发布期间或多个终端之间不得并发执行独立 `npm run build`。
- 依赖与 sidecar 缓存记录必须使用版本化 JSON envelope 和同目录原子写入；记录缺失、损坏、不完整、指纹或产物哈希不符时均按 miss 处理。
- 非 CheckOnly 的依赖预检只验证输入指纹与缓存记录；必须在真正复用前重新计算输入并执行唯一一次完整环境探针。
- 缓存 sidecar 冒烟失败只表示开始恢复；必须在 clean 重建、重建后冒烟与缓存刷新全部成功后才能把原失败阶段标记为已恢复。
- 每次发布尝试必须在 `build/metrics/` 保存成功/失败、总耗时、逐阶段退出码/缓存状态/日志和成功产物哈希。
- 删除和移动只能操作经过验证的预期父目录直接子项。
- 发布脚本不得读取、写入或输出 Git 凭据，不执行安装、代码签名或上传。

## 手工发布语义

1. 版本提交通过 Pull Request 合并到干净的 `main`。
2. 在该提交运行 `release-desktop.ps1`，完成本地构建、冒烟和公开目录生成。
3. 在干净 Windows 10/11 消费者环境安装、启动、连接私有仓库、同步、覆盖升级并卸载。
4. 为同一提交创建带 SSH 或 GPG 签名的 annotated tag，并推送标签。
5. 从标签创建 GitHub prerelease，粘贴中英文发布说明，并只上传公开目录中的两个文件。
6. 重新下载安装器和校验文件，确认下载后的 SHA-256 与 GitHub 资产摘要一致。

- 已公开的标签和安装器不得移动或替换；只允许修正文案与链接。产物变化必须使用新版本。
- Public Beta 安装器允许未做 Authenticode 签名，但发布说明和安装文档必须明确 SmartScreen 行为与 SHA-256 核验步骤。
- 企业策略禁止运行未签名应用的环境不属于当前支持范围。

## 验收标准

- Windows PowerShell 5.1 解析两个入口、共享模块和自测文件时没有语法错误。
- 自测覆盖版本规则、稳定指纹、缓存损坏与原子写入、发布锁、门禁并行退出码与逐任务超时、Rust 装饰输出、可执行文件 fallback、安全目录、MSI/NSIS 判断、staging 缺失预检以及发布替换与回滚。
- `setup.ps1 -CheckOnly` 在环境完整时返回 0，并保持 `.venv` 与 `node_modules` 不变。
- 默认 `release-desktop.ps1` 和 `release-desktop.ps1 -Clean` 都必须把 `-NonInteractive` 显式传给 `setup.ps1`，且发布脚本不得包含未携带该开关的环境准备调用。
- 锁定前端依赖图必须同时通过 `npm audit --package-lock-only --audit-level=moderate`、`npm test` 和 `npm run build`。
- 一条 `release-desktop.ps1` 命令完成所有门禁，生成并哈希桌面 exe、sidecar、MSI 和 NSIS，且 sidecar 冒烟通过。
- 发布入口必须拒绝版本不一致，并生成只含规范化 NSIS 安装器和匹配校验文件的公开目录。
- Windows Tauri CI 必须证明正式目标依赖树不包含 `glib 0.18.5`。
- Pull Request 中的 Gitleaks 扫描必须只授予 `contents: read` 和
  `pull-requests: read`，不得为读取扫描范围授予写权限。
- 公开目录中的安装器哈希必须与内部已验证 NSIS 完全一致；重复运行不得混入旧文件。
- 解包后的桌面 EXE 与 sidecar 不得包含构建用户名、用户目录、仓库绝对路径或
  CI 工作目录；NSIS 压缩层无匹配不能代替解包产物检查。
- 性能验收必须在同一提交、机器和工具版本下，对优化前与优化后分别执行三次成功的无修改默认暖构建，取 `value.durationMs` 中位数，并满足 `candidateMedian <= baselineMedian * 0.70`；`-Clean`、前端、Python 和 Rust 修改场景仅单独记录，不套用该硬门槛。

## 历史性能实验记录

以下数据是 2026-07-24 在同一验收机上的实现决策证据，不属于维护者日常发布
步骤。指标文件是本机构建记录，不作为仓库发布资产。

### 第一阶段

- Rust 脏构建基线中位数为 48.70 秒；仅生成 `rlib` 的候选中位数为
  54.49 秒，慢 11.89%，因此回退，继续保留 `staticlib`、`cdylib` 和
  `rlib`。
- 原质量门禁调度中位数为 51.285 秒；“四项并行后 pytest 独占”的候选为
  62.350 秒，慢 21.58%，因此回退并维持最多四路并行。
- 原完整暖发布中位数为 102.814 秒；两波候选为 115.701 秒，慢
  12.53%，超过 5% 非回退上限。
- 两组候选均通过完整功能门禁；回退原因是性能不达标，不是产物或行为错误。

### 第二阶段

- 只为 Windows 正式发布的 pytest 门禁增加 `pytest-xdist==3.8.0`，使用
  `workers = max(1, min(4, [Environment]::ProcessorCount))` 和
  `--dist load`；CI 保持串行 pytest。
- 串行 pytest 为 42.28 秒，4-worker 为 20.25 秒，均为 257 passed、
  1 skipped，测试墙钟减少 52.1%。
- 三次候选质量门禁中位数为 27.377 秒，相对 51.285 秒基线减少
  46.62%；完整暖发布中位数为 75.565 秒，相对 102.814 秒减少
  26.50%。
- 三次候选的依赖和 sidecar 缓存均命中，全部质量门禁、冒烟、哈希与四类
  内部产物验证通过，因此保留 xdist 方案。
