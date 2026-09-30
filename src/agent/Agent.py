"""Bounded, read-only Text-to-SQL generation, validation and repair."""
import hashlib
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from dotenv import load_dotenv
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from src.agent.llm import is_transient, llm_descriptor, make_llm
from src.preprocessing.schema import DB_PATH, DESCRIPTIONS, read_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = PROJECT_ROOT / 'src/prompts'
load_dotenv(PROJECT_ROOT / '.env')
ALLOWED_RELATIONS = set(DESCRIPTIONS)
DEFAULT_LIMIT, MAX_LIMIT, MAX_REPAIR_ATTEMPTS = 100, 500, 3
SQL_POLICY_VERSION = '3'
ALLOWED_FUNCTIONS = {
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'ROUND', 'ABS', 'COALESCE', 'NULLIF',
    'LOWER', 'UPPER', 'TRIM', 'LENGTH', 'CAST', 'TRY_CAST', 'CASE', 'IF',
    'ROW_NUMBER', 'RANK', 'DENSE_RANK', 'GREATEST', 'LEAST', 'COUNT_IF',
}


def validate_sql(sql: str) -> tuple[bool, str, str]:
    """Parse, check scopes/capabilities, qualify views and apply an AST row cap."""
    try:
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError('Requête vide.')
        statements = [s for s in sqlglot.parse(sql, read='duckdb') if s is not None]
        if len(statements) != 1:
            raise ValueError('Une seule instruction est autorisée.')
        parsed = statements[0]
        if not isinstance(parsed, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise ValueError('Seules les requêtes SELECT sont autorisées.')
        if any(isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)) for node in parsed.walk()):
            raise ValueError('Instruction non autorisée dans la requête.')
        for cte in parsed.find_all(exp.CTE):
            if not isinstance(cte.this, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
                raise ValueError('CTE non SELECT interdite.')
        if any(w.args.get('recursive') for w in parsed.find_all(exp.With)):
            raise ValueError('CTE récursive interdite.')
        for func in parsed.find_all(exp.Func):
            # sqlglot models AND/OR and EXISTS as Func subclasses; they are operators, not functions.
            if isinstance(func, (exp.And, exp.Or, exp.Exists)):
                continue
            if isinstance(func, exp.Anonymous) or func.sql_name() not in ALLOWED_FUNCTIONS:
                raise ValueError(f'Fonction non autorisée: {func.sql_name()}')
        # Scope resolution distinguishes CTE aliases from physical relations,
        # including nested/shadowed CTEs; schema-qualified tables stay physical.
        for scope in traverse_scope(parsed):
            for _, source in scope.selected_sources.values():
                if not isinstance(source, exp.Table):
                    continue
                if not isinstance(source.this, exp.Identifier) or source.catalog:
                    raise ValueError('Source externe ou fonction de table interdite.')
                schema, name = source.db.lower(), source.name.lower()
                relation = f'{schema or "mart"}.{name}'
                if relation not in ALLOWED_RELATIONS:
                    raise ValueError(f'Relation non autorisée: {relation}')
                source.set('db', exp.to_identifier('mart'))
                source.set('this', exp.to_identifier(name))
        for node in parsed.walk():
            node.comments = None
        limit = parsed.args.get('limit')
        value = limit.expression if limit else None
        if value is None:
            cap = DEFAULT_LIMIT
        elif isinstance(value, exp.Literal) and value.is_int:
            cap = min(max(int(value.this), 0), MAX_LIMIT)
        else:
            cap = MAX_LIMIT
        # Replacing the entire node also removes PERCENT/WITH TIES options.
        parsed.set('limit', exp.Limit(expression=exp.Literal.number(cap)))
        return True, parsed.sql(dialect='duckdb'), ''
    except Exception as exc:
        return False, sql, str(exc)


def _execute_worker(pipe, db_path, sql):
    """Runs in a disposable process so even expensive joins have a deadline."""
    try:
        with duckdb.connect(str(db_path), read_only=True, config={
            'enable_external_access': 'false', 'threads': '2',
            'memory_limit': '256MB', 'max_temp_directory_size': '0B',
            'autoload_known_extensions': 'false', 'autoinstall_known_extensions': 'false',
        }) as con:
            cur = con.execute(sql)
            pipe.send({'ok': True, 'sql': sql, 'columns': [d[0] for d in cur.description],
                       'rows': cur.fetchall()})
    except Exception as exc:
        pipe.send({'ok': False, 'stage': 'execution', 'sql': sql, 'error': str(exc)})
    finally:
        pipe.close()


def run_safe_sql(sql: str, db_path=DB_PATH, timeout_seconds=5.0) -> dict[str, Any]:
    ok, fixed, error = validate_sql(sql)
    if not ok:
        return {'ok': False, 'stage': 'validation', 'error': error, 'sql': sql}
    if timeout_seconds <= 0:
        raise ValueError('SQL timeout must be positive')
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_execute_worker, args=(child, str(db_path), fixed), daemon=True)
    try:
        process.start()
        child.close()
        if parent.poll(timeout_seconds):
            try:
                return parent.recv()
            except EOFError:
                return {'ok': False, 'stage': 'execution', 'sql': fixed, 'error': 'SQL worker exited unexpectedly'}
        return {'ok': False, 'stage': 'timeout', 'sql': fixed, 'error': 'SQL execution deadline exceeded'}
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
        child.close()
        parent.close()


