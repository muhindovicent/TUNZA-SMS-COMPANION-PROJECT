# Phase 1A — Application foundation and local setup

**Implemented scope:** a React/TypeScript application shell, modular Flask API, PostgreSQL connectivity, SQLAlchemy, Alembic migration history, environment configuration, and foundation tests. All development and verification use synthetic data only.

The [architecture](architecture.md), [API design](api-specifications.md), [security requirements](security-and-consent.md), and [decision register](unresolved-decisions.md) remain the design baseline. This milestone is a prerequisite to the roadmap's identity/isolation work, not completion of A01–A03 or permission/audit controls. No authentication, stakeholder dashboards, patient/domain tables, messaging, or clinical workflows are implemented.

## 1. Prerequisites

- **Node.js 22.12+ or 24+**, with npm. Node 24 is recommended and was used for verification.
- **Python 3.12+**; Python 3.14 is the verified development version. Prefer a stable supported release with psycopg/SQLAlchemy wheels.
- **PostgreSQL 16+**, running locally with a dedicated empty development database. PostgreSQL 18 is the verified version.
- A local database account permitted to connect. Alembic additionally needs an owner/migration account permitted to create its version table.

Use an isolated synthetic development database, never an existing clinical or production database. Local servers bind to loopback; neither server is a deployment configuration.

## 2. Repository layout

```text
.env.example                 # Blank secret/database values; public configuration labels
backend/
  pyproject.toml             # Package and development dependency declarations
  requirements-dev.lock      # Tested pinned dependency snapshot, no local paths
  src/tunza/
    __init__.py              # create_app factory and blueprint registration
    config.py                # Validated environment configuration
    extensions.py            # Unbound SQLAlchemy and Flask-Migrate extensions
    http.py                  # Sanitized JSON errors and server request IDs
    health/                  # Infrastructure blueprint
    services/database.py     # Read-only SELECT 1 connectivity check
  migrations/                # Alembic configuration and baseline revision
  tests/                     # Unit and opt-in PostgreSQL integration tests
frontend/
  src/App.tsx                # Responsive branded shell and connection status
  src/api/health.ts          # Bounded, validated health requests
  src/styles.css             # Local responsive styles; no remote fonts/assets
  src/test/                  # Test setup
  vite.config.ts             # Same-origin /api development proxy
  package.json
  package-lock.json
docs/                        # Existing architecture plus this setup guide
```

## 3. Configure your environment

From the repository root, copy `.env.example` to `.env` using your editor or file manager. `.env` is ignored by Git. Generate a local key:

```shell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `SECRET_KEY` to that generated value. Set `DATABASE_URL` to your dedicated database connection, using this **format**, not literal placeholder values:

```text
postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/DATABASE
```

URL-encode special characters in credentials. PostgreSQL URLs using `postgresql://` or `postgres://` normalize to the psycopg driver. SQLite/MySQL URLs and incomplete PostgreSQL URLs are rejected. There is no database or secret fallback.

Phase 2A-1 supersedes this earlier setup: migrations require a separate `MIGRATION_DATABASE_URL`, and the API must connect directly as the restricted runtime role. See [Phase 2A-1](phase-2a-1-patient-registry.md).

The factory loads only the root `.env`, with process environment taking precedence. Backend configuration is evaluated per factory instance, not at import. Debug and SQL echo are off by default. The key is configured for future framework use; no authentication/session routes exist yet.

Only `VITE_*` variables may enter the browser bundle. **Never prefix secrets or connection strings with `VITE_`.** `API_PROXY_TARGET` is read only by Vite's server configuration.

## 4. Install and start the backend

### Windows PowerShell (repository root)

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.lock -e "./backend[dev]"
.\.venv\Scripts\python.exe -m flask --app tunza db upgrade
.\.venv\Scripts\python.exe -m flask --app tunza run --host 127.0.0.1 --port 5000
```

### macOS/Linux (repository root)

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.lock -e './backend[dev]'
.venv/bin/python -m flask --app tunza db upgrade
.venv/bin/python -m flask --app tunza run --host 127.0.0.1 --port 5000
```

If you need a platform-specific dependency re-resolution, install `./backend[dev]` directly within the declared ranges, run all checks, and review changes to the pinned snapshot. Do not commit a freeze containing editable absolute paths or private index credentials.

### Migrations

`0001_foundation` intentionally creates no application tables. Alembic records the baseline in its own `alembic_version` table. Models, tenant ownership, RLS and domain constraints belong to later authorized milestones. There is no startup `create_all`, migration, seeding, or database mutation.

```shell
python -m flask --app tunza db current
python -m flask --app tunza db heads
python -m flask --app tunza db check
python -m flask --app tunza db upgrade --sql
```

Use your virtual-environment Python executable for these commands. Future migrations are generated with `flask db migrate -m "description"`, then manually reviewed before upgrade. Rollback examples must be used only on a dedicated disposable database; the integration test exercises downgrade to base and re-upgrade.

## 5. Install and start the frontend

In a second terminal, with working directory `frontend/`:

```shell
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The shell checks the API and database independently and provides a retry action. Unavailable services are shown honestly rather than reported as ready.

Default browser API prefix: `/api/v1`. Vite proxies `/api` to `http://127.0.0.1:5000`. Change the root `.env` `API_PROXY_TARGET` if your local API uses another port. Restart Vite after configuration changes.

