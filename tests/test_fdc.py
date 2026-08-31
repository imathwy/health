import unittest

from healthlog.fdc import analysis_item_candidate, normalize_food


class FoodDataCentralTests(unittest.TestCase):
    def test_normalizes_and_scales_abridged_food(self) -> None:
        payload = {
            "fdcId": 12345,
            "description": "Test cooked fish",
            "dataType": "Foundation",
            "foodNutrients": [
                {"nutrientId": 1008, "nutrientName": "Energy", "unitName": "KCAL", "value": 200},
                {"nutrientId": 1003, "nutrientName": "Protein", "unitName": "G", "value": 20},
                {"nutrientId": 1005, "nutrientName": "Carbohydrate, by difference", "unitName": "G", "value": 1},
                {"nutrientId": 1004, "nutrientName": "Total lipid (fat)", "unitName": "G", "value": 12},
                {"nutrientId": 1079, "nutrientName": "Fiber, total dietary", "unitName": "G", "value": 0},
                {"nutrientId": 1093, "nutrientName": "Sodium, Na", "unitName": "MG", "value": 80},
                {"nutrientId": 1092, "nutrientName": "Potassium, K", "unitName": "MG", "value": 350},
            ],
        }

        food = normalize_food(payload)
        candidate = analysis_item_candidate(food, 150, 200)

        self.assertEqual(food["fdc_id"], 12345)
        self.assertEqual(candidate["nutrition"]["kcal"], [300.0, 400.0])
        self.assertEqual(candidate["nutrition"]["protein_g"], [30.0, 40.0])
        self.assertEqual(candidate["optional_nutrients"]["potassium_mg"], [525.0, 700.0])
        self.assertEqual(candidate["missing_core_nutrients"], [])
        self.assertEqual(candidate["evidence"]["nutrition_source"], "usda_fdc")
        self.assertEqual(candidate["evidence"]["references"][0]["id"], "12345")

    def test_converts_kilojoules_to_kilocalories(self) -> None:
        payload = {
            "fdcId": 2,
            "description": "Energy test",
            "dataType": "Foundation",
            "foodNutrients": [
                {
                    "nutrient": {"id": 2047, "name": "Metabolizable Energy (Atwater General Factor)", "unitName": "kJ"},
                    "amount": 418.4,
                }
            ],
        }
        food = normalize_food(payload)
        self.assertAlmostEqual(food["nutrients_per_100g"]["kcal"], 100.0)


if __name__ == "__main__":
    unittest.main()
