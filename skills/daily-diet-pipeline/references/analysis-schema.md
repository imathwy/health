# analysis.json schema v2

Keep `analysis.json` human-reviewable. The CLI is the final validator; this reference shows the item-level fields added in v2.

```json
{
  "name": "Fish, salmon, cooked, dry heat",
  "portion": "150–220 g",
  "nutrition": {
    "kcal": [346.5, 508.2],
    "protein_g": [38.58, 56.58],
    "carbohydrate_g": [0, 0],
    "fat_g": [20.07, 29.44],
    "fiber_g": [0, 0],
    "sodium_mg": [90, 132]
  },
  "optional_nutrients": {
    "potassium_mg": [757.5, 1111]
  },
  "confidence": "medium",
  "evidence": {
    "portion_method": "manual_range",
    "nutrition_source": "usda_fdc",
    "references": [
      {
        "provider": "USDA FoodData Central",
        "id": "171999",
        "url": "https://fdc.nal.usda.gov/food-details/171999/nutrients",
        "description": "Fish, salmon, chinook, cooked, dry heat",
        "basis": "per 100 g scaled to 150–220 g"
      }
    ],
    "notes": [
      "Confirm that species and dry-heat preparation match the image."
    ]
  }
}
```

Allowed `portion_method` values:

- `manual_weight`: one user-measured gram value
- `manual_range`: user-measured or explicitly supplied gram range
- `manual_serving`: user-supplied count/spoon/cup
- `package_serving`: serving or weight visible on packaging
- `visual_estimate`: portion inferred from the image
- `unknown`: consumption is known but amount is not

Allowed `nutrition_source` values:

- `package_label`
- `usda_fdc`
- `chinese_food_composition`
- `recipe_estimate`
- `manual`
- `unknown`

Rules:

- Core nutrients always require numeric `[low, high]` ranges.
- Put a sourced zero only when the source supports zero. Missing optional nutrients stay absent.
- `references` cite the composition source, not merely the image used for portion estimation.
- If a package image only identifies the product but lacks a nutrition panel, use it as a reference note while keeping `nutrition_source` as `recipe_estimate` or `unknown`.
