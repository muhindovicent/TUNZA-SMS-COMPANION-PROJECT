# Phase 2A-2: Patient eligibility and consent management

This phase extends the synthetic patient registry with eligibility assessments,
versioned consent notices, append-only consent events, and protected evidence
references. D02 and D03 remain unresolved; all clinical eligibility thresholds,
consent notice wording, and evidence requirements are test fixtures only and do
not represent approved clinical or legal policy.

## Models

- `protected_evidence` — encrypted evidence references with facility scope
- `adult_eligibility_checks` — adult eligibility assessments with result
  (verified/uncertain/ineligible), method, and evidence reference
- `consent_notice_versions` — versioned consent notice text (en/lg) with purpose
  codes and approval status
- `consent_events` — append-only consent events (grant/withdraw/correct) with
  event sequence for deterministic ordering

## APIs

- `POST /patients/{id}/eligibility` (`eligibility.record`) — record an adult
  eligibility check for an assigned synthetic patient
- `GET /consent-notices` (`consent.read`) — list approved consent notice
  versions filtered by purpose and language
- `GET /patients/{id}/consent-events` (`consent.read`) — list consent events
  for an assigned patient
- `POST /patients/{id}/consent-events` (`consent.record` / `consent.withdraw`)
  — record a consent grant, withdrawal, or correction
- `GET /patients/{id}/evidence/{evidence_id}` (`consent.read` /
  `eligibility.record`) — access protected evidence for an authorized patient

## Security

- All new tables use PostgreSQL `FORCE ROW LEVEL SECURITY` with transaction-local
  facility context as defense in depth
- The API connects directly as the non-owner `tunza_runtime` role
- Migrations require a distinct `MIGRATION_DATABASE_URL` owner connection
- RLS policies restrict access to the actor's current facility
- Audit events record all eligibility and consent operations atomically with
  the mutation

## Messaging eligibility

- Default unverified participants are ineligible for messaging
- Missing evidence blocks messaging eligibility
- Consent withdrawal immediately invalidates future messaging eligibility
- Historical consent and eligibility records are preserved (append-only)
- No clinical policy values are implemented; D02 and D03 remain unresolved

## Frontend

- Role-restricted eligibility and consent management interfaces for VHTs and
  healthcare workers
- Synthetic data only; no real patient processing

## Acceptance (A04–A06)

- A04: Unverified/ineligible synthetic registrations cannot become
  message-eligible; no invented adult-verification fallback
- A05: Every consent grant links purpose, notice, method, actor and evidence;
  corrections preserve original events; missing evidence blocks dispatch
- A06: After committed withdrawal, messaging eligibility is invalidated and
  history is retained