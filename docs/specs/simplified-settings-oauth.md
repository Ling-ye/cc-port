# 设置简化、GitHub OAuth 与说明页合并规格

## 目标

桌面设置只负责四件事：绑定资源仓库、授权 GitHub、启停目标工具、按需运行环境诊断。`config.toml` 与 CLI 继续兼容旧的高级字段，但桌面端不再编辑 Owner、分支、凭据模式、目录、Git 可执行文件、仓库前缀或本地状态参数。

“说明”和“关于”合并为单一“说明”入口，页面依次展示资源类型、桌面功能和项目信息。

桌面侧栏只保留“资源”“设置”和“说明”。“操作历史”不再作为独立桌面入口，
也不由桌面应用根组件渲染；现有前端页面实现继续保留，以便后续按需重新接入。
持久化操作记录、恢复、状态清理、维护审计、CLI、Desktop API 和本机状态数据均保持不变。

## 新配置默认值

以下默认值只适用于不存在配置文件的新用户；已有配置继续按原值及旧版缺省语义加载。

- 新建资源仓库默认私有，仓库前缀为 `lpm-`。
- 资源仓库 URL 中的 Owner 是绑定后的权威 Owner；旧 `[github].owner` 只在尚未绑定资源仓库时兼容回退。
- 资源仓库分支为 `main`，Git 传输使用本机凭据，Git 可执行文件自动发现。
- Codex、Claude Code、Cursor、Windsurf、opencode 五个具有完整目录预设的工具默认启用。
- 工具目录来自内部工具适配器；Cline 与 Gemini CLI 没有完整可写预设，不进入默认平台列表。
- 锁等待、操作保留、最近操作保护和备份软上限继续使用 10 秒、90 天、20 次和 2048 MiB。

## 目标工具列表

- 目标工具始终使用单列纵向列表，不按窗口宽度切换为多列；新增工具只追加一行。
- 每行只显示 AI 工具名称和右侧启停开关，不展示路径、图标、安装状态、分类或搜索。
- 工具区域说明只描述同步目标，不使用“自动路径”措辞；同步可能创建未安装工具预设目录的通用警告继续保留。
- 平台筛选、排序、启停窄接口和旧配置兼容语义保持不变。

## 设置刷新

- 每次点击侧栏“设置”页签都静默刷新设置，包括重复点击已经激活的设置页签。
- 设置刷新并行读取 `config_get` 与 `github_auth_status`，不自动运行环境诊断。
- 设置页不显示页面级“刷新”或“重新加载”按钮；再次点击页签是用户主动重试入口。
- 多次刷新并发时只允许最新请求更新页面状态；刷新失败保留已经显示的数据，并沿用全局错误反馈。

## 按需诊断

- 桌面侧栏不保留独立“健康检查”入口；设置页底部使用默认收起的“诊断”区域。
- 展开区域不会自动请求；用户显式运行后复用 `doctor` 只读接口和任务中心反馈。
- 结果汇总正常、警告、错误和跳过数量，只逐条展示警告与错误；失败前清空旧结果。
- 诊断任务只禁用自身按钮，不阻断设置页其他操作；Python 服务、桌面接口和 `lpm doctor` CLI 保持兼容。

## GitHub 授权

正式桌面包默认使用项目维护的共享 GitHub OAuth broker，开发、自托管和企业环境可以用 `LPM_GITHUB_OAUTH_BROKER_URL` 在运行时覆盖。GitHub OAuth App 的 `client_id` 与 `client_secret` 只配置在 broker；禁止在客户端内置 `client_secret`。打包不依赖 broker 是否已部署；未配置有效 broker URL 时授权入口必须禁用并显示服务未配置，其他桌面功能保持可用。

新设置页授权采用系统浏览器 Web Flow：

1. `standard` 申请 `repo`。
2. `organization_owner` 按需申请 `repo read:org`。
3. `remote_delete` 在现有权限上按需追加 `delete_repo`。

PKCE verifier、broker 轮询凭据和会话状态保存在本机状态目录的临时会话中，前端只持有随机会话 ID 与公开 authorization URL。GitHub 回调 broker 后使用 `lingye-lpm://oauth/complete` 唤回桌面端，后台定时轮询作为兜底。新 Token 只有在账号与所需 scope 验证通过后才能覆盖旧 Token。

