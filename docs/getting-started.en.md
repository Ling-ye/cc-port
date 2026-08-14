# Getting Started

This guide is for CC Port's Windows desktop app. After the first-time setup,
you can scan resources from several AI coding tools and synchronize individual
items between their native directories and a private Git repository you control.

## 1. Prepare the computer

The target computer needs:

- Windows 10 or Windows 11 x64.
- [Git for Windows](https://git-scm.com/download/win).
- Git Credential Manager, which is normally included with Git for Windows.
- A private GitHub repository that you control.

After installing Git, check it in PowerShell:

```powershell
git --version
git credential-manager --version
git config --global --get-all credential.helper
```

If the last command does not show Git Credential Manager, finish configuring
credential management for Git for Windows before starting CC Port.

## 2. Install CC Port

1. Open the [CC Port v0.6.0 Public Beta](https://github.com/Ling-ye/cc-port/releases/tag/v0.6.0).
2. Download `cc-port_0.6.0_windows_x64_setup.exe`.
3. Verify its hash against `SHA256SUMS.txt` from the same Release.
4. Run the installer and follow its prompts.

The v0.6.0 installer is not code-signed. Windows SmartScreen may show an
unknown-publisher warning. After confirming that the file came from
`github.com/Ling-ye/cc-port` and that its SHA-256 matches, select **More info**
to continue.

The v0.6.0 installer contains the desktop application, Desktop API sidecar, and
standalone `cc-port.exe` CLI/MCP agent. You do not need to install Python,
Node.js, or Rust; section 5 explains how to enable AI invocation.

## 3. Create a resource repository

Create a private repository on GitHub, for example `ai-coding-resources`:

- Set its visibility to **Private**.
- Do not put a token in the repository or its URL.
- Use `main` as the default branch.
- Store only synchronized resources, the tool-neutral `registry.yaml`, and the
  optional `cc-port.yaml` consumer settings there. Do not put CC Port
  configuration, state/backups, the legacy install target, or any AI-tool
  profile target in the repository, and do not make either path an ancestor of
  the other.

CC Port does not create or delete repositories, or change their visibility.

An initialized repository contains a version 1 Registry and the seven
conventional `skills/`, `mcp/`, `rules/`, `prompts/`, `plugins/`,
`instructions/`, and `memories/` directories. This is not a CC Port-private
format; other tools can maintain it using the
[Registry v1 specification](specs/registry-v1.md).

## 4. Connect the repository

1. Start CC Port.
2. Open **Settings**.
3. Review the Git, Git Credential Manager, and `credential.helper` diagnostics.
4. Paste the repository root URL, for example:

   ```text
   https://github.com/<owner>/<repo>
   ```

5. Select **Connect and verify repository**.
6. On first use, Git Credential Manager may open a browser for GitHub sign-in.

Verification reads remote references and probes write permission without
uploading resources. Background refresh remains non-interactive. If credentials
expire, return to Settings and verify the connection again.

## 5. Enable AI invocation

This step is optional and does not remove or replace the desktop client:

1. Open **Settings → AI automation**.
2. Select **Review enable plan** for the exact native Windows profile that
   should expose CC Port to an AI. Schema v1 explicitly blocks automatic
   bootstrap for WSL profiles; this does not remove the existing WSL asset
   inventory and plan/apply workflows.
3. Check the Skill target, MCP configuration target, launch command, and
   planned actions. Same-name unmanaged content is blocked by default; create
   a takeover plan only after confirming that CC Port should own that target.
4. Select **Approve and enable**. CC Port installs its packaged `cc-port`
   Skill, registers local `cc-port.exe mcp --stdio`, starts the MCP process,
   and verifies its tool manifest.

After enablement, an MCP-capable AI coding tool can discover CC Port. Reads and
plans can run automatically; every write plan appears under **Pending AI
approvals** in Settings. Approval is bound to the displayed operation,
`plan_hash`, and complete scope, expires automatically, and can be consumed
only once. Target drift requires review of a new plan and cannot reuse the old
approval.

When the host has no MCP support, use the
`cc-port --non-interactive ... --json` machine interface. It returns one
versioned JSON envelope and enforces the same approval boundary. The complete
workflow is documented in the
[AI agent discovery, approval, and invocation specification (Chinese)](specs/ai-agent-interface.md).

## 6. Scan and synchronize

1. Open **Resources**.
2. Choose the global or project scopes to scan.
3. Start a scan and review the Skills, MCP servers, Rules, Prompts, Plugins,
   Instructions, and Memories found for each configured profile. Personal
   Instructions and Memories come only from the environment-aware asset
   inventory; generic global or directory discovery never turns a global user
   instruction or auto memory into an upload candidate.
4. Review the Registry health on the remote card. Use **Check repository** to
   inspect issues and the proposed YAML diff.
5. Choose an action for an individual resource:
   - **Upload to repository** writes a local instance to the private repository.
   - **Install to tool** writes a remote resource to the selected tool directory.
   - **Save as copy** preserves the current instance under a new name.
   - **Set install alias** uses a different directory name on each platform.
6. Review the operation plan, warnings, and blockers before confirming.

Remote refresh audits but never repairs automatically. A missing, malformed, or
linked Registry must be corrected manually; local discovery remains available,
while remote upload and installation actions are blocked. Once present, the
optional `cc-port.yaml` must also be a regular non-symlink file and pass full
validation. A malformed file, invalid binding, or machine-local Memory
slot/install alias fails closed instead of being treated as an empty overlay;
Registry repair never rewrites it.

CC Port does not interpret a missing remote resource as a deletion request and
does not silently overwrite same-name local content that lacks CC Port ownership
metadata.

Each `[platforms.<profile-id>]` id must match
`[a-z0-9][a-z0-9._-]{0,127}` and be unique across the configuration. Quote an
id containing `.` as `[platforms."claude.wsl"]`; invalid or duplicate ids make
configuration loading fail. The resource repository must not equal, contain,
or sit under the configuration file, local state/backups, legacy install
target, or any profile's `skills_dir`, `mcp_json`, `rules_dir`, `prompts_dir`,
`plugins_dir`, `instructions_path`, `memories_dir`, or `settings_path`.
Configuration writes and asset planning are blocked until the overlap is fixed.

Each Windows or WSL profile uses its own `settings_path` for the tool's native
user-level configuration file: normally `settings.json` for Claude Code and
`config.toml` for Codex. Each profile currently parses only that one explicit
user-level/native configuration input. CC Port does not merge Claude managed
policy, project/local settings that become active after workspace trust, or
temporary `--settings` sources, and does not claim to have derived the complete
effective runtime configuration. If one of those sources overrides
`autoMemoryDirectory`, configure a separate explicit direct profile/path. The
configured file is used only for native-path and capability discovery and is
never uploaded or migrated wholesale.

A Memory is an exact directory snapshot. Topic directories named `build/`,
`cache/`, or `tmp/` are uploaded and restored unchanged when they satisfy the
regular UTF-8 Markdown contract; generic Skill exclusions do not apply. The
upload plan and apply scan every Markdown file, block secret-like content, and
never echo the matched value.

The `cc-port publish` command and MCP `publish_local_skill` tool are
dedicated-repository publishing entry points and reject `instruction` and
`memory`; legacy `sync`, `check`, and installation planning skip both kinds.
Personal resources use only the profile-aware asset workflow: desktop asset
batches, `cc-port asset ...`, or the MCP tools `asset_inventory`,
`asset_action_plan`/`asset_action_apply`, and
`asset_batch_plan`/`asset_batch_apply`. Set `scan_local=true` on
`asset_inventory` to discover local instances through MCP. Platform arguments
use exact profile ids; apply must carry the original operation id or
`plan_hash` and pass rescanning and stale-plan validation.

Claude project `.claude/rules/**/*.md` files have a different scope from the
configured user `rules_dir`. Only rules discovered from the user `rules_dir` by
a global user scan and already in a portable layout can migrate. Project rules
found by a directory-scope scan stay read-only and blocked because the current
model has no project target identity; they cannot be promoted or downloaded
into global user rules.

### Cursor Prompt commands

Cursor Prompts install to `~/.cursor/commands/<name>.md` by default and can be
invoked in Cursor as `/<name>`. The repository still stores each Prompt under
`prompts/<name>/`. A download from that directory to Cursor requires exactly
one non-symlink root-level `.md` file; otherwise the operation plan is blocked.
An install alias changes the local command filename. Custom platforms without
`prompts_dir` continue to use the legacy `rules_dir/<install-name>` target.

## Upgrade and uninstall

CC Port does not currently update itself. Download a newer installer from
[Releases](https://github.com/Ling-ye/cc-port/releases) and install it over the
existing version.

Uninstalling CC Port does not delete:

- Your GitHub resource repository.
- Resources in the native directories of your AI coding tools.
- Local CC Port state, backups, or operation history.

Before intentionally deleting local state, confirm that you no longer need its
recovery records and follow [Local state directory](troubleshooting.en.md#local-state-directory).

## Next steps

- [Troubleshooting](troubleshooting.en.md)
- [Security policy](../SECURITY.md)
- [Support scope and known limitations](../README.en.md#current-limitations)
- [CLI and development guide (Chinese)](development.md)
