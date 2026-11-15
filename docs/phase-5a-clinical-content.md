# Phase 5A — Synthetic clinical-content management

## Scope and acceptance boundary

Built on verified merge `ccbb3ec` (Phase 4B). Baseline CI passed before work:
https://github.com/muhindovicent/TUNZA-SMS-COMPANION-PROJECT/actions/runs/43768045456.

This implements the A13 independent-review and A14 suspension **technical
foundation** for English (`en`) and Luganda (`lg`) antenatal weekly/reminder slots.
**D07 is unresolved.** A technical fixture decision is not clinical sign-off,
an approved translation or authorization to send. No medical text, clinical week
coverage, substitution policy, reminder schedule or emergency rules are invented.
All other unresolved decisions remain unchanged, including D05/D06 timing and
D14/D14a retention. There is no scheduler, dispatch table, provider integration,
live messaging or deployment in this milestone.

Only text of this exact form is accepted:

```text
SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - en - SYN-A1B2C3D4
SYNTHETIC PLACEHOLDER - NOT CLINICAL ADVICE - lg - SYN-DEADBEEF
```

The final token is 8–32 uppercase hexadecimal characters; its language must match
the version. `allowed_parameters` must be exactly `[]`. Reasons are synthetic
codes matching `^SYN-[A-Z0-9][A-Z0-9-]{1,59}$`, not narrative clinical assessments.
Every version projection carries `approval_scope:synthetic_fixture_only`,
`clinical_signoff:false`, `dispatch_eligible:false` and
`dispatch_hold_reason:D07_unconfigured`, including after publication.

## API and permissions

Paths below are under `/api/v1`, with existing staff sessions, CSRF and no-store
responses. Authority comes from active programme role bindings and actual seeded
permission grants, not a facility supplied by the caller. Content roles gain no
patient-chart access. Other authors cannot read private drafts/submitted content;
reviewers can read submitted and later versions but cannot read others' drafts.
Published/suspended/retired versions form the authorized content corpus.

| Endpoint | Permission and result |
| --- | --- |
| GET/POST `/message-templates` | `content.read` / `content.author`; list / `201` metadata |
| GET `/message-templates/{id}` | `content.read`; metadata and template ETag |
| GET/POST `/template-versions` | `content.read` / `content.author`; list / `201` owned draft |
| GET/PATCH `/template-versions/{id}` | `content.read` / `content.author`; detail / `200` edited owned draft |
| POST `/template-versions/{id}/submit` | `content.submit`; `{}` → frozen submitted version |
| POST `/template-versions/{id}/review` | `content.review`; `{decision,reason,content_digest}` → `201` version with immutable review |
| POST `/template-versions/{id}/publish` | `content.publish`; `{effective_from,effective_to?}` → published fixture |
| POST `/template-versions/{id}/suspend` or `/retire` | `content.suspend`; `{reason}` → suspended/retired version |
| GET `/template-versions/{id}/events` | `content.read`; authorized immutable ordered history |

Template input is `{code,programme:"antenatal",purpose,trigger_kind,gestational_week?}`.
Code matches `^SYN-[A-Z0-9][A-Z0-9_-]{2,59}$`. `weekly_advice` requires trigger
`weekly` and a nonnegative integer index (PostgreSQL integer range); this is
technical metadata with no clinical coverage meaning. `appointment_reminder`
requires the same-named trigger and no weekly index. Metadata is immutable.

Draft creation takes `{template_id,language,text,allowed_parameters:[]}`; PATCH
accepts only `{text,allowed_parameters:[]}`. Only the template's original author
can add its versions. Corrections to frozen text/language/metadata use a new
version (or a new template for metadata changes), never an overwrite.

All writes require a printable `Idempotency-Key`. Creation of a version requires
the **template** If-Match ETag; draft edits/lifecycle actions require the **version**
ETag. Missing If-Match returns `428`, stale versions `412`, invalid state/digest,
key reuse or publication conflicts `409`, invalid fixture fields `422`, missing
endpoint permission `403`, and missing/inaccessible resources generic `404`.

Lists use `limit` 1–100 (default 25), stable created-at/ID ordering, and signed
actor/filter-bound `cursor` with `page.next_cursor`. Version filters are
`template_id`, `language` and `state`; they never widen RLS visibility.

## Review, publication and suspension

Lifecycle: `draft → submitted → approved/rejected`; `approved → published`;
`published → suspended/retired`; `suspended → published/retired`. Rejected and
retired versions are terminal. Submitted text/parameters/digest and identity are
immutable, including through SQL; draft edits also append history.

The SHA-256 digest binds template identity/code/programme/purpose/trigger/index,
version identity/revision/language/author, plaintext, empty parameters and the
synthetic-only approval scope. Reads and mutations authenticate/decrypt the body
and recompute its digest. Each language/version has one immutable decision by a
different person, even when the author holds both roles. An English review cannot
approve Luganda or another revision. Publication requires that exact approval.

