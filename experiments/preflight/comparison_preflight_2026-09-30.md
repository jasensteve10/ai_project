# Text-to-SQL comparison preflight — 30 September 2026

The protocol was fixed before inspecting new live model outputs. The four
conditions use identical questions, Gemini configuration, examples, SQL validation,
execution and repair limits. A uses fixed schema/domain context without search;
B1/B2/B3 use BM25/E5/hybrid context. Internal IDs remain A/B/C/D.

## Checks performed

- Re-extracted the 35-page supplied PDF and reproduced the CSV/Parquet values.
- Executed all 16 answerable development gold queries against the read-only
  database and independently reproduced their results with pandas over the CSV.
- Checked all development evidence-card IDs and absence of cross-split
  paraphrase-family overlap. No API calls were used for these checks.
- Inspected rendered PDF pages 14 and 15. Page 14 has an empty region column for
  constituencies 073–077; page 15 contains the GONTOUGO label. The stored data
  forward-fills GOH from the preceding page. Reproducible extraction therefore
  does not establish that this region assignment is semantically correct.
- Tested a **hypothetical**, in-memory reassignment of 073–077 to GONTOUGO.
  This is a sensitivity check, not a source correction. No input data changed.

## Exclusions declared before the new live run

| ID | Reason |
|---|---|
| dev-006 | Bottom-five regional turnout changes under the region sensitivity check. |
| dev-007 | GOH membership changes; also, a mathematically recomputed turnout can be rejected against the PDF-rounded reference by the current comparison policy. |

The remaining **18 development questions** contain **14 answerable**, **2
ambiguous** and **2 unsupported** requests. dev-015 retains the same winning
region and count under the sensitivity check. All four conditions receive this
same retained set. The 60 test records are not used. No record was marked as
human-reviewed or frozen; findings remain preliminary and database-relative.

## Artifacts and interpretation

- Reproduce: `.venv/bin/python docs/evaluation/audit_dev_comparison.py`.
- Detailed results and input hashes: `comparison_preflight_2026-09-30.json`.
- Fixed comparison settings: `configs/text_to_sql_comparison.json`.
- The original PDF, benchmark, CSV, Parquet, database and retrieval cards are
  unchanged; hashes before and after the audit are compared in the audit output.
- The source issue remains an open data-quality limitation. It must be resolved
  from authoritative source evidence before affected regional analyses are used.
- Executable SQL is not automatically correct. Primary scoring compares returned
  values against independently reproduced references. Wrong SQL results and
  unsupported-question answers are reported separately; these are not a complete
  measurement of hallucination in free-form text.

## Reproducible comparison

```sh
.venv/bin/python -m src.evaluation.compare_rag --mode retrieval-only --run-id text-to-sql-comparison-retrieval-20260930
.venv/bin/python -m src.evaluation.compare_rag --mode fake --pace 0 --run-id text-to-sql-comparison-preflight-20260930
# Real API calls; cap includes transport retries and SQL repairs.
.venv/bin/python -m src.evaluation.compare_rag --mode live --max-calls 120 --pace 12 --run-id comparison-new-run
.venv/bin/python -m src.evaluation.report --run-id comparison-new-run
```

Use a new run ID for a new experiment. An unchanged interrupted run can resume
only unfinished budget-stopped items. Completed wrong answers are retained;
there is no selective rerunning until success. Every attempted provider call is
logged without raw prompts, responses or credentials. Bounded transport retries
and SQL repairs are part of the same prespecified policy for all conditions.
