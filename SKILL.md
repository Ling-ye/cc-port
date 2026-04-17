---
name: lpm
description: Publish, register, install and sync AI agent resources (skills, MCP servers, rules) across Cursor and Claude Code through the LingyePluginMarketplace MCP server. Use when the user wants to publish a local skill or MCP config to GitHub, register a third-party resource, install or update all resources on a new machine, check for updates, or migrate their agent environment. Triggers include phrases like "publish/upload/share this skill", "add/track/collect this skill repo", "install my skills on this machine", "sync skills", "migrate skills", "add MCP server", "register MCP", "lpm", "发布 skill", "上传 skill", "登记 skill", "迁移技能", "同步技能", "添加 MCP 服务器".
---

# LingyePluginMarketplace (LPM)

LPM 管理 AI 编程助手的资源注册表 -- skills、MCP 服务器配置、规则 -- 跨 Cursor 和 Claude Code 两个平台。通过 MCP 工具（或 `lpm` CLI）完成发布、登记、安装、同步等操作。

## 配置

所有配置从 `~/.config/lpm/config.toml` 读取（`lpm init` 可生成模板）。核心字段：

- `[github].token` -- GitHub PAT（也可用 `LPM_GITHUB_TOKEN` 环境变量，优先级更高）
- `[github].owner` -- 发布仓库时的 GitHub 用户名或组织名
- `[github].repo_prefix` -- 新建仓库的名称前缀，默认 `cursor-skill-`
- `[github].default_private` -- 默认仓库可见性
- `[platforms.cursor]` / `[platforms.claude-code]` -- 各平台的 skills 目录、mcp.json 路径

## 意图 -> 工具映射

| 用户意图 | MCP 工具 |
|---------|---------|
| "发布 / 上传这个 skill 到 GitHub" | `publish_local_skill` |
| "把仓库改成公开/私有" | `set_skill_visibility` |
| "登记 / 收藏这个 skill 仓库" | `add_external_skill` (kind="skill") |
| "添加一个 MCP 服务器" | `add_mcp_server` 或 `add_external_skill` (kind="mcp") |
| "把所有资源装到本机 / 新电脑迁移" | `sync_skills` |
| "看哪些有更新" | `skill_status` |
| "更新某个资源" | `update_skill` |
| "删除 / 取消注册某个资源" | `remove_skill` |
| "列出已注册的资源" | `list_items` 或 `list_skills` |
| "查看平台配置" | `list_platforms` |

不确定时，先问一个澄清问题再操作。

## 前置检查（每台新机器执行一次）

操作 GitHub 前先确认：

1. `lpm` 已安装（`pip install -e <LPM 仓库>`）
2. `git` 在 PATH 中
3. GitHub PAT 已配置（二选一）：
   - 环境变量 `LPM_GITHUB_TOKEN`（推荐）
   - config.toml 中的 `[github].token`

token 缺失时不要静默失败 -- 指引用户设环境变量或编辑 config.toml。

## 资源类型

- **skill** -- 包含 `SKILL.md` 的 Agent 技能目录，安装到各平台 skills 目录
- **mcp** -- MCP 服务器配置（command/args/env），注入到各平台 mcp.json
- **rule** -- 编码规则和约定文件，安装到各平台 rules 目录

## 典型工作流

### 发布本地 skill

1. 确认目录包含有效的 `SKILL.md`（frontmatter 含 `name` 和 `description`）
2. 确认可见性：问用户 "公开还是私有？"
3. 调用 `publish_local_skill(path=<绝对路径>, private=<bool>)`
4. 提醒用户 commit 并 push `registry.yaml`

### 登记 MCP 服务器

```
add_mcp_server(name="github", github_url="https://github.com/...",
               command="npx", args=["-y", "@modelcontextprotocol/server-github"],
               env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"})
```

### 新电脑迁移

1. 确认 LPM 仓库已 clone 并 install
2. 确认 token 已配置（config.toml 或环境变量）
3. 调用 `sync_skills()`，自动安装到所有启用平台

### 修改可见性

`set_skill_visibility(name=..., private=<bool>)`，仅限 `owned` 资源。

## 硬性规则

- **token 永远不入库。** registry.yaml 和日志只存纯 HTTPS URL。
- **资源名称**：小写字母/数字/连字符，最长 64 字符。
- **发布后不要重新 git init。** 后续修改走正常 `git commit && git push`。
- **优先用 MCP 工具。** 只在 MCP 不可用时回退到 CLI。

## CLI 命令速查

```
lpm init [--claude-code] [-f]
lpm doctor
lpm publish <path> [--name --description --private/--public --kind --mcp-config -y]
lpm set-visibility <name> {public|private}
lpm add <github-url> [--subdir --ref --name --kind --mcp-config]
lpm sync [--only NAME --kind TYPE --platform NAME]
lpm status [--kind TYPE]
lpm list [--kind TYPE]
lpm update <name>
lpm remove <name> [--uninstall]
lpm install-self [--force]
lpm platforms
```
