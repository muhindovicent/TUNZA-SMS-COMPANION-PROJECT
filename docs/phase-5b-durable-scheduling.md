# Phase 5B — Durable synthetic message scheduling

## Verified starting point and scope

Created `feature/phase-5b-durable-scheduling` from `main` at
`97138b5d2385d86070f967a0682b55af50f9bc64`. Both Phase 5A post-merge jobs were
verified successful **before implementation**:
https://github.com/muhindovicent/TUNZA-SMS-COMPANION-PROJECT/actions/runs/43775874788.

This milestone implements the A15 durable-business-key and A16 synthetic-capacity
technical workflows. **D05, D06, D07, D08, D14, D14a and D15 remain unresolved.**
Earlier D02/D03 eligibility/consent restrictions also remain in force. Every
candidate and preparation reports `dispatch_eligible:false`. PostgreSQL rejects
active occurrences and ready/submitting/accepted dispatch states, even in owner
DML. Phase 5A fixture approval is never clinical sign-off.

Planning uses explicitly entered **synthetic** weekly indexes and UTC windows.
These are test inputs, not calculated clinical gestational ages, approved coverage,
reminder offsets or sending windows. No automatic clinical week/offset derivation
is configured. Planning/revalidation services are invoked through authenticated,
assigned-care actions; repeated invocations and process restarts reuse PostgreSQL
identities. An invocation finding an expired window persists expiration rather
than producing late ready work.

There is no SMS submission, provider call, callback, delivery claim, worker
submission capability or deployment. No Phase 6 implementation is included.

## Durable records and identities

Migration `0016_durable_scheduling` adds nine FORCE-RLS tables:

| Table | Purpose |
| --- | --- |
| `message_occurrences` | Assigned synthetic candidate, immutable business identity/window, current holds/state/version and server check time |
| `message_dispatches` | One held preparation shell per occurrence; optional exact published fixture version/generation; no recipient or rendered body |
| `message_scheduling_events` | Immutable state/hold history, sequence and unique matching audit FK |
| `message_operations` | Identity-free operations projection, generated from audited scheduling events |
| `message_operation_events` | Sanitized immutable operations history |
| `messaging_policies` | Append-only global/facility pause snapshots and explicit synthetic codes; empty policy configuration only |
| `messaging_budgets` | Immutable explicit synthetic periods, caps and per-segment test costs in `SYN` units |
| `cost_reservations` | Append-only capacity simulations linked to audits and all applicable budgets; no dispatch/attempt link |
| `message_commands` | Actor/operation/key uniqueness, request hash, encrypted response/status and authorization scope |

Upgrade is additive: existing patient/consent/pregnancy/appointment/content rows
and histories are preserved. Downgrade removes these nine tables, their source
invalidation hooks and the new planning grant; it is destructive to Phase 5B
history and is not a D14/D14a retention procedure.

Weekly uniqueness is **episode + antenatal programme + gestational week**.
Reminder uniqueness is **appointment ID + synthetic reminder code**. PostgreSQL
partial unique indexes enforce both under concurrent execution. A unique occurrence
FK enforces one preparation shell. Language, template, dating revisions, facility,
planner invocation and idempotency key never create a second weekly identity.

An identical business-key plan with a new request key returns the current record
without another scheduling event or preparation. Conflicting immutable window or
template inputs return `409`. An identical command key replays its historical
encrypted response after current authorization; different input conflicts. Replay
does not reactivate work, re-reserve budget or append duplicate scheduling history.
No retention/expiry deletion rule is inferred from D14/D14a.

## Eligibility and invalidation

Each planning/recheck transaction reads current ongoing/communication state,
current responsibility and individual assignment, language preference, adult and
purpose-specific consent facts, appointment confirmation/window where relevant,
publication interval/digest-review/generation, applicable pause snapshots and
budget availability. There is no language fallback. Missing contact routing and
safe-contact policy are explicit holds because those approved records do not
exist in this baseline. Synthetic eligibility/consent facts are not promoted into
clinical approval.

