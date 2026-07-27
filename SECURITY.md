# Security Policy

CC Port 会读取和写入 AI coding 工具配置，并与用户控制的 Git 仓库交互。请不要在公开 Issue、Discussion、Pull Request 或日志中披露凭据、私有仓库内容或可利用的漏洞细节。

## Supported versions

安全修复只面向 GitHub Releases 中最新发布的 CC Port 版本。Public Beta 阶段不维护旧版本的安全补丁分支。

| Version | Supported |
| --- | --- |
| Latest GitHub Release | Yes |
| Older releases and source snapshots | No |

## Reporting a vulnerability

请使用仓库 Security 页面中的 **Report a vulnerability** 提交私密报告：

<https://github.com/Ling-ye/cc-port/security/advisories/new>

不要为安全问题创建公开 Issue。

报告应尽量包含：

- 受影响版本和操作系统。
- 漏洞类型、影响和所需前置条件。
- 最小复现步骤或概念验证。
- 受影响的文件、接口或资源类型。
- 建议的缓解或修复方向。
- 是否已经向其他人披露。

请在提交前删除真实 Token、Cookie、SSH Key、私有仓库内容和个人数据。需要共享敏感样本时，先在私密报告中说明，不要直接上传。

## Response process

维护者会：

1. 确认收到报告并判断是否能够复现。
2. 在 GitHub Security Advisory 中私密讨论影响和修复。
3. 准备修复、回归测试和升级说明。
4. 在修复版本可用后协调公开披露。

项目目前由个人维护，不承诺固定响应或修复时限。报告者可以在 Advisory 中请求署名。

## Security boundaries

CC Port 的桌面安全边界包括：

- GitHub HTTPS 凭据由 Git Credential Manager 保存，桌面端不读取或存储 Token。
- 后台 Git 操作保持非交互，只有用户在设置页明确验证仓库时允许登录。
- MCP 环境变量字面值在采集时替换为占位符。
- 写操作使用计划、所有权检查、备份、目标锁、结果验证和失败回滚。
- 远端缺失资源不会触发隐式删除。

这些边界被绕过、未按预期执行或导致敏感信息泄露时，应按安全漏洞私密报告。

一般 Bug、安装失败和不涉及安全边界的异常请使用公开 Issue 模板。
