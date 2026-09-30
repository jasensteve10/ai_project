"""Gold benchmark: record schema, gold-result computation, validation and freezing.

Records are drafted in ``benchmarks/*.jsonl`` with ``reviewer_status: draft``. A human
checks each against the PDF and sets ``reviewed``; the test split is then frozen.
"""
import argparse
import datetime as dt
import hashlib
import itertools
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from src.agent.Agent import DEFAULT_LIMIT, run_safe_sql, validate_sql
from src.retrieval.bm25 import fold
from src.retrieval.corpus import load_cards
from src.schema.schema import DB_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = PROJECT_ROOT / 'benchmarks/edan_2025_v1.jsonl'
EXAMPLES_PATH = PROJECT_ROOT / 'src/prompts/examples.json'
ANSWERABILITY = {'answerable', 'unsupported', 'ambiguous'}
POLICIES = {'ordered', 'unordered', 'scalar'}
REVIEW_STATUSES = {'draft', 'reviewed', 'excluded'}


def freeze_path(path):
    return Path(path).with_name(Path(path).stem + '.FREEZE.json')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_benchmark(path=BENCHMARK_PATH, split=None, include_excluded=False):
    records = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if split:
        records = [r for r in records if r['split'] == split]
    return [r for r in records if include_excluded or r.get('reviewer_status') != 'excluded']


def write_benchmark(records, path=BENCHMARK_PATH):
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))


def _jsonable(value):
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _with_limit(sql, n):
    tree = sqlglot.parse_one(sql, read='duckdb')
    tree.set('limit', exp.Limit(expression=exp.Literal.number(n)))
    return tree.sql(dialect='duckdb')


def gold_limit(sql):
    ok, fixed, _ = validate_sql(sql)
    limit = sqlglot.parse_one(fixed, read='duckdb').args['limit'].expression
    return int(limit.this) if ok else DEFAULT_LIMIT


def expand_slots(slots):
    """Every combination choosing one acceptable card per slot is a complete evidence set."""
    return [sorted(set(combo)) for combo in itertools.product(*slots)] if slots else []


def compute_gold(records, db_path=DB_PATH):
    """Execute gold SQL through the production validator and derive retrieval labels."""
    dev_entities = {c for r in records if r['split'] == 'dev' for s in r['evidence_slots'] for c in s
                    if c.startswith('entity:')}
    for r in records:
        slots = r.get('evidence_slots') or []
        r['relevant_schema_ids'] = sorted({c for s in slots for c in s if not c.startswith('entity:')})
        r['relevant_evidence_ids'] = sorted({c for s in slots for c in s if c.startswith('entity:')})
        r['acceptable_alternative_evidence_sets'] = expand_slots(slots)
        r['entity_heldout'] = (r['split'] == 'test' and bool(r['relevant_evidence_ids'])
                               and not set(r['relevant_evidence_ids']) & dev_entities)
        if not r.get('gold_sql'):
            r['gold_result'] = None
            r.setdefault('source_pages', [])
            continue
        res = run_safe_sql(r['gold_sql'], db_path=db_path)
        if not res['ok']:
            raise ValueError(f"{r['id']}: gold SQL failed at {res['stage']}: {res['error']}")
        limit = gold_limit(r['gold_sql'])
        r['gold_result'] = {'columns': res['columns'], 'rows': [[_jsonable(v) for v in row] for row in res['rows']],
                            'truncated': len(res['rows']) >= limit and limit in (DEFAULT_LIMIT,)}
        r['source_pages'] = _source_pages(r, db_path)
    return records


def _source_pages(r, db_path):
    """Pages a reviewer should open to check this record against the PDF."""
    circs = [c.split(':')[-1] for c in r['relevant_evidence_ids'] if c.startswith('entity:circ:')]
    regions = [c.split(':', 2)[-1] for c in r['relevant_evidence_ids'] if c.startswith('entity:region:')]
    clauses = []
    if circs:
        clauses.append('circonscription_id IN (' + ', '.join(f"'{c}'" for c in circs) + ')')
    if regions:
        clauses.append('region IN (' + ', '.join("'" + x.replace("'", "''") + "'" for x in regions) + ')')
    cols = r['gold_result']['columns']
    pages = set()
    if 'source_page' in cols:
        i = cols.index('source_page')
        pages |= {row[i] for row in r['gold_result']['rows']}
    if clauses:
        res = run_safe_sql('SELECT DISTINCT source_page FROM mart.vw_resultats_candidats WHERE '
                           + ' OR '.join(clauses) + ' LIMIT 500', db_path=db_path)
        pages |= {row[0] for row in res.get('rows', [])}
    return sorted(pages) if pages else ([1] if r.get('entity_family') == 'national' else [])


def _norm_tokens(text):
    return set(re.findall(r'\w+', fold(text)))


