"""chat admin read cursor (unread badges)

Revision ID: 008
Revises: 007
Create Date: 2026-06-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column("admin_last_read_message_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Существующие диалоги считаем прочитанными — иначе все старые сообщения станут «новыми».
    op.execute(
        """
        UPDATE chat_threads t
        SET admin_last_read_message_id = COALESCE(
            (
                SELECT MAX(m.id)
                FROM chat_messages m
                WHERE m.thread_id = t.id AND m.deleted_at IS NULL
            ),
            0
        )
        """
    )


def downgrade() -> None:
    op.drop_column("chat_threads", "admin_last_read_message_id")
