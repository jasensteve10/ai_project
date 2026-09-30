"""Build validated election marts and their versioned column catalog."""
import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd
from src.ingestion.ingestion import CONSTITUENCY_COLUMNS, validate_data

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PARQUET = PROJECT_ROOT / 'dataset/clean/edan_2025_resultats.parquet'
DB_PATH = PROJECT_ROOT / 'dataset/db/edan_2025.duckdb'
CATALOG_PATH = PROJECT_ROOT / 'dataset/clean/schema_catalog.json'
DESCRIPTIONS = {
    'mart.vw_circonscriptions': 'Une ligne par circonscription; totaux et participation sans double comptage.',
    'mart.vw_resultats_candidats': 'Une ligne par candidature/liste; score = voix, elu = marque ELU(E) du PDF.',
    'mart.vw_vainqueur': 'Candidatures/listes marquées ELU(E); leur nombre ne représente pas les sièges.',
}


def read_catalog(con):
    return {
        relation: {
            'description': description,
            'columns': [{'name': r[0], 'type': r[1]} for r in con.execute(f'DESCRIBE {relation}').fetchall()],
        }
        for relation, description in DESCRIPTIONS.items()
    }


def build_database(parquet=PARQUET, db_path=DB_PATH, catalog_path=CATALOG_PATH):
    df = pd.read_parquet(parquet)
    validate_data(df)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute('BEGIN TRANSACTION')
        try:
            con.execute('CREATE SCHEMA IF NOT EXISTS brute')
            con.execute('CREATE SCHEMA IF NOT EXISTS mart')
            con.register('validated_results', df)
            con.execute('CREATE OR REPLACE TABLE brute.resultats AS SELECT * FROM validated_results')
            # Consistency is checked above, so DISTINCT retains exact source values.
            con.execute('CREATE OR REPLACE VIEW mart.vw_circonscriptions AS SELECT DISTINCT '
                        + ', '.join(CONSTITUENCY_COLUMNS) + ' FROM brute.resultats')
            con.execute('CREATE OR REPLACE VIEW mart.vw_resultats_candidats AS SELECT '
                        'region, circonscription_id, circonscription_name, parti, candidat, score, score_pct, elu, '
                        'source_page, source_table, source_row FROM brute.resultats')
            con.execute('CREATE OR REPLACE VIEW mart.vw_vainqueur AS SELECT * FROM brute.resultats WHERE elu = TRUE')
            catalog = read_catalog(con)
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    payload = {'schema_version': hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
               'relations': catalog}
    Path(catalog_path).parent.mkdir(parents=True, exist_ok=True)
    Path(catalog_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parquet', type=Path, default=PARQUET)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--catalog', type=Path, default=CATALOG_PATH)
    args = parser.parse_args()
    result = build_database(args.parquet, args.db, args.catalog)
    print(f'Database built: {args.db}; schema version: {result["schema_version"]}')


if __name__ == '__main__':
    main()
