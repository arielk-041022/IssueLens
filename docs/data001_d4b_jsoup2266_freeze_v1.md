# DATA 001-D4-B: jsoup #2266 strict GT freeze v1

## Approval and release boundary

The current user authorized the independently reviewed single target in the
DATA 001-D4-B instruction. Approval is recorded on 2026-10-21; this is not an
assertion of an unspecified offline review time. The research lead's personal
name or third-party signature was not supplied. Codex is the AI compiler of
this record, not a separate human reviewer.

The user also reported ordinary Windows-user D4-A acceptance: exit 0,
5512 VERIFIED, zero UNVERIFIABLE/blocking issues, deterministic index and
metadata bytes. That statement is user-reported acceptance; this freeze also
independently rechecks the sealed D4-A files. No unspecified acceptance log
is invented as an external signed artifact.

Target approval permits CONFIRMED/FROZEN GT fields. These three new freeze
artifacts remain PENDING_ARTIFACT_ACCEPTANCE. Dataset split is UNASSIGNED and
localization_experiment_authorized / experiment_authorized remain false.

## Task and sole target

PRE_FIX_EXISTING_METHOD_LOCALIZATION, ANY_CONFIRMED_TARGET.
Repo: jhy/jsoup.
Pre-fix: 8086b1e4088eb586e0abf939af18cf2d4beb4acb.
Fix: 5d31cac5888b872daa54e09238feeaa8e4e435e7.
Snapshot: sha256:eca1ac73179f98c878c8f655be75ce153096456a852f97d006ee03e0b677c4f3.
File: src/main/java/org/jsoup/nodes/Document.java.
Method: java-source-v1:org.jsoup.nodes.Document#ensureXmlDecl().
Unique in the full catalog; AVAILABLE identity/location; range 328:5-337:5.
Seven method evidence occurrences are VERIFIED. No other target is accepted;
required_groups is empty. Task-message abbreviations are not replacement IDs.

The complete pre-fix tracked tree has 258 files (166 Java files in all), with
83 production Java files and 1581 indexed methods. Source identity covers the
complete src/main/java Java set, not tests or POM. D4-A preserves tree/blob
hashes, raw source bytes, canonical manifest and the index/metadata binding.

## Frozen localization input

TITLE_LF_LF_ORIGINAL_BODY_V1 means exactly title + two LF + original body.
No strip, translation, summarization, newline normalization or added trailing
newline is applied. The JSON original_body restores the exact UTF-8 bytes.
Only issue_text is a localization query; comments, patch, commit/fix information,
tests, method ID, GT, review and snapshot metadata are excluded from query input.
The source was archived on 2026-10-19; creation-time text is not guaranteed.
Body SHA-256: 583fad636144d04bf49d642d54f0300626233764dc1b40e6891f6cc1ee46242b.
Input SHA-256: 57f7170c9a4c898a11d64fec1d7a1a79fe67acd2483ab98cdf802eb56317aedb.

## Patch, test and review evidence

The archived patch states Fixes #2266 and changes ensureXmlDecl from
ensureChildNodes().get(0) to firstChild(). The createShell change is Javadoc.
The post-fix DocumentTest.charsetOnEmptyDoc at lines 437-446 asserts XML
declaration generation and HTML meta charset generation on empty documents.
Assertions were inspected, not executed locally. The original reported failure
was not independently reproduced in a pre/post behavioral run.

D3 proposed this existing method as a priority inspection target, separating it
from caller methods appearing in the stack trace. D4-A verified its full source
identity. The current user approved freezing that proposal. This is an
ACCEPTED_LOCALIZATION_TARGET, not ROOT_CAUSE_CONFIRMED (false). Source evidence
VERIFIED does not prove localization accuracy, fault causality or AST node type
independently; the Validator does not reparse an independent AST.

The archived patch and actual Git diff differ only in optional hunk headings
after @@. D4-A retained the initial failed exact-text comparison and verified
all paths, blob IDs, ranges, context and additions/deletions. No formal source,
patch, index or validation condition was edited to obtain a pass.

## Leakage, split and historical protection

The issue contains a stack trace with Document.ensureMetaCharsetElement and
Document.charset, plus API usage. It does not directly name the accepted helper
ensureXmlDecl. Internal-name/stack-trace leakage is explicitly present; no numeric
leakage grade is inferred from an unspecified grading rubric. Pretraining
contamination is not ruled out. No text was redacted to lower leakage.

Split is UNASSIGNED. No independence from other jsoup cases or time/commit
dependencies is asserted; grouping and repository/time checks precede splitting.
The known #2130/#2137 group remains unchanged in its existing files.

Only three new project files are created. #2130's frozen input/GT, the historical
six-case benchmark, original GT, implementation and D4-A artifacts are protected
by before/after hashes. The original D2 full inventory remains INCOMPLETE with
813 ACL-restricted fixtures; this task does not declare it globally PASS.

## Schema, audit and acceptance

Reuse D2's array benchmark, sidecar schema_version=1.0 and strict-localization-v1.
The sidecar binds exact benchmark bytes, an 83-entry canonical source manifest,
index and metadata hashes. Approval/input/leakage/release fields are descriptive
extensions. Existing strict audit validates the core schema and source identity;
the independent freeze checker validates the extra input/provenance/release data.

Audit only: expected tool_status=OK, evaluation_status=NOT_EVALUABLE,
denominators total/method/file=1/1/1, ranking_status=NOT_REQUESTED; localization
metric values remain null. This does not authorize or report any ranking result.
All new logs, protection manifests and acceptance tooling reside in a new TEMP
directory. Rejection requires retaining this version and creating an explicitly
approved successor; do not overwrite v1 or alter historical data.
