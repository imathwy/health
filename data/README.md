# Local private data

This directory is intentionally excluded from Git except for this notice. `scripts/setup.sh` creates the working subdirectories:

- `daily/`: exported Apple Photos, manifests, analyses, and generated reports
- `medical/`: private examination records
- `reports/nutrition/`: generated 7/30-day Markdown, HTML, and JSON summaries
- `state/healthlog.sqlite3`: rebuildable local nutrition index and USDA response cache
- `supplements/`: private supplement photos and reports

Do not force-add files from these folders. The pre-commit privacy check rejects them even if `.gitignore` is bypassed.
