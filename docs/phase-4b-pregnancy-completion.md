# Phase 4B — Synthetic pregnancy completion and lifecycle closure

## Scope and policy boundaries

This milestone implements A09 and the documented `CompletionInput` technical
workflow on top of verified merge `89b3d74`. The merged baseline's frontend and
backend jobs were verified green before work:
https://github.com/muhindovicent/TUNZA-SMS-COMPANION-PROJECT/actions/runs/43761375730.

All records are synthetic. **D02, D03, D04, D05, D06, D09, D14 and D14a remain
unresolved.** Completion records accept `approved_outcome_fields: {}` exclusively;
there are no invented clinical outcomes, newborn fields or gestational/date-range
rules. A staff-entered completion date is a valid ISO date, not a clinical outcome
or authorization to create a new programme. The reason is an encrypted operational
rationale under the existing contract. No postnatal enrolment, SMS dispatch or
deployment is introduced.

## API and authorization

All paths below are under `/api/v1` and require existing staff authentication,
current permission/membership, an exact active individual episode assignment,
verified `facility_id` context and PostgreSQL RLS. Source/destination ownership
after a transfer remains enforced; origin-facility provenance is not authorization.

| Endpoint | Contract |
| --- | --- |
| POST `/pregnancy-episodes/{id}/completion?facility_id=…` | `pregnancy.complete`; `{completed_on,approved_outcome_fields:{},reason,supersedes_id?}` → `201` immutable record/snapshot and parent-version ETag |
| GET `/pregnancy-episodes/{id}/completion?facility_id=…` | `pregnancy.complete`; authorized history, newest sequence first, and current parent ETag |
| GET `/pregnancy-episodes/{id}?facility_id=…` | Existing `pregnancy.read`; adds opaque `completion_record_id`, `messaging_eligible:false` and a completed/policy hold reason without disclosing completion reasons/outcomes to read-only roles |

POST requires a printable `Idempotency-Key` and a quoted pregnancy version in
`If-Match`. Missing version returns `428`, stale version `412`, and invalid state,
current-predecessor or key conflicts `409`. Missing/inaccessible patient/episode
or facility scope returns generic `404`; missing endpoint permission returns
`403`. Unknown/server-controlled fields, malformed dates/IDs and non-empty or
non-object outcome fields return `422` without echoing submitted clinical content.
All clinical responses retain no-store handling.

### Initial completion

The first record requires an **ongoing** episode with no current completion
record. In one transaction it:

1. Locks and freshly reauthorizes the pregnancy after waiting for concurrent work.
2. Captures one database-server closure instant, independent of `completed_on`.
3. Inserts an immutable completion record with encrypted empty outcomes,
   encrypted reason and encrypted durable response snapshot.
4. Locks all future scheduled/confirmed appointments for the **current facility
   responsibility**, changes them through their valid cancellation transition,
   increments booking versions, and appends events linked by FK to the completion.
   Each cancellation has an audit entry using the completion authority.
5. Sets lifecycle to `completed`, sets communication state to `completed` unless
   it was already `withdrawn`, updates the same-episode completion pointer and
   advances the pregnancy version once.
6. Commits the completion, booking changes/events, parent state, idempotency data
   and audit together. Any failure rolls back every change.

“Future” means `starts_at >=` the original server closure instant. It is not
calculated from a backdated or future staff-entered `completed_on`, and it does
not invent D06 clinical windows. Past bookings and attended, missed, cancelled or
rescheduled records are unchanged. The current replacement of a rescheduled
booking is cancelled if it is a future scheduled/confirmed booking; its original
rescheduled predecessor and history remain unchanged.

Appointments owned by a former facility/responsibility after transfer are
historical ownership records. They are preserved, not rewritten across RLS. Their
closed responsibility already excludes them from current care/reminders, and
completion adds a completed-episode hold. Tests prove destination completion
cancels destination-owned future work without exposing or rewriting source history.

### Corrections

A correction is the explicit exception to the ongoing-first-completion rule:
it requires a **completed** episode, the current version, active completion
authority and `supersedes_id` equal to the current completion pointer. It appends
a new record and advances the parent pointer/version. It never updates/deletes
earlier records, reopens the episode, changes the original closure instant,
reverses withdrawal or reruns historical appointment cancellation. The correction
has cancellation count zero and a same-episode/facility predecessor link.

Only the latest record may be superseded. Sequence and successor uniqueness,
same-scope foreign keys and parent consistency prevent forks, cross-patient links
and arbitrary current-pointer rewrites. Identical completed-request replay is
checked after current authorization but before stale-version/state checks, so
retrying a lost successful response does not create another revision or event.

## Lifecycle closure and races

- Appointment creation and confirmation/rescheduling require an ongoing pregnancy;
  SQL guards also lock/check the parent to prevent direct-SQL race bypass.
