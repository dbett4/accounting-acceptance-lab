# Accounting Acceptance Lab

[![CI](https://github.com/dbett4/accounting-acceptance-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/dbett4/accounting-acceptance-lab/actions/workflows/ci.yml)

A credential-free, synthetic reconciliation lab for testing whether an accounting agent can produce evidence-linked proposals without silently widening authority or duplicating ledger effects.

## One-command offline demo

```bash
bash scripts/offline_demo.sh
```

The command uses only the Python standard library and writes local state and receipts under `.state/`. The full local gate is:

```bash
bash scripts/proof.sh
```

## Surfaces

```bash
export PYTHONPATH=src
python3 -m aal.cli demo
python3 -m aal.cli benchmark
python3 -m aal.cli inspect C03
python3 -m aal.cli approve C03 --actor "$USER" --reason "reviewed source and policy"
python3 -m aal.cli reject C03 --actor "$USER" --reason "evidence is insufficient"
python3 -m aal.cli replay C12:timing:v1
python3 -m aal.cli serve --port 8080
```

The UI and health endpoint are then at `http://127.0.0.1:8080/` and `/health`. The service is localhost-only by default. Health executes `SELECT 1` against SQLite and returns HTTP 503 if the probe fails.

## Acceptance Gateway

The gateway extends the synthetic lab with an authenticated, tenant-scoped asynchronous API and durable SQLite job queue. It remains localhost-only and credential-free by default: bearer-token bindings are supplied at runtime, and the proof route uses synthetic fixtures without contacting a model provider.

```bash
export PYTHONPATH=src
export AAL_GATEWAY_DEMO_TOKEN='local-demo-token'
export AAL_GATEWAY_TOKEN_BINDINGS='{"local-demo-token":"tenant-demo"}'
python3 -m aal.gateway_cli --state-dir .state serve

# In another terminal, submit and process one synthetic job.
curl -sS -X POST http://127.0.0.1:8090/v1/jobs \
  -H "Authorization: Bearer ${AAL_GATEWAY_DEMO_TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-c01' \
  --data '{"case_id":"C01"}'
python3 -m aal.gateway_cli --state-dir .state worker --worker-id local-worker --max-jobs 1
```

Run the complete gateway evidence gate with `PYTHONPATH=src python3 scripts/gateway_proof.py`. It exercises authentication and tenant isolation, strict request bounds, duplicate delivery, concurrent claims, lease renewal, bounded retries, restart persistence, stale-generation handling, uncertain-effect recovery, redacted correlated events, database-readiness failure, measured localhost load, and a reversible schema migration. See [GATEWAY_ARCHITECTURE.md](GATEWAY_ARCHITECTURE.md), [GATEWAY_THREAT_MODEL.md](GATEWAY_THREAT_MODEL.md), and [GATEWAY_PROOF.md](GATEWAY_PROOF.md).

**Approval lifecycle:** `approve` and `reject` record an attributable decision and advance the case generation. An approval stores the exact proposal digest plus action, amount, currency, and resulting generation. Approval alone does **not** apply a ledger effect. Applying it is a separate bounded-ledger request that must match the approved scope exactly. Authorization precedes idempotency readback, and key reuse with different canonical request fields is rejected. Rejection is terminal.

## Optional OpenAI-compatible backend

`OpenAICompatibleBackend` reads only `AAL_OPENAI_BASE_URL`, `AAL_OPENAI_API_KEY`, and `AAL_OPENAI_MODEL`. It is not selected by the offline demo. Tests exercise the HTTP protocol against a local fake server, malformed structured output, and invented citations; they make no provider call. Live inference remains unexercised.

## Docker

```bash
docker compose up --build
```

Compose binds only `127.0.0.1:8080`, runs as an unprivileged user, includes a health check, and persists SQLite state in the `aal-state` volume.

## What is synthetic

All GL, bank, policy, vendor, support, proposals, approvals used in examples, and effects are local synthetic fixtures. Demo fixtures never claim a human approved anything. `recorded_model` is immutable fixture provenance, not live inference. Cost is reported as zero for the recorded backend and is not a claim about provider pricing. The independent gate is a deterministic surrogate, not third-party verification.
