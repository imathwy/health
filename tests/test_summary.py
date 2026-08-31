import unittest
from datetime import date, timedelta

from healthlog.summary import make_summary
from healthlog.tracking import tracking_template


class SummaryTests(unittest.TestCase):
    def test_missing_days_are_not_counted_as_zero_and_trend_uses_bounds(self) -> None:
        start = date(2026, 1, 1)
        rows = []
        for offset in range(5):
            value = 100 + 10 * offset
            rows.append(
                {
                    "date": (start + timedelta(days=offset)).isoformat(),
                    "day_type": "rest",
                    "photo_coverage": "complete",
                    "overall_confidence": "medium",
                    "comparisons": [],
                    "nutrients": {
                        "kcal": {"low": value, "high": value + 20, "unit": "kcal"},
                        "protein_g": {"low": 20, "high": 30, "unit": "g"},
                    },
                }
            )
        result = make_summary(
            rows=rows,
            start=start,
            end=start + timedelta(days=6),
            requested_days=7,
            provenance={"recipe_estimate": 5},
        )

        self.assertEqual(result["period"]["logged_days"], 5)
        self.assertEqual(len(result["period"]["missing_dates"]), 2)
        self.assertEqual(result["average_daily_ranges"]["kcal"]["low"], 120)
        self.assertEqual(result["average_daily_ranges"]["kcal"]["logged_days"], 5)
        self.assertEqual(result["trends"]["kcal"]["status"], "increasing")
        self.assertEqual(result["trends"]["protein_g"]["status"], "stable")

    def test_tracking_uses_known_days_and_reports_body_change(self) -> None:
        start = date(2026, 1, 1)
        rows = []
        for offset, weight in enumerate((72.0, 71.6)):
            tracking = tracking_template()
            tracking["meal_tagging"] = {
                "source": "photo_review",
                "coverage": "partial",
                "notes": [],
            }
            tracking["body_measurements"]["weight_kg"] = {
                "value": weight,
                "source": "measured",
                "recorded_at": "08:00",
                "context": "before breakfast",
                "notes": [],
            }
            if offset == 0:
                tracking["observations"]["direct_water_ml"] = {
                    "range": [1600, 1800],
                    "source": "user_reported",
                    "coverage": "complete",
                    "notes": [],
                }
            rows.append(
                {
                    "date": (start + timedelta(days=offset)).isoformat(),
                    "day_type": "rest",
                    "photo_coverage": "partial",
                    "overall_confidence": "medium",
                    "comparisons": [],
                    "nutrients": {},
                    "tracking": tracking,
                    "meals": [
                        {
                            "meal_id": "lunch",
                            "label": "Lunch",
                            "meal_time": "12:00",
                            "protein_target_applicable": True,
                            "tracking_tags": ["heme_iron"],
                            "nutrients": {
                                "protein_g": {
                                    "low": 25,
                                    "high": 35,
                                    "unit": "g",
                                }
                            },
                        }
                    ]
                    + (
                        [
                            {
                                "meal_id": "fruit",
                                "label": "Fruit",
                                "meal_time": "13:00",
                                "protein_target_applicable": False,
                                "tracking_tags": [],
                                "nutrients": {
                                    "protein_g": {
                                        "low": 1,
                                        "high": 2,
                                        "unit": "g",
                                    }
                                },
                            }
                        ]
                        if offset == 0
                        else []
                    ),
                }
            )

        result = make_summary(
            rows=rows,
            start=start,
            end=start + timedelta(days=6),
            requested_days=7,
            provenance={},
        )
        tracking = result["tracking"]

        self.assertEqual(
            tracking["observation_averages"]["direct_water_ml"]["logged_days"],
            1,
        )
        self.assertEqual(
            tracking["observation_averages"]["calcium_mg"]["logged_days"], 0
        )
        self.assertIsNone(
            tracking["observation_averages"]["calcium_mg"]["low"]
        )
        self.assertEqual(
            tracking["meal_frequencies"]["heme_iron"]["confirmed_meals"], 2
        )
        self.assertEqual(
            tracking["meal_frequencies"]["heme_iron"][
                "confirmed_meals_per_week"
            ],
            2.0,
        )
        self.assertEqual(tracking["meal_protein"]["counts"], {"目标内": 2})
        self.assertEqual(tracking["meal_protein"]["observed_meals"], 3)
        self.assertEqual(
            tracking["meal_protein"]["meals"][1]["status"], "观察项"
        )
        self.assertAlmostEqual(
            tracking["body_changes"]["weight_kg"]["change"], -0.4
        )


if __name__ == "__main__":
    unittest.main()
