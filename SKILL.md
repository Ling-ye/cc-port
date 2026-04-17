---
name: skillhub
description: Publish, register, install and sync Cursor Agent Skills through the SkillHub MCP server. Use when the user wants to publish a local skill to GitHub, register a third-party skill repository, install or update all skills on a new machine, check for skill updates, or migrate their Cursor skill environment. Triggers include phrases like "publish/upload/share this skill", "add/track/collect this skill repo", "install my skills on this machine", "sync skills", "migrate skills", "skillhub", "发布 skill", "上传 skill", "登记 skill", "迁移技能", "同步技能".
---

# SkillHub

SkillHub manages a personal registry of Cursor Agent Skills. Use the SkillHub MCP tools (or the `skillhub` CLI as fallback) for any task involving publishing, registering, installing, syncing, or auditing skills.

## When to use

Map user intent to tools:

| User intent | Tool |
|---|---|
| "publish / upload / share this local skill" / "把这个 skill 发布到 GitHub" | `publish_local_skill` |
| "make my skill repo public/private" / "把我的 skill 改成公开/私有" | `set_skill_visibility` |
| "add / register / track this skill repo" / "登记 / 收藏这个 skill" | `add_external_skill` |
| "install all my skills" / "new machine setup" / "新电脑装一下我的 skill" | `sync_skills` |
| "are my skills up to date" / "看哪些 skill 有更新" | `skill_status` |
| "update <name>" | `update_skill` |
| "remove / unregister <name>" | `remove_skill` |
| "list / show my skills" / "看下注册了哪些 skill" | `list_skills` |

If unsure which one applies, ask one clarifying question before acting.

## Pre-flight (run once per new environment)

Before any GitHub-touching operation, verify:

1. The `skillhub` package is installed (`pip install -e <SkillHub repo>`).
2. `git` is on PATH.
3. A GitHub PAT with `repo` scope is available via either:
   - `SKILLHUB_GITHUB_TOKEN` environment variable (preferred, keeps repos open-source-safe), OR
   - `~/.config/skillhub/config.toml` written by `skillhub init`.

When the token is missing, do NOT silently fail — instruct the user to either export the env var or run `skillhub init`.

## Workflows

### 1. Publish a local skill

1. Confirm the user's directory contains a valid `SKILL.md` with `name` and `description` frontmatter.
2. **Confirm visibility (REQUIRED before calling).** Ask the user explicitly:
   "Should this skill repo be public or private?" Map the answer to the
   `private` argument (`True` = private, `False` = public). Never silently
   default — the user might publish secret content as public, or vice versa.
3. Call `publish_local_skill(path=<absolute path>, private=<bool>)`.
   - Creates `<owner>/cursor-skill-<name>` on GitHub with the chosen visibility,
     pushes the directory, and writes an `owned` entry to `registry.yaml`.
   - If the repo already exists with a different visibility, the tool returns
     `error: "visibility_mismatch"`. In that case, ask the user whether to
     change visibility; if yes, call again with `update_visibility=True`.
4. Remind the user to **commit and push `registry.yaml`** so the new entry travels to their other machines.

### 1b. Change visibility of a published skill

If the user later wants to flip a published skill between public and private:

1. Confirm which skill (`name`) and the desired state.
2. Call `set_skill_visibility(name=..., private=<bool>)`.
3. Only works for `owned` skills. For `external` skills, refuse and explain
   that the user does not own that GitHub repo.

### 2. Register a third-party skill

1. Get the GitHub URL. Ask for `subdir` if the SKILL.md is not at repo root.
2. Call `add_external_skill(github_url=..., subdir=..., ref="main")`.
3. Suggest `sync_skills` next to actually install it locally.

### 3. Migrate to a new machine (the headline flow)

1. Confirm SkillHub repo is cloned and installed (`pip install -e .`).
2. Confirm a token is configured.
3. Call `sync_skills()`. Show the result table to the user.
4. If any row reports `failed`, surface the `detail` field verbatim — usually a git auth or network issue.

### 4. Audit / update

- `skill_status` — read-only, compares local vs remote commits.
- `update_skill(name=...)` — force-sync a single one.
- `sync_skills(only=[...])` — sync a subset.

## Hard rules

- **Tokens never enter committed files.** `registry.yaml`, the config file, and any logs must contain plain HTTPS URLs only. The installer already strips tokens after each operation; do not undo that.
- **Skill names**: lowercase letters/digits/hyphens, max 64 chars. If the user proposes an invalid name, normalize it (lowercase, replace spaces with `-`) and confirm before publishing.
- **Don't re-init a published repo.** After `publish_local_skill`, the local directory already has `.git/` with `origin` set; later edits should be normal `git commit && git push`.
- **Prefer MCP tools over shell.** Only fall back to the `skillhub` CLI via shell when the MCP server is unreachable.

## CLI fallback

If MCP is unavailable, the same operations exist as CLI commands:

```
skillhub doctor
skillhub publish <path> [--name --description --private/--public --update-visibility -y]
skillhub set-visibility <name> {public|private}
skillhub add <github-url> [--subdir --ref --name]
skillhub sync [--only NAME]
skillhub status
skillhub list
skillhub update <name>
skillhub remove <name> [--uninstall]
skillhub install-self      # copies this SKILL.md to ~/.cursor/skills/skillhub/
```

When `skillhub publish` is invoked without `--private`/`--public` and without
`-y`, it interactively prompts the user. The default in the prompt comes from
`github.default_private` in the config.
