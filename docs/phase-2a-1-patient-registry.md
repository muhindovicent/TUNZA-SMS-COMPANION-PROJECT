# Phase 2A-1: synthetic patient registry

This foundation is limited to synthetic adult records. Registration accepts a facility,
synthetic local reference, empty demographics object, and English or Luganda language.
It creates the facility profile, held episode and individual assignment in one transaction.
Eligibility and consent policies remain unresolved (D02 and D03); no clinical or messaging
eligibility is implied.

Patient reads require the relevant facility permission, an active membership and an active
individual assignment. RLS enforces transaction-local facility context as defense in depth;
application authorization and assignment checks remain necessary. The API database credential
must be a non-owner role without superuser or BYPASSRLS privileges. Synthetic demographics and
registration replay responses are encrypted using the configured patient key and key version.

The API connects directly with `DATABASE_URL` as `tunza_runtime`: `SESSION_USER` and
`CURRENT_USER` must both be that role. Connections fail closed when the role has superuser,
BYPASSRLS, database/role creation or replication privileges, any role membership, schema creation
privilege, or ownership of the database, public schema, or public relations. Do not use a privileged
login and `SET ROLE`. The migration command requires a distinct `MIGRATION_DATABASE_URL` owner
login; supply it only to migration processes. The disposable PostgreSQL CI service uses two login
roles with trust authentication confined to the isolated local test service. Integration tests
require `TEST_DATABASE_URL` for the runtime login and `TEST_MIGRATION_DATABASE_URL` for its owner.

Registration requires an `Idempotency-Key`. The actor and key uniquely identify a request;
retries with matching normalized input receive the encrypted stored result only after current
authorization and assignment are checked. Concurrent requests serialize on the unique key.
Different input with the same key is rejected. Mutation, replay record and audit event commit
atomically. The retention period for idempotency records remains unresolved (D14).

The registry interface provides permission-restricted registration, exact-reference search and
detail. PostgreSQL integration tests exercise synthetic registration, concurrent retries,
restricted-role isolation, missing context, connection reuse, assignment expiry and audit failure.
These tests do not approve real patient processing or deployment.
