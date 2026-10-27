# IssueLens — CEA v0.1 (offline metadata intervention)

## What it does

Reuses the current `src/retriever.py` keyword extractor and Rule scorer. It
recalculates the ranking for every method in an existing full-ranking JSON,
then separately masks `name`, `strings`, and `calls` in **copies** of the saved
candidate features and recalculates the complete rank order.

The output records how the baseline Top-1 and Top-5 move under each ablation,
including Top-20 set overlap, score shifts, method IDs, scorer source SHA-256,
and input hashes. This creates testable **ranking sensitivity diagnostics**;
it does not claim causal fault localization or calibrated confidence.

**Important limitation:** Saved metadata fields are not independent. `name`
may also appear in `signature` or `file`, and `calls` may still be reflected
in other fields. No source-code execution, semantic oracle, GT, or API is used.
This is **not** a measurement of BM25 stability: the full-ranking JSON supplies
only the method catalog, while all CEA base/intervened ranks are recomputed
with the Rule scorer.

## Install

Extract the ZIP **into the IssueLens repository root**, keeping paths:

- `src/cea_evidence_audit.py`
- `tests/test_cea_evidence_audit.py`

Do not overwrite existing files. No third-party library is added by the CEA
script itself, but the project's current `retriever.py` dependencies must be
installed (as in existing experiments).

## Test (no repository cases or API required)

```powershell
python -B src/cea_evidence_audit.py --self-test
```

## Run a local diagnostic (known development case, NOT an independent test)

```powershell
$ranking = 'experiments/acc006-frozen-prediction-20261023-v1/bm25/LANG-1646/full-ranking.json'
$issue = 'experiments/structural-selection-acceptance-20261025/LANG-1646.issue.txt'
$output = 'experiments/cea-local-v1/LANG-1646.audit.json'
python -B src/cea_evidence_audit.py --ranking $ranking --issue-file $issue --output $output
Write-Host "ExitCode=$LASTEXITCODE"
```

The output path **must be new**. Never write into the input ranking's case
directory. Existing outputs are never overwritten. The report has `status`
`OFFLINE_COUNTERFACTUAL_METADATA_DIAGNOSTIC_NOT_CORRECTNESS_PROOF` and lists
limitations explicitly. No GT, DeepSeek, or automatic retry is involved.

## Interpreting the report

- `top1_changed`: Whether masking a single metadata field changes the Rule Top-1.
- `original_top1_new_rank`: Where the original Rule Top-1 moves after masking.
- `topk_overlap_count`, `top20_overlap_count`: Candidate-set retention.
- `candidates_with_score_change`: Number of catalog methods with changed Rule scores.
- `original_topk_moves`: Exact method IDs, ranks, and score deltas for the original top K.

Small movement is not proof of correctness. Large movement is not proof of
incorrectness. Sensitivity to a field does **not** establish that the field is
a separately verified source-code fact.

For a competition result, first specify a hypothesis (e.g. evidence sensitivity
predicts error/abstention outcomes), then use newly held-out cases and a
predeclared scoring protocol. Do not promote prior known-GT diagnostics into
blind testing.
