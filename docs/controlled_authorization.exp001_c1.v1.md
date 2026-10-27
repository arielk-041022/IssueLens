# EXP001-C1-R1: controlled offline authorization

Development and synthetic acceptance ONLY. No real prediction is authorized and
no valid real permit is issued by this task. Default B1/B2/B3 fixture gates remain.
The three approved core edits affect admission/context transport only; Rule source,
BM25 mathematical implementation, B1 audit guard and strict aggregate are frozen.

## Authority and scope

`controlled_authorization.py` is the local authority and orchestration module.
`init_issuer` creates an owner-controlled HMAC registry outside the project. The
registry is the trust root, not the permit itself. It holds a random issuer ID,
256-bit HMAC key and initially EMPTY `real_approval_pins`. Do not publish a real
registry key. Synthetic fixtures retain their keys solely as test artifacts.

This is reproducibility/access discipline for trusted local research processes,
not third-party reviewer identity authentication, digital signatures by a named
person, or a sandbox against a malicious registry owner/native code. A same-user
attacker who rewrites trusted code or issuer storage is outside this threat model.
Hashes/keys/ledger files must remain protected and be included in local custody
records. No ACL, elevation, installation, network or external service is required.

Only `issue-synthetic` exists. It requires four distinct fixture IDs, a signed
qualification receipt and non-real source/input scope. It rejects jhy/jsoup,
known frozen source snapshots, known input fingerprints and jsoup source paths.
Renaming a real case to fixture-* does not authorize it. The default unpermitted
parent/worker/B3/BM25 paths also reject these real-data fingerprints.

The verifier understands REAL_OFFLINE_ONCE only if the exact canonical permit
body SHA-256 has been registered by a separately approved research authority in
the issuer's `real_approval_pins[run_id]`, AND all normal signatures/bindings pass.
Current registry initialization leaves this map empty and no provided command
adds real approval or issues a real permit. A future real issuance operation and
exact approved body/pin require explicit approval after C1 manual acceptance.
The C1 development instruction is never accepted as a permit or approval pin.

## Permit schema and hashes

Serialization is UTF-8 JSON, ensure_ascii=false, sort_keys=true, compact comma/
colon separators and allow_nan=false. Duplicate keys and NaN/Infinity are rejected.
The outer envelope has exactly `{body, mac, issuer_registry}`. `mac` is HMAC-SHA256
over canonical body bytes. CLI execution also requires the externally pinned SHA256
of the complete permit file. Known fields only; no authorized=True shortcut.

Body fields:

| Field | Meaning |
|---|---|
| version, scope, issuer_id, run_id | permit-v1; SYNTHETIC_ONLY or separately approved REAL_OFFLINE_ONCE |
| cases | Exactly four ordered case descriptors |
| systems | Exactly [Rule-only, BM25-only] |
| N, K | Integer 4 and 5; booleans rejected |
| runtime | Resolved executable, complete Python version and executable byte SHA256 |
| code_sha256 | Exact adapter, B3 bridge, BM25, authority, Rule, strict evaluator, Validator hashes |
| specification, specification_sha256 | Complete controlled protocol object and canonical SHA256; binds accepted BM25 SPEC digest |
| output | Exact new absolute campaign root; outside project, source and prepared-package roots |
| eligibility | Signed-stage sanitized four-case qualification facts, without any GT material |

Each case descriptor has exactly:
`case_id, repo_id, source_snapshot_id, input, repo, index, package,
input_file_sha256, issue_text_sha256, index_sha256, metadata_sha256,
source_manifest_sha256, catalog_sha256, request_sha256,
request_canonical_sha256, candidate_count`.

`source_manifest_sha256` is the canonical full production-source manifest digest,
not a pretty JSON file hash or Git HEAD. Parent and worker recompute it from the
bound index, hash every source byte and check the exact production Java file set.
They compare the request's full catalog to the actual index, preserving ordinal,
overloads and bodyless methods. Original frozen Issue text is revalidated with
B1's input contract; a rehashed changed query cannot retain the original input
binding. Input/repo/index paths must agree with the sanitized qualification facts.

