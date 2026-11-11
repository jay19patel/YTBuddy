"""LLM daily budget: 0 now means "no limit" and is the default (free API keys cost nothing).

Users still on the old automatic $2.00 default are moved to 0; a value someone chose stays.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.alter_column("daily_budget_usd", server_default="0")
    op.execute(sa.text('UPDATE "user" SET daily_budget_usd = 0 WHERE daily_budget_usd = 2.0'))


def downgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.alter_column("daily_budget_usd", server_default="2.0")
