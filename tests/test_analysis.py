import unittest
from datetime import date

from healthlog.analysis import analysis_template, validate_analysis


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


if __name__ == "__main__":
    unittest.main()
