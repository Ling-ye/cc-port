# 快速开始

本指南面向使用 CC Port Windows 桌面版的用户。完成首次配置后，你可以扫描多个 AI coding 工具的资源，并在自己的私有 Git 仓库与本机工具目录之间逐项同步。

## 1. 准备环境

目标电脑需要：

- Windows 10 或 Windows 11 x64。
- [Git for Windows](https://git-scm.com/download/win)。
- Git Credential Manager（Git for Windows 默认包含）。
- 一个由你控制的 GitHub 私有仓库。

安装 Git 后，在 PowerShell 中确认：

```powershell
git --version
git credential-manager --version
git config --global --get-all credential.helper
```

如果最后一条命令没有显示 Git Credential Manager，请先完成 Git for Windows 的凭据管理配置，再启动 CC Port。

## 2. 安装 CC Port

1. 打开 [CC Port v0.5.1 Public Beta](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.1)。
2. 下载 `cc-port_0.5.1_windows_x64_setup.exe`。
3. 对照同一 Release 中的 `SHA256SUMS.txt` 验证文件哈希。
4. 双击安装器并按提示完成安装。

v0.5.1 尚未代码签名。Windows SmartScreen 可能显示“未知发布者”；确认下载地址为 `github.com/Ling-ye/cc-port` 且 SHA-256 一致后，选择“更多信息”继续安装。

安装包已包含桌面程序与 Python sidecar，不需要额外安装 Python、Node.js 或 Rust。

## 3. 创建资源仓库

在 GitHub 创建一个私有仓库，例如 `ai-coding-resources`：

- Visibility 选择 **Private**。
- 不要在仓库或 URL 中放入 Token。
- 默认分支使用 `main`。
- 仓库只用于保存可同步资源与 `registry.yaml`，不要把应用备份目录放进去。

CC Port 不会替你创建、删除仓库或改变仓库可见性。

## 4. 连接仓库

1. 启动 CC Port。
2. 打开“设置”。
3. 查看 Git、Git Credential Manager 和 `credential.helper` 诊断结果。
4. 粘贴仓库根地址，例如：

   ```text
   https://github.com/<owner>/<repo>
   ```

5. 点击“连接并验证仓库”。
6. 首次使用时，Git Credential Manager 可能打开浏览器完成 GitHub 登录。

验证阶段只读取远端引用并执行写权限探测，不会上传资源。后台刷新保持非交互；凭据失效时，请回到设置页重新验证。

## 5. 扫描与同步

1. 打开“资源”页。
2. 选择要扫描的全局或项目范围。
3. 点击扫描，检查发现的 Skill、MCP、Rule、Prompt 和 Plugin。
4. 对某个资源选择：
   - **上传到仓库**：将本地实例写入私有仓库。
   - **安装到工具**：将远端资源写入选中的工具目录。
   - **另存副本**：保留当前实例并使用新名称。
   - **设置安装别名**：同一逻辑资源在不同平台使用不同目录名。
5. 检查操作计划、警告与阻断项，确认后再执行。

CC Port 不会把“远端缺失”解释为删除命令，也不会静默覆盖没有 CC Port 所有权标记的同名本地内容。

### Cursor Prompt 命令

Cursor Prompt 默认安装到 `~/.cursor/commands/<name>.md`，并可在 Cursor 中以
`/<name>` 调用。远端仓库仍把它保存为 `prompts/<name>/`；从该目录下载到 Cursor
时，目录根级必须恰好有一个非符号链接 `.md` 文件，否则操作计划会阻断。设置安装
别名会改变本地命令文件名。自定义平台没有配置 `prompts_dir` 时，继续使用旧的
`rules_dir/<install-name>` 目标。

## 升级与卸载

当前版本没有自动更新。升级时从 [Releases](https://github.com/Ling-ye/cc-port/releases) 下载新安装器并覆盖安装。

卸载 CC Port 不会删除：

- 你的 GitHub 资源仓库。
- AI coding 工具原生目录中的资源。
- 本机 CC Port 状态、备份和操作历史。

确需删除本机状态时，先确认不再需要恢复记录，再按[故障排查](troubleshooting.md#本机状态目录)中的说明处理。

## 下一步

- [故障排查](troubleshooting.md)
- [安全策略](../SECURITY.md)
- [支持范围与已知限制](../README.md#当前限制)
- [CLI 与开发指南](development.md)
