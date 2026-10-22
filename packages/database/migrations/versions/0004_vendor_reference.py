"""Expand vendor integration references; rollback only while the optional field is unused."""

import sqlalchemy as sa
from alembic import op

revision = "0004_vendor_ref"
down_revision = "0003_financial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vendors", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.add_column(sa.Column("external_reference", sa.String(80), nullable=True))
        batch.create_unique_constraint(
            "uq_vendors_organization_id_external_reference",
            ["organization_id", "external_reference"],
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM vendors WHERE external_reference IS NOT NULL")
    ):
        raise RuntimeError(
            "Vendor integration references are in use; retain them with a forward migration"
        )
    with op.batch_alter_table("vendors", reflect_kwargs={"resolve_fks": False}) as batch:
        batch.drop_constraint("uq_vendors_organization_id_external_reference", type_="unique")
        batch.drop_column("external_reference")
