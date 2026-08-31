# Nutrition source selection

| Situation | Portion method | Nutrition source | Action |
|---|---|---|---|
| User weighed the eaten food | `manual_weight` or `manual_range` | label/database/recipe as applicable | Scale the matching composition source to the measured grams. |
| Exact package label is readable | `package_serving` or user method | `package_label` | Transcribe the label and preserve image filename in references. |
| Plain single food with preparation known | visual or user method | `usda_fdc` | Search, inspect candidates, fetch the exact FDC ID, then scale. |
| Cafeteria stir-fry, soup, mixed plate | `visual_estimate` | `recipe_estimate` | Model ingredients, oil, sauce, and eaten fraction as a wide range. |
| Package front only; amount unknown | `unknown` | `recipe_estimate` or `unknown` | Product identity is known, nutrition and quantity are not. |
| Nutrient not present in the source | any | any | Omit it from `optional_nutrients`; never fill zero. |

## USDA commands

```bash
# Human-readable search
./bin/diet fdc-search "rice white cooked" --limit 5

# Compact JSON for an agent
./bin/diet fdc-search "rice white cooked" --limit 5 --agent

# Fetch exact record and scale its per-100-g values
./bin/diet fdc-food FDC_ID --grams 110:170 --agent

# Reuse private SQLite cache without network
./bin/diet fdc-food FDC_ID --grams 110:170 --offline --agent
```

Environment variables are read in this order: `FDC_API_KEY`, `USDA_API_KEY`, then USDA's rate-limited `DEMO_KEY`. Never write a real API key into the profile, Skill, analysis, report, or Git.

USDA matching is strongest for plain foods and documented preparations. It does not resolve an uncertain species, recipe, oil quantity, or consumed fraction. For a Chinese mixed dish, database values may help with identifiable ingredients, but the final plate still needs a recipe range.
