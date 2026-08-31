import unittest
from datetime import date, timedelta

from healthlog.summary import make_summary


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


if __name__ == "__main__":
    unittest.main()
