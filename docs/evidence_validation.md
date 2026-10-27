# TASK 002: independent source evidence validator

Publication note (2026-10-27): personal machine paths below are placeholders. Historical results are unchanged; original experiment inputs, hashes and raw logs were not edited. Substitute your own existing paths when following command examples.

## Scope and changes

Only three project files are added: `src/evidence_validator.py`,
`tests/test_evidence_validator.py`, and this document. TASK 001 Java code/schema,
retriever, reranker, verifier, metrics, benchmark, ground truth and historical
reports are unchanged. Python 3.10+ standard library only; no installation, Java,
Maven, LLM API or network is needed to run validation.

`VERIFIED` means the index binding, safe path, exact source bytes, declared identity
and ownership, UTF-16 source range, source snippet and TASK 001 evidence ID match.
It is NOT independent AST syntax verification. The evidence type comes from the
TASK 001 JavaParser index. The validator does not reparse Java or prove that the
substring is really an annotation/string/call AST node. Method identity checks are
declared signature/container consistency, not independently resolved declarations,
semantic call targets, execution traces, root causes, fixes or LLM reasoning.

This limitation is machine-readable in `verification_scope`:

```json
{
  "checks": ["INDEX_BINDING", "PATH", "SOURCE_BYTES", "DECLARED_OWNERSHIP", "UTF16_RANGE", "EXACT_SNIPPET", "EVIDENCE_ID"],
  "independent_ast_parse": false,
  "evidence_type_origin": "TASK001_JAVAPARSER_INDEX_DECLARATION",
  "method_identity_scope": "DECLARED_SIGNATURE_CONSISTENCY_NOT_SEMANTIC_RESOLUTION"
}
```

## Interface and report schema

```text
python -B src/evidence_validator.py --repo ROOT --index INDEX [--meta META] [--out NEW_REPORT]
```

Metadata defaults to the complete index filename plus `.meta.json`. No --out means
JSON on stdout. With --out, a new report is exclusively created outside the target
repository; input files and existing reports cannot be overwritten. Progress and
output errors go to stderr. The index and source files are never written.

Public importable entry point: `validate(repo, index, meta=None) -> dict`.
It never writes a report by itself. CLI main uses its `exit_code`.

| Exit | Meaning |
| ---: | --- |
| 0 | COMPLETE/PASS; at least one evidence record, all checks passed |
| 1 | COMPLETE/FAIL; stale, missing, mismatched, invalid range, identity/evidence failure or unverifiable input |
| 2 | Integrity/path error, ABORTED execution, argument error or output write error |

Report fields:

| Field | Type and meaning |
| --- | --- |
| report_schema_version / validator_version | string, both `1.0` |
| verification_scope | object above, explicit absence of independent AST parse |
| report_status | `COMPLETE` or `ABORTED` |
| overall_verdict | `PASS` or `FAIL` |
| exit_code | integer 0, 1 or 2 |
| inputs | resolved repo root, input paths, actual main/metadata hashes, schema and position convention when readable |
| index_integrity | status plus expected and actual main hash when available |
| summary | file/method/evidence totals, verified, failed, unverifiable, evidence_status_counts, blocking_issues |
| file_results | JSON Pointer, path, source status, expected/actual byte hashes when readable |
| method_results | JSON Pointer, method_id, declared ownership/range availability status |
| evidence_results | JSON Pointer, original ID/type/path, method_id, range, status, reason_code and basis |
| issues | stage, status, reason_code, detail, record_pointer |

Every evidence result has a JSON Pointer such as
`/0/classes/0/methods/0/evidence_occurrences/0`. Results are lists, not maps keyed by
IDs: duplicated or missing identities cannot silently overwrite a result.
`basis` records the computed evidence ID, source hashes, declared/extracted snippet
hashes, exact extracted snippet and passed checks when reached. These refer back
to the input hashes in the report header and allow reproduction.

`COMPLETE` means the validator completed its handling of an input, including a
deliberate early refusal. Invalid input, illegal path, stale source and ordinary
verification failures are COMPLETE/FAIL. `ABORTED` is reserved for unexpected
execution exceptions or caught keyboard interruption, always FAIL. It does not
mean that finding a validation error interrupted execution abnormally.

Expected failures still generate JSON when --out is a safe, writable, new path.
An invalid/unwritable/existing output destination produces OUTPUT_ERROR on stderr
and exit 2. CLI argument parsing errors use argparse stderr/exit 2. A forced process
termination or OS crash cannot be guaranteed to produce a report.

## States and fail-closed policy