The prediction permit contains NO GT target, sidecar path/hash, patch, regression
test or review material. Frozen experiment_authorized flags are never edited.

## Separate phases and commands

All examples are templates for synthetic data, not executable real permits.
For the future production workflow the phases MUST be separate OS processes.

1. `qualify-synthetic --issuer ... --cohort ... --out-dir NEW_DIR`
   invokes the existing B1 preflight CLI as a separate isolated subprocess. Only
   this stage accepts a GT-bearing cohort. It preserves audit logs and emits a
   sanitized HMAC eligibility receipt: four IDs, repo/snapshot, input/repo/index
   paths and input/index/metadata hashes, N/K. No GT names/paths/hashes leave it.
2. Run existing B1 prepare separately for four input/index/source bindings. No GT
   argument. Build four shared, hash-pinned requests with complete catalogs.
3. `issue-synthetic --issuer ... --run-id ... --eligibility ... --plan ...
   --output NEW_CAMPAIGN_ROOT --permit NEW_PERMIT_FILE`. The GT-free plan is an
   array of four `{input_path,index,repo,package}` descriptors. Issuance is outside
   the prediction phase and reserves run_id before writing the permit.
4. `execute --permit FILE --permit-sha256 EXPECTED_HASH` has NO GT/cohort option.
   It verifies all bindings before reserving execution and creates a fresh root.
   Exactly eight registered jobs run independently. Both systems use the same
   per-case request bytes; no Rule Top5 is passed to BM25 and no cross-fallback.
5. The controller preserves all statuses, raw worker logs, actual scores/timing,
   source/capsule/authorization bindings and eight results, then writes seal.json.
   Pin the seal file SHA256 outside the output before any scoring.
6. `evaluate --output SEALED_ROOT --seal-sha256 EXPECTED_HASH --cohort ...
   --out-dir NEW_EVALUATION_ROOT` is a separate GT-reading process. It checks
   exact sealed file sets/hashes, current code/source bindings and then reuses
   unchanged B1 strict aggregation for two independent N=4 reports.

In library tests fixtures and qualification may be prepared in one test process;
the actual prediction worker is always a new `-I -B` interpreter with B1's minimal
environment. The production CLI execute does not import a GT cohort or carry the
qualification process memory. A qualification audit report must never be passed
to the prediction worker; only its sanitized signed receipt is in the permit.

## Three-layer validation and replay prevention

Parent: B1 predict's optional authorization keyword validates the pinned permit
through the independent authority, including all four source bindings, runtime,
code, exact registered capsule and assigned output. It consumes an exclusive
parent claim for this case/system. With no context the original synthetic gates
still apply, plus real-data renaming detection. The old predict CLI remains
unprivileged; there is no global authorization boolean.

Worker: internal _worker may receive an authorization ticket file. Before installing
the unchanged B1 audit hook, it independently repeats disk validation and checks
the parent claim, then exclusively consumes its own case/system claim. Runtime
and code/source checks are not trusted merely because the parent passed. It builds
a process-bound HMAC context and installs the original guard before loading/scoring.

Producer: B3 and BM25 independently load the exact hash-pinned authority source
from the in-memory context and check its permit signature, complete request hash,
catalog/counts, runtime identity, system, output cwd and worker PID/proof. This
uses no post-guard reads outside B1's capabilities. A boolean, foreign verifier,
different request/system/process or unsigned context is rejected. The actual
authority source SHA256 is pinned in both production layers and in the permit.
Producer checks also require the context fingerprint registered in this process
by a successful disk-validated worker claim. A caller's chosen key and self-signed
dictionary alone are insufficient. This process-local capability assumes trusted
framework code; malicious code rewriting sys globals remains outside the stated
same-owner threat model, just like monkey-patching Python itself.

