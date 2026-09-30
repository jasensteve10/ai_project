"""Result comparison, retrieval metrics and outcome/error classification.

Execution results are compared by values, never by SQL text. Columns are matched by
content (names are free), extra predicted columns are allowed, and each policy
states whether row order matters.
"""
import itertools
import math
import re

SUCCESS = {'correct', 'correct_abstention', 'correct_clarification'}


# ---------------------------------------------------------------- values
def _decimals(x):
    text = repr(float(x))
    if 'e' in text or 'inf' in text or 'nan' in text:
        return None
    return len(text.split('.')[1].rstrip('0')) if '.' in text else 0


def values_equal(pred, gold, scale=1):
    """Exact for text/ints; floats within 1e-6 relative, or within the prediction's own rounding
    when that rounding keeps at least ~0.5 % relative precision (e.g. 35.04 for 0.350362 x 100)."""
    if gold is None or pred is None:
        return gold is None and pred is None
    if isinstance(gold, bool) or isinstance(pred, bool):
        return pred == gold
    if isinstance(gold, (int, float)) and isinstance(pred, (int, float)):
        target = gold * scale
        tol = max(1e-6 * abs(target), 1e-9)
        if abs(pred - target) <= tol:
            return True
        d = _decimals(pred) if isinstance(pred, float) else None
        if d is not None and d >= 1 and target:
            round_tol = 0.5 * 10 ** -d + 1e-12
            return round_tol / abs(target) <= 0.005 and abs(pred - target) <= round_tol
        return False
    if isinstance(gold, str) and isinstance(pred, str):
        return pred.strip() == gold.strip()
    return str(pred).strip() == str(gold).strip()


def _column(rows, j):
    return [row[j] for row in rows]


def _multiset_match(pred_rows, gold_rows, eq):
    if len(pred_rows) != len(gold_rows):
        return False
    unused = list(range(len(pred_rows)))
    for g in gold_rows:
        hit = next((i for i in unused if eq(pred_rows[i], g)), None)
        if hit is None:
            return False
        unused.remove(hit)
    return True


def compare_results(pred_columns, pred_rows, gold, policy, pred_limit_hit=False):
    """Return (match: bool, reason: str)."""
    gold_cols, gold_rows = gold['columns'], [list(r) for r in gold['rows']]
    pred_rows = [list(r) for r in pred_rows or []]
    if pred_limit_hit and len(gold_rows) > len(pred_rows):
        return False, 'truncated prediction'
    if not pred_rows:
        return False, 'empty prediction'
    groups = policy.get('required_columns') or [[c] for c in gold_cols]
    scales = (1, 100) if policy.get('percent_ok') else (1,)
    # Candidate (gold column, pred column, scale) per group, filtered by column-level multiset match.
    candidates = []
    for group in groups:
        opts = []
        for gname in group:
            if gname not in gold_cols:
                continue
            gi = gold_cols.index(gname)
            gcol = [[v] for v in _column(gold_rows, gi)]
            for pj in range(len(pred_columns)):
                pcol = [[v] for v in _column(pred_rows, pj)]
                for s in scales:
                    if _multiset_match(pcol, gcol, lambda p, g, s=s: values_equal(p[0], g[0], s)):
                        opts.append((gi, pj, s))
        if not opts:
            return False, f'no predicted column matches {group}'
        candidates.append(opts)
    order_key = policy.get('order_key')
    for combo in itertools.islice(itertools.product(*candidates), 256):
        pjs = [pj for _, pj, _ in combo]
        if len(set(pjs)) != len(pjs):
            continue
        g_proj = [[row[gi] for gi, _, _ in combo] for row in gold_rows]
        p_proj = [[row[pj] for _, pj, _ in combo] for row in pred_rows]

        def row_eq(p, g):
            return all(values_equal(pv, gv, s) for pv, gv, (_, _, s) in zip(p, g, combo))

        if policy['type'] == 'ordered':
            if order_key:
                # Multiset equality plus the gold order of the sort key: tied rows may swap.
                if not _multiset_match(p_proj, g_proj, row_eq):
                    continue
                ki = gold_cols.index(order_key)
                key_opts = [pj for pj in range(len(pred_columns)) for s in scales
                            if all(values_equal(p[pj], g[ki], s) for p, g in zip(pred_rows, gold_rows))]
                if key_opts:
                    return True, 'ordered match'
                continue
            if all(row_eq(p, g) for p, g in zip(p_proj, g_proj)) and len(p_proj) == len(g_proj):
                return True, 'ordered match'
        elif _multiset_match(p_proj, g_proj, row_eq):
            return True, 'match'
    return False, 'rows or order differ'


# ---------------------------------------------------------------- retrieval
def slot_hits(ids, slots):
    present = set(ids)
    return [any(c in present for c in slot) for slot in slots]


