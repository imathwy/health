import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from healthlog.tracking import tracking_template


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliIntegrationTests(unittest.TestCase):
    def test_render_verify_and_summary_share_one_private_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "config").mkdir()
            record_dir = root / "data" / "daily" / "20260102"
            record_dir.mkdir(parents=True)
            profile = {
                "schema_version": 1,
                "targets": {
                    "energy_kcal": {key: [1800, 2200] for key in ("unknown", "rest", "strength", "swim", "tennis", "mixed")},
                    "protein_g": [90, 130],
                    "carbohydrate_g": {key: [200, 300] for key in ("unknown", "rest", "strength", "swim", "tennis", "mixed")},
                    "fat_g": [50, 80],
                    "fiber_g": [25, 35],
                    "sodium_mg_max": 2000,
                },
                "pipeline": {
                    "shortcut_name": "test",
                    "daily_records_directory": "data/daily",
                    "runtime_directory": "runtime",
                    "site_directory": "site",
                },
            }
            (root / "config" / "health_profile.json").write_text(json.dumps(profile))
            analysis = {
                "schema_version": 3,
                "date": "2026-01-02",
                "day_context": {"day_type": "rest", "training_notes": "", "photo_coverage": "partial", "notes": []},
                "images": [],
                "meals": [
                    {
                        "id": "manual",
                        "label": "Manual meal",
                        "time": "12:00",
                        "images": [],
                        "protein_target_applicable": True,
                        "tracking_tags": ["heme_iron"],
                        "notes": [],
                        "items": [
                            {
                                "name": "Measured meal",
                                "portion": "one serving",
                                "nutrition": {
                                    "kcal": [500, 600],
                                    "protein_g": [30, 40],
                                    "carbohydrate_g": [50, 70],
                                    "fat_g": [15, 25],
                                    "fiber_g": [5, 8],
                                    "sodium_mg": [400, 700],
                                },
                                "optional_nutrients": {},
                                "confidence": "medium",
                                "evidence": {
                                    "portion_method": "manual_serving",
                                    "nutrition_source": "manual",
                                    "references": [],
                                    "notes": [],
                                },
                            }
                        ],
                    }
                ],
                "tracking": tracking_template(),
                "assessment": {"summary": [], "strengths": [], "gaps": [], "next_actions": [], "supplement_note": ""},
                "assumptions": [],
                "overall_confidence": "medium",
            }
            analysis["tracking"]["observations"]["direct_water_ml"] = {
                "range": [1600, 1800],
                "source": "user_reported",
                "coverage": "complete",
                "notes": [],
            }
            (record_dir / "analysis.json").write_text(json.dumps(analysis))
            environment = dict(os.environ, HEALTHLOG_ROOT=str(root))

            for command in (
                ["prepare", "2026-01-02", "--skip-export"],
                ["render", "2026-01-02"],
                ["verify", "2026-01-02"],
                ["summary", "--days", "7", "--end", "2026-01-02"],
                ["dashboard"],
            ):
                completed = subprocess.run(
                    [str(PROJECT_ROOT / "bin" / "diet"), *command],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

            self.assertTrue((root / "runtime" / "state" / "healthlog.sqlite3").is_file())
            self.assertTrue((root / "site" / "index.html").is_file())
            self.assertTrue((root / "site" / "daily" / "20260102" / "index.html").is_file())
            self.assertTrue((root / "site" / "nutrition" / "20260102-7d.html").is_file())
            self.assertIn(
                "饮水、钙、恢复与训练",
                (root / "site" / "daily" / "20260102" / "index.html").read_text(),
            )
            self.assertIn(
                "体重与围度变化",
                (root / "site" / "nutrition" / "20260102-7d.html").read_text(),
            )
            self.assertFalse(any((root / "data").rglob("*.html")))
            self.assertFalse(any((root / "runtime").rglob("*.html")))

            shutil.rmtree(root / "runtime")
            rebuilt = subprocess.run(
                [str(PROJECT_ROOT / "bin" / "diet"), "rebuild-db"],
                cwd=PROJECT_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr + rebuilt.stdout)
            self.assertTrue((root / "runtime" / "state" / "healthlog.sqlite3").is_file())
            self.assertTrue(
                (
                    root
                    / "runtime"
                    / "daily"
                    / "20260102"
                    / "pipeline"
                    / "manifest.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
