"""Read-only preflight for the development comparison; never loads .env or calls an API.

Run from the project root: .venv/bin/python -m src.evaluation.preflight
Only audit JSON under experiments/preflight is written. Source, benchmark, and database
remain unchanged. Independent CSV formulas avoid treating executable SQL alone
as proof that a reference answer is appropriate.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.preprocessing.ingestion import extract_pdf, transform_data, build_audit
from src.evaluation.scoring import compare_results


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def equal_rows(left, right, ordered=True):
    if not ordered:
        left, right = sorted(left, key=str), sorted(right, key=str)
    return len(left) == len(right) and all(
        len(a) == len(b) and all(
            math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12)
            if isinstance(x, (int, float)) and isinstance(y, (int, float))
            else x == y for x, y in zip(a, b)
        ) for a, b in zip(left, right)
    )


def records(frame, cols):
    return frame[cols].values.tolist()


def independent_answers(frame):
    c = frame.drop_duplicates('circonscription_id')
    w = frame[frame.elu]
    byid = c.set_index('circonscription_id')
    ratios = c.groupby('region')[['votants', 'inscrits']].sum()
    ratios['participation'] = ratios.votants / ratios.inscrits
    joined = c.drop(columns='candidat').merge(w[['circonscription_id', 'candidat']], on='circonscription_id')
    pdci = c[c.circonscription_id.isin(w[w.parti.eq('PDCI-RDA')].circonscription_id)]
    margins = []
    for cid, g in frame.groupby('circonscription_id'):
        scores = g.score.sort_values(ascending=False).tolist()
        if len(scores) > 1:
            margins.append([cid, g.iloc[0].circonscription_name, scores[0] - scores[1]])
    wins = w[w.parti.eq('RHDP')].groupby('region').size().sort_values(ascending=False)
    return {
        'dev-001': records(w[w.circonscription_id.eq('060')], ['candidat', 'parti']),
        'dev-002': [[int(byid.loc['100', 'inscrits'])]],
        'dev-003': [[int(c[c.region.eq('PORO')].votants.sum())]],
        'dev-004': [[int(frame[frame.parti.eq('INDEPENDANT')].score.sum())]],
        'dev-005': records(c.sort_values('inscrits', ascending=False).head(3),
                           ['circonscription_id', 'circonscription_name', 'inscrits']),
        'dev-006': records(ratios.sort_values('participation').head(5).reset_index(),
                           ['region', 'participation']),
        'dev-007': records(joined[joined.region.eq('GOH')],
                           ['circonscription_id', 'circonscription_name', 'candidat', 'taux_participation']),
        'dev-008': [[float(pdci.votants.sum() / pdci.inscrits.sum())]],
        'dev-009': [[float(c.bulletins_nuls.sum() / c.votants.sum())]],
        'dev-010': [[float(frame[frame.parti.eq('RHDP')].score.sum() / frame.score.sum())]],
        'dev-011': records(frame[frame.circonscription_id.eq('175')], ['candidat', 'parti', 'score']),
        'dev-012': [[float(ratios.loc['HAUT- SASSANDRA', 'participation'])]],
        'dev-015': [[wins.index[0], int(wins.iloc[0])]],
        'dev-016': sorted(margins, key=lambda r:r[2])[:1],
        'dev-019': [[int(x)] for x in frame[frame.circonscription_id.eq('053')].source_page.unique()],
        'dev-020': [[int(x)] for x in w[w.circonscription_id.eq('041')].source_page],
    }


def main():
    paths = {k: ROOT / p for k, p in {
        'benchmark':'benchmarks/edan_2025_v1.jsonl',
        'csv':'dataset/clean/edan_2025_resultats.csv',
        'parquet':'dataset/clean/edan_2025_resultats.parquet',
        'pdf':'dataset/raw/EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf',
        'database':'dataset/db/edan_2025.duckdb',
        'cards':'dataset/retrieval/cards.jsonl',
    }.items()}
    before = {k:digest(p) for k,p in paths.items()}
    benchmark = [json.loads(x) for x in paths['benchmark'].read_text().splitlines() if x]
    dev = [r for r in benchmark if r['split'] == 'dev']
    cards = {json.loads(x)['id'] for x in paths['cards'].read_text().splitlines() if x}
    df = pd.read_csv(paths['csv'], dtype={'circonscription_id':str})
    raw = transform_data(extract_pdf(paths['pdf']))
    pd.testing.assert_frame_equal(raw, pd.read_parquet(paths['parquet']))
    pd.testing.assert_frame_equal(raw, df, check_dtype=False, check_exact=False, atol=1e-15, rtol=1e-15)
    expected = independent_answers(df)
    altered = df.copy()
    altered.loc[altered.circonscription_id.isin(['073','074','075','076','077']), 'region'] = 'GONTOUGO'
    sensitivity = independent_answers(altered)
    results = []
    with duckdb.connect(str(paths['database']), read_only=True) as con:
        for r in dev:
            item = {'id':r['id'], 'question':r['question'], 'answerability':r['answerability'],
                    'all_evidence_card_ids_exist':all(cid in cards for slot in r['evidence_slots'] for cid in slot)}
            if r['gold_sql']:
                cursor = con.execute(r['gold_sql'])
                cols = [d[0] for d in cursor.description]
                rows = [list(x) for x in cursor.fetchall()]
                ordered = r['result_comparison_policy']['type'] == 'ordered'
                item.update({
                    'gold_columns_match_database':cols == r['gold_result']['columns'],
                    'gold_rows_match_database':equal_rows(rows,r['gold_result']['rows'],ordered),
                    'gold_rows_match_independent_csv_formula':equal_rows(expected[r['id']],r['gold_result']['rows'],ordered),
                    'hypothetical_region_reassignment_changes_answer':not equal_rows(expected[r['id']],sensitivity[r['id']],ordered),
                    'stored_gold_rows':r['gold_result']['rows'],
                })
            results.append(item)
    d7 = next(r for r in dev if r['id']=='dev-007')
    precision_rows = [x.copy() for x in expected['dev-007']]
    c = df.drop_duplicates('circonscription_id').set_index('circonscription_id')
    for row in precision_rows:
        row[-1] = float(c.loc[row[0],'votants'] / c.loc[row[0],'inscrits'])
    precision_result = compare_results(d7['gold_result']['columns'], precision_rows,
                                       d7['gold_result'],d7['result_comparison_policy'])
    report = {
        'scope':'Predeclared development audit; no real-model output inspected or API called.',
        'input_sha256':before,
        'inputs_unchanged':before == {k:digest(p) for k,p in paths.items()},
        'counts':{'dev':len(dev),'answerable':sum(r['answerability']=='answerable' for r in dev),
                  'draft':sum(r['reviewer_status']=='draft' for r in benchmark), 'total':len(benchmark)},
        'fresh_pdf_extraction_matches_parquet_and_csv':True,
        'source_reconciliation':build_audit(raw),
        'cross_split_paraphrase_overlap':sorted({r['paraphrase_family'] for r in dev} &
                                               {r['paraphrase_family'] for r in benchmark if r['split']=='test'}),
        'dev_results':results,
        'dev007_recomputed_turnout_precision_scoring':list(precision_result),
        'hypothetical_only_region_sensitivity':{
            'changed_ids':['073','074','075','076','077'], 'from':'GOH','to':'GONTOUGO',
            'dev006_before':expected['dev-006'], 'dev006_after':sensitivity['dev-006'],
            'dev015_before':expected['dev-015'], 'dev015_after':sensitivity['dev-015'],
        },
        'recommended_exclusions':['dev-006','dev-007'],
        'recommended_included_ids':[r['id'] for r in dev if r['id'] not in ['dev-006','dev-007']],
    }
    output = ROOT / 'experiments/preflight/comparison_preflight_2026-09-30.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    assert report['inputs_unchanged']
    assert all(all(v for k,v in r.items() if k.endswith('_match_database') or k.endswith('_match_independent_csv_formula')) for r in results)
    print(json.dumps({'output':str(output.relative_to(ROOT)), 'dev_records':len(dev),
                      'gold_queries_verified':len(expected), 'retained':18,
                      'excluded':report['recommended_exclusions'],
                      'precision_probe':precision_result},ensure_ascii=False))


if __name__=='__main__':
    main()
