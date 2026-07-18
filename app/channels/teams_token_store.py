"""File-backed OAuth token store for the teams-mcp client.

Persisting the refresh token to disk is what makes the credential durable: the
MCP OAuthClientProvider auto-refreshes the access token from it, so a one-time
interactive sign-in keeps the unattended backend authenticated indefinitely."""

import json
from pathlib import Path

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config.settings import get_settings

DEFAULT_PATH = Path("data/local/teams_token.json")


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
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(exclude_none=True)
        self._write(data)

    def has_credentials(self) -> bool:
        return bool(self._read().get("tokens"))
