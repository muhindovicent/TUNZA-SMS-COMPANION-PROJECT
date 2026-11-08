# REST API contracts and authentication

> **DRAFT — Proposed contract, not an implemented API. Synthetic patient data exclusively. Clinical policy values remain unresolved where indicated.**

## 1. Common contract

- Base path: `/api/v1`. HTTPS, JSON for staff endpoints, UTF-8.
- IDs: UUID strings; instants: RFC 3339 with explicit offset; dates: ISO `YYYY-MM-DD`.
- Responses use `{ "data": ... }`; lists use `{ "data": [...], "page": { "next_cursor": null } }`.
- Cursor pagination: `limit` integer 1–100, default 25 (draft technical defaults); opaque cursor bound to filters and authorized scope. Stable ordering includes ID as tie-breaker.
- Reject unknown write fields, malformed IDs/dates, oversized payloads, invalid enumerations, and forbidden server-controlled fields. Draft JSON request-body ceiling: 64 KiB; evidence uses references rather than uploads; provider limits verified under D11.
- Evidence is a protected reference, not an arbitrary public URL or file upload. Upload/storage procedures require D03/D12.
- Draft text limits: codes 64 characters, reason/disposition 1–1000, login identifier 254, display/local reference 200. Clinical message length is validated by approved content/segment policy, not arbitrary truncation.
- `facility_id` selects requested context; server-side membership, role and assignment decide whether that context is authorized.
- Do not expose contact numbers in URLs. Sensitive search uses POST and scoped audit.
- All examples and all patient-facing test values are synthetic; no valid recipient numbers or clinical message text are supplied here.

### Headers, idempotency, concurrency

| Header | Contract |
| --- | --- |
| Session cookie | Opaque server-side token; Secure, HttpOnly, SameSite=Lax for same-origin design |
| `X-CSRF-Token` | Required for staff mutations including login/recovery with pre-auth browser binding; not used to authenticate provider callbacks |
| `Idempotency-Key` | Required for all POST resource creation and state-changing business actions except authentication; printable opaque key <= 128 characters |
| `If-Match` | Required for updates/actions on a mutable existing aggregate; quoted integer version from response ETag |
| `X-Request-ID` | Server generates trusted correlation UUID; clients cannot spoof audit identity |

The idempotency namespace is user + named operation + key. Hash method, path, selected facility and canonical payload. A repeated key with the same request returns the persisted result after reauthorization; a different hash returns 409. Pending requests return 409 `request_in_progress`. Encrypt any stored response containing sensitive data. Expiry is D14; business uniqueness remains effective after key expiry.

Optimistic updates compare version inside the write transaction; mismatch is 412. An impossible current-state transition with a valid version is 409. Newly created child/history rows update their parent aggregate version. Idempotent completed-response replay is checked before version comparison, but still requires current authorization.