Typical reasons include `D05_unconfigured`, `D06_unconfigured`,
`D07_unconfigured`, `D08_unconfigured`, `D14_unconfigured`, `D14a_unconfigured`,
`D15_unconfigured`, `consent_absent`, `consent_unapproved`,
`contact_safety_unconfigured`, `policy_missing`, `budget_missing`,
`content_unavailable` and `content_fixture_only`. Database checks allow only fixed
reason codes and require the D07 hold; arbitrary clinical text cannot be stored
in this field.

Candidates are created as `held`, `suppressed`, `cancelled` or `expired`, according
to current restrictions. Cancelled/expired candidates are terminal. A reminder
requires a current confirmed booking with confirmation provenance; its explicitly
entered fixture expiry must not extend past the booking. Replacement bookings
require their own confirmation and get their own reminder identity.

Database triggers invalidate existing unsent preparations in the source mutation
transaction:

- Withdrawal appends suppression; pregnancy completion cancels remaining work.
- Cancellation, rescheduling and other loss of booking confirmation cancel its
  reminders. Existing appointment/completion histories remain immutable.
- Closing responsibility during a controlled transfer cancels source-owned work.
  Source access is revoked; source consent/text/history is not copied to the
  destination. The same weekly business key cannot be duplicated there. No D09
  portability/replanning policy is invented.
- Content suspension/retirement clears selected preparation references and adds
  a content hold. Resumption requires explicit revalidation/fresh selection.
- Global/facility pause snapshots add the corresponding hold. Unpause never
  activates held candidates; revalidation retains every unresolved policy gate.

Invalidation appends its own sanitized service audit and history. A source-action
audit failure rolls back both the source mutation and scheduling invalidation.
Expired content/windows are also checked on every fresh planning/recheck invocation.
No scheduling snapshot constitutes a later submission authorization.

## Concurrency, audit and security

Lock order is command key → shared messaging-control advisory lock → pregnancy
row → existing patient/facility consent advisory lock → optional Phase 5A template
advisory lock → booking/occurrence rows. Content locks are taken before occurrence
locks. Withdrawal/content changes therefore cannot fall between eligibility
selection and durable candidate creation; lifecycle and appointment commands
continue to serialize through the pregnancy row. All locks are transaction-scoped.

The new `message.plan` permission is seeded **only to HCW**, requiring active
facility membership, actual facility permission and current individual assignment.
`message.read` remains assigned-care-only. Existing PA `message.ops` and
`messaging.control` grants cover redacted metadata and controls. No new FA grant
or clinical delegation is implied by draft D04 proposals.

Invokers establish actor/facility context and permission predicates verify actual
active account/bindings/membership, not claimed role/session flags. Ordinary runtime
is still non-owner, NOBYPASSRLS and without privileged role memberships. The separate
fixture-transfer principal retains its existing boundary and only mirrors the new
ordinary helper/DML grants. Care roles cannot read content bodies through scheduling:
a narrowly scoped metadata helper returns only a published fixture ID/generation
after checking assigned messaging access. New reference updates must match that
exact current fixture and language.

Operations roles cannot SELECT identity-bearing occurrence/history/preparation
rows. They read separate projections with opaque reference, facility, kind,
technical state, fixture due/expiry, version and sanitized holds. Patient IDs,
episode/appointment IDs, language/week, consent evidence, actor identity, contact
data and message bodies are absent. Patient-specific reasons map to
`eligibility_restricted`; policy/control reasons remain visible. Projection writes
are trigger-only; there are no application DML grants on those tables.

Restricted SECURITY DEFINER triggers have pinned search paths, qualified tables,
source checks/owner-only invalidation origin, and no PUBLIC/application execution
capability. Explicit trusted-owner maintenance policies support FORCE RLS even
when the migration owner lacks BYPASSRLS; runtime has no owner membership. Caller
RLS, fixed non-sendable states, current audit provenance and immutable histories
remain enforced. API audit and encrypted replay persistence commit atomically with
occurrence, preparation, projection, controls and reservations.

## Synthetic budgets and segment estimation

Global and facility pause/configuration checks combine restrictively. No policy
record implies approved sending: `config` must be `{}` and `synthetic_only:true`.
Budget periods, caps and unit costs are explicit inputs; currency is exactly `SYN`.
No production prices, caps or financial charging/release rules are supplied.

