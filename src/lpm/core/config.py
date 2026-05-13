"""LPM user configuration.

Loads ``~/.config/lpm/config.toml``.  The GitHub token may also come from
the ``LPM_GITHUB_TOKEN`` environment variable (which takes precedence).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

from .platforms import PlatformsConfig, load_platforms_from_dict

CONFIG_ENV_VAR = "LPM_GITHUB_TOKEN"
CONFIG_PATH_ENV_VAR = "LPM_CONFIG"
RESOURCE_HOME_ENV_VAR = "LPM_RESOURCE_HOME"
DEFAULT_CONFIG_RELATIVE = Path(".config/lpm/config.toml")
DEFAULT_INSTALL_TARGET = "~/.cursor/skills"
DEFAULT_REPO_PREFIX = "cursor-skill-"
DEFAULT_RESOURCE_REPO_NAME = "LingyeAIResources"
DEFAULT_RESOURCE_BRANCH = "main"


@dataclass
class GithubConfig:
    token: str = ""
    owner: str = ""
    repo_prefix: str = DEFAULT_REPO_PREFIX
    default_private: bool = False


@dataclass
class InstallConfig:
    target: str = DEFAULT_INSTALL_TARGET

    @property
    def target_path(self) -> Path:
        return Path(self.target).expanduser()


@dataclass
class ResourcesConfig:
    repo_name: str = DEFAULT_RESOURCE_REPO_NAME
    repo_url: str = ""
    local_path: str = ""
    branch: str = DEFAULT_RESOURCE_BRANCH

    @property
    def local_path_value(self) -> Path:
        if self.local_path:
            return Path(self.local_path).expanduser()
        return Path.home() / (self.repo_name or DEFAULT_RESOURCE_REPO_NAME)


@dataclass
class Config:
    github: GithubConfig = field(default_factory=GithubConfig)
    install: InstallConfig = field(default_factory=InstallConfig)
    resources: ResourcesConfig = field(default_factory=ResourcesConfig)
    platforms: PlatformsConfig = field(default_factory=PlatformsConfig)
    source_path: Path | None = None


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_RELATIVE


def load_config(path: Path | None = None, *, apply_env: bool = True) -> Config:
    cfg_path = path or default_config_path()
    data: dict = {}
    if cfg_path.is_file():
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)

    gh_data = data.get("github", {}) or {}
    install_data = data.get("install", {}) or {}
    resources_data = data.get("resources", {}) or {}

    plat_cfg = load_platforms_from_dict(data)

    cfg = Config(
        github=GithubConfig(
            token=str(gh_data.get("token", "") or ""),
            owner=str(gh_data.get("owner", "") or ""),
            repo_prefix=str(gh_data.get("repo_prefix", DEFAULT_REPO_PREFIX) or ""),
            default_private=bool(gh_data.get("default_private", False)),
        ),
        install=InstallConfig(
            target=str(install_data.get("target", DEFAULT_INSTALL_TARGET) or DEFAULT_INSTALL_TARGET),
        ),
        resources=ResourcesConfig(
            repo_name=str(
                resources_data.get("repo_name", DEFAULT_RESOURCE_REPO_NAME)
                or DEFAULT_RESOURCE_REPO_NAME
            ),
            repo_url=str(resources_data.get("repo_url", "") or ""),
            local_path=str(resources_data.get("local_path", "") or ""),
            branch=str(resources_data.get("branch", DEFAULT_RESOURCE_BRANCH) or DEFAULT_RESOURCE_BRANCH),
        ),
        platforms=plat_cfg,
        source_path=cfg_path if cfg_path.is_file() else None,
    )

    if apply_env:
        env_token = os.environ.get(CONFIG_ENV_VAR, "").strip()
        if env_token:
            cfg.github.token = env_token

        env_resource_home = os.environ.get(RESOURCE_HOME_ENV_VAR, "").strip()
        if env_resource_home:
            cfg.resources.local_path = env_resource_home

    return cfg


def load_raw_config(path: Path | None = None) -> Config:
    """Load config.toml without environment overrides.

    Desktop settings editing needs the persisted config, while normal runtime
    calls still use env overrides such as ``LPM_GITHUB_TOKEN``.
    """
    return load_config(path, apply_env=False)


def write_config(cfg: Config, path: Path | None = None) -> Path:
    """Write a config TOML file (used by ``lpm init``)."""
    out = path or default_config_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# LPM (LingyePluginMarketplace) config -- edit this file, then run `lpm doctor` to verify.",
        "",
        "[github]",
        "# GitHub Personal Access Token (repo scope). You can also set the",
        f"# {CONFIG_ENV_VAR} environment variable instead (takes precedence).",
        f'token = "{_escape(cfg.github.token)}"',
        "",
        "# GitHub user or org to create repos under. Leave empty to auto-detect from token.",
        f'owner = "{_escape(cfg.github.owner)}"',
        "",
        f'repo_prefix = "{_escape(cfg.github.repo_prefix)}"',
        f"default_private = {str(cfg.github.default_private).lower()}",
        "",
        "[install]",
        f'target = "{_escape(cfg.install.target)}"',
        "",
        "[resources]",
        "# Private data repository for your selected skills/rules/prompts/MCP/plugins.",
        f'repo_name = "{_escape(cfg.resources.repo_name)}"',
        f'repo_url = "{_escape(cfg.resources.repo_url)}"',
        f'local_path = "{_escape(cfg.resources.local_path)}"',
        f'branch = "{_escape(cfg.resources.branch)}"',
        "",
    ]

    for profile in cfg.platforms.profiles:
        lines.append(f"[platforms.{profile.name}]")
        lines.append(f"enabled = {str(profile.enabled).lower()}")
        lines.append(f'skills_dir = "{_escape(profile.skills_dir)}"')
        lines.append(f'mcp_json = "{_escape(profile.mcp_json)}"')
        lines.append(f'rules_dir = "{_escape(profile.rules_dir)}"')
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    try:
        os.chmod(out, 0o600)
    except OSError:  # pragma: no cover - non-POSIX filesystems
        pass
    return out


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
