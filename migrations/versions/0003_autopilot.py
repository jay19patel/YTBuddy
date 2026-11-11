"""Autopilot tables: videos, trends, schedules, pipeline runs/steps, dead letters, LLM costs,
review-queue assets, master media files, in-app notifications.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("channel") as b:
        b.add_column(sa.Column("uploads_playlist", sa.String(), nullable=True))
        b.add_column(sa.Column("videos_synced_at", sa.DateTime(), nullable=True))

    op.create_table(
        "video",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("channel_id", sa.String(), nullable=False, index=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("likes", sa.Integer(), nullable=False),
        sa.Column("comments", sa.Integer(), nullable=False),
        sa.Column("avg_view_duration", sa.Float(), nullable=True),
        sa.Column("avg_view_pct", sa.Float(), nullable=True),
        sa.Column("ctr", sa.Float(), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("retention", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "trendvideo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.String(), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("multiple", sa.Float(), nullable=False),
        sa.UniqueConstraint("channel_id", "day", "video_id"),
    )
    op.create_table(
        "schedule",
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("run_time", sa.String(), nullable=False),
        sa.Column("days", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("region", sa.String(), nullable=False),
        sa.Column("niche", sa.String(), nullable=False),
        sa.Column("custom_topics", sa.JSON(), nullable=False),
        sa.Column("target_minutes", sa.Integer(), nullable=False),
        sa.Column("shorts_enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_utc", sa.DateTime(), nullable=True, index=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "pipeline_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("channel_id", sa.String(), nullable=False, index=True),
        sa.Column("pipeline", sa.String(), nullable=False),
        sa.Column("idem_key", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "step_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "step"),
    )
    op.create_table(
        "dead_letter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=True, index=True),
        sa.Column("step", sa.String(), nullable=True),
        sa.Column("task", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "llm_call",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("template", sa.String(), nullable=False),
        sa.Column("template_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )
    op.create_table(
        "asset",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("channel_id", sa.String(), nullable=False, index=True),
        sa.Column("run_id", sa.Integer(), nullable=True, index=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
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


def downgrade() -> None:
    for name in (
        "notification",
        "media_file",
        "asset",
        "llm_call",
        "dead_letter",
        "step_run",
        "pipeline_run",
        "schedule",
        "trendvideo",
        "video",
    ):
        op.drop_table(name)
    with op.batch_alter_table("channel") as b:
        b.drop_column("videos_synced_at")
        b.drop_column("uploads_playlist")
