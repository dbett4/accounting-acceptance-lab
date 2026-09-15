"""Evaluate a declared set of proposals without dropping failed inputs."""
from pathlib import Path
import json

from .evaluation import NO_CLAIM, compare, digest, evaluate, format_text, load, rows, shape, text

OUTCOMES = ("checks_passed_within_scope", "checks_failed", "review_required", "invalid_input")


def summary(members):
    counts = dict.fromkeys(OUTCOMES, 0)
    for member in members:
        counts[member["evaluation"]["outcome"] if member["evaluation"] else "invalid_input"] += 1
    return {"total": len(members), **counts}


def exit_code(result):
    counts = result["summary"]
    if counts["invalid_input"]:
        return 2
    if counts["checks_failed"]:
        return 1
    if counts["review_required"]:
        return 3
    return 0


def evaluate_batch(manifest_path):
    path = Path(manifest_path).resolve()
    manifest = load(path)
    shape(manifest, "schema_version name members", "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("manifest.schema_version: expected 1")
    text(manifest["name"], "manifest.name")
    rows(manifest["members"], "manifest.members")
    if not manifest["members"]:
        raise ValueError("manifest.members: empty batch cannot be evaluated")
    ids = set()
    for i, member in enumerate(manifest["members"]):
        shape(member, "id case proposal review", f"manifest.members[{i}]")
        for field in ("id", "case", "proposal"):
            text(member[field], f"manifest.members[{i}].{field}")
        if member["review"] is not None:
            text(member["review"], f"manifest.members[{i}].review")
        if member["id"] in ids:
            raise ValueError(f"manifest.members[{i}].id: duplicate")
        ids.add(member["id"])

    # Read each unique path once, so a case shared by proposals has one snapshot.
    # This is not a filesystem-wide atomic snapshot; no writers should edit inputs.
    cache = {}
    def read(relative):
        relative_path = Path(relative)
        resolved = (path.parent / relative_path).resolve()
        if relative_path.is_absolute() or not resolved.is_relative_to(path.parent):
            raise ValueError(f"input path must stay inside manifest directory: {relative!r}")
        if resolved not in cache:
            try:
                if not resolved.is_file():
                    raise ValueError(f"expected a regular input file: {relative!r}")
                cache[resolved] = load(resolved)
            except (ValueError, OSError) as exc:
                cache[resolved] = exc
        if isinstance(cache[resolved], Exception):
            raise cache[resolved]
        return cache[resolved]

    snapshots = []
    for member in manifest["members"]:
        try:
            snapshots.append((read(member["case"]), read(member["proposal"]),
                              read(member["review"]) if member["review"] is not None else None))
        except (ValueError, OSError) as exc:
            snapshots.append(exc)
    members = []
    for member, snapshot in zip(manifest["members"], snapshots):
        evaluation, error = None, None
        try:
            if isinstance(snapshot, Exception):
                raise snapshot
            if member["review"] is not None and snapshot[2] is None:
                raise ValueError("review file must contain a disposition, not null")
            evaluation = evaluate(*snapshot)
        except (ValueError, OSError) as exc:
            error = str(exc)
        members.append({"id": member["id"], "evaluation": evaluation, "error": error})
    result = {"kind": "batch_evaluation", "schema_version": 1, "manifest": manifest,
              "manifest_hash": digest(manifest), "members": members,
              "summary": summary(members), "no_claim": NO_CLAIM}
    return {**result, "result_hash": digest(result)}


def validate_result(result):
    shape(result, "kind schema_version manifest manifest_hash members summary no_claim result_hash", "batch result")
    if result["kind"] != "batch_evaluation" or type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ValueError("unsupported batch result")
    if result["result_hash"] != digest({k: v for k, v in result.items() if k != "result_hash"}):
        raise ValueError("batch result hash mismatch")
    if result["manifest_hash"] != digest(result["manifest"]):
        raise ValueError("batch manifest hash mismatch")
    rows(result["members"], "batch members")
    if not result["members"]:
        raise ValueError("empty batch result")
    ids = []
    for member in result["members"]:
        shape(member, "id evaluation error", "batch member")
        text(member["id"], "batch member.id")
        ids.append(member["id"])
        if member["evaluation"] is None:
            text(member["error"], "batch member.error")
        else:
            if member["error"] is not None or member["evaluation"]["outcome"] not in OUTCOMES[:3]:
                raise ValueError("invalid batch member outcome")
            compare(member["evaluation"], member["evaluation"])
    if len(set(ids)) != len(ids) or ids != [m["id"] for m in result["manifest"]["members"]]:
        raise ValueError("batch member identity mismatch")
    if result["summary"] != summary(result["members"]):
        raise ValueError("batch summary mismatch")


def compare_batches(before, after):
    validate_result(before)
    validate_result(after)
    old = {m["id"]: m for m in before["members"]}
    new = {m["id"]: m for m in after["members"]}
    added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
    members = []
    for mid in sorted(old.keys() & new.keys()):
        a, b = old[mid], new[mid]
        delta = compare(a["evaluation"], b["evaluation"]) if a["evaluation"] and b["evaluation"] else None
        members.append({"id": mid, "comparison": delta, "before_error": a["error"], "after_error": b["error"]})
    comparable = not added and not removed and all(m["comparison"] and m["comparison"]["comparable"] for m in members)
    return {"kind": "batch_comparison", "comparable": bool(comparable),
            "added": added, "removed": removed, "members": members,
            "before_summary": before["summary"], "after_summary": after["summary"],
            "before_hash": before["result_hash"], "after_hash": after["result_hash"],
            "no_claim": NO_CLAIM}


def format_batch_text(result):
    quoted = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True)
    if result["kind"] == "batch_evaluation":
        output = [f"Batch {quoted(result['manifest']['name'])}: {quoted(result['summary'])}"]
        for member in result["members"]:
            output.append(f"\nMember {quoted(member['id'])}")
            output.append(format_text(member["evaluation"]) if member["evaluation"] else f"invalid_input: {quoted(member['error'])}")
    else:
        output = ["Comparable batch inputs." if result["comparable"] else "NOT a same-basis batch comparison.",
                  f"Before: {quoted(result['before_summary'])}", f"After: {quoted(result['after_summary'])}",
                  f"Added: {quoted(result['added'])}; removed: {quoted(result['removed'])}."]
        for member in result["members"]:
            output.append(f"\nMember {quoted(member['id'])}")
            output.append(format_text(member["comparison"]) if member["comparison"] else
                          f"Not comparable: input errors before={quoted(member['before_error'])}, after={quoted(member['after_error'])}.")
    output.append(f"\nLimits: {NO_CLAIM} Batch counts are outcomes, not accuracy scores.")
    return "\n".join(output)
