# metadata-localization-v1: request construction and reference validation

This version implements request preparation only. It has no CLI, predictor,
ranking, evaluation, authorization issuance, network operation, or file reader.
It does not change the existing experiment protocol, scorer, or Validator.
No real input or method catalog is loaded by this module or its tests.

## Public API

```python
request = build_metadata_request(case_metadata, catalog, bindings)
check = validate_metadata_request(request, independently_retained_bindings)
```

Both functions operate only on explicitly supplied dictionaries/lists and return
detached, JSON-serializable dictionaries. All invalid inputs raise
`MetadataRequestError` (a `ValueError`) with a fixed `reason_code`; no partial
request, fallback, filtered catalog, or error message containing input text is
returned. Neither function modifies inputs.

`validate_metadata_request` returns `status="VALID"` only for structural and
reference consistency. This is not an experimental eligibility decision.

## Trust and completeness boundary

`bindings` is an external reference, not a self-authenticating proof. Its original
catalog count and digest must be established independently before constructing a
request. Never derive replacement reference hashes from a modified request and
then call that validation. Validation compares embedded bindings to the explicit
external reference, then reconstructs and compares the complete request.

The module does not read an index, source file, report, or code file. Therefore it
does NOT prove that a supplied catalog was actually extracted from a particular
index, that a report was executed, or that the supplied file/code hashes describe
existing bytes. A future separately approved preparation controller must verify
those origins and byte bindings. Internally consistent invented references cannot
be distinguished from authentic references by these pure functions.

Completeness here means **all original indexed method records relative to the
external reference**, not all semantic Java methods in a repository. Enum,
anonymous-class and other indexer coverage limitations remain. No row is deleted
because it has no body or has unavailable source positions.

## Case metadata: exact fields

| Field | Rule |
|---|---|
| `case_id` | Nonempty canonical identifier, at most 128 characters |
| `repo_id` | Explicit `owner/repository`; no inference from paths |
| `source_snapshot_id` | `sha256:` plus canonical manifest SHA-256 |
| `issue_text` | Explicit frozen text, nonblank, at most 100,000 Unicode code points |
| `input_version_status` | `PRE_FIX_VERIFIED`, `PRE_FIX_UNVERIFIED`, or `POST_FIX_MODIFIED` |

Text is preserved exactly, including CRLF, whitespace and Unicode. No strip,
translation, Unicode normalization, or truncation occurs. Title/body assembly and
historical authenticity must be checked outside this module. Recording either
unverified or post-fix-modified text does not authorize using it in an experiment.
Case status, GT approval and held-out assignment are not changed or generated.

## Catalog: exact row fields

Each row contains the four identity fields `repo_id`, `source_snapshot_id`,
`file_path`, `method_id`, plus:

- `ordinal`: exact original zero-based list position; never repaired.
- `has_body`: Boolean, including `False` without exclusion.
- `identity_status`: must be `AVAILABLE` for every row.
- `location_status`: `AVAILABLE` or `UNAVAILABLE_MAPPING`, retained unchanged.
- `features`: exactly `name`, `class`, `signature`, `file`, `annotations`,
  `strings`, `calls`.

The first four features are strings; the last three are lists of strings. Their
original order, duplicates, empty strings, case, and contents are preserved.
`features.file` must exactly equal `file_path`. Paths must be canonical relative
`src/main/java/...java` paths, with no traversal, backslashes, URL suffixes or
Windows alternate streams. No path is normalized into an acceptable value.

All rows must belong to the same explicit repository and snapshot. Four-tuples
must be unique, and method IDs must also be unique throughout that catalog.
Method IDs must use `java-source-v1:` with a declaration signature matching the
features and a last owner type matching `features.class`. Nested types and
overloads remain distinct. These checks are declaration consistency, not AST or
Symbol Solver analysis.

Any missing/conflicting/unavailable identity rejects the **entire case**. No
surrogate method ID is created and no subset receives a successful request.
The original records remain with the caller for diagnosis. An empty catalog is
allowed only when the external reference also specifies zero rows; it does not
represent a successful prediction.

## External bindings: exact fields

| Field | Meaning |
|---|---|
| `schema_version` | `metadata-bindings-v1` |
| `protocol_sha256` | SHA-256 of exported `PROTOCOL` canonical JSON |
| `case_metadata_sha256` | Canonical JSON SHA-256 of the entire case object |
| `catalog_sha256`, `catalog_count` | Original full ordered catalog digest/count |
| `index_sha256`, `metadata_sha256` | External raw file byte digests |
| `source_manifest`, `source_manifest_sha256` | Full scoped file list and canonical digest |
| `source_scope` | Explicit module/repository scope below |
| `validator_report_sha256` | External raw report byte digest |
| `validator_summary` | Strict diagnostic projection below, not an authenticated report |
| `code_sha256` | Exact named external code byte digests below |

All hashes are 64 lowercase hexadecimal characters. Code binding keys are exactly
`Main.java`, `evidence_validator.py`, `retriever.py`, `bm25_baseline.py`, and
`metadata_experiment.py`. No scoring code is imported or executed to obtain them.

