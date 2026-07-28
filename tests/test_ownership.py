from __future__ import annotations

from pathlib import Path

import pytest

from cc_port.core.models import RegistryItem
from cc_port.core.ownership import (
    is_cc_port_managed,
    managed_marker_path,
    managed_resource_key,
    read_managed_marker,
    remove_managed_marker,
    write_managed_marker,
)


def _entry(*, kind: str = "prompt") -> RegistryItem:
    return RegistryItem(
        name="demo",
        kind=kind,
        source="local",
        path=f"{kind}s/demo",
    )


def test_directory_marker_path_and_ownership_remain_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    entry = _entry(kind="skill")

    marker = write_managed_marker(target, entry, platform="cursor")

    assert marker == target / ".cc-port-managed.json"
    assert managed_marker_path(target) == marker
    assert managed_resource_key(target) == "skill:demo"
    assert is_cc_port_managed(target, resource_key="skill:demo")
    assert not is_cc_port_managed(target, resource_key="prompt:demo")

    dotted_directory = tmp_path / "demo.bundle"
    dotted_directory.mkdir()
    assert managed_marker_path(dotted_directory) == dotted_directory / ".cc-port-managed.json"


def test_file_marker_is_hidden_sidecar_and_can_be_removed(tmp_path: Path) -> None:
    target = tmp_path / "commands" / "demo.md"
    target.parent.mkdir()
    target.write_text("prompt\n", encoding="utf-8")
    entry = _entry()

    marker = write_managed_marker(target, entry, platform="cursor")
    payload = read_managed_marker(target)

    assert marker == target.parent / ".demo.md.cc-port-managed.json"
    assert managed_marker_path(target) == marker
    assert payload is not None
    assert payload == {
        "managed_by": "cc-port",
        "resource": "demo",
        "kind": "prompt",
        "resource_key": "prompt:demo",
        "platform": "cursor",
        "updated_at": payload["updated_at"],
    }
    assert managed_resource_key(target) == "prompt:demo"
    assert is_cc_port_managed(target, resource_key="prompt:demo")
    assert not is_cc_port_managed(target, resource_key="skill:demo")
    assert remove_managed_marker(target) is True
    assert remove_managed_marker(target) is False
    assert read_managed_marker(target) is None


def test_file_marker_atomic_replace_does_not_mutate_hardlink_alias(
    tmp_path: Path,
) -> None:
    target = tmp_path / "commands" / "demo.md"
    target.parent.mkdir()
    target.write_text("prompt\n", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep":"unchanged"}\n', encoding="utf-8")
    marker = managed_marker_path(target, file_target=True)
    try:
        marker.hardlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Hard links are unavailable: {exc}")

    written = write_managed_marker(
        target,
        _entry(),
        platform="cursor",
        file_target=True,
    )

    assert written == marker
    assert outside.read_text(encoding="utf-8") == '{"keep":"unchanged"}\n'
    assert read_managed_marker(target, file_target=True)["resource_key"] == "prompt:demo"


def test_missing_file_target_with_extension_uses_same_sidecar_path(tmp_path: Path) -> None:
    target = tmp_path / "commands" / "demo.md"
    entry = _entry()

    marker = write_managed_marker(target, entry, platform="cursor")

    assert not target.exists()
    assert marker == target.parent / ".demo.md.cc-port-managed.json"
    assert marker.is_file()
    assert read_managed_marker(target)["resource_key"] == "prompt:demo"
    assert managed_resource_key(target) == "prompt:demo"
    assert is_cc_port_managed(target, resource_key="prompt:demo")
    assert remove_managed_marker(target) is True


def test_missing_extensionless_target_is_not_marked_as_a_file(tmp_path: Path) -> None:
    target = tmp_path / "demo"

    assert managed_marker_path(target) == target / ".cc-port-managed.json"
    assert write_managed_marker(target, _entry(), platform="cursor") is None
    assert not target.exists()


def test_explicit_file_target_keeps_sidecar_stable_when_path_is_a_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "commands" / "demo.md"
    target.mkdir(parents=True)

    assert managed_marker_path(target) == target / ".cc-port-managed.json"
    assert managed_marker_path(target, file_target=True) == (
        target.parent / ".demo.md.cc-port-managed.json"
    )


def test_markdown_symlink_uses_file_sidecar_but_cannot_be_managed(tmp_path: Path) -> None:
    target = tmp_path / "commands" / "demo.md"
    outside = tmp_path / "outside.md"
    target.parent.mkdir()
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    marker = target.parent / ".demo.md.cc-port-managed.json"

    assert managed_marker_path(target) == marker
    assert read_managed_marker(target) is None
    assert write_managed_marker(target, _entry(), platform="cursor") is None
    assert not is_cc_port_managed(target, resource_key="prompt:demo")
    assert remove_managed_marker(target) is False
    assert target.is_symlink()


def test_file_marker_symlink_is_never_read_or_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "commands" / "demo.md"
    target.parent.mkdir()
    target.write_text("prompt\n", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text(
        '{"managed_by":"cc-port","resource_key":"prompt:demo"}\n',
        encoding="utf-8",
    )
    marker = managed_marker_path(target)
    try:
        marker.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    before = outside.read_bytes()

    assert read_managed_marker(target) is None
    assert not is_cc_port_managed(target, resource_key="prompt:demo")
    assert write_managed_marker(target, _entry(), platform="cursor") is None
    assert outside.read_bytes() == before
