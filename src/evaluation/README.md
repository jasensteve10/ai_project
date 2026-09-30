# Model evaluation

This folder groups the benchmark, scorer, retrieval comparison, experiment runner,
report exporter, preflight audit and saved-result verifier.

```sh
python -m src.evaluation.verify_saved_run --run-id text-to-sql-comparison-haiku-20260930
python -m src.evaluation.preflight
python -m src.evaluation.compare_rag --mode retrieval-only --run-id new-local-retrieval
python -m src.evaluation.compare_rag --mode fake --pace 0 --run-id new-local-check
python -m src.evaluation.report --run-id new-local-check
```

All commands above are local with cached E5. Real API evaluation is a separate
explicit `--mode live` operation with a fixed provider and call cap. Never mix
scripted gold outputs with model accuracy. Keep `experiments/runs/` as immutable
evidence; generated summaries and figures live in `experiments/reports/`, source
audits in `experiments/preflight/` and independent checks in `experiments/reviews/`.
