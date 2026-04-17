"""SkillHub command-line interface (Typer + Rich)."""

from __future__ import annotations

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
from .registry import find_registry_path, load_registry

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="SkillHub: publish, register and sync Cursor Agent Skills.",
)
console = Console()


def _load() -> Config:
    return load_config()


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config."),
) -> None:
    """Interactively create the SkillHub config file."""
    path = default_config_path()
    if path.exists() and not force:
        console.print(f"[yellow]Config already exists at[/yellow] {path}")
        if not typer.confirm("Overwrite?", default=False):
            raise typer.Exit(0)

    console.print("[bold]Configuring SkillHub[/bold]")
    token = typer.prompt(
        f"GitHub Personal Access Token (or leave empty to use ${CONFIG_ENV_VAR})",
        default="",
        hide_input=True,
        show_default=False,
    ).strip()
    owner = typer.prompt(
        "GitHub owner for new skill repos (empty = your authenticated user)",
        default="",
        show_default=False,
    ).strip()
    repo_prefix = typer.prompt("Prefix for new skill repos", default="cursor-skill-").strip()
    default_private = typer.confirm("Make new skill repos private by default?", default=False)
    install_target = typer.prompt(
        "Install target directory", default="~/.cursor/skills"
    ).strip() or "~/.cursor/skills"

    cfg = Config(
        github=GithubConfig(
            token=token,
            owner=owner,
            repo_prefix=repo_prefix,
            default_private=default_private,
        ),
        install=InstallConfig(target=install_target),
    )
    written = write_config(cfg)
    console.print(f"[green]Config written to[/green] {written}")
    if not token:
        console.print(
            f"[yellow]No token saved.[/yellow] Set [bold]{CONFIG_ENV_VAR}[/bold] in your shell to use GitHub features."
        )


@app.command("publish")
def cmd_publish(
    path: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    name: str | None = typer.Option(None, "--name", help="Override the skill name."),
    description: str | None = typer.Option(None, "--description"),
    private: bool | None = typer.Option(
        None,
        "--private/--public",
        help="Repo visibility. If omitted, you'll be prompted (or pass --yes to use the configured default).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the interactive visibility prompt and use --private/--public or the config default.",
    ),
    update_visibility: bool = typer.Option(
        False,
        "--update-visibility",
        help="If the GitHub repo already exists with a different visibility, change it to match.",
    ),
) -> None:
    """Publish a local skill directory to a new GitHub repository."""
    cfg = _load()

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
        )
    except publisher.VisibilityMismatchError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    visibility = "[red]private[/red]" if result.private else "[green]public[/green]"
    state = "created" if result.created else "updated"
    msg = f"[green]Published[/green] {result.name} -> {result.repo_url} ({visibility}, {state})"
    if result.visibility_changed:
        msg += " [yellow](visibility changed)[/yellow]"
    console.print(msg)


@app.command("add")
def cmd_add(
    github_url: str = typer.Argument(..., help="HTTPS or SSH GitHub URL of the skill repo."),
    name: str | None = typer.Option(None, "--name"),
    subdir: str | None = typer.Option(None, "--subdir", help="Path inside the repo to install."),
    ref: str = typer.Option("main", "--ref"),
    description: str = typer.Option("", "--description"),
) -> None:
    """Register an external (third-party) skill in the registry."""
    entry = publisher.add_external_skill(
        github_url,
        name=name,
        subdir=subdir,
        ref=ref,
        description=description,
    )
    console.print(f"[green]Added[/green] {entry.name} ({entry.repo}@{entry.ref})")


