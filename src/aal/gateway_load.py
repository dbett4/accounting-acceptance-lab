from __future__ import annotations

import http.client
import json
import math
import os
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .gateway import make_gateway_server


def percentile(values: list[float], percent: int) -> float:
    if not values or percent < 0 or percent > 100:
        raise ValueError("non-empty values and percentile 0..100 required")
    ordered = sorted(values)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[index]


def run_local_load(root: str | Path, requests: int = 200, concurrency: int = 8) -> dict:
    """Run a real localhost HTTP submit workload and return measured, non-canonical timing."""
    if requests < 1 or requests > 10_000 or concurrency < 1 or concurrency > 128:
        raise ValueError("load parameters outside local safety bounds")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    token = "local-load-token"
    server = make_gateway_server("127.0.0.1", 0, root / "load.db", root / "artifacts",
                                 {token: "load-tenant"}, rate_limit=requests + 1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    def submit(index: int) -> tuple[float, int]:
        body = json.dumps({"case_id": "C01"}, separators=(",", ":")).encode()
        started = time.perf_counter()
        connection = http.client.HTTPConnection(host, port, timeout=10)
        try:
            connection.request("POST", "/v1/jobs", body=body, headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Idempotency-Key": f"load-{index}",
            })
            response = connection.getresponse()
            response.read()
            status = response.status
        finally:
            connection.close()
        return (time.perf_counter() - started) * 1000, status

    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(submit, range(requests)))
    finally:
        server.shutdown(); server.server_close(); thread.join()
    elapsed = time.perf_counter() - started
    latencies = [latency for latency, _ in results]
    errors = sum(status not in {200, 202} for _, status in results)
    return {
        "label": "local_synthetic_benchmark_not_capacity_or_slo_evidence",
        "workload": {"method": "POST", "route": "/v1/jobs", "request": {"case_id": "C01"},
                     "requests": requests, "concurrency": concurrency, "transport": "localhost_http"},
        "host_run_configuration": {"python": platform.python_version(), "platform": platform.platform(),
                                   "logical_cpu_count": os.cpu_count(), "executable": sys.executable},
        "completed": len(results),
        "errors": errors,
        "elapsed_seconds": round(elapsed, 6),
        "throughput_requests_per_second": round(len(results) / elapsed, 3),
        "latency_ms": {"p50": round(percentile(latencies, 50), 3),
                       "p95": round(percentile(latencies, 95), 3)},
    }
