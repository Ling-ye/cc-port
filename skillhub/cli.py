"""SkillHub command-line interface (Typer + Rich)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import publisher
from .config import (
    CONFIG_ENV_VAR,
    Config,
    GithubConfig,
    InstallConfig,
    default_config_path,
    load_config,
    write_config,
)
from .installer import (
    SyncAction,
    status_all,
    sync_all,
    sync_one,
    uninstall_one,
)
from .platforms import PlatformProfile, PlatformsConfig, build_platform
from .registry import find_registry_path, load_registry

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="SkillHub: publish, register and sync skills, MCP servers and rules across Cursor and Claude Code.",
)
console = Console()


def _load() -> Config:
    return load_config()


# ---- init ---- #


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config."),
    claude_code: bool = typer.Option(False, "--claude-code", help="Also enable Claude Code platform."),
) -> None:
    """Generate the SkillHub config file with sensible defaults.

    Usage:
        skillhub init                # generate config (Cursor only)
        skillhub init --claude-code  # generate config (Cursor + Claude Code)

    Then edit ~/.config/skillhub/config.toml to fill in your token and owner.
    Or set the SKILLHUB_GITHUB_TOKEN environment variable instead.
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
    console.print("  2. Run [bold]skillhub doctor[/bold] to verify")
    console.print("  3. Run [bold]skillhub publish <path> -y[/bold] to publish")


# ---- publish ---- #


@app.command("publish")
def cmd_publish(
    path: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    name: str | None = typer.Option(None, "--name", help="Override the item name."),
    description: str | None = typer.Option(None, "--description"),
    kind: str = typer.Option("skill", "--kind", "-k", help="Resource type: skill | mcp | rule."),
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
) -> None:
    """Publish a local directory to a new GitHub repository."""
    cfg = _load()

    if kind not in {"skill", "mcp", "rule"}:
        console.print(f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule.")
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
    kind: str = typer.Option("skill", "--kind", "-k", help="Resource type: skill | mcp | rule."),
    mcp_config_json: str | None = typer.Option(
        None,
        "--mcp-config",
        help='MCP server config as JSON string (for --kind mcp).',
    ),
) -> None:
    """Register an external (third-party) resource in the registry."""
    if kind not in {"skill", "mcp", "rule"}:
        console.print(f"[red]Invalid kind {kind!r}.[/red] Expected: skill, mcp, rule.")
        raise typer.Exit(2)

    mcp_config = None
    if mcp_config_json:
        try:
            mcp_config = json.loads(mcp_config_json)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid --mcp-config JSON:[/red] {exc}")
            raise typer.Exit(2) from exc

    entry = publisher.add_external_skill(
        github_url,
        name=name,
        subdir=subdir,
        ref=ref,
        description=description,
        kind=kind,
        mcp_config=mcp_config,
    )
    console.print(f"[green]Added[/green] {entry.name} ({entry.kind}) ({entry.repo}@{entry.ref})")


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
        console.print("[yellow]Registry is empty.[/yellow] Use `skillhub publish` or `skillhub add`.")
        return
    table = Table(title=f"SkillHub registry ({find_registry_path()})")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Source")
    table.add_column("Repo")
    table.add_column("Ref")
    table.add_column("Subdir")
    table.add_column("Visibility")
    table.add_column("Installed")
    install_root = cfg.install.target_path
    for s in items:
        installed = (install_root / s.install_target_name()).exists()
        visibility_cell = "-"
        if s.source == "owned" and cfg.github.token:
            try:
                from .github_client import GithubClient
                from .publisher import _parse_owner_repo

                owner, repo_name = _parse_owner_repo(s.repo)
                client = GithubClient(cfg.github.token)
                gh_repo = client.get_repo(owner, repo_name)
                if gh_repo is not None:
                    visibility_cell = (
                        "[red]private[/red]" if gh_repo.private else "[green]public[/green]"
                    )
            except Exception:
                visibility_cell = "?"
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
        )
    console.print(table)


# ---- sync ---- #


@app.command("sync")
def cmd_sync(
    only: list[str] = typer.Option(None, "--only", help="Restrict to one or more item names."),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Only sync items of this type."),
    platform: str | None = typer.Option(None, "--platform", "-p", help="Only sync to this platform."),
) -> None:
    """Install or update every item in the registry."""
    cfg = _load()
    results = sync_all(config=cfg, only=only or None, kind=kind, platform_filter=platform)
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
    }
    failures = 0
    for r in results:
        if r.action is SyncAction.FAILED:
            failures += 1
        table.add_row(
            r.name,
            f"[{style[r.action]}]{r.action.value}[/{style[r.action]}]",
            str(r.install_path),
            ", ".join(r.platforms_installed) or "-",
            r.detail,
        )
    console.print(table)
    if failures:
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
    table = Table(title="SkillHub status")
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
            f"[yellow]No GitHub token configured.[/yellow] Set ${CONFIG_ENV_VAR} or run `skillhub init`."
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

    if cfg.source_path:
        console.print(f"[green]Config:[/green] {cfg.source_path}")
    else:
        console.print(f"[yellow]Config not found at[/yellow] {default_config_path()}")

    # Platform checks
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
    """Install SkillHub's own SKILL.md to all enabled platforms.

    Copies the project's SKILL.md (and any companion .md files at repo root)
    into each platform's skills directory under a ``skillhub/`` subdirectory.
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

    # Determine target dirs: explicit target, or all enabled platforms
    if target:
        target_dirs = [target.expanduser() / "skillhub"]
    else:
        target_dirs = []
        for plat in cfg.platforms.enabled():
            sp = plat.skills_path()
            if sp:
                target_dirs.append(sp / "skillhub")
        if not target_dirs:
            target_dirs = [cfg.install.target_path / "skillhub"]

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
        console.print("[green]Installed SkillHub skill files:[/green]")
        for p in total_copied:
            console.print(f"  - {p}")
        console.print(
            "\nNext: register the MCP server in your platform's MCP config. Example for Cursor:\n"
            '  ~/.cursor/mcp.json -> {"mcpServers": {"skillhub": {"command": "skillhub-mcp"}}}\n'
            "Example for Claude Code:\n"
            '  claude mcp add skillhub -- skillhub-mcp\n'
            "Then restart your IDE."
        )
    else:
        console.print("[yellow]Nothing copied. SKILL.md already installed on all platforms.[/yellow]")


# ---- platforms ---- #


@app.command("platforms")
def cmd_platforms() -> None:
    """Show configured platforms and their directories."""
    cfg = _load()
    table = Table(title="SkillHub platforms")
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


def _find_project_root() -> Path:
    """Locate the SkillHub project root by walking up from this file."""
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "SKILL.md").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    return here.parent.parent


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
