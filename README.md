# Evaluating Retrieval Augmentation for Electoral Text-to-SQL

Compare a fixed-context Text-to-SQL baseline with BM25, multilingual E5 and hybrid retrieval on Côte d’Ivoire’s EDAN 2025 election results. The application turns questions into guarded, read-only DuckDB queries and returns results with source context.

## Submission structure

| Required component | Python module | Entry point |
|---|---|---|
| EDA | `src/eda/` | `python -m src.eda` |
| Data and RAG preprocessing | `src/preprocessing/` | `python -m src.preprocessing` |
| Model training/setup | `src/model_training/` | `python -m src.model_training` |
| Model evaluation | `src/evaluation/` | Commands below |

**No model was trained or fine-tuned in the evaluated experiment.** The training/setup module records this explicitly and can prepare the frozen pretrained encoder and its index. Epochs, optimizer, learning rate and a training split are not applicable. The local `models/e5-small-edan-ft` folder is not referenced by the comparison configuration; its name alone is not evidence of project training.

```text
app/                         Streamlit interface
src/eda/                     Raw PDF / ingested CSV analysis and plots
src/preprocessing/           PDF parsing, validation, CSV/Parquet, SQL views, RAG cards
src/model_training/          Pretrained setup manifest and optional E5 indexing
src/evaluation/              Benchmark, retrieval/SQL scoring, reporting, local verification
src/retrieval/                BM25, E5, hybrid ranking and context construction
src/agent/                   Guarded Text-to-SQL and provider adapters
src/prompts/                 Shared prompts and SQL examples
configs/                     Versioned experiment settings and model revision
benchmarks/                  Questions, expected results and scoring policies
experiments/preflight/       Source/label checks and exclusions
experiments/runs/            Preserved manifests, traces and provider call ledgers
experiments/reports/         Saved metric tables, figures and comparisons
experiments/reviews/         Independent local replay of saved measurements
dataset/                     Source PDF, clean tables, database and retrieval cards
docs/eda/                    Preserved EDA tables, figures and report section
docs/report/                 Report sources and version guide
output/                      Document exports and generated setup manifests
tests/                       Local automated tests
```

Older imports under `src/analysis`, `src/ingestion`, `src/schema` and `src/ETL_fonctions` remain compatible. Their implementations now live in the modules above. Historical run instructions are retained in [docs/LEGACY_RUN_GUIDE.md](docs/LEGACY_RUN_GUIDE.md); use this README for the current workflow.

## Checked results

The archived run `text-to-sql-comparison-haiku-20260930` contains 18 development questions: 14 answerable, two ambiguous and two unsupported. Each condition was run once. Saved response metadata identifies `claude-haiku-4-5-20251001`.

| Condition | SQL results correct / 14 | All tasks correct / 18 |
|---|---:|---:|
| A — baseline without retrieval | 11 (78.6%) | 15 (83.3%) |
| B1 — BM25 RAG | 14 (100%) | 18 (100%) |
| B2 — E5 RAG | 13 (92.9%) | 17 (94.4%) |
| B3 — hybrid RAG | 13 (92.9%) | 17 (94.4%) |

Internal condition IDs in saved files are A/B/C/D respectively. Baseline A receives all seven schema and 12 domain cards, but no entity retrieval. All four conditions share the same SQL rules and examples. Retrieval quotas are three schema, three domain and five entity cards.

The local review reproduced all 72 saved scores, all 56 SQL execution outcomes, and all 72 initial prompt hashes. The ledger records 73 provider attempts and 72 successful responses. **This review sends no requests to an LLM.** See the [review](experiments/reviews/text-to-sql-comparison-haiku-20260930/REVIEW.md) and [machine-readable verification](experiments/reviews/text-to-sql-comparison-haiku-20260930/verification.json).

BM25 had the highest SQL accuracy on this small development set; E5 had the highest retrieval coverage. Better retrieval coverage did not guarantee a correct SQL query. These are draft development results, not held-out test accuracy or evidence of statistical superiority. Questions `dev-006` and `dev-007` were excluded before measurement because the PDF’s page-14 region boundary remains ambiguous.

## Validation status

The host full suite passed **143 tests with one skipped**. After strengthening ledger verification, all **nine saved-run verification tests** passed separately. A separate offline retrieval run passed all **five retrieval tests**, including dense retrieval. Preprocessing reproduced the archived 300-card corpus; EDA passed all 21 checks; offline E5 indexing succeeded. See [the layout and verification record](docs/PROJECT_LAYOUT.md). No new generation API requests were made.

## Local setup

Reference environment: Python 3.14.4, macOS arm64. Use a fresh environment; `uv` is optional if an appropriate Python interpreter is already installed.

```bash
uv venv --python 3.14.4 .venv
uv pip install --python .venv/bin/python -r requirements-frozen.txt
source .venv/bin/activate
uv pip check
```

`requirements-frozen.txt` records the reference environment, including EDA and report dependencies. The smaller `requirements*.txt` files retain the original dependency groups. Linux may need additional platform dependencies; the Docker build captures its resolved packages in `/opt/image-requirements.txt`.

Run tests and verify the archived measurements without any API key or encoder download:

```bash
python -m pytest -q
python -m src.evaluation.verify_saved_run --run-id text-to-sql-comparison-haiku-20260930
```

