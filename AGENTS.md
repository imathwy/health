# Local HealthLog workspace

This repository separates reusable source from private health data. Never transmit local health data or force-add ignored files unless the user explicitly asks to share a specific artifact.

## Project boundaries

- Reusable source: `src/`, `bin/`, `scripts/`, `skills/`, `docs/`, and the example config.
- Durable private records: `config/health_profile.json` and `data/`. Original
  media and `analysis.json` stay here and should be backed up.
- Rebuildable private runtime: `runtime/`. Manifests, Markdown/JSON reports,
  SQLite, and caches belong here; HTML does not.
- Private web display: `site/`. All browser-facing HTML and display-only images
  belong here. Most dated pages are rebuildable; the curated health page may be
  locally authored and should be backed up when changed.
- Developer build output: `build/`. Signed Shortcuts and build/test scratch files
  belong here and are never health-record inputs.
- Local web display: `site/index.html` may show private reports. Never serve
  the repository root or expose the portal beyond loopback without explicit
  authorization.
- Do not create root-level compatibility aliases such as `daily`; each path has
  one owner.
- Before committing, run `python3 scripts/check_privacy.py --staged`. Fix the cause rather than bypassing the hook.

## Daily diet workflow

When the user asks to analyze food for today, yesterday, or a date:

1. Resolve the date in the local timezone.
2. Run `./bin/diet prepare YYYY-MM-DD` so the configured Shortcut exports Apple Photos and creates a manifest plus JPEG previews.
3. Read `config/health_profile.json`, the canonical analysis under
   `data/daily/YYYYMMDD/`, and the generated manifest/template under
   `runtime/daily/YYYYMMDD/pipeline/`.
4. Inspect every manifest preview under `site/daily/YYYYMMDD/assets/`. Classify every asset as `consumed_food`, `possible_food`, or `unrelated`.
5. Apply `diet_context.food_photo_means_consumed` from the local profile. When true, a food or drink photo confirms some consumption, but it does not establish the amount or that the whole portion was eaten.
6. Reconstruct meals from timestamps and visual context. Pair before/after photos and count repeated angles or Live Photo pairs once.
7. Write schema-v2 `analysis.json` with range estimates for calories, protein, carbohydrate, fat, fiber, and sodium. Every item also records `evidence.portion_method` and `evidence.nutrition_source`; optional nutrients are omitted when unknown rather than filled with zero.
8. Prefer a visible package label, then a verified matching single-food database record, then a wide recipe estimate. Do not map a mixed cafeteria plate to one USDA item or invent a source after estimating from general knowledge.
9. Run `./bin/diet render YYYY-MM-DD` and `./bin/diet verify YYYY-MM-DD`. Rendering also syncs the private SQLite index. Fix errors until verification passes.
10. Run `./bin/diet dashboard` when the unified portal is missing or stale.
11. Return links to `site/index.html`, the dated runtime Markdown and site HTML,
    plus the material uncertainty.

Use the targets and health guardrails from the ignored local profile. Do not infer diagnoses or add supplements from one day of photos. Never delete or alter original media. Do not use `--reset-analysis` on meaningful work unless it has first been preserved. Use `--skip-export` only when the user explicitly wants existing files analyzed or when testing downstream code.

## Nutrition data and longitudinal reports

- `data/daily/YYYYMMDD/analysis.json` is canonical;
  `runtime/state/healthlog.sqlite3` is a private, rebuildable index.
- Use `./bin/diet fdc-search QUERY --agent` and `./bin/diet fdc-food ID --grams LOW:HIGH --agent` only for defensible single-food matches. These commands send text or an ID to USDA; they never send images.
- Use `./bin/diet rebuild-db` after bulk edits or copying dated analyses.
- Use `./bin/diet summary --days 7|30 --end DATE` for longitudinal reports. State coverage, never count missing days as zero, and require at least five logged dates before describing interval trends.
