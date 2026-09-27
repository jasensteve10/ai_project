# Project context for continuing work

Last updated: 26 September 2026.

## User intent

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

## Established state

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

Next engineering priority: repair and verify data ingestion and the SQL baseline, then build the benchmark/trace runner before extending retrieval. Agree practical scope using the pending constraints; do not require the user to reapprove the evaluation-only direction they already reported as approved.
