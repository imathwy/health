# Local HealthLog workspace

This repository separates reusable source from private health data. Never transmit local health data or force-add ignored files unless the user explicitly asks to share a specific artifact.

## Project boundaries

- Reusable source: `src/`, `bin/`, `scripts/`, `skills/`, `docs/`, and the example config.
- Private operational settings: `config/health_profile.json` selects one active
  `profile_id` and controls paths, privacy, and the Shortcut. It must not become
  a second source for personal facts.
- Private reminder settings: `config/reminder.local.json` stores the active
  owner's local daily time and notification preference. The corresponding
  LaunchAgent lives outside the repository under `~/Library/LaunchAgents/`.
- Durable private records: `data/`. The canonical personal context is
  `data/profiles/<profile_id>/profile.json`; its structured medical index and
  raw records live under the same profile. Original food media and
  `analysis.json` stay in `data/daily/`. Back up this layer.
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

## Source ownership

- `cli.py` only parses arguments, selects a command, and maps `PipelineError` to
  an exit code. Put workflow behavior in `commands.py`.
- `commands.py` orchestrates dated nutrition use cases; `profile_workflow.py`
  orchestrates personal-profile initialization, migration, validation, and
  rendering. They may compose domain code and adapters, but adapters must not
  import them.
- `reminder.py` owns the validated schedule/config model;
  `reminder_workflow.py` owns local notification and launchd orchestration.
- `analysis.py`, `nutrition.py`, `tracking.py`, `tracking_summary.py`,
  `personal_profile.py`, and `summary.py` are the domain layer. They must
  not import CLI, filesystem, media, presentation, network, or SQLite adapters.
- `workspace.py`, `media.py`, `presentation.py`, `profile_presentation.py`,
  `store.py`, and `fdc.py` own configuration/filesystem, Photos/preview, static
  display, personal-profile display, SQLite, and USDA boundaries respectively.
- Preserve these import rules in `tests/test_boundaries.py`. Prefer a focused
  module or immutable value object over adding another responsibility to an
  existing large module.

## Daily diet workflow

When the user asks to analyze food for today, yesterday, or a date:

1. Resolve the date in the local timezone.
2. Run `./bin/diet prepare YYYY-MM-DD` so the configured Shortcut exports Apple Photos and creates a manifest plus JPEG previews.
3. Read `config/health_profile.json` only for the active ID, privacy, and paths.
   Read the canonical personal context and medical index under
   `data/profiles/<active_profile_id>/`, the canonical analysis under
   `data/daily/YYYYMMDD/`, and the generated manifest/template under
   `runtime/daily/YYYYMMDD/pipeline/`.
4. Inspect every manifest preview under `site/daily/YYYYMMDD/assets/`; never sample. Classify every asset as `consumed_food`, `possible_food`, or `unrelated` before estimating nutrition.
5. Treat `consumed_food` and `possible_food` as the food-related screening set, but link only `consumed_food` to a meal. `possible_food` and `unrelated` must keep an empty `meal_id` and never contribute to nutrient totals.
6. Apply `diet_context.food_photo_means_consumed` from the local profile. When true, a food, drink, package, or nutrition-label photo confirms some consumption, but it does not establish the amount or that the whole portion was eaten.
7. Reconstruct meals from confirmed consumed-food photos, timestamps, and visual context. Pair before/after photos and count repeated angles or Live Photo pairs once.
8. Write schema-v3 `analysis.json` with range estimates for calories, protein, carbohydrate, fat, fiber, and sodium. Every item also records `evidence.portion_method` and `evidence.nutrition_source`; every meal has `tracking_tags`; non-photo observations live in the top-level `tracking` block. Optional and tracking values remain null/absent when unknown rather than being filled with zero.
9. Prefer a visible package label, then a verified matching single-food database record, then a wide recipe estimate. Do not map a mixed cafeteria plate to one USDA item or invent a source after estimating from general knowledge.
10. Run `./bin/diet render YYYY-MM-DD` and `./bin/diet verify YYYY-MM-DD`. Rendering also syncs the private SQLite index. Fix errors until verification passes.
11. Run `./bin/diet dashboard` when the unified portal is missing or stale.
12. Return links to `site/index.html`, the dated runtime Markdown and site HTML,
    plus the material uncertainty.

Use targets and health guardrails from the canonical durable personal profile.
Do not infer diagnoses or add supplements from one day of photos. Never delete
or alter original media. Do not use `--reset-analysis` on meaningful work unless
it has first been preserved. Use `--skip-export` only when the user explicitly
wants existing files analyzed or when testing downstream code.

## Personal profile and medical history

- Run `./bin/diet profile-init` when the active profile documents are absent.
  For a schema-v1 workspace, use `--migrate-config` only when migration is part
  of the requested work; it first preserves the legacy document under the
  profile's ignored `migrations/` directory.
- Treat `data/profiles/<profile_id>/profile.json` as the source of truth for
  demographics, current status, goals, activity, nutrition targets, diet
  context, conditions, symptoms, medicines, allergies, and guardrails.
- Put raw medical files only in
  `data/profiles/<profile_id>/medical/files/`. Register each one in
  `medical/index.json` with a safe `files/...` relative path. Conditions and
  symptoms may reference record IDs; do not duplicate raw document contents.
- Do not interpret an empty medicines or allergies list as a confirmed absence;
  the generated page labels it as unregistered. Preserve whether a fact is
  user-reported or supported by a medical record.
- Open a raw record only when the user asks for it or a required fact cannot be
  resolved from the structured index. Never copy raw records or clickable raw
  paths into `site/`.
- After editing either canonical document, run `./bin/diet profile` and then
  `./bin/diet dashboard`. Fix validation errors and stale record references
  before using the profile for health interpretation.

## Nutrition data and longitudinal reports

- `data/daily/YYYYMMDD/analysis.json` is canonical;
  `runtime/state/healthlog.sqlite3` is a private, rebuildable index.
- Use `./bin/diet fdc-search QUERY --agent` and `./bin/diet fdc-food ID --grams LOW:HIGH --agent` only for defensible single-food matches. These commands send text or an ID to USDA; they never send images.
- Use `./bin/diet rebuild-db` after bulk edits or copying dated analyses.
- Use `./bin/diet summary --days 7|30 --end DATE` for longitudinal reports. State coverage, never count missing days as zero, and require at least five logged dates before describing interval trends.
- Per-meal protein is derived from meal items. Count heme-iron and oily-fish
  frequency only from reviewed meal tags. Never infer direct water, body
  measurements, sleep, training RPE, or supplement timing from photos.

## Daily local reminder

- Set or update it with `./bin/diet reminder set --time HH:MM`; use
  `--open-dashboard` only when the user wants the portal opened automatically.
- Use a generic lock-screen-safe message unless the user explicitly supplies
  different text. Never place diagnoses, medicines, measurements, or raw
  medical details in a notification by default.
- Verify with `./bin/diet reminder status` and, when the user asked to see a
  notification, `./bin/diet reminder test`. Remove it with
  `./bin/diet reminder remove`.
- The ignored reminder config and workspace-specific external LaunchAgent are local operational
  state. Do not commit the plist or its clone-specific absolute paths.
