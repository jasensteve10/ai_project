"""Prepare local retrieval assets, inspect context, or ask the production RAG."""
import argparse
import json

from src.retrieval.corpus import build_cards, corpus_hash, write_cards
from src.retrieval.pipeline import APP_CONDITIONS, CONFIG_PATH, ElectionRAG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare', help='build corpus; optionally download and index E5')
    prepare.add_argument('--download-model', action='store_true')
    for action in ('search', 'ask'):
        command = sub.add_parser(action)
        command.add_argument('question')
        command.add_argument('--condition', choices=APP_CONDITIONS, default='B')
    args = parser.parse_args()
    if args.command == 'prepare':
        cards = build_cards()
        write_cards(cards)
        if args.download_model:
            from src.retrieval.dense import DenseRetriever
            config = json.loads(CONFIG_PATH.read_text())
            DenseRetriever(cards, config['dense_model'], revision=config.get('dense_revision'),
                           corpus_version=corpus_hash(cards), download=True)
        print(f'{len(cards)} cards ready; corpus {corpus_hash(cards)}')
        return
    rag = ElectionRAG(args.condition)
    output = rag.search(args.question) if args.command == 'search' else rag.run_query(args.question)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
