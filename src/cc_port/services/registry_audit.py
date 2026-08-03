"""Audit and explicitly repair the portable registry in a remote resource repository."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from ..core.config import Config, load_config, resource_repo_auth_token
from ..core.models import Registry, RegistryResource
from ..core.registry import (
    DEFAULT_REGISTRY_FILENAME,
    canonical_registry_text,
    save_registry,
)
from ..core.resource_files import is_resource_path_excluded
from ..core.secret_scan import find_secret_text, redact_secret_text
from ..core.validator import validate_item
from ..infrastructure import git_ops
from .local_path_probe import resource_tree_issues
from .local_transaction import resource_hash_path
from .ui_messages import UiMessageRef, ui_message

RegistryStatus = Literal[
    "healthy",
    "issues",
    "legacy",
    "missing",
    "invalid",
    "unavailable",
]

KNOWN_KINDS = {"skill", "mcp", "rule", "prompt", "plugin"}
RESOURCE_ROOTS = {
    "skills": "skill",
    "mcp": "mcp",
    "rules": "rule",
    "prompts": "prompt",
    "plugins": "plugin",
}


@dataclass(frozen=True)
class RegistryRepairChoice:
    issue_id: str
    action: str
    name: str = ""


@dataclass
class RegistryAuditIssue:
    id: str
    code: str
    severity: str
    message: str
    message_ref: UiMessageRef | None = None
    resource_key: str = ""
    kind: str = ""
    name: str = ""
    path: str = ""
    default_action: str = "keep"
    actions: list[str] = field(default_factory=list)
    blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistryHealthSummary:
    status: RegistryStatus
    checked_commit: str
    issue_count: int
    repairable_count: int
    blocked_count: int
    message: str


@dataclass
class RegistryRepairPlan:
    remote_commit: str
    repo_url: str
    branch: str
    registry_status: RegistryStatus
    issues: list[RegistryAuditIssue]
    choices: list[RegistryRepairChoice]
    registry_diff: str
    plan_hash: str
    executable_count: int
    blocked_count: int
    repairable: bool
    original_registry_hash: str = ""
    candidate_fingerprints: dict[str, str] = field(default_factory=dict)
    resulting_registry_text: str = ""
    legacy_item_count: int = 0
    rebuilt_item_count: int = 0
    dropped_item_count: int = 0

    @property
    def health(self) -> RegistryHealthSummary:
        messages = {
            "healthy": "registry.yaml matches the current repository tree.",
            "issues": "registry.yaml has repository consistency issues.",
            "legacy": "registry.yaml uses legacy v7 and can be replaced from current content.",
            "missing": "registry.yaml is missing and must be repaired manually.",
            "invalid": "registry.yaml is invalid and must be repaired manually.",
            "unavailable": "The remote repository is unavailable.",
        }
        return RegistryHealthSummary(
            status=self.registry_status,
            checked_commit=self.remote_commit,
            issue_count=len(self.issues),
            repairable_count=self.executable_count,
            blocked_count=self.blocked_count,
            message=messages[self.registry_status],
        )


@dataclass
class RegistryRepairResult:
    status: str
    plan_hash: str
    remote_commit: str = ""
    message: str = ""
    stale_plan: RegistryRepairPlan | None = None


@dataclass
class _RegistryDocument:
    status: RegistryStatus
    text: str
    raw: dict[str, Any] | None
    resources: list[RegistryResource]
    issues: list[RegistryAuditIssue]
    repairable: bool
    legacy_item_count: int = 0
    legacy_resource_keys: list[str] = field(default_factory=list)
    resource_indices: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class _Candidate:
    resource: RegistryResource
    fingerprint: str
    discovered_name: str


def audit_registry_root(
    root: Path,
    *,
    remote_commit: str = "",
    repo_url: str = "",
    branch: str = "main",
    choices: list[RegistryRepairChoice] | None = None,
) -> RegistryRepairPlan:
    """Audit one immutable repository tree without modifying it."""
    root = root.absolute()
    document = _read_registry_document(root)
    if document.status in {"missing", "invalid"}:
        return _unrepairable_plan(
            document,
            remote_commit=remote_commit,
            repo_url=repo_url,
            branch=branch,
        )

    candidates, candidate_issues = _discover_candidates(root)
    issues = [*document.issues, *candidate_issues]
    selected = {choice.issue_id: choice for choice in choices or []}
    resources = list(document.resources)
    resource_indices = list(document.resource_indices)

    dropped_indices: set[int] = set()
    for issue in document.issues:
        choice = selected.get(issue.id)
        if issue.code in {"invalid-resource-entry", "invalid-source", "unsafe-path"}:
            if choice is not None and choice.action == "remove":
                issue.blocking = False
            continue
        if issue.code not in {"duplicate-key", "duplicate-path"}:
            continue
        indexes = [int(value) for value in issue.details.get("indexes", [])]
        if choice is None or choice.action != "select-entry":
            continue
        selected_index = _selected_duplicate_index(
            choice.name,
            issue.details.get("entries", []),
        )
        if selected_index not in indexes:
            issue.details["choice_error"] = "Select exactly one listed registry entry."
            continue
        dropped_indices.update(index for index in indexes if index != selected_index)
        issue.blocking = False

    if dropped_indices:
        resources = [
            resource
            for index, resource in zip(resource_indices, resources, strict=True)
            if index not in dropped_indices
        ]
        resource_indices = [
            index for index in resource_indices if index not in dropped_indices
        ]
    resource_indexes = {resource.resource_key: index for index, resource in enumerate(resources)}
    path_indexes = {
        resource.path: resource.resource_key for resource in resources if resource.path
    }
    candidate_fingerprints = {
        candidate.resource.path: candidate.fingerprint for candidate in candidates
    }

    if document.status == "legacy":
        resources = []
        resource_indexes = {}
        path_indexes = {}

    invalid_resource_keys: set[str] = set()
    for resource in list(resources):
        if not resource.path:
            continue
        problem = _registered_path_problem(root, resource)
        if problem is None:
            continue
        code, message, severity = problem
        issue = _issue(
            code,
            message,
            resource_key=resource.resource_key,
            kind=resource.kind,
            name=resource.name,
            path=resource.path,
            default_action="remove" if code == "missing-resource" else "keep",
            actions=["keep", "remove"],
            blocking=code in {"unsafe-path", "unsafe-link"},
            severity=severity,
        )
        issues.append(issue)
        choice = selected.get(issue.id)
        action = choice.action if choice else issue.default_action
        if action == "remove":
            issue.blocking = False
            invalid_resource_keys.add(resource.resource_key)

    if invalid_resource_keys:
        resources = [
            resource for resource in resources if resource.resource_key not in invalid_resource_keys
        ]
        resource_indexes = {
            resource.resource_key: index for index, resource in enumerate(resources)
        }
        path_indexes = {
            resource.path: resource.resource_key for resource in resources if resource.path
        }

    for candidate in candidates:
        resource = candidate.resource
        if resource.path in path_indexes:
            continue
        discovered_name = candidate.discovered_name
        issue_code = (
            "unregistered-resource" if discovered_name else "invalid-resource-name"
        )
        message = (
            f"Valid {resource.kind} content at {resource.path} is not registered."
            if discovered_name
            else f"Valid {resource.kind} content at {resource.path} needs a safe resource name."
        )
        issue = _issue(
            issue_code,
            message,
            resource_key=resource.resource_key if discovered_name else "",
            kind=resource.kind,
            name=discovered_name,
            path=resource.path,
            default_action="add",
            actions=["add", "keep"],
            severity="warning",
        )
        issues.append(issue)
        choice = selected.get(issue.id)
        action = choice.action if choice else issue.default_action
        if action != "add":
            continue
        proposed_name = (
            choice.name.strip() if choice and choice.name else discovered_name
        )
        try:
            proposed = resource.model_copy(update={"name": proposed_name})
            proposed = RegistryResource.model_validate(proposed.model_dump(mode="json"))
        except ValidationError as exc:
            issue.blocking = True
            issue.details["name_error"] = str(exc)
            continue
        if proposed.resource_key in resource_indexes or proposed.path in path_indexes:
            issue.blocking = True
            issue.details["conflict"] = "The proposed identity or path already exists."
            continue
        resources.append(proposed)
        resource_indexes[proposed.resource_key] = len(resources) - 1
        path_indexes[proposed.path] = proposed.resource_key

    for issue in issues:
        choice = selected.get(issue.id)
        if choice is not None and issue.actions and choice.action not in issue.actions:
            issue.blocking = True
            issue.details["choice_error"] = (
                f"Unsupported action {choice.action!r}; choose one of {issue.actions}."
            )

    structural_blockers = [issue for issue in issues if issue.blocking]
    if structural_blockers:
        resulting_text = document.text
    else:
        try:
            resulting_registry = Registry(resources=resources)
            resulting_text = canonical_registry_text(resulting_registry)
        except ValidationError as exc:
            issue = _issue(
                "invalid-result",
                "The selected repairs do not produce a valid registry.",
                severity="error",
                blocking=True,
                details=_validation_error_details(exc),
            )
            issues.append(issue)
            structural_blockers.append(issue)
            resulting_text = document.text

    if document.status == "legacy":
        legacy_issue = next(issue for issue in issues if issue.code == "legacy-v7")
        choice = selected.get(legacy_issue.id)
        action = choice.action if choice else legacy_issue.default_action
        if action != "replace":
            resulting_text = document.text
    elif resulting_text != document.text:
        effective_actions = {
            (selected.get(issue.id).action if selected.get(issue.id) else issue.default_action)
            for issue in issues
        }
        if not effective_actions.intersection({"add", "remove", "select-entry"}):
            normalize_issue = _issue(
                "noncanonical-registry",
                "registry.yaml is valid but does not use canonical ordering or serialization.",
                severity="warning",
                default_action="normalize",
                actions=["normalize", "keep"],
            )
            issues.append(normalize_issue)
            choice = selected.get(normalize_issue.id)
            if choice is not None and choice.action not in normalize_issue.actions:
                normalize_issue.blocking = True
                normalize_issue.details["choice_error"] = (
                    f"Unsupported action {choice.action!r}; choose one of "
                    f"{normalize_issue.actions}."
                )
                structural_blockers.append(normalize_issue)
            if choice is not None and choice.action == "keep":
                resulting_text = document.text

    original_text = document.text
    display_original_text = (
        redact_secret_text(original_text)
        if any(issue.code == "invalid-source" for issue in issues)
        else original_text
    )
    display_resulting_text = (
        redact_secret_text(resulting_text)
        if any(issue.code == "invalid-source" for issue in issues)
        else resulting_text
    )
    registry_diff = "".join(
        difflib.unified_diff(
            display_original_text.splitlines(keepends=True),
            display_resulting_text.splitlines(keepends=True),
            fromfile="registry.yaml (current)",
            tofile="registry.yaml (proposed)",
        )
    )
    effective_choices = _effective_choices(issues, selected)
    executable_count = sum(
        1
        for choice in effective_choices
        if choice.action in {"add", "remove", "replace", "normalize", "select-entry"}
    )
    if not registry_diff:
        executable_count = 0
    blocked_count = len(structural_blockers)
    original_hash = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
    plan_hash = _plan_hash(
        remote_commit=remote_commit,
        original_hash=original_hash,
        candidate_fingerprints=candidate_fingerprints,
        choices=effective_choices,
        resulting_text=resulting_text,
        issues=issues,
    )
    status: RegistryStatus = document.status
    if status != "legacy":
        status = "issues" if issues or registry_diff else "healthy"
    rebuilt = len(resources) if document.status == "legacy" else 0
    rebuilt_keys = {resource.resource_key for resource in resources}
    dropped = (
        sum(key not in rebuilt_keys for key in document.legacy_resource_keys)
        if document.status == "legacy"
        else 0
    )
    public_resulting_text = (
        ""
        if any(issue.code == "invalid-source" and issue.blocking for issue in issues)
        else resulting_text
    )
    return RegistryRepairPlan(
        remote_commit=remote_commit,
        repo_url=repo_url,
        branch=branch,
        registry_status=status,
        issues=issues,
        choices=effective_choices,
        registry_diff=registry_diff,
        plan_hash=plan_hash,
        executable_count=executable_count,
        blocked_count=blocked_count,
        repairable=document.repairable,
        original_registry_hash=original_hash,
        candidate_fingerprints=candidate_fingerprints,
        resulting_registry_text=public_resulting_text,
        legacy_item_count=document.legacy_item_count,
        rebuilt_item_count=rebuilt,
        dropped_item_count=dropped,
    )


def build_registry_repair_plan(
    *,
    config: Config | None = None,
    choices: list[RegistryRepairChoice] | None = None,
) -> RegistryRepairPlan:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    repo_url = _configured_remote_url(cfg)
    branch = cfg.resources.branch or "main"
    if not repo_url:
        document = _RegistryDocument(
            status="unavailable",
            text="",
            raw=None,
            resources=[],
            issues=[
                _issue(
                    "remote-unavailable",
                    "No remote resource repository URL is configured.",
                    severity="error",
                    blocking=True,
                )
            ],
            repairable=False,
        )
        return _unrepairable_plan(document, branch=branch)
    with tempfile.TemporaryDirectory(prefix="cc-port-registry-check-") as temporary:
        clone = Path(temporary) / "repo"
        _clone_remote(repo_url, clone, cfg)
        commit = git_ops.head_commit(clone) or ""
        return audit_registry_root(
            clone,
            remote_commit=commit,
            repo_url=repo_url,
            branch=branch,
            choices=choices,
        )


def apply_registry_repair(
    *,
    expected_plan_hash: str,
    config: Config | None = None,
    choices: list[RegistryRepairChoice] | None = None,
) -> RegistryRepairResult:
    cfg = config or load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    repo_url = _configured_remote_url(cfg)
    branch = cfg.resources.branch or "main"
    if not repo_url:
        return RegistryRepairResult(
            status="blocked",
            plan_hash=expected_plan_hash,
            message="No remote resource repository URL is configured.",
        )
    with tempfile.TemporaryDirectory(prefix="cc-port-registry-repair-") as temporary:
        clone = Path(temporary) / "repo"
        _clone_remote(repo_url, clone, cfg)
        current = audit_registry_root(
            clone,
            remote_commit=git_ops.head_commit(clone) or "",
            repo_url=repo_url,
            branch=branch,
            choices=choices,
        )
        if current.plan_hash != expected_plan_hash:
            return RegistryRepairResult(
                status="stale",
                plan_hash=current.plan_hash,
                message="The remote registry or repository tree changed after planning.",
                stale_plan=current,
            )
        if not current.repairable or current.blocked_count:
            return RegistryRepairResult(
                status="blocked",
                plan_hash=current.plan_hash,
                message="The registry repair plan has unresolved blockers.",
            )
        if not current.registry_diff or current.executable_count == 0:
            return RegistryRepairResult(
                status="unchanged",
                plan_hash=current.plan_hash,
                remote_commit=current.remote_commit,
                message="registry.yaml already matches the selected state.",
            )
        secret = find_secret_text(current.resulting_registry_text)
        if secret is not None:
            return RegistryRepairResult(
                status="blocked",
                plan_hash=current.plan_hash,
                message=f"The proposed registry contains secret-like content: {secret.reason}",
            )
        proposed_data = yaml.safe_load(current.resulting_registry_text)
        registry = Registry.model_validate(proposed_data)
        save_registry(
            registry,
            clone / DEFAULT_REGISTRY_FILENAME,
            save_cc_port_overlay=False,
        )
        changed = git_ops.status_entries(clone)
        if any(
            entry.path != DEFAULT_REGISTRY_FILENAME
            or (entry.original_path and entry.original_path != DEFAULT_REGISTRY_FILENAME)
            for entry in changed
        ):
            return RegistryRepairResult(
                status="blocked",
                plan_hash=current.plan_hash,
                message="Registry repair detected unrelated worktree changes.",
            )
        git_ops.add_paths(clone, [DEFAULT_REGISTRY_FILENAME])
        git_ops.commit(clone, message="修复资源索引")
        committed = git_ops.head_commit(clone) or ""
        try:
            git_ops.push(
                clone,
                branch=branch,
                token=resource_repo_auth_token(cfg),
            )
        except git_ops.GitError as exc:
            stale = build_registry_repair_plan(config=cfg, choices=choices)
            return RegistryRepairResult(
                status="stale" if stale.plan_hash != current.plan_hash else "failed",
                plan_hash=stale.plan_hash,
                message=str(exc),
                stale_plan=stale if stale.plan_hash != current.plan_hash else None,
            )
        return RegistryRepairResult(
            status="succeeded",
            plan_hash=current.plan_hash,
            remote_commit=committed,
            message="registry.yaml was repaired and pushed.",
        )


def _read_registry_document(root: Path) -> _RegistryDocument:
    path = root / DEFAULT_REGISTRY_FILENAME
    if path.is_symlink():
        return _RegistryDocument(
            status="invalid",
            text="",
            raw=None,
            resources=[],
            issues=[
                _issue(
                    "registry-symlink",
                    "registry.yaml must be a regular non-symlink file.",
                    severity="error",
                    path=DEFAULT_REGISTRY_FILENAME,
                    blocking=True,
                )
            ],
            repairable=False,
        )
    if path.exists() and not path.is_file():
        return _RegistryDocument(
            status="invalid",
            text="",
            raw=None,
            resources=[],
            issues=[
                _issue(
                    "invalid-yaml",
                    "registry.yaml must be a regular file.",
                    severity="error",
                    path=DEFAULT_REGISTRY_FILENAME,
                    blocking=True,
                )
            ],
            repairable=False,
        )
    if not path.is_file():
        return _RegistryDocument(
            status="missing",
            text="",
            raw=None,
            resources=[],
            issues=[
                _issue(
                    "missing-registry",
                    "registry.yaml is missing and automatic repair is disabled.",
                    severity="error",
                    path=DEFAULT_REGISTRY_FILENAME,
                    blocking=True,
                )
            ],
            repairable=False,
        )
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return _RegistryDocument(
            status="invalid",
            text="",
            raw=None,
            resources=[],
            issues=[
                _issue(
                    "invalid-yaml",
                    f"registry.yaml cannot be parsed: {redact_secret_text(str(exc))}",
                    severity="error",
                    path=DEFAULT_REGISTRY_FILENAME,
                    blocking=True,
                )
            ],
            repairable=False,
        )
    if not isinstance(data, dict):
        return _invalid_document(text, "registry.yaml root must be a mapping.")
    version = data.get("version")
    if version == 7 and isinstance(data.get("items"), list):
        legacy_resource_keys = [
            f"{str(item.get('kind') or 'skill').strip().lower()}:{str(item.get('name') or '').strip()}"
            if isinstance(item, dict) and str(item.get("name") or "").strip()
            else f"__invalid__:{index}"
            for index, item in enumerate(data["items"])
        ]
        issue = _issue(
            "legacy-v7",
            "Legacy registry v7 can be replaced from current repository content; "
            "reference-only entries and CC Port settings will be discarded.",
            severity="warning",
            default_action="replace",
            actions=["replace", "keep"],
            details={"legacy_item_count": len(data["items"])},
        )
        return _RegistryDocument(
            status="legacy",
            text=text,
            raw=data,
            resources=[],
            issues=[issue],
            repairable=True,
            legacy_item_count=len(data["items"]),
            legacy_resource_keys=legacy_resource_keys,
        )
    if version != 1 or not isinstance(data.get("resources"), list):
        return _invalid_document(
            text,
            "registry.yaml must use version 1 with a resources list.",
        )
    unknown_top = set(data) - {"version", "resources"}
    if unknown_top:
        return _invalid_document(
            text,
            "registry.yaml contains unsupported top-level fields: "
            + ", ".join(sorted(str(item) for item in unknown_top)),
        )
    resources: list[RegistryResource] = []
    resource_indices: list[int] = []
    issues: list[RegistryAuditIssue] = []
    key_groups: dict[str, list[tuple[int, RegistryResource]]] = {}
    path_groups: dict[str, list[tuple[int, RegistryResource]]] = {}
    for index, raw in enumerate(data["resources"]):
        secret = find_secret_text(yaml.safe_dump(raw, allow_unicode=True))
        if secret is not None:
            issues.append(
                _issue(
                    "invalid-source",
                    "registry source data contains credential-like content and cannot be preserved safely.",
                    severity="error",
                    default_action="keep",
                    actions=["keep", "remove"],
                    blocking=True,
                    details={"index": index, "reason": secret.reason},
                )
            )
            continue
        try:
            resource = RegistryResource.model_validate(raw)
        except ValidationError as exc:
            code = (
                "invalid-source"
                if isinstance(raw, dict) and "source" in raw
                else "unsafe-path"
                if _raw_path_is_unsafe(raw)
                else "invalid-resource-entry"
            )
            issues.append(
                _issue(
                    code,
                    f"registry resource at index {index} is invalid.",
                    severity="error",
                    default_action="keep",
                    actions=["keep", "remove"],
                    blocking=True,
                    details={"index": index, **_validation_error_details(exc)},
                )
            )
            continue
        resources.append(resource)
        resource_indices.append(index)
        key_groups.setdefault(resource.resource_key, []).append((index, resource))
        if resource.path:
            path_groups.setdefault(resource.path, []).append((index, resource))
    for resource_key, entries in key_groups.items():
        if len(entries) <= 1:
            continue
        issues.append(
            _duplicate_issue(
                "duplicate-key",
                f"Multiple registry entries use identity {resource_key}.",
                entries,
                resource_key=resource_key,
            )
        )
    for path, entries in path_groups.items():
        if len(entries) <= 1:
            continue
        issues.append(
            _duplicate_issue(
                "duplicate-path",
                f"Multiple registry entries use path {path}.",
                entries,
                path=path,
            )
        )
    return _RegistryDocument(
        status="issues" if issues else "healthy",
        text=text,
        raw=data,
        resources=resources,
        issues=issues,
        repairable=True,
        resource_indices=resource_indices,
    )


def _invalid_document(text: str, message: str) -> _RegistryDocument:
    return _RegistryDocument(
        status="invalid",
        text=text,
        raw=None,
        resources=[],
        issues=[
            _issue(
                "invalid-yaml",
                message,
                severity="error",
                path=DEFAULT_REGISTRY_FILENAME,
                blocking=True,
            )
        ],
        repairable=False,
    )


def _discover_candidates(root: Path) -> tuple[list[_Candidate], list[RegistryAuditIssue]]:
    candidates: list[_Candidate] = []
    issues: list[RegistryAuditIssue] = []
    for directory, kind in RESOURCE_ROOTS.items():
        parent = root / directory
        if parent.is_symlink():
            issues.append(
                _issue(
                    "unsafe-link",
                    f"Resource root {directory} is a symbolic link.",
                    severity="error",
                    path=directory,
                    blocking=True,
                )
            )
            continue
        if not parent.is_dir():
            continue
        try:
            children = sorted(parent.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            issues.append(
                _issue(
                    "unreadable-resource-root",
                    f"Resource root {directory} cannot be read: {exc}",
                    severity="error",
                    path=directory,
                    blocking=True,
                )
            )
            continue
        for child in children:
            relative = child.relative_to(root).as_posix()
            if is_resource_path_excluded(Path(relative)):
                continue
            name = _slug(child.stem if child.is_file() else child.name)
            if child.is_symlink():
                issues.append(
                    _issue(
                        "unsafe-link",
                        f"Candidate resource {relative} is a symbolic link.",
                        severity="error",
                        kind=kind,
                        name=name,
                        path=relative,
                        blocking=True,
                    )
                )
                continue
            tree_problems = resource_tree_issues(child) if child.is_dir() else []
            if tree_problems:
                issues.append(
                    _issue(
                        "unsafe-link",
                        f"Candidate resource {relative} contains unsafe or unreadable entries.",
                        severity="error",
                        kind=kind,
                        name=name,
                        path=relative,
                        blocking=True,
                        details={
                            "problems": [
                                f"{problem.relative_path}: {problem.detail}"
                                for problem in tree_problems
                            ]
                        },
                    )
                )
                continue
            try:
                validate_item(child, kind)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 - audit reports per candidate
                issues.append(
                    _issue(
                        "invalid-resource",
                        f"Candidate resource {relative} is invalid: {exc}",
                        severity="warning",
                        kind=kind,
                        name=name,
                        path=relative,
                    )
                )
                continue
            try:
                fingerprint = resource_hash_path(child)
            except Exception as exc:  # noqa: BLE001
                issues.append(
                    _issue(
                        "unreadable-resource",
                        f"Candidate resource {relative} cannot be fingerprinted: {exc}",
                        severity="error",
                        kind=kind,
                        name=name,
                        path=relative,
                        blocking=True,
                    )
                )
                continue
            resource = RegistryResource(
                kind=kind,
                name=name or "unnamed",
                path=relative,
            )
            candidates.append(
                _Candidate(
                    resource=resource,
                    fingerprint=fingerprint,
                    discovered_name=name,
                )
            )
    return candidates, issues


def _registered_path_problem(
    root: Path,
    resource: RegistryResource,
) -> tuple[str, str, str] | None:
    if is_resource_path_excluded(Path(resource.path)):
        return (
            "unsafe-path",
            f"Registered path {resource.path} is excluded by the resource file policy.",
            "error",
        )
    member = _safe_member(root, resource.path)
    if member is None:
        return "unsafe-path", f"Registered path {resource.path} is unsafe.", "error"
    if not member.exists() and not member.is_symlink():
        return "missing-resource", f"Registered path {resource.path} does not exist.", "warning"
    if member.is_symlink():
        return "unsafe-link", f"Registered path {resource.path} is a symbolic link.", "error"
    if member.is_dir():
        problems = resource_tree_issues(member)
        if problems:
            return (
                "unsafe-link",
                f"Registered path {resource.path} contains unsafe or unreadable entries.",
                "error",
            )
    if resource.kind not in KNOWN_KINDS:
        return None
    try:
        validate_item(member, resource.kind)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return "invalid-resource", f"Registered resource {resource.resource_key} is invalid: {exc}", "warning"
    return None


def _safe_member(root: Path, relative: str) -> Path | None:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            return current
    return current


def _validation_error_details(exc: ValidationError) -> dict[str, Any]:
    return {
        "errors": [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "type": str(error.get("type") or "validation_error"),
                "message": str(error.get("msg") or "Invalid value."),
            }
            for error in exc.errors(include_input=False, include_url=False)
        ]
    }


def _raw_path_is_unsafe(raw: Any) -> bool:
    if not isinstance(raw, dict) or "path" not in raw:
        return False
    value = raw.get("path")
    if not isinstance(value, str):
        return True
    path = value.strip()
    if not path or "\\" in path or path.startswith("/") or path.endswith("/"):
        return True
    if re.match(r"^[a-zA-Z]:", path):
        return True
    return any(part in {"", ".", ".."} for part in path.split("/"))


def _duplicate_issue(
    code: str,
    message: str,
    entries: list[tuple[int, RegistryResource]],
    *,
    resource_key: str = "",
    path: str = "",
) -> RegistryAuditIssue:
    details = {
        "indexes": [index for index, _resource in entries],
        "entries": [
            {
                "index": index,
                "resource_key": resource.resource_key,
                "path": resource.path,
                "source_type": resource.source.type if resource.source else "",
            }
            for index, resource in entries
        ],
    }
    return _issue(
        code,
        message,
        severity="error",
        resource_key=resource_key,
        path=path,
        default_action="keep",
        actions=["select-entry"],
        blocking=True,
        details=details,
    )


def _selected_duplicate_index(value: str, entries: Any) -> int | None:
    selector = (value or "").strip()
    if selector.isdigit():
        return int(selector)
    if not isinstance(entries, list) or not selector:
        return None
    matches = [
        int(entry["index"])
        for entry in entries
        if isinstance(entry, dict)
        and selector in {str(entry.get("resource_key") or ""), str(entry.get("path") or "")}
    ]
    return matches[0] if len(matches) == 1 else None


def _issue(
    code: str,
    message: str,
    *,
    severity: str,
    resource_key: str = "",
    kind: str = "",
    name: str = "",
    path: str = "",
    default_action: str = "keep",
    actions: list[str] | None = None,
    blocking: bool = False,
    details: dict[str, Any] | None = None,
) -> RegistryAuditIssue:
    identity = json.dumps(
        {
            "code": code,
            "resource_key": resource_key,
            "kind": kind,
            "name": name,
            "path": path,
            "details": details or {},
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    issue_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return RegistryAuditIssue(
        id=issue_id,
        code=code,
        severity=severity,
        message=message,
        message_ref=_issue_message_ref(
            code,
            message,
            resource_key=resource_key,
            kind=kind,
            name=name,
            path=path,
        ),
        resource_key=resource_key,
        kind=kind,
        name=name,
        path=path,
        default_action=default_action,
        actions=list(actions or []),
        blocking=blocking,
        details=dict(details or {}),
    )


def _issue_message_ref(
    code: str,
    fallback: str,
    **params: str,
) -> UiMessageRef:
    message_codes = {
        "duplicate-key": "registry.issue.duplicate_key",
        "duplicate-path": "registry.issue.duplicate_path",
        "invalid-resource": "registry.issue.invalid_resource",
        "invalid-resource-entry": "registry.issue.invalid_resource_entry",
        "invalid-resource-name": "registry.issue.invalid_resource_name",
        "invalid-result": "registry.issue.invalid_result",
        "invalid-source": "registry.issue.invalid_source",
        "invalid-yaml": "registry.issue.invalid_yaml",
        "legacy-v7": "registry.issue.legacy_v7",
        "missing-registry": "registry.issue.missing_registry",
        "missing-resource": "registry.issue.missing_resource",
        "noncanonical-registry": "registry.issue.noncanonical_registry",
        "registry-symlink": "registry.issue.registry_symlink",
        "remote-unavailable": "registry.issue.remote_unavailable",
        "unreadable-resource": "registry.issue.unreadable_resource",
        "unreadable-resource-root": "registry.issue.unreadable_resource_root",
        "unregistered-resource": "registry.issue.unregistered_resource",
        "unsafe-link": "registry.issue.unsafe_link",
        "unsafe-path": "registry.issue.unsafe_path",
    }
    message_code = message_codes.get(code)
    if message_code == "registry.issue.duplicate_key":
        return ui_message("registry.issue.duplicate_key", fallback, **params)
    if message_code == "registry.issue.duplicate_path":
        return ui_message("registry.issue.duplicate_path", fallback, **params)
    if message_code == "registry.issue.invalid_resource":
        return ui_message("registry.issue.invalid_resource", fallback, **params)
    if message_code == "registry.issue.invalid_resource_entry":
        return ui_message("registry.issue.invalid_resource_entry", fallback, **params)
    if message_code == "registry.issue.invalid_resource_name":
        return ui_message("registry.issue.invalid_resource_name", fallback, **params)
    if message_code == "registry.issue.invalid_result":
        return ui_message("registry.issue.invalid_result", fallback, **params)
    if message_code == "registry.issue.invalid_source":
        return ui_message("registry.issue.invalid_source", fallback, **params)
    if message_code == "registry.issue.invalid_yaml":
        return ui_message("registry.issue.invalid_yaml", fallback, **params)
    if message_code == "registry.issue.legacy_v7":
        return ui_message("registry.issue.legacy_v7", fallback, **params)
    if message_code == "registry.issue.missing_registry":
        return ui_message("registry.issue.missing_registry", fallback, **params)
    if message_code == "registry.issue.missing_resource":
        return ui_message("registry.issue.missing_resource", fallback, **params)
    if message_code == "registry.issue.noncanonical_registry":
        return ui_message("registry.issue.noncanonical_registry", fallback, **params)
    if message_code == "registry.issue.registry_symlink":
        return ui_message("registry.issue.registry_symlink", fallback, **params)
    if message_code == "registry.issue.remote_unavailable":
        return ui_message("registry.issue.remote_unavailable", fallback, **params)
    if message_code == "registry.issue.unreadable_resource":
        return ui_message("registry.issue.unreadable_resource", fallback, **params)
    if message_code == "registry.issue.unreadable_resource_root":
        return ui_message("registry.issue.unreadable_resource_root", fallback, **params)
    if message_code == "registry.issue.unregistered_resource":
        return ui_message("registry.issue.unregistered_resource", fallback, **params)
    if message_code == "registry.issue.unsafe_link":
        return ui_message("registry.issue.unsafe_link", fallback, **params)
    if message_code == "registry.issue.unsafe_path":
        return ui_message("registry.issue.unsafe_path", fallback, **params)
    return UiMessageRef(code=f"registry.issue.{code.replace('-', '_')}", fallback=fallback, params=params)


def _effective_choices(
    issues: list[RegistryAuditIssue],
    selected: dict[str, RegistryRepairChoice],
) -> list[RegistryRepairChoice]:
    return [
        selected.get(issue.id)
        or RegistryRepairChoice(issue_id=issue.id, action=issue.default_action)
        for issue in issues
        if issue.actions or issue.default_action not in {"keep", ""}
    ]


def _plan_hash(
    *,
    remote_commit: str,
    original_hash: str,
    candidate_fingerprints: dict[str, str],
    choices: list[RegistryRepairChoice],
    resulting_text: str,
    issues: list[RegistryAuditIssue],
) -> str:
    payload = {
        "remote_commit": remote_commit,
        "original_hash": original_hash,
        "candidate_fingerprints": dict(sorted(candidate_fingerprints.items())),
        "choices": [asdict(choice) for choice in sorted(choices, key=lambda item: item.issue_id)],
        "resulting_registry_hash": hashlib.sha256(resulting_text.encode("utf-8")).hexdigest(),
        "issues": [
            {
                "id": issue.id,
                "code": issue.code,
                "message": issue.message,
                "blocking": issue.blocking,
                "details": issue.details,
            }
            for issue in sorted(issues, key=lambda item: item.id)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _unrepairable_plan(
    document: _RegistryDocument,
    *,
    remote_commit: str = "",
    repo_url: str = "",
    branch: str = "main",
) -> RegistryRepairPlan:
    original_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    plan_hash = _plan_hash(
        remote_commit=remote_commit,
        original_hash=original_hash,
        candidate_fingerprints={},
        choices=[],
        resulting_text=document.text,
        issues=document.issues,
    )
    return RegistryRepairPlan(
        remote_commit=remote_commit,
        repo_url=repo_url,
        branch=branch,
        registry_status=document.status,
        issues=document.issues,
        choices=[],
        registry_diff="",
        plan_hash=plan_hash,
        executable_count=0,
        blocked_count=sum(1 for issue in document.issues if issue.blocking),
        repairable=False,
        original_registry_hash=original_hash,
        resulting_registry_text=document.text,
    )


def _configured_remote_url(cfg: Config) -> str:
    if cfg.resources.repo_url.strip():
        return cfg.resources.repo_url.strip()
    root = cfg.resources.local_path_value.expanduser().resolve()
    if git_ops.is_repo(root):
        return git_ops.current_remote_url(root) or ""
    return ""


def _clone_remote(repo_url: str, destination: Path, cfg: Config) -> None:
    branch = cfg.resources.branch or "main"
    try:
        git_ops.clone(
            repo_url,
            destination,
            ref=branch,
            token=resource_repo_auth_token(cfg),
        )
    except git_ops.GitError:
        git_ops.clone(
            repo_url,
            destination,
            token=resource_repo_auth_token(cfg),
        )
        git_ops.checkout_local_branch(destination, branch)
    git_ops.configure_host_autocrlf_disabled_checkout(destination)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized[:64].rstrip("-")
