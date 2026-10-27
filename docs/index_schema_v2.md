# IssueLens TASK 001: index schema 2.0

Publication note (2026-10-27): personal machine paths below are placeholders. Historical results are unchanged; original experiment inputs, hashes and raw logs were not edited. Substitute your own existing paths when following command examples.

This change adds source evidence to the JavaParser index only. It does not implement
Evidence Validator, CEA, ADD, ranking changes, or a new benchmark.

## Compatibility and output

The main JSON document remains an array. Existing fields retain their types and
extraction rules:

| Level | Existing fields |
| --- | --- |
| File | `file` string, `package` string, `classes` array |
| Class | `name` string, `methods` array |
| Method | `name` string, `line` integer, `annotations`/`strings`/`calls` string arrays |

`line` remains `MethodDeclaration.getBegin().line`, or -1 when unavailable. It can
start at an annotation. Old annotation strings still use JavaParser formatting and
only include Spring Mapping annotations. Old string/call arrays retain recursive
body traversal, order, and deduplication. They are not occurrence-level evidence.
JSON object property order is now alphabetical; consumers must use field names.

Usage:

```text
Main <projectRoot> [--output <newIndexPath>]
```

The one-argument default still names `<projectRoot>/index.json`, but existing files
are never overwritten. Explicit output to that default path is rejected even if it
does not exist. A new output path and its sidecar must both be absent. Parent
directories are created as needed. Invalid arguments or I/O errors propagate as
nonzero Java/Maven execution failures. A no-argument invocation prints usage.

For output `example.json`, metadata is `example.json.meta.json`. The sidecar is the
index-level schema metadata, including for an empty main array. There is no metadata
sentinel inside the main array.

## New file fields

* `source_sha256`: lowercase SHA-256 of the exact original file bytes. Null if bytes
  cannot be read. A UTF-8 decoding error still retains the readable bytes' hash.
* `parse_status`: `PARSED`, `PARTIAL`, `FAILED`, or `READ_ERROR`.
* `parse_problems`: parser/read diagnostics, including recovery problems.
* `limitations`: explicit source mapping or unsupported structure limitations.

`PARSED` means JavaParser reports success under the recorded configuration. It does
not mean semantic analysis, successful javac compilation, full structure coverage,
or verification. `PARTIAL` has a recovered AST and parser problems. `FAILED` has
no available AST (or a parser exception). `READ_ERROR` includes strict UTF-8 decode
failure. A failed file remains in the array with empty classes instead of silently
disappearing. Recovered legacy structure may remain for PARTIAL files, but no new
evidence or stable identity is asserted for those methods.

## Method identity

New method fields:

```text
method_id, qualified_class_name, signature
identity_status, location_status
start_line, start_column, end_line, end_column
has_body, evidence_occurrences, evidence_omissions
```

For supported methods:

```text
method_id = "java-source-v1:" + qualified_class_name + "#" + signature
signature = methodName + "(" + commaJoinedParameterTypes + ")"
qualified_class_name = package + topLevelAndMemberTypePath
```

Examples:

```text
java-source-v1:p.A#f(int)
java-source-v1:p.A#f(String)
java-source-v1:p.A.Inner#f(String[])
```

Parameter types preserve source type spelling, qualification, generic arguments and
array dimensions. Whitespace, comments, parameter names, modifiers and type
annotations are excluded. Varargs normalize to arrays. Return types, body content,
line numbers and hashes do not enter method identity. This is a source declaration
identity, scoped to one indexed repository, not a resolved JVM descriptor. Import
rewrites, generic parameter renames or `String` to `java.lang.String` may change it.
Generic erasure equivalence and semantic duplicate declarations are not resolved.

Constructors remain outside the original methods collection; no constructor IDs
are emitted. A future constructor scheme would need a separately specified
`<init>` convention. Named member classes use their enclosing type path. Local
types and types inside anonymous scopes retain legacy records but receive
`UNSUPPORTED_LOCAL_TYPE`, null method_id, and no occurrence evidence. The qualified
name on such a record is descriptive, not an asserted unique identity.

