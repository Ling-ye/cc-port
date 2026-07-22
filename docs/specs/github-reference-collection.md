# GitHub 引用收集与本地内容导入规格

## 目标

第三方 GitHub Skill 和 MCP 通过引用进入私有资源仓库，不复制上游源码。引用在收集时解析并锁定到完整 commit SHA，使换机恢复不受上游分支或标签后续移动影响。本地导入继续保存内容，用于用户自有资源、用户修改过的第三方资源，以及明确需要由私有资源仓库托管的快照。

本规格只定义 `skill` 和 `mcp` 的 GitHub 收集、恢复与本地导入语义；插件继续遵循 [Registry v7](registry-v7.md) 的 content/reference 双轨规格，其他资源类型维持现有行为。

## 收集与导入

### GitHub 收集

- GitHub 收集创建 `source: external` 条目，只写 registry 引用，不在 `skills/`、`mcp/` 或兼容内容目录中复制上游文件。
- 输入中的仓库默认分支、分支或标签只用于本次解析；写入前必须解析为该时刻对应的完整 commit SHA。
- `repo` 保存规范化 GitHub 根仓库地址，`subdir` 保存仓库内资源相对路径，`ref` 只保存解析后的完整 commit SHA。MVP 不同时保存原始 selector 与锁定 SHA。
- 无法访问仓库、无法解析 ref、commit 不存在或 `subdir` 含 `..` 等不安全路径段时，本次收集失败且不写入或更新 registry。普通 GitHub 收集还必须先通过资源检测确认远端路径存在；兼容用的直接 API 在离线 SHA 模式下只保证路径语法安全。
- 用户要保存自有源码或修改过的第三方源码时，必须先取得本地目录再使用本地导入；GitHub 收集入口不提供“同时复制内容”模式。

Skill 引用示例：

```yaml
- name: example-skill
  kind: skill
  source: external
  repo: https://github.com/example/skills
  subdir: skills/example
  ref: 0123456789abcdef0123456789abcdef01234567
```

MCP 引用除相同的 `repo`、`subdir` 和锁定 `ref` 外，必须保存可部署的 `mcp_config`：

```yaml
- name: example-mcp
  kind: mcp
  source: external
  repo: https://github.com/example/mcp-servers
  subdir: servers/example
  ref: 89abcdef0123456789abcdef0123456789abcdef
  mcp_config:
    command: npx
    args:
      - -y
      - '@example/mcp-server'
    env:
      EXAMPLE_TOKEN: ${EXAMPLE_TOKEN}
```

`mcp_config` 必须在写 registry 前经过统一脱敏：已有 `${NAME}` 占位符保持不变，非空字面量环境变量值替换为以该环境变量名生成的 `${NAME}`，真实值不得进入 registry、资源内容、日志或错误信息。配置必须至少包含 `command` 或 `url`，并继续接受现有的参数与非敏感字段。

### 接口契约

- CLI `lpm collect` 和 Desktop API `collect` 复用同一收集服务与 ref 解析规则，不允许任一入口写入未解析的 branch/tag。
- 收集 MCP 时 `mcp_config` 为必填；CLI 接受 JSON 配置参数，Desktop API 接受 mapping。非 MCP 收集携带该字段时拒绝请求。
- 任何兼容用的 `skip_verify` 标志都不能允许新条目保存可变 ref，也不能跳过 `subdir` 路径语法安全校验。它只允许调用方离线提供完整 commit SHA；该 SHA 若在恢复时不可获取，必须明确失败。
- 返回的 entry 中 `ref` 必须已经是完整 commit SHA；调用方不得用输入 selector 覆盖它。

### 本地导入

- 本地导入创建 `source: local` 内容条目，把选定目录或 MCP 配置复制到私有资源仓库的管理路径，并在 registry 中保存该相对 `path`。
- 所有内容继续遵循[统一资源文件策略](resource-file-policy.md)：排除真实环境文件、依赖、缓存、构建产物、二进制发布物和符号链接，并执行敏感内容检查。
- MCP 内容和派生出的 `mcp_config` 同样必须脱敏；“保存内容”不允许绕过 `${NAME}` 占位符规则。
- 本地导入不要求或推导 GitHub 引用，也不自动转成 `source: external`。

## 恢复与失败语义

- 新收集的 GitHub 引用必须获取存储的 commit，并以 detached checkout 恢复；`subdir` 只限制安装内容范围，不改变 commit 身份。
- 上游默认分支、原始分支或标签移动后，恢复结果仍必须来自 registry 中的 commit SHA。
- 仓库不可访问、凭据不足、commit 无法获取、`subdir` 不存在或内容校验失败时，恢复明确失败；不得回退到默认分支、同名分支、同名标签、最新提交或本地陈旧缓存。
- 失败不改写 `ref`，不自动把引用转换为 content，也不生成上游镜像。
- MCP 恢复部署的是脱敏后的启动或连接配置。对于远程 HTTP MCP，LPM 只能复现 `url` 等配置，不能固定或证明远程服务端代码、数据、版本、可用性或运行行为。

## 兼容与 MVP 边界

- 既有 branch/tag `ref` 条目继续可读取和安装，不自动迁移、不猜测原始 selector，也不在普通加载时改写 registry。
- 旧 branch/tag 条目按原有可变引用语义解析，因此不具备新条目的可复现保证；通过新 GitHub 收集流程创建或显式更新后，才写为完整 commit SHA。
- 无论新 SHA 还是旧 branch/tag 无法解析，都明确失败且不得回退到其他 ref。
- Registry 版本和既有字段保持不变；MVP 不新增 selector/lock 双字段、不做上游镜像或离线制品缓存、不自动跟踪更新，也不增加签名或制品摘要。

## 验收标准

- 收集 GitHub Skill 后，registry 只出现 `repo`、`subdir` 和完整 commit SHA 引用，私有资源仓库中没有该 Skill 的内容副本。
- 收集 GitHub MCP 后，registry 同时包含锁定引用和脱敏后的 `mcp_config`，任何非空 `env` 字面量均未保存。
- 上游分支移动后，在另一台机器恢复仍使用收集时的 commit。
- 固定 commit 或仓库不可用时恢复失败，且没有默认分支、最新提交或陈旧缓存回退。
- 本地导入的 Skill/MCP 内容进入私有资源仓库，并应用文件排除、敏感检查和 MCP 脱敏规则。
- 现有 branch/tag registry 条目仍能加载；它们不可用时给出明确错误且保持原条目不变。
- 远程 HTTP MCP 恢复结果只承诺配置一致，不宣称服务端行为可复现。
