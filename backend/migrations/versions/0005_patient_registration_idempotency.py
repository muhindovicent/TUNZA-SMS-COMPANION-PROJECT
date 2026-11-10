"""Durable synthetic patient registration replay records.

Revision ID: 0005_patient_idempotency
Revises: 0004_patient_registry
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_patient_idempotency"
down_revision = "0004_patient_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_registration_idempotency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("facility_id", sa.Uuid(), sa.ForeignKey("facilities.id"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("response_cipher", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("actor_id", "key", name="uq_patient_registration_actor_key"),
    )
    op.execute("ALTER TABLE patient_registration_idempotency ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE patient_registration_idempotency FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY patient_registration_idempotency_actor_scope
        ON patient_registration_idempotency
        USING (actor_id = tunza_current_uuid('tunza.actor_id'))
        WITH CHECK (actor_id = tunza_current_uuid('tunza.actor_id'))""")
    op.execute("GRANT SELECT, INSERT, UPDATE ON patient_registration_idempotency TO tunza_runtime")


def downgrade() -> None:
    op.drop_table("patient_registration_idempotency")
