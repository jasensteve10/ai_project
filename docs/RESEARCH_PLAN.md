# Research plan: static versus agentic retrieval

Status: proposed design, 26 September 2026. No experimental results have been produced. The user has confirmed **58 hours until submission, working alone, with a Mac M5**. The election dataset is approved and training is not expected. Existing Gemini access is available: **use free quota first and agree a cap before paid usage**. Verify free-tier eligibility before running experiments. Exact machine memory remains unknown.

## Submission scope: 58 hours

Prioritize a small complete experiment over the broader options below. Use the existing election dataset after repair; do not add another election corpus unless the present source proves unusable. Do not add model training, a graph database, cloud deployment or a multi-agent framework for this submission.

Core deliverables: a reproducible SQL baseline, a frozen benchmark, lexical and neural retrieval baselines, one bounded adaptive retrieval policy, scored results with error analysis, and the course report. Keep the model and SQL repair policy fixed. A short domain glossary is useful; a full ontology and semantic answer cache are optional future work. Evaluate the existing exact cache separately only if the core experiment and report are already secure.

| Elapsed window | Priority and decision gate |
|---|---|
| Hours 0-8 | Fix setup, data defects, schema/prompt mismatch and model configuration. Inspect data source discrepancies; explicitly exclude unresolved examples instead of inventing values. Get five development smoke questions working. |
| Hours 8-20 | Build a headless runner and labels. Target 20 development + 60 held-out questions with separate intent/paraphrase families; retain a small independently rechecked subset. Freeze data and test set. |
| Hours 20-34 | Run full-schema, BM25, dense and bounded-agent conditions. Add hybrid only if straightforward. Stop tuning in time to retain complete traces and analyze results. |
| Hours 34-46 | Compare correctness, evidence recall and latency/cost; inspect representative failures; draft a 6-7 page report including references. |
| Hours 46-58 | Reproduce the main table from saved runs, verify report claims and submission files, prepare a short local demo and hypothetical bank adaptation. Keep this window as contingency, not a new-feature sprint. |

These are elapsed windows, not an expectation of 58 continuous working hours; reserve sleep and breaks. If label review is the bottleneck, use a smaller clearly described pilot (for example 30 held-out questions) and limit conclusions. If API access fails, pivot early to a local supported generator or a retrieval-only experiment, explicitly recording that agentic end-to-end evidence is incomplete in the latter case. Do not present an unrun method as evaluated.

## Topic and research question

**Recommended title:** Evaluating Static and Agentic Retrieval for Structured Question Answering: Accuracy, Robustness and Cost.

**Specific application:** electoral questions answered through validated SQL, with retrieval of schema descriptions, domain definitions and source evidence.

**Primary question:** Under a declared inference budget, when does adaptive retrieval improve correct, supported answers over a strong fixed retrieval pipeline and a full-schema SQL baseline?

This is a study of system behavior with a fixed model, not a clean measurement of a model in isolation. Changing model, data cleaning, prompts, retrieval and repair policy simultaneously would prevent attributing improvements.

The current database exposes only three views. Full-schema prompting may win on both quality and latency. Such a result is scientifically useful. Do not manufacture complexity to force agents to win. A separately labelled scale/generalization experiment is needed before claiming benefits for large enterprise schemas.

## Working hypotheses

1. Lexical retrieval handles exact IDs and party names well; dense retrieval may help paraphrases and synonyms. Hybrid retrieval may combine those strengths.
2. Iterative retrieval may help questions needing multiple pieces of evidence, while costing more for simple lookups.
3. Explicit aliases and metric definitions may improve semantic correctness even when SQL already executes successfully.
4. Caching may lower repeated-workload latency and cost, but semantic reuse can introduce wrong-entity or stale-answer failures.

These are hypotheses to test, not expected results to report as established facts.

## Define retrieval before building infrastructure

Use stable IDs and provenance for each retrievable unit:

- Schema cards: one view/table or coherent column group, types, keys and valid joins.
- Domain cards: metric definitions, denominators, aggregation grain, missing-data rules and terminology.
- Entity cards: canonical IDs, aliases and relevant value examples.
- Evidence units: PDF passages/table regions tied to document hash, page and source row, when source evidence is required by the question.

