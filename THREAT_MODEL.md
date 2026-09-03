# Threat model

## Assets and assumptions

Assets are the synthetic ledger's integrity, attributable approval records, evidence/proposal lineage, tenant isolation expectations, local state, event logs, and acceptance receipts. Inputs, retrieved documents, model output, tool output, HTTP responses, and retry status are untrusted. This lab has no client data, production write path, or committed credentials.

| Threat | Current control | Residual risk / missing production control |
|---|---|---|
| Prompt injection or retrieved-content attack | Retrieved text is serialized as evidence; the live system instruction marks it untrusted; schema, citation, risk, and authority gates run after inference. | A capable model can still be influenced. Production needs content isolation, attack classifiers, allowlisted sources, monitoring, and adversarial evaluation. |
| Tool-output attack | Tool/model output is parsed into fixed schemas; actions are independently reconstructed and bounded. | Outer provider response shape and denial-of-service behavior need stronger typed validation and size limits. |
| Authority widening | Models only propose. Allowlisted actions, USD-only/amount bounds, expected generation, case state, and attributable approval gate execution. | The allowlist and threshold are lab constants, not production policy administration. |
| Approval/effect confusion | Approval only stores a decision and increments generation; CLI says whether an effect exists. A separate action request is required. | There is no full operator command for constructing the post-approval effect; integration code must preserve this separation. |
| Duplicate delivery / unknown effect | Unique semantic idempotency key, attempt status, native read-before-retry, and effect readback. | Real downstream systems need durable request/effect reconciliation and provider-specific idempotency guarantees. |
| Stale generation | Every action carries `expected_generation`; mismatch fails closed. | Multi-database races and distributed transactions are outside this lab. |
| Concurrent/overlapping writer | Expiring resource lease blocks a foreign owner over the synthetic ledger range. | One coarse range and SQLite are not a distributed lock service. |
| Tenant leakage | Fixtures and SQLite database are local and synthetic; metadata filters restrict retrieval. | There is no tenant model. Production requires tenant keys on every row, query, cache, log, and citation plus isolation tests. |
| Secret leakage | No credentials in fixtures; optional credentials come from environment; event keys named token/secret/password/authorization are redacted; secret scan runs. | Redaction is shallow and key-name based. Nested/unusual secrets and process/environment exposure remain risks. |
| Citation fabrication | Both backends reject citations not present in retrieved evidence; non-abstaining proposals require a retrieved citation. | Citation presence does not prove entailment. Production needs claim-level entailment checks and source freshness/signature validation. |
| Malformed provider output | Structured fields, enum/type/range checks, and citation checks reject malformed output. Local fake HTTP tests cover malformed JSON object content and invented citations. | HTTP status taxonomy, response size, timeouts, retries, and all provider dialects are not comprehensively tested. |
| Evaluator overlap / benchmark gaming | Held-out rows are distinct from demo cases; challenge wording uses realistic paraphrases; dataset hash and detailed scores are emitted. | Policies and the hand-authored synonym projection are shared. The dataset is too small for unbiased external validity; an independent frozen corpus is needed. |
| Ledger or packet tampering | SQLite native readback, canonical packet JSON, source hashes, and SHA-256 receipts expose changes. | Hashes are not signed and local operators can modify both artifact and receipt. |
| Network exposure | Default service host and compose port bind are localhost-only; image runs unprivileged. | A user can explicitly bind more broadly; there is no authentication or TLS, so doing so is unsafe. |

## Fail-closed behavior

Missing/stale/conflicting/unsupported/ambiguous evidence, materiality, high risk, malformed proposals, invented citations, stale generations, missing approval, out-of-bounds actions, overlap, and absent uncertain attempts all stop automatic execution. The lab does not treat a model statement, approval record, transport success, or self-attested verifier status as native effect proof.
