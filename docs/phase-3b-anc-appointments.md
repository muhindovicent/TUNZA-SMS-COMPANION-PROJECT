# Phase 3B — Synthetic ANC appointment management

This milestone adds staff-entered bookings to the selected-patient pregnancy
workflow. **D06 remains unresolved.** There is no generated clinical schedule,
gestational target, automatic missed-visit decision, reminder offset or SMS
dispatch. D02, D03, D05, D14 and D14a also remain unresolved.

## Staff workflow

1. An assigned HCW registers or searches for a synthetic patient in **Patient
   registry**, then selects **Manage pregnancy episodes**.
2. The **ANC appointments** panel follows the selected pregnancy and its verified
   facility responsibility. There is no independent patient-ID or facility entry.
3. Enter a booking time in **Africa/Kampala**. Saving creates a `scheduled`,
   unconfirmed booking; confirmation is a separate explicit staff action.
4. Reschedule or cancel a scheduled/confirmed booking with a reason. Rescheduling
   creates a new ID, initially unconfirmed, and preserves the old time, confirmation
   provenance, booking and encrypted event history.
5. From a confirmed booking, staff can explicitly record `attended` or `missed`,
   with a staff-entered effective time and optional reason. No elapsed-time rule
   or reply infers either outcome. These are synthetic staff-recorded facts, not
   approved D06 missed-visit criteria.
6. Read persisted booking history and follow predecessor/replacement references.
   Status filtering and **Load more appointments** use scoped cursor pagination.

The existing HCW permissions control mutations: `appointment.schedule`,
`appointment.confirm`, `appointment.reschedule`, `appointment.cancel` and
`appointment.attend`. Assigned VHTs retain `appointment.read`; administrative
roles do not acquire clinical appointment access. HCWs registering synthetic
patients receive their own individual assignment through the existing registry.

## API contract

Paths are under `/api/v1`; `facility_id` is required on every request. Existing
server-side sessions, CSRF, current membership/bindings, individual assignments,
no-store responses and atomic audit controls apply. An absent/inaccessible object
returns a generic `404`; a missing endpoint permission returns `403`.

| Endpoint | Input / behavior |
| --- | --- |
| POST `/appointments?facility_id=…` | `{episode_facility_assignment_id,starts_at,reference_contact_id?}`; creates scheduled booking, `201` + Location + ETag. Only null/omitted `reference_contact_id` is supported while reference schedules remain unconfigured. |
| GET `/appointments?facility_id=…&episode_id=…` | Assigned episode bookings; optional `status`, timezone-aware `from` (inclusive), `to` (exclusive), `limit` (1–100, default 25) and signed `cursor`. Returns `data:[]` and `page.next_cursor`. |
| GET `/appointments/{id}?facility_id=…` | Current authorized booking, version/ETag, confirmation, attendance, replacement references and reminder hold. |
| POST `/appointments/{id}/confirm?facility_id=…` | `{reason?}`; scheduled → confirmed, `200`. Server records confirming actor/time. |
| POST `/appointments/{id}/reschedule?facility_id=…` | `{starts_at,reason}`; original → rescheduled; returns replacement scheduled booking, `201`. |
| POST `/appointments/{id}/cancel?facility_id=…` | `{reason}`; scheduled/confirmed → cancelled, `200`. |
| POST `/appointments/{id}/attendance?facility_id=…` | `{status,effective_at,reason?}`; confirmed → explicitly recorded attended/missed, `200`. |
| GET `/appointments/{id}/events?facility_id=…` | Authorized chronological event history, including decrypted event reasons and recording actor/time. |

All POSTs require `Idempotency-Key` and a quoted `If-Match` version. Scheduling
uses the selected **pregnancy** version; actions use the **appointment** version.
Missing If-Match returns `428`, a stale version `412`, and a prohibited transition
or duplicate active slot `409`. Unknown write fields and invalid timestamps return
`422`. Scheduling and replacement creation advance the pregnancy version; all
booking state changes increment the booking version and append its next event.

Valid transitions are:

```text
scheduled -> confirmed | cancelled | rescheduled
confirmed -> attended | missed | cancelled | rescheduled
attended, missed, cancelled, rescheduled -> terminal
```

