# Local private data

This directory is intentionally excluded from Git except for this notice. It is
the durable private record layer and should be backed up. `scripts/setup.sh`
creates these subdirectories:

- `daily/YYYYMMDD/`: original Apple Photos exports and canonical `analysis.json`
- `medical/`: private examination records
- `supplements/`: private supplement photos and reports

Generated manifests, Markdown/JSON, and SQLite state belong in `runtime/`;
browser-facing HTML and display images belong in `site/`. Do not create
root-level aliases such as `daily`. Do not force-add
files from these folders; the pre-commit privacy check rejects them even if
`.gitignore` is bypassed.
