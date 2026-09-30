# Retrieval augmentation for electoral Text-to-SQL

A French-language assistant answers questions about Côte d'Ivoire's 2025 legislative
election results (supplied 35-page PDF) by generating SQL over a validated DuckDB
database. The project compares text-to-SQL without retrieval (A) with three retrieval
variants (BM25, multilingual E5, hybrid) and fine-tunes the E5 retriever. Methods,
results and discussion are in the report: [report/PROJECT_REPORT_RESULTS.docx](report/PROJECT_REPORT_RESULTS.docx).

## Project structure

```
src/pipeline/       entry points: eda, preprocess, train, evaluate
src/preprocessing/  PDF extraction, validation, DuckDB marts
src/eda/            EDA computations and figures
src/retrieval/      corpus cards, BM25, E5, hybrid fusion, context selection
src/training/       synthetic training pairs, contrastive fine-tuning loop
src/agent/          SQL generation (Gemini/Claude), validation, repair
src/evaluation/     benchmark, scoring, experiment runner, reports
src/prompts/, src/semantic/   prompt text, glossary, data dictionary
configs/            core.json, text_to_sql_comparison.json (protocol), training.json
dataset/            raw PDF, cleaned CSV/Parquet, DuckDB, retrieval cards
benchmarks/         20 dev + 60 held-out questions with gold results
outputs/            figures, tables and metrics written by the pipeline stages
experiments/        pre-run audit, run traces/manifests and per-run reports
report/             project report (Markdown source, docx, figure/docx builders)
app/                Streamlit application
tests/              offline regression tests
```

## Setup

Verified on macOS arm64 with Python 3.14.4.

```sh
uv venv .venv --python 3.14.4
uv pip install --python .venv/bin/python -r requirements.lock
source .venv/bin/activate
```

`requirements.txt` lists direct pins; `requirements.lock` is the full resolved
environment. Settings live in `configs/`. API keys are only needed for live SQL
generation and the app: copy `.env.example` to `.env`.

## Pipeline

| Stage | Command | Writes |
|---|---|---|
| 1 EDA | `python -m src.pipeline.eda` | `outputs/eda/` figures, tables, summary |
| 2 Preprocessing | `python -m src.pipeline.preprocess` | `outputs/preprocessing/summary.json` |
| 3 Training | `python -m src.pipeline.train` | `models/`, `outputs/training/` |
| 4 Evaluation | `python -m src.pipeline.evaluate` | `experiments/`, `outputs/evaluation/` |

- **Preprocessing** rebuilds everything in a temporary folder and checks it against the
  committed `dataset/` and gold labels. `--write` overwrites them instead; the rebuilt
  DuckDB file has new bytes, so the pre-run audit hashes checked by
  `src.evaluation.compare_rag` would then need a new audit (`python -m src.evaluation.preflight`).
- **Training** fits the BM25 and E5 indices and fine-tunes E5 (about 3 minutes on
  Apple MPS). First run: add `--download-model` to fetch the pinned E5 weights.
  `--skip-finetune` builds the indices only.
- **Evaluation** makes no API calls by default: it recomputes retrieval metrics and
  rebuilds the report of the saved Claude Haiku generation run. A new generation run is
  billed and capped: `python -m src.pipeline.evaluate --live --provider claude --max-calls 300`.
- Report figures and document: `python report/build_figures.py && python report/build_docx.py`.

## Application

```sh
python -m streamlit run app/app.py
```

Uses `GEMINI_MODEL`; `LLM_FALLBACK=claude` enables a capped, billed Claude fallback.
Each question is independent; answers are computed by read-only SQL.

## Tests

```sh
python -m pytest -q
```

Offline: no API key or network needed once the E5 weights are cached.
