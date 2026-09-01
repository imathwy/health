# Personal profile and medical index

The operational file `config/health_profile.json` selects one active
`profile_id` and owns paths, the Shortcut name, and privacy switches. Personal
facts belong in the durable, ignored profile tree:

```text
data/profiles/<profile_id>/
├── profile.json
└── medical/
    ├── index.json
    └── files/          # original PDFs, images, and other records
```

`profile.json` schema v1 separates the user's stable context:

- `demographics`: age, sex, and height;
- `current_status`: baseline weight and goals;
- `activity`: stable work and exercise pattern;
- `nutrition_targets` and `diet_context`: inputs to dated analysis;
- `health_status.conditions`, `symptoms`, `medications`, and `allergies`:
  structured facts with stable IDs, status, notes, and optional `record_ids`;
- `health_status.context_notes` and `supplement_guardrails`: concise context
  that existing daily-analysis projections consume;
- `provenance`: source, review date, and notes.

Allowed health statuses are `active`, `monitoring`, `resolved`, and
`historical`. Empty lists mean no item has been registered. They establish a
confirmed absence only when that fact is recorded explicitly in provenance or
notes.

`medical/index.json` schema v1 contains `profile_id` and `records`. A record has:

```json
{
  "id": "2026-01-01-example",
  "date": "2026-01-01",
  "category": "examination",
  "title": "Example examination",
  "provider": "",
  "status": "historical",
  "summary": ["Short searchable summary"],
  "findings": ["Structured finding"],
  "source_files": [
    {
      "path": "files/example.pdf",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "media_type": "application/pdf"
    }
  ],
  "tags": ["example"],
  "notes": []
}
```

Categories are `examination`, `diagnosis`, `laboratory`, `imaging`,
`treatment`, `prescription`, `vaccination`, and `other`. Record statuses are
`current`, `monitoring`, `resolved`, and `historical`. Every source stores a safe
relative `path` beginning with `files/`, its lowercase SHA-256 digest, and an
optional media type. Absolute paths and `..` are invalid; `diet profile` also
rejects symlink escapes and changed file hashes. Plain string paths remain
readable for migration but produce a missing-hash warning.

The generated HTML shows summaries and the number of originals, but never a raw
file path or link. Run `./bin/diet profile` as the final validator and renderer.
