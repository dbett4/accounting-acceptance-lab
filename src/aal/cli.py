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
    inspect = sub.add_parser("inspect"); inspect.add_argument("case_id")
    for decision in ("approve", "reject"):
        p = sub.add_parser(decision); p.add_argument("case_id"); p.add_argument("--actor", required=True); p.add_argument("--reason", required=True)
    replay = sub.add_parser("replay"); replay.add_argument("idempotency_key")
    serve = sub.add_parser("serve"); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
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
