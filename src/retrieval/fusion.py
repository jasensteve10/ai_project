"""Reciprocal rank fusion of ranked lists (Cormack et al., 2009)."""


def rrf(rankings, k=60, top=10):
    scores = {}
    for ranking in rankings:
        for rank, (card_id, _) in enumerate(ranking, start=1):
            scores[card_id] = scores.get(card_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top]
