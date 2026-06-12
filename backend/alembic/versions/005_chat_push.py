"""chat push subscriptions (web push, CH5)

Revision ID: 005
Revises: 004
Create Date: 2026-06-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chat_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("endpoint", name="uq_chat_push_endpoint"),
    )
    op.create_index(
        "ix_chat_push_subscriptions_chat_user_id",
        "chat_push_subscriptions",
        ["chat_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_push_subscriptions_chat_user_id", table_name="chat_push_subscriptions")
    op.drop_table("chat_push_subscriptions")
