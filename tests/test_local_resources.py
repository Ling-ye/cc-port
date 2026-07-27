from __future__ import annotations

import json
from pathlib import Path

from cc_port.core.models import Registry, RegistryItem
from cc_port.core.registry import save_registry
from cc_port.services.local_resources import export_claude_plugin


def test_export_claude_plugin_uses_valid_slug_and_active_local_skills(tmp_path: Path) -> None:
    root = tmp_path / "LingyeAIResources"
    active = root / "skills" / "active-skill"
    removed = root / "skills" / "removed-skill"
    active.mkdir(parents=True)
    removed.mkdir(parents=True)
    (active / "SKILL.md").write_text(
        "---\nname: active-skill\ndescription: Active\n---\n",
        encoding="utf-8",
    )
    (removed / "SKILL.md").write_text(
        "---\nname: removed-skill\ndescription: Removed\n---\n",
        encoding="utf-8",
    )
    registry_path = root / "registry.yaml"
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="active-skill",
                    kind="skill",
                    source="local",
                    path="skills/active-skill",
                ),
                RegistryItem(
                    name="removed-skill",
                    kind="skill",
                    source="local",
                    path="skills/removed-skill",
                    lifecycle="removed",
                ),
                RegistryItem(
                    name="cursor-only",
                    kind="skill",
                    source="local",
                    path="skills/cursor-only",
                    platforms=["cursor"],
                ),
            ]
        ),
        registry_path,
    )

    output = export_claude_plugin(registry_path=registry_path)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload == {
        "name": "lingye-ai-resources",
        "skills": ["./skills/active-skill"],
    }
