# Evaluation

`PYTHONPATH=src python3 -m aal.cli benchmark` generated the latest local result in `.state/artifacts/benchmark.json`.

## Held-out result

Dataset: `synthetic-heldout-v1`, 16 cases, SHA-256 `460536de7621071c96e33d1a382fef69e2f78335ef25e1181238e888ba089470`. Demo cases and held-out rows are separate, while policies are intentionally shared. Six added challenges use natural accounting paraphrases, including one unsupported/adversarial paraphrase; labels describe the applicable policy rather than the retrieval implementation.

| Comparison / metric | Observed result |
|---|---:|
| lexical-only citation accuracy | 10/16 = 0.625 |
| hybrid + reranked citation accuracy | 16/16 = 1.000 |
| accepted-output rate | 5/16 = 0.3125 |
| false-accept rate | 0.0000 |
| unsupported-conclusion rate | 0.0000 |
| duplicate-effect rate, effect-safe retry | 0.0000 |
| first-pass acceptance | 5/16 = 0.3125 |
| human-review count | 11 |
| human-review minutes estimate | 33 |
| recovery success | 1.0000 |
| p50 local retrieval/gate latency | 0.383354 ms |
| p95 local retrieval/gate latency | 0.446450 ms |
| estimated cost per accepted item | USD 0.00 |

Latency values are from one run and will vary by host. The receipt for this run is `.state/artifacts/benchmark_receipt.json`, run ID `460536de7621071c`, benchmark SHA-256 `fedb0254edf023ddb7756e7e436ea5970f3dc863e227bc5f8f22b63bc77c08df`.

## Executable comparisons

- **Retrieval:** lexical-only uses exact token evidence. Hybrid adds a fixed, label-independent accounting synonym projection, character-vector similarity, coverage/phrase reranking, version filters, and parent expansion. The paraphrase cases make the comparison discriminative rather than preserving a 1.0/1.0 ceiling.
- **Retry:** blind retry with a new transport key produces two effects (duplicate-effect rate 1.0). Semantic key plus native read-before-retry recovers one existing effect (one effect, rate 0.0, recovery success 1.0).
- **Verification:** self-attestation accepts 13 and falsely accepts 8 (rate 0.6153846154). The executable independent rule gate accepts 5 and falsely accepts 0. This gate is independent code, but it is not an external human or third-party verifier.

## Metric definitions

- **Accepted-output rate:** accepted terminal outputs / held-out cases.
- **False-accept rate:** unsafe accepted outputs / accepted outputs; zero when none are accepted.
- **Unsupported-conclusion rate:** accepted planted unsupported cases / planted unsupported cases.
- **Citation accuracy:** top citations matching held-out labels / held-out cases.
- **Duplicate-effect rate:** effects beyond one / action scenarios.
- **First-pass acceptance:** safe cases accepted without review or retry / held-out cases.
- **Human-review count:** cases routed to attributable human review.
- **Human-review minutes estimate:** review count × a disclosed synthetic three-minute planning assumption.
- **Recovery success:** unknown-effect scenarios recovered with exactly one effect / unknown-effect scenarios.
- **p50/p95 latency:** nearest-rank percentiles of measured local retrieval/gate latency.
- **Estimated cost per accepted item:** estimated provider cost / accepted items. Recorded backend cost is zero, not provider-billed cost.

## Limitations

The corpus is small, synthetic, English-only, and contains a hand-authored accounting concept lexicon. It does not establish production recall, calibration, accounting correctness, or resistance to novel attacks. Recorded responses are not live inference. No human-review duration was observed. No provider-billed cost was measured. No external verifier was run. A perfect hybrid score on 16 rows is a regression signal for this fixture, not a general performance claim.
