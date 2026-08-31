# Architecture

The repository has three boundaries:

1. **Tracked application** — `src/healthlog/cli.py` owns date resolution, Shortcut execution, media manifests, previews, schema validation, report rendering, indexes, and verification.
2. **Platform bridge** — `scripts/build_shortcut.py` creates a clone-specific Apple Shortcut. Python does not request Photos access; the Shortcut owns that permission and writes into `data/daily/`.
3. **Private working set** — local configuration and all health artifacts live in ignored paths.

`bin/diet` resolves the repository from its own location, so it works both from the clone and through a symlink in `~/.local/bin`. `HEALTHLOG_ROOT` may override root discovery for isolated tests.

The Shortcut builder places absolute paths only in ignored build artifacts. No tracked file should contain a user home path.
