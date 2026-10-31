"""Dense embeddings per chunk and embedding version."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "20261023_0009"
down_revision = "20260907_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("corpus_version_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_version", sa.String(200), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.PrimaryKeyConstraint("chunk_id", "embedding_version"),
        sa.CheckConstraint("dims > 0", name="ck_chunk_embeddings_dims_positive"),
        sa.CheckConstraint("vector_dims(embedding) = dims", name="ck_chunk_embeddings_dims"),
        sa.ForeignKeyConstraint(
            ["course_id", "corpus_version_id", "chunk_id"],
            ["chunks.course_id", "chunks.corpus_version_id", "chunks.id"],
            name="fk_chunk_embeddings_chunk",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_scope",
        "chunk_embeddings",
        ["course_id", "corpus_version_id", "embedding_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_scope", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.execute("DROP EXTENSION IF EXISTS vector")
