"""Detect resource types and normalize user-facing resource arguments."""

from __future__ import annotations

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
}


class ResourceDetectionError(RuntimeError):
    """Raised when LPM cannot safely infer a resource."""


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
        if (p / "SKILL.md").is_file() or len(list(p.glob("*/SKILL.md"))) == 1:
            return "skill"
        if (p / ".claude-plugin" / "plugin.json").is_file() or (
            p / ".codex-plugin" / "plugin.json"
        ).is_file():
            return "plugin"
        if any((p / name).is_file() for name in ("mcp.yaml", "mcp.yml", "mcp.json")):
            return "mcp"
        if "rule" in p.name.lower() and list(p.glob("*.md")):
            return "rule"
        if list(p.glob("*.md")):
            return "prompt"
    elif p.is_file():
        lower = p.name.lower()
        if lower in {"mcp.yaml", "mcp.yml", "mcp.json"}:
            return "mcp"
        if p.suffix.lower() == ".md":
            return "rule" if "rule" in lower else "prompt"

    raise ResourceDetectionError(
        f"Could not detect resource type for {p}. Pass --type skill|mcp|rule|prompt|plugin."
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
        "User-Agent": "LPM",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _tree_url(parsed: ParsedGithubUrl, subdir: str) -> str:
    return f"{parsed.repo_url}/tree/{parsed.ref}/{subdir.strip('/')}"
