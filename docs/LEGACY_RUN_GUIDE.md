# Previous run guide

Retained for historical context; use the root README for the submission layout and current commands.

# Côte d'Ivoire 2025 election SQL assistant

A pretrained Gemini model translates independent questions into read-only DuckDB
queries, guided by local retrieval over the supplied election PDF data. The application does not train a model.
Candidate rows can represent lists; elected-row counts are not seat counts.

## Project report

The [eight-page project report](output/pdf/PROJECT_REPORT.pdf) combines the EDA,
RAG method and saved evaluation results. Its [editable source and build instructions](docs/report/README.md)
document which results are preliminary and which evaluations remain outstanding.

## Setup

Verified on macOS arm64 with Python 3.14.4. Install [uv](https://docs.astral.sh/uv/)
if it is not already available, then run from the repository root:

```sh
uv venv .venv --python 3.14.4
uv pip install --python .venv/bin/python -r requirements.lock
source .venv/bin/activate
```

`requirements.txt` pins direct dependencies; `requirements.lock` pins the full
resolved environment used for verification. No API key is needed to rebuild data
or run the automated tests.

## Rebuild and verify

```sh
python -m src.ingestion.ingestion
python -m src.schema.schema
python -m pytest -q
```

The ingestion CLI also accepts `--pdf` and `--output-dir`; the schema CLI accepts
`--parquet`, `--db`, and `--catalog`. Defaults resolve from the project directory.

The extractor ignores large background rectangles, reconstructs vertical region
labels from character positions, closes open table bottoms, and resolves merged
cells that span page breaks. It reads every detected table. Required values,
constituency consistency, vote accounting and rounded percentages are checked
before publication. Extracted totals must reconcile with the PDF's printed totals.

Outputs:

- `dataset/clean/edan_2025_resultats.csv` and `.parquet`: 1,125 candidate/list rows,
  205 constituencies, and page/table/row provenance.
- `dataset/clean/edan_2025_validation.json`: totals, coverage checks and hashes.
- `dataset/db/edan_2025.duckdb`: validated raw table and three mart views.
- `dataset/clean/schema_catalog.json`: generated column/type catalog and version.

The mart builder rejects inconsistent data before changing the database. The
constituency view retains distinct verified values instead of hiding conflicts
with `ANY_VALUE` or `MAX`. Do not rebuild while the Streamlit app has active queries.

## Exploratory data analysis

The [full EDA](docs/eda/EDA_REPORT.md) compares the raw PDF and ingested CSV,
documents the analytical units, and includes distributions, regional and party
summaries, competitiveness, outliers, associations and sensitivity checks.
Use [REPORT_SECTION.md](docs/eda/REPORT_SECTION.md) for a concise project-report
section, or open [the HTML report](docs/eda/index.html) for a version with embedded
figures. PNG/SVG figures and full-precision CSV tables are under `docs/eda/`.

```sh
uv pip install --python .venv/bin/python -r requirements-eda.txt
.venv/bin/python -m src.analysis.eda
.venv/bin/python -m pytest tests/test_eda.py -q
```

The EDA leaves source files unchanged. Its manifest records input/code/output
hashes and dependency versions. EDA tests are skipped if optional EDA dependencies
have not been installed.

## Functional RAG

The Streamlit sidebar exposes BM25, multilingual E5, hybrid retrieval and bounded
agent searches, with retrieved cards and source pages. The application and the
evaluation runner share the same context and generation pipeline.
See [RAG.md](docs/RAG.md) for setup, provenance, CLI commands and verification.

```sh
python -m src.retrieval.cli prepare
python -m src.retrieval.cli prepare --download-model  # semantic/hybrid modes
python -m src.retrieval.cli search "Participation dans le Haut-Sassandra" --condition D
```

## Retrieval evaluation

The evaluation compares how much context each condition gives the SQL generator. The
model, base prompt, validator and repair budget (three repairs) stay fixed; only the
context changes. Nothing is trained; the dense retriever is the pretrained
`intfloat/multilingual-e5-small` bi-encoder.

| Condition | Context sent to the model |
|---|---|
| A | All schema + domain-definition cards; no entity cards (full-schema baseline) |
| B / C / D | Per-kind top-k (schema 3, domain 3, entity k) from BM25 / dense / RRF hybrid |
| F1 / F3 | Dev-selected static retriever, plus up to 1 or 3 model-requested searches (`needs_context`) |
| ORACLE, FULL | Diagnostics: labelled relevant cards only / all 300 cards |

```sh
uv pip install --python .venv/bin/python -r requirements-retrieval.txt
python -m src.retrieval.corpus                                   # 300 cards -> dataset/retrieval/cards.jsonl
python -m src.evaluation.benchmark --compute-gold --validate     # gold results + checks
python -m src.evaluation.runner --split dev                      # retrieval-only (no API calls)
python -m src.evaluation.runner select --run-id <dev run id>     # choose F's retriever/k on dev only
python -m src.evaluation.runner --split dev --mode fake          # offline pipeline check (gold-answering stub)
python -m src.evaluation.runner --split dev --mode live --max-calls 200 --pace 5   # uses your Gemini quota
python -m src.evaluation.report --run-id <run id>                # results.md, CSV, figures
```

- **Benchmark:** [`benchmarks/edan_2025_v1.jsonl`](benchmarks/edan_2025_v1.jsonl) has 20 dev
  and 60 held-out French questions across ten families: lookup, aggregation, ranking,
  join, ratio, alias, ambiguous, unsupported, multi-step and source evidence. Splits
  never share a paraphrase family.
  - Every question starts as `reviewer_status: "draft"`. Check each gold result against
    the PDF pages in `source_pages`, then set it to `reviewed`, or to `excluded` with a
    note. `--freeze NOTE` refuses to freeze while any test item is still a draft.
  - Live test runs refuse an unfrozen benchmark.
- **Scoring:** results are compared by value, never by SQL text. Columns are matched by
  content, extra columns are allowed, and ranked items check order on a declared key
  so ties may swap. Retrieval is scored on evidence slots, where any acceptable card
  fills a slot: slot recall, nDCG@k and complete-evidence success.
- **Traces:** runs write `experiments/runs/<id>/traces.jsonl` and `manifest.json`.
  - The manifest records the benchmark, corpus and runtime hashes, git SHA and versions.
  - Each trace line records context IDs, SQL, rows, stage timings, tokens and call counts,
    plus an outcome and a rule-based error category.
- **Safeguards and statistics:**
  - The answer cache is never used during runs.
  - Runs resume, and an item stopped at the call cap is re-run.
  - Reports give paired differences with 95% cluster-bootstrap intervals over paraphrase
    families.
  - Costs appear only when dated prices are added to `configs/core.json`, and are
    labelled as estimates.

## Run the app

Copy `.env.example` to `.env` and set `GOOGLE_API_KEY` and `GEMINI_MODEL`.
The example uses `gemini-3.8-flash`, which offers a free tier in Google's
[pricing documentation](https://ai.google.dev/gemini-api/docs/pricing).
Check model access and free quota in your own account before making requests.

```sh
python -m streamlit run app/app.py
```

### Optional Claude fallback (paid, off by default)

If Gemini stops working (revoked key, retired model, exhausted quota or an outage), the app
can answer with Claude instead. **The Anthropic API has no free tier; every fallback call is
billed.** Enable it only after agreeing a budget:

```sh
LLM_FALLBACK=claude
ANTHROPIC_API_KEY=...            # from console.anthropic.com
ANTHROPIC_MODEL=claude-opus-5    # default
CLAUDE_FALLBACK_MAX_CALLS=20     # hard cap per app process
```

- **When it switches:**
  - Gemini errors such as a bad key, a missing model or a permission failure switch
    to Claude at once.
  - Temporary errors (429, 5xx, timeouts) retry Gemini first, then fall back after two
    consecutive failures.
  - Gemini is then skipped for five minutes before being tried again.
- **Same pipeline:** Claude receives the same prompt, retrieved context and SQL validator.
  Its output is constrained to the same JSON contract through structured outputs.
- **Refusals:** Anthropic's server-side refusal fallback (`fallbacks: "default"`) is
  enabled. It only affects requests Claude declines.
- **Visibility:** every result records the model that answered. The UI warns when Claude
  answered and when the cap is spent.
- **Cost:** one question can use up to about five model calls (generation, repairs,
  agent searches).
- **Experiments never use the fallback.** A run uses exactly one provider:
  `python -m src.evaluation.runner --mode live --provider claude --max-calls N`.
  The manifest records the provider, and a run cannot be resumed with a different one.
  Results from different providers are separate studies, not one comparison.

Each question is independent. The app displays clarification and unsupported
responses separately from errors. The live smoke script is optional and makes
API calls: `python -m src.agent.test_agent`. Automated tests use fake model
responses and make no paid or live model calls.

## Query behavior

- The prompt uses the selected schema/domain/entity cards and executable examples.
  The corpus is checked against the live database before use.
- Regional/national turnout is `SUM(votants) / SUM(inscrits)` over the constituency
  view; explicitly requested mean constituency turnout uses `AVG`.
- Parser-based validation permits SELECT/set queries and scoped CTEs, qualifies
  allowed views, rejects external sources and unapproved functions, and enforces
  a finite outer LIMIT of at most 500 rows (default 100).
- SQL runs in a disposable process with a five-second deadline, a read-only
  connection, external access and extension loading disabled, two DuckDB threads,
  a 256 MB DuckDB memory budget, and no temporary-disk spill. This is a local
  prototype boundary, not a claim of complete hostile-process isolation.
- Up to four generation rounds include the initial round and three repairs.
  Transient transport errors retry twice with one- and two-second backoff;
  SDK retries are disabled. SQL timeouts stop repair. Results record model,
  temperature, API-call and generation-attempt counts, and stage traces.
- The exact-question cache is per session, successful-query-only, capped at 128
  entries, expires after five minutes, and includes database, prompt, model and
  code versions. Clearing history also clears that session's cache.

See [the fix report](docs/AUDIT_RESOLUTION_2026-09-27.md) for evidence and limits.
The original [assessment](docs/PROJECT_ASSESSMENT.md) is retained as a historical
record. The evaluation harness is implemented, but the benchmark is not yet reviewed
or frozen. Bounded development smoke runs verify the integration; see
[the RAG verification record](docs/RAG_VERIFICATION_2026-09-27.md). Passing regression
tests or scripted gold-answer runs does not measure model accuracy.
