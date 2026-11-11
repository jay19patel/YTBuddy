"""Encrypted OAuth token storage. Only the refresh token, access token, expiry and scopes are kept;
the Google client id/secret come from config, never from the stored blob."""

import json
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for
from ytstudio.domain.errors import NotFound, ReconnectNeeded
from ytstudio.domain.models import OAuthToken

PREFIX = "v1:"


class TokenCipher:
    def __init__(self, keys: str):
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        if not parts:
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        self.f = MultiFernet([Fernet(k) for k in parts])  # first key encrypts; all keys decrypt

    def encrypt(self, data: dict[str, Any]) -> str:
        return PREFIX + self.f.encrypt(json.dumps(data).encode()).decode()

    def decrypt(self, blob: str) -> dict[str, Any]:
        if not blob.startswith(PREFIX):
            raise InvalidToken("not an encrypted token blob")
        return json.loads(self.f.decrypt(blob[len(PREFIX) :].encode()))

    def rotate(self, blob: str) -> str:
        return PREFIX + self.f.rotate(blob[len(PREFIX) :].encode()).decode()


def token_to_dict(tok: OAuthToken) -> dict[str, Any]:
    return {
        "refresh_token": tok.refresh_token,
        "token": tok.access_token,
        "expiry": tok.expiry.isoformat() if tok.expiry else None,
        "scopes": tok.scopes,
    }


def token_from_dict(d: dict[str, Any]) -> OAuthToken:
    """Also reads the legacy google Credentials.to_json() shape."""
    expiry = d.get("expiry")
    return OAuthToken(
        refresh_token=d.get("refresh_token") or "",
        access_token=d.get("token"),
        expiry=datetime.fromisoformat(expiry.replace("Z", "+00:00")) if expiry else None,
        scopes=list(d.get("scopes") or []),
    )


class DbTokenVault:
    def __init__(self, url: str, cipher: TokenCipher):
        self.url, self.cipher = url, cipher

    def save(self, channel_id: str, token: OAuthToken) -> None:
        with session_for(self.url) as s:
            if not (row := s.get(t.Channel, channel_id)):
                raise NotFound("channel")
            row.creds = self.cipher.encrypt(token_to_dict(token))
            s.add(row)
            s.commit()

    def load(self, channel_id: str) -> OAuthToken:
        with session_for(self.url) as s:
            row = s.get(t.Channel, channel_id)
        if not row:
            raise NotFound("channel")
        try:
            return token_from_dict(self.cipher.decrypt(row.creds))
        except InvalidToken as e:  # wrong/rotated-away key, or never connected
            raise ReconnectNeeded(channel_id) from e
