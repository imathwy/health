# Local private data

This directory is intentionally excluded from Git except for this notice. `scripts/setup.sh` creates the working subdirectories:

- `daily/`: exported Apple Photos, manifests, analyses, and generated reports
- `medical/`: private examination records
- `supplements/`: private supplement photos and reports

Do not force-add files from these folders. The pre-commit privacy check rejects them even if `.gitignore` is bypassed.
