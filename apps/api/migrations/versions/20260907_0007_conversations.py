"""Durable conversations, captured evidence and auditable citations.

Revision ID: 20260907_0007
Revises: 20260905_0006
"""

from alembic import op

revision = "20260907_0007"

down_revision = "20260905_0006"

branch_labels = None

depends_on = None


def upgrade() -> None:

    op.create_unique_constraint(
        "uq_chunks_scope", "chunks", ["course_id", "corpus_version_id", "id"]
    )

    op.execute("""
CREATE TABLE conversations (
id UUID NOT NULL,
course_id UUID NOT NULL,
owner_user_id UUID NOT NULL,
idempotency_key VARCHAR(255) NOT NULL,
next_sequence INTEGER DEFAULT '1' NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_conversations_course_id UNIQUE (course_id, id),
CONSTRAINT uq_conversations_creation UNIQUE (course_id, owner_user_id, idempotency_key),
FOREIGN KEY(course_id) REFERENCES courses (id) ON DELETE CASCADE,
FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE RESTRICT
)
    """)

    op.execute("""
CREATE TABLE messages (
id UUID NOT NULL,
course_id UUID NOT NULL,
conversation_id UUID NOT NULL,
corpus_version_id UUID,
sequence INTEGER NOT NULL,
idempotency_key VARCHAR(255) NOT NULL,
question TEXT NOT NULL,
state VARCHAR(16) NOT NULL,
answer TEXT,
mode VARCHAR(32),
grounded BOOLEAN,
failure_code VARCHAR(64),
correlation_id VARCHAR(100) NOT NULL,
prompt_version VARCHAR(100) NOT NULL,
retrieval_config_version VARCHAR(100) NOT NULL,
model_version VARCHAR(255) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
deadline_at TIMESTAMP WITH TIME ZONE NOT NULL,
completed_at TIMESTAMP WITH TIME ZONE,
PRIMARY KEY (id),
CONSTRAINT fk_messages_conversation FOREIGN KEY(course_id, conversation_id) REFERENCES
conversations (course_id, id) ON DELETE CASCADE,
CONSTRAINT fk_messages_corpus FOREIGN KEY(course_id, corpus_version_id) REFERENCES
corpus_versions (course_id, id) ON DELETE RESTRICT,
CONSTRAINT uq_messages_idempotency UNIQUE (conversation_id, idempotency_key),
CONSTRAINT uq_messages_sequence UNIQUE (conversation_id, sequence),
CONSTRAINT uq_messages_scope UNIQUE (course_id, corpus_version_id, id),
CONSTRAINT ck_messages_state CHECK (state IN ('pending', 'completed', 'failed')),
CONSTRAINT ck_messages_question CHECK (length(btrim(question)) BETWEEN 1 AND 4000),
CONSTRAINT ck_messages_sequence CHECK (sequence > 0),
CONSTRAINT ck_messages_terminal_payload CHECK ((state = 'pending' AND answer IS NULL AND
mode IS NULL AND grounded IS NULL AND failure_code IS NULL AND completed_at IS NULL) OR
(state = 'failed' AND answer IS NULL AND mode IS NULL AND grounded IS NULL AND
failure_code IS NOT NULL AND completed_at IS NOT NULL) OR (state = 'completed' AND
answer IS NOT NULL AND mode IS NOT NULL AND grounded IS NOT NULL AND failure_code IS
NULL AND completed_at IS NOT NULL)),
CONSTRAINT ck_messages_mode CHECK (mode IS NULL OR mode IN
('hint','guided_question','explanation','abstain'))
)
    """)

    op.execute("""
CREATE INDEX ix_messages_deadline ON messages (deadline_at) WHERE state = 'pending'
    """)

    op.execute("""
CREATE UNIQUE INDEX uq_messages_one_pending ON messages (conversation_id) WHERE state =
'pending'
    """)

    op.execute("""
CREATE TABLE retrieved_evidence (
message_id UUID NOT NULL,
chunk_id UUID NOT NULL,
course_id UUID NOT NULL,
corpus_version_id UUID NOT NULL,
document_version_id UUID NOT NULL,
document_title VARCHAR(255) NOT NULL,
document_sha256 VARCHAR(64) NOT NULL,
page_start INTEGER NOT NULL,
page_end INTEGER NOT NULL,
fragment VARCHAR(100) NOT NULL,
content TEXT NOT NULL,
content_hash VARCHAR(64) NOT NULL,
PRIMARY KEY (message_id, chunk_id),
CONSTRAINT fk_evidence_message FOREIGN KEY(course_id, corpus_version_id, message_id)
REFERENCES messages (course_id, corpus_version_id, id) ON DELETE CASCADE,
CONSTRAINT fk_evidence_chunk FOREIGN KEY(course_id, corpus_version_id, chunk_id)
REFERENCES chunks (course_id, corpus_version_id, id) ON DELETE RESTRICT,
CONSTRAINT uq_evidence_scope UNIQUE (course_id, message_id, chunk_id)
)
    """)

    op.execute("""
CREATE TABLE citations (
message_id UUID NOT NULL,
chunk_id UUID NOT NULL,
course_id UUID NOT NULL,
ordinal INTEGER NOT NULL,
quote TEXT NOT NULL,
PRIMARY KEY (message_id, chunk_id),
CONSTRAINT fk_citations_evidence FOREIGN KEY(course_id, message_id, chunk_id) REFERENCES
retrieved_evidence (course_id, message_id, chunk_id) ON DELETE CASCADE,
CONSTRAINT uq_citations_ordinal UNIQUE (message_id, ordinal)
)
    """)


def downgrade() -> None:

    op.drop_table("citations")

    op.drop_table("retrieved_evidence")

    op.drop_table("messages")

    op.drop_table("conversations")

    op.drop_constraint("uq_chunks_scope", "chunks", type_="unique")
