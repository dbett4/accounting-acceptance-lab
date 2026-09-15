import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AccrualDemoTests(unittest.TestCase):
    def test_full_demo_retains_failures_gaps_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "run"
            command = [sys.executable, str(ROOT / "scripts/accrual_demo.py"), "--out", str(out)]
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            process = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual((summary["total"], summary["matched"]), (5, 5))
            self.assertEqual([m["exit_code"] for m in summary["members"]], [0, 1, 3, 3, 1])
            self.assertEqual([m["observed"]["additional"] for m in summary["members"]], ["1250.40", "0.00", "800.40", None, "1500.04"])
            for member in summary["members"]:
                directory = out / member["id"]
                result = json.loads((directory / "result.json").read_text())
                self.assertEqual(result["intake"]["workbook_sha256"], hashlib.sha256((directory / "source.xlsx").read_bytes()).hexdigest())
                self.assertEqual(result["outcome"], member["observed"]["outcome"])
                self.assertTrue((directory / "stdout.txt").read_text())
            before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
            process = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(before, {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()})
