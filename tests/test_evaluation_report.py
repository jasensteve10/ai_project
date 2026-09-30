"""Regression checks for honest denominators, paired effects and resource totals."""
import csv
import json

import pytest

from src.evaluation import report


def trial(qid='q1', condition='A', outcome='correct', answerability='answerable', **kwargs):
    return {
        'question_id': qid, 'condition': condition, 'repeat': 0, 'answerability': answerability,
        'outcome': outcome, 'success': outcome in {'correct', 'correct_abstention', 'correct_clarification'},
        'executed': outcome in {'correct', 'wrong_result', 'missed_abstention'},
        'seconds': {'total': 10.0}, 'slot_recall': None, 'complete_evidence': None,
        'input_tokens': 100, 'output_tokens': 10, 'api_calls': 1, 'retrieval_calls': 0,
        'repairs': 0, 'metered_calls': 1, 'trace': [], 'intent_family': 'counts',
        'error_category': None, 'detail': '', **kwargs,
    }


def test_perfect_small_sample_has_nonzero_uncertainty():
    rows = [trial(f'q{i}') for i in range(3)]
    families = {f'q{i}': f'f{i}' for i in range(3)}
    point, (low, high), method = report.success_interval(rows, 'A', families)
    assert point == 1 and low == pytest.approx(0.4385029682449546) and high == pytest.approx(1)
    assert method == 'wilson_independent_questions'


def test_clustered_homogeneous_sample_does_not_claim_perfect_certainty():
    point, ci, method = report.success_interval([trial('q1'), trial('q2')], 'A', {'q1': 'f1', 'q2': 'f1'})
    assert point == 1 and ci == (None, None)
    assert method == 'cluster_bootstrap_degenerate_withheld'


def test_accuracy_denominators_distinguish_sql_clarification_and_budget():
    rows = [trial('q1'), trial('q2', outcome='correct_clarification'),
            trial('q3', outcome='transport_error'), trial('q4', outcome='budget_exceeded'),
            trial('q5', outcome='correct_abstention', answerability='ambiguous'),
            trial('q6', outcome='missed_abstention', answerability='unsupported', repairs=1)]
    s = report.summarize(rows, ['A'], {f'q{i}': f'f{i}' for i in range(1, 7)}, {})[0]
    assert s['n'] == 5 and s['budget_exceeded'] == 1 and s['n_answerable'] == 3
    assert s['success'] == pytest.approx(3 / 5)
    assert s['strict_answer_accuracy'] == pytest.approx(1 / 3)
    assert s['answerable_correct'] == pytest.approx(2 / 3)
    assert s['n_correct_clarifications'] == 1 and s['executed'] == pytest.approx(1 / 3)
    assert s['unsupported_answer_rate'] == 1 and s['abstention_correct'] == 0.5
    assert s['repair_rate'] == 0.2 and s['n_transport_errors'] == 1


def test_all_budget_stopped_has_no_accuracy_or_latency():
    s = report.summarize([trial(outcome='budget_exceeded')], ['A'], {'q1': 'f1'}, {})[0]
    assert s['n'] == 0 and s['success'] is None and s['strict_answer_accuracy'] is None
    assert s['p50_s'] is None and s['success_ci'] == (None, None)


def test_paired_comparison_matches_repeats_and_excludes_unfinished_trials():
    rows = [trial('q1', 'A', outcome='wrong_result'), trial('q1', 'B'),
            trial('q1', 'B', outcome='wrong_result', repeat=1),
            trial('q2', 'A'), trial('q2', 'B', outcome='budget_exceeded')]
    d = report.paired_difference(rows, 'B', 'A', {'q1': 'f1', 'q2': 'f2'})
    assert d['n'] == d['n_paired_trials'] == 1 and d['diff'] == 1
    assert d['wins'] == 1 and d['losses'] == 0
    assert d['ci_low'] is None and d['ci_high'] is None


def test_identical_paired_outcomes_do_not_prove_equivalence():
    rows = [trial(f'q{i}', c) for i in range(3) for c in ['A', 'B']]
    d = report.paired_difference(rows, 'B', 'A', {f'q{i}': f'f{i}' for i in range(3)})
    assert d['diff'] == 0 and d['both_correct'] == 3 and d['wins'] == d['losses'] == 0
    assert d['ci_low'] is None and d['mcnemar_exact_p_unadjusted'] == 1


def test_provider_time_is_separate_from_pacing():
    rows = [trial(provider_events=[{'provider_seconds': 2, 'pacing_seconds': 10},
                                   {'provider_seconds': 3, 'pacing_seconds': 5}])]
    s = report.summarize(rows, ['A'], {'q1': 'f1'}, {})[0]
    assert s['provider_p50_s'] == 5 and s['provider_mean_s'] == 5 and s['pacing_mean_s'] == 15


