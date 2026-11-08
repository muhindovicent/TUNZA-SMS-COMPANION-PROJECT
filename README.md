# TUNZA — Maternal Health SMS Companion

**A Zest Technologies project**

TUNZA is a proposed maternal-health communication and care-coordination system. It is designed to help participating healthcare teams register consenting adult participants, coordinate antenatal care (ANC), and deliver clinically approved, pregnancy-stage-appropriate SMS information.

**Project status: Phase 1A — Application Foundation.** A React/TypeScript shell, Flask health API, and PostgreSQL/Alembic foundation are implemented for local development. Clinical features remain unimplemented. All development and verification use synthetic data exclusively.

**Start here:** [Phase 1A setup and verification guide](docs/phase-1a-foundation.md).

## Proposed pilot hospitals

- **St. Paul's Hospital Kasese**
- **Mt St Mary's Hospital Kasese**

These are the proposed pilot facilities. Listing them here does not assert an executed facility agreement or approval to operate.

## Objectives

- Support adult pregnancy registration, versioned consent, and safe withdrawal.
- Coordinate facility-confirmed ANC appointments and recorded attendance.
- Deliver independently reviewed English and Luganda weekly educational messages.
- Track submission, provider-reported delivery, failures, and follow-up work.
- Provide facility- and assignment-scoped staff dashboards.
- Demonstrate inbound replies, STOP, and controlled escalation workflows in the SMS sandbox.
- Establish auditable privacy, access, and messaging controls.
- Prepare for future postnatal expansion without implementing postnatal functionality now.

TUNZA supports communication and coordination; it is not an automated diagnosis or emergency-response service.

## Technology stack

| Layer | Planned technology |
| --- | --- |
| Frontend | React with TypeScript |
| Backend | Python Flask, modular application factory and blueprints |
| Database | PostgreSQL, SQLAlchemy, Alembic |
| Messaging | Africa's Talking sandbox through a provider adapter |
| Scheduling | Separate supervised Python worker backed by durable PostgreSQL records |
| Authentication | Server-side sessions, CSRF protection, privileged-role MFA |

The proposed architecture is a modular monolith. PostgreSQL-backed scheduling avoids an initial Redis/Celery operating dependency while retaining a path to additional infrastructure when measurements justify it.

## Documentation

Start with the [draft documentation index](docs/README.md).

| Document | Purpose |
| --- | --- |
| [Phase 1A setup](docs/phase-1a-foundation.md) | Local installation, environment variables, health API, migrations and tests |
| [Requirements](docs/requirements.md) | Recorded v1.1 mapping, MVP boundaries, acceptance identifiers |
| [Architecture](docs/architecture.md) | Components, module boundaries, deployments, design decisions |
| [Database schema and ERD](docs/database-schema.md) | Entities, relationships, keys, constraints, data dictionary |
| [Role-based permissions](docs/role-based-permissions.md) | Dashboard access and precise proposed permission matrix |
| [REST API specifications](docs/api-specifications.md) | Contracts, validation, authentication, concurrency, errors |
| [SMS workflows](docs/sms-workflows.md) | Scheduling, approval, opt-out, provider events, recovery |
| [Security and consent](docs/security-and-consent.md) | Database isolation, consent, audit, recovery controls |
| [Implementation roadmap](docs/implementation-roadmap.md) | Sequential milestones and measurable acceptance criteria |
| [Unresolved decisions](docs/unresolved-decisions.md) | Decisions requiring source verification or stakeholder approval |

The earlier [technical implementation plan](TECHNICAL_IMPLEMENTATION_PLAN.md) is retained as the historical baseline. The documents under `docs/` are a **draft refinement**, not a claim that unresolved clinical or operational decisions have been approved.

## Development boundary

This milestone contains application infrastructure only: no authentication, stakeholder dashboards, patient registration, SMS messaging, or clinical features. See the setup guide for local installation. No application has been deployed. Do not add credentials, provider API keys, production exports, real patient information, or unreviewed clinical content to source control. Development and sandbox exercises must use synthetic patient data exclusively.

Postnatal workflows, minors' enrolment, live two-way SMS, and live emergency escalation require separate approval and are outside the current implementation authorization.
