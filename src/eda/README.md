# EDA module

`python -m src.eda` compares the source PDF with the ingested CSV. The implementation
is `pipeline.py`; the historical `src.analysis.eda` import remains compatible.

Use `--output-dir output/reproduced-eda` to preserve the published outputs. Tables,
PNG/SVG figures, the HTML report and provenance manifests are retained in
`docs/eda/` and referenced from the project report.
