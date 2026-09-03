# Acceptance Gateway proof

## Run

```bash
bash scripts/proof.sh
```

The repository gate runs the original Accounting Acceptance Lab suite and demo/benchmark/restart checks, then runs `scripts/gateway_proof.py` and the tracked-worktree secret scan. The gateway proof writes deterministic control outcomes to `evidence/gateway_proof.json` and runtime-only latency observations to `.state/artifacts/gateway_load_runtime.json`.

## Acceptance evidence

- 52 unit and integration tests, including all 32 pre-gateway tests.
- Tenant identity is derived from bearer-token bindings supplied at runtime; callers cannot select a tenant in request JSON.
- Job submission is idempotent per tenant and request payload. Cross-tenant reads and lists are scoped by the authenticated tenant.
- SQLite `BEGIN IMMEDIATE` claims prevent simultaneous ownership. Expired claims are recoverable, claim attempts are bounded, and claim tokens fence completion.
- Workers renew live leases while processing; the slow-worker test crosses the original lease boundary and proves one execution and one effect.
- Queued and terminal jobs survive a store restart. `/ready` fails closed when SQLite is unavailable.
- Synthetic timeout, duplicate delivery, stale generation, uncertain effect, and database interruption paths are exercised.
- Structured events carry request, trace, and span correlation while tenant identifiers and secret-like fields are redacted.
- The local load harness sends 100 authenticated HTTP submissions at concurrency 8 and records measured p50/p95 latency. This is a repeatable workload observation, not an SLO or production-capacity claim.
- A disposable SQLite database upgrades and rolls back one schema revision with native schema/data readback.

## Evidence boundaries

The implementation uses synthetic fixtures, localhost HTTP, runtime-only demo token bindings, and no live model provider. The event format is W3C-correlated structured telemetry, not an exported OpenTelemetry pipeline. The migration exercise proves reversibility for the declared revision only. Passing local proof is `EXERCISED`; publication or deployment requires an exact-revision independent verdict and a separate release decision.