from __future__ import annotations

from .models import CanonicalRecord, ReconciliationResult


def _norm(value: str) -> str:
    return "".join(c.lower() for c in value if c.isalnum())


def deterministic_reconcile(gl: list[CanonicalRecord], bank: list[CanonicalRecord]) -> list[ReconciliationResult]:
    """Match only exact amount/date/reference records; leave judgment unresolved."""
    used: set[str] = set()
    results: list[ReconciliationResult] = []
    for entry in gl:
        candidates = [b for b in bank if b.record_id not in used
                      and entry.amount == -b.amount and entry.date == b.date
                      and _norm(entry.reference) and _norm(entry.reference) == _norm(b.reference)]
        if len(candidates) == 1:
            match = candidates[0]
            used.add(match.record_id)
            results.append(ReconciliationResult(f"case-{entry.record_id}", "clean_match",
                                                 (entry.record_id, match.record_id),
                                                 "exact amount, date, and reference"))
        else:
            reason = "ambiguous deterministic candidates" if len(candidates) > 1 else "no exact deterministic match"
            results.append(ReconciliationResult(f"case-{entry.record_id}", "unresolved", (entry.record_id,), reason))
    for entry in bank:
        if entry.record_id not in used:
            results.append(ReconciliationResult(f"case-{entry.record_id}", "unresolved", (entry.record_id,), "unmatched bank record"))
    return results
