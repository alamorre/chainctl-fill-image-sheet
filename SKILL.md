---
name: chainctl-fill-image-sheet
description: Fill a copy of a container-image intake sheet with live Chainguard catalog matches from the chainguard-private organization. Use for local CSV/XLSX intake files or accessible Google Sheets whose layout, headers, versions, and FIPS requirements vary, including availability, equivalent-image, catalog-tier, active-tag, candidate-count, notes, and progress fields.
---

# Fill a Chainguard image intake sheet

Use `chainctl` once per run to cache the live catalog, use `scripts/match_images.py` for deterministic header detection and matching, and edit a copy through the appropriate spreadsheet workflow. Read [references/rules.md](references/rules.md) before inspecting or modifying a sheet.

## 1. Resolve scope and source

- Accept a local CSV/XLSX file or an accessible Google Sheet.
- If the invocation explicitly names `chainguard-private`, proceed. Otherwise state that this skill is configured for `chainguard-private` and obtain concise confirmation before using `--parent`. Never substitute another organization.
- Make a copy by default. Never overwrite the source unless explicitly requested.
- For Google Sheets, use the connected Google Drive/Sheets edit workflow. For local XLSX, use the standalone Spreadsheets skill and its required artifact-tool import, render, inspect, edit, verify, and export workflow. For CSV, the bundled script can create the copy directly.
- Treat `assets/chainguard-image-intake-template.csv` only as a sanitized structural example. Never assume its row numbers, indexes, order, spelling, or FIPS values apply to another sheet.

## 2. Authenticate and cache the catalog

Run these in order. Authenticate only with `chainctl auth login`; never expose or persist tokens.

```bash
command -v chainctl
chainctl update
chainctl auth login
chainctl auth status
```

Create a temporary run directory outside the skill/repository, then run each read-only query exactly once:

```bash
chainctl images repos list --parent chainguard-private --recursive -o json > "$RUN_DIR/repos.json"
chainctl images list --parent chainguard-private --recursive --active-only -o json > "$RUN_DIR/images.json"
```

Do not commit either cache. Reuse both files for every input row in the run.

## 3. Analyze and match

For CSV, create the filled copy directly:

```bash
python3 scripts/match_images.py \
  --repos-json "$RUN_DIR/repos.json" \
  --images-json "$RUN_DIR/images.json" \
  --input-csv "$SOURCE" \
  --output-csv "$OUTPUT" \
  --summary-json "$RUN_DIR/summary.json"
```

Add `--destination-org NAME` only when the user supplies the destination registry organization. Without it, the script uses the explicit `cgr.dev/ORGANIZATION/...` placeholder.
Add `--allow-overwrite` only when the user explicitly requests source replacement.

For XLSX or Google Sheets, inspect cell values and formulas without flattening the workbook, write the relevant sheet rows as a UTF-8 JSON array-of-arrays, and run:

```bash
python3 scripts/match_images.py \
  --repos-json "$RUN_DIR/repos.json" \
  --images-json "$RUN_DIR/images.json" \
  --rows-json "$RUN_DIR/rows.json" \
  --result-json "$RUN_DIR/matches.json"
```

Apply only the returned `header_row`, `output_columns`, and `row_results[].values` to a copy. Preserve formulas, styles, merges, sheet names, comments, filters, unrelated cells, and unrelated sheets. When returned output columns have `existing: false`, append those headers at the right edge of the detected table and extend neighboring header/data styles without restructuring the sheet.

## 4. Verify and report

- Re-open/inspect the output and verify representative values, formulas, and unchanged sheet structure.
- Render every modified XLSX sheet and visually check headers, output cells, and appended columns. Use the connected-sheet verification flow for Google Sheets.
- Ensure no authentication data, catalog cache, customer input, or completed customer sheet is inside the repository.
- Report exact matches, missing tags, review/ambiguous rows, unmatched rows, and the output location. Explicitly call out any unclassified catalog entry or unresolved fuzzy candidate.
