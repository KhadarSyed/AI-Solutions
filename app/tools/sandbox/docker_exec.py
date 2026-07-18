"""DockerExecSandbox — runs code in the network-less prsol-sandbox sidecar via
`docker exec`. Job files travel through the shared sandbox_tmp volume."""

import asyncio
import base64
import json
import shutil
import uuid
from pathlib import Path

from app.config.settings import get_settings
from app.observability.logging import get_logger
from app.tools.sandbox.base import ErrorKind, SandboxExecutor, SandboxResult

log = get_logger(__name__)

# The api and sandbox containers share the `sandbox_tmp` volume at /sandbox.
SHARED_ROOT = Path("/sandbox")

HARNESS = '''
import json, sys, base64, traceback, os
JOB = os.environ["JOB_DIR"]
try:
    with open(os.path.join(JOB, "user_code.py")) as f:
        code = f.read()
    ns = {{"__name__": "__sandbox__", "JOB_DIR": JOB}}
    exec(compile(code, "user_code.py", "exec"), ns)
    out = {{}}
    if "result" in ns and isinstance(ns["result"], (dict, list)):
        out["result"] = ns["result"]
    files = {{}}
    for fn in os.listdir(JOB):
        if fn.endswith((".png", ".svg", ".json")) and fn not in ("user_code.py", "harness.py"):
            with open(os.path.join(JOB, fn), "rb") as fh:
                files[fn] = base64.b64encode(fh.read()).decode()
    out["files"] = files
    with open(os.path.join(JOB, "_out.json"), "w") as f:
        json.dump({{"ok": True, "out": out}}, f)
except Exception:
    with open(os.path.join(JOB, "_out.json"), "w") as f:
        json.dump({{"ok": False, "traceback": traceback.format_exc()}}, f)
'''


class DockerExecSandbox(SandboxExecutor):
    def __init__(self):
        self._container = get_settings().sandbox_container

    async def run(
        self, code: str, input_files: dict[str, bytes], timeout: float = 30.0
    ) -> SandboxResult:
        job_id = uuid.uuid4().hex[:12]
        job_dir = SHARED_ROOT / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "user_code.py").write_text(code, encoding="utf-8")
            (job_dir / "harness.py").write_text(
                HARNESS.format(), encoding="utf-8"
            )
            for name, data in input_files.items():
                (job_dir / name).write_bytes(data)
        except Exception as exc:
            return SandboxResult(ok=False, error_kind=ErrorKind.INFRA,
                                 stderr=f"failed to stage job: {exc}")

        container_job = f"/sandbox/{job_id}"
        cmd = [
            "docker", "exec", "-e", f"JOB_DIR={container_job}",
            self._container, "python", f"{container_job}/harness.py",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            return SandboxResult(ok=False, error_kind=ErrorKind.INFRA,
                                 stderr=f"sandbox timeout after {timeout}s")
        except FileNotFoundError:
            return SandboxResult(ok=False, error_kind=ErrorKind.INFRA,
                                 stderr="docker CLI unavailable in this container")
        except Exception as exc:
            return SandboxResult(ok=False, error_kind=ErrorKind.INFRA, stderr=str(exc))

        out_file = job_dir / "_out.json"
        if proc.returncode != 0 and not out_file.exists():
            return SandboxResult(
                ok=False, error_kind=ErrorKind.INFRA,
                stderr=(stderr_b.decode(errors="replace") or "sandbox exec failed")[:2000],
            )
        try:
            payload = json.loads(out_file.read_text())
        except Exception as exc:
            return SandboxResult(ok=False, error_kind=ErrorKind.INFRA,
                                 stderr=f"no sandbox output: {exc}")

        if not payload.get("ok"):
            return SandboxResult(
                ok=False, error_kind=ErrorKind.CODE,
                stderr=payload.get("traceback", "")[:4000],
                stdout=stdout_b.decode(errors="replace")[:2000],
            )

        out = payload.get("out", {})
        return SandboxResult(
            ok=True, error_kind=ErrorKind.NONE,
            stdout=stdout_b.decode(errors="replace")[:2000],
            result_json=out.get("result"),
            files=out.get("files", {}),
        )

    def cleanup(self, job_id: str) -> None:
        shutil.rmtree(SHARED_ROOT / job_id, ignore_errors=True)


def _decode_files(files: dict[str, str]) -> dict[str, bytes]:
    return {k: base64.b64decode(v) for k, v in files.items()}
