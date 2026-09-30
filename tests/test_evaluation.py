import copy
import json
from types import SimpleNamespace

import pytest

from src.agent.Agent import ElectionSQLAgent, run_safe_sql
from src.evaluation import benchmark as bm
from src.evaluation import runner
from src.evaluation.report import cluster_bootstrap, paired_difference
from src.evaluation.scoring import (classify, compare_results, complete_set, error_category, ndcg, slot_recall,
                                    values_equal)
from src.retrieval.corpus import build_cards

RECORDS = bm.load_benchmark(include_excluded=True)


# ---------------------------------------------------------------- comparison policies
GOLD = {'columns': ['circonscription_id', 'n'], 'rows': [['001', 3], ['002', 2], ['003', 2]]}


def pol(**kw):
    return {'type': 'unordered', 'required_columns': None, 'order_key': None, 'percent_ok': False,
            'accept_clarification': False, **kw}


def test_unordered_ignores_row_order_and_column_names():
    ok, _ = compare_results(['x', 'y'], [['003', 2], ['001', 3], ['002', 2]], GOLD, pol())
    assert ok


def test_extra_columns_allowed_but_missing_rows_rejected():
    assert compare_results(['name', 'id', 'n'], [['A', '001', 3], ['B', '002', 2], ['C', '003', 2]], GOLD, pol())[0]
    assert not compare_results(['id', 'n'], [['001', 3], ['002', 2]], GOLD, pol())[0]


def test_ordered_with_order_key_allows_ties_to_swap():
    p = pol(type='ordered', order_key='n')
    assert compare_results(['id', 'n'], [['001', 3], ['003', 2], ['002', 2]], GOLD, p)[0]
    assert not compare_results(['id', 'n'], [['002', 2], ['001', 3], ['003', 2]], GOLD, p)[0]


def test_ordered_without_key_is_strict():
    gold = {'columns': ['r'], 'rows': [['a'], ['b']]}
    assert compare_results(['r'], [['a'], ['b']], gold, pol(type='ordered'))[0]
    assert not compare_results(['r'], [['b'], ['a']], gold, pol(type='ordered'))[0]


def test_required_column_groups_accept_id_or_name():
    gold = {'columns': ['circonscription_id', 'circonscription_name', 'v'], 'rows': [['001', 'X', 1]]}
    p = pol(required_columns=[['circonscription_id', 'circonscription_name'], ['v']])
    assert compare_results(['name', 'v'], [['X', 1]], gold, p)[0]
    assert not compare_results(['v'], [[1]], gold, p)[0]


def test_numeric_tolerance_percent_scaling_and_nulls():
    assert values_equal(0.3504, 0.35036196)
    assert values_equal(35.04, 0.35036196, 100)
    assert not values_equal(0.35, 0.35036196)          # rounding too coarse
    assert not values_equal(35.04, 0.35036196)         # percent without percent_ok scale
    assert values_equal(None, None) and not values_equal(0, None)
    gold = {'columns': ['p'], 'rows': [[0.35036196]]}
    assert compare_results(['p'], [[35.04]], gold, pol(type='scalar', percent_ok=True))[0]
    assert not compare_results(['p'], [[35.04]], gold, pol(type='scalar'))[0]


def test_truncated_prediction_is_not_correct():
    assert not compare_results(['id', 'n'], [['001', 3]], GOLD, pol(), pred_limit_hit=True)[0]


# ---------------------------------------------------------------- retrieval metrics
SLOTS = [['s1', 's1b'], ['d1'], ['e1']]