旧设备流及 `LPM_GITHUB_OAUTH_CLIENT_ID` 继续作为兼容接口保留，但新设置页不再调用或显示设备码。完整协议与安全边界见 `docs/specs/github-browser-oauth.md`。

Token 继续明文写入 `config.toml`。普通状态响应只能返回首尾各四位的掩码，完整 Token 只允许由兼容用显式 reveal 接口返回。设置页不显示 Token、来源或 scope，也不调用 reveal 接口；只显示连接状态、授权账号和登录、重新授权、移除本机授权操作。`LPM_GITHUB_TOKEN` 仍覆盖配置 Token，环境变量值不可由 GUI 查看、替换或清除。

## Owner 规则

- 已绑定资源仓库时，从规范化的 `resources.repo_url` 解析 Owner，并用于后续创建、发布及默认作者元数据。
- Owner 优先级固定为：资源仓库 URL Owner、旧 `[github].owner`、OAuth 授权账号。绑定时不把 URL Owner 复制进旧字段，避免形成两个可冲突的真源。
- 未绑定资源仓库的旧 CLI 和 API 调用继续读取 `[github].owner`；该字段为空时回退到 OAuth 授权账号。
- 显式资源仓库 URL 或资源条目自身携带的 Owner 不受全局 Owner 解析规则覆盖。
- `github_owner_set`、`organization_owner` 授权目的和旧 Owner 验证逻辑继续作为兼容接口保留，但新设置页不调用。
- 组织策略是否允许建仓由实际创建请求最终确认并原样报告失败。

## 桌面接口

- 保留 `config_get`、`config_save`、`config_branches` 兼容旧调用；新设置页不调用完整保存接口。
- `github_auth_status` 返回来源、状态、账号、scope 与掩码。
- `github_web_auth_start`、`github_web_auth_poll`、`github_web_auth_cancel` 管理新设置页的浏览器 OAuth 会话；前端只获得本机会话 ID 和公开授权 URL。
- `github_auth_start`、`github_auth_poll`、`github_auth_cancel` 管理设备流会话。
- `github_token_reveal`、`github_token_clear` 只操作配置 Token；新设置页只调用 clear，不调用 reveal。
- `github_owner_set` 继续验证并更新旧 Owner 字段，但新设置页不调用。
- `platform_set_enabled` 只更新一个预设平台的启停状态。

所有窄写入接口都必须先读取现有配置、只修改目标字段、再完整写回，禁止重置隐藏字段。除 `github_token_reveal` 外，Token 不得出现在进程参数、任务记录、普通响应、错误、日志或 Tauri 原始响应中。

## 说明页

- 侧栏只保留“说明”，删除独立“关于”视图。
- 项目信息展示项目名、定位、开发者、GitHub 仓库和 MIT 开源状态。
- GitHub 仓库链接为 `https://github.com/Ling-ye/LingyePluginMarketplace`，使用系统浏览器打开。

## 验收

- 高级设置控件在桌面端不可见，仓库绑定行为不回退。
- 设置页不显示 Owner 输入、Token、Token 来源或 scope，也不调用 Owner 保存或 Token reveal 接口。
- 已绑定 HTTPS 或 SSH 资源仓库时，URL Owner 覆盖冲突的旧 Owner；未绑定时旧 Owner 和 OAuth 账号回退保持兼容。
- 新配置生成五个启用的平台预设，旧配置的开关和路径不改变。
- 目标工具以单列语义列表展示，每项只包含工具名称和开关，页面不出现“自动路径”。
- 每次点击设置页签都会读取最新配置与 GitHub 授权状态，重复点击同一页签也生效，页面不显示手动刷新按钮。
- 侧栏不存在“健康检查”，设置页诊断默认收起且不自动运行，只展示异常详情。
- OAuth 的 PKCE、深链、等待、限速、拒绝、过期、成功和 scope 升级均有测试。
- OAuth 区显示账号及登录、重新授权、移除本机授权；授权时使用系统浏览器且不要求输入或复制字符串。
- Owner URL 解析、优先级、旧字段回退和 OAuth 账号兜底均有测试；旧 Owner 验证测试继续保留。
- 说明页包含三个信息区，侧栏不存在“关于”。
- 侧栏不存在“操作历史”，默认页面仍为“资源”，且操作历史相关 CLI、Desktop API 和本机数据不受影响。
- Python、Vitest、TypeScript/Vite、Rust/Tauri 与发布构建全部通过。
