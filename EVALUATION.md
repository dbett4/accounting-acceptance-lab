# Evaluation

`PYTHONPATH=src python3 -m aal.cli benchmark` generated the latest local result in `.state/artifacts/benchmark.json`.

## Held-out execution and scoring

Dataset: `synthetic-heldout-v1`, 16 cases, SHA-256 `613bbe116fa7811550b7869442a28a148004ae12914553b97ca43e9e710b6acb`. Each row separates execution `input` from scoring `labels`, then executes hybrid retrieval, case inference from observed evidence, the recorded proposal backend, citation validation, and an independent rule gate. Only after an accept/review decision exists are `safe_to_accept`, `supported`, and expected-citation labels used to score it. Regression tests change scoring labels without changing observed output and inject conflicting/wrong-action proposals to prove rejection.

| Comparison / metric | Observed result |
|---|---:|
| lexical-only citation accuracy | 10/16 = 0.625 |
| hybrid + reranked citation accuracy | 16/16 = 1.000 |
| accepted-output rate | 4/16 = 0.2500 |
| false-accept rate | 0/4 = 0.0000 |
| unsupported-conclusion rate | 0/3 = 0.0000 |
| duplicate-effect rate, effect-safe retry | 0.0000 |
| first-pass acceptance | 4/16 = 0.2500 |
| human-review count | 12 |
| human-review minutes estimate | 36 |
| recovery success | 1.0000 |
| p50 retrieval/proposal/gate latency | 0.397757 ms |
| p95 retrieval/proposal/gate latency | 0.535163 ms |
| estimated cost per accepted item | USD 0.00 |

Latency values are from one run and vary by host. The receipt run ID is `613bbe116fa78115`; benchmark SHA-256 is `ca3e774c4e63bab792fa36179ae0e29da3b92c103abb4781b908e8440850eb84`.

## Executable comparisons

- **Retrieval:** lexical-only scored 10/16. Hybrid retrieval scored 16/16 using version filters, a fixed accounting synonym projection, character-vector similarity, parent expansion, and reranking.
- **Retry:** blind retry with a new transport key produced two effects. Semantic key plus authorized native read-before-retry recovered one existing effect.
- **Verification:** self-attestation accepted 10 observed proposals and falsely accepted 6 labelled-unsafe rows (0.6). The independent executable gate accepted 4 observed outputs with 0 false accepts and 0 unsupported accepts. It is a local rule surrogate, not external verification.

## Metric definitions

Definitions are emitted with the machine-readable benchmark. Acceptance metrics are computed from observed gate outputs; safety/support labels are applied only to score those outputs. Human-review minutes use a disclosed three-minute planning assumption. Recorded-backend provider cost is zero and is not provider-billed cost.

## Limitations

The corpus is small, synthetic, English-only, and contains a hand-authored accounting concept lexicon. It does not establish production recall, calibration, accounting correctness, or resistance to novel attacks. Recorded responses are not live inference. No human-review duration or provider-billed cost was observed. No external verifier was run. A perfect hybrid score on 16 rows is a fixture regression signal, not a general performance claim.
