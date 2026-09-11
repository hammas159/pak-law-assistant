"""BM25 retrieval over a statute book, always as of a date.

BM25 rather than embeddings, and that is a considered choice for this corpus rather
than a shortcut.

Legal text is **terminology-bound**. "Reasonable apprehension of death" is a term of
art; a semantically similar paraphrase is not the same provision and retrieving it
instead is a wrong answer. Embeddings are good at paraphrase, which is exactly the
wrong strength here. Statutes also use a small, stable vocabulary that classic term
weighting handles well.

The more important property is that BM25 is **inspectable**. When a lawyer asks why a
provision was returned, "these terms matched with these weights" is an answer. "It was
close in a 384-dimensional space" is not, and in a domain where the reasoning has to be
auditable that difference decides the design.

Implemented from the formula. It is a sum over query terms.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from .corpus import Corpus, Provision

_TOKEN = re.compile(r"[\w؀-ۿ']+", re.UNICODE)

# Words that carry no discriminating power in a statute book, where nearly every
# provision contains "section", "act", "shall" and "person".
LEGAL_STOPWORDS = {
    "the", "a", "an", "of", "in", "to", "for", "by", "or", "and", "any", "such",
    "shall", "be", "is", "are", "as", "on", "with", "under", "this", "that", "it",
    "act", "section", "sub", "clause", "provided", "may", "which", "who", "where",
    "has", "have", "had", "been", "was", "were", "not", "no", "if", "than", "then",
}


def tokenise(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Words, lowercased. Urdu script is preserved as its own tokens.

    Legal numbers are kept: "302" is one of the most discriminating tokens in a penal
    code, and a tokeniser that drops digits loses the ability to find a section by its
    number.
    """
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    if keep_stopwords:
        return tokens
    return [t for t in tokens if t not in LEGAL_STOPWORDS]


@dataclass
class Hit:
    provision: Provision
    score: float
    matched_terms: dict[str, float] = field(default_factory=dict)

    def why(self) -> str:
        """Why this provision was returned, in terms a lawyer can check."""
        ranked = sorted(self.matched_terms.items(), key=lambda kv: -kv[1])[:5]
        return ", ".join(f"{term} ({weight:.2f})" for term, weight in ranked)


@dataclass
class BM25Index:
    k1: float = 1.5
    b: float = 0.75
    # Matching a section *number* should outrank matching its prose.
    number_boost: float = 3.0
    heading_boost: float = 2.0

    documents: list[Provision] = field(default_factory=list)
    _tokens: list[list[str]] = field(default_factory=list)
    _frequencies: list[Counter] = field(default_factory=list)
    _document_frequency: Counter = field(default_factory=Counter)
    _average_length: float = 0.0

    def fit(self, provisions: Sequence[Provision]) -> BM25Index:
        self.documents = list(provisions)
        self._tokens = []
        self._frequencies = []
        self._document_frequency = Counter()

        for provision in self.documents:
            tokens = (
                tokenise(provision.text)
                + tokenise(provision.heading) * int(self.heading_boost)
                # The number is repeated so term frequency carries the boost, rather
                # than bolting a separate score on afterwards.
                + [provision.number.lower()] * int(self.number_boost)
            )
            self._tokens.append(tokens)
            frequencies = Counter(tokens)
            self._frequencies.append(frequencies)
            self._document_frequency.update(frequencies.keys())

        lengths = [len(t) for t in self._tokens]
        self._average_length = sum(lengths) / len(lengths) if lengths else 0.0
        return self

    def _idf(self, term: str) -> float:
        n = len(self.documents)
        df = self._document_frequency.get(term, 0)
        # Lucene's variant: always positive, so a term in most documents contributes
        # little rather than subtracting score from documents that contain it.
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, *, limit: int = 5) -> list[Hit]:
        if not self.documents:
            return []

        terms = tokenise(query)
        if not terms:
            return []

        hits: list[Hit] = []
        for index, provision in enumerate(self.documents):
            frequencies = self._frequencies[index]
            length = len(self._tokens[index])
            score = 0.0
            matched: dict[str, float] = {}

            for term in set(terms):
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                idf = self._idf(term)
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / (self._average_length or 1)
                )
                contribution = idf * frequency * (self.k1 + 1) / denominator
                score += contribution
                matched[term] = round(contribution, 4)

            if score > 0:
                hits.append(Hit(provision=provision, score=round(score, 6),
                                matched_terms=matched))

        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


@dataclass
class LawSearch:
    """Retrieval that is always anchored to a date.

    There is no way to search the corpus without supplying one. That is deliberate: an
    optional date parameter defaults to something, and the default eventually gets used
    for a question where it is wrong.
    """

    corpus: Corpus
    _indexes: dict[str, BM25Index] = field(default_factory=dict)

    def _index_for(self, as_of: dt.date) -> BM25Index:
        key = as_of.isoformat()
        if key not in self._indexes:
            self._indexes[key] = BM25Index().fit(self.corpus.as_of(as_of))
        return self._indexes[key]

    def search(
        self, query: str, *, as_of: str | dt.date, limit: int = 5,
        statute: str | None = None,
    ) -> list[Hit]:
        as_of = dt.date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
        hits = self._index_for(as_of).search(query, limit=limit * 3)
        if statute:
            hits = [h for h in hits if h.provision.statute == statute]
        return hits[:limit]

    def by_citation(self, key: str, *, as_of: str | dt.date) -> Provision | None:
        """Look up a provision the user named directly.

        A question that cites a section is not a search problem — returning the
        *nearest* provision to a citation the user spelled out is how a system answers
        about the wrong law.
        """
        return self.corpus.version_on(key, as_of)
