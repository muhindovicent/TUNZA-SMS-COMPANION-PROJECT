"""Add durable idempotency and storage-reference uniqueness to protected_evidence.

Revision ID: 0008_evidence_idempotency
Revises: 0007_evidence_patient
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_evidence_idempotency"
down_revision = "0007_evidence_patient"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "tunza_runtime"


def upgrade() -> None:
    op.add_column(
        "protected_evidence",
        sa.Column(
            "idempotency_key",
            sa.String(128),
            nullable=True,
        ),
    )
    op.add_column(
        "protected_evidence",
        sa.Column(
            "request_hash",
            sa.LargeBinary(),
            nullable=False,
            server_default=sa.text("decode('', 'hex')"),
        ),
    )
    op.add_column(
        "protected_evidence",
        sa.Column(
            "key_version",
            sa.String(64),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_index(
        "ix_protected_evidence_idempotency_key",
        "protected_evidence",
        ["idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_protected_evidence_facility_idempotency_key",
        "protected_evidence",
        ["facility_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_protected_evidence_patient_facility_storage_reference",
        "protected_evidence",
        ["patient_id", "facility_id", "storage_reference"],
    )
    # Ensure existing rows have a non-null request_hash (default is empty digest).
    op.execute(
        "UPDATE protected_evidence SET request_hash = decode('', 'hex') WHERE request_hash IS NULL"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON protected_evidence TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.drop_constraint(
        "uq_protected_evidence_patient_facility_storage_reference",
        "protected_evidence",
        type_="unique",
    )
    op.drop_constraint(
        "uq_protected_evidence_facility_idempotency_key",
        "protected_evidence",
        type_="unique",
    )
    op.drop_index("ix_protected_evidence_idempotency_key", table_name="protected_evidence")
    op.drop_column("protected_evidence", "key_version")
    op.drop_column("protected_evidence", "request_hash")
    op.drop_column("protected_evidence", "idempotency_key")
