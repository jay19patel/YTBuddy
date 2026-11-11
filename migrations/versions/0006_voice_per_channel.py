"""voice.md belongs to each channel (it was one voice shared by all of a user's channels).

Existing voice text is copied onto every channel the user owns, then dropped from the user.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("schedule") as b:
        b.add_column(sa.Column("voice_md", sa.Text(), nullable=False, server_default=""))
    op.execute(
        sa.text(
            'UPDATE schedule SET voice_md = COALESCE((SELECT u.voice_md FROM "user" u '
            "JOIN channel c ON c.user_id = u.id WHERE c.id = schedule.channel_id), '')"
        )
    )
    with op.batch_alter_table("user") as b:
        b.drop_column("voice_md")


def downgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.add_column(sa.Column("voice_md", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("schedule") as b:
        b.drop_column("voice_md")
