# Local review of the saved RAG–SQL comparison

Reviewed 2026-09-30. No new provider requests were made. Evidence: the original
run manifest, 72 saved traces and provider call ledger under
`experiments/runs/text-to-sql-comparison-haiku-20260930/`, checked by
`python -m src.evaluation.verify_saved_run --run-id text-to-sql-comparison-haiku-20260930`.
Machine-readable checks and input file hashes are in [verification.json](verification.json).

## What was verified

- Benchmark and RAG corpus hashes match the archived run.
- All expected 18 question × four condition combinations are present.
- Rescoring the saved outputs reproduces all 72 stored outcomes and success flags.
- Re-executing all 56 saved SQL queries reproduces their scored outcomes. All execute.
- Reconstructing each initial prompt from saved context IDs reproduces all 72 prompt hashes.
- The ledger has 73 provider attempts and 72 successful responses, matching trace call counts.
- The manifest specifies Claude with fallback disabled. Saved successful-response metadata
  names `claude-haiku-4-5-20251001`; the configured alias was `claude-haiku-4-5`.

This establishes internal consistency of the saved artifacts and reproducibility of their
SQL scores. It does not authenticate the records against the provider or reproduce generation.

## Results and interpretation

| Display condition (internal ID) | Correct SQL / 14 | Successful tasks / 18 |
|---|---:|---:|
| A — fixed context without retrieval (A) | 11 / 14 (78.6%) | 15 / 18 (83.3%) |
| B1 — BM25 retrieval (B) | 14 / 14 (100%) | 18 / 18 (100%) |
| B2 — E5 retrieval (C) | 13 / 14 (92.9%) | 17 / 18 (94.4%) |
| B3 — hybrid retrieval (D) | 13 / 14 (92.9%) | 17 / 18 (94.4%) |

All conditions correctly handled the two ambiguous and two unsupported questions.
There were no SQL repairs. Execution success alone was insufficient: five executable
queries returned the wrong results.

| Condition / question | Observed failure |
|---|---|
| A / dev-001 | Accented Bouaké pattern did not match the stored locality; empty result. |
| A / dev-011 | `SAN-PEDRO` did not match the stored `SAN PEDRO` name; empty result. |
| A / dev-019 | Broad Yamoussoukro/commune filtering selected constituency 052 rather than target 053. |
| B2 / dev-011 | Broad San Pedro filtering included both constituencies 173 and 175, rather than only 175. |
| B3 / dev-010 | Grouping by party and taking one row produced the wrong denominator/percentage. |

The hybrid dev-010 SQL also has no `ORDER BY`; its selected row is not guaranteed.
Its replay remained incorrect under the benchmark scoring policy.

Retrieval coverage and SQL accuracy measure different stages. Among the 16 retrieval-eligible
questions, complete required-evidence coverage was 10/16 for BM25, 15/16 for E5 and 14/16
for hybrid. Mean evidence-slot recall was 79.69%, 97.92% and 93.75%, respectively.
E5 found more required evidence, but BM25 produced the best final SQL results in this run.
Retrieval can help resolve entity names while leaving SQL scope and arithmetic errors unresolved.

## Limits to claims

- This is one run of a small, unfrozen **development** benchmark, not an unseen test set.
- Exact paired SQL tests against A give p = 0.25 (BM25), 0.50 (E5), 0.625 (hybrid):
  the observed gains do not establish statistical superiority.
- Dev-006 and dev-007 were excluded before measurement due to unresolved page-14 region carry
  in the source PDF. Reproducible ingestion does not establish that the geographic label is correct.
- Scoring compares expected result values under declared ordering/tolerance rules and permits
  extra predicted columns. It does not prove every generated claim or explanation is supported.
- Provider artifacts are locally auditable but not independently authenticated. Future generation
  can differ even with the same model alias and settings.
- No training or fine-tuning is evidenced by this experiment. The active E5 configuration points
  to the pinned public pretrained revision, not the unrelated local model folder.

The appropriate conclusion is that retrieval improved observed SQL result correctness on this
sample, with BM25 the strongest measured SQL condition. A larger frozen test set, repeated runs,
manual semantic review and a resolved source geography are needed for broader claims.
