"""In-process Okapi BM25 over the card corpus, with an accent-folding French tokenizer."""
import math
import re
import unicodedata
from collections import Counter

STOPWORDS = set('''
a au aux avec ce ces dans de des du elle en et est il ils je la le les leur lui ma mais me meme mes moi mon ne nos
notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos votre vous c d j l m n s t y
quel quelle quels quelles combien est-ce the of
'''.split())


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


def tokenize(text):
    # IDs such as '001' and labels such as 'PDCI-RDA' are kept as tokens, as well as their parts.
    tokens = []
    for raw in re.findall(r"[\w'.-]+", fold(text)):
        raw = raw.strip("'.-")
        parts = [p for p in re.split(r"['.-]", raw) if p]
        for tok in ([raw] if len(parts) > 1 else []) + parts:
            if tok and tok not in STOPWORDS:
                tokens.append(tok)
    return tokens


class BM25:
    def __init__(self, cards, k1=1.5, b=0.75):
        if not cards:
            raise ValueError('BM25 requires a nonempty corpus')
        self.cards, self.k1, self.b = cards, k1, b
        self.docs = [Counter(tokenize(c['title'] + ' ' + c['text'])) for c in cards]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths) or 1.0
        df = Counter(t for d in self.docs for t in d)
        n = len(cards)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query):
        terms = tokenize(query)
        out = []
        for doc, length in zip(self.docs, self.lengths):
            s = 0.0
            for t in terms:
                tf = doc.get(t)
                if tf:
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_len))
            out.append(s)
        return out

    def search(self, query, k=10):
        """Ranked (card_id, score) pairs; zero-score cards are not returned."""
        ranked = sorted(zip(self.scores(query), range(len(self.cards))), key=lambda x: (-x[0], x[1]))
        return [(self.cards[i]['id'], s) for s, i in ranked[:k] if s > 0]