@app.command("remove")
def cmd_remove(
    name: str = typer.Argument(...),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Also delete the local installation."
    ),
) -> None:
    """Remove a skill from the registry."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name)
    removed = publisher.remove_skill(name)
    if removed is None:
        console.print(f"[yellow]No skill named[/yellow] {name}")
        raise typer.Exit(1)
    if uninstall and entry is not None:
        uninstall_one(entry, config=cfg)
    console.print(f"[green]Removed[/green] {name}")


@app.command("list")
def cmd_list() -> None:
    """List skills in the registry along with their local installation state."""
    cfg = _load()
    registry = load_registry()
    if not registry.skills:
        console.print("[yellow]Registry is empty.[/yellow] Use `skillhub publish` or `skillhub add`.")
        return
    table = Table(title=f"SkillHub registry ({find_registry_path()})")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Repo")
    table.add_column("Ref")
    table.add_column("Subdir")
    table.add_column("Visibility")
    table.add_column("Installed")
    install_root = cfg.install.target_path
    for s in registry.skills:
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
        table.add_row(
            s.name,
            s.source,
            s.repo,
            s.ref,
            s.subdir or "-",
            visibility_cell,
            "yes" if installed else "no",
        )
    console.print(table)


@app.command("sync")
def cmd_sync(
    only: list[str] = typer.Option(None, "--only", help="Restrict to one or more skill names."),
) -> None:
    """Install or update every skill in the registry."""
    cfg = _load()
    results = sync_all(config=cfg, only=only or None)
    if not results:
        console.print("[yellow]Nothing to sync.[/yellow]")
        return
    table = Table(title=f"Sync -> {cfg.install.target_path}")
    table.add_column("Name")
    table.add_column("Action")
    table.add_column("Path")
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
            r.detail,
        )
    console.print(table)
    if failures:
        raise typer.Exit(1)


@app.command("status")
def cmd_status() -> None:
    """Show local vs remote commit status for each skill."""
    cfg = _load()
    rows = status_all(config=cfg)
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


@app.command("doctor")
def cmd_doctor() -> None:
    """Check that the environment is ready (git, token, permissions)."""
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

    if issues:
        raise typer.Exit(1)


@app.command("uninstall")
def cmd_uninstall(name: str = typer.Argument(...)) -> None:
    """Remove a skill's local files (without touching the registry)."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name)
    if entry is None:
        console.print(f"[yellow]No skill named[/yellow] {name}")
        raise typer.Exit(1)
    removed = uninstall_one(entry, config=cfg)
    msg = "Uninstalled" if removed else "Nothing to remove"
    console.print(f"[green]{msg}[/green] {name}")


@app.command("set-visibility")
def cmd_set_visibility(
    name: str = typer.Argument(..., help="Name of an `owned` skill in the registry."),
    visibility: str = typer.Argument(..., help="`public` or `private`."),
) -> None:
    """Change the GitHub visibility of an owned skill repository (public <-> private)."""
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


@app.command("install-self")
def cmd_install_self(
    target: Path | None = typer.Option(
        None,
        "--target",
        help="Override install root (defaults to the configured install target).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files."),
) -> None:
    """Install SkillHub's own SKILL.md so the Cursor Agent learns when to use it.

    Copies the project's SKILL.md (and any companion .md files at repo root)
    into <install-target>/skillhub/. Run this once per new machine after
    `pip install -e .`.
    """
    cfg = _load()
    project_root = _find_project_root()
    skill_md = project_root / "SKILL.md"
    if not skill_md.is_file():
        console.print(f"[red]SKILL.md not found at[/red] {skill_md}")
        raise typer.Exit(1)

    install_root = target.expanduser() if target else cfg.install.target_path
    dest = install_root / "skillhub"
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    candidates = [skill_md]
    for extra in ("reference.md", "examples.md"):
        p = project_root / extra
        if p.is_file():
            candidates.append(p)

    for src in candidates:
        out = dest / src.name
        if out.exists() and not force:
            console.print(f"[yellow]skip[/yellow] {out} (use --force to overwrite)")
            continue
        shutil.copy2(src, out)
        copied.append(str(out))

    if copied:
        console.print("[green]Installed SkillHub skill files:[/green]")
        for p in copied:
            console.print(f"  - {p}")
        console.print(
            "\nNext: register the MCP server in [bold]~/.cursor/mcp.json[/bold] "
            'with `{"mcpServers": {"skillhub": {"command": "skillhub-mcp"}}}` '
            "and restart Cursor."
        )
    else:
        console.print("[yellow]Nothing copied. SKILL.md already installed.[/yellow]")


def _find_project_root() -> Path:
    """Locate the SkillHub project root by walking up from this file."""
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "SKILL.md").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    return here.parent.parent


@app.command("update")
def cmd_update(name: str = typer.Argument(...)) -> None:
    """Force-sync a single skill."""
    cfg = _load()
    registry = load_registry()
    entry = registry.get(name)
    if entry is None:
        console.print(f"[yellow]No skill named[/yellow] {name}")
        raise typer.Exit(1)
    result = sync_one(entry, config=cfg)
    color = "green" if result.action is not SyncAction.FAILED else "red"
    console.print(f"[{color}]{result.action.value}[/{color}] {name} -> {result.install_path}")
    if result.detail:
        console.print(result.detail)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    app()
