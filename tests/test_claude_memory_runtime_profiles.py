"""Claude Code memory and runtime-profile discovery contracts.

The behavior in this module follows the Claude Code documentation rather than
the shape of Claude's entire local state directory:

* https://code.claude.com/docs/en/memory
* https://code.claude.com/docs/en/settings
* https://code.claude.com/docs/en/installation
* https://developers.openai.com/codex/guides/agents-md/

In particular, ``autoMemoryDirectory`` names the memory directory itself,
while the default layout stores one memory directory below each project key.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import cc_port.services.env_manager as env_manager
from cc_port.core.config import Config
from cc_port.core.models import Registry
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.core.registry import save_registry
from cc_port.core.validator import SkillValidationError, validate_item
from cc_port.services.registry_audit import audit_registry_root


def _claude_profile(
    *,
    name: str,
    root: Path,
    environment_kind: str,
    environment_name: str = "",
) -> PlatformProfile:
    return PlatformProfile(
        name=name,
        tool_id="claude-code",
        environment_kind=environment_kind,
        environment_name=environment_name,
        display_name=f"Claude Code ({name})",
        home_dir=str(root.parent),
        enabled=True,
        instructions_path=str(root / "CLAUDE.md"),
        memories_dir=str(root / "projects"),
        memory_layout="projects",
        settings_path=str(root / "settings.json"),
    )


def _home_scoped_claude_profile(
    *,
    name: str,
    home: Path,
    environment_kind: str,
    environment_name: str = "",
) -> PlatformProfile:
    """Use identical ``~`` templates while keeping each runtime home isolated."""
    return PlatformProfile(
        name=name,
        tool_id="claude-code",
        environment_kind=environment_kind,
        environment_name=environment_name,
        display_name=f"Claude Code ({name})",
        home_dir=str(home),
        enabled=True,
        instructions_path="~/.claude/CLAUDE.md",
        memories_dir="~/.claude/projects",
        memory_layout="projects",
        settings_path="~/.claude/settings.json",
    )


def _config(*profiles: PlatformProfile) -> Config:
    return Config(platforms=PlatformsConfig(profiles=list(profiles)))


def _write_memory(path: Path, *, heading: str) -> None:
    path.mkdir(parents=True)
    (path / "MEMORY.md").write_text(f"# {heading}\n", encoding="utf-8")
    (path / "details.md").write_text("Supporting memory.\n", encoding="utf-8")


def test_native_windows_and_wsl_claude_profiles_keep_separate_identities(
    tmp_path: Path,
) -> None:
    windows_root = tmp_path / "windows-user" / ".claude"
    wsl_root = tmp_path / "wsl-home" / ".claude"
    windows_root.mkdir(parents=True)
    wsl_root.mkdir(parents=True)
    windows_instruction = windows_root / "CLAUDE.md"
    wsl_instruction = wsl_root / "CLAUDE.md"
    windows_instruction.write_text("# Native Windows preferences\n", encoding="utf-8")
    wsl_instruction.write_text("# WSL preferences\n", encoding="utf-8")
    (windows_root / "settings.json").write_text("{}\n", encoding="utf-8")
    (wsl_root / "settings.json").write_text("{}\n", encoding="utf-8")

    result = env_manager.discover_environment(
        home=tmp_path / "unused-home",
        config=_config(
            _home_scoped_claude_profile(
                name="claude-windows",
                home=windows_root.parent,
                environment_kind="windows",
            ),
            _home_scoped_claude_profile(
                name="claude-wsl-ubuntu",
                home=wsl_root.parent,
                environment_kind="wsl",
                environment_name="Ubuntu-24.04",
            ),
        ),
    )

    assert {
        (
            tool.id,
            tool.tool_id,
            tool.environment_kind,
            tool.environment_name,
            tool.instruction_path,
        )
        for tool in result.tools
    } == {
        (
            "claude-windows",
            "claude-code",
            "windows",
            "",
            windows_instruction.resolve(),
        ),
        (
            "claude-wsl-ubuntu",
            "claude-code",
            "wsl",
            "Ubuntu-24.04",
            wsl_instruction.resolve(),
        ),
    }
    assert {
        (
            resource.tool,
            resource.tool_id,
            resource.environment_kind,
            resource.environment_name,
            resource.kind,
            resource.name_hint,
            resource.path,
        )
        for resource in result.resources
    } == {
        (
            "claude-windows",
            "claude-code",
            "windows",
            "",
            "instruction",
            "claude-code-user-instructions",
            windows_instruction.resolve(),
        ),
        (
            "claude-wsl-ubuntu",
            "claude-code",
            "wsl",
            "Ubuntu-24.04",
            "instruction",
            "claude-code-user-instructions",
            wsl_instruction.resolve(),
        ),
    }


def test_default_claude_projects_layout_discovers_only_exact_memory_directory(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    project_root = claude_root / "projects" / "project-one"
    memory = project_root / "memory"
    _write_memory(memory, heading="Default project memory")
    settings = claude_root / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    history = claude_root / "history.jsonl"
    history.write_text('{"display":"private prompt"}\n', encoding="utf-8")
    session = project_root / "session-id.jsonl"
    session.write_text('{"type":"assistant"}\n', encoding="utf-8")
    (project_root / "tool-results").mkdir()
    (project_root / "tool-results" / "result.txt").write_text(
        "not portable\n",
        encoding="utf-8",
    )
    profile = _claude_profile(
        name="claude-wsl",
        root=claude_root,
        environment_kind="wsl",
        environment_name="Ubuntu",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "home",
        config=_config(profile),
    )

    opaque_name = "claude-memory-" + hashlib.sha256(
        b"claude-wsl\0project-one"
    ).hexdigest()[:12]
    assert [
        (
            resource.kind,
            resource.name_hint,
            resource.install_name_hint,
            resource.path,
        )
        for resource in result.resources
    ] == [("memory", opaque_name, "project-one", memory.resolve())]
    assert result.tools[0].memory_layout == "projects"
    assert result.tools[0].memories_path == (claude_root / "projects").resolve()
    assert settings.resolve() in result.tools[0].config_paths
    resource_paths = {resource.path for resource in result.resources}
    assert settings.resolve() not in resource_paths
    assert history.resolve() not in resource_paths
    assert session.resolve() not in resource_paths
    assert (project_root / "tool-results").resolve() not in resource_paths


def test_unmapped_same_project_slot_is_not_implicitly_merged_across_profiles(
    tmp_path: Path,
) -> None:
    windows_root = tmp_path / "windows" / ".claude"
    wsl_root = tmp_path / "wsl" / ".claude"
    for root, heading in ((windows_root, "Windows"), (wsl_root, "WSL")):
        root.mkdir(parents=True)
        (root / "settings.json").write_text("{}\n", encoding="utf-8")
        _write_memory(root / "projects" / "same-slot" / "memory", heading=heading)

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(
            _claude_profile(
                name="claude-windows",
                root=windows_root,
                environment_kind="windows",
            ),
            _claude_profile(
                name="claude-wsl-ubuntu",
                root=wsl_root,
                environment_kind="wsl",
                environment_name="Ubuntu",
            ),
        ),
    )

    memories = [item for item in result.resources if item.kind == "memory"]
    assert len(memories) == 2
    assert len({item.name_hint for item in memories}) == 2
    assert {item.install_name_hint for item in memories} == {"same-slot"}


def test_claude_user_rules_are_recursive_unique_and_block_lossy_flattening(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    rules = claude_root / "rules"
    frontend_rule = rules / "frontend" / "security.md"
    backend_rule = rules / "backend" / "security.md"
    frontend_rule.parent.mkdir(parents=True)
    backend_rule.parent.mkdir(parents=True)
    frontend_rule.write_text("# Frontend security\n", encoding="utf-8")
    backend_rule.write_text("# Backend security\n", encoding="utf-8")
    collision_nested = rules / "a" / "b.md"
    collision_flat = rules / "a-b.md"
    collision_nested.parent.mkdir(parents=True)
    collision_nested.write_text("# Nested\n", encoding="utf-8")
    collision_flat.write_text("# Flat\n", encoding="utf-8")
    (rules / "ignored.txt").write_text("not a rule\n", encoding="utf-8")
    profile = _claude_profile(
        name="claude-windows",
        root=claude_root,
        environment_kind="windows",
    )
    profile.rules_dir = str(rules)

    result = env_manager.discover_environment(
        home=tmp_path / "process-home",
        config=_config(profile),
    )

    discovered = {
        resource.path: resource
        for resource in result.resources
        if resource.kind == "rule"
    }
    assert set(discovered) == {
        backend_rule.resolve(),
        frontend_rule.resolve(),
        collision_nested.resolve(),
        collision_flat.resolve(),
    }
    assert len({resource.name_hint for resource in discovered.values()}) == 4
    assert discovered[collision_flat.resolve()].name_hint == "a-b"
    for path in (backend_rule, frontend_rule, collision_nested):
        resource = discovered[path.resolve()]
        relative_stem = path.relative_to(rules).with_suffix("").as_posix()
        expected = "claude-rule-" + hashlib.sha256(
            relative_stem.encode("utf-8")
        ).hexdigest()[:12]
        assert resource.name_hint == expected
        assert resource.status == "blocked"
        assert "cannot yet be restored losslessly" in " ".join(resource.blockers)


def test_auto_memory_directory_is_used_as_the_direct_memory_directory(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    claude_root.mkdir(parents=True)
    default_memory = claude_root / "projects" / "default-project" / "memory"
    configured_memory = tmp_path / "portable-claude-memory"
    _write_memory(default_memory, heading="Default memory must be ignored")
    _write_memory(configured_memory, heading="Configured direct memory")
    settings = claude_root / "settings.json"
    settings.write_text(
        json.dumps({"autoMemoryDirectory": str(configured_memory)}),
        encoding="utf-8",
    )
    profile_name = "claude-windows" if os.name == "nt" else "claude-wsl-test"
    profile = _claude_profile(
        name=profile_name,
        root=claude_root,
        environment_kind="windows" if os.name == "nt" else "wsl",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "home",
        config=_config(profile),
    )

    assert result.tools[0].memory_layout == "direct"
    assert result.tools[0].memories_path == configured_memory.resolve()
    expected_name = "claude-memory-" + hashlib.sha256(
        f"{profile_name}\0{os.path.normcase(str(configured_memory.resolve()))}".encode()
    ).hexdigest()[:12]
    assert [(resource.kind, resource.name_hint, resource.path) for resource in result.resources] == [
        ("memory", expected_name, configured_memory.resolve())
    ]
    assert all(resource.path != default_memory.resolve() for resource in result.resources)
    assert all(
        resource.path != (configured_memory / "default-project" / "memory").resolve()
        for resource in result.resources
    )


def test_tilde_auto_memory_directory_expands_inside_each_profile_home(
    tmp_path: Path,
) -> None:
    windows_home = tmp_path / "windows-user"
    wsl_home = tmp_path / "wsl-home"
    windows_claude = windows_home / ".claude"
    wsl_claude = wsl_home / ".claude"
    windows_claude.mkdir(parents=True)
    wsl_claude.mkdir(parents=True)
    windows_memory = windows_home / "direct-memory"
    wsl_memory = wsl_home / "direct-memory"
    _write_memory(windows_memory, heading="Windows direct memory")
    _write_memory(wsl_memory, heading="WSL direct memory")
    settings_payload = json.dumps({"autoMemoryDirectory": "~/direct-memory"})
    (windows_claude / "settings.json").write_text(settings_payload, encoding="utf-8")
    (wsl_claude / "settings.json").write_text(settings_payload, encoding="utf-8")

    result = env_manager.discover_environment(
        home=tmp_path / "process-home-must-not-be-used",
        config=_config(
            _home_scoped_claude_profile(
                name="claude-windows",
                home=windows_home,
                environment_kind="windows",
            ),
            _home_scoped_claude_profile(
                name="claude-wsl-ubuntu",
                home=wsl_home,
                environment_kind="wsl",
                environment_name="Ubuntu-24.04",
            ),
        ),
    )

    assert {
        (tool.id, tool.memory_layout, tool.memories_path)
        for tool in result.tools
    } == {
        ("claude-windows", "direct", windows_memory.resolve()),
        ("claude-wsl-ubuntu", "direct", wsl_memory.resolve()),
    }
    by_tool = {resource.tool: resource for resource in result.resources}
    assert set(by_tool) == {"claude-windows", "claude-wsl-ubuntu"}
    assert by_tool["claude-windows"].path == windows_memory.resolve()
    assert by_tool["claude-wsl-ubuntu"].path == wsl_memory.resolve()
    assert len({resource.name_hint for resource in result.resources}) == 2
    for resource in result.resources:
        expected_name = "claude-memory-" + hashlib.sha256(
            f"{resource.tool}\0{os.path.normcase(str(resource.path))}".encode()
        ).hexdigest()[:12]
        assert resource.name_hint == expected_name
    assert all(
        (tmp_path / "process-home-must-not-be-used") not in resource.path.parents
        for resource in result.resources
    )


def test_codex_global_agents_override_takes_priority(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-user"
    instruction_root = codex_home / ".codex"
    instruction_root.mkdir(parents=True)
    base = instruction_root / "AGENTS.md"
    override = instruction_root / "AGENTS.override.md"
    base.write_text("# Shared guidance\n", encoding="utf-8")
    override.write_text("# Temporary global override\n", encoding="utf-8")
    profile = PlatformProfile(
        name="codex-wsl-ubuntu",
        tool_id="codex",
        environment_kind="wsl",
        environment_name="Ubuntu-24.04",
        home_dir=str(codex_home),
        enabled=True,
        instructions_path="~/.codex/AGENTS.md",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "process-home-must-not-be-used",
        config=_config(profile),
    )

    assert result.tools[0].instruction_path == override.resolve()
    assert [
        (resource.tool, resource.kind, resource.name_hint, resource.path)
        for resource in result.resources
    ] == [
        (
            "codex-wsl-ubuntu",
            "instruction",
            "codex-user-instructions",
            override.resolve(),
        )
    ]
    assert all(resource.path != base.resolve() for resource in result.resources)


def test_empty_codex_global_override_falls_back_to_agents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    base = codex / "AGENTS.md"
    override = codex / "AGENTS.override.md"
    base.write_text("# Base guidance\n", encoding="utf-8")
    override.write_bytes(b"")
    profile = PlatformProfile(
        name="codex-windows",
        tool_id="codex",
        environment_kind="windows",
        home_dir=str(home),
        instructions_path="~/.codex/AGENTS.md",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    assert result.tools[0].instruction_path == base.resolve()
    assert [resource.path for resource in result.resources] == [base.resolve()]


@pytest.mark.parametrize("with_override", [False, True])
def test_empty_codex_instruction_files_are_not_discovered(
    tmp_path: Path,
    with_override: bool,
) -> None:
    home = tmp_path / "home"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    base = codex / "AGENTS.md"
    base.write_text(" \n\t", encoding="utf-8")
    if with_override:
        (codex / "AGENTS.override.md").write_bytes(b"")
    profile = PlatformProfile(
        name="codex-windows",
        tool_id="codex",
        environment_kind="windows",
        home_dir=str(home),
        instructions_path="~/.codex/AGENTS.md",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    assert result.tools[0].instruction_path == base.resolve()
    assert not [resource for resource in result.resources if resource.kind == "instruction"]


def test_projects_memory_mapping_rejoins_distinct_runtime_slots_by_logical_name(
    tmp_path: Path,
) -> None:
    profiles: list[PlatformProfile] = []
    expected_paths: set[Path] = set()
    for profile_name, slot in (
        ("claude-windows", "C--work-cc-port"),
        ("claude-wsl", "-mnt-d-code-cc-port"),
    ):
        home = tmp_path / profile_name
        memory = home / ".claude" / "projects" / slot / "memory"
        _write_memory(memory, heading=profile_name)
        (home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
        profiles.append(
            PlatformProfile(
                name=profile_name,
                tool_id="claude-code",
                environment_kind="windows" if "windows" in profile_name else "wsl",
                home_dir=str(home),
                settings_path="~/.claude/settings.json",
                memories_dir="~/.claude/projects",
                memory_install_names={"cc-port-memory": slot},
            )
        )
        expected_paths.add(memory.resolve())

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(*profiles),
    )

    memories = [resource for resource in result.resources if resource.kind == "memory"]
    assert {resource.name_hint for resource in memories} == {"cc-port-memory"}
    assert {resource.path for resource in memories} == expected_paths
    assert {resource.install_name_hint for resource in memories} == {
        "C--work-cc-port",
        "-mnt-d-code-cc-port",
    }


def test_project_slot_link_is_blocked_without_following_external_memory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    projects = home / ".claude" / "projects"
    projects.mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    external_slot = tmp_path / "outside-slot"
    _write_memory(external_slot / "memory", heading="Must not be followed")
    slot = projects / "linked-slot"
    try:
        slot.symlink_to(external_slot, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symbolic links are unavailable: {exc}")
    profile = PlatformProfile(
        name="claude-wsl",
        tool_id="claude-code",
        environment_kind="wsl",
        home_dir=str(home),
        settings_path="~/.claude/settings.json",
        memories_dir="~/.claude/projects",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    memories = [resource for resource in result.resources if resource.kind == "memory"]
    assert len(memories) == 1
    assert memories[0].path == (slot / "memory").absolute()
    assert memories[0].content_path is None
    assert memories[0].status == "blocked"
    assert "was not followed" in " ".join(memories[0].blockers)


def test_claude_rules_include_hidden_build_and_unbounded_depth(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    rules = home / ".claude" / "rules"
    paths = [
        rules / ".hidden" / "hidden.md",
        rules / "build" / "generated.md",
            rules.joinpath(*("d" for _ in range(33)), "deep.md"),
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n", encoding="utf-8")
    (home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    profile = PlatformProfile(
        name="claude-wsl",
        tool_id="claude-code",
        environment_kind="wsl",
        home_dir=str(home),
        rules_dir="~/.claude/rules",
        settings_path="~/.claude/settings.json",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    discovered = {
        resource.path
        for resource in result.resources
        if resource.kind == "rule"
    }
    assert discovered == {path.resolve() for path in paths}
    assert all(
        resource.status == "blocked"
        for resource in result.resources
        if resource.kind == "rule"
    )


def test_registry_audit_discovers_instruction_and_memory_roots(tmp_path: Path) -> None:
    save_registry(Registry(), tmp_path / "registry.yaml")
    instruction = tmp_path / "instructions" / "claude-user"
    instruction.mkdir(parents=True)
    (instruction / "CLAUDE.md").write_text(
        "# Portable Claude user instructions\n",
        encoding="utf-8",
    )
    memory = tmp_path / "memories" / "project-one"
    _write_memory(memory, heading="Portable Claude project memory")

    plan = audit_registry_root(tmp_path, remote_commit="contract-test")

    additions = {
        (issue.kind, issue.name, issue.path)
        for issue in plan.issues
        if issue.code == "unregistered-resource"
    }
    assert additions == {
        ("instruction", "claude-user", "instructions/claude-user"),
        ("memory", "project-one", "memories/project-one"),
    }
    assert plan.blocked_count == 0
    assert plan.executable_count == 2
    assert "kind: instruction" in plan.resulting_registry_text
    assert "path: instructions/claude-user" in plan.resulting_registry_text
    assert "kind: memory" in plan.resulting_registry_text
    assert "path: memories/project-one" in plan.resulting_registry_text


def test_memory_startup_limit_warns_but_preserves_complete_candidate(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    memory = claude_root / "projects" / "opaque-slot" / "memory"
    memory.mkdir(parents=True)
    complete = "".join(f"line {index}\n" for index in range(1, 203))
    (memory / "MEMORY.md").write_text(complete, encoding="utf-8")
    profile = _claude_profile(
        name="claude-wsl",
        root=claude_root,
        environment_kind="wsl",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "home",
        config=_config(profile),
    )

    candidate = next(resource for resource in result.resources if resource.kind == "memory")
    assert candidate.status == "warning"
    assert "first 200 lines or 25 KiB" in " ".join(candidate.warnings)
    assert (candidate.path / "MEMORY.md").read_text(encoding="utf-8") == complete


def test_codex_dangling_override_is_selected_and_blocked_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    base = codex / "AGENTS.md"
    override = codex / "AGENTS.override.md"
    base.write_text("# Base must not win\n", encoding="utf-8")
    try:
        override.symlink_to(codex / "missing.md")
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    profile = PlatformProfile(
        name="codex-wsl",
        tool_id="codex",
        environment_kind="wsl",
        home_dir=str(home),
        instructions_path="~/.codex/AGENTS.md",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    assert result.tools[0].instruction_path == override.absolute()
    candidate = next(resource for resource in result.resources if resource.kind == "instruction")
    assert candidate.path == override.absolute()
    assert candidate.status == "blocked"
    assert candidate.path != base.resolve()


def test_instruction_and_memory_validation_rejects_extra_or_linked_payloads(
    tmp_path: Path,
) -> None:
    instruction = tmp_path / "instruction"
    instruction.mkdir()
    (instruction / "CLAUDE.md").write_text("# Safe\n", encoding="utf-8")
    (instruction / "extra.txt").write_text("must not migrate\n", encoding="utf-8")
    with pytest.raises(SkillValidationError, match="only one regular"):
        validate_item(instruction, "instruction")

    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("# Safe\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("private\n", encoding="utf-8")
    linked = memory / "linked.md"
    try:
        linked.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    with pytest.raises(SkillValidationError, match="symbolic links"):
        validate_item(memory, "memory")


def test_untrusted_claude_settings_blocks_memory_discovery_but_not_instruction(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    instruction = claude_root / "CLAUDE.md"
    instruction.parent.mkdir(parents=True)
    instruction.write_text("# Still discover me\n", encoding="utf-8")
    _write_memory(
        claude_root / "projects" / "private-project" / "memory",
        heading="Must not be guessed",
    )
    (claude_root / "settings.json").write_text("{broken", encoding="utf-8")
    profile = _claude_profile(
        name="claude-windows",
        root=claude_root,
        environment_kind="windows",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "home",
        config=_config(profile),
    )

    assert [(resource.kind, resource.path) for resource in result.resources] == [
        ("instruction", instruction.resolve())
    ]
    assert result.tools[0].memories_path is None
    assert "blocked" in result.tools[0].memory_blocker


def test_symlinked_claude_settings_never_falls_back_to_default_memory(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    claude_root.mkdir(parents=True)
    _write_memory(
        claude_root / "projects" / "private-project" / "memory",
        heading="Must not be guessed",
    )
    outside = tmp_path / "outside-settings.json"
    outside.write_text("{}\n", encoding="utf-8")
    try:
        (claude_root / "settings.json").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    profile = _claude_profile(
        name="claude-windows",
        root=claude_root,
        environment_kind="windows",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "home",
        config=_config(profile),
    )

    assert not [resource for resource in result.resources if resource.kind == "memory"]
    assert "symbolic link" in result.tools[0].memory_blocker


def test_linked_claude_config_ancestor_blocks_instruction_and_memory_reads(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside-claude"
    outside.mkdir()
    (outside / "settings.json").write_text("{}\n", encoding="utf-8")
    (outside / "CLAUDE.md").write_text("# Must not follow\n", encoding="utf-8")
    _write_memory(outside / "projects" / "private" / "memory", heading="Private")
    try:
        (home / ".claude").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symbolic links are unavailable: {exc}")
    profile = _home_scoped_claude_profile(
        name="claude-windows",
        home=home,
        environment_kind="windows",
    )
    profile.rules_dir = "~/.claude/rules"

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    instructions = [item for item in result.resources if item.kind == "instruction"]
    assert len(instructions) == 1
    assert instructions[0].status == "blocked"
    assert instructions[0].content_path is None
    assert not [item for item in result.resources if item.kind == "memory"]
    assert "linked or unreadable path component" in result.tools[0].memory_blocker


def test_linked_claude_rules_root_is_reported_without_following(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside-rules"
    outside.mkdir()
    (outside / "private.md").write_text("# Must not follow\n", encoding="utf-8")
    try:
        (claude / "rules").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symbolic links are unavailable: {exc}")
    profile = _home_scoped_claude_profile(
        name="claude-windows",
        home=home,
        environment_kind="windows",
    )
    profile.rules_dir = "~/.claude/rules"

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
    )

    rules = [item for item in result.resources if item.kind == "rule"]
    assert len(rules) == 1
    assert rules[0].status == "blocked"
    assert rules[0].path == (claude / "rules").absolute()
    assert outside / "private.md" not in {item.path for item in result.resources}


@pytest.mark.parametrize("payload", ["[]", '"not-an-object"'])
def test_non_object_claude_settings_blocks_default_memory_discovery(
    tmp_path: Path,
    payload: str,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    claude_root.mkdir(parents=True)
    _write_memory(
        claude_root / "projects" / "private-project" / "memory",
        heading="Must not be guessed",
    )
    (claude_root / "settings.json").write_text(payload, encoding="utf-8")
    profile = _claude_profile(
        name="claude-windows",
        root=claude_root,
        environment_kind="windows",
    )

    result = env_manager.discover_environment(
        home=tmp_path / "home",
        config=_config(profile),
    )

    assert not [resource for resource in result.resources if resource.kind == "memory"]
    assert result.tools[0].memories_path is None
    assert "not an object" in result.tools[0].memory_blocker


def test_windows_host_maps_posix_auto_memory_only_for_named_wsl_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "wsl-home-via-host"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"autoMemoryDirectory": "/home/alice/.claude-memory"}),
        encoding="utf-8",
    )
    profile = PlatformProfile(
        name="claude-wsl",
        tool_id="claude-code",
        environment_kind="wsl",
        environment_name="Ubuntu-24.04",
        home_dir=str(home),
        settings_path="~/.claude/settings.json",
        memories_dir="~/.claude/projects",
    )
    monkeypatch.setattr(env_manager, "_host_is_windows", lambda: True)

    mapped, problem = env_manager._claude_memory_override_path(
        "/home/alice/.claude-memory",
        profile=profile,
        home=home,
    )
    assert str(mapped) == r"\\wsl.localhost\Ubuntu-24.04\home\alice\.claude-memory"
    assert problem == ""

    for unsafe_value in (
        r"C:\Users\alice\.claude-memory",
        r"\\wsl.localhost\Debian\home\alice\.claude-memory",
        r"\\server\share\.claude-memory",
    ):
        blocked_path, blocked_problem = env_manager._claude_memory_override_path(
            unsafe_value,
            profile=profile,
            home=home,
        )
        assert blocked_path is None
        assert "matching named distro UNC" in blocked_problem

    matching_unc, matching_problem = env_manager._claude_memory_override_path(
        r"\\wsl.localhost\Ubuntu-24.04\home\alice\.claude-memory",
        profile=profile,
        home=home,
    )
    assert str(matching_unc) == r"\\wsl.localhost\Ubuntu-24.04\home\alice\.claude-memory"
    assert matching_problem == ""

    result = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
        scan_global=False,
    )

    assert result.tools[0].memories_path is None
    assert "cannot be accessed" in result.tools[0].memory_blocker

    profile.environment_name = ""
    blocked = env_manager.discover_environment(
        home=tmp_path / "unused",
        config=_config(profile),
        scan_global=False,
    )
    assert blocked.tools[0].memories_path is None
    assert "explicit WSL distro identity" in blocked.tools[0].memory_blocker


def test_implicit_cross_runtime_home_is_never_scanned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_home = tmp_path / "windows-home"
    wrong_memory = process_home / ".claude" / "projects" / "wrong" / "memory"
    _write_memory(wrong_memory, heading="Must not scan host home as WSL")
    (process_home / ".claude" / "settings.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    profile = PlatformProfile(
        name="claude-wsl",
        tool_id="claude-code",
        environment_kind="wsl",
        environment_name="Ubuntu",
        home_dir="~",
        settings_path="~/.claude/settings.json",
        memories_dir="~/.claude/projects",
    )
    monkeypatch.setattr(
        env_manager,
        "current_environment_identity",
        lambda: ("windows", ""),
    )

    result = env_manager.discover_environment(
        home=process_home,
        config=_config(profile),
    )

    assert result.resources == []
    assert result.tools[0].detected is False
    assert "differs from the current process" in result.tools[0].memory_blocker
