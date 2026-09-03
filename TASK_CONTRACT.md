# Build contract: Accounting Acceptance Lab

## Objective
Build one coherent, end-to-end synthetic reconciliation and exception workflow that demonstrates the missing agent-engineering proof in Dave Bettner's portfolio: evidence-grounded retrieval, model-generated proposals, bounded tools, human approval, effect-safe retry, native readback, held-out evaluation, operational packaging, and a concise demo.

## Required workflow
1. Ingest synthetic GL, bank, policy, vendor, and supporting-evidence inputs.
2. Normalize records into typed canonical schemas.
3. Reconcile deterministic matches without model judgment.
4. Retrieve relevant policy/evidence for unresolved items using a real hybrid lexical + vector-like local retrieval implementation, metadata filters, parent-document expansion, and reranking. It must run offline and expose retrieval scores/citations.
5. Send unresolved cases to a proposal backend with structured-output validation. Ship:
   - a credential-free recorded-response backend with immutable fixtures and explicit `recorded_model` provenance;
   - an optional OpenAI-compatible live backend configured only through environment variables, with no secrets committed.
6. Require evidence-linked proposals and abstain/route to review when evidence is missing, stale, conflicting, unsupported, or material.
7. Represent human approval explicitly. No fixture may fabricate a human approval.
8. Apply approved low-risk actions only to a synthetic ledger through a bounded action interface.
9. Use semantic idempotency keys, expected generations, ownership/overlap protection, uncertain-effect classification, read-before-retry, and native readback.
10. Compile a deterministic acceptance packet containing source hashes, policy version, retrieval citations, proposal provenance, approval state, action/effect identity, readback, verifier status, residuals, latency, and estimated cost.

## Required cases
At minimum: clean match, timing difference, duplicate, missing source, conflicting source, unsupported accrual, ambiguous vendor, prior-period error, policy-version change, materiality threshold, suspicious unresolved item, and retry after unknown external effect.

## Required evaluation
Create a held-out benchmark and executable comparison covering:
- lexical-only versus hybrid+rered/reranked retrieval;
- naive retry versus effect classification/readback;
- self-attested result versus independent verification gate.
Report accepted-output rate, false-accept rate, unsupported-conclusion rate, citation accuracy, duplicate-effect rate, first-pass acceptance, human-review count/minutes estimate, recovery success, p50/p95 latency, and estimated cost per accepted item. Define every metric. Never manufacture human decisions or provider-billed cost.

## Required product surfaces
- CLI: run demo, run benchmark, inspect a case, approve/reject a pending case, and replay an uncertain effect.
- Small operator UI showing cases, evidence/citations, proposal, risk/materiality, approval state, readback, and terminal verdict. Localhost only.
- Dockerfile and compose file or equivalent reproducible local deployment.
- Health endpoint and persisted local state across restart.
- Structured event log with redaction.

## Required proof and documentation
- README with one-command offline demo.
- ARCHITECTURE.md with data/control flow and trust boundaries.
- EVALUATION.md generated or updated from real benchmark output.
- THREAT_MODEL.md covering prompt injection/retrieved-content attacks, tool-output attacks, authority widening, duplicate delivery, stale generations, tenant leakage, secret leakage, and evaluator overlap.
- PROOF.md listing exact commands and actual observed outputs.
- PORTFOLIO_CASE_STUDY.md in Dave's direct, restrained voice.
- DEMO_SCRIPT.md for a 2–5 minute failure-and-recovery demonstration.
- Machine-readable receipts and benchmark JSON.

## Proof gates
- All tests pass from a clean local environment.
- Demo runs end to end without credentials.
- Held-out benchmark executes and writes truthful metrics.
- Duplicate-effect test proves one accepted effect after uncertain response and retry.
- Planted corruptions and unsupported cases produce zero false accepts.
- Approval-required cases cannot execute without an attributable human action; automated tests may assert blocking but may not invent approval.
- Identical evidence compiles to byte-identical canonical verdict JSON/hash.
- Service health works and state survives a container/process restart.
- No secrets or client identifiers appear in tracked files.

## Stop conditions
Stop and report `BLOCKED` rather than weakening a gate if the host cannot run required tooling, a dependency cannot be installed, or a live-provider test is impossible. The offline recorded-model route is required; live inference is optional and must be labeled unexercised unless actually run.

## Handoff
Leave the repository runnable and clean enough for an independent read-only verifier. Do not publish, push, deploy externally, or change Git remotes.
