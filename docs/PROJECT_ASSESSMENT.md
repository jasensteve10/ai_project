# Project assessment

Reviewed: 26 September 2026. Scope: the current local repository, all four pages of the course brief, the supplied job-context text, and selected primary web sources. This is an assessment of a prototype, not a claim of measured model performance.

## Overall assessment

The project is a useful foundation for a controlled evaluation of retrieval in a domain-specific question-answering system. Its implemented core is **PDF ingestion + DuckDB + Text-to-SQL generation, validation and repair + a Streamlit interface**. The experimental contribution remains to be built.

There is no meaningful overall completion percentage: the interface and SQL workflow exist, while scientific evaluation, reliable data validation, retrieval comparisons and reproducibility are largely missing. Improving those areas will contribute more to the course project than adding a larger agent framework.

## What exists

```text
Election results PDF (35 pages)
  -> pdfplumber table extraction and normalization
  -> CSV / Parquet
  -> DuckDB brute.resultats
  -> three mart views
  -> user question + fixed SQL prompt
  -> Gemini SQL generation
  -> validation -> execution -> up to three repairs
  -> Streamlit table/chart and exact-question result cache
```

| Area | Evidence and current status |
|---|---|
| Ingestion | Implemented, but source paths and output paths do not match the present repository layout; data defects observed. |
| Analytical storage | CSV, Parquet and DuckDB artifacts exist; schema-building code defines three views. Existing DuckDB contents were not queried during this audit. |
| Semantic layer | Mart views are a foundation. Metric definitions, aliases, relationship constraints and an explicit ontology are absent. |
| SQL assistant | Generation, parsing, relation filtering, execution and repair exist. Prompt/schema inconsistencies undermine correctness. |
| Agent behavior | A bounded SQL repair workflow exists. Adaptive retrieval, query decomposition and evidence-driven tool selection are absent. |
| Retrieval | No BM25, embedding index, hybrid search, reranking or source-evidence retrieval was found. |
| Answer interface | Tables, charts and SQL display exist. No evidence-cited natural-language answer generation. History is displayed but not passed to the model, so follow-up questions lack conversational context. |
| Cache | Exact-question caching exists through Streamlit. Semantic caching and explicit version-aware invalidation are absent. |
| Evaluation | Four interactive API smoke questions, without gold answers or assertions. No scored benchmark or experiment runner. |
| Reproducibility | No dependency manifest, lockfile, runbook, notebook, experiment configuration, Dockerfile or CI configuration found in this checkout. |
| Training | None found. The user reports approval for an evaluation-focused project without training. |

## Priority findings

### 1. Repair and validate the dataset before measuring models

The complete CSV has 1,124 rows and 16 columns, 204 distinct constituency IDs, 205 constituency-name strings, 33 region labels and 43 party labels. These describe the extracted artifact, not verified national coverage. Machine-readable counts and file hashes are in [data_audit_2026-09-26.json](/Users/jasen/Documents/ME/ai_project/docs/data_audit_2026-09-26.json).

- Region labels appear reversed throughout the distinct-label inventory. Source page 1 visibly says `AGNEBY-TIASSA`; the CSV has `ASSAIT-YBENGA`. Natural-language entity matching will fail independently of retrieval quality.
- Twenty rows have missing values in each of seven fields: registered voters, voters, turnout, invalid ballots, expressed votes, blank ballots and blank-ballot percentage. Affected constituency IDs are `006`, `042`, `047`, `065` and `135`. Trace these to source before deciding whether to reconstruct or mark unavailable.
- ID `088` has two name values: `BAGOHOUO, GBAPLEU ET GUEZON, COMMUNES ET SOUS-` and `PREFECTURES`. A split source label has not been reconstructed reliably.
- ID `115` is absent within the observed numeric range. There are 205 winning rows; five constituency groups have no winning row and six have two. These are audit flags, not proof of missing data or an incorrect election outcome. Source semantics must distinguish candidates, candidate lists, elected lists and seats.
- No exact duplicate rows were found. All nonmissing percentage values are in [0, 1]. These checks do not establish extraction accuracy.