Terminal records are not silently reopened or overwritten. Attendance corrections
requiring separate clinical delegation remain outside this milestone. A reschedule
must change the time, preserve the same episode/facility, and require fresh
confirmation of the replacement. A patient can have multiple distinct staff-entered
bookings; an active scheduled/confirmed duplicate at the same responsibility/time
is prevented by a partial unique index, without inventing clinical spacing rules.

## Time handling

`starts_at` and attendance `effective_at` require timezone-aware RFC 3339 instants.
Offset forms and `Z` normalize to UTC; API timestamps use `Z`, and PostgreSQL
`timestamptz` retains the absolute instant. Naive timestamps are rejected.

The React datetime-local control explicitly means **Africa/Kampala**, independently
of the browser's timezone. IANA-zone conversion sends UTC to the API; displayed
booking and event times explicitly use Africa/Kampala. No clinical time is
pre-filled or calculated from dating history.

## Durability, isolation and concurrency

Migration `0010_anc_appointments` is additive. It creates `appointments`,
`appointment_events` and `appointment_idempotency`; existing registry, consent,
dating and assignment rows are unchanged. Populated upgrade/downgrade/re-upgrade
tests verify Phase 3A history survives. Downgrading below 0010 discards Phase 3B
booking tables, so it is not a booking-data retention procedure.

- Composite FKs bind patient, episode, facility and responsibility; a predecessor
  must belong to the same episode/patient/facility and can have only one replacement.
- Database transition guards preserve original booking times and provenance,
  require scheduled/unconfirmed creation, enforce version increments and prohibit
  illegal transitions. Deferred checks require matching append-only history and
  atomic replacement of a rescheduled booking.
- FORCE RLS checks current facility responsibility and exact individual pregnancy
  assignment. Reads can resolve a booking only through an authorized actor/facility;
  established patient context additionally restricts rows. Writes and encrypted
  child/replay records require the verified patient context. Missing/expired
  assignment and cross-facility or same-facility unassigned access fail closed.
- Events and completed command records are immutable even through owner UPDATE/
  DELETE attempts. The runtime role cannot delete bookings or rewrite event or
  replay records, and appointment UPDATE grants cover only transition fields.
- A completed command is unique on `(actor_id,operation,key)` and binds facility,
  patient, episode, target and canonical request fingerprint. UTC-equivalent input
  timestamps canonicalize identically. Conflicting reuse returns `409`, including
  hidden cross-patient collisions, without returning hidden IDs.
- Command-key advisory locks, pregnancy row locks and booking row locks use a
  consistent order. Identical concurrent commands replay one committed result;
  competing writes at the same version produce one success and one `412`.
- Replay is checked after current authorization and before stale-version rejection.
  The encrypted response snapshot/status is persisted atomically; replay can return
  that historical mutation response after later state changes. The UI reloads
  current records after successful writes. D14/D14a retention/expiry is not invented.
- Reasons/event details and response snapshots use the existing authenticated
  encryption/key-version infrastructure with distinct purposes. Audit entries do
  not copy raw reasons. Writes, events, versions, replacement, replay record and
  audit commit together; failure rolls back everything. Responses are materialized
  before commit clears transaction-local RLS context.

## Reminder boundary

Every projection returns `reminder_eligible: false` in this phase. The hold reason
distinguishes scheduled, cancelled, superseded, attended/missed, completed-episode
and past-time bookings. Even a confirmed future current booking is held as
`D06_unconfigured`. No stored historical replay response can authorize reminders.
There is no dispatch, occurrence creation, worker, provider integration or approved
reminder offset in this milestone.

## Verification

Local verification uses locked dependencies, PostgreSQL 18 on a dedicated loopback
test database, Python 3.15.1 and Node 24:

- Backend/PostgreSQL/security/migration suite: **209 passed**, no failures or skips.
- Frontend suite: **71 passed across 11 files**; TypeScript, ESLint and production
  build passed.
- Ruff lint/format and `pip check` passed. `npm ci` and `npm audit` passed with
  zero vulnerabilities.

Coverage includes valid/invalid transitions, initial unconfirmed state, manual
attendance, replacement confirmation/history, equivalent UTC input, durable
replay after later mutations, conflicting keys, duplicate active slots, signed
cursor scope, same-facility/cross-facility denial, revoked assignment, concurrent
scheduling/actions/replacement, direct RLS denial, immutable database history,
encrypted event/replay content, audit rollback and populated migration round-trips.
The PR records the independent Linux/Python 3.14/PostgreSQL 18 GitHub Actions result.
