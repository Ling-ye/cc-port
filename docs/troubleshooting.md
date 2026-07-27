# 故障排查

## 安装器被 Windows SmartScreen 拦截

v0.5.1 安装器尚未代码签名。请先确认：

1. 文件来自 `https://github.com/Ling-ye/cc-port/releases`。
2. 文件名为 `cc-port_0.5.1_windows_x64_setup.exe`。
3. SHA-256 与 Release 中的 `SHA256SUMS.txt` 一致。

确认后，在 SmartScreen 窗口中选择“更多信息”继续。哈希不一致时立即删除文件，不要运行。受企业策略管理的电脑可能禁止绕过未签名程序；这类环境不在当前 Public Beta 支持范围。

## 设置页提示找不到 Git

安装 [Git for Windows](https://git-scm.com/download/win) 后重新启动 CC Port。非标准安装位置可以在 `config.toml` 的 `[git].executable` 中填写绝对路径，或设置 `CC_PORT_GIT_EXECUTABLE`。

在 PowerShell 中检查：

```powershell
git --version
where.exe git
```

## Git Credential Manager 不可用

检查：

```powershell
git credential-manager --version
git config --global --get-all credential.helper
```

Git for Windows 通常包含 Git Credential Manager。安装不完整时，请重新运行 Git for Windows 安装器并启用凭据管理组件。CC Port 不会自动修改全局 Git 配置。

## 连接仓库失败

桌面端只接受 GitHub 仓库根地址：

```text
https://github.com/<owner>/<repo>
```

以下地址会被拒绝：

- 用户或组织主页。
- `tree`、`issues` 或具体文件页面。
- 在 URL 中嵌入用户名、Token 或密码。
- 非 GitHub 域名和自定义端口。
- 桌面端设置中的 SSH 地址。

确认当前 GitHub 账号对仓库具有读取和推送权限。取消登录、无权限、网络超时和凭据失效会返回不同错误；修复后回到设置页重新验证。

## 刷新远端失败

- 确认仓库存在默认分支。
- 确认绑定的仓库没有被删除或改名。
- 在设置页重新执行连接验证，刷新 Git Credential Manager 凭据。
- 检查代理、防火墙和 GitHub 连接状态。

后台刷新不会弹出登录窗口。需要交互登录时必须回到设置页手动触发。

## 资源显示“目标冲突”

目标工具中存在同名资源，但没有 CC Port 所有权标记。为避免覆盖手工配置，CC Port 会阻断普通安装。

可选处理方式：

- 检查并手工备份现有内容后，再决定是否导入。
- 使用“另存副本”安装为新名称。
- 如果两者确实是同一资源，先通过受支持的导入流程接管，不要直接伪造所有权文件。

## 写操作失败

写操作不会自动重试。先查看任务中心或 CLI 输出，确认当前目标状态，再从原入口重新生成计划。

失败操作可能出现三种结果：

- 写入前失败：目标未改变。
- 写入后成功回滚：目标恢复，操作记录保留。
- 回滚失败：停止继续操作，保留备份并提交 Bug 报告。

提交 Bug 时不要粘贴 Token、私有仓库内容或未脱敏的 MCP 环境变量。

## 本机状态目录

Windows 默认状态目录：

```text
%LOCALAPPDATA%\cc-port
```

这里包含操作历史、备份、锁、远端缓存与快照。不要在 CC Port 运行时删除该目录。

如果需要排查，可临时设置独立状态目录：

```powershell
$env:CC_PORT_STATE_HOME = "D:\Temp\cc-port-state"
```

删除状态目录不会删除 GitHub 远端仓库，但会永久丢失本机恢复记录、所有权信息和备份。

## 收集诊断信息

公开 Issue 可以包含：

- CC Port 版本。
- Windows 版本。
- Git 与 Git Credential Manager 版本。
- 可复现步骤。
- 已脱敏的错误消息。
- 不包含用户数据的截图。

安全漏洞必须按照[安全策略](../SECURITY.md)私密报告。
