"""Synthetic development fixtures for local authentication demonstrations."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import click
from flask import current_app
from sqlalchemy import select

from tunza.extensions import db
from tunza.identity.models import Facility, FacilityMembership, Role, RoleBinding, User
from tunza.identity.passwords import hash_password

SYNTHETIC_FACILITIES = (
    ("SYNTH-A", "Synthetic Clinic A"),
    ("SYNTH-B", "Synthetic Clinic B"),
)
SYNTHETIC_USERS = (
    ("synthetic.pa", "Synthetic Platform Admin", "PA", None, None),
    ("synthetic.fa.a", "Synthetic Facility Admin A", "FA", "SYNTH-A", "SYNTH-A"),
    ("synthetic.vht.a", "Synthetic VHT A", "VHT", "SYNTH-A", "SYNTH-A"),
    ("synthetic.vht.b", "Synthetic VHT B", "VHT", "SYNTH-B", "SYNTH-B"),
)


@click.command("seed-synthetic-identity")
def seed_synthetic_identity() -> None:
    if current_app.config["APP_ENV"] == "production":
        raise click.ClickException("Synthetic identity seeding is disabled in production.")

    now = datetime.now(UTC)
    facilities: dict[str, Facility] = {}
    for code, name in SYNTHETIC_FACILITIES:
        facility = db.session.scalar(select(Facility).where(Facility.code == code))
        if facility is None:
            facility = Facility(code=code, name=name, timezone="Africa/Kampala", active=True)
            db.session.add(facility)
            db.session.flush()
            click.echo(f"Created synthetic facility {code}.")
        facilities[code] = facility

    roles = {role.code: role for role in db.session.scalars(select(Role)).all()}
    grantor: User | None = None
    issued: list[tuple[str, str]] = []

    for login, display_name, _role_code, _facility_code, _membership_code in SYNTHETIC_USERS:
        user = db.session.scalar(select(User).where(User.login_identifier == login))
        if user is None:
            password = secrets.token_urlsafe(18)
            user = User(
                login_identifier=login,
                display_name=display_name,
                password_hash=hash_password(password),
                status="active",
            )
            db.session.add(user)
            db.session.flush()
            issued.append((login, password))
            click.echo(f"Created synthetic account {login}.")
        if login == "synthetic.pa":
            grantor = user

    if grantor is None:
        raise click.ClickException("The synthetic platform administrator is required.")

    for login, _display_name, role_code, facility_code, membership_code in SYNTHETIC_USERS:
        user = db.session.scalar(select(User).where(User.login_identifier == login))
        role = roles.get(role_code)
        if user is None or role is None:
            raise click.ClickException("Synthetic seed data is incomplete.")
        if membership_code is not None:
            membership = db.session.scalar(
                select(FacilityMembership).where(
                    FacilityMembership.user_id == user.id,
                    FacilityMembership.facility_id == facilities[membership_code].id,
                )
            )
            if membership is None:
                db.session.add(
                    FacilityMembership(
                        user_id=user.id,
                        facility_id=facilities[membership_code].id,
                        status="active",
                        starts_at=now,
                    )
                )
        binding = db.session.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.role_id == role.id,
            )
        )
        if binding is None:
            db.session.add(
                RoleBinding(
                    user_id=user.id,
                    role_id=role.id,
                    facility_id=facilities[facility_code].id if facility_code else None,
                    scope_kind=role.scope_kind,
                    starts_at=now,
                    granted_by=grantor.id,
                )
            )

    db.session.commit()
    click.echo("Synthetic identity fixtures are ready. Passwords are shown once:")
    for login, password in issued:
        click.echo(f"  {login} :: {password}")
    if not issued:
        click.echo("  No new accounts were created.")
