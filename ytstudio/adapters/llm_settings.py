"""LLM models and keys saved in the database. Keys are encrypted with the same Fernet key as OAuth tokens."""

import logging
from datetime import UTC, datetime

from cryptography.fernet import InvalidToken
from sqlmodel import select

from ytstudio.adapters.persistence import tables as t
from ytstudio.adapters.persistence.db import session_for
from ytstudio.adapters.token_vault import TokenCipher

log = logging.getLogger(__name__)


class DbLLMSettings:
    def __init__(self, url: str, cipher: TokenCipher):
        self.url, self.cipher = url, cipher

    def keys(self, scope: str) -> dict[str, str]:
        out: dict[str, str] = {}
        with session_for(self.url) as s:
            for row in s.exec(select(t.LLMKeyRow).where(t.LLMKeyRow.scope == scope)):
                try:
                    out[row.provider] = str(self.cipher.decrypt(row.secret)["key"])
                except InvalidToken:  # encrypted with a key that is no longer configured: act as if it were not set
                    log.warning("could not decrypt the saved %s key for %s; enter it again", row.provider, scope)
        return out

    def set_key(self, scope: str, provider: str, key: str) -> None:
        with session_for(self.url) as s:
            s.merge(
                t.LLMKeyRow(
                    scope=scope,
                    provider=provider,
                    secret=self.cipher.encrypt({"key": key}),
                    updated_at=datetime.now(UTC),
                )
            )
            s.commit()

    def delete_key(self, scope: str, provider: str) -> None:
        with session_for(self.url) as s:
            if row := s.get(t.LLMKeyRow, (scope, provider)):
                s.delete(row)
                s.commit()

    def aliases(self, scope: str) -> dict[str, list[str]]:
        with session_for(self.url) as s:
            rows = s.exec(select(t.LLMAliasRow).where(t.LLMAliasRow.scope == scope))
            return {r.alias: list(r.models) for r in rows}

    def set_aliases(self, scope: str, aliases: dict[str, list[str]]) -> None:
        with session_for(self.url) as s:
            for alias, models in aliases.items():
                row = s.get(t.LLMAliasRow, (scope, alias))
                if not models:
                    if row:
                        s.delete(row)
                elif row:
                    row.models = models
                    s.add(row)
                else:
                    s.add(t.LLMAliasRow(scope=scope, alias=alias, models=models))
            s.commit()
