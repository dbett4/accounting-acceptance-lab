# Proof

All evidence below was produced locally from synthetic fixtures. No provider, client system, remote, or human approver was contacted.

## Full offline gate

```bash
bash scripts/proof.sh
```

Observed terminal result:

```text
Ran 15 tests in approximately 2.4s
OK

demo: case_count=12
accepted_deterministic_match=1
accepted_after_read_before_retry=1
blocked_pending_human_approval=2
abstained_or_routed=8
false_accepts=0
unsupported_accepts=0
recovery_success=1

benchmark: lexical_only=10/16 (0.625)
benchmark: hybrid_reranked=16/16 (1.0)
benchmark: effect_safe effect_count=1 duplicate_effect_rate=0.0
benchmark: naive_retry effect_count=2 duplicate_effect_rate=1.0
benchmark: independent_gate false_accepts=0

process restart: phases=2, process_restarts=1, persisted_case=C01, passed=true
secret_scan files_scanned=tracked_worktree findings=0
ALL_LOCAL_PROOF_GATES_PASSED
```

The 15 tests include a local fake OpenAI-compatible HTTP server. They prove a valid request/response path, rejection of malformed structured output, and rejection of a citation absent from retrieved evidence. They do not make a real provider call.

## Container gate

The current user can reach Docker Engine 29.7.1 through passwordless `sudo -n`; direct socket access is denied because the user is not in the `docker` group. The proof script detects either route.

```bash
bash scripts/container_proof.sh
```

Observed after repairing the image packaging path and waiting for the native Docker health check:

```text
build=passed
container_health_status=healthy
health_before_restart.status=ok
health_after_restart.status=ok
case_before_restart.case_id=C01
case_after_restart.case_id=C01
case_after_restart.state=accepted
passed=true
```

The script builds the image, starts it as UID 10001, initializes the synthetic demo in a named volume, reads C01, restarts the same container, reads C01 again, writes `.state/artifacts/container_receipt.json`, and removes the proof container, volume, and image. This is a container restart and state-persistence check, not merely a process restart.

Compose was also parsed with:

```bash
sudo -n docker compose config --quiet
```

## Artifact locations

Runtime evidence is generated under `.state/artifacts/` and ignored by Git. The final observed benchmark, summary, proof transcript, and restart/container receipts are copied into `evidence/` before commit so a read-only verifier can inspect the evidence without rerunning mutable commands:

- `evidence/benchmark.json`
- `evidence/benchmark_receipt.json`
- `evidence/demo_summary.json`
- `evidence/restart_receipt.json`
- `evidence/container_receipt.json`
- `evidence/proof-output.txt`

## Scope and limitations

- Live OpenAI-compatible inference: unexercised.
- External or human verification: not run. The benchmark's independent gate is a rule-based surrogate.
- Human review minutes: a three-minute-per-case planning estimate, not observed labor.
- Provider-billed cost: not measured. Recorded-backend estimated provider cost is zero.
- Container result is local to this host and base-image digest; it is not a deployment claim.
