"""Validate the cross-artifact contract for one CC Port live remote E2E run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GIT_OID_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
REPOSITORY_RE = re.compile(r"^cc-port-e2e-[0-9]{8}-[0-9]{6}(?:-[0-9a-f]{8})?$")
EXPECTED_REMOTE_PATHS = {
    "registry.yaml",
    "skills/cc-port-e2e-skill/SKILL.md",
    "skills/cc-port-e2e-skill/references/proof.md",
}
REQUIRED_FILES = (
    "preflight.json",
    "github-repo.json",
    "session-context.json",
    "ui-enable.json",
    "inventory-initial.json",
    "plan-upload.json",
    "ui-approve-upload.json",
    "apply-upload.json",
    "remote-upload-verification.json",
    "prepare-download.json",
    "plan-download.json",
    "ui-approve-download.json",
    "apply-download.json",
    "verify-download-files.json",
    "ui-uninstall.json",
    "session-cleanup.json",
)


class EvidenceError(RuntimeError):
    pass


def _load(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    if not path.is_file():
        raise EvidenceError(f"Missing required evidence: {name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"Invalid JSON evidence {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"Evidence root must be an object: {name}")
    return value


def _need(condition: object, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _result(report: dict[str, Any], name: str) -> dict[str, Any]:
    _need(report.get("success") is True, f"{name} did not report success")
    result = report.get("result")
    _need(isinstance(result, dict), f"{name} has no result object")
    return result


def _approval_dialog_matched(report: dict[str, Any], operation_id: object) -> bool:
    for step in report.get("steps") or []:
        if not isinstance(step, dict) or step.get("name") != "verify-exact-operation-in-dialog":
            continue
        detail = step.get("detail") or {}
        return (
            step.get("ok") is True
            and detail.get("matched") is True
            and detail.get("expectedOperationId") == operation_id
        )
    return False


def _windows_child(path: object, parent: object) -> bool:
    if not isinstance(path, str) or not isinstance(parent, str):
        return False
    child_path = PureWindowsPath(path)
    parent_path = PureWindowsPath(parent)
    return child_path != parent_path and parent_path in child_path.parents


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise EvidenceError(completed.stderr.strip() or "git validation failed")
    return completed.stdout.strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EvidenceError(completed.stderr.decode("utf-8", errors="replace").strip() or "git validation failed")
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _untracked_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name in _git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise EvidenceError("Git returned an unsafe untracked path")
        candidate = root / relative
        if candidate.is_symlink():
            payload = b"symlink\0" + os.readlink(candidate).encode("utf-8")
            result[name] = hashlib.sha256(payload).hexdigest()
        elif candidate.is_file():
            result[name] = _sha256(candidate)
        else:
            raise EvidenceError("Untracked source entry is not a regular file or symbolic link")
    return dict(sorted(result.items()))


def validate(root: Path, repo_root: Path, final_remote_head: str) -> dict[str, Any]:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    _need(not missing, f"Missing required evidence: {', '.join(missing)}")
    _need(GIT_OID_RE.fullmatch(final_remote_head), "--final-remote-head must be a full Git object id")

    preflight = _load(root, "preflight.json")
    github = _load(root, "github-repo.json")
    context = _load(root, "session-context.json")
    ui_enable = _load(root, "ui-enable.json")
    initial = _result(_load(root, "inventory-initial.json"), "inventory-initial.json")
    plan_upload = _result(_load(root, "plan-upload.json"), "plan-upload.json")
    ui_upload = _load(root, "ui-approve-upload.json")
    apply_upload = _result(_load(root, "apply-upload.json"), "apply-upload.json")
    remote = _load(root, "remote-upload-verification.json")
    prepare = _result(_load(root, "prepare-download.json"), "prepare-download.json")
    plan_download = _result(_load(root, "plan-download.json"), "plan-download.json")
    ui_download = _load(root, "ui-approve-download.json")
    apply_download = _result(_load(root, "apply-download.json"), "apply-download.json")
    files = _result(_load(root, "verify-download-files.json"), "verify-download-files.json")
    ui_uninstall = _load(root, "ui-uninstall.json")
    cleanup = _load(root, "session-cleanup.json")

    repo_name = github.get("repository")
    _need(isinstance(repo_name, str) and REPOSITORY_RE.fullmatch(repo_name), "Unsafe or reused repository name")
    owner = github.get("owner")
    _need(isinstance(owner, str) and owner and "/" not in owner, "GitHub owner is missing or malformed")
    _need(github.get("fullName") == f"{owner}/{repo_name}", "GitHub full repository identity mismatch")
    _need(github.get("htmlUrl") == f"https://github.com/{owner}/{repo_name}", "GitHub HTML URL mismatch")
    _need(github.get("cloneUrl") == f"https://github.com/{owner}/{repo_name}.git", "GitHub clone URL mismatch")
    _need(github.get("private") is True, "Test repository is not private")
    _need(github.get("defaultBranch") == "main", "Test repository default branch is not main")
    _need(github.get("registryVersion") == 1, "Initial Registry is not version 1")
    _need(preflight.get("repository_name") == repo_name, "Preflight and GitHub repository names differ")
    _need(preflight.get("retain_repository") is True, "Preflight did not retain the repository by default")

    installer = preflight.get("installer") or {}
    _need(context.get("installerSha256", "").lower() == str(installer.get("sha256", "")).lower(), "Installer hash mismatch")
    _need(SHA256_RE.fullmatch(str(installer.get("sha256", ""))), "Invalid installer SHA-256")
    _need(context.get("repositoryName") == repo_name, "Session repository name mismatch")
    _need(context.get("repositoryUrl") == github.get("cloneUrl"), "Session repository URL mismatch")
    test_root = context.get("testRoot")
    _need(
        isinstance(test_root, str)
        and PureWindowsPath(test_root).name.startswith(("cc-port-live-e2e-", "cc-port-real-remote-e2e-")),
        "Session test root is not a generated Windows E2E root",
    )
    for key in (
        "installDir",
        "stateDir",
        "configPath",
        "webViewDir",
        "toolHome",
        "skillRoot",
        "fixtureDir",
        "fixtureBackup",
        "mcpPath",
        "resourceRepo",
    ):
        _need(_windows_child(context.get(key), test_root), f"Session {key} escaped the isolated test root")
    _need(_windows_child(context.get("fixtureDir"), context.get("skillRoot")), "Fixture is outside the isolated Skill root")

    _need(ui_enable.get("success") is True, "AI integration enable UI failed")
    _need(ui_enable.get("testId") == context.get("testId"), "Enable UI used another session")
    enable_steps = ui_enable.get("steps") or []
    enable_detail = next((step.get("detail") for step in enable_steps if step.get("name") == "verify-enabled-real-integration"), {})
    _need(enable_detail.get("transportStatus") == "verified", "Packaged MCP transport was not verified")
    _need(enable_detail.get("trustedInteractionErrorObserved") is False, "Trusted interaction error was observed")

    initial_inventory = initial.get("inventory") or {}
    initial_resource = initial_inventory.get("resource") or {}
    _need(initial.get("requiredToolsDiscovered") is True and int(initial.get("toolCount", 0)) >= 28, "Packaged MCP tool discovery failed")
    _need((initial.get("status") or {}).get("approvalMode") == "desktop-only", "Approval mode is not desktop-only")
    _need((initial.get("status") or {}).get("approvalToolsExposed") is False, "Approval tools were exposed through MCP")
    _need((initial_inventory.get("registryHealth") or {}).get("status") == "healthy", "Initial Registry is not healthy")
    _need(initial_inventory.get("remoteAvailable") is True and not initial_inventory.get("remoteWarning"), "Initial remote is unavailable or warned")
    _need(initial_resource.get("status") == "local-only", "Initial Skill is not local-only")
    _need("upload" in (initial_resource.get("availableActions") or []), "Upload is not available")

    initial_commit = github.get("initialCommit")
    _need((initial_inventory.get("registryHealth") or {}).get("checkedCommit") == initial_commit, "Initial Registry audit used another commit")
    upload_plan = plan_upload.get("plan") or {}
    _need(upload_plan.get("action") == "upload" and upload_plan.get("blocked") is False, "Upload plan is blocked or wrong")
    _need(upload_plan.get("resourceKey") == "skill:cc-port-e2e-skill" and upload_plan.get("profileId") == "package-test", "Upload plan scope is wrong")
    _need(upload_plan.get("localInstanceId") == initial_resource.get("localInstanceId"), "Upload plan did not bind the inventory instance")
    _need(SHA256_RE.fullmatch(str(upload_plan.get("planHash", ""))), "Upload plan hash is malformed")
    _need(not upload_plan.get("warnings"), "Upload plan has unresolved warnings")
    _need(upload_plan.get("remoteCommit") == initial_commit, "Upload plan is not bound to the initial commit")
    _need(upload_plan.get("requiresApproval") is True and upload_plan.get("approvalStatus") == "pending", "Upload approval is not pending")
    _need(ui_upload.get("success") is True and ui_upload.get("expectedOperationId") == upload_plan.get("operationId"), "Upload UI approved the wrong operation")
    _need(ui_upload.get("testId") == context.get("testId"), "Upload UI used another session")
    _need(_approval_dialog_matched(ui_upload, upload_plan.get("operationId")), "Upload dialog did not prove the exact operation")

    uploaded = apply_upload.get("apply") or {}
    _need(uploaded.get("operationId") == upload_plan.get("operationId"), "Upload operation id mismatch")
    _need(uploaded.get("planHash") == upload_plan.get("planHash"), "Upload plan hash mismatch")
    _need(uploaded.get("approvalId") == upload_plan.get("approvalId"), "Upload approval id mismatch")
    _need(uploaded.get("status") == "succeeded" and uploaded.get("approvalStatus") == "consumed", "Upload did not succeed with a consumed approval")
    _need(not uploaded.get("warnings"), "Upload completed with unresolved warnings")
    upload_commit = uploaded.get("remoteCommit")
    _need(isinstance(upload_commit, str) and upload_commit and upload_commit != initial_commit, "Upload did not create a new remote commit")
    upload_verified_inventory = apply_upload.get("verificationInventory") or {}
    _need((upload_verified_inventory.get("resource") or {}).get("status") == "same", "Post-upload state is not same")
    _need(upload_verified_inventory.get("remoteCommit") == upload_commit, "Post-upload inventory used another commit")
    _need(
        (upload_verified_inventory.get("registryHealth") or {}).get("status") == "healthy"
        and (upload_verified_inventory.get("registryHealth") or {}).get("checkedCommit") == upload_commit,
        "Post-upload Registry is not healthy at the upload commit",
    )

    _need(remote.get("head") == upload_commit, "Independent clone HEAD differs from upload commit")
    _need(remote.get("clean") is True, "Independent clone is dirty")
    _need(set(remote.get("changedPaths") or []) == EXPECTED_REMOTE_PATHS, "Upload commit changed unexpected paths")
    _need(remote.get("registryVersion") == 1 and remote.get("registryResourceCount") == 1, "Remote Registry proof is invalid")
    _need(remote.get("registryResourceKey") == "skill:cc-port-e2e-skill", "Remote Registry resource key is wrong")
    _need(remote.get("bytesMatchOriginalFixture") is True, "Remote bytes do not match original fixture")
    _need(str(remote.get("skillSha256", "")).lower() == str(context.get("fixtureSkillSha256", "")).lower(), "Remote Skill hash differs from session fixture")
    _need(str(remote.get("proofSha256", "")).lower() == str(context.get("fixtureProofSha256", "")).lower(), "Remote proof hash differs from session fixture")

    _need(prepare.get("fixtureMovedOutOfProfile") is True and prepare.get("profileTargetExists") is False, "Local removal setup failed")
    _need(prepare.get("backupExists") is True, "Local fixture backup is missing")
    download_inventory = plan_download.get("inventory") or {}
    download_resource = download_inventory.get("resource") or {}
    _need(download_inventory.get("remoteCommit") == upload_commit, "Download inventory used another remote commit")
    _need(
        (download_inventory.get("registryHealth") or {}).get("status") == "healthy"
        and (download_inventory.get("registryHealth") or {}).get("checkedCommit") == upload_commit,
        "Download Registry is not healthy at the upload commit",
    )
    _need(download_resource.get("status") == "remote-only" and download_resource.get("localStatus") == "missing", "Download precondition is not remote-only/missing")
    _need("download" in (download_resource.get("availableActions") or []), "Download is not available")
    download_plan = plan_download.get("plan") or {}
    _need(download_plan.get("action") == "download" and download_plan.get("blocked") is False, "Download plan is blocked or wrong")
    _need(download_plan.get("resourceKey") == "skill:cc-port-e2e-skill" and download_plan.get("profileId") == "package-test", "Download plan scope is wrong")
    _need(SHA256_RE.fullmatch(str(download_plan.get("planHash", ""))), "Download plan hash is malformed")
    _need(not download_plan.get("warnings"), "Download plan has unresolved warnings")
    _need(download_plan.get("remoteCommit") == upload_commit, "Download plan is not bound to upload commit")
    _need(download_plan.get("requiresApproval") is True and download_plan.get("approvalStatus") == "pending", "Download approval is not pending")
    _need(ui_download.get("success") is True and ui_download.get("expectedOperationId") == download_plan.get("operationId"), "Download UI approved the wrong operation")
    _need(ui_download.get("testId") == context.get("testId"), "Download UI used another session")
    _need(_approval_dialog_matched(ui_download, download_plan.get("operationId")), "Download dialog did not prove the exact operation")

    downloaded = apply_download.get("apply") or {}
    _need(downloaded.get("operationId") == download_plan.get("operationId"), "Download operation id mismatch")
    _need(downloaded.get("planHash") == download_plan.get("planHash"), "Download plan hash mismatch")
    _need(downloaded.get("approvalId") == download_plan.get("approvalId"), "Download approval id mismatch")
    _need(downloaded.get("status") == "succeeded" and downloaded.get("approvalStatus") == "consumed", "Download did not succeed with a consumed approval")
    _need(not downloaded.get("warnings"), "Download completed with unresolved warnings")
    _need(downloaded.get("remoteCommit") == upload_commit, "Download changed or used another remote commit")
    verified_download = (apply_download.get("verificationInventory") or {}).get("resource") or {}
    _need(verified_download.get("status") == "same" and verified_download.get("ownership") == "managed", "Downloaded Skill is not same/managed")
    download_verified_inventory = apply_download.get("verificationInventory") or {}
    _need(download_verified_inventory.get("remoteCommit") == upload_commit, "Post-download inventory used another commit")
    _need(
        (download_verified_inventory.get("registryHealth") or {}).get("status") == "healthy"
        and (download_verified_inventory.get("registryHealth") or {}).get("checkedCommit") == upload_commit,
        "Post-download Registry is not healthy at the upload commit",
    )
    _need(files.get("matchesOriginalFixture") is True, "Downloaded bytes do not match original fixture")
    _need(str(files.get("skillSha256", "")).lower() == str(remote.get("skillSha256", "")).lower(), "Skill hash differs across remote/download")
    _need(str(files.get("proofSha256", "")).lower() == str(remote.get("proofSha256", "")).lower(), "Reference hash differs across remote/download")

    _need(ui_uninstall.get("success") is True, "AI integration uninstall UI failed")
    _need(ui_uninstall.get("testId") == context.get("testId"), "Uninstall UI used another session")
    uninstall_detail = next((step.get("detail") for step in (ui_uninstall.get("steps") or []) if step.get("name") == "verify-scoped-integration-uninstall"), {})
    _need(uninstall_detail.get("fixturePreserved") is True and uninstall_detail.get("managedMcpEntryRemoved") is True, "AI integration uninstall scope is wrong")
    _need(cleanup.get("ready") is True and cleanup.get("testId") == context.get("testId"), "Session was not ready or cleanup used another session")
    _need(cleanup.get("uninstallEntriesRemaining") == 0, "CC Port uninstall entries remain")
    _need(cleanup.get("testRootRemoved") is True and not cleanup.get("cleanupErrors"), "Windows test-root cleanup failed")

    _need(final_remote_head.lower() == str(upload_commit).lower(), "Final remote HEAD differs from product upload commit")
    source = preflight.get("source") or {}
    current_head = _git(repo_root, "rev-parse", "HEAD")
    current_origin_main = _git(repo_root, "rev-parse", "origin/main")
    current_status_text = _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    current_status = current_status_text.splitlines() if current_status_text else []
    current_staged_text = _git(repo_root, "diff", "--cached", "--name-only")
    current_staged = current_staged_text.splitlines() if current_staged_text else []
    current_unstaged_diff = hashlib.sha256(
        _git_bytes(repo_root, "diff", "--binary", "--no-ext-diff")
    ).hexdigest()
    current_staged_diff = hashlib.sha256(
        _git_bytes(repo_root, "diff", "--cached", "--binary", "--no-ext-diff")
    ).hexdigest()
    current_untracked = _untracked_hashes(repo_root)
    _need(current_head == source.get("head"), "Source HEAD changed since preflight")
    _need(current_origin_main == source.get("origin_main"), "Source origin/main changed since preflight")
    _need(current_status == (source.get("status_porcelain") or []), "Source worktree changed since preflight")
    _need(current_staged == (source.get("staged_paths") or []), "Source index changed since preflight")
    _need(current_unstaged_diff == source.get("unstaged_diff_sha256"), "Source unstaged diff content changed since preflight")
    _need(current_staged_diff == source.get("staged_diff_sha256"), "Source staged diff content changed since preflight")
    _need(current_untracked == (source.get("untracked_file_sha256") or {}), "Source untracked content changed since preflight")

    return {
        "schema_version": 1,
        "status": "PASS",
        "certified_scope": "Windows packaged single-Skill upload and download/install through MCP plus desktop approval",
        "repository": github.get("htmlUrl"),
        "private": True,
        "initial_commit": initial_commit,
        "upload_commit": upload_commit,
        "final_remote_head": final_remote_head,
        "resource_key": "skill:cc-port-e2e-skill",
        "profile_id": "package-test",
        "installer_sha256": installer.get("sha256"),
        "skill_sha256": files.get("skillSha256"),
        "proof_sha256": files.get("proofSha256"),
        "approval_mode": "desktop-only",
        "upload_approval_status": uploaded.get("approvalStatus"),
        "download_approval_status": downloaded.get("approvalStatus"),
        "cleanup": "complete",
        "untested": [
            "other resource kinds",
            "batch UI",
            "conflict and overwrite paths",
            "links",
            "WSL profiles",
            "Marketplace plugins",
            "concurrent races",
            "credential-expiry and network recovery",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--final-remote-head", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        evidence_dir = args.evidence_dir.resolve(strict=True)
        if args.output:
            output = args.output.resolve()
            if output.parent != evidence_dir:
                raise EvidenceError("Validation output must stay inside the evidence directory")
            if output.exists():
                raise EvidenceError("Refusing to overwrite an existing validation summary")
            args.output = output
        summary = validate(evidence_dir, args.repo_root.resolve(strict=True), args.final_remote_head)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    rendered = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
