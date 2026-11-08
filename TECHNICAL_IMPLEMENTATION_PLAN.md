# TUNZA — Technical Implementation Plan

## Revised against MVP Requirements Specification v1.1

- **Organisation:** Zest Technologies
- **Pilot facilities:** St. Paul's Hospital Kasese and Mt St Mary's Hospital Kasese
- **Status:** Proposed architecture for Gate A approval
- **Application implementation status:** Not started

This document is based on the stakeholder-provided TUNZA MVP Requirements Specification v1.1. It records the proposed technical plan; it does not authorize application implementation, deployment, live enrolment, or live patient processing.

## 1. Executive recommendation

Build TUNZA as a **modular Flask application**, supported by a **React staff dashboard**, **PostgreSQL**, and a **supervised database-backed messaging worker**.

Use **Africa's Talking sandbox** for outbound messaging, delivery callbacks, inbound replies, appointment-response workflows, and controlled escalation demonstrations.

| Area | Decision |
| --- | --- |
| Background processing | PostgreSQL-backed work records and a supervised Python worker; no initial Redis or Celery dependency |
| VHT access | Explicit participant assignments, not facility-wide patient access |
| Clinical staff access | Assigned patients or approved care-team responsibilities |
| Inbound messaging | Required in the sandbox MVP |
| Danger-sign workflows | Controlled sandbox recognition, guidance, and escalation queues |
| Facility transfers | Required, with explicit acceptance, controlled handover, and audit |
| Enrolment eligibility | Adult participants only; minors deferred |
| Appointment reminders | Generated only for facility-confirmed appointments |
| Live operation | Requires Gates A–D; real two-way communication requires its own approved arrangements |

**Architectural principle:** separate clinical records, communication eligibility, message scheduling, and operational follow-up so that one cannot incorrectly determine another. An unanswered SMS must never imply a missed appointment or a pregnancy outcome.

## 2. System architecture

```text
VHTs / Healthcare workers / Facility administrators
Clinical reviewers / Programme analysts / Zest operators
                              |
                            HTTPS
                              |
                    React staff application
                              |
                       Flask REST API
                              |
       +----------------------+----------------------+
       |                      |                      |
Identity & assignments   Maternal care      Communication services
Facilities & transfers   Consent & ANC      Content & follow-up
       |                      |                      |
       +----------------------+----------------------+
                              |
                         PostgreSQL
                              |
                    Durable dispatch records
                              |
                   Supervised Python worker
                              |
                  Africa's Talking adapter
                              |
                  Africa's Talking sandbox
                              |
                  Inbound / delivery callbacks
                              |
                         Flask API
```

### Technology choices

| Component | Proposed technology |
| --- | --- |
| Staff interface | React with TypeScript |
| API | Python Flask, application factory and modular blueprints |
| Validation/API contracts | Consistent request schemas and OpenAPI documentation |
| Database access | SQLAlchemy |
| Migrations | Alembic |
| Database | PostgreSQL |
| Scheduling | Database-backed occurrences and polling worker |
| Worker operation | Separate supervised process with health monitoring |
| Authentication | Individual staff accounts and secure server-side sessions |
| SMS provider | Africa's Talking behind a provider adapter |
| Testing | Backend unit/integration tests and selected browser-based workflow tests |
| Hosting | Low-cost managed application hosting and managed PostgreSQL |

Serve the frontend and API under the same origin where practical, simplifying secure cookies and browser security controls.

### Initial deployment units

1. React static application.
2. Flask API.
3. Python messaging worker.
4. PostgreSQL.

The worker must run on hosting that supports reliable background processes. Hosting that sleeps or suspends scheduled work is unsuitable.

### Backend modules

- `identity`: accounts, authentication, recovery, permissions.
- `facilities`: facilities, memberships, teams.
- `assignments`: participant responsibility and controlled transfers.
- `patients`: minimal identity, contacts, preferences.
- `pregnancies`: dating, episode state, completion.
- `consent`: notices, evidence, consent events, withdrawal.
- `appointments`: reference schedules, confirmed bookings, attendance.
- `content`: templates, translations, reviews, publication.
- `messaging`: occurrences, dispatch, attempts, callbacks, spending controls.
- `inbound`: reply ingestion and interpretation.
- `follow_up`: cases, escalation, acknowledgement, actions.
- `reporting`: dashboards and aggregate indicators.
- `audit`: sensitive access and significant changes.

Keep business rules in service functions shared by the API and worker. Neither interface should bypass consent, authorization, or content rules.

