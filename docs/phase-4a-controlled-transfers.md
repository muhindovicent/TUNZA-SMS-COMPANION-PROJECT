# Phase 4A — Controlled synthetic facility transfers

## Verified starting point

Before implementation, both jobs of the post-merge CI for `830bd47` were verified
successful: **209 backend/PostgreSQL tests** and **71 frontend tests**.
Run: https://github.com/muhindovicent/TUNZA-SMS-COMPANION-PROJECT/actions/runs/43735251970.

## Policy boundary: D04 and D09 remain unresolved

This is an explicitly enabled **fixture-only** demonstration. It does not approve
facility agreements, transfer authority, participant agreement procedures,
clinical handover fields, consent portability or historical access rules.
Existing HCW/FA permission seeds are exercised as synthetic technical roles only;
no new role grants or approved transfer-policy defaults are introduced.

- `SYNTHETIC_TRANSFERS_ENABLED=false` by default. Authenticated capabilities report
  `live_enabled:false`; disabled mutations and metadata reads return `403
  policy_unconfigured` before accessing transfer data.
- A local synthetic demonstration requires both an explicitly enabled flag and
  a **separately authenticated fixture database login**, provisioned as described
  below. An environment flag or caller-set PostgreSQL variable alone is insufficient.
  The UI has no policy-enabling switch.
- Production configuration rejects enabling this flag or using fixture database
  credentials, even with the flag disabled. The standard `tunza_runtime` database
  role has no transfer-table access or EXECUTE privilege on the fixture entry points.
  Live transfer operations stay disabled.
- Only existing synthetic profiles and active facilities whose technical codes
  begin with `SYN` or `TST` are supported. This prefix is a fixture identifier,
  not a clinical/facility approval.
- Handover accepts exactly `{"synthetic":true,"fixture_code":"SYN-…"}`. The code
  must match `SYN-[A-Z0-9][A-Z0-9-]{1,59}`. There are no clinical free-text,
  demographic, contact, dating, evidence-upload or chart-copy fields.
- Reasons are bounded synthetic codes using the same pattern. Non-null
  `agreement_evidence_id` is rejected: this phase does not invent participant
  agreement sufficiency or treat consent evidence as transfer approval.

D02, D03, D05, D06, D14 and D14a also remain unresolved. There is no Phase 4B,
completion workflow, SMS, provider integration or deployment in this work.

## Staff workflow

### Source care workflow

An individually assigned synthetic HCW opens a patient through **Patient registry**,
selects **Manage pregnancy episodes**, then uses **Controlled facility transfers**.
The patient, episode and source responsibility come from the authorized selection;
the form cannot replace them with arbitrary patient IDs. A bounded fixture
destination directory supplies active synthetic facility metadata only.

Initiation requires `transfer.initiate`, current facility membership and exact
individual care assignment, plus the pregnancy `If-Match` version. It records a
pending request and encrypted immutable fixture handover, advances the pregnancy
version and audits the transaction. There can be only one pending request for a
source responsibility.

Pending initiation does not invent D09 messaging hold/resumption policy or move
responsibility. Current source care remains scoped to its existing responsibility.
Existing messaging and reminder holds remain in force.

### Destination administrative workflow

The existing destination FA fixture role uses **Transfer inbox**. It sees only
bounded request metadata and the synthetic handover code, not a patient chart or
complete source records. Acceptance requires explicit selection of an existing,
active destination HCW membership, with an active HCW binding and actual
`patient.read`/`pregnancy.read` grants. A synthetic destination language is also
entered explicitly; no source preference or consent is inferred.

Acceptance is one narrowly scoped transaction:

1. Revalidate the exact destination, current actor membership/binding/permission,
   synthetic mode, pending request/version and ongoing episode.
2. Lock pregnancy, transfer and source responsibility in consistent order.
3. Close current source responsibility and active source participant assignments
   at one database-generated cutover instant.
4. Create destination responsibility and the explicitly selected HCW's individual
   assignment at that **same instant**, retaining the episode and patient identity.
5. If needed, create a destination-owned synthetic profile with encrypted empty
   demographics and a newly generated local reference. Create a preference using
   the explicit fixture language only if one does not already exist. Existing
   destination profiles/preferences are preserved.
6. Preserve `withdrawn` communication state; otherwise set it to `paused`.
   No facility messaging control is enabled.
7. Record a destination-owned immutable decision, terminal request state/version,
   parent version, encrypted command response and audit, then commit together.

No consent, clinical dating, contact, evidence or source appointment rows are
copied. Source appointments/events retain their original values and owning
responsibility. Access to them fails after that responsibility closes; destination
staff do not inherit the source appointment or dating history. Destination care
can enter its own later records under the new assignment. Source staff cannot
read that activity (A08).

The accepted destination reference is returned only in the destination-owned
receipt. Source receipts exclude destination receiver, new responsibility and
local reference, both through API projection and separate decision-table RLS.
The original requester may retain bounded request-status receipts without gaining
clinical access after cutover.

### Rejection and cancellation

