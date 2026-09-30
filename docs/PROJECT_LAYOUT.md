# Submission layout and reproducibility

Reorganized on 2026-09-30. Start with the root [README](../README.md).

## Module mapping

| Grading requirement | Implementation | Preserved evidence |
|---|---|---|
| EDA | `src/eda/pipeline.py`, package CLI | `docs/eda/tables`, `docs/eda/figures`, EDA Markdown and HTML |
| Data preprocessing | `src/preprocessing/ingest.py`, `pdf_helpers.py`, `schema.py`, package CLI | Original PDF, CSV/Parquet, ingestion audit, DuckDB, 300 RAG cards |
| Model training | `src/model_training` records that fitting was not performed; optional frozen E5 indexing | `output/model_setup/manifest.json`, pinned encoder revision in configuration |
| Model evaluation | `src/evaluation` contains benchmark validation, scoring, runners, reports, preflight and local replay | `experiments/preflight`, `runs`, `reports`, `reviews` |
| Reproducibility | `requirements-frozen.txt`, `configs`, `Dockerfile`, `compose.yaml`, README commands | Input hashes, run settings, prompts, call ledgers, stored SQL/results, exported figures |

The EDA, ingestion, schema and PDF helper implementations were moved, not duplicated.
Their previous Python paths remain small compatibility shims, allowing existing notebooks
and imports to work. The former executable `docs/evaluation/audit_dev_comparison.py` is
also a shim for `src.evaluation.preflight`. New preflight outputs live under
`experiments/preflight`; historical copies in `docs/evaluation` remain for older report links.
The former `read.md` is preserved in `docs/notes/DEVELOPMENT_NOTES.md`.

Retrieval algorithms and shared corpus construction stay in `src/retrieval`, because both
preprocessing and the application use them. The preprocessing CLI explicitly calls that
shared corpus builder after creating the database. SQL generation stays in `src/agent`.

## Verification performed

- Rebuilt preprocessing into `tmp/reproducibility/dataset`, without replacing active data:
  1,125 entries, 205 constituencies and 300 RAG cards. Corpus SHA-256 matches the archived
  experiment: `86412bb2b9e8d294edc8e33d0266799c01d336f1c0bbf3bf7936102aa6e0178b`.
- Regenerated EDA into `tmp/reproducibility/eda`: 35 PDF pages, 21 checks, zero failures.
  Published graph files and tables remain under `docs/eda`.
- Prepared the 300-card E5 index with `HF_HUB_OFFLINE=1`, using the cached pinned encoder.
  A separate offline run passed all five retrieval tests, including dense retrieval.
- Verified saved Haiku measurements locally: all 72 scores and initial prompts reproduced;
  all 56 saved SQL outcomes reproduced. See the detailed experiment review.
- Checked the 120-package Python environment with `uv pip check`; no dependency conflicts.
- Parsed Compose YAML and verified README local links and unchanged preflight input hashes.

The full automated test result is recorded in the README validation note. No new LLM calls
were made during this reorganization or local review.

## Remaining environmental limitation

Docker is not installed on this machine. Container build and execution are therefore
**not verified**. The Dockerfile pins the Python version and installs the recorded packages;
Linux-specific dependency resolution still requires a real build. Its resolved environment
is written to `/opt/image-requirements.txt` for archival. Pin a verified base-image digest and
retain the final image digest when packaging the submission.

This reorganization does not fix the source PDF’s ambiguous region boundary and does not
turn the draft development benchmark into a held-out test set. It also does not establish
that the unrelated local model artifact was trained or used in the reported comparison.