Ledger files are exclusively created with xb: issuer/run_id issuance reservation,
campaign started reservation and eight parent/eight worker job claims. Claims
remain consumed on failure or timeout; there are no automatic retries or lease
reclamation. A run_id is unique within its trusted issuer namespace; switching
registry is a new authority, not permission to reuse an old campaign. The exact
output binding and started marker prevent moving/replaying the same permit.
Do not remove ledger files. Recovery/retries require separately approved policy.

The HMAC key stays in issuer storage and ephemeral worker context, never in
request, prediction, ticket, campaign output or telemetry. Tickets bind only the
permit path/hash, run_id, case_id and system. The public permit is copied into the
sealed campaign so later evaluation can revalidate it even with failed jobs.

## Predictions, failures and sealing

Existing states SUCCESS, EMPTY, INVALID_PREDICTION and EXECUTION_ERROR remain.
Invalid authorization is never converted to an empty successful prediction.
Each authorized case/system has its own record and no use of the other system's
ranking as a replacement. Timing, raw stdout/stderr and worker exits remain in
the existing artifacts. Campaign report_status=COMPLETE means processing finished;
overall_verdict=FAIL records job failures. Execute CLI returns nonzero if jobs fail
but still prints the seal hash so independent failure-aware scoring remains possible.

File creation is exclusive and every command requires a new output directory.
Seal lists exact output files and byte hashes; evaluator rejects additions,
deletions, modifications or path escapes and does not write inside the sealed root.
This is tamper detection against an independently pinned seal, NOT OS immutable
storage or an ACL-based write lock; no ACL changes are made.

All four case slots stay in each system's report. Any missing/error/invalid case
makes that system INVALID_RUN/null authoritative metrics. Other system results
remain independent. Legal completed empty predictions are misses with denominator4.
Legacy unreranked transport is still named stage=rule; explicit system and exact
producer/source pins distinguish Rule-only from BM25-only. Metric semantics are
not changed to fix old labels. N4/K5, no tuning and no independent test set remain.

## Algorithm and historical compatibility

retriever.py is byte-for-byte unchanged. Rule still calls the original functions.
BM25 tokenize/document/score_tokens and predict's scoring/order body are AST-
identical to the B2 backup. Only optional authorization admission is added.
BM25's frozen SPEC digest is unchanged. B1 _install_worker_guard, validate_result,
_read_prediction, aggregate and summarize are unchanged. The R1 cold-cache logic
is unchanged. Full before/after diffs and structural invariance report are part of
the task handoff. Current BM25 file SHA256 necessarily changes with its entry gate;
B3 source pins and temporary capsules are updated, never labelled with old hashes.

Historical B1/B2/B3 tests are not edited. Acceptance requires C1 plus 39/55/57 old
tests with zero failures AND zero skips under the supplied Python3.10.11/Jieba0.42.1.
Agent runtime dependency skips mean PENDING, not PASS. New tests include signed
scope/hash tampering, wrong run, replay/claims, worker and producer verification,
GT/write/network denial, original input binding, renamed-real refusal, full
synthetic eight-job sealing/replay and fixed-four failure aggregation. No test
runs real data or issues a valid real permit.

## Manual acceptance, protection and limits

Use the task-local accept.cmd. It selects the ordinary-user installed Python310,
performs read-only dependency checks, runs all suites with skip-rejecting exit
rules, verifies protected hashes/algorithm invariants and seals new random TEMP
logs/fixtures. Retain all failures; do not alter ACL or install packages to hide
environment issues. The three edited core files have recoverable byte backups.
Rollback: archive current changed/new files, restore only the three approved
backups and verify their original hashes; archive the new authority/tests/doc.
Do not git reset or restore any GT/data/history file. Preserve issuer test records.

The accepted real-data manifests/GT and historical predictions/logs are not changed.
Original D2 full protection remains INCOMPLETE for 813 inaccessible test fixtures;
this scoped protection does not upgrade it. Before real experiments, C1 artifacts
must pass manual acceptance and the research lead must separately approve exact
real run/permit/issuer/output/hash bindings. No such approval, permit issuance,
real ranking, LLM integration, paid API or later task is performed here.