Destination FA fixtures can reject a pending request. An assigned source HCW or
the existing source FA administrative fixture role may cancel a pending request.
The inbox's outgoing view supports source administrative cancellations without
patient chart access. Both outcomes preserve source responsibility, profiles,
appointments, dating and consent history. Neither activates communication.

All terminal decisions are immutable; a rejected/cancelled request does not
silently reopen. A new request requires a new command and current parent version.
Handover retrieval is limited to pending/accepted requests; closed rejection or
cancellation receipts do not reopen handover access.

## Implemented API

All paths are under `/api/v1`. Except capabilities, reads and writes require a
valid `facility_id` query context and enabled fixture mode. Existing staff
sessions, CSRF, no-store responses, active facility grants and auditing apply.

| Endpoint | Contract |
| --- | --- |
| GET `/transfers/capabilities` | Authenticated mode/policy indicators only; always `live_enabled:false` |
| GET `/transfers/destinations?facility_id=…` | `transfer.initiate`; bounded active fixture-facility directory |
| POST `/transfers?facility_id=…` | `{source_assignment_id,destination_facility_id,handover,agreement_evidence_id?:null}` → pending request, `201` |
| GET `/transfers?facility_id=…&direction=incoming\|outgoing` | Bounded scoped requests; optional `state` (default pending, or all), `episode_id`, `limit` (1–100, default 25) and signed `cursor` |
| GET `/transfers/{id}?facility_id=…` | Authorized bounded request/status and the acting facility's permitted receipt, with ETag |
| GET `/transfers/{id}/handover?facility_id=…` | Only the encrypted fixture-code subset; no clinical chart |
| GET `/transfers/{id}/receivers?facility_id=…` | Destination-only pending FA view of eligible membership IDs/display names |
| POST `/transfers/{id}/decision?facility_id=…` | `transfer.accept`; `{decision:"accept"\|"reject",reason}`; accept additionally requires `receiver_membership_id` and `synthetic_language:"en"\|"lg"` |
| POST `/transfers/{id}/cancel?facility_id=…` | Source `transfer.cancel`; `{reason}` → cancelled request |

Every POST requires `Idempotency-Key`. Initiation requires the quoted pregnancy
version in `If-Match`; decision/cancellation requires the quoted transfer version.
Missing versions return `428`, stale versions `412`, and state/pending/duplicate
conflicts `409`. Inaccessible requests and wrong-side actions return generic
`404`; the endpoint permission decorator can return `403` for missing permission.
Invalid/unapproved fields return `422` without echoing clinical content.

## PostgreSQL and transaction controls

Migration `0011_controlled_transfers` adds:

- `facility_transfers`: scope-aligned immutable identity and one-pending-source index.
- `transfer_handover_records`: purpose-bound encrypted fixture data, append-only.
- `transfer_decisions`: immutable facility-owned terminal decisions, with accepted
  destination assignment/receiver/reference kept out of shared source receipts.
- `transfer_idempotency`: immutable encrypted completed responses, unique on
  `(actor_id,operation,key)` and bound to facility, request target and fingerprint.
- A GiST exclusion constraint over episode responsibility `[valid_from,valid_to)`
  intervals, in addition to the existing one-open-responsibility index. It uses
  `btree_gist` to reject historical as well as current overlaps. Existing overlap
  errors abort migration; no prior responsibility/history is deleted or repaired
  silently. Downgrade removes this constraint and the new transfer tables, retaining
  the extension because it may be shared by other database objects.

FORCE RLS covers all new tables. Destination and source side checks use actual
current database memberships, bindings and permissions. Decisions are owned by
the destination for accept/reject and by the source for cancellation. A source
care role cannot directly SELECT a destination decision. The runtime role receives
no general responsibility/profile UPDATE or cross-facility chart access.

### Security correction: migration `0012_fixture_execution_boundary`

The original 0011 grant allowed the standard database service login to call the
privileged function after setting its own session variables. Flask's production
flag could not secure that direct-SQL path. Migration 0012 removes this capability:

- All non-owner EXECUTE grants are stripped from the old implementation, including
  unexpected existing grantees. PUBLIC and `tunza_runtime` cannot execute it.
- The implementation becomes the owner-only **SECURITY INVOKER** worker
  `tunza_synthetic_transfer_apply_private`. It has no standalone definer authority
  and is not executable by either application login.
- The fixed-signature `tunza_synthetic_transfer_decide` wrapper retains the narrow
  SECURITY DEFINER boundary and checks authenticated `session_user` equals
  `tunza_fixture_runtime` and the actual database name matches
  `^tunza_[a-z0-9_]+_(test|synthetic)$`. These identities are not caller-settable
  environment GUCs, and `SET ROLE` cannot change `session_user`.
- Normal upgrades create no fixture login and grant no application role fixture
  execution. Transfer-table and helper-function grants are also removed from the
  standard runtime; production does not need fixture metadata privileges.
- Downgrade does **not** automatically restore the unsafe runtime grants. It
  returns the previous function name/schema with owner-only access. An isolated
  fixture must be explicitly reprovisioned after re-upgrading.
