"""Aggregate a run into tables and figures; every number traces to the run's traces/manifest.

Paired differences use a cluster bootstrap over paraphrase families (questions in one
family are related). Repeats are averaged within a question before any comparison.
Budget-stopped trials are not scored as wrong answers. Provider failures are. Resource
accounting retains superseded attempts, separately from the latest scored trials.
"""
import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict

from src.evaluation.benchmark import file_hash, load_benchmark
from src.evaluation.runner import PROJECT_ROOT, RUNS_DIR, read_traces

REPORTS_DIR = PROJECT_ROOT / 'experiments/reports'
ERROR_ORDER = ['retrieval_miss', 'entity_value', 'semantics', 'sql_tool', 'abstention', 'transport_error',
               'budget_exceeded']
# Reference palette (dataviz skill): categorical slots in fixed order; ink and surface tokens.
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948']
SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
DISPLAY_LABELS = {'A': 'A', 'B': 'B1', 'C': 'B2', 'D': 'B3'}
CONDITION_NAMES = {'A': 'Text-to-SQL without retrieval', 'B': 'Text-to-SQL + BM25 RAG',
                   'C': 'Text-to-SQL + E5 RAG', 'D': 'Text-to-SQL + hybrid RAG'}


# ---------------------------------------------------------------- statistics
def percentile(values, q):
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def cluster_bootstrap(per_question, families, iters=10000, seed=0):
    """95% CI of the mean of per-question values, resampling whole families."""
    by_fam = defaultdict(list)
    for qid, v in per_question.items():
        by_fam[families[qid]].append(v)
    fams = list(by_fam)
    if not fams:
        return None, None, None
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        vals = [v for f in (rng.choice(fams) for _ in fams) for v in by_fam[f]]
        means.append(sum(vals) / len(vals))
    point = sum(per_question.values()) / len(per_question)
    return point, percentile(means, 0.025), percentile(means, 0.975)


def per_question_success(rows, condition):
    acc = defaultdict(list)
    for r in rows:
        if r['condition'] == condition and r.get('outcome') != 'budget_exceeded':
            acc[r['question_id']].append(1.0 if r['success'] else 0.0)
    return {q: sum(v) / len(v) for q, v in acc.items()}


def wilson_interval(successes, n, z=1.959963984540054):
    """Binomial Wilson interval; valid only for independent binary observations."""
    if not n:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def success_interval(rows, condition, families):
    """Avoid zero-width certainty from a bootstrap of a tiny homogeneous sample.

    Wilson is used only for a single trial per distinct paraphrase family. Repeated
    or clustered samples retain a family bootstrap, with collapsed intervals withheld.
    These intervals concern sampling uncertainty, not annotation or domain-shift error.
    """
    rs = [r for r in rows if r['condition'] == condition and r.get('outcome') != 'budget_exceeded']
    values = per_question_success(rs, condition)
    point = _mean(list(values.values()))
    if not values:
        return point, (None, None), 'not_available'
    if len(rs) == len(values) == len({families[q] for q in values}):
        return point, wilson_interval(sum(values.values()), len(values)), 'wilson_independent_questions'
    _, lo, hi = cluster_bootstrap(values, families)
    if lo == hi or len({families[q] for q in values}) < 2:
        return point, (None, None), 'cluster_bootstrap_degenerate_withheld'
    return point, (lo, hi), 'cluster_bootstrap_paraphrase_families'


def paired_difference(rows, cond, base, families, seed=0):
    # Match repeat IDs too: an extra repeat in one arm must not change a paired effect.
    valid = [r for r in rows if r.get('outcome') != 'budget_exceeded']
    a = {(r['question_id'], r.get('repeat', 0)): float(r['success']) for r in valid if r['condition'] == cond}
    b = {(r['question_id'], r.get('repeat', 0)): float(r['success']) for r in valid if r['condition'] == base}
    common = sorted(a.keys() & b.keys())
    by_question = defaultdict(list)
    for key in common:
        by_question[key[0]].append(a[key] - b[key])
    diffs = {q: _mean(v) for q, v in by_question.items()}
    point, lo, hi = cluster_bootstrap(diffs, families, seed=seed)
    n_families = len({families[q] for q in diffs})
    method = 'cluster_bootstrap_paraphrase_families'
    if not diffs:
        method = 'not_available'
    elif lo == hi or n_families < 2:
        lo, hi = None, None
        method = 'cluster_bootstrap_degenerate_withheld'
    wins = sum(a[k] > b[k] for k in common)
    losses = sum(a[k] < b[k] for k in common)
    # Exact paired binary test only when observations are independent across families.
    p_exact = None
    if len(common) == len(diffs) == n_families and common:
        discordant = wins + losses
        p_exact = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) /
                      2 ** discordant) if discordant else 1.0
    return {'condition': cond, 'display_label': DISPLAY_LABELS.get(cond, cond), 'baseline': base,
            'baseline_display_label': DISPLAY_LABELS.get(base, base), 'n': len(diffs),
            'n_paired_trials': len(common), 'n_families': n_families,
            'diff': point, 'ci_low': lo, 'ci_high': hi, 'ci_method': method,
            'wins': wins, 'losses': losses, 'both_correct': sum(a[k] == b[k] == 1 for k in common),
            'both_wrong': sum(a[k] == b[k] == 0 for k in common),
            'mcnemar_exact_p_unadjusted': p_exact}


