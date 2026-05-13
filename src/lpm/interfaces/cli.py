"""LingyePluginMarketplace command-line interface (Typer + Rich)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
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
from ..core.platforms import PlatformProfile, PlatformsConfig, build_platform
from ..core.registry import find_registry_path, load_registry
from ..core.resource_detection import (
    ResourceDetectionError,
    detect_local_resource_type,
    detect_remote_resource,
)
from ..services import publisher
from ..services.installer import (
    SyncAction,
    check_all,
    status_all,
    sync_all,
    sync_one,
    uninstall_one,
)
from ..services.local_resources import export_claude_plugin, import_local_resource
from ..services.resource_repo import (
    init_resource_repo,
    inspect_resource_repo,
    pull_resource_repo,
    push_resource_repo,
    use_resource_repo,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="LPM (LingyePluginMarketplace): publish, register and sync skills, MCP servers and rules across AI coding platforms.",
)
resource_app = typer.Typer(help="Manage the private LPM resource repository.")
app.add_typer(resource_app, name="resource")
console = Console()
VALID_KINDS = {"skill", "mcp", "rule", "prompt", "plugin"}


def _load() -> Config:
    return load_config()


# ---- init ---- #


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config."),
    claude_code: bool = typer.Option(False, "--claude-code", help="Also enable Claude Code platform."),
) -> None:
    """Generate the LPM config file with sensible defaults.

    Usage:
        lpm init                # generate config (Cursor only)
        lpm init --claude-code  # generate config (Cursor + Claude Code)

    Then edit ~/.config/lpm/config.toml to fill in your token and owner.
    Or set the LPM_GITHUB_TOKEN environment variable instead.
    """
    path = default_config_path()
    if path.exists() and not force:
        console.print(f"[yellow]Config already exists at[/yellow] {path}")
        console.print(f"  Use [bold]--force[/bold] to overwrite, or edit it directly: {path}")
        raise typer.Exit(0)

    profiles: list[PlatformProfile] = [build_platform("cursor", {"enabled": True})]
    if claude_code:
        profiles.append(build_platform("claude-code", {"enabled": True}))

    cfg = Config(
        github=GithubConfig(),
        install=InstallConfig(),
        platforms=PlatformsConfig(profiles=profiles),
    )
    written = write_config(cfg)
    console.print(f"[green]Config generated at[/green] {written}")
    console.print()
    console.print("Next steps:")
    console.print(f"  1. Edit [bold]{written}[/bold] to fill in your [bold]token[/bold] and [bold]owner[/bold]")
    console.print(f"     Or set env var: [bold]$env:{CONFIG_ENV_VAR} = \"ghp_xxx\"[/bold]")
    console.print("  2. Run [bold]lpm resource init[/bold] to create/connect your private resource repo")
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
    try:
        info = push_resource_repo(message=message, config=_load())
    except Exception as exc:
        console.print(f"[red]Resource push failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    _print_resource_info(info)


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


def _maybe_push_resource_repo(cfg: Config, *, push: bool, no_push: bool) -> None:
    if push and no_push:
        console.print("[red]Choose only one of --push or --no-push.[/red]")
        raise typer.Exit(2)
    should_push = push
    if not push and not no_push:
        should_push = typer.confirm("Push changes to your private resource repo now?", default=False)
    if not should_push:
        console.print("[yellow]Not pushed.[/yellow] Run `lpm resource push` when ready.")
        return
    try:
        info = push_resource_repo(config=cfg)
    except Exception as exc:
        console.print(f"[red]Resource push failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Pushed[/green] {info.local_path}")


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
    version: str = typer.Option("", "--version", "-v", help="Semantic version, e.g. '1.0.0'."),
    author: str = typer.Option("", "--author", help="Author name."),
    item_license: str = typer.Option("", "--license", help="SPDX license id, e.g. 'MIT'."),
) -> None:
    """Publish a local directory to a new GitHub repository."""
    cfg = _load()

    if kind not in VALID_KINDS:
        console.print(f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule, prompt, plugin.")
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
        choice = typer.prompt(
            "Repository visibility? [public/private]",
            default="private" if default else "public",
        ).strip().lower()
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
        help='MCP server config as JSON string (for --kind mcp).',
    ),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    category: str = typer.Option("", "--category", "-c", help="Category, e.g. 'productivity'."),
) -> None:
    """Register an external (third-party) resource in the registry."""
    cfg = _load()
    if kind not in VALID_KINDS:
        console.print(f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule, prompt, plugin.")
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
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing local resource."),
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
    kind: str = typer.Option("skill", "--kind", "-k", help="Resource type: skill | mcp | rule | prompt | plugin."),
    category: str = typer.Option("", "--category", "-c", help="Stored under <kind>/<category>/<name>."),
    tags: list[str] = typer.Option([], "--tag", "-t", help="Tags for discovery (repeatable)."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing local resource."),
    mcp_config_json: str | None = typer.Option(None, "--mcp-config", help="MCP server config JSON."),
) -> None:
    """Copy a local resource into this repository and register it."""
    if kind not in VALID_KINDS:
        console.print(f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule, prompt, plugin.")
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
    name: str | None = typer.Option(None, "--name", help="Plugin name (defaults to repo folder name)."),
) -> None:
    """Generate .claude-plugin/plugin.json for local skills in this repository."""
    path = export_claude_plugin(plugin_name=name)
    console.print(f"[green]Generated[/green] {path}")


# ---- remove ---- #


@app.command("remove")
def cmd_remove(
    name: str = typer.Argument(...),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete the local installation."
    ),
) -> None:
    """Remove an item from the registry."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name)
    removed = publisher.remove_skill(name)
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
    remote: bool = typer.Option(False, "--remote", "-r", help="Also search GitHub for SKILL.md repos."),
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
            i for i in items
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
    tags_filter: list[str] = typer.Option(None, "--tag", "-t", help="Only sync items with these tags."),
    include_mcp: bool = typer.Option(False, "--include-mcp", help="Also sync MCP configs."),
    include_rule: bool = typer.Option(False, "--include-rule", help="Also sync rules."),
    include_prompt: bool = typer.Option(False, "--include-prompt", help="Also sync prompts."),
    include_plugin: bool = typer.Option(False, "--include-plugin", help="Also sync plugins."),
    all_kinds: bool = typer.Option(False, "--all-kinds", help="Sync every resource kind."),
    platform: str | None = typer.Option(None, "--platform", "-p", help="Only sync to this platform."),
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
    prune: bool = typer.Option(False, "--prune", help="Remove unreachable items from the registry."),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete local files when pruning."
    ),
) -> None:
    """Check reachability of all registered repositories.

    Reports which items point to repositories that no longer exist or are
    inaccessible.  Use ``--prune`` to automatically remove dead entries.
    """
    cfg = _load()
    results, pruned = check_all(
        config=cfg, kind=kind, prune=prune, uninstall=uninstall
    )
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
        console.print(f"\n[green]Pruned {len(pruned)} unreachable item(s):[/green] {', '.join(pruned)}")
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
    issues: list[str] = []

    git_path = shutil.which("git")
    if git_path:
        version = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        console.print(f"[green]git:[/green] {version} ({git_path})")
    else:
        console.print("[red]git not found on PATH.[/red]")
        issues.append("git missing")

    if cfg.github.token:
        console.print("[green]GitHub token: configured[/green]")
    else:
        console.print(
            f"[yellow]No GitHub token configured.[/yellow] Set ${CONFIG_ENV_VAR} or run `lpm init`."
        )

    target = cfg.install.target_path
    try:
        target.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]Install target writable:[/green] {target}")
    except OSError as exc:
        console.print(f"[red]Install target not writable[/red] {target}: {exc}")
        issues.append("install target")

    reg_path = find_registry_path()
    console.print(f"[green]Registry:[/green] {reg_path}")
    resource_info = inspect_resource_repo(cfg)
    console.print(f"[green]Resource repo:[/green] {resource_info.local_path}")
    if not resource_info.exists:
        console.print("[yellow]Resource repo is not initialized.[/yellow] Run `lpm resource init`.")
    elif not resource_info.is_git_repo:
        console.print("[yellow]Resource repo exists but is not a git repository.[/yellow]")

    if cfg.source_path:
        console.print(f"[green]Config:[/green] {cfg.source_path}")
    else:
        console.print(f"[yellow]Config not found at[/yellow] {default_config_path()}")

    console.print("\n[bold]Platforms[/bold]")
    for plat in cfg.platforms.profiles:
        status = "[green]enabled[/green]" if plat.enabled else "[dim]disabled[/dim]"
        console.print(f"  {plat.name}: {status}")
        if plat.enabled:
            sp = plat.skills_path()
            if sp:
                try:
                    sp.mkdir(parents=True, exist_ok=True)
                    console.print(f"    skills_dir: [green]{sp}[/green]")
                except OSError:
                    console.print(f"    skills_dir: [red]{sp} (not writable)[/red]")
                    issues.append(f"{plat.name} skills_dir")
            mp = plat.mcp_json_path()
            if mp:
                console.print(f"    mcp_json: {mp} {'[green](exists)[/green]' if mp.is_file() else '[dim](not yet created)[/dim]'}")
            rp = plat.rules_path()
            if rp:
                console.print(f"    rules_dir: {rp}")

    if issues:
        raise typer.Exit(1)


