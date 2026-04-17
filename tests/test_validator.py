from __future__ import annotations

from pathlib import Path

import pytest

from skillhub.validator import SkillValidationError, parse_skill


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_parse_valid(tmp_path: Path) -> None:
    d = tmp_path / "good"
    _write(
        d / "SKILL.md",
        "---\nname: good\ndescription: a description.\n---\n# body\n",
    )
    meta = parse_skill(d)
    assert meta.name == "good"
    assert meta.description == "a description."
    assert meta.skill_md_path == d / "SKILL.md"


def test_missing_skill_md(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(SkillValidationError):
        parse_skill(d)


def test_missing_name(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    _write(d / "SKILL.md", "---\ndescription: missing name\n---\n")
    with pytest.raises(SkillValidationError):
        parse_skill(d)


def test_invalid_name(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    _write(d / "SKILL.md", "---\nname: BAD NAME\ndescription: x\n---\n")
    with pytest.raises(SkillValidationError):
        parse_skill(d)


def test_nested_skill_md(tmp_path: Path) -> None:
    d = tmp_path / "outer"
    _write(d / "inner" / "SKILL.md", "---\nname: nested\ndescription: y\n---\n")
    meta = parse_skill(d)
    assert meta.name == "nested"
