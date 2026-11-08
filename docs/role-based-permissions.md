# Stakeholder dashboards and role-based permissions

> **DRAFT — Precise proposed defaults; facility duties/delegation require D04 approval. Synthetic patient data exclusively.**

## 1. Authorization formula

Allow an operation only if all are true:

1. Active authenticated account, completed required MFA, active session.
2. Active role binding contains the exact permission.
3. Binding scope includes the facility/programme resource.
4. Required active facility membership exists.
5. Patient/team/case assignment and field restrictions permit the record.
6. Record state permits the action and an audit event can persist.

Default is deny. List counts, search, exports, attachments, and background operations obey the same scope. A dashboard route or role name alone never authorizes data access.

## 2. Roles and dashboards

| Role | Dashboard | Default data boundary |
| --- | --- | --- |
| VHT | Assigned caseload, registration, consent, contact correction, assigned tasks | Assigned participants at a member facility |
| Healthcare worker (HCW) | Care-team patients, dating, bookings, attendance, completion, clinical cases | Individual assignment or approved active team assignment |
| Facility administrator (FA) | Accounts, memberships, assignments, facility operations | Own facility administration; no automatic identifiable clinical access |
| Content author (AUTHOR) | Template/translation drafts and submission | Authored/authorized content, no patient information |
| Clinical reviewer (REVIEWER) | Review queue, approval, suspension, retirement | Content only; independent from author |
| Zest platform administrator (PA) | Facility configuration, redacted service health, provider reconciliation | Platform operations; no automatic clinical/message-body access |
| Programme analyst/manager (ANALYST) | Authorized aggregate indicators | Explicit approved list of facilities; no patient drill-down |
| Participant | No web dashboard | Supported SMS/withdrawal channels only |

People may hold multiple approved roles. Combining author and reviewer roles never permits self-approval. Combining an administrator and care role grants only the care role's separately assigned patient access.

## 3. Scope notation

- **A:** assigned patient at member facility, including active authorized care-team assignment for HCW.
- **S:** individual participant assignment only (VHT default; team-wide clinical access is not implied).
- **F:** own active member facility, bounded administrative fields.
- **C:** case explicitly assigned to the user; HCW may use approved case-team assignment.
- **O:** own content draft or submitted version; published corpus may be read by authorized content roles.
- **R:** authorized review corpus; excludes own authored version for decisions.
- **P:** platform metadata/control, excluding patient identity, message body, and consent evidence.
- **G:** approved programme facility list with aggregate/disclosure rules.
- **—:** denied by default.

## 4. Permission matrix

Every cell is a proposed default grant plus scope, not an informal suggestion.

| Permission | VHT | HCW | FA | AUTHOR | REVIEWER | PA | ANALYST |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `facility.read` | F | F | F | — | — | P | G |
| `facility.manage` | — | — | — | — | — | P | — |
| `account.provision` | — | — | F | — | — | P | — |
| `membership.manage` | — | — | F | — | — | P | — |
| `role.delegate` | — | — | F | — | — | P | — |
| `team.manage` | — | — | F | — | — | — | — |
| `assignment.manage` | — | — | F | — | — | — | — |
| `patient.register` | F* | F* | — | — | — | — | — |
| `patient.read` | S | A | — | — | — | — | — |
| `patient.update` | S | A | — | — | — | — | — |
| `eligibility.record` | S | A | — | — | — | — | — |
| `contact.manage` | S | A | — | — | — | — | — |
| `consent.read` | S | A | — | — | — | — | — |
| `consent.record` | S | A | — | — | — | — | — |
| `consent.withdraw` | S | A | — | — | — | — | — |
| `pregnancy.create` | S | A | — | — | — | — | — |
| `pregnancy.read` | S | A | — | — | — | — | — |
| `pregnancy.date` | — | A | — | — | — | — | — |
| `communication.pause` | S | A | — | — | — | — | — |
| `communication.resume` | S | A | — | — | — | — | — |
| `pregnancy.complete` | — | A | — | — | — | — | — |
| `transfer.initiate` | — | A | — | — | — | — | — |
| `transfer.accept` | — | — | F** | — | — | — | — |
| `transfer.cancel` | — | A | F** | — | — | — | — |
| `anc_reference.read` | F | F | F | O | R | — | — |
| `appointment.read` | S | A | — | — | — | — | — |
| `appointment.schedule` | — | A | — | — | — | — | — |
| `appointment.confirm` | — | A | — | — | — | — | — |
| `appointment.reschedule` | — | A | — | — | — | — | — |
| `appointment.attend` | — | A | — | — | — | — | — |
| `appointment.cancel` | — | A | — | — | — | — | — |
| `content.read` | — | — | — | O | R | — | — |
| `content.author` | — | — | — | O | — | — | — |
| `content.submit` | — | — | — | O | — | — | — |
| `content.review` | — | — | — | — | R | — | — |
| `content.publish` | — | — | — | — | R | — | — |
| `content.suspend` | — | — | — | — | R | — | — |
| `message.read` | S | A | — | — | — | — | — |
| `message.ops` | — | — | F | — | — | P | — |
| `message.reconcile` | — | — | — | — | — | P | — |
| `messaging.control` | — | — | F | — | — | P | — |
| `inbound.read` | C | C | — | — | — | P*** | — |
| `inbound.resolve` | — | C | — | — | — | — | — |
| `case.read` | C | C | — | — | — | — | — |
| `case.act` | C | C | — | — | — | — | — |
| `case.assign` | — | — | F | — | — | — | — |
| `dashboard.read` | S | A | F | O | R | P | G |
| `report.aggregate` | — | — | F | — | — | P**** | G |
| `report.export` | — | — | — | — | — | — | — |
| `audit.read` | — | — | — | — | — | — | — |