def test_slot_recall_complete_and_ndcg_hand_computed():
    assert slot_recall(['s1b', 'x', 'd1'], SLOTS) == pytest.approx(2 / 3)
    assert not complete_set(['s1', 'd1'], SLOTS)
    assert complete_set(['e1', 's1', 'd1'], SLOTS)
    # gains at ranks 1 and 3 (s1 and s1b satisfy the same slot: only the first counts)
    ranked = ['s1', 's1b', 'd1', 'x']
    ideal = 1 + 1 / 1.584962500721156 + 1 / 2
    assert ndcg(ranked, SLOTS, 4) == pytest.approx((1 + 1 / 2) / ideal)
    assert ndcg(['e1', 's1', 'd1'], SLOTS, 3) == pytest.approx(1.0)


# ---------------------------------------------------------------- outcomes
def rec(answerability='answerable', **policy):
    return {'answerability': answerability, 'result_comparison_policy': pol(type='scalar', **policy),
            'gold_result': {'columns': ['n'], 'rows': [[5]]}, 'evidence_slots': [], 'relevant_evidence_ids': []}


def test_outcome_classification():
    ok = {'status': 'answerable', 'ok': True, 'columns': ['n'], 'rows': [[5]]}
    assert classify(rec(), ok)[0] == 'correct'
    assert classify(rec(), {**ok, 'rows': [[6]]})[0] == 'wrong_result'
    assert classify(rec(), {'status': 'unsupported'})[0] == 'wrong_abstention'
    assert classify(rec(accept_clarification=True), {'status': 'needs_clarification'})[0] == 'correct_clarification'
    assert classify(rec('unsupported'), {'status': 'needs_clarification'})[0] == 'correct_abstention'
    assert classify(rec('unsupported'), ok)[0] == 'missed_abstention'
    assert classify(rec('ambiguous'), {'status': 'unsupported'})[0] == 'abstention_type_mismatch'
    assert classify(rec(), {'status': 'error', 'stage': 'execution'})[0] == 'exec_error'
    assert classify(rec(), {'status': 'error', 'stage': 'transport', 'error_type': 'BudgetExceeded'})[0] == \
        'budget_exceeded'


def test_error_category_distinguishes_retrieval_entity_and_semantics(database):
    cards = {c['id']: c for c in build_cards(database)}
    r = {'evidence_slots': [['schema:vw_circonscriptions'], ['entity:region:HAUT- SASSANDRA']],
         'relevant_evidence_ids': ['entity:region:HAUT- SASSANDRA']}
    wrong = {'final_sql': "SELECT 1 FROM mart.vw_circonscriptions WHERE region = 'HAUT-SASSANDRA'"}
    right = {'final_sql': "SELECT 1 FROM mart.vw_circonscriptions WHERE region = 'HAUT- SASSANDRA'"}
    assert error_category(r, wrong, 'wrong_result', ['entity:region:HAUT- SASSANDRA'], cards) == 'retrieval_miss'
    full = ['schema:vw_circonscriptions', 'entity:region:HAUT- SASSANDRA']
    assert error_category(r, wrong, 'wrong_result', full, cards) == 'entity_value'
    assert error_category(r, right, 'wrong_result', full, cards) == 'semantics'
    # Condition A lacks entity cards by design: a correctly resolved entity is not a retrieval miss.
    assert error_category(r, right, 'wrong_result', ['schema:vw_circonscriptions'], cards) == 'semantics'
    circ = {'evidence_slots': [['entity:circ:060']], 'relevant_evidence_ids': ['entity:circ:060']}
    ilike = {'final_sql': "SELECT 1 FROM mart.vw_vainqueur WHERE circonscription_name ILIKE '%BOUAKE%'"}
    assert error_category(circ, ilike, 'wrong_result', [], cards) == 'semantics'


# ---------------------------------------------------------------- benchmark
def test_benchmark_gold_is_current_and_valid(database):
    fresh = bm.compute_gold(copy.deepcopy(RECORDS), database)
    for old, new in zip(RECORDS, fresh):
        if old['gold_result']:
            # Policy-aware: tied rows under an order_key may legitimately come back reordered.
            g = new['gold_result']
            assert compare_results(g['columns'], g['rows'], old['gold_result'],
                                   old['result_comparison_policy'])[0], old['id']
        assert old['acceptable_alternative_evidence_sets'] == new['acceptable_alternative_evidence_sets']
    errors, _ = bm.validate(fresh, database, build_cards(database))
    assert errors == []