## 3. Core domain and state model

### 3.1 Patient versus pregnancy episode

- A **patient** represents the participant.
- A **pregnancy episode** represents one pregnancy.
- Multiple episodes may belong to the same patient over time.
- A telephone number is a contact endpoint, not a patient identifier.

Use an opaque internal patient ID and facility-local references. Do not expose a globally searchable patient directory to facility users.

### 3.2 Clinical state versus communication state

Maintain these separately.

**Clinical episode lifecycle**

- Ongoing.
- Completed, with an authorized completion record.

Any additional clinical lifecycle values require clinical approval.

**Communication state required by the specification**

| State | Meaning |
| --- | --- |
| Active | May receive messages if all other eligibility checks pass |
| Paused | Temporarily suppressed; not necessarily a consent withdrawal |
| Completed | Antenatal messaging ended following confirmed pregnancy completion |
| Withdrawn | Participation or messaging withdrawn under the recorded scope |

Active communication state alone is insufficient to authorize a send.

**Dispatch eligibility requires all relevant conditions:**

- Valid consent for the message purpose.
- Active communication state.
- Eligible contact endpoint.
- Appropriate approved language/content.
- Permitted sending window.
- Valid appointment confirmation, where applicable.
- No applicable phone suppression.
- No facility/global pause.
- Available expenditure allowance.

Resuming after withdrawal requires a new valid consent event; changing a communication-state field alone must not restore eligibility.

### 3.3 Appointment state

Support all required statuses:

`scheduled`, `confirmed`, `attended`, `missed`, `cancelled`, `rescheduled`

Recommended behavior:

- A reference ANC schedule suggests contacts but does not create confirmed bookings.
- Only a facility-authorized confirmation makes an appointment reminder-eligible.
- Rescheduling preserves the old booking history and invalidates its reminders.
- A patient SMS response is recorded separately from clinical attendance.
- "Missed" depends on an approved attendance window and staff workflow, not SMS delivery.

### 3.4 Pregnancy completion

An authorized clinical user records completion and the minimum approved delivery/outcome information.

Completion must:

- Stop future antenatal educational messages.
- Cancel remaining ANC reminders.
- Close or review related follow-up cases as appropriate.
- Preserve historical records.
- Avoid automatic postnatal enrolment.

A communication withdrawal, unanswered message, or passed EDD must not automatically create a clinical outcome.

## 4. Stakeholder dashboards and permissions

Use **role-based permissions plus facility and patient-assignment scope**.

A role defines what someone may do. Assignments define whose records they may access.

### Dashboard design

| Stakeholder | Dashboard and permitted work |
| --- | --- |
| Pregnant woman | No web account in the MVP. Receives approved messages and uses supported reply/withdrawal channels |
| VHT / community health worker | Assigned participants, registration, consent capture, contact preferences, follow-up tasks, permitted appointment information |
| Midwife / healthcare worker | Assigned patients or care-team workload, dating corrections, confirmed appointments, attendance, completion, clinical follow-up |
| Facility administrator | Facility accounts, teams, assignments, operational dashboards and authorized reports |
| Clinical content reviewer | English/Luganda templates, versions, approval decisions, retirement and correction; no patient access |
| Zest platform administrator | Facility configuration, service health, provider configuration, access administration, central pause; no unrestricted clinical access |
| Programme manager / analyst | Approved aggregate indicators, trends and comparisons without unrestricted patient records |

### Key permission boundaries

| Capability | VHT | Healthcare worker | Facility admin | Content reviewer | Platform admin | Analyst |
| --- | --- | --- | --- | --- | --- | --- |
| Register adult participant | Assigned scope | Assigned scope | Only with care role | No | No | No |
| Record consent/withdrawal | Assigned scope | Assigned scope | Only with care role | No | No | No |
| Correct EDD | No | Explicit clinical permission | Only with clinical permission | No | No | No |
| Confirm ANC booking | No by default | Explicit permission | Only with care role | No | No | No |
| Record attendance/completion | Restricted by duty | Assigned scope | Only with care role | No | No | No |
| Manage participant assignments | No | Delegated permission | Own facility | No | Technical administration only | No |
| Approve clinical content | No | Only with reviewer role | No | Yes | No | No |
| View identifiable follow-up cases | Assigned cases | Assigned/team cases | Only with care role | No | No by default | No |
| View aggregate reports | Limited assigned work | Authorized scope | Own facility | Content metrics | Service metrics | Approved programme scope |