Identity states:

* `AVAILABLE`: a supported syntactic identity was generated.
* `UNAVAILABLE_PARSE`: file parsing is incomplete or failed.
* `UNSUPPORTED_LOCAL_TYPE`: stable scope identity is not supported.
* `CONFLICT`: the generated identity appears multiple times in the same index.

Conflict detection runs across all files. All conflicting method records are kept,
their method_id is set to null, and their evidence arrays are cleared. No line
suffix or arbitrary winner is used. The metadata retains the conflicting proposed
IDs, conflict group count, and affected method count. Location availability remains
independent of identity availability.

## Locations and source evidence

Locations are one-based, inclusive, UTF-16 code-unit columns, with tab width one.
They are not byte offsets, Unicode code-point columns or display-width columns.
LF, CRLF and CR are mapped without newline normalization. Multiline snippets retain
the original line terminators. The exact same file byte snapshot supplies both the
SHA-256 and the decoded parser input.

The mapper requires both complete token-text reconstruction of the original source
and significant-token position/text agreement. Per-node token ranges and text are
checked again against the original input. An in-bounds range alone is insufficient.
Source snippets are substrings of that input, never pretty-printed AST text.

JavaParser 3.26.4 StringLiteralExpr node ranges can differ from literal token ranges
for escaped strings. New positions use the validated first/last token endpoints;
the original `line` field is not redefined. Missing node/token ranges or failed
token-to-original mapping produce unavailable positions, not guessed positions.

`location_status` is `AVAILABLE`, `UNAVAILABLE_MAPPING`, or `UNAVAILABLE_PARSE`.
Unavailable new method positions are null. These states are independent of
`identity_status`. A method with a valid identity can have unavailable positions;
a conflict can have available positions but no identity-linked evidence.

Unicode preprocessing remains explicitly false, matching the old configuration.
TASK 005-B2 permits only exact character tokens `'\u0000'` and `'\uFFFD'`.
A conservative raw lexical walk skips comments/ordinary quoted strings and
classifies these tokens only in code context. Every raw backslash-u marker,
including malformed or ineligible markers, must belong to such a token.
Other escape values/spellings, strings, comments, identifiers, multiple-u forms,
and text-block contexts remain blocked. This is not general Unicode translation.
The indexer additionally checks CharLiteralExpr character value, token text/range,
full-file token reconstruction and existing method/evidence slice checks.
Unknown mapping retains `UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED`.
UTF-8 BOM mapping is likewise blocked
with `BOM_RAW_MAPPING_UNSUPPORTED` when an AST is recoverable. Ordinary Chinese and
UTF-16 surrogate pairs are supported and tested. No Unicode escape back-map is
claimed.

Each `evidence_occurrences` item contains:

```json
{
  "evidence_id": "sha256:<64 lowercase hexadecimal digits>",
  "evidence_type": "METHOD_CALL",
  "file_path": "src/main/java/p/A.java",
  "start_line": 3,
  "start_column": 5,
  "end_line": 3,
  "end_column": 13,
  "source_snippet": "sink(\"x\")"
}
```

Supported types:

* `ANNOTATION`: method declaration and direct parameter annotations. These include
  non-Mapping annotations; the old annotations array stays unchanged. Class and
  type-use annotations are not newly indexed as evidence.
* `STRING_LITERAL`: StringLiteralExpr occurrences in the method body.
* `METHOD_CALL`: MethodCallExpr occurrences in the method body.

Each occurrence is retained, including repeated identical values. Annotation
arguments are represented by the annotation snippet; their string nodes are not
separately indexed. Expressions inside local/anonymous type declarations are not
attributed to the outer method in new evidence; lambda bodies are included.
Anonymous constructor arguments still belong to the enclosing method.
Method-call ranges describe expressions, not containing statements; they do not
include statement semicolons. Chained calls can have overlapping ranges.

