"""Drive the installed CC Port agent through real stdio MCP for remote Skill E2E."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import traceback
from pathlib import Path
from typing import Any, TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RESOURCE_KEY = "skill:cc-port-e2e-skill"
PROFILE_ID = "package-test"


def _jsonable(result: object) -> dict[str, Any]:
    for attribute in ("structuredContent", "structured_content"):
        value = getattr(result, attribute, None)
        if isinstance(value, dict):
            return value
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        value = dump(mode="json", by_alias=True)
        if isinstance(value, dict):
            return value
    raise RuntimeError("MCP tool result did not contain a structured JSON object.")


def _assert_envelope(payload: dict[str, Any], *, tool: str) -> dict[str, Any]:
    if payload.get("contract_version") != 1:
        raise RuntimeError(f"{tool} returned an unexpected contract version: {payload.get('contract_version')!r}")
    if payload.get("ok") is not True:
        raise RuntimeError(f"{tool} failed: status={payload.get('status')!r} error={payload.get('error')!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{tool} returned no data object.")
    return data


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    payload = _jsonable(result)
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise RuntimeError(f"{name} returned an MCP error: {payload!r}")
    return payload


def _resource(inventory: dict[str, Any]) -> dict[str, Any]:
    resources = inventory.get("resources")
    if not isinstance(resources, list):
        raise RuntimeError("asset_inventory returned no resources list.")
    for item in resources:
        if isinstance(item, dict) and item.get("resource_key") == RESOURCE_KEY:
            return item
    raise RuntimeError(f"{RESOURCE_KEY} was not found in the fresh inventory.")


def _local_instance_or_none(resource: dict[str, Any]) -> dict[str, Any] | None:
    instances = resource.get("local_instances")
    if not isinstance(instances, list):
        raise RuntimeError("Selected resource returned no local_instances list.")
    for instance in instances:
        if isinstance(instance, dict) and instance.get("platform") == PROFILE_ID:
            return instance
    return None


def _local_instance(resource: dict[str, Any]) -> dict[str, Any]:
    instance = _local_instance_or_none(resource)
    if instance is not None:
        return instance
    raise RuntimeError(f"Selected resource returned no {PROFILE_ID} local instance.")


def _status_summary(data: dict[str, Any]) -> dict[str, Any]:
    profiles = data.get("profiles") if isinstance(data.get("profiles"), list) else []
    profile = next((item for item in profiles if isinstance(item, dict) and item.get("profile_id") == PROFILE_ID), None)
    if profile is None:
        raise RuntimeError(f"cc_port_status did not expose the exact {PROFILE_ID} profile.")
    recommended = data.get("recommended_tools")
    for required in ("asset_inventory", "asset_action_plan", "asset_action_apply"):
        if not isinstance(recommended, list) or required not in recommended:
            raise RuntimeError(f"cc_port_status did not recommend {required}.")
    return {
        "version": data.get("version"),
        "approvalMode": data.get("approval_mode"),
        "approvalToolsExposed": data.get("approval_tools_exposed"),
        "profile": {
            "profileId": profile.get("profile_id"),
            "toolId": profile.get("tool_id"),
            "environmentKind": profile.get("environment_kind"),
            "enabled": profile.get("enabled"),
        },
        "recommendedToolsPresent": True,
    }


def _inventory_summary(data: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
    local = _local_instance_or_none(resource) or {}
    registry = data.get("registry_health") if isinstance(data.get("registry_health"), dict) else {}
    return {
        "branch": data.get("branch"),
        "remoteCommit": data.get("remote_commit"),
        "remoteAvailable": data.get("remote_available"),
        "remoteWarning": data.get("remote_warning"),
        "scannedLocal": data.get("scanned_local"),
        "registryHealth": {
            "status": registry.get("status"),
            "checkedCommit": registry.get("checked_commit"),
            "issueCount": registry.get("issue_count"),
        },
        "resource": {
            "resourceKey": resource.get("resource_key"),
            "status": resource.get("status"),
            "localStatus": resource.get("local_status"),
            "remoteStatus": resource.get("remote_status"),
            "availableActions": resource.get("available_actions"),
            "remoteExists": (resource.get("remote") or {}).get("exists"),
            "remoteCommit": (resource.get("remote") or {}).get("commit"),
            "localInstanceId": local.get("id"),
            "localInstanceStatus": local.get("status"),
            "localFingerprint": local.get("fingerprint"),
            "ownership": local.get("ownership"),
        },
    }


async def _open_session(context: dict[str, Any], error_log: TextIO):
    env = dict(os.environ)
    env["CC_PORT_CONFIG"] = str(context["configPath"])
    env["CC_PORT_STATE_HOME"] = str(context["stateDir"])
    parameters = StdioServerParameters(
        command=str(context["agentExe"]),
        args=["mcp", "--stdio"],
        env=env,
    )
    return stdio_client(parameters, errlog=error_log)


async def _run_mcp_phase(
    phase: str,
    context: dict[str, Any],
    plan_state_path: Path,
    error_log: TextIO,
) -> dict[str, Any]:
    transport = await _open_session(context, error_log)
    async with transport as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_names = sorted(tool.name for tool in listed.tools)

            status_payload = await _call(session, "cc_port_status", {})
            status = _assert_envelope(status_payload, tool="cc_port_status")
            status_summary = _status_summary(status)

            inventory_payload = await _call(
                session,
                "asset_inventory",
                {"scan_local": True, "refresh_remote": True, "scan_global": True},
            )
            inventory = _assert_envelope(inventory_payload, tool="asset_inventory")
            resource = _resource(inventory)
            instance = _local_instance_or_none(resource)
            inventory_summary = _inventory_summary(inventory, resource)

            base = {
                "interface": "installed cc-port.exe mcp --stdio",
                "phase": phase,
                "toolCount": len(tool_names),
                "requiredToolsDiscovered": all(
                    item in tool_names
                    for item in ("cc_port_status", "asset_inventory", "asset_action_plan", "asset_action_apply")
                ),
                "status": status_summary,
                "inventory": inventory_summary,
                "diff": {"called": False, "reason": "The selected state is one-sided; no content diff is applicable."},
            }

            if phase == "inventory":
                return base

            if phase in {"plan-upload", "plan-download"}:
                action = phase.removeprefix("plan-")
                expected_status = "local-only" if action == "upload" else "remote-only"
                if resource.get("status") != expected_status:
                    raise RuntimeError(
                        f"{RESOURCE_KEY} status was {resource.get('status')!r}; expected {expected_status!r} before {action}."
                    )
                if action not in (resource.get("available_actions") or []):
                    raise RuntimeError(f"{action} was not available for {RESOURCE_KEY}.")
                if action == "upload" and instance is None:
                    raise RuntimeError("Upload requires the exact local instance returned by inventory.")
                plan_payload = await _call(
                    session,
                    "asset_action_plan",
                    {
                        "action": action,
                        "kind": "skill",
                        "name": "cc-port-e2e-skill",
                        "platform": PROFILE_ID,
                        "local_instance_id": instance.get("id") if instance is not None else "",
                        "new_name": "",
                        "new_install_name": "",
                        "overwrite_unmanaged": False,
                        "link_target_confirmed": False,
                    },
                )
                plan = _assert_envelope(plan_payload, tool="asset_action_plan")
                if plan.get("blocked") or plan.get("requires_approval") is not True or plan.get("approval_status") != "pending":
                    raise RuntimeError(f"{action} plan was not executable with a pending approval: {plan!r}")
                state = {
                    "action": action,
                    "resourceKey": RESOURCE_KEY,
                    "profileId": PROFILE_ID,
                    "operationId": plan.get("operation_id"),
                    "planHash": plan.get("plan_hash"),
                    "approvalId": plan.get("approval_id"),
                    "approvalScopeHash": plan.get("approval_scope_hash"),
                    "localInstanceId": plan.get("local_instance_id"),
                    "remoteCommit": plan.get("remote_commit"),
                    "remoteBranch": plan.get("remote_branch"),
                    "createdAt": plan.get("created_at"),
                }
                plan_state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
                base["plan"] = {
                    **state,
                    "blocked": plan.get("blocked"),
                    "blockers": plan.get("blockers"),
                    "warnings": plan.get("warnings"),
                    "requiresApproval": plan.get("requires_approval"),
                    "approvalStatus": plan.get("approval_status"),
                    "remoteTargetExists": plan.get("remote_target_exists"),
                    "targetExists": plan.get("target_exists"),
                    "targetManaged": plan.get("target_managed"),
                }
                return base

            if phase in {"apply-upload", "apply-download"}:
                action = phase.removeprefix("apply-")
                state = json.loads(plan_state_path.read_text(encoding="utf-8"))
                if state.get("action") != action:
                    raise RuntimeError("Persisted plan state does not match the requested apply phase.")
                apply_payload = await _call(
                    session,
                    "asset_action_apply",
                    {
                        "operation_id": state["operationId"],
                        "plan_hash": state["planHash"],
                        "approval_id": state["approvalId"],
                    },
                )
                applied = _assert_envelope(apply_payload, tool="asset_action_apply")
                if applied.get("status") not in {"succeeded", "unchanged"}:
                    raise RuntimeError(f"{action} apply did not succeed: {applied!r}")

                verified_payload = await _call(
                    session,
                    "asset_inventory",
                    {"scan_local": True, "refresh_remote": True, "scan_global": True},
                )
                verified = _assert_envelope(verified_payload, tool="asset_inventory")
                verified_resource = _resource(verified)
                verified_summary = _inventory_summary(verified, verified_resource)
                if verified_resource.get("status") != "same":
                    raise RuntimeError(
                        f"Post-{action} inventory status was {verified_resource.get('status')!r}; expected 'same'."
                    )
                base["apply"] = {
                    "operationId": applied.get("operation_id"),
                    "planHash": applied.get("plan_hash"),
                    "approvalId": applied.get("approval_id"),
                    "approvalStatus": applied.get("approval_status"),
                    "status": applied.get("status"),
                    "message": applied.get("message"),
                    "remoteCommit": applied.get("remote_commit"),
                    "replayedOnLatest": applied.get("replayed_on_latest"),
                    "pushRetryCount": applied.get("push_retry_count"),
                    "warnings": applied.get("warnings"),
                }
                base["verificationInventory"] = verified_summary
                return base

            raise RuntimeError(f"Unsupported MCP phase: {phase}")


def _prepare_download(context: dict[str, Any]) -> dict[str, Any]:
    test_root = Path(context["testRoot"]).resolve()
    fixture = Path(context["fixtureDir"]).resolve()
    backup = Path(context["fixtureBackup"]).resolve()
    if test_root not in fixture.parents or test_root not in backup.parents:
        raise RuntimeError("Fixture or backup path escaped the isolated test root.")
    if not (fixture / "SKILL.md").is_file():
        raise RuntimeError("Fixture Skill is missing before the download setup step.")
    if backup.exists():
        raise RuntimeError("Fixture backup already exists.")
    shutil.move(str(fixture), str(backup))
    if fixture.exists() or not (backup / "SKILL.md").is_file():
        raise RuntimeError("Fixture move did not produce a clean missing-local state.")
    return {
        "phase": "prepare-download",
        "fixtureMovedOutOfProfile": True,
        "profileTargetExists": fixture.exists(),
        "backupExists": backup.exists(),
    }


def _verify_downloaded_files(context: dict[str, Any]) -> dict[str, Any]:
    fixture = Path(context["fixtureDir"])
    skill = fixture / "SKILL.md"
    proof = fixture / "references" / "proof.md"
    if not skill.is_file() or not proof.is_file():
        raise RuntimeError("Downloaded Skill files are missing.")
    skill_hash = hashlib.sha256(skill.read_bytes()).hexdigest().upper()
    proof_hash = hashlib.sha256(proof.read_bytes()).hexdigest().upper()
    expected_skill = str(context["fixtureSkillSha256"]).upper()
    expected_proof = str(context["fixtureProofSha256"]).upper()
    if skill_hash != expected_skill or proof_hash != expected_proof:
        raise RuntimeError("Downloaded Skill bytes do not match the original fixture.")
    return {
        "phase": "verify-downloaded-files",
        "skillInstalled": True,
        "skillSha256": skill_hash,
        "proofSha256": proof_hash,
        "matchesOriginalFixture": True,
    }


async def _async_main(args: argparse.Namespace, context: dict[str, Any]) -> dict[str, Any]:
    if args.phase == "prepare-download":
        return _prepare_download(context)
    if args.phase == "verify-downloaded-files":
        return _verify_downloaded_files(context)
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        return await asyncio.wait_for(
            _run_mcp_phase(args.phase, context, args.plan_state, error_log),
            timeout=180.0,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=(
            "inventory",
            "plan-upload",
            "apply-upload",
            "prepare-download",
            "plan-download",
            "apply-download",
            "verify-downloaded-files",
        ),
    )
    parser.add_argument("context", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("plan_state", type=Path)
    args = parser.parse_args()
    try:
        context_path = args.context.resolve(strict=True)
        evidence_dir = context_path.parent
        report_path = args.report.resolve()
        plan_state_path = args.plan_state.resolve()
        if report_path.parent != evidence_dir or plan_state_path.parent != evidence_dir:
            raise RuntimeError("Context, report, and plan state must share one evidence directory.")
        if report_path.exists():
            raise RuntimeError("Refusing to overwrite an existing MCP phase report.")
        if args.phase in {"plan-upload", "plan-download"} and plan_state_path.exists():
            raise RuntimeError("Refusing to overwrite an existing plan state.")
        context = json.loads(context_path.read_text(encoding="utf-8"))
        if context.get("schemaVersion") != 1:
            raise RuntimeError("Session context schema is missing or unsupported.")
        if Path(str(context.get("evidenceDirectory", ""))).resolve() != evidence_dir:
            raise RuntimeError("Session context does not identify this evidence directory.")
        args.context = context_path
        args.report = report_path
        args.plan_state = plan_state_path
    except Exception as exc:
        print(json.dumps({"phase": args.phase, "success": False, "failure": str(exc)}, indent=2))
        return 2
    started = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    success = False
    result: dict[str, Any] | None = None
    failure = ""
    try:
        result = asyncio.run(_async_main(args, context))
        success = True
    except Exception:
        failure = traceback.format_exc()
    report = {
        "phase": args.phase,
        "startedAtUtc": started,
        "finishedAtUtc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "success": success,
        "failure": failure or None,
        "result": result,
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