def slot_recall(ids, slots):
    return sum(slot_hits(ids, slots)) / len(slots) if slots else None


def complete_set(ids, slots):
    return all(slot_hits(ids, slots)) if slots else None


def ndcg(ranked, slots, k):
    """Binary gain for the first card that satisfies each still-unsatisfied slot."""
    if not slots:
        return None
    satisfied, dcg = set(), 0.0
    for rank, cid in enumerate(ranked[:k], start=1):
        for i, slot in enumerate(slots):
            if i not in satisfied and cid in slot:
                satisfied.add(i)
                dcg += 1 / math.log2(rank + 1)
                break
    ideal = sum(1 / math.log2(r + 1) for r in range(1, min(len(slots), k) + 1))
    return dcg / ideal


def retrieval_metrics(ranked, slots, ks):
    out = {}
    for k in ks:
        out[f'recall@{k}'] = slot_recall(ranked[:k], slots)
        out[f'ndcg@{k}'] = ndcg(ranked, slots, k)
        out[f'complete@{k}'] = complete_set(ranked[:k], slots)
    return out


# ---------------------------------------------------------------- outcomes
_STAGE_OUTCOME = {'transport': 'transport_error', 'validation': 'validation_error', 'execution': 'exec_error',
                  'timeout': 'exec_error', 'generation': 'generation_error'}


def classify(record, result, pred_limit_hit=False):
    """Return (outcome, detail) for one run against a gold record."""
    if result.get('error_type') == 'BudgetExceeded':
        return 'budget_exceeded', 'call cap reached'
    status = result.get('status')
    policy = record['result_comparison_policy']
    kind = record['answerability']
    if status == 'error':
        return _STAGE_OUTCOME.get(result.get('stage'), 'error'), result.get('error') or ''
    if kind == 'answerable':
        if status == 'answerable':
            ok, reason = compare_results(result.get('columns') or [], result.get('rows') or [],
                                         record['gold_result'], policy, pred_limit_hit)
            return ('correct' if ok else 'wrong_result'), reason
        if status == 'needs_clarification' and policy.get('accept_clarification'):
            return 'correct_clarification', 'clarification accepted for this item'
        return 'wrong_abstention', f'abstained ({status}) on an answerable question'
    if status == 'answerable':
        return 'missed_abstention', f'answered a {kind} question'
    if kind == 'unsupported':
        return 'correct_abstention', status
    # ambiguous: only a clarification request is correct
    if status == 'needs_clarification':
        return 'correct_abstention', status
    return 'abstention_type_mismatch', f'{status} instead of needs_clarification'


def _fold(text):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', text.upper()) if not unicodedata.combining(c))


def _entity_used(card_id, sql_literals, cards_by_id):
    """True if the SQL's string literals point at this entity (ID, exact label or an ILIKE fragment)."""
    kind, value = card_id.split(':')[1], card_id.split(':', 2)[2]
    targets = {_fold(value)}
    if kind == 'circ' and cards_by_id and card_id in cards_by_id:
        m = re.search(r"circonscription_name = '(.*)', région", cards_by_id[card_id]['text'])
        if m:
            targets.add(_fold(m.group(1)))
    for lit in sql_literals:
        frag = _fold(lit).strip('%').strip()
        if not frag:
            continue
        if frag == _fold(value) or any(len(frag) >= 3 and frag in t for t in targets):
            return True
    return False


def error_category(record, result, outcome, context_ids, cards_by_id=None):
    """Coarse, rule-based failure category; 'semantics' is the residual to inspect by hand."""
    if outcome in SUCCESS:
        return None
    if outcome in {'transport_error', 'budget_exceeded'}:
        return outcome
    slots = record.get('evidence_slots') or []
    missing = [slot for slot, hit in zip(slots, slot_hits(context_ids or [], slots)) if not hit]
    literals = [a or b for a, b in re.findall(r"'((?:[^']|'')*)'|\"([^\"]*)\"", result.get('final_sql') or '')]
    literals = [x.replace("''", "'") for x in literals]
    unresolved = [cid for cid in record.get('relevant_evidence_ids') or []
                  if not _entity_used(cid, literals, cards_by_id)]
    # A missing card explains the failure only if it is schema/domain context, or an entity the SQL
    # did not resolve anyway (condition A has no entity cards by design and may still use ILIKE).
    if context_ids is not None and any(
            not c.startswith('entity:') or c in unresolved for slot in missing for c in slot):
        return 'retrieval_miss'
    if outcome in {'validation_error', 'exec_error', 'generation_error'}:
        return 'sql_tool'
    if outcome in {'wrong_abstention', 'missed_abstention', 'abstention_type_mismatch'}:
        return 'abstention'
    if unresolved:
        return 'entity_value'
    return 'semantics'
