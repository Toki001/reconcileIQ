"""Persist non-financial processing stage while retaining all existing job records."""

import sqlalchemy as sa
from alembic import op

revision = "0006_processing_stage"
down_revision = "0005_evidence_seals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("stage", sa.String(32), nullable=False, server_default="admitted")
    )


def downgrade() -> None:
    # Audit transitions remain retained. This optional operational projection is safe to remove.
    op.drop_column("jobs", "stage")