Every account receives explicit permissions. No shared staff accounts.

### Enforcement

- Backend authorization on every protected operation.
- Database-level facility isolation as defense in depth.
- Patient/team-assignment checks for identifiable clinical information.
- Field-level restrictions where roles need different views of the same record.
- Auditing of sensitive reads, permission changes, exports, and record changes.
- Immediate membership/account deactivation support.
- Content authors cannot approve their own versions.

Facility administrators may manage access but do not automatically receive clinical-record permissions. Permission delegation must not permit an administrator to grant privileges beyond their authorized delegation scope.

## 5. Database design

Use UUIDs, timezone-aware timestamps, explicit foreign keys, record versions, and migration-controlled schema changes.

### 5.1 Principal tables

| Area | Tables and important attributes |
| --- | --- |
| Facilities | `facilities`: name, code, timezone, active status, messaging settings |
| Identity | `users`, `roles`, `permissions`, `role_permissions`, `user_permission_grants` |
| Memberships | `facility_memberships`, `care_teams`, `team_memberships` |
| Patient identity | `patients`: opaque identity and minimum approved attributes |
| Local profiles | `facility_patient_profiles`: local reference and facility-owned demographic/contact view |
| Contacts | `contact_endpoints`, `patient_contact_links`, `communication_preferences` |
| Eligibility | Adult-eligibility verification method, date, actor, and minimum necessary evidence |
| Pregnancy | `pregnancy_episodes`, `pregnancy_dating_revisions`, `pregnancy_completion_records` |
| Responsibility | `episode_facility_assignments`, `participant_assignments` |
| Transfers | `facility_transfers`, `transfer_handover_records` |
| Consent | `consent_notice_versions`, `consent_events`, protected evidence references |
| ANC guidance | `anc_reference_schedule_versions`, `anc_reference_contacts` |
| Appointments | `appointments`, `appointment_events`, `appointment_patient_responses` |
| Content | `message_templates`, `template_versions`, `clinical_approvals` |
| Scheduling | `message_occurrences`, `message_dispatches`, `dispatch_attempts` |
| Provider events | `sms_delivery_events` |
| Inbound | `inbound_messages`, `inbound_processing_events`, candidate matches |
| Follow-up | `follow_up_cases`, `case_assignments`, `case_actions` |
| Suppression | `contact_suppressions`, patient/episode communication restrictions |
| Cost control | `messaging_budgets`, `cost_reservations`, `provider_cost_entries` |
| Audit | `audit_events` |

### 5.2 Consent events

Record:

- Patient and applicable episode.
- Consent scope and decision.
- Notice version.
- Language.
- Method.
- Recording actor.
- Event time.
- Verification evidence reference.
- Withdrawal or update details where applicable.

Events are append-only. Corrections create new events rather than erasing the original evidence.

SMS consent and authorization to retain clinical records are separate matters. Withdrawal must not trigger arbitrary deletion of legally retained records.

### 5.3 Content and translation records

Each approved version records:

- Message purpose.
- Gestational week or trigger.
- Language: English or Luganda.
- Exact text.
- Author and translation provenance.
- Clinical approval.
- Effective dates.
- Encoding/segment preview.
- Retirement or suspension state.

English approval must not automatically approve a Luganda translation.

### 5.4 Occurrence and dispatch separation

Use two related entities:

- **Message occurrence:** the logical intended message, such as a particular pregnancy week.
- **Message dispatch:** the durable execution record for sending that occurrence.

An occurrence may be skipped, cancelled, or held without creating another logical occurrence. Multiple transmission attempts belong to the same dispatch record.

Examples of occurrence identifiers:

- Weekly education: episode + programme + gestational week.
- Appointment reminder: appointment booking/version + reminder type.

Database uniqueness prevents duplicate dispatch records for repeated scheduler runs. It does not guarantee exactly-once delivery by the telecom provider.

### 5.5 Contact privacy

- Encrypt phone numbers and sensitive contact data.
- Use a keyed lookup value for matching normalized numbers.
- Do not impose global uniqueness on phone numbers.
- Record whether a phone is personal or shared.
- Do not expose another participant's identity when a number matches multiple records.

### 5.6 Tenant isolation and transfers

Clinical activities must retain their owning facility.

A transfer must not make all historical source-facility records automatically visible to the destination.

Use:

- One current facility assignment per active episode.
- Facility-owned clinical activities.
- Explicit participant assignments.
- A minimum necessary handover record.
- Controlled receiving-facility acceptance.