Separate schema/domain retrieval from document-evidence retrieval in evaluation. A schema match is not a citation to a factual election result. Numerical filtering, sums, ratios and ranks should continue to use SQL over audited data. Serializing every numeric row as prose does not make semantic search a reliable aggregation engine.

At this corpus size an in-process lexical index and local embedding matrix may suffice; a new hosted vector database is not a prerequisite.

## Experiment matrix

| ID | Condition | What changes |
|---|---|---|
| A | Full schema + fixed domain definitions | Strong SQL baseline, no schema selection. |
| B | BM25, one retrieval pass | Lexical selection of context. |
| C | Dense retrieval, one pass | Neural context selection. |
| D | BM25 + dense with reciprocal rank fusion | Ranking combination. |
| E, optional | D + reranking | Extra ranking stage only. |
| F | Best development-selected static retriever + bounded agent | Decide whether to reformulate, fetch missing evidence or stop. |
| Diagnostic | Human-labelled relevant context | Oracle context to isolate downstream generation/execution errors. |

For the confirmed deadline: A, B, C and F are the core study; D is optional and E is out of the initial submission scope. Select the static retriever used by F on development questions only. A document-evidence task needs its own complete-context or fixed-evidence baseline; A alone is not a full-document baseline. Keep schema/domain context retrieval as the primary target; add full source-document answer generation only if it fits after core results.

Freeze the database snapshot, generator model, SQL validator, base instructions, output format and SQL-repair allowance. Run zero versus three SQL repairs as a separate ablation if useful. Retrieval iteration and SQL repair are different mechanisms.

Compare agent retrieval caps of one and three calls. Set a maximum cumulative context/token budget, tool-call cap, wall-time cap and stop rule. Report both a matched-budget comparison and a quality/cost curve across budgets. Charge all planning, reformulation, reranking and repair calls to the corresponding condition. A larger allowance can be a legitimate deployment option, but must not masquerade as a free methodological gain.

