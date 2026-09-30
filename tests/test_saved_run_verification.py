"""Regression checks for local verification; never initialize a live provider."""
import json
from pathlib import Path
import shutil

import pytest

from src.evaluation import verify_saved_run as verifier
from src.model_training.__main__ import describe

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = 'text-to-sql-comparison-haiku-20260930'


@pytest.fixture
def saved_subset(tmp_path, monkeypatch):
    """One real archived clarification response, retaining real prompt/scoring checks."""
    source = ROOT / 'experiments/runs' / RUN_ID
    run = tmp_path / 'experiments/runs/subset'
    run.mkdir(parents=True)
    manifest = json.loads((source / 'manifest.json').read_text())
    trace = next(json.loads(line) for line in (source / 'traces.jsonl').read_text().splitlines()
                 if json.loads(line)['answerability'] == 'ambiguous')
    manifest.update(question_ids=[trace['question_id']], conditions=[trace['condition']], repeats=1)
    benchmark = tmp_path / manifest['benchmark']['path']
    benchmark.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / manifest['benchmark']['path'], benchmark)
    (run / 'manifest.json').write_text(json.dumps(manifest))
    (run / 'traces.jsonl').write_text(json.dumps(trace)+'\n')
    (run / 'provider_calls.jsonl').write_text('\n'.join(json.dumps(e) for e in trace['provider_events'])+'\n')
    monkeypatch.setattr(verifier, 'ROOT', tmp_path)
    return run, tmp_path / 'review'


def test_saved_response_verifies_without_a_provider(saved_subset):
    _, output = saved_subset
    result = verifier.verify('subset', output)
    assert all(result['checks'].values())
    assert result['successful_responses'] == 1


@pytest.mark.parametrize('corruption', ['score', 'success', 'prompt', 'ledger', 'ledger_event', 'missing_trial'])
def test_corrupt_saved_evidence_is_rejected(saved_subset, corruption):
    run, output = saved_subset
    path = run / 'traces.jsonl'
    trace = json.loads(path.read_text())
    if corruption == 'score':
        trace['outcome'] = 'correct'
    elif corruption == 'success':
        trace['success'] = False
    elif corruption == 'prompt':
        trace['provider_events'][0]['prompt_sha256'] = 'incorrect'
    elif corruption == 'ledger':
        (run / 'provider_calls.jsonl').write_text('')
    elif corruption == 'ledger_event':
        ledger = run / 'provider_calls.jsonl'
        event = json.loads(ledger.read_text())
        event['ok'] = not event['ok']
        ledger.write_text(json.dumps(event)+'\n')
    if corruption == 'missing_trial':
        path.write_text('')
    else:
        path.write_text(json.dumps(trace)+'\n')
    with pytest.raises(ValueError):
        verifier.verify('subset', output)


def test_changed_benchmark_is_rejected(saved_subset):
    run, output = saved_subset
    manifest = json.loads((run / 'manifest.json').read_text())
    manifest['benchmark']['sha256'] = 'changed'
    (run / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Benchmark differs'):
        verifier.verify('subset', output)


def test_setup_records_no_training_and_the_actual_encoder():
    config = json.loads((ROOT / 'configs/text_to_sql_comparison.json').read_text())
    setup = describe(config)
    assert setup['training_performed'] is False
    assert all(setup[k] is None for k in ('train_split', 'epochs', 'optimizer', 'learning_rate'))
    assert setup['encoder']['model'] == 'intfloat/multilingual-e5-small'
    assert setup['encoder']['revision'] == config['dense_revision']
