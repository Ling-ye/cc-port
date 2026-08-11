from __future__ import annotations

from pathlib import Path

from cc_port.services.resource_discovery import discover_exact_resource


def test_memory_startup_limit_ignores_frontmatter_and_block_html_comments(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    frontmatter = "---\nnotes: " + ("x" * (26 * 1024)) + "\n---\n"
    comment = "<!--\n" + "\n".join(f"hidden {index}" for index in range(250)) + "\n-->\n"
    payload = frontmatter + comment + "# Visible memory\n"
    (memory / "MEMORY.md").write_text(payload, encoding="utf-8")

    resource = discover_exact_resource(
        memory,
        tool="claude-code",
        kind="memory",
        name_hint="test-memory",
    )

    assert resource is not None
    assert resource.status == "ready"
    assert not resource.warnings
    assert (memory / "MEMORY.md").read_text(encoding="utf-8") == payload


def test_memory_startup_limit_counts_visible_content(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    payload = "".join(f"visible {index}\n" for index in range(201))
    (memory / "MEMORY.md").write_text(payload, encoding="utf-8")

    resource = discover_exact_resource(
        memory,
        tool="claude-code",
        kind="memory",
        name_hint="test-memory",
    )

    assert resource is not None
    assert resource.status == "warning"
    assert "first 200 lines or 25 KiB" in " ".join(resource.warnings)
