# Evidence contract

Declare the one-Skill live E2E PASS only when every required artifact exists and satisfies this
contract. A later success does not make an earlier failure disappear; classify and explain it.

## Required evidence

| Artifact | Required proof |
|---|---|
| `preflight.json` | Safe run id/repository name, source baseline, installer size/hash, isolated evidence directory |
| `github-repo.json` | Authenticated owner, private visibility, default branch, repository id, Registry v1 initial commit |
| `session-context.json` | Unique Windows temp root, installed binaries, exact profile, isolated paths, installer and fixture hashes |
| `ui-enable.json` | Real WebView connection, managed Skill/MCP installation, installed command match, verified transport, no trusted-interaction error |
| `inventory-initial.json` | Packaged MCP, 28 or more tools, exact profile, healthy Registry, local-only Skill, upload available |
| `plan-upload.json` | Unblocked plan, exact identities, pending desktop approval, initial remote commit |
| `ui-approve-upload.json` | Exact upload operation id matched before scope confirmation and one-time approval |
| `apply-upload.json` | Succeeded apply, consumed approval, remote commit, zero unresolved warnings, refreshed `same` |
| `remote-upload-verification.json` | Independent Windows clone, exact commit paths, Registry v1 identity/path, original byte hashes |
| `prepare-download.json` | Only isolated fixture moved, profile target missing, backup present |
| `plan-download.json` | Remote-only/missing state, download available, unblocked plan, pending approval, upload commit bound |
| `ui-approve-download.json` | Exact download operation id matched before scope confirmation and one-time approval |
| `apply-download.json` | Succeeded apply, consumed approval, unchanged remote commit, refreshed `same`, managed ownership |
| `verify-download-files.json` | Installed Skill and reference hashes equal the original fixture |
| `ui-uninstall.json` | Managed AI integration removed, fixture preserved, no trusted-interaction error |
| `session-cleanup.json` | App uninstalled, temp root removed, zero cleanup errors |

## Cross-artifact invariants

- The repository full name and URL identify the same uniquely named private repository.
- The repository name begins with the approved `cc-port-e2e-` prefix and was not reused.
- The session installer hash equals preflight's installer hash.
- The upload plan's remote commit equals the initial Registry commit.
- The upload apply remote commit differs from the initial commit and equals independent cloned HEAD.
- The post-upload and post-download inventory remote commits equal the upload commit.
- Upload and download operation ids, plan hashes, and approval ids match their UI/apply artifacts.
- Each approval finishes `consumed`; no approval is reused.
- The original, independently cloned, and downloaded file hashes are equal.
- The source repository final HEAD and index equal the preflight baseline; no source push or commit was
  created by the test.
- Final Windows `git ls-remote` equals the product upload commit.

## Failure classifications

- **Product failure**: packaged component crash, remote fetch/push failure with valid credentials,
  invalid Registry written by product, trusted desktop rejection, stale/approval contract breach,
  wrong commit scope, byte mismatch, missing ownership, incorrect final state, or cleanup damage.
- **Harness failure**: invalid quoting, shallow clone missing its parent, host line-ending conversion
  in an independent checkout, wrong assumption about remote-only local instances, report parser bug,
  or inability to address the actual UI despite a healthy product.
- **Environment blocker**: user installation already present, GCM missing or unauthenticated,
  GitHub unavailable, installer absent, WebView2 debugging unavailable, or required Windows runtime
  missing.

Fix harness failures and rerun only the affected safe phase. Do not rerun a consumed product write
approval; generate a fresh plan. Do not label an environment blocker or unexecuted phase PASS.

## Minimum final report

Lead with `PASS`, `FAIL`, or `BLOCKED`, plus the exact certified scope. Include:

- installer filename, size, SHA-256, version, and environment;
- repository link, owner, private visibility, default branch, initial commit, upload commit, final HEAD;
- resource key, profile id, MCP interface and tool count;
- upload/download operation ids and plan hashes; approval ids may be shortened in prose but remain
  complete in evidence;
- approval mode and final `consumed` states;
- Registry health before and after;
- original/remote/downloaded hashes;
- independent commit path list;
- uninstall, temp-root, process, worktree, and index cleanup;
- commands actually run, harness corrections, skipped checks, and untested ranges.

Always state that the single-resource Skill proof does not itself certify other kinds, batch UI,
conflicts, links, WSL, Marketplace plugins, concurrent races, credential-expiry recovery, or network
recovery. If `references/remaining-scope.md` was also executed, report those results as a separate
native source/local-Git layer and keep packaged/real-GitHub claims limited to this contract.
