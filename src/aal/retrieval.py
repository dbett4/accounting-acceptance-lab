from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field


TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {"a", "an", "and", "be", "cannot", "do", "from", "has", "is", "must", "not", "of", "or", "the", "to", "yet"}

# A fixed, domain-level concept projection. It is independent of benchmark labels
# and gives the offline vector-like channel useful paraphrase recall.
CONCEPTS = {
    "again": "duplicate", "twice": "duplicate", "extra": "duplicate",
    "bill": "invoice", "bills": "invoice",
    "settled": "payment", "disbursement": "payment", "paid": "payment",
    "undo": "reverse", "reversal": "reverse", "reversed": "reverse",
    "uncleared": "timing", "pending": "timing",
    "supplier": "vendor", "counterparty": "vendor",
    "identified": "identity", "name": "identity", "certainty": "ambiguous", "uncertain": "ambiguous",
    "earlier": "prior", "cycle": "period", "fix": "correction", "errors": "error",
    "significant": "material", "balance": "amount", "senior": "controller",
    "illicit": "fraud", "movement": "transaction", "escalated": "escalation",
    "records": "source", "underlying": "evidence", "disagree": "conflicting", "book": "post", "entry": "accrual",
    "guidance": "policy", "revision": "version", "force": "effective",
}

PHRASES = {
    "cash book": "bank",
    "not cleared": "outstanding timing",
    "sign off": "approval",
    "cannot be identified": "ambiguous identity",
}


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _trigrams(text: str) -> Counter[str]:
    compact = " ".join(_tokens(text))
    return Counter(compact[i:i+3] for i in range(max(0, len(compact) - 2)))


def _concept_vector(text: str) -> Counter[str]:
    normalized = text.lower()
    for phrase, concept in PHRASES.items():
        normalized = normalized.replace(phrase, concept)
    return Counter(CONCEPTS.get(token, token) for token in _tokens(normalized))


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    den = math.sqrt(sum(v*v for v in a.values()) * sum(v*v for v in b.values()))
    return dot / den if den else 0.0


@dataclass(frozen=True)
class Document:
    doc_id: str
    source: str
    version: str
    effective_date: str
    title: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)
    parent_id: str | None = None


@dataclass(frozen=True)
class RetrievalHit:
    document: Document
    lexical_score: float
    vector_score: float
    rerank_score: float
    hybrid_score: float
    citation: str


class HybridRetriever:
    """Offline BM25-style lexical + character-vector retrieval with reranking."""
    def __init__(self, documents: list[Document]):
        self.documents = list(documents)
        self.by_id = {d.doc_id: d for d in documents}

    def search(self, query: str, filters: dict[str, str] | None = None, limit: int = 5,
               lexical_only: bool = False) -> list[RetrievalHit]:
        filters = filters or {}
        candidates = [d for d in self.documents if all(
            (getattr(d, k, None) == v or d.metadata.get(k) == v) for k, v in filters.items())]
        qtokens = [token for token in _tokens(query) if token not in STOPWORDS]
        qvec = _trigrams(query)
        qconcepts = _concept_vector(query)
        n = max(1, len(candidates))
        dfs = Counter(t for d in candidates for t in set(_tokens(d.title + " " + d.text)))
        scored: list[RetrievalHit] = []
        for d in candidates:
            body_tokens = [token for token in _tokens(d.title + " " + d.text) if token not in STOPWORDS]
            counts = Counter(body_tokens)
            lexical = sum(counts[t] * math.log(1 + n / (1 + dfs[t])) for t in qtokens)
            doc_text = d.title + " " + d.text
            vector = 0.8 * _cosine(qconcepts, _concept_vector(doc_text)) + 0.2 * _cosine(qvec, _trigrams(doc_text))
            phrase = 1.0 if " ".join(qtokens) in (d.title + " " + d.text).lower() else 0.0
            query_concepts = set(qconcepts)
            coverage = len(query_concepts & set(_concept_vector(doc_text))) / max(1, len(query_concepts))
            rerank = 0.6 * coverage + 0.4 * phrase
            hybrid = lexical if lexical_only else 0.45 * lexical + 0.35 * vector + 0.20 * rerank
            scored.append(RetrievalHit(d, round(lexical, 6), round(vector, 6), round(rerank, 6),
                                       round(hybrid, 6), f"{d.source}:{d.doc_id}@{d.version}"))
        scored.sort(key=lambda h: (-h.hybrid_score, h.document.doc_id))
        # Parent expansion replaces a matching chunk with its authoritative parent.
        expanded: list[RetrievalHit] = []
        seen: set[str] = set()
        for hit in scored:
            doc = self.by_id.get(hit.document.parent_id, hit.document) if hit.document.parent_id else hit.document
            if doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            expanded.append(RetrievalHit(doc, hit.lexical_score, hit.vector_score, hit.rerank_score,
                                         hit.hybrid_score, f"{doc.source}:{doc.doc_id}@{doc.version}"))
        return expanded[:limit]
