# Database security, facility isolation, audit, and consent

> **DRAFT — Proposed controls. Synthetic patient data exclusively.**

## 1. Database privileges

| Database principal | Permitted scope |
| --- | --- |
| Migration/owner role | Schema changes through reviewed migrations; unavailable to API/worker |
| API runtime role | Approved service operations under authenticated actor/facility scope; non-owner, no `BYPASSRLS` |
| Worker role | Approved due-work/dispatch operations under explicit facility scope; non-owner, no `BYPASSRLS` |
| Callback ingress role | Minimal durable event ingestion; no clinical profile listing |
| Inbound routing role | Restricted candidate matching and suppression operations; no unrestricted user-facing search |
| Reporting role | Authorized scoped views/approved aggregates, not unrestricted base-table access |
| Backup operator | Restricted operational backup/restore responsibilities; no routine clinical browsing |

Use role-specific grants, parameterized queries, reviewed migrations, restricted schema creation, and separate service credentials. Audit and consent histories disallow ordinary UPDATE/DELETE. Runtime users cannot change schema, policies, or database roles.

PostgreSQL `FORCE ROW LEVEL SECURITY` is planned for owned clinical tables; owner/administrative bypass still requires separate restricted operational controls. Database policies are defense in depth, not a replacement for application authorization.

## 2. Scope establishment

1. Authenticate an active individual account.
2. Resolve active role bindings, membership, and approved facility scope from server records.
3. Establish actor/facility context transaction-locally, never by trusting a browser-supplied facility claim.
4. Verify participant/team assignment for identifiable care access.
5. Apply row-level policies and service-layer field restrictions.
6. End the transaction; pooled connections must not retain a previous request's scope.

Unset/invalid context denies access. A raw UUID is not authorization. Membership and assignment expiry/revocation must be checked on each protected operation. Changing current facility cannot expand permissions.

RLS context variables are not an independent authentication mechanism: a compromised database service credential may set them. Restrict SQL capabilities, use parameterized queries, review privileged functions, and treat service-credential compromise as an incident.

## 3. Ownership and transfers

- Canonical patient/episode IDs provide continuity but do not expose a global patient directory.
- Local identifiable profiles and contact links require facility ownership and authorized assignment.
- Clinical records reference an episode-facility assignment through matching facility/episode keys.
- Current assignments allow current care; bounded historical access is separately checked.
- Destination staff see approved handover fields only, not all source records.
- Source users cannot read new destination records.
- Transfer acceptance is a narrow audited transaction, not a general cross-tenant service bypass.
- Restricted routing may match shared endpoints across facilities but must not return candidate identities to ordinary callers.

Cross-facility handover acceptance needs a narrowly scoped operation that verifies the named destination, active transfer, and acceptance permission. Its database function, if used, must use fixed search paths and tightly limited grants; no general `SECURITY DEFINER` query endpoint is permitted.

## 4. Consent model

Each append-only event records participant, optional episode, purpose, decision, notice version, language, recording method, actor, event time, recorded time, and protected evidence reference. Purpose identifiers are technical labels; approved legal wording remains D03.

Effective consent is derived from applicable events, not a mutable boolean:

1. Resolve the purpose and episode.
2. Validate the notice/evidence and scope.
3. Apply the latest applicable withdrawal/supersession.
4. Check facility/transfer portability approved under D03/D09.
5. Require renewed valid consent after withdrawal.

Use deterministic event ordering by effective time plus persisted sequence; prohibit arbitrary client backdating without an authorized correction workflow. Corrections append events and preserve evidence.

Staff withdrawal and endpoint suppression invalidate pending work in the same transaction. Dispatch checks also consult current eligibility. Communication state alone never authorizes messaging.

Consent to SMS, participation, and retention of clinical records are distinct matters. Withdrawal does not imply arbitrary deletion or erase legally retained history. Retention and data-subject handling remain D13.

## 5. Shared contacts

Encrypt numbers; normalize for a keyed lookup used only by restricted matching services. Store shared-phone status and safe-contact preferences. Do not expose linked participants from an endpoint query.

Recognized STOP creates immediate endpoint suppression even if the sender cannot be safely attributed. Patient withdrawal is recorded only for a reliably identified person. Restoring one participant's consent must not automatically remove endpoint-wide suppression; approved endpoint-control verification is also required (D08).

## 6. Authentication controls

Use Argon2id password hashing, server-side opaque sessions, Secure/HttpOnly cookies, appropriate SameSite policy, CSRF tokens for mutations, login/recovery throttling, and no account-enumerating recovery responses.

Rotate session IDs at login, completed MFA, and privilege changes. Revoke sessions on logout, deactivation, password reset, or recovery compromise. Privileged users must finish MFA before receiving privileged access. Store MFA secrets encrypted and recovery codes hashed. Parameters and recovery channel remain D16.

See [API authentication flows](api-specifications.md) for state and response contracts.

## 7. Audit contract

Record actor/service, request correlation ID, permission/action, facility, resource reference, outcome, timestamp, and reason where required. Audit sensitive views/searches, writes, exports, consent, clinical corrections, transfers, approvals, role changes, and operational overrides.

- Mutation plus audit commits atomically; audit failure rolls back the mutation.
- Sensitive read audit must persist before a response containing patient information is released. Failure returns a generic service error, not unlogged PHI.
- Denied access and failed authentication generate sanitized security events without identifying whether an inaccessible patient exists.
- Audit rows exclude full message bodies, passwords, tokens, raw phone numbers, and unnecessary before/after PHI.
- Runtime roles cannot update/delete audit rows. This does not make the database tamper-proof against privileged operators; backup/export review and administrative monitoring are separate responsibilities.
- Audit access requires explicit delegated scope; a generic platform-admin role does not grant patient audit browsing.

## 8. Data protection and provider boundaries

Encrypt transport, sensitive fields, message bodies, evidence, and backups. Keep keys outside the database/source repository, restrict key use, version keys for rotation, and avoid decrypted values in logs. No PHI in URLs, frontend analytics, browser persistent storage, or crash reports.

Validate and bound webhook payloads. Confirm the provider-supported authentication mechanism under D11; do not invent a signature scheme. Persist only allowlisted fields and sanitize operational logs. Callbacks cannot authenticate as staff or alter attendance.

Sandbox records contain synthetic information only. Templates must be reviewed before being presented as clinically approved content; documentation does not supply clinical messages.

## 9. Backup and restoration

- Encrypted automated backups; point-in-time recovery if supported within budget.
- Restricted restore access and a documented exercise before technical acceptance.
- Restore with outgoing messaging paused and callback/event consistency checked.
- Reconcile all potentially submitted attempts before enabling the worker; a restored older database must not replay messages already sent.
- Verify audit, consent, and suppression history before resumption.
- Earlier RPO/RTO suggestions are planning options, not approved guarantees; final targets are D14.

## 10. Acceptance

Exercise absent RLS context, wrong facility, unassigned care access, expired assignment, revoked membership, cross-facility references, shared endpoints, transfer handovers, audit failure, unknown submissions, and restore replay prevention. Use synthetic participants only. See [roadmap acceptance](implementation-roadmap.md).
