from pathlib import Path

from lpm.core.models import Registry, RegistryItem
from lpm.core.registry import save_registry
from lpm.interfaces.desktop_api import run_action
from lpm.services.resource_discovery import discover_resources, read_discovered_resource


def test_discover_global_common_resources(monkeypatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    _write_skill(tmp_path / ".codex" / "skills" / "code-helper", "code-helper")
    _write(tmp_path / ".claude" / "commands" / "review.md", "Review this code.")
    _write(tmp_path / ".claude" / "CLAUDE.md", "Use concise answers.")
    _write(tmp_path / ".cursor" / "rules" / "style.mdc", "---\nalwaysApply: true\n---\nUse ruff.")
    _write_skill(tmp_path / ".codex" / "skills" / ".system" / "builtin", "builtin")

    items = discover_resources(scope="global")

    by_name = {item.name_hint: item for item in items}
    assert by_name["code-helper"].kind == "skill"
    assert by_name["review"].kind == "prompt"
    assert by_name["claude"].kind == "rule"
    assert by_name["style"].kind == "rule"
    assert "builtin" not in by_name


def test_discover_directory_root_and_nested_resources(monkeypatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    root = tmp_path / "bundle"
    _write_skill(root / "skill-one", "skill-one")
    _write(root / "nested" / "prompts" / "refactor.md", "Refactor this module.")
    _write(root / ".cursor" / "rules" / "ui.mdc", "Use accessible controls.")
    _write(root / "node_modules" / "prompts" / "ignored.md", "Do not discover.")
    _write(root / "a" / "b" / "c" / "d" / "prompts" / "too-deep.md", "Do not discover.")

    items = discover_resources(scope="directory", root_path=root)

    by_name = {item.name_hint: item for item in items}
    assert by_name["skill-one"].kind == "skill"
    assert by_name["refactor"].kind == "prompt"
    assert by_name["ui"].kind == "rule"
    assert "ignored" not in by_name
    assert "too-deep" not in by_name


def test_discovery_marks_registry_and_candidate_name_conflicts(monkeypatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    root = tmp_path / "bundle"
    _write(root / "commands" / "review.md", "Review this code.")
    _write(root / "prompts" / "review.md", "Review this design.")
    registry_path = tmp_path / "resources" / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="review",
                    kind="prompt",
                    source="local",
                    path="prompts/review",
                )
            ]
        ),
        registry_path,
    )

    items = discover_resources(scope="directory", root_path=root, registry_path=registry_path)

    review_items = [item for item in items if item.name_hint == "review"]
    assert len(review_items) == 2
    assert all(item.status == "conflict" for item in review_items)
    assert any("registry" in " ".join(item.warnings).lower() for item in review_items)
    assert any("same inferred name" in " ".join(item.warnings).lower() for item in review_items)


def test_read_discovered_resource_truncates_and_reports_binary(monkeypatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    root = tmp_path / "bundle"
    _write(root / "prompts" / "long.md", "a" * 300)
    binary = root / "prompts" / "binary.md"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x00\x01not text")
    items = discover_resources(scope="directory", root_path=root)

    long_item = next(item for item in items if item.name_hint == "long")
    long_preview = read_discovered_resource(
        long_item.id,
        scope="directory",
        root_path=root,
        max_chars=100,
    )
    assert long_preview.truncated is True
    assert len(long_preview.text) == 100

    binary_item = next(item for item in items if item.name_hint == "binary")
    binary_preview = read_discovered_resource(binary_item.id, scope="directory", root_path=root)
    assert binary_preview.warning
    assert binary_preview.text == ""


def test_desktop_api_upload_discovered_resources_wraps_rule_file(monkeypatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    resource_home = tmp_path / "resources"
    monkeypatch.setenv("LPM_RESOURCE_HOME", str(resource_home))
    root = tmp_path / "bundle"
    _write(root / "rules" / "coding.mdc", "Use clear names.")

    discovered = run_action("discover_resources", {"scope": "directory", "root_path": str(root)})
    assert discovered["ok"] is True
    coding = next(item for item in discovered["data"]["items"] if item["name_hint"] == "coding")

    uploaded = run_action(
        "upload_discovered_resources",
        {
            "scope": "directory",
            "root_path": str(root),
            "items": [{"id": coding["id"]}],
        },
    )

    assert uploaded["ok"] is True
    assert uploaded["data"]["imported"] == 1
    assert (resource_home / "rules" / "coding" / "coding.mdc").is_file()
    assert uploaded["data"]["results"][0]["entry"]["path"] == "rules/coding"


def _isolate_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("LPM_CONFIG", raising=False)
    monkeypatch.delenv("LPM_RESOURCE_HOME", raising=False)
    monkeypatch.delenv("LPM_GITHUB_TOKEN", raising=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_skill(path: Path, name: str) -> None:
    _write(
        path / "SKILL.md",
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {name} description",
                "---",
                "",
                "Use this skill.",
            ]
        ),
    )