# ---- uninstall ---- #


@app.command("uninstall")
def cmd_uninstall(name: str = typer.Argument(...)) -> None:
    """Remove an item's local files (without touching the registry)."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name)
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
        console.print(f"[red]Invalid visibility {visibility!r}.[/red] Expected 'public' or 'private'.")
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
            '  claude mcp add lpm -- lpm-mcp\n'
            "Then restart your IDE."
        )
    else:
        console.print("[yellow]Nothing copied. SKILL.md already installed on all platforms.[/yellow]")


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
    for plat in cfg.platforms.profiles:
        table.add_row(
            plat.name,
            "[green]yes[/green]" if plat.enabled else "[dim]no[/dim]",
            plat.skills_dir or "-",
            plat.mcp_json or "-",
            plat.rules_dir or "-",
        )
    console.print(table)


# ---- update ---- #


@app.command("update")
def cmd_update(name: str = typer.Argument(...)) -> None:
    """Force-sync a single item."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name)
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
        ".", "--project", "-p", help="Project root directory (defaults to CWD).",
    ),
    only: list[str] = typer.Option(None, "--only", help="Only link specific items."),
    tags_filter: list[str] = typer.Option(None, "--tag", "-t", help="Only link items with these tags."),
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
        console.print("[yellow]No skill symlinks created (skills may not be installed yet).[/yellow]")
    console.print(
        "\nAI agents in this project will now auto-discover linked skills."
    )


@app.command("unlink")
def cmd_unlink(
    project: Path = typer.Option(
        ".", "--project", "-p", help="Project root directory (defaults to CWD).",
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
