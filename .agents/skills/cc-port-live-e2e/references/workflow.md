# Live E2E workflow

Use this sequence for the one-Skill, single-resource Windows package proof. Do not silently expand
it to other resources or user profiles.

## 1. Resolve authority and baseline

1. Confirm the user explicitly authorized account use, private repository creation, real remote
   writes, and desktop control for exact approvals.
2. Record `pwd`, repository root, branch, `HEAD`, `origin/main`, worktree status, staged paths, and
   worktrees. Stop on unrelated dirty changes that overlap the Skill or harness.
3. Resolve one already-built Windows installer. Record its absolute path, size, SHA-256, version,
   and signing status. Do not rebuild unless requested.
4. Confirm Windows Git and Git Credential Manager are available. A stale WSL `gh` token does not
   represent Windows-native credentials.
5. Choose `build/live-e2e/<run-id>/` for evidence. Never use the source root, a real tool home, or a
   saved resource checkout as a test target.

Run the source-read-only preflight; it writes only a new ignored evidence plan:

```text
python .agents/skills/cc-port-live-e2e/scripts/preflight.py \
  --repo-root <cc-port-root> \
  --installer <absolute-windows-installer> \
  --evidence-root <cc-port-root>/build/live-e2e
```

Use the generated `run_id`, `repository_name`, and evidence directory unchanged.

## 2. Create the private test repository

Run `create_private_repo.ps1` with the generated repository name. It must:

- obtain GitHub credentials from Windows GCM only in memory;
- verify the authenticated login;
- create a user-owned private repository whose name matches the generated test prefix;
- enable no Issues, Projects, or Wiki;
- auto-initialize the default branch and add only `registry.yaml` v1 after the README commit;
- report the owner, URL, visibility, branch, repository id, initial commit, and Registry blob SHA;
- clear credential variables and never delete the repository.

Read back repository metadata and `registry.yaml` through an independent API or connector when
available. Also run Windows `git ls-remote` against the default branch.

## 3. Start the isolated installed session

Run `session.ps1` in a long-lived Windows process. Pass the installer, expected installer hash,
repository name/URL/branch, and generated evidence directory. The script derives the context,
stop-signal, and cleanup-report paths inside that directory. Wait for its `READY` record before
continuing.

The session must refuse an existing CC Port installation. It creates only:

- a unique Windows temporary root;
- an installer target inside that root;
- isolated config, state, WebView2, tool home, MCP JSON, resource checkout, and fixture backup;
- profile id `package-test`, tool id `cursor`, environment kind `windows`;
- `skill:cc-port-e2e-skill` containing inert Markdown only.

Check the context's installer hash against preflight before using it.

## 4. Enable the packaged AI integration

With the user's explicit live-run authority, run `ui_driver.mjs enable`. It must connect to the real
`http://tauri.localhost/` WebView, click the displayed enable plan, and verify:

- the managed `cc-port` Skill exists beside the fixture;
- the isolated MCP JSON contains only the expected managed entry;
- the command is the installed session's `cc-port.exe` with `mcp --stdio`;
- the installed sidecar returns `transport_status=verified`;
- the trusted-desktop-interaction error is absent.

Do not launch the sidecar directly to approve a write. The sidecar verification call is read-only.

## 5. Upload through the packaged MCP agent

Run these `mcp_roundtrip.py` phases with the context and a dedicated upload plan-state file:

1. `inventory`
2. `plan-upload`
3. `ui_driver.mjs approve ... <upload-operation-id>`
4. `apply-upload`

Require the pre-plan inventory to show:

- version and exact `package-test` Windows profile;
- `approval_mode=desktop-only` and no exposed approval tools;
- remote available, no warning, healthy Registry at the initial commit;
- `skill:cc-port-e2e-skill` is `local-only` with `upload` available;
- one exact local instance id returned by inventory.

The plan must be unblocked, warning-free unless explicitly reviewed, bound to the initial remote
commit, and pending one desktop approval. Before clicking, the approval dialog must display the
exact operation id. Apply with the unchanged operation id, plan hash, and approval id. Require
`succeeded`, `consumed`, no stale replacement, and a refreshed `same` state.

## 6. Independently verify the upload

Run `verify_remote.ps1` with the apply result's remote commit and the original two fixture hashes.
It must use Git for Windows, depth 2, and `core.autocrlf=false`, then require:

- cloned HEAD equals the product upload commit;
- the clone is clean;
- the commit changed exactly `registry.yaml`, `SKILL.md`, and `references/proof.md`;
- Registry v1 contains exactly one `skill:cc-port-e2e-skill` path entry;
- both repository bytes match the original fixture.

Do not treat a host-transformed checkout hash as a remote blob hash. Keep the parent commit
available when checking exact diff scope.

## 7. Remove the isolated local copy and download

Run these phases with a separate download plan-state file:

1. `prepare-download`
2. `plan-download`
3. `ui_driver.mjs approve ... <download-operation-id>`
4. `apply-download`
5. `verify-downloaded-files`

`prepare-download` may move only the fixture under the isolated test root. It must retain a backup
and leave the profile target missing. Require the next inventory to show `remote-only`, local
`missing`, remote `present`, and `download` available.

Do not invent a local instance id when the remote-only inventory contains none. Keep the exact
profile id; the plan may resolve the expected target instance. Require an unblocked pending plan,
exact desktop approval, `succeeded`, `consumed`, refreshed `same`, managed local ownership, and both
downloaded hashes equal to the originals.

## 8. Uninstall and clean up

Run `ui_driver.mjs uninstall`. Confirm it removes only the managed `cc-port` automation Skill and
MCP entry while preserving the downloaded fixture. Then create the exact stop-signal file and wait
for `session.ps1` to finish.

Require its cleanup report to show:

- remaining CC Port uninstall entries: zero;
- unique Windows test root removed;
- no cleanup errors.

Remove only exact test debug clones. Recheck remote HEAD, repository visibility, source `HEAD`,
`origin/main`, status, and index. Keep the private repository and ignored evidence directory.

## 9. Validate and report

Run `validate_evidence.py` over the evidence directory. Read
`references/evidence-contract.md` before declaring PASS. Report harness failures and corrections
separately from product failures; never erase a failed report merely because a later run passed.

Use the native Windows command templates in `references/commands.md`; do not substitute WSL Node
or WSL Python for the UI and MCP phases because the context contains Windows paths and controls a
Windows process.

If the user requests the remaining resource/link/WSL/failure scope, finish this package workflow
first, then run `references/remaining-scope.md` as a separately labeled native source-integration
layer. Do not reuse its local bare repository as the package run's unique GitHub repository.