Historical access by the source facility must be limited to approved retained records and duties. Source staff must not gain access to new destination-facility activity.

PostgreSQL row-level security should reinforce facility boundaries; service-layer checks enforce assignment and handover rules. Database connection context must be transaction-scoped and fail closed. The runtime database role must not bypass row-level security. Workers must also establish explicit authorized scope.

Use foreign keys and appropriate composite constraints to prevent facility-owned child records from referencing an unrelated facility's parent records.

## 6. Facility-transfer workflow

1. An authorized source-facility user initiates the transfer.
2. Confirm participant agreement and the approved information-sharing basis.
3. Prepare the minimum necessary handover information.
4. A designated destination user accepts or rejects it.
5. On acceptance, transactionally:
   - End the previous current facility assignment.
   - Create the new assignment.
   - Update responsible staff/team assignments.
   - Invalidate pending reminders tied to the old facility.
   - Revalidate future educational messages, consent, contacts, and sender context.
6. Preserve the same pregnancy episode identity.
7. Audit initiation, access, decision, handover, and reassignment.

Recommended behavior while transfer acceptance is pending: temporarily hold affected outbound messages and make the reason visible to authorized staff. Rejection or cancellation restores eligibility after revalidation.

A transfer does not automatically confirm an appointment at the receiving hospital.

## 7. API structure

Use `/api/v1`, documented through OpenAPI.

### Main endpoint groups

| Area | Representative endpoints |
| --- | --- |
| Authentication | `/auth/login`, `/auth/logout`, `/auth/me`, password-recovery endpoints |
| Facilities/accounts | `/facilities`, `/memberships`, `/users`, `/teams` |
| Assignments | `/participant-assignments`, `/case-assignments` |
| Patients | `/patients`, `/patients/search`, `/patients/{id}/contacts` |
| Eligibility/consent | `/patients/{id}/eligibility`, `/patients/{id}/consent-events` |
| Pregnancy | `/pregnancy-episodes`, `/pregnancy-episodes/{id}/dating-revisions`, `/pregnancy-episodes/{id}/communication-state`, `/pregnancy-episodes/{id}/completion` |
| Transfers | `/transfers`, `/transfers/{id}/accept`, `/transfers/{id}/reject`, `/transfers/{id}/cancel` |
| ANC reference | `/anc-reference-schedules` |
| Appointments | `/appointments`, `/appointments/{id}/confirm`, `/appointments/{id}/reschedule`, `/appointments/{id}/attendance`, `/appointments/{id}/cancel` |
| Content | `/message-templates`, `/template-versions`, `/template-versions/{id}/approve`, `/template-versions/{id}/retire` |
| Messaging | `/message-occurrences`, `/dispatches`, authorized hold/retry actions |
| Inbound | `/inbound-messages`, `/inbound-messages/{id}/resolve` |
| Follow-up | `/follow-up-cases`, `/follow-up-cases/{id}/acknowledge`, `/follow-up-cases/{id}/actions`, `/follow-up-cases/{id}/close` |
| Reports | `/facilities/{id}/dashboard`, `/reports/...` |
| Controls | Messaging policy, budget, and central-pause endpoints |
| Provider callbacks | `/webhooks/africastalking/inbound`, `/webhooks/africastalking/delivery` |

### API requirements

- Validate permissions and record scope server-side.
- Use idempotency keys for important create operations.
- Use optimistic concurrency for contested updates.
- Use POST bodies for sensitive searches rather than placing phone numbers in URLs.
- Paginate lists and cap result sizes.
- Return consistent, non-sensitive error responses.
- Restrict bulk export separately from ordinary read access.
- Do not expose a general-purpose clinical SMS composition endpoint.

## 8. Outbound SMS workflows

### 8.1 Registration and eligibility

An authorized VHT or healthcare worker:

1. Confirms adult eligibility using the approved procedure.
2. Selects the participating facility.
3. Records minimum necessary information and safe-contact preferences.
4. Captures the approved consent statement and evidence.
5. Creates the pregnancy episode and assignments.
6. Records pregnancy dating where available.

If dating is insufficient, weekly gestational messaging remains on hold. Other eligible functions, such as reminders for a confirmed appointment, can operate under their own rules.

Minors cannot enter the live enrolment workflow until a separately approved pathway exists.

### 8.2 Weekly education

