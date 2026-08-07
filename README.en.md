# CC Port

> Safely sync Skills, MCP servers, Rules, Prompts, and Plugins across Codex, Claude Code, Cursor, Windsurf, and OpenCode through a portable Git resource repository you control.

[中文](README.md) · [Download for Windows](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.4) · [Quick start](docs/getting-started.en.md) · [Report an issue](https://github.com/Ling-ye/cc-port/issues)

[![CI](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml/badge.svg)](https://github.com/Ling-ye/cc-port/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Windows 10/11 x64](https://img.shields.io/badge/Windows-10%2F11_x64-0078D4?logo=windows)
![Public Beta](https://img.shields.io/badge/status-public_beta-orange)

CC Port is a local desktop resource manager for people who use more than one AI coding tool. It scans each tool's native directories, uses your Git repository as the cross-device source of truth, and produces an explicit plan before writing anything. The repository and `registry.yaml` use an open format; other consumers do not need CC Port.

## At a glance

| Resource inventory | Write plan | Settings and diagnostics |
| --- | --- | --- |
| ![Resource inventory and platform state](docs/assets/screenshots/resources-overview.png) | ![Write plan before confirmation](docs/assets/screenshots/operation-plan.png) | ![Settings and environment diagnostics](docs/assets/screenshots/settings-diagnostics.png) |

## The problem it solves

- **Scattered configuration:** Skills, MCP servers, Rules, Prompts, and Plugins live in different tool directories.
- **Cross-device drift:** the same resource gradually becomes a different version on each computer or tool.
- **Unsafe copying:** manual directory copies can overwrite local work and are difficult to undo.
- **Credential exposure:** MCP configuration can contain literal tokens or environment values that should not be committed.

CC Port compares a remote repository snapshot with every local platform instance. Upload, install, copy, and install-alias operations begin with a plan. Writes use backups, target locks, result verification, and rollback on failure.

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

Descriptions, versions, authors, licenses, tags, hashes, and check timestamps are derived rather than persisted in the Registry. MCP configuration lives in `mcp/<name>/mcp.json|yaml|yml`. Optional CC Port-only platform aliases and plugin installation intent live in `cc-port.yaml`, which other tools may ignore. See the [Registry v1 specification](docs/specs/registry-v1.md) for the complete contract.

Every remote refresh audits the Registry at the same commit without modifying the repository. **Check repository** previews additions, removals, manual blockers, and the final YAML diff. Only explicit confirmation creates and normally pushes one commit containing `registry.yaml` and no resource content or `cc-port.yaml` changes.

A missing, malformed, or linked Registry is diagnostic-only and has no Apply button. The repository remains connected and local discovery remains available, while actions that depend on the remote manifest are blocked. CLI equivalents are:

```text
cc-port resource registry-check --json
cc-port resource registry-repair --dry-run
cc-port resource registry-repair --yes --choices choices.yaml
```

## Get started in five steps

1. Install [Git for Windows](https://git-scm.com/download/win) and make sure Git Credential Manager is available.
2. Download `cc-port_0.5.4_windows_x64_setup.exe` from the [v0.5.4 Public Beta release](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.4).
3. Create an empty private GitHub repository for your AI coding resources.
4. Start CC Port, paste the repository HTTPS URL into Settings, and verify the connection.
5. Scan local resources, then choose which items to upload or install from the Resources page.

The installer includes both the desktop application and its Python sidecar. End users do not need Python, Node.js, or Rust. See the [quick-start guide](docs/getting-started.en.md) for setup and uninstall details.

## Safety boundaries

- **You own the repository:** CC Port has no hosted cloud service; resources stay in the Git repository you choose.
- **Credentials stay with the OS:** the desktop app uses Git Credential Manager and does not read or store a GitHub token.
- **Plan before write:** desktop and CLI write operations expose targets, actions, and blockers first.
- **Unmanaged content is protected:** ownership metadata distinguishes CC Port-managed items from manually maintained files.
- **Dangling links replace only the link itself:** when a download target is a root-level dangling native Windows symlink, CC Port removes that link and writes regular content only after explicit unmanaged-target confirmation; it never follows or modifies the link target.
- **Recoverable writes:** installation, removal, deployment, and recovery use persistent transactions, backups, and rollback.
- **MCP secret placeholders:** literal MCP environment values are replaced with `${SECRET_NAME}` placeholders during collection.
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

### AI coding tools

| Tool | Status | Default writable resources |
| --- | --- | --- |
| Codex | Stable | Skill |
| Claude Code | Stable | Skill, MCP, Plugin |
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

- **Desktop GUI:** daily discovery, comparison, upload, installation, and environment diagnostics; the Guide page links to the project repository and its GitHub Star action.
- **CLI:** scripting, batch operations, history recovery, and state maintenance.
- **MCP server:** exposes CC Port capabilities to compatible AI coding tools.

All three interfaces share the same Python core. See the [architecture (Chinese)](docs/architecture.md) for boundaries and sync state machines.

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
- [Desktop packaging and release (Chinese)](docs/packaging-and-deployment.md)
- [Behavior specifications (Chinese)](docs/specs/)
- [Changelog](CHANGELOG.md)

## Contributing

Bug fixes, documentation corrections, and small changes can go directly to a pull request. Open an issue before implementing a larger feature or behavior change so that the problem and scope can be agreed first. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Lingye · [Third-party notices](THIRD_PARTY_NOTICES.md)
