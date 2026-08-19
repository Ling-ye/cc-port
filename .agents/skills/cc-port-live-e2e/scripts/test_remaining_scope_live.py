from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_port.core.config import Config, GitConfig, InstallConfig, ResourcesConfig
from cc_port.core.models import Registry, RegistryItem
from cc_port.core.ownership import managed_resource_key
from cc_port.core.platforms import PlatformProfile, PlatformsConfig
from cc_port.core.registry import load_registry, save_registry
from cc_port.infrastructure import git_ops
from cc_port.services import asset_sync, resource_discovery
from cc_port.services.local_path_probe import (
    WINDOWS_REPARSE_TAG_LX_SYMLINK,
    is_known_canonical_link_target,
    probe_local_path,
)


GIT = git_ops.discover_git_executable(configured="").path
if GIT is None:
    raise RuntimeError("Git for Windows is required for this live integration test.")

def _configured_wsl_distro() -> str:
    requested = os.environ.get("CC_PORT_E2E_WSL_DISTRO", "").strip()
    if requested or sys.platform != "win32":
        return requested
    result = subprocess.run(
        ["wsl.exe", "-l", "-q"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return ""
    names = [
        item.strip()
        for item in result.stdout.decode("utf-16le", errors="replace").lstrip("\ufeff").splitlines()
        if item.strip()
    ]
    return names[0] if len(names) == 1 else ""


WSL_DISTRO = _configured_wsl_distro()

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="The remaining-scope integration requires native Windows Python.",
)


def _require_wsl_distro() -> None:
    if not WSL_DISTRO:
        pytest.skip("Set CC_PORT_E2E_WSL_DISTRO to one exact installed WSL distro name.")


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob(repo: Path, relative_path: str) -> bytes:
    result = subprocess.run(
        [str(GIT), "-C", str(repo), "show", f"HEAD:{relative_path}"],
        check=True,
        capture_output=True,
    )
    return result.stdout


def _wsl_root(name: str) -> tuple[str, Path]:
    linux_root = f"/tmp/{name}"
    unc_root = Path(rf"\\wsl.localhost\{WSL_DISTRO}\tmp\{name}")
    return linux_root, unc_root


def _remove_wsl_root(linux_root: str) -> None:
    _run("wsl.exe", "-d", WSL_DISTRO, "--", "rm", "-rf", "--", linux_root)


def _seed_bare_remote(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    seed = tmp_path / "seed"
    seed.mkdir()
    payloads = {
        "skill": b"---\nname: remaining-e2e-skill\ndescription: isolated live WSL test\n---\n\n# Skill\n",
        "mcp": b'{"command":"node","args":["isolated-server.js"]}\n',
        "instruction": b"# Isolated WSL instruction\n",
        "memory": b"# Isolated WSL memory\n",
        "rule": b"# Isolated WSL rule\n",
        "prompt": b"# Isolated WSL prompt\n",
    }
    skill = seed / "skills" / "remaining-e2e-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(payloads["skill"])
    mcp = seed / "mcp" / "remaining-e2e-mcp"
    mcp.mkdir(parents=True)
    (mcp / "mcp.json").write_bytes(payloads["mcp"])
    instruction = seed / "instructions" / "remaining-e2e-instruction"
    instruction.mkdir(parents=True)
    (instruction / "CLAUDE.md").write_bytes(payloads["instruction"])
    memory = seed / "memories" / "remaining-e2e-memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_bytes(payloads["memory"])
    rule = seed / "rules" / "remaining-e2e-rule"
    rule.mkdir(parents=True)
    (rule / "RULE.md").write_bytes(payloads["rule"])
    prompt = seed / "prompts" / "remaining-e2e-prompt"
    prompt.mkdir(parents=True)
    (prompt / "remaining-e2e-prompt.md").write_bytes(payloads["prompt"])
    save_registry(
        Registry(
            items=[
                RegistryItem(
                    name="remaining-e2e-skill",
                    kind="skill",
                    source="local",
                    path="skills/remaining-e2e-skill",
                    platforms=["claude-code"],
                ),
                RegistryItem(
                    name="remaining-e2e-mcp",
                    kind="mcp",
                    source="local",
                    path="mcp/remaining-e2e-mcp",
                    platforms=["claude-code"],
                ),
                RegistryItem(
                    name="remaining-e2e-instruction",
                    kind="instruction",
                    source="local",
                    path="instructions/remaining-e2e-instruction",
                    platforms=["claude-code"],
                ),
                RegistryItem(
                    name="remaining-e2e-memory",
                    kind="memory",
                    source="local",
                    path="memories/remaining-e2e-memory",
                    platforms=["claude-code"],
                ),
                RegistryItem(
                    name="remaining-e2e-rule",
                    kind="rule",
                    source="local",
                    path="rules/remaining-e2e-rule",
                    platforms=["cursor"],
                ),
                RegistryItem(
                    name="remaining-e2e-prompt",
                    kind="prompt",
                    source="local",
                    path="prompts/remaining-e2e-prompt",
                    platforms=["cursor"],
                ),
            ]
        ),
        seed / "registry.yaml",
    )
    git_ops.init_repo(seed)
    git_ops.add_all(seed)
    git_ops.commit(seed, "seed isolated remaining-scope repository")
    bare = tmp_path / "remote.git"
    _run(str(GIT), "clone", "--bare", str(seed), str(bare))
    return bare, payloads


def test_real_windows_junction_probe_and_discovery(tmp_path: Path) -> None:
    canonical = tmp_path / ".agents" / "skills" / "junction-e2e"
    logical = tmp_path / ".claude" / "skills" / "junction-e2e"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text(
        "---\nname: junction-e2e\ndescription: junction e2e\n---\n",
        encoding="utf-8",
    )
    logical.parent.mkdir(parents=True)
    result = _run("cmd.exe", "/d", "/c", "mklink", "/J", str(logical), str(canonical))
    try:
        probe = probe_local_path(logical)
        resources = resource_discovery.discover_resources(
            scope="directory",
            root_path=logical.parent,
        )
        assert probe.path_kind == "junction"
        assert probe.health == "ready"
        assert probe.content_path == canonical.resolve()
        assert is_known_canonical_link_target(probe) is True
        assert len(resources) == 1
        assert resources[0].path == logical.absolute()
        assert resources[0].content_path == canonical.resolve()
        assert resources[0].link_target_trusted is True
        print(
            "JUNCTION_EVIDENCE",
            probe.path_kind,
            probe.health,
            probe.reparse_tag_hex,
            result.returncode,
        )
    finally:
        if logical.exists():
            logical.rmdir()


def test_real_wsl_lx_symlink_is_blocked_without_stopping_scan() -> None:
    _require_wsl_distro()
    name = f"cc-port-remaining-link-{os.getpid()}"
    linux_root, unc_root = _wsl_root(name)
    try:
        _run("wsl.exe", "-d", WSL_DISTRO, "--", "mkdir", "-p", f"{linux_root}/skills/good")
        (unc_root / "skills" / "good" / "SKILL.md").write_text(
            "---\nname: good\ndescription: good\n---\n",
            encoding="utf-8",
        )
        _run(
            "wsl.exe",
            "-d",
            WSL_DISTRO,
            "--",
            "ln",
            "-s",
            "good",
            f"{linux_root}/skills/lx-link",
        )
        link = unc_root / "skills" / "lx-link"
        probe = probe_local_path(link)
        resources = resource_discovery.discover_resources(
            scope="directory",
            root_path=unc_root / "skills",
        )
        assert probe.path_kind == "wsl-symlink"
        assert probe.health == "unsupported-wsl"
        assert probe.content_path is None
        assert probe.reparse_tag == WINDOWS_REPARSE_TAG_LX_SYMLINK
        assert [(item.name_hint, item.status) for item in resources] == [
            ("good", "ready"),
            ("lx-link", "blocked"),
        ]
        print(
            "WSL_LINK_EVIDENCE",
            probe.path_kind,
            probe.health,
            probe.reparse_tag_hex,
            [(item.name_hint, item.status) for item in resources],
        )
    finally:
        _remove_wsl_root(linux_root)


def test_real_wsl_unc_roundtrip_with_local_bare_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_wsl_distro()
    name = f"cc-port-remaining-download-{os.getpid()}"
    linux_root, unc_root = _wsl_root(name)
    try:
        home = unc_root / "claude-home"
        cursor_home = unc_root / "cursor-home"
        home.mkdir(parents=True)
        cursor_home.mkdir(parents=True)
        bare, payloads = _seed_bare_remote(tmp_path)
        state = tmp_path / "state"
        monkeypatch.setenv("CC_PORT_STATE_HOME", str(state))
        profile = PlatformProfile(
            name="claude-wsl-real",
            tool_id="claude-code",
            environment_kind="wsl",
            environment_name=WSL_DISTRO,
            display_name="Claude Code real WSL integration",
            home_dir=str(home),
            skills_dir="~/.claude/skills",
            mcp_json="~/.claude.json",
            instructions_path="~/.claude/CLAUDE.md",
            memories_dir="~/.claude/projects",
            memory_install_names={"remaining-e2e-memory": "isolated-project-slot"},
        )
        cursor_profile = PlatformProfile(
            name="cursor-wsl-real",
            tool_id="cursor",
            environment_kind="wsl",
            environment_name=WSL_DISTRO,
            display_name="Cursor real WSL integration",
            home_dir=str(cursor_home),
            rules_dir="~/.cursor/rules",
            prompts_dir="~/.cursor/commands",
        )
        cfg = Config(
            git=GitConfig(executable=str(GIT)),
            install=InstallConfig(target=str(tmp_path / "install-cache")),
            resources=ResourcesConfig(
                repo_url=str(bare),
                local_path=str(tmp_path / "legacy-workspace"),
                branch="main",
            ),
            platforms=PlatformsConfig(profiles=[profile, cursor_profile]),
        )
        inventory = asset_sync.build_asset_inventory(
            config=cfg,
            scan_local=True,
            refresh_remote=True,
            scan_global=False,
            enabled_profiles_only=True,
        )
        assert inventory.remote_available is True
        resources = (
            ("skill", "remaining-e2e-skill", profile.name),
            ("mcp", "remaining-e2e-mcp", profile.name),
            ("instruction", "remaining-e2e-instruction", profile.name),
            ("memory", "remaining-e2e-memory", profile.name),
            ("rule", "remaining-e2e-rule", cursor_profile.name),
            ("prompt", "remaining-e2e-prompt", cursor_profile.name),
        )
        for kind, resource_name, platform in resources:
            plan = asset_sync.build_asset_action_plan(
                "download",
                kind=kind,
                name=resource_name,
                platform=platform,
                config=cfg,
            )
            assert plan.blocked is False, plan.blockers
            result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)
            assert result.status == "succeeded", result.message

        skill_target = home / ".claude" / "skills" / "remaining-e2e-skill" / "SKILL.md"
        mcp_target = home / ".claude.json"
        instruction_target = home / ".claude" / "CLAUDE.md"
        memory_target = (
            home
            / ".claude"
            / "projects"
            / "isolated-project-slot"
            / "memory"
            / "MEMORY.md"
        )
        rule_target = cursor_home / ".cursor" / "rules" / "remaining-e2e-rule" / "RULE.md"
        prompt_target = cursor_home / ".cursor" / "commands" / "remaining-e2e-prompt.md"
        assert skill_target.read_bytes() == payloads["skill"]
        assert instruction_target.read_bytes() == payloads["instruction"]
        assert memory_target.read_bytes() == payloads["memory"]
        assert rule_target.read_bytes() == payloads["rule"]
        assert prompt_target.read_bytes() == payloads["prompt"]
        assert b'"remaining-e2e-mcp"' in mcp_target.read_bytes()
        assert b'"isolated-server.js"' in mcp_target.read_bytes()
        assert managed_resource_key(
            home / ".claude" / "skills" / "remaining-e2e-skill"
        ) == "skill:remaining-e2e-skill"
        assert managed_resource_key(instruction_target, file_target=True) == (
            "instruction:remaining-e2e-instruction"
        )
        assert managed_resource_key(
            memory_target.parent,
            file_target=True,
        ) == "memory:remaining-e2e-memory"
        assert managed_resource_key(rule_target.parent) == "rule:remaining-e2e-rule"
        assert managed_resource_key(prompt_target) == "prompt:remaining-e2e-prompt"
        _run(
            "wsl.exe",
            "-d",
            WSL_DISTRO,
            "--",
            "test",
            "-f",
            f"{linux_root}/claude-home/.claude/skills/remaining-e2e-skill/SKILL.md",
        )

        updated = {
            "skill": b"---\nname: remaining-e2e-skill\ndescription: updated isolated live WSL test\n---\n\n# Updated Skill\n",
            "instruction": b"# Updated isolated WSL instruction\n",
            "memory": b"# Updated isolated WSL memory\n",
            "rule": b"# Updated isolated WSL rule\n",
            "prompt": b"# Updated isolated WSL prompt\n",
        }
        skill_target.write_bytes(updated["skill"])
        instruction_target.write_bytes(updated["instruction"])
        memory_target.write_bytes(updated["memory"])
        rule_target.write_bytes(updated["rule"])
        prompt_target.write_bytes(updated["prompt"])
        mcp_document = json.loads(mcp_target.read_text(encoding="utf-8"))
        mcp_document["mcpServers"]["remaining-e2e-mcp"]["args"] = [
            "isolated-server-updated.js"
        ]
        mcp_target.write_text(
            json.dumps(mcp_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        upload_commits: list[str] = []
        for kind, resource_name, platform in resources:
            plan = asset_sync.build_asset_action_plan(
                "upload",
                kind=kind,
                name=resource_name,
                platform=platform,
                config=cfg,
            )
            assert plan.blocked is False, plan.blockers
            result = asset_sync.apply_asset_action_plan(plan.operation_id, config=cfg)
            assert result.status == "succeeded", result.message
            upload_commits.append(result.remote_commit)

        verify = tmp_path / "verify"
        _run(str(GIT), "clone", "--branch", "main", str(bare), str(verify))
        registry = load_registry(verify / "registry.yaml")
        assert len(registry.items) == 6
        assert _git_blob(verify, "skills/remaining-e2e-skill/SKILL.md") == updated["skill"]
        assert _git_blob(
            verify,
            "instructions/remaining-e2e-instruction/CLAUDE.md",
        ) == updated["instruction"]
        assert _git_blob(
            verify,
            "memories/remaining-e2e-memory/MEMORY.md",
        ) == updated["memory"]
        assert _git_blob(verify, "rules/remaining-e2e-rule/RULE.md") == updated["rule"]
        assert _git_blob(
            verify,
            "prompts/remaining-e2e-prompt/remaining-e2e-prompt.md",
        ) == updated["prompt"]
        remote_mcp = json.loads(
            _git_blob(verify, "mcp/remaining-e2e-mcp/mcp.json").decode("utf-8")
        )
        assert remote_mcp["args"] == ["isolated-server-updated.js"]
        print(
            "WSL_ROUNDTRIP_EVIDENCE",
            inventory.remote_commit,
            upload_commits[-1],
            len(registry.items),
            _sha256(skill_target),
            _sha256(instruction_target),
            _sha256(memory_target),
        )
    finally:
        _remove_wsl_root(linux_root)


def test_real_network_failure_and_git_credential_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CC_PORT_STATE_HOME", str(tmp_path / "state"))
    home = tmp_path / "home"
    skill = home / ".cursor" / "skills" / "offline-local"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: offline-local\ndescription: offline local\n---\n",
        encoding="utf-8",
    )
    profile = PlatformProfile(
        name="cursor-offline-real",
        tool_id="cursor",
        environment_kind="windows",
        home_dir=str(home),
        skills_dir="~/.cursor/skills",
    )
    cfg = Config(
        git=GitConfig(executable=str(GIT)),
        install=InstallConfig(target=str(tmp_path / "install-cache")),
        resources=ResourcesConfig(
            repo_url="http://127.0.0.1:9/cc-port-unreachable.git",
            local_path=str(tmp_path / "legacy-workspace"),
            branch="main",
        ),
        platforms=PlatformsConfig(profiles=[profile]),
    )
    snapshot = asset_sync._refresh_remote_snapshot(cfg, refresh=True)
    inventory = asset_sync.build_asset_inventory(
        config=cfg,
        scan_local=True,
        remote_snapshot=snapshot,
        scan_global=True,
        enabled_profiles_only=True,
    )
    local_rows = [
        row
        for row in inventory.rows
        if row.resource_key == "skill:offline-local" and row.platform == profile.name
    ]
    print(
        "OFFLINE_INVENTORY_ROWS",
        [
            (
                row.resource_key,
                row.platform,
                row.local_exists,
                row.supported,
                row.blockers,
            )
            for row in inventory.rows
        ],
    )
    assert snapshot.available is False
    assert inventory.remote_available is False
    assert len(local_rows) == 1 and local_rows[0].local_exists is True
    plan = asset_sync.build_asset_action_plan(
        "upload",
        kind="skill",
        name="offline-local",
        platform=profile.name,
        local_instance_id=local_rows[0].local_instance_id,
        config=cfg,
        _remote_snapshot=snapshot,
        _inventory=inventory,
        _persist=False,
    )
    assert plan.blocked is True
    assert any("unavailable" in blocker.lower() for blocker in plan.blockers)
    ready = git_ops.git_credential_status(str(GIT))
    missing = git_ops.git_credential_status(str(tmp_path / "missing-git.exe"))
    assert ready.ready is True
    assert ready.gcm_available is True
    assert ready.gcm_configured is True
    assert missing.state == "git_missing"
    assert missing.ready is False
    print(
        "FAILURE_EVIDENCE",
        snapshot.available,
        len(local_rows),
        plan.blocked,
        ready.state,
        missing.state,
    )
