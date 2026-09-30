"""Build the retrievable card corpus: schema, domain and entity cards with stable IDs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import duckdb

from src.schema.schema import DB_PATH, DESCRIPTIONS, read_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GLOSSARY_PATH = PROJECT_ROOT / 'src/semantic/glossary.json'
DICTIONARY_PATH = PROJECT_ROOT / 'docs/eda/tables/data_dictionary.csv'
CARDS_PATH = PROJECT_ROOT / 'dataset/retrieval/cards.jsonl'
PDF_PATH = PROJECT_ROOT / 'dataset/raw/EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf'

# Coherent column groups: one card each, so retrieval can select part of a view.
COLUMN_GROUPS = {
    'schema:circ.turnout': ('mart.vw_circonscriptions', 'Participation et électeurs par circonscription',
                            ['circonscription_id', 'region', 'nb_bureaux_vote', 'inscrits', 'votants', 'taux_participation']),
    'schema:circ.ballots': ('mart.vw_circonscriptions', 'Bulletins nuls, blancs et suffrages exprimés par circonscription',
                            ['circonscription_id', 'votants', 'bulletins_nuls', 'suffrages_exprimes',
                             'bulletins_blancs_nb', 'bulletins_blancs_pct']),
    'schema:cand.scores': ('mart.vw_resultats_candidats', 'Voix et pourcentages des candidatures/listes',
                           ['circonscription_id', 'parti', 'candidat', 'score', 'score_pct', 'elu']),
    'schema:cand.provenance': ('mart.vw_resultats_candidats', 'Provenance PDF des candidatures',
                               ['candidat', 'circonscription_id', 'source_page', 'source_table', 'source_row']),
}


def _dictionary():
    with open(DICTIONARY_PATH, newline='') as f:
        return {r['column']: r['definition'] for r in csv.DictReader(f)}


def _column_line(col, types, defs):
    return f"- {col} ({types.get(col, '?')}): {defs.get(col, '')}".rstrip(': ')


def build_cards(db_path=DB_PATH, glossary_path=GLOSSARY_PATH):
    defs = _dictionary()
    with duckdb.connect(str(db_path), read_only=True) as con:
        catalog = read_catalog(con)
        regions = con.execute(
            'SELECT region, COUNT(*), SUM(inscrits) FROM mart.vw_circonscriptions GROUP BY 1 ORDER BY 1').fetchall()
        circs = con.execute(
            'SELECT circonscription_id, circonscription_name, region FROM mart.vw_circonscriptions ORDER BY 1').fetchall()
        parties = con.execute(
            'SELECT parti, COUNT(*), COUNT_IF(elu) FROM mart.vw_resultats_candidats GROUP BY 1 ORDER BY 1').fetchall()
        pages = dict(con.execute('SELECT circonscription_id, LIST(DISTINCT source_page ORDER BY source_page) '
                                 'FROM mart.vw_resultats_candidats GROUP BY 1').fetchall())
    cards = []
    for relation, info in catalog.items():
        types = {c['name']: c['type'] for c in info['columns']}
        lines = [_column_line(c, types, defs) for c in types]
        cards.append({
            'id': f'schema:{relation.split(".")[1]}', 'kind': 'schema', 'title': relation,
            'text': f"Vue {relation}. {DESCRIPTIONS[relation]} Jointure par circonscription_id.\nColonnes:\n"
                    + '\n'.join(lines),
            'provenance': 'duckdb catalog + data dictionary'})
    for card_id, (relation, title, cols) in COLUMN_GROUPS.items():
        types = {c['name']: c['type'] for c in catalog[relation]['columns']}
        cards.append({
            'id': card_id, 'kind': 'schema', 'title': f'{relation}: {title}',
            'text': f"{title} ({relation}).\n" + '\n'.join(_column_line(c, types, defs) for c in cols),
            'provenance': 'duckdb catalog + data dictionary'})
    for item in json.loads(Path(glossary_path).read_text()):
        cards.append({**item, 'kind': 'domain', 'provenance': 'src/semantic/glossary.json'})
    for region, n, inscrits in regions:
        cards.append({
            'id': f'entity:region:{region}', 'kind': 'entity', 'title': f'Région {region}',
            'text': f"Région (colonne region) = '{region}'. {n} circonscriptions, {inscrits} inscrits.",
            'provenance': 'mart.vw_circonscriptions'})
    for cid, name, region in circs:
        cards.append({
            'id': f'entity:circ:{cid}', 'kind': 'entity', 'title': f'Circonscription {cid}',
            'text': f"Circonscription circonscription_id = '{cid}', circonscription_name = '{name}', région '{region}'.",
            'provenance': 'mart.vw_circonscriptions', 'source_pages': pages[cid]})
    for parti, n, elus in parties:
        cards.append({
            'id': f'entity:party:{parti}', 'kind': 'entity', 'title': f'Parti {parti}',
            'text': f"Parti/groupement (colonne parti) = '{parti}'. {n} candidatures/listes, {elus} marquées élues.",
            'provenance': 'mart.vw_resultats_candidats'})
    ids = [c['id'] for c in cards]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate card IDs')
    return cards


def verified_cards(db_path=DB_PATH, path=CARDS_PATH):
    """Refuse a stale persisted corpus rather than evaluate yesterday's data."""
    cards = load_cards(path)
    if corpus_hash(cards) != corpus_hash(build_cards(db_path)):
        raise ValueError('Corpus périmé : exécuter python -m src.retrieval.corpus')
    return cards


def corpus_hash(cards):
    payload = json.dumps(sorted(cards, key=lambda c: c['id']), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def write_cards(cards, path=CARDS_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in cards))


def load_cards(path=CARDS_PATH):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def format_cards(cards):
    """Render cards as the prompt context block, grouped by kind for readability."""
    order = {'schema': 0, 'domain': 1, 'entity': 2}
    return '\n\n'.join(f"[{c['id']}] {c['title']}\n{c['text']}"
                       for c in sorted(cards, key=lambda c: order.get(c['kind'], 3)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--output', type=Path, default=CARDS_PATH)
    args = parser.parse_args()
    cards = build_cards(args.db)
    write_cards(cards, args.output)
    kinds = {k: sum(c['kind'] == k for c in cards) for k in ('schema', 'domain', 'entity')}
    print(f'{len(cards)} cards {kinds} -> {args.output}; hash {corpus_hash(cards)}')


if __name__ == '__main__':
    main()
