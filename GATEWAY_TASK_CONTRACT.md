# Build contract: Acceptance Gateway

## Objective

Extend Accounting Acceptance Lab into a credential-free, production-shaped gateway that proves authenticated tenant isolation, durable asynchronous jobs, bounded concurrency and retries, traceable request-to-effect execution, measured operation, and reversible schema evolution without weakening the existing acceptance controls.

This is a synthetic reference implementation. It is not a customer system, production accounting integration, hosted multi-tenant service, or claim of live provider operation.

## Authority and boundaries

- Work only in this repository and worktree.
- Preserve all existing Accounting Acceptance Lab behavior and evidence boundaries.
- Use synthetic fixtures only. Do not access customer data, credentials, external accounting systems, or paid model APIs.
- Do not push, publish, deploy, send messages, change remotes, or alter host services.
- Do not expose a mutable unauthenticated public service.
- Do not fabricate human approvals, provider cost, deployment, load capacity, or rollback evidence.
- Keep the default proof route offline and credential-free.

## Required implementation

### 1. Authentication and tenant scope

- Authenticate every `/v1/*` route with a bearer token.
- Load token-to-tenant bindings from runtime configuration; never commit usable secrets.
- Store only token digests where persistence is needed and compare secrets in constant time.
- Derive tenant identity from authentication. Reject or ignore caller-supplied tenant identity.
- Enforce tenant scope in every job, event, case, approval, and effect lookup exposed through the gateway.
- Return indistinguishable not-found responses for cross-tenant object access.

### 2. Bounded HTTP API

Provide versioned JSON routes for:

- submit a job using an explicit idempotency key;
- inspect one owned job;
- list owned jobs with a hard page limit and stable cursor/order;
- process work through a separate worker surface, not inside submission;
- health and readiness.

Require strict JSON content type, bounded request bytes, strict allowed fields, stable error envelopes, request IDs, and per-tenant rate limiting. Do not trust forwarded identity headers.

### 3. Durable asynchronous jobs

- Persist jobs in SQLite with explicit states and legal transitions.
- Make duplicate submission under the same tenant/idempotency key return the original job.
- Reject key reuse with a different canonical request.
- Claim work atomically so concurrent workers cannot execute one job twice.
- Recover expired worker claims through a bounded lease policy.
- Record attempts, terminal state, error class, and linked acceptance/effect identity.
- Survive process restart without losing queued or terminal jobs.

### 4. Existing acceptance controls

The worker must use the real Accounting Acceptance Lab ingestion, retrieval, proposal, approval, bounded-action, readback, and packet paths. The queue is transport, not authority. A queued job cannot widen action scope, invent approval, or convert abstention/review into acceptance.

### 5. Observability and redaction

- Emit structured request/job events with trace ID, span ID, request ID, tenant-safe identifier, job ID, state transition, duration, and error class.
- Accept and validate W3C `traceparent`; generate a trace when absent.
- Never log bearer tokens, raw authorization headers, secrets, or unrestricted request bodies.
- Produce deterministic operational summary JSON for tests and portfolio evidence.
- If no OpenTelemetry SDK/exporter is shipped, describe traces accurately as W3C-correlated structured spans, not as exported OpenTelemetry telemetry.

### 6. Failure, load, and rollback proof

Exercise at least:

- duplicate delivery;
- concurrent worker claim;
- expired claim recovery;
- lost/unknown effect response with read-before-retry;
- stale generation;
- provider/proposal timeout classification through a synthetic injected fault;
- database interruption and readiness failure;
- cross-tenant read/list/idempotency attacks;
- malformed/oversized/rate-limited requests;
- restart persistence;
- schema upgrade and rollback from a disposable database.

Add a deterministic local load harness that reports actual throughput and p50/p95 latency for a declared synthetic workload and host/run configuration. Label the result as a local benchmark, not service capacity or production SLO evidence.

## Required artifacts

- Gateway implementation under `src/aal/`.
- Tests under `tests/`.
- One executable gateway proof script under `scripts/`.
- Machine-readable evidence under `evidence/` generated from real execution.
- `GATEWAY_ARCHITECTURE.md` with API/data flow, state machine, tenant boundary, trust boundaries, and rollback model.
- `GATEWAY_THREAT_MODEL.md` covering auth bypass, tenant leakage, replay, queue races, stale leases, log leakage, resource exhaustion, prompt/retrieval injection, and evaluator overlap.
- `GATEWAY_PROOF.md` with exact commands and observed results.
- README integration with explicit synthetic/local limitations.

## Acceptance gates

1. All pre-existing tests and `scripts/proof.sh` remain green.
2. New tests prove every required control and negative path.
3. Same-input replay has no duplicate job or ledger effects.
4. Two concurrent workers cannot both claim or execute one job.
5. Cross-tenant reads, lists, and idempotency collisions reveal no foreign existence or data.
6. Restart retains queued and terminal jobs.
7. Readiness fails when SQLite is unavailable.
8. Logs and evidence contain no bearer token or authorization header values.
9. Upgrade and rollback work on a disposable database with native readback.
10. Load metrics are generated by real execution and include workload definition.
11. Tracked evidence is deterministic where identity is claimed; runtime timing stays outside canonical identity.
12. Secret scan and `git diff --check` pass.
13. Independent verifier can reproduce the proof from the exact candidate revision.

## Stop conditions

Stop rather than weakening scope if tenant isolation cannot be proven, queue claims permit duplicate execution, rollback is destructive or unverifiable, the implementation requires real credentials, or existing acceptance controls would need to be bypassed.

## Handoff state

Leave a clean, reviewable candidate with exact changed-file inventory and proof output. Do not commit unless the coordinator explicitly authorizes it. Do not push or deploy.