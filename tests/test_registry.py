from __future__ import annotations

from pathlib import Path

import pytest

from lpm.models import Registry, RegistryItem
from lpm.registry import load_registry, save_registry


def test_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "registry.yaml"
    reg = Registry(
        items=[
            RegistryItem(
                name="alpha",
                kind="skill",
                repo="https://github.com/me/alpha",
                source="owned",
                ref="main",
                description="x",
            ),
            RegistryItem(
                name="beta",
                kind="mcp",
                repo="https://github.com/anth/skills",
                source="external",
                subdir="beta",
                ref="v1",
                mcp_config={"command": "npx", "args": ["-y", "@test/mcp"]},
            ),
        ]
    )
    save_registry(reg, p)
    reloaded = load_registry(p)
    assert [s.name for s in reloaded.items] == ["alpha", "beta"]
    assert reloaded.items[0].kind == "skill"
    assert reloaded.items[1].kind == "mcp"
    assert reloaded.items[1].subdir == "beta"
    assert reloaded.items[1].source == "external"
    assert reloaded.items[1].mcp_config["command"] == "npx"


def test_round_trip_backward_compat(tmp_path: Path) -> None:
    """Constructing via skills= kwarg still works."""
    p = tmp_path / "registry.yaml"
    reg = Registry(
        skills=[
            RegistryItem(
                name="alpha",
                repo="https://github.com/me/alpha",
                source="owned",
                ref="main",
                description="x",
            ),
        ]
    )
    save_registry(reg, p)
    reloaded = load_registry(p)
    assert [s.name for s in reloaded.skills] == ["alpha"]
    assert reloaded.items[0].kind == "skill"


def test_v1_migration(tmp_path: Path) -> None:
    """v1 registries with 'skills' key are auto-migrated to v2."""
    p = tmp_path / "registry.yaml"
    p.write_text(
        "version: 1\n"
        "skills:\n"
        "  - name: old-skill\n"
        "    repo: https://github.com/x/old\n"
        "    source: owned\n"
        "    ref: main\n",
        encoding="utf-8",
    )
    reg = load_registry(p)
    assert reg.version == 3
    assert len(reg.items) == 1
    assert reg.items[0].kind == "skill"
    assert reg.items[0].name == "old-skill"


def test_upsert_and_remove() -> None:
    reg = Registry()
    reg.upsert(RegistryItem(name="b", repo="https://github.com/x/b"))
    reg.upsert(RegistryItem(name="a", repo="https://github.com/x/a"))
    assert [s.name for s in reg.items] == ["a", "b"]

    reg.upsert(RegistryItem(name="a", repo="https://github.com/x/a", description="updated"))
    assert reg.get("a").description == "updated"
    assert len(reg.items) == 2

    removed = reg.remove("a")
    assert removed is not None
    assert reg.get("a") is None


def test_filter_by_kind() -> None:
    reg = Registry(items=[
        RegistryItem(name="s1", kind="skill", repo="https://github.com/x/s1"),
        RegistryItem(name="m1", kind="mcp", repo="https://github.com/x/m1",
                     mcp_config={"command": "npx"}),
        RegistryItem(name="s2", kind="skill", repo="https://github.com/x/s2"),
    ])
    assert len(reg.filter_by_kind("skill")) == 2
    assert len(reg.filter_by_kind("mcp")) == 1
    assert len(reg.filter_by_kind("rule")) == 0


def test_invalid_name() -> None:
    with pytest.raises(ValueError):
        RegistryItem(name="Bad Name!", repo="https://github.com/x/y")


def test_invalid_repo() -> None:
    with pytest.raises(ValueError):
        RegistryItem(name="ok", repo="https://gitlab.com/x/y")


def test_subdir_traversal() -> None:
    with pytest.raises(ValueError):
        RegistryItem(name="ok", repo="https://github.com/x/y", subdir="../../etc")


def test_mcp_config_validation() -> None:
    with pytest.raises(ValueError, match="command.*url"):
        RegistryItem(
            name="bad-mcp",
            kind="mcp",
            repo="https://github.com/x/y",
            mcp_config={"env": {"FOO": "bar"}},
        )

    item = RegistryItem(
        name="good-mcp",
        kind="mcp",
        repo="https://github.com/x/y",
        mcp_config={"command": "npx", "args": ["-y", "@test/mcp"]},
    )
    assert item.mcp_config["command"] == "npx"

    item2 = RegistryItem(
        name="http-mcp",
        kind="mcp",
        repo="https://github.com/x/y",
        mcp_config={"url": "https://mcp.example.com"},
    )
    assert item2.mcp_config["url"] == "https://mcp.example.com"


def test_save_omits_none_mcp_config(tmp_path: Path) -> None:
    """Skill items should not have mcp_config in the saved YAML."""
    p = tmp_path / "registry.yaml"
    reg = Registry(items=[
        RegistryItem(name="s", repo="https://github.com/x/s"),
    ])
    save_registry(reg, p)
    text = p.read_text()
    assert "mcp_config" not in text
