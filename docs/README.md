# TUNZA documentation

> **DRAFT — Technical refinement for review. Synthetic patient data exclusively. No application implementation or deployment is authorized by these documents.**

## Basis and status

These documents refine the repository's [earlier implementation plan](../TECHNICAL_IMPLEMENTATION_PLAN.md) and the architecture discussed with Zest Technologies. They maintain React, Python Flask, PostgreSQL, and Africa's Talking sandbox.

The standalone approved MVP Requirements Specification v1.1 is not present in this repository. FR-01–FR-10 below follow the mapping captured in the earlier plan; they are not a transcription of the original specification. Exact clause-level reconciliation is tracked as **D01** in the [decision register](unresolved-decisions.md).

The architecture direction has been requested for documentation. Detailed schema and API choices here remain proposed draft contracts. Clinical wording, reminder policies, access delegations, and live-operation approvals are not implied.

## Reading order

For the implemented local infrastructure, see [Phase 1A setup and verification](phase-1a-foundation.md). The draft architecture below describes the broader future system; Phase 1A does not implement identity, isolation or clinical workflows.

For automated checks of that foundation, see the [Phase 1B CI verification checkpoint](ci-verification.md).

For implemented staff sign-in, sessions, and facility-scoped access, see the [Phase 1B-1 authentication guide](phase-1b-1-authentication.md).

For the implemented synthetic pregnancy and dating-history milestone, see
[Phase 3A — pregnancy episodes and dating history](phase-3a-pregnancy-dating.md).

For staff-entered synthetic ANC booking workflows, see
[Phase 3B — ANC appointment management](phase-3b-anc-appointments.md).

For the opt-in fixture workflow and its unresolved D04/D09 boundaries, see
[Phase 4A — controlled synthetic transfers](phase-4a-controlled-transfers.md).

For append-only completion and auditable lifecycle closure, see
[Phase 4B — pregnancy completion](phase-4b-pregnancy-completion.md).

For synthetic authoring, independent technical review and publication controls, see
[Phase 5A — clinical-content management](phase-5a-clinical-content.md).

For durable held candidates, redacted scheduling history and synthetic budget tests,
see [Phase 5B — durable message scheduling](phase-5b-durable-scheduling.md).

1. [Requirements and scope](requirements.md)
2. [System architecture](architecture.md)
3. [Database schema and ERD](database-schema.md)
4. [Permissions and stakeholder dashboards](role-based-permissions.md)
5. [API specifications and authentication](api-specifications.md)
6. [SMS and clinical-content workflows](sms-workflows.md)
7. [Security, facility isolation, consent, and audit](security-and-consent.md)
8. [Sequential roadmap and acceptance](implementation-roadmap.md)
9. [Unresolved decisions](unresolved-decisions.md)

## Interpretation rules

- **Recorded requirement:** a requirement captured by the existing baseline; original-source verification remains D01.
- **Draft technical decision:** a proposed implementable design, open to technical review.
- **Pending policy:** a clinical, facility, privacy, or operational decision that must not be guessed.
- Schema and API examples define contracts, not executable migrations or application code.
- Every patient-related example is synthetic. Placeholder values are not valid recipients or provider credentials.
- A held, rejected, unknown, or suppressed message must remain distinguishable from a delivered message.
- Application acceptance, clinical approval, operational readiness, and live-pilot authorization are separate decisions.

## Consistency contract

Permission identifiers are defined in [role-based permissions](role-based-permissions.md), entity names in [database schema](database-schema.md), and workflow states in [SMS workflows](sms-workflows.md). APIs reference those definitions rather than introducing alternate states. Pending policies reference decision identifiers D01–D16.
