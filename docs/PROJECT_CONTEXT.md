# Project context for continuing work

Last updated: 27 September 2026.

## Engineering update — 27 September 2026

The user requested implementation of the audit fixes. Data ingestion and the SQL
baseline have now been repaired, with CSV/Parquet/DuckDB rebuilt from the source.
The corrected extraction has 1,125 candidate/list rows and 205 constituencies;
all seven printed national totals reconcile exactly. SQL validation, execution
deadlines, structured abstention, transport retries, versioned session caching,
model configuration and reproducible setup are implemented. See
[AUDIT_RESOLUTION_2026-09-27.md](AUDIT_RESOLUTION_2026-09-27.md) and the root README
for details and verification. The assessment and original audit remain historical.
No live model request or paid usage was made. Next research work is the benchmark
and trace/experiment runner, followed by controlled retrieval comparisons.

## Evaluation harness — 27 September 2026 (later)

The evaluation phase from RESEARCH_PLAN.md is implemented as tooling. No results
exist yet. The chosen scope is the full core study (A, B, C, D, F1, F3 plus ORACLE and
FULL diagnostics). The dense retriever is `intfloat/multilingual-e5-small`, and the
runner is dry-run by default. See the README section "Retrieval evaluation".

- **Corpus:** 300 cards (7 schema, 12 domain from `src/semantic/glossary.json`, 281
  entity). Static retrievers use per-kind quotas. On dev, a global top-k starved the
  model of schema cards.
- **Benchmark:** 20 dev and 60 test questions, drafted by Claude with gold results
  computed by the production validator. All are `draft`. **The user must review them
  against the PDF and freeze the set before any test run.**
- **Validator bug fixed:** sqlglot 30 models AND/OR/EXISTS as functions, so the
  function allow-list rejected every query with AND or OR. Before this fix, the live
  app would have failed all such questions. The SQL policy version is now 3, and
  regression tests were added.
- **Tests:** 85 pass offline. A fake-mode run returning gold answers scores 100% on
  every condition, which checks the harness only.
- **Early dev coverage (retrieval-only, labels unreviewed):** complete evidence is
  dense 94%, hybrid 89% and BM25 61%. BM25 misses vocabulary gaps such as "gagné" vs
  `vw_vainqueur` and "indépendantes" vs `INDEPENDANT`. This is provisional and not a
  reported result.
- **Next steps:**
  1. Review and freeze the benchmark.
  2. Run the dev retrieval-only run, then `select`.
  3. Run live dev with a call cap. Confirm the free quota first, and agree any paid
     cap before paid use.
  4. Run live test, then the report.

## A versus RAG, first live measurement (Claude Haiku 4.5) — 30 September 2026

- **Setup:** predeclared dev comparison (`src/evaluation/compare_rag.py`), run
  `text-to-sql-comparison-haiku-20260930`, `claude-haiku-4-5` at temperature 0, 18 dev
  questions (14 answerable) x 4 conditions, 1 repeat. Report:
  `experiments/reports/text-to-sql-comparison-haiku-20260930/results.md`.
- **Usage:** 73 calls (1 timeout, retried), 237,673 input and 6,129 output tokens,
  about $0.27 at list price.
- **SQL-result accuracy:** A 11/14, B1 BM25 14/14, B2 E5 13/14, B3 hybrid 13/14.
  Paired versus A: B1 won 3 and lost 0 (exact p = 0.25); B2 2-0 (p = 0.50);
  B3 3-1 (p = 0.625). **None is statistically significant.**
- **Failures:** A's misses are entity spellings (accented `BOUAKÉ`, hyphenated
  `SAN-PEDRO`, source `SAN PEDRO`) and an under-specified name match with `LIMIT 1`,
  which is the failure entity cards target.
- **Caveats:** dev split; unreviewed labels; Haiku, not the Gemini primary; a single
  repeat. Held-out test not run.
- **Fixes made for this run:** SDK 1.x dropped `temperature` (now sent through
  `extra_body`); the API key needs `ANTHROPIC_WORKSPACE_ID`.

