# Phase 1B-1 — Secure authentication and facility access foundation

> **DRAFT — Technical refinement for review. Synthetic staff accounts and data exclusively. Session and throttling values are draft defaults pending D16.**

This milestone adds staff sign-in, server-side sessions, role-based permissions, and facility-level isolation to the Phase 1A foundation. The [architecture](architecture.md), [API specifications](api-specifications.md), [role-based permissions](role-based-permissions.md), [security requirements](security-and-consent.md), and [decision register](unresolved-decisions.md) remain the design baseline. Timeout and throttling values below are **draft technical defaults pending D16**; they are not approved operational policy.

## What this milestone adds

- Staff login (`POST /api/v1/auth/login`) with Argon2id password hashing and a generic, non-enumerating failure response.
- Opaque server-side sessions stored in `auth_sessions`, delivered as an `HttpOnly`, `SameSite=Lax` cookie (`tunza_session`). Only the SHA-256 hash of the token is stored.
- Idle and absolute session expiry, revocation on logout, and forced invalidation when a user's `security_version` changes.
- CSRF protection for every mutating `/api/v1/` request: session-bound tokens after sign-in, signed expiring pre-auth nonce tokens before sign-in, enforced server-side before any view runs.
- In-process sliding-window login rate limiting (per IP and per identifier) with `429` and `Retry-After`.
- PostgreSQL models and Alembic migration `0002_identity_and_access` for `users`, `roles`, `permissions`, `role_permissions`, `facilities`, `facility_memberships`, `role_bindings`, `auth_sessions`, and `audit_events`, plus deterministic role/permission seeds and an append-only audit trigger.
- RBAC with facility isolation: each active role binding is evaluated against its own role's permissions and its declared scope (binding scope must match the role definition; mismatched bindings are ignored); `require_permission` returns `401`/`403` with a denied audit event; facility scope requires both an active membership and a facility-scoped binding that itself grants the permission, so permissions never inherit across facilities or from unrelated platform bindings; platform scope grants facility metadata access only for permissions the platform role holds; out-of-scope facility detail requests return `404` so facility existence is not disclosed.
- A responsive staff sign-in page and a signed-in shell that shows the user's name, role badges, facility badges, and a sign-out action.
- A synthetic development seed command. There is no public self-registration endpoint.

## Data model

| Table | Purpose |
| --- | --- |
| `users` | Login identifier, display name, Argon2id hash, status (`invited`/`active`/`disabled`), `security_version` |
| `roles` | Seeded codes: `VHT`, `HCW`, `FA`, `AUTHOR`, `REVIEWER`, `ANALYST`, `PA` with scope kind |
| `permissions` / `role_permissions` | The 50 permission codes from the role matrix, seeded per role |
| `facilities` | Facility code, name, timezone, active flag |
| `facility_memberships` | User↔facility membership with `starts_at`/`ends_at` and status |
| `role_bindings` | User↔role assignment, `facility`/`platform`/`programme` scope, validity window, grantor |
| `auth_sessions` | Hashed token, state, `expires_at`, `last_seen_at`, `security_version` |
| `audit_events` | Append-only (database trigger blocks `UPDATE`/`DELETE`), sanitized metadata allowlist |

## API surface

| Endpoint | Notes |
| --- | --- |
| `GET /api/v1/auth/csrf` | `200 {csrf_token}`, `Cache-Control: no-store`; pre-auth nonce cookie or session-bound token |
| `POST /api/v1/auth/login` | `{login_identifier,password}` → `200 {state:"active"}`; generic `401`, `422` validation, `429` throttled |
| `GET /api/v1/auth/me` | `200 {user_id,display_name,state,facilities,bindings,permissions}`; `401` without a valid session |
| `POST /api/v1/auth/logout` | `204`, revokes the server-side session and clears the cookie |
| `GET /api/v1/facilities` | `facility.read`, scoped to accessible active facilities |
| `GET /api/v1/facilities/{id}` | Same permission; `404` when the facility is out of scope or inactive |

Failed sign-in responses are identical for unknown identifiers, wrong passwords, and disabled accounts. A dummy Argon2 verification runs for missing users so response timing does not reveal account existence.

## Draft session and throttling defaults (pending D16)

| Setting | Default |
| --- | --- |
| Idle session timeout | 30 minutes |
| Absolute session timeout | 12 hours |
| CSRF token max age | 15 minutes |
| Login attempts | 5 failures per 300-second window per IP and per identifier |

Rate limiting is in-process per worker. This is documented as a single-process development limitation; a shared store is required before multi-process deployment.

## Local sign-in walkthrough

```sh
# Backend (terminal 1), from backend/ with migrations applied:
flask --app tunza seed-synthetic-identity
flask --app tunza run

# Frontend (terminal 2), from frontend/:
npm run dev
```

The seed command prints one-time synthetic passwords for `synthetic.pa`, `synthetic.fa.a`, `synthetic.vht.a`, and `synthetic.vht.b`. It is disabled when `APP_ENV=production`. Open the dev server, sign in with a seeded account, and use **Sign out** to end the session. Synthetic development data only — no real credentials exist in this repository.

## Verification

- Frontend: `npm run typecheck`, `npm run lint`, `npm run test:coverage`, `npm run build` — 28 tests across the session shell, sign-in page, health API, and administration panel.
- Backend: `python -m ruff check .`, `python -m ruff format --check .`, `python -m pytest --cov=tunza --cov-report=term-missing` — 72 tests, of which 31 require `TEST_DATABASE_URL` (the Phase 1B migration round-trip plus authentication/RBAC/CSRF/session/audit and Phase 1B-2 administration integration tests, including multi-role cross-facility scoping, platform/facility permission isolation, expired bindings, ended memberships, scope-mismatch fail-closed cases, and delegation/escalation controls). The integration suite creates its schema from migrations, seeds synthetic staff itself, and drops back to base afterwards so the migration test still sees an empty database.
- CI runs the same commands on every push and pull request to `main`; see the [CI verification guide](ci-verification.md).

## Out of scope

Patient registration, clinical records, dashboards, SMS messaging, MFA, password recovery, production deployment, and any invented real credentials or clinical approvals remain out of scope. Repository secrets, provider keys, and real patient information must never be committed.
