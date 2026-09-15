"""Offline reconciliation of one scoped revenue accrual addback, not a journal."""
from decimal import Decimal

from .evaluation import day, digest, money, rows, shape, text

VERSION = "accrual-addback-v1"
NO_CLAIM = (
    "Arithmetic and supplied source assertions only. Does not authenticate sources, "
    "infer collection allocation, certify period completeness, recalculate a workbook, "
    "or authorize a posting. Positive amounts increase reported revenue."
)


def evaluate_accrual(case, proposal):
    shape(case, "schema_version case_id scope currency period_start period_end support_as_of reversal collections outstanding already_reflected history_complete", "case")
    shape(proposal, "schema_version case_id producer scope currency date additional_adjustment", "proposal")
    for name, obj in (("case", case), ("proposal", proposal)):
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise ValueError(f"{name}.schema_version: expected 1")
        for key in ("case_id", "scope", "currency"):
            text(obj[key], f"{name}.{key}")
        if obj["currency"] != "USD":
            raise ValueError(f"{name}.currency: USD only")
    text(proposal["producer"], "proposal.producer")
    start, end = (day(case[k], k) for k in ("period_start", "period_end"))
    if start > end:
        raise ValueError("case: reversed period")
    support_date = day(case["support_as_of"], "support_as_of")
    proposal_date = day(proposal["date"], "proposal.date")
    if type(case["history_complete"]) is not bool:
        raise ValueError("history_complete: expected boolean")
    references = []
    identifiers = set()

    def item(value, path, dated=False):
        shape(value, "id amount source date" if dated else "id amount source", path)
        for key in ("id", "source"):
            text(value[key], f"{path}.{key}")
        if value["id"] in identifiers:
            raise ValueError(f"{path}: duplicate item id")
        identifiers.add(value["id"])
        references.append(value["source"])
        amount = money(value["amount"], f"{path}.amount")
        if dated:
            day(value["date"], f"{path}.date")
        return amount

    reversal = item(case["reversal"], "reversal", True)
    for key in ("collections", "outstanding"):
        rows(case[key], key)
    collected = sum((item(v, f"collections[{i}]", True) for i, v in enumerate(case["collections"])), Decimal(0))
    outstanding = sum((item(v, f"outstanding[{i}]") for i, v in enumerate(case["outstanding"])), Decimal(0))
    reflected = None
    if case["already_reflected"] is not None:
        reflected = item(case["already_reflected"], "already_reflected")
    raw = proposal["additional_adjustment"]
    if not isinstance(raw, str):
        raise ValueError("additional_adjustment: expected signed decimal string")
    additional = money(raw[1:] if raw.startswith("-") else raw, "additional_adjustment")
    if raw.startswith("-"):
        additional = -additional
    needed = reversal - collected
    expected = None if reflected is None else needed - reflected
    checks = []

    def check(name, status, expected_value, observed):
        checks.append({"id": name, "status": status, "expected": expected_value, "observed": observed})

    def match(name, expected_value, observed):
        check(name, "pass" if expected_value == observed else "fail", expected_value, observed)

    fmt = lambda n: format(n, ".2f")
    match("case_identity", [case["case_id"], case["scope"], case["currency"]],
          [proposal["case_id"], proposal["scope"], proposal["currency"]])
    check("proposal_period", "pass" if start <= proposal_date <= end else "fail",
          [start.isoformat(), end.isoformat()], proposal_date.isoformat())
    dated = [case["reversal"], *case["collections"]]
    outside = [v["id"] for v in dated if not start <= day(v["date"], "date") <= proposal_date]
    match("activity_period", [], outside)
    match("nonnegative_uncollected", True, needed >= 0)
    match("outstanding_tie", fmt(needed), fmt(outstanding))
    check("support_cutoff", "pass" if support_date == end == proposal_date else "indeterminate",
          end.isoformat(), {"support_as_of": support_date.isoformat(), "proposal_date": proposal_date.isoformat()})
    check("history_coverage", "pass" if case["history_complete"] else "indeterminate", True, case["history_complete"])
    if reflected is None:
        check("additional_adjustment", "indeterminate", "known amount already reflected required", fmt(additional))
    else:
        match("additional_adjustment", fmt(expected), fmt(additional))
    outcome = ("checks_failed" if any(c["status"] == "fail" for c in checks)
               else "review_required" if any(c["status"] == "indeterminate" for c in checks)
               else "checks_passed_within_scope")
    result = {
        "evaluator_version": VERSION, "case_hash": digest(case), "proposal_hash": digest(proposal),
        "outcome": outcome, "checks": checks, "source_references": references,
        "reconciliation": {"reversal": fmt(reversal), "prior_period_collections": fmt(collected),
                           "total_addback": fmt(needed), "outstanding": fmt(outstanding),
                           "already_reflected": None if reflected is None else fmt(reflected),
                           "expected_additional_adjustment": None if expected is None else fmt(expected)},
        "no_claim": NO_CLAIM,
    }
    result["result_hash"] = digest(result)
    return result


def format_accrual_text(result):
    lines = [result["outcome"], json_text(result["reconciliation"])]
    lines.extend(f'{c["id"]}: {c["status"]}; expected={json_text(c["expected"])}; observed={json_text(c["observed"])}'
                 for c in result["checks"])
    lines.extend(f"source: {json_text(ref)}" for ref in result["source_references"])
    lines.append(result["no_claim"])
    return "\n".join(lines)


def json_text(value):
    # JSON escaping keeps source/user strings safe for terminal display.
    import json
    return json.dumps(value, sort_keys=True, ensure_ascii=True)
