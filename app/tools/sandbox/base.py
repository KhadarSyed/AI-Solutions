"""Sandbox contract — isolated execution of LLM-generated analysis code.

error_kind distinguishes a code fault (regenerate with the traceback, up to 3×)
from an infrastructure fault (abort immediately, no retry)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class ErrorKind(StrEnum):
    NONE = "none"
    CODE = "code_error"       # traceback from the user code → LLM regenerates
    INFRA = "infra_error"     # sandbox/docker/timeout → abort, non-retryable


@dataclass
class SandboxResult:
    ok: bool
    error_kind: ErrorKind = ErrorKind.NONE
    stdout: str = ""
    stderr: str = ""
    result_json: dict | None = None      # parsed /sandbox/out.json if the code wrote it
    files: dict[str, str] = field(default_factory=dict)  # name → base64 (e.g. chart PNG)


class SandboxExecutor(ABC):
    @abstractmethod
    async def run(
        self, code: str, input_files: dict[str, bytes], timeout: float = 30.0
    ) -> SandboxResult: ...
