"""Patient-specific evidence association for consent grants.

Revision ID: 0007_patient_evidence_association
Revises: 0006_patient_eligibility_consent
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_patient_evidence_association"
down_revision = "0006_patient_eligibility_consent"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "tunza_runtime"


def upgrade() -> None:
    op.add_column(
        "protected_evidence",
        sa.Column(
            "patient_id",
            sa.Uuid(),
            sa.ForeignKey(
                "patients.id",
                name="fk_protected_evidence_patient_id_patients",
                ondelete="RESTRICT",
            ),
            nullable=True,
            index=True,
        ),
    )
    op.create_unique_constraint(
        "uq_protected_evidence_patient_id",
        "protected_evidence",
        ["patient_id", "id"],
    )
    op.execute("ALTER TABLE protected_evidence ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE protected_evidence FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY protected_evidence_patient_scope ON protected_evidence "
        "USING (patient_id = tunza_current_uuid('tunza.patient_id') "
        "AND facility_id = tunza_current_uuid('tunza.facility_id')) "
        "WITH CHECK (patient_id = tunza_current_uuid('tunza.patient_id') "
        "AND facility_id = tunza_current_uuid('tunza.facility_id'))"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON protected_evidence TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS protected_evidence_patient_scope ON protected_evidence")
    op.drop_constraint("uq_protected_evidence_patient_id", "protected_evidence", type_="unique")
    op.drop_column("protected_evidence", "patient_id")