### Errors

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Request validation failed.",
    "fields": [{ "field": "language", "code": "unsupported_value" }],
    "request_id": "00000000-0000-4000-8000-000000000001"
  }
}
```

| Status | Meaning |
| --- | --- |
| 200 / 201 / 202 / 204 | Read/action success; created resource with Location; asynchronous accepted work; completed no-content action |
| 400 | Malformed JSON/encoding or invalid query syntax |
| 401 | Missing/expired/revoked session or incomplete authentication for protected routes |
| 403 | Authenticated action denied without disclosing a protected record; also failed CSRF |
| 404 | Resource absent or outside caller's discoverable scope; do not reveal hidden existence |
| 409 | State conflict, duplicate business key, incompatible idempotency reuse, unresolved processing |
| 412 | Stale `If-Match` |
| 413 / 415 | Body too large / unsupported media type |
| 422 | Well-formed request fails field/domain validation |
| 428 | Missing required `If-Match` |
| 429 | Rate limited; Retry-After supplied |
| 503 | Dependency/audit unavailable; no sensitive partial response |

Never echo passwords, tokens, raw phone numbers, submitted clinical text, or inaccessible candidate identities in errors. Validation runs within authorization boundaries; unknown patient IDs cannot be probed through detailed field errors.

## 2. Authentication and provisioning flows

### Invitation and activation

1. An authorized administrator provisions an invited account and bounded role binding; no public registration.
2. Deliver a single-use activation token using the approved channel (D16). Never return it in ordinary staff account lists or logs.
3. Activation atomically consumes the hashed token and sets the password; password is checked against an approved strength/breached-password policy.
4. Required privileged MFA enrollment/verification completes before privileged access.

### Login and sessions

1. Browser requests a CSRF token tied to an authenticated session or a signed, expiring pre-auth browser nonce.
2. Login validates Origin/CSRF and rate limits. Failure is generic regardless of account existence.
3. Successful password verification rotates the opaque session token. Return 202 `pending_mfa` when MFA is required; otherwise 200 `active`.
4. Pending sessions can access only self-authentication/MFA endpoints, never facility or patient routes.
5. Successful MFA consumes the challenge, atomically advances the factor's last verified TOTP step, and rotates to an active session. Replayed/expired challenges and reused factor codes fail.
6. Each protected request checks account/security version, session, active bindings, membership and scope. Authorization is not cached past revocation.

### Recovery, logout, and deactivation

Recovery request always returns generic 202. Reset atomically consumes the token, sets a new password and revokes all sessions; it does not silently bypass MFA. MFA recovery uses a one-time recovery code or an explicitly approved identity-verification process (D16). Logout revokes the session. Account deactivation immediately blocks sessions and new work by that staff account.

### Authentication endpoint contracts

| Method / path | Request | Response / permission |
| --- | --- | --- |
| GET `/auth/csrf` | None | 200 `{csrf_token}` with no-store; bound pre-auth or session context |
| POST `/auth/login` | `{login_identifier,password}` | 200 `{state:"active"}` or 202 `{state:"pending_mfa",challenge_id}`; sets cookie; generic 401 failure |
| GET `/auth/me` | Session | 200 `{user_id,state,bindings,facilities,permissions}`; pending MFA returns only self-auth state, no bindings |
| POST `/auth/mfa/verify` | `{challenge_id,code}` | 200 active; rotated cookie; own pending session only |
| POST `/auth/mfa/enrolments` | `{kind:"totp"}` | 201 own provisional factor with one-time setup response; no-store; recent auth/invitation enrollment context only |
| POST `/auth/mfa/enrolments/{id}/verify` | `{code}` | 200 verified and one-time recovery-code delivery; own unexpired enrollment |
| POST `/auth/mfa/recover` | `{challenge_id,recovery_code}` | 200 active after atomic code consumption; approved recovery requirements D16 |
| POST `/auth/logout` | Empty body | 204; revoke cookie/session |
| POST `/auth/recovery-requests` | `{login_identifier}` | 202 generic; no account-existence indication |
| POST `/auth/password-resets` | `{token,new_password}` | 204; one-time token; revoke sessions |
| POST `/auth/activations` | `{token,new_password}` | 200 `{state:"activated",mfa_required}`; no automatic clinical privilege |

Session inactivity/absolute lifetimes, attempt limits, provisioning channel and MFA recovery operation remain D16. Responses containing auth state use `Cache-Control: no-store`.

## 3. Resource schema definitions

The following are complete allowed body fields for the named contracts. Fields marked `?` are optional; everything else is required. IDs/timestamps/status/actor/history fields not listed in an input are server-controlled. Cipher fields are never exposed as a client storage format: services validate allowlisted plaintext and encrypt it internally.

| Schema | Fields and rules |
| --- | --- |
| `FacilityInput` | `code`, `name`, `timezone`, `active?`; IANA timezone; unique code |
| `AccountInput` | `login_identifier`, `display_name`, `facility_id?`; invitation only; platform/facility scope enforced |
| `AccountState` | `status` (active or disabled), `reason`; cannot self-activate an invited account or bypass required verification |
| `MembershipInput` | `user_id`, `facility_id`, `starts_at`, `ends_at?`; valid interval |
| `BindingInput` | `user_id`, `role_code`, `scope_kind`, `facility_id?`, `programme_facility_ids?`, `starts_at`, `ends_at?`; exact role scope/delegation checks |
| `TeamInput` | `facility_id`, `code`, `name`, `active?` |
| `TeamMembershipInput` | `membership_id`, `starts_at`, `ends_at?`; target team determines facility |
| `AssignmentInput` | `episode_facility_assignment_id`, exactly one `membership_id` or `care_team_id`, `duty`, `starts_at`, `ends_at?`; same facility |
| `RegistrationInput` | `facility_id`, `local_reference`, `demographics`, `language`, `contact?`, `eligibility?`; demographics allowlist D02; optional ContactInput/EligibilityInput; atomically creates profile, initial episode/facility responsibility and own participant assignment, no cross-facility matching; messaging initially held |
| `PatientUpdate` | `demographics?`, `local_reference?`; at least one; approved mutable fields D02; no patient/facility ID reassignment |
| `PatientSearch` | `facility_id`, exactly one `local_reference` or `phone`, `limit?`, `cursor?`; assigned scope only; shared phone results contain only caller-authorized records |
| `ContactInput` | `number`, `shared`, `language`, `safe_window?`, `privacy_flags?`; number normalizes to E.164; sandbox tests use provider-approved synthetic destinations only; language en/lg; window schema `{start,end}` local time |
| `ContactEnd` | `ended_at`, `reason`; contact changes create a new link rather than rewrite the sent destination |
| `EligibilityInput` | `method`, `result` (verified, uncertain or ineligible), `checked_at`, `evidence_id?`; verification rules/evidence D02 |
| `ConsentInput` | `episode_id?`, `purpose`, `decision` (grant, withdraw or correct), `notice_version_id?`, `method`, `language`, `evidence_id?`, `supersedes_id?`, `reason?`; D03 validates notice, evidence and effective scope; actor/recorded time server-set |
| `PregnancyInput` | `patient_id`, `facility_id`; for a subsequent episode of an already authorized patient; atomically creates facility responsibility and own approved participant assignment; dating is a separate HCW-authorized revision; initial messaging held until eligible |
| `DatingInput` | `basis`, `estimated_due_date`, `reason`; valid method/date range D05; no arbitrary client current-revision pointer |
| `CommunicationInput` | `state` (active or paused), `reason`; active requires consent, noncompleted episode and no unresolved restrictions; withdrawal/completion set by their workflows |
| `CompletionInput` | `completed_on`, `approved_outcome_fields`, `reason`, `supersedes_id?`; clinical field allowlist D02/D05; new revision rather than overwritten history |
| `TransferInput` | `source_assignment_id`, `destination_facility_id`, `agreement_evidence_id`, `handover`; distinct facility, D09 field allowlist, one pending transfer |
| `TransferDecision` | `decision` (accept or reject), `reason`; exact destination and pending-state checks; care-role approval if handover requires clinical authority |
| `AppointmentInput` | `episode_facility_assignment_id`, `starts_at`, `reference_contact_id?`; creates scheduled booking only |
| `AppointmentConfirm` | `reason?`; staff confirmation of a valid current scheduled booking; server records actor/time |
| `RescheduleInput` | `starts_at`, `reason`; creates a replacement scheduled booking; independent confirmation required |
| `AttendanceInput` | `status` (attended or missed), `effective_at`, `reason?`; missed window D06; no inference from replies |
| `CancelInput` | `reason`; permitted from scheduled/confirmed only |
| `TemplateInput` | `code`, `programme:"antenatal"`, `purpose`, `trigger_kind`, `gestational_week?`; trigger-specific fields |
| `VersionInput` | `template_id`, `language` (en or lg), `text`, `allowed_parameters`; server revision/digest/author; approved parameter allowlist D07 |
| `RuleVersionInput` | `code`, `rules`, `guidance_template_version_ids`; rule schema and reviewed content D07; sandbox only |
| `ReviewInput` | `decision` (approve or reject), `reason`, `content_digest`; author != reviewer; submitted immutable target |
| `PublicationInput` | `effective_from`, `effective_to?`; independently approved exact digest, no overlapping published version for template/language |
| `SuspendInput` | `reason`; suspension invalidates unsent selection without changing historical text |
| `InboundResolution` | `episode_facility_assignment_id`, `classification`, `evidence_basis`, `appointment_id?`; target within reviewer's authorized case scope; ambiguity cannot be guessed |
| `CaseAssignment` | Exactly one `membership_id` or `care_team_id`, `reason`; same facility, delegated responsibility |
| `CaseAction` | `action_type`, `notes?`, `disposition?`; resolve/close requires disposition; notes field permissions apply |
| `MessagingControl` | `paused`, `reason`; selected global/facility scope comes from route; does not grant content approval |
| `PolicyInput` | `config`, `approval_reference`; approved/test-labelled schema only; cannot invent clinical timing defaults |
| `BudgetInput` | `period_start`, `period_end`, `currency`, `cap`, `approval_reference`; nonnegative decimal strings; delegate/cap authority D15 |
| `ReconcileInput` | `provider_reference`, `reason`; requests provider-status review; never a force-resend instruction |

## 4. Response projections

| Schema | Returned fields |
| --- | --- |
| `Facility` | id, code, name, timezone, active, version |
| `Account` | id, display_name, status, scoped memberships/bindings, version; no password/hash/token/MFA secret |
| `Patient` | id, facility_id, local_reference, authorized demographic fields, eligibility summary, version |
| `Contact` | id, shared, language, safe-window/privacy subset, masked_number; full number only on authorized explicit care-detail view with audit |
| `ConsentEvent` | id, purpose, decision, notice/language/method, actor, effective/recorded time, evidence availability; no public evidence URL |
| `Pregnancy` | id, patient_id, permitted assignment, lifecycle, communication_state, permitted dating/completion summary, version |
| `Appointment` | id, authorized episode/facility, starts_at, status, supersedes_id, confirmation and attendance summary, version |
| `Transfer` | id, source/destination, state, permitted handover subset, decision summary, version |
| `ContentVersion` | id, template/rule reference, revision, language where applicable, text/rules for authorized content role, state, digest, effective interval, version |
| `Dispatch` | id, occurrence, status, template version, permitted timestamps, reason; care view may include approved message history; operations excludes body/contact/identity |
| `Inbound` | id, authorized case linkage, classification/processing state, permitted relevant body only; no global candidate list |
| `Case` | id, type, priority, state, assignment, due time, permitted actions/disposition, version |
| `Dashboard` | Role-specific scope, period, counts by defined states, hold reasons and authorized workload; no unauthorized record IDs |
| `AggregateReport` | facility/programme scope, denominator definition, period/cutoff, aggregate values after disclosure policy |
| `AuditEvent` | id, action, actor/service, scope, resource reference, outcome, time, sanitized metadata within delegated audit scope |

All clinical/auth responses use no-store. Individual detail responses carry ETag matching their current aggregate version. Lists may not expose broader fields than detail permission permits.

## 5. Administration contracts

| Method / path | Permission | Body -> result |
| --- | --- | --- |
| GET `/facilities` and `/facilities/{id}` | `facility.read` | Scoped Facility list/detail |
| POST `/facilities` | `facility.manage` | FacilityInput -> 201 Facility |
| PATCH `/facilities/{id}` | `facility.manage` | Mutable FacilityInput subset -> 200 Facility |
| POST `/users` | `account.provision` | AccountInput -> 201 Account; invitation delivered out-of-band |
| PATCH `/users/{id}` | `account.provision` | AccountState -> 200 Account; cannot disable users outside delegated scope |
| GET/POST `/memberships` | `membership.manage` | Filtered list / MembershipInput -> 201 membership |
| PATCH `/memberships/{id}` | `membership.manage` | `{status,ends_at?,reason}` -> 200 membership; revoke sessions/scope as appropriate |
| POST `/role-bindings` | `role.delegate` | BindingInput -> 201 binding |
| POST `/role-bindings/{id}/revoke` | `role.delegate` | `{reason}` -> 200 revoked binding |
| GET/POST `/teams` | `team.manage` | Filtered list / TeamInput -> 201 team |
| PATCH `/teams/{id}` | `team.manage` | TeamInput mutable subset -> 200 team |
| POST `/teams/{id}/memberships` | `team.manage` | TeamMembershipInput -> 201 team membership |
| POST `/team-memberships/{id}/end` | `team.manage` | `{ends_at,reason}` -> 200 ended membership |
| GET/POST `/participant-assignments` | `assignment.manage` | Scoped minimal-reference list / AssignmentInput -> 201 assignment |
| POST `/participant-assignments/{id}/end` | `assignment.manage` | `{ends_at,reason}` -> 200 ended assignment |

Administration list/detail projections exclude clinical profile fields. Binding IDs are not accepted as arbitrary authorization claims.

## 6. Care and transfer contracts

| Method / path | Permission | Body -> result |
| --- | --- | --- |
| POST `/patients` | `patient.register` | RegistrationInput -> 201 `{patient:Patient,pregnancy:Pregnancy,participant_assignment_id}` |
| POST `/patients/search` | `patient.read` | PatientSearch -> 200 paginated Patient; audit as sensitive read, not resource creation |
| GET `/patients/{id}` | `patient.read` | Patient; facility context required and checked |
| PATCH `/patients/{id}` | `patient.update` | PatientUpdate -> 200 Patient |
| GET/POST `/patients/{id}/contacts` | `patient.read` / `contact.manage` | Contact list / ContactInput -> 201 Contact |
| POST `/contacts/{id}/end` | `contact.manage` | ContactEnd -> 200 ended Contact; invalidate pending destination selection |
| GET `/patients/{id}/contacts/{contact_id}/number` | `contact.manage` | 200 `{number}` only when necessary for assigned care; sensitive-read audit |
| POST `/patients/{id}/eligibility` | `eligibility.record` | EligibilityInput -> 201 check |
| GET `/consent-notices` | `consent.read` | Approved notice versions filtered by purpose/language; notice text is not patient data |
| GET `/patients/{id}/consent-events` | `consent.read` | Scoped ConsentEvent list and effective-purpose summary |
| POST `/patients/{id}/consent-events` | `consent.record` or `consent.withdraw` | ConsentInput -> 201 ConsentEvent; withdraw requires withdraw permission |
| GET `/patients/{id}/evidence/{evidence_id}` | `consent.read` or `eligibility.record` | Authorized purpose-bound evidence stream; no-store; audit; patient/facility correspondence checked |
| POST `/pregnancy-episodes` | `pregnancy.create` | PregnancyInput -> 201 Pregnancy and initial facility assignment |
| GET `/pregnancy-episodes/{id}` | `pregnancy.read` | Pregnancy projection |
| POST `/pregnancy-episodes/{id}/dating-revisions` | `pregnancy.date` | DatingInput -> 201 revision and new parent version |
| POST `/pregnancy-episodes/{id}/communication-state` | `communication.pause` / `communication.resume` | CommunicationInput -> 200 Pregnancy |
| POST `/pregnancy-episodes/{id}/completion` | `pregnancy.complete` | CompletionInput -> 201 record; invalidates future work atomically |
| POST `/transfers` | `transfer.initiate` | TransferInput -> 201 pending Transfer |
| GET `/transfers/{id}` | `transfer.initiate` or `transfer.accept` | Source assigned-care/destination bounded Transfer view only |
| POST `/transfers/{id}/decision` | `transfer.accept` | TransferDecision -> 200 Transfer; acceptance updates responsibility atomically |
| POST `/transfers/{id}/cancel` | `transfer.cancel` | `{reason}` -> 200 cancelled Transfer |
| GET `/anc-reference-schedules` | `anc_reference.read` | Approved schedule/contact versions; no unapproved reference timing |
| POST `/appointments` | `appointment.schedule` | AppointmentInput -> 201 scheduled Appointment |
| GET `/appointments` and `/appointments/{id}` | `appointment.read` | Scoped list/detail; date/status filters |
| POST `/appointments/{id}/confirm` | `appointment.confirm` | AppointmentConfirm -> 200 confirmed Appointment |
| POST `/appointments/{id}/reschedule` | `appointment.reschedule` | RescheduleInput -> 201 replacement scheduled Appointment; old becomes rescheduled |
| POST `/appointments/{id}/attendance` | `appointment.attend` | AttendanceInput -> 200 Appointment plus append-only event |
| POST `/appointments/{id}/cancel` | `appointment.cancel` | CancelInput -> 200 Appointment |

POST actions on an existing patient/episode/booking require the relevant parent ETag. Search does not require idempotency/If-Match. Evidence access requires association with the authorized patient and correct purpose; an eligibility grant cannot read unrelated consent evidence.

Approved notice and ANC source artifacts are controlled configuration inputs with review provenance. An unrestricted clinical-guidance editor is not part of this API. Their provisioning procedure must be agreed under D03/D06 before acceptance.

## 7. Content and messaging contracts

| Method / path | Permission | Body -> result |
| --- | --- | --- |
| GET/POST `/message-templates` | `content.read` / `content.author` | Filtered corpus / TemplateInput -> 201 template |
| GET `/template-versions/{id}` | `content.read` | ContentVersion |
| POST `/template-versions` | `content.author` | VersionInput -> 201 draft ContentVersion |
| PATCH `/template-versions/{id}` | `content.author` | VersionInput mutable subset, draft only -> 200 |
| POST `/template-versions/{id}/submit` | `content.submit` | Empty -> 200 submitted immutable version |
| POST `/template-versions/{id}/review` | `content.review` | ReviewInput -> 201 immutable decision; version approved/rejected |
| POST `/template-versions/{id}/publish` | `content.publish` | PublicationInput -> 200 published version |
| POST `/template-versions/{id}/suspend` or `/retire` | `content.suspend` | SuspendInput -> 200 version; invalidate unsent affected work |
| GET/POST `/escalation-rule-versions` | `content.read` / `content.author` | Corpus / RuleVersionInput -> 201 draft |
| POST `/escalation-rule-versions/{id}/submit`, `/review`, `/publish`, `/suspend`, `/retire` | Same content permissions as templates | Same action schemas/state requirements; sandbox-only rules |
| GET `/message-occurrences` and `/dispatches` | `message.read` or `message.ops` | Scoped list with role-specific projection |
| GET `/dispatches/{id}` | `message.read` or `message.ops` | Dispatch, attempts/events with appropriate redaction |
| POST `/dispatches/{id}/reconcile` | `message.reconcile` | ReconcileInput -> 202 reconciliation request; no force-send |
| GET/POST `/messaging/global/pause` or `/messaging/facilities/{id}/pause` | `messaging.control` | Current control / MessagingControl -> 200 |
| GET/POST `/messaging/global/policies` or `/messaging/facilities/{id}/policies` | `messaging.control` | Active policy / PolicyInput -> 201 version |
| GET/POST `/messaging/global/budgets` or `/messaging/facilities/{id}/budgets` | `messaging.control` | Scoped list / BudgetInput -> 201 budget |

Reconciliation request persistence sets durable dispatch reconciliation state and records an audit event in one transaction; a worker performs provider review. No arbitrary-message composer, force-retry, skip-consent, or mark-delivered endpoint exists.

## 8. Inbound, case, reporting contracts

| Method / path | Permission | Body -> result |
| --- | --- | --- |
| GET `/inbound-messages` and `/inbound-messages/{id}` | `inbound.read` | Scoped metadata/body projection; unresolved cross-facility candidate sets are withheld |
| POST `/inbound-messages/{id}/resolve` | `inbound.resolve` | InboundResolution -> 200 classification/case reference, no automatic attendance |
| GET `/follow-up-cases` and `/follow-up-cases/{id}` | `case.read` | Assigned list / Case |
| POST `/follow-up-cases/{id}/assignments` | `case.assign` | CaseAssignment -> 201 assignment |
| POST `/follow-up-cases/{id}/acknowledge` | `case.act` | `{notes?}` -> 201 action; parent state acknowledged |
| POST `/follow-up-cases/{id}/actions` | `case.act` | CaseAction -> 201 action; legal transition only |
| GET `/dashboards/me` | `dashboard.read` | Role-filtered Dashboard |
| GET `/facilities/{id}/dashboard` | `dashboard.read` | Facility summary within role's scope, not automatically full-facility clinical totals |
| POST `/reports/aggregates/query` | `report.aggregate` | `{facility_ids,period_start,period_end,metric_codes}` -> 200 AggregateReport |
| POST `/reports/exports` | `report.export` | Same query plus approved format -> 202 only after D13 grant/format approval; disabled by default |
| GET `/audit-events` | `audit.read` | Scoped sanitized list; permission has no default role grant |

Aggregate query is read-only and audited; no idempotency header needed. Export retrieval and disclosure formats remain D13; do not expose an unfinished download route or silently grant bulk patient export.

Case transitions: open -> assigned -> acknowledged -> resolved -> closed, with recorded disposition for resolve/close; reopen -> assigned/open with reason. Reassignment retains prior history. Case ownership alone does not grant the full patient chart.

## 9. Appointment and content transitions

- Appointment: scheduled -> confirmed/cancelled/rescheduled; confirmed -> attended/missed/cancelled/rescheduled. Corrections to attended/missed require append-only clinical correction with reason, never overwriting history. Exact correction delegation is D04/D06.
- Rescheduling creates a new scheduled booking ID and invalidates old reminder keys; the replacement requires explicit confirmation.
- Content: draft -> submitted -> approved/rejected; approved -> published; published -> suspended/retired; suspended may return to published only after authorization and unchanged digest. Rejected/retired content requires a new version rather than body mutation.
- An effective date does not substitute for approval. English and Luganda versions are independently reviewed.

## 10. Provider callback contracts

Provider wire field names, content type, authentication, event IDs, and expected acknowledgement body must be verified under D11. The following is the **internal normalized contract**, not a claim about Africa's Talking's exact payload.

| Endpoint | Required normalized fields | Behavior |
| --- | --- | --- |
| POST `/webhooks/africastalking/delivery` | provider_account_ref, provider_message_id, provider_status, received_at; optional provider_event_id/provider_event_at/cost | Authenticate provider channel, validate/bound, persist/deduplicate, acknowledge after commit; unmatched IDs retained |
| POST `/webhooks/africastalking/inbound` | provider_account_ref, sender, body, received_at; optional provider_inbound_id/provider_event_at | Persist encrypted body and restricted endpoint match; durable STOP processing/routing; no staff-session privileges |

Callback success means durable receipt, not clinical handling. Persistence failure returns a retryable non-success response compatible with the verified provider contract. A repeated callback returns the same successful acknowledgement after confirming the existing record. Ambiguous payloads are quarantined without clinical mutation.

Deduplication fallback must not collapse distinct legitimate inbound messages with identical text. Exact keys and provider status precedence remain D11. Staff CSRF does not authenticate callbacks; provider ingress must have independently verified controls.
