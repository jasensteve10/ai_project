"""Headless experiment runner: JSONL traces per (question, condition, repeat), resumable.

Modes:
  retrieval-only  no model calls; ranking and context-coverage metrics (default)
  fake            scripted model that returns the gold answer; tests the pipeline offline
  live            real model calls; requires --max-calls, paces requests, never uses the answer cache

Subcommand ``select`` picks the static retriever and entity quota for F from a *dev* run.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from src.agent.Agent import DB_PATH, ElectionSQLAgent, runtime_fingerprint
from src.agent.llm import DEFAULT_CLAUDE_MODEL, make_provider_llm
from src.evaluation.benchmark import file_hash, freeze_status, load_benchmark
from src.retrieval.context import ContextBuilder, Retrievers, resolve_retriever
from src.evaluation.scoring import (SUCCESS, classify, complete_set, error_category, retrieval_metrics,
                                    slot_recall)
from src.retrieval.corpus import CARDS_PATH, corpus_hash, verified_cards
from src.retrieval.pipeline import rag_fingerprint, run_with_context

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / 'configs/core.json'
RUNS_DIR = PROJECT_ROOT / 'experiments/runs'


class BudgetExceeded(Exception):
    """Raised before a model call that would exceed the run's call cap."""


class MeteredLLM:
    """Counts and paces every model call across the whole run (retries included)."""

    def __init__(self, llm, max_calls, pace=0.0, sleep=time.sleep, used=0, audit_path=None):
        self.llm, self.max_calls, self.pace, self.sleep = llm, max_calls, pace, sleep
        self.calls, self._last = used, None
        self.audit_path, self.events, self.tags = audit_path, [], {}

    def invoke(self, messages):
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f'call cap {self.max_calls} reached')
        pacing_seconds = 0.0
        if self.pace and self._last is not None:
            wait = self.pace - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
                pacing_seconds = wait
        self.calls += 1
        self._last = time.monotonic()
        event = {**self.tags, 'invocation_call': self.calls,
                 'started': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                 'pacing_seconds': pacing_seconds,
                 'prompt_sha256': hashlib.sha256(json.dumps(
                     [m.content for m in messages], ensure_ascii=False, default=str).encode()).hexdigest()}
        try:
            response = self.llm.invoke(messages)
            metadata = getattr(response, 'response_metadata', None) or {}
            event.update(ok=True, usage=getattr(response, 'usage_metadata', None),
                         response_metadata={k: metadata[k] for k in ('model_name', 'model_version', 'finish_reason')
                                            if k in metadata})
            return response
        except Exception as exc:
            event.update(ok=False, error_type=type(exc).__name__)
            raise
        finally:
            event['provider_seconds'] = time.monotonic() - self._last
            self.events.append(event)
            if self.audit_path:
                _append(self.audit_path, event)


class GoldLLM:
    """Offline stand-in that answers from the benchmark gold labels (pipeline test only)."""

    def __init__(self, records):
        self.by_question = {r['question']: r for r in records}

    def invoke(self, messages):
        text = messages[-1].content
        record = self.by_question.get(text) or next(
            (r for q, r in self.by_question.items() if f'Question: {q}\n' in text or text.startswith(q + '\n')), None)
        if record is None or record['answerability'] == 'unsupported':
            payload = {'status': 'unsupported', 'sql': None, 'response': 'Donnée absente.'}
        elif record['answerability'] == 'ambiguous':
            payload = {'status': 'needs_clarification', 'sql': None, 'response': 'Pouvez-vous préciser ?'}
        else:
            payload = {'status': 'answerable', 'sql': record['gold_sql'], 'response': None}
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False),
                               usage_metadata={'input_tokens': len(messages[0].content) // 4, 'output_tokens': 30})


def _git():
    try:
        sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=PROJECT_ROOT, capture_output=True,
                                    text=True).stdout.strip())
        return {'sha': sha, 'dirty': dirty}
    except OSError:
        return None


def _versions():
    import importlib.metadata as md
    out = {'python': sys.version.split()[0]}
    for pkg in ('duckdb', 'sqlglot', 'langchain-google-genai', 'sentence-transformers', 'torch', 'numpy'):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            pass
    return out


def read_traces(path):
    """Latest line per (question, condition, repeat) wins, so budget-stopped items can be re-run."""
    latest = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                latest[(row['question_id'], row['condition'], row.get('repeat', 0))] = row
    return latest


