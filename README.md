# Accounting Acceptance Lab

[![CI](https://github.com/dbett4/accounting-acceptance-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/dbett4/accounting-acceptance-lab/actions/workflows/ci.yml)

A credential-free, synthetic reconciliation lab for testing whether an accounting agent can produce evidence-linked proposals without silently widening authority or duplicating ledger effects.

## Try an accounting review

The main example checks a revenue accrual addback against an independently supplied
outstanding schedule. It distinguishes the total addback from the additional amount
needed after an adjustment is already reflected in the report.

```text
Reversed prior-year accrual       2,400.75
Less allocated collections         525.25
Total addback                    1,875.50
Less already reflected             625.10
Additional amount                1,250.40
```

The same workflow reads five fictional XLSX workpapers and catches different mistakes:

| Scenario | Expected behavior |
|---|---|
| Part of the addback already exists | Accept only the remaining 1,250.40. |
| The entire addback already exists | Reject a second 1,300.65 addback. |
| September support for a December balance | Request cutoff evidence even though the numbers tie. |
| Existing adjustment is unknown | Preserve the unknown; do not assume zero. |
| Outstanding schedule differs by one cent | Retain the difference and fail the tie-out. |

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install ".[xlsx]"
PYTHONPATH=src python3 scripts/accrual_demo.py --out .state/accrual-demo-01
```

The runner retains the authored expectations, source workbooks, source hashes and
individual results. Its successful exit means the observed outcomes match the
expectations, including the deliberate failures and evidence gaps. It does not mean
all five proposals passed. Run again with a new output directory to preserve the
first result.

## What this demonstrates

- Accounting-specific checks that distinguish an outstanding amount from a new adjustment.
- Reusable workbook intake with explicit scope, cutoff, source coordinates and layout checks.
- Separate outcomes for a failed calculation and an unresolved evidence question.
- A separate duplicate-reversal evaluator and a synthetic ledger control/recovery lab.

These are authored synthetic cases. The tool does not post a reporting adjustment,
authenticate evidence or supply accounting-policy judgment. It is not a complete
cash-flow statement or an ASC 230 compliance review. The [scope and acceptance
history](docs/SYNTHETIC-SCOPE.md) records the exact completion boundaries.

## Related reporting tools

[Regulated Reporting MCP](https://github.com/dbett4/regulated-reporting-mcp) supplies
a guarded reporting API and a credential-free simulated Workiva demo. This lab
checks accounting proposals. They are separate runnable tools; no integration or
shared approval authority is implied.

## Inspect or correct a proposal

Each scenario directory retains `result.json`, `stdout.txt` and `stderr.txt`.
The root `expectations.json` preserves the authored answers before evaluation;
`summary.json` compares amounts, outcomes, unresolved checks and exit codes.
The runner exits 0 when expectations match, 1 for a mismatch or member CLI
failure, and 2 for a setup error. It never overwrites an existing output directory.

To try a correction without changing the frozen scenario or earlier result:

```bash
PYTHONPATH=src python3 -m aal.cli reconcile-accrual \
  --workbook .state/accrual-demo-01/SYN-A02/source.xlsx \
  --mapping .state/accrual-demo-01/SYN-A02/mapping.json \
  --additional-adjustment 0.00 --producer synthetic-correction \
  --out .state/accrual-demo-01/SYN-A02/corrected.json --format text
```

The original double-addback fails; this zero-additional proposal passes against the
same workbook and mapping. This is a proposal correction, not a review override.

`reconcile-accrual` checks one scoped reporting adjustment without opening a ledger,
calling a provider, or posting. It distinguishes the total addback from the **additional**
amount needed after an adjustment already included in the reporting workbook:

```text
Prior-year accrual reversed during the reporting period
− collections allocated to that prior-year accrual
= total revenue addback (must tie to an independent outstanding schedule)
− addback already reflected in the selected reporting surface
= additional adjustment proposed now
```

```bash
mkdir -p .state/evaluations
PYTHONPATH=src python3 -m aal.cli reconcile-accrual \
  --case examples/accrual-case.json --proposal examples/accrual-proposal.json \
  --out .state/evaluations/accrual.json --format text
```

Copy the **synthetic** examples for the exact v1 schema. All fields are required;
unknown fields, duplicate item IDs, and duplicate JSON keys are invalid. `scope`
identifies one entity/fund/revenue-account/reporting surface, supplied by the case
preparer. All rows must belong to that scope and currency; the evaluator cannot
infer their classification from prose. Dates are ISO dates. Amounts are nonnegative
fixed two-decimal strings, except `additional_adjustment`, which also permits a minus
sign to reduce an over-reflected addback. Positive adjustments increase revenue.

`reversal` describes a prior-year revenue accrual reversed in the reporting period.
`collections` contains only the supported prior-year portions of receipts, not whole
receipts that also include current-year revenue. `outstanding` is a separately prepared
ending schedule; an empty list asserts zero outstanding. Every amount carries a source
reference. `already_reflected: null` means unknown, **not zero**; otherwise it identifies
the amount already included in this particular reporting surface, not necessarily a
posted journal. Do not combine reporting-formula adjustments and ledger postings as
though they were separate effects without reconciling their relationship.

An amount disagreement or out-of-period activity fails. Stale or later-dated support,
unknown existing adjustments, or `history_complete: false` requires review. A pass
requires support and proposal dates equal to the reporting cutoff. Completeness,
allocation, scope, and source authenticity remain supplied assertions, not facts the
code can discover. No review override is available. This command uses the same exit
codes (0/1/2/3) and create-only JSON output convention as the reversal evaluator;
it is not yet supported by the batch, comparison, or review-disposition commands.

The example's $920.50 reversal less $120.25 collection ties to $800.25 outstanding;
with $200.00 already included, only $600.25 remains. Regression tests exercise partial
and excessive existing adjustments, unknown amounts, one-cent source disagreement,
scope/date boundaries, stale evidence, invalid inputs, and output preservation.
These checks do not establish accounting-policy compliance or human review-time savings.
Keep client-derived cases outside Git, even when identifiers have been replaced.

### Read a workbook without preparing case/proposal JSON

Install the optional XLSX dependency in your local virtual environment:

```bash
python -m pip install ".[xlsx]"
PYTHONPATH=src python -m aal.cli reconcile-accrual \
  --workbook /private/workpaper.xlsx --mapping /private/reviewed-layout.json \
  --additional-adjustment 0.00 --producer "$USER" \
  --out /private/new-result.json --format text
```

The mapping is configured once for a supported layout, then reused for files with
that layout and reporting scope. `examples/accrual-workbook-mapping.json` documents
the exact synthetic layout contract: sheet `Accrual`, labels in A1:A6, cutoff text
in D1, amounts in B2:B6, and dates in C2:C3. Tests generate actual XLSX workbooks
with different amounts against this same mapping; no client file is bundled.

Each amount maps to one or more numeric literal cells and an explicit `sign` of 1
or -1. Dates map to ISO date text or Excel dates at midnight. Text amounts, blanks,
booleans, excess precision, and formula amounts are rejected rather than coerced,
rounded, or treated as zero. Each source amount must be below one trillion in
absolute value. For a reviewed sum, map its literal components and put the exact
controlling formula in `checks`. Formula checks compare text only: **this is not an
Excel calculation engine, and formula caches are never trusted.** Unsupported
formulas require a reviewed extraction strategy, not a bypass.

Use `checks` to pin the meaningful labels, fund/account identities, period headings,
allocation explanations, and formula links. Changed checked values stop intake with
exit 2. At least one check is required, but the code cannot decide whether your
chosen checks adequately establish the layout or accounting meaning. Notes, scope,
cutoff dates, signs, and completeness remain preparer assertions requiring review.
A new period or layout requires reviewing the mapping; do not blindly update checks
to match a changed workbook. This first intake supports XLSX, not legacy XLS or CSV.

The proposal date is the mapping's reporting cutoff, not a journal posting date.
`--producer` is a locally supplied name, not reviewer authentication. JSON mode
(`--case` plus `--proposal`) remains available; mixing the two input modes is invalid.
Output contains the extracted case/proposal, selected cell observations, exact mapping,
mapping hash, and hash of the complete workbook byte snapshot. A workbook change,
even outside the selected cells, changes the case identity. Retain the original
private workbook with the result; hashes detect changes, not source authenticity.

Synthetic intake exercises S2. Real-file trials and independent accountant acceptance
remain separate, deferred work; synthetic passes do not close those former pilot goals.

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

The demo uses only the Python standard library and writes local state and receipts
under `.state/`. The full local gate also tests XLSX intake; install `.[xlsx]` first:

```bash
python -m pip install ".[xlsx]"
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