| Evidence state | Meaning |
| --- | --- |
| VERIFIED | Every prerequisite and evidence check passed, and no batch fault exists |
| STALE_SOURCE | Actual source byte hash differs; old ranges are not interpreted |
| INVALID_RANGE | Provided range has invalid types, ordering, bounds or UTF-16 endpoints |
| SNIPPET_MISMATCH | Valid range on the matching source bytes does not yield the declared snippet |
| MISSING_SOURCE | Safe relative path has no source file |
| INDEX_INTEGRITY_ERROR | Reported as a batch issue for invalid JSON/schema/binding/counts |
| INVALID_PATH | Illegal, escaping, aliased/reparse path, unsupported root or wrong file ownership |
| EVIDENCE_ID_MISMATCH | Recomputed TASK 001 evidence ID differs |
| DUPLICATE_EVIDENCE_ID | All copies of a repeated evidence ID are rejected |
| OWNERSHIP_ERROR | Evidence outside method range or declared method/container identity mismatch |
| UNVERIFIABLE | Legacy/no evidence, unsupported configuration/type, missing location/hash, parse/identity/mapping unavailable, read failure, or batch suppression |

Issues include explicit stages: REPOSITORY, INDEX_READ, METADATA_READ, SCHEMA,
INTEGRITY, CONFIGURATION, PATH, SOURCE, PARSE, POSITION, OWNERSHIP, RANGE,
IDENTIFIER, SNIPPET, EVIDENCE and EXECUTION. Each has a specific reason_code and
detail; e.g. INDEX_HASH_MISMATCH, EVIDENCE_OUTSIDE_METHOD, METHOD_ID_CONFLICT,
EXACT_SNIPPET_MISMATCH or SOURCE_CHANGED_DURING_READ.

The policy is batch-wide: **any blocking issue means zero VERIFIED records**.
Otherwise successful evidence becomes UNVERIFIABLE/BATCH_FAIL_CLOSED. Its
intermediate basis may remain for diagnostics, but is not a verification grant.
Records that cannot be reached after an early input gate are UNVERIFIABLE with
PRECONDITION_FAILED. If JSON is unreadable there may be no safely enumerated
evidence rows; the report-level issue still expresses the failure.

Source hashes can match for methods with no occurrences; those methods are marked
NO_EVIDENCE, never VERIFIED. An entire input without evidence cannot pass through
vacuous success. `evidence_omissions > 0` is a blocking completeness issue. There is
no proof that the index captured every possible AST node.

## Integrity and ID algorithm

Main SHA-256 is computed over the exact input bytes, not reserialized JSON. The
sidecar's hash, basename, schema, fixed TASK 001 parser settings, source roots,
counts and conflict statistics are checked. Duplicate JSON keys, NaN/Infinity,
unpaired surrogates, malformed field types and boolean counters are rejected.
Metadata schema is 2.0 and position convention is
`1-based-inclusive-UTF16-tab1`. Unsupported conventions fail closed.

SHA-256 binding is consistency, not authenticated provenance. A party able to
rewrite both index and metadata can rebind them. Source correspondence remains
separately checked; no signature or trusted publishing service is provided here.

Evidence ID reproduces Main.java's actual Jackson 2.18.2 serialization of:

```text
[file_path, source_sha256, method_id, evidence_type,
 start_line, start_column, end_line, end_column]
```

This is the original TASK 001 algorithm, not a replacement naming scheme. In
particular it uses the UTF-8 generator and DefaultPrettyPrinter array layout:

```text
[ "src/main/java/p/A.java", "...hash...", "...method...", "METHOD_CALL", 3, 5, 3, 13 ]
```

There is a space after `[` and before `]`, with comma-space separators, and no
array trailing newline. Strings use Jackson short escapes for quote/backslash/
backspace/formfeed/newline/CR/tab, uppercase hex for other control escapes, literal
BMP Unicode and uppercase surrogate-pair escapes for supplementary characters.
The result is UTF-8 encoded; evidence_id is `sha256:` plus lowercase SHA-256.
Python json.dumps defaults are not used for this calculation.

Three golden vectors were generated using the already compiled TASK 001 Main.JSON
and Main.sha256, without editing TASK 001 source. Tests pin their actual hashes:

```text
ASCII:       9f0dda2618de57b980401deec5d8864a9d3952998c873844e09bf082515284d3
Chinese/😀:  a47199eb731099d1a7e32866cdef1414301f8007d088e0c22212f045f4da6f5f
Escapes:     9b6ebb74102cd074f5fb6511d1e93db77ea8c39a14f7db037f56f887619f35e5
```

Each ID calculation uses the owning file hash/method ID, not untrusted alternate
values in the evidence. File path equality, method containment and duplicate-ID
checks are separate gates. ID matching alone cannot grant VERIFIED.

## Paths and source bytes

