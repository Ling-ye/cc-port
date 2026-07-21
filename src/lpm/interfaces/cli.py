"""LingyePluginMarketplace command-line interface (Typer + Rich)."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..core.config import (
    CONFIG_ENV_VAR,
    Config,
    GithubConfig,
    InstallConfig,
    default_config_path,
    load_config,
    write_config,
)
from ..core.platforms import PLATFORM_PRESETS, PlatformProfile, PlatformsConfig, build_platform
from ..core.registry import find_registry_path, load_registry
from ..core.resource_detection import (
    ResourceDetectionError,
    detect_local_resource_type,
    detect_remote_resource,
)
from ..infrastructure import git_ops
from ..services import publisher
from ..services.asset_sync import (
    AssetBatchChoice,
    add_plugin_reference,
    apply_asset_action_plan,
    apply_asset_batch_plan,
    apply_plugin_delete_plan,
    build_asset_action_plan,
    build_asset_batch_plan,
    build_asset_inventory,
    build_plugin_delete_plan,
)
from ..services.doctor import build_doctor_checks, has_doctor_errors
from ..services.installer import (
    SyncAction,
    check_all,
    status_all,
    sync_all,
    sync_one,
    uninstall_one,
)
from ..services.local_resources import export_claude_plugin, import_local_resource
from ..services.operation_history import (
    operation_detail,
    operation_history_page,
    restore_operation,
)
from ..services.plugin_management import (
    add_plugin_project,
    list_plugin_projects,
    remove_plugin_project,
)
from ..services.resource_commit import build_resource_commit_plan
from ..services.resource_manager import resource_install_plan
from ..services.resource_repo import (
    init_resource_repo,
    inspect_resource_repo,
    pull_resource_repo,
    push_resource_repo,
    use_resource_repo,
)
from ..services.resource_sync import (
    apply_resource_sync_plan,
    build_resource_sync_plan,
    cancel_resource_sync_plan,
    cleanup_stale_resource_sync_plan,
    inspect_resource_sync,
    list_stale_resource_sync_plans,
    resolve_resource_sync_plan,
)
from ..services.state_maintenance import (
    delete_orphan_quarantine,
    export_orphan_backup,
    list_maintenance_audits,
    list_orphan_backups,
    list_orphan_quarantines,
    load_maintenance_audit,
    quarantine_orphan_backups,
)
from ..services.state_retention import (
    StateRetentionPlan,
    build_state_retention_plan,
    prune_state,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="LPM (LingyePluginMarketplace): publish, register and sync skills, MCP servers and rules across AI coding platforms.",
)
resource_app = typer.Typer(help="Manage the private LPM resource repository.")
asset_app = typer.Typer(
    help="Inspect and synchronize logical resources across local AI tools and the private repository."
)
operations_app = typer.Typer(help="Inspect and restore persisted local write operations.")
plugin_app = typer.Typer(help="Manage dual-track plugin references and project scan roots.")
plugin_project_app = typer.Typer(help="Manage explicit project roots used by plugin scans.")
plugin_reference_app = typer.Typer(help="Manage plugin references without uploading cache content.")
app.add_typer(resource_app, name="resource")
app.add_typer(asset_app, name="asset")
app.add_typer(operations_app, name="operations")
app.add_typer(plugin_app, name="plugin")
plugin_app.add_typer(plugin_project_app, name="project")
plugin_app.add_typer(plugin_reference_app, name="reference")
console = Console()
VALID_KINDS = {"skill", "mcp", "rule", "prompt", "plugin"}
DEPRECATED_SYNC_MESSAGE = (
    "Deprecated: use `lpm asset list`, `lpm asset plan`, and `lpm asset apply`. "
    "Git workspace sync commands will be removed in the next release."
)


def _load() -> Config:
    cfg = load_config()
    git_ops.configure_git_executable(cfg.git.executable)
    return cfg


def _print_machine_json(data: object) -> None:
    """Write stable JSON without terminal styling or ANSI escape sequences."""
    typer.echo(json.dumps(data, default=str, ensure_ascii=False, indent=2))


def _print_sync_deprecation() -> None:
    console.print(f"[yellow]{DEPRECATED_SYNC_MESSAGE}[/yellow]")


# ---- init ---- #


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config."),
    claude_code: bool = typer.Option(
        False,
        "--claude-code",
        help="Deprecated compatibility flag; all complete platform presets are enabled.",
    ),
) -> None:
    """Generate the LPM config file with sensible defaults.

    Usage:
        lpm init                # generate config (all complete platform presets)

    Then edit ~/.config/lpm/config.toml to fill in your token and owner.
    Or set the LPM_GITHUB_TOKEN environment variable instead.
    """
    path = default_config_path()
    if path.exists() and not force:
        console.print(f"[yellow]Config already exists at[/yellow] {path}")
        console.print(f"  Use [bold]--force[/bold] to overwrite, or edit it directly: {path}")
        raise typer.Exit(0)

    profiles: list[PlatformProfile] = [
        build_platform(name, {"enabled": True}) for name in PLATFORM_PRESETS
    ]

    cfg = Config(
        github=GithubConfig(),
        install=InstallConfig(),
        platforms=PlatformsConfig(profiles=profiles),
    )
    written = write_config(cfg)
    console.print(f"[green]Config generated at[/green] {written}")
    console.print()
    console.print("Next steps:")
    console.print(
        f"  1. Edit [bold]{written}[/bold] to fill in your [bold]token[/bold] and [bold]owner[/bold]"
    )
    console.print(f'     Or set env var: [bold]$env:{CONFIG_ENV_VAR} = "ghp_xxx"[/bold]')
    console.print(
        "  2. Run [bold]lpm resource init[/bold] to create/connect your private resource repo"
    )
    console.print("  3. Run [bold]lpm doctor[/bold] to verify")


# ---- resource repo ---- #


@resource_app.command("init")
def cmd_resource_init(
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Private GitHub resource repo name. Defaults to config or LingyeAIResources.",
    ),
) -> None:
    """Create/connect the private resource repo and generate its structure."""
    cfg = _load()
    repo_name = name
    if repo_name is None and not cfg.resources.repo_url:
        repo_name = typer.prompt("Resource repository name", default=cfg.resources.repo_name)
    try:
        info = init_resource_repo(name=repo_name, config=cfg)
    except Exception as exc:
        console.print(f"[red]Resource init failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("use")
def cmd_resource_use(
    target: str = typer.Argument(..., help="Existing local path or Git URL for the resource repo."),
) -> None:
    """Bind LPM to an existing private resource repository."""
    try:
        info = use_resource_repo(target, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource use failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("status")
def cmd_resource_status() -> None:
    """Show private resource repository configuration and git state."""
    _print_resource_info(inspect_resource_repo(_load()))


@resource_app.command("pull")
def cmd_resource_pull() -> None:
    """Pull the private resource repository after checking it is clean."""
    _print_sync_deprecation()
    try:
        info = pull_resource_repo(_load())
    except Exception as exc:
        console.print(f"[red]Resource pull failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("push")
def cmd_resource_push(
    message: str = typer.Option("lpm: update resources", "--message", "-m"),
) -> None:
    """Commit local resource changes if needed and push the private repo."""
    _print_sync_deprecation()
    try:
        info = push_resource_repo(message=message, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource push failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


@resource_app.command("commit-plan")
def cmd_resource_commit_plan() -> None:
    """Preview resource-level changes and safety blockers before committing."""
    try:
        plan = build_resource_commit_plan(config=_load())
    except Exception as exc:
        console.print(f"[red]Resource commit planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    table = Table(title="LPM resource commit plan")
    table.add_column("Resource")
    table.add_column("Kind")
    table.add_column("Action")
    table.add_column("Paths")
    for item in plan.resources:
        table.add_row(item.name, item.kind, item.action, ", ".join(item.paths))
    console.print(table)
    if plan.blocked_paths or plan.secret_findings:
        blocked = Table(title="Blocked resource changes")
        blocked.add_column("Path")
        blocked.add_column("Reason")
        for item in [*plan.blocked_paths, *plan.secret_findings]:
            blocked.add_row(item.path, item.reason)
        console.print(blocked)
    console.print(f"Suggested message: {plan.suggested_message}")


@resource_app.command("sync-status")
def cmd_resource_sync_status(
    fetch: bool = typer.Option(False, "--fetch", help="Fetch remote refs before reporting."),
) -> None:
    """Show ahead/behind/diverged state without changing the working tree."""
    _print_sync_deprecation()
    try:
        plan = inspect_resource_sync(config=_load(), fetch=fetch)
    except Exception as exc:
        console.print(f"[red]Resource sync status failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-plan")
def cmd_resource_sync_plan() -> None:
    """Fetch and build a safe fast-forward or three-way merge plan."""
    _print_sync_deprecation()
    try:
        plan = build_resource_sync_plan(config=_load())
    except Exception as exc:
        console.print(f"[red]Resource sync planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-resolve")
def cmd_resource_sync_resolve(
    operation_id: str = typer.Argument(..., help="Operation id returned by sync-plan."),
    choices: Path = typer.Option(
        ...,
        "--choices",
        exists=True,
        dir_okay=False,
        readable=True,
        help="YAML file mapping conflict ids to local or incoming.",
    ),
) -> None:
    """Resolve a persisted three-way merge plan."""
    _print_sync_deprecation()
    raw = yaml.safe_load(choices.read_text(encoding="utf-8")) or {}
    values = raw.get("items", raw) if isinstance(raw, dict) else {}
    if not isinstance(values, dict):
        console.print("[red]Choices must be a YAML mapping.[/red]")
        raise typer.Exit(2)
    try:
        plan = resolve_resource_sync_plan(
            operation_id,
            {str(key): str(value) for key, value in values.items()},
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Resource sync resolution failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-apply")
def cmd_resource_sync_apply(
    operation_id: str = typer.Argument(..., help="Operation id returned by sync-plan."),
) -> None:
    """Apply a ready sync plan to the resource repository."""
    _print_sync_deprecation()
    try:
        plan = apply_resource_sync_plan(operation_id, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource sync apply failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-cancel")
def cmd_resource_sync_cancel(
    operation_id: str = typer.Argument(..., help="Operation id returned by sync-plan."),
) -> None:
    """Cancel a pending sync plan and remove its temporary worktree."""
    _print_sync_deprecation()
    try:
        plan = cancel_resource_sync_plan(operation_id, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource sync cancellation failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


@resource_app.command("sync-stale")
def cmd_resource_sync_stale(
    min_age_hours: float = typer.Option(
        24,
        "--min-age-hours",
        min=0,
        help="Only show pending worktrees at least this old.",
    ),
) -> None:
    """List abandoned-looking merge worktrees without modifying them."""
    _print_sync_deprecation()
    plans = list_stale_resource_sync_plans(min_age_hours=min_age_hours)
    table = Table(title="Stale resource sync worktrees")
    table.add_column("Operation")
    table.add_column("Status")
    table.add_column("Age hours")
    table.add_column("Worktree")
    for plan in plans:
        table.add_row(
            plan.operation_id,
            plan.status,
            str(plan.age_hours),
            str(plan.worktree_path),
        )
    console.print(table)


@resource_app.command("sync-cleanup")
def cmd_resource_sync_cleanup(
    operation_id: str = typer.Argument(...),
    force: bool = typer.Option(
        False,
        "--force",
        help="Abandon a newer pending plan instead of requiring stale age.",
    ),
) -> None:
    """Explicitly abandon a pending sync plan and remove its worktree."""
    _print_sync_deprecation()
    try:
        plan = cleanup_stale_resource_sync_plan(
            operation_id,
            force=force,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Resource sync cleanup failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_sync_plan(plan)


# ---- asset-level sync ---- #


@plugin_project_app.command("list")
def cmd_plugin_project_list(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List the explicit project roots available to plugin scans."""
    projects = list_plugin_projects(_load())
    if json_output:
        _print_machine_json([asdict(item) for item in projects])
        return
    table = Table(title="LPM plugin projects")
    table.add_column("ID", style="bold")
    table.add_column("Path")
    table.add_column("Git identity")
    table.add_column("Mode")
    for item in projects:
        table.add_row(
            item.id,
            str(item.path),
            f"{item.repo}{('/' + item.subdir) if item.subdir else ''}" or "-",
            "portable" if item.portable else "observe-only",
        )
    console.print(table)


