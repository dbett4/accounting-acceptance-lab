"""Generate fictional workbooks and retain actual CLI results for every scenario."""
import argparse
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys

from aal.evaluation import digest, load

ROOT = Path(__file__).resolve().parents[1]


def run(destination):
    import openpyxl

    fixtures = load(ROOT / "examples/accrual-synthetic-scenarios.json")
    template = load(ROOT / "examples/accrual-workbook-mapping.json")
    destination.mkdir(parents=True, exist_ok=False)
    # Preserve the authored expectations before running any evaluation.
    (destination / "expectations.json").write_text(json.dumps(fixtures, indent=2) + "\n")
    members = []
    for scenario in fixtures["scenarios"]:
        directory = destination / scenario["id"]
        directory.mkdir()
        mapping = copy.deepcopy(template)
        mapping["case_id"] = scenario["id"]
        mapping["support_as_of"] = scenario["support_as_of"]
        mapping["history_complete"] = scenario["history_complete"]
        mapping["checks"].append({"ref": {"sheet": "Accrual", "cell": "D2"}, "equals": scenario["support_as_of"]})
        if scenario["already_reflected"] is None:
            mapping["already_reflected"] = None
        (directory / "mapping.json").write_text(json.dumps(mapping, indent=2) + "\n")
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Accrual"
        for row in [
            ["Synthetic prior-year revenue accrual", None, None, mapping["period_end"]],
            ["Reversal", Decimal(scenario["reversal"]), "2025-07-01", scenario["support_as_of"]],
            ["Prior-year portion collected", -Decimal(scenario["collection"]), "2025-08-20"],
            ["Outstanding A", Decimal(scenario["outstanding"][0])],
            ["Outstanding B", Decimal(scenario["outstanding"][1])],
            ["Already included", None if scenario["already_reflected"] is None else Decimal(scenario["already_reflected"])],
        ]:
            sheet.append(row)
        book.save(directory / "source.xlsx")
        book.close()
        command = [sys.executable, "-m", "aal.cli", "reconcile-accrual",
                   "--workbook", str(directory / "source.xlsx"),
                   "--mapping", str(directory / "mapping.json"),
                   "--additional-adjustment", scenario["proposed_additional"],
                   "--producer", "synthetic-demo-not-human-review",
                   "--out", str(directory / "result.json"), "--format", "text"]
        process = subprocess.run(command, capture_output=True, text=True,
                                 env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))
        (directory / "stdout.txt").write_text(process.stdout)
        (directory / "stderr.txt").write_text(process.stderr)
        observed = None
        if (directory / "result.json").exists():
            result = load(directory / "result.json")
            observed = {"outcome": result["outcome"],
                        "total_addback": result["reconciliation"]["total_addback"],
                        "additional": result["reconciliation"]["expected_additional_adjustment"],
                        "unresolved": {c["id"]: c["status"] for c in result["checks"] if c["status"] != "pass"}}
        expected = scenario["expected"]
        expected_exit = {"checks_passed_within_scope": 0, "checks_failed": 1, "review_required": 3}[expected["outcome"]]
        matched = observed == expected and process.returncode == expected_exit
        members.append({"id": scenario["id"], "treatment": scenario["treatment"], "expected": expected,
                        "observed": observed, "exit_code": process.returncode, "matches_expectation": matched})
        print(f"{scenario['id']}: {observed['outcome'] if observed else 'execution_error'}; expectation {'matched' if matched else 'MISMATCH'}", flush=True)
    summary = {"provenance": fixtures["provenance"], "expectations_hash": digest(fixtures),
               "total": len(members), "matched": sum(m["matches_expectation"] for m in members), "members": members,
               "no_claim": "Synthetic regression results only, not human approval, independent accounting verification, or client performance."}
    (destination / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{summary['matched']}/{summary['total']} expectations matched. Files: {destination}")
    return 0 if summary["matched"] == summary["total"] and members else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory; existing directories are never overwritten")
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.out.resolve()))
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
