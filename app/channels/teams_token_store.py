"""File-backed OAuth token store for the teams-mcp client.

Persisting the refresh token to disk is what makes the credential durable. We
manage the refresh ourselves (a deterministic HTTP refresh_token grant against
the server's /token endpoint) rather than leaning on the MCP SDK's built-in
refresh, which under unattended polling would fall back to interactive auth and
die. A one-time interactive sign-in seeds the file; from then on `refresh()` at
startup and on a timer keeps the access token live indefinitely."""

import contextlib
import json
import time
from pathlib import Path

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_PATH = Path("data/local/teams_token.json")


def token_endpoint() -> str:
    """Derive the OAuth token endpoint from the MCP URL (…/mcp → …/token)."""
    base = (get_settings().teams_mcp_url or "").rstrip("/")
    for suffix in ("/mcp", "/sse"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base + "/token"


class FileTokenStorage(TokenStorage):
    def __init__(self, path: Path | None = None):
        self._path = path or Path(get_settings().teams_token_path or DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _write(self, data: dict) -> None:
        # create owner-only, then write — the file holds a refresh token
        self._path.touch(mode=0o600, exist_ok=True)
        with contextlib.suppress(OSError, NotImplementedError):
            self._path.chmod(0o600)  # Windows ignores POSIX modes; ACLs govern there
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def has_credentials(self) -> bool:
        return bool(self._read().get("tokens"))

    # ── self-managed refresh (bypasses the SDK's fragile refresh path) ──

    def access_token(self) -> str | None:
        return (self._read().get("tokens") or {}).get("access_token")

    def _expires_at(self) -> float:
        with contextlib.suppress(Exception):
            return float(self._read().get("expires_at", 0))
        return 0.0

    def needs_refresh(self, skew: int = 300) -> bool:
        """True if the access token is missing or within `skew` s of expiry."""
        return time.time() >= self._expires_at() - skew

    async def refresh(self, force: bool = False) -> bool:
        """Exchange the stored refresh token for a fresh access token and persist
        the rotated pair. No-op (returns True) when the current token is still
        comfortably valid unless force=True. Serialized by the caller's lock."""
        data = self._read()
        tokens = data.get("tokens") or {}
        rt = tokens.get("refresh_token")
        cid = (data.get("client_info") or {}).get("client_id")
        if not rt or not cid:
            log.warning("teams.token_no_refresh_creds")
            return False
        if not force and not self.needs_refresh():
            return True

        import httpx

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(token_endpoint(), data={
                    "grant_type": "refresh_token",
                    "refresh_token": rt,
                    "client_id": cid,
                }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:
            log.warning("teams.token_refresh_err", error=str(exc)[:200])
            return False
        if resp.status_code != 200:
            log.warning("teams.token_refresh_http", status=resp.status_code,
                        body=resp.text[:200])
            return False

        new = resp.json()
        for k in ("access_token", "token_type", "scope", "expires_in"):
            if k in new:
                tokens[k] = new[k]
        if new.get("refresh_token"):          # rotation — keep the new one
            tokens["refresh_token"] = new["refresh_token"]
        data["tokens"] = tokens
        data["expires_at"] = time.time() + float(new.get("expires_in", 3600))
        self._write(data)
        log.info("teams.token_refreshed", expires_in=new.get("expires_in"))
        return True
