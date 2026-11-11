"""Channel About (description + keywords), mirrored from YouTube and editable here.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("channel") as b:
        b.add_column(sa.Column("about", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("keywords", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("channel") as b:
        b.drop_column("keywords")
        b.drop_column("about")
