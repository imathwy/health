# Architecture

The repository has six explicit boundaries:

1. **Tracked application** — `src/healthlog/` owns date resolution, Shortcut
   execution, media manifests, previews, schema validation, source-aware
   nutrition records, report rendering, SQLite indexing, USDA normalization,
   summaries, and verification.
2. **Platform bridge** — `scripts/build_shortcut.py` creates a clone-specific
   Apple Shortcut. Python does not request Photos access; the Shortcut owns that
   permission and writes directly into `data/daily/`.
3. **Durable private records** — `data/daily/YYYYMMDD/` contains unmodified media
   and the human-reviewed `analysis.json`. `data/medical/` and
   `data/supplements/` hold the other user-owned health records. Back up this
   layer.
4. **Rebuildable private runtime** — `runtime/` contains manifests, analysis
   templates, Markdown/JSON reports, SQLite indexes, and the USDA cache. It can
   be removed and recreated from `data/` plus the local profile.
5. **Private presentation layer** — `site/` is the only browser-facing tree. It
   contains the portal, health/supplement page, daily HTML, JPEG previews, and
   longitudinal HTML. Dated outputs are rebuildable; locally authored health
   presentation files should be backed up.
6. **Developer build output** — `build/` contains signed Shortcut artifacts and
   temporary test/build output. It is never a data source.

```mermaid
flowchart LR
    A[Date] --> B[Apple Shortcut]
    B --> C[data/daily: original media]
    C --> D[runtime/daily: manifest and template]
    C --> P[site/daily: browser previews]
    D --> E[Codex review]
    P --> E
    U[Optional USDA text or ID query] --> E
    E --> F[data/daily: analysis.json v2]
    F --> G[runtime/daily: Markdown]
    F --> W[site/daily: HTML]
    F --> H[runtime/state: SQLite index]
    H --> I[runtime/reports: 7/30-day JSON and Markdown]
    H --> T[site/nutrition: 7/30-day HTML]
    W --> J[site/index.html: local portal]
    T --> J
    I --> J
```

The two uncertainties in each item stay separate: `portion_method` explains how
consumed quantity was inferred, while `nutrition_source` explains where
composition values came from. This prevents a database match from implying that
the photographed portion was measured.

The Shortcut's returned `EXPORT_DIR` must exactly match the configured dated
record directory. The CLI deliberately rejects root aliases such as `daily/`,
even if a symlink would resolve to the same target. This keeps ownership visible
and prevents compatibility paths from becoming permanent API surface.

`bin/diet` resolves the repository from its own location, so it works both from
the clone and through a symlink in `~/.local/bin`. `HEALTHLOG_ROOT` may override
root discovery for isolated tests.

The Shortcut builder places absolute paths only in ignored build artifacts. No
tracked file should contain a user home path.