# ---------------------------------------------------------------- tables
def summarize(rows, conditions, families, prices):
    out = []
    for c in conditions:
        available = [r for r in rows if r['condition'] == c]
        if not available:
            continue
        rs = [r for r in available if r['outcome'] != 'budget_exceeded']
        ans = [r for r in rs if r['answerability'] == 'answerable']
        non = [r for r in rs if r['answerability'] != 'answerable']
        unsupported = [r for r in non if r['answerability'] == 'unsupported']
        point, interval, method = success_interval(rs, c, families)
        strict_rows = [{**r, 'success': r['outcome'] == 'correct'} for r in ans]
        strict_point, strict_interval, strict_method = success_interval(strict_rows, c, families)
        provider_seconds = [sum(e.get('provider_seconds', 0) for e in r['provider_events'])
                            for r in rs if r.get('provider_events') is not None]
        pacing_seconds = [sum(e.get('pacing_seconds', 0) for e in r['provider_events'])
                          for r in rs if r.get('provider_events') is not None]
        lat = [r['seconds']['total'] for r in rs]
        recall = [r['slot_recall'] for r in rs if r['slot_recall'] is not None]
        complete = [r['complete_evidence'] for r in rs if r['complete_evidence'] is not None]
        tin = sum(r['input_tokens'] for r in rs)
        tout = sum(r['output_tokens'] for r in rs)
        succ = sum(r['success'] for r in rs)
        cost = None
        if prices.get('input_per_million_tokens') is not None and prices.get('output_per_million_tokens') is not None:
            total = (tin * prices['input_per_million_tokens'] + tout * prices['output_per_million_tokens']) / 1e6
            cost = total / succ if succ else None
        out.append({
            'condition': c, 'display_label': DISPLAY_LABELS.get(c, c),
            'condition_name': CONDITION_NAMES.get(c, c), 'n': len(rs), 'n_trials_recorded': len(available),
            'n_questions': len({r['question_id'] for r in rs}),
            'n_families': len({families[r['question_id']] for r in rs}),
            'n_success': succ, 'success': point, 'success_ci': interval, 'success_ci_method': method,
            'n_answerable': len(ans), 'n_correct_results': sum(r['outcome'] == 'correct' for r in ans),
            'strict_answer_accuracy': strict_point,
            'strict_answer_ci_low': strict_interval[0], 'strict_answer_ci_high': strict_interval[1],
            'strict_answer_ci_method': strict_method,
            'n_correct_clarifications': sum(r['outcome'] == 'correct_clarification' for r in ans),
            'answerable_correct': _rate([r['outcome'] in ('correct', 'correct_clarification') for r in ans]),
            'n_answerable_executed': sum(r['executed'] for r in ans), 'executed': _rate([r['executed'] for r in ans]),
            'n_nonanswerable': len(non), 'n_correct_abstentions': sum(r['success'] for r in non),
            'abstention_correct': _rate([r['success'] for r in non]),
            'n_unsupported': len(unsupported),
            'n_unsupported_answered': sum(r['outcome'] == 'missed_abstention' for r in unsupported),
            'unsupported_answer_rate': _rate([r['outcome'] == 'missed_abstention' for r in unsupported]),
            'n_transport_errors': sum(r['outcome'] == 'transport_error' for r in rs),
            'slot_recall': _mean(recall), 'complete_evidence': _rate(complete),
            'n_evidence_labeled': len(recall),
            'p50_s': percentile(lat, 0.5), 'p95_s': percentile(lat, 0.95), 'n_latency': len(lat),
            'n_provider_timed': len(provider_seconds), 'provider_p50_s': percentile(provider_seconds, 0.5),
            'provider_mean_s': _mean(provider_seconds), 'pacing_mean_s': _mean(pacing_seconds),
            'input_tokens': tin / len(rs) if rs else None, 'output_tokens': tout / len(rs) if rs else None,
            'api_calls': _mean([r['api_calls'] for r in rs]), 'retrieval_calls': _mean([r['retrieval_calls'] for r in rs]),
            'repairs': _mean([r['repairs'] for r in rs]),
            'n_repair_trials': sum(r['repairs'] > 0 for r in rs),
            'repair_rate': _rate([r['repairs'] > 0 for r in rs]),
            'budget_exceeded': len(available) - len(rs),
            'cost_per_success_est': cost,
        })
    return out


