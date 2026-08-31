# chainctl-fill-image-sheet

Agent skill that fills a Chainguard container-image intake sheet with live catalog matches from `chainguard-private`.

## What it does

Given a local CSV/XLSX or an accessible Google Sheet, the skill:

1. Authenticates with `chainctl` and caches the live image catalog
2. Detects headers and image rows (layouts vary)
3. Matches each row to Chainguard images (exact, tag availability, fuzzy/review)
4. Writes results into a **copy** of the sheet (never overwrites the source unless asked)

## Prerequisites

- [`chainctl`](https://edu.chainguard.dev/chainguard/chainctl/) installed and on `PATH`
- Access to the `chainguard-private` organization
- Python 3 (for `scripts/match_images.py`)

## How to use

Point an agent at this skill and ask it to fill your sheet, for example:

```text
Use the chainctl-fill-image-sheet skill to fill my intake sheet at ./my-images.csv
```

Or with a Google Sheet / XLSX path:

```text
Fill this image intake sheet with live Chainguard catalog data: <path-or-sheet-url>
```

The agent will confirm `chainguard-private`, authenticate, cache catalog JSON outside the repo, run the matcher, and report exact matches, missing tags, review rows, and unmatched rows.

### Optional

- Equivalent references default to `cgr.dev/chainguard-private/...`. Pass a destination registry org (e.g. `acme`) to use `cgr.dev/acme/...` instead.
- Say explicitly if you want the source file overwritten (default is always a copy).

## Layout

| Path | Purpose |
|------|---------|
| `SKILL.md` | Skill instructions for the agent |
| `references/rules.md` | Matching and sheet-discovery rules |
| `scripts/match_images.py` | Deterministic header detection + matching |
| `assets/chainguard-image-intake-template.csv` | Sanitized structural example only |

## Notes

- Catalog caches and customer sheets must stay outside this repository.
- Treat the CSV under `assets/` as a shape example — real sheets may differ in headers, order, and FIPS columns.
