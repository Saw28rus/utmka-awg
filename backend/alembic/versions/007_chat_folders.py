"""chat folders + thread.folder_id (CH7)

Revision ID: 007
Revises: 006
Create Date: 2026-06-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "chat_threads",
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_threads_folder_id",
        "chat_threads",
        "chat_folders",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chat_threads_folder_id", "chat_threads", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_threads_folder_id", table_name="chat_threads")
    op.drop_constraint("fk_chat_threads_folder_id", "chat_threads", type_="foreignkey")
    op.drop_column("chat_threads", "folder_id")
    op.drop_table("chat_folders")
