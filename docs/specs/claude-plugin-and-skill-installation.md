# Claude Code Plugin 与 Skill 识别、同步和安装规格

本文定义 CC Port 对 Claude Code Plugin 和 Skill 的原生语义、可上传来源、下载目标和 Marketplace 安装流程。它不适用于 Codex Plugin；Codex 的 `.codex-plugin/plugin.json` 与 Claude Code 的 `.claude-plugin/plugin.json` 是两个独立格式，不能互相改名、转换或复用安装器。

官方语义来源：

- [Create plugins](https://code.claude.com/docs/en/plugins)
- [Plugins reference](https://code.claude.com/docs/en/plugins-reference)
- [Create and distribute a plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces)
- [Discover and install prebuilt plugins](https://code.claude.com/docs/en/discover-plugins)

## 三种 Claude 资源形态

| 形态 | 识别条件 | 本机名称 | CC Port 轨道 | 安装方式 |
| --- | --- | --- | --- | --- |
| 普通 Skill | `<skills-dir>/<name>/SKILL.md`，且同级没有 `.claude-plugin/plugin.json` | `<name>` | Skill content | 把普通文件快照写入 profile 的 `skills_dir` |
| skills-directory Plugin | `<skills-dir>/<plugin>/.claude-plugin/plugin.json` | `<manifest-name>@skills-dir` | Plugin content | 把完整普通文件快照写入用户 `skills_dir/<manifest-name>` 或项目 `.claude/skills/<manifest-name>`，再对齐 `enabledPlugins` |
| Marketplace Plugin | 原生设置或 CLI 返回 `<plugin>@<marketplace>`，并可关联 Marketplace 来源 | `<plugin>@<marketplace>` | Plugin reference | 通过目标 profile 对应运行环境中的 `claude plugin marketplace add` 和 `claude plugin install` 安装 |

一个 plugin 内部的 `skills/<skill>/SKILL.md` 是该 plugin 的组件，不是新的顶层 Skill 资源。一个 skills-directory Plugin 即使根目录也有 `SKILL.md`，仍必须先按 Plugin 识别。`~/.claude/plugins/cache`、Marketplace checkout、安装记录和 `${CLAUDE_PLUGIN_DATA}` 都是 Claude 的运行时状态，不是可上传的 Plugin content。

Claude Code 普通 Skill 的命令名来自目录名；`SKILL.md` frontmatter 的全部字段都是可选的，`name` 只影响展示，`description` 缺失时 Claude 使用正文首段。因此 Claude profile 的发现和上传不得套用要求 `name`/`description` 必填的其他工具或跨工具打包校验。`SKILL.md` 本身和非空指令正文仍是必要条件。个人、项目和 enterprise skills 位置中名为 `synced` 的目录由 Claude 的云同步机制保留，不作为普通可移植 Skill 上传。

## Claude Plugin 内容契约

CC Port 只把带 `.claude-plugin/plugin.json` 的目录作为可直接下载到 skills directory 的 Claude Plugin content。虽然 Claude Marketplace 或 `--plugin-dir` 可以加载没有 manifest 的插件目录，但缺少 manifest 时无法在同一个 `skills_dir` 中无歧义地区分普通 Skill 和 Plugin，因此该形态不进入 content 同步。

Plugin 根目录可以包含以下原生组件：

```text
<plugin>/
├── .claude-plugin/plugin.json
├── SKILL.md
├── skills/<name>/SKILL.md
├── commands/*.md
├── agents/*.md
├── workflows/
├── output-styles/
├── themes/
├── hooks/hooks.json
├── .mcp.json
├── .lsp.json
├── monitors/monitors.json
├── bin/
└── settings.json
```

只有 `plugin.json` 可以放在 `.claude-plugin/` 内。`skills/`、`commands/`、`agents/`、`hooks/` 等组件误放进 `.claude-plugin/` 时，发现和上传必须拒绝该 Plugin。Manifest 中的自定义组件路径必须以 `./` 开头、保持在 Plugin 根目录内并指向现存的非链接内容；`skills` 另外允许 `.` 或 `./`。Hooks、MCP、LSP 和 settings 的默认 JSON 文件必须是对象，monitor 文件必须是数组，相关 Markdown frontmatter 必须可解析。

Plugin 可以包含脚本、可执行文件、Hook、MCP、LSP、Monitor 和依赖声明。扫描、diff、上传和下载只把它们当作不可信数据，不执行任何内容。项目级 skills-directory Plugin 仍由 Claude Code 的 workspace trust、MCP 单项批准和其他原生限制决定是否运行；CC Port 写入文件不代表已授予运行权限。

## 发现和上传

Claude profile 的 `skills_dir` 是普通 Skill 与 skills-directory Plugin 的共同发现根。扫描每个直接子目录时必须先检查 `.claude-plugin/plugin.json`：

1. 有有效 manifest：产生一个 `kind=plugin`、`track=content` 的本地实例；不要再把它或内部 Skill 作为顶层 Skill 返回。
2. 没有 manifest：按普通 Skill 规则检查 `SKILL.md`。
3. 有 manifest 但格式无效：该目录不能降级成普通 Skill，必须保持不可上传并暴露阻断原因。

配置中的 Claude `plugins_dir` 只允许指向用户明确维护的 Plugin 源码集合；默认留空。即使用户误配为 `~/.claude/plugins`，CC Port 也不得把 Claude 的运行时根或 `cache/` 当作 content 来源。

上传 content 前必须再次验证 Plugin 结构、manifest name 与已发现的 `plugin_id`、本地实例身份、内容指纹、链接状态和疑似秘密。仓库收到的是普通文件快照，不包含符号链接、junction、reparse point、缓存或本机设置路径。

Marketplace Plugin 上传为 reference，不复制缓存。Registry v1 的 external `source.locator` 保存可移植 Marketplace 来源，例如 GitHub `owner/repo` 或无凭据的完整 Git URL；可选 `cc-port.yaml` 保存 Claude Marketplace 名称、Plugin id 和期望安装 scope。profile id、用户目录、项目本机路径、缓存路径和已解析凭据不得进入远端文件。

## 下载与安装

### skills-directory Plugin

用户 scope 的目标是精确 Claude profile 的 `skills_dir/<plugin_id>`。项目 scope 的目标是已经显式映射的项目根目录下 `.claude/skills/<plugin_id>`。Claude 没有 local-only skills-directory 位置，因此 content Plugin 的 local scope 必须阻断；如需 local scope，应使用 Marketplace reference。

计划必须绑定以下信息，并在 apply 前全部重检：

- 精确 profile id、`tool_id=claude-code`、Windows/WSL 环境种类和环境名；
- 远端 Plugin 指纹与 manifest name；
- 目标目录存在状态、内容指纹和 CC Port 所有权；
- 原生 settings 文件路径、存在状态和指纹；
- project scope 对应的精确 project mapping。

apply 以同一事务写入 Plugin 普通文件快照和 `enabledPlugins["<plugin>@skills-dir"]`。未接管的同名目录只有在计划明确确认覆盖后才能替换。项目 Plugin 被写入后仍需在 Claude Code 中接受 workspace trust；其他组件变化需要 Claude Code 重启或 `/reload-plugins` 才会生效。

### Marketplace Plugin

Marketplace 注册、下载、依赖安装、缓存布局、scope 设置和安装结果以 Claude CLI 为唯一实现。CC Port 不自行拼装 `~/.claude/plugins/cache`，也不把一个 Git 仓库直接复制到缓存目录。

legacy content installer 只复制实体目录，不能处理 reference Plugin，因此必须拒绝 Marketplace reference；这类资源只能进入 profile-aware asset workflow 和原生安装器。

可执行计划只在以下条件全部满足时产生：

- reference 的 platform 是 `claude-code`，origin type 是 `marketplace`；
- Marketplace 名称是 Claude Desktop 可接受的可移植标识，Plugin id 是最长 128 字符的 kebab-case 标识；
- Marketplace 已注册且其 source/ref 与计划一致，或远端 reference 带有可安装的 GitHub `owner/repo`/无凭据 Git URL；有 pin 时必须按 Claude 原生的 `@ref` 或 `#ref` 语法保留；
- user、project 或 local scope 的目标可以由精确 profile 和 project mapping 解析；
- `claude` 可执行文件属于该 profile 的同一运行环境。Windows profile 不能借用 WSL CLI，WSL profile 不能借用 Windows CLI，不同 WSL distribution 也不能互相代替。

执行顺序如下：

```text
claude plugin marketplace list --json
claude plugin marketplace add <source> --scope <scope>   # 仅在缺失时
claude plugin install <plugin>@<marketplace> --scope <scope>
claude plugin disable <plugin>@<marketplace> --scope <scope>  # 期望禁用时
claude plugin list --json                                  # 验证
```

调用时将 `CLAUDE_CONFIG_DIR` 绑定到目标 profile 的配置目录；project/local scope 同时把进程工作目录绑定到精确 project mapping。命令参数使用参数数组传递，不经过 shell，也不执行远端 description、Plugin 文本或 manifest 字段。

如果 CLI 不属于目标环境、Marketplace 来源不可移植、项目 mapping 缺失或 managed policy 控制该安装，计划保持 manual/blocked，不用复制模式伪装成功。CLI 执行失败时，新增的 Marketplace 声明应尽力移除；失败的写计划不能凭旧审批重试，必须重新 inventory、plan 和审批。

## Codex 边界

| 项目 | Claude Code | Codex |
| --- | --- | --- |
| Manifest | `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` |
| 本地直载 Plugin | `skills_dir/<name>`，标识为 `<name>@skills-dir` | Codex 原生 Plugin/Marketplace 目录 |
| 分发安装 | Claude Marketplace + `claude plugin ...` | Codex 自身的 Plugin/Marketplace 语义 |
| 原生状态 | Claude JSON `enabledPlugins` | Codex TOML Plugin 状态 |
| 运行时缓存 | `~/.claude/plugins/...`，只读观察 | Codex 自身缓存，只读观察 |

任何一侧的 manifest、配置键、Marketplace 名称、缓存布局或 CLI 命令都不能替另一侧生成。跨工具只共享远端逻辑资源模型和 plan/apply 安全合同，不共享原生 Plugin 格式。

## 验收条件

- 同一个 Claude `skills_dir` 中，普通 Skill 和 manifest-backed Plugin 能稳定区分，且不会重复发现内部 Skill。
- 无效 manifest、误放组件、自定义路径越界/缺失、错误 JSON 容器或缓存来源会 fail closed。
- skills-directory Plugin 上传为普通文件快照，下载回用户/项目 skills directory 并对齐 `<plugin>@skills-dir` 状态。
- Marketplace 名称与可移植来源能通过 Registry v1 和 `cc-port.yaml` 往返，不泄露本机路径。
- Marketplace 缺失时使用 Claude 原生 CLI 注册并安装；已注册时不会重复添加；禁用意图和安装后验证生效。
- 安装计划绑定精确 profile、环境、project mapping、settings 指纹和远端指纹；任一变化返回 stale plan。
- Codex Plugin 继续使用 `.codex-plugin` 和 Codex 原生状态，不被 Claude 识别器或安装器修改。