## Claude fallback — 30 September 2026

On the user's request, an opt-in Claude fallback was added for when Gemini fails
(`src/agent/llm.py`).
- **Off by default:** it runs only with `LLM_FALLBACK=claude`.
- **Paid:** the Anthropic API has no free tier. Calls are capped per process by
  `CLAUDE_FALLBACK_MAX_CALLS` (default 20), and the default model is `claude-opus-5`.
- **Traceable:** the model that answered is recorded in every result.
- **Experiments:** runs never mix providers; `--provider claude` runs a whole study on
  Claude.
- **Not yet tested live:** no live Anthropic call has been made. A spending cap must be
  agreed before enabling it.

## User intent

EDA completed in English on 27 September 2026: see
[full analysis](eda/EDA_REPORT.md), [report-ready section](eda/REPORT_SECTION.md)
and [HTML with embedded figures](eda/index.html). The reproducible command is
`python -m src.analysis.eda` after installing `requirements-eda.txt`. It compares
all 35 PDF pages with the CSV, applies 21 integrity checks, and produces nine
PNG/SVG figures plus detailed tables. National turnout is 35.04%, versus 42.22%
mean constituency turnout. Source-confirmed unusual values are retained. The
source files were not modified by the EDA. The work does not add model-accuracy
results or change the planned research scope.

- Evaluate and improve the existing election chatbot project.
- Use it for a Deep Learning with Python course project focused on comparing retrieval methods in an agentic system, with appropriate methodologies and metrics.
- The user reports instructor approval for the election dataset and an evaluation-focused project; training is not expected.
- Provide continuing help with research framing, current primary-source research, implementation, experiments, scientific writing, semantic-layer/ontology ideas and caching.
- Use the project as evidence in a job interview related to AI programme leadership. Prepare a clearly hypothetical enterprise/banking adaptation of the local project, discussing requirements such as sovereignty without claiming they are implemented or comprehensively solved.

## Confirmed constraints

- Deadline: 2 days 10 hours (58 hours) from the user's clarification on 26 September 2026.
- Team: working alone.
- Resources: user's Mac M5; no separate GPU/cloud resource stated.
- Existing election dataset approved; enlarging it with other Côte d'Ivoire election data is an option only if necessary.
- Training is not expected. Do not spend the remaining time adding it solely to satisfy the generic rubric wording.
- Existing Gemini access is available. Use free quota first; agree an explicit cap with the user before any paid usage. Unified memory size is not yet established.

## Evidence and authority

The course PDF supplies the rubric. The pasted French text is a prior job analysis, not a verified CV, original offer or instruction to perform every suggested activity. Its recommendations do not authorize contacting recruiters, publishing work or building unrelated deliverables.

Course source: `/Users/jasen/Documents/SCHOOL/courses_master_2/[S25] Deep Learning with Python/project/DEEP-LEARNING-Project.pdf`.

Job-context source: `/Users/jasen/.codex/attachments/0bd5799c-8a3f-44b5-8828-83b2e918651b/Pasted text.txt`.

## State at the original audit (26 September)

The current repository is a small Text-to-SQL prototype over Côte d'Ivoire 2025 election results, with a PDF ETL pipeline, CSV/Parquet/DuckDB artifacts, three mart views, Gemini generation/repair and Streamlit caching/UI. Document/vector retrieval, ontology, benchmark, experiment traces and scored evaluation are not implemented.

The audit found extraction issues, schema/prompt mismatches, incomplete SQL safeguards, fragile failure handling, stale-cache risks and missing reproducibility files. The hard-coded Gemini 2.0 Flash model is listed as shut down in Google's current lifecycle documentation.

Read [PROJECT_ASSESSMENT.md](/Users/jasen/Documents/ME/ai_project/docs/PROJECT_ASSESSMENT.md) for evidence and verification limits, [RESEARCH_PLAN.md](/Users/jasen/Documents/ME/ai_project/docs/RESEARCH_PLAN.md) for the proposed experiment, and [data_audit_2026-09-26.json](/Users/jasen/Documents/ME/ai_project/docs/data_audit_2026-09-26.json) for observed CSV statistics and hashes. These documents are assessment outputs; no source-code fixes or performance experiments have been completed.

