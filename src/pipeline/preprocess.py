"""Stage 2 — data preprocessing: PDF -> validated CSV/Parquet -> DuckDB marts -> retrieval cards -> gold labels.

    python -m src.pipeline.preprocess            # rebuild in a temp dir and check against committed data
    python -m src.pipeline.preprocess --write    # overwrite dataset/ (changes the DuckDB bytes; see README)
"""
import argparse
import json
import shutil
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from src.evaluation import benchmark
from src.preprocessing.ingestion import OUTPUT_DIR, PDF_PATH, extract_pdf, save_data, transform_data
from src.preprocessing.schema import CATALOG_PATH, DB_PATH, DESCRIPTIONS, build_database
from src.retrieval.corpus import CARDS_PATH, build_cards, corpus_hash, load_cards, write_cards

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'outputs/preprocessing/summary.json'


def run(root):
    """Run every step into ``root`` (a dataset/-like folder); return the step outputs."""
    clean, db, retrieval = root / 'clean', root / 'db', root / 'retrieval'
    df = transform_data(extract_pdf(PDF_PATH))
    audit = save_data(df, clean)
    catalog = build_database(clean / 'edan_2025_resultats.parquet', db / 'edan_2025.duckdb',
                             clean / 'schema_catalog.json')
    cards = build_cards(db / 'edan_2025.duckdb')
    write_cards(cards, retrieval / 'cards.jsonl')
    records = benchmark.compute_gold(benchmark.load_benchmark(include_excluded=True), db / 'edan_2025.duckdb')
    errors, warnings = benchmark.validate(records, db / 'edan_2025.duckdb', cards)
    return {'rows': len(df), 'constituencies': int(df.circonscription_id.nunique()),
            'national_totals_reconcile': not any(audit['national_total_differences'].values()),
            'schema_version': catalog['schema_version'], 'cards': len(cards), 'corpus_sha256': corpus_hash(cards),
            'benchmark_errors': errors, 'benchmark_warnings': warnings, 'records': records}


def _marts(db_path):
    with duckdb.connect(str(db_path), read_only=True) as con:
        return {view: con.execute(f'SELECT * FROM {view} ORDER BY ALL').fetchall() for view in DESCRIPTIONS}


def compare_with_committed(root, records):
    """Content equality with the committed artifacts (file bytes of DuckDB/Parquet are not stable)."""
    fresh = pd.read_csv(root / 'clean/edan_2025_resultats.csv', dtype=str)
    return {
        'csv_identical': (root / 'clean/edan_2025_resultats.csv').read_bytes()
        == (OUTPUT_DIR / 'edan_2025_resultats.csv').read_bytes(),
        'parquet_content_identical': pd.read_parquet(root / 'clean/edan_2025_resultats.parquet').equals(
            pd.read_parquet(OUTPUT_DIR / 'edan_2025_resultats.parquet')),
        'csv_rows': len(fresh),
        'database_views_identical': _marts(root / 'db/edan_2025.duckdb') == _marts(DB_PATH),
        'schema_catalog_identical': json.loads((root / 'clean/schema_catalog.json').read_text())
        == json.loads(CATALOG_PATH.read_text()),
        'cards_identical': corpus_hash(load_cards(root / 'retrieval/cards.jsonl')) == corpus_hash(load_cards(CARDS_PATH)),
        'gold_results_identical': [r['gold_result'] for r in records]
        == [r['gold_result'] for r in benchmark.load_benchmark(include_excluded=True)],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true', help='overwrite dataset/ and the benchmark gold labels')
    args = parser.parse_args(argv)
    if args.write:
        result = run(ROOT / 'dataset')
        benchmark.write_benchmark(result['records'], benchmark.BENCHMARK_PATH)
        checks = None
    else:
        tmp = Path(tempfile.mkdtemp(prefix='edan-preprocess-'))
        try:
            result = run(tmp)
            checks = compare_with_committed(tmp, result['records'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    summary = {k: v for k, v in result.items() if k != 'records'}
    summary['mode'] = 'write' if args.write else 'check'
    summary['reproduces_committed_data'] = checks
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    failed = result['benchmark_errors'] or (checks is not None and not all(v for k, v in checks.items() if k != 'csv_rows'))
    if failed:
        raise SystemExit('Preprocessing check failed; see outputs/preprocessing/summary.json')


if __name__ == '__main__':
    main()