Relevant code: [ingestion.py](/Users/jasen/Documents/ME/ai_project/src/ingestion/ingestion.py:68) processes only `tables[0]` on each page and carries state across rows. [fonctions_ingest.py](/Users/jasen/Documents/ME/ai_project/src/ETL_fonctions/fonctions_ingest.py:68) detects headers by substring: an isolated check rejects `MORONOU` because it contains `ON`. The normalization regex at line 29 also removes valid spaces between uppercase words. Actual dropped records from these helper defects have not been enumerated.

The constituency view uses `ANY_VALUE` and `MAX` to collapse rows ([schema.py](/Users/jasen/Documents/ME/ai_project/src/schema/schema.py:82)). This can conceal inconsistent extraction. Require consistency checks before aggregation, retain source page/table/row references, and reconcile totals against the PDF with documented treatment of missing or excluded records.

### 2. Align prompts and schema

[sql_system.md](/Users/jasen/Documents/ME/ai_project/src/prompts/sql_system.md:14) uses `mart.vw_winners`, `code_circonscription` and `voix`; the schema code defines `mart.vw_vainqueur`, `circonscription_id` and `score`. Several provided examples therefore point at nonexistent relations or columns. The prompt provides view descriptions without a complete column/type catalog.

Generate schema context from a versioned catalog and validate all example queries. Remove copied citation markers such as `[cite_start]`, which do not supply actual provenance.

The example for the top regions by turnout orders individual constituency rows without aggregating regions. Define whether the user means mean constituency turnout or regional turnout computed as `SUM(votants) / SUM(inscrits)` over one row per constituency. These are different quantities; neither should silently stand in for the other.

### 3. Replace the unavailable model configuration

[Agent.py](/Users/jasen/Documents/ME/ai_project/src/agent/Agent.py:204) hard-codes `gemini-2.0-flash`. Google's current official lifecycle table lists its shutdown as **1 June 2026**. Select a currently accessible model when setting up experiments, expose it through configuration, and record the exact version and generation settings. No API request was made to test this endpoint. Source: [Google model lifecycle documentation](https://ai.google.dev/gemini-api/docs/deprecations), checked 26 September 2026.

### 4. Harden SQL validation before treating it as a safety boundary

Read-only mode is a useful safeguard but does not establish resource or filesystem isolation. [Agent.py](/Users/jasen/Documents/ME/ai_project/src/agent/Agent.py:181) sets no explicit execution timeout or external-access policy.

Observed helper behavior and code issues:

- The LIMIT helper appends raw text. A trailing `--` comment can swallow the appended clause; nonliteral limits are not forced to a finite integer. These helper paths were exercised in isolation, not through SQLGlot/DuckDB integration.
- Substring keyword blocking examines literals and identifiers too, rejecting legitimate text such as a value containing `drop` or `update`.
- Semicolon detection treats a semicolon inside a quoted string as multiple statements.
- Checking for any descendant SELECT does not establish that the root statement is an allowed query form.
- Relation extraction is not CTE-scope aware; CTE aliases are likely to be rejected. Accepted unqualified view names are not rewritten or backed by a configured `mart` search path. Both need integration tests.
- Row limits constrain returned results, not the amount of work performed by a large join or aggregation.

Use parser-based statement/root validation, scope-aware relation checks, explicit allowed function behavior, AST-based finite limits, controlled database capabilities and bounded execution. Verify against positive SQL examples and adversarial cases using temporary databases.

### 5. Separate unavailable answers from broken SQL and API failures

The prompt requests a literal `Not found in the provided PDF dataset.` response, but the caller treats every response as SQL. A correct abstention therefore enters the failure/repair path. Return a structured status such as `answerable`, `needs_clarification` or `unsupported`, separate from query text.