## Proposed direction, not yet an accepted final scope

Working title: “Evaluating Static and Agentic Retrieval for Structured Question Answering: Accuracy, Robustness and Cost.” Preserve a full-schema baseline because there are only three views. Compare lexical, neural and bounded adaptive retrieval with controlled inference/repair budgets. The 58-hour plan prioritizes 20 development + 60 held-out questions, four core conditions and a complete report. A full ontology, semantic cache, dataset expansion, reranker training and deployment are deferred. A narrow glossary and separate exact-cache workload may fit after the core study.

Metrics should distinguish retrieval relevance, executable result correctness, answer support, abstention, latency, token/tool use and cost. A small evaluated prototype is sufficient; a large multi-agent platform is not the proposed deliverable.

## Working roles

- Research supervisor: sharpen hypotheses, challenge unsupported claims and prevent unnecessary scope growth.
- Retrieval researcher: read primary papers/docs and choose relevant baselines.
- Data/software reviewer: inspect extraction, reproducibility, SQL boundaries and focused verification.
- Evaluation/statistics reviewer: define labels, splits, controls, metrics and uncertainty.
- Scientific editor: turn actual experiment evidence into the concise course report.
- Interview coach: translate technical evidence into a clear decision narrative without inventing experience or production claims.

Use independent subagent reviews when they improve quality; roles do not require separate permanent tasks. PDF skill was used for the brief. Document, spreadsheet and presentation skills should be used when their corresponding artifacts are actually requested. No new plugin installation was needed for the assessment.

## Open inputs

Already answered: deadline, solo work, Mac M5, election dataset approval, no training expectation and existing Gemini access. The user explicitly chose free quota first and a separate agreement before paid usage. No further question blocks assessment/planning. Verify the account/model's free-tier availability before experiments; no paid cap is currently authorized. Any residual Hugging Face requirement can be addressed through an open neural retrieval component without reopening the reported no-training approval.

The hypothetical interview adaptation is saved in [ENTERPRISE_SCENARIO.md](/Users/jasen/Documents/ME/ai_project/docs/ENTERPRISE_SCENARIO.md). It is a design discussion, not an implemented bank system or a second application to build within the deadline.

Next engineering priority after the 27 September repairs: build the benchmark/trace
runner before extending retrieval. Keep the existing evaluation-only scope and
free-quota-first constraint; no paid budget has been authorized.


## RAG and evaluation integration — 27 September 2026

The user's requested RAG is connected to Claude's evaluation implementation.
`src/retrieval/context.py` and `pipeline.py::run_with_context` are shared by Streamlit
and the experiment runner. Modes A/B/C/D/F1/F3 use `configs/core.json`. The corpus
contains 300 stable-ID cards with constituency PDF pages; E5 runs locally from a
pinned revision. The interface offers source-only search without a Gemini call,
context/source inspection and mode-aware session caching.

The configured Gemini key was detected without disclosure. Live integration exposed
and fixed typed-text-block response handling in the current SDK. See
[RAG.md](RAG.md) and [RAG_VERIFICATION_2026-09-27.md](RAG_VERIFICATION_2026-09-27.md)
for commands and actual verification results. The held-out benchmark still needs
human review and freezing; development smoke results are not final study results.


## Project report — 27 September 2026

An English eight-page technical summary is available at
`output/pdf/PROJECT_REPORT.pdf`, with editable Markdown in
`docs/report/PROJECT_REPORT.md`. It includes the audit, EDA figures, RAG method,
18 labelled development-question retrieval results (11/18 BM25, 17/18 E5, 16/18
hybrid complete contexts), and six live checks. Unreviewed labels and absence of
held-out live evaluation are explicit. Rebuild via `src.analysis.project_report`;
all eight pages were rendered and visually checked.
