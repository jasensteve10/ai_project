# Project report

- Main submission: [eight-page PDF](../../output/pdf/PROJECT_REPORT.pdf).
- Editable source: [PROJECT_REPORT.md](PROJECT_REPORT.md), in English.
- Full EDA supplement: [EDA_REPORT.md](../eda/EDA_REPORT.md) and [illustrated HTML](../eda/index.html).
- Derived retrieval/live result tables: [tables/](tables/).
- Input hashes and build versions: [manifest.json](manifest.json).

The report follows the course's 4-8 page PDF limit and includes the dataset audit,
EDA, retrieval architecture, hyperparameters, preliminary coverage comparison,
actual live checks, limitations and references. It distinguishes unreviewed
retrieval labels, scripted harness checks and real model responses. It does not
claim completion of the held-out evaluation or a training experiment.

Rebuild without making model calls:

```sh
uv pip install --python .venv/bin/python -r requirements-report.txt
MPLCONFIGDIR=/tmp/edan-matplotlib .venv/bin/python -m src.analysis.project_report
```

The Markdown narrative is deliberately editable. The builder recomputes charts and
CSV summaries from saved traces and EDA tables; update the narrative if those inputs
change. It verifies an eight-page result and records source/output hashes.
The final PDF was rendered and all eight pages visually checked for legibility,
complete tables, captions, page numbering and absence of clipping.
