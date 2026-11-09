"""staff administration activation tokens

Revision ID: 0003_staff_administration
Revises: 0002_identity_and_access
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_staff_administration"
down_revision = "0002_identity_and_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("activation_token_hash", sa.LargeBinary(32), nullable=True))
    op.add_column(
        "users", sa.Column("activation_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        "uq_users_activation_token_hash", "users", ["activation_token_hash"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_activation_token_hash", "users", type_="unique")
    op.drop_column("users", "activation_expires_at")
    op.drop_column("users", "activation_token_hash")
