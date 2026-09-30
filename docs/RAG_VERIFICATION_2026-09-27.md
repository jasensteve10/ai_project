# RAG integration verification — 27 September 2026

The RAG is connected to the existing evaluation harness through the same
`run_with_context` function used by the application. The application runs with
Gemini 3.8 Flash, local retrieval and read-only DuckDB execution.

## Live smoke checks

Three development questions were tested in BM25 (B) and hybrid (D), giving six
successful result comparisons against the benchmark gold results:

| Question | B result | D result |
|---|---|---|
| dev-003: total voters in Poro | 291,906 | 291,906 |
| dev-012: turnout in Haut-Sassandra | 0.2955082439078201 | 0.2955082439078201 |
| dev-019: PDF page for Yamoussoukro commune | constituency 053, page 10 | constituency 053, page 10 |

Turnout is a fraction: approximately 29.55%. The calculation uses constituency
totals, avoiding candidate-row duplication. No final test-set questions were sent
to the model.

- [Generated live report](../experiments/reports/rag-integration-live-fixed-20260927/results.md)
- [Live traces](../experiments/runs/rag-integration-live-fixed-20260927/traces.jsonl)
- [Live manifest](../experiments/runs/rag-integration-live-fixed-20260927/manifest.json)

The initial live run revealed a real SDK integration defect: Gemini returned typed
text blocks, while the parser required a string. This is fixed, covered by a
regression test, and the original failed run is retained in
`experiments/runs/rag-integration-live-20260927/` for transparency. Its eight metered
calls include responses that were unnecessarily repaired before the fix.

The corrected run initially used eight calls, stopped at its cap, and resumed with
three more calls to finish the two page checks. Some calls encountered transient
provider errors; retries are visible in the traces. The trace reader keeps the
latest result per question/condition for scoring, so total provider attempts across
resumptions exceed the call sum of the final six scored results. No billing settings
were changed; billing charges were not independently audited.

## Local integration checks

Full regression suite: **94 passed**, with one dependency deprecation warning
from the Google SDK (Python 3.14); no test failures.

- All 20 development questions passed through retrieval-only A/B/C/D/F1/F3 without
  Gemini calls. [Retrieval output](../experiments/runs/rag-integration-retrieval-20260927/retrieval.jsonl).
- Three development questions × six conditions completed with the scripted gold
  model: 18/18 pipeline checks. This verifies plumbing, not model accuracy.
  [Offline report](../experiments/reports/rag-integration-offline-20260927/results.md).
- Benchmark validation: 80 records (20 dev, 60 test), zero errors/warnings; all 80
  remain draft and the benchmark is not frozen.
- The actual pinned E5 model loaded locally and hybrid search resolved the exact
  `HAUT- SASSANDRA` entity label. The model did not need a download during verification.
- Streamlit tests cover SQL results, abstention, clearing history/cache, local-only
  search and switching conditions. The local server also started successfully.
- `uv pip check`: all 114 installed packages compatible. `git diff --check`: clean.

The focused regression tests cover shared application/evaluation context, audited
SQL totals, stale-corpus rejection, provenance pages, retrieval failure traces,
per-question context isolation, exclusion of oracle labels from the app, typed
Gemini text blocks and provider-call accounting at the budget cap.

## Interpretation limits

These six live checks demonstrate working integration only. They do not establish
held-out accuracy, superiority of one retriever, statistical significance or
production reliability. F1/F3 additional-search behavior is covered by scripted
tests; this smoke run did not evaluate adaptive-search quality with a live model.
Human benchmark review, freezing and the planned full study remain separate work.
