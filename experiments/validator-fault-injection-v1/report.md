# Controlled Validator fault injection v1

|Scenario|Applicability|Expected|Actual|Local verdict|Repository verdict|Key issue codes|
|---|---|---|---|---|---|---|
|A_real|SUPPORTED|MATCH|MATCH|PASS|FAIL|METHOD_ID_UNAVAILABLE,METHOD_LOCATION_UNAVAILABLE,UNSUPPORTED_RAW_MAPPING|
|A_fixture|SUPPORTED|MATCH|MATCH|PASS|PASS|(none)|
|B_source|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|METHOD_ID_UNAVAILABLE,SOURCE_HASH_MISMATCH,UNSUPPORTED_RAW_MAPPING|
|C_snippet|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|EXACT_SNIPPET_MISMATCH|
|C_range|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|EVIDENCE_OUTSIDE_METHOD|
|D_method_id|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|METHOD_ID_OWNERSHIP_MISMATCH|
|D_evidence_owner|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|EVIDENCE_ID_MISMATCH|
|E_source_hash|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|SOURCE_HASH_MISMATCH|
|E_trusted_pin|SUPPORTED|MISMATCH|MISMATCH|FAIL|FAIL|PINNED_INPUT_HASH_MISMATCH|
|F_snippet|SUPPORTED|UNVERIFIABLE|UNVERIFIABLE|FAIL|FAIL|INVALID_INDEX_SCHEMA|
|F_range|SUPPORTED|UNVERIFIABLE|UNVERIFIABLE|FAIL|FAIL|MISSING_EVIDENCE_RANGE|
|F_selected|SUPPORTED|UNVERIFIABLE|UNVERIFIABLE|FAIL|PASS|(none)|
|G_v2|NOT_APPLICABLE|UNVERIFIABLE|UNVERIFIABLE|FAIL|FAIL|UNSUPPORTED_INDEX_CONFIGURATION|
|H_retrusted_signature|NOT_APPLICABLE|MATCH|MATCH|PASS|PASS|(none)|

MATCH = byte correspondence only, not Bug correctness or AST ownership.
Local PASS and repository FAIL remain separate; missing selected evidence can also coexist with repository PASS.
7 explicit inconsistencies, 3 incomplete-evidence cases, unsupported v2 and coherent re-trusted identity are separate denominators.
H is not an original-pin bypass: caller approved modified index/metadata. Its semantic signature forgery is outside independent AST validation and was actually accepted.
No synthetic detection statistic is localization accuracy or a general robustness guarantee.
Without Validator comparison describes missing verification checks, not a newly measured competing system.
Complete raw reports, isolated inputs, initial expectations and code hashes are retained.
