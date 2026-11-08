# Requirements and scope

> **DRAFT — Recorded v1.1 baseline, awaiting original-source reconciliation (D01). Synthetic patient data exclusively.**

## Purpose

TUNZA supports maternal-health communication and antenatal-care coordination for the proposed St. Paul's Hospital Kasese and Mt St Mary's Hospital Kasese pilot. It must preserve participant privacy, clinical-content accountability, and clear responsibility for follow-up.

## Recorded functional requirements and traceability

The identifiers below come from the previous repository plan. Acceptance scenarios are design commitments proposed by this refinement, not quotations from the absent source specification.

| ID | Recorded requirement | Design coverage | Acceptance |
| --- | --- | --- | --- |
| FR-01 | Facility/staff management and scoped access | Identity, memberships, RBAC, facility isolation | A01–A03 |
| FR-02 | Adult registration, consent evidence, withdrawal | Profiles, eligibility, consent events, suppression | A04–A06 |
| FR-03 | Pregnancy episodes, dating, communication state, completion | Episodes, dating revisions, completion, transfers | A07–A09 |
| FR-04 | ANC reference guidance, confirmed bookings, attendance | Appointment state machine and booking revisions | A10–A12 |
| FR-05 | Clinically approved English/Luganda content | Versioned templates and independent approval | A13–A14 |
| FR-06 | Weekly SMS, reminders, delivery, retries, cost controls | Occurrences, dispatches, attempts, events, reservations | A15–A19 |
| FR-07 | Staff opt-out and sandbox STOP | Consent withdrawal, endpoint suppression | A06, A20 |
| FR-08 | Sandbox two-way replies and ambiguity handling | Restricted routing, candidate matching, response history | A21–A22 |
| FR-09 | Controlled sandbox escalation and action tracking | Versioned reviewed rules and follow-up cases | A23 |
| FR-10 | Facility dashboards, reporting, audit | Scoped queries, event audit, aggregate definitions | A24–A26 |

Detailed entities: [schema](database-schema.md). Enforcement: [permissions](role-based-permissions.md) and [security](security-and-consent.md). Scenario definitions: [roadmap](implementation-roadmap.md).

## Essential MVP

- Individual staff accounts, facility memberships, explicit roles, and caseload/team assignments.
- Adult-only synthetic registration, minimal safe-contact information, and versioned consent.
- Pregnancy dating history, communication controls, and authorized completion.
- Controlled facility transfers with explicit acceptance and bounded handover access.
- Versioned ANC reference guidance distinct from facility-confirmed bookings.
- Appointments, rescheduling, patient acknowledgement, attendance, and follow-up.
- English/Luganda templates with independent clinical approval per version/language.
- Durable weekly occurrences, confirmed-appointment reminders, worker recovery, provider events, and spending limits.
- Sandbox inbound replies, STOP, assistance, ambiguity review, and controlled escalation demonstrations.
- Facility/assignment dashboards, basic approved aggregates, audit, and tested restoration.

Sandbox inbound and escalation demonstrations remain essential in the recorded baseline. Their live equivalents are separate operating capabilities.

## Deferred features

| Later capability | Current boundary |
| --- | --- |
| Postnatal and newborn care | Extension points only; no newborn tables, schedules, or automatic enrolment |
| Minors' enrolment | No enrolment pathway without separately approved eligibility and consent rules |
| Live two-way SMS | Disabled until provider arrangements, cost, channels, and staffing are approved |
| Live emergency escalation | Disabled; no promise of emergency monitoring |
| Patient app/accounts | SMS interaction only in MVP |
| Additional languages/channels | Adapter/version design supports later work |
| EHR integration and advanced analytics | No initial interoperability or predictive features |
| Offline clinical storage | No sensitive offline browser cache |
| Redis/Celery/microservices | Add only for measured operational need |

## Domain invariants

1. A patient may have multiple episodes; a phone number does not identify a patient uniquely.
2. Clinical episode state, consent, contact suppression, and communication state are separate.
3. Active communication does not establish valid consent.
4. A reference ANC contact is not a confirmed appointment.
5. A reply or delivery report is not attendance or proof of reading.
6. Completion requires an authorized record; EDD expiry and silence cannot establish an outcome.
7. Transfers preserve episode identity without exposing all historical records.
8. Database uniqueness prevents duplicate logical work, not exactly-once telecom delivery.
9. Missing policy/content/authorization results in a visible hold or denial, never an improvised clinical default.
10. No patient data from real people is used in documentation, development, tests, or sandbox exercises.

## Non-functional requirements

| Area | Draft requirement | Verification |
| --- | --- | --- |
| Low bandwidth | Responsive paginated views, bounded payloads, minimal bundle sizes | Mobile workflow review; quantitative load target pending D14 |
| Security | Deny-by-default facility and assignment authorization | Negative API and database-access scenarios |
| Reliability | Durable work, atomic claims, unknown-submission reconciliation | Worker interruption and concurrency scenarios |
| Localization | Explicit English/Luganda versions; no silent fallback | Missing-language dispatch is held |
| Maintainability | Modular services and configurable approved policies | Changes do not require editing worker business logic |
| Observability | Heartbeat, lag, failures, unknown outcomes, budget and callback alerts | Operational dashboard and induced failures |
| Recovery | Encrypted backups and demonstrated restoration | Restore plus worker-reconciliation exercise |
| Cost | Budget reservation and actual segment calculation | Concurrent budget and Unicode scenarios |

## Pilot measurement definitions

- Count currently consented participants, not consent-event rows.
- Keep scheduled, accepted, delivered, failed, pending, and unknown message counts separate.
- Define attendance denominators and observation windows with facilities (D06).
- Attribute facility activity by its owning facility and period; deduplicate programme participant counts across transfers.
- Do not interpret SMS delivery as message comprehension or clinical improvement.
- Programme comparisons and small-cell disclosure rules require D13 approval.
