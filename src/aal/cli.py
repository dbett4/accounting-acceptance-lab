from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .app import Lab
from .benchmark import run_benchmark
from .service import make_server


def paths(args):
    state = Path(args.state_dir or os.environ.get("AAL_STATE_DIR", ".state"))
    return state / "lab.db", state / "artifacts"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aal")
    parser.add_argument("--state-dir")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo")
    sub.add_parser("benchmark")
    accrual = sub.add_parser("reconcile-accrual", help="Check a scoped revenue accrual addback without posting")
    accrual_input = accrual.add_mutually_exclusive_group(required=True)
    accrual_input.add_argument("--case", help="Prepared JSON case; requires --proposal")
    accrual_input.add_argument("--workbook", help="XLSX source; requires --mapping, --additional-adjustment and --producer")
    accrual.add_argument("--proposal")
    accrual.add_argument("--mapping", help="Reviewed reusable XLSX layout mapping")
    accrual.add_argument("--additional-adjustment", help="Signed USD amount to add now; use =-49.75 for a negative amount")
    accrual.add_argument("--producer", help="Locally asserted proposal author, not an authenticated reviewer")
    accrual.add_argument("--out", required=True)
    accrual.add_argument("--format", choices=("json", "text"), default="json")
    for command in ("evaluate", "review-proposal"):
        p = sub.add_parser(command)
        p.add_argument("--case", required=True)
        p.add_argument("--proposal", required=True)
        p.add_argument("--out", required=True)
        if command == "evaluate":
            p.add_argument("--review")
            p.add_argument("--format", choices=("json", "text"), default="json", help="stdout format; --out always preserves full JSON")
        else:
            p.add_argument("--actor", required=True)
            p.add_argument("--decision", choices=("approve", "reject"), required=True)
            p.add_argument("--reason", required=True)
    comparison = sub.add_parser("compare")
    comparison.add_argument("before")
    comparison.add_argument("after")
    comparison.add_argument("--format", choices=("json", "text"), default="json")
    batch = sub.add_parser("evaluate-batch")
    batch.add_argument("--manifest", required=True)
    batch.add_argument("--out", required=True)
    batch.add_argument("--format", choices=("json", "text"), default="json")
    batch_comparison = sub.add_parser("compare-batches")
    batch_comparison.add_argument("before")
    batch_comparison.add_argument("after")
    batch_comparison.add_argument("--format", choices=("json", "text"), default="json")
    inspect = sub.add_parser("inspect"); inspect.add_argument("case_id")
    for decision in ("approve", "reject"):
        p = sub.add_parser(decision); p.add_argument("case_id"); p.add_argument("--actor", required=True); p.add_argument("--reason", required=True)
    replay = sub.add_parser("replay"); replay.add_argument("idempotency_key")
    serve = sub.add_parser("serve"); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.command == "reconcile-accrual":
        from .accrual import evaluate_accrual, format_accrual_text
        from .evaluation import load
        if args.workbook:
            if args.proposal is not None or any(value is None for value in (args.mapping, args.additional_adjustment, args.producer)):
                parser.error("--workbook requires --mapping, --additional-adjustment and --producer; --proposal is not allowed")
        elif args.proposal is None or any(value is not None for value in (args.mapping, args.additional_adjustment, args.producer)):
            parser.error("--case requires --proposal; workbook mapping/amount/producer options are not allowed")
        try:
            if args.workbook:
                from .accrual_intake import reconcile_workbook
                result = reconcile_workbook(args.workbook, load(args.mapping), args.additional_adjustment, args.producer)
            else:
                result = evaluate_accrual(load(args.case), load(args.proposal))
            with Path(args.out).open("x", encoding="utf-8") as output:
                output.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            print(format_accrual_text(result) if args.format == "text" else json.dumps(result, indent=2, sort_keys=True))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        raise SystemExit({"checks_passed_within_scope": 0, "checks_failed": 1, "review_required": 3}[result["outcome"]])
    if args.command in {"evaluate-batch", "compare-batches"}:
        from .batch import compare_batches, evaluate_batch, exit_code, format_batch_text
        from .evaluation import load
        try:
            if args.command == "evaluate-batch":
                result = evaluate_batch(args.manifest)
                with Path(args.out).open("x", encoding="utf-8") as output:
                    output.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            else:
                result = compare_batches(load(args.before), load(args.after))
            print(format_batch_text(result) if args.format == "text" else json.dumps(result, indent=2, sort_keys=True))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        if args.command == "evaluate-batch":
            raise SystemExit(exit_code(result))
        return
    if args.command in {"evaluate", "review-proposal", "compare"}:
        from .evaluation import compare, evaluate, format_text, load, review
        try:
            if args.command == "compare":
                result = compare(load(args.before), load(args.after))
            else:
                case, proposal = load(args.case), load(args.proposal)
                if args.command == "evaluate":
                    result = evaluate(case, proposal, load(args.review) if args.review else None)
                else:
                    result = review(case, proposal, args.actor, args.decision, args.reason)
                # Preserve earlier failures and source inputs, including symlink targets.
                with Path(args.out).open("x", encoding="utf-8") as output:
                    output.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            print(format_text(result) if getattr(args, "format", "json") == "text" else json.dumps(result, indent=2, sort_keys=True))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        if args.command == "evaluate":
            raise SystemExit({"checks_passed_within_scope": 0, "checks_failed": 1, "review_required": 3}[result["outcome"]])
        return
    db, artifacts = paths(args)
    if args.command == "benchmark":
        print(json.dumps(run_benchmark(artifacts), indent=2, sort_keys=True)); return
    if args.command == "serve":
        server = make_server(args.host, args.port, db, artifacts)
        print(f"http://{args.host}:{args.port}", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return
    lab = Lab(db, artifacts)
    try:
        if args.command == "demo": result = lab.run_demo(reset=True)
        elif args.command == "inspect": result = lab.inspect(args.case_id)
        elif args.command in {"approve", "reject"}:
            decision = "approved" if args.command == "approve" else "rejected"
            lab.store.approve(args.case_id, decision, args.actor, args.reason)
            result = lab.inspect(args.case_id)
            result["approval_lifecycle"] = {
                "decision_recorded": decision,
                "effect_applied": bool(result["effects"]),
                "next_step": ("submit a separately constructed bounded action with the new generation"
                              if decision == "approved" else "none; rejection is terminal"),
            }
        elif args.command == "replay": result = lab.ledger.replay_uncertain(args.idempotency_key).__dict__
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    finally: lab.close()


if __name__ == "__main__": main()
