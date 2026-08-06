"""Shared detection helpers for likely credentials in managed text files."""

from __future__ import annotations

import re
from dataclasses import dataclass

SECRET_VALUE_RE = re.compile(
    r"(?P<prefix>(?:api[_-]?key|token|secret|password|auth[_-]?token)\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[A-Za-z0-9_./+=-]{8,})"
    r"(?P=quote)",
    re.IGNORECASE,
)
HIGH_RISK_SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)",
)
CREDENTIAL_URL_RE = re.compile(
    r"(?P<prefix>https?://[^/\s:@]+:)(?P<secret>[^@\s/]+)(?P<suffix>@)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SecretTextMatch:
    reason: str
    preview: str


def find_secret_text(text: str) -> SecretTextMatch | None:
    value_match = SECRET_VALUE_RE.search(text)
    if value_match and not is_secret_placeholder(value_match.group("value")):
        return SecretTextMatch(
            reason="token-like key/value",
            preview=_line_preview(text, value_match.start()),
        )
    high_risk = HIGH_RISK_SECRET_RE.search(text)
    if high_risk:
        return SecretTextMatch(
            reason="high-risk token pattern",
            preview=_line_preview(text, high_risk.start()),
        )
    return None


def redact_secret_text(text: str) -> str:
    """Replace recognized secret values while preserving useful surrounding context."""
    redacted = SECRET_VALUE_RE.sub(
        lambda match: f"{match.group('prefix')}${{SECRET_VALUE}}",
        text,
    )
    redacted = HIGH_RISK_SECRET_RE.sub("${SECRET_VALUE}", redacted)
    return CREDENTIAL_URL_RE.sub(
        lambda match: f"{match.group('prefix')}${{SECRET_VALUE}}{match.group('suffix')}",
        redacted,
    )


def is_secret_placeholder(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("${")
        and normalized.endswith("}")
        and len(normalized) > 3
    ) or normalized in {"***REDACTED***", "REPLACE_ME", "replace_me"}


def _line_preview(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    line = text[start:end].strip()
    return redact_secret_text(line)[:160]