Evidence ID is `sha256:` plus SHA-256 of the deterministic JSON bytes for:

```text
[file_path, source_sha256, method_id, evidence_type,
 start_line, start_column, end_line, end_column]
```

The same configured Jackson serializer is used. Evidence IDs bind to a source
snapshot, so any file-byte change may change them. `evidence_omissions` counts
individual candidate occurrences whose mapping failed after method-level gates
passed. It is not an expected occurrence total; whole-method suppression is
explained by the method/file states.

## Metadata, determinism and publication

The sidecar contains schema_version `2.0`, method_id_scheme `java-source-v1`, parser
version/language/configuration, source roots, position convention, file/method/
evidence counts, all four parse_counts (including zeros), unavailable-method counts,
conflict counts/IDs, index_file and index_sha256.

`index_sha256` is calculated from exactly the final main-index bytes, before those
same bytes are written. Serialization uses sorted object properties and map keys,
sorted input file paths, deterministic AST traversal and occurrence ordering, UTF-8,
and no run timestamp or absolute repository path. Repeated indexing of the same
snapshot yields identical main bytes. Sidecars also match when output basenames
match; a different basename intentionally changes index_file.

Both payloads are serialized and staged in the output directory before publication.
Neither destination is replaced. If sidecar publication fails after main publication,
the newly published main file is removed and an exception is raised. Success is
announced only after both writes finish. Two-file publication is not crash-atomic:
process termination or filesystem failure can leave an orphan. A consumer must
require both files and check the binding; no validator is implemented here.

## Coverage limits

The scan remains a single src/main/java tree. ClassOrInterfaceDeclaration and its
direct methods remain the indexed structures. Constructors, anonymous class methods,
and enum/record/annotation declarations' own methods are not added. Unsupported
declarations are noted in limitations. Local classes keep legacy records without a
stable ID. Text blocks, method references, object construction as a call, call target
resolution, class annotations, inheritance and multi-module discovery are outside
this task. Sources resolving outside the repository are rejected.

Hashes, raw snippets, paths and locations are extractable source facts. Identity is
a documented syntactic naming scheme. Parse/availability states are processing
results. None of these is an independent VERIFIED assertion, execution trace,
semantic call graph, correctness proof or root-cause determination.

## Actual implementation validation (2026-10-19)

All artifacts and original-byte backups are retained outside the project at:

```text
C:\path\to\task001-temp
```

The sandbox TEMP was not persistent between tool invocations, so this explicitly
authorized directory served as the temporary build/test/output directory. No
existing target repository index was used as an output destination.

27 offline JUnit tests passed, with zero failures, errors or skips. Tests cover
overloads, packages and member types, deterministic IDs, repeated occurrences,
original snippets, file bytes, legacy fields, LF/CRLF/CR/tab/Chinese/surrogates,
Unicode escape fail-closed behavior, BOM, damaged/missing ranges, conflict groups,
scope ownership, abstract methods, unsupported declarations, PARSED/PARTIAL/FAILED/
READ_ERROR, deterministic serialization, metadata hashes, no-overwrite CLI, failed
publication rollback and empty input.

Earlier failures are retained: Maven mirror/cache repository-ID resolution failure,
an escaped-string node-range regression and the adversarial damaged-range check.
They are not represented as passing runs. The offline settings file lists both
already-cached central and aliyun repository IDs; `-o` forbids network resolution.
No artifacts were downloaded. Maven launcher startup emitted Access is denied in
the sandbox, followed by real Maven execution; exit codes and Surefire XML are the
test evidence. JDK 25 also warned about source/target 17 without --release. A CLI
smoke attempt using unsupported --help failed; no-argument usage is supported.

| Repository | Historical methods | Old indexer on current snapshot | New indexer | New evidence |
| --- | ---: | ---: | ---: | ---: |
| spring-petclinic | 87 | 88 | 88 | 402 |
| SpringBoot_TCS | 22 | 22 | 22 | 125 |

