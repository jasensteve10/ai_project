"""Check archived measurements by rescoring and re-executing saved SQL, without an API."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from src.agent.Agent import ElectionSQLAgent, run_safe_sql
from src.evaluation.benchmark import load_benchmark, file_hash
from src.evaluation.runner import read_traces
from src.evaluation.scoring import SUCCESS, classify
from src.retrieval.context import ContextBuilder
from src.retrieval.corpus import load_cards, corpus_hash

ROOT = Path(__file__).resolve().parents[2]


def verify(run_id, output_dir=None):
    run = ROOT / 'experiments/runs' / run_id
    manifest = json.loads((run / 'manifest.json').read_text())
    records = {r['id']: r for r in load_benchmark(ROOT / manifest['benchmark']['path'])}
    cards = load_cards()
    if file_hash(ROOT / manifest['benchmark']['path']) != manifest['benchmark']['sha256']:
        raise ValueError('Benchmark differs from the archived run; refusing to rescore with changed labels.')
    if corpus_hash(cards) != manifest['corpus']['sha256']:
        raise ValueError('Corpus differs from the archived run.')
    traces = read_traces(run / 'traces.jsonl')
    expected = {(q, c, rep) for q in manifest['question_ids'] for c in manifest['conditions']
                for rep in range(manifest.get('repeats', 1))}
    if set(traces) != expected:
        raise ValueError('Missing or extra question/condition/repeat combinations.')
    # No retriever or generator is called: render the context IDs saved in the trace.
    builder = ContextBuilder(cards, manifest['config'], retrievers=None)
    agent = ElectionSQLAgent(llm=object(), model=manifest['model'])
    details, counts = [], {c: Counter() for c in manifest['conditions']}
    for key in sorted(expected):
        trace, record = traces[key], records[key[0]]
        outcome, _ = classify(record, trace, trace.get('limit_hit', False))
        checked = {'question_id': key[0], 'condition': key[1], 'repeat': key[2],
                   'stored_outcome': trace['outcome'], 'rescored_outcome': outcome,
                   'scoring_matches': outcome == trace['outcome'],
                   'success_flag_matches': trace['success'] == (outcome in SUCCESS)}
        if trace.get('final_sql'):
            sql_result = run_safe_sql(trace['final_sql'])
            sql_result['status'] = 'answerable' if sql_result['ok'] else 'error'
            replay, _ = classify(record, sql_result, trace.get('limit_hit', False))
            # Row order can differ for queries with no ORDER BY; scoring still
            # applies the benchmark's declared order policy.
            checked.update(replayed_outcome=replay, replay_matches=replay == outcome,
                           sql_executes=sql_result['ok'])
        context = builder.render(trace['context_ids'])
        prompt = [agent.build_system_prompt(context), record['question']]
        digest = hashlib.sha256(json.dumps(prompt, ensure_ascii=False, default=str).encode()).hexdigest()
        events = trace.get('provider_events', [])
        checked['initial_prompt_hash_matches'] = bool(events) and events[0]['prompt_sha256'] == digest
        counts[key[1]][outcome] += 1
        details.append(checked)
    audit = [json.loads(line) for line in (run / 'provider_calls.jsonl').read_text().splitlines() if line]
    summary = {}
    for c in manifest['conditions']:
        rs = [r for r in traces.values() if r['condition'] == c]
        summary[c] = {'questions': len(rs), 'answerable': sum(r['answerability'] == 'answerable' for r in rs),
                      'correct_sql': counts[c]['correct'],
                      'successful_tasks': sum(r['success'] for r in rs),
                      'outcomes': dict(counts[c])}
    checks = {'all_saved_scores_reproduce': all(r['scoring_matches'] for r in details),
              'all_success_flags_reproduce': all(r['success_flag_matches'] for r in details),
              'all_executed_outcomes_reproduce': all(r.get('replay_matches', True) for r in details),
              'all_initial_prompts_reproduce': all(r['initial_prompt_hash_matches'] for r in details),
              'call_ledger_matches_traces': len(audit) == sum(r['metered_calls'] for r in traces.values()),
              'call_events_match_traces': Counter(json.dumps(e, sort_keys=True) for e in audit) ==
                                         Counter(json.dumps(e, sort_keys=True) for r in traces.values()
                                                 for e in r.get('provider_events', []))}
    result = {'run_id': run_id, 'verification': 'local replay; no provider requests',
              'manifest_model': manifest['model'], 'provider': manifest.get('provider'),
              'reported_response_models': sorted({e.get('response_metadata', {}).get('model_name')
                                                  for e in audit if e.get('response_metadata', {}).get('model_name')}),
              'mode': manifest['mode'], 'checks': checks, 'summary': summary,
              'provider_attempts': len(audit), 'successful_responses': sum(e['ok'] for e in audit),
              'input_sha256': {name: file_hash(run / name) for name in
                               ('manifest.json', 'traces.jsonl', 'provider_calls.jsonl')},
              'items': details,
              'limits': ['Saved artifacts are internally checked, not independently authenticated with the provider.',
                         'Draft development benchmark, not held-out test accuracy.',
                         'Value-based scoring permits extra predicted columns; it is not proof of every output claim.']}
    out = Path(output_dir) if output_dir else ROOT / 'experiments/reviews' / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('items', 'input_sha256')}, indent=2))
    if not all(checks.values()):
        raise ValueError(f'Saved-run verification needs review; see {out / "verification.json"}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    verify(args.run_id, args.output_dir)


if __name__ == '__main__':
    main()