def _rate(flags):
    flags = list(flags)
    return sum(flags) / len(flags) if flags else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _fmt(v, pct=False, digits=2):
    if v is None:
        return '–'
    if isinstance(v, tuple):
        return '[' + ', '.join(_fmt(x, pct, digits) for x in v) + ']'
    return f'{100 * v:.1f}%' if pct else f'{v:.{digits}f}'


def _table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|']
    lines += ['| ' + ' | '.join(str(x) for x in row) + ' |' for row in rows]
    return '\n'.join(lines)


def _write_csv(path, rows):
    with open(path, 'w', newline='') as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def resource_accounting(raw_rows, latest_rows, conditions, provider_events=None):
    """All trace attempts versus retained, non-budget-stopped scored trials.

    Tokens are provider-reported successful-response usage, not total billable use:
    failed requests can have unknown usage. A process crash before trace append is
    not recoverable from this file and must not be silently presented as measured.
    """
    out = []
    for condition in conditions:
        raw = [r for r in raw_rows if r['condition'] == condition]
        latest = [r for r in latest_rows if r['condition'] == condition]
        scored = [r for r in latest if r['outcome'] != 'budget_exceeded']
        events = [e for e in provider_events if e['condition'] == condition] if provider_events is not None else None
        out.append({
            'condition': condition, 'display_label': DISPLAY_LABELS.get(condition, condition),
            'all_trace_attempts': len(raw), 'latest_trials': len(latest), 'scored_trials': len(scored),
            'superseded_trace_attempts': len(raw) - len(latest),
            'all_metered_calls': sum(r.get('metered_calls', r.get('api_calls', 0)) for r in raw),
            'scored_metered_calls': sum(r.get('metered_calls', r.get('api_calls', 0)) for r in scored),
            'all_recorded_input_tokens': sum(r.get('input_tokens', 0) for r in raw),
            'all_recorded_output_tokens': sum(r.get('output_tokens', 0) for r in raw),
            'all_failed_transport_attempts': sum(t['stage'] == 'transport' and not t.get('ok') and
                                                t.get('attempted', True) for r in raw for t in r.get('trace', [])),
            'all_trace_seconds': sum(r['seconds']['total'] for r in raw),
            'provider_audit_calls': len(events) if events is not None else None,
            'provider_audit_failures': sum(not e.get('ok') for e in events) if events is not None else None,
            'provider_audit_seconds': sum(e.get('provider_seconds', 0) for e in events) if events is not None else None,
            'provider_audit_pacing_seconds': sum(e.get('pacing_seconds', 0) for e in events) if events is not None else None,
            'provider_audit_input_tokens': sum((e.get('usage') or {}).get('input_tokens', 0) or 0
                                              for e in events) if events is not None else None,
            'provider_audit_output_tokens': sum((e.get('usage') or {}).get('output_tokens', 0) or 0
                                               for e in events) if events is not None else None,
        })
    return out


# ---------------------------------------------------------------- figures
def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def _figure(w, h):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(w, h), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    return plt, fig, ax


