# Accounting Acceptance Lab

I built this lab to show the part of agent engineering that a polished answer usually hides: whether the answer is supported, authorized, safe to apply, and recoverable when the transport result is uncertain.

The workflow is deliberately small and synthetic. It normalizes accounting inputs, handles exact reconciliation without model judgment, retrieves policy for exceptions, validates recorded model proposals, routes unsafe cases, records human decisions separately from effects, and writes only through a bounded local ledger. Every accepted effect has a semantic identity and native readback.

The useful failure case is an adjustment that commits and then returns an unknown result. A naive retry with a new transport key creates two effects. The controlled path looks up the semantic key before retrying and accepts the one existing effect. That is the distinction between retry logic and effect-safe recovery.

I also wanted the retrieval comparison to say something. The original held-out set let lexical and hybrid retrieval both score 1.0. I added ordinary accounting paraphrases and an adversarial unsupported phrasing. Lexical-only scored 10/16. The offline hybrid and reranked path scored 16/16 on this small fixture. The result is reproducible and useful as a regression test, but it is not evidence of production retrieval quality.

The latest offline demo compiled 12 cases: one deterministic match, one effect-safe recovery, two cases blocked pending human approval, and eight abstentions or review routes. The held-out path separately executed retrieval, recorded proposals, and the rule gate before consulting labels for scoring: four outputs were accepted, with zero false or unsupported accepts. No fixture claims that a person approved a case. Approval in the CLI records a decision bound to the exact proposal and action; it does not apply an effect.

The lab runs without credentials, uses only synthetic data, exposes a localhost operator view and health endpoint, persists SQLite state across restart, and ships as a non-root container. The optional OpenAI-compatible route is tested against a local fake HTTP server for protocol, malformed output, and citation fabrication. I did not call a live provider and do not present the rule-based verifier as an independent external review.

The main limitations are direct: 16 held-out rows, a hand-authored English concept lexicon, recorded rather than live model responses, no observed human-review timing, no provider-billed cost, no cryptographic signing, and no production accounting system. The artifact demonstrates control design and executable evidence, not production readiness.