Capacity simulations generate a fixed labelled fixture from an encoding choice
and repeat count. GSM-7 basic characters count one septet, extension characters
two; single/multipart capacities are 160/153. Unicode counts UTF-16 code units with
70/67 capacities. GSM escape pairs and Unicode surrogate pairs cannot split at a
segment boundary. These are technical estimates, not verified provider billing.

All currently applicable global/facility budgets are reserved in deterministic
order inside one transaction. SQL guards independently check current authority,
pause/configuration, period, exact `segments × unit_cost`, audit provenance and
remaining cap. A shared control lock serializes reservations with pause/budget
changes. Deferred constraints require the complete budget set, preventing a raw SQL
client from reserving only one cap. Any failure rolls back every reservation.
Exhaustion/missing configuration returns explicit capacity hold reasons.

Reservations are **capacity tests only**, with no dispatch/attempt FK and no effect
on candidate eligibility. They remain append-only; no guessed timer-based release,
provider settlement, charge or refund behavior is implemented.

## API and React overview

All paths are under `/api/v1` and use existing staff sessions, CSRF and no-store.

| Endpoint | Contract |
| --- | --- |
| POST `/message-occurrences?facility_id=…` | `message.plan`; `{episode_id,kind,due_at,expires_at,gestational_week? ,appointment_id?,reminder_code?,template_id?}` → `201` new / `200` existing held candidate and preparation |
| POST `/message-occurrences/{id}/evaluate?facility_id=…` | `message.plan`; `{}` and occurrence If-Match → current revalidated record |
| GET `/message-occurrences?facility_id=…` | `message.read`; assigned care projections |
| GET `/message-occurrences?view=operations&facility_id?=…` | `message.ops`; separate redacted projections |
| GET `/message-occurrences/{id}/events` | Same explicit view/facility scope; immutable authorized history |
| GET/POST `/messaging/global/pause` or `/messaging/facilities/{id}/pause` | `messaging.control`; `{paused,code,config:{}}`; current scope ETag required on POST |
| GET/POST corresponding `/policies` paths | Same immutable policy-snapshot contract |
| GET/POST corresponding `/budgets` paths | `{period_start,period_end,cap,unit_cost,currency:"SYN",synthetic_only:true}`; explicit decimal strings |
| POST `/messaging/facilities/{id}/capacity-reservations` | `{fixture_encoding:"gsm7"|"gsm7_extension"|"unicode",units}` → reserved/held synthetic result, always dispatch-ineligible |

Writes require Idempotency-Key. Missing/stale evaluation ETags return `428`/`412`
without consuming the key; conflicts return `409`, invalid fields `422`, and
inaccessible records generic `404`. Lists use bounded `limit` (1–100, default 25),
`state` and signed actor/view/facility/filter-bound cursors. Failed audit returns
no payload. UTC windows are required; the UI converts labelled Kampala wall time.

`Messaging overview` navigation requires `message.read` or `message.ops`. Assigned
HCW can enter explicit fixture candidates and recheck them. The operations view
renders only safe fields, scheduling history, holds, pause state and synthetic
budget metadata. It offers synthetic control/capacity forms only with permission;
rate/cap/period fields have no defaults. Uncertain writes retain their key; a
confirmed write followed by failed refresh recovers through reads and blocks
further writes until refreshed. No send/approval-override control exists.

## Verification

Complete local suites passed with PostgreSQL 18, Python 3.15.1 and Node 24:

- **380 backend tests**, zero failures/skips, including all 340 Phase 5A tests.
- **115 frontend tests across 15 files**, including all 104 Phase 5A tests.
- Ruff lint/format, dependency checks, migration upgrade/current/check, full and
  populated migration round-trips, API/database readiness, TypeScript, ESLint,
  coverage, zero-vulnerability npm audit and production build passed.

New regressions cover duplicate/concurrent planning, global/facility concurrent
caps, Unicode/extension boundaries, incomplete raw SQL reservations, withdrawal
and completion races, rescheduling/confirmation, fixture transfers, content
suspension, language changes, expired candidates, pause gates, authorization/RLS,
immutable/no-activation SQL guards, audit/replay rollback, cursor scope and UI
privacy/recovery. The PR records independent Linux/Python 3.14 CI results.
