# Media retention and deletion boundary

The Apple Shortcut exports all assets for a date into the configured daily
record directory because food relevance cannot be known before visual review.
These candidates may therefore exist briefly inside the health workspace while
Codex inspects every preview. They are not all retained after a completed
analysis.

## Classification policy

| Classification | Workspace media | Nutrient use |
|---|---|---|
| `consumed_food` | Retained | May link to one meal and contribute to totals |
| `possible_food` | Retained | Never contributes until explicitly confirmed |
| `unrelated` | Export copy and derived preview purged after validation | Never contributes |
| `unreviewed` | Retained temporarily | Blocks `render` |

`diet render DATE` first validates the complete analysis. It then resolves each
`unrelated` asset strictly within `data/daily/YYYYMMDD/`, checks its size and
SHA-256 against the manifest, deletes that workspace copy, and deletes only its
derived preview under `site/daily/YYYYMMDD/assets/`. No delete action is sent to
Shortcuts or Apple Photos.

## Durable audit and repeated exports

The private `data/daily/YYYYMMDD/media-audit.json` stores a filename, SHA-256,
size, media type, timestamps, and the explicit disposition reason. It stores no
image bytes or absolute Photos-library path. Runtime manifests represent each
purged asset as a tombstone so `diet verify DATE` can prove that:

- every exported candidate has a classification;
- a purged item was classified `unrelated`;
- its workspace source and preview are absent;
- its hash is present in the durable audit;
- retained food-related media still matches its manifest hash.

If the Shortcut later exports the same unrelated image again, `prepare` checks
the audit and current analysis, matches the SHA-256, and removes the repeated
workspace copy together with any preview created during that same run. A
same-named file with changed content is retained and marked for review instead
of being deleted by filename.

## Correcting a classification

If an item was wrongly marked `unrelated`, change its analysis classification
to `possible_food` or `consumed_food`, repair its meal link if needed, and run
`diet prepare DATE` without `--skip-export`. The audit decision is active only
while the current analysis still says `unrelated`, so the newly exported copy
will be retained for review. Then run `render` and `verify` again.

Apple Photos remains the recovery source. The pipeline never deletes or modifies
Photos originals, medical records, supplement media, `possible_food`, or
`consumed_food` assets.
