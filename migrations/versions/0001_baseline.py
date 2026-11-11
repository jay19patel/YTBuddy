"""Baseline: the schema the app had before migrations (commit 9f05e75).

Existing databases are stamped at this revision by `python -m ytstudio.cli migrate`.

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("thumb", sa.String(), nullable=False),
        sa.Column("creds", sa.String(), nullable=False),
        sa.Column("subscribers", sa.Integer(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("videos", sa.Integer(), nullable=False),
        sa.Column("stats_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "snapshot",
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("subscribers", sa.Integer(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("videos", sa.Integer(), nullable=False),
    )
    op.create_index("ix_snapshot_channel_id", "snapshot", ["channel_id"])
    op.create_table(
        "analyticscache",
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("days", sa.Integer(), primary_key=True),
        sa.Column("data", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stats_minutes", sa.Integer(), nullable=False),
        sa.Column("analytics_minutes", sa.Integer(), nullable=False),
        sa.Column("stats_enabled", sa.Boolean(), nullable=False),
        sa.Column("analytics_enabled", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
    )


def downgrade() -> None:
    for name in ("settings", "analyticscache", "snapshot", "channel"):
        op.drop_table(name)
