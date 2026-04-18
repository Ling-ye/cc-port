from __future__ import annotations

from pathlib import Path

from lpm.publisher import add_external_skill, remove_skill
from lpm.registry import load_registry


def test_add_and_remove_external(registry_path: Path) -> None:
    entry = add_external_skill(
        "https://github.com/anthropics/skills",
        name="pdf",
        subdir="pdf",
        ref="main",
        description="PDF tools",
        registry_path=registry_path,
        skip_verify=True,
    )
    assert entry.name == "pdf"
    assert entry.subdir == "pdf"
    assert entry.kind == "skill"

    reg = load_registry(registry_path)
    assert len(reg.items) == 1
    assert reg.items[0].source == "external"

    removed = remove_skill("pdf", registry_path=registry_path)
    assert removed is not None
    assert load_registry(registry_path).items == []


def test_add_mcp_external(registry_path: Path) -> None:
    entry = add_external_skill(
        "https://github.com/someone/mcp-github",
        name="github-mcp",
        kind="mcp",
        mcp_config={"command": "npx", "args": ["-y", "@mcp/github"]},
        registry_path=registry_path,
        skip_verify=True,
    )
    assert entry.kind == "mcp"
    assert entry.mcp_config["command"] == "npx"

    reg = load_registry(registry_path)
    assert reg.items[0].kind == "mcp"


def test_inferred_name(registry_path: Path) -> None:
    entry = add_external_skill(
        "https://github.com/foo/bar.git",
        registry_path=registry_path,
        skip_verify=True,
    )
    assert entry.name == "bar"


def test_add_with_v1_registry(registry_v1_path: Path) -> None:
    """Adding to a v1 registry should auto-migrate."""
    entry = add_external_skill(
        "https://github.com/foo/new-skill",
        name="new",
        registry_path=registry_v1_path,
        skip_verify=True,
    )
    assert entry.name == "new"
    reg = load_registry(registry_v1_path)
    assert reg.version == 3
