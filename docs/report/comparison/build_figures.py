"""Reproduce report figures directly from the local retrieval trace; no API calls."""
import csv
import json
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
TRACE = ROOT / 'experiments/runs/text-to-sql-comparison-retrieval-20260930/retrieval.jsonl'


def main():
    rows = [json.loads(line) for line in TRACE.read_text().splitlines() if line]
    eligible = [r for r in rows if r['n_slots']]
    data = []
    for label, ranker in [('B1 BM25', 'bm25'), ('B2 E5', 'dense'), ('B3 Hybrid', 'hybrid')]:
        quota = [r['rankers'][ranker]['quota_coverage']['5'] for r in eligible]
        data.append({'method': label, 'eligible_questions': len(eligible),
                     'complete_contexts': sum(x['complete'] for x in quota),
                     'complete_context_rate': mean(x['complete'] for x in quota),
                     'mean_slot_recall': mean(x['slot_recall'] for x in quota),
                     'global_recall_at_5': mean(r['rankers'][ranker]['recall@5'] for r in eligible),
                     'global_ndcg_at_5': mean(r['rankers'][ranker]['ndcg@5'] for r in eligible)})
    (HERE / 'tables').mkdir(exist_ok=True)
    with (HERE / 'tables/local_retrieval_comparison.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]))
        writer.writeheader(); writer.writerows(data)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, ax = plt.subplots(figsize=(8.7, 2.8), dpi=200)
    y = np.arange(len(data))
    for offset, key, label, color in [(-.17, 'complete_context_rate', 'Complete context', '#245573'),
                                     (.17, 'mean_slot_recall', 'Mean slot recall', '#3FA29A')]:
        values = [r[key] * 100 for r in data]
        ax.barh(y + offset, values, height=.30, color=color, label=label)
        for i, value in enumerate(values):
            ax.text(value + .8, i + offset, f'{value:.1f}%', va='center', fontsize=9)
    ax.set_yticks(y, [r['method'] for r in data]); ax.invert_yaxis()
    ax.set_xlim(0, 111); ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel('Evidence coverage (%) — 16 questions with annotated slots')
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.01), ncol=2, frameon=False)
    ax.spines[['top', 'right', 'left']].set_visible(False)
    ax.grid(axis='x', color='#E7E7E7', linewidth=.7); ax.set_axisbelow(True)
    fig.tight_layout(pad=.7)
    (HERE / 'figures').mkdir(exist_ok=True)
    for extension in ['png', 'svg']:
        fig.savefig(HERE / f'figures/context_coverage.{extension}', bbox_inches='tight')
    plt.close(fig)
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
