"""AI keys and models configured in the app (global and per user) instead of .env / config files.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_key",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("secret", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "llm_alias",
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("alias", sa.String(), primary_key=True),
        sa.Column("models", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_alias")
    op.drop_table("llm_key")