def test_benchmark_shape():
    counts = {s: sum(r['split'] == s for r in RECORDS) for s in ('dev', 'test')}
    assert counts == {'dev': 20, 'test': 60}
    assert len({r['intent_family'] for r in RECORDS}) == 10


def test_validator_catches_leakage_split_overlap_and_unknown_cards(database):
    cards = build_cards(database)
    bad = copy.deepcopy(RECORDS[:2])
    bad[1]['paraphrase_family'] = bad[0]['paraphrase_family']
    bad[1]['split'] = 'test'
    bad[0]['question'] = 'Top 5 des régions par participation'
    bad[0]['evidence_slots'] = [['schema:nope']]
    errors, _ = bm.validate(bad, database, cards)
    text = '\n'.join(errors)
    assert 'spans splits' in text and 'leakage' in text and 'unknown card id schema:nope' in text


def test_freeze_requires_reviewed_test_items(tmp_path):
    path = tmp_path / 'b.jsonl'
    bm.write_benchmark(RECORDS, path)
    with pytest.raises(SystemExit):
        bm.freeze(path)
    reviewed = [{**r, 'reviewer_status': 'reviewed'} for r in RECORDS]
    bm.write_benchmark(reviewed, path)
    assert bm.freeze_status(path) == 'unfrozen'
    bm.freeze(path)
    assert bm.freeze_status(path) == 'frozen'
    path.write_text(path.read_text() + '\n')
    assert bm.freeze_status(path) == 'modified'


# ---------------------------------------------------------------- bounded agent loop
class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.systems = list(payloads), []

    def invoke(self, messages):
        self.systems.append(messages[0].content)
        return SimpleNamespace(content=json.dumps(self.payloads.pop(0)),
                               usage_metadata={'input_tokens': 100, 'output_tokens': 10})


def need(search):
    return {'status': 'needs_context', 'search': search, 'sql': None, 'response': None}


ANSWER = {'status': 'answerable', 'sql': 'SELECT COUNT(*) FROM mart.vw_vainqueur', 'response': None}


def test_agent_retrieves_then_answers_and_counts_budgets(database):
    llm = Scripted(need('région Poro'), ANSWER)
    calls = []

    def retrieve(q):
        calls.append(q)
        return ['entity:region:PORO'], "[entity:region:PORO] Région PORO"

    result = ElectionSQLAgent(llm=llm, db_path=database).run_query('q', context='ctx', retrieve=retrieve,
                                                                    max_retrievals=3)
    assert result['ok'] and result['retrieval_calls'] == 1 and result['api_calls'] == 2
    assert result['added_context_ids'] == ['entity:region:PORO']
    assert 'Région PORO' in llm.systems[1] and 'needs_context' in llm.systems[1]
    assert result['input_tokens'] == 200 and result['output_tokens'] == 20


def test_agent_retrieval_cap_and_no_new_cards_stop_rule(database):
    llm = Scripted(need('a'), need('b'), ANSWER)
    result = ElectionSQLAgent(llm=llm, db_path=database).run_query(
        'q', context='ctx', retrieve=lambda q: (['x'], 'X'), max_retrievals=1)
    # After the single allowed search, needs_context is invalid and handled as a generation error.
    assert result['retrieval_calls'] == 1 and result['ok']
    assert 'needs_context' not in llm.systems[1]
    llm = Scripted(need('a'), ANSWER)
    result = ElectionSQLAgent(llm=llm, db_path=database).run_query(
        'q', context='ctx', retrieve=lambda q: ([], ''), max_retrievals=3)
    assert result['retrieval_calls'] == 1 and result['ok'] and 'needs_context' not in llm.systems[1]


