# Failure recovery

Use the narrowest recovery that preserves evidence and approval semantics.

| Symptom | Classification and action |
|---|---|
| WSL `gh auth status` fails but Windows GCM lists the account | Environment difference. Use Windows Git/GCM; do not copy or expose credentials. |
| Windows GCM has no account | Block before repository creation and ask the user to authenticate through native Git tooling. |
| Repository created but Registry seed fails | Keep the private repository, record its URL/id, repair only the test repository initialization, and never delete without separate authority. |
| Existing CC Port installation detected | Block. Do not uninstall or overwrite it. Use another test host or obtain explicit instructions for a safe existing-install workflow. |
| Desktop never becomes ready | Stop the session, preserve logs/context, uninstall the isolated candidate if present, and report BLOCKED or product failure according to evidence. |
| WebView2 has the requested debug argument but no listener | Check `netsh interface ipv4 show excludedportrange protocol=tcp`. Classify a selected excluded port as a harness failure, preserve the failed UI report, clean the session, and restart with a port that an exclusive loopback `TcpListener` can actually bind. |
| Trusted desktop interaction error | Product failure. Do not invoke the sidecar approval action directly. Preserve UI and sidecar evidence. |
| Pending approval is not visible | Reload the actual Settings view. Match the exact operation id; do not approve another pending request. |
| Plan is blocked | Stop. Report blockers; do not add overwrite, ownership, link, rename, or takeover flags without explicit user authority. |
| Apply returns `stale-plan` | Treat the replacement as new information. Discard the old approval, review the new plan, and obtain a fresh desktop approval. |
| Apply fails after approval consumption | Do not retry the consumed approval. Refresh, generate a new plan, and require a new approval. |
| Remote-only inventory has no local instance | Expected. Pass the exact target profile and an empty instance id; never fabricate identity. |
| Independent diff-tree finds no paths at shallow depth 1 | Harness failure. Reclone with depth 2 so the upload parent is available. |
| Independent checkout hash differs only by CRLF | Reclone with `core.autocrlf=false` and compare repository blob bytes. Do not change global Git config. |
| Independent commit changed extra paths | Product failure until explained. Inspect the exact diff before any download test. |
| Downloaded bytes differ | Product failure. Preserve original, remote blob, and installed hashes; do not overwrite evidence. |
| Cleanup cannot remove the unique temp root | Preserve the cleanup report, stop relevant exact test processes, retry only exact validated paths, and report remaining material. |
| Source worktree/index changed | Stop. Do not reset or clean. Identify whether changes are user-owned, Skill changes, or unexpected product effects. |
| Final remote HEAD drifted | Do not claim PASS. Compare refs/commits, classify an external race, and generate a fresh bounded plan if continuing. |

Never use `git reset --hard`, broad recursive deletion, force push, repository deletion, or real-profile
cleanup as recovery. Keep failed evidence alongside later successful evidence with timestamps.
