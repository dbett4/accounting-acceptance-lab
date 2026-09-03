from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .gateway import GatewayStore, GatewayWorker, load_token_bindings, make_gateway_server
from .gateway_load import run_local_load


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="aal-gateway")
    parser.add_argument("--state-dir", default=os.environ.get("AAL_STATE_DIR", ".state"))
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    worker = sub.add_parser("worker")
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--max-jobs", type=int, default=1)
    load = sub.add_parser("load")
    load.add_argument("--requests", type=int, default=200)
    load.add_argument("--concurrency", type=int, default=8)
    sub.add_parser("summary")
    args = parser.parse_args(argv)
    root = Path(args.state_dir)
    db_path = root / "gateway.db"
    artifacts = root / "gateway-artifacts"

    if args.command == "serve":
        bindings = load_token_bindings(os.environ.get("AAL_GATEWAY_TOKEN_BINDINGS"))
        server = make_gateway_server(args.host, args.port, db_path, artifacts, bindings)
        print(f"http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if args.command == "load":
        result = run_local_load(root / "gateway-load", args.requests, args.concurrency)
        output = root / "artifacts" / "gateway_load_runtime.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    store = GatewayStore(db_path)
    try:
        if args.command == "worker":
            if args.max_jobs < 1 or args.max_jobs > 10_000:
                raise SystemExit("--max-jobs must be between 1 and 10000")
            implementation = GatewayWorker(store, artifacts)
            results = []
            for _ in range(args.max_jobs):
                result = implementation.process_one(args.worker_id)
                if result is None:
                    break
                results.append(result)
            print(json.dumps({"processed": len(results), "results": results}, indent=2, sort_keys=True))
        else:
            print(json.dumps(store.operational_summary(), indent=2, sort_keys=True))
    finally:
        store.close()


if __name__ == "__main__":
    main()
