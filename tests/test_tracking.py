import unittest
from datetime import date

from healthlog.analysis import analysis_template, validate_analysis
from healthlog.tracking import (
    daily_observation_rows,
    effective_tracking,
    meal_protein_rows,
)


def food_item(*, protein: list[float], calcium: list[float] | None = None) -> dict:
    optional = {"calcium_mg": calcium} if calcium is not None else {}
    return {
        "name": "Test food",
        "portion": "one serving",
        "nutrition": {
            "kcal": [200, 250],
            "protein_g": protein,
            "carbohydrate_g": [20, 30],
            "fat_g": [5, 10],
            "fiber_g": [2, 4],
            "sodium_mg": [100, 200],
        },
        "optional_nutrients": optional,
        "confidence": "medium",
        "evidence": {
            "portion_method": "manual_serving",
            "nutrition_source": "manual",
            "references": [],
            "notes": [],
        },
    }


class TrackingTests(unittest.TestCase):
    def analysis(self) -> tuple[dict, dict]:
        manifest = {"date": "2026-01-02", "assets": []}
        analysis = analysis_template(date(2026, 1, 2), manifest)
        analysis["day_context"]["day_type"] = "rest"
        analysis["day_context"]["photo_coverage"] = "partial"
        analysis["meals"] = [
            {
                "id": "lunch",
                "label": "Lunch",
                "time": "12:00",
                "images": [],
                "protein_target_applicable": True,
                "tracking_tags": ["heme_iron"],
                "notes": [],
                "items": [
                    food_item(protein=[18, 24], calcium=[100, 140]),
                    food_item(protein=[8, 12]),
                ],
            }
        ]
        return analysis, manifest

    def test_template_preserves_unknowns_and_validates_meal_tags(self) -> None:
        analysis, manifest = self.analysis()

        errors, warnings = validate_analysis(analysis, manifest)

        self.assertEqual(errors, [])
        self.assertTrue(any("未知值不会按 0 处理" in item for item in warnings))
        self.assertIsNone(
            analysis["tracking"]["observations"]["direct_water_ml"]["range"]
        )

    def test_calcium_is_only_a_partial_known_subtotal(self) -> None:
        analysis, _ = self.analysis()

        tracking = effective_tracking(analysis)
        calcium = tracking["observations"]["calcium_mg"]

        self.assertEqual(calcium["range"], [100.0, 140.0])
        self.assertEqual(calcium["source"], "derived_from_items")
        self.assertEqual(calcium["coverage"], "partial")

    def test_per_meal_protein_is_derived_once(self) -> None:
        analysis, _ = self.analysis()
        profile = {"targets": {"tracking": {"protein_per_meal_g": [20, 40]}}}

        protein = meal_protein_rows(analysis, profile)[0]
        calcium = next(
            row
            for row in daily_observation_rows(analysis, profile)
            if row["key"] == "calcium_mg"
        )

        self.assertEqual(protein["range"], [26.0, 36.0])
        self.assertEqual(protein["status"], "目标内")
        self.assertEqual(calcium["status"], "覆盖不全")


if __name__ == "__main__":
    unittest.main()
