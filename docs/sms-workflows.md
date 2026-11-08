# SMS scheduling, clinical approval, opt-out, and delivery

> **DRAFT — Africa's Talking sandbox only. Synthetic patient data exclusively. No clinical messages, timing policy, or live escalation rules are invented here.**

## 1. Separate the records

| Record | Meaning |
| --- | --- |
| Occurrence | One logical intended message, independent of scheduler runs |
| Dispatch | One durable execution aggregate for that occurrence |
| Attempt | One intentional provider submission, including a pre-call intent record |
| Delivery event | Immutable provider report, possibly duplicated, delayed, or unmatched |

Weekly occurrence identity is episode + antenatal programme + gestational week. Appointment reminder identity is booking ID + reminder code; a replacement booking has a new ID. Identified sandbox-response identity is inbound ID + response kind. Unique indexes implement these keys.

Phone number, EDD revision, worker lease, content language, and scheduler-run ID do not create new identities for an already submitted weekly occurrence.

## 2. Clinical-content workflow

```text
Draft -> Submitted -> Independent approval -> Publication/effective interval
                  -> Rejected (new version required)
Published -> Suspended or Retired
```

1. An author prepares a version in English or Luganda with purpose/trigger and allowed parameters.
2. Submission freezes text, parameters and digest. Edits require a new draft version.
3. An independent authorized reviewer approves/rejects the exact digest and records a reason.
4. Publication requires approval and a nonoverlapping effective interval for template/language.
5. Renderer validates substitutions against the approved allowlist. Clinical free text cannot be injected into placeholders.
6. Store the version, exact rendered body, digest, language and segment count with dispatch history.
7. Suspension blocks unsent uses, including already selected versions. Submitted history is preserved.

English approval never approves Luganda automatically. Retired/rejected content cannot be edited back into use. Missing approved content creates a visible hold, not invented wording or language fallback. Sandbox escalation rules and referenced guidance use independent review as well.

## 3. Planning weekly education

1. Select an ongoing episode under current approved responsibility.
2. Resolve effective dating and the approved programme/week calculation (D05).
3. Upsert the unique weekly occurrence for the useful current window.
4. Resolve current consent, language, eligible contact and approved version.
5. Compute due/expiry times from approved local sending policy; persist UTC instants.
6. Create the unique dispatch only after required content/contact values are available; otherwise hold the occurrence with a reason.

Do not create a historical backlog burst for late registration. Missing dating holds gestational education, but not independently eligible confirmed-appointment reminders. Dating revisions regenerate future eligibility while preserving already submitted occurrence identities. No future message is invented beyond approved programme coverage.

## 4. Appointment reminders

Reference ANC guidance assists planning only. A reminder can submit only for a facility-confirmed booking with valid current assignment and consent.

- Lead times, reminder count, expiry and missed-appointment windows are D06 policy values.
- Rescheduling marks the old booking rescheduled, cancels unsent reminders, and creates a new scheduled booking.
- The replacement requires explicit facility confirmation before reminders become eligible.
- Cancellation, transfer or completion invalidates affected pending work.
- Patient YES/acknowledgement is stored separately from booking confirmation and attendance.
- Expired reminder windows do not produce late sends on worker recovery.

## 5. States and transitions

### Occurrence

`planned -> active -> fulfilled`, with `held`, `suppressed`, `cancelled`, or `expired` alternatives.

Held work may become active only after revalidation. Fulfilled means the provider accepted the intended submission; it does not mean delivered/read. A known delivery failure does not create a new weekly occurrence.

### Dispatch

| State | Meaning / next action |
| --- | --- |
| ready | Fully specified work, waiting until due |
| leased | Worker owns a bounded claim; no provider submission authorized yet |
| submitting | Submission intent and budget reservations committed; provider outcome may become uncertain |
| accepted | Provider accepted submission; wait for independent delivery events |
| retry_wait | Explicit retryable nonacceptance; wait until bounded backoff |
| held | Eligibility/configuration issue; no network call |
| suppressed | Consent/contact restriction prevents send |
| cancelled | Superseded appointment/transfer/completion or authorized cancellation |
| expired | Useful delivery window ended |
| rejected | Known permanent submission failure or exhausted permitted retries |
| unknown | Acceptance cannot be established; reconciliation only |
| reconcile_pending | Explicit durable reconciliation request, still prohibited from normal sending |

Held/suppressed work can resume only through renewed policy/consent/endpoint checks; it is not automatically retried on a timer. Already accepted work cannot be cancelled retroactively. Unknown work cannot enter ready/retry_wait unless nonacceptance is established and retry remains eligible.

### Attempt

`intent -> accepted | rejected | unknown`. Intent is persisted before the network call. A stale intent is uncertain, not proof that the call never occurred. Provider request references support reconciliation but must not be assumed to provide provider idempotency unless D11 verifies it.

### Delivery

Delivery has a separate reduced status: `pending`, `delivered`, `failed`, or `unknown`. Preserve provider raw/normalized events. Define provider-specific precedence under D11, not timestamp-only last-write-wins. Conflicting final reports require reconciliation. Delivery failure after acceptance does not by itself authorize a second send.

## 6. Worker algorithm and transaction boundaries

