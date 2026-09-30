"""Stage 4 — model evaluation on the 18 predeclared development questions.

    python -m src.pipeline.evaluate
        1. retrieval: BM25, E5, fine-tuned E5, hybrid (no API calls)
        2. generation: rebuild the report of the saved Claude Haiku run (no API calls)
    python -m src.pipeline.evaluate --live --provider claude --max-calls 300
        2'. run a new capped A vs RAG generation comparison instead (billed)

Outputs: experiments/runs|reports/<run id> and outputs/evaluation/.
"""
import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from src.evaluation import compare_rag, report, runner

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/evaluation'
CORE = ROOT / 'configs/core.json'
PROTOCOL = ROOT / 'configs/text_to_sql_comparison.json'
RETRIEVAL_RUN = 'retrieval-dev-finetuned'
SAVED_GENERATION_RUN = 'text-to-sql-comparison-haiku-20260930'
RETRIEVERS = [('bm25', 'B1 — BM25'), ('dense', 'B2 — E5'), ('dense_ft', 'B2-FT — fine-tuned E5'),
              ('hybrid', 'B3 — hybrid')]


def protocol_ids():
    protocol = json.loads(PROTOCOL.read_text())['comparison']
    records = [json.loads(l) for l in (ROOT / 'benchmarks/edan_2025_v1.jsonl').open()]
    return [r['id'] for r in records if r['split'] == 'dev' and r['id'] not in protocol['excluded_ids']]


def evaluate_retrieval():
    shutil.rmtree(runner.RUNS_DIR / RETRIEVAL_RUN, ignore_errors=True)  # deterministic; always recomputed
    runner.main(['--config', str(CORE), '--split', 'dev', '--ids', ','.join(protocol_ids()),
                 '--conditions', 'A,B,C,C_FT,D', '--mode', 'retrieval-only', '--run-id', RETRIEVAL_RUN])
    report.build_report(RETRIEVAL_RUN)
    rows = [json.loads(l) for l in (runner.RUNS_DIR / RETRIEVAL_RUN / 'retrieval.jsonl').open()]
    rows = [r for r in rows if r['n_slots']]
    table = []
    for name, label in RETRIEVERS:
        rk = [r['rankers'][name] for r in rows]
        quota = [x['quota_coverage']['5'] for x in rk]
        table.append({'retriever': label, 'questions': len(rk),
                      'complete_context': sum(bool(q['complete']) for q in quota),
                      'mean_slot_recall': sum(q['slot_recall'] for q in quota) / len(rk),
                      'recall@5': sum(x['recall@5'] for x in rk) / len(rk),
                      'ndcg@5': sum(x['ndcg@5'] for x in rk) / len(rk),
                      'complete@5': sum(bool(x['complete@5']) for x in rk)})
    return table


def plot_retrieval(table, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.8, 2.3), dpi=200)
    names = [r['retriever'] for r in table]
    values = [r['complete_context'] / r['questions'] for r in table]
    ax.barh(names, values, height=0.5, color='#2a78d6', zorder=3)
    for y, r in enumerate(table):
        ax.text(values[y] + 0.012, y, f"{r['complete_context']}/{r['questions']}", va='center', color='#52514e',
                fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.12)
    ax.xaxis.set_major_formatter(lambda v, _: f'{v:.0%}')
    ax.set_xlabel('Questions with complete evidence in the selected context (quotas 3/3/5)', fontsize=8.5,
                  color='#52514e')
    ax.grid(axis='x', color='#e4e3df', linewidth=0.8, zorder=0)
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(labelsize=8.5, colors='#52514e')
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true', help='run a new generation comparison (billed API calls)')
    parser.add_argument('--provider', choices=['gemini', 'claude'], default='claude')
    parser.add_argument('--max-calls', type=int)
    parser.add_argument('--pace', type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.live and args.max_calls is None:
        parser.error('--live requires --max-calls')
    OUT.mkdir(parents=True, exist_ok=True)

    table = evaluate_retrieval()
    with open(OUT / 'retrieval_comparison.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    plot_retrieval(table, OUT / 'retrieval_comparison.png')

    generation_run = SAVED_GENERATION_RUN
    if args.live:
        generation_run = f'text-to-sql-comparison-{args.provider}-{datetime.now():%Y%m%d-%H%M%S}'
        compare_rag.main(['--mode', 'live', '--provider', args.provider, '--run-id', generation_run,
                          '--max-calls', str(args.max_calls), '--pace', str(args.pace)])
    report.build_report(generation_run)
    shutil.copy(runner.PROJECT_ROOT / 'experiments/reports' / generation_run / 'summary.csv',
                OUT / 'generation_summary.csv')
    summary = {'retrieval_run': RETRIEVAL_RUN, 'generation_run': generation_run, 'retrieval': table,
               'training_heldout': json.loads((ROOT / 'outputs/training/metrics.json').read_text())
               if (ROOT / 'outputs/training/metrics.json').exists() else None}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(table, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
