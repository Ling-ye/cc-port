"""Environment capture and deployment for local AI agent tool configs."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..core.config import Config, load_config
from ..core.models import ItemKind, Registry, RegistryItem
from ..core.ownership import (
    is_lpm_managed,
    is_lpm_managed_mcp,
    mcp_ownership_path,
    write_managed_marker,
)
from ..core.platforms import PlatformProfile
from ..core.registry import load_registry, save_registry
from ..core.resource_files import is_resource_path_excluded, resource_copy_ignore
from ..core.secrets import sanitize_mcp_config_for_storage
from ..core.tool_adapters import TOOL_ADAPTERS
from ..infrastructure import git_ops
from .installer import SyncAction, sync_all
from .local_transaction import (
    ChangeTarget,
    LocalChangeTransaction,
    resource_hash_path,
)
from .mcp_installer import list_mcp_servers
from .publisher import _slug
from .resource_discovery import DiscoveredResource, discover_resources
from .resource_repo import (
    ensure_structure,
    pull_resource_repo,
    push_resource_repo,
    registry_path,
    resource_root,
)

ENV_PROFILE_PATH = Path("profiles/default.yaml")
SECRETS_EXAMPLE_PATH = Path("secrets.example.yaml")
BACKUP_DIR = "backups"
CAPTURE_SOURCE = "env-capture"
RESOURCE_SUBDIRS: dict[ItemKind, str] = {
    "skill": "skills",
    "mcp": "mcp",
    "rule": "rules",
    "prompt": "prompts",
    "plugin": "plugins",
}


@dataclass(frozen=True)
class ToolScanSpec:
    id: str
    name: str
    root: str
    config_files: tuple[str, ...] = ()
    resource_dirs: tuple[str, ...] = ()
    mcp_config_files: tuple[str, ...] = ()


@dataclass
class DiscoveredTool:
    id: str
    name: str
    root_path: Path
    detected: bool
    confidence: str
    config_paths: list[Path] = field(default_factory=list)
    resource_paths: list[Path] = field(default_factory=list)
    mcp_config_paths: list[Path] = field(default_factory=list)
    supports_kinds: list[ItemKind] = field(default_factory=list)


@dataclass
class DiscoveredMcpServer:
    id: str
    tool: str
    name: str
    config_path: Path
    config: dict[str, Any]
    secret_keys: list[str] = field(default_factory=list)


@dataclass
class EnvDiscoveryResult:
    tools: list[DiscoveredTool]
    resources: list[DiscoveredResource]
    mcp_servers: list[DiscoveredMcpServer]


@dataclass
class CapturedResource:
    name: str
    kind: ItemKind
    source: str
    path: Path
    target_tools: list[str] = field(default_factory=list)
    secret_placeholders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SecretPlaceholder:
    name: str
    tool: str
    resource: str
    purpose: str


@dataclass
class EnvironmentProfile:
    name: str
    created_at: str
    tools: list[dict[str, Any]]
    resource_counts: dict[str, int]


@dataclass
class CaptureResult:
    root: Path
    registry_path: Path
    profile_path: Path
    secrets_path: Path
    captured: list[CapturedResource]
    skipped: list[CapturedResource]
    secrets: list[SecretPlaceholder]


@dataclass
class DeployPlanItem:
    name: str
    kind: ItemKind
    platform: str
    target_path: Path
    action: str
    reason: str = ""
    backup_path: Path | None = None


@dataclass
class DeployPlan:
    root: Path
    registry_path: Path
    dry_run: bool
    backup_root: Path | None
    items: list[DeployPlanItem]
    missing_secrets: list[SecretPlaceholder] = field(default_factory=list)
    selected_names: list[str] = field(default_factory=list)
    operation_id: str = ""
    status: str = "planned"
    rolled_back: bool = False


class DeploymentTransactionError(RuntimeError):
    """Raised after a failed deployment transaction."""

    def __init__(self, message: str, plan: DeployPlan) -> None:
        super().__init__(message)
        self.plan = plan


@dataclass
class EnvVersionChoice:
    id: str
    choice: str


@dataclass
class EnvSecretFinding:
    path: Path
    reason: str
    preview: str = ""


@dataclass
class EnvDiffItem:
    id: str
    group: str
    name: str
    kind: str
    status: str
    local_path: Path | None
    incoming_path: Path | None
    default_choice: str
    selected_choice: str = ""
    preview: str = ""
    reason: str = ""


@dataclass
class EnvDiffPlan:
    operation: str
    source: str
    local_root: Path
    incoming_root: Path
    items: list[EnvDiffItem]
    default_choices: dict[str, str]
    blocked: bool = False
    secret_findings: list[EnvSecretFinding] = field(default_factory=list)


TOOL_SPECS: tuple[ToolScanSpec, ...] = tuple(
    ToolScanSpec(
        id=adapter.id,
        name=adapter.name,
        root=adapter.discovery_root,
        config_files=adapter.config_files,
        resource_dirs=adapter.resource_dirs,
        mcp_config_files=adapter.mcp_config_files,
    )
    for adapter in TOOL_ADAPTERS
    if adapter.discovery_root
)

SECRET_KEY_RE = re.compile(r"(token|secret|api[_-]?key|auth|password|credential)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(
    r"(?P<prefix>(?:api[_-]?key|token|secret|password|auth[_-]?token)\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[A-Za-z0-9_./+=-]{8,})"
    r"(?P=quote)",
    re.IGNORECASE,
)


HIGH_RISK_SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)",
)


class EnvSecretScanError(ValueError):
    """Raised when a diff/import source contains likely secret material."""

    def __init__(self, findings: list[EnvSecretFinding]) -> None:
        self.findings = findings
        preview = ", ".join(str(item.path) for item in findings[:5])
        suffix = "" if len(findings) <= 5 else f" and {len(findings) - 5} more"
        super().__init__(f"Secret-like content found in environment data: {preview}{suffix}")


def discover_environment(
    *,
    home: Path | None = None,
    registry_path_override: Path | None = None,
) -> EnvDiscoveryResult:
    """Discover local agent tools, resources, and MCP server entries."""
    effective_home = home or Path.home()
    tools = [_discover_tool(spec, home=effective_home) for spec in TOOL_SPECS]
    resources = _discover_tool_resources(tools, registry_path_override=registry_path_override)
    mcp_servers = _discover_mcp_servers(tools)
    return EnvDiscoveryResult(tools=tools, resources=resources, mcp_servers=mcp_servers)


def capture_environment(
    *,
    config: Config | None = None,
    home: Path | None = None,
    tools: list[str] | None = None,
    kinds: list[str] | None = None,
    overwrite: bool = True,
) -> CaptureResult:
    """Capture discovered non-secret resources into the configured private repo."""
    cfg = config or load_config()
    root = resource_root(cfg)
    ensure_structure(root)
    _ensure_env_structure(root)
    reg_path = registry_path(cfg)
    discovery = discover_environment(home=home, registry_path_override=reg_path)
    selected_tools = {item.strip() for item in tools or [] if item.strip()}
    selected_kinds = {item.strip() for item in kinds or [] if item.strip()}

    registry = load_registry(reg_path)
    captured: list[CapturedResource] = []
    skipped: list[CapturedResource] = []
    secrets: list[SecretPlaceholder] = []

    for resource in discovery.resources:
        if selected_tools and resource.tool not in selected_tools:
            continue
        if selected_kinds and resource.kind not in selected_kinds:
            continue
        result = _capture_file_resource(resource, root=root, registry=registry, overwrite=overwrite)
        captured.append(result)

    for server in discovery.mcp_servers:
        if selected_tools and server.tool not in selected_tools:
            continue
        if selected_kinds and "mcp" not in selected_kinds:
            continue
        result, placeholders = _capture_mcp_server(server, root=root, registry=registry, overwrite=overwrite)
        captured.append(result)
        secrets.extend(placeholders)

    save_registry(registry, reg_path)
    profile = _build_profile(discovery, captured)
    profile_path = root / ENV_PROFILE_PATH
    _write_yaml(profile_path, profile)
    secrets_path = root / SECRETS_EXAMPLE_PATH
    _write_yaml(secrets_path, _secrets_payload(secrets))
    return CaptureResult(
        root=root,
        registry_path=reg_path,
        profile_path=profile_path,
        secrets_path=secrets_path,
        captured=captured,
        skipped=skipped,
        secrets=secrets,
    )


def export_environment_snapshot(
    out: Path | str,
    *,
    config: Config | None = None,
) -> Path:
    """Export the private environment repo as a zip snapshot."""
    cfg = config or load_config()
    root = resource_root(cfg)
    ensure_structure(root)
    _ensure_env_structure(root)
    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.resolve() == out_path:
                continue
            if path.is_symlink() or not path.is_file() or _is_snapshot_excluded(path, root):
                continue
            archive.write(path, path.relative_to(root).as_posix())
    return out_path


def build_env_push_diff(*, config: Config | None = None) -> EnvDiffPlan:
    """Compare the local private environment repo with the configured remote before pushing."""
    cfg = config or load_config()
    return _build_env_diff_plan(
        operation="push",
        source="remote",
        local_root=_prepared_local_env_root(cfg),
        incoming_root=_clone_remote_environment_repo(cfg),
    )


def apply_env_push(
    *,
    choices: dict[str, str] | None = None,
    config: Config | None = None,
) -> EnvDiffPlan:
    """Apply push choices locally and push the resource repo."""
    cfg = config or load_config()
    plan = build_env_push_diff(config=cfg)
    resolved = _resolve_env_choices(plan, choices)
    _raise_if_secret_findings(plan)
    _apply_env_diff_plan(plan, resolved)
    push_resource_repo(message="lpm: update environment resources", config=cfg)
    return _mark_selected_choices(plan, resolved)


def build_env_pull_diff(*, config: Config | None = None) -> EnvDiffPlan:
    """Compare the configured remote environment repo with the local repo before pulling."""
    cfg = config or load_config()
    return _build_env_diff_plan(
        operation="pull",
        source="remote",
        local_root=_prepared_local_env_root(cfg),
        incoming_root=_clone_remote_environment_repo(cfg),
    )


def apply_env_pull(
    *,
    choices: dict[str, str] | None = None,
    config: Config | None = None,
) -> EnvDiffPlan:
    """Apply pull choices into the local private environment repo without auto-pushing."""
    cfg = config or load_config()
    plan = build_env_pull_diff(config=cfg)
    resolved = _resolve_env_choices(plan, choices)
    _raise_if_secret_findings(plan)
    _apply_env_diff_plan(plan, resolved)
    return _mark_selected_choices(plan, resolved)


def build_env_import_diff(
    snapshot: Path | str,
    *,
    config: Config | None = None,
) -> EnvDiffPlan:
    """Compare a zip snapshot with the local private environment repo before importing."""
    cfg = config or load_config()
    return _build_env_diff_plan(
        operation="import",
        source="snapshot",
        local_root=_prepared_local_env_root(cfg),
        incoming_root=_extract_snapshot_root(Path(snapshot)),
    )


def apply_env_import(
    snapshot: Path | str,
    *,
    choices: dict[str, str] | None = None,
    config: Config | None = None,
) -> EnvDiffPlan:
    """Apply snapshot import choices into the local private environment repo."""
    cfg = config or load_config()
    plan = build_env_import_diff(snapshot, config=cfg)
    resolved = _resolve_env_choices(plan, choices)
    _raise_if_secret_findings(plan)
    _apply_env_diff_plan(plan, resolved)
    return _mark_selected_choices(plan, resolved)


def load_env_choices(
    path: Path | str,
    *,
    operation: str | None = None,
    source: str | None = None,
) -> dict[str, str]:
    """Load local/incoming resource-level choices from a YAML file."""
    data = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Choices file must contain a YAML mapping.")
    actual_operation = str(data.get("operation") or "").strip()
    actual_source = str(data.get("source") or "").strip()
    if operation and actual_operation and actual_operation != operation:
        raise ValueError(f"Choices operation must be {operation!r}, got {actual_operation!r}.")
    if source and actual_source and actual_source != source:
        raise ValueError(f"Choices source must be {source!r}, got {actual_source!r}.")
    items = data.get("items")
    if not isinstance(items, dict):
        raise ValueError("Choices file must contain an 'items' mapping.")
    return _normalize_choice_map(items)


def pull_environment_repo(*, config: Config | None = None) -> Any:
    """Pull the configured private environment repo."""
    return pull_resource_repo(config or load_config())


def build_deploy_plan(
    *,
    config: Config | None = None,
    force: bool = False,
    names: list[str] | None = None,
) -> DeployPlan:
    """Build a deployment plan without writing local tool files."""
    cfg = config or load_config()
    root = resource_root(cfg)
    reg_path = registry_path(cfg)
    registry = load_registry(reg_path)
    requested = {item.strip() for item in names or [] if item.strip()}
    items: list[DeployPlanItem] = []
    selected: set[str] = set()
    missing_secrets = _load_secret_placeholders(root)

    for entry in registry.items:
        if entry.lifecycle != "active":
            continue
        if requested and entry.name not in requested:
            continue
        selected.add(entry.name)
        entry_items = _plan_entry(
            entry,
            cfg.platforms.enabled(),
            force=force,
            resource_repo_root=root,
            install_root=cfg.install.target_path,
        )
        items.extend(entry_items)

    return DeployPlan(
        root=root,
        registry_path=reg_path,
        dry_run=True,
        backup_root=None,
        items=items,
        missing_secrets=missing_secrets,
        selected_names=sorted(selected),
    )


def deploy_environment(
    *,
    config: Config | None = None,
    dry_run: bool = False,
    force: bool = False,
    names: list[str] | None = None,
) -> DeployPlan:
    """Deploy selected environment resources into enabled platform directories."""
    plan = build_deploy_plan(config=config, force=force, names=names)
    if dry_run:
        return plan

    cfg = config or load_config()
    eligible_items = [
        item
        for item in plan.items
        if item.action in {"create", "update"} and item.platform
    ]
    registry = load_registry(registry_path(cfg))
    change_targets: list[ChangeTarget] = []
    for item in eligible_items:
        for path in _deploy_item_paths(item, config=cfg, registry=registry):
            change_targets.append(
                ChangeTarget(
                    path=path,
                    change_action=item.action,
                    resource=item.name,
                    platform=item.platform if path == item.target_path else "",
                )
            )
    transaction = LocalChangeTransaction.begin(
        "environment-deploy",
        change_targets,
        metadata={"selected_names": plan.selected_names},
        lock_timeout_seconds=cfg.state.lock_timeout_seconds,
    )
    plan.operation_id = transaction.record.operation_id
    plan.backup_root = transaction.backup_root
    plan.status = "running"
    for item in eligible_items:
        snapshot = transaction.snapshots.get(item.target_path.expanduser().absolute())
        if snapshot is not None:
            item.backup_path = snapshot.backup_path
    try:
        for item in eligible_items:
            transaction.mark_attempted(
                _deploy_item_paths(item, config=cfg, registry=registry)
            )
            results = sync_all(
                config=cfg,
                registry_path=registry_path(cfg),
                only=[item.name],
                include_optional=True,
                include_kinds={"mcp", "rule", "prompt", "plugin"},
                platform_filter=item.platform,
                force_unmanaged=force,
                transactional=False,
            )
            matched = [result for result in results if result.name == item.name]
            if not matched:
                raise RuntimeError(
                    f"No deployment result for {item.name} on {item.platform}"
                )
            failed = [
                result
                for result in matched
                if result.action
                in {SyncAction.FAILED, SyncAction.REPO_GONE, SyncAction.SKIPPED}
            ]
            if failed:
                details = "; ".join(
                    result.detail or result.action.value for result in failed
                )
                raise RuntimeError(
                    f"Failed to deploy {item.name} on {item.platform}: {details}"
                )
            _verify_deploy_item(item, config=cfg, registry=registry)

        if eligible_items:
            _write_managed_markers(eligible_items, registry)
        plan.dry_run = False
        plan.status = "succeeded"
        transaction.complete()
        return plan
    except Exception as exc:
        rollback_errors = transaction.rollback(str(exc))
        plan.status = "rolled_back" if not rollback_errors else "rollback_failed"
        plan.rolled_back = not rollback_errors
        message = str(exc)
        if rollback_errors:
            message += " | rollback errors: " + "; ".join(rollback_errors)
        raise DeploymentTransactionError(
            f"{message} (operation {plan.operation_id}, status {plan.status})",
            plan,
        ) from exc


def _prepared_local_env_root(cfg: Config) -> Path:
    root = resource_root(cfg)
    ensure_structure(root)
    _ensure_env_structure(root)
    return root


def _clone_remote_environment_repo(cfg: Config) -> Path:
    repo_url = cfg.resources.repo_url.strip()
    if not repo_url:
        raise ValueError("No remote resource repo is configured. Run `lpm resource use <git-url>` first.")
    temp_root = Path(tempfile.mkdtemp(prefix="lpm-env-remote-")) / "repo"
    git_ops.clone(
        repo_url,
        temp_root,
        ref=cfg.resources.branch or "main",
        depth=1,
        token=cfg.github.token or None,
    )
    return temp_root


def _extract_snapshot_root(snapshot: Path) -> Path:
    snapshot_path = snapshot.expanduser().resolve()
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")
    temp_root = Path(tempfile.mkdtemp(prefix="lpm-env-snapshot-"))
    try:
        with zipfile.ZipFile(snapshot_path) as archive:
            for info in archive.infolist():
                rel = _safe_zip_member(info.filename)
                if rel is None:
                    continue
                target = temp_root / rel
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as dest:
                    shutil.copyfileobj(src, dest)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid zip snapshot: {snapshot_path}") from exc
    if not (temp_root / "registry.yaml").is_file():
        raise ValueError("Snapshot must contain registry.yaml at the archive root.")
    return temp_root


def _safe_zip_member(name: str) -> Path | None:
    normalized = name.replace("\\", "/").strip()
    if not normalized:
        return None
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if pure.is_absolute() or not parts:
        raise ValueError(f"Unsafe snapshot path: {name}")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe snapshot path: {name}")
    if ".git" in parts or any(":" in part for part in parts[:1]):
        raise ValueError(f"Unsafe snapshot path: {name}")
    return Path(*parts)


def _build_env_diff_plan(
    *,
    operation: str,
    source: str,
    local_root: Path,
    incoming_root: Path,
) -> EnvDiffPlan:
    local_registry = load_registry(local_root / "registry.yaml")
    incoming_registry = load_registry(incoming_root / "registry.yaml")
    items: list[EnvDiffItem] = []
    local_items = {item.resource_key: item for item in local_registry.items}
    incoming_items = {item.resource_key: item for item in incoming_registry.items}
    for resource_key in sorted(set(local_items) | set(incoming_items)):
        items.append(
            _resource_diff_item(
                resource_key,
                operation=operation,
                local_root=local_root,
                incoming_root=incoming_root,
                local_entry=local_items.get(resource_key),
                incoming_entry=incoming_items.get(resource_key),
            )
        )
    items.append(
        _metadata_diff_item(
            ENV_PROFILE_PATH,
            group="profile",
            name="profiles/default.yaml",
            operation=operation,
            local_root=local_root,
            incoming_root=incoming_root,
        )
    )
    items.append(
        _metadata_diff_item(
            SECRETS_EXAMPLE_PATH,
            group="secrets",
            name="secrets.example.yaml",
            operation=operation,
            local_root=local_root,
            incoming_root=incoming_root,
        )
    )
    defaults = {item.id: item.default_choice for item in items}
    scan_root = local_root if operation == "push" else incoming_root
    findings = _scan_root_for_secrets(scan_root)
    return EnvDiffPlan(
        operation=operation,
        source=source,
        local_root=local_root,
        incoming_root=incoming_root,
        items=items,
        default_choices=defaults,
        blocked=bool(findings),
        secret_findings=findings,
    )


def _resource_diff_item(
    resource_key: str,
    *,
    operation: str,
    local_root: Path,
    incoming_root: Path,
    local_entry: RegistryItem | None,
    incoming_entry: RegistryItem | None,
) -> EnvDiffItem:
    local_sig = _resource_signature(local_root, local_entry)
    incoming_sig = _resource_signature(incoming_root, incoming_entry)
    status = _diff_status(
        local_entry is not None,
        incoming_entry is not None,
        local_sig == incoming_sig,
        operation=operation,
        both_differ_status="conflict",
    )
    entry = local_entry or incoming_entry
    local_path = _entry_local_path(local_root, local_entry)
    incoming_path = _entry_local_path(incoming_root, incoming_entry)
    return EnvDiffItem(
        id=f"resource:{resource_key}",
        group="resource",
        name=entry.name if entry is not None else resource_key,
        kind=entry.kind if entry is not None else "resource",
        status=status,
        local_path=local_path,
        incoming_path=incoming_path,
        default_choice=_default_choice(operation),
        preview=_resource_diff_preview(
            entry.name if entry is not None else resource_key,
            local_root=local_root,
            incoming_root=incoming_root,
            local_entry=local_entry,
            incoming_entry=incoming_entry,
        ),
    )


def _metadata_diff_item(
    rel_path: Path,
    *,
    group: str,
    name: str,
    operation: str,
    local_root: Path,
    incoming_root: Path,
) -> EnvDiffItem:
    local_path = local_root / rel_path
    incoming_path = incoming_root / rel_path
    local_text = _read_text_or_marker(local_path)
    incoming_text = _read_text_or_marker(incoming_path)
    status = _diff_status(
        local_path.is_file(),
        incoming_path.is_file(),
        local_text == incoming_text,
        operation=operation,
        both_differ_status="modified",
    )
    return EnvDiffItem(
        id=f"meta:{rel_path.as_posix()}",
        group=group,
        name=name,
        kind="file",
        status=status,
        local_path=local_path if local_path.exists() else None,
        incoming_path=incoming_path if incoming_path.exists() else None,
        default_choice=_default_choice(operation),
        preview=_short_unified_diff(local_text, incoming_text, "local", "incoming"),
    )


def _diff_status(
    has_local: bool,
    has_incoming: bool,
    same: bool,
    *,
    operation: str,
    both_differ_status: str,
) -> str:
    if has_local and has_incoming:
        return "same" if same else both_differ_status
    if has_local:
        return "added" if operation == "push" else "deleted"
    if has_incoming:
        return "deleted" if operation == "push" else "added"
    return "same"


def _default_choice(operation: str) -> str:
    return "local" if operation == "push" else "incoming"


def _resource_signature(root: Path, entry: RegistryItem | None) -> str:
    if entry is None:
        return "<missing>"
    payload = entry.model_dump(mode="json")
    if entry.path:
        payload["_tree"] = _path_signature(root / entry.path)
    return yaml.safe_dump(payload, sort_keys=True, allow_unicode=True)


def _path_signature(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return [{"path": "", "sha256": "<missing>"}]
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    out: list[dict[str, str]] = []
    for item in files:
        rel = Path(item.name) if path.is_file() else item.relative_to(path)
        if item.is_symlink() or _is_copy_excluded(rel):
            continue
        try:
            digest = hashlib.sha256(item.read_bytes()).hexdigest()
        except OSError:
            digest = "<unreadable>"
        out.append({"path": rel.as_posix(), "sha256": digest})
    return out


def _resource_diff_preview(
    name: str,
    *,
    local_root: Path,
    incoming_root: Path,
    local_entry: RegistryItem | None,
    incoming_entry: RegistryItem | None,
) -> str:
    local_text = _registry_item_text(local_entry)
    incoming_text = _registry_item_text(incoming_entry)
    registry_preview = _short_unified_diff(local_text, incoming_text, f"local:{name}", f"incoming:{name}")
    content_preview = _resource_content_preview(local_root, incoming_root, local_entry, incoming_entry)
    if content_preview:
        return f"{registry_preview}\n\n{content_preview}" if registry_preview else content_preview
    return registry_preview


def _registry_item_text(entry: RegistryItem | None) -> str:
    if entry is None:
        return "<missing>\n"
    return yaml.safe_dump(entry.model_dump(mode="json"), sort_keys=True, allow_unicode=True)


def _resource_content_preview(
    local_root: Path,
    incoming_root: Path,
    local_entry: RegistryItem | None,
    incoming_entry: RegistryItem | None,
) -> str:
    if local_entry is None or incoming_entry is None or not local_entry.path or not incoming_entry.path:
        return ""
    left = local_root / local_entry.path
    right = incoming_root / incoming_entry.path
    local_files = _text_file_map(left)
    incoming_files = _text_file_map(right)
    for rel in sorted(set(local_files) | set(incoming_files)):
        left_text = local_files.get(rel, "<missing>\n")
        right_text = incoming_files.get(rel, "<missing>\n")
        if left_text != right_text:
            return _short_unified_diff(left_text, right_text, f"local:{rel}", f"incoming:{rel}")
    return ""


def _text_file_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    out: dict[str, str] = {}
    for item in files:
        rel = item.name if path.is_file() else item.relative_to(path).as_posix()
        if item.is_symlink() or is_resource_path_excluded(Path(rel)):
            continue
        try:
            raw = item.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[: min(len(raw), 4096)]:
            continue
        try:
            out[rel] = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return out


def _read_text_or_marker(path: Path) -> str:
    if not path.is_file():
        return "<missing>\n"
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "<binary>\n"


def _short_unified_diff(left: str, right: str, left_label: str, right_label: str, *, max_lines: int = 80) -> str:
    if left == right:
        return ""
    lines = list(
        difflib.unified_diff(
            left.splitlines(),
            right.splitlines(),
            fromfile=left_label,
            tofile=right_label,
            lineterm="",
        )
    )
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"... truncated {len(lines) - max_lines} line(s) ..."]
    return "\n".join(lines)


def _entry_local_path(root: Path, entry: RegistryItem | None) -> Path | None:
    if entry is None or not entry.path:
        return None
    return root / entry.path


def _normalize_choice_map(value: dict[Any, Any]) -> dict[str, str]:
    choices: dict[str, str] = {}
    for key, raw_choice in value.items():
        item_id = str(key).strip()
        choice = str(raw_choice).strip()
        if not item_id:
            continue
        if choice not in {"local", "incoming"}:
            raise ValueError(f"Choice for {item_id!r} must be 'local' or 'incoming'.")
        choices[item_id] = choice
    return choices


def _resolve_env_choices(plan: EnvDiffPlan, choices: dict[str, str] | None) -> dict[str, str]:
    resolved = dict(plan.default_choices)
    if choices:
        normalized = _normalize_choice_map(choices)
        unknown = sorted(set(normalized) - set(plan.default_choices))
        if unknown:
            raise ValueError(f"Unknown diff item id(s): {', '.join(unknown)}")
        resolved.update(normalized)
    return resolved


def _mark_selected_choices(plan: EnvDiffPlan, choices: dict[str, str]) -> EnvDiffPlan:
    for item in plan.items:
        item.selected_choice = choices.get(item.id, item.default_choice)
    return plan


def _raise_if_secret_findings(plan: EnvDiffPlan) -> None:
    if plan.secret_findings:
        raise EnvSecretScanError(plan.secret_findings)


def _apply_env_diff_plan(plan: EnvDiffPlan, choices: dict[str, str]) -> None:
    local_registry = load_registry(plan.local_root / "registry.yaml")
    local_items = {item.resource_key: item for item in local_registry.items}
    incoming_items = {
        item.resource_key: item
        for item in load_registry(plan.incoming_root / "registry.yaml").items
    }
    for item in plan.items:
        choice = choices.get(item.id, item.default_choice)
        if item.group == "resource":
            _apply_resource_choice(
                item,
                choice=choice,
                local_root=plan.local_root,
                incoming_root=plan.incoming_root,
                registry=local_registry,
                local_items=local_items,
                incoming_items=incoming_items,
            )
        else:
            _apply_metadata_choice(item, choice=choice, local_root=plan.local_root, incoming_root=plan.incoming_root)
    save_registry(local_registry, plan.local_root / "registry.yaml")


def _apply_resource_choice(
    item: EnvDiffItem,
    *,
    choice: str,
    local_root: Path,
    incoming_root: Path,
    registry: Registry,
    local_items: dict[str, RegistryItem],
    incoming_items: dict[str, RegistryItem],
) -> None:
    resource_key = item.id.removeprefix("resource:")
    local_entry = local_items.get(resource_key)
    incoming_entry = incoming_items.get(resource_key)
    selected_entry = local_entry if choice == "local" else incoming_entry
    selected_root = local_root if choice == "local" else incoming_root
    if selected_entry is None:
        if local_entry and local_entry.path:
            _remove_path_if_exists(local_root / local_entry.path)
        if local_entry is not None:
            registry.remove(local_entry.name, local_entry.kind)
        return

    if local_entry and local_entry.path and local_entry.path != selected_entry.path:
        _remove_path_if_exists(local_root / local_entry.path)
    if selected_entry.path:
        source = selected_root / selected_entry.path
        target = local_root / selected_entry.path
        if source.exists() and source.resolve() != target.resolve():
            _copy_path_exact(source, target)
    registry.upsert(RegistryItem.model_validate(selected_entry.model_dump(mode="json")))


def _apply_metadata_choice(item: EnvDiffItem, *, choice: str, local_root: Path, incoming_root: Path) -> None:
    rel = Path(item.id.removeprefix("meta:"))
    src = (local_root if choice == "local" else incoming_root) / rel
    dest = local_root / rel
    if not src.exists():
        _remove_path_if_exists(dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)


def _copy_path_exact(src: Path, dest: Path) -> None:
    if dest.exists():
        _remove_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dest, ignore=resource_copy_ignore)
    else:
        shutil.copy2(src, dest)


def _remove_path_if_exists(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _remove_path(path)


def _scan_root_for_secrets(root: Path) -> list[EnvSecretFinding]:
    findings: list[EnvSecretFinding] = []
    if not root.exists():
        return findings
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_secret_scan_excluded(rel):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[: min(len(raw), 4096)]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        finding = _secret_finding_for_text(rel, text)
        if finding is not None:
            findings.append(finding)
    return findings


def _secret_finding_for_text(path: Path, text: str) -> EnvSecretFinding | None:
    value_match = SECRET_VALUE_RE.search(text)
    if value_match and not _is_placeholder(value_match.group("value")):
        return EnvSecretFinding(path=path, reason="token-like key/value", preview=_line_preview(text, value_match.start()))
    high_risk = HIGH_RISK_SECRET_RE.search(text)
    if high_risk:
        return EnvSecretFinding(path=path, reason="high-risk token pattern", preview=_line_preview(text, high_risk.start()))
    return None


def _line_preview(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    line = text[start:end].strip()
    return SECRET_VALUE_RE.sub(lambda match: f"{match.group('prefix')}${{SECRET_VALUE}}", line)[:160]


def _is_secret_scan_excluded(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    if lowered & {".git", BACKUP_DIR.lower(), "__pycache__", "node_modules", ".venv"}:
        return True
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".gz", ".tar"}


def _discover_tool(spec: ToolScanSpec, *, home: Path) -> DiscoveredTool:
    root = _expand_home(spec.root, home=home)
    config_paths = _existing_paths(root, spec.config_files)
    resource_paths = _existing_paths(root, spec.resource_dirs, dirs_only=True)
    mcp_config_paths = _existing_paths(root, spec.mcp_config_files)
    detected = root.exists() or bool(config_paths or resource_paths or mcp_config_paths)
    supports = _supported_kinds(spec)
    confidence = "high" if root.exists() and (config_paths or resource_paths or mcp_config_paths) else "medium" if detected else "none"
    return DiscoveredTool(
        id=spec.id,
        name=spec.name,
        root_path=root,
        detected=detected,
        confidence=confidence,
        config_paths=config_paths,
        resource_paths=resource_paths,
        mcp_config_paths=mcp_config_paths,
        supports_kinds=supports,
    )


def _discover_tool_resources(
    tools: list[DiscoveredTool],
    *,
    registry_path_override: Path | None,
) -> list[DiscoveredResource]:
    out: list[DiscoveredResource] = []
    seen: set[str] = set()
    for tool in tools:
        for path in [tool.root_path, *tool.resource_paths]:
            if not path.is_dir():
                continue
            for resource in discover_resources(
                scope="directory",
                root_path=path,
                registry_path=registry_path_override,
            ):
                if resource.kind == "mcp":
                    continue
                if resource.id in seen:
                    continue
                resource.tool = tool.id
                seen.add(resource.id)
                out.append(resource)
    return sorted(out, key=lambda item: (item.tool, item.kind, item.name_hint, str(item.path)))


def _discover_mcp_servers(tools: list[DiscoveredTool]) -> list[DiscoveredMcpServer]:
    out: list[DiscoveredMcpServer] = []
    for tool in tools:
        for path in tool.mcp_config_paths:
            for name, raw_config in _read_mcp_servers(path).items():
                if not isinstance(raw_config, dict):
                    continue
                secret_keys = _secret_env_keys(raw_config)
                out.append(
                    DiscoveredMcpServer(
                        id=f"{tool.id}:{path}:{name}",
                        tool=tool.id,
                        name=_slug(name),
                        config_path=path,
                        config=dict(raw_config),
                        secret_keys=secret_keys,
                    )
                )
    return sorted(out, key=lambda item: (item.tool, item.name))


def _capture_file_resource(
    resource: DiscoveredResource,
    *,
    root: Path,
    registry: Registry,
    overwrite: bool,
) -> CapturedResource:
    item_name = _captured_name(resource.tool, resource.kind, resource.name_hint)
    relative_path = Path("resources") / RESOURCE_SUBDIRS[resource.kind] / resource.tool / item_name
    dest = root / relative_path
    warnings: list[str] = []
    if dest.exists() and not overwrite:
        warnings.append("Destination already exists; resource was left unchanged.")
    else:
        _copy_resource_sanitized(resource.path, dest)
    entry = RegistryItem(
        name=item_name,
        kind=resource.kind,
        source="local",
        path=relative_path.as_posix(),
        description=resource.description,
        tags=sorted({resource.tool, CAPTURE_SOURCE, resource.kind}),
        category=CAPTURE_SOURCE,
    )
    registry.upsert(entry)
    return CapturedResource(
        name=item_name,
        kind=resource.kind,
        source=str(resource.path),
        path=relative_path,
        target_tools=[resource.tool],
        warnings=[*resource.warnings, *warnings],
    )


def _capture_mcp_server(
    server: DiscoveredMcpServer,
    *,
    root: Path,
    registry: Registry,
    overwrite: bool,
) -> tuple[CapturedResource, list[SecretPlaceholder]]:
    item_name = _captured_name(server.tool, "mcp", server.name)
    relative_dir = Path("resources") / "mcp" / server.tool / item_name
    dest = root / relative_dir / "mcp.json"
    sanitized = sanitize_mcp_config_for_storage(server.config) or {}
    if overwrite or not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    entry = RegistryItem(
        name=item_name,
        kind="mcp",
        source="local",
        path=relative_dir.as_posix(),
        description=f"MCP server captured from {server.tool}.",
        mcp_config=sanitized,
        tags=sorted({server.tool, CAPTURE_SOURCE, "mcp"}),
        category=CAPTURE_SOURCE,
    )
    registry.upsert(entry)
    placeholders = [
        SecretPlaceholder(
            name=key,
            tool=server.tool,
            resource=item_name,
            purpose=f"MCP env value from {server.config_path}",
        )
        for key in server.secret_keys
    ]
    return (
        CapturedResource(
            name=item_name,
            kind="mcp",
            source=str(server.config_path),
            path=relative_dir,
            target_tools=[server.tool],
            secret_placeholders=[item.name for item in placeholders],
        ),
        placeholders,
    )


def _plan_entry(
    entry: RegistryItem,
    platforms: list[PlatformProfile],
    *,
    force: bool,
    resource_repo_root: Path,
    install_root: Path,
) -> list[DeployPlanItem]:
    items: list[DeployPlanItem] = []
    for platform in platforms:
        if not entry.supports_platform(platform.name):
            continue
        target = platform.resolve_install_path(
            entry.kind,
            entry.install_target_name(platform.name),
        )
        if target is None:
            continue
        action = "create"
        reason = ""
        if entry.kind == "mcp":
            action, reason = _mcp_target_action(
                target,
                entry,
                platform=platform.name,
                force=force,
            )
        elif target.exists():
            if is_lpm_managed(target, resource_key=entry.resource_key) or force:
                source = _deploy_source_path(
                    entry,
                    resource_repo_root=resource_repo_root,
                    install_root=install_root,
                )
                if (
                    source is not None
                    and resource_hash_path(source) == resource_hash_path(target)
                ):
                    action = "skip"
                    reason = "Target already matches the resource content."
                else:
                    action = "update"
            else:
                action = "conflict"
                reason = "Target exists and is not marked as LPM-managed."
        items.append(
            DeployPlanItem(
                name=entry.name,
                kind=entry.kind,
                platform=platform.name,
                target_path=target,
                action=action,
                reason=reason,
            )
        )
    if not items:
        items.append(
            DeployPlanItem(
                name=entry.name,
                kind=entry.kind,
                platform="",
                target_path=Path(),
                action="skip",
                reason="No enabled platform supports this resource type.",
            )
        )
    return items


def _mcp_target_action(
    target: Path,
    entry: RegistryItem,
    *,
    platform: str = "",
    force: bool,
) -> tuple[str, str]:
    if not target.exists():
        return "create", ""
    try:
        servers = list_mcp_servers(target)
    except Exception as exc:  # noqa: BLE001 - invalid target config is a deploy conflict
        return "conflict", f"Cannot read MCP config: {exc}"
    server_name = entry.install_target_name(platform)
    if server_name not in servers:
        return "update", ""
    if is_lpm_managed_mcp(
        target,
        server_name,
        resource_key=entry.resource_key,
    ):
        expected = sanitize_mcp_config_for_storage(entry.mcp_config or {})
        if servers.get(server_name) == expected:
            return "skip", "MCP server already matches the managed configuration."
        return "update", ""
    return ("update", "") if force else ("conflict", "MCP server already exists in target config.")


def _deploy_source_path(
    entry: RegistryItem,
    *,
    resource_repo_root: Path,
    install_root: Path,
) -> Path | None:
    if entry.source in {"local", "owned"} and entry.path:
        return resource_repo_root / entry.path
    install_path = install_root / entry.install_target_name()
    return install_path if install_path.exists() else None


def _deploy_item_paths(
    item: DeployPlanItem,
    *,
    config: Config,
    registry: Registry,
) -> list[Path]:
    paths = [item.target_path]
    entry = registry.get(item.name, item.kind)
    if entry is None:
        return paths
    if entry.kind == "mcp":
        paths.append(mcp_ownership_path())
        return paths
    paths.append(config.install.target_path / entry.install_target_name())
    if entry.source == "external" and entry.subdir:
        paths.append(config.install.target_path / ".lpm" / "clones" / entry.name)
    return paths


def _verify_deploy_item(
    item: DeployPlanItem,
    *,
    config: Config,
    registry: Registry,
) -> None:
    entry = registry.get(item.name, item.kind)
    if entry is None:
        raise RuntimeError(f"Registry entry disappeared during deploy: {item.name}")
    if entry.kind == "mcp":
        expected = sanitize_mcp_config_for_storage(entry.mcp_config or {})
        actual = list_mcp_servers(item.target_path).get(
            entry.install_target_name(item.platform)
        )
        if actual != expected:
            raise RuntimeError(
                f"Deployment verification failed for {item.name} on {item.platform}"
            )
        return

    cache_path = config.install.target_path / entry.install_target_name()
    source_hash = resource_hash_path(cache_path)
    target_hash = resource_hash_path(item.target_path)
    if not source_hash or source_hash != target_hash:
        raise RuntimeError(
            f"Deployment verification failed for {item.name} on {item.platform}"
        )


def _write_managed_markers(items: list[DeployPlanItem], registry: Registry) -> None:
    for item in items:
        if item.action not in {"create", "update"} or item.kind == "mcp":
            continue
        entry = registry.get(item.name, item.kind)
        if entry is None:
            continue
        target = item.target_path
        try:
            write_managed_marker(target, entry, platform=item.platform)
        except OSError:
            continue


def _copy_resource_sanitized(src: Path, dest: Path) -> None:
    src = src.expanduser().resolve()
    if dest.exists():
        _remove_path(dest)
    if src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_sanitized(src, dest)
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_symlink() or _is_copy_excluded(rel):
            continue
        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file_sanitized(path, target)


def _copy_file_sanitized(src: Path, dest: Path) -> None:
    try:
        raw = src.read_bytes()
    except OSError:
        return
    if b"\x00" in raw[: min(len(raw), 4096)]:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    dest.write_text(_sanitize_text(text), encoding="utf-8")


def _sanitize_text(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        return f"{prefix}${{SECRET_VALUE}}"

    return SECRET_VALUE_RE.sub(repl, text)


def _read_mcp_servers(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        if isinstance(data, dict):
            servers = data.get("mcpServers")
            return dict(servers) if isinstance(servers, dict) else data
        return {}
    try:
        return list_mcp_servers(path)
    except Exception:
        return {}


def _secret_env_keys(config: dict[str, Any]) -> list[str]:
    env = config.get("env")
    if not isinstance(env, dict):
        return []
    keys: list[str] = []
    for key, value in env.items():
        if value in ("", None):
            continue
        if SECRET_KEY_RE.search(str(key)) or (isinstance(value, str) and not _is_placeholder(value)):
            keys.append(str(key))
    return sorted(set(keys))


def _build_profile(discovery: EnvDiscoveryResult, captured: list[CapturedResource]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in captured:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    profile = EnvironmentProfile(
        name="default",
        created_at=_timestamp(),
        tools=[
            {
                "id": tool.id,
                "name": tool.name,
                "detected": tool.detected,
                "confidence": tool.confidence,
                "root_path": str(tool.root_path),
                "config_paths": [str(path) for path in tool.config_paths],
                "resource_paths": [str(path) for path in tool.resource_paths],
                "mcp_config_paths": [str(path) for path in tool.mcp_config_paths],
                "supports_kinds": tool.supports_kinds,
            }
            for tool in discovery.tools
        ],
        resource_counts=counts,
    )
    return {
        "version": 1,
        "name": profile.name,
        "created_at": profile.created_at,
        "tools": profile.tools,
        "resource_counts": profile.resource_counts,
    }


def _secrets_payload(secrets: list[SecretPlaceholder]) -> dict[str, Any]:
    seen: set[tuple[str, str, str]] = set()
    items: list[dict[str, str]] = []
    for secret in secrets:
        key = (secret.name, secret.tool, secret.resource)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "name": secret.name,
                "tool": secret.tool,
                "resource": secret.resource,
                "purpose": secret.purpose,
                "value": "",
            }
        )
    return {"version": 1, "secrets": items}


def _load_secret_placeholders(root: Path) -> list[SecretPlaceholder]:
    path = root / SECRETS_EXAMPLE_PATH
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[SecretPlaceholder] = []
    secret_items = data.get("secrets", []) if isinstance(data, dict) else []
    for item in secret_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if os.environ.get(name):
            continue
        out.append(
            SecretPlaceholder(
                name=name,
                tool=str(item.get("tool") or ""),
                resource=str(item.get("resource") or ""),
                purpose=str(item.get("purpose") or ""),
            )
        )
    return out


def _ensure_env_structure(root: Path) -> None:
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "resources").mkdir(parents=True, exist_ok=True)
    for dirname in RESOURCE_SUBDIRS.values():
        (root / "resources" / dirname).mkdir(parents=True, exist_ok=True)
    secrets = root / SECRETS_EXAMPLE_PATH
    if not secrets.exists():
        _write_yaml(secrets, {"version": 1, "secrets": []})


def _supported_kinds(spec: ToolScanSpec) -> list[ItemKind]:
    kinds: set[ItemKind] = set()
    dirs = {item.lower() for item in spec.resource_dirs}
    if "skills" in dirs:
        kinds.add("skill")
    if "rules" in dirs:
        kinds.add("rule")
    if {"prompts", "commands"} & dirs:
        kinds.add("prompt")
    if "plugins" in dirs:
        kinds.add("plugin")
    if spec.mcp_config_files:
        kinds.add("mcp")
    return sorted(kinds)


def _existing_paths(root: Path, values: tuple[str, ...], *, dirs_only: bool = False) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = (root / value).expanduser().resolve()
        if dirs_only:
            if path.is_dir():
                paths.append(path)
        elif path.exists():
            paths.append(path)
    return paths


def _expand_home(value: str, *, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith("~/") or value.startswith("~\\"):
        return (home / value[2:]).resolve()
    return Path(value).expanduser().resolve()


def _captured_name(tool: str, kind: str, name_hint: str) -> str:
    return _slug(f"{tool}-{kind}-{name_hint}")


def _is_copy_excluded(path: Path) -> bool:
    return is_resource_path_excluded(path)


def _is_snapshot_excluded(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    first = rel.parts[0] if rel.parts else ""
    return first in {".git", BACKUP_DIR} or is_resource_path_excluded(rel)


def _is_placeholder(value: str) -> bool:
    value = value.strip()
    return value.startswith("${") and value.endswith("}") and len(value) > 3


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
