# Rebuildable local runtime

This directory is ignored by Git except for this notice. It contains private
outputs that can be regenerated from `data/` and the local profile:

- `daily/YYYYMMDD/`: manifests, JPEG previews, and rendered daily Markdown/HTML
- `reports/nutrition/`: generated 7/30-day JSON, Markdown, and HTML summaries
- `state/healthlog.sqlite3`: the nutrition index and optional USDA response cache
- `index.html`: the local static portal for switching between health,
  supplements, daily evidence, and 7/30-day reports

Deleting `runtime/` does not delete original photos, medical documents,
supplement records, or canonical `analysis.json` files. Recreate one day's
outputs with `diet prepare DATE --skip-export && diet render DATE`, then verify
them with `diet verify DATE`.
