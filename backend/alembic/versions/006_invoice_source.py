"""invoice source column (admin | client_self) for self-payment limits (CH10)

Revision ID: 006
Revises: 005
Create Date: 2026-06-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("source", sa.String(16), nullable=False, server_default="admin"),
    )
    op.create_index("ix_invoices_source", "invoices", ["source"])


def downgrade() -> None:
    op.drop_index("ix_invoices_source", table_name="invoices")
    op.drop_column("invoices", "source")
