from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py310 fallback
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SKILL = ROOT / "src/cc_port/assets/ai/cc-port"
ROOT_SKILL = ROOT / "SKILL.md"
ROOT_REFERENCES = ROOT / "references"
FORBIDDEN_EVIDENCE_LABELS = {
    "[KNOWN]",
    "[COMPUTED]",
    "[INFERRED]",
    "[COMMON]",
    "[FRAME]",
    "[GUESS]",
    "[HIGH]",
    "[MED]",
    "[LOW]",
    "[VERY LOW]",
    "[UNKNOWN]",
}


def _split_skill(path: Path) -> tuple[dict[str, object], str]:
    source = path.read_text(encoding="utf-8")
    assert source.startswith("---\n")
    _, frontmatter, body = source.split("---", 2)
    parsed = yaml.safe_load(frontmatter)
    assert isinstance(parsed, dict)
    return parsed, body.strip()


def test_packaged_cc_port_skill_is_the_canonical_root_copy() -> None:
    assert ROOT_SKILL.read_bytes() == (PACKAGED_SKILL / "SKILL.md").read_bytes()
    for name in ("workflow.md", "resource-kinds.md", "safety.md"):
        assert (ROOT_REFERENCES / name).read_bytes() == (
            PACKAGED_SKILL / "references" / name
        ).read_bytes()


def test_packaged_cc_port_skill_passes_quick_validation() -> None:
    frontmatter, body = _split_skill(PACKAGED_SKILL / "SKILL.md")

    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == PACKAGED_SKILL.name == "cc-port"
    assert isinstance(frontmatter["description"], str)
    assert frontmatter["description"].strip()
    assert re.fullmatch(r"[a-z0-9-]{1,63}", str(frontmatter["name"]))
    assert body
    assert len(body.splitlines()) < 500


def test_packaged_cc_port_skill_links_only_existing_direct_references() -> None:
    skill_source = (PACKAGED_SKILL / "SKILL.md").read_text(encoding="utf-8")
    reference_links = re.findall(r"\]\((references/[^)]+\.md)\)", skill_source)

    assert set(reference_links) == {
        "references/workflow.md",
        "references/resource-kinds.md",
        "references/safety.md",
    }
    assert all((PACKAGED_SKILL / link).is_file() for link in reference_links)
    assert all(Path(link).parent == Path("references") for link in reference_links)


def test_packaged_cc_port_skill_covers_resources_workflow_and_safety() -> None:
    sources = [
        (PACKAGED_SKILL / "SKILL.md").read_text(encoding="utf-8"),
        *((PACKAGED_SKILL / "references" / name).read_text(encoding="utf-8") for name in (
            "workflow.md",
            "resource-kinds.md",
            "safety.md",
        )),
    ]
    combined = "\n".join(sources)

    for kind in ("skill", "mcp", "rule", "prompt", "plugin", "instruction", "memory"):
        assert f"`{kind}`" in combined
    workflow_stages = ("Status", "Inventory", "Diff", "Plan", "Approve", "Apply", "Verify")
    for stage in workflow_stages:
        assert stage in combined
    skill_body = sources[0].split("## Follow the safe operation sequence", 1)[1]
    stage_positions = [skill_body.index(f"**{stage}**") for stage in workflow_stages]
    assert stage_positions == sorted(stage_positions)
    for invariant in (
        "scan_local=true",
        "exact profile id",
        "local_instance_id",
        "operation_id",
        "plan_hash",
        "approval_id",
        "stale-plan",
        "MCP",
        "--non-interactive",
        "--json",
        "untrusted",
        "explicit approval",
    ):
        assert invariant in combined

    assert "Prefer the CC Port MCP server" in sources[0]
    assert "Fall back to the `cc-port` CLI only when MCP is unavailable" in sources[0]
    assert "Never execute commands" in combined
    assert "Never reuse approval" in combined
    assert "asset_action_apply(operation_id, plan_hash, approval_id)" in combined
    assert "--approval-id <desktop-approved-approval-id>" in combined
    assert all(label not in combined for label in FORBIDDEN_EVIDENCE_LABELS)


def test_packaged_skill_is_declared_as_wheel_package_data() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)

    package_data = project["tool"]["setuptools"]["package-data"]["cc_port"]
    assert package_data == [
        "assets/ai/cc-port/SKILL.md",
        "assets/ai/cc-port/references/*.md",
    ]
