# 2–5 minute demo script

## 0:00 — Establish scope

Say: “This is synthetic, offline, and localhost-only. The recorded model provenance is not live inference, and no fixture fabricates human approval.”

```bash
bash scripts/offline_demo.sh
```

Point out the 12 cases, zero false accepts, zero unsupported accepts, and the mix of accepted, blocked, and routed outcomes.

## 0:40 — Show evidence and a blocked proposal

```bash
PYTHONPATH=src python3 -m aal.cli inspect C03
```

Show the retrieved citation and scores, proposal provenance, `pending_approval`, no approval actor, and an empty effects list.

## 1:20 — Record approval without pretending an effect happened

Use your actual local identity; do not copy a fictitious person from this script.

```bash
PYTHONPATH=src python3 -m aal.cli approve C03 --actor "$USER" --reason "reviewed synthetic invoice and policy"
```

Point to `approval_lifecycle.decision_recorded: approved` and `effect_applied: false`. Say: “This decision advances the generation. It does not post anything. Execution would be a separate bounded action carrying the new generation and semantic idempotency key.”

## 2:00 — Demonstrate failure and recovery

```bash
PYTHONPATH=src python3 -m aal.cli inspect C12
PYTHONPATH=src python3 -m aal.cli replay C12:timing:v1
```

Explain that the initial write committed but its response was classified `effect_unknown`. Replay performs native read-before-retry and returns `accepted_existing_effect`; the ledger still contains one effect.

## 2:50 — Run the discriminative benchmark

```bash
PYTHONPATH=src python3 -m aal.cli benchmark
```

Show:

- lexical-only citation accuracy: 0.625 (10/16)
- hybrid+rered/reranked citation accuracy: 1.0 (16/16)
- naive retry effects: 2; effect-safe effects: 1
- self-attested false-accept rate: 0.6 (6/10); independent rule gate: 0
- final false accepts and unsupported accepts: 0

Say: “The perfect hybrid score is only for this small synthetic fixture. The verifier is executable independent logic, not third-party evidence.”

## 3:40 — Close on proof

```bash
bash scripts/proof.sh
```

Show the test count, process restart receipt, secret scan, and `ALL_LOCAL_PROOF_GATES_PASSED`. If time permits, open `http://127.0.0.1:8080/` after running `PYTHONPATH=src python3 -m aal.cli serve` to show evidence, proposal, approval, readback, and verdict surfaces.
