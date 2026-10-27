# EXP001-C2-A: minimal real issuance

This supplements C1's issuance-only limitation. All scoring, BM25 SPEC, worker
guard, GT separation, N=4/K=5 and one-use claims remain unchanged. No genuine
approval or valid four-issue permit is created during development/acceptance.

## Commands

`qualify-real --issuer DIR --cohort FILE --out-dir NEW_DIR --approval FILE`

Requires Python 3.10 and exactly jsoup-2130/2266/2068/2078. The approval binds the
byte SHA256 of the GT-bearing cohort. Runs existing B1 preflight in a separate
`-I -B` process. Emits the existing sanitized signed eligibility format; it
contains no GT paths, targets, hashes, patches or approval-record text.

`issue-real --issuer DIR --run-id ID --eligibility FILE --plan FILE
--output NEW_CAMPAIGN_DIR --permit NEW_FILE --approval FILE`

The GT-free plan remains four `{input_path,index,repo,package}` records. Each
case is checked against its own known snapshot, not any snapshot in the set.
The permit body is deterministically proposed by the read-only `real_body`
function using `describe` records, the signed eligibility and current runtime,
code, protocol and output bindings. This proposal is NOT a signed permit and
does not grant execution. Review and approve its exact canonical SHA256 before
issuance. Subsequent code/runtime/request changes require a new proposal/review.

Use the ordinary user's existing Python310 executable. No dependency installation
or new identity/permission framework is involved.

## Separately supplied research approval

Neither command creates or registers research approval. The trusted issuer
custodian must provision approval materials only after a new explicit research
lead decision. This task's development approval is not such a decision.

At `ISSUER/research-approval-pins.json`, store the independently reviewed approval
file's SHA256 under `QUALIFY_REAL` or `ISSUE_REAL`. This is trusted local issuer
storage, not a CLI flag or a hash supplied by the requester. No command is
provided to create/install these pins or human approvals.

The approval file is `{body,mac}`, HMAC-SHA256 signed by the existing issuer key.
Body has exactly:

* version: `research-approval-v1`
* issuer_id: the existing issuer's identity
* role: `RESEARCH_LEAD`
* decision: `APPROVED`
* purpose: `QUALIFY_REAL` or `ISSUE_REAL`
* subject_sha256: cohort bytes hash or proposed canonical permit-body hash
* record: actual explicit human decision/custody reference (at least 20 characters)

The trusted custodian is responsible for authenticity of the human decision;
role/text fields alone do not establish it. A file without a trusted pin, an
invalid MAC, wrong subject/issuer/role/purpose or a boolean is rejected. This is
C1's owner-managed HMAC trust boundary, not third-party signature authentication
or protection against a malicious owner rewriting registry/code. Never expose
the issuer key in predictions, logs or approval documents.

Issuance reserves run_id exclusively, records its exact approved body pin and
verifies all disk/request/source/catalog bindings through existing verify_disk.
Only successful issuance returns the final permit SHA. Binding failure revokes
the registered body pin and retains a `.REJECTED` file; the run_id stays consumed.
No automatic retry or ledger deletion is allowed. Permit output is exclusive;
there is no ranking in qualification or issuance.

## Later execution (not authorized here)

After issuance, use existing `execute --permit ... --permit-sha256 ...`, pin the
resulting seal externally, then a separate `evaluate --output ...
--seal-sha256 ... --cohort ... --out-dir NEW_DIR` process. Only qualification and
evaluation read GT. Eight predictions use the same per-case request and complete
catalog; failures remain in each system's fixed N=4 denominator.

## Acceptance and rollback

New tests use only fixtures. A positive issuance test patches the real universe
to fixture IDs/body; its artifacts cannot pass unpatched production real-scope
checks. It never authorizes the actual four issues. Ordinary Python310 acceptance
must have zero failures/skips; agent Jieba skips mean PENDING.

Restore the three changed source files from the C2-A byte backups and verify
before hashes; archive this new test/doc. Preserve logs and rejected records.
Do not restore or modify any Issue/GT/index/source/history file.
