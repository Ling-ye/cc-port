from __future__ import annotations

from pathlib import Path

import pytest

from skillhub.models import Registry, SkillEntry
from skillhub.registry import load_registry, save_registry


def test_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "registry.yaml"
    reg = Registry(
        skills=[
            SkillEntry(
                name="alpha",
                repo="https://github.com/me/alpha",
                source="owned",
                ref="main",
                description="x",
            ),
            SkillEntry(
                name="beta",
                repo="https://github.com/anth/skills",
                source="external",
                subdir="beta",
                ref="v1",
            ),
        ]
    )
    save_registry(reg, p)
    reloaded = load_registry(p)
    assert [s.name for s in reloaded.skills] == ["alpha", "beta"]
    assert reloaded.skills[1].subdir == "beta"
    assert reloaded.skills[1].source == "external"


def test_upsert_and_remove() -> None:
    reg = Registry()
    reg.upsert(SkillEntry(name="b", repo="https://github.com/x/b"))
    reg.upsert(SkillEntry(name="a", repo="https://github.com/x/a"))
    assert [s.name for s in reg.skills] == ["a", "b"]

    reg.upsert(SkillEntry(name="a", repo="https://github.com/x/a", description="updated"))
    assert reg.get("a").description == "updated"
    assert len(reg.skills) == 2

    removed = reg.remove("a")
    assert removed is not None
    assert reg.get("a") is None


def test_invalid_name() -> None:
    with pytest.raises(ValueError):
        SkillEntry(name="Bad Name!", repo="https://github.com/x/y")


def test_invalid_repo() -> None:
    with pytest.raises(ValueError):
        SkillEntry(name="ok", repo="https://gitlab.com/x/y")


def test_subdir_traversal() -> None:
    with pytest.raises(ValueError):
        SkillEntry(name="ok", repo="https://github.com/x/y", subdir="../../etc")
