# Phase 3A — Synthetic pregnancy episodes and dating history

This milestone records staff-entered synthetic dating information. **D05 remains
unresolved**: there is no approved medical-method allowlist, gestational calculation,
clinical EDD range, automatic ANC scheduling or messaging activation. D02, D03,
D14 and D14a also remain unresolved in the decision register.

## Staff workflow

1. Register or search for an assigned synthetic patient in **Patient registry**.
2. Select **Manage pregnancy episodes** on that patient's result. The selected
   patient ID and facility travel together; the pregnancy screen has no arbitrary
   patient-ID entry or independent facility selector.
3. Load the episode already created by patient registration. Registration still
   creates one paused, ongoing episode and the registering staff member's assignment.
4. An assigned HCW may record an EDD, recorded method and reason. EDD is a
   date-only `YYYY-MM-DD` value (`<input type="date">` and PostgreSQL `date`).
5. A correction appends another revision, links its predecessor and increments
   the parent episode version. Reloaded history retains the earlier values,
   recording actor, timestamp, reason and sequence.

VHTs retain episode creation/read permissions; the existing HCW permission
`pregnancy.date` controls dating writes. Facility administrators and platform
administrators do not acquire clinical access from this phase.

## Implemented API

All paths below are under `/api/v1`. Reads and writes require the current
facility permission, active membership and individual patient/episode assignment.
An unknown or inaccessible target returns a generic `404`. Existing staff-session,
CSRF, no-store response and audit controls apply.

| Method/path | Permission | Behavior |
| --- | --- | --- |
| GET `/pregnancy-episodes?patient_id=…&facility_id=…` | `pregnancy.read` | Assigned episodes for the selected patient; includes the initial registration episode |
| POST `/pregnancy-episodes` | `pregnancy.create` | `{patient_id,facility_id}` creates a **subsequent** paused episode and responsibility/own participant assignments atomically; `201` with Location and ETag |
| GET `/pregnancy-episodes/{id}?facility_id=…` | `pregnancy.read` | Assigned episode with current dating summary and ETag |
| GET `/pregnancy-episodes/{id}/dating-revisions?facility_id=…` | `pregnancy.read` | Persisted append-only history, newest sequence first |
| POST `/pregnancy-episodes/{id}/dating-revisions?facility_id=…` | `pregnancy.date` | `{basis,estimated_due_date,reason}`; `201` with revision and parent version/ETag |

Both POSTs require `Idempotency-Key`. Dating creation also requires a quoted
parent version in `If-Match`: missing is `428`, stale is `412`. A completed replay
is checked before the stale-version check, **after current authorization**.
The fingerprint binds operation, actor, patient/episode, facility and canonical
request values. Facility/key uniqueness is enforced in PostgreSQL. Conflicting
reuse returns `409`, including keys hidden by another patient's RLS scope; hidden
row IDs and payloads are never returned.

There can be only **one ongoing episode per patient**, enforced by a partial
unique index across facilities. A different creation request while an ongoing
episode exists returns `409`; clients retrieve that episode through GET.
This phase does not introduce a completion endpoint. Tests prepare subsequent
episode scenarios through a separate privileged synthetic fixture connection.

Dating writes lock the episode row, re-read its version, allocate a persisted
sequence and append a revision in one transaction. Simultaneous identical keys
return the same revision ID; competing different keys at the same version yield
one success and one stale-write rejection. Episode creation uses a patient-wide
transaction advisory lock plus database uniqueness. Response data is materialized
before commit, avoiding ORM expiration queries after transaction-local RLS resets.

## Migration `0009_pregnancy_dating`

The unverified feature-branch migration has been corrected in place before release:

- Adds episode origin `facility_id`, nullable legacy `created_by`, optional creation
  idempotency key and request fingerprint. Patient registration supplies the new
  fields without replacing its existing encrypted registration-replay mechanism.
- Backfills existing episodes from their earliest recorded facility assignment,
  including completed episodes with closed assignments. It preserves IDs,
  lifecycle, versions, profile ciphertext and prior records. Historical actors or
  idempotency keys are not invented.
- Fails transactionally, with a diagnostic, if legacy episodes have no facility
  assignment or a patient has multiple ongoing episodes. It does not delete,
  silently merge or invent completion of conflicting records. Such a dataset
  requires explicit correction before retrying the upgrade.
- Creates `pregnancy_dating_revisions`, including non-null idempotency key/hash,
  `parent_version`, sequence uniqueness and same-episode predecessor FK.
- Adds an immutable-history trigger rejecting UPDATE/DELETE, even via the owner
  connection. Runtime privileges on revisions are SELECT/INSERT only; episode
  UPDATE is limited to `version` and `updated_at`.
- Preserves FORCE RLS on episodes and adds FORCE RLS to revisions. Episode reads
  and dating operations verify the actor's current exact participant assignment,
  membership and facility responsibility. Revision access additionally requires
  transaction-local patient/facility context. A profile at the same facility alone
  does not authorize care.
- Episode INSERT RLS permits the initial profile/episode registration in one
  transaction; subsequent episode insertion requires an existing care assignment.
  Direct unassigned episode and dating INSERTs are rejected by the runtime role.
- Uses statement-time validity checks in the assignment predicate, so a transaction
  waiting for concurrent episode creation can see the newly committed assignment
  without relaxing assignment checks.

Origin facility is provenance; the live responsibility assignment determines
access. Downgrade to `0008_evidence_idempotency` restores the earlier episode RLS
policy and removes Phase 3A dating tables/columns, so normal downgrade semantics
discard the new dating feature's data. There is no deployment in this work.

## Verification

With locked dependencies on Windows, Python 3.15.1, Node 24.22.1 and an isolated,
loopback PostgreSQL 18 cluster:

- Backend: **159 passed, 0 failed, 0 skipped** (`pytest --cov=tunza`); 84% overall
  statement coverage. Includes authentication, consent/evidence, registration,
  migration upgrade/downgrade, runtime-role checks, assignment/RLS denial,
  correction history, concurrent replay/stale writes and atomic audit rollback.
- Frontend: **51 passed in 9 files**, TypeScript, ESLint, coverage and production
  build passed. Includes registry selection, existing-episode loading, date-only
  EDD, persisted corrections, permissions, retry keys and actual request headers.
- `pip check`, Ruff lint/format, `npm ci` and `npm audit` passed; npm reported
  zero vulnerabilities.
- Populated migration tests cover both valid legacy history and non-destructive
  rejection of duplicate/orphan legacy episodes, with downgrade/re-upgrade and
  model drift checks.

The PR's GitHub Actions run verifies Linux/Python 3.14, Node 24 and PostgreSQL 18;
its live result is reported on the PR rather than inferred from local success.
