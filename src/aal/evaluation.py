"""Offline duplicate-reversal checks. Never grants authority or calls the ledger."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

VERSION = "duplicate-reversal-v1"
NO_CLAIM = "Checks apply only to supplied evidence and policy; not proof of authenticity, completeness, posting authority, or an applied effect. Reviewer identity is locally asserted, not authenticated."


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"invalid JSON number: {value}")
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_constant=invalid)


def shape(value, fields, path):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError(f"{path}: expected exactly {fields}")


def text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected nonempty string")


def money(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})\.[0-9]{2}", value):
        raise ValueError(f"{path}: expected nonnegative decimal string with two places")
    return Decimal(value)


def day(value, path):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{path}: expected ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{path}: invalid date") from exc


def rows(value, path):
    if not isinstance(value, list):
        raise ValueError(f"{path}: expected array")


def lines(value, path):
    rows(value, path)
    if len(value) < 2:
        raise ValueError(f"{path}: at least two lines required")
    ids = set()
    for i, line in enumerate(value):
        p = f"{path}[{i}]"
        shape(line, "id account debit credit", p)
        for key in ("id", "account"):
            text(line[key], f"{p}.{key}")
        if line["id"] in ids:
            raise ValueError(f"{p}.id: duplicate")
        ids.add(line["id"])
        d, c = (money(line[k], f"{p}.{k}") for k in ("debit", "credit"))
        if (d > 0) == (c > 0):
            raise ValueError(f"{p}: exactly one positive debit or credit required")


def totals(value, reverse=False):
    result = {}
    for line in value:
        account = line["account"]
        d, c = Decimal(line["debit"]), Decimal(line["credit"])
        old = result.get(account, (Decimal(0), Decimal(0)))
        result[account] = (old[0] + (c if reverse else d), old[1] + (d if reverse else c))
    return {k: [format(d, ".2f"), format(c, ".2f")] for k, (d, c) in sorted(result.items())}


def balanced(value):
    return sum(Decimal(x["debit"]) - Decimal(x["credit"]) for x in value) == 0


def validate(case, proposal):
    shape(case, "schema_version case_id entity currency period_start period_end policy journals evidence prior_reversals history_complete", "case")
    shape(proposal, "schema_version producer target_id duplicate_of entity currency date lines rationale citations", "proposal")
    for name, obj in (("case", case), ("proposal", proposal)):
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise ValueError(f"{name}.schema_version: expected 1")
    for key in ("case_id", "entity", "currency"):
        text(case[key], f"case.{key}")
    if case["currency"] != "USD":
        raise ValueError("case.currency: v1 supports USD only")
    start, end = (day(case[k], f"case.{k}") for k in ("period_start", "period_end"))
    if start > end:
        raise ValueError("case: reversed period")
    shape(case["policy"], "version require_review", "case.policy")
    text(case["policy"]["version"], "case.policy.version")
    for value, path in ((case["history_complete"], "case.history_complete"), (case["policy"]["require_review"], "case.policy.require_review")):
        if type(value) is not bool:
            raise ValueError(f"{path}: expected boolean")
    journals = {}
    rows(case["journals"], "case.journals")
    for i, journal in enumerate(case["journals"]):
        p = f"case.journals[{i}]"
        shape(journal, "id entity currency date purchase_id lines", p)
        for key in ("id", "entity", "currency", "purchase_id"):
            text(journal[key], f"{p}.{key}")
        day(journal["date"], f"{p}.date")
        lines(journal["lines"], f"{p}.lines")
        if journal["id"] in journals:
            raise ValueError(f"{p}.id: duplicate")
        journals[journal["id"]] = journal
    evidence = {}
    rows(case["evidence"], "case.evidence")
    for i, item in enumerate(case["evidence"]):
        p = f"case.evidence[{i}]"
        shape(item, "id kind journal_ids text", p)
        for key in ("id", "kind", "text"):
            text(item[key], f"{p}.{key}")
        rows(item["journal_ids"], f"{p}.journal_ids")
        for ref in item["journal_ids"]:
            text(ref, f"{p}.journal_ids")
            if ref not in journals:
                raise ValueError(f"{p}: unknown journal {ref}")
        if len(set(item["journal_ids"])) != len(item["journal_ids"]) or item["id"] in evidence:
            raise ValueError(f"{p}: duplicate identifier")
        evidence[item["id"]] = item
    rows(case["prior_reversals"], "case.prior_reversals")
    for ref in case["prior_reversals"]:
        text(ref, "case.prior_reversals")
        if ref not in journals:
            raise ValueError(f"case.prior_reversals: unknown journal {ref}")
    for key in ("producer", "target_id", "duplicate_of", "entity", "currency", "rationale"):
        text(proposal[key], f"proposal.{key}")
    day(proposal["date"], "proposal.date")
    lines(proposal["lines"], "proposal.lines")
    rows(proposal["citations"], "proposal.citations")
    for ref in proposal["citations"]:
        text(ref, "proposal.citations")
    return journals, evidence


def binding(case, proposal):
    return {"case_hash": digest(case), "policy_hash": digest(case["policy"]), "proposal_hash": digest(proposal), "evaluator_version": VERSION}


def review(case, proposal, actor, decision, reason):
    validate(case, proposal)
    text(actor, "actor")
    text(reason, "reason")
    if decision not in {"approve", "reject"}:
        raise ValueError("decision: expected approve or reject")
    return {"binding": binding(case, proposal), "actor": actor, "decision": decision, "reason": reason}


def evaluate(case, proposal, disposition=None):
    journals, evidence = validate(case, proposal)
    findings = []
    def add(code, status, expected, observed, refs=()):
        findings.append(dict(code=code, status=status, expected=expected, observed=observed, source_refs=list(refs)))
    def check(code, ok, expected, observed, refs=()):
        add(code, "pass" if ok else "fail", expected, observed, refs)
    target = journals.get(proposal["target_id"])
    original = journals.get(proposal["duplicate_of"])
    refs = [proposal["target_id"], proposal["duplicate_of"]]
    check("source_linkage", bool(target and original) and all(x in evidence for x in proposal["citations"]), "existing journals and citations", refs + proposal["citations"], refs)
    check("journal_balance", balanced(proposal["lines"]), "debits equal credits", totals(proposal["lines"]))
    if target:
        check("exact_reversal", totals(target["lines"], True) == totals(proposal["lines"]), totals(target["lines"], True), totals(proposal["lines"]), [target["id"]])
    else:
        add("exact_reversal", "indeterminate", "target posting", "missing target", refs)
    scope = proposal["entity"] == case["entity"] and proposal["currency"] == case["currency"] and case["period_start"] <= proposal["date"] <= case["period_end"]
    scope = scope and all(j["entity"] == case["entity"] and j["currency"] == case["currency"] and j["date"] <= proposal["date"] for j in (target, original) if j)
    check("scope", scope, {k: case[k] for k in ("entity", "currency", "period_start", "period_end")}, {k: proposal[k] for k in ("entity", "currency", "date")}, refs)
    if target and original:
        same = target["id"] != original["id"] and target["purchase_id"] == original["purchase_id"] and totals(target["lines"]) == totals(original["lines"])
        check("duplicate_consistency", same and balanced(target["lines"]) and balanced(original["lines"]), "distinct balanced postings for same purchase and account amounts", {j["id"]: j["purchase_id"] for j in (target, original)}, refs)
    else:
        add("duplicate_consistency", "indeterminate", "two source postings", "missing source", refs)
    relevant = [e for e in evidence.values() if set(e["journal_ids"]) == set(refs)]
    conflicts = [e["id"] for e in relevant if e["kind"] == "distinct_purchases"]
    supports = [e["id"] for e in relevant if e["kind"] == "duplicate_confirmation" and e["id"] in proposal["citations"]]
    add("duplicate_support", "fail" if conflicts else "pass" if supports else "indeterminate", "cited structured duplicate confirmation with no contrary record", {"support": supports, "conflicts": conflicts}, supports + conflicts)
    add("prior_reversal", "fail" if any(ref in case["prior_reversals"] for ref in refs) else "pass" if case["history_complete"] else "indeterminate", "neither posting reversed in complete supplied history", {"history_complete": case["history_complete"], "prior_reversals": case["prior_reversals"]}, refs)
    bound = binding(case, proposal)
    if disposition is not None:
        shape(disposition, "binding actor decision reason", "review")
        text(disposition["actor"], "review.actor")
        text(disposition["reason"], "review.reason")
        if disposition["decision"] not in {"approve", "reject"}:
            raise ValueError("review.decision: expected approve or reject")
        check("review_binding", disposition["binding"] == bound, bound, disposition["binding"])
        check("review_decision", disposition["decision"] == "approve", "approve", disposition["decision"])
    else:
        add("review_decision", "indeterminate" if case["policy"]["require_review"] else "pass", "local disposition" if case["policy"]["require_review"] else "review not required by supplied policy", "not supplied")
    statuses = {f["status"] for f in findings}
    outcome = "checks_failed" if "fail" in statuses else "review_required" if "indeterminate" in statuses else "checks_passed_within_scope"
    result = dict(binding=bound, case_id=case["case_id"], outcome=outcome, findings=findings, review=disposition, no_claim=NO_CLAIM)
    return json.loads(json.dumps(dict(result, result_hash=digest(result))))


def compare(before, after):
    for result in (before, after):
        if result.get("result_hash") != digest({k: v for k, v in result.items() if k != "result_hash"}):
            raise ValueError("evaluation result hash mismatch")
    basis = [k for k in ("case_hash", "policy_hash", "evaluator_version") if before["binding"][k] != after["binding"][k]]
    old = {f["code"]: f for f in before["findings"]}
    new = {f["code"]: f for f in after["findings"]}
    return {"comparable": not basis, "changed_basis": basis,
            "proposal_changed": before["binding"]["proposal_hash"] != after["binding"]["proposal_hash"],
            "review_changed": before["review"] != after["review"],
            "before": before["outcome"], "after": after["outcome"],
            "changes": [{"code": k, "before": old.get(k), "after": new.get(k)} for k in sorted(old.keys() | new.keys()) if old.get(k) != new.get(k)],
            "unresolved": [f for f in after["findings"] if f["status"] != "pass"], "no_claim": NO_CLAIM}


def format_text(result):
    """Readable findings; quote untrusted input rather than emit terminal controls."""
    def quoted(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)

    output = []
    if "outcome" in result:
        output.append(f"{result['outcome']} — case {quoted(result['case_id'])}")
        counts = {status: sum(f["status"] == status for f in result["findings"])
                  for status in ("pass", "fail", "indeterminate")}
        output.append(f"Checks: {counts['pass']} passed, {counts['fail']} failed, {counts['indeterminate']} indeterminate.")
        findings = [f for f in result["findings"] if f["status"] != "pass"]
    else:
        output.append(f"{quoted(result['before'])} -> {quoted(result['after'])}")
        if result["comparable"]:
            output.append("Same case, policy, and evaluator: proposal comparison permitted.")
        else:
            output.append(f"NOT a proposal-only comparison: changed basis {quoted(result['changed_basis'])}.")
        output.append(f"Proposal changed: {result['proposal_changed']}; review changed: {result['review_changed']}.")
        for change in result["changes"]:
            before, after = change["before"], change["after"]
            old = before["status"] if before else "absent"
            new = after["status"] if after else "absent"
            output.append(f"  {quoted(change['code'])}: {quoted(old)} -> {quoted(new)}"
                          + (" (observations changed)" if old == new else ""))
        findings = result["unresolved"]
        output.append(f"Remaining unresolved checks: {len(findings)}.")
    for finding in findings:
        output.append(f"\n{quoted(finding['status'])}: {quoted(finding['code'])}")
        output.append(f"  Sources: {quoted(finding['source_refs'])}")
        expected, observed = finding["expected"], finding["observed"]
        if finding["code"] == "exact_reversal" and isinstance(expected, dict) and isinstance(observed, dict):
            for account in sorted(expected.keys() | observed.keys()):
                want, actual = expected.get(account, ["0.00", "0.00"]), observed.get(account, ["0.00", "0.00"])
                if want != actual:
                    output.append(f"  {quoted(account)}: expected debit {quoted(want[0])}, credit {quoted(want[1])};"
                                  f" proposed debit {quoted(actual[0])}, credit {quoted(actual[1])}.")
        else:
            output.append(f"  Expected: {quoted(expected)}")
            output.append(f"  Observed: {quoted(observed)}")
    output.append(f"\nLimits: {NO_CLAIM}")
    return "\n".join(output)
