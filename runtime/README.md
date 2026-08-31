# Rebuildable local runtime

This directory is ignored by Git except for this notice. It contains private
outputs that can be regenerated from `data/` and the local profile:

- `daily/YYYYMMDD/`: manifests, analysis templates, and rendered daily Markdown
- `reports/nutrition/`: generated 7/30-day JSON and Markdown summaries
- `state/healthlog.sqlite3`: the nutrition index and optional USDA response cache

HTML and browser-ready images never belong here. They are written to the
separate private `site/` presentation layer.

Deleting `runtime/` does not delete original photos, medical documents,
supplement records, or canonical `analysis.json` files. Recreate one day's
outputs with `diet prepare DATE --skip-export && diet render DATE`, then verify
them with `diet verify DATE`.
