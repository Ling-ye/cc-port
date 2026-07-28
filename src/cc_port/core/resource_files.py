"""Shared file-selection policy for managed AI resources."""

from __future__ import annotations

from pathlib import Path

DEFAULT_EXCLUDED_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "cache",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "out",
    "target",
    "temp",
    "tmp",
    "venv",
}
DEFAULT_EXCLUDED_FILES = {
    ".DS_Store",
    ".cc-port-managed.json",
    "Thumbs.db",
}
DEFAULT_EXCLUDED_FILE_NAMES = {item.lower() for item in DEFAULT_EXCLUDED_FILES}
MANAGED_MARKER_SUFFIX = ".cc-port-managed.json"
DEFAULT_EXCLUDED_SUFFIXES = {
    ".7z",
    ".dll",
    ".dylib",
    ".exe",
    ".lock",
    ".log",
    ".msi",
    ".pyc",
    ".so",
    ".zip",
}
SAFE_ENV_SUFFIXES = {".example", ".sample", ".template"}


def is_sensitive_env_file(path: Path) -> bool:
    """Return whether *path* is a real environment file rather than a template."""
    name = path.name.lower()
    if name == ".env":
        return True
    if not name.startswith(".env."):
        return False
    return Path(name).suffix not in SAFE_ENV_SUFFIXES


def is_resource_path_excluded(path: Path) -> bool:
    """Apply policy to a resource-relative path or a single absolute entry."""
    if path.is_symlink():
        return True
    parts = path.parts if not path.is_absolute() else (path.name,)
    lower_parts = {part.lower() for part in parts}
    if lower_parts & DEFAULT_EXCLUDED_DIRS:
        return True
    name = path.name.lower()
    return (
        name in DEFAULT_EXCLUDED_FILE_NAMES
        or name.endswith(MANAGED_MARKER_SUFFIX)
        or path.suffix.lower() in DEFAULT_EXCLUDED_SUFFIXES
        or is_sensitive_env_file(path)
    )


def resource_copy_ignore(directory: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback using the common resource policy."""
    return {
        name
        for name in names
        if (Path(directory) / name).is_symlink()
        or is_resource_path_excluded(Path(name))
    }
