# Phase 1B-2 — Controlled staff and facility administration

> **DRAFT — Technical refinement for review. Synthetic staff accounts and data exclusively. Invitation delivery, MFA enrollment, and recovery remain out of scope pending D16.**

This milestone adds controlled staff account provisioning, facility membership management, and delegation-scoped role assignment on top of the [Phase 1B-1 authentication foundation](phase-1b-1-authentication.md). The [architecture](architecture.md), [API specifications](api-specifications.md), [role-based permissions](role-based-permissions.md), and [security requirements](security-and-consent.md) remain the design baseline.

## What this milestone adds

- `POST /api/v1/users` (`account.provision`): creates an invited account, optional facility scope check, returns a one-time activation token for out-of-band delivery (never listed again).
- `POST /api/v1/auth/activations`: consumes the single-use SHA-256-hashed activation token, sets an approved-strength password (minimum 12 characters), activates the account, bumps `security_version`, and revokes outstanding sessions.
- `GET /api/v1/users` and `PATCH /api/v1/users/{id}` (`account.provision`): scoped account listing; enable/disable with reason; disable bumps `security_version`, clears the activation token, and revokes active sessions immediately; self-disable and out-of-scope targets are denied; invited accounts cannot be activated through `PATCH`.
- `GET`/`POST /api/v1/memberships` and `PATCH /api/v1/memberships/{id}` (`membership.manage`): scoped membership listing, creation with a valid interval, and end with a required reason.
- `POST /api/v1/role-bindings` and `POST /api/v1/role-bindings/{id}/revoke` (`role.delegate`): delegation-scoped grant/revoke with allowlists, facility alignment, membership prerequisites for care roles, and no self-elevation. Revocation applies the same allowlists as grant (including protected clinical and platform roles) and closes future-dated bindings so they can never become active while preserving `ends_at > starts_at`.
- Delegation allowlists in `tunza/identity/delegation.py`:
  - FA may bind only facility-scoped `VHT`, `HCW`, and `FA` roles within facilities where FA holds an active membership and a facility `role.delegate` binding.
  - PA may bind `FA`, `AUTHOR`, `ANALYST`, and `PA`; PA may never bind clinical or clinical-approval roles (`VHT`, `HCW`, `REVIEWER`).
  - Platform/programme bindings require a platform-scope `role.delegate` grant; binding scope must equal the role definition's scope.
- Append-only audit events for provision, activation, state change, membership create/end, role grant, and role revoke (success and denied outcomes), using the sanitized metadata allowlist.
- Responsive administration UI (`AdminPanel`) behind an **Administration** nav item that appears only when the signed-in identity holds any of `account.provision`, `membership.manage`, or `role.delegate`.

## Data model changes

| Change | Purpose |
| --- | --- |
| Migration `0003_staff_administration` | Adds `users.activation_token_hash` (unique SHA-256, nullable) and `users.activation_expires_at` (72-hour TTL) |
| `User` model | Mirrors the activation columns for token issue/consume |

Existing tables, CSRF enforcement, session cookie handling, and the append-only `audit_events` trigger are unchanged.

## API surface

| Endpoint | Permission | Notes |
| --- | --- | --- |
| `POST /api/v1/users` | `account.provision` | `201` invited account + one-time `activation_token`; `409` duplicate login; `404` out-of-scope facility |
| `GET /api/v1/users` | `account.provision` | Platform-wide or active members of managed facilities |
| `PATCH /api/v1/users/{id}` | `account.provision` | `status` `active`/`disabled` + `reason` (required to disable); `403` self-disable and shared/global accounts beyond facility authority; `404` out-of-scope-only targets; `409` invited transitions |
| `POST /api/v1/auth/activations` | None (pre-auth) | `{token,new_password}` → `200 {state:"activated",mfa_required:false}`; invalid/expired → `400` |
| `GET /api/v1/memberships` | `membership.manage` | Scoped list; optional `facility_id` filter |
| `POST /api/v1/memberships` | `membership.manage` | `MembershipInput` → `201`; `404` out-of-scope facility; `409` duplicate |
| `PATCH /api/v1/memberships/{id}` | `membership.manage` | `{status,ends_at?,reason}`; ending requires reason; `ends_at` always `> starts_at` |
| `POST /api/v1/role-bindings` | `role.delegate` | `BindingInput` → `201`; `403` self-grant/delegation/PA clinical ban; `404` out-of-scope facility; `409` care role without active membership |
| `POST /api/v1/role-bindings/{id}/revoke` | `role.delegate` | `{reason}` → `200` with `ends_at`; `403` delegation not allowed; `409` already revoked |

All mutating routes require the existing `X-CSRF-Token` header. Unknown write fields return `422`. Passwords, activation tokens, and session tokens are never returned in list projections or audit metadata.

## Privilege-escalation and isolation controls

- **No self-elevation**: administrators cannot create role bindings for their own user id; cannot disable their own account.
- **No cross-facility action**: facility-scoped provision/membership/bindings require an active membership plus a facility-scoped grant for the relevant permission; out-of-scope targets return `404` and write a denied audit event.
- **Shared/global account protection**: facility administrators cannot disable accounts that also hold active memberships outside their managed facilities or any active platform/programme binding (`403` + denied audit); platform administrators remain able to manage those accounts.
- **PA clinical ban**: platform administrators cannot grant `VHT`, `HCW`, or `REVIEWER`.
- **FA allowlist**: facility administrators cannot grant platform/programme roles or roles outside `{VHT, HCW, FA}`.
- **Delegation on revoke**: role revocation re-checks the same allowlists as grant, so protected clinical and platform roles cannot be revoked by unauthorized grantors.
- **Future-dated revoke**: revoking a binding that has not started yet closes its interval at revocation time with `ends_at > starts_at`, so the grant never becomes active.
- **Membership prerequisite**: assigning non-FA facility care roles requires an active membership at the target facility (`409` otherwise).
- **Immediate session revocation**: account disable bumps `security_version`, revokes active `auth_sessions`, and clears any pending activation token.

## Frontend

- `frontend/src/api/admin.ts` — typed client for the administration endpoints and `hasAdminPermission`.
- `frontend/src/components/AdminPanel.tsx` — responsive provision/list/disable, membership end, and role grant/revoke UI with one-time token callout.
- `StaffShell` shows the **Administration** nav control only for authorized identities; VHT-only identities never see admin sections.
- CSS additions keep tables scrollable and forms single-column under 720px.

## Verification

- Backend: ruff check/format clean; disposable PostgreSQL suite via `verify_tunza_foundation.py --database-only` runs unit + integration tests (migration round-trip, authentication suite, and the Phase 1B-2 administration suite covering provision/activate, FA cross-facility denial, PA clinical-role ban, self-grant denial, role revoke with delegation allowlists, future-dated revoke never activating, shared/global account disable denial, membership end, VHT 403, deactivation session revocation, CSRF, and audit outcomes).
- Frontend: `npm run typecheck`, `npm run lint`, `npm run test:coverage`, `npm run build` — 28 tests (session shell, sign-in, health API, AdminPanel).
- CI runs the same commands on every push and pull request to `main`; see the [CI verification guide](ci-verification.md).

## Out of scope

Patient records, clinical workflows, SMS, MFA enrollment, password recovery, production invitation delivery channels, deployment, and any real credentials remain out of scope. Synthetic development data only.