Only canonical repository-relative `src/main/java/.../*.java` paths with `/` are
accepted. Absolute/drive-relative/UNC/device paths, backslashes, dot segments,
empty segments, ADS colons, Windows forbidden characters, control characters,
reserved names and trailing dots/spaces are rejected. Percent escapes are not
decoded. Evidence path must exactly equal the containing file path.

All paths are preflighted before any source opens. Each descendant component is
checked with lstat, rejecting symlinks and Windows reparse points, including
junctions pointing inside the repository. Resolved containment uses path
components, not string-prefix matching. Duplicate effective file paths are rejected.
The user-supplied repository root itself is resolved as the trusted root.

A source is read once into bytes. fstat before/after reading, path reinspection and
post-read stat detect observable replacement/changes. Exact bytes supply both the
SHA-256 and strict UTF-8 decode. No replacement decoding, newline normalization or
source rewriting occurs. This is an offline check over captured snapshots, not
an OS-level sandbox against a hostile concurrent process. Concurrent adversarial
directory races cannot be fully excluded by portable Python path checks.

## Positions and ownership

CRLF, LF and CR define lines. Other Unicode separators do not. Coordinates are
positive Java-sized integer values (booleans excluded), one-based, inclusive,
UTF-16 code-unit columns; each tab counts as one. The mapper builds legal
code-point boundaries and refuses endpoints inside a surrogate pair. Source
snippets preserve original line terminators and spelling. Line-content bounds
exclude CR/LF endpoints and prohibit spillover into another line.

File bytes must match before decoding/range checks. Method identity and position
must be AVAILABLE, file parsing must be PARSED without problems, and the method
range must itself be valid. Evidence must be wholly inside that range. Ranges
describe expressions; no containing statement or semicolon is inferred. Nested
or overlapping chained call ranges are allowed. Duplicate ID is not allowed.

TASK 005-B2 independently recognizes only exact raw character literals
`'\u0000'` and `'\uFFFD'` in code context. The standard-library lexical gate
skips comments and ordinary quoted strings, handles escaped delimiters, and
requires every raw backslash-u marker to be accounted for by those exact tokens.
Multiple-u, malformed, ineligible, string/comment/identifier escapes and text-block
interactions fail closed. Neither a regex match nor an index success flag grants
permission. This gate does not independently prove AST node types; those continue
to originate in TASK 001. The two allowed character values cannot create a Java
quote, backslash, line terminator or token delimiter inside their character token.
BOM and explicit unavailable-mapping limitations still cannot generate VERIFIED;
old failed indexes must not be promoted by deleting limitations. Chinese,
supplementary characters, tabs, and all three line-ending styles are supported.
Raw Unicode escapes are not translated or guessed back to source columns.

Legacy arrays without v2 fields/sidecar produce COMPLETE/FAIL,
UNVERIFIABLE/LEGACY_INDEX_NO_EVIDENCE. Their string/call arrays are not upgraded to
evidence. A v2 index missing metadata is an integrity error, not legacy fallback.
Existing retriever.py behavior and benchmark remain untouched.

## Actual validation and retained artifacts

TASK 002 artifacts (outside the project):

```text
C:\path\to\task002-temp
```

The final offline suite has 55 unittest tests, including multiple failure subcases.
No dependencies are installed and no tests are skipped. The first 47-test sandbox
run had one environment failure while creating a real link. The same tests passed
outside the sandbox, including real reparse-point protection. Expanded tests cover
malformed statuses/Unicode JSON, read permission errors, observed read-time changes
and interrupted execution. All original logs remain in that directory.

Fixtures retain test-id.txt, separately numbered failure reports and subprocess
stdout/stderr. No injected failure modifies a real repository. Unexpected runtime
and interruption tests explicitly assert ABORTED/FAIL. Ordinary invalid inputs
assert COMPLETE/FAIL. Every negative helper asserts zero VERIFIED, including
valid evidence in another file of the same failed batch.

| Real repository | VERIFIED | Failed | Unverifiable | Exit | Report/verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| spring-petclinic | 402 / 402 | 0 | 0 | 0 | COMPLETE/PASS |
| SpringBoot_TCS | 125 / 125 | 0 | 0 | 0 | COMPLETE/PASS |

Real inputs are the manually accepted TASK 001 outputs in
`C:\path\to\task001-inputs`.
The original repository index.json files are not regenerated. Reports, repeated
report byte comparisons, scope/target hash checks and real exit codes are retained.

## Windows CMD manual acceptance

Run from CMD, one command per line. Keep the same window and its variables.
These commands only write a new temporary acceptance directory. Do not re-use an
existing report output; refusal to overwrite is intentional.