1. Determine gestational stage from the approved dating basis.
2. Resolve the applicable weekly occurrence.
3. Select an effective, clinically approved English or Luganda version.
4. Apply preferred delivery hours and privacy preferences.
5. Create the durable dispatch record.
6. Revalidate immediately before provider submission.
7. Record the provider response and subsequent delivery reports.

Rules:

- No historical backlog burst after late enrolment.
- No unapproved language fallback.
- No improvised content when a week lacks approval.
- EDD changes regenerate future eligibility without duplicating already submitted weekly occurrences.
- Retired or suspended versions cannot proceed to submission.
- Content replacements for unsent work preserve occurrence identity and an audit trail.
- Weekly coverage, pregnancy dating calculations, and programme start/end rules require clinical approval.

### 8.3 ANC reminders

Maintain a versioned reference schedule based on clinically approved WHO and Uganda Ministry of Health guidance.

The reference schedule supports planning; it does not create bookings.

Only **confirmed appointments** generate reminder dispatches. Configurable settings cover:

- Lead times.
- Sending windows.
- Reminder count limits.
- Expiry after the useful reminder window.

Rescheduling supersedes the old booking/reminders and creates the appropriate replacement work.

### 8.4 Worker reliability

The worker:

- Polls PostgreSQL for due work.
- Claims rows atomically, using row locking and leases.
- Rechecks current eligibility.
- Submits through the provider adapter.
- Persists outcomes.
- Recovers abandoned claims after interruption.

Do not run a scheduler independently inside every Flask web process.

Retries must distinguish:

| Condition | Handling |
| --- | --- |
| Clearly transient rejection | Bounded retry with backoff |
| Invalid recipient | Stop and create correction task |
| Expired reminder | Mark expired; do not send late |
| Consent/pause restriction | Suppress or hold according to policy |
| Unknown result after timeout | Reconciliation path; no blind resubmission |
| Repeated infrastructure failure | Alert and operational review |

### 8.5 Callback processing

- Persist and deduplicate provider events.
- Accept delayed and repeated callbacks safely.
- Keep submission status separate from reported delivery status.
- Prevent older events from incorrectly reversing a final state.
- Retain sanitized provider references for reconciliation.

### 8.6 Spending and pause controls

Support:

- Global outgoing-message pause.
- Facility-level pause.
- Daily/monthly budgets.
- Per-participant volume limits.
- Segment-count estimates.
- Cost reservations before submission.
- Reconciliation with provider charges where available.

Budget reservations must be atomic so concurrent workers cannot overspend the same remaining allowance.

Luganda text may change SMS encoding and segment counts. Preview the actual content and parameters rather than assuming one message equals one billable segment.

### 8.7 Sandbox boundaries

Sandbox testing validates request formatting, status mapping, callbacks, inbound workflows, and controlled failure handling. It does not prove production handset delivery, production pricing, sender-ID approval, or live network behavior.

Any later production integration requires a separate messaging-readiness review.

## 9. Inbound replies and withdrawal

### 9.1 Sandbox inbound pipeline

```text
Provider callback
      |
Persist and deduplicate
      |
Normalize and classify
      |
STOP / appointment reply / assistance / danger-sign test / unknown
      |
Safe action or restricted human review
```

Inbound records should retain processing status, classification version, candidate matches, resolution history, and assigned facility/case where safely established.

### 9.2 Safe participant identification

A phone-number match alone is insufficient to identify a pregnancy.

Use a combination of:

- Explicit message/appointment reference where supported.
- Outbound conversation context.
- Eligible candidate records.
- Approved verification or human review.

Ambiguous messages must not update attendance, appointments, pregnancy state, or another person's consent.

Do not expose candidate participant identities in an automated reply.

### 9.3 STOP handling

For recognized STOP requests:

1. Apply immediate suppression to the sending contact endpoint.
2. Cancel or hold pending messages to that endpoint.
3. Link a patient-level withdrawal only when attribution is reliable.
4. Route ambiguity to authorized review.

For shared phones, endpoint suppression may temporarily affect several records. This is safer than continuing health messages, but it must not falsely record that every linked participant withdrew.

Staff-recorded withdrawal must be available immediately, independently of SMS functionality.

Any acknowledgement or administrative reply must follow an approved policy; it must not become a route for continuing health messaging after withdrawal.

### 9.4 Appointment replies

Keep a patient's reply distinct from:

- Facility booking confirmation.
- Attendance confirmation.

For example, "YES" may record that an identified participant acknowledged a reminder. It must not automatically mark the appointment attended.

### 9.5 Live messaging boundary

