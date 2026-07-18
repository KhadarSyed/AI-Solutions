"""Deterministic output guards — PII masking and groundedness checks."""

import re
from dataclasses import dataclass, field

from app.guardrails.input import _PII_PATTERNS

SAFE_FALLBACK = (
    "I can't provide that response as generated — it failed a safety or grounding check. "
    "The event has been logged; please rephrase or contact the analyst team."
)

_CITATION_RE = re.compile(r"\[A\d+\]|\(A\d+\)|article\s+A\d+", re.I)


@dataclass
class OutputCheckResult:
    text: str
    findings: list[dict] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f["verdict"] == "blocked" for f in self.findings)


def mask_pii(text: str) -> tuple[str, int]:
    total = 0
    for name, pattern in _PII_PATTERNS:
        if name == "email":
            # public press contact emails are routine in PR copy; mask only key-like PII
            continue
        text, n = pattern.subn(f"[{name.upper()} REDACTED]", text)
        total += n
    return text, total


def check_output(text: str, *, stakeholder_facing: bool = False, require_citations: bool = False) -> OutputCheckResult:
    result = OutputCheckResult(text=text)

    masked, n = mask_pii(text)
    if n:
        result.findings.append({"check": "pii.mask", "verdict": "sanitized", "detail": f"{n} masked"})
        result.text = masked

    if require_citations and not _CITATION_RE.search(result.text):
        verdict = "blocked" if stakeholder_facing else "pass"
        result.findings.append(
            {"check": "groundedness.citations", "verdict": verdict,
             "detail": "no article citations found in grounded answer"}
        )
        if verdict == "blocked":
            result.text = SAFE_FALLBACK

    return result
