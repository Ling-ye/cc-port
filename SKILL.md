---
name: skillhub
description: Publish, register, install and sync AI agent resources (skills, MCP servers, rules) across Cursor and Claude Code through the SkillHub MCP server. Use when the user wants to publish a local skill or MCP config to GitHub, register a third-party resource, install or update all resources on a new machine, check for updates, or migrate their agent environment. Triggers include phrases like "publish/upload/share this skill", "add/track/collect this skill repo", "install my skills on this machine", "sync skills", "migrate skills", "add MCP server", "register MCP", "skillhub", "发布 skill", "上传 skill", "登记 skill", "迁移技能", "同步技能", "添加 MCP 服务器".
---

# SkillHub

SkillHub manages a personal registry of AI coding agent resources — skills, MCP server configurations, and rules — across multiple platforms (Cursor and Claude Code). Use the SkillHub MCP tools (or the `skillhub` CLI as fallback) for any task involving publishing, registering, installing, syncing, or auditing these resources.

## When to use

Map user intent to tools:

| User intent | Tool |
|---|---|
| "publish / upload / share this local skill" / "把这个 skill 发布到 GitHub" | `publish_local_skill` |
| "make my repo public/private" / "把我的仓库改成公开/私有" | `set_skill_visibility` |
| "add / register / track this skill repo" / "登记 / 收藏这个 skill" | `add_external_skill` (kind="skill") |
| "add / register this MCP server" / "添加这个 MCP 服务器" | `add_mcp_server` or `add_external_skill` (kind="mcp") |
| "install all my resources" / "new machine setup" / "新电脑装一下我的技能" | `sync_skills` |
| "are my resources up to date" / "看哪些有更新" | `skill_status` |
| "update <name>" | `update_skill` |
| "remove / unregister <name>" | `remove_skill` |
| "list / show my resources" / "看下注册了哪些" | `list_items` or `list_skills` |
| "show platforms" / "看下平台配置" | `list_platforms` |

If unsure which one applies, ask one clarifying question before acting.

## Pre-flight (run once per new environment)

Before any GitHub-touching operation, verify:

1. The `skillhub` package is installed (`pip install -e <SkillHub repo>`).
2. `git` is on PATH.
3. A GitHub PAT with `repo` scope is available via either:
   - `SKILLHUB_GITHUB_TOKEN` environment variable (preferred), OR
   - `~/.config/skillhub/config.toml` written by `skillhub init`.

When the token is missing, do NOT silently fail — instruct the user to either export the env var or run `skillhub init`.

## Resource types

SkillHub manages three types of resources:

- **skill** — Agent skill directories containing `SKILL.md` (installed to each platform's skills directory)
- **mcp** — MCP server configurations (injected into each platform's mcp.json)
- **rule** — Rule/convention files (installed to each platform's rules directory)

## Workflows

### 1. Publish a local skill

1. Confirm the directory contains a valid `SKILL.md` with `name` and `description` frontmatter.
2. **Confirm visibility (REQUIRED).** Ask: "Should this repo be public or private?"
3. Call `publish_local_skill(path=<absolute path>, private=<bool>)`.
4. Remind the user to **commit and push `registry.yaml`**.

### 2. Register an MCP server

Use `add_mcp_server` for the simplest flow:

```
add_mcp_server(name="github", github_url="https://github.com/...",
               command="npx", args=["-y", "@modelcontextprotocol/server-github"],
               env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"})
```

Or use `add_external_skill(kind="mcp", mcp_config={...})` for full control.

### 3. Migrate to a new machine

1. Confirm SkillHub repo is cloned and installed.
2. Confirm a token is configured.
3. Call `sync_skills()`. It installs to all enabled platforms (Cursor + Claude Code).

### 4. Change visibility

Use `set_skill_visibility(name=..., private=<bool>)`. Only works for `owned` items.

## Hard rules

- **Tokens never enter committed files.**
- **Item names**: lowercase letters/digits/hyphens, max 64 chars.
- **Don't re-init a published repo.**
- **Prefer MCP tools over shell.** Only fall back to CLI when MCP is unreachable.

## CLI fallback

```
skillhub doctor
skillhub publish <path> [--name --description --private/--public --kind --mcp-config -y]
skillhub set-visibility <name> {public|private}
skillhub add <github-url> [--subdir --ref --name --kind --mcp-config]
skillhub sync [--only NAME --kind TYPE --platform NAME]
skillhub status [--kind TYPE]
skillhub list [--kind TYPE]
skillhub update <name>
skillhub remove <name> [--uninstall]
skillhub install-self [--force]
skillhub platforms
```