- Real two-way SMS remains disabled until provider arrangements, cost, staffing, and operating procedures are approved.
- A one-way sender must not advertise a reply-to-STOP mechanism.
- A usable alternative withdrawal channel is mandatory before any one-way live pilot.

## 10. Follow-up and sandbox escalation

### Follow-up case types

- Appointment attendance follow-up.
- Assistance request.
- Uninterpretable reply.
- Ambiguous participant identity.
- Invalid contact.
- Consent deficiency.
- Pregnancy completion update required.
- Controlled danger-sign escalation test.

### Case lifecycle

`open → assigned → acknowledged → action recorded → resolved/closed`

Also support reassignment, reopening, and an overdue indicator based on the approved operating model.

A closed case must have a recorded disposition; acknowledging a case is not the same as resolving it.

### Danger-sign demonstration

Use a versioned, clinically reviewed keyword/rule set—not an AI diagnosis system.

In the sandbox:

- Detect configured test phrases.
- Record a suspected escalation event.
- Use approved emergency-guidance text where the test policy permits.
- Route to a restricted facility queue.
- Record acknowledgement, actions, and disposition.
- Test ambiguous, misleading, and unrecognized messages.

Keyword recognition is incomplete and must not be presented as reliable emergency triage.

**Live escalation remains disabled** until staffing, support hours, escalation responsibilities, verified contacts, and clinical protocols are approved. Passing sandbox tests alone does not activate it.

## 11. Dashboards and pilot measurement

### Facility dashboard

- Adult registrations and active episodes.
- Valid consents and consent deficiencies.
- Messaging eligibility and suppression reasons.
- Scheduled, submitted, delivered, failed, pending, and unknown messages.
- Estimated/reconciled SMS expenditure.
- Confirmed appointments and recorded attendance.
- Missed appointments requiring follow-up.
- Opt-outs.
- Unresolved inbound cases.
- Unacknowledged or unresolved escalation tests.
- Completed follow-ups.
- Pregnancy completions requiring record review.

### VHT and healthcare-worker dashboards

Show assigned workload rather than unrestricted facility lists:

- Participants needing contact.
- Upcoming confirmed appointments.
- Pending consent/contact corrections.
- Assigned reply cases.
- Overdue follow-up actions.

### Programme reporting

Provide appropriately aggregated indicators:

| Category | Measures |
| --- | --- |
| Operational | Enrolled/consented participants, dispatches, delivery reports, failures, opt-outs, cost per participant |
| Care coordination | Confirmed appointments, attendance, missed contacts, completed and unresolved follow-ups |
| Safety/privacy | Consent deficiencies, incorrect-recipient incidents, disclosure incidents, unhandled replies, unresolved escalation events |

Agree denominators, cutoff dates, and baseline methods before interpreting pilot outcomes.

Examples:

- Provider acceptance is not delivery.
- Delivery is not proof of reading.
- A reminder response is not attendance.
- Counts of consent events are not counts of currently consented participants.
- Transfers must not double-count the same participant in programme totals.

Cross-facility reports require explicit programme scope. Small-number disclosure controls should be agreed with the privacy lead.

## 12. Security, privacy, and recovery

### Authentication and authorization

- Strong password hashing, preferably Argon2id.
- Secure, HttpOnly session cookies and CSRF protection.
- Session expiry and revocation.
- Rate-limited login and recovery.
- Single-use, expiring password-recovery tokens.
- Recovery procedures that do not reveal whether an account exists.
- MFA for privileged operational and approval roles.
- Immediate account/membership deactivation.
- Explicit facility and assignment checks for API and worker operations.

### Data protection

- HTTPS.
- Encryption for sensitive database fields and backups.
- Secrets stored outside source code.
- No real patient data in development or sandbox demonstrations.
- No PHI, SMS bodies, passwords, or credentials in technical logs.
- Restricted access to inbound/outbound message bodies.
- Masked phone numbers in routine operational views.
- No sensitive browser persistent storage or offline cache in the initial MVP.
- Approved retention rules for records, consent evidence, messages, audit, and backups.

### Audit

Audit significant changes and sensitive access, including:

- Patient views and searches.
- Consent recording and withdrawal.
- Dating and completion corrections.
- Assignment and facility transfers.
- Clinical approvals.
- Follow-up actions.
- Dispatch controls and retries.
- Account and permission changes.
- Reports and exports where sensitive.

Audit metadata should identify the action and record reference without unnecessarily duplicating sensitive content. Ordinary application permissions must not permit audit-event modification or deletion.

