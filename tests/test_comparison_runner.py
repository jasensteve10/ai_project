import json
import hashlib
from types import SimpleNamespace

import pytest

from src.evaluation import runner
from src.evaluation.compare_rag import verify_preflight


def test_provider_audit_keeps_usage_model_and_failures_without_response_text(tmp_path):
    class Model:
        def invoke(self, messages):
            return SimpleNamespace(content='private answer', usage_metadata={'input_tokens': 5},
                                   response_metadata={'model_name': 'actual-model', 'secret': 'not logged'})

    path = tmp_path / 'calls.jsonl'
    meter = runner.MeteredLLM(Model(), 1, audit_path=path)
    meter.tags = {'question_id': 'q1', 'condition': 'A'}
    meter.invoke([SimpleNamespace(content='private prompt')])
    row = json.loads(path.read_text())
    assert row['response_metadata'] == {'model_name': 'actual-model'}
    assert row['usage'] == {'input_tokens': 5}
    assert row['condition'] == 'A' and row['provider_seconds'] >= 0
    assert 'private' not in path.read_text() and 'secret' not in path.read_text()
    with pytest.raises(runner.BudgetExceeded):
        meter.invoke([])
    assert len(path.read_text().splitlines()) == 1

    class Failed:
        def invoke(self, messages):
            raise TimeoutError('sensitive transport details')

    failed = runner.MeteredLLM(Failed(), 1, audit_path=path)
    with pytest.raises(TimeoutError):
        failed.invoke([])
    last = json.loads(path.read_text().splitlines()[-1])
    assert last['ok'] is False and last['error_type'] == 'TimeoutError'
    assert 'sensitive' not in path.read_text()


@pytest.mark.parametrize('field,new', [('conditions', ['A']), ('question_ids', ['q2']),
                                      ('repeats', 2), ('seed', 1), ('evaluation_fingerprint', 'new')])
def test_resume_rejects_changed_experiment_design(tmp_path, monkeypatch, field, new):
    monkeypatch.setattr(runner, 'RUNS_DIR', tmp_path)
    args = SimpleNamespace(run_id='study')
    manifest = {'created': 'today', 'conditions': ['A', 'B'], 'question_ids': ['q1'],
                'repeats': 1, 'seed': 0, 'evaluation_fingerprint': 'original'}
    runner._prepare_run_dir(args, manifest)
    with pytest.raises(SystemExit, match=field):
        runner._prepare_run_dir(args, {**manifest, field: new})


def test_preflight_blocks_data_changed_after_independent_audit(tmp_path):
    paths = {'benchmark': 'bench.jsonl', 'csv': 'dataset/clean/edan_2025_resultats.csv',
             'parquet': 'dataset/clean/edan_2025_resultats.parquet',
             'pdf': 'dataset/raw/EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf',
             'database': 'dataset/db/edan_2025.duckdb', 'cards': 'dataset/retrieval/cards.jsonl'}
    hashes = {}
    for name, path in paths.items():
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(name)
        hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    audit = {'inputs_unchanged': True, 'cross_split_paraphrase_overlap': [],
             'input_sha256': hashes, 'recommended_exclusions': ['q1'],
             'dev_results': [{'id': 'q1', 'gold_rows_match_database': True,
                              'gold_rows_match_independent_csv_formula': True}]}
    path = tmp_path / 'audit.json'
    path.write_text(json.dumps(audit))
    config = {'benchmark': paths['benchmark'], 'comparison': {'excluded_ids': {'q1': 'reason'}}}
    assert verify_preflight(config, root=tmp_path, audit_path=path) == audit
    (tmp_path / paths['benchmark']).write_text('altered gold')
    with pytest.raises(ValueError, match='benchmark changed'):
        verify_preflight(config, root=tmp_path, audit_path=path)
