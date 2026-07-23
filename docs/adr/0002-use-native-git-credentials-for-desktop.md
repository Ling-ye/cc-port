# 桌面端使用原生 Git 凭据，不管理 GitHub OAuth

桌面端只连接用户提供的完整 HTTPS 仓库地址，并让 Git Credential Manager 负责 GitHub 登录与系统凭据存储；应用不再部署 OAuth broker、保存 Token 或调用仓库管理 API。这样把桌面能力限制为 Git 内容读写，同时保留 CLI/MCP 的 PAT、SSH 与 GitHub API 兼容路径，代价是正式 Windows 包必须依赖外部 Git for Windows/GCM，且用户需自行在 GitHub 管理仓库生命周期。

## Considered Options

- 拒绝 GitHub App/OAuth broker：它需要应用注册、回调服务、Token 生命周期与权限管理，超出了“连接既有仓库并同步内容”的桌面目标。
- 拒绝把 Git/GCM 打包进应用：这会让应用承担 Git/GCM 的更新、安全修复和系统凭据集成责任。

## Implementation Boundary

- 桌面配置加载会清空仅供 CLI/MCP 使用的 GitHub Token 副本，且不会回写配置文件。
- 桌面仓库入口只有 `config_bind_repo`；旧的远端创建与任意仓库切换动作不再暴露。
- 需要从 GitHub 收集资源时由用户显式选择资源类型，避免为自动识别调用 GitHub API。