The exact legacy projection of the new output matches the old indexer's current
snapshot output for both projects, including array ordering and values. Petclinic's
historical index lacks OwnerController.handleOwnerNotFound at line 179. TCS's
historical index has old line numbers and iterator-related calls in findByName and
findByEmail that are absent from current source. These are historical input drift,
not new-indexer behavior differences. Detailed diff files are retained.

Both current scans are entirely PARSED, with zero identity conflicts, unavailable
identities or unavailable method locations. Original index hashes remain:

```text
petclinic: 15F4E1F8160272503489AA7AD42EE9D7701F1CB17EC13721C29BA2516E7494EC
tcs:      BC2E98F57A26C3BE3C2246F5315C0FDBFBCEFE4EB40B4C7D7CEC9CB82C7D2A0A
```

## Windows CMD acceptance

Run the following from Windows CMD, one command per line. Use a fresh acceptance
directory; do not reuse an existing output pair. JAVA_HOME may instead identify
another installed JDK 17 or newer. Do not remove `-o` to work around a missing cache.

```bat
cd /d C:\path\to\issue-landing-assistant
set "TASK001_ARTIFACTS=C:\path\to\task001-temp"
set "TASK001_ACCEPT=%TEMP%\issuelens-task001-accept-%RANDOM%-%RANDOM%"
set "JAVA_HOME=C:\Program Files\Java\jdk-25"
mkdir "%TASK001_ACCEPT%"
certutil -hashfile "C:\path\to\spring-petclinic\index.json" SHA256 > "%TASK001_ACCEPT%\petclinic-before.txt"
certutil -hashfile "C:\path\to\SpringBoot_TCS\index.json" SHA256 > "%TASK001_ACCEPT%\tcs-before.txt"
call C:\maven\bin\mvn.cmd -o -s "%TASK001_ARTIFACTS%\offline-settings.xml" -f java-indexer\pom.xml "-Dtask001.build.directory=%TASK001_ACCEPT%\build" test > "%TASK001_ACCEPT%\junit.log" 2>&1
echo MavenExit=%ERRORLEVEL%
type "%TASK001_ACCEPT%\junit.log"
```

Expected: MavenExit=0, BUILD SUCCESS and `Tests run: 27, Failures: 0, Errors: 0,
Skipped: 0`. XML/text reports are in build\surefire-reports and retained fixtures
are in build\test-temp. Missing offline dependencies are failures, not skipped
success. Stop if this step fails.

Use the compiled classes with explicit existing dependency paths (no exec plugin
resolution required):

```bat
set "TASK001_CP=%TASK001_ACCEPT%\build\classes;%USERPROFILE%\.m2\repository\com\github\javaparser\javaparser-core\3.26.4\javaparser-core-3.26.4.jar;%USERPROFILE%\.m2\repository\com\fasterxml\jackson\core\jackson-core\2.18.2\jackson-core-2.18.2.jar;%USERPROFILE%\.m2\repository\com\fasterxml\jackson\core\jackson-annotations\2.18.2\jackson-annotations-2.18.2.jar;%USERPROFILE%\.m2\repository\com\fasterxml\jackson\core\jackson-databind\2.18.2\jackson-databind-2.18.2.jar"
"%JAVA_HOME%\bin\java.exe" -Dfile.encoding=UTF-8 -cp "%TASK001_CP%" Main "C:\path\to\spring-petclinic" --output "%TASK001_ACCEPT%\petclinic.index.v2.json" > "%TASK001_ACCEPT%\petclinic-scan.log" 2>&1
echo PetclinicExit=%ERRORLEVEL%
"%JAVA_HOME%\bin\java.exe" -Dfile.encoding=UTF-8 -cp "%TASK001_CP%" Main "C:\path\to\SpringBoot_TCS" --output "%TASK001_ACCEPT%\tcs.index.v2.json" > "%TASK001_ACCEPT%\tcs-scan.log" 2>&1
echo TcsExit=%ERRORLEVEL%
type "%TASK001_ACCEPT%\petclinic.index.v2.json.meta.json"
type "%TASK001_ACCEPT%\tcs.index.v2.json.meta.json"
certutil -hashfile "%TASK001_ACCEPT%\petclinic.index.v2.json" SHA256
certutil -hashfile "%TASK001_ACCEPT%\tcs.index.v2.json" SHA256
certutil -hashfile "C:\path\to\spring-petclinic\index.json" SHA256 > "%TASK001_ACCEPT%\petclinic-after.txt"
certutil -hashfile "C:\path\to\SpringBoot_TCS\index.json" SHA256 > "%TASK001_ACCEPT%\tcs-after.txt"
fc /b "%TASK001_ACCEPT%\petclinic-before.txt" "%TASK001_ACCEPT%\petclinic-after.txt"
fc /b "%TASK001_ACCEPT%\tcs-before.txt" "%TASK001_ACCEPT%\tcs-after.txt"
```

