# Audit resolution — 27 September 2026

The defects in the 26 September audit have been repaired in the ingestion and SQL
baseline. The original audit files are retained unchanged as the before-state.
No live Gemini request, paid usage, or model-accuracy experiment was performed.

## Dataset: causes and verified result

The PDF contains thin rectangles for table rules and large rectangles for shaded
backgrounds. Treating both as borders split constituency cells and assigned some
candidates to the previous constituency. Some bottom rows have no closing border,
and two constituency blocks begin on a page before their labels appear on the
next page. Simple forward filling therefore lost data and corrupted assignments.

The extractor now uses rule geometry, excludes background edges, closes tables
at the actual bottom of their rules, and carries unresolved candidate blocks
forward until their constituency metadata is available. Vertical region text is
read bottom-to-top using glyph coordinates; real word spaces are preserved.

| Check | Audited artifact | Rebuilt artifact |
|---|---:|---:|
| Candidate/list rows | 1,124 | 1,125 |
| Distinct constituency IDs | 204 | 205 |
| Rows missing each audited statistic | 20 | 0 |
| Constituency names for ID 088 | 2 fragments | 1 complete label |
| Missing ID 115 | Yes | Recovered from page 20 |
| Constituencies with no elected row | 5 | 0 |
| Constituencies with two elected rows | 6 | 0 |
| Region labels | Reversed, spaces damaged | 33 labels read in source direction |

Pages 1, 2, 10, 17, 20 and 31 were rendered and visually inspected. Checks against
all 35 pages show that every constituency's candidate scores equal expressed
ballots minus blank ballots, and all source percentages agree with their counts
within the source's two-decimal rounding. There is one marked elected row for
each of the 205 observed constituencies. These are candidate/list rows, not seats.

All seven extracted totals equal the printed national totals on page 1:

| Metric | Extracted and printed value |
|---|---:|
| Polling stations | 25,338 |
| Registered voters | 8,597,092 |
| Voters | 3,012,094 |
| Invalid ballots | 68,525 |
| Expressed ballots | 2,943,569 |
| Blank ballots | 29,578 |
| Candidate/list votes | 2,913,991 |

The recovered page-20 candidate row contributes 3,760 votes. Page-break candidates
previously assigned to 060 and 181 now belong to 061 and 182. CSV, Parquet and
DuckDB have all been rebuilt; each candidate includes source page/table/row.
Hashes and reconciliation results are in
[`edan_2025_validation.json`](../dataset/clean/edan_2025_validation.json).
Table/row numbers refer to the corrected extraction, using one-based indices.

## SQL and application fixes

- Schema building validates constituent values before mutation and uses a
  transaction. Its constituency view no longer conceals conflicting values.
- Prompts use the actual mart names and columns, with a live column/type catalog;
  all six examples execute against a freshly built temporary database.
- Turnout examples distinguish regional weighted turnout from an explicitly
  requested mean of constituency rates. Fake citation markers were removed.
- The retired hard-coded model was removed. `GEMINI_MODEL` is required for real
  requests, with a sanitized environment example. The example model is listed in
  Google's [lifecycle documentation](https://ai.google.dev/gemini-api/docs/deprecations),
  checked 27 September 2026. Account access/free quota remains untested.
- SQL checks are parser-based, scope-aware for CTEs, qualify unqualified mart
  names, and enforce limits through the AST. Semicolons and words such as `drop`
  in literals are valid. Root DDL/DML, external sources, recursive queries and
  unapproved functions are rejected.
- Queries use a disposable process, a deadline, read-only DuckDB, disabled
  external access/extension loading, a memory budget and no disk spill.
- Structured answer/clarification/unsupported statuses prevent abstention from
  entering SQL repair. Initial and repair transport errors are caught, transient
  failures have bounded backoff, and traces separate stages and count attempts.
- Session caches retain only successful query results, expire after five minutes,
  and include data/schema/prompt/model/code versions. History clearing clears
  the cache. UI copy describes a pretrained model querying the dataset.
- Pinned dependencies, a resolved lock file, module entry points, offline tests,
  and a runbook provide a reproducible execution path.

## Verification and remaining scope

The regression suite covers fresh PDF extraction, stored-artifact equivalence,
ballot reconciliation, refusal to build inconsistent data, positive/adversarial
SQL, prompt execution, external-access denial at the DuckDB layer, query deadlines,
mocked generation/repair/transport failures, cache invalidation, and Streamlit
success/abstention/history clearing. Run `python -m pytest -q` from the repository
root; no API key is needed.

Final result: **48 tests passed**. Python syntax compilation and `git diff --check`
also passed. The installed Google SDK emits one Python deprecation warning;
there were no failing checks. Artifact hashes and the final audit scope are saved
in [data_audit_2026-09-27.json](data_audit_2026-09-27.json).

Model output quality and real account/model availability remain unmeasured. The
requested baseline repairs do not implement the proposed retrieval comparison,
benchmark, ontology, semantic cache, or deployment. Party aliases and source
spelling variations are preserved rather than merged without evidence.
