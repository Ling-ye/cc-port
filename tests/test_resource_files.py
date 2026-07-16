from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lpm.core.resource_files import (
    is_resource_path_excluded,
    resource_copy_ignore,
)


def test_absolute_temp_parent_does_not_exclude_resource_file(tmp_path: Path) -> None:
    resource = tmp_path / "resource" / "SKILL.md"
    resource.parent.mkdir()
    resource.write_text("# Skill\n", encoding="utf-8")

    assert is_resource_path_excluded(resource) is False
    assert is_resource_path_excluded(Path("temp") / "SKILL.md") is True


def test_copy_policy_uses_entries_relative_to_resource_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (source / ".env.example").write_text("TOKEN=\n", encoding="utf-8")
    generated = source / "temp"
    generated.mkdir()
    (generated / "output.txt").write_text("generated\n", encoding="utf-8")

    target = tmp_path / "target"
    shutil.copytree(source, target, ignore=resource_copy_ignore)

    assert (target / "SKILL.md").is_file()
    assert (target / ".env.example").is_file()
    assert not (target / ".env").exists()
    assert not (target / "temp").exists()


def test_copy_policy_excludes_symbolic_links(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    link = source / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    target = tmp_path / "target"
    shutil.copytree(source, target, ignore=resource_copy_ignore)

    assert not (target / "linked.txt").exists()
