# TUNZA documentation

> **DRAFT — Technical refinement for review. Synthetic patient data exclusively. No application implementation or deployment is authorized by these documents.**

## Basis and status

These documents refine the repository's [earlier implementation plan](../TECHNICAL_IMPLEMENTATION_PLAN.md) and the architecture discussed with Zest Technologies. They maintain React, Python Flask, PostgreSQL, and Africa's Talking sandbox.

The standalone approved MVP Requirements Specification v1.1 is not present in this repository. FR-01–FR-10 below follow the mapping captured in the earlier plan; they are not a transcription of the original specification. Exact clause-level reconciliation is tracked as **D01** in the [decision register](unresolved-decisions.md).

The architecture direction has been requested for documentation. Detailed schema and API choices here remain proposed draft contracts. Clinical wording, reminder policies, access delegations, and live-operation approvals are not implied.

## Reading order

For the implemented local infrastructure, see [Phase 1A setup and verification](phase-1a-foundation.md). The draft architecture below describes the broader future system; Phase 1A does not implement identity, isolation or clinical workflows.

For automated checks of that foundation, see the [Phase 1B CI verification checkpoint](ci-verification.md).

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
