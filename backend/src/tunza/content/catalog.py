"""Live fixture selection/revalidation only; this module never dispatches messages."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select

from tunza.content.models import ContentReview, TemplateVersion
from tunza.extensions import db


@dataclass(frozen=True)
class FixtureReference:
    version_id: UUID
    content_digest: bytes
    publication_version: int


def _current(at):
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise ValueError("A timezone-aware selection instant is required.")
    return (
        select(TemplateVersion.id, TemplateVersion.content_digest, TemplateVersion.version)
        .join(ContentReview, ContentReview.version_id == TemplateVersion.id)
        .where(
            TemplateVersion.state == "published",
            TemplateVersion.effective_from <= at,
            sa.or_(TemplateVersion.effective_to.is_(None), TemplateVersion.effective_to > at),
            ContentReview.decision == "approve",
            ContentReview.author_id == TemplateVersion.author_id,
            ContentReview.reviewer_id != TemplateVersion.author_id,
            ContentReview.content_digest == TemplateVersion.content_digest,
            ContentReview.clinical_signoff.is_(False),
        )
    )


def select_fixture_version(template_id, language, at):
    """No language fallback, no draft/expired/suspended versions, no ORM cache reuse."""
    if language not in {"en", "lg"}:
        return None
    row = db.session.execute(
        _current(at).where(
            TemplateVersion.template_id == template_id, TemplateVersion.language == language
        )
    ).one_or_none()
    return FixtureReference(row.id, row.content_digest, row.version) if row else None


def revalidate_fixture_reference(reference, at):
    """Re-query state/window/review/generation, even for an already selected ID.

    Suspension invalidates cached references; resuming requires a fresh selection.
    A future dispatcher must perform its live checks at use time under its own
    submission transaction. D07 approval and all dispatch remain unimplemented.
    """
    return (
        db.session.execute(
            _current(at).where(
                TemplateVersion.id == reference.version_id,
                TemplateVersion.content_digest == reference.content_digest,
                TemplateVersion.version == reference.publication_version,
            )
        ).first()
        is not None
    )
