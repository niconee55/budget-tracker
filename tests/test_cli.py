from __future__ import annotations

import csv
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "emails"
ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_scan_directory_writes_expected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()

            for filename in [
                "01_income.txt",
                "02_utility.txt",
                "03_grocery.txt",
                "04_transportation.txt",
                "05_eating_out.txt",
                "06_healthcare.txt",
                "07_personal_care.txt",
                "08_housing.txt",
                "09_entertainment.txt",
            ]:
                (input_dir / filename).write_bytes((FIXTURES / filename).read_bytes())

            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "budget_tracker.cli",
                    "--input-dir",
                    str(input_dir),
                    "--output-json",
                    str(output_dir / "transactions.json"),
                    "--summary-csv",
                    str(output_dir / "summary.csv"),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            transactions_path = output_dir / "transactions.json"
            self.assertTrue(transactions_path.exists())
            self.assertTrue((output_dir / "summary.csv").exists())

            transactions = json.loads(transactions_path.read_text())
            self.assertEqual(len(transactions), 9)
            self.assertEqual(
                {item["category"] for item in transactions},
                {
                    "Monthly Income",
                    "Utilities",
                    "Groceries",
                    "Transportation",
                    "Eating Out",
                    "Healthcare",
                    "Clothes/Personal Care",
                    "Housing Supplies",
                    "Entertainment",
                },
            )
            for entry in transactions:
                self.assertIn("date", entry)
                self.assertIn("merchant", entry)
                self.assertIn("amount", entry)
                self.assertIn("category", entry)
                self.assertIn("source_file", entry)
                self.assertTrue(entry["source_file"].endswith((".txt", ".eml")))

            with (output_dir / "summary.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            summary = {row["category"]: row["total"] for row in rows}
            self.assertEqual(summary["Groceries"], "138.42")
            self.assertEqual(summary["Entertainment"], "15.99")
