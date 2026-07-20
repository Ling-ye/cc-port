# GitHub 资源仓库绑定规格

## 目标

桌面设置允许用户只提供一个 `github.com` 根仓库链接，通过一次显式操作验证 Git 读取和写入链路并保存绑定。绑定不是同步操作，不得下载仓库内容、创建远端仓库、更新远端引用或初始化本地资源目录。

## 输入与规范化

- 接受 `https://github.com/<owner>/<repo>[.git]`、`git@github.com:<owner>/<repo>[.git]` 和 `ssh://git@github.com/<owner>/<repo>[.git]`。
- HTTPS 保存为带 `.git` 的规范地址；SSH 保存为 scp 形式，并保留用户选择的协议。
- 拒绝 HTTP、非 `github.com` 主机、自定义端口、查询参数、片段、内嵌凭据及仓库根目录之外的路径。
- 请求必须携带页面加载时看到的 `expected_current_repo_url`；实际配置已变化时拒绝保存。

## 验证流程

1. 使用 `git ls-remote --symref` 验证读取权限并发现默认分支与分支列表；不得 clone 或 fetch。
2. 在系统临时目录创建一次性空 Git 仓库，以唯一探测分支执行 `git push --dry-run --porcelain`。
3. 只有读取和写入探测都成功时才写配置；任何错误、超时或用户取消都保持原配置。
4. 临时仓库始终清理，探测分支不得出现在远端。

HTTPS 绑定允许 Git Credential Manager 在这次用户触发的操作中打开图形登录，同时保持终端提示关闭。SSH 始终使用 `BatchMode` 和连接超时；失败时提示用户加载 SSH Key 或改用 HTTPS。

## 配置语义

- 一键绑定设置 `resources.credential_mode = "native"`，资源仓库的 Git 传输使用 GCM/系统凭据或 SSH Key，不注入全局 GitHub API Token。
- 简化设置页不再暴露 `credential_mode`、`branch` 或 `local_path`；这些字段仍可通过 `config.toml` 与 CLI 管理。
- 旧配置缺少该字段时视为 `auto`，继续使用 Token 优先的兼容行为；`token` 强制要求有效 Token。
- 成功绑定更新 `repo_name`、`repo_url` 和默认 `branch`。
- 同一仓库与协议的重新验证保留自定义 `local_path`；切换仓库或协议时清空路径配置，旧目录不移动、不删除。

## 后续拉取与推送

- 绑定完成后不创建本地目录。首次显式拉取在目标不存在或为空时 clone，并在本地补齐 LPM 结构，但不自动提交或推送。
- 本地目标非空且不是 Git 仓库、缺少 origin 或 origin 与绑定不一致时阻断。
- 未首次拉取时禁止推送；所有资源仓库缓存、资产同步、环境同步及遗留同步使用相同 `credential_mode`。

## 验收标准

- 绑定调用只包含 `ls-remote`、本地临时仓库命令和 `push --dry-run`，不包含真实 clone/fetch/pull/push 更新。
- HTTPS 首次登录成功后，后续无交互资源操作可复用系统凭据。
- 过期的全局 Token 不影响 `native` 资源仓库。
- 重新绑定保留旧目录内容，并把新仓库的本地创建延迟到首次拉取。
- 错误、任务记录、配置和远端 URL 不包含凭据。
- 设置页重新绑定只调用窄接口，不得通过完整配置保存覆盖隐藏字段。
