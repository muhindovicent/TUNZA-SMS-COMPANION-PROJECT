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

Registration requires an `Idempotency-Key`. The actor and key uniquely identify a request;
retries with matching normalized input receive the encrypted stored result only after current
authorization and assignment are checked. Concurrent requests serialize on the unique key.
Different input with the same key is rejected. Mutation, replay record and audit event commit
atomically. The retention period for idempotency records remains unresolved (D14).

The registry interface provides permission-restricted registration, exact-reference search and
detail. PostgreSQL integration tests exercise synthetic registration, concurrent retries,
restricted-role isolation, missing context, connection reuse, assignment expiry and audit failure.
These tests do not approve real patient processing or deployment.
