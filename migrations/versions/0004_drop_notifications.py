"""Drop notifications: no in-app, email or Telegram messages (events are only logged now).

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("notification")
    with op.batch_alter_table("user") as b:
        b.drop_column("notify_email")
        b.drop_column("telegram_chat_id")


def downgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.add_column(sa.Column("telegram_chat_id", sa.String(), nullable=True))
        b.add_column(sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("link", sa.String(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
