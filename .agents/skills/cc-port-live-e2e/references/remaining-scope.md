# Remaining-scope validation

Use this local native-integration lane after the packaged one-Skill GitHub proof when the user asks
for broader confidence. It performs no GitHub write and does not require desktop approval. Keep its
result separate from package certification because it imports the source services directly.

## Coverage layers

1. Run the complete native Windows backend suite and record every skip reason.
2. Run all frontend tests, the production frontend build, and Rust library tests.
3. Run the named high-value Git/filesystem cases for batch commits, push races, three-way conflicts,
   unmanaged overwrite, Prompt, Plugin, Instruction, Memory, and Marketplace command orchestration.
4. Run `scripts/test_remaining_scope_live.py` through native Windows pytest. It verifies:
   - a real Windows directory junction is classified and resolved without losing its logical path;
   - a real WSL LX symlink is blocked while a sibling ordinary Skill remains discoverable;
   - Skill, MCP, Rule, Prompt, Instruction, and Memory complete local-bare-Git download, WSL UNC
     install, local update, upload, independent clone, Registry v1, and Git-blob byte verification;
   - an unreachable localhost Git remote leaves local scanning available and blocks upload;
   - the configured Git/GCM ready path and a missing-Git diagnostic path both behave as declared.
5. If the prior package was built from a dirty or older source state, run
   `package-provenance.md` before relying on it as the current-commit candidate.
6. When the user explicitly authorizes a real Claude Marketplace install, create one exact
   `/tmp/cc-port-claude-e2e-*` root inside the target WSL distribution, install CC Port into an
   isolated WSL virtual environment, and run `scripts/test_real_claude_marketplace.py`. The script
   must resolve the target profile's same-runtime Claude CLI, use an isolated
   `CLAUDE_CONFIG_DIR`, install without starting the plugin, verify disabled and enabled native
   states, uninstall, remove the Marketplace, and prove the protected real Claude config is
   unchanged. Remove only the exact generated `/tmp` root afterward.

The bundled test uses only pytest temporary directories, one generated WSL `/tmp/cc-port-remaining-*`
root, and a local bare Git repository. Cleanup removes the exact generated WSL root. It does not
touch user profiles, the configured resource repository, GitHub, CC Port source Git state, or an
installed desktop application.

## Native Windows commands

Run from the cc-port repository root. With exactly one installed distro, the bundled test reads its
exact name from `wsl.exe -l -q`. With multiple distros, set `CC_PORT_E2E_WSL_DISTRO` to one exact
listed name; do not infer or normalize it.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs

Push-Location desktop
npm.cmd exec vitest run
npm.cmd run build
Pop-Location

Push-Location desktop\src-tauri
$cargo = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
& $cargo test --lib
Pop-Location

.\.venv\Scripts\python.exe -m pytest -vv `
  tests/test_asset_sync.py::test_cursor_prompt_upload_and_copy_to_remote_keep_directory_storage `
  tests/test_asset_sync.py::test_content_plugin_upload_commits_planned_spec_and_source `
  tests/test_asset_sync.py::test_opencode_content_download_restores_file_and_only_declared_dependencies `
  tests/test_asset_sync.py::test_remote_batch_applies_multiple_changes_in_one_commit `
  tests/test_asset_sync.py::test_remote_push_race_revalidates_and_retries_once `
  tests/test_asset_sync.py::test_download_requires_explicit_unmanaged_overwrite_and_writes_composite_marker `
  tests/test_asset_sync.py::test_instruction_rows_keep_windows_and_wsl_profiles_distinct_and_apply_one `
  tests/test_asset_sync.py::test_memory_download_requires_local_project_mapping_and_rechecks_target `
  tests/test_resource_sync.py::test_resource_sync_fast_forward_and_three_way_conflict `
  tests/test_claude_plugins.py::test_native_claude_installer_adds_marketplace_installs_and_disables

.\.venv\Scripts\python.exe -m pytest -vv -s `
  .agents\skills\cc-port-live-e2e\scripts\test_remaining_scope_live.py
```

For the separately authorized real WSL Claude Marketplace lane, run inside the exact WSL
distribution. Do not reuse the Windows virtual environment, do not point `--config-dir` at the
real `~/.claude`, and do not start Claude or any installed plugin:

```bash
repo_root="$(pwd -P)"
test_root="$(mktemp -d /tmp/cc-port-claude-e2e-XXXXXX)"
python3 -m venv "$test_root/venv"
"$test_root/venv/bin/pip" install --disable-pip-version-check -e "$repo_root"
PATH="$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
PYTHONPYCACHEPREFIX="$test_root/pycache" \
"$test_root/venv/bin/python" \
  .agents/skills/cc-port-live-e2e/scripts/test_real_claude_marketplace.py \
  --config-dir "$test_root/config" \
  --output <evidence-dir>/claude-marketplace.json
case "$test_root" in
  /tmp/cc-port-claude-e2e-*) rm -rf -- "$test_root" ;;
  *) printf 'Refusing unsafe cleanup target: %s\n' "$test_root" >&2; exit 1 ;;
esac
```

The final removal is allowed only for the exact `mktemp` result after validating that it is under
`/tmp` and begins with `cc-port-claude-e2e-`.

When multiple WSL distros are installed, wrap the last command with the exact parameter and unset
it afterward if the shell remains open:

```powershell
$env:CC_PORT_E2E_WSL_DISTRO = 'Ubuntu-22.04'
.\.venv\Scripts\python.exe -m pytest -vv -s `
  .agents\skills\cc-port-live-e2e\scripts\test_remaining_scope_live.py
Remove-Item Env:CC_PORT_E2E_WSL_DISTRO -ErrorAction SilentlyContinue
```

## Result classification

- The full backend, frontend, build, Rust, and named tests are automated source/build evidence.
- The bundled remaining-scope test is native Windows plus real local Git/WSL filesystem evidence,
  but it bypasses packaged MCP and desktop approval by calling source services.
- The GitHub package run is the only proof of packaged MCP, trusted desktop approval, Windows GCM
  remote authentication, and a real GitHub upload/download round trip.
- The default named Marketplace unit test uses a controlled fake Claude CLI. A result from
  `test_real_claude_marketplace.py` is separate native WSL evidence only when it records the exact
  distro and CLI, real add/install/disable/enable/uninstall/remove operations, unchanged protected
  config hashes, zero residual plugin/Marketplace state, and zero cleanup errors.
- Native Windows symbolic-link tests remain unexecuted when Developer Mode or elevation is absent;
  a junction PASS does not certify symbolic links.
- A localhost connection refusal certifies fail-closed behavior at loss of transport, not credential
  expiry or successful recovery after an external outage.

Never merge these layers into one broad “all E2E passed” claim. Report each layer, skip, blocker,
and correction independently.