def _append(path, row):
    with open(path, 'a') as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def _manifest(args, config, cards, records, bench_path, model, dense_revision=None, dense_ft_version=None):
    return {
        'run_id': args.run_id, 'created': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'mode': args.mode, 'split': args.split, 'conditions': args.conditions, 'repeats': args.repeats,
        'question_ids': [r['id'] for r in records], 'config': config,
        'benchmark': {'path': str(bench_path.relative_to(PROJECT_ROOT)), 'sha256': file_hash(bench_path),
                      'freeze_status': freeze_status(bench_path)},
        'corpus': {'path': str(CARDS_PATH.relative_to(PROJECT_ROOT)), 'sha256': corpus_hash(cards), 'cards': len(cards)},
        'provider': getattr(args, 'provider', 'gemini') if args.mode == 'live' else None, 'llm_fallback': 'disabled',
        'model': model, 'runtime_fingerprint': runtime_fingerprint(DB_PATH, model=model) if model else None,
        'dense_model': config['dense_model'], 'dense_model_revision': dense_revision,
        'dense_ft_model': config.get('dense_ft_model') if dense_ft_version else None,
        'dense_ft_model_version': dense_ft_version,
        'rag_fingerprint': rag_fingerprint('evaluation', config_path=args.config),
        'answer_cache': 'disabled', 'git': _git(), 'versions': _versions(),
        'evaluation_fingerprint': hashlib.sha256(b''.join(
            p.read_bytes() for p in (Path(__file__), Path(__file__).with_name('scoring.py')))).hexdigest(),
        'seed': args.seed,
        'invocations': [{'at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                         'max_calls': args.max_calls, 'pace_seconds': args.pace}],
    }


_IDENTITY = ('benchmark', 'corpus', 'model', 'runtime_fingerprint', 'rag_fingerprint', 'mode', 'split', 'config',
             'conditions', 'question_ids', 'repeats', 'seed', 'evaluation_fingerprint', 'provider')


def _prepare_run_dir(args, manifest):
    run_dir = RUNS_DIR / args.run_id
    path = run_dir / 'manifest.json'
    if path.exists():
        old = json.loads(path.read_text())
        diffs = [k for k in _IDENTITY if json.dumps(old.get(k), sort_keys=True) != json.dumps(manifest.get(k), sort_keys=True)]
        if diffs:
            raise SystemExit(f'Refusing to resume {args.run_id}: {diffs} changed. Use a new --run-id.')
        manifest['created'] = old['created']
        manifest['resumed'] = old.get('resumed', []) + [dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')]
        manifest['invocations'] = old.get('invocations', []) + manifest.get('invocations', [])
    run_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + '\n')
    return run_dir


def run_retrieval_only(records, conditions, config, builder, run_dir):
    out = run_dir / 'retrieval.jsonl'
    out.unlink(missing_ok=True)
    ks = config['retrieval']['ranking_k']
    grid = config['retrieval']['entity_k_grid']
    rankers = sorted({resolve_retriever(config['conditions'][c], config) for c in conditions
                      if config['conditions'][c]['context'] == 'retrieval'})
    for r in records:
        slots = r.get('evidence_slots') or []
        row = {'question_id': r['id'], 'split': r['split'], 'intent_family': r['intent_family'],
               'paraphrase_family': r['paraphrase_family'], 'n_slots': len(slots), 'rankers': {}, 'contexts': {}}
        for name in rankers:
            start = time.monotonic()
            ranking = builder.retrievers.rank(name, r['question'])
            seconds = time.monotonic() - start
            coverage = {}
            for ek in grid:
                quotas = {**config['retrieval']['quotas'], 'entity': ek}
                ids = builder.quota_select(ranking, quotas)
                coverage[str(ek)] = {'slot_recall': slot_recall(ids, slots), 'complete': complete_set(ids, slots)}
            row['rankers'][name] = {'top': ranking[:max(ks)], 'seconds': seconds,
                                    **retrieval_metrics(ranking, slots, ks), 'quota_coverage': coverage}
        for c in conditions:
            ids, _ = builder.build(config['conditions'][c], r)
            row['contexts'][c] = {'n_cards': len(ids), 'slot_recall': slot_recall(ids, slots),
                                  'complete': complete_set(ids, slots)}
        _append(out, row)
    return out


def run_generation(records, conditions, config, builder, run_dir, agent, metered, repeats, seed=0):
    out = run_dir / 'traces.jsonl'
    done = read_traces(out)
    cap = config.get('result_row_cap_in_trace', 50)
    stopped = False
    for r in records:
        order = list(conditions)
        random.Random(f'{seed}:{r["id"]}').shuffle(order)  # interleave conditions per question
        for rep in range(repeats):
            for c in order:
                key = (r['id'], c, rep)
                if key in done and done[key]['outcome'] != 'budget_exceeded':
                    continue
                if stopped:
                    continue
                cond = config['conditions'][c]
                t0 = time.monotonic()
                calls_before = metered.calls
                event_start = len(metered.events)
                metered.tags = {'question_id': r['id'], 'condition': c, 'repeat': rep}
                result = run_with_context(agent, builder, cond, r)
                total = time.monotonic() - t0
                ids = result['context_ids']
                retrieval_seconds = result['retrieval_seconds']
                agent_log = result['agent_searches']
                limit_hit = bool(result.get('rows')) and len(result['rows']) >= _applied_limit(result.get('final_sql'))
                outcome, detail = classify(r, result, limit_hit)
                used_ids = ids + result.get('added_context_ids', [])
                trace = result.get('trace', [])
                llm_seconds = sum(t.get('seconds', 0) for t in trace if t['stage'] == 'transport')
                agent_seconds = sum(t.get('seconds', 0) for t in trace if t['stage'] == 'retrieval')
                row = {
                    'question_id': r['id'], 'condition': c, 'repeat': rep, 'split': r['split'],
                    'intent_family': r['intent_family'], 'paraphrase_family': r['paraphrase_family'],
                    'answerability': r['answerability'], 'entity_heldout': r.get('entity_heldout', False),
                    'outcome': outcome, 'success': outcome in SUCCESS, 'detail': detail,
                    'error_category': error_category(r, result, outcome, used_ids, builder.by_id),
                    'status': result.get('status'), 'stage': result.get('stage'), 'error': result.get('error'),
                    'error_type': result.get('error_type'),
                    'executed': result.get('ok', False), 'response': result.get('response'),
                    'proposed_sql': result.get('proposed_sql'), 'final_sql': result.get('final_sql'),
                    'columns': result.get('columns'), 'rows': (result.get('rows') or [])[:cap],
                    'n_rows': len(result.get('rows') or []), 'limit_hit': limit_hit,
                    'context_ids': ids, 'added_context_ids': result.get('added_context_ids', []),
                    'agent_searches': agent_log,
                    'slot_recall': slot_recall(used_ids, r.get('evidence_slots') or []),
                    'complete_evidence': complete_set(used_ids, r.get('evidence_slots') or []),
                    'seconds': {'total': total, 'context': retrieval_seconds, 'llm': llm_seconds,
                                'agent_retrieval': agent_seconds,
                                'sql_and_overhead': max(total - retrieval_seconds - llm_seconds - agent_seconds, 0)},
                    'api_calls': result.get('api_calls', 0), 'attempts': result.get('attempts', 0),
                    'retrieval_calls': result.get('retrieval_calls', 0),
                    'repairs': sum(t['stage'] == 'repair' for t in trace),
                    'input_tokens': result.get('input_tokens', 0), 'output_tokens': result.get('output_tokens', 0),
                    'metered_calls': metered.calls - calls_before, 'trace': trace,
                    'provider_events': metered.events[event_start:],
                    'at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                }
                _append(out, row)
                print(f'{r["id"]}/{c}: {outcome}; {metered.calls - calls_before} provider calls', flush=True)
                if outcome == 'budget_exceeded':
                    stopped = True
                    print(f'Call cap reached at {r["id"]}/{c}; re-run with the same --run-id to resume.')
    return out, stopped


def _applied_limit(sql):
    if not sql:
        return 10 ** 9
    import sqlglot
    limit = sqlglot.parse_one(sql, read='duckdb').args.get('limit')
    try:
        return int(limit.expression.this)
    except (AttributeError, ValueError):
        return 10 ** 9


def select(run_id, config_path=CONFIG_PATH):
    """Choose F's static retriever and entity quota from a dev retrieval-only run (context coverage)."""
    run_dir = RUNS_DIR / run_id
    manifest = json.loads((run_dir / 'manifest.json').read_text())
    if manifest['split'] != 'dev':
        raise SystemExit('Selection must use a dev run only.')
    rows = [json.loads(line) for line in (run_dir / 'retrieval.jsonl').read_text().splitlines()]
    rows = [r for r in rows if r['n_slots']]
    scores = {}
    for name in rows[0]['rankers']:
        for ek in rows[0]['rankers'][name]['quota_coverage']:
            cov = [r['rankers'][name]['quota_coverage'][ek] for r in rows]
            scores[(name, int(ek))] = (sum(c['complete'] for c in cov) / len(cov),
                                       sum(c['slot_recall'] for c in cov) / len(cov))
    # Highest complete-set rate, then slot recall, then the smaller (cheaper) context.
    (name, ek), (complete, recall) = max(scores.items(), key=lambda kv: (kv[1][0], kv[1][1], -kv[0][1]))
    config = json.loads(Path(config_path).read_text())
    config['agent']['static_retriever'] = name
    config['retrieval']['quotas']['entity'] = ek
    config['agent']['selection'] = {
        'run_id': run_id, 'split': 'dev', 'criterion': 'complete-evidence rate, then slot recall, then smaller k',
        'chosen': {'retriever': name, 'entity_k': ek, 'complete': complete, 'slot_recall': recall},
        'candidates': {f'{n}@{k}': {'complete': v[0], 'slot_recall': v[1]} for (n, k), v in sorted(scores.items())},
        'at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}
    Path(config_path).write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n')
    return config['agent']['selection']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')
    sel = sub.add_parser('select', help='pick F retriever + entity quota from a dev retrieval-only run')
    sel.add_argument('--run-id', required=True)
    sel.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--split', choices=['dev', 'test'], default='dev')
    parser.add_argument('--conditions', help='comma-separated; default from config')
    parser.add_argument('--ids', help='comma-separated question IDs (subset)')
    parser.add_argument('--mode', choices=['retrieval-only', 'fake', 'live'], default='retrieval-only')
    parser.add_argument('--max-calls', type=int, help='hard cap on model calls for this invocation (live: required)')
    parser.add_argument('--pace', type=float, default=0.0, help='minimum seconds between model calls')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--seed', type=int, default=0, help='fixed seed for condition interleaving')
    parser.add_argument('--run-id')
    parser.add_argument('--allow-unfrozen', action='store_true', help='live test run on an unfrozen benchmark')
    parser.add_argument('--provider', choices=['gemini', 'claude'], default='gemini',
                        help='live model provider for the whole run (never mixed; claude is billed per token)')
    args = parser.parse_args(argv)

    if args.command == 'select':
        print(json.dumps(select(args.run_id, args.config), indent=2, ensure_ascii=False))
        return
    config = json.loads(args.config.read_text())
    args.conditions = args.conditions.split(',') if args.conditions else config['default_conditions']
    unknown = [c for c in args.conditions if c not in config['conditions']]
    if unknown:
        parser.error(f'unknown conditions {unknown}')
    bench_path = PROJECT_ROOT / config['benchmark']
    records = load_benchmark(bench_path, split=args.split)
    if args.ids:
        wanted = set(args.ids.split(','))
        missing = wanted - {r['id'] for r in records}
        if missing:
            parser.error(f'unknown question IDs for {args.split}: {sorted(missing)}')
        records = [r for r in records if r['id'] in wanted]
    if not records or args.repeats < 1 or args.pace < 0 or (args.max_calls is not None and args.max_calls < 1):
        parser.error('require nonempty questions, positive repeats/call cap, and nonnegative pacing')
    if args.mode == 'live':
        if args.max_calls is None:
            parser.error('--mode live requires --max-calls (free-quota guard)')
        if args.split == 'test' and freeze_status(bench_path) != 'frozen' and not args.allow_unfrozen:
            parser.error('test benchmark is not frozen; review and freeze it first (or pass --allow-unfrozen)')
    args.run_id = args.run_id or f'{dt.datetime.now():%Y%m%d-%H%M%S}-{args.split}-{args.mode}'

    cards = verified_cards()
    retrievers = Retrievers(cards, config['dense_model'], corpus_hash(cards), config.get('dense_revision'),
                            config.get('dense_ft_model'))
    builder = ContextBuilder(cards, config, retrievers)
    uses_dense = any(resolve_retriever(config['conditions'][c], config) in ('dense', 'hybrid')
                     for c in args.conditions if config['conditions'][c]['context'] == 'retrieval')
    dense_rev = retrievers.dense.model_revision if uses_dense else None
    uses_ft = any(resolve_retriever(config['conditions'][c], config) == 'dense_ft'
                  for c in args.conditions if config['conditions'][c]['context'] == 'retrieval')
    ft_version = retrievers.dense_ft.model_revision if uses_ft else None
    if args.mode == 'live':
        model = (os.getenv('GEMINI_MODEL') if args.provider == 'gemini'
                 else os.getenv('ANTHROPIC_MODEL') or DEFAULT_CLAUDE_MODEL)
    else:
        model = 'fake-gold' if args.mode == 'fake' else None
    manifest = _manifest(args, config, cards, records, bench_path, model, dense_rev, ft_version)
    run_dir = _prepare_run_dir(args, manifest)

    if args.mode == 'retrieval-only':
        out = run_retrieval_only(records, args.conditions, config, builder, run_dir)
        print(f'Retrieval metrics for {len(records)} questions -> {out}')
        return
    base = make_provider_llm(args.provider, model) if args.mode == 'live' else GoldLLM(records)
    metered = MeteredLLM(base, args.max_calls, args.pace, audit_path=run_dir / 'provider_calls.jsonl')
    agent = ElectionSQLAgent(llm=metered, model=model, db_path=DB_PATH)
    out, stopped = run_generation(records, args.conditions, config, builder, run_dir, agent, metered, args.repeats,
                                 seed=args.seed)
    print(f'{metered.calls} model calls this invocation; traces -> {out}' + (' (stopped at cap)' if stopped else ''))


if __name__ == '__main__':
    main()