Rebuild data and EDA into separate directories, preserving the published experiment inputs:

```bash
python -m src.preprocessing --dataset-dir output/rebuilt-data
python -m src.eda --output-dir output/reproduced-eda
python -m src.model_training
```

The preprocessing pipeline extracts and validates the PDF, exports CSV/Parquet and an audit, creates DuckDB views, and builds the 300-card RAG corpus. The source contains 1,125 candidate/list entries across 205 constituencies. EDA distinguishes these two units to avoid double-counting constituency totals.

The default preprocessing destination is `dataset/`; use it only when intentionally replacing the active data, with the app stopped. Then rerun `python -m src.evaluation.preflight` before a new comparison. A rebuilt DuckDB file can have a different binary hash despite identical logical contents. Do not resume archived runs against changed inputs.

## Retrieval and new evaluation runs

The pinned frozen encoder is `intfloat/multilingual-e5-small`, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`. To download its public weights and prepare the local index once:

```bash
python -m src.model_training --prepare-encoder --download-model
```

Omit `--download-model` to require already cached weights. This step computes embeddings; it does not train a model or contact a generation API.

With the encoder cached, use new run IDs for local retrieval evaluation and a synthetic runner check:

```bash
python -m src.evaluation.compare_rag --mode retrieval-only --run-id local-retrieval-check
python -m src.evaluation.report --run-id local-retrieval-check
python -m src.evaluation.compare_rag --mode fake --pace 0 --run-id local-runner-check
```

Fake-mode scores test the evaluation machinery and **must not be reported as model performance**. Settings, exclusions, quotas and the scoring protocol are in [configs/text_to_sql_comparison.json](configs/text_to_sql_comparison.json), [the evaluation guide](src/evaluation/README.md) and [preflight](experiments/preflight/comparison_preflight_2026-09-30.md). There was no gradient training or post-result hyperparameter tuning in the four-condition comparison.

Live evaluation is optional, sends prompts to the selected provider and can consume quota. Only run it when intended, with credentials configured locally. For a new Claude run:

```bash
ANTHROPIC_MODEL=claude-haiku-4-5 python -m src.evaluation.compare_rag \
  --mode live --provider claude --max-calls 120 --pace 1 --run-id new-haiku-study
```

This command is not necessary to check the saved results. A future provider response may differ even with the same settings.

## Docker build, test and execution

[Dockerfile](Dockerfile), [.dockerignore](.dockerignore) and [compose.yaml](compose.yaml) define the container workflow. Secrets, local model artifacts, caches and document exports are excluded from the build context. The default build downloads the pinned public E5 encoder, so subsequent local retrieval can run without a network connection.

```bash
docker build -t electoral-rag:local .
docker run --rm --network none electoral-rag:local python -m pytest -q
docker run --rm --network none electoral-rag:local python -m src.evaluation.verify_saved_run \
  --run-id text-to-sql-comparison-haiku-20260930
docker run --rm -p 127.0.0.1:8501:8501 electoral-rag:local
```

Open `http://localhost:8501`. Local search works without an API key; answer generation requires one. If you have a configured `.env`, pass it only at runtime:

```bash
docker run --rm --env-file .env -p 127.0.0.1:8501:8501 electoral-rag:local
```

Use [.env.example](.env.example) as a reference; do not overwrite an existing `.env`. Automatic provider fallback is disabled in Compose to keep the selected model explicit.

To keep regenerated EDA files on the host:

```bash
mkdir -p output/docker
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -e MPLCONFIGDIR=/tmp/matplotlib \
  --mount type=bind,source="$(pwd)/output/docker",target=/app/output \
  electoral-rag:local python -m src.eda --output-dir output/eda
```

Compose equivalents:

```bash
docker compose build
docker compose --profile test run --rm tests
docker compose up app
```

Compose keeps application output in a named `results` volume. For a build without downloaded encoder weights, set `--build-arg INCLUDE_E5=0`; dense retrieval then requires separate preparation. To archive a fully resolved container, retain its image digest and `/opt/image-requirements.txt`. The Python base tag is versioned but not pinned to an immutable digest; `PYTHON_IMAGE` accepts a verified digest when available.

**Validation limit:** Docker is not installed in the development environment used for this reorganization. The image has not been built or run here; Linux dependency compatibility remains to be checked with the commands above. Host test, preprocessing and saved-run verification results are reported separately and do not substitute for a container test.

## Report and preserved outputs

- [EDA report section](docs/eda/REPORT_SECTION.md), with tables and graph files under `docs/eda/`.
- [Report version guide](docs/report/README.md) and [latest results text](docs/report/results/PROJECT_REPORT_RESULTS.md).
- [Latest Word report](output/docx/PROJECT_REPORT_RESULTS.docx); earlier PDFs and Word exports remain in `output/`.
- Saved comparison figures and metric tables: `experiments/reports/text-to-sql-comparison-haiku-20260930/`.
- [RAG implementation guide](docs/RAG.md).

The measured improvements support retrieval augmentation as a useful way to resolve electoral entities. They do not establish general model accuracy: the benchmark is small, unfrozen and development-only, source geography has a known ambiguity, and value-based scoring is not a complete audit of every generated explanation.
