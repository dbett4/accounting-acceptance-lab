# Proof

All evidence below was produced locally from synthetic fixtures. No provider, client system, remote, or human approver was contacted.

## Full offline gate

```bash
bash scripts/proof.sh
```

Observed terminal result on the repaired revision:

```text
Ran 32 tests in 3.795s
OK

demo: case_count=12
accepted_deterministic_match=1
accepted_after_read_before_retry=1
blocked_pending_human_approval=2
abstained_or_routed=8
recovery_success=1

benchmark: lexical_only=10/16 (0.625)
benchmark: hybrid_reranked=16/16 (1.0)
benchmark: independent_gate accepted=4 false_accepts=0 unsupported_accepts=0
benchmark: self_attested accepted=10 false_accepts=6
benchmark: effect_safe effect_count=1 duplicate_effect_rate=0.0
benchmark: naive_retry effect_count=2 duplicate_effect_rate=1.0

process restart: phases=2, process_restarts=1, persisted_case=C01, passed=true
secret_scan files_scanned=tracked_worktree findings=0
ALL_LOCAL_PROOF_GATES_PASSED
```

The 32 tests include:

- two fresh full demo runs with byte-identical verdict JSON and hashes for all 12 cases, while latency remains in separate telemetry files;
- held-out label-independence and planted unsafe-proposal rejection;
- unauthorized/mismatched idempotency-key reuse rejection without cross-case readback;
- proposal/action/case-type/amount/currency/generation approval binding, stale-approval invalidation, and terminal rejection;
- generation consumption after effects and non-reset demo replay without duplicate application;
- a real two-connection SQLite race proving one overlapping writer wins and one is rejected;
- failed-database health returning HTTP 503;
- local fake OpenAI-compatible protocol, malformed-output, and invented-citation checks.

## Container gate

```bash
bash scripts/container_proof.sh
```

Observed result:

```text
build=passed
container_health_status=healthy
health_before_restart.status=ok
health_before_restart.database=reachable
health_after_restart.status=ok
health_after_restart.database=reachable
case_before_restart.case_id=C01
case_after_restart.case_id=C01
case_after_restart.state=accepted
passed=true
```

The script built the image, started it as UID 10001, initialized the synthetic demo in a named volume, queried database-backed health and C01, restarted the same container, queried both again, and removed the proof container, volume, and image.

## Additional gates

```bash
PYTHONPATH=src python3 -m compileall -q src tests
git diff --check
python3 scripts/secret_scan.py
```

All returned success; the independent tracked-worktree secret scan reported zero findings.

## Artifact locations

Runtime evidence is generated under `.state/artifacts/`. The final observed benchmark, summary, proof transcript, and restart/container receipts are copied into tracked `evidence/` for read-only verification:

- `evidence/benchmark.json`
- `evidence/benchmark_receipt.json`
- `evidence/demo_summary.json`
- `evidence/restart_receipt.json`
- `evidence/container_receipt.json`
- `evidence/proof-output.txt`

## Scope and limitations

- Live OpenAI-compatible inference: unexercised.
- External or human verification: not run. The benchmark gate is a rule-based surrogate.
- Human review minutes: a three-minute-per-case planning estimate, not observed labor.
- Provider-billed cost: not measured. Recorded-backend estimated provider cost is zero.
- Container result is local to this host and base-image digest; it is not a deployment claim.