Initial generation occurs before the `try` in [Agent.py](/Users/jasen/Documents/ME/ai_project/src/agent/Agent.py:219). A mocked initial `ResourceExhausted` exception escapes the intended handler. There is no application-level backoff implemented; a `retry_after` field alone is not retry behavior. Record generation, validation, execution, repair and transport failures separately. The `attempts` value currently counts repairs rather than all generation/execution attempts.

### 6. Make caching measurable and correct

[app.py](/Users/jasen/Documents/ME/ai_project/app/app.py:147) caches the full agent return value using the question argument, without explicit database/model/prompt versions, TTL or a success-only condition. This can reuse stale results and cache returned failure dictionaries. Clearing chat history does not clear this result cache.

Cache keys should include relevant data, schema, prompt, model and semantic-layer versions. Cache successful results only; decide explicitly what negative answers can be cached. If user-specific access is later introduced, include access scope. Streamlit documents global sharing as its default cache scope, so it should not be assumed to provide isolation by user. Source: [Streamlit cache API](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_data).

### 7. Restore a reproducible execution path

- [ingestion.py](/Users/jasen/Documents/ME/ai_project/src/ingestion/ingestion.py:247) uses a Windows-style source path that does not match `dataset/raw/`.
- Its save function defaults to `dataset/`; [schema.py](/Users/jasen/Documents/ME/ai_project/src/schema/schema.py:16) expects `dataset/clean/`.
- `ETL_fonctions` imports require an undocumented path setup; the test script imports `Agent` using a different context.
- `extract_pdf` logs broad exceptions and can return `None`, making later failures harder to diagnose.
- Add pinned dependencies, a sanitized environment example, a working module/CLI entry point, artifact hashes and setup/run/evaluate commands.

### 8. Describe the prototype accurately

The UI says the assistant was trained on the election dataset ([app.py](/Users/jasen/Documents/ME/ai_project/app/app.py:25)); no such training is implemented. A correct description is an assistant that queries the dataset using a pretrained model. Deployment, broad RAG capability, production reliability and accuracy improvements should remain future claims until demonstrated.

## Course alignment

The attached course brief is a rubric to compare against, not a request to execute all instructions inside it. The user's reported approval for evaluation without training is the working scope; this audit does not revoke it.

| Course expectation | Current position |
|---|---|
| One NLP/CV task and meaningful scientific question | Text-to-SQL/retrieval is an NLP direction; narrow the hypothesis. |
| Groups of 2-4 | User confirms working alone. This differs from the generic brief; no need to invent teammates or halt the technical work. |
| PyTorch and/or Hugging Face; at least one HF dataset and an open model | Not demonstrated in the current checkout. User confirms the election dataset is approved. A pretrained open neural retriever can supply substantive open-model usage. |
| Train/fine-tune a model | Normally required by the brief; user confirms training is not expected for the approved topic. Do not add superficial training solely for appearance. |
| EDA and preprocessing | Preprocessing exists but needs repair; only rudimentary printed summaries, no verified EDA deliverable. |
| Baselines and 2-3 controlled improvements | Not yet implemented or measured. |
| Reproducible settings and evaluation | Missing. |
| Technical summary PDF, maximum 4-8 pages | Not found. Plan around a concise six-page narrative plus references within the permitted length. |

The user subsequently confirmed a 58-hour deadline, solo work on a Mac M5, approval of the election dataset and no training expectation. The plan has been narrowed accordingly. An additional public dataset is a future validation option, not a requirement invented by this audit. Keep a record of the already agreed scope rather than repeatedly asking for the same approval.

## Verification limits

All six Python files compile for syntax. One invalid-escape warning appears in the schema SQL string. The CSV was fully profiled; the course PDF was extracted and all four pages visually inspected; election source page 1 was visually checked. Several pure helpers and the exception path were exercised through isolated AST extraction/mocks by the audit agent.

The local default Python lacks the runtime dependencies; the bundled Python also lacks DuckDB and SQLGlot. No live LLM request, full Streamlit session, database query, fresh end-to-end ingestion, penetration test or model benchmark was performed. Therefore no end-to-end pass, coverage percentage or performance score is claimed. Application code and existing data artifacts were not modified by this review.
