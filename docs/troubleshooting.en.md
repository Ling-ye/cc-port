# Troubleshooting

## Windows SmartScreen blocks the installer

The v0.5.4 installer is not code-signed. First confirm:

1. The file came from `https://github.com/Ling-ye/cc-port/releases`.
2. Its name is `cc-port_0.5.4_windows_x64_setup.exe`.
3. Its SHA-256 matches `SHA256SUMS.txt` from the Release.

After verifying those details, select **More info** in SmartScreen to continue.
Delete the file without running it if the hash does not match. A managed
enterprise computer may enforce a policy that prevents bypassing unsigned
applications; that environment is outside the current Public Beta support scope.

## Settings cannot find Git

Install [Git for Windows](https://git-scm.com/download/win), then restart CC
Port. For a non-standard installation, set an absolute path in
`[git].executable` in `config.toml`, or set `CC_PORT_GIT_EXECUTABLE`.

Check it in PowerShell:

```powershell
git --version
where.exe git
```

## Git Credential Manager is unavailable

Check:

```powershell
git credential-manager --version
git config --global --get-all credential.helper
```

Git for Windows normally includes Git Credential Manager. If the installation
is incomplete, rerun the Git for Windows installer and enable its credential
management component. CC Port does not modify global Git configuration.

## Repository connection fails

The desktop app accepts only a GitHub repository root URL:

```text
https://github.com/<owner>/<repo>
```

It rejects:

- A user or organization home page.
- A `tree`, `issues`, or individual-file page.
- A URL containing a username, token, or password.
- A non-GitHub host or custom port.
- An SSH URL entered in desktop Settings.

Confirm that the current GitHub account can read and push to the repository.
Cancelled sign-in, insufficient permission, network timeout, and expired
credentials produce different errors. After fixing the cause, verify the
connection again in Settings.

## Remote refresh fails

- Confirm that the repository has a default branch.
- Confirm that the bound repository has not been deleted or renamed.
- Verify the connection again in Settings to refresh Git Credential Manager
  credentials.
- Check proxy, firewall, and GitHub connectivity.

Background refresh never opens an interactive sign-in window. Return to Settings
when an interactive sign-in is required.

## A resource shows “target conflict”

The target tool contains a same-name resource without CC Port ownership
metadata. CC Port blocks a normal installation to avoid overwriting manually
maintained content.

Possible actions:

- Inspect and manually back up the existing content before deciding whether to
  import it.
- Use **Save as copy** to install under a new name.
- If both instances are the same resource, use a supported import path to take
  ownership; do not forge ownership files.

## A write operation fails

Write operations are not automatically retried. Review Task Center or CLI
output, check the current target state, then create a new plan from the original
entry point.

A failed operation can have one of three outcomes:

- It failed before writing and the target is unchanged.
- It failed after writing but rollback succeeded; the target was restored and
  the operation record remains.
- Rollback failed; stop further writes, preserve the backup, and file a bug.

Do not paste tokens, private repository contents, or unredacted MCP environment
values into a bug report.

## Local state directory

The default Windows state directory is:

```text
%LOCALAPPDATA%\cc-port
```

It contains operation history, backups, locks, remote cache, and snapshots. Do
not delete it while CC Port is running.

For isolated troubleshooting, temporarily select another state directory:

```powershell
$env:CC_PORT_STATE_HOME = "D:\Temp\cc-port-state"
```

Deleting the state directory does not delete the GitHub repository, but it
permanently removes local recovery records, ownership information, and backups.

## Collect diagnostic information

A public issue may include:

- CC Port version.
- Windows version.
- Git and Git Credential Manager versions.
- Reproduction steps.
- Redacted error messages.
- Screenshots without user data.

Report vulnerabilities privately as described in the
[security policy](../SECURITY.md).
