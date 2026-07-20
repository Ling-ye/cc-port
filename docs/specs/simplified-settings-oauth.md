# 设置简化、GitHub OAuth 与说明页合并规格

## 目标

桌面设置只负责四件事：绑定资源仓库、授权 GitHub、填写新仓库 Owner、启停目标工具。`config.toml` 与 CLI 继续兼容旧的高级字段，但桌面端不再编辑分支、凭据模式、目录、Git 可执行文件、仓库前缀或本地状态参数。

“说明”和“关于”合并为单一“说明”入口，页面依次展示资源类型、桌面功能和项目信息。

## 新配置默认值

以下默认值只适用于不存在配置文件的新用户；已有配置继续按原值及旧版缺省语义加载。

- 新建资源仓库默认私有，仓库前缀为 `lpm-`。
- Owner 不自动填充；用户必须填写个人 GitHub 用户名或组织名。
- 资源仓库分支为 `main`，Git 传输使用本机凭据，Git 可执行文件自动发现。
- Codex、Claude Code、Cursor、Windsurf、opencode 五个具有完整目录预设的工具默认启用。
- 工具目录来自内部工具适配器；Cline 与 Gemini CLI 没有完整可写预设，不进入默认平台列表。
- 锁等待、操作保留、最近操作保护和备份软上限继续使用 10 秒、90 天、20 次和 2048 MiB。

## GitHub 授权

正式桌面包使用项目注册的 GitHub OAuth App 公共 `client_id`，开发环境可以用 `LPM_GITHUB_OAUTH_CLIENT_ID` 覆盖。禁止在客户端内置 `client_secret`。未配置正式 `client_id` 时授权入口必须禁用并显示构建配置错误。

授权采用设备流：

1. `standard` 申请 `repo`。
2. `organization_owner` 按需申请 `repo read:org`。
3. `remote_delete` 在现有权限上按需追加 `delete_repo`。

设备码保存在本机状态目录的临时会话中，前端只持有随机会话 ID。轮询必须遵守 GitHub 返回的间隔、`slow_down` 和过期时间。新 Token 只有在账号与所需 scope 验证通过后才能覆盖旧 Token。

Token 继续明文写入 `config.toml`。普通状态响应只能返回首尾各四位的掩码。完整 Token 只允许由显式 reveal 接口返回；GUI 在 30 秒、窗口失焦、组件卸载、重新授权或清除时重新掩码。`LPM_GITHUB_TOKEN` 仍覆盖配置 Token，环境变量值不可由 GUI 查看或清除。

## Owner 规则

- Owner 必填并由用户手工输入。
- 个人 Owner 必须与 OAuth 授权账号一致。
- 组织 Owner 必须存在，且授权账号必须是有效成员；成员信息不足时触发 `organization_owner` 权限升级。
- GitHub 没有无副作用的接口可以证明组织策略最终允许该成员建仓；保存时只验证身份、组织与成员关系，最终建仓权限由第一次创建请求确认并原样报告失败。

## 桌面接口

- 保留 `config_get`、`config_save`、`config_branches` 兼容旧调用；新设置页不调用完整保存接口。
- `github_auth_status` 返回来源、状态、账号、scope 与掩码。
- `github_auth_start`、`github_auth_poll`、`github_auth_cancel` 管理设备流会话。
- `github_token_reveal`、`github_token_clear` 只操作配置 Token。
- `github_owner_set` 只验证并更新 Owner。
- `platform_set_enabled` 只更新一个预设平台的启停状态。

所有窄写入接口都必须先读取现有配置、只修改目标字段、再完整写回，禁止重置隐藏字段。除 `github_token_reveal` 外，Token 不得出现在进程参数、任务记录、普通响应、错误、日志或 Tauri 原始响应中。

## 说明页

- 侧栏只保留“说明”，删除独立“关于”视图。
- 项目信息展示项目名、定位、开发者、GitHub 仓库和 MIT 开源状态。
- GitHub 仓库链接为 `https://github.com/Ling-ye/LingyePluginMarketplace`，使用系统浏览器打开。

## 验收

- 高级设置控件在桌面端不可见，仓库绑定行为不回退。
- 新配置生成五个启用的平台预设，旧配置的开关和路径不改变。
- OAuth 的等待、限速、拒绝、过期、成功和 scope 升级均有测试。
- Owner 的个人匹配、组织成员、权限不足和窄写入保留均有测试。
- 说明页包含三个信息区，侧栏不存在“关于”。
- Python、Vitest、TypeScript/Vite、Rust/Tauri 与发布构建全部通过。
