"""agent observability — intent, answer mode, verifier pass rate, tokens, trace

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("intent", sa.String(30), nullable=True))
    op.add_column("messages", sa.Column("answer_mode", sa.String(20), nullable=True))
    op.add_column("messages", sa.Column("verifier_pass_rate", sa.Float(), nullable=True))
    op.add_column("messages", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("trace", postgresql.JSONB(), nullable=True))
    op.create_index("ix_messages_intent", "messages", ["intent"])


def downgrade() -> None:
    op.drop_index("ix_messages_intent", table_name="messages")
    for col in ("trace", "total_tokens", "verifier_pass_rate", "answer_mode", "intent"):
        op.drop_column("messages", col)
