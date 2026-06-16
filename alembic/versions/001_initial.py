"""Initial schema migration."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("firm_id", sa.String(64), server_default="marigold"),
        sa.Column("disposition", sa.String(32), server_default="NEW"),
        sa.Column("intake_data", sa.JSON(), server_default="{}"),
        sa.Column("engagement_data", sa.JSON(), server_default="{}"),
        sa.Column("consent_data", sa.JSON(), server_default="{}"),
        sa.Column("retainer_data", sa.JSON(), server_default="{}"),
        sa.Column("language", sa.String(8), server_default="en"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("event_id", sa.String(64), unique=True, nullable=False),
        sa.Column("lead_id", sa.String(64), index=True, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("sequence", sa.Integer(), server_default="0"),
        sa.Column("data", sa.JSON(), server_default="{}"),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "audit_trail",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("lead_id", sa.String(64), index=True, nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("details", sa.JSON(), server_default="{}"),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_trail")
    op.drop_table("events")
    op.drop_table("leads")