Publication uses explicit timezone-aware instants and half-open `[start,end)`
intervals; null end is open-ended. A PostgreSQL GiST exclusion prevents overlapping
published periods for a template/language, including concurrent publications.
Published intervals cannot be rewritten. Suspension releases the active slot;
resumption is authorized only for the original interval and fails if a replacement
now overlaps. Retirement permanently removes the fixture from selection.

`content.catalog.select_fixture_version` and `revalidate_fixture_reference` are
live database queries requiring authorized content context and an aware instant.
They check state, interval, independent exact-digest approval and publication
generation, with no language fallback. Suspension invalidates an already-selected
reference, even across suspend/resume; a fresh selection is required. These are
fixture references, **not dispatch eligibility**. A future submission worker must
perform its own current checks inside the submission transaction, including D07,
consent and other unresolved policy gates. There is currently no sent-message
history or unsent queue to mutate; A14's dispatch integration remains future work.
Frozen source text and historical event/replay snapshots remain unchanged.

## Database and transactional security

Additive migration `0014_clinical_content` introduces:

| Table | Concrete responsibility |
| --- | --- |
| `message_templates` | Immutable synthetic metadata, original author, optimistic counter |
| `template_versions` | Unique template/language/revision, authenticated ciphertext, digest, lifecycle, immutable publication interval and optimistic counter |
| `content_reviews` | One immutable exact-digest decision/version; independent author/reviewer FKs; encrypted reason; clinical sign-off constrained false |
| `content_events` | Append-only version/sequence history with actor/time and encrypted digest/reason/interval snapshot |
| `content_idempotency` | Actor/operation/key uniqueness, canonical request hash, encrypted response/status and resource links |

All five tables have FORCE RLS. `tunza_content_permission(text)` is SECURITY
INVOKER, checks active users/programme grants, has pinned search path and no PUBLIC
execution. Runtime DML is SELECT/INSERT plus narrow template counter/version update
columns. Triggers enforce identity/body/period immutability, legal transitions,
review independence/digest, matching history and atomic review/state consistency.
Reviews, events and replay reject UPDATE/DELETE, including owner DML.

Commands lock actor/operation/key, then template, then version. Parent serialization
prevents revision/publication races. Body, review reason, event payload and replay
use separate authenticated-encryption purposes and existing key-version settings.
Review, state/counters, events, replay and sanitized audit commit together. Creation,
mutation, read and replay audit failures return no content and roll back writes.
Identical retries replay the original encrypted snapshot after checking current
authority, even after later transitions; changed key input conflicts. No retention
expiry is guessed. A replay snapshot never authorizes current fixture use.

The migration leaves prior rows/security boundaries intact, adds no SECURITY
DEFINER function and preserves ordinary-runtime isolation from the opt-in fixture
transfer database principal. The separate non-production fixture provisioner only
mirrors the new ordinary helper/DML grants. Downgrade removes these five tables
and their guards; it is destructive to Phase 5A history, not a retention workflow.

## Staff interface

`Clinical content` navigation requires `content.read`. Authors create metadata,
generate fixed language-labelled placeholders, edit owned drafts and submit.
Reviewers start in the submitted queue, inspect the exact digest and record a
synthetic approve/reject reason; own-content review controls are absent. Publication
inputs explicitly use Africa/Kampala wall time converted to UTC. Authorized users
can inspect event history and suspend/resume/retire published fixtures. Lists are
paginated; filters and layouts adapt to narrow screens. Uncertain retries preserve
the key; conflicts prompt refresh. D07/dispatch restrictions stay visible.

## Verification

Locked dependencies were installed and verified locally with PostgreSQL 18,
Python 3.15.1 and Node 24:

- **326 backend tests**, no failures/skips; 30 new content regressions and all 296
  existing tests pass. Coverage: 88% overall, 93% content routes.
- **99 frontend tests across 14 files**, including nine new content tests and all
  90 prior tests; TypeScript, ESLint, coverage and production build pass.
- Ruff lint/format, dependency checks and zero-vulnerability npm audit pass.
- Migration/readiness/schema checks and populated pre-Phase-5A table snapshots
  verify additive upgrade preservation and FORCE RLS.

Regressions cover separate languages, frozen corrections, self-review and wrong
digest denial, role separation, encryption, concurrent review/publication,
durable replay/key conflicts, pagination scope, period boundaries, suspension of
already-selected references, retirement, revoked authority, direct SQL guards and
atomic audit-failure rollback. The PR records independent Linux/Python 3.14 CI
verification before delivery. Clinical acceptance still requires D07 approval.