### Provider endpoints

Validate payloads, rate-limit appropriately, and deduplicate events. Confirm Africa's Talking's supported callback-authentication mechanisms before selecting implementation controls.

Do not assume webhook signatures exist without verifying the provider contract.

### Privacy governance

Before live use, confirm responsibilities and requirements under applicable Ugandan data-protection law, including:

- Controller/processor arrangements.
- Facility agreements.
- Hosting location and cross-border processing.
- Adult eligibility verification.
- Consent and withdrawal evidence.
- Shared-phone safeguards.
- Incident handling.
- Retention and data-subject requests.

### Backups and restoration

- Automated encrypted backups.
- Point-in-time recovery where affordable.
- Restricted backup access.
- Documented restoration procedure.
- Restoration test before Gate B acceptance.
- Approved recovery targets before live operation.

Proposed live-pilot planning targets are **RPO ≤ 1 hour** and **RTO ≤ 4 hours**, subject to hosting capability and budget approval.

## 13. Non-functional implementation targets

| Requirement | Proposed implementation |
| --- | --- |
| Low bandwidth | Small frontend bundles, paginated lists, concise payloads, no mandatory large uploads |
| Ordinary mobile devices | Responsive forms, accessible controls, clear save/error states |
| Scheduling reliability | Database-backed work, leases, retry limits, reconciliation |
| Localization | Reviewed English/Luganda text and explicit language selection |
| Maintainability | Configurable templates, ANC schedules, reminder policies, and keyword rules |
| Operational visibility | Worker heartbeat, scheduling lag, failures, callback errors, budget alerts |
| Scalability | Indexed queries, bounded worker batches, safe concurrent claims |
| Cost efficiency | Small managed services; no initial microservices, Redis, or Celery |
| Availability | Hosting and support model agreed before pilot; no unsupported availability promise |
| Recovery | Verified backups and a tested restore procedure |

Queue infrastructure or separately scaled services should be introduced only if measurements justify them.

## 14. Phased development and stage gates

**Indicative effort:** approximately **10–12 weeks after Gate A**, assuming two engineers, part-time QA, and available clinical/product reviewers. This is a planning estimate, not a delivery commitment.

### Phase 0 — Gate A: specification and design approval

**Outputs**

- Agreed requirements baseline.
- Permission and assignment matrix.
- Minimum data dictionary.
- Workflow wireframes.
- Consent/evidence model.
- Clinical content ownership.
- Transfer policy.
- Messaging and cost policies.
- Acceptance test matrix.

**Exit:** explicit approval to begin implementation.

### Phase 1 — Foundation and access controls

**Estimate: 1–1.5 weeks**

- React/Flask foundations.
- PostgreSQL schema and migrations.
- Individual accounts and password recovery.
- Facilities, roles, teams, assignments.
- Audit foundation.
- Synthetic data setup.

**Exit:** facility and assignment isolation tests pass.

### Phase 2 — Registration, consent, pregnancy and transfer

**Estimate: 2 weeks**

- Adult eligibility.
- Patient/contact registration.
- Consent evidence and withdrawal.
- Dating history and communication states.
- Controlled transfers.
- Completion/outcome recording.

**Exit:** synthetic end-to-end enrolment, correction, transfer, and withdrawal workflows pass.

### Phase 3 — ANC and follow-up

**Estimate: 1–1.5 weeks**

- Versioned reference guidance.
- Facility-confirmed bookings.
- Rescheduling and attendance.
- Assigned follow-up queues.

**Exit:** reminders can only be planned for eligible confirmed appointments.

### Phase 4 — Content and outbound messaging

**Estimate: 2 weeks**

- English/Luganda versioning and approvals.
- Weekly occurrences.
- Persistent worker.
- Sandbox integration.
- Callbacks, retries, reconciliation.
- Budget limits and pause controls.

**Exit:** scheduler repeatability, approved-content enforcement, and withdrawal suppression pass.

### Phase 5 — Inbound and controlled escalation

**Estimate: 1–1.5 weeks**

- Sandbox inbound processing.
- STOP suppression.
- Appointment responses.
- Assistance and ambiguous-message queues.
- Controlled danger-sign tests.
- Acknowledgement/action tracking.

**Exit:** no unsafe automatic patient attribution; all supported scenarios reach the correct workflow.

### Phase 6 — Reporting, hardening and Gate B

**Estimate: 2 weeks**

- Facility/assigned-work dashboards.
- Aggregate indicators.
- Security and access tests.
- Worker interruption tests.
- Backup restoration.
- End-to-end demonstration.
- User guides and operations documentation.

