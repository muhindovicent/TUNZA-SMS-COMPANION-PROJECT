# Sequential implementation roadmap

> **DRAFT — Planning only. Synthetic patient data exclusively. No application implementation or deployment is included in this documentation change.**

## Planning assumptions

Assume two engineers, part-time QA, and available product, clinical, security, and privacy reviewers. The earlier 10–12 week estimate is optimistic for the fully specified scope. Plan **12–16 engineering weeks after design approval**, with external clinical/provider/legal waiting time tracked separately. This is a draft range, not a delivery promise. Re-estimate after D01 and workload discovery.

Each stage depends on the previous stage's exit. Security, auditing, and automated verification begin with the foundation rather than being deferred to final hardening. Clinical drafting/review can occur in parallel, but unapproved content cannot pass the messaging milestone.

## Milestones

| Stage | Draft effort | Deliverables | Exit evidence |
| --- | --- | --- | --- |
| 0. Requirements and contracts | 1 week | Reconcile D01; sign off ERD, permission defaults, consent/evidence, API, policy owners | FR-to-acceptance mapping complete; blocking policies resolved or explicitly held |
| 1. Identity and isolation | 1.5–2 weeks | Accounts, sessions/MFA/recovery, facilities, assignments, RLS, audit | A01–A03, A25 pass |
| 2. Registration and consent | 1.5–2 weeks | Synthetic adult profiles, shared contacts, eligibility, consent/withdrawal | A04–A06 pass |
| 3. Pregnancy and appointments | 1.5–2 weeks | Dating history, states, confirmed bookings, rescheduling, attendance | A07, A10–A12 pass |
| 4. Transfers and completion | 1 week | Controlled acceptance/handover, reassignment, completion | A08–A09 pass |
| 5. Content and durable scheduling | 1.5–2 weeks | Independent review, publication, occurrences, dispatch claims, budgets | A13–A16 pass |
| 6. Sandbox submission and callbacks | 1.5–2 weeks | Adapter, delivery mapping, retries, reconciliation, failure monitoring | A17–A19 pass; D11 resolved |
| 7. Inbound and follow-up | 1–1.5 weeks | STOP, ambiguous replies, assistance, sandbox escalation, action tracking | A20–A23 pass |
| 8. Reporting and technical acceptance | 1.5–2 weeks | Scoped dashboards, denominator reconciliation, restore, runbooks, end-to-end review | A24–A26 and regression scenarios pass |

These ranges sum to approximately 12–16 engineering weeks including stage 0. Capacity shortages or changes after source reconciliation require explicit re-planning, not removal of safeguards or recorded MVP requirements.

## Measurable acceptance catalogue

| ID | Scenario | Pass criterion |
| --- | --- | --- |
| A01 | Cross-facility/assignment access | Every protected list/detail/mutation rejects unauthorized scope; no count, identity, or existence leak in negative fixtures |
| A02 | Session and account lifecycle | Incomplete MFA cannot use privileged routes; logout/reset/deactivation revokes sessions; recovery tokens cannot be reused |
| A03 | Database isolation | Wrong/missing transaction context returns no unauthorized rows; pooled requests do not inherit prior scope; cross-facility child references fail |
| A04 | Eligibility | Unverified/ineligible synthetic registrations cannot become message-eligible; no invented adult-verification fallback |
| A05 | Consent evidence | Every grant links purpose, notice, method, actor and evidence; corrections preserve original event; missing evidence blocks dispatch |
| A06 | Staff withdrawal | After committed withdrawal, no not-yet-authorized submission passes eligibility; pending work is suppressed and history retained |
| A07 | Dating revision | Future work recalculates; history remains; previously submitted weekly occurrences are not recreated |
| A08 | Transfer | Concurrent acceptance yields one current facility; destination sees only permitted handover; source cannot access new destination activity |
| A09 | Completion | Authorized completion cancels future ANC work and weekly messages; no postnatal enrolment appears |
| A10 | Appointment eligibility | Scheduled/unconfirmed bookings create no reminder submissions; confirmed eligible booking does |
| A11 | Rescheduling | Old revision reminders cannot submit; replacement retains history and has distinct logical reminder keys |
| A12 | Attendance | Patient acknowledgement and delivery reports cannot mark attendance; missed status follows approved D06 policy |
| A13 | Independent approval | Self-approval fails; each language/version needs valid review; edits require a new version |
| A14 | Suspended content | Suspending an already-selected version prevents unsent submission; historical submitted text stays unchanged |
| A15 | Scheduler idempotency | Repeated and concurrent planning creates exactly one occurrence per defined business key and one dispatch per occurrence |
| A16 | Concurrent budget control | Combined reservations cannot exceed the configured synthetic cap; Unicode segment estimates are applied; pause blocks authorization |
| A17 | Worker interruption | Interruptions before intent, after intent, and after acceptance enter the documented recoverable/reconciliation state without blind resend |
| A18 | Provider events | Duplicate callbacks do not duplicate effects; delayed/out-of-order reports preserve valid state; unknown IDs are retained for reconciliation |
| A19 | Retry/expiry | Known transient rejection obeys configured bounded backoff; invalid recipient stops; expired reminders are not sent; unknown results are held |
| A20 | Shared-phone STOP | Endpoint suppresses all future eligible sends to that endpoint; only reliably identified participant withdrawal is recorded |
| A21 | Ambiguous inbound | No automatic clinical/consent update occurs when multiple candidates remain; ordinary users cannot see other-facility candidates |
| A22 | Appointment replies | A safely identified reply links its booking; acknowledgement remains separate from booking confirmation and attendance |
| A23 | Sandbox escalation | Reviewed synthetic phrases route to restricted cases; acknowledgement and disposition persist; live escalation remains disabled |
| A24 | Dashboard reconciliation | Fixture-derived counts match records by defined status/period; transfer does not double-count programme participants |
| A25 | Audit | Sensitive reads/writes/exports have scoped audit events; failed mutation audit rolls back; failed sensitive-read audit releases no PHI |
| A26 | Restore | A restored synthetic database retains consent/audit/suppression; outgoing work stays paused until uncertain submissions reconcile |

## Gate model

- **A — Design:** accept source reconciliation and draft contracts; record remaining policy blockers.
- **B — Technical:** accept all essential MVP scenarios using synthetic data and sandbox only.
- **C — Clinical/privacy:** approved content, consent, agreements, processing responsibilities.
- **D — Operations:** trained staff, verified support, withdrawal channels, incident process, budget and recovery plan.
- **E — Controlled pilot:** explicit authorization of limited live operation after A–D.
- **F — Expansion:** evidence-based review of facilities, languages, channels, or postnatal scope.

Technical completion does not open live messaging. Two-way live SMS and escalation require their own readiness decisions even if a one-way pilot is approved.

## Documentation acceptance and publication

Before this draft is committed: verify relative links, entity/permission/API/state consistency, whitespace, and absence of credentials or sensitive information. Stage only the intended README and draft documentation. No application tests or deployment are part of this documentation-only change. See [decision register](unresolved-decisions.md) for unresolved items rather than fabricated policy values.
