"""Record the pretrained-model setup. No model fitting or fine-tuning is performed."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def describe(config):
    return {'training_performed': False, 'train_split': None, 'epochs': None,
            'optimizer': None, 'learning_rate': None,
            'method': 'Inference with frozen pretrained models; retrieval settings evaluated on development data.',
            'encoder': {'model': config['dense_model'], 'revision': config['dense_revision'], 'device': 'cpu'},
            'generator': 'Selected per evaluation run; inspect its manifest for provider/model.',
            'unverified_artifacts': 'models/e5-small-edan-ft is not referenced by the comparison config; its name alone does not establish that training occurred.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/text_to_sql_comparison.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/model_setup/manifest.json')
    parser.add_argument('--prepare-encoder', action='store_true', help='index cards with the cached frozen encoder')
    parser.add_argument('--download-model', action='store_true', help='explicitly allow downloading the pinned public encoder')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    manifest = describe(config)
    manifest['config_sha256'] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    if args.download_model and not args.prepare_encoder:
        parser.error('--download-model requires --prepare-encoder')
    if args.prepare_encoder:
        from src.retrieval.corpus import verified_cards, corpus_hash
        from src.retrieval.dense import DenseRetriever
        cards = verified_cards()
        DenseRetriever(cards, config['dense_model'], revision=config['dense_revision'],
                       corpus_version=corpus_hash(cards), download=args.download_model)
        manifest['indexed_cards'] = len(cards)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