def plot_quality_cost(summary, path, x_key='input_tokens', x_label='Mean input tokens per question (all calls)'):
    plt, fig, ax = _figure(6.4, 4.0)
    pts = [s for s in summary if s['success'] is not None]
    if not pts:
        plt.close(fig)
        return False
    xs = [s[x_key] for s in pts]
    span_x = (max(xs) - min(xs)) or 1
    placed = []
    for s in sorted(pts, key=lambda s: (-s['success'], s[x_key])):
        lo, hi = s['success_ci']
        yerr = [[max(0, s['success'] - lo)], [max(0, hi - s['success'])]] if lo is not None and hi is not None else None
        ax.errorbar(s[x_key], s['success'], yerr=yerr, fmt='o',
                    color=SERIES[0], ecolor=SERIES[0], elinewidth=2, capsize=0, markersize=8,
                    markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        # Dodge labels that would overlap an earlier label (close in both x and y).
        dy = 4
        while any(abs(s[x_key] - px) / span_x < 0.08 and abs(dy - pdy) < 11 and abs(s['success'] - py) < 0.05
                  for px, py, pdy in placed):
            dy -= 12
        placed.append((s[x_key], s['success'], dy))
        ax.annotate(s.get('display_label', s['condition']), (s[x_key], s['success']), xytext=(9, dy), textcoords='offset points',
                    color=INK, fontsize=9, va='center')
    ax.set_xlabel(x_label, color=INK_2, fontsize=9)
    ax.set_ylabel('Task success (95% interval where estimable)', color=INK_2, fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(lambda v, _: f'{v:.0%}')
    ax.xaxis.set_major_formatter(lambda v, _: f'{v:,.0f}')
    ax.set_title('Quality versus cost by condition', color=INK, fontsize=11, loc='left')
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return True


def plot_errors(rows, conditions, path):
    counts = {c: Counter(r['error_category'] for r in rows if r['condition'] == c and r['error_category'])
              for c in conditions}
    cats = [e for e in ERROR_ORDER if any(counts[c][e] for c in conditions)]
    if not cats:
        return False
    plt, fig, ax = _figure(6.4, 0.5 * len(conditions) + 1.6)
    left = [0] * len(conditions)
    for i, cat in enumerate(cats):
        vals = [counts[c][cat] for c in conditions]
        ax.barh(conditions, vals, left=left, color=SERIES[i % len(SERIES)], height=0.6,
                edgecolor=SURFACE, linewidth=2, label=cat.replace('_', ' '), zorder=3)
        left = [a + b for a, b in zip(left, vals)]
    for y, total in enumerate(left):
        ax.text(total + 0.2, y, str(total), va='center', color=INK_2, fontsize=9)
    ax.invert_yaxis()
    ax.set_yticks(range(len(conditions)), [DISPLAY_LABELS.get(c, c) for c in conditions])
    ax.grid(axis='y', visible=False)
    ax.set_xlabel('Failed runs', color=INK_2, fontsize=9)
    ax.set_title('Failure categories by condition', color=INK, fontsize=11, loc='left')
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.25), ncol=min(4, len(cats)), frameon=False,
              fontsize=8, labelcolor=INK_2)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE, bbox_inches='tight')
    plt.close(fig)
    return True


def plot_coverage(values, path, title):
    plt, fig, ax = _figure(6.4, 0.45 * len(values) + 1.4)
    names = list(values)
    ax.barh(names, [values[n] for n in names], color=SERIES[0], height=0.55, zorder=3)
    for y, n in enumerate(names):
        ax.text(values[n] + 0.01, y, f'{values[n]:.0%}', va='center', color=INK_2, fontsize=9)
    ax.invert_yaxis()
    ax.grid(axis='y', visible=False)
    ax.set_xlim(0, 1.1)
    ax.xaxis.set_major_formatter(lambda v, _: f'{v:.0%}')
    ax.set_title(title, color=INK, fontsize=11, loc='left')
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------- sections
def retrieval_section(run_dir, out_dir, config):
    rows = [json.loads(line) for line in (run_dir / 'retrieval.jsonl').read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r['n_slots']]
    if not rows:
        return '_No retrieval labels in this run._'
    ks = config['retrieval']['ranking_k']
    lines = [f'Questions with labelled evidence: {len(rows)}. Slot recall/nDCG/complete-set are computed on '
             'the single global ranking (retriever quality); quota coverage is the per-kind context actually '
             'fed to the model.\n']
    head = ['Retriever'] + [f'{m}@{k}' for k in ks for m in ('recall', 'nDCG', 'complete')] + ['mean ms']
    body = []
    for name in rows[0]['rankers']:
        vals = [name]
        for k in ks:
            for m in ('recall', 'ndcg', 'complete'):
                vals.append(_fmt(_mean([r['rankers'][name][f'{m}@{k}'] for r in rows]), pct=True))
        vals.append(f"{1000 * _mean([r['rankers'][name]['seconds'] for r in rows]):.1f}")
        body.append(vals)
    lines.append(_table(head, body))
    grid = list(rows[0]['rankers'][next(iter(rows[0]['rankers']))]['quota_coverage'])
    q = config['retrieval']['quotas']
    lines.append(f"\n**Context coverage with per-kind quotas** (schema {q['schema']}, domain {q['domain']}, "
                 'entity k varies): complete-evidence rate / mean slot recall.\n')
    body = [[name] + [f"{_fmt(_mean([r['rankers'][name]['quota_coverage'][ek]['complete'] for r in rows]), True)} / "
                      f"{_fmt(_mean([r['rankers'][name]['quota_coverage'][ek]['slot_recall'] for r in rows]), True)}"
                      for ek in grid] for name in rows[0]['rankers']]
    lines.append(_table(['Retriever'] + [f'entity k={ek}' for ek in grid], body))
    conds = list(rows[0]['contexts'])
    cov = {c: _mean([r['contexts'][c]['complete'] for r in rows]) for c in conds}
    lines.append('\n**Complete evidence in the context each condition would send**\n')
    lines.append(_table(['Condition', 'Cards', 'Complete evidence', 'Slot recall'],
                        [[DISPLAY_LABELS.get(c, c), rows[0]['contexts'][c]['n_cards'], _fmt(cov[c], True),
                          _fmt(_mean([r['contexts'][c]['slot_recall'] for r in rows]), True)] for c in conds]))
    plot_coverage({DISPLAY_LABELS.get(c, c): v for c, v in cov.items()}, out_dir / 'context_coverage.png',
                  'Complete evidence in context, by condition')
    lines.append('\n![Context coverage](context_coverage.png)\n\nCondition A has no entity cards by design; '
                 'its coverage counts entity slots as missing even when the model can match names with ILIKE.')
    return '\n'.join(lines)


