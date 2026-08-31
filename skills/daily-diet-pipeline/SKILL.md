---
name: daily-diet-pipeline
description: Run the repository's local Apple Photos diet workflow for today, yesterday, or a date; create or repair schema-v3 meal and health tracking with explicit provenance; render and verify Markdown/HTML; query USDA FoodData Central for suitable single foods; or generate local 7/30-day SQLite summaries. Do not use for unrelated general nutrition questions.
---

# Daily Diet Pipeline

Use the repository as the executable source of truth. Find its root by locating `bin/diet`, `config/health_profile.json`, and `src/healthlog/cli.py`, then run commands from that root. Read the ignored local profile before interpreting food, setting targets, or discussing supplements.

## Choose the workflow

- Dated food-photo analysis: follow **Daily analysis**.
- Existing report is stale or broken: run `./bin/diet status DATE`, then repair the earliest failing stage.
- 7/30-day pattern review: follow **Longitudinal summary**.
- Exact single-food composition or gram scaling: follow **FoodData Central lookup**.
- General nutrition advice without this repository or its dated photos: do not invoke this skill.

## Daily analysis

1. Resolve `today`, `yesterday`, or the requested `YYYY-MM-DD` in local time.
2. Run `./bin/diet prepare DATE`. Use `--skip-export` only when the user explicitly wants existing files analyzed or while testing downstream code.
3. Read `config/health_profile.json`, the canonical
   `data/daily/YYYYMMDD/analysis.json`, and the generated
   `runtime/daily/YYYYMMDD/pipeline/manifest.json` plus
   `analysis.template.json`.
4. Inspect every manifest asset through its `preview_path`, which resolves into the private `site/` display tree. If a preview failed, inspect the original with another supported local viewer. Do not sample.
5. Reconstruct meals from timestamps, before/after pairs, repeated angles, and user statements. Count duplicate views and Live Photo pairs once.
6. Write schema-v3 `analysis.json`. Every item must include core nutrient ranges, confidence, and `evidence`; every meal must include `tracking_tags`; the top-level `tracking` block preserves non-photo observations and explicit unknowns. Load `references/analysis-schema.md` when authoring or repairing the file.
7. Run `./bin/diet render DATE`. This validates the analysis, writes Markdown to `runtime/`, writes standalone HTML to `site/`, and transactionally syncs the private SQLite index.
8. Run `./bin/diet verify DATE`. Fix errors until it prints `VERIFY=passed`.
9. Run `./bin/diet dashboard` if the unified local portal is missing or stale.
10. Return links to `site/index.html`, the dated runtime Markdown and site HTML, the
    energy/protein intervals, and the largest uncertainty.

## Interpret photo evidence

- Apply `diet_context.food_photo_means_consumed` from the local profile. When true, a food or drink photo confirms some consumption, including a package-only photo. It does not establish quantity or that the full visible portion was eaten.
- Prefer user-provided weights, counts, or consumed fractions. Next prefer before/after evidence. Otherwise use a wide visual portion range.
- Use `possible_food` only when the image may not represent the user's intake; use `unrelated` for non-food media. Never leave `unreviewed` in a final analysis.
- Record visible facts in `observations`. Put uncertain identity, recipe, oil, sauce, shared amount, and consumed fraction in `uncertainties`.
- Keep `photo_coverage` as `partial` unless the evidence supports a complete day. Do not turn missing meals into zero intake.

## Estimate nutrition with provenance

Use this order:

1. Visible package label for that exact product and serving.
2. Verified composition record for a matching single food and preparation.
3. A recipe or common-dish range, widened for oil, sauce, ingredients, and eaten fraction.

Photo recognition establishes likely identity and portion; it is not a nutrient database. Keep these two dimensions separate in `evidence.portion_method` and `evidence.nutrition_source`.

- Required ranges: `kcal`, `protein_g`, `carbohydrate_g`, `fat_g`, `fiber_g`, and `sodium_mg`.
- Optional measured or sourced nutrients go in `optional_nutrients`; absence means unknown, not zero.
- Use `package_label` only when the visible label supports the number.
- Use `usda_fdc` only after checking the FDC description and preparation. Preserve the FDC ID and URL in `references`.
- Use `recipe_estimate` for mixed dishes, cafeteria dishes, or unmatched Chinese foods. Do not map an entire mixed plate to one USDA item.
- Never invent a database citation after estimating from general knowledge.
- Explain whether the daily interval is below, overlaps, or exceeds the configured target. Never present a midpoint as measured intake.

## Track decision-relevant health observations