@plugin_project_app.command("add")
def cmd_plugin_project_add(
    path: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Add one explicit project root; no home-directory recursion is performed."""
    project = add_plugin_project(path)
    if json_output:
        _print_machine_json(asdict(project))
    else:
        console.print(
            f"[bold]{project.id}[/bold] {project.path} "
            f"({'portable' if project.portable else 'observe-only: no Git remote'})"
        )


@plugin_project_app.command("remove")
def cmd_plugin_project_remove(
    project_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Remove a project scan mapping without touching the project directory."""
    project = remove_plugin_project(project_id)
    if json_output:
        _print_machine_json(asdict(project))
    else:
        console.print(f"Removed project mapping [bold]{project.id}[/bold]; files were not changed.")


@plugin_reference_app.command("add")
def cmd_plugin_reference_add(
    platform: str = typer.Option(..., "--platform", help="codex | claude-code | opencode"),
    plugin_id: str = typer.Option(..., "--plugin-id"),
    origin_type: str = typer.Option(..., "--origin", help="marketplace | npm | git"),
    scope: str = typer.Option("user", "--scope", help="user | project | local | managed"),
    marketplace: str = typer.Option("", "--marketplace"),
    source: str = typer.Option("", "--source"),
    package: str = typer.Option("", "--package"),
    repo: str = typer.Option("", "--repo"),
    selector: str = typer.Option("", "--selector"),
    observed_version: str = typer.Option("", "--observed-version"),
    project_id: str = typer.Option("", "--project"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    name: str = typer.Option("", "--name"),
    description: str = typer.Option("", "--description"),
    push: bool = typer.Option(True, "--push/--no-push"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Store desired plugin source/state without uploading installed cache content."""
    try:
        result = add_plugin_reference(
            platform=platform,
            plugin_id=plugin_id,
            origin_type=origin_type,
            scope=scope,
            enabled=enabled,
            marketplace=marketplace,
            source=source,
            package=package,
            repo=repo,
            selector=selector,
            observed_version=observed_version,
            project_id=project_id,
            name=name,
            description=description,
            push=push,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Plugin reference add failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        _print_machine_json(asdict(result))
    else:
        console.print(f"[bold]{result.status}[/bold] {result.resource_key}")


@plugin_app.command("delete")
def cmd_plugin_delete(
    resource_key: str = typer.Argument(..., help="Composite plugin resource key."),
    instance: list[str] = typer.Option([], "--instance", help="Instance id; repeatable."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the uninstall plan only."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm selected instance removal."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Uninstall selected instances before marking their remote desired state removed."""
    try:
        plan = build_plugin_delete_plan(
            resource_key,
            selected_instance_ids=instance or None,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Plugin delete planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output and dry_run:
        _print_machine_json(asdict(plan))
        return
    if not json_output:
        table = Table(title=f"Plugin delete {resource_key}")
        table.add_column("Instance", style="bold")
        table.add_column("Scope")
        table.add_column("Method")
        table.add_column("Selectable")
        table.add_column("Detail")
        for item in plan.instances:
            table.add_row(item.id, item.scope, item.method, str(item.selectable).lower(), item.detail)
        console.print(table)
    if dry_run:
        return
    if plan.blocked:
        console.print("[red]" + "; ".join(plan.blockers) + "[/red]")
        raise typer.Exit(1)
    if not yes and not typer.confirm(
        f"Uninstall {len(plan.selected_instance_ids)} plugin instance(s)?",
        default=False,
    ):
        console.print("[yellow]Plugin delete cancelled.[/yellow]")
        return
    result = apply_plugin_delete_plan(
        resource_key,
        selected_instance_ids=plan.selected_instance_ids,
        expected_plan_hash=plan.plan_hash,
        config=_load(),
    )
    if json_output:
        _print_machine_json(asdict(result))
    else:
        console.print(f"[bold]{result.status}[/bold] {resource_key}")
        for item in result.results:
            console.print(f"{item.status}: {item.message}")
    if result.status != "succeeded":
        raise typer.Exit(1)


@asset_app.command("list")
def cmd_asset_list(
    scan_local: bool = typer.Option(
        False,
        "--scan-local",
        help="Scan configured and detected platforms for unregistered assets and extra instances.",
    ),
    refresh_remote: bool = typer.Option(
        True,
        "--refresh-remote/--cached-remote",
        help="Fetch the configured branch before building the inventory.",
    ),
    scan_global: bool = typer.Option(
        True,
        "--global/--no-global",
        help="Include or exclude global plugin locations when --scan-local is used.",
    ),
    project: list[str] = typer.Option(
        [],
        "--project",
        help="Saved plugin project id. Repeatable; an empty list scans every saved project.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """List one logical resource row with nested local tool instances."""
    try:
        inventory = build_asset_inventory(
            config=_load(),
            scan_local=scan_local,
            refresh_remote=refresh_remote,
            scan_global=scan_global,
            project_ids=project or None,
        )
    except Exception as exc:
        console.print(f"[red]Asset inventory failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        payload = asdict(inventory)
        payload.pop("rows", None)
        _print_machine_json(payload)
        return

    table = Table(title=f"LPM assets ({inventory.branch or 'unconfigured branch'})")
    table.add_column("Resource", style="bold")
    table.add_column("Description")
    table.add_column("Local")
    table.add_column("Remote")
    table.add_column("Status")
    table.add_column("Actions")
    for row in inventory.resources:
        table.add_row(
            row.resource_key,
            row.description or "-",
            row.local_status,
            row.remote_status,
            row.status,
            ", ".join(row.available_actions) or "-",
        )
    console.print(table)
    if inventory.remote_warning:
        console.print(f"[yellow]{inventory.remote_warning}[/yellow]")
    if inventory.legacy_write_blocker:
        console.print(f"[red]Remote writes blocked:[/red] {inventory.legacy_write_blocker}")


@asset_app.command("plan")
def cmd_asset_plan(
    action: str = typer.Argument(
        ...,
        help="download | upload | copy-to-local | copy-to-remote | set-platform-install-name",
    ),
    kind: str = typer.Option(..., "--kind", "-k", help="Asset kind."),
    name: str = typer.Option(..., "--name", "-n", help="Asset name."),
    platform: str = typer.Option(..., "--platform", "-p", help="Platform id."),
    local_instance_id: str = typer.Option(
        "",
        "--local-instance-id",
        help="Required when a platform has multiple local instances.",
    ),
    new_name: str = typer.Option(
        "",
        "--new-name",
        help="New asset name for copy-to-local or copy-to-remote.",
    ),
    new_install_name: str = typer.Option(
        "",
        "--new-install-name",
        help="Platform install alias for set-platform-install-name.",
    ),
    overwrite_unmanaged: bool = typer.Option(
        False,
        "--overwrite-unmanaged",
        help="Explicitly allow replacing an unmanaged local target.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Persist one revalidatable asset action plan."""
    if kind not in VALID_KINDS:
        console.print(f"[red]Unsupported resource kind:[/red] {kind}")
        raise typer.Exit(2)
    try:
        plan = build_asset_action_plan(
            action,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            platform=platform,
            local_instance_id=local_instance_id,
            new_name=new_name,
            new_install_name=new_install_name,
            overwrite_unmanaged=overwrite_unmanaged,
            config=_load(),
        )
    except Exception as exc:
        console.print(f"[red]Asset planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        _print_machine_json(asdict(plan))
        return
    table = Table(title="LPM asset action plan")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for label, value in (
        ("Operation", plan.operation_id),
        ("Action", plan.action),
        ("Source", plan.resource_key),
        ("Target", plan.target_resource_key),
        ("Platform", plan.platform),
        ("Remote commit", plan.remote_commit),
        ("Blocked", str(plan.blocked).lower()),
    ):
        table.add_row(label, str(value))
    console.print(table)
    for warning in plan.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    for blocker in plan.blockers:
        console.print(f"[red]Blocked:[/red] {blocker}")


@asset_app.command("apply")
def cmd_asset_apply(
    operation_id: str = typer.Argument(..., help="Operation id returned by asset plan."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Revalidate and apply one persisted asset action plan."""
    try:
        result = apply_asset_action_plan(operation_id, config=_load())
    except Exception as exc:
        console.print(f"[red]Asset apply failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output:
        _print_machine_json(asdict(result))
    else:
        console.print(
            f"[bold]{result.status}[/bold] {result.target_resource_key} "
            f"on {result.platform}: {result.message}"
        )
        for warning in result.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
    if result.status not in {"succeeded", "unchanged"}:
        raise typer.Exit(1)


@asset_app.command("upload")
def cmd_asset_upload(
    resource: list[str] = typer.Option(
        [], "--resource", "-r", help="Logical resource key. Repeatable."
    ),
    all_resources: bool = typer.Option(
        False, "--all", help="Upload every scanned logical resource."
    ),
    choices: Path | None = typer.Option(None, "--choices", help="YAML batch choices file."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the current plan without writing."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the interactive confirmation."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Upload selected local resources in one remote commit."""
    _run_asset_batch_command(
        "upload",
        resource_keys=resource,
        all_resources=all_resources,
        platforms=[],
        choices_path=choices,
        dry_run=dry_run,
        yes=yes,
        json_output=json_output,
    )


@asset_app.command("download")
def cmd_asset_download(
    resource: list[str] = typer.Option(
        [], "--resource", "-r", help="Logical resource key. Repeatable."
    ),
    all_resources: bool = typer.Option(
        False, "--all", help="Download every remote logical resource."
    ),
    platform: list[str] = typer.Option(
        [], "--platform", "-p", help="Enabled target AI tool. Repeatable."
    ),
    choices: Path | None = typer.Option(None, "--choices", help="YAML batch choices file."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the current plan without writing."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the interactive confirmation."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Download selected remote resources to one or more enabled AI tools."""
    if not platform:
        console.print("[red]Select at least one target with --platform.[/red]")
        raise typer.Exit(2)
    _run_asset_batch_command(
        "download",
        resource_keys=resource,
        all_resources=all_resources,
        platforms=platform,
        choices_path=choices,
        dry_run=dry_run,
        yes=yes,
        json_output=json_output,
    )


def _run_asset_batch_command(
    direction: str,
    *,
    resource_keys: list[str],
    all_resources: bool,
    platforms: list[str],
    choices_path: Path | None,
    dry_run: bool,
    yes: bool,
    json_output: bool,
) -> None:
    cfg = _load()
    keys = list(dict.fromkeys(item.strip() for item in resource_keys if item.strip()))
    if all_resources:
        inventory = build_asset_inventory(config=cfg, scan_local=True, refresh_remote=True)
        keys = [
            item.resource_key
            for item in inventory.resources
            if direction == "upload" or item.remote.exists
        ]
    if not keys:
        console.print("[red]Select at least one resource with --resource or --all.[/red]")
        raise typer.Exit(2)
    batch_choices = _load_asset_batch_choices(choices_path)
    try:
        plan = build_asset_batch_plan(
            direction,
            resource_keys=keys,
            target_platforms=platforms,
            choices=batch_choices,
            config=cfg,
        )
    except Exception as exc:
        console.print(f"[red]Asset batch planning failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if json_output and dry_run:
        _print_machine_json(asdict(plan))
        return
    if not json_output:
        _print_asset_batch_plan(plan)
    if dry_run:
        return
    has_manual = any(item.disposition == "manual" for item in plan.items)
    if plan.executable_count == 0 and not has_manual:
        console.print("[red]The plan has no executable items.[/red]")
        raise typer.Exit(1)
    if plan.blocked_count:
        console.print("[red]Resolve or remove blocked items before applying.[/red]")
        raise typer.Exit(1)
    if plan.executable_count and not yes and not typer.confirm(
        f"Apply {plan.executable_count} {direction} action(s)?",
        default=False,
    ):
        console.print("[yellow]Batch cancelled.[/yellow]")
        return
    result = apply_asset_batch_plan(
        direction,
        resource_keys=keys,
        target_platforms=platforms,
        choices=batch_choices,
        expected_plan_hash=plan.plan_hash,
        config=cfg,
    )
    if json_output:
        _print_machine_json(asdict(result))
    else:
        console.print(f"[bold]{result.status}[/bold]")
        for item in result.results:
            console.print(
                f"{item.status}: {item.target_resource_key}"
                f"{f' on {item.platform}' if item.platform else ''} - {item.message}"
            )
    if result.status not in {"succeeded"}:
        raise typer.Exit(1)


def _load_asset_batch_choices(path: Path | None) -> list[AssetBatchChoice]:
    if path is None:
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_items = payload.get("items", payload) if isinstance(payload, dict) else payload
    choices: list[AssetBatchChoice] = []
    if isinstance(raw_items, dict):
        iterable = [
            {"resource_key": key, **(value if isinstance(value, dict) else {"resolution": value})}
            for key, value in raw_items.items()
        ]
    elif isinstance(raw_items, list):
        iterable = raw_items
    else:
        raise ValueError("Batch choices must be a mapping or list.")
    for item in iterable:
        if not isinstance(item, dict) or not str(item.get("resource_key") or "").strip():
            continue
        choices.append(
            AssetBatchChoice(
                resource_key=str(item["resource_key"]).strip(),
                platform=str(item.get("platform") or "").strip(),
                local_instance_id=str(item.get("local_instance_id") or "").strip(),
                resolution=str(item.get("resolution") or "overwrite").strip(),
                new_name=str(item.get("new_name") or "").strip(),
                overwrite_unmanaged=bool(item.get("overwrite_unmanaged", False)),
                plugin_track=str(item.get("plugin_track") or "").strip(),
                ownership_confirmed=bool(item.get("ownership_confirmed", False)),
                reference_origin={
                    str(key): str(value)
                    for key, value in (item.get("reference_origin") or {}).items()
                }
                if isinstance(item.get("reference_origin"), dict)
                else {},
                plugin_dependencies={
                    str(key): str(value)
                    for key, value in (item.get("plugin_dependencies") or {}).items()
                }
                if isinstance(item.get("plugin_dependencies"), dict)
                else {},
            )
        )
    return choices


def _print_asset_batch_plan(plan: object) -> None:
    table = Table(title=f"LPM asset batch {getattr(plan, 'direction', '')}")
    table.add_column("Resource", style="bold")
    table.add_column("Platform")
    table.add_column("Action")
    table.add_column("Plan")
    table.add_column("Reason")
    for item in getattr(plan, "items", []):
        table.add_row(
            getattr(item, "resource_key", ""),
            getattr(item, "platform", "") or "-",
            getattr(item, "action", ""),
            getattr(item, "disposition", ""),
            getattr(item, "reason", "") or "-",
        )
    console.print(table)
    console.print(
        f"Executable: {getattr(plan, 'executable_count', 0)}; "
        f"blocked: {getattr(plan, 'blocked_count', 0)}; "
        f"skipped: {getattr(plan, 'skipped_count', 0)}"
        f"; manual: {sum(item.disposition == 'manual' for item in getattr(plan, 'items', []))}"
    )


def _print_resource_info(info: object) -> None:
    table = Table(title="LPM resource repository")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field in (
        "repo_name",
        "local_path",
        "registry_path",
        "repo_url",
        "remote_url",
        "branch",
        "current_branch",
        "exists",
        "is_git_repo",
        "dirty",
    ):
        value = getattr(info, field)
        table.add_row(field, str(value))
    console.print(table)


def _print_resource_sync_plan(plan: object) -> None:
    table = Table(title="LPM resource Git synchronization")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field in (
        "operation_id",
        "status",
        "branch",
        "local_commit",
        "remote_commit",
        "merge_base",
        "ahead",
        "behind",
        "merge_commit",
        "detail",
    ):
        table.add_row(field, str(getattr(plan, field)))
    console.print(table)
    conflicts = plan.conflicts
    if conflicts:
        conflict_table = Table(title="Conflicts")
        conflict_table.add_column("Id")
        conflict_table.add_column("Path")
        conflict_table.add_column("Reason")
        for conflict in conflicts:
            conflict_table.add_row(conflict.id, conflict.path, conflict.reason)
        console.print(conflict_table)


def _maybe_push_resource_repo(cfg: Config, *, push: bool, no_push: bool) -> None:
    if push and no_push:
        console.print("[red]Choose only one of --push or --no-push.[/red]")
        raise typer.Exit(2)
    should_push = push
    if not push and not no_push:
        should_push = typer.confirm(
            "Push changes to your private resource repo now?", default=False
        )
    if not should_push:
        console.print("[yellow]Not pushed.[/yellow] Run `lpm resource push` when ready.")
        return
    try:
        info = push_resource_repo(config=cfg)
    except Exception as exc:
        console.print(f"[red]Resource push failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Pushed[/green] {info.local_path}")


# ---- persisted operations ---- #


@operations_app.command("list")
def cmd_operations_list(
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    offset: int = typer.Option(0, "--offset", min=0),
) -> None:
    """List local write operations in reverse chronological order."""
    page = operation_history_page(offset=offset, limit=limit)
    table = Table(title="LPM operation history")
    table.add_column("Operation")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Changed")
    table.add_column("Started")
    table.add_column("Restorable")
    for item in page.operations:
        table.add_row(
            item.operation_id,
            item.kind,
            item.status,
            str(item.changed_target_count),
            item.started_at,
            "yes" if item.restorable else "no",
        )
    console.print(table)
    console.print(
        f"Showing {len(page.operations)} of {page.total} operation(s) from offset {page.offset}."
    )


@operations_app.command("show")
def cmd_operations_show(
    operation_id: str = typer.Argument(...),
) -> None:
    """Show one operation including metadata and target details."""
    try:
        detail = operation_detail(operation_id)
    except Exception as exc:
        console.print(f"[red]Operation detail failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_machine_json(asdict(detail))


@operations_app.command("restore")
def cmd_operations_restore(
    operation_id: str = typer.Argument(...),
    force: bool = typer.Option(
        False,
        "--force",
        help="Restore even when targets changed after the original operation.",
    ),
) -> None:
    """Restore a successful operation to its before-state."""
    try:
        result = restore_operation(operation_id, force=force)
    except Exception as exc:
        console.print(f"[red]Operation restore failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Restored[/green] {result.source_operation_id} "
        f"with operation {result.operation.operation_id}"
    )


@operations_app.command("retention-plan")
def cmd_operations_retention_plan(
    retention_days: int | None = typer.Option(None, "--retention-days", min=0),
    keep_latest: int | None = typer.Option(None, "--keep-latest", min=0),
    max_backup_mb: int | None = typer.Option(None, "--max-backup-mb", min=0),
) -> None:
    """Preview operation and backup cleanup without deleting anything."""
    plan = build_state_retention_plan(
        config=_load(),
        retention_days=retention_days,
        keep_latest_operations=keep_latest,
        max_backup_mb=max_backup_mb,
    )
    _print_retention_plan(plan)


@operations_app.command("prune")
def cmd_operations_prune(
    operation_id: list[str] = typer.Option(
        [],
        "--operation-id",
        help="Delete only this eligible operation. Repeatable; defaults to all candidates.",
    ),
    retention_days: int | None = typer.Option(None, "--retention-days", min=0),
    keep_latest: int | None = typer.Option(None, "--keep-latest", min=0),
    max_backup_mb: int | None = typer.Option(None, "--max-backup-mb", min=0),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip cleanup confirmation."),
) -> None:
    """Explicitly delete operations selected by a fresh retention plan."""
    cfg = _load()
    plan = build_state_retention_plan(
        config=cfg,
        retention_days=retention_days,
        keep_latest_operations=keep_latest,
        max_backup_mb=max_backup_mb,
    )
    selected = operation_id or [item.operation_id for item in plan.candidates]
    _print_retention_plan(plan)
    if not selected:
        console.print("[yellow]No operations are eligible for cleanup.[/yellow]")
        return
    if not yes and not typer.confirm(
        f"Delete {len(selected)} operation record(s) and their backups?",
        default=False,
    ):
        console.print("[yellow]Cleanup cancelled.[/yellow]")
        return
    try:
        result = prune_state(
            selected,
            config=cfg,
            retention_days=retention_days,
            keep_latest_operations=keep_latest,
            max_backup_mb=max_backup_mb,
        )
    except Exception as exc:
        console.print(f"[red]State cleanup failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Deleted[/green] {len(result.deleted_operation_ids)} operation(s); "
        f"reclaimed {result.reclaimed_bytes} bytes."
    )
    if result.failed:
        for failure in result.failed:
            console.print(f"[red]{failure.operation_id}:[/red] {failure.error}")
    console.print(f"Audit: {result.audit_path}")


def _print_retention_plan(plan: StateRetentionPlan) -> None:
    summary = Table(title="LPM state retention plan")
    summary.add_column("Field")
    summary.add_column("Value")
    for field in (
        "operation_count",
        "running_operation_count",
        "protected_operation_count",
        "operation_record_bytes",
        "backup_bytes",
        "orphan_backup_count",
        "orphan_backup_bytes",
        "candidate_count",
        "reclaimable_bytes",
        "projected_backup_bytes",
    ):
        summary.add_row(field, str(getattr(plan, field)))
    console.print(summary)
    if plan.candidates:
        candidates = Table(title="Cleanup candidates")
        candidates.add_column("Operation")
        candidates.add_column("Kind")
        candidates.add_column("Age days")
        candidates.add_column("Bytes")
        candidates.add_column("Reasons")
        for item in plan.candidates:
            candidates.add_row(
                item.operation_id,
                item.kind,
                str(item.age_days),
                str(item.reclaimable_bytes),
                ", ".join(item.reasons),
            )
        console.print(candidates)


@operations_app.command("orphans")
def cmd_operations_orphans() -> None:
    """List backup entries that have no valid operation record."""
    table = Table(title="Orphan backups")
    table.add_column("Name")
    table.add_column("Kind")
    table.add_column("Bytes")
    table.add_column("Modified")
    for item in list_orphan_backups():
        table.add_row(
            item.name,
            item.kind,
            str(item.size_bytes),
            item.modified_at,
        )
    console.print(table)


@operations_app.command("orphan-export")
def cmd_operations_orphan_export(
    name: str = typer.Argument(...),
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Export one orphan backup to a ZIP without following symlinks."""
    try:
        result = export_orphan_backup(name, output_path=out, config=_load())
    except Exception as exc:
        console.print(f"[red]Orphan export failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Exported[/green] {result.name} to {result.output_path}")


@operations_app.command("orphan-quarantine")
def cmd_operations_orphan_quarantine(
    name: list[str] = typer.Option(
        [],
        "--name",
        help="Orphan backup name. Repeat for multiple items.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Move explicitly selected orphan backups into quarantine."""
    if not name:
        console.print("[red]Select at least one orphan with --name.[/red]")
        raise typer.Exit(2)
    if not yes and not typer.confirm(
        f"Quarantine {len(set(name))} orphan backup(s)?",
        default=False,
    ):
        console.print("[yellow]Quarantine cancelled.[/yellow]")
        return
    try:
        result = quarantine_orphan_backups(name, config=_load())
    except Exception as exc:
        console.print(f"[red]Orphan quarantine failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Quarantined[/green] {result.quarantine.item_count} item(s) "
        f"as {result.quarantine.quarantine_id}"
    )
    console.print(f"Audit: {result.audit_path}")


@operations_app.command("quarantines")
def cmd_operations_quarantines() -> None:
    """List orphan backup quarantine batches."""
    table = Table(title="Orphan backup quarantines")
    table.add_column("Quarantine")
    table.add_column("Items")
    table.add_column("Bytes")
    table.add_column("Created")
    for item in list_orphan_quarantines():
        table.add_row(
            item.quarantine_id,
            str(item.item_count),
            str(item.size_bytes),
            item.created_at,
        )
    console.print(table)


@operations_app.command("quarantine-delete")
def cmd_operations_quarantine_delete(
    quarantine_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Permanently delete one orphan backup quarantine batch."""
    if not yes and not typer.confirm(
        f"Permanently delete quarantine {quarantine_id}?",
        default=False,
    ):
        console.print("[yellow]Delete cancelled.[/yellow]")
        return
    result = delete_orphan_quarantine(quarantine_id, config=_load())
    if not result.deleted:
        console.print(f"[red]Quarantine delete failed:[/red] {result.error}")
        raise typer.Exit(1)
    console.print(
        f"[green]Deleted[/green] {quarantine_id}; reclaimed {result.reclaimed_bytes} bytes."
    )
    console.print(f"Audit: {result.audit_path}")


@operations_app.command("audits")
def cmd_operations_audits(
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """List state maintenance audit records."""
    table = Table(title="State maintenance audits")
    table.add_column("Audit")
    table.add_column("Action")
    table.add_column("Status")
    table.add_column("Items")
    table.add_column("Reclaimed")
    table.add_column("Created")
    for item in list_maintenance_audits(limit=limit):
        table.add_row(
            item.audit_id,
            item.action,
            item.status,
            str(item.item_count),
            str(item.reclaimed_bytes),
            item.created_at,
        )
    console.print(table)


@operations_app.command("audit")
def cmd_operations_audit(
    audit_id: str = typer.Argument(...),
) -> None:
    """Show one state maintenance audit record."""
    try:
        payload = load_maintenance_audit(audit_id)
    except Exception as exc:
        console.print(f"[red]Maintenance audit failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_machine_json(payload)


# ---- publish ---- #


@app.command("publish")
def cmd_publish(
    path: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    name: str | None = typer.Option(None, "--name", help="Override the item name."),
    description: str | None = typer.Option(None, "--description"),
    kind: str = typer.Option(
        "skill", "--kind", "-k", help="Resource type: skill | mcp | rule | prompt | plugin."
    ),
    private: bool | None = typer.Option(
        None,
        "--private/--public",
        help="Repo visibility. If omitted, you'll be prompted.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the interactive visibility prompt.",
    ),
    update_visibility: bool = typer.Option(
        False,
        "--update-visibility",
        help="If the GitHub repo already exists with a different visibility, change it.",
    ),
    mcp_config_json: str | None = typer.Option(
        None,
        "--mcp-config",
        help='MCP server config as JSON string (for --kind mcp), e.g. \'{"command":"npx","args":["-y","@mcp/test"]}\'.',
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    category: str = typer.Option("", "--category", "-c", help="Category, e.g. 'software-dev'."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    version: str = typer.Option("", "--version", "-v", help="Semantic version, e.g. '1.0.0'."),
    author: str = typer.Option("", "--author", help="Author name."),
    item_license: str = typer.Option("", "--license", help="SPDX license id, e.g. 'MIT'."),
) -> None:
    """Publish a local directory to a new GitHub repository."""
    cfg = _load()

    if kind not in VALID_KINDS:
        console.print(
            f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule, prompt, plugin."
        )
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    if private is None and not yes:
        default = cfg.github.default_private
        choice = (
            typer.prompt(
                "Repository visibility? [public/private]",
                default="private" if default else "public",
            )
            .strip()
            .lower()
        )
        if choice in {"private", "priv", "p"}:
            private = True
        elif choice in {"public", "pub"}:
            private = False
        else:
            console.print(f"[red]Invalid choice {choice!r}.[/red] Expected 'public' or 'private'.")
            raise typer.Exit(2)

    try:
        result = publisher.publish_local_skill(
            path,
            config=cfg,
            name=name,
            description=description,
            private=private,
            update_visibility=update_visibility,
            kind=kind,
            mcp_config=mcp_config,
            tags=tags or None,
            category=category,
            platforms=platforms or None,
            version=version,
            author=author,
            item_license=item_license,
        )
    except publisher.VisibilityMismatchError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    visibility = "[red]private[/red]" if result.private else "[green]public[/green]"
    state = "created" if result.created else "updated"
    msg = f"[green]Published[/green] {result.name} ({kind}) -> {result.repo_url} ({visibility}, {state})"
    if result.visibility_changed:
        msg += " [yellow](visibility changed)[/yellow]"
    console.print(msg)


# ---- add ---- #


@app.command("add")
def cmd_add(
    github_url: str = typer.Argument(..., help="HTTPS or SSH GitHub URL of the repo."),
    name: str | None = typer.Option(None, "--name"),
    subdir: str | None = typer.Option(None, "--subdir", help="Path inside the repo to install."),
    ref: str = typer.Option("main", "--ref"),
    description: str = typer.Option("", "--description"),
    kind: str = typer.Option(
        "skill", "--kind", "-k", help="Resource type: skill | mcp | rule | prompt | plugin."
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Skip remote repository reachability check.",
    ),
    mcp_config_json: str | None = typer.Option(
        None,
        "--mcp-config",
        help="MCP server config as JSON string (for --kind mcp).",
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    category: str = typer.Option("", "--category", "-c", help="Category, e.g. 'productivity'."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
) -> None:
    """Register an external (third-party) resource in the registry."""
    cfg = _load()
    if kind not in VALID_KINDS:
        console.print(
            f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule, prompt, plugin."
        )
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    try:
        entry = publisher.add_external_skill(
            github_url,
            name=name,
            subdir=subdir,
            ref=ref,
            description=description,
            kind=kind,
            mcp_config=mcp_config,
            skip_verify=no_verify,
            token=cfg.github.token or None,
            tags=tags or None,
            category=category,
            platforms=platforms or None,
        )
    except publisher.RepoUnreachableError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Added[/green] {entry.name} ({entry.kind}) ({entry.repo}@{entry.ref})")


@app.command("collect")
def cmd_collect(
    github_url: str = typer.Argument(..., help="HTTPS or SSH GitHub URL of the repo."),
    resource_type: str | None = typer.Option(
        None,
        "--type",
        help="Override detected type: skill, mcp, rule, prompt, plugin.",
    ),
    name: str | None = typer.Option(None, "--name", help="Override the resource name."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    push: bool = typer.Option(False, "--push", help="Push private resource repo without asking."),
    no_push: bool = typer.Option(False, "--no-push", help="Do not push private resource repo."),
) -> None:
    """Collect a third-party resource by recording its upstream URL only."""
    cfg = _load()
    try:
        detected = detect_remote_resource(
            github_url,
            explicit_type=resource_type,
            token=cfg.github.token or None,
        )
        entry = publisher.add_external_skill(
            detected.repo_url,
            name=name or detected.name_hint,
            subdir=detected.subdir,
            ref=detected.ref,
            kind=detected.kind,
            skip_verify=False,
            token=cfg.github.token or None,
            tags=detected.tags,
            platforms=platforms or None,
        )
    except (ValueError, ResourceDetectionError, publisher.RepoUnreachableError) as exc:
        console.print(f"[red]Collect failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Collected[/green] {entry.name} ({entry.kind}) -> {entry.repo}"
        f"{f'/{entry.subdir}' if entry.subdir else ''}"
    )
    _maybe_push_resource_repo(cfg, push=push, no_push=no_push)


@app.command("upload")
def cmd_upload(
    path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=True),
    resource_type: str | None = typer.Option(
        None,
        "--type",
        help="Override detected type: skill, mcp, rule, prompt, plugin.",
    ),
    name: str | None = typer.Option(None, "--name", help="Override the resource name."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing local resource."
    ),
    push: bool = typer.Option(False, "--push", help="Push private resource repo without asking."),
    no_push: bool = typer.Option(False, "--no-push", help="Do not push private resource repo."),
) -> None:
    """Upload a local resource into the private resource repo."""
    cfg = _load()
    try:
        kind = detect_local_resource_type(path, explicit_type=resource_type)
        result = import_local_resource(
            path,
            kind=kind,
            name=name,
            platforms=platforms or None,
            overwrite=force,
        )
    except Exception as exc:
        console.print(f"[red]Upload failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Uploaded[/green] {result.entry.name} ({result.entry.kind}) -> {result.entry.path}"
    )
    _maybe_push_resource_repo(cfg, push=push, no_push=no_push)


@app.command("import-local")
def cmd_import_local(
    path: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=True),
    name: str | None = typer.Option(None, "--name", help="Override the item name."),
    description: str | None = typer.Option(None, "--description"),
    kind: str = typer.Option(
        "skill", "--kind", "-k", help="Resource type: skill | mcp | rule | prompt | plugin."
    ),
    category: str = typer.Option(
        "", "--category", "-c", help="Stored under <kind>/<category>/<name>."
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    platforms: list[str] = typer.Option(
        [],
        "--platform",
        "-p",
        help="Restrict installation to these platforms (repeatable).",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing local resource."
    ),
    mcp_config_json: str | None = typer.Option(
        None, "--mcp-config", help="MCP server config JSON."
    ),
) -> None:
    """Copy a local resource into this repository and register it."""
    if kind not in VALID_KINDS:
        console.print(
            f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule, prompt, plugin."
        )
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    try:
        result = import_local_resource(
            path,
            kind=kind,
            name=name,
            description=description,
            category=category,
            tags=tags or None,
            platforms=platforms or None,
            overwrite=force,
            mcp_config=mcp_config,
        )
    except Exception as exc:
        console.print(f"[red]Import failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Imported[/green] {result.entry.name} ({result.entry.kind}) -> {result.entry.path}"
    )


@app.command("export-plugin")
def cmd_export_plugin(
    name: str | None = typer.Option(
        None,
        "--name",
        help="Plugin name (defaults to a kebab-case form of the repo folder name).",
    ),
) -> None:
    """Generate .claude-plugin/plugin.json for local skills in this repository."""
    path = export_claude_plugin(plugin_name=name)
    console.print(f"[green]Generated[/green] {path}")


# ---- remove ---- #


@app.command("remove")
def cmd_remove(
    name: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Resource type."),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete the local installation."
    ),
) -> None:
    """Remove an item from the registry."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name, kind)
    removed = publisher.remove_skill(name, kind=kind)
    if removed is None:
        console.print(f"[yellow]No item named[/yellow] {name}")
        raise typer.Exit(1)
    if uninstall and entry is not None:
        uninstall_one(entry, config=cfg)
    console.print(f"[green]Removed[/green] {name}")


# ---- list ---- #


@app.command("list")
def cmd_list(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
) -> None:
    """List items in the registry along with their local installation state."""
    cfg = _load()
    registry = load_registry()
    items = registry.items
    if kind:
        items = [i for i in items if i.kind == kind]
    if not items:
        console.print("[yellow]Registry is empty.[/yellow] Use `lpm publish` or `lpm add`.")
        return
    table = Table(title=f"LPM registry ({find_registry_path()})")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Source")
    table.add_column("Repo")
    table.add_column("Ref")
    table.add_column("Subdir")
    table.add_column("Visibility")
    table.add_column("Installed")
    table.add_column("Reachable")
    install_root = cfg.install.target_path
    for s in items:
        installed = (install_root / s.install_target_name()).exists()
        if s.private is True:
            visibility_cell = "[red]private[/red]"
        elif s.private is False:
            visibility_cell = "[green]public[/green]"
        else:
            visibility_cell = "-"
        if s.reachable is True:
            reachable_cell = "[green]yes[/green]"
        elif s.reachable is False:
            reachable_cell = "[red]no[/red]"
        else:
            reachable_cell = "[dim]-[/dim]"
        kind_style = {"skill": "cyan", "mcp": "magenta", "rule": "yellow"}.get(s.kind, "")
        table.add_row(
            s.name,
            f"[{kind_style}]{s.kind}[/{kind_style}]" if kind_style else s.kind,
            s.source,
            s.repo,
            s.ref,
            s.subdir or "-",
            visibility_cell,
            "yes" if installed else "no",
            reachable_cell,
        )
    console.print(table)


# ---- search ---- #


@app.command("search")
def cmd_search(
    query: str = typer.Argument("", help="Search query (matches name, description, tags)."),
    tags_filter: list[str] = typer.Option([], "--tag", "-t", help="Filter by tag (repeatable)."),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category."),
    remote: bool = typer.Option(
        False, "--remote", "-r", help="Also search GitHub for SKILL.md repos."
    ),
) -> None:
    """Search the local registry (and optionally GitHub) for resources.

    Examples:
        lpm search python
        lpm search --tag testing --kind skill
        lpm search fastapi --remote
    """
    registry = load_registry()
    items = registry.items

    if kind:
        items = [i for i in items if i.kind == kind]
    if category:
        items = [i for i in items if i.category and category.lower() in i.category.lower()]
    if tags_filter:
        tag_set = {t.lower() for t in tags_filter}
        items = [i for i in items if tag_set & {t.lower() for t in i.tags}]
    if query:
        q = query.lower()
        items = [
            i
            for i in items
            if q in i.name.lower()
            or q in i.description.lower()
            or any(q in t.lower() for t in i.tags)
        ]

    if items:
        table = Table(title="Local results")
        table.add_column("Name", style="bold")
        table.add_column("Kind")
        table.add_column("Description")
        table.add_column("Tags")
        table.add_column("Category")
        for s in items:
            kind_style = {"skill": "cyan", "mcp": "magenta", "rule": "yellow"}.get(s.kind, "")
            table.add_row(
                s.name,
                f"[{kind_style}]{s.kind}[/{kind_style}]" if kind_style else s.kind,
                (s.description[:60] + "...") if len(s.description) > 60 else s.description or "-",
                ", ".join(s.tags) if s.tags else "-",
                s.category or "-",
            )
        console.print(table)
    else:
        console.print("[yellow]No local matches.[/yellow]")

    if remote:
        _search_github(query or "SKILL.md")


def _search_github(query: str) -> None:
    """Search GitHub for repos containing SKILL.md (best-effort)."""
    try:
        import urllib.parse
        import urllib.request

        search_q = urllib.parse.quote(f"{query} filename:SKILL.md")
        url = f"https://api.github.com/search/repositories?q={search_q}&per_page=10&sort=stars"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import json as _json

            data = _json.loads(resp.read())

        repos = data.get("items", [])
        if not repos:
            console.print("[yellow]No remote results on GitHub.[/yellow]")
            return

        table = Table(title="GitHub results (add with `lpm add <url>`)")
        table.add_column("Repository", style="bold")
        table.add_column("Stars")
        table.add_column("Description")
        for r in repos:
            table.add_row(
                r.get("html_url", ""),
                str(r.get("stargazers_count", 0)),
                (r.get("description") or "-")[:70],
            )
        console.print(table)
    except Exception as exc:
        console.print(f"[yellow]GitHub search failed:[/yellow] {exc}")


# ---- sync ---- #


@app.command("sync")
def cmd_sync(
    only: list[str] = typer.Option(None, "--only", help="Restrict to one or more item names."),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Only sync items of this type."),
    tags_filter: list[str] = typer.Option(
        None, "--tag", "-t", help="Only sync items with these tags."
    ),
    include_mcp: bool = typer.Option(False, "--include-mcp", help="Also sync MCP configs."),
    include_rule: bool = typer.Option(False, "--include-rule", help="Also sync rules."),
    include_prompt: bool = typer.Option(False, "--include-prompt", help="Also sync prompts."),
    include_plugin: bool = typer.Option(False, "--include-plugin", help="Also sync plugins."),
    all_kinds: bool = typer.Option(False, "--all-kinds", help="Sync every resource kind."),
    platform: str | None = typer.Option(
        None, "--platform", "-p", help="Only sync to this platform."
    ),
) -> None:
    """Install or update registry items.

    By default this syncs skills only. MCP/rule/prompt/plugin resources are
    opt-in because they can modify tool configuration or agent behavior.
    """
    cfg = _load()
    include_kinds = set()
    if include_mcp:
        include_kinds.add("mcp")
    if include_rule:
        include_kinds.add("rule")
    if include_prompt:
        include_kinds.add("prompt")
    if include_plugin:
        include_kinds.add("plugin")
    results = sync_all(
        config=cfg,
        only=only or None,
        kind=kind,
        tags=tags_filter or None,
        include_optional=all_kinds,
        include_kinds=include_kinds,
        platform_filter=platform,
    )
    if not results:
        console.print("[yellow]Nothing to sync.[/yellow]")
        return
    table = Table(title=f"Sync -> {cfg.install.target_path}")
    table.add_column("Name")
    table.add_column("Action")
    table.add_column("Path")
    table.add_column("Platforms")
    table.add_column("Operation")
    table.add_column("Detail")
    style = {
        SyncAction.INSTALLED: "green",
        SyncAction.UPDATED: "cyan",
        SyncAction.UNCHANGED: "dim",
        SyncAction.SKIPPED: "yellow",
        SyncAction.FAILED: "red",
        SyncAction.REPO_GONE: "red bold",
    }
    failures = 0
    repo_gone = 0
    for r in results:
        if r.action is SyncAction.FAILED:
            failures += 1
        elif r.action is SyncAction.REPO_GONE:
            repo_gone += 1
        table.add_row(
            r.name,
            f"[{style[r.action]}]{r.action.value}[/{style[r.action]}]",
            str(r.install_path),
            ", ".join(r.platforms_installed) or "-",
            r.operation_id or "-",
            r.detail,
        )
    console.print(table)
    if repo_gone:
        console.print(
            f"\n[yellow]{repo_gone} repo(s) appear to have been deleted.[/yellow] "
            "Run [bold]lpm check --prune[/bold] to clean up."
        )
    if failures or repo_gone:
        raise typer.Exit(1)


@app.command("plan-install")
def cmd_plan_install(
    name: str = typer.Argument(..., help="Registered resource name."),
    platform: str | None = typer.Option(
        None, "--platform", "-p", help="Only plan for this platform."
    ),
) -> None:
    """Build an install plan without writing local files."""
    try:
        plan = resource_install_plan(name, config=_load(), platform_filter=platform)
    except Exception as exc:
        console.print(f"[red]Install plan failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[bold]{plan.name}[/bold] ({plan.kind})")
    console.print(f"Source: {plan.source_path}")
    if plan.manifest_path:
        console.print(f"Manifest: {plan.manifest_path}")
    if plan.warnings:
        for warning in plan.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")

    target_table = Table(title="Install targets")
    target_table.add_column("Platform")
    target_table.add_column("Mechanism")
    target_table.add_column("Auto")
    target_table.add_column("Path")
    for target in plan.targets:
        target_table.add_row(
            target.platform,
            target.install_mechanism,
            "yes" if target.auto_install else "manual",
            str(target.path),
        )
    console.print(target_table)
    console.print(f"Files after filtering: {len(plan.files)}")


# ---- status ---- #


@app.command("status")
def cmd_status(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
) -> None:
    """Show local vs remote commit status for each item."""
    cfg = _load()
    rows = status_all(config=cfg, kind=kind)
    if not rows:
        console.print("[yellow]Registry is empty.[/yellow]")
        return
    table = Table(title="LPM status")
    table.add_column("Name")
    table.add_column("Installed")
    table.add_column("Local")
    table.add_column("Remote")
    table.add_column("Update?")
    for s in rows:
        table.add_row(
            s.name,
            "yes" if s.installed else "no",
            (s.local_commit or "-")[:10],
            (s.remote_commit or "-")[:10],
            "[red]yes[/red]" if s.has_update else "no",
        )
    console.print(table)


# ---- check ---- #


@app.command("check")
def cmd_check(
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by resource type."),
    prune: bool = typer.Option(
        False, "--prune", help="Remove unreachable items from the registry."
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete local files when pruning."
    ),
) -> None:
    """Check reachability of all registered repositories.

    Reports which items point to repositories that no longer exist or are
    inaccessible.  Use ``--prune`` to automatically remove dead entries.
    """
    cfg = _load()
    results, pruned = check_all(config=cfg, kind=kind, prune=prune, uninstall=uninstall)
    if not results:
        console.print("[yellow]Registry is empty.[/yellow]")
        return

    table = Table(title="LPM Health Check")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Repo")
    table.add_column("Status")
    unreachable = 0
    for r in results:
        if r.reachable:
            status = "[green]reachable[/green]"
        else:
            unreachable += 1
            status = "[red]NOT FOUND[/red]"
        kind_style = {"skill": "cyan", "mcp": "magenta", "rule": "yellow"}.get(r.kind, "")
        table.add_row(
            r.name,
            f"[{kind_style}]{r.kind}[/{kind_style}]" if kind_style else r.kind,
            r.repo,
            status,
        )
    console.print(table)

    if pruned:
        console.print(
            f"\n[green]Pruned {len(pruned)} unreachable item(s):[/green] {', '.join(pruned)}"
        )
    elif unreachable:
        console.print(
            f"\n[yellow]{unreachable} unreachable item(s) found.[/yellow] "
            "Run [bold]lpm check --prune[/bold] to remove them."
        )

    if unreachable and not prune:
        raise typer.Exit(1)


# ---- doctor ---- #


@app.command("doctor")
def cmd_doctor() -> None:
    """Check that the environment is ready (git, token, permissions, platforms)."""
    cfg = _load()
    checks = build_doctor_checks(cfg)
    general_checks = [
        check for check in checks if not str(check.get("id", "")).startswith("platform:")
    ]
    platform_checks = [
        check for check in checks if str(check.get("id", "")).startswith("platform:")
    ]

    for check in general_checks:
        _print_doctor_check(check)
    console.print(f"[green]Registry:[/green] {escape(str(find_registry_path()))}")
    console.print("\n[bold]Platforms[/bold]")
    for check in platform_checks:
        _print_doctor_check(check, indent="  ")

    if has_doctor_errors(checks):
        raise typer.Exit(1)


def _print_doctor_check(check: dict, *, indent: str = "") -> None:
    status = str(check.get("status") or ("ok" if check.get("ok") else "error"))
    style = {
        "ok": "green",
        "warning": "yellow",
        "error": "red",
        "skipped": "dim",
    }.get(status, "white")
    label = escape(str(check.get("label") or check.get("id") or "Check"))
    detail = escape(str(check.get("detail") or ""))
    console.print(f"{indent}[{style}]{label}:[/{style}] {detail}")


# ---- uninstall ---- #


@app.command("uninstall")
def cmd_uninstall(
    name: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Resource type."),
) -> None:
    """Remove an item's local files (without touching the registry)."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name, kind)
    if entry is None:
        console.print(f"[yellow]No item named[/yellow] {name}")
        raise typer.Exit(1)
    removed = uninstall_one(entry, config=cfg)
    msg = "Uninstalled" if removed else "Nothing to remove"
    console.print(f"[green]{msg}[/green] {name}")


# ---- set-visibility ---- #


@app.command("set-visibility")
def cmd_set_visibility(
    name: str = typer.Argument(..., help="Name of an `owned` item in the registry."),
    visibility: str = typer.Argument(..., help="`public` or `private`."),
) -> None:
    """Change the GitHub visibility of an owned repository (public <-> private)."""
    cfg = _load()
    v = visibility.strip().lower()
    if v not in {"public", "private"}:
        console.print(
            f"[red]Invalid visibility {visibility!r}.[/red] Expected 'public' or 'private'."
        )
        raise typer.Exit(2)
    private = v == "private"
    try:
        result = publisher.set_skill_visibility(name, config=cfg, private=private)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    label = "[red]private[/red]" if result["private"] else "[green]public[/green]"
    console.print(f"[green]Updated[/green] {result['full_name']} -> {label}")


# ---- install-self ---- #


@app.command("install-self")
def cmd_install_self(
    target: Path | None = typer.Option(
        None,
        "--target",
        help="Override install root (defaults to all enabled platform skill dirs).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files."),
) -> None:
    """Install LPM's own SKILL.md to all enabled platforms.

    Copies the project's SKILL.md (and any companion .md files at repo root)
    into each platform's skills directory under a ``lpm/`` subdirectory.
    """
    cfg = _load()
    project_root = _find_project_root()
    skill_md = project_root / "SKILL.md"
    if not skill_md.is_file():
        console.print(f"[red]SKILL.md not found at[/red] {skill_md}")
        raise typer.Exit(1)

    candidates = [skill_md]
    for extra in ("reference.md", "examples.md"):
        p = project_root / extra
        if p.is_file():
            candidates.append(p)

    if target:
        target_dirs = [target.expanduser() / "lpm"]
    else:
        target_dirs = []
        for plat in cfg.platforms.enabled():
            sp = plat.skills_path()
            if sp:
                target_dirs.append(sp / "lpm")
        if not target_dirs:
            target_dirs = [cfg.install.target_path / "lpm"]

    total_copied: list[str] = []
    for dest in target_dirs:
        dest.mkdir(parents=True, exist_ok=True)
        for src in candidates:
            out = dest / src.name
            if out.exists() and not force:
                console.print(f"[yellow]skip[/yellow] {out} (use --force to overwrite)")
                continue
            shutil.copy2(src, out)
            total_copied.append(str(out))

    if total_copied:
        console.print("[green]Installed LPM skill files:[/green]")
        for p in total_copied:
            console.print(f"  - {p}")
        console.print(
            "\nNext: register the MCP server in your platform's MCP config. Example for Cursor:\n"
            '  ~/.cursor/mcp.json -> {"mcpServers": {"lpm": {"command": "lpm-mcp"}}}\n'
            "Example for Claude Code:\n"
            "  claude mcp add lpm -- lpm-mcp\n"
            "Then restart your IDE."
        )
    else:
        console.print(
            "[yellow]Nothing copied. SKILL.md already installed on all platforms.[/yellow]"
        )


# ---- platforms ---- #


@app.command("platforms")
def cmd_platforms() -> None:
    """Show configured platforms and their directories."""
    cfg = _load()
    table = Table(title="LPM platforms")
    table.add_column("Platform", style="bold")
    table.add_column("Enabled")
    table.add_column("Skills Dir")
    table.add_column("MCP Config")
    table.add_column("Rules Dir")
    table.add_column("Plugins Dir")
    for plat in cfg.platforms.profiles:
        table.add_row(
            plat.name,
            "[green]yes[/green]" if plat.enabled else "[dim]no[/dim]",
            plat.skills_dir or "-",
            plat.mcp_json or "-",
            plat.rules_dir or "-",
            plat.plugins_dir or "-",
        )
    console.print(table)


# ---- update ---- #


@app.command("update")
def cmd_update(
    name: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Resource type."),
) -> None:
    """Force-sync a single item."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name, kind)
    if entry is None:
        console.print(f"[yellow]No item named[/yellow] {name}")
        raise typer.Exit(1)
    result = sync_one(entry, config=cfg)
    color = "green" if result.action is not SyncAction.FAILED else "red"
    console.print(f"[{color}]{result.action.value}[/{color}] {name} -> {result.install_path}")
    if result.platforms_installed:
        console.print(f"  Platforms: {', '.join(result.platforms_installed)}")
    if result.detail:
        console.print(result.detail)


# ---- link / unlink ---- #


@app.command("link")
def cmd_link(
    project: Path = typer.Option(
        ".",
        "--project",
        "-p",
        help="Project root directory (defaults to CWD).",
    ),
    only: list[str] = typer.Option(None, "--only", help="Only link specific items."),
    tags_filter: list[str] = typer.Option(
        None, "--tag", "-t", help="Only link items with these tags."
    ),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Only link items of this type."),
) -> None:
    """Link registry skills into a project for AI auto-discovery.

    Creates .cursor/rules/lpm-skills.md (Cursor Rule index) and symlinks
    in .cursor/skills/ pointing to globally-installed skill directories.
    AI agents reading the rule file will automatically know which skills
    are available and when to use them.
    """
    from ..services.linker import link

    cfg = _load()
    project_path = project.resolve()

    linked, rule_path = link(
        project_path, cfg, only=only or None, tags=tags_filter or None, kind=kind
    )

    console.print(f"[green]Rule index:[/green] {rule_path}")
    if linked:
        console.print(f"[green]Linked {len(linked)} skill(s):[/green] {', '.join(linked)}")
    else:
        console.print(
            "[yellow]No skill symlinks created (skills may not be installed yet).[/yellow]"
        )
    console.print("\nAI agents in this project will now auto-discover linked skills.")


@app.command("unlink")
def cmd_unlink(
    project: Path = typer.Option(
        ".",
        "--project",
        "-p",
        help="Project root directory (defaults to CWD).",
    ),
) -> None:
    """Remove all LPM links and the skill index from a project."""
    from ..services.linker import unlink

    project_path = project.resolve()
    removed, rule_removed = unlink(project_path)

    if removed:
        console.print(f"[green]Removed {len(removed)} symlink(s):[/green] {', '.join(removed)}")
    if rule_removed:
        console.print("[green]Removed[/green] lpm-skills.md rule file")
    if not removed and not rule_removed:
        console.print("[yellow]Nothing to unlink.[/yellow]")


def _find_project_root() -> Path:
    """Locate the LPM project root by walking up from this file."""
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "SKILL.md").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    return here.parent.parent


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
