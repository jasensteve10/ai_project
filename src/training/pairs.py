"""Synthetic (query, card) pairs for contrastive fine-tuning of the retriever.

Queries come from templates over the card corpus only — never from benchmark
questions — and any query too similar to a benchmark question is dropped.
Pairs are split by card, so held-out cards are unseen during training.
"""
import hashlib
import random
import re

from src.retrieval.bm25 import fold

# Hand-written intents for the 19 schema/domain cards (independent of the benchmark).
FIXED_QUERIES = {
    'schema:vw_circonscriptions': ['totaux par circonscription', 'inscrits et votants de chaque circonscription',
                                   'bureaux de vote et participation'],
    'schema:vw_resultats_candidats': ['toutes les candidatures et leurs voix', 'score de chaque liste',
                                      'tableau des candidats par circonscription'],
    'schema:vw_vainqueur': ['liste des élus', 'candidat élu dans chaque circonscription', 'les gagnants'],
    'schema:circ.turnout': ['nombre de votants et d’inscrits', 'colonnes de participation'],
    'schema:circ.ballots': ['bulletins nuls et blancs', 'suffrages exprimés par circonscription'],
    'schema:cand.scores': ['voix obtenues par liste', 'pourcentage des voix d’un candidat'],
    'schema:cand.provenance': ['numéro de page du document', 'ligne du tableau source'],
    'domain:regional_turnout': ['taux de participation pondéré', 'participation d’une région entière'],
    'domain:mean_constituency_turnout': ['moyenne simple des taux', 'participation moyenne non pondérée'],
    'domain:fractions': ['pourcentage ou fraction', 'seuil en pourcentage'],
    'domain:expressed_ballots': ['différence entre votants et exprimés', 'taux de bulletins nuls'],
    'domain:no_double_count': ['éviter de compter deux fois les inscrits', 'somme des inscrits correcte'],
    'domain:elected_not_seats': ['nombre de sièges', 'élu ou député'],
    'domain:independents': ['candidats sans parti', 'les indépendants'],
    'domain:winner_margin': ['écart entre premier et second', 'marge de victoire'],
    'domain:competition': ['nombre de candidats en lice', 'circonscription sans adversaire'],
    'domain:provenance': ['citer la source', 'où trouver ce résultat dans le PDF'],
    'domain:scope': ['données démographiques', 'élection précédente', 'résultats par bureau de vote'],
    'domain:name_matching': ['orthographe des noms de lieux', 'chercher une commune'],
}
CIRC_TEMPLATES = ['Qui l’emporte à {n} ?', 'résultats {n}', 'participation {n}', 'inscrits à {n}',
                  'liste élue {n}']
REGION_TEMPLATES = ['région {n}', 'participation en {n}', 'circonscriptions de {n}', 'votants {n}']
PARTY_TEMPLATES = ['voix du {n}', 'élus {n}', 'candidatures {n}']


def _variants(name, rng):
    """Surface forms users type: title case, hyphen/space swaps, accent-free."""
    base = re.sub(r'\s+', ' ', name).strip(' ,')
    forms = {base.title(), base.lower(), base.replace('-', ' ').title(), base.replace(' ', '-').title(), fold(base).title()}
    return rng.sample(sorted(forms), k=min(2, len(forms)))


_ADMIN = re.compile(r"\b(COMMUNES?|SOUS-?\s?PR[EÉ]FECTURES?|VILLE)\b", re.IGNORECASE)


def _places(circ_name):
    """Town names inside a constituency label, e.g. 'GOMON ET SIKENSI, COMMUNES ...' -> GOMON, SIKENSI."""
    out = []
    for part in re.split(r',| ET ', _ADMIN.sub(' ', circ_name)):
        name = re.sub(r'\s+', ' ', part).strip(' -,')
        if len(name) >= 3:
            out.append(name)
    return out or [circ_name]


def _entity_name(card):
    match = re.search(r"= '(.*?)'", card['text'])
    return match.group(1) if match else card['title']


def _circ_name(card):
    return re.search(r"circonscription_name = '(.*?)', région", card['text']).group(1)


def generate(cards, seed):
    rng = random.Random(seed)
    # A place appearing in several constituency names would make an ambiguous positive.
    owners = {}
    for card in cards:
        if card['id'].startswith('entity:circ:'):
            for place in set(map(fold, _places(_circ_name(card)))):
                owners.setdefault(place, set()).add(card['id'])
    pairs = []
    for card in cards:
        cid = card['id']
        if cid in FIXED_QUERIES:
            pairs += [(q, cid) for q in FIXED_QUERIES[cid]]
        elif cid.startswith('entity:circ:'):
            places = _places(_circ_name(card))
            unique = [pl for pl in places if len(owners[fold(pl)]) == 1]
            if not unique:  # only the numeric ID identifies this constituency unambiguously
                pairs.append((f'circonscription {cid.split(":")[-1]}', cid))
                continue
            place = rng.choice(unique)
            for template in rng.sample(CIRC_TEMPLATES, 2):
                pairs += [(template.format(n=v), cid) for v in _variants(place, rng)]
            pairs.append((f'circonscription {cid.split(":")[-1]}', cid))
        elif cid.startswith('entity:region:'):
            for template in rng.sample(REGION_TEMPLATES, 2):
                pairs += [(template.format(n=v), cid) for v in _variants(_entity_name(card), rng)]
        elif cid.startswith('entity:party:'):
            for template in rng.sample(PARTY_TEMPLATES, 2):
                pairs.append((template.format(n=_entity_name(card)), cid))
    return pairs


def _tokens(text):
    return set(re.findall(r'\w+', fold(text)))


def drop_benchmark_lookalikes(pairs, benchmark_questions, threshold):
    """Leakage guard: remove queries whose token Jaccard with any benchmark question reaches ``threshold``."""
    bench = [_tokens(q) for q in benchmark_questions]
    kept, dropped = [], []
    for query, cid in pairs:
        t = _tokens(query)
        worst = max((len(t & b) / len(t | b) for b in bench if t | b), default=0)
        (dropped if worst >= threshold else kept).append((query, cid))
    return kept, dropped


def split_by_card(pairs, heldout_fraction, seed):
    """Deterministic card-level split: all queries of a held-out card stay out of training."""
    def heldout(cid):
        h = int(hashlib.sha256(f'{seed}:{cid}'.encode()).hexdigest(), 16)
        return (h % 10_000) / 10_000 < heldout_fraction
    train = [p for p in pairs if not heldout(p[1])]
    test = [p for p in pairs if heldout(p[1])]
    return train, test