1. Poll indexed due work in bounded batches.
2. Atomically claim ready/retry_wait rows using row locking (`SKIP LOCKED`) and leases. Commit the claim quickly.
3. In a fresh authorization transaction, acquire shared coordination locks in a documented consistent order. Serialize against consent withdrawal, endpoint suppression, assignment changes, pauses and budget updates.
4. Recheck lease ownership and all eligibility conditions: adult eligibility, clinical/communication state, purpose-specific consent, contact, assignment, template, useful window, appointment, pause, limits and budget.
5. Atomically create the attempt intent and all applicable budget reservations; set dispatch submitting; record eligibility references and audit. Commit.
6. Call the provider outside the database transaction.
7. Persist accepted/rejected/unknown outcome. Retain provider references and update reservations without discarding uncertainty.

Eligibility snapshots contain version/event references and reason codes, not copied consent evidence or decrypted phone data. They support audit but do not replace live checks for a later attempt.

### Recovery matrix

| Interruption | Recovery |
| --- | --- |
| Claim expires before any attempt intent | Reclaim after checking no intent exists; rerun eligibility |
| Intent exists but process died before/around the call | Mark unknown; reconcile; never infer that the network call did not occur |
| Provider accepted but database write failed | Reconcile by verified provider reference/callback; do not resubmit |
| Explicit transient nonacceptance | Bounded backoff if window, consent and budget still valid |
| Invalid recipient/permanent rejection | Reject and create authorized contact-correction follow-up |
| Repeated infrastructure failure | Alert, retain durable work, use operational review |
| Database restored to older state | Outgoing pause plus provider reconciliation before worker resumes |

Retry count/backoff, polling, lease periods and reconciliation support remain D11/D14. Test settings must be explicitly labelled synthetic configuration.

### Consent race boundary

A committed withdrawal/suppression must prevent any subsequent submission authorization. A send already authorized and possibly entering a network call may not be retractable. Record the boundary honestly and perform an additional cancellation check immediately before the call where practical; do not promise atomic recall across PostgreSQL and the provider.

## 7. Cost and sending controls

- Global and facility pause apply together; the more restrictive control wins.
- Sending windows use the facility timezone and safe-contact preferences.
- Count actual encoding/segments after substitution; Luganda Unicode may change cost.
- Per-participant volume limits use existing attempts/reservations, not just delivered messages.
- Reserve against every applicable global/facility period cap atomically, with deterministic lock ordering.
- Known nonacceptance releases reservation according to verified charging policy. Accepted/unknown submissions retain enough allowance to prevent overspend until reconciled.
- Timer expiry alone does not release the financial risk of an unknown submission.
- Record provider charge/refund entries without double-counting both reservation and settled charge.

Budget values and rate cards remain D15; sandbox results do not establish production pricing.

## 8. Delivery callback handling

1. Verify the provider-supported channel authentication (D11), payload size and allowlisted fields.
2. Normalize identifiers and persist the encrypted event with a provider-supported deduplication key.
3. Acknowledge only after durable commit.
4. Match attempt by provider account/message ID; retain unmatched events for reconciliation.
5. Apply the verified state-reduction policy. Duplicate events have no duplicate effect.
6. Keep submission and delivery timestamps/statuses separate; expose unknown explicitly.

Callbacks cannot mark appointments attended or confirm pregnancy outcomes. Do not assume webhook signatures, provider request idempotency, or status-query support until verified.

## 9. Inbound and STOP pipeline

```text
Authenticated provider callback
  -> durable encrypted receipt and deduplication
  -> recognized STOP suppression in the ingestion transaction
  -> asynchronous supported classification / safe matching
  -> authorized case or restricted unresolved queue
```

For an approved recognized STOP token, lock the endpoint, create suppression, invalidate pending work and persist the inbound receipt before success acknowledgement. This prevents queue-processing lag from delaying recognized endpoint suppression. Vocabulary/normalization is D08; do not guess ambiguous free text as reliable attribution.

The restricted matching service may use explicit references, outbound context and authorized candidate links. Phone match alone is insufficient. If more than one participant remains possible:

- Do not change appointments, outcomes, attendance, or participant consent.
- Do not disclose candidate identities in replies or to unrelated facility users.
- Retain the message in restricted review. D10 must resolve who can safely review cross-facility ambiguity.

If STOP is reliably attributed, append the applicable participant withdrawal event. Otherwise suppress the endpoint only. Multiple participants sharing a phone are not all recorded as withdrawing.

Staff withdrawal works independently of SMS. Renewed participant consent does not lift endpoint suppression automatically; D08 endpoint-control verification is also required.

No automatic acknowledgement to an ambiguous/withdrawn endpoint is configured by this draft. Any permitted administrative response requires approved wording, purpose and cost handling. Identified sandbox responses use durable occurrence/dispatch records; unknown-recipient responses remain held rather than inventing an episode.

## 10. Assistance and controlled escalation

Assistance, contact correction, appointment follow-up and sandbox escalation become restricted follow-up cases with accountable assignment, acknowledgement, action history and disposition.

Use a reviewed versioned keyword/rule set for synthetic demonstrations. Record rule version, classification and approved guidance version. Test false positives, unknown text, repeated messages and identity ambiguity. Keyword rules are not reliable clinical triage and cannot diagnose.

No live escalation, monitoring promise, emergency contact or guidance text is introduced by these drafts. Staffing, verified contacts, hours and protocols remain D07/D10.

## 11. Sandbox boundary and acceptance

Sandbox exercises validate request construction, callbacks, state mapping, scheduling, failure recovery, opt-out and controlled routing. They do not prove handset delivery, production pricing, sender-ID approval or live support readiness.

Required scenarios include repeated scheduling, concurrent claims, lost acceptance responses, withdrawal races, shared phones, suspended content, rescheduling, transfers, Unicode segment calculation, duplicate/out-of-order callbacks and restoration. See [acceptance A13–A23 and A26](implementation-roadmap.md).