Each manifest entry has exactly `file_path` and `source_sha256`. Paths are unique
and already sorted; unsorted manifests are rejected rather than silently sorted.
The manifest may include files with no indexed methods. The snapshot ID is the
manifest canonical digest prefixed with `sha256:`. Changing the indexing root can
change relative paths and snapshot identity; it must not happen silently.

`source_scope` has exactly `kind`, `index_root_relative`, and
`production_source_roots`. The last field is exactly `["src/main/java"]`.
`REPOSITORY_PRODUCTION` requires root `.`; `CORE_MODULE` requires an explicit
canonical module-relative path such as `core`. A core-module scope is never called
full-repository coverage. References bind the scope; no selection is performed.

## Validator failure is preserved

This narrow protocol accepts only this diagnostic projection:

```json
{
  "report_status": "COMPLETE",
  "overall_verdict": "FAIL",
  "exit_code": 1,
  "index_integrity_status": "PASS",
  "blocking_issues": [
    {
      "file_path": "src/main/java/fiction/Blocked.java",
      "record_pointer": "/0",
      "stage": "POSITION",
      "status": "UNVERIFIABLE",
      "reason_code": "UNSUPPORTED_RAW_MAPPING"
    }
  ],
  "summary": {
    "files_total": 1,
    "methods_total": 0,
    "evidence_total": 0,
    "verified": 0,
    "unverifiable": 0,
    "failed": 0,
    "blocking_issues": 1
  }
}
```

This is an illustrative synthetic projection, NOT a claim of Validator execution.
Actual summary counts must agree with the supplied manifest/catalog and blockers;
all supplied evidence must remain unverifiable. Each blocker has a unique file and
original file pointer. Resolving a pointer to its source index file is an external
controller responsibility; a manifest's sorted order is not assumed to be the
index file order. The pointer must be syntactically valid and within file count.

Every method in a blocked file must retain `UNAVAILABLE_MAPPING`; every method
with that state must have a matching blocked file. Blocked files with no indexed
methods are allowed. This models the existing file-wide mapping rejection. PASS,
ABORTED, index integrity errors, stale/missing source or any other blocker are
rejected, not silently waived. Supporting different failure modes needs a new
review, not weaker validation in this version.

## Request and validation output

A built request has exactly:

```text
schema_version = metadata-request-v1
protocol_id = metadata-localization-v1
protocol_sha256
case_metadata
issue_text_sha256
catalog
catalog_count
bindings
assurance
request_sha256
```

`issue_text_sha256` hashes the exact UTF-8 text bytes. `request_sha256` hashes the
canonical entire request **excluding only its own field**. Key order has no effect;
array order always matters. Input arrays are never sorted. Canonical JSON uses
`ensure_ascii=False`, `sort_keys=True`, compact `(',', ':')` separators,
`allow_nan=False`, and strict UTF-8 encoding, without BOM/newline. File byte hashes
must not be confused with hashes of canonical JSON representations.

Both request and validation report always carry:

```json
{
  "binding_assurance": "CALLER_SUPPLIED_REFERENCE_ONLY",
  "feature_kind": "INDEX_FEATURES",
  "feature_verification_status": "UNVERIFIED",
  "evidence_validation_status": "FAIL",
  "source_evidence_status": "UNVERIFIABLE",
  "issue_relevance_status": "NOT_ASSESSED",
  "independent_ast_parse": false,
  "execution_authorized": false,
  "prediction_performed": false
}
```

The validation report also has `schema_version=metadata-request-validation-v1`,
`status=VALID`, request hash, catalog count, location-status counts and original
input-version status. It contains no ranking, metric, GT or candidate selection.
Changing assurance fields and recomputing a checksum does not make them valid.

All levels use exact field whitelists, including nested features, manifest,
validator projection and code bindings. GT targets, sidecars, patches, repair
tests, predictions, scores and metrics are not protocol fields. This does not
semantically inspect free issue/feature text or prove that a caller did not put
an answer inside text; input freezing and independent review remain necessary.

## Fixed capacity limits and failure behavior

Limits are protocol capacity limits, not tuning against real issue outcomes:

- At most 100,000 catalog rows and 100,000 manifest files.
- Issue text at most 100,000 Unicode code points.
- Identity strings at most 4,096; paths at most 1,024 code points.
- Each feature string at most 65,536 code points; each feature list at most 10,000
  items. No feature is shortened or deduplicated.
- Each canonical serialized object at most 64 MiB.
- Invalid Unicode surrogates, Boolean integers and non-JSON data are rejected.

Oversized or invalid inputs fail; no successful truncated request is returned.

## Acceptance and research boundary

Run from the project root with an existing Python environment:

```cmd
python -B -m unittest discover -s tests -p test_metadata_experiment.py -v
```

Tests use only in-memory fictional cases and reference hashes. They exercise full
catalog retention, unavailable mappings, overloads/nested types, external binding
checks, tampering, forbidden fields, exact text and hash rules, immutability and
denied file/network/environment/process operations.

No real dataset, GT or EXP001 artifact is read. Existing requests/authorizations
are neither accepted nor reused. There is no real execution mechanism in this
module, and no evidence authentication, root-cause assertion, sample approval or
held-out assignment. Future prediction and independent scoring need separately
approved tasks; the existing Validator FAIL and existing execution gates remain.
