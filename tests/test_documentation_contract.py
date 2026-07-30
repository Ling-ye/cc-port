from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(
            part in {".git", "build", "node_modules", "target"}
            or part.startswith(".venv")
            for part in path.parts
        )
    )


def _link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return target.split(maxsplit=1)[0]


def test_relative_markdown_links_resolve_to_repository_files() -> None:
    broken: list[str] = []
    for document in _markdown_files():
        for match in MARKDOWN_LINK_RE.finditer(document.read_text(encoding="utf-8")):
            target = _link_target(match.group(1))
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative_path = unquote(target.split("#", 1)[0])
            if not relative_path:
                continue
            resolved = (document.parent / relative_path).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                broken.append(f"{document.relative_to(ROOT)} -> {target} (outside repository)")
                continue
            if not resolved.exists():
                broken.append(f"{document.relative_to(ROOT)} -> {target}")

    assert broken == []


def test_release_notes_only_use_portable_absolute_links() -> None:
    offenders: list[str] = []
    for document in sorted((ROOT / "docs" / "releases").glob("*.md")):
        for match in MARKDOWN_LINK_RE.finditer(document.read_text(encoding="utf-8")):
            target = _link_target(match.group(1))
            if target and not target.startswith(("#", "https://")):
                offenders.append(f"{document.name} -> {target}")

    assert offenders == []


def test_english_readme_links_to_the_english_user_path() -> None:
    source = (ROOT / "README.en.md").read_text(encoding="utf-8")

    assert "docs/getting-started.en.md" in source
    assert "docs/troubleshooting.en.md" in source
    assert "docs/releases/v0.5.4.en.md" in source
