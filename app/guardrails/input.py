"""Deterministic input guards — fast, free, always on."""

import re
from dataclasses import dataclass, field

MAX_INPUT_CHARS = 60_000

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_instructions", re.compile(
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|above|prior|system)\b.{0,30}"
        r"\b(instruction|prompt|rule|message)s?\b", re.I | re.S)),
    ("role_hijack", re.compile(
        r"\byou are (now|no longer)\b|\bact as\b.{0,30}\b(system|developer|admin)\b", re.I)),
    ("prompt_leak", re.compile(
        r"\b(reveal|show|print|repeat)\b.{0,30}\b(system prompt|instructions|rules)\b", re.I)),
    ("base64_blob", re.compile(r"[A-Za-z0-9+/=]{400,}")),
]

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3}[\s-]?\d{3,4}[\s-]?\d{4}(?!\d)")),
    ("ssn_like", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("api_key_like", re.compile(r"\b(sk|pk|ghp|xox[bap]|AKIA)[-_A-Za-z0-9]{16,}\b")),
]


@dataclass
class InputCheckResult:
    text: str
    findings: list[dict] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f["verdict"] == "blocked" for f in self.findings)


def check_input(text: str, *, strict: bool = False) -> InputCheckResult:
    """Run deterministic input checks.

    strict=True (user-originated content) blocks on injection; otherwise findings
    are recorded and the text is passed through (batch/internal paths).
    """
    result = InputCheckResult(text=text)

    if len(text) > MAX_INPUT_CHARS:
        result.findings.append(
            {"check": "input_size", "verdict": "sanitized", "detail": f"truncated to {MAX_INPUT_CHARS}"}
        )
        result.text = text[:MAX_INPUT_CHARS]

    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(result.text):
            verdict = "blocked" if strict else "pass"
            result.findings.append({"check": f"injection.{name}", "verdict": verdict, "detail": ""})

    for name, pattern in _PII_PATTERNS:
        hits = pattern.findall(result.text)
        if hits:
            result.findings.append(
                {"check": f"pii.{name}", "verdict": "pass", "detail": f"{len(hits)} occurrence(s)"}
            )

    return result