def generation_section(run_dir, out_dir, manifest, records):
    trace_path = run_dir / 'traces.jsonl'
    traces = read_traces(trace_path)
    expected_keys = {(qid, c, rep) for qid in manifest['question_ids'] for c in manifest['conditions']
                     for rep in range(manifest.get('repeats', 1))}
    rows = [r for key, r in traces.items() if key in expected_keys]
    raw_rows = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    raw_rows = [r for r in raw_rows if (r['question_id'], r['condition'], r.get('repeat', 0)) in expected_keys]
    scored = [r for r in rows if r['outcome'] != 'budget_exceeded']
    conditions = list(manifest['conditions'])
    labels = DISPLAY_LABELS
    label = lambda c: labels.get(c, c)
    families = {r['id']: r['paraphrase_family'] for r in records}
    expected, done = len(expected_keys), len(scored)
    simulated = manifest.get('mode') == 'fake'
    prices = manifest['config'].get('prices', {})
    summary = summarize(rows, conditions, families, prices)
    for s in summary:
        s['display_label'] = label(s['condition'])
        s['n_expected'] = sum(c == s['condition'] for _, c, _ in expected_keys)
        s['n_missing_or_budget_stopped'] = s['n_expected'] - s['n']
        if simulated:
            s['success_ci'] = (None, None)
            s['strict_answer_ci_low'] = s['strict_answer_ci_high'] = None
            s['success_ci_method'] = s['strict_answer_ci_method'] = 'scripted_pipeline_check_not_applicable'
    lines = [f'Scored trials: {done} of {expected} expected '
             f'({expected - done} not run or stopped at the call cap). Model: `{manifest.get("model")}`.\n']
    if simulated:
        lines.append('> **Scripted pipeline verification only — no real language model was evaluated.** '
                     'The stand-in returns prepared gold SQL or abstentions. Success counts verify plumbing, '
                     'not Text-to-SQL quality or benefit from RAG. Statistical intervals, paired model '
                     'comparisons and the quality-versus-cost figure are intentionally omitted. Token counts '
                     'are fixture estimates; timings are local harness timings, not provider latency.\n')
    if done != expected:
        lines.append('> **Incomplete comparison.** Conditions can have different denominators; do not rank '
                     'their unpaired rates. Paired comparisons use only matching completed question/repeat IDs.\n')
    lines.append(_table(['Display label', 'Internal condition', 'Method'],
                        [[label(c), c, CONDITION_NAMES.get(c, c)] for c in conditions]))
    if manifest['config'].get('comparison', {}).get('baseline'):
        lines.append('\nBaseline A: ' + manifest['config']['comparison']['baseline'])
    lines.append('\nBudget-stopped or unrun trials are not model errors and are excluded from accuracy. '
                 'Transport failures remain failures in the end-to-end denominator. Task success accepts '
                 'a correct result or the benchmark-prescribed abstention/clarification. SQL-result accuracy '
                 'requires a correct executed result on an answerable item; accepted clarification is reported '
                 'separately in `summary.csv`. Execution rate alone does not establish correctness.\n')
    lines.append(_table(
        ['Cond.', 'Task success count', 'Task success [95% interval]', 'SQL-result accuracy', 'Executed',
         'Abstention / clarification correct', 'Unsupported questions answered', 'Repair rate'],
        [[s['display_label'], f"{s['n_success']}/{s['n']}",
          f"{_fmt(s['success'], True)} {_fmt(s['success_ci'], True)}",
          f"{s['n_correct_results']}/{s['n_answerable']} ({_fmt(s['strict_answer_accuracy'], True)})",
          f"{s['n_answerable_executed']}/{s['n_answerable']} ({_fmt(s['executed'], True)})",
          f"{s['n_correct_abstentions']}/{s['n_nonanswerable']}",
          f"{s['n_unsupported_answered']}/{s['n_unsupported']}", _fmt(s['repair_rate'], True)] for s in summary]))
    lines.append('\nIntervals use Wilson for one binary observation per independent paraphrase family; '
                 'otherwise a 10,000-resample family bootstrap is used. Collapsed bootstrap intervals are '
                 'withheld, not interpreted as certainty. Repeats are averaged within question for task success. '
                 'Intervals do not capture benchmark annotation errors or generalization beyond this dataset. '
                 'Unsupported-answer rate is one observable safety check, not a comprehensive hallucination metric.\n')
    lines.append(_table(['Cond.', 'Evidence-labeled n', 'Slot recall', 'Complete evidence', 'Latency n',
                         'p50 s', 'p95 s', 'Mean input tokens', 'Mean output tokens', 'Mean API calls',
                         'Mean repairs', 'Cost/success (est.)'],
                        [[s['display_label'], s['n_evidence_labeled'], _fmt(s['slot_recall'], True),
                          _fmt(s['complete_evidence'], True), s['n_latency'], _fmt(s['p50_s']), _fmt(s['p95_s']),
                          _fmt(s['input_tokens'], digits=0), _fmt(s['output_tokens'], digits=0),
                          _fmt(s['api_calls']), _fmt(s['repairs']), _fmt(s['cost_per_success_est'], digits=5)]
                         for s in summary]))
    if prices.get('input_per_million_tokens') is None:
        lines.append('\nCost column empty: no dated prices in the config. Token counts are reported instead.')
    else:
        lines.append(f"\nCost is an estimate from list prices as of {prices.get('as_of')}, not a billed amount.")
    lines.append('\nLatency includes configured pacing, provider retries, context retrieval and SQL execution; '
                 'it is operational run time, not isolated model speed. Tail latency from small samples is noisy. '
                 'Input/output tokens are recorded successful-response usage; failed-request billing can be unknown. '
                 'Condition A retrieves no entity cards, so its evidence coverage is not a retrieval-quality baseline.')
    if any(s['n_provider_timed'] for s in summary):
        lines.append('\n**Provider time separated from deliberate pacing** (sum of calls per scored trial; '
                     'includes provider retries, excludes client pacing).\n')
        lines.append(_table(['Condition', 'Timed trials', 'Provider median s', 'Provider mean s', 'Pacing mean s'],
                            [[s['display_label'], s['n_provider_timed'], _fmt(s['provider_p50_s']),
                              _fmt(s['provider_mean_s']), _fmt(s['pacing_mean_s'])] for s in summary]))

    diffs = [paired_difference(rows, c, 'A', families) for c in conditions if c != 'A'] \
        if 'A' in conditions and not simulated else []
    for d in diffs:
        d['display_label'], d['baseline_display_label'] = label(d['condition']), label(d['baseline'])
    if diffs:
        lines.append('\n**Prespecified paired differences versus A** (10,000 family-bootstrap resamples; '
                     'difference in percentage points). Wins/losses count matching trials where only one '
                     'condition succeeds. Zero-width intervals are withheld; no observed difference is not '
                     'proof of equivalence. Exact McNemar p-values, when independent pairs permit them, are '
                     'two-sided and unadjusted for the multiple comparisons; they do not establish superiority.\n')
        lines.append(_table(['Condition', 'vs', 'Paired questions', 'Wins', 'Losses', 'Both correct',
                             'Both wrong', 'Difference (pp)', '95% interval (pp)', 'Exact p (unadjusted)'],
                            [[d['display_label'], d['baseline_display_label'], d['n'], d['wins'], d['losses'],
                              d['both_correct'], d['both_wrong'], _fmt(d['diff'] * 100 if d['diff'] is not None else None),
                              _fmt(tuple(v * 100 if v is not None else None for v in (d['ci_low'], d['ci_high']))),
                              _fmt(d['mcnemar_exact_p_unadjusted'], digits=4)] for d in diffs]))
    strict_rows = [{**r, 'success': r['outcome'] == 'correct'} for r in rows if r['answerability'] == 'answerable']
    strict_diffs = [paired_difference(strict_rows, c, 'A', families) for c in conditions if c != 'A'] \
        if 'A' in conditions and not simulated else []
    if strict_diffs:
        lines.append('\n**Primary paired comparison: strict SQL-result accuracy on answerable questions.** '
                     'Same uncertainty rules as above; correct clarification is not a correct SQL result.\n')
        lines.append(_table(['Condition', 'vs', 'Paired questions', 'Wins', 'Losses', 'Both correct', 'Both wrong',
                             'Difference (pp)', 'Exact p (unadjusted)'],
                            [[d['display_label'], 'A', d['n'], d['wins'], d['losses'], d['both_correct'],
                              d['both_wrong'], _fmt(d['diff'] * 100 if d['diff'] is not None else None),
                              _fmt(d['mcnemar_exact_p_unadjusted'], digits=4)] for d in strict_diffs]))
    fams = sorted({r['intent_family'] for r in scored})
    lines.append('\n**Task success by question family**\n')
    lines.append(_table(['Family', 'n q'] + [label(c) for c in conditions], [
        [f, len({r['question_id'] for r in scored if r['intent_family'] == f})] +
        [_fmt(_mean(list(per_question_success([r for r in scored if r['intent_family'] == f], c).values())), True)
         for c in conditions] for f in fams]))
    held = [r for r in scored if r.get('entity_heldout')]
    if held:
        lines.append('\n**Entity-held-out test slice** (entities absent from dev): ' + ', '.join(
            f"{c} {_fmt(_rate([r['success'] for r in held if r['condition'] == c]), True)}" for c in conditions))
    if {'F1', 'F3'} <= set(conditions):
        lines.append('\n**Retrieval budget (F1 vs F3)**: ' + '; '.join(
            f"{s['condition']}: success {_fmt(s['success'], True)}, {_fmt(s['api_calls'])} calls, "
            f"{_fmt(s['retrieval_calls'])} searches, {s['input_tokens']:.0f} input tokens"
            for s in summary if s['condition'] in ('F1', 'F3')))
    counts = {c: Counter(r['error_category'] for r in scored if r['condition'] == c and r['error_category'])
              for c in conditions}
    lines.append('\n**Failure categories** (rule-based; `semantics` is the residual for manual review)\n')
    cats = [e for e in ERROR_ORDER if any(counts[c][e] for c in conditions)]
    lines.append(_table(['Condition'] + cats, [[c] + [counts[c][e] for e in cats] for c in conditions]))
    if summary and not simulated and plot_quality_cost(summary, out_dir / 'quality_cost.png'):
        lines.append('\n![Quality versus cost](quality_cost.png)')
    elif simulated:
        (out_dir / 'quality_cost.png').unlink(missing_ok=True)
    if plot_errors(scored, conditions, out_dir / 'errors.png'):
        lines.append('\n![Failure categories](errors.png)')
    failures = [r for r in rows if not r['success'] and r['outcome'] != 'budget_exceeded']
    lines.append(f'\n**Failures for manual inspection** ({len(failures)})\n')
    lines.append(_table(['Question', 'Cond.', 'Outcome', 'Category', 'Detail'], [
        [r['question_id'], r['condition'], r['outcome'], r['error_category'] or '',
         (r['detail'] or '').replace('|', '/').replace('\n', ' ')[:90]]
        for r in sorted(failures, key=lambda r: (r['question_id'], r['condition']))]))
    provider_path = run_dir / 'provider_calls.jsonl'
    provider_events = [json.loads(line) for line in provider_path.read_text().splitlines() if line.strip()] \
        if provider_path.exists() else None
    if provider_events is not None:
        provider_events = [e for e in provider_events if
                           (e.get('question_id'), e.get('condition'), e.get('repeat', 0)) in expected_keys]
    resources = resource_accounting(raw_rows, rows, conditions, provider_events)
    lines.append('\n**All-attempt resource accounting** retains superseded traces and any provider calls '
                 'recorded before a trial was interrupted. Provider audit totals are preferred when available. '
                 'No trace or audit can recover a request that crashed before its accounting record was written; '
                 'failed-request token use may be unknown.\n')
    lines.append(_table(['Condition', 'Recorded trace attempts', 'Superseded attempts', 'Scored calls',
                         'All trace calls', 'Provider audit calls', 'Provider failures'],
                        [[r['display_label'], r['all_trace_attempts'], r['superseded_trace_attempts'],
                          r['scored_metered_calls'], r['all_metered_calls'],
                          r['provider_audit_calls'] if r['provider_audit_calls'] is not None else '–',
                          r['provider_audit_failures'] if r['provider_audit_failures'] is not None else '–']
                         for r in resources]))
    _write_csv(out_dir / 'summary.csv', [{**{k: v for k, v in s.items() if k != 'success_ci'},
                                        'success_ci_low': s['success_ci'][0],
                                        'success_ci_high': s['success_ci'][1]} for s in summary])
    _write_csv(out_dir / 'paired_vs_baseline.csv', diffs)
    _write_csv(out_dir / 'paired_sql_accuracy_vs_baseline.csv', strict_diffs)
    _write_csv(out_dir / 'resource_accounting.csv', resources)
    question_rows = []
    for qid in manifest['question_ids']:
        for rep in range(manifest.get('repeats', 1)):
            record = next((r for r in records if r['id'] == qid), {})
            qrow = {'question_id': qid, 'repeat': rep, 'question': record.get('question', ''),
                    'answerability': record.get('answerability', '')}
            for c in conditions:
                trial = traces.get((qid, c, rep))
                qrow[f'{label(c)}_outcome'] = trial['outcome'] if trial else 'not_run'
                qrow[f'{label(c)}_success'] = trial['success'] if trial and trial['outcome'] != 'budget_exceeded' else None
            question_rows.append(qrow)
    _write_csv(out_dir / 'per_question.csv', question_rows)
    (out_dir / 'comparison.json').write_text(json.dumps({
        'run_id': manifest.get('run_id'), 'split': manifest.get('split'), 'mode': manifest.get('mode'),
        'real_model_performance_evidence': manifest.get('mode') == 'live',
        'expected_trials': expected, 'scored_trials': done, 'complete': expected == done,
        'summary': summary, 'paired_task_success': diffs, 'paired_sql_accuracy': strict_diffs,
        'resources': resources, 'per_question': question_rows,
    }, indent=2, ensure_ascii=False) + '\n')
    return '\n'.join(lines)


