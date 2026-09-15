# Accounting Acceptance Lab

[![CI](https://github.com/dbett4/accounting-acceptance-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/dbett4/accounting-acceptance-lab/actions/workflows/ci.yml)

A credential-free, synthetic reconciliation lab for testing whether an accounting agent can produce evidence-linked proposals without silently widening authority or duplicating ledger effects.

## Evaluate an external duplicate-reversal proposal

The offline evaluator accepts your own JSON files, independently of bundled demo
case IDs and recorded model answers. It checks one USD duplicate-posting reversal.
It never opens the ledger, calls a model, or posts an entry.

```bash
export PYTHONPATH=src
mkdir -p .state/evaluations
python3 -m aal.cli evaluate --case examples/reversal-case.json \
  --proposal examples/reversal-proposal.json --out .state/evaluations/initial.json
# Exit 3 is expected: the example requires a reviewer disposition.
```

After inspecting the sources, a reviewer can record a local decision, then evaluate
again. This command records the supplied identity; it does not authenticate it.

```bash
python3 -m aal.cli review-proposal --case examples/reversal-case.json \
  --proposal examples/reversal-proposal.json --actor "$USER" \
  --decision approve --reason "Reviewed the synthetic example sources" \
  --out .state/evaluations/review.json
python3 -m aal.cli evaluate --case examples/reversal-case.json \
  --proposal examples/reversal-proposal.json --review .state/evaluations/review.json \
  --out .state/evaluations/reviewed.json
python3 -m aal.cli compare .state/evaluations/initial.json .state/evaluations/reviewed.json
```

Copy the examples to supply a new case and proposal. Every shown field is required;
unknown fields and duplicate JSON keys are rejected. `schema_version` is integer 1.
Amounts are nonnegative strings with exactly two decimal places (maximum 12 integer
digits); each line has exactly one positive debit or credit. Dates are ISO dates.
Journal IDs are unique within a case; line IDs are unique within a journal.
`target_id` is the posting to reverse; `duplicate_of` is the posting to retain.
The reversal is checked by debit/credit totals per account, allowing split lines.
Proposal date must be in the case period and not precede either source posting.

**Evidence contract:** `purchase_id` is a supplied business-event identity, not an
inferred match. An evidence record with `kind: duplicate_confirmation` must name
exactly the two journal IDs and be cited by the proposal to support duplication.
Any `distinct_purchases` record for that pair fails the check even if uncited.
Other kinds (such as `invoice`) supply context only. Free text is retained and
hashed, but is not interpreted: contradictory prose must be classified by the case
preparer. A typed confirmation is an assertion in the supplied evidence, not proof
of authenticity. Keep case preparation separate from proposal generation.

`prior_reversals` lists journal IDs already reversed. Either member of the pair
being reversed fails this narrow workflow. `history_complete: false` leaves the
check indeterminate. This declaration covers supplied reversal history through the
proposal date; the evaluator cannot independently establish completeness.
The only configurable v1 policy is `require_review`; the named version and all case
content are hashed. Core checks cannot be disabled through the policy input.

Results include expected/observed values, source references, and every unresolved
finding. Outcomes and CLI exit codes are **checks passed within scope (0)**,
**checks failed (1)**, **invalid input/output error (2)**, and **review required (3)**.
Add `--format text` to `evaluate` or `compare` for a readable terminal summary:
failed account amounts and remaining evidence gaps are shown directly. JSON remains
the default stdout format, and `evaluate --out` always saves the full JSON result
regardless of display format. Text output escapes control characters in input data.
An approval cannot override failed or indeterminate accounting checks. A review
binds the exact case, policy, proposal, and evaluator version; changed inputs require
a new disposition. Output files are create-only: use a new filename for every run.

`compare` reports changed observations as well as changed statuses, remaining
unresolved findings, and review changes separately from proposal changes. Different
case/policy/evaluator hashes set `comparable: false`; this is not a proposal-only
improvement. Result hashes detect accidental modification, not malicious forgery
or authenticated provenance. Input array order is part of canonical identity.

**Limits:** passing is conditional on supplied structured evidence and policy. It
does not certify accounting truth, authorize posting, or prove an applied effect.
The examples and automated tests are synthetic regression evidence. A separate
agent authored 10 proposals without reading the implementation or tests; the first
execution produced 3 passes, 5 failures, and 2 review-required outcomes. The latter
two disagreed with the author's frozen failure labels: missing evidence and
incomplete history are indeterminate under the pre-existing contract. Original
labels and hashes are preserved under `tests/fixtures/blind_reversals`; separate
test adjudications explain those disagreements without rewriting the manifest.
The erroneous/corrected account-allocation pair is checked against identical case
and policy hashes. These inputs are now regression fixtures, not fresh blind
evidence or proof of real-client performance or review-time savings.

Run that regression alone with:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_blind_reversals.py -v
```

## Compare proposal batches

Use `evaluate-batch` to evaluate all proposals from a candidate, then
`compare-batches` to compare saved runs by stable member ID. A manifest declares
the complete member set before execution; it is not a scoring-label file:

```json
{
  "schema_version": 1,
  "name": "candidate-v1",
  "members": [
    {"id": "office-duplicate", "case": "case.json", "proposal": "proposal.json", "review": null}
  ]
}
```

All fields above are required; `review` is either null or a disposition JSON path.
Paths are relative to the manifest directory and must remain inside it, including
symlink targets. Empty batches, duplicate IDs, unknown fields, and malformed
manifests are rejected. Missing or invalid member inputs instead remain visible as
`invalid_input` members in a saved result; other members still execute. Every
declared member contributes to `summary.total`, including errors. A shared case
file is read once per run. Keep source files unchanged during execution: this is
not a transactional filesystem snapshot. Preserve the source files with the result.

Runnable example using three existing synthetic regression cases:

```bash
export PYTHONPATH=src
TRIAL=tests/fixtures/blind_reversals
mkdir -p .state/evaluations
python3 -m aal.cli evaluate-batch --manifest "$TRIAL/batch-before.json" \
  --out .state/evaluations/batch-before.json --format text
# Expected exit 1: one pass, one failure, one review-required member.
python3 -m aal.cli evaluate-batch --manifest "$TRIAL/batch-after.json" \
  --out .state/evaluations/batch-after.json --format text
# Expected exit 3: two passes, no failures, one review-required member.
python3 -m aal.cli compare-batches .state/evaluations/batch-before.json \
  .state/evaluations/batch-after.json --format text
```

Create a new manifest for each candidate using the same member IDs and case files,
but that candidate's proposal paths. Comparisons expose added/removed members,
input errors, changed case/policy/evaluator hashes, review changes, corrected checks,
and remaining unresolved findings. A dropped member or changed basis sets
`comparable: false`; reordering alone does not. A successful comparison command
(exit 0) means the comparison ran, not that a candidate improved or passed.

Batch evaluation exits 2 for any invalid member, otherwise 1 for any failed check,
otherwise 3 for any review-required member, otherwise 0. Invalid manifests and
output errors also exit 2. Outputs are create-only JSON, with optional readable
stdout. Hashes detect accidental modification; they do not authenticate inputs.
**These counts are check outcomes, not accuracy scores or evidence of time saved.**

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

Both HTTP servers join active request threads before closing their SQLite connection,
so an already-sent response does not lose its trailing event write during shutdown.
Sockets have a 10-second inactivity timeout; this is not a total request deadline.
When embedding a server running in another thread, call `shutdown()` before
`server_close()` to stop accepting requests before draining them.

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
