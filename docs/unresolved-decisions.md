# Unresolved decisions

> **DRAFT — These items are not approved defaults. Synthetic patient data exclusively.**

The listed owners are proposed accountable stakeholder groups, not claims of named appointments. No due dates or facility approvals are invented.

| ID | Decision / evidence required | Proposed owner | Blocks |
| --- | --- | --- | --- |
| D01 | Obtain the standalone approved MVP Requirements Specification v1.1 and verify every recorded FR, wording, and exclusion | Zest product lead and facility representatives | Final requirements sign-off |
| D02 | Minimum registration fields, adult eligibility threshold/procedure, evidence, and treatment of uncertain eligibility | Clinical lead and privacy lead | Registration policy acceptance |
| D03 | English/Luganda consent notices, purposes, evidence, re-consent, and transfer portability | Privacy lead and clinical lead | Consent acceptance and live enrolment |
| D04 | Named authors/reviewers, delegation rules, exact VHT clinical duties, team coverage, transfer initiation/acceptance authority | Facility leads and Zest product lead | Permission-profile sign-off |
| D05 | Approved dating method, acceptable correction rules, weekly programme boundaries and coverage | Clinical lead | Clinical scheduling sign-off |
| D06 | ANC reference version, booking confirmation authority, reminder offsets, expiry, attendance windows, missed-contact rules | Facility clinical leads | Appointment-policy acceptance |
| D07 | Approved English/Luganda template corpus, translations, substitutions, keyword/rule set and emergency guidance for demonstrations | Independent clinical reviewers | Content and sandbox escalation acceptance |
| D08 | Sending windows, shared-phone wording, STOP vocabulary/normalization, acknowledgement policy, alternative withdrawal channels, endpoint reactivation verification | Privacy lead and facility leads | Messaging-policy sign-off |
| D09 | Transfer handover fields, participant agreement, pending-transfer hold policy, historical access and retention | Facility leads and privacy lead | Transfer acceptance |
| D10 | Case ownership, staffing, support hours, acknowledgement/closure targets, verified contacts, unresolved-case handling | Facility operational leads | Follow-up operating acceptance; all live escalation |
| D11 | Verify Africa's Talking sandbox callback payloads, callback authentication, delivery state mapping, deduplication identifiers and reconciliation capabilities | Integration lead | External adapter contract acceptance |
| D12 | Hosting region/provider, agreements, controller/processor responsibilities, legal basis and cross-border arrangements | Zest operations and privacy lead | Live hosting authorization |
| D13 | Retention/deletion, data-subject requests, export authority, aggregate denominators and small-cell disclosure controls | Privacy lead and programme leads | Reporting/export and live-data policy |
| D14 | Expected workload, page/latency targets, polling interval, retry ceiling/backoff, lease length, recovery targets and support budget | Engineering and operations leads | Performance/recovery acceptance |
| D15 | SMS budget caps, rate-card/segment estimates, reservation expiry and unknown-submission cost reconciliation | Programme budget owner and integration lead | Spending-policy acceptance |
| D16 | Account provisioning/recovery delivery channel, MFA enrollment/recovery, privileged audit-review delegation, authentication timeouts/rate limits | Security lead and facility administrators | Authentication and audit operating acceptance |

## Treatment in the draft

- Technical mechanisms may be fully specified while their policy values remain unconfigured.
- Missing policy that affects safe dispatch creates a visible hold.
- Proposed role defaults and state transitions remain drafts until reviewed; they are not evidence of clinical approval.
- Synthetic test configurations must be labelled as test settings, never represented as approved clinical timing or operational service levels.
- Any scope change discovered during D01 reconciliation must update requirements, schema, API, workflows, and acceptance criteria together.

## Separate live-use decisions

Technical sandbox acceptance does not approve real participant processing. Clinical/privacy approval, operational readiness, a controlled pilot decision, production messaging arrangements, and any live two-way/escalation service require explicit separate authorization.

## Idempotency-retention subdecision (D14-related)

| ID | Decision / evidence required | Proposed owner | Blocks |
| --- | --- | --- | --- |
| D14a | Idempotency key retention window for eligibility checks and consent events (replay cache TTL, purge criteria, audit interaction) | Engineering and privacy lead | Idempotency-replay safety in production |
