import unittest
from datetime import date

from healthlog.analysis import (
    analysis_template,
    image_classification_counts,
    validate_analysis,
)


def valid_item() -> dict:
    return {
        "name": "Test meal",
        "portion": "one serving",
        "nutrition": {
            "kcal": [400, 500],
            "protein_g": [25, 35],
            "carbohydrate_g": [40, 60],
            "fat_g": [10, 20],
            "fiber_g": [4, 8],
            "sodium_mg": [300, 600],
        },
        "optional_nutrients": {},
        "confidence": "medium",
        "evidence": {
            "portion_method": "visual_estimate",
            "nutrition_source": "recipe_estimate",
            "references": [],
            "notes": [],
        },
    }


class AnalysisValidationTests(unittest.TestCase):
    def test_reports_cross_record_errors_and_partial_coverage_warning(self) -> None:
        manifest = {
            "date": "2026-01-02",
            "assets": [{"file": "meal.jpg"}],
        }
        analysis = analysis_template(date(2026, 1, 2), manifest)
        analysis["day_context"]["day_type"] = "rest"
        analysis["day_context"]["photo_coverage"] = "partial"
        analysis["images"][0]["classification"] = "consumed_food"
        analysis["images"][0]["meal_id"] = "missing-meal"

        errors, warnings = validate_analysis(analysis, manifest)

        self.assertIn(
            "meal.jpg 标记为 consumed_food，但 meal_id 无效",
            errors,
        )
        self.assertIn("照片覆盖可能不完整，不能把估算视为完整饮食日志", warnings)

    def test_only_confirmed_consumed_photos_can_link_to_meals(self) -> None:
        manifest = {
            "date": "2026-01-02",
            "assets": [
                {"file": "meal.jpg"},
                {"file": "possible.jpg"},
                {"file": "unrelated.jpg"},
            ],
        }
        analysis = analysis_template(date(2026, 1, 2), manifest)
        analysis["day_context"]["day_type"] = "rest"
        analysis["day_context"]["photo_coverage"] = "partial"
        analysis["meals"] = [
            {
                "id": "lunch",
                "label": "Lunch",
                "time": "12:00",
                "images": ["meal.jpg", "possible.jpg", "unrelated.jpg"],
                "protein_target_applicable": True,
                "tracking_tags": [],
                "notes": [],
                "items": [valid_item()],
            }
        ]
        for image, classification in zip(
            analysis["images"],
            ("consumed_food", "possible_food", "unrelated"),
            strict=True,
        ):
            image["classification"] = classification
            image["meal_id"] = "lunch"

        errors, _ = validate_analysis(analysis, manifest)
        counts = image_classification_counts(analysis)

        self.assertIn(
            "餐次 lunch 引用了非确认摄入图片：possible.jpg (possible_food)",
            errors,
        )
        self.assertIn(
            "餐次 lunch 引用了非确认摄入图片：unrelated.jpg (unrelated)",
            errors,
        )
        self.assertIn(
            "possible.jpg 分类为 possible_food，meal_id 必须为空", errors
        )
        self.assertIn("unrelated.jpg 分类为 unrelated，meal_id 必须为空", errors)
        self.assertEqual(counts["food_related"], 2)
        self.assertEqual(counts["reviewed"], 3)


if __name__ == "__main__":
    unittest.main()
