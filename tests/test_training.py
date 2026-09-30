"""Training data and loop: leakage guard, card-disjoint split, batch construction, one real step."""
import os
import random

import pytest

from src.evaluation.benchmark import load_benchmark
from src.retrieval.corpus import load_cards
from src.training import finetune, pairs as pairgen

CARDS = load_cards()


def test_pairs_cover_every_card_and_have_unambiguous_positives():
    generated = pairgen.generate(CARDS, seed=42)
    assert {c for _, c in generated} == {c['id'] for c in CARDS}
    assert pairgen.generate(CARDS, seed=42) == generated  # deterministic
    for query, cid in generated:
        if cid.startswith('entity:circ:') and not query.startswith('circonscription '):
            assert 'prefecture' not in query.lower() and 'commune' not in query.lower()


def test_leakage_guard_and_card_disjoint_split():
    questions = [r['question'] for r in load_benchmark(include_excluded=True)]
    kept, dropped = pairgen.drop_benchmark_lookalikes(pairgen.generate(CARDS, 42) + [(questions[0], 'x')],
                                                       questions, 0.5)
    assert (questions[0], 'x') in dropped and (questions[0], 'x') not in kept
    train, heldout = pairgen.split_by_card(kept, 0.2, 42)
    assert not {c for _, c in train} & {c for _, c in heldout}
    assert 0.1 < len({c for _, c in heldout}) / len(CARDS) < 0.3


def test_batches_never_repeat_a_positive_card():
    pairs = [(f'q{i}', f'card{i % 5}') for i in range(40)]
    seen = []
    for batch in finetune.batches(pairs, 4, random.Random(0)):
        assert len({c for _, c in batch}) == len(batch) > 1
        seen += batch
    assert len(seen) == len(set(seen)) and set(seen) <= set(pairs)
    assert len(seen) >= len(pairs) - 4  # only negative-free leftovers are dropped
    # a leftover single pair has no in-batch negative and is not yielded
    assert list(finetune.batches([('q', 'a'), ('r', 'a')], 4, random.Random(0))) == []


def test_one_training_epoch_runs_and_records_history():
    st = pytest.importorskip('sentence_transformers')
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    try:
        model = st.SentenceTransformer('intfloat/multilingual-e5-small', local_files_only=True, device='cpu',
                                       revision='614241f622f53c4eeff9890bdc4f31cfecc418b3')
    except Exception as exc:
        pytest.skip(f'E5 not available offline: {exc}')
    cards = [c for c in CARDS if c['kind'] == 'domain']
    pairs = [(q, cid) for cid, qs in pairgen.FIXED_QUERIES.items() if cid.startswith('domain:') for q in qs]
    cfg = {'device': 'cpu', 'max_seq_length': 64, 'seed': 0, 'batch_size': 8, 'epochs': 1, 'warmup_ratio': 0.1,
           'learning_rate': 1e-5, 'weight_decay': 0.01, 'temperature': 0.05, 'max_grad_norm': 1.0}
    history = finetune.train(model, pairs, pairs[:6], cards, cfg, log=lambda *_: None)
    assert [e['epoch'] for e in history['epochs']] == [0, 1]
    assert history['total_steps'] == len(history['steps']) > 0
    assert all(s['loss'] == s['loss'] and s['loss'] > 0 for s in history['steps'])  # finite, positive
    assert 0 <= history['epochs'][1]['recall@1'] <= 1