- Dating INSERTs lock/check the ongoing parent; replay of an existing immutable
  command creates no new dating revision.
- Existing transfer acceptance locks the same pregnancy and rejects completed
  episodes. If acceptance wins first, a source completion reauthorizes in a new
  statement and fails because source responsibility was revoked. If completion
  wins, acceptance fails without moving responsibility. Existing pending transfer
  metadata is retained; no new D09 cancellation/notification policy is invented.
- Completion and appointment commands use consistent key → pregnancy → child
  locking. If a new booking changes the pregnancy first, stale completion is
  rejected and must refresh. Confirmation/cancellation races cannot leave an
  active future booking after successful completion.
- Current communication state and durable lifecycle stay completed/withdrawn;
  pointer-backed episodes cannot be reopened or have their pointer cleared.
- The pregnancy projection reports `messaging_eligible:false` and
  `messaging_hold_reason:episode_completed`; existing appointment reminders and
  consent/messaging helpers remain fail-closed. There is no message queue/weekly
  occurrence or dispatch subsystem to mutate in this milestone. No new messaging
  work or programme enrolment is created by completion or correction.

Durable idempotency binds actor, named operation, patient, episode, facility,
canonical fields and predecessor. Actor/key uniqueness survives process restarts;
hidden cross-patient collisions return a generic conflict and cannot replay the
other patient's snapshot. D14/D14a expiry/retention is not guessed. Replays are
historical command responses, not authority to reactivate current work.

## Database migration and security

`0013_pregnancy_completion` is additive on upgrade:

- Adds `pregnancy_completion_records` and a nullable same-episode FK pointer on
  `pregnancy_episodes`; outcomes/reasons/replay are authenticated ciphertext with
  separate encryption purposes and key version.
- Adds optional completion linkage to existing append-only appointment events;
  only cancellation events may reference a completion.
- Uses FORCE RLS plus an invoker-only permission predicate checking actual active
  facility grants, synthetic profile and individual assignment. Full history and
  reasons require `pregnancy.complete`, not merely read access to the pregnancy.
- Restricts record privileges to SELECT/INSERT, enforces append-only history even
  for owner UPDATE/DELETE, and validates initial/correction chains under the parent
  lock. Deferred constraints check parent/pointer/version consistency, matching
  cancellation events/count and absence of future current active bookings.
- Adds narrow lifecycle/communication/pointer UPDATE columns to the ordinary
  application role. Transition guards require authorized linked history; the
  original RLS, non-owner/NO-BYPASSRLS/no-membership checks and the isolated fixture
  transfer database-principal boundary are retained. There is **no new SECURITY
  DEFINER capability**. The separate fixture provisioner mirrors the new ordinary
  DML/helper grants without restoring any standard-runtime transfer execution.

Existing legacy episodes that were already `completed` without recorded closure
facts remain unchanged with a null pointer and empty history. No clinical date,
actor or outcome is fabricated. Trusted migration/owner fixtures can still
represent those legacy states; ordinary application logins cannot fabricate a
new closure without linked history. The UI explains unrecorded legacy history
and does not offer a fabricated correction. As before, database owners are outside
the ordinary application-credential threat model.

Downgrade removes Phase 4B records/linkage/guards and revokes the extra lifecycle
UPDATE grants from normal and provisioned fixture application logins. It preserves
older tables/rows but is not a completion-history retention procedure. Retention
and any approved legacy-data reconciliation require separate policy decisions.

## React workflow

The selected pregnancy shows a permission-aware **Pregnancy completion** panel.
Only completion-authorized staff fetch full history and reasons. The date and
reason are reviewed in a separate, explicit confirmation step; the preview freezes
the parent version used for POST. Staff may go back without submitting. The UI
preserves an idempotency key across uncertain retries and reports stale versions
without pretending completion succeeded.

After success, parent lifecycle/pointer, selector labels, completion history and
appointment states refresh. Dating, new booking and source transfer initiation
forms are hidden for the completed episode. Corrections require a separate action
and confirmation, keep both revisions visible and explain that closure/history
remain unchanged. No outcome form or postnatal enrolment control is present.

## Verification

On locked dependencies with dedicated loopback PostgreSQL 18, Python 3.15.1 and
Node 24, the complete suites passed:

- **296 backend tests**, zero failures/skips, including all prior 264 tests.
- **90 frontend tests across 13 files**, including all prior 81 tests; TypeScript,
  ESLint, coverage and production build passed.
- Ruff lint/format, migration round-trips and schema consistency passed.

New regressions cover closure/corrections, all terminal appointment states,
historical preservation, immutable ciphertext history, replay/collisions,
authorization/RLS, direct lifecycle bypass denial, concurrent booking/confirmation/
correction/transfer operations, source reauthorization after transfer, and complete
or mid-batch audit rollback. The PR records independent GitHub Actions verification
on Linux/Python 3.14/PostgreSQL 18 before delivery.
