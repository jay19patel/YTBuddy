"""Users, sessions, OAuth state; encrypt channel tokens; import legacy tokens/ files.

Data migration: every plain-text `channel.creds` JSON is re-encrypted with TOKEN_ENCRYPTION_KEY and the
duplicated OAuth client secret is dropped from the blob. Rows are kept, never deleted.

Revision ID: 0002
Revises: 0001
"""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("voice_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("telegram_chat_id", sa.String(), nullable=True),
        sa.Column("daily_budget_usd", sa.Float(), nullable=False, server_default="2.0"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.create_table(
        "websession",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("csrf", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_websession_user_id", "websession", ["user_id"])
    op.create_table(
        "oauthstate",
        sa.Column("state", sa.String(), primary_key=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("binding", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("channel") as b:
        b.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("status", sa.String(), nullable=False, server_default="ok"))
        b.create_index("ix_channel_user_id", ["user_id"])

    encrypt_tokens()


def encrypt_tokens() -> None:
    from ytstudio.adapters.token_vault import PREFIX, TokenCipher, token_from_dict, token_to_dict
    from ytstudio.config import get_settings

    conn = op.get_bind()
    channel = sa.table(
        "channel",
        sa.column("id"),
        sa.column("title"),
        sa.column("thumb"),
        sa.column("creds"),
        sa.column("subscribers"),
        sa.column("views"),
        sa.column("videos"),
        sa.column("status"),
    )
    rows = list(conn.execute(sa.select(channel.c.id, channel.c.creds)))
    folder = get_settings().legacy_tokens_dir
    legacy = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not any(not r.creds.startswith(PREFIX) for r in rows) and not legacy:
        return
    cipher = TokenCipher(get_settings().token_encryption_key)  # raises with instructions if unset

    for r in rows:
        if not r.creds.startswith(PREFIX):
            blob = cipher.encrypt(token_to_dict(token_from_dict(json.loads(r.creds))))
            conn.execute(channel.update().where(channel.c.id == r.id).values(creds=blob))

    # The old app re-imported tokens/<id>.json on every start, resurrecting removed channels.
    # Import once here (only channels not already present), then the folder can be deleted.
    have, imported = {r.id for r in rows}, 0
    for path in legacy:
        data = json.loads(Path(path).read_text())
        if data["id"] in have:
            continue
        creds = data["creds"] if isinstance(data["creds"], dict) else json.loads(data["creds"])
        conn.execute(
            channel.insert().values(
                id=data["id"],
                title=data["title"],
                thumb=data["thumb"],
                subscribers=0,
                views=0,
                videos=0,
                status="ok",
                creds=cipher.encrypt(token_to_dict(token_from_dict(creds))),
            )
        )
        imported += 1
    if legacy:
        print(
            f"Imported {imported} legacy token file(s); the rest were already in the database. Delete the folder now: rm -r {folder}"
        )


def downgrade() -> None:
    # Tokens stay encrypted on downgrade: writing secrets back in plain text is not offered.
    with op.batch_alter_table("channel") as b:
        b.drop_index("ix_channel_user_id")
        b.drop_column("status")
        b.drop_column("user_id")
    op.drop_table("oauthstate")
    op.drop_index("ix_websession_user_id", "websession")
    op.drop_table("websession")
    op.drop_index("ix_user_email", "user")
    op.drop_table("user")
