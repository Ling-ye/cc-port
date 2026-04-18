from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lpm.config import Config, GithubConfig, InstallConfig
from lpm.platforms import PlatformProfile, PlatformsConfig

_NO_PROMPT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GCM_INTERACTIVE": "never",
    "GIT_CONFIG_NOSYSTEM": "1",
}
"""Environment overrides that prevent git from ever opening a credential popup."""


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Create a minimal valid skill directory."""
    d = tmp_path / "demo-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: A demo skill used for tests.\n"
        "---\n\n"
        "# Demo skill\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    p = tmp_path / "registry.yaml"
    p.write_text("version: 2\nitems: []\n", encoding="utf-8")
    return p


@pytest.fixture
def registry_v1_path(tmp_path: Path) -> Path:
    """A v1-format registry for migration testing."""
    p = tmp_path / "registry.yaml"
    p.write_text("version: 1\nskills: []\n", encoding="utf-8")
    return p


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Reusable Config fixture pointing at temp directories."""
    return Config(
        github=GithubConfig(token=""),
        install=InstallConfig(target=str(tmp_path / "skills")),
        platforms=PlatformsConfig(profiles=[
            PlatformProfile(
                name="cursor",
                enabled=True,
                skills_dir=str(tmp_path / "cursor-skills"),
            ),
        ]),
    )


@pytest.fixture
def fake_remote_repo(tmp_path: Path) -> Path:
    """Create a bare git repo with one commit, suitable for cloning."""
    pytest.importorskip("yaml")
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not installed")

    import os

    env = {**os.environ, **_NO_PROMPT_ENV}

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "SKILL.md").write_text(
        "---\nname: upstream-skill\ndescription: Upstream test skill.\n---\n# Hi\n",
        encoding="utf-8",
    )
    env_args = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-b", "main"], cwd=upstream, check=True, env=env)
    subprocess.run(["git", *env_args, "add", "-A"], cwd=upstream, check=True, env=env)
    subprocess.run(
        ["git", *env_args, "commit", "-m", "init"], cwd=upstream, check=True, env=env,
    )

    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "--bare", str(upstream), str(bare)], check=True, env=env,
    )
    return bare
