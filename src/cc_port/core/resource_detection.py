"""Detect resource types and normalize user-facing resource arguments."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .models import ItemKind

TYPE_ALIASES: dict[str, ItemKind] = {
    "skill": "skill",
    "skills": "skill",
    "mcp": "mcp",
    "mcps": "mcp",
    "rule": "rule",
    "rules": "rule",
    "prompt": "prompt",
    "prompts": "prompt",
    "plugin": "plugin",
    "plugins": "plugin",
    "instruction": "instruction",
    "instructions": "instruction",
    "memory": "memory",
    "memories": "memory",
}
MANIFEST_FILENAMES = {"cc-port.resource.json", "cc-port-resource.json"}


class ResourceDetectionError(RuntimeError):
    """Raised when CC Port cannot safely infer a resource."""


class MultipleResourceCandidatesError(ResourceDetectionError):
    def __init__(self, candidates: list[str]):
        self.candidates = candidates
        super().__init__(
            "Multiple resource candidates found. Use one of these GitHub tree URLs:\n"
            + "\n".join(f"- {c}" for c in candidates)
        )


@dataclass
class ParsedGithubUrl:
    repo_url: str
    owner: str
    repo: str
    ref: str
    subdir: str


@dataclass
class DetectedRemoteResource:
    repo_url: str
    ref: str
    subdir: str
    kind: ItemKind
    name_hint: str
    tags: list[str]


def normalize_resource_type(value: str | None) -> ItemKind | None:
    if value is None or value == "":
        return None
    key = value.strip().lower()
    if key not in TYPE_ALIASES:
        allowed = ", ".join(sorted(TYPE_ALIASES))
        raise ValueError(f"Invalid --type {value!r}. Expected one of: {allowed}.")
    return TYPE_ALIASES[key]


def detect_local_resource_type(path: Path, explicit_type: str | None = None) -> ItemKind:
    explicit = normalize_resource_type(explicit_type)
    if explicit is not None:
        return explicit

    p = path.expanduser().resolve()
    if p.is_dir():
        manifest_kind = _local_manifest_kind(p)
        if manifest_kind is not None:
            return manifest_kind
        if (p / "SKILL.md").is_file() or len(list(p.glob("*/SKILL.md"))) == 1:
            return "skill"
        if (p / ".claude-plugin" / "plugin.json").is_file() or (
            p / ".codex-plugin" / "plugin.json"
        ).is_file():
            return "plugin"
        if any((p / name).is_file() for name in ("mcp.yaml", "mcp.yml", "mcp.json")):
            return "mcp"
        if (p / "MEMORY.md").is_file():
            return "memory"
        instruction_files = [
            candidate
            for candidate in (p / "CLAUDE.md", p / "AGENTS.md")
            if candidate.is_file()
        ]
        if len(instruction_files) == 1 and len(list(p.glob("*.md"))) == 1:
            return "instruction"
        rule_files = list(p.glob("*.md")) + list(p.glob("*.mdc"))
        if "rule" in p.name.lower() and rule_files:
            return "rule"
        if list(p.glob("*.md")):
            return "prompt"
    elif p.is_file():
        lower = p.name.lower()
        if lower in {"mcp.yaml", "mcp.yml", "mcp.json"}:
            return "mcp"
        if lower in {"agents.md", "claude.md"}:
            return "instruction"
        if lower == ".cursorrules" or p.suffix.lower() == ".mdc":
            return "rule"
        if p.suffix.lower() == ".md":
            parent_names = {parent.name.lower() for parent in p.parents}
            if "rules" in parent_names:
                return "rule"
            if {"commands", "prompts"} & parent_names:
                return "prompt"
            return "rule" if "rule" in lower else "prompt"

    raise ResourceDetectionError(
        "Could not detect resource type for "
        f"{p}. Pass --type skill|mcp|rule|prompt|plugin|instruction|memory."
    )


def parse_github_url(url: str) -> ParsedGithubUrl:
    raw = url.strip().rstrip("/")
    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1].removesuffix(".git")
        parts = path.split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {url}")
        owner, repo = parts[0], parts[1]
        return ParsedGithubUrl(
            repo_url=f"https://github.com/{owner}/{repo}",
            owner=owner,
            repo=repo,
            ref="main",
            subdir="",
        )

    parsed = urlparse(raw)
    if parsed.netloc.lower() != "github.com":
        raise ValueError(f"Only github.com URLs are supported, got: {url}")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"Invalid GitHub URL: {url}")

    owner, repo = parts[0], parts[1].removesuffix(".git")
    ref = "main"
    subdir = ""
    if len(parts) >= 5 and parts[2] == "tree":
        ref = parts[3]
        subdir = "/".join(parts[4:])
    return ParsedGithubUrl(
        repo_url=f"https://github.com/{owner}/{repo}",
        owner=owner,
        repo=repo,
        ref=ref,
        subdir=subdir,
    )


def detect_remote_resource(
    url: str,
    *,
    explicit_type: str | None = None,
    token: str | None = None,
) -> DetectedRemoteResource:
    parsed = parse_github_url(url)
    explicit = normalize_resource_type(explicit_type)
    if explicit is not None:
        return _remote_result(parsed, kind=explicit, subdir=parsed.subdir)

    kind = _detect_github_path_kind(parsed, token=token)
    if kind is not None:
        return _remote_result(parsed, kind=kind, subdir=parsed.subdir)

    skill_candidates = _find_skill_candidates(parsed, token=token)
    if len(skill_candidates) == 1:
        return _remote_result(parsed, kind="skill", subdir=skill_candidates[0])
    if len(skill_candidates) > 1:
        raise MultipleResourceCandidatesError(
            [_tree_url(parsed, candidate) for candidate in skill_candidates]
        )

    raise ResourceDetectionError(
        f"Could not detect resource type for {parsed.repo_url}. "
        "Use a GitHub /tree/<ref>/<path> URL or pass --type."
    )


def _remote_result(parsed: ParsedGithubUrl, *, kind: ItemKind, subdir: str) -> DetectedRemoteResource:
    name_hint = (subdir.rstrip("/").split("/")[-1] if subdir else parsed.repo).removesuffix(".git")
    tag_parts = [kind, parsed.repo]
    if subdir:
        tag_parts.append(name_hint)
    return DetectedRemoteResource(
        repo_url=parsed.repo_url,
        ref=parsed.ref,
        subdir=subdir,
        kind=kind,
        name_hint=name_hint,
        tags=sorted({part.lower() for part in tag_parts if part}),
    )


def _detect_github_path_kind(parsed: ParsedGithubUrl, *, token: str | None) -> ItemKind | None:
    try:
        entries = _github_contents(parsed, parsed.subdir, token=token)
    except Exception as exc:  # noqa: BLE001 - convert network/API failures to detection errors
        raise ResourceDetectionError(f"Could not inspect GitHub repository: {exc}") from exc

    if isinstance(entries, dict):
        name = entries.get("name", "").lower()
        if name in {"mcp.yaml", "mcp.yml", "mcp.json"}:
            return "mcp"
        if name.endswith(".md"):
            return "rule" if "rule" in name else "prompt"
        return None

    names = {entry.get("name", "") for entry in entries}
    lower_names = {name.lower() for name in names}
    manifest_name = next((name for name in names if name.lower() in MANIFEST_FILENAMES), None)
    if manifest_name:
        manifest_kind = _remote_manifest_kind(parsed, parsed.subdir, manifest_name, token=token)
        if manifest_kind is not None:
            return manifest_kind
    if "SKILL.md" in names:
        return "skill"
    if ".claude-plugin" in names or ".codex-plugin" in names:
        return "plugin"
    if {"mcp.yaml", "mcp.yml", "mcp.json"} & lower_names:
        return "mcp"
    md_names = [name for name in lower_names if name.endswith(".md")]
    if any("rule" in name for name in md_names):
        return "rule"
    if md_names and parsed.subdir:
        return "prompt"
    return None


def _find_skill_candidates(parsed: ParsedGithubUrl, *, token: str | None) -> list[str]:
    candidates: list[str] = []

    def visit(path: str, depth: int) -> None:
        if depth > 3:
            return
        entries = _github_contents(parsed, path, token=token)
        if isinstance(entries, dict):
            return
        names = {entry.get("name", "") for entry in entries}
        if "SKILL.md" in names:
            candidates.append(path)
            return
        for entry in entries:
            if entry.get("type") == "dir":
                child = f"{path}/{entry['name']}" if path else entry["name"]
                visit(child, depth + 1)

    visit(parsed.subdir, 0)
    return candidates


def _github_contents(parsed: ParsedGithubUrl, path: str, *, token: str | None) -> Any:
    encoded_path = quote(path.strip("/"))
    url = f"https://api.github.com/repos/{parsed.owner}/{parsed.repo}/contents"
    if encoded_path:
        url += f"/{encoded_path}"
    url += f"?ref={quote(parsed.ref)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CC Port",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _local_manifest_kind(path: Path) -> ItemKind | None:
    manifest = next((path / name for name in MANIFEST_FILENAMES if (path / name).is_file()), None)
    if manifest is None:
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _kind_from_manifest(data)


def _remote_manifest_kind(
    parsed: ParsedGithubUrl,
    subdir: str,
    manifest_name: str,
    *,
    token: str | None,
) -> ItemKind | None:
    manifest_path = f"{subdir.strip('/')}/{manifest_name}" if subdir else manifest_name
    try:
        data = _github_contents(parsed, manifest_path, token=token)
        if not isinstance(data, dict):
            return None
        content = str(data.get("content") or "")
        if not content:
            return None
        text = base64.b64decode(content.encode("ascii")).decode("utf-8")
        return _kind_from_manifest(json.loads(text))
    except Exception:
        return None


def _kind_from_manifest(data: Any) -> ItemKind | None:
    if not isinstance(data, dict):
        return None
    buckets = {str(key).lower(): value for key, value in data.items()}
    if _has_manifest_entries(buckets, "skills"):
        return "skill"
    if _has_manifest_entries(buckets, "mcp"):
        return "mcp"
    if _has_manifest_entries(buckets, "rules"):
        return "rule"
    if _has_manifest_entries(buckets, "prompts") or _has_manifest_entries(buckets, "commands"):
        return "prompt"
    if any(_has_manifest_entries(buckets, key) for key in ("plugins", "agents", "hooks")):
        return "plugin"
    return None


def _has_manifest_entries(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return False


def _tree_url(parsed: ParsedGithubUrl, subdir: str) -> str:
    return f"{parsed.repo_url}/tree/{parsed.ref}/{subdir.strip('/')}"
