# Architecture

## Data flow

```text
synthetic GL / bank / policy / vendor / support
                    |
          typed canonical records
                    |
       deterministic reconciliation
          | clean            | unresolved
          v                  v
   deterministic verdict   metadata-filtered retrieval
                            lexical + concept-vector
                            parent expansion + reranking
                                      |
                           recorded proposal fixture
                         (optional OpenAI-compatible client)
                                      |
                         schema + citation validation
                                      |
                  risk/evidence/materiality acceptance gate
                       | accept | abstain | human review
                                      |
                    attributable decision record
                    (decision is not an effect)
                                      |
                  separate bounded action request
                                      |
              generation / lease / amount / authority gates
                                      |
            semantic idempotency key -> synthetic SQLite ledger
                                      |
                         native effect readback
                                      |
              canonical packet + SHA-256 + event log
```

## Components

- `models.py` normalizes and validates typed records.
- `reconcile.py` owns deterministic matching.
- `retrieval.py` applies version/metadata filters, BM25-style lexical scoring, a fixed accounting-concept vector projection plus character similarity, reranking, parent expansion, and citations.
- `proposals.py` validates immutable recorded responses and the optional OpenAI-compatible response shape. Any non-abstaining proposal needs a citation present in the retrieved evidence.
- `control.py` is the bounded action interface. It enforces action/currency/amount bounds, expected generations, attributable approval for proposal authority, overlap leases, semantic idempotency, uncertain-effect classification, and read-before-retry.
- `store.py` persists cases, approvals, leases, attempts, effects, and redacted events in SQLite.
- `packet.py` emits canonical byte-stable verdict JSON and hashes.
- `service.py` provides the localhost operator UI, JSON case views, and health endpoint.

## Control flow and authority

A model can propose; it cannot approve or execute. A human decision changes approval state and case generation but does not itself create an effect. Execution requires a new explicit `ActionRequest`. Deterministic-policy actions are restricted to cases marked `system_authorized`; proposal actions require an attributable stored approval. The ledger is synthetic and local.

## Trust boundaries

1. **Fixtures to parser:** input is untrusted until typed validation succeeds.
2. **Retrieved content to proposal backend:** evidence is data, not instruction. The live system prompt says so; output still passes schema and citation checks.
3. **Proposal to acceptance gate:** model provenance is not authority. Missing, stale, conflicting, unsupported, ambiguous, high-risk, or material results route away from automatic action.
4. **Human decision to effect:** approval is evidence of authority, not proof an effect occurred. A separate bounded action and readback cross this boundary.
5. **Transport to effect store:** an uncertain response is not retried blindly. Native lookup by semantic key determines whether an effect already exists.
6. **Service/container to host:** service binds to localhost; the image runs as UID 10001 and writes only its mounted state directory.

## Persistence and determinism

SQLite uses WAL mode. Docker mounts `/state`; process and container restarts reopen the same database. Acceptance packet canonicalization excludes nondeterministic serialization differences. Runtime latency remains an observed, intentionally variable field in demo packets; the packet identity test uses identical evidence and payload.