def build_report(run_id):
    run_dir = RUNS_DIR / run_id
    manifest = json.loads((run_dir / 'manifest.json').read_text())
    out_dir = REPORTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    benchmark_path = PROJECT_ROOT / manifest['benchmark']['path']
    if file_hash(benchmark_path) != manifest['benchmark']['sha256']:
        raise ValueError('Benchmark changed since the run; restore the recorded benchmark before reporting.')
    records = load_benchmark(benchmark_path, split=manifest['split'])
    head = [f'# Results: run `{run_id}`', '',
            f"Split **{manifest['split']}**, mode **{manifest['mode']}**, created {manifest['created']}. "
            f"Benchmark `{manifest['benchmark']['path']}` sha256 `{manifest['benchmark']['sha256'][:12]}` "
            f"({manifest['benchmark']['freeze_status']}); corpus sha256 `{manifest['corpus']['sha256'][:12]}` "
            f"({manifest['corpus']['cards']} cards); dense model `{manifest['dense_model']}` "
            f"rev `{manifest.get('dense_model_revision')}`; git `{(manifest.get('git') or {}).get('sha', '')[:10]}`"
            f"{' (dirty)' if (manifest.get('git') or {}).get('dirty') else ''}; answer cache {manifest['answer_cache']}."]
    if manifest['mode'] == 'fake':
        head.append('\n> **Pipeline check only.** The fake model returns gold answers; these numbers measure '
                    'the harness, not any model or retrieval method.')
    if manifest['benchmark']['freeze_status'] != 'frozen':
        head.append('\n> Benchmark not frozen at run time: results are provisional.')
    sections = []
    if (run_dir / 'retrieval.jsonl').exists():
        sections += ['## Retrieval (no model calls)', retrieval_section(run_dir, out_dir, manifest['config'])]
    if (run_dir / 'traces.jsonl').exists():
        sections += ['## End-to-end', generation_section(run_dir, out_dir, manifest, records)]
    path = out_dir / 'results.md'
    path.write_text('\n'.join(head) + '\n\n' + '\n\n'.join(sections) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(f'Report: {build_report(args.run_id)}')


if __name__ == '__main__':
    main()