`*` Registration is a narrow atomic flow: create a synthetic local profile, minimum contact/eligibility records, an initial pregnancy episode with facility responsibility, and an assignment to the registering user (or an authorized preselected team). It does not expose a global directory or let the registrant select arbitrary facilities/assignees. Dating remains a separately authorized clinical action.

`**` Draft proposal: destination FA accepts/rejects transfers and sees only an approved administrative handover subset. Clinical handover fields require a separately assigned care role or specifically delegated clinical receiver. Source FA may cancel pending transfers administratively. Exact authority is D04/D09; this draft is not evidence of facility approval.

`***` PA sees processing metadata, error category and counts only, never inbound body, sender, or candidate identities. Unresolved cross-facility ambiguity is handled by a restricted routing service and an explicitly approved human review arrangement (D10), not ordinary PA browsing.

`****` Platform aggregates are infrastructure/delivery health only. Programme clinical aggregates require a distinct approved analyst binding.

## 5. Field-level restrictions

| Resource | Permitted subset / restriction |
| --- | --- |
| Participant | VHT sees approved registration/contact and follow-up fields; clinical outcome details remain HCW-only unless D04 explicitly approves a narrower disclosure |
| EDD and completion | VHT read view may show messaging stage/communication completion, but cannot write clinical dating or outcomes |
| Assignments | FA sees opaque participant/local reference and responsibility metadata; no demographics, contact, consent evidence or clinical notes |
| Cases | VHT sees only explicitly delegated task fields; clinical-only notes are excluded; case assignment does not independently grant the whole patient chart |
| Dispatch | Care user sees assigned participant messaging history; operations sees redacted provider references/status/cost only |
| Inbound | Authorized case owner sees relevant resolved message; candidate sets spanning facilities are never exposed |
| Consent evidence | Care-role purpose-limited access; object references never become public download URLs |
| Reporting | Aggregates only with disclosure policy; no patient-level drill-down or bulk export by default |
| Audit | No default grant. A separately delegated auditor permission must specify facility/resource scope and read-purpose under D16 |

## 6. Delegation and account lifecycle

- FA may invite/manage facility users and bind only roles in an approved delegation allowlist; cannot grant platform/reviewer/auditor powers or self-elevate.
- PA may provision technical accounts/facilities, but cannot self-grant clinical or clinical-approval roles. Such bindings require an authorized approving authority recorded under D04/D16.
- A grant cannot exceed the grantor's delegation scope. A grantor's own role does not automatically imply delegation authority.
- Revoked memberships immediately invalidate care access; role/session caching must not postpone revocation.
- No shared accounts, public self-registration, direct audit modification, or blanket patient export.
- `report.export` and `audit.read` remain denied until explicit bounded delegation is documented. Their API contracts exist so the future grant cannot bypass security.

## 7. Workflow prerequisites

- Resume checks renewed consent and endpoint suppression; it is not a consent override.
- Transfer acceptance checks exact destination and pending state.
- Approval verifies reviewer/author separation and the version digest.
- Reconciliation cannot force-resend unknown provider work.
- Appointment acknowledgement cannot use `appointment.attend` through an SMS callback.
- Worker/service permissions are separate database/service identities, not staff dashboard roles.

All policy deviations require an explicit reviewed role-profile change and updated negative acceptance scenarios, not one-off frontend exceptions.
