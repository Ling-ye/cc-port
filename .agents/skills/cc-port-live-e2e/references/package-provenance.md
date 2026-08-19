# Current-HEAD package provenance

Use this lane when the existing installer was built from an older or dirty source state and the
user needs proof for the current commit. It produces and consumes a local candidate only; it does
not authorize a tag, Release, repository creation, desktop approval, or remote write.

## Build from an exact clean tree

1. Record the main repository `HEAD`, `origin/main`, branch, status, index, and worktrees.
2. Build directly from clean synchronized `main` when possible.
3. If task-related deliverables make the main worktree dirty, create a detached worktree at the
   exact `HEAD`; do not create a branch merely for packaging.
4. Create a Windows-target build worktree with **Windows Git**. A worktree created by WSL Git may
   write `/mnt/...` into its `.git` pointer, causing native Windows Git tests to fail with exit 128.
5. Give the build worktree its own `.venv` and `desktop/node_modules`. Do not junction a shared
   editable virtual environment: setup will repoint the shared `cc-port` installation to the build
   worktree and leave the developer environment coupled to temporary state.
6. Require Windows Git status to be empty and the detached `HEAD` to equal the recorded source SHA.
7. Run the official Windows PowerShell entrypoint:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass `
     -File .\scripts\release-desktop.ps1 -Clean
   ```

8. Require all release phases, public allowlist, checksums, host-path scan, packaged sidecar smoke,
   and packaged MCP agent smoke to pass. Retain failed metrics separately from a later successful
   rerun.

## Perform a read-only actual installation

Use one unique Windows temporary root and refuse to continue when a user CC Port installation is
already registered. Point `CC_PORT_CONFIG`, `CC_PORT_STATE_HOME`, WebView2 data, tool home, and all
profile targets inside that root.

1. Verify the installer SHA-256 before execution.
2. Install silently to `<test-root>/app` and require desktop, sidecar, agent, and uninstaller files.
3. Launch the installed desktop and require a real nonzero window handle.
4. Run `tools/packaging/agent/smoke_agent.py` against the **installed** `cc-port.exe`; require MCP
   initialize, tools/list, `cc_port_status`, and the expected tool count.
5. Invoke the installed sidecar with a read-only action such as `operation_history_page`; require a
   structured `ok` response.
6. Compare installed agent and sidecar hashes with the verified internal artifacts. The installed
   desktop may differ because Tauri patches bundle metadata for MSI/NSIS; require the installed copy
   to launch and record both hashes.
7. Do not click enable, approve, or apply in this lane. Those actions belong to the separately
   authorized live E2E workflow.
8. Stop the desktop, run the exact installed uninstaller silently, remove only the generated test
   root, and require zero uninstall entries and cleanup errors.

## Evidence and claims

Record the exact source SHA, successful metrics file, installer name/size/hash, installed component
hashes, MCP tool count, sidecar action, desktop readiness, uninstall state, temporary-root cleanup,
commands, and harness corrections.

This lane proves current-source clean build, package integrity, installation, launch, packaged
read-only interfaces, and uninstall. It does not prove packaged resource mutation, trusted desktop
approval, real GitHub authentication, multi-resource remote round trips, SmartScreen on a clean VM,
native symbolic links, or a real Claude Marketplace installation.