def runtime_fingerprint(db_path=DB_PATH, model=None):
    """Versions data, live view definitions, prompts, model and query policy."""
    digest = hashlib.sha256()
    for path in [Path(db_path), Path(__file__), PROJECT_ROOT / 'src/preprocessing/schema.py',
                 PROMPT_DIR / 'sql_system.md', PROMPT_DIR / 'sql_repair.md', PROMPT_DIR / 'examples.json',
                 PROMPT_DIR / 'agent_retrieval.md', PROJECT_ROOT / 'requirements.lock']:
        digest.update(path.read_bytes())
    digest.update(json.dumps({'model': model or llm_descriptor(), 'temperature': 0,
                              'policy': SQL_POLICY_VERSION}, sort_keys=True).encode())
    return digest.hexdigest()


def _parse_response(content, allow_context=False):
    # Gemini 3 / current LangChain can return typed blocks, even for JSON output.
    # Only public answer text participates in parsing; reasoning/signatures do not.
    if isinstance(content, list):
        content = ''.join(block if isinstance(block, str) else block.get('text', '')
                          for block in content
                          if isinstance(block, str) or (isinstance(block, dict)
                              and block.get('type') == 'text' and not block.get('thought')))
    if not isinstance(content, str):
        raise ValueError('Model response must contain text')
    # Recognize the previous prompt's abstention too, without executing it.
    if content.strip() == 'Not found in the provided PDF dataset.':
        return {'status': 'unsupported', 'response': content.strip(), 'sql': None}
    payload = json.loads(content)
    statuses = {'answerable', 'unsupported', 'needs_clarification'} | ({'needs_context'} if allow_context else set())
    if not isinstance(payload, dict) or payload.get('status') not in statuses:
        raise ValueError('Invalid response status')
    if payload['status'] == 'needs_context':
        if not isinstance(payload.get('search'), str) or not payload['search'].strip():
            raise ValueError('needs_context requires a search query')
    elif payload['status'] == 'answerable':
        if not isinstance(payload.get('sql'), str) or not payload['sql'].strip():
            raise ValueError('Answerable response requires SQL')
    elif payload.get('sql') is not None or not isinstance(payload.get('response'), str) or not payload['response'].strip():
        raise ValueError('Non-SQL response requires an explanation and sql=null')
    return payload


_transient = is_transient


