import tempfile
import unittest
from datetime import date
from pathlib import Path

from healthlog.store import NutritionStore


class NutritionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "healthlog.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sample(self, kcal_low: float = 500) -> tuple[dict, dict, dict]:
        analysis = {
            "schema_version": 2,
            "date": "2026-01-02",
            "day_context": {"day_type": "rest", "training_notes": "", "photo_coverage": "partial"},
            "overall_confidence": "medium",
            "assessment": {"summary": ["sample"]},
            "assumptions": ["sample"],
            "images": [
                {"file": "meal.jpg", "classification": "consumed_food", "meal_id": "lunch", "observations": [], "uncertainties": []}
            ],
            "meals": [
                {
                    "id": "lunch",
                    "label": "Lunch",
                    "time": "12:00",
                    "images": ["meal.jpg"],
                    "notes": [],
                    "items": [
                        {
                            "name": "Rice bowl",
                            "portion": "one bowl",
                            "confidence": "medium",
                            "nutrition": {
                                "kcal": [kcal_low, kcal_low + 100],
                                "protein_g": [20, 30],
                                "carbohydrate_g": [60, 80],
                                "fat_g": [10, 20],
                                "fiber_g": [4, 7],
                                "sodium_mg": [500, 900],
                            },
                            "optional_nutrients": {"potassium_mg": [300, 500]},
                            "evidence": {
                                "portion_method": "visual_estimate",
                                "nutrition_source": "recipe_estimate",
                                "references": [],
                                "notes": [],
                            },
                        }
                    ],
                }
            ],
        }
        manifest = {"asset_count": 1, "assets": [{"file": "meal.jpg", "sha256": "abc"}]}
        totals = {
            "kcal": [kcal_low, kcal_low + 100],
            "protein_g": [20, 30],
            "carbohydrate_g": [60, 80],
            "fat_g": [10, 20],
            "fiber_g": [4, 7],
            "sodium_mg": [500, 900],
        }
        return analysis, manifest, totals

    def test_upsert_is_idempotent_and_preserves_provenance(self) -> None:
        analysis, manifest, totals = self.sample()
        with NutritionStore(self.db_path) as store:
            for digest in ("first", "second"):
                store.upsert_day(
                    analysis=analysis,
                    manifest=manifest,
                    analysis_path="data/daily/20260102/analysis.json",
                    analysis_sha256=digest,
                    totals=totals,
                    targets={},
                    comparisons=[],
                )
            state = store.day_state("2026-01-02")
            stats = store.database_stats()
            provenance = store.provenance_counts(
                date(2026, 1, 1),
                date(2026, 1, 3),
            )

        self.assertIsNotNone(state)
        self.assertEqual(state["analysis_sha256"], "second")
        self.assertEqual(stats["day_count"], 1)
        self.assertEqual(stats["food_item_count"], 1)
        self.assertEqual(state["nutrients"]["potassium_mg"]["covered_items"], 1)
        self.assertEqual(provenance, {"recipe_estimate": 1})


if __name__ == "__main__":
    unittest.main()
