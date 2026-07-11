"""expand policy embeddings to NVIDIA's 2048-dimensional contract

Revision ID: 4d902f85f639
Revises: 19a203385d8a
Create Date: 2026-07-11 14:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4d902f85f639"
down_revision: str | None = "19a203385d8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_policy_documents_embedding_hnsw", table_name="policy_documents")
    op.execute(
        "ALTER TABLE policy_documents ALTER COLUMN embedding TYPE halfvec(2048) "
        "USING array_fill(0.0::real, ARRAY[2048])::halfvec(2048)"
    )
    op.execute("UPDATE policy_documents SET embedding_model = 'pending-nvidia-reindex'")
    op.execute(
        "CREATE INDEX ix_policy_documents_embedding_hnsw ON policy_documents "
        "USING hnsw (embedding halfvec_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_policy_documents_embedding_hnsw", table_name="policy_documents")
    op.execute(
        "ALTER TABLE policy_documents ALTER COLUMN embedding TYPE vector(8) "
        "USING array_fill(0.0::real, ARRAY[8])::vector(8)"
    )
    op.execute("UPDATE policy_documents SET embedding_model = 'deterministic-test-8d'")
    op.execute(
        "CREATE INDEX ix_policy_documents_embedding_hnsw ON policy_documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )
