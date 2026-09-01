# analysis.json schema v3

Keep `analysis.json` human-reviewable. The CLI is the final validator. Schema v3
retains the v2 item provenance below and adds explicit daily observations, body
measurements, and meal tags.

Every manifest asset must have one top-level image record before meals are
reconstructed:

```json
{
  "file": "IMG_0001.HEIC",
  "classification": "consumed_food",
  "meal_id": "lunch",
  "observations": ["A plated meal and drink are visible"],
  "uncertainties": ["Consumed fraction is not measured"]
}
```

- `consumed_food`: confirmed to represent some intake; it must link to exactly
  one meal and may support nutrient estimation;
- `possible_food`: food-related, but it may not represent the user's intake;
  keep `meal_id` empty and exclude it from nutrient totals until confirmed;
- `unrelated`: non-food media; keep `meal_id` empty and exclude it from meals;
- `unreviewed`: template-only state and invalid in a final report.

When the local profile sets `food_photo_means_consumed` to true, a food, drink,
package, or nutrition-label photo is `consumed_food` even without a plated-food
view. The photo establishes some consumption, not the quantity or that the full
package was consumed.

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

Every schema-v3 meal also has `tracking_tags`:

```json
{
  "id": "lunch",
  "label": "Lunch",
  "time": "12:30",
  "images": ["IMG_0001.HEIC"],
  "protein_target_applicable": true,
  "tracking_tags": ["heme_iron"],
  "notes": [],
  "items": []
}
```

Allowed tags:

- `heme_iron`: a meaningful meat, poultry, fish, or seafood source is confirmed;
  eggs and dairy do not qualify;
- `oily_fish`: salmon, sardine, mackerel, herring, trout, or another defensible
  oily-fish match is confirmed. Generic “fish” is not enough.

Set `protein_target_applicable` to `true` for a main meal or intentional protein
feeding. Set it to `false` for a fruit course, drink, condiment, or small snack
that is not intended to meet the per-meal protein range. All meals still show
their protein amount; only applicable meals enter target-status counts.

The top-level `tracking` block is complete even when every value is unknown:

```json
{
  "observations": {
    "direct_water_ml": {
      "range": [1600, 1800],
      "source": "user_reported",
      "coverage": "complete",
      "notes": ["Bottle refills reported by the user"]
    },
    "calcium_mg": {
      "range": [650, 900],
      "source": "derived_from_items",
      "coverage": "partial",
      "notes": ["Some cafeteria dishes lack calcium composition"]
    },
    "sleep_hours": {
      "range": [6.5, 6.5],
      "source": "user_reported",
      "coverage": "complete",
      "notes": []
    },
    "caffeine_mg": {
      "range": [120, 180],
      "source": "package_label",
      "coverage": "complete",
      "notes": []
    },
    "training_minutes": {
      "range": [60, 60],
      "source": "user_reported",
      "coverage": "complete",
      "notes": []
    },
    "session_rpe": {
      "range": [7, 7],
      "source": "user_reported",
      "coverage": "complete",
      "notes": []
    },
    "vegetables_g": {
      "range": [250, 350],
      "source": "photo_review",
      "coverage": "partial",
      "notes": []
    },
    "fruit_g": {
      "range": [180, 250],
      "source": "photo_review",
      "coverage": "partial",
      "notes": []
    }
  },
  "last_caffeine_time": "15:00",
  "meal_tagging": {
    "source": "photo_review",
    "coverage": "partial",
    "notes": ["Photos do not cover the full day"]
  },
  "iron_calcium_timing": {
    "status": "not_applicable_no_iron_supplement",
    "source": "user_reported",
    "notes": []
  },
  "body_measurements": {
    "weight_kg": {
      "value": 72.0,
      "source": "measured",
      "recorded_at": "07:40",
      "context": "after waking and toileting, before breakfast",
      "notes": []
    },
    "waist_cm": {
      "value": null,
      "source": "unknown",
      "recorded_at": null,
      "context": "",
      "notes": []
    },
    "chest_cm": {
      "value": null,
      "source": "unknown",
      "recorded_at": null,
      "context": "",
      "notes": []
    },
    "upper_arm_cm": {
      "value": null,
      "source": "unknown",
      "recorded_at": null,
      "context": "",
      "notes": []
    },
    "thigh_cm": {
      "value": null,
      "source": "unknown",
      "recorded_at": null,
      "context": "",
      "notes": []
    }
  }
}
```

Use `null`, never `[0, 0]`, for an unknown observation. A known zero is valid
only with `coverage: complete`. Per-meal protein is derived from food items and
must not be duplicated manually. Calcium is automatically summed from item
`optional_nutrients.calcium_mg` when available; incomplete item or photo
coverage remains partial.

Allowed iron–calcium statuses:

- `unknown`
- `not_applicable_no_iron_supplement`
- `food_only`
- `supplements_separated`
- `potential_supplement_overlap`

Ordinary mixed-food meals use `food_only`, not
`potential_supplement_overlap`. The latter is reserved for separate iron and
calcium supplements taken together. Follow the iron product label or clinician
instructions for whether high-calcium foods also need different timing.

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
- See `docs/tracking-metrics.md` for metric definitions, targets, and evidence rules.
