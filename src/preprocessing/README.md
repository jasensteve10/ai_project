# Data and RAG preprocessing

`python -m src.preprocessing` runs PDF extraction and validation, writes CSV and
Parquet, builds DuckDB views and the schema catalog, then builds the RAG cards.
Use `--dataset-dir output/rebuilt-data` to keep the archived experiment inputs intact.

- `pdf_helpers.py`: source-table cleaning and parsing helpers.
- `ingest.py`: extraction, typing, provenance and numerical validation.
- `schema.py`: read-only analytical views and catalog construction.
- `__main__.py`: the complete pipeline; retrieval-card construction is shared with
  `src/retrieval/corpus.py` so the application and preprocessing agree.

Embedding preparation uses `python -m src.model_training --prepare-encoder`.
This is indexing with pretrained weights, not fitting a model. The unresolved
PDF page-14 region assignment is documented in `experiments/preflight/`.