class ElectionSQLAgent:
    def __init__(self, *, llm=None, model=None, db_path=DB_PATH, sleep=time.sleep):
        self.db_path = Path(db_path)
        self.model = model or os.getenv('GEMINI_MODEL')
        if llm is None:
            llm = make_llm(self.model)
        self.llm, self.sleep = llm, sleep
        with duckdb.connect(str(self.db_path), read_only=True) as con:
            catalog = read_catalog(con)
        self._catalog = catalog
        self._base_prompt = (PROMPT_DIR / 'sql_system.md').read_text()
        self._examples = '\n\nExemples:\n' + (PROMPT_DIR / 'examples.json').read_text()
        self._system_prompt = self.build_system_prompt()
        self._agent_addendum = (PROMPT_DIR / 'agent_retrieval.md').read_text()
        self._repair_tpl = (PROMPT_DIR / 'sql_repair.md').read_text()

    def build_system_prompt(self, context=None, agent=False):
        """Default: live catalog. Experiments replace it with a rendered card context."""
        if context is None:
            block = '\n\nCatalogue réel:\n' + json.dumps(self._catalog, ensure_ascii=False)
        else:
            block = '\n\nContexte fourni:\n' + context
        prompt = self._base_prompt + block + self._examples
        return prompt + '\n\n' + self._agent_addendum if agent else prompt

    def _invoke(self, prompt, trace, system=None):
        from langchain_core.messages import HumanMessage, SystemMessage
        system = system or self._system_prompt
        for retry in range(3):
            start = time.monotonic()
            try:
                response = self.llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
                metadata = getattr(response, 'response_metadata', None) or {}
                trace.append({'stage': 'transport', 'ok': True, 'retry': retry,
                              'seconds': time.monotonic() - start,
                              'usage': getattr(response, 'usage_metadata', None),
                              'provider': metadata.get('provider'), 'served_model': metadata.get('model_name'),
                              'temperature': metadata.get('temperature', 0),
                              **({'fallback_reason': metadata['fallback_reason']}
                                 if metadata.get('fallback_reason') else {})})
                return response.content
            except Exception as exc:
                trace.append({'stage': 'transport', 'ok': False, 'retry': retry,
                              'seconds': time.monotonic() - start, 'error_type': type(exc).__name__,
                              'attempted': type(exc).__name__ != 'BudgetExceeded'})
                if retry == 2 or not _transient(exc):
                    raise
                self.sleep(2 ** retry)

    def run_query(self, question: str, *, context=None, retrieve=None, max_retrievals=0) -> dict[str, Any]:
        """Generate, validate and repair SQL.

        ``context`` replaces the catalog block. With ``retrieve`` and ``max_retrievals`` > 0 the model
        may answer ``needs_context``; ``retrieve(search)`` returns (card_ids, rendered_text) for cards
        not yet in context. Retrieval calls and SQL repairs have separate budgets.
        """
        agent = retrieve is not None and max_retrievals > 0
        system = self.build_system_prompt(context, agent=agent) if (context is not None or agent) else None
        trace, initial, current, attempts, repairs, retrievals = [], None, None, 0, 0, 0
        added_ids, retrieval_open = [], agent
        prompt = question
        result = None
        while repairs <= MAX_REPAIR_ATTEMPTS:
            attempts += 1
            try:
                content = self._invoke(prompt, trace, system)
            except Exception as exc:
                return self._result(False, attempts, initial, trace, status='error', stage='transport',
                                    error=str(exc), error_type=type(exc).__name__,
                                    retrieval_calls=retrievals, added_context_ids=added_ids)
            try:
                payload = _parse_response(content, allow_context=retrieval_open)
                trace.append({'stage': 'repair' if repairs else 'generation', 'ok': True})
            except (ValueError, TypeError) as exc:
                result = {'ok': False, 'stage': 'generation', 'error': str(exc)}
                current = '[Invalid JSON response]' if isinstance(content, list) else content
            else:
                if payload['status'] == 'needs_context':
                    retrievals += 1
                    start = time.monotonic()
                    try:
                        ids, text = retrieve(payload['search'])
                    except Exception as exc:
                        trace.append({'stage': 'retrieval', 'ok': False, 'error_type': type(exc).__name__,
                                      'seconds': time.monotonic() - start})
                        return self._result(False, attempts, initial, trace, status='error', stage='retrieval',
                                            error=str(exc), retrieval_calls=retrievals, added_context_ids=added_ids)
                    trace.append({'stage': 'retrieval', 'ok': bool(ids), 'search': payload['search'],
                                  'card_ids': ids, 'seconds': time.monotonic() - start})
                    if ids:
                        added_ids += ids
                        context = (context or '') + '\n\n' + text
                    # Stop rule: budget spent or nothing new found -> the model must now decide.
                    retrieval_open = bool(ids) and retrievals < max_retrievals
                    system = self.build_system_prompt(context, agent=retrieval_open)
                    prompt = question if retrieval_open else (
                        question + '\n\n(Plus de recherche possible: réponds avec answerable, '
                        'unsupported ou needs_clarification.)')
                    continue
                if payload['status'] != 'answerable':
                    return self._result(False, attempts, initial, trace, status=payload['status'],
                                        response=payload['response'], error=None,
                                        retrieval_calls=retrievals, added_context_ids=added_ids)
                current = payload['sql']
                if initial is None:
                    initial = current
                result = run_safe_sql(current, db_path=self.db_path)
                if result['ok']:
                    trace.append({'stage': 'execution', 'ok': True})
                    return self._result(True, attempts, initial, trace, status='answerable',
                                        final_sql=result['sql'], columns=result['columns'], rows=result['rows'],
                                        retrieval_calls=retrievals, added_context_ids=added_ids)
            trace.append({'stage': result['stage'], 'ok': False, 'error': result['error']})
            if result['stage'] == 'timeout':
                break
            repairs += 1
            prompt = self._repair_tpl.format(question=question, bad_sql=current,
                                             error=result['error'], relations=', '.join(sorted(ALLOWED_RELATIONS)))
        return self._result(False, attempts, initial, trace, status='error', stage=result['stage'], error=result['error'],
                            retrieval_calls=retrievals, added_context_ids=added_ids)

    def _result(self, ok, attempts, initial, trace, **kwargs):
        answered = [t for t in trace if t['stage'] == 'transport' and t['ok']]
        usage = [t.get('usage') or {} for t in answered]
        served = sorted({t['served_model'] for t in answered if t.get('served_model')})
        claude = any(t.get('provider') == 'anthropic' for t in answered)
        temps = {t.get('temperature', 0) for t in answered} or {0}
        return {'ok': ok, 'attempts': attempts,
                'api_calls': sum(t['stage'] == 'transport' and t.get('attempted', True) for t in trace),
                'proposed_sql': initial, 'final_sql': None, 'model': self.model,
                'served_by': served, 'fallback_used': claude,
                # Gemini and Haiku run at 0; Claude Opus 5 / Fable reject sampling params (None).
                'temperature': temps.pop() if len(temps) == 1 else None, 'trace': trace,
                'input_tokens': sum(u.get('input_tokens', 0) or 0 for u in usage),
                'output_tokens': sum(u.get('output_tokens', 0) or 0 for u in usage), **kwargs}