- Derive each meal's protein range from its food items. Do not manually copy a
  second protein total into `tracking`. Set `protein_target_applicable` true for
  main meals/protein feedings and false for fruit courses or small snacks; show
  every meal's protein, but judge the target only when applicable.
- Tag a meal `heme_iron` only when meat, poultry, fish, or seafood is confirmed.
  Do not tag eggs or dairy. Tag `oily_fish` only when the species/type is
  defensible; generic fish remains untagged.
- Record direct drinking water separately from soup, milk, juice, and food
  moisture. Water and unsweetened light tea may count. A bottle photo proves the
  container, not the amount consumed.
- Estimate calcium only from a visible label or defensible composition source.
  The renderer can sum item-level `optional_nutrients.calcium_mg`; incomplete
  food or photo coverage stays `partial`.
- Never infer weight, waist, chest, arm, thigh, sleep, caffeine timing, training
  RPE, or supplement timing from an image. Use user-reported, measured,
  wearable, or package-label data; otherwise keep the field `null`.
- Treat an exact known zero as `[0, 0]` with complete coverage. Keep unknown as
  `null`; never fill it with zero for a cleaner report.
- For iron and calcium, ordinary mixed meals are `food_only`. Record
  `potential_supplement_overlap` only when separate iron and calcium supplements
  were taken together. Follow the iron label or clinician instructions for
  high-calcium foods. If no separate iron supplement is used, select
  `not_applicable_no_iron_supplement`.
- If the user has not supplied the non-photo observations, finish the photo
  analysis with explicit unknowns, then ask one compact follow-up listing only
  the values that would materially improve the report.

Load `references/nutrition-sources.md` for the decision table and lookup commands.

## FoodData Central lookup

The CLI sends only a text query or numeric FDC ID; images stay local. Use remote lookup only when the user explicitly requests it or `privacy.allow_usda_text_queries` is true in the local profile.

```bash
./bin/diet fdc-search "salmon cooked" --limit 5 --agent
./bin/diet fdc-food 171999 --grams 150:220 --agent
```

Prefer Foundation, SR Legacy, and Survey/FNDDS results. Branded search is opt-in with `--include-branded`; a visible local package label usually has priority. A generated item candidate is usable only when `missing_core_nutrients` is empty and the food/preparation match is defensible. Cached lookups work with `--offline`.

## Longitudinal summary

`render` keeps SQLite current. If analyses were copied or edited outside the normal workflow, run:

```bash
./bin/diet rebuild-db
```

Then generate a local report:

```bash
./bin/diet summary --days 7 --end today
./bin/diet summary --days 30 --end yesterday --agent
```

The report must:

- state logged days versus requested days and list missing dates;
- average lower bounds and upper bounds separately over logged days;
- keep photo coverage and confidence visible;
- show nutrition-source counts;
- show per-meal protein distribution, confirmed heme-iron/oily-fish meal
  frequency, calcium/direct-water coverage, iron–calcium timing, and measured
  body changes;
- refuse to infer a trend from fewer than five logged days;
- describe trends from both interval bounds, never from a hidden midpoint;
- avoid correlations with sleep, training, weight, or symptoms until those variables have enough dated observations.

## Health and supplement guardrails

- Use targets and contraindication notes from the local profile.
- Do not diagnose disease or infer deficiency from food photos.
- Do not add a supplement from one day or from an unmeasured micronutrient.
- Prefer food-structure changes for ordinary energy, protein, fiber, and sodium gaps.
- Flag clinical diets, kidney disease, diabetes medication, pregnancy, eating-disorder history, unexplained weight change, or supplement/medication interactions for clinician or registered-dietitian review.

## Preserve and recover

- Never delete or alter original exported media.
- `prepare` preserves an existing `analysis.json`; reconcile `preserved-needs-sync` manually.
- Do not use `--reset-analysis` on meaningful work without preserving it.
- SQLite under `runtime/state/` is derived and rebuildable. The dated
  `analysis.json` under `data/daily/` remains the reviewable canonical record.
- Keep `config/health_profile.json`, private contents of `data/`, `runtime/`, `site/`, and
  `build/` out of Git. Never introduce root-level compatibility aliases such as
  `daily`. Run `python3 scripts/check_privacy.py --staged` before committing
  repository changes.

## Completion checks

The job is complete only when every asset was reviewed, duplicates were not
double counted, each food has source-aware range estimates, schema-v3 tracking
uses explicit unknowns, both daily reports and the unified portal exist, SQLite
matches the analysis hash, and `./bin/diet verify DATE` prints `VERIFY=passed`.
