# Privacy boundary

## Tracked

- application and setup source;
- Codex Skill instructions;
- documentation;
- a non-personal example profile;
- the pre-commit privacy gate.

## Never tracked

- `config/health_profile.json` (private operational settings and active ID);
- `config/reminder.local.json` (private reminder time/message), plus the
  clone-specific plist installed under `~/Library/LaunchAgents/`;
- `data/profiles/<profile_id>/profile.json`, every medical index, and every raw
  medical record;
- anything below `data/` except `data/README.md`;
- anything below `runtime/` except `runtime/README.md`;
- anything below `site/` except `site/README.md`;
- anything below `build/`;
- photos, videos, PDFs, analyses, SQLite databases, USDA caches, signed Shortcuts, environment files, or generated reports.

`.gitignore` prevents ordinary staging. `.githooks/pre-commit` also inspects the staged Git snapshot, rejects private paths and binary media, limits file size, and searches for home-directory paths and common credential forms. This second layer catches accidental `git add -f` usage.

`site/index.html` is the local display surface for private health reports. Its
profile page contains structured medical summaries but never raw-record paths or
links. Open
it as a local file, or serve only `site/` on `127.0.0.1`. Do not use the
repository root as an HTTP document root and do not
publish the portal without a separate, explicit sharing decision.

Daily reminder text may appear on the lock screen. The generated default is
deliberately generic and contains no condition, medicine, measurement, or
medical-record detail. A custom message is a user's explicit local choice and
must still remain outside Git.

Food screening retains `consumed_food` and `possible_food` exports locally.
After validation, `unrelated` copies and their browser previews are removed from
the health workspace; `media-audit.json` retains only private metadata and a
SHA-256. The cleanup code is bounded to the configured daily record and site
preview roots and never issues a delete request to Apple Photos. See
[media retention](media-retention.md).

If private data is ever committed, removing it in a later commit is insufficient because it remains in history. Stop before pushing, remove it from history with an appropriate history-rewrite tool, rotate any exposed credentials, and verify a fresh clone before publishing.

## Optional external nutrition lookup

The daily photo pipeline is local. `diet fdc-search` and `diet fdc-food` are explicit optional commands that contact USDA FoodData Central. They send only a text food query or numeric FDC ID; they do not upload photos, analyses, profile data, or health records. Responses are cached in the ignored SQLite database.

Use `FDC_API_KEY` or `USDA_API_KEY` through the process environment. The key is never written to the database or reports. Without either variable, the CLI uses USDA's limited `DEMO_KEY`. `.env` files are ignored, and this project does not automatically load them.