- Existing connection verification still rejects superusers, BYPASSRLS, ownership,
  CREATE privileges and **all role memberships**. It additionally fails closed
  if an ordinary runtime login can execute either transfer entry point, or if a
  fixture login can execute the private worker.

The wrapper accepts no actor/scope overrides and delegates to the existing atomic
implementation only after its database-principal guard. Logical staff identity,
current grants, destination and pending/version checks still occur inside that
implementation. Fixed `pg_catalog, public, pg_temp` search paths and schema-qualified
queries are retained. Only a bounded decision ID is returned; no general cross-tenant
query is exposed. No RLS policy, audit transaction or application encryption control
is relaxed.

### Owner-only fixture provisioning

Use a **separate isolated loopback database** named `tunza_…_test` or
`tunza_…_synthetic`, never a production database/cluster. Apply migrations through
the existing separate owner connection first, then explicitly run:

```text
python -m tunza.fixture_database
```

The command reads `APP_ENV` and `MIGRATION_DATABASE_URL`. It refuses production,
non-loopback or non-fixture database targets and standard/fixture application
credentials. It also checks the authenticated database provisioner's actual
owner/superuser authority; changing the environment string cannot confer that.

It provisions `tunza_fixture_runtime` as a non-owner login with NOSUPERUSER,
NOBYPASSRLS, NOINHERIT, NOCREATEDB, NOCREATEROLE, NOREPLICATION and **no memberships**.
Base table/column DML ACLs are mirrored directly, retaining narrow UPDATE grants;
only the required RLS helper functions and fixture tables/wrapper are added. There
is no schema CREATE, function ownership, private-worker EXECUTE or grant option.
No password is copied, generated or printed. Supply separate fixture authentication
through PostgreSQL provisioning; passwordless trust is confined to the disposable
loopback CI/test service, not an application credential fallback.

Run the fixture application with all of:

```text
APP_ENV=development (or testing)
DATABASE_URL=<separate tunza_fixture_runtime credentials for the isolated fixture DB>
DATABASE_RUNTIME_ROLE=tunza_fixture_runtime
SYNTHETIC_TRANSFERS_ENABLED=true
```

CI and local integration tests use `TEST_FIXTURE_DATABASE_URL` for this separate
login, while the normal suites retain `TEST_DATABASE_URL` as `tunza_runtime`.
Fixture setup uses the owner only for migrations/provisioning, then runs **all
application operations through the restricted fixture login**, on the same database
transaction as audit and idempotency writes. There is no second privileged API
connection that could commit reassignment independently of audit rollback.

### Remaining trust limits

The isolated fixture login intentionally carries fixture-transfer capability. A
holder of that credential can set the logical actor/facility context for that
synthetic database; it must never be distributed as a production credential.
Database naming supplements isolation but is not external attestation that data
is synthetic. Trusted provisioning and separate infrastructure remain necessary.
Database owners/superusers can change grants, owners or session authorization and
are outside the ordinary-runtime threat model. Application-set RLS context is
still trusted for logical staff identity, as documented in the existing security
contract; this correction is not a general defense against DB-owner or ordinary
application-credential compromise.

Command-key advisory locks precede pregnancy/request/responsibility locks.
Concurrent identical commands replay one result; competing decisions at one
version yield one terminal outcome and one current responsibility. Completed
replay is checked before stale-version rejection but after current authorization.
Responses are encrypted snapshots and may reflect an earlier request state; the
UI reloads current scoped lists. D14/D14a key expiry/retention remains unconfigured.

The function participates in the caller's transaction: audit or replay persistence
failure rolls back source closure, destination profile/preference/assignments,
decision, parent/request versions and cached response together. Responses are
materialized before commit clears transaction-local context. Tests also verify
search-path/EXECUTE restrictions, direct-call denial, missing context, append-only
history, overlap rejection and pooled-context reset.

## Verification

Locked dependencies on a dedicated loopback PostgreSQL 18 database, Python 3.15.1
and Node 24:

- **264 backend tests passed**, no failures or skips, including the original
  appointment/pregnancy/authentication suites and populated migration round-trips.
- **81 frontend tests passed across 12 files**, plus TypeScript, ESLint, coverage
  and production build.
- Ruff lint/format, `pip check`, `npm ci` and `npm audit` passed; npm reported zero
  vulnerabilities.

Direct SQL regressions cover standard runtime accept/reject/cancel attempts with
valid forged actor/facility/mode GUCs, SET ROLE/session-authorization denial,
private-worker denial for both application logins, accidental EXECUTE grants,
flag-only API attempts and migration revocation of the legacy grant without data
loss. Original acceptance, atomicity, source/destination isolation, replay and
audit-rollback regressions continue through the separately authenticated fixture
login. The security downgrade does not restore the original vulnerability.

The PR reports the independent GitHub Actions result on Linux/Python 3.14 and
PostgreSQL 18. Technical fixture acceptance does not resolve D04/D09 approval or
authorize live operations, consent portability, messaging, merge or deployment.
