"""Structured tutor interventions, derived follow-up turns and continuity metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261029_0010"
down_revision = "20261023_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("kind", sa.String(16), nullable=False, server_default="question"),
    )
    op.add_column("messages", sa.Column("intent", sa.String(16)))
    op.add_column("messages", sa.Column("parent_message_id", sa.Uuid()))
    op.add_column("messages", sa.Column("retrieval_query", sa.Text()))
    op.add_column("messages", sa.Column("query_version", sa.String(64)))
    op.add_column("messages", sa.Column("intervention", postgresql.JSONB()))
    op.add_column("messages", sa.Column("contract_version", sa.String(64)))
    op.add_column("messages", sa.Column("situation", sa.String(32)))
    op.add_column("messages", sa.Column("help_level", sa.Integer()))
    op.create_unique_constraint(
        "uq_messages_conversation_id", "messages", ["conversation_id", "id"]
    )
    op.create_foreign_key(
        "fk_messages_parent",
        "messages",
        "messages",
        ["conversation_id", "parent_message_id"],
        ["conversation_id", "id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_messages_kind", "messages", "kind IN ('question', 'followup', 'action')"
    )
    op.create_check_constraint(
        "ck_messages_intent",
        "messages",
        "intent IS NULL OR intent IN ('another_hint', 'explain', 'dont_know')",
    )
    op.create_check_constraint(
        "ck_messages_parent", "messages", "(kind = 'action') = (parent_message_id IS NOT NULL)"
    )
    op.execute("ALTER TABLE messages DROP CONSTRAINT ck_messages_mode")
    op.create_check_constraint(
        "ck_messages_mode",
        "messages",
        "mode IS NULL OR mode IN ('hint','guided_question','explanation','abstain','clarify')",
    )


def downgrade() -> None:
    # Clarifications are single questions; keep them readable under the previous mode set.
    op.execute("UPDATE messages SET mode = 'guided_question' WHERE mode = 'clarify'")
    op.execute("ALTER TABLE messages DROP CONSTRAINT ck_messages_mode")
    op.create_check_constraint(
        "ck_messages_mode",
        "messages",
        "mode IS NULL OR mode IN ('hint','guided_question','explanation','abstain')",
    )
    for name in ("ck_messages_parent", "ck_messages_intent", "ck_messages_kind"):
        op.drop_constraint(name, "messages", type_="check")
    op.drop_constraint("fk_messages_parent", "messages", type_="foreignkey")
    op.drop_constraint("uq_messages_conversation_id", "messages", type_="unique")
    for column in (
        "help_level",
        "situation",
        "contract_version",
        "intervention",
        "query_version",
        "retrieval_query",
        "parent_message_id",
        "intent",
        "kind",
    ):
        op.drop_column("messages", column)
