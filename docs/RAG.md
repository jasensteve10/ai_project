# RAG implementation and evaluation integration

The application implements retrieval-augmented Text-to-SQL over the audited election
data. Gemini generates SQL using retrieved schema, metric-definition and entity
cards; DuckDB computes the answer. The model is pretrained, and no training is done.
The original PDF is available for download in the interface.

## Run

From the project root, with the database already rebuilt:

```sh
uv pip install --python .venv/bin/python -r requirements-retrieval.txt
.venv/bin/python -m src.retrieval.cli prepare
# Optional first-time download for semantic/hybrid modes:
.venv/bin/python -m src.retrieval.cli prepare --download-model
.venv/bin/python -m streamlit run app/app.py
```

Set `GOOGLE_API_KEY` and `GEMINI_MODEL` in `.env`; `.env.example` provides the format.
The key stays on the server and is not written to experiment traces. Restart the
application after changing credentials. The sidebar's **Recherche dans les sources
uniquement** toggle performs local retrieval without a Gemini request. BM25 needs
no embedding download. Dense/hybrid modes fail with setup instructions when the
model is unavailable locally; they do not silently change retrieval methods.

```sh
.venv/bin/python -m src.retrieval.cli search 'Participation dans le Haut-Sassandra' --condition D
# This command makes real Gemini calls:
.venv/bin/python -m src.retrieval.cli ask 'Quel est le taux de participation national ?' --condition B
```

## Shared application and experiment pipeline

`src/retrieval/context.py` owns the condition definitions and per-kind quotas.
`src/retrieval/pipeline.py::run_with_context` selects cards, constructs the prompt,
attaches bounded searches, invokes the SQL agent, and returns context IDs, search
logs and source cards. Both Streamlit (`ElectionRAG`) and Claude's evaluation runner
call this function. `src/evaluation/conditions.py` retains compatible imports.

| Condition | Behavior |
|---|---|
| A | All schema and domain cards, no entity cards |
| B | BM25 with accent-normalized French tokenization; application default |
| C | Local multilingual E5 embeddings and cosine similarity |
| D | Reciprocal rank fusion of BM25 and E5 |
| F1 / F3 | Configured static retrieval plus at most 1 / 3 additional model-requested searches |

`configs/core.json` is the single source for quotas, the embedding model/revision,
and the static retriever used by F. An unselected F configuration is provisional;
select it on development data before the final study. ORACLE and FULL remain
experiment diagnostics. The application does not load benchmark labels.

Gemini responses can be text strings or typed content blocks. Only answer text is
parsed as JSON. SQL validation, execution deadlines, clarification/abstention and
three-repair limits remain shared across all conditions. The interface presents the
executed result table and SQL; it does not add a second model-generated narrative.

## Corpus, provenance and cache validity

The 300 cards comprise 7 schema cards, 12 domain cards and 281 entity cards
(33 regions/districts, 205 constituencies and 43 party/grouping labels). They are
built from the audited DuckDB views, glossary and EDA dictionary, with stable IDs
compatible with the existing benchmark evidence slots. Constituency cards include
PDF page numbers obtained from candidate provenance. These are structured
representations of the source, not arbitrary free-text PDF chunks.

The application and evaluation runner verify the persisted corpus against a fresh
build before use. Rebuild it after changing the database or glossary. E5's revision
is pinned to `614241f622f53c4eeff9890bdc4f31cfecc418b3`; its embeddings are local,
L2-normalized and cached by revision and corpus content. Normal queries load only
cached model files. The E5 [model card](https://huggingface.co/intfloat/multilingual-e5-small)
documents the query/passage prefixes and model architecture.

Session answer-cache keys include the condition, configuration, database, prompts,
retrieval code, corpus, glossary, dictionary and PDF. Experiment runs bypass this
cache and record the same RAG fingerprint in their manifests. Context sources are
kept in chat history even after switching retrieval modes.

**Evidence distinction:** retrieved cards guide SQL generation. They are not proof
that a national total or complete ranking is supported by just those pages. The
result table comes from SQL over the complete database. Exact result-page references
are displayed when SQL returns `source_page`; the interface separately labels the
pages associated with retrieved constituency cards and records the source PDF hash.

## Evaluation commands

The existing benchmark, gold-result comparison, traces and reporting remain intact:

```sh
# Local retrieval on all 20 development questions; no API calls:
.venv/bin/python -m src.evaluation.runner --split dev --mode retrieval-only --run-id dev-retrieval
# Scripted gold-answer model: plumbing test only, not model accuracy:
.venv/bin/python -m src.evaluation.runner --split dev --mode fake --run-id dev-offline
# Bounded live smoke test, after verifying free quota in the account:
.venv/bin/python -m src.evaluation.runner --split dev --mode live --conditions B,D --ids dev-003,dev-012,dev-019 --max-calls 8 --pace 12 --run-id dev-live-smoke
.venv/bin/python -m src.evaluation.report --run-id dev-live-smoke
```

The call cap includes transport retries and repairs. A budget refusal is recorded
without counting it as a provider call. Changes to the RAG fingerprint prevent
resuming an incompatible run. Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing)
lists a free tier for Gemini 3.8 Flash, checked 27 September 2026; actual billing
status depends on the account. No paid tier activation or model fallback is performed.

The held-out benchmark remains subject to its existing human-review/freeze rules.
Development smoke tests are not an estimate of held-out model accuracy.

## A versus B1/B2/B3 comparison — 30 September 2026

`configs/text_to_sql_comparison.json` defines the reproducible comparison:
A is fixed schema/domain context with no search; B1/B2/B3 are BM25/E5/hybrid
(internal trace IDs B/C/D). The RAG family B is not a fifth condition. The entry
point verifies input hashes against the independent preflight before running.

Two development questions (dev-006/007) are excluded before evaluation because
their answers depend on ambiguous PDF page-14 region carry-forward. See
`docs/evaluation/comparison_preflight_2026-09-30.md`. This leaves 18 questions:
14 answerable, two ambiguous and two unsupported. The test split is untouched.

The completed new work is **local verification**, with no new successful Gemini
responses: all 72 scripted pipeline checks pass, and retrieval-only traces compare
the three actual search methods. Scripted model output comes from reference SQL;
its correctness does not measure SQL generation. On 16 questions with evidence
slots, complete-context counts are BM25 10, E5 15 and hybrid 14. A has no entity
retrieval, so its slot coverage is not a measure of baseline SQL accuracy.

```sh
# Entirely local, using the already cached E5 encoder:
.venv/bin/python -m src.evaluation.compare_rag --mode retrieval-only --run-id local-retrieval
.venv/bin/python -m src.evaluation.compare_rag --mode fake --pace 0 --run-id local-pipeline-check
.venv/bin/python -m src.evaluation.report --run-id local-retrieval
.venv/bin/python -m src.evaluation.report --run-id local-pipeline-check
```

The report exporter distinguishes scripted results from model performance,
reports explicit denominators, verifies the benchmark hash, and exports per-question
outcomes. A future real-model run can compare strict SQL-result accuracy and paired
differences against A; this new local study does **not** establish an accuracy gain
from RAG. No further Gemini requests were made after local-only verification was
requested. Existing 27 September live smoke checks remain separate historical evidence.
