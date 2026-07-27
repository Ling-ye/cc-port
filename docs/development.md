# 开发指南

CC Port 由 Python 核心、React 前端和 Tauri/Rust 桌面外壳组成。GUI、CLI 和 MCP Server 共用 `src/cc_port` 中的业务逻辑。

## 环境要求

- Python 3.10–3.12
- Node.js 20
- npm
- Rust stable
- Windows 桌面构建所需的 Visual Studio C++ Build Tools

Windows 可以使用仓库脚本检查并准备环境：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& .\scripts\setup.ps1
```

Linux 或 WSL 开发环境：

```bash
./scripts/setup.sh
```

## 启动桌面开发环境

Windows：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& .\scripts\dev.ps1
```

非 Windows 环境：

```bash
./scripts/dev.sh
```

开发脚本先构建 `cc-port-desktop-api` sidecar，再启动 Tauri 开发窗口。

## 质量检查

Python：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src/cc_port tests tools/packaging
```

前端：

```powershell
Push-Location .\desktop
npm test
npm run build
Pop-Location
```

Tauri：

```powershell
cargo check --manifest-path .\desktop\src-tauri\Cargo.toml
```

CI 会在 Ubuntu 和 Windows 上运行 Python 测试，在 Ubuntu 上运行前端测试与构建，并在 Windows 上检查 Tauri 后端。

## 规格驱动开发

行为变化先在 `docs/specs/` 中记录：

1. 明确问题、范围和不变量。
2. 写出用户可观察行为、失败模式和验收标准。
3. 增加会失败的测试。
4. 实现最小改动。
5. 更新 README、英文镜像、CHANGELOG 和相关技术文档。

架构级取舍记录在 `docs/adr/`。不要在 Tauri/Rust 桥接层重复 Python 业务逻辑。

## 桌面发布

唯一正式入口：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& .\scripts\release-desktop.ps1
```

该脚本验证版本与依赖、运行完整质量门禁、构建并冒烟测试 sidecar、生成
MSI 与 NSIS 安装器，并在所有检查通过后更新 `release/desktop/`。供人工
上传的规范化安装器与 `SHA256SUMS.txt` 单独写入
`release/publish/v<version>/`；脚本不签名或上传。

完整缓存、回滚、指标和故障处理规则见[桌面打包与发布](packaging-and-deployment.md)。

## 提交贡献

提交前阅读[贡献指南](../CONTRIBUTING.md)。较大的功能或行为变化必须先创建 Issue。
