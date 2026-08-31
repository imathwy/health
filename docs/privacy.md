# Privacy boundary

## Tracked

- application and setup source;
- Codex Skill instructions;
- documentation;
- a non-personal example profile;
- the pre-commit privacy gate.

## Never tracked

- `config/health_profile.json`;
- anything below `data/` except `data/README.md`;
- anything below `build/`;
- photos, videos, PDFs, signed Shortcuts, environment files, or caches.

`.gitignore` prevents ordinary staging. `.githooks/pre-commit` also inspects the staged Git snapshot, rejects private paths and binary media, limits file size, and searches for home-directory paths and common credential forms. This second layer catches accidental `git add -f` usage.

If private data is ever committed, removing it in a later commit is insufficient because it remains in history. Stop before pushing, remove it from history with an appropriate history-rewrite tool, rotate any exposed credentials, and verify a fresh clone before publishing.
