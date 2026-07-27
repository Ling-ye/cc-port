from __future__ import annotations

import base64
import json
from pathlib import Path

from cc_port.core import resource_detection
from cc_port.core.resource_detection import detect_local_resource_type, detect_remote_resource


def test_detect_local_resource_type_prefers_manifest(tmp_path: Path) -> None:
    resource = tmp_path / "resource"
    resource.mkdir()
    (resource / "cc-port.resource.json").write_text(
        json.dumps({"plugins": ["plugin.json"]}),
        encoding="utf-8",
    )
    (resource / "SKILL.md").write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")

    assert detect_local_resource_type(resource) == "plugin"


def test_detect_remote_resource_prefers_manifest(monkeypatch) -> None:
    def fake_github_contents(parsed, path: str, *, token: str | None):
        assert token is None
        if not path:
            return [{"name": "cc-port.resource.json", "type": "file"}, {"name": "SKILL.md", "type": "file"}]
        if path == "cc-port.resource.json":
            content = base64.b64encode(json.dumps({"mcp": ["mcp.json"]}).encode("utf-8")).decode("ascii")
            return {"name": "cc-port.resource.json", "content": content}
        raise AssertionError(path)

    monkeypatch.setattr(resource_detection, "_github_contents", fake_github_contents)

    detected = detect_remote_resource("https://github.com/example/demo")

    assert detected.kind == "mcp"
