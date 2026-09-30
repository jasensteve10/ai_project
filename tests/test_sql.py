import json
import pytest
import sqlglot
from src.agent.Agent import validate_sql, run_safe_sql, PROMPT_DIR


@pytest.mark.parametrize('sql', [
    "SELECT 'drop;update' AS text",
    'SELECT * FROM vw_vainqueur',
    'SELECT * FROM mart.vw_vainqueur -- trailing comment',
    'WITH winners AS (SELECT * FROM mart.vw_vainqueur) SELECT * FROM winners',
    'SELECT candidat FROM mart.vw_vainqueur UNION ALL SELECT candidat FROM mart.vw_vainqueur',
    'SELECT * FROM mart.vw_vainqueur LIMIT 100000',
    'SELECT * FROM mart.vw_vainqueur LIMIT (SELECT 100000)',
    'SELECT * FROM mart.vw_vainqueur LIMIT 100 PERCENT',
    'SELECT * FROM mart.vw_vainqueur LIMIT ALL',
    'SELECT candidat FROM mart.vw_vainqueur WHERE score > 1 AND parti = \'RHDP\'',
    'SELECT parti FROM mart.vw_vainqueur GROUP BY parti HAVING COUNT(*) > 1 OR SUM(score) > 5',
    'SELECT a.candidat FROM mart.vw_resultats_candidats a JOIN mart.vw_resultats_candidats b '
    'ON a.circonscription_id = b.circonscription_id AND a.score > b.score',
    'SELECT circonscription_id FROM mart.vw_circonscriptions c WHERE EXISTS '
    '(SELECT 1 FROM mart.vw_vainqueur v WHERE v.circonscription_id = c.circonscription_id)',
])
def test_positive_queries_and_finite_limits(sql, database):
    ok, fixed, error = validate_sql(sql)
    assert ok, error
    assert int(sqlglot.parse_one(fixed, read='duckdb').args['limit'].expression.this) <= 500
    result = run_safe_sql(sql, database)
    assert result['ok'], result
    assert len(result['rows']) <= 500


@pytest.mark.parametrize('sql', [
    'SELECT 1; SELECT 2', 'DELETE FROM mart.vw_vainqueur',
    "COPY (SELECT * FROM mart.vw_vainqueur) TO '/tmp/result.csv'",
    'CREATE TABLE x AS SELECT 1', 'EXPLAIN SELECT * FROM mart.vw_vainqueur',
    'SELECT * FROM brute.resultats', 'SELECT * FROM information_schema.tables',
    'SELECT * FROM other.mart.vw_vainqueur',
    "SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM '/etc/passwd'",
    "SELECT getenv('HOME')", 'SELECT * FROM range(1000000000)',
    'SELECT * FROM query(\'SELECT 1\')',
    'WITH x AS (SELECT * FROM brute.resultats) SELECT * FROM x',
    'WITH vw_vainqueur AS (SELECT * FROM brute.resultats) SELECT * FROM vw_vainqueur',
    'WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT * FROM x) SELECT * FROM x',
    'SELECT * INTO x FROM mart.vw_vainqueur',
    'SELECT 1 FROM mart.vw_vainqueur v WHERE EXISTS (SELECT 1 FROM brute.resultats)',
    "SELECT 1 FROM mart.vw_vainqueur WHERE elu AND getenv('HOME') = ''",
])
def test_forbidden_queries(sql):
    assert not validate_sql(sql)[0]


def test_all_prompt_examples_execute(database):
    for example in json.loads((PROMPT_DIR / 'examples.json').read_text()):
        result = run_safe_sql(example['sql'], database)
        assert result['ok'], (example, result)
        assert result['rows']


def test_region_turnout_uses_weighted_totals(database):
    result = run_safe_sql("SELECT SUM(votants)/SUM(inscrits) FROM mart.vw_circonscriptions", database)
    assert result['rows'][0][0] == pytest.approx(3012094 / 8597092)


def test_expensive_join_is_terminated(database):
    sql = ('SELECT SUM(a.score * b.score * c.score * d.score) FROM '
           'mart.vw_resultats_candidats a CROSS JOIN mart.vw_resultats_candidats b '
           'CROSS JOIN mart.vw_resultats_candidats c CROSS JOIN mart.vw_resultats_candidats d')
    result = run_safe_sql(sql, database, timeout_seconds=0.5)
    assert result['stage'] == 'timeout'


def test_duckdb_external_access_disabled_even_without_validator(database, tmp_path):
    from src.agent.Agent import _execute_worker
    path = tmp_path / 'private.csv'
    path.write_text('secret\n42\n')
    class Capture:
        def send(self, result):
            self.result = result
        def close(self):
            pass
    pipe = Capture()
    _execute_worker(pipe, database, f"SELECT * FROM read_csv('{path}')")
    assert not pipe.result['ok']
    assert 'disabled' in pipe.result['error'].lower()
