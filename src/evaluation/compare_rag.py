"""Run the predeclared A versus B1/B2/B3 development comparison.

Internal trace IDs A/B/C/D are retained for compatibility; the report displays
A (no retrieval), B1 (BM25), B2 (E5) and B3 (hybrid). No test labels are exposed.
"""
import argparse
import hashlib
import json
from pathlib import Path

from src.evaluation.benchmark import load_benchmark
from src.evaluation.runner import main as run

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'configs/text_to_sql_comparison.json'
AUDIT = ROOT / 'experiments/preflight/comparison_preflight_2026-09-30.json'


def verify_preflight(config, root=ROOT, audit_path=AUDIT):
    """Fail closed if the independently checked references or data have changed."""
    audit = json.loads(audit_path.read_text())
    if not audit['inputs_unchanged'] or audit['cross_split_paraphrase_overlap']:
        raise ValueError('The preflight did not pass data integrity/leakage checks.')
    paths = {'benchmark': config['benchmark'], 'csv': 'dataset/clean/edan_2025_resultats.csv',
             'parquet': 'dataset/clean/edan_2025_resultats.parquet',
             'pdf': 'dataset/raw/EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf',
             'database': 'dataset/db/edan_2025.duckdb', 'cards': 'dataset/retrieval/cards.jsonl'}
    for name, path in paths.items():
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != audit['input_sha256'][name]:
            raise ValueError(f'{name} changed after the independent preflight; audit again before running.')
    if set(config['comparison']['excluded_ids']) != set(audit['recommended_exclusions']):
        raise ValueError('Exclusions differ from the predeclared source audit.')
    for row in audit['dev_results']:
        checks = [v for k, v in row.items() if k.endswith('_match_database') or
                  k.endswith('_match_independent_csv_formula') or k == 'all_evidence_card_ids_exist']
        if not all(checks):
            raise ValueError(f'Preflight reference check failed for {row["id"]}.')
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['retrieval-only', 'fake', 'live'], default='retrieval-only')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--max-calls', type=int)
    parser.add_argument('--pace', type=float, default=12)
    parser.add_argument('--provider', choices=['gemini', 'claude'], default='gemini',
                        help='single live provider for the whole comparison (claude is billed per token)')
    args = parser.parse_args(argv)
    config = json.loads(CONFIG.read_text())
    try:
        verify_preflight(config)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(f'Preflight verification failed: {exc}')
    protocol = config['comparison']
    excluded = protocol['excluded_ids']
    records = load_benchmark(ROOT / config['benchmark'], split='dev')
    ids = [r['id'] for r in records if r['id'] not in excluded]
    if len(ids) != 18 or sum(r['answerability'] == 'answerable' for r in records if r['id'] in ids) != 14:
        parser.error('The predeclared sample changed; review the protocol before running.')
    if args.mode == 'live' and args.max_calls is None:
        parser.error('Live comparison requires an explicit --max-calls cap.')
    command = ['--config', str(CONFIG), '--split', 'dev', '--conditions', 'A,B,C,D',
               '--ids', ','.join(ids), '--mode', args.mode, '--run-id', args.run_id,
               '--pace', str(args.pace), '--seed', str(protocol['seed']),
               '--repeats', str(protocol['repeats']), '--provider', args.provider]
    if args.max_calls is not None:
        command += ['--max-calls', str(args.max_calls)]
    run(command)


if __name__ == '__main__':
    main()