`VITE_API_BASE_URL` may select another API prefix/origin. For this milestone prefer same-origin routing or the development proxy: no permissive CORS middleware is installed, so an arbitrary cross-origin URL will be blocked by the browser unless separately configured. `npm run preview` serves the built static frontend only; it does not proxy the API and is not an end-to-end deployment.

The browser uses five-second request timeouts and cancels stale/unmounted checks. Backend connect, pool and statement timeouts are bounded and configurable. A browser timeout does not terminate a server-side database operation.

## 6. Implemented public health API

These are the only implemented application endpoints. They deliberately expose no identities, hostnames, database names, credentials, schema details or driver exceptions.

### GET `/api/v1/health/live`

HTTP 200, independent of database availability:

```json
{ "data": { "service": "tunza-api", "status": "ok" } }
```

### GET `/api/v1/health/ready`

HTTP 200 when a read-only `SELECT 1` succeeds:

```json
{
  "data": {
    "service": "tunza-api",
    "status": "ready",
    "checks": { "database": "connected" }
  }
}
```

HTTP 503 when the database is unavailable:

```json
{
  "data": {
    "service": "tunza-api",
    "status": "not_ready",
    "checks": { "database": "unavailable" }
  }
}
```

Readiness validates connectivity only, not clinical schema, migration head, permissions, backups or operational readiness. It does not use the migration-owner connection. All responses have server-generated `X-Request-ID`, `Cache-Control: no-store` and security headers. Unknown routes and unsupported methods return sanitized JSON error envelopes; no client-supplied request ID is trusted. Health accepts GET/HEAD/OPTIONS, not business writes.

## 7. Checks

### Backend (working directory `backend/`)

```powershell
..\.venv\Scripts\python.exe -m pytest -m "not integration" --cov=tunza --cov-report=term-missing
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
..\.venv\Scripts\python.exe -m pip check
```

On macOS/Linux use `../.venv/bin/python`. Unit tests require no database and never use the configured development database. Tests supply generated ephemeral keys and synthetic connection identifiers.

### PostgreSQL integration (opt-in)

Create a separate **empty** loopback database whose name starts with `tunza_` and ends with `_test`. Set `TEST_DATABASE_URL` in the test process only, then run `python -m pytest -m integration` from `backend/`. The test rejects nonlocal or incorrectly named databases and databases containing application tables. It performs a migration upgrade, idempotent upgrade, metadata check, downgrade, re-upgrade and real readiness query. It must never target your development data or any shared/clinical database.

Use `TEST_DATABASE_URL` for the restricted test runtime login and `TEST_MIGRATION_DATABASE_URL` for the distinct test owner; both must target the exact same host, port and database. With no `TEST_DATABASE_URL`, pytest reports the integration test as **skipped**, not passed.

### Frontend (working directory `frontend/`)

```shell
npm run typecheck
npm run lint
npm run test:coverage
npm run build
npm audit
```

Tests cover the shell, loading/success/outage/retry states, cancellation, independent API/database results, response validation and configurable API URLs. Unit fetches are mocked; they never contact external services. TypeScript is strict. `package-lock.json` pins the resolved frontend dependency tree.

The `minimatch` override selects the patched 11.x line for development lint tooling (GHSA-g3hv-75wg-m249). Keep it until upstream dependencies adopt the patched range; lint/tests must pass after dependency updates.

### Phase 1A verification performed

Verified on Windows with Python 3.14, Node 24 and a newly initialized disposable PostgreSQL 18 cluster, separate from the existing local database service:

- 24 backend tests, including real PostgreSQL migration upgrade/check/downgrade/re-upgrade and readiness.
- 14 frontend tests, strict TypeScript checks, ESLint, and a production asset build.
- Ruff lint/format checks and Python dependency consistency.
- Frontend dependency audit: no reported vulnerabilities in the resolved tree.
- Actual HTTP connectivity through the Vite proxy using a non-owner runtime database role without schema CREATE privilege.
- Chromium desktop and 360px mobile checks: real connection status, retry, no horizontal overflow, and no page errors.
- Actual PostgreSQL shutdown: readiness returned 503 while API liveness remained 200.

The test cluster and development-server processes were stopped after verification. Browser checks are verification observations, not a committed deployment or claim of full cross-browser testing.

## 8. Security and remaining setup

- Keep local `.env`, virtual environments, dependencies, build outputs and logs out of Git.
- Provision your own empty PostgreSQL database and set local connection/key values before starting Flask.
- Use environment variables for secrets; no credentials or provider keys are bundled or committed.
- Neither framework debug mode nor raw driver-exception logging is enabled by this milestone.
- No patient fixtures, PHI storage, SMS credentials, sender ID, deployment or production connection is configured. Staff sign-in exists as of Phase 1B-1 with synthetic seed accounts only; see the [Phase 1B-1 authentication guide](phase-1b-1-authentication.md).
- Existing D01–D16 decisions remain open. Nothing in this milestone approves consent wording, clinical policy, live operation or privileged data access.

The original architecture and roadmap remain preserved. Phase 1A establishes only the prerequisites for their later, separately authorized work.
