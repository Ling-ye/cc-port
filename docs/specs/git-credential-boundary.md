# Git 凭据边界规格

## 目标

桌面端不获取、不保存也不显示 GitHub OAuth Token。桌面端仅通过原生 Git 访问用户已经创建的 GitHub 仓库；HTTPS 凭据由 Git Credential Manager（GCM）获取并保存在操作系统凭据库中。

CLI/MCP 的现有兼容能力不变：它们仍可使用 HTTPS、SSH、`LPM_GITHUB_TOKEN` 和配置 Token，并可继续调用已有 GitHub API 能力。

## 桌面仓库绑定

- 唯一入口是用户提供的完整 HTTPS 仓库根链接：`https://github.com/{owner}/{repository}`；`.git` 后缀可选。
- 拒绝 GitHub 用户/组织主页、仓库子路径、非 HTTPS 协议、非 `github.com` 主机、查询参数、片段、自定义端口和嵌入凭据。
- 桌面端将链接规范化为无凭据的 `https://github.com/{owner}/{repository}.git`。
- 用户显式点击“连接并验证仓库”后，先执行 `git ls-remote` 验证读取权限，再在临时仓库执行 `git push --dry-run` 验证写入权限。
- 两项验证均成功后才能保存绑定；失败、取消、超时或并发配置变更均不得改写当前绑定。
- 验证不会 clone、fetch、pull 或更新任何远端 ref。

## Git 与 GCM 前提

- `git_credential_status` 是只读桌面接口，报告 Git、GCM 和 `credential.helper` 的可用状态、版本、检测来源、结构化状态与官方安装入口。
- 状态检测不得执行登录、读取凭据内容或修改任何 Git 配置。
- 桌面绑定要求 Git 可用、GCM 可用且 `credential.helper` 已配置为 GCM；缺少任一前提时返回独立、可操作的错误。
- 正式 Windows x64 包依赖外部 Git for Windows/GCM，不在应用包中嵌入 Git，也不自动执行 GCM `configure` 或修改用户全局 Git 配置。
- 只有用户显式触发绑定验证时设置 `GCM_INTERACTIVE=auto`；后台刷新、clone、fetch、pull、push、分支读取和探测均设置 `GCM_INTERACTIVE=never` 与 `GIT_TERMINAL_PROMPT=0`。
- 凭据失效时后台操作返回“需要重新登录”，不弹出浏览器；用户回到设置页再次点击绑定验证以触发 GCM 交互。

## 桌面 GitHub API 边界

- 桌面端不暴露 `github_auth_*`、`github_web_auth_*`、Token reveal/clear 或 GitHub owner 修改接口。
- 桌面端不创建 GitHub 仓库、不删除整个 GitHub 仓库、不修改仓库可见性。
- 桌面端删除资源时，如果该操作会删除自有 GitHub 仓库，必须在任何副作用前拒绝；本地文件删除和仓库内容提交仍可继续使用。
- 用户在 GitHub 网站或其他工具中创建仓库、删除仓库和修改可见性。
- 桌面 API 不暴露 `resource_init` 或 `resource_use` 旁路；仓库绑定必须经过 `config_bind_repo` 的 HTTPS、GCM、读写权限与并发保护。
- 桌面运行时使用不含 GitHub Token 的配置副本；配置文件中的 PAT 仅供 CLI/MCP 使用。
- 桌面从 GitHub 收集资源时必须显式提交资源类型，使用原生 Git 解析提交，不通过 GitHub Contents API 自动识别。

## 凭据与错误安全

- GitHub Token 或其他 HTTPS 凭据不得进入远端 URL、命令参数、临时脚本、错误消息、任务记录或用户可见日志。
- HTTPS Token 兼容路径只通过 Git 子进程环境中的临时配置传递，不生成包含 Token 字面量的 AskPass 文件。
- branch、ref 和 remote 参数必须作为独立参数传递，并拒绝控制字符和以 `-` 开头的值。
- Git 错误中的 URL userinfo、认证头、环境变量值和 Token 必须脱敏。
- 用户取消 GCM 登录、需要重新登录、无写权限、Git 缺失、GCM 缺失、GCM 未配置和网络超时必须返回可区分的结构化错误。

## 验收标准

- `https://github.com/Ling-ye/repository` 与相同地址的 `.git` 形式规范化为同一绑定。
- `https://github.com/Ling-ye`、仓库子路径、SSH 地址和嵌入凭据的 HTTPS 地址在桌面 API 边界被拒绝。
- 无缓存凭据的私有仓库绑定允许 GCM 打开登录；成功后读写验证通过并保存绑定。
- 后续后台刷新和 pull/push 不重复弹出登录；凭据失效时提示重新登录。
- 无写权限账号即使 `ls-remote` 成功也不得保存绑定。
- Git/GCM 状态检测不改变全局配置，不返回凭据内容。
- 桌面 API 无 OAuth、Token 管理、仓库创建、整仓删除或可见性修改入口。
- 桌面配置响应不含 GitHub Token 状态或 GitHub API 管理字段，桌面 Git 操作不读取 CLI/MCP PAT。
- 桌面诊断不显示仅供 CLI/MCP 使用的 GitHub Token 检查。
- 桌面 GitHub 收集缺少显式资源类型时在网络调用前失败；显式类型路径不调用 GitHub API。
- CLI/MCP 的 PAT、SSH 和 GitHub API 回归测试保持通过。
