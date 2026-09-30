"""Stage 3 — model training: fit retrieval indices, then fine-tune the E5 retriever.

    python -m src.pipeline.train                    # indices + fine-tuning (configs/training.json)
    python -m src.pipeline.train --skip-finetune    # indices only
    python -m src.pipeline.train --download-model   # first run: fetch the pinned E5 weights

The SQL generator (Gemini/Claude) is a hosted pretrained model and is not trained here.
Outputs: models/<output_dir> (gitignored) and outputs/training/.
"""
import argparse
import importlib.metadata
import json
import platform
from pathlib import Path

import numpy as np

from src.evaluation.benchmark import load_benchmark
from src.retrieval.bm25 import BM25
from src.retrieval.corpus import corpus_hash, verified_cards
from src.retrieval.dense import DenseRetriever
from src.training import finetune, pairs as pairgen

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/training'
CONFIG = ROOT / 'configs/training.json'


def fit_indices(cards, cfg, download):
    """Lexical statistics and the pretrained embedding matrix (cached under dataset/retrieval/embeddings)."""
    bm25 = BM25(cards)
    dense = DenseRetriever(cards, cfg['base_model'], revision=cfg['base_revision'],
                           corpus_version=corpus_hash(cards), download=download)
    return {'cards': len(cards), 'bm25': {'k1': bm25.k1, 'b': bm25.b, 'vocabulary': len(bm25.idf),
                                           'mean_card_tokens': round(bm25.avg_len, 2)},
            'e5': {'model': cfg['base_model'], 'revision': dense.revision,
                   'embedding_matrix': list(dense.matrix.shape),
                   'norms_min_max': [float(np.linalg.norm(dense.matrix, axis=1).min()),
                                     float(np.linalg.norm(dense.matrix, axis=1).max())]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--skip-finetune', action='store_true')
    parser.add_argument('--download-model', action='store_true')
    args = parser.parse_args(argv)
    cfg = json.loads(args.config.read_text())
    cards = verified_cards()
    OUT.mkdir(parents=True, exist_ok=True)

    indices = fit_indices(cards, cfg, args.download_model)
    finetune.write_json(OUT / 'indices.json', indices)
    print(json.dumps(indices, indent=2))
    if args.skip_finetune:
        return

    from sentence_transformers import SentenceTransformer
    finetune.seed_everything(cfg['seed'])
    generated = pairgen.generate(cards, cfg['seed'])
    questions = [r['question'] for r in load_benchmark(include_excluded=True)]
    kept, dropped = pairgen.drop_benchmark_lookalikes(generated, questions, cfg['leakage_jaccard_threshold'])
    train_pairs, heldout_pairs = pairgen.split_by_card(kept, cfg['heldout_fraction'], cfg['seed'])
    finetune.write_json(OUT / 'pairs.json', {
        'generated': len(generated), 'dropped_benchmark_lookalikes': dropped,
        'train_pairs': len(train_pairs), 'train_cards': len({c for _, c in train_pairs}),
        'heldout_pairs': len(heldout_pairs), 'heldout_cards': len({c for _, c in heldout_pairs}),
        'train': train_pairs, 'heldout': heldout_pairs})

    model = SentenceTransformer(cfg['base_model'], revision=cfg['base_revision'], local_files_only=True)
    history = finetune.train(model, train_pairs, heldout_pairs, cards, cfg)
    out_dir = ROOT / cfg['output_dir']
    model.save(str(out_dir))
    finetune.write_json(OUT / 'history.json', history)
    finetune.save_history_plot(history, OUT / 'training_curves.png')
    base, final = history['epochs'][0], history['epochs'][-1]
    metrics = {'heldout_pretrained': base, 'heldout_finetuned': final,
               'delta': {k: final[k] - base[k] for k in ('recall@1', 'recall@5', 'mrr')}}
    finetune.write_json(OUT / 'metrics.json', metrics)
    finetune.write_json(OUT / 'manifest.json', {
        'command': 'python -m src.pipeline.train', 'config': cfg, 'device': history['device'],
        'corpus_sha256': corpus_hash(cards), 'weights_sha256': finetune.weights_sha256(out_dir),
        'python': platform.python_version(), 'platform': platform.platform(),
        'libraries': {name: importlib.metadata.version(name) for name in ('torch', 'sentence-transformers',
                                                                        'transformers', 'numpy')},
        'note': 'MPS/GPU kernels are not bit-for-bit deterministic; the seed fixes data order and initial state.'})
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
