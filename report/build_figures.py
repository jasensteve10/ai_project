"""Report figures from pipeline outputs and saved traces; no model calls.

    python report/build_figures.py     (after src.pipeline.eda / train / evaluate)
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'experiments/runs/text-to-sql-comparison-haiku-20260930'
OUT = Path(__file__).with_name('figures')
EDA_TABLES = ROOT / 'outputs/eda/tables'
LABELS = [('A', 'A — no retrieval'), ('B', 'B1 — BM25'), ('C', 'B2 — E5'), ('D', 'B3 — hybrid')]
SERIES, SURFACE, INK, INK_2, GRID = '#2a78d6', '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis='x', color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8.5)


def aggregation_figure():
    """Turnout depends on the aggregation grain; candidate rows repeat constituency totals."""
    import pandas as pd
    c = pd.read_csv(EDA_TABLES / 'constituencies.csv')
    estimates = [('National turnout\n(one row per constituency)', c.votants.sum() / c.inscrits.sum()),
                 ('Mean constituency turnout\n(equal weight)', c.turnout.mean()),
                 ('Incorrect: summed over\ncandidate rows', (c.votants * c.entries).sum() / (c.inscrits * c.entries).sum())]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax = axes[0]
    ax.barh([e[0] for e in estimates], [e[1] for e in estimates], height=0.5, color=SERIES, zorder=3)
    for y, (_, v) in enumerate(estimates):
        ax.text(v + 0.008, y, f'{100 * v:.2f}%', va='center', color=INK_2, fontsize=8.5)
    ax.invert_yaxis(); ax.set_xlim(0, 0.53); ax.xaxis.set_major_formatter(lambda v, _: f'{v:.0%}')
    ax.set_title('A. The aggregation grain changes the statistic', color=INK, fontsize=9.5, loc='left')
    _style(ax)
    counts = c.entries.value_counts().sort_index()
    ax = axes[1]
    ax.bar(counts.index, counts.values, width=0.7, color=SERIES, zorder=3)
    ax.set_xticks(range(1, int(counts.index.max()) + 1, 2))
    ax.set_xlabel('Candidate/list entries per constituency', color=INK_2, fontsize=8.5)
    ax.set_ylabel('Constituencies', color=INK_2, fontsize=8.5)
    ax.set_title('B. Repetition varies across constituencies', color=INK, fontsize=9.5, loc='left')
    ax.grid(axis='y', color=GRID, linewidth=0.8, zorder=0)
    _style(ax); ax.grid(axis='x', visible=False)
    fig.tight_layout()
    for ext in ('png', 'svg'):
        fig.savefig(OUT / f'aggregation.{ext}', facecolor=SURFACE)
    plt.close(fig)


def main():
    bench = {json.loads(l)['id']: json.loads(l) for l in (ROOT / 'benchmarks/edan_2025_v1.jsonl').open()}
    traces = [json.loads(l) for l in (RUN / 'traces.jsonl').open()]
    rows = []
    for key, label in LABELS:
        ans = [t for t in traces if t['condition'] == key and bench[t['question_id']]['answerability'] == 'answerable']
        rows.append((label, sum(t['outcome'] == 'correct' for t in ans), len(ans)))
    fig, ax = plt.subplots(figsize=(6.8, 2.3), dpi=200)
    fig.patch.set_facecolor(SURFACE); ax.set_facecolor(SURFACE)
    names = [r[0] for r in rows]
    values = [r[1] / r[2] for r in rows]
    ax.barh(names, values, height=0.5, color=SERIES, zorder=3)
    for y, (label, k, n) in enumerate(rows):
        ax.text(k / n + 0.012, y, f'{k}/{n}  ({100 * k / n:.1f}%)', va='center', color=INK_2, fontsize=8.5)
    ax.invert_yaxis(); ax.set_xlim(0, 1.18)
    ax.set_xticks([0, .25, .5, .75, 1]); ax.xaxis.set_major_formatter(lambda v, _: f'{v:.0%}')
    ax.grid(axis='x', color=GRID, linewidth=0.8, zorder=0); ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8.5)
    ax.set_xlabel('Strict SQL-result accuracy on 14 answerable development questions', color=INK_2, fontsize=8.5)
    fig.tight_layout()
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / 'sql_accuracy.png', facecolor=SURFACE)
    fig.savefig(OUT / 'sql_accuracy.svg', facecolor=SURFACE)
    print(rows)
    aggregation_figure()


if __name__ == '__main__':
    main()
