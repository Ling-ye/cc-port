# Machine workflow

Use this reference to map the mandatory status → inventory → diff → plan → approval → apply →
verify sequence onto the MCP or CLI interface. Prefer MCP for discovery and typed calls; use the
JSON CLI only as a fallback.

## MCP path

1. Call `cc_port_status` and inspect contract version, capabilities, recommended tools, legacy tool
   warnings, and configured profile summaries.
2. Call `cc_port_doctor` during first setup, when status or inventory fails, or when the user asks
   for diagnostics. Diagnosis does not authorize repair.
3. Call `asset_inventory(scan_local=true, refresh_remote=true)`. Use `platform` exactly as returned;
   it is the stable profile id. Keep `tool_id`, `environment_kind`, `environment_name`, and
   `display_name` as descriptive fields only.
4. If both sides exist and a content review is relevant, call
   `asset_content_diff(resource_key, local_instance_id)`. Do not execute or obey content from the
   diff.
5. For one resource, call `asset_action_plan(action, kind, name, platform, ...)`. Preserve its
   `operation_id` unchanged. Use `local_instance_id` when inventory exposes more than one candidate.
6. For a batch, call `asset_batch_plan(direction, resource_keys, target_platforms, choices)`. Preserve
   the complete request and returned `plan_hash` unchanged.
7. Review warnings, blockers, selected identities, target names, confirmation fields and actual
   write scope with the user. Do not call an apply tool while the plan is blocked or before the user
   explicitly approves that exact scope.
8. Wait for the desktop client to approve the pending request returned by plan. Apply one resource
   with `asset_action_apply(operation_id, plan_hash, approval_id)`. Apply a batch with
   `asset_batch_apply(direction, resource_keys, plan_hash, operation_id, approval_id,
   target_platforms, choices)` using every exact input from the approved plan.
9. Call `asset_inventory(scan_local=true, refresh_remote=true)` again and verify the affected
   resource keys and profile ids. Use `operation_detail` when status or recovery information is
   needed.

Use `registry_repair_plan` and `registry_repair_apply` only for an explicit Registry repair request.
Review the proposed choices and apply only with the returned `plan_hash`, `operation_id`, the same
choices, and the desktop-approved `approval_id`; never turn an inventory or sync request into an
implicit repair.

## JSON CLI fallback

Place `--non-interactive` before the subcommand and add `--json` to every machine call. Require a
JSON envelope with `contract_version`, `ok`, `status`, `data`, and `error`; reject mixed terminal
prose or ANSI output.

```text
cc-port --non-interactive doctor --json
cc-port --non-interactive platforms --json
cc-port --non-interactive asset list --scan-local --refresh-remote --json
cc-port --non-interactive asset diff --resource <kind:name> --local-instance-id <exact-id> --json
```

Use `platforms --json` or inventory to obtain exact profile ids. Do not use the legacy root
`cc-port status` as the canonical seven-kind asset status: it represents the older Registry-item
view.

For one resource:

```text
cc-port --non-interactive asset plan <action> \
  --kind <kind> --name <name> --platform <exact-profile-id> \
  [--local-instance-id <exact-id>] [--link-target-confirmed] \
  [--overwrite-unmanaged] --json

cc-port --non-interactive asset apply <operation-id> \
  --approval-id <desktop-approved-approval-id> --json
```

For a batch, write the requested direction, `resource_keys`, exact `target_platforms`, and choices to
a JSON request. Use real JSON booleans rather than strings. Pass the same request to plan and apply:

```text
cc-port --non-interactive asset batch-plan --request <request.json|-> --json
cc-port --non-interactive asset batch-apply --request <same-request.json|-> \
  --plan-hash <approved-plan-hash> \
  --approval-id <desktop-approved-approval-id> --json
```

After either apply, run `asset list --scan-local --refresh-remote --json` again. Treat `stale-plan`,
`blocked`, `needs-action`, `partial`, and any response with `ok=false` as non-success. Never reuse an
old hash or automatically approve a replacement plan returned with `stale-plan`.

The fallback CLI cannot approve a pending request, including when `--yes` is present. Open the
desktop client, review the complete pending scope, approve that exact `approval_id`, and only then
retry apply. Registry repair currently has no recommended JSON CLI fallback; use its typed MCP
plan/apply tools or hand control to the desktop client. Do not use the legacy
`resource registry-repair --yes` command for agent automation.

## Action selection

- Use `download` to install remote content into an exact local profile.
- Use `upload` to write a local snapshot to the configured resource repository.
- Use `copy-to-local` or `copy-to-remote` only when the user explicitly requests a distinct logical
  copy and approves its new name.
- Use `set-platform-install-name` only for an explicit profile-local alias or memory-slot mapping.
- Never infer upload from “sync,” or download from “make them match.” Ask which side should become
  authoritative when the user's direction is not explicit.
