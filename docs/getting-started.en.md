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

1. Open the [CC Port v0.5.2 Public Beta](https://github.com/Ling-ye/cc-port/releases/tag/v0.5.2).
2. Download `cc-port_0.5.2_windows_x64_setup.exe`.
3. Verify its hash against `SHA256SUMS.txt` from the same Release.
4. Run the installer and follow its prompts.

The v0.5.2 installer is not code-signed. Windows SmartScreen may show an
unknown-publisher warning. After confirming that the file came from
`github.com/Ling-ye/cc-port` and that its SHA-256 matches, select **More info**
to continue.

The installer contains the desktop application and its Python sidecar. You do
not need to install Python, Node.js, or Rust.

## 3. Create a resource repository

Create a private repository on GitHub, for example `ai-coding-resources`:

- Set its visibility to **Private**.
- Do not put a token in the repository or its URL.
- Use `main` as the default branch.
- Store only synchronized resources and `registry.yaml` there; do not put the
  application backup directory in the repository.

CC Port does not create or delete repositories, or change their visibility.

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

## 5. Scan and synchronize

1. Open **Resources**.
2. Choose the global or project scopes to scan.
3. Start a scan and review the discovered Skills, MCP servers, Rules, Prompts,
   and Plugins.
4. Choose an action for an individual resource:
   - **Upload to repository** writes a local instance to the private repository.
   - **Install to tool** writes a remote resource to the selected tool directory.
   - **Save as copy** preserves the current instance under a new name.
   - **Set install alias** uses a different directory name on each platform.
5. Review the operation plan, warnings, and blockers before confirming.

CC Port does not interpret a missing remote resource as a deletion request and
does not silently overwrite same-name local content that lacks CC Port ownership
metadata.

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