**Exit:** all technical acceptance criteria pass with synthetic data and sandbox messaging.

### Subsequent gates

| Gate | Required decision |
| --- | --- |
| C — Clinical/regulatory | Qualified reviewers, approved content, facility agreements, privacy and processing arrangements |
| D — Operational readiness | Training, staffed workflows, withdrawal channels, incident procedures, budgets, verified support arrangements |
| E — Controlled live pilot | Explicit authorization after A–D, limited population, agreed monitoring |
| F — Expansion evaluation | Evidence-based decision on additional facilities, channels, languages, or postnatal functions |

Technical completion does not authorize real patient enrolment. Live two-way SMS and live escalation remain separately restricted even if a one-way pilot is approved.

## 15. Verification and requirements coverage

| Requirements | Principal verification |
| --- | --- |
| FR-01 Facility/staff management | Role, membership, assignment, deactivation, recovery, cross-facility isolation tests |
| FR-02 Registration/consent | Adult eligibility, complete consent evidence, declined consent, withdrawal tests |
| FR-03 Pregnancy episodes | Multiple episodes, dating revisions, scheduling recalculation, state/completion tests |
| FR-04 ANC | Reference-versus-confirmed booking distinction, statuses, late enrolment, rescheduling |
| FR-05 Content | Approval, translation, retirement, correction, dispatch blocking |
| FR-06 SMS | Unique occurrences, repeated scheduler runs, retries, callbacks, expenditure controls |
| FR-07 Opt-out | Staff withdrawal, sandbox STOP, shared-phone suppression, queued-work cancellation |
| FR-08 Two-way sandbox | Reply classification, ambiguity handling, appointment-response separation |
| FR-09 Escalation sandbox | Controlled phrases, guidance versioning, restricted queue, acknowledgement/action |
| FR-10 Reporting/audit | Dashboard reconciliation, access/change audit, aggregate access limits |

Additional failure scenarios should include:

- Worker termination before and after provider submission.
- Unknown provider result.
- Repeated and out-of-order callbacks.
- EDD correction during queued work.
- Consent withdrawal during dispatch processing.
- Shared-phone STOP.
- Transfer while reminders are pending.
- Suspended content already selected for an unsent dispatch.
- Concurrent workers approaching the budget limit.
- Restoration from backup.

A message already submitted to the provider may not be retractable. The system must record that boundary honestly rather than claim withdrawal can recall it.

## 16. Postnatal extensibility

Prepare for future expansion through:

- Stable patient identity across episodes.
- Pregnancy episodes separate from contact and consent history.
- Programme-aware templates and messaging policies.
- Explicit consent scopes.
- A pregnancy-completed domain event.
- Provider-independent messaging.
- Follow-up cases that can later reference additional episode types.

Do not implement newborn records, postnatal schedules, or postnatal messaging now. Do not treat pregnancy consent as automatic authorization for a later programme.

## 17. Decisions needed for Gate A

The specification resolves the major scope questions. The following operational details still need agreement:

1. **Registration dataset:** minimum identity fields and adult-verification evidence.
2. **Consent:** approved English/Luganda notices, evidence methods, and scope.
3. **Patient assignment:** VHT caseloads, clinical team coverage, and delegated duties.
4. **Clinical content:** reviewers, authors, translation approval, weekly coverage.
5. **ANC policy:** approved reference version, confirmation authority, reminder offsets, missed-contact rules.
6. **Transfers:** handover dataset, acceptance authority, historical access, and consent requirements.
7. **Follow-up:** case ownership, operating hours, acknowledgement expectations, and closure rules.
8. **Messaging:** preferred windows, volume limits, budget caps, shared-phone wording, and withdrawal channels.
9. **Outcome recording:** minimum delivery/completion fields and correction authority.
10. **Data governance:** hosting, retention, reporting disclosure, recovery targets, and incident ownership.

### Proposed approval

Approve this as the **Gate A technical baseline**, subject to the decisions above:

- Modular React/Flask/PostgreSQL architecture.
- PostgreSQL-backed supervised worker.
- Facility- and assignment-scoped access.
- Versioned consent and clinically approved English/Luganda content.
- Sandbox outbound, inbound, STOP, follow-up, and escalation workflows.
- Controlled facility transfers.
- Separate technical, clinical, operational, and live-pilot gates.

**No implementation, deployment, or live patient processing is authorized by this plan alone.**