def test_default_prompt_unchanged_without_context(database):
    agent = ElectionSQLAgent(llm=Scripted(), db_path=database)
    assert 'Catalogue réel' in agent._system_prompt and 'needs_context' not in agent._system_prompt
    assert agent.build_system_prompt('CTX').count('Contexte fourni:\nCTX') == 1


# ---------------------------------------------------------------- runner
def test_fake_run_writes_complete_traces_and_resumes(tmp_path, monkeypatch, database):
    monkeypatch.setattr(runner, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(runner, 'DB_PATH', database)
    ids = 'dev-002,dev-013,dev-017'
    runner.main(['--mode', 'fake', '--conditions', 'A,ORACLE', '--ids', ids, '--run-id', 'r1'])
    rows = [json.loads(line) for line in (tmp_path / 'r1/traces.jsonl').read_text().splitlines()]
    assert len(rows) == 6 and all(r['success'] for r in rows)
    for key in ('context_ids', 'seconds', 'api_calls', 'input_tokens', 'trace', 'outcome', 'error_category'):
        assert key in rows[0]
    manifest = json.loads((tmp_path / 'r1/manifest.json').read_text())
    assert manifest['answer_cache'] == 'disabled' and manifest['corpus']['sha256']
    runner.main(['--mode', 'fake', '--conditions', 'A,ORACLE', '--ids', ids, '--run-id', 'r1'])
    assert len((tmp_path / 'r1/traces.jsonl').read_text().splitlines()) == 6


def test_call_cap_stops_and_resume_completes(tmp_path, monkeypatch, database):
    monkeypatch.setattr(runner, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(runner, 'DB_PATH', database)
    args = ['--mode', 'fake', '--conditions', 'A', '--ids', 'dev-002,dev-003,dev-004', '--run-id', 'cap']
    runner.main(args + ['--max-calls', '1'])
    latest = runner.read_traces(tmp_path / 'cap/traces.jsonl')
    assert [r['outcome'] for r in latest.values()] == ['correct', 'budget_exceeded']
    runner.main(args)
    latest = runner.read_traces(tmp_path / 'cap/traces.jsonl')
    assert len(latest) == 3 and all(r['outcome'] == 'correct' for r in latest.values())


def test_live_mode_requires_call_cap():
    with pytest.raises(SystemExit):
        runner.main(['--mode', 'live', '--split', 'dev'])


def test_metered_llm_paces_and_caps():
    waits = []
    m = runner.MeteredLLM(Scripted(ANSWER, ANSWER), max_calls=2, pace=10, sleep=waits.append)
    m.invoke([SimpleNamespace(content='s')])
    m.invoke([SimpleNamespace(content='s')])
    assert len(waits) == 1 and 9 < waits[0] <= 10
    with pytest.raises(runner.BudgetExceeded):
        m.invoke([SimpleNamespace(content='s')])


# ---------------------------------------------------------------- statistics
def test_cluster_bootstrap_and_paired_difference():
    fams = {'q1': 'f1', 'q2': 'f1', 'q3': 'f2'}
    point, lo, hi = cluster_bootstrap({'q1': 1, 'q2': 1, 'q3': 0}, fams, iters=2000)
    assert point == pytest.approx(2 / 3) and 0 <= lo <= point <= hi <= 1
    rows = [{'condition': c, 'question_id': q, 'success': s}
            for c, q, s in [('A', 'q1', 0), ('A', 'q2', 1), ('A', 'q3', 0),
                            ('B', 'q1', 1), ('B', 'q2', 1), ('B', 'q3', 1)]]
    d = paired_difference(rows, 'B', 'A', fams)
    assert d['n'] == 3 and d['diff'] == pytest.approx(2 / 3) and d['ci_low'] > 0


def test_gold_sql_runs_through_production_validator(database):
    for r in RECORDS:
        if r.get('gold_sql'):
            assert run_safe_sql(r['gold_sql'], db_path=database)['ok'], r['id']
