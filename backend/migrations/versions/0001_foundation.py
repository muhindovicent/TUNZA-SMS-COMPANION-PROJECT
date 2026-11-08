"""Establish migration history without creating domain or authentication tables.

Revision ID: 0001_foundation
Revises: None
"""

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates its own version table. Domain schema belongs to later phases.
    pass


def downgrade() -> None:
    pass
