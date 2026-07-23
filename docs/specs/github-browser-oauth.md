# GitHub 浏览器无输入授权规格

## 目标与边界

- [FRAME] 设置页的“GitHub 访问”使用系统浏览器完成 OAuth Web Flow；用户不输入设备码、Token、Client ID 或其他字符串。置信度：HIGH。
- [FRAME] “连接资源仓库”的 URL 输入、绑定验证、Git Credential Manager、SSH Key 和 Owner 解析规则不变。置信度：HIGH。
- [FRAME] 旧设备流 Desktop API 保留兼容，但新设置页只调用浏览器流 API。置信度：HIGH。

## 用户流程

1. [FRAME] 用户点击“登录 GitHub”，桌面端创建 PKCE verifier，并向 OAuth broker 创建十分钟会话。置信度：HIGH。
2. [FRAME] 桌面端使用系统浏览器打开 broker 返回的 GitHub authorization URL。置信度：HIGH。
3. [FRAME] GitHub 回调 broker；broker 验证 `state` 后记录授权码或拒绝状态。置信度：HIGH。
4. [FRAME] broker 页面尝试打开 `lingye-lpm://oauth/complete`；Tauri 聚焦已有窗口并触发立即轮询。置信度：HIGH。
5. [FRAME] 定时轮询始终保留，作为浏览器阻止自定义协议时的兜底。置信度：HIGH。
6. [FRAME] Python sidecar 从 broker 取得 Token 后再次验证账号与 scope，验证通过才原子覆盖旧 Token。置信度：HIGH。

## Broker 协议

- [FRAME] `POST /v1/oauth/sessions` 接收 `purpose` 与 `code_challenge`，返回 `session_id`、`poll_token`、`authorization_url`、`expires_in` 和 `interval`。置信度：HIGH。
- [FRAME] `GET /oauth/callback` 只接收 GitHub 的 `code`、`state` 或 `error`，不在 HTML、URL、日志或深链中返回授权码。置信度：HIGH。
- [FRAME] `POST /v1/oauth/sessions/{id}/poll` 使用 Bearer `poll_token`，并接收 `code_verifier`；等待时返回 `pending`，完成时换取 Token。置信度：HIGH。
- [FRAME] `DELETE /v1/oauth/sessions/{id}` 使用 Bearer `poll_token` 取消会话。置信度：HIGH。
- [FRAME] 每个会话使用独立 Durable Object，最多存活十分钟，完成、取消、拒绝或过期后清空。置信度：HIGH。

## 权限与安全

- [FRAME] 授权目的固定为 `standard`、`organization_owner`、`remote_delete`；对应基础 scope 为 `repo`、`repo read:org`、`repo delete_repo`。置信度：HIGH。
- [FRAME] 客户端不能提交 scope；Worker 只按授权目的生成固定 scope，拒绝以其他输入扩大权限。置信度：HIGH。
- [FRAME] GitHub OAuth `client_secret` 仅存在于 Cloudflare Secret；桌面包只包含 broker URL。置信度：HIGH。
- [FRAME] `state`、PKCE、随机轮询凭据、请求大小限制、固定 GitHub 端点和会话限速必须同时启用。置信度：HIGH。
- [FRAME] Token 只在 broker 与 Python sidecar 的 HTTPS 响应中出现，不进入前端、Tauri IPC、任务记录、日志或错误文本。置信度：HIGH。
- [FRAME] `LPM_GITHUB_TOKEN` 生效时禁止 GUI 重新授权；GUI 不能查看、替换或清除环境变量 Token。置信度：HIGH。

## Desktop API

- [FRAME] `github_web_auth_start` 请求 `{purpose}`，响应 `{session_id, authorization_url, expires_in, interval, purpose, scopes}`。置信度：HIGH。
- [FRAME] `github_web_auth_poll` 请求 `{session_id, immediate?}`，响应继续使用 `pending`、`authorized`、`denied`、`expired`。置信度：HIGH。
- [FRAME] `github_web_auth_cancel` 请求 `{session_id}`，响应 `{cancelled}`。置信度：HIGH。
- [FRAME] `github_auth_start`、`github_auth_poll`、`github_auth_cancel` 继续表示旧设备流，不删除字段或改变语义。置信度：HIGH。

## 配置与发布

- [FRAME] 项目维护一个官方共享 OAuth broker；安装包和源码构建默认使用源码内置的官方 HTTPS origin，普通用户不部署 Worker、不输入 broker URL、Client ID 或 Client Secret。置信度：HIGH。
- [FRAME] 开发、自托管和企业环境可用 `LPM_GITHUB_OAUTH_BROKER_URL` 在运行时覆盖内置 broker。置信度：HIGH。
- [FRAME] `github_auth_status.oauth_configured` 表示浏览器流 broker 是否可用，不再表示设备流 Client ID 是否存在。置信度：HIGH。
- [FRAME] 打包与 broker 部署解耦：内置 broker URL 为空或无效时发布脚本必须给出醒目警告但继续构建，运行时只禁用 GitHub 登录，资源仓库和其他功能不受影响。置信度：HIGH。
- [FRAME] 官方分发包应配置共享 broker；缺少 broker 的包属于“GitHub 登录未启用”的有效降级包，而不是构建失败。置信度：HIGH。
- [FRAME] broker 部署所需 OAuth Client ID 与 Client Secret 由维护者一次性配置，不由终端用户提供。置信度：HIGH。

## 验收

- [FRAME] 未授权用户只需点击登录、在 GitHub 页面批准，即可回到应用并看到账号。置信度：HIGH。
- [FRAME] 等待界面只有状态、“重新打开 GitHub”和“取消”，不存在设备码或复制操作。置信度：HIGH。
- [FRAME] 浏览器打开失败时会话保留；拒绝、过期、broker 故障或 Token 校验失败时旧 Token 保留。置信度：HIGH。
- [FRAME] 深链只携带会话 ID 与结果状态，且只有匹配当前活动会话时才能触发轮询。置信度：HIGH。
- [FRAME] 内置 broker 为空时仍能生成 MSI、NSIS 与便携产物；设置页显示服务未配置并禁用登录按钮。置信度：HIGH。
- [FRAME] Worker、Python、前端、Tauri、发布门禁和文档测试全部通过。置信度：HIGH。
