# Acceptance Gateway architecture

## Scope

The gateway is a credential-free, localhost reference implementation over synthetic Accounting Acceptance Lab fixtures. It is production-shaped, but it is not hosted, a capacity claim, a production SLO, an exported OpenTelemetry system, or a connection to an accounting provider.

## API and data flow

```text
runtime bearer binding ──SHA-256/constant-time match──> authenticated tenant
        │
        └─ POST /v1/jobs + Idempotency-Key
                    │ strict JSON, size/field/rate/trace gates
                    v
         SQLite gateway_jobs (tenant + key + canonical request digest)
                    │
                    │ atomic lease claim (separate worker process)
                    v
      tenant-specific synthetic Lab SQLite database
                    │
        real ingest → reconcile → retrieve → propose → acceptance gate
                    │                         │
                    │                         └─ abstain/review remains non-authoritative
                    v
       bounded action → effect classification/read-before-retry → readback
                    │
                    v
       job terminal state + acceptance/effect identity + correlated event
```

The queue is transport only. `GatewayWorker` selects the requested immutable fixture and calls `Lab.run_demo(reset=False)`, so the existing parser, deterministic reconciliation, hybrid retrieval, recorded proposal validation, acceptance gate, bounded ledger, unknown-effect recovery, native readback, and packet compiler remain authoritative. A job cannot approve a proposal or add action fields.

## HTTP surface

| Method | Route | Behavior |
|---|---|---|
| `POST` | `/v1/jobs` | Submit `{ "case_id": "C01" }` with an explicit `Idempotency-Key`; optional `inject_fault` is restricted to the documented synthetic timeout. |
| `GET` | `/v1/jobs/{job_id}` | Read one authenticated tenant-owned job. Foreign and absent IDs return the same `404` envelope. |
| `GET` | `/v1/jobs?limit=50&cursor=...` | Stable descending insertion order, opaque exclusive cursor, hard limit 100. |
| `GET` | `/v1/health`, `/v1/ready` | Authenticated SQLite liveness/readiness. |
| `GET` | `/health`, `/ready` | Minimal unauthenticated localhost probes for process supervision. |

Every `/v1/*` request requires an `Authorization: Bearer <runtime-token>` header. JSON submission requires exactly `application/json`, a valid content length no greater than 16 KiB by default, an allowlisted object shape, and a bounded idempotency key. Errors use `{ "error": {"code", "message"}, "request_id" }`. The server ignores forwarding and caller identity headers. `X-Request-ID` and W3C `traceparent` are accepted only after syntax checks; a fresh trace/span is otherwise generated.

## Tenant boundary

Raw tokens exist only in runtime configuration. `Authenticator` immediately hashes each binding with SHA-256 and compares candidate digests with `hmac.compare_digest`; tokens and authorization values are neither stored nor logged. Tenant identity is derived only from the binding. Every gateway job uniqueness constraint and read/list query includes `tenant_id`. The API removes internal tenant IDs from output. Cross-tenant job reads are indistinguishable from absent jobs, lists select only the authenticated tenant, and equal idempotency keys in different tenants are independent.

Worker execution uses a database and artifact subtree named by a one-way tenant-safe identifier. This keeps cases, approvals, effects, retrieval artifacts, and idempotency keys from different tenants in separate SQLite files in addition to the gateway query boundary. There is no gateway case/effect endpoint; linked identifiers appear only on an owned job.

## Durable job state machine

```text
queued ──claim──> running ──success──> succeeded
                    │
                    ├─ retryable failure (attempt < max) ──> retry_wait ──due claim──> running
                    └─ terminal/non-retryable failure ─────> failed

running + expired lease ──atomic recovery claim──> running (new owner/token, attempts + 1)
```

`BEGIN IMMEDIATE` serializes selection and claim. A random claim token is required by conditional renewal and completion updates, so a stale owner can neither renew nor complete a recovered job. During processing, the worker renews its still-current, unexpired claim at a bounded interval and performs a final fenced renewal before transition. A separate claimant therefore cannot reclaim work merely because processing outlasts the original lease. If renewal fails, the worker records `claim_lost` and does not attempt a stale state transition. Interrupts stop and join the renewal thread. Claims and renewals are 1–300 seconds; expired abandoned claims remain recoverable, stale tokens remain rejected, and expired claims stop at the configured attempt bound. SQLite WAL, uniqueness constraints, canonical request digests, and explicit states preserve queued and terminal work across restart. Duplicate same-request delivery returns the original job; same-tenant key reuse with another canonical request returns conflict.

This is not a general exactly-once guarantee. The proof establishes single execution while the SQLite lease can be renewed and one final effect for the synthetic long-running case. A process or storage failure after an external side effect but before durable gateway completion can still cause re-execution; safety then depends on the existing SQLite/idempotent-effect read-before-retry boundary. No distributed queue, consensus protocol, or live-provider exactly-once behavior is claimed.

## Observability

`gateway_events` stores W3C-correlated structured spans with event type, trace ID, span ID, request ID, tenant-safe ID, job ID, transition, measured duration, and error class. Recursive key-based redaction removes authorization, token, secret, password, cookie, and API-key fields. Request bodies and headers are never logged. This is accurately described as structured W3C-correlated spans; no OpenTelemetry SDK or exporter is shipped.

`GatewayStore.operational_summary()` emits deterministic state/event counts. Local load timings are written under ignored `.state/artifacts/` and do not enter canonical evidence identity.

## Schema evolution and rollback

`upgrade_gateway()` creates only `gateway_*` tables and indexes and records schema version 1. `rollback_gateway()` drops only those owned objects. The disposable proof creates a pre-existing `cases` row, upgrades, reads version 1, rolls back, verifies gateway tables are absent, and reads the original row unchanged. Rollback does not downgrade or rewrite Accounting Acceptance Lab tables. A future migration must supply the same owned-object and native-readback proof before version advancement.

## Trust boundaries

1. Bearer secret to derived tenant: runtime-only secret, digest comparison, no forwarding trust.
2. HTTP bytes to canonical request: strict type, byte, field, identifier, and rate bounds.
3. Request to durable queue: tenant-bound idempotency and atomic persistence.
4. Queue to worker: lease is scheduling authority only, never accounting authority.
5. Worker to Lab: immutable fixture selection; all existing acceptance and approval controls remain in force.
6. Unknown response to effect: native read-before-retry, never blind replay.
7. Telemetry to evidence: redaction and tenant-safe IDs; volatile timing excluded from canonical identity.
8. Schema to rollback: gateway-owned objects only, disposable native readback required.