Both scans must return 0 and print INDEX_PAIR_WRITTEN in the scan logs. Compare each
new file hash with its sidecar index_sha256 (hexadecimal case is immaterial). For
the unchanged snapshot, expect methods_total 88/22 and evidence_occurrences 402/125.
Both fc comparisons must report no differences.

Explicit no-overwrite acceptance, intentionally expecting nonzero Java exit:

```bat
certutil -hashfile "%TASK001_ACCEPT%\tcs.index.v2.json" SHA256 > "%TASK001_ACCEPT%\new-before.txt"
"%JAVA_HOME%\bin\java.exe" -cp "%TASK001_CP%" Main "C:\path\to\SpringBoot_TCS" --output "%TASK001_ACCEPT%\tcs.index.v2.json" > "%TASK001_ACCEPT%\refuse-overwrite.log" 2>&1
echo ExpectedNonzeroExit=%ERRORLEVEL%
certutil -hashfile "%TASK001_ACCEPT%\tcs.index.v2.json" SHA256 > "%TASK001_ACCEPT%\new-after.txt"
fc /b "%TASK001_ACCEPT%\new-before.txt" "%TASK001_ACCEPT%\new-after.txt"
```

Failure conditions: any failed/error/skipped test; an expected successful command
returns nonzero; overwrite refusal returns zero; existing index hashes change;
sidecar missing or hash mismatched; unexplained legacy projection differences;
unexpected method identity conflict or unavailable location in these snapshots;
any change outside the four authorized project files. PARSED alone is insufficient
for acceptance. Do not silently accept a changed source snapshot's different counts.

## Rollback (without Git)

Backups: task001-temp\backup\Main.java and pom.xml, with baseline-hashes.json.
Before rollback, compare current project hashes to task001-temp\modified-hashes.json.
If they differ, stop and preserve subsequent user edits before restoring.

From CMD after that check:

```bat
cd /d C:\path\to\issue-landing-assistant
copy /b /y "%TASK001_ARTIFACTS%\backup\Main.java" "java-indexer\src\main\java\Main.java"
copy /b /y "%TASK001_ARTIFACTS%\backup\pom.xml" "java-indexer\pom.xml"
del "java-indexer\src\test\java\MainTest.java"
del "docs\index_schema_v2.md"
certutil -hashfile "java-indexer\src\main\java\Main.java" SHA256
certutil -hashfile "java-indexer\pom.xml" SHA256
```

Expected original hashes:

```text
Main.java: 294141E1AA547B85F06BC4F61F009F8C02477001BDB045B886F22D57AD41DB9E
pom.xml:   76A92F0CB861574CBCFEE88A8F695B327B57201367D3537A3AAF39C6F51211EC
```

No recursive deletion is needed. Keep temporary outputs and backups through manual
acceptance. Neither external repository needs restoration because its source and
old index were not modified. TASK 002 requires separate authorization after TASK
001 acceptance.
