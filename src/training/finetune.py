"""Contrastive fine-tuning of multilingual E5 on synthetic (query, card) pairs.

Loss: in-batch-negatives cross-entropy (InfoNCE) over cosine similarities / temperature.
Batches never contain two queries for the same card, so no negative is a false one.
"""
import hashlib
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from src.retrieval.dense import passage_text


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def pick_device(name):
    if name != 'auto':
        return name
    return 'mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu'


def batches(pairs, size, rng):
    """Shuffled batches with unique positive cards; a final single-pair batch (no negatives) is dropped."""
    pool = pairs[:]
    rng.shuffle(pool)
    while pool:
        batch, seen, rest = [], set(), []
        for query, cid in pool:
            if len(batch) < size and cid not in seen:
                batch.append((query, cid))
                seen.add(cid)
            else:
                rest.append((query, cid))
        if len(batch) > 1:
            yield batch
        pool = rest


def _encode(model, texts, device):
    encode = getattr(model, 'preprocess', None) or model.tokenize
    features = {k: v.to(device) if torch.is_tensor(v) else v for k, v in encode(texts).items()}
    return F.normalize(model(features)['sentence_embedding'], dim=-1)


@torch.no_grad()
def evaluate(model, pairs, cards, device, batch_size=64):
    """Rank all cards for each held-out query: Recall@1, Recall@5, MRR."""
    model.eval()
    ids = [c['id'] for c in cards]
    index = {cid: i for i, cid in enumerate(ids)}
    passages = torch.cat([_encode(model, [passage_text(c) for c in cards[i:i + batch_size]], device)
                          for i in range(0, len(cards), batch_size)])
    ranks = []
    for i in range(0, len(pairs), batch_size):
        chunk = pairs[i:i + batch_size]
        queries = _encode(model, [f'query: {q}' for q, _ in chunk], device)
        scores = queries @ passages.T
        for row, (_, cid) in zip(scores, chunk):
            ranks.append(int((row > row[index[cid]]).sum().item()) + 1)
    ranks = np.array(ranks)
    return {'n': len(ranks), 'recall@1': float((ranks <= 1).mean()), 'recall@5': float((ranks <= 5).mean()),
            'mrr': float((1 / ranks).mean())}


def train(model, train_pairs, heldout_pairs, cards, cfg, log=print):
    """Fine-tune ``model`` in place; return the per-step/epoch history."""
    device = pick_device(cfg['device'])
    model.to(device)
    model.max_seq_length = cfg['max_seq_length']
    rng = random.Random(cfg['seed'])
    by_id = {c['id']: c for c in cards}
    steps_per_epoch = sum(1 for _ in batches(train_pairs, cfg['batch_size'], random.Random(0)))
    total = steps_per_epoch * cfg['epochs']
    warmup = max(1, int(cfg['warmup_ratio'] * total))
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg['learning_rate'], weight_decay=cfg['weight_decay'])
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lambda s: (s + 1) / warmup if s < warmup else max(0.0, (total - s) / max(1, total - warmup)))
    history = {'device': device, 'steps_per_epoch': steps_per_epoch, 'total_steps': total, 'steps': [],
               'epochs': [{'epoch': 0, **evaluate(model, heldout_pairs, cards, device)}]}
    log(f"epoch 0 (pretrained) held-out {history['epochs'][0]}")
    step = 0
    for epoch in range(1, cfg['epochs'] + 1):
        model.train()
        start = time.monotonic()
        for batch in batches(train_pairs, cfg['batch_size'], rng):
            q = _encode(model, [f'query: {query}' for query, _ in batch], device)
            p = _encode(model, [passage_text(by_id[cid]) for _, cid in batch], device)
            logits = q @ p.T / cfg['temperature']
            loss = F.cross_entropy(logits, torch.arange(len(batch), device=device))
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg['max_grad_norm'])
            optimizer.step()
            scheduler.step()
            step += 1
            history['steps'].append({'step': step, 'epoch': epoch, 'loss': float(loss.item()),
                                     'lr': scheduler.get_last_lr()[0]})
        metrics = evaluate(model, heldout_pairs, cards, device)
        mean_loss = float(np.mean([s['loss'] for s in history['steps'] if s['epoch'] == epoch]))
        history['epochs'].append({'epoch': epoch, 'train_loss': mean_loss,
                                  'seconds': time.monotonic() - start, **metrics})
        log(f'epoch {epoch}: loss {mean_loss:.4f} held-out {metrics}')
    return history


def weights_sha256(model_dir):
    digest = hashlib.sha256()
    for path in sorted(Path(model_dir).rglob('*.safetensors')):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def save_history_plot(history, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    steps = history['steps']
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2), dpi=180)
    x = [s['step'] for s in steps]
    loss = [s['loss'] for s in steps]
    window = max(1, len(loss) // 20)
    smooth = np.convolve(loss, np.ones(window) / window, mode='valid')
    axes[0].plot(x, loss, color='#2a78d6', alpha=0.25, linewidth=1)
    axes[0].plot(x[window - 1:], smooth, color='#2a78d6', linewidth=2)
    axes[0].set(xlabel='Step', ylabel='Contrastive loss', title='Training loss')
    epochs = history['epochs']
    for key, color in (('recall@1', '#2a78d6'), ('mrr', '#eb6834')):
        axes[1].plot([e['epoch'] for e in epochs], [e[key] for e in epochs], marker='o', color=color,
                     linewidth=2, label=key)
    axes[1].set(xlabel='Epoch (0 = pretrained)', ylim=(0, 1.02), title='Held-out cards',
                xticks=[e['epoch'] for e in epochs])
    axes[1].legend(frameon=False)
    for ax in axes:
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(color='#e4e3df', linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_json(path, payload):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + '\n')

