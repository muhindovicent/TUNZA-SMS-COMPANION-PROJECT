# Phase 1B checkpoint — Automated foundation verification

This checkpoint adds automated verification to the existing Phase 1A foundation. The [architecture](architecture.md), [security requirements](security-and-consent.md), [roadmap](implementation-roadmap.md), and [unresolved decisions](unresolved-decisions.md) remain the design baseline.

## Workflow and triggers

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

[View Foundation CI runs](https://github.com/muhindovicent/TUNZA-SMS-COMPANION-PROJECT/actions/workflows/ci.yml).

It runs on:

- Every push to `main`, including documentation-only changes.
- Every pull request whose target branch is `main`, including fork pull requests.
- Manual dispatch from the Actions page.

The workflow uses `pull_request`, not `pull_request_target`. It requests read-only repository contents permission, does not persist checkout credentials, pins external actions to commit SHAs, and supplies no repository secrets to application tests. Concurrent superseded runs for the same branch/PR are cancelled. Each job has a 15-minute timeout.

## Jobs and commands

### Frontend verification

Ubuntu 24.04, Node.js 24; npm cache keyed by `frontend/package-lock.json`.

Commands run from `frontend/` in order:

```sh
npm ci
npm audit
npm run typecheck
npm run lint
npm run test:coverage
npm run build
```

These verify the locked dependency tree, dependency advisories, React/TypeScript types, ESLint rules, existing frontend tests, and production asset compilation. No application is deployed by the build step. Failed checks fail the job; no `continue-on-error` or test exclusions are used.

### Backend and PostgreSQL verification

Ubuntu 24.04, Python 3.14, PostgreSQL 18; pip cache keyed by the backend dependency snapshot and package metadata.

Commands run from `backend/`:

```sh
python -m pip install -r requirements-dev.lock -e ".[dev]"
python -m pip check
python -m ruff check .
python -m ruff format --check .
python -m flask --app tunza db upgrade
python -m flask --app tunza db current
python -m flask --app tunza db check
python -m pytest --cov=tunza --cov-report=term-missing
```

An additional inline smoke check calls the existing Flask liveness/readiness endpoints and requires HTTP 200 plus a connected database status. Readiness executes the actual SQLAlchemy `SELECT 1` check; it is not mocked.

The full backend suite runs with `TEST_DATABASE_URL` and `TEST_MIGRATION_DATABASE_URL` set. The PostgreSQL integration tests therefore run rather than taking their local no-database skip path. The migration round-trip verifies idempotent upgrade, Alembic head, metadata consistency, downgrade/re-upgrade, and real database readiness; the authentication integration suite exercises login, sessions, CSRF, rate limiting, RBAC, facility isolation, and the append-only audit trigger against the same disposable database, then drops back to base so the migration test still sees an empty database. Loopback/test-name/same-database guards remain intact.

## Disposable database and synthetic configuration

- Each backend job receives a fresh `postgres:18` service with database `tunza_ci_test` and synthetic role `tunza_ci`.
- The service is published only on runner loopback (`127.0.0.1:5432`). Its health check must pass before job steps proceed.
- Trust authentication is restricted to this disposable CI service to avoid a committed or real database password. This is not a configuration for deployment, shared databases, or future clinical runtime roles.
- Runtime, migration and test URLs point only to that service. Its owner role is used for migration/integration inspection of the empty test database, not as a proposed clinical API role.
- A random `SECRET_KEY` is generated for each backend job, masked in logs, and passed through the runner environment. No key or `.env` file is stored in the repository or an artifact.
- No patient fixtures, real patient information, SMS keys, hosting credentials, or GitHub repository secrets are required. Existing test keys and records remain synthetic.
- No database data directory or application environment file is cached. GitHub disposes of the job's service after execution.

## Results and troubleshooting

Open the run from the Actions page to inspect each named step. Frontend coverage and pytest results appear in the job logs. The current foundation has 25 frontend tests and 56 backend tests, including 15 PostgreSQL integration tests; future authorized changes may increase those counts.

If a check fails, fix the underlying dependency, formatting, build, configuration or test problem and push a new commit. Do not suppress failures, remove integration tests, relax database guards, or change advisory thresholds merely to make the workflow green. Hosted-run success is reported only after inspecting the actual run.

Local equivalents and prerequisites are documented in the [Phase 1A setup guide](phase-1a-foundation.md). Docker is supplied by the GitHub runner; it is not required for the database-independent local unit tests. Local PostgreSQL verification still requires a dedicated disposable database.

## Required-check setup

The workflow creates the status checks **Frontend verification** and **Backend and PostgreSQL verification**. To make them mandatory for merging, a repository maintainer must select them in the `main` branch ruleset/protection settings after the first run. This checkpoint configures verification; it does not change repository branch-protection settings.

## Scope

No patient registration, stakeholder dashboards, SMS or clinical functionality is introduced. Staff authentication and facility-scoped access are covered by the [Phase 1B-1 guide](phase-1b-1-authentication.md). No deployment is configured. The existing architecture, application tests and security checks are preserved.
