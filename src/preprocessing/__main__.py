"""Build validated tabular data, SQL views and RAG reference cards."""
import argparse
import json
from pathlib import Path

from .ingest import PDF_PATH, extract_pdf, transform_data, save_data
from .schema import build_database

ROOT = Path(__file__).resolve().parents[2]


def run(pdf=PDF_PATH, dataset_dir=ROOT / 'dataset'):
    # A separate dataset directory allows a reproducibility check without
    # overwriting the database used by archived experiments.
    from src.retrieval.corpus import build_cards, write_cards, corpus_hash
    dataset_dir = Path(dataset_dir)
    clean = dataset_dir / 'clean'
    db = dataset_dir / 'db/edan_2025.duckdb'
    frame = transform_data(extract_pdf(pdf))
    audit = save_data(frame, clean)
    build_database(clean / 'edan_2025_resultats.parquet', db, clean / 'schema_catalog.json')
    cards = build_cards(db_path=db)
    target = dataset_dir / 'retrieval/cards.jsonl'
    write_cards(cards, target)
    return {'rows': audit['rows'], 'constituencies': audit['constituencies'],
            'cards': len(cards), 'corpus_sha256': corpus_hash(cards),
            'dataset_directory': str(dataset_dir.resolve()),
            'source_limitations': 'PDF page 14 region carry remains unresolved; see experiments/preflight.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, default=PDF_PATH)
    parser.add_argument('--dataset-dir', type=Path, default=ROOT / 'dataset')
    args = parser.parse_args()
    print(json.dumps(run(args.pdf, args.dataset_dir), indent=2))


if __name__ == '__main__':
    main()
