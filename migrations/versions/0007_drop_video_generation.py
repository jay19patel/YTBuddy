"""Video/audio generation is gone: drop the master-file table and the per-channel Shorts switch.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("media_file")
    with op.batch_alter_table("schedule") as b:
        b.drop_column("shorts_enabled")


def downgrade() -> None:
    with op.batch_alter_table("schedule") as b:
        b.add_column(sa.Column("shorts_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "media_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("channel_id", sa.String(), nullable=False, index=True),
        sa.Column("video_id", sa.String(), nullable=True, index=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