def test_resource_totals_preserve_resumed_and_interrupted_call_usage():
    stopped, final = trial(outcome='budget_exceeded', metered_calls=2), trial(metered_calls=1)
    audit = [{'condition': 'A', 'ok': ok, 'provider_seconds': 1, 'pacing_seconds': 2,
              'usage': {'input_tokens': 20, 'output_tokens': 3} if ok else None}
             for ok in [True, False, True, False]]
    r = report.resource_accounting([stopped, final], [final], ['A'], audit)[0]
    assert r['superseded_trace_attempts'] == 1 and r['all_metered_calls'] == 3
    assert r['scored_metered_calls'] == 1 and r['provider_audit_calls'] == 4
    assert r['provider_audit_failures'] == 2 and r['provider_audit_input_tokens'] == 40


def test_report_exports_compact_labels_and_only_manifest_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'plot_quality_cost', lambda *args, **kwargs: False)
    monkeypatch.setattr(report, 'plot_errors', lambda *args, **kwargs: False)
    run, out = tmp_path / 'run', tmp_path / 'out'
    run.mkdir()
    out.mkdir()
    raw = [trial('q1', 'A', outcome='budget_exceeded'), trial('q1', 'A'),
           trial('q1', 'B', outcome='wrong_result'), trial('outside_manifest', 'B')]
    (run / 'traces.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in raw))
    manifest = {'run_id': 'synthetic', 'model': 'fake', 'conditions': ['A', 'B'], 'question_ids': ['q1'],
                'repeats': 1, 'config': {'display_labels': {'B': 'B1 — very long description'}}}
    records = [{'id': 'q1', 'paraphrase_family': 'f1', 'question': 'comma, quote " question',
                'answerability': 'answerable'}]
    text = report.generation_section(run, out, manifest, records)
    assert 'Scored trials: 2 of 2' in text
    result = json.loads((out / 'comparison.json').read_text())
    assert result['complete'] and result['summary'][1]['display_label'] == 'B1'
    assert result['resources'][0]['superseded_trace_attempts'] == 1
    assert result['paired_sql_accuracy'][0]['losses'] == 1
    with open(out / 'per_question.csv') as handle:
        qs = list(csv.DictReader(handle))
    assert len(qs) == 1 and qs[0]['question'] == records[0]['question']
    assert qs[0]['B1_outcome'] == 'wrong_result'
    with open(out / 'summary.csv') as handle:
        summary = list(csv.DictReader(handle))
    assert summary[1]['strict_answer_accuracy'] == '0.0'


def test_report_refuses_changed_benchmark_instead_of_relabeling_old_results(tmp_path, monkeypatch):
    run = tmp_path / 'runs' / 'r1'
    run.mkdir(parents=True)
    (tmp_path / 'benchmark.jsonl').write_text('changed\n')
    (run / 'manifest.json').write_text(json.dumps({
        'benchmark': {'path': 'benchmark.jsonl', 'sha256': 'old_hash'}, 'split': 'dev'}))
    monkeypatch.setattr(report, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(report, 'REPORTS_DIR', tmp_path / 'reports')
    monkeypatch.setattr(report, 'PROJECT_ROOT', tmp_path)
    with pytest.raises(ValueError, match='Benchmark changed'):
        report.build_report('r1')


def test_plot_accepts_withheld_intervals_and_uses_labels(tmp_path):
    pytest.importorskip('matplotlib')
    rows = [trial('q1'), trial('q2')]
    summary = report.summarize(rows, ['A'], {'q1': 'f1', 'q2': 'f1'}, {})
    path = tmp_path / 'quality.png'
    assert report.plot_quality_cost(summary, path)
    assert path.stat().st_size > 0


def test_fake_report_omits_model_comparisons_and_quality_chart(tmp_path, monkeypatch):
    def forbidden_chart(*args, **kwargs):
        pytest.fail('A scripted fixture must not produce a model-quality chart')
    monkeypatch.setattr(report, 'plot_quality_cost', forbidden_chart)
    monkeypatch.setattr(report, 'plot_errors', lambda *args, **kwargs: False)
    (tmp_path / 'traces.jsonl').write_text('\n'.join(json.dumps(trial('q1', c)) for c in ['A', 'B']))
    (tmp_path / 'quality_cost.png').write_bytes(b'stale prior chart')
    manifest = {'run_id': 'fake', 'mode': 'fake', 'model': 'fake-gold', 'conditions': ['A', 'B'],
                'question_ids': ['q1'], 'config': {}}
    record = {'id': 'q1', 'paraphrase_family': 'f1', 'answerability': 'answerable'}
    text = report.generation_section(tmp_path, tmp_path, manifest, [record])
    result = json.loads((tmp_path / 'comparison.json').read_text())
    assert 'Scripted pipeline verification only' in text
    assert result['real_model_performance_evidence'] is False
    assert result['paired_task_success'] == result['paired_sql_accuracy'] == []
    assert result['summary'][0]['success_ci'] == [None, None]
    assert not (tmp_path / 'quality_cost.png').exists()