def validate(records, db_path=DB_PATH, cards=None):
    """Return (errors, warnings). Errors block freezing."""
    errors, warnings = [], []
    card_ids = {c['id'] for c in (cards if cards is not None else load_cards())}
    ids = [r['id'] for r in records]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errors.append(f'duplicate id {dup}')
    splits = {}
    for r in records:
        splits.setdefault(r['paraphrase_family'], set()).add(r['split'])
    for fam, s in splits.items():
        if len(s) > 1:
            errors.append(f'paraphrase family {fam} spans splits {sorted(s)}')
    examples = [_norm_tokens(e['question']) for e in json.loads(EXAMPLES_PATH.read_text())]
    for r in records:
        rid = r['id']
        pol = r['result_comparison_policy']
        if r['split'] not in {'dev', 'test'}:
            errors.append(f'{rid}: bad split')
        if r['answerability'] not in ANSWERABILITY:
            errors.append(f'{rid}: bad answerability')
        if r.get('reviewer_status') not in REVIEW_STATUSES:
            errors.append(f'{rid}: bad reviewer_status')
        if pol['type'] not in POLICIES:
            errors.append(f'{rid}: bad policy {pol["type"]}')
        for slot in r.get('evidence_slots') or []:
            for c in slot:
                if c not in card_ids:
                    errors.append(f'{rid}: unknown card id {c}')
        toks = _norm_tokens(r['question'])
        for ex in examples:
            if toks and len(toks & ex) / len(toks | ex) >= 0.8:
                errors.append(f'{rid}: near-duplicate of a prompt example (leakage)')
        if r['answerability'] == 'answerable':
            gold = r.get('gold_result')
            if not r.get('gold_sql') or not gold:
                errors.append(f'{rid}: answerable item needs gold_sql and computed gold_result')
                continue
            if not gold['rows']:
                errors.append(f'{rid}: empty gold result')
            if gold.get('truncated'):
                errors.append(f'{rid}: gold result hits the default LIMIT (truncated)')
            cols = gold['columns']
            for group in pol.get('required_columns') or []:
                if not set(group) & set(cols):
                    errors.append(f'{rid}: required column group {group} not in gold columns {cols}')
            if pol.get('order_key') and pol['order_key'] not in cols:
                errors.append(f'{rid}: order_key not in gold columns')
            if pol['type'] == 'scalar' and (len(gold['rows']) != 1):
                errors.append(f'{rid}: scalar policy needs exactly one row')
            tie = _boundary_tie(r, db_path)
            if tie:
                warnings.append(f'{rid}: {tie}')
        elif r.get('gold_sql'):
            errors.append(f'{rid}: non-answerable item must not have gold_sql')
    return errors, warnings


def _boundary_tie(r, db_path):
    """A LIMIT on an ordered result is ill-defined if the next row ties with the last kept row."""
    pol = r['result_comparison_policy']
    if pol['type'] != 'ordered':
        return None
    tree = sqlglot.parse_one(r['gold_sql'], read='duckdb')
    if not tree.args.get('limit') or not tree.args.get('order'):
        return None
    n = len(r['gold_result']['rows'])
    res = run_safe_sql(_with_limit(r['gold_sql'], n + 1), db_path=db_path)
    if not res['ok'] or len(res['rows']) <= n:
        return None
    key = pol.get('order_key')
    idx = res['columns'].index(key) if key in res['columns'] else len(res['columns']) - 1
    if res['rows'][n - 1][idx] == res['rows'][n][idx]:
        return f'tie at LIMIT boundary on {res["columns"][idx]}={res["rows"][n][idx]!r}'
    return None


def freeze(path=BENCHMARK_PATH, note='initial freeze'):
    records = load_benchmark(path, include_excluded=True)
    pending = [r['id'] for r in records if r['split'] == 'test' and r['reviewer_status'] == 'draft']
    if pending:
        raise SystemExit(f'Cannot freeze: {len(pending)} test items still draft, e.g. {pending[:5]}')
    fp = freeze_path(path)
    log = json.loads(fp.read_text())['log'] if fp.exists() else []
    log.append({'at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'note': note,
                'sha256': file_hash(path)})
    fp.write_text(json.dumps({'benchmark': Path(path).name, 'sha256': file_hash(path),
                              'test_ids': [r['id'] for r in records if r['split'] == 'test'], 'log': log},
                             indent=2, ensure_ascii=False) + '\n')
    return fp


def freeze_status(path=BENCHMARK_PATH):
    """'unfrozen', 'frozen', or 'modified' (file changed since the last logged freeze)."""
    fp = freeze_path(path)
    if not fp.exists():
        return 'unfrozen'
    return 'frozen' if json.loads(fp.read_text())['sha256'] == file_hash(path) else 'modified'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=BENCHMARK_PATH)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--compute-gold', action='store_true')
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--freeze', metavar='NOTE', help='Freeze (or log a correction to) the reviewed benchmark')
    args = parser.parse_args()
    if args.compute_gold:
        if freeze_status(args.path) == 'frozen':
            print('Warning: benchmark is frozen; recomputing gold requires a logged correction (--freeze NOTE).')
        records = compute_gold(load_benchmark(args.path, include_excluded=True), args.db)
        write_benchmark(records, args.path)
        print(f'Gold computed for {sum(r["gold_result"] is not None for r in records)} of {len(records)} records.')
    if args.validate:
        errors, warnings = validate(load_benchmark(args.path), args.db)
        for w in warnings:
            print('WARNING', w)
        for e in errors:
            print('ERROR', e)
        records = load_benchmark(args.path, include_excluded=True)
        counts = {s: sum(r['split'] == s for r in records) for s in ('dev', 'test')}
        reviewed = sum(r['reviewer_status'] == 'reviewed' for r in records)
        print(f'{counts}; reviewed {reviewed}/{len(records)}; freeze status: {freeze_status(args.path)}; '
              f'{len(errors)} errors, {len(warnings)} warnings')
        if errors:
            raise SystemExit(1)
    if args.freeze:
        print(f'Frozen: {freeze(args.path, args.freeze)}')


if __name__ == '__main__':
    main()
