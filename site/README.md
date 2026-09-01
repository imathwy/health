# Private local site

This directory is the browser-facing presentation layer. Everything below it
except this notice is ignored by Git because pages can contain private health
information.

- `index.html`: the unified local dashboard
- `profile/`: validated personal status and medical-history summary; raw medical
  files are never linked or copied here
- `health/`: the health and supplement page plus its display-only assets
- `daily/YYYYMMDD/`: rendered daily HTML and browser-ready JPEG previews
- `nutrition/`: rendered 7/30-day HTML summaries

The canonical records remain in `data/`; machine-oriented manifests, Markdown,
JSON, SQLite, and caches remain in `runtime/`. Open `site/index.html` directly.
If an HTTP server is needed for browser testing, serve only this directory on
`127.0.0.1`. Never use the repository root as the document root, and do not
publish these pages without a separate, explicit sharing decision.
