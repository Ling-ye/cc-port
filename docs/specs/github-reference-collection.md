# GitHub 引用收集与本地内容导入规格

## 目标

外部 Git 资源通过 Registry v1 的通用 `source` 进入资源仓库，不复制上游源码。本地导入用于用户自有资源、修改后的第三方资源或需要由当前仓库托管的快照，并通过 `path` 指向仓库内普通内容。

本规格服从 [Registry v1](registry-v1.md)。Registry 是工具中立清单，不保存 CC Port 安装状态、派生元数据或 MCP 配置副本。

## 外部 Git 引用

Git 引用使用以下结构：

```yaml
version: 1
resources:
  - kind: skill
    name: example-skill
    source:
      type: git
      locator: https://github.com/example/skills
      revision: 0123456789abcdef0123456789abcdef01234567
      subpath: skills/example
```

- `locator` 是不含凭据的规范化仓库定位符；
- `revision` 保存用户选择的版本策略。需要可复现收集时，CC Port 把 branch/tag 解析为完整 commit SHA 后再写入；
- `subpath` 是来源仓库内部的安全 POSIX 相对路径；
- 无法访问仓库、无法解析 revision、路径不存在或路径不安全时，收集失败且不写 Registry；
- 用户需要保存上游内容副本时，必须先取得本地内容再使用本地导入，Git 收集入口不隐式复制源码。

Registry 审计不联网验证外部来源。可达性只在用户执行收集、安装或显式来源检查时验证，并且不写回 Registry 健康缓存。

## MCP

Registry 不保存 `mcp_config`。需要可部署的 MCP 配置时，先脱敏并作为普通内容写入 `mcp/<name>/mcp.json|yaml|yml`，然后登记 `path`：

```yaml
version: 1
resources:
  - kind: mcp
    name: example-mcp
    path: mcp/example-mcp
```

```json
{
  "command": "npx",
  "args": ["-y", "@example/mcp-server"],
  "env": {
    "EXAMPLE_TOKEN": "${EXAMPLE_TOKEN}"
  }
}
```

非空环境变量字面量必须替换为 `${NAME}` 占位符。真实值不得进入 Registry、资源内容、diff、日志或错误信息。

只引用外部 Git 仓库而不保存 MCP 配置时，可以登记 `source`，但 CC Port 只能只读展示引用；在取得可部署配置前不能把它安装为 MCP server。

## 本地内容导入

- 内容按 `skills/`、`mcp/`、`rules/`、`prompts/` 或 `plugins/` 的约定目录保存；
- Registry 条目只写 `kind`、`name` 和 `path`；
- 描述、版本、作者和许可证从内容派生，不复制进 Registry；
- 文件遵循[统一资源文件策略](resource-file-policy.md)，排除真实环境文件、依赖、缓存、构建产物、二进制发布物和符号链接；
- MCP 内容在写入前执行统一脱敏；
- 本地导入不推导 Git 来源，也不自动改成 `source`。

## 恢复与失败语义

- Git 引用按保存的 `revision` 和 `subpath` 获取；固定 commit 不得回退到默认分支、最新提交或陈旧缓存；
- 仓库不可访问、凭据不足、revision 无法获取、subpath 不存在或内容验证失败时明确失败；
- 失败不改写 `revision`，也不自动把引用转换为内容副本；
- 远程 HTTP MCP 只能复现脱敏后的连接配置，不能证明服务端代码、数据、版本、可用性或行为。

## 接口约束

- CLI、Desktop API 和 Desktop GUI 调用同一收集服务；
- MCP 收集如果要成为可安装内容，必须写脱敏后的资源文件，不得把配置塞入 Registry；
- `skip_verify` 一类兼容入口不得跳过路径安全或凭据检查；
- 返回给调用方的运行时资源可以包含从内容解析的 MCP 配置，但保存 Registry 时必须丢弃所有派生字段。

## 验收

- 外部 Git Skill 只产生 `source`，仓库中没有隐式内容副本；
- 本地 Skill/MCP 只产生 `path`，Registry 不含描述、标签或 MCP 配置；
- MCP 资源文件只保留占位符，不保存真实环境值；
- 固定 commit 不可用时失败，不回退；
- 保存、加载和上传运行时资源后，Registry 仍符合 v1 canonical 格式。
