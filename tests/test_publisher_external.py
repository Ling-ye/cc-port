from __future__ import annotations

from pathlib import Path

from skillhub.publisher import add_external_skill, remove_skill
from skillhub.registry import load_registry


def test_add_and_remove_external(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text("version: 1\nskills: []\n", encoding="utf-8")

    entry = add_external_skill(
        "https://github.com/anthropics/skills",
        name="pdf",
        subdir="pdf",
        ref="main",
        description="PDF tools",
        registry_path=reg_path,
    )
    assert entry.name == "pdf"
    assert entry.subdir == "pdf"

    reg = load_registry(reg_path)
    assert len(reg.skills) == 1
    assert reg.skills[0].source == "external"

    removed = remove_skill("pdf", registry_path=reg_path)
    assert removed is not None
    assert load_registry(reg_path).skills == []


def test_inferred_name(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text("version: 1\nskills: []\n", encoding="utf-8")
    entry = add_external_skill(
        "https://github.com/foo/bar.git",
        registry_path=reg_path,
    )
    assert entry.name == "bar"