```bat
cd /d C:\path\to\issue-landing-assistant
set "TASK002_OUT=%TEMP%\issuelens-task002-accept-%RANDOM%-%RANDOM%"
mkdir "%TASK002_OUT%"
set "TASK002_TEST_ROOT=%TASK002_OUT%\test-temp"
python -B -m unittest discover -s tests -p test_evidence_validator.py -v > "%TASK002_OUT%\tests.log" 2>&1
set "TASK002_TEST_EXIT=%ERRORLEVEL%"
echo TestExit=%TASK002_TEST_EXIT%
type "%TASK002_OUT%\tests.log"
```

Expected: Ran 55 tests, OK, TestExit=0, no skips. If Windows denies both symlink and
junction creation, the suite fails explicitly instead of hiding that coverage.
Do not modify validator conditions to make such a run pass.

```bat
set "TASK001_INPUT=C:\path\to\task001-inputs"
python -B src\evidence_validator.py --repo "C:\path\to\spring-petclinic" --index "%TASK001_INPUT%\petclinic.index.v2.json" --out "%TASK002_OUT%\petclinic.validation.json" > "%TASK002_OUT%\petclinic.stdout.log" 2> "%TASK002_OUT%\petclinic.stderr.log"
set "TASK002_PETCLINIC_EXIT=%ERRORLEVEL%"
echo PetclinicExit=%TASK002_PETCLINIC_EXIT%
python -B src\evidence_validator.py --repo "C:\path\to\SpringBoot_TCS" --index "%TASK001_INPUT%\tcs.index.v2.json" --out "%TASK002_OUT%\tcs.validation.json" > "%TASK002_OUT%\tcs.stdout.log" 2> "%TASK002_OUT%\tcs.stderr.log"
set "TASK002_TCS_EXIT=%ERRORLEVEL%"
echo TcsExit=%TASK002_TCS_EXIT%
python -B -c "import json,os; from pathlib import Path; d=Path(os.environ['TASK002_OUT']); names=['petclinic','tcs']; reports=[json.loads((d/(n+'.validation.json')).read_text(encoding='utf-8')) for n in names]; print('\n'.join(n+': '+r['report_status']+' / '+r['overall_verdict']+' '+json.dumps(r['summary']) for n,r in zip(names,reports)))"
```

Both exits must be 0. Expect COMPLETE/PASS and 402/402, 125/125 with zero failed,
unverifiable and blocking issues. Source changes must produce failure and explanation,
not relaxed requirements. For full evidence details, open the JSON files in an editor.

Original index protection can be checked with:

```bat
certutil -hashfile "C:\path\to\spring-petclinic\index.json" SHA256
certutil -hashfile "C:\path\to\SpringBoot_TCS\index.json" SHA256
```

Expected unchanged hashes:

```text
15f4e1f8160272503489aa7ad42ee9d7701f1cb17ec13721c29ba2516e7494ec
bc2e98f57a26c3be3c2246f5315c0fdbfbcefe4eb40b4c7d7cec9cb82c7d2a0a
```

Failure-report acceptance can be done without editing any source/index:

```bat
echo {not-json> "%TASK002_OUT%\corrupt.index.json"
python -B src\evidence_validator.py --repo "C:\path\to\SpringBoot_TCS" --index "%TASK002_OUT%\corrupt.index.json" --out "%TASK002_OUT%\corrupt.validation.json" > "%TASK002_OUT%\corrupt.stdout.log" 2> "%TASK002_OUT%\corrupt.stderr.log"
set "TASK002_BAD_EXIT=%ERRORLEVEL%"
echo ExpectedExit2=%TASK002_BAD_EXIT%
type "%TASK002_OUT%\corrupt.validation.json"
```

Expected exit 2, report_status COMPLETE, overall_verdict FAIL, zero verified,
issue stage INDEX_READ and reason INDEX_READ_OR_JSON_ERROR. The report must exist.
The unittest suite covers ID corruption/duplication, ownership and method conflicts,
invalid paths and stale source using isolated fixtures. No real source mutation is
needed for failure injection.

## Rollback

Before creation, the workspace file hashes and target source/index hashes were
recorded in task002-temp/workspace-before.json and targets-before.json. This task
adds files only, so no existing source restoration or git reset is needed.

Before removal, compare the three current file hashes with
task002-temp/added-hashes.json. If a file differs, preserve subsequent edits first.
Then run from the project directory:

```bat
del "src\evidence_validator.py"
del "tests\test_evidence_validator.py"
del "docs\evidence_validation.md"
```

No recursive deletion is needed. Keep artifacts, logs and acceptance reports until
manual acceptance completes. TASK 003 must not start until TASK 002 is explicitly
accepted and further work is authorized.