A recent controlled study directly motivates measuring search depth and inference budgets, but does not determine what will work for this dataset: [McCleary and Ghawaly, 2026](https://arxiv.org/abs/2603.08877).

## Gold benchmark

For this submission, target 20 manually checked development questions and 60 held-out questions. A later extension could grow to 30-50 development and 100-150 held-out questions. These are practical targets, not a statistical power guarantee. Freeze test questions before tuning and log any later corrections to invalid gold labels.

Question families: lookup, aggregation, ranking/ties, joins, ratios, entity aliases, ambiguous requests, multi-step questions, unsupported questions and source-evidence questions. Preserve French language as the primary domain unless bilingual evaluation is an explicit goal.

Each benchmark record should contain:

```text
id, question, split, intent_family, entity_family, difficulty,
answerability, gold_sql_or_answer_specification,
gold_result, result_comparison_policy,
relevant_schema_ids, relevant_evidence_ids,
acceptable_alternative_evidence_sets,
source_pages, annotation_notes, reviewer_status
```

Human-check gold answers against the original PDF and audited data. Use independent review for a subset and adjudicate disagreements. LLM-generated questions are drafts, not ground truth. Gold SQL must not be generated and scored by the same unverified pipeline.

Split by intent/template and related paraphrase families to prevent near-duplicate leakage; use a stricter entity-held-out slice when feasible. Document whether evaluation is in-domain (same available corpus/database) or generalization to a new domain. Retrieving test-time documents is normal; including test questions or their gold solutions in prompt examples is leakage.

Do not add a training split if no parameters are trained: use development and held-out test sets and describe them honestly. Add a distinct training pool only for a learned component.

## Metrics and analysis

| Layer | Primary measures | Interpretation |
|---|---|---|
| Retrieval | Recall@k; nDCG@k; required table/column recall; complete-evidence-set success | Did the retriever find all the needed context? Define relevance and valid alternative evidence sets. |
| SQL | Execution success; normalized result correctness | A runnable query can still answer the wrong question. Avoid SQL-string exact match as the main score. |
| End-to-end | Correct and supported task success; correct abstention; clarification behavior | Includes wrong results, unsupported claims and unnecessary rejection. |
| Efficiency | p50/p95 latency, input/output tokens, retrieval/SQL/model calls, repair count, cost per successful task | Report actual paid API cost when available; label token-price calculations as estimates with dated prices. |
| Reliability | Failure counts and success by question family; repeated-run variation | Diagnose data, retrieval, semantics, SQL, tool, budget and cache errors separately. |
| Optional prose answers | Citation correctness/coverage and calibrated judge scores | Supplement executable gold results and human checks. |

Define result comparison before running: order matters for ranked questions; compare unordered row multisets otherwise; specify duplicate handling, numeric tolerances, NULLs and tied results. Handle truncation explicitly: a silently capped result is not necessarily a complete answer.

Use counterexample database fixtures for important SQL families so an accidentally correct result on one snapshot is less likely to pass. This follows the motivation in [Zhong et al., 2020](https://aclanthology.org/2020.emnlp-main.29/). Treat single-snapshot execution accuracy as a limited measure, not semantic proof.

Report paired differences with confidence intervals; bootstrap at the question-family level when examples are related. Repeat stochastic agent conditions where budget permits and aggregate repeated runs within question before comparing methods. Record seeds where supported; temperature zero does not establish universal deterministic behavior. Report failures and budget overruns rather than silently dropping them. Tail latency from a small sample is noisy; include sample size.

Run algorithm comparisons with answer caching disabled. Warm model/index infrastructure consistently if measuring steady-state latency, report cold startup separately, interleave conditions where service load could bias timings, and account separately for index build time/storage. Never mix cache hits into the uncached model accuracy comparison.

## Semantic layer / ontology experiment

The mart views provide a useful starting point. A lightweight ontology can define:

```text
Election -> Constituency -> CandidateOrList -> Party
Region -> Constituency
Metric -> numerator, denominator, unit, aggregation grain, SQL mapping
Entity -> canonical identifier, validated aliases, source provenance
```

Validate the candidate/list distinction from the source before finalizing the model. Preserve accented/display labels separately from normalized lookup labels. Treat abbreviations and synonyms as reviewed aliases, not substring substitutions.

Example: distinguish `mean_constituency_turnout = AVG(taux_participation)` from `regional_turnout = SUM(votants)/SUM(inscrits)` over unique constituencies. Scores and turnout are stored as fractions, so presentation as percent must scale exactly once.

Controlled ablation: schema only -> schema plus glossary/aliases -> glossary plus constrained relationship expansion. Keep retrieval policy fixed and test alias/metric/join question slices. Add no graph database unless it provides a measured benefit.

GraphRAG's original task is global question-focused summarization over text collections. It is useful related work, not evidence that a graph will improve exact electoral SQL: [Edge et al., 2024](https://arxiv.org/abs/2404.16130).

## Cache experiment

After the main uncached study, compare no cache, exact cache and conservative semantic cache on a separate sequential workload. Include repeats, paraphrases, near-identical questions with different IDs, changed metrics/year, modified data versions and transient failures.

Define denominators explicitly: hit rate = all hits / all requests; correct-hit rate = verified correct hits / all hits; incorrect-reuse rate = incorrect hits / all hits. Also report overall workload answer correctness, stale-hit count, latency and total cost. A low wrong-hit count is not evidence of a safe cache if the sample is tiny.

Keys or invalidation rules must account for corpus/database hash, schema, model, prompt, semantic layer and access scope where applicable. Keep entity/metric constraints in semantic matching. Choose thresholds on development traffic, not the test sequence. Do not preload gold evaluation answers. Report cold and warm workload results separately. Exact answer caching, SQL-result caching, embedding caching and provider prompt caching are different interventions; name the one being measured.

Primary reference: [GPTCache, Bang 2023](https://aclanthology.org/2023.nlposs-1.24/).

## Deep-learning content and optional training

Accept the user's reported evaluation-only approval. Explain the pretrained components: a neural bi-encoder places queries and passages in an embedding space; a reranker scores query/context relevance jointly; the language model generates SQL or selects retrieval actions. Compare their measurable contributions against a lexical baseline.

One candidate family to benchmark is Qwen3 Embedding/Reranking, which includes 0.6B variants; hardware suitability and latency still need a local pilot. It is a candidate, not a claim of current best performance. Source: [Qwen3 Embedding paper](https://arxiv.org/abs/2506.05176).

If training becomes necessary or scientifically useful, choose one small reranker or retriever adaptation. Train on separate query-positive-negative examples, compare pretrained versus fine-tuned with everything else fixed, and include leakage controls. A complexity router is another possibility, but it introduces an additional subsystem. Do not train a generator simply to add a training section.

## Delivery order and acceptance evidence

| Phase | Deliverable | Exit evidence |
|---|---|---|
| 1. Stabilize | Reproducible environment, corrected ingestion, reconciled data, matching schema/prompt, configurable model | Clean setup works; data checks and representative gold SQL checks pass. |
| 2. Instrument | Headless query interface, benchmark format, experiment configs and JSONL traces | Every outcome has question/config IDs, timings, token/tool counts, cache status and classified errors. |
| 3. Establish baseline | Gold dev/test sets and full-schema/BM25/dense runs | Gold labels reviewed; no test tuning; scored raw outputs retained. |
| 4. Compare retrieval | Hybrid if feasible and bounded agent | Controlled comparison and error analysis, including negative findings. |
| 5. Focus extension | Semantic-layer ablation first; cache workload second if schedule permits | Benefit and failure cases measured independently. |
| 6. Communicate | Course report and compact interview demonstration | Every chart and claim traces to a run; limitations are stated. |

Allocate most time to data/benchmark correctness and experiments, with a smaller share to the interface and packaging. Docker/cloud deployment and a large orchestration framework are deferred portfolio work. Do not spend the final project window on them before the experiments are complete.

Suggested future module boundaries: `data_quality`, `semantic`, `retrieval`, `agent`, `evaluation`, `configs`, `experiments`, and `reports`. Introduce them gradually; the existing repository need not be rewritten wholesale.

## Report plan within 4-8 pages

1. Problem, research question and contribution (about 0.5 page).
2. Dataset, key EDA and extraction/annotation limitations (about 1 page).
3. Methods, pretrained components and experimental controls (about 1.5 pages).
4. Results: main comparison, cost/latency trade-off and one ablation (about 1.5 pages).
5. Error analysis, threats to validity and conclusion (about 1 page).
6. References and compact settings table (about 0.5-1 page).

Explain the approved absence of training and describe inference/development tuning instead of inventing a training pipeline. Keep results placeholders visibly pending until runs exist. Use one main comparison table, a quality-versus-cost plot and an error breakdown; save detailed traces/configurations with the code.

## Interview use

The verified [Nexoris posting](https://www.collective.work/jobs/fr/program-leader-ia-reow) emphasizes senior programme governance, AI consumption measurement, agentic strategy and security. It names an anonymous investment bank; the pasted Natixis/BPCE identification remains unconfirmed. A small prototype demonstrates technical judgment, not the requested years of programme leadership.

Derive a one-page decision memo from the same experiments: problem, alternatives, measured quality/cost, operational risks, recommended scope and next decision. Show one query trace and a compact consumption dashboard. Describe public-data experiments as a prototype, not a bank deployment.

Honest current pitch: “I am building an evaluation framework to identify when agentic retrieval is worth its additional cost. I compare baselines, trace errors and turn the results into a documented engineering decision.” Replace intentions with measured claims only after experiments run.

## Further primary reading

- [BEIR, 2021](https://arxiv.org/abs/2104.08663): lexical and neural retrieval evaluation across datasets; use to justify strong baselines, not transfer its scores to this corpus.
- [Reciprocal Rank Fusion, 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf): simple combination of ranked lists.
- [CHESS, 2024](https://arxiv.org/abs/2405.16755): database context retrieval, schema selection and SQL generation/revision; especially relevant to this application.
- [Adaptive-RAG, 2024](https://aclanthology.org/2024.naacl-long.389/): retrieval strategy selection by complexity. Its trained classifier is not reproduced by a prompt-only router.
- [BIRD, 2023](https://proceedings.neurips.cc/paper_files/paper/2023/hash/83fc8fab1710363050bbd1d4b8cc0021-Abstract-Datasets_and_Benchmarks.html): real database grounding and execution-oriented evaluation.
- [Spider 2.0 project](https://spider2-sql.github.io/): enterprise Text-to-SQL workflows; useful external perspective, not a requirement to reproduce the full benchmark.
- [RAGAS, 2024](https://aclanthology.org/2024.eacl-demo.16/): complementary retrieval/generation diagnostics. Calibrate automatic judges; do not replace executable gold answers with a single judge score.

Sources were checked during this review. This is a focused reading list, not an exhaustive survey or a claim of state-of-the-art coverage.
