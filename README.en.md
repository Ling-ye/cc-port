# CC Port

> Safely sync Skills, MCP servers, Rules, Prompts, Plugins, user instructions, and Claude auto memory across Codex, Claude Code, Cursor, Windsurf, and OpenCode through a portable Git resource repository you control.

[中文](README.md) · [Download for Windows](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.4) · [Quick start](docs/getting-started.en.md) · [Report an issue](https://github.com/Ling-ye/cc-port/issues)

[![CI](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml/badge.svg)](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?logo=windows)
![Public Beta](https://img.shields.io/badge/status-public_beta-orange)

CC Port is a local resource manager for people who use more than one AI coding tool. Humans keep the desktop client, while AI agents can discover and call the same capabilities through the bundled Skill, strict JSON CLI, and stdio MCP server. It scans each tool's native directories, uses your Git repository as the cross-device source of truth, and produces an explicit plan before writing anything. The repository and `registry.yaml` use an open format; other consumers do not need CC Port.

## At a glance

| Resource inventory | Write plan | Settings and diagnostics |
| --- | --- | --- |
| ![Resource inventory and platform state](docs/assets/screenshots/resources-overview.png) | ![Write plan before confirmation](docs/assets/screenshots/operation-plan.png) | ![Settings and environment diagnostics](docs/assets/screenshots/settings-diagnostics.png) |

## The problem it solves

- **Scattered configuration:** Skills, MCP servers, Rules, Prompts, Plugins, user instructions, and Claude auto memory live in different tool directories.
- **Cross-device drift:** the same resource gradually becomes a different version on each computer or tool.
- **Unsafe copying:** manual directory copies can overwrite local work and are difficult to undo.
- **Credential exposure:** MCP configuration can contain literal tokens or environment values that should not be committed.

CC Port compares a remote repository snapshot with every local platform instance. Upload, install, copy, and install-alias operations begin with a plan. Writes use backups, target locks, result verification, and rollback on failure.

### Upload to the repository

When you select assets and open **Upload to repository**, CC Port refreshes the remote snapshot and rescans local instances before building the batch plan. While that check is running, the dialog exposes only progress and cancellation. Resource editing cards, conflict choices, and execution controls appear only after the check finishes and reflect that check's local, remote, and aggregate states.

A local asset that has no remote counterpart is an addition, not a conflict. It does not show conflict resolution or the download-only option to replace a local target with the remote asset. Overwrite and rename choices appear only when both sides exist and their content or metadata differs.

Root-level native Windows symlinks and directory junctions may be local sources. CC Port preserves the logical installation path, dereferences the content, and uploads a regular-file snapshot rather than a link. Links to known `.agents/skills` roots are trusted automatically; other targets require a fresh-plan confirmation. Nested, dangling, circular, unreadable, or unsupported links block only the affected resource.

WSL LX symlinks are not native Windows reparse points, and the Windows desktop service does not start a WSL bridge to read them. Recreate the link as a native Windows link or reinstall the resource in copy mode, such as `npx skills add ... --copy`.

### Independent Windows and WSL runtime environments

A native Windows installation and every WSL distribution are independent profiles; this applies to both Codex and Claude Code. In `[platforms.<profile-id>]`, `<profile-id>` is the stable, unique `name` used by discovery, selection, upload, and download plans. `tool_id` identifies only the tool's native resource semantics. `environment_kind`, `environment_name`, `display_name`, and `home_dir` describe the runtime explicitly. CC Port never parses tool or environment identity from the `name` text and never merges write targets merely because two profiles share a `tool_id`.

A profile id must match `[a-z0-9][a-z0-9._-]{0,127}`, be unique across the configuration, and contain no path separator, control character, or machine-private path. Quote a dotted id as a TOML table key, for example `[platforms."claude.wsl"]`. CC Port rejects invalid or duplicate ids instead of renaming or merging them.

```toml
[platforms.claude-windows]
tool_id = "claude-code"
environment_kind = "windows"
display_name = "Claude Code"
home_dir = "C:/Users/example"

[platforms.claude-wsl-ubuntu]
tool_id = "claude-code"
environment_kind = "wsl"
environment_name = "Ubuntu-24.04"
display_name = "Claude Code"
home_dir = '\\wsl.localhost\Ubuntu-24.04\home\example'
```

Each enabled profile independently scans its `skills_dir`, `mcp_json`, `rules_dir`, `prompts_dir`, `plugins_dir`, `instructions_path`, `memories_dir`, and `settings_path`. `settings_path` points to the tool's native user-level configuration file, such as Claude Code's `settings.json` or Codex's `config.toml`; each profile currently parses exactly that one explicit user-level/native configuration input. CC Port does not automatically merge Claude managed policy, project/local settings that become active after workspace trust, or temporary `--settings` sources, and it does not claim to have derived Claude's complete effective runtime configuration. If one of those higher- or project-scoped sources overrides `autoMemoryDirectory`, configure a separate explicit direct profile/path. A WSL profile can use `home_dir` to expand `~` under that distribution's UNC user directory; Windows and WSL Codex profiles also need distinct `name` values. Regular UNC content can enter a plan, while Linux symlinks inside those directories remain individually blocked as WSL LX links. If a WSL distribution is stopped or its UNC path cannot be reached, the profile is unavailable and writes are blocked; unreachability is never treated as a missing resource or deletion signal. See the [configuration example](config/config.example.toml) for complete profiles.

The resource repository must be completely separate from the CC Port configuration file, local state and backup directory, legacy install target, and every profile resource target and `settings_path`. Equality or either path containing the other counts as overlap. Configuration writes and asset discovery, planning, and apply fail closed until the conflicting path is moved; machine state and native tool configuration must never enter the Git repository.

Claude Code's user instruction file, `~/.claude/CLAUDE.md`, migrates as an `instruction`, but personal `instruction` and `memory` resources are discovered and migrated only by the configured profile's environment-aware asset inventory. Generic global or directory discovery never exposes a global user instruction or auto memory as an upload candidate; project instructions remain observation-only. The configured user `rules_dir` (normally `~/.claude/rules/`) is scanned recursively for Markdown by profile-aware global-user discovery, but only root-level Markdown files there can currently be migrated directly. A nested user rule is shown as `claude-rule-<relative-path-hash>`, which does not expose the relative path, then remains blocked until the user reorganizes it into an explicit portable rule directory or layout. The hash distinguishes entries; it is not a reversible path encoding. Project `.claude/rules/**/*.md` files have a different scope from user rules. Project rules found by a directory-scope scan stay read-only and blocked because the current model has no project target identity; they cannot be promoted or downloaded into the global user `rules_dir`. Project-level `CLAUDE.md`, `.claude/CLAUDE.md`, and `CLAUDE.local.md` files are likewise not treated as global user instructions. Default auto-memory discovery is limited to `~/.claude/projects/<project-key>/memory/`. If a trusted `settings.json` declares `autoMemoryDirectory`, that value is the final memory directory and must not have another `<project-key>/memory` suffix appended. A memory ownership marker is stored beside the directory, never inside the memory content tree.

A Memory directory is an exact snapshot. Topic directories named `build/`, `cache/`, or `tmp/` are uploaded and restored unchanged when they contain only regular UTF-8 Markdown; generic exclusions used for Skills and other resource kinds do not apply. Every Markdown file in the tree is scanned for secret-like content before upload and again at apply time. A match blocks the complete operation without echoing the secret value.

A Claude project slot may encode a local absolute path or username. A projects-layout Memory therefore receives the default discovery candidate name `claude-memory-<slot-hash>`, which contains no plaintext slot. The exact slot remains only in the local discovery result's `install_name_hint` and the profile's `memory_install_names`; during upload, the user can rename the candidate to a meaningful remote logical name such as `cc-port-memory`.

Windows and WSL slots may differ even when they refer to the same Git repository. CC Port does not infer or aggregate them from paths, identical content, or their hashed candidate names. To bind both sides to one remote Memory, choose the same remote logical name and map that name to the exact existing child of `projects/` separately in each target profile's local `memory_install_names`. If the target does not already exist and the mapping is absent, the plan is blocked instead of guessing a path. Direct memory layout needs no mapping. Plaintext candidate slots and `memory_install_names` must not enter the Registry or `cc-port.yaml`.

The `cc-port publish` command and the MCP `publish_local_skill` tool are dedicated-repository publishing entry points and reject `instruction` and `memory`; legacy `sync`, `check`, and installation planning also skip both kinds. The supported personal-resource entry points are the profile-aware asset workflow: desktop asset batches, `cc-port asset ...` in the CLI, or the MCP tools `asset_inventory`, `asset_action_plan`/`asset_action_apply`, and `asset_batch_plan`/`asset_batch_apply`. Call `asset_inventory(scan_local=true)` to discover local instances through MCP. MCP `platform` and `target_platforms` values are exact profile ids. Plan/apply revalidates the operation id or `plan_hash`, so callers cannot bypass rescanning or stale-plan checks.

CC Port does not migrate all of `~/.claude.json`, Claude `settings.json`, or Codex `config.toml`; `~/.claude.json` is used only for a redacted MCP projection, while settings/config files are used only for native path and capability discovery. Claude/Codex credentials, sessions, chat history, file history, plans, todos, logs, telemetry, plugin caches, and runtime caches outside the exact Memory directory never enter the resource repository. Multi-profile instances are shown as identical copies or variants only after the user has bound them to the same `kind:name`; different Claude project slots are never merged merely because their contents match. See the [Claude Code instructions, memory, and runtime-environment specification](docs/specs/claude-memory-and-runtime-environments.md) for the official semantics and full safety contract.

### Portable Registry v1 and repository checks

Repository content or an external `source` is authoritative. `registry.yaml` is only a portable membership manifest: it stores the stable `(kind, name)` identity and exactly one repository-relative `path` or external `source`.

```yaml
version: 1
resources:
  - kind: skill
    name: code-review
    path: skills/code-review
  - kind: plugin
    name: browser-tools
    source:
      type: marketplace
      locator: openai-bundled/browser-tools
      revision: latest
```

Descriptions, versions, authors, licenses, tags, hashes, and check timestamps are derived rather than persisted in the Registry. MCP configuration lives in `mcp/<name>/mcp.json|yaml|yml`. Profile ids, `tool_id`, Windows/WSL identities, user directories, and local target paths are also forbidden from the Registry. Optional CC Port-only platform/tool allowlists, install aliases, and plugin intent live in `cc-port.yaml`, which other tools may ignore. `instruction` and `memory` add known kinds and the conventional `instructions/` and `memories/` roots without changing the Registry v1 schema. See the [Registry v1 specification](docs/specs/registry-v1.md) for the complete contract.

Although `cc-port.yaml` is optional, once present it must be a regular non-symlink file and pass complete YAML and portable-overlay semantic validation. A malformed file, a semantically invalid binding, or a machine-local Memory slot/install alias makes the remote manifest fail closed. CC Port does not continue upload or download by treating it as an empty overlay, and Registry repair never silently rewrites it.

Every remote refresh audits the Registry at the same commit without modifying the repository. **Check repository** previews additions, removals, manual blockers, and the final YAML diff. Only explicit confirmation creates and normally pushes one commit containing `registry.yaml` and no resource content or `cc-port.yaml` changes.

A missing, malformed, or linked Registry is diagnostic-only and has no Apply button. The repository remains connected and local discovery remains available, while actions that depend on the remote manifest are blocked. CLI equivalents are:

```text
cc-port resource registry-check --json
cc-port resource registry-repair --dry-run
```

The CLI `registry-repair` command only builds and displays a plan; `--yes` has no
authorization or write semantics. Apply an actual repair after desktop review,
or use the approval-gated MCP `registry_repair_plan` / `registry_repair_apply`
workflow.

## Get started in five steps

1. Install [Git for Windows](https://git-scm.com/download/win) and make sure Git Credential Manager is available.
2. Download `cc-port_0.5.4_windows_x64_setup.exe` from the [v0.5.4 Public Beta release](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.4).
3. Create an empty private GitHub repository for your AI coding resources.
4. Start CC Port, paste the repository HTTPS URL into Settings, and verify the connection.
5. Scan local resources, then choose which items to upload or install from the Resources page.

New Windows builds from this repository include the desktop application, Desktop API sidecar, and standalone `cc-port.exe` CLI/MCP agent. End users do not need Python, Node.js, or Rust. The already-published v0.5.4 installer predates this capability and does not contain the agent; wait for a newer release that includes it. See the [quick-start guide](docs/getting-started.en.md) for setup, AI integration, and uninstall details.

## Safety boundaries

- **You own the repository:** CC Port has no hosted cloud service; resources stay in the Git repository you choose.
- **Credentials stay with the OS:** the desktop app uses Git Credential Manager and does not read or store a GitHub token.
- **Plan before write:** desktop and CLI write operations expose targets, actions, and blockers first.
- **Machine interfaces do not expose self-approval:** recommended MCP and non-interactive CLI writes require a single-use local approval bound to the operation, plan hash, and complete scope. A user approves it in the desktop app, and stale plans require a new review.
- **Unmanaged content is protected:** ownership metadata distinguishes CC Port-managed items from manually maintained files.
- **Dangling links replace only the link itself:** when a download target is a root-level dangling native Windows symlink, CC Port removes that link and writes regular content only after explicit unmanaged-target confirmation; it never follows or modifies the link target.
- **Recoverable writes:** installation, removal, deployment, and recovery use persistent transactions, backups, and rollback.
- **MCP secret placeholders:** literal MCP environment values are replaced with `${SECRET_NAME}` placeholders during collection.
- **Local roots stay outside the repository:** configuration, state/backups, and every profile-native target must not be equal to, contain, or sit under the Git resource repository.
- **Missing is not deletion:** an absent remote item never triggers implicit deletion.

Do not open a public issue for a vulnerability. Follow the [security policy](SECURITY.md) and use GitHub private vulnerability reporting.

## Support matrix

### Resource types

| Type | Discover and register | Resource repository sync | Install to tools |
| --- | :---: | :---: | :---: |
| Skill | ✓ | ✓ | ✓ |
| MCP Server | ✓ | ✓ | ✓ |
| Rule | ✓ | ✓ | ✓ |
| Prompt | ✓ | ✓ | ✓ |
| Plugin | ✓ | ✓ | ✓ |
| Instruction | ✓ | ✓ | ✓ |
| Memory | ✓ | ✓ | ✓ |

For Instruction and Memory, “Discover and register” means the configured profile's environment-aware asset inventory only. Generic global or directory discovery is not a personal-resource upload entry point.

### Instruction and Memory compatibility

| Type | Codex | Claude Code | Cross-tool rule |
| --- | --- | --- | --- |
| Instruction | User-level `AGENTS.override.md` or `AGENTS.md` | User-level `~/.claude/CLAUDE.md` | Write only with the source tool's native semantics; never auto-convert between formats |
| Memory | Does not support the Claude auto-memory contract | Default project memory or the final directory named by `autoMemoryDirectory` | Install only to Claude Code profiles; never rename it into a Codex instruction |

### AI coding tools

| Tool | Status | Default writable resources |
| --- | --- | --- |
| Codex | Stable | Skill, Instruction |
| Claude Code | Stable | Skill, MCP, Rule, Plugin, Instruction, Memory |
| Cursor | Stable | Skill, MCP, Prompt |
| Windsurf | Experimental | Skill, MCP |
| OpenCode | Experimental | Skill, MCP, Rule, Prompt, Plugin |
| Cline, Gemini CLI | Discovery only | No complete writable preset yet |

### Cursor Prompt commands

The Cursor preset installs Prompt `<name>` as the global custom command
`~/.cursor/commands/<name>.md`; a platform install alias replaces `<name>` in
that filename. The resource repository continues to store portable
content under `prompts/<name>/`. For downloads to this file-style target, the
remote Prompt must be a Markdown file or a directory containing exactly one
non-symlink `.md` file at its root. Zero or multiple root-level Markdown files
block the plan instead of being selected arbitrarily.

Existing custom platforms without `prompts_dir` retain the legacy
`rules_dir/<install-name>` behavior, so existing configurations are not
silently migrated. See the
[Cursor Prompt command specification](docs/specs/cursor-prompt-commands.md)
(Chinese) for the complete contract.

Advanced users can add custom platform paths in `config.toml`. See the [configuration example](config/config.example.toml).

## Three interfaces

- **Desktop GUI:** daily discovery, comparison, upload, installation, AI integration, approval, and environment diagnostics. The human client remains supported.
- **CLI:** human scripting, strict `--non-interactive --json` machine calls, batch operations, history recovery, and state maintenance.
- **MCP server:** `cc-port mcp --stdio` exposes typed plan/apply capabilities to compatible AI coding tools.

All three interfaces share the same Python core. See the [architecture (Chinese)](docs/architecture.md) for boundaries and sync state machines.

### Let an AI use CC Port

In a new build that includes this capability, open **Settings → AI automation** and review an enable plan for an exact profile. After approval, CC Port installs only its packaged `cc-port` Skill into that profile's Skill directory and adds a local `cc-port.exe mcp --stdio` entry to the tool's native configuration. It does not remove the desktop client or rewrite unrelated MCP servers. Schema v1 automatically bootstraps native Windows profiles only. A WSL profile is explicitly blocked at this Skill-plus-MCP registration step instead of treating a Windows process as a verified WSL connection; the existing profile-aware WSL asset inventory and plan/apply workflows remain available.

The AI prefers MCP discovery and follows `status → inventory(scan_local=true) → diff → plan → approval → apply → verify`; it uses the single-envelope non-interactive CLI only when MCP is unavailable. Reads and plans can run automatically. A write plan appears under **Pending AI approvals** in the desktop app and cannot apply until the user grants a one-time approval. Approvals expire and can be consumed only once. Any target drift produces a fresh stale plan and invalidates the old authorization. See the [AI agent discovery, approval, and invocation specification (Chinese)](docs/specs/ai-agent-interface.md) for commands, schemas, and security boundaries.

This is an application-level approval boundary, not a separate Windows security
principal. The AI host must prevent the agent from directly modifying CC Port's
local state or impersonating the desktop-sidecar channel. Version 1 does not
claim an operating-system-level proof of human presence against code that has
the same unrestricted filesystem and process privileges as the human user.

## Current limitations

- The Public Beta officially supports Windows 10/11 x64 only.
- The v0.5.4 installer is unsigned, so Windows SmartScreen may show an unknown-publisher warning.
- Git for Windows and Git Credential Manager are required on the target computer.
- The desktop app does not create, delete, or change the visibility of GitHub repositories.
- Automatic updates are not available yet; download upgrades from Releases.
- The desktop app focuses on resource management. Some recovery and maintenance features remain CLI/Desktop API only.

For installation, sign-in, or sync failures, see [troubleshooting](docs/troubleshooting.en.md).

## Documentation

- [Quick start](docs/getting-started.en.md)
- [Troubleshooting](docs/troubleshooting.en.md)
- [v0.5.4 release notes](docs/releases/v0.5.4.en.md)
- [Development guide (Chinese)](docs/development.md)
- [Architecture (Chinese)](docs/architecture.md)
- [Registry v1 specification (Chinese)](docs/specs/registry-v1.md)
- [Claude Code instructions, memory, and runtime-environment specification (Chinese)](docs/specs/claude-memory-and-runtime-environments.md)
- [AI agent discovery, approval, and invocation specification (Chinese)](docs/specs/ai-agent-interface.md)
- [Desktop packaging and release (Chinese)](docs/packaging-and-deployment.md)
- [Behavior specifications (Chinese)](docs/specs/)
- [Changelog](CHANGELOG.md)

## Contributing

Bug fixes, documentation corrections, and small changes can go directly to a pull request. Open an issue before implementing a larger feature or behavior change so that the problem and scope can be agreed first. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Lingye · [Third-party notices](THIRD_PARTY_NOTICES.md)
