# Local HealthLog workspace

This repository separates reusable source from private health data. Never transmit local health data or force-add ignored files unless the user explicitly asks to share a specific artifact.

## Project boundaries

- Reusable source: `src/`, `bin/`, `scripts/`, `skills/`, `docs/`, and the example config.
- Private local state: `config/health_profile.json`, `data/`, and `build/`.
- Before committing, run `python3 scripts/check_privacy.py --staged`. Fix the cause rather than bypassing the hook.

## Daily diet workflow

When the user asks to analyze food for today, yesterday, or a date:

1. Resolve the date in the local timezone.
2. Run `./bin/diet prepare YYYY-MM-DD` so the configured Shortcut exports Apple Photos and creates a manifest plus JPEG previews.
3. Read `config/health_profile.json`, the generated manifest, and the generated analysis template under the configured daily directory.
4. Inspect every manifest preview. Classify every asset as `consumed_food`, `possible_food`, or `unrelated`.
5. Apply `diet_context.food_photo_means_consumed` from the local profile. When true, a food or drink photo confirms some consumption, but it does not establish the amount or that the whole portion was eaten.
6. Reconstruct meals from timestamps and visual context. Pair before/after photos and count repeated angles or Live Photo pairs once.
7. Write `analysis.json` with range estimates for calories, protein, carbohydrate, fat, fiber, and sodium. State uncertain identity, portion, oil, sauce, and consumed fraction.
8. Run `./bin/diet render YYYY-MM-DD` and `./bin/diet verify YYYY-MM-DD`. Fix errors until verification passes.
9. Return links to the dated Markdown and static HTML reports plus the material uncertainty.

Use the targets and health guardrails from the ignored local profile. Do not infer diagnoses or add supplements from one day of photos. Never delete or alter original media. Do not use `--reset-analysis` on meaningful work unless it has first been preserved. Use `--skip-export` only when the user explicitly wants existing files analyzed or when testing downstream code.
