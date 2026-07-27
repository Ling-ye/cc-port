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

# 无条件重新同步 Python 与前端依赖
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\setup.ps1 -ForceSync

# 自动准备环境并执行完整发布
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1

# 强制依赖同步、sidecar clean build 与 Cargo 主程序重新链接
Set-ExecutionPolicy -Scope Process Bypass -Force; & .\scripts\release-desktop.ps1 -Clean
```

- [KNOWN] `setup.ps1` 默认汇总操作后确认一次，并支持 `-NonInteractive` 跳过确认；`release-desktop.ps1` 必须始终以非交互模式调用它，因此默认发布命令不读取 `y/n` 输入。发布入口继续接受 `-NonInteractive` 以兼容旧调用，但该参数不再改变行为。置信度：HIGH。
- [KNOWN] `-CheckOnly` 不安装系统包、不创建或修改 `.venv`、不运行 pip/npm 安装，并以退出码表示环境是否完整。置信度：HIGH。
- [KNOWN] `release-desktop.ps1` 必须先调用 `setup.ps1`，不能复制第二套环境安装逻辑。置信度：HIGH。
- [KNOWN] 默认入口必须复用验证通过的依赖、sidecar 与 Cargo 产物；`-ForceSync`/`-Clean` 是显式绕过路径，均不得执行 `cargo clean`。置信度：HIGH。

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

[KNOWN] 发布按以下顺序执行；质量门禁组最多四路并行并等待全部结束后统一报告，组内任一失败都禁止进入后续打包，其他阶段失败时立即停止：置信度：HIGH。

1. 环境检查、系统工具补齐，并验证依赖缓存；输入、工具或完整环境探针失效时才同步 `.venv` 与锁定 npm 依赖。
2. 最多四路并行执行 PowerShell 构建逻辑自测、完整 pytest、Ruff、Vitest 与 `npm audit --package-lock-only --audit-level=moderate`；每项保留独立日志和真实退出码，900 秒超时记为 `124`。
3. 生成缺失图标，并验证 sidecar 的输入指纹、缓存产物哈希和隔离冒烟；未命中或冒烟失败时 clean 重建一次。
4. 默认只删除 Tauri bundle 与目标 sidecar，保留 Cargo 管理的主程序；`-Clean` 额外删除顶层主程序以强制重新链接，但不清理依赖编译产物。
5. 把验证通过的源 sidecar 显式复制到 Tauri `target/release/cc-port-desktop-api.exe`，并在构建前校验源、目标 SHA-256 一致。
6. 执行一次 Tauri release build；前端生产构建只由 `beforeBuildCommand` 触发一次，先在隔离目录生成 Vite 输出，内容未变化时保留现有 `desktop/dist` 及时间戳，内容变化时才事务式替换，再同时生成 MSI 与 NSIS。
7. 构建后再次验证 Tauri 目标 sidecar 与源 sidecar 的 SHA-256 一致，再把桌面 exe、sidecar、MSI 和 NSIS 复制到同级临时发布目录。
8. 在隔离状态目录运行临时 sidecar，验证 JSON `ok` 响应，并在临时目录计算四类产物 SHA-256。
9. 全部验证成功后事务式切换正式目录。
10. 在终端输出逐阶段耗时、缓存状态与总耗时，并原子写入本次 JSON 指标。

### 锁定前端依赖安全性

- [KNOWN] `desktop/package-lock.json` 是桌面前端发布使用的锁定依赖图；根目录的空锁文件不参与桌面依赖审计。置信度：HIGH。
- [KNOWN] `npm audit --package-lock-only --audit-level=moderate` 必须返回 0；任何中危、高危或严重漏洞都阻止发布。置信度：HIGH。
- [KNOWN] 当存在满足上游语义版本范围的首个安全补丁时，只更新对应的传递依赖锁定项，不修改直接依赖声明，也不增加 `overrides`。置信度：HIGH。
- [KNOWN] GHSA-r28c-9q8g-f849 的修复必须使 `postcss` 锁定版本不低于 `8.5.18`。置信度：HIGH。

## 产物与失败语义

- [KNOWN] staging 目录必须在移动现有正式产物前通过安全路径检查并确认为真实目录；缺失时抛出与操作系统语言无关的项目错误，正式产物保持不变。置信度：HIGH。
- [KNOWN] 正式目录固定为 `release/desktop/x86_64-pc-windows-msvc/`。置信度：HIGH。
- [KNOWN] Windows 发布必须同时存在 MSI 与名称以 `-setup.exe` 结尾的 NSIS。置信度：HIGH。
- [KNOWN] 测试、构建、安装包验证或 sidecar 冒烟失败不得覆盖上一次已验证目录。置信度：HIGH。
- [KNOWN] 正式目录事务切换发生可捕获移动失败时必须先尝试恢复旧目录；回滚本身失败时必须报告保留的 backup 绝对路径，不得掩盖原始错误。置信度：HIGH。
- [KNOWN] 同一仓库只允许一个发布入口持有 `build/cache/release.lock` 独占句柄；并发发布立即失败，独立执行的 `setup.ps1` 不受此锁保护。置信度：HIGH。
- [KNOWN] 稳定 Vite 输出切换只由正式发布锁序列化；正式发布期间或多个终端之间不得并发执行独立 `npm run build`。置信度：HIGH。
- [KNOWN] 依赖与 sidecar 缓存记录必须使用版本化 JSON envelope 和同目录原子写入；记录缺失、损坏、不完整、指纹或产物哈希不符时均按 miss 处理。置信度：HIGH。
- [KNOWN] 非 CheckOnly 的依赖预检只验证输入指纹与缓存记录；必须在真正复用前重新计算输入并执行唯一一次完整环境探针。置信度：HIGH。
- [KNOWN] 缓存 sidecar 冒烟失败只表示开始恢复；必须在 clean 重建、重建后冒烟与缓存刷新全部成功后才能把原失败阶段标记为已恢复。置信度：HIGH。
- [KNOWN] 每次发布尝试必须在 `build/metrics/` 保存成功/失败、总耗时、逐阶段退出码/缓存状态/日志和成功产物哈希。置信度：HIGH。
- [KNOWN] 删除和移动只能操作经过验证的预期父目录直接子项。置信度：HIGH。
- [KNOWN] 发布脚本不得读取、写入或输出 Git 凭据，不执行安装、代码签名或上传。置信度：HIGH。

## 验收标准

- [KNOWN] Windows PowerShell 5.1 解析两个入口、共享模块和自测文件时没有语法错误。置信度：HIGH。
- [KNOWN] 自测覆盖版本规则、稳定指纹、缓存损坏与原子写入、发布锁、门禁并行退出码与逐任务超时、Rust 装饰输出、可执行文件 fallback、安全目录、MSI/NSIS 判断、staging 缺失预检以及发布替换与回滚。置信度：HIGH。
- [KNOWN] `setup.ps1 -CheckOnly` 在环境完整时返回 0，并保持 `.venv` 与 `node_modules` 不变。置信度：HIGH。
- [KNOWN] 默认 `release-desktop.ps1` 和 `release-desktop.ps1 -Clean` 都必须把 `-NonInteractive` 显式传给 `setup.ps1`，且发布脚本不得包含未携带该开关的环境准备调用。置信度：HIGH。
- [KNOWN] 锁定前端依赖图必须同时通过 `npm audit --package-lock-only --audit-level=moderate`、`npm test` 和 `npm run build`。置信度：HIGH。
- [KNOWN] 一条 `release-desktop.ps1` 命令完成所有门禁，生成并哈希桌面 exe、sidecar、MSI 和 NSIS，且 sidecar 冒烟通过。置信度：HIGH。
- [KNOWN] 性能验收必须在同一提交、机器和工具版本下，对优化前与优化后分别执行三次成功的无修改默认暖构建，取 `value.durationMs` 中位数，并满足 `candidateMedian <= baselineMedian * 0.70`；`-Clean`、前端、Python 和 Rust 修改场景仅单独记录，不套用该硬门槛。置信度：HIGH。
