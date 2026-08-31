---
name: daily-diet-pipeline
description: Run a local Apple Photos daily-diet workflow when asked to analyze food photos for today, yesterday, or a specific date; generate or repair its structured analysis, Markdown report, static HTML report, and daily index; or diagnose the dated photo pipeline. Do not use for general nutrition questions without this local photo workflow.
---

# Daily Diet Pipeline

Operate the existing repository end to end. Locate its root by finding `bin/diet`, `config/health_profile.json`, and `src/healthlog/cli.py`. Commands below run from that root.

## Run a dated analysis

1. Resolve `today`, `yesterday`, or the requested `YYYY-MM-DD` in local time.
2. Run `./bin/diet prepare DATE`. This calls the Shortcut named in the local profile and creates a manifest plus JPEG previews below the configured `pipeline.daily_directory`. Use `--skip-export` only when the user explicitly wants existing files analyzed or while testing downstream code.
3. Read `config/health_profile.json`, the generated `.diet-pipeline/manifest.json`, and `.diet-pipeline/analysis.template.json`.
4. Inspect every manifest asset through its `preview_path` with the local image viewer. Do not finish after sampling. If a preview failed, read `preview_error` and inspect the source through another supported local viewer when possible.
5. Write the dated `analysis.json` in the schema enforced by `src/healthlog/cli.py`. Never reuse foods, portions, or conclusions from a different date.
6. Run `./bin/diet render DATE`, fix every validation error, then run `./bin/diet verify DATE`. Stop only when verification passes.
7. Return clickable paths to that date’s `README.md` and `index.html`, along with the key estimate and material uncertainty.

## Interpret photo evidence

- Classify each asset as `consumed_food`, `possible_food`, or `unrelated`. Never leave `unreviewed` in a final analysis.
- Read `diet_context.food_photo_means_consumed` from the local profile. When true, any dated food or drink photo means that some amount was consumed, including package views and meal-before photos.
- Confirmation of consumption does not determine quantity or completion. Use a user-provided amount when available; otherwise estimate a wide consumed-portion range and state the uncertainty. Use `possible_food` only when an image may not depict the user's food or drink, and `unrelated` for non-food media.
- Prefer before/after pairs when estimating consumed portions. Link both to one meal and describe visible leftovers.
- Treat near-identical angles, Live Photo pairs, and repeated product-label shots as evidence for one item or meal; do not double count them.
- Base observations on visible facts. Put uncertain identification, portion, ingredients, cooking oil, sauces, and shared quantities in `uncertainties`.
- Set `photo_coverage` to `partial` unless evidence supports a complete day. Unrecorded snacks and drinks remain an explicit limitation.

## Estimate nutrition

- Use ranges rather than point estimates. Every food item needs ranges for `kcal`, `protein_g`, `carbohydrate_g`, `fat_g`, `fiber_g`, and `sodium_mg`, plus `low`, `medium`, or `high` confidence.
- Prefer a visible package label. Otherwise use the visible portion and a plausible recipe range. Widen the range when food identity, oil, sauce, or consumed fraction is unclear.
- Use the target for the recorded `day_type`. If training is unknown, keep `day_type` as `unknown`; do not interrupt the workflow solely to ask.
- Explain whether the estimated interval is below, overlaps, or exceeds the target. Do not present a midpoint as measured intake.
- Do not infer a diagnosis or add supplements from one day of photos. Respect guardrails in the local profile. Address ordinary sodium, fiber, and energy issues through food structure first.

## Preserve data and recover safely

- Never delete or alter original exported media.
- `prepare` preserves an existing `analysis.json`. If it reports `preserved-needs-sync`, reconcile its image records with the manifest while keeping completed observations. Do not use `--reset-analysis` on meaningful work without preserving it.
- If no assets export, check `./bin/diet doctor`, the Shortcut name in the profile, and the Shortcut output. Ask for user interaction only when a macOS permission dialog still blocks progress.
- If previews fail, inspect `preview_error`; `magick` is preferred for HEIC orientation and `sips` is the fallback.
- Do not delete similarly named Shortcuts while repairing the workflow unless the user explicitly asks.
- Keep `config/health_profile.json`, `data/`, and `build/` out of Git. When repository work is involved, run `python3 scripts/check_privacy.py --staged` before committing.

## Completion checks

An analysis is complete only when:

- the manifest covers every media file in the dated folder;
- every asset has been inspected and classified;
- duplicate views are not counted twice;
- the structured analysis passes schema validation;
- Markdown, standalone HTML, and the daily indexes exist;
- `./bin/diet verify DATE` prints `VERIFY=passed`.
