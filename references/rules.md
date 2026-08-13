# Sheet and matching rules

## Sheet discovery

- Inspect all sheets and plausible tables. Search leading rows for a semantic header instead of relying on a fixed row or column.
- Normalize case, whitespace, punctuation, apostrophes, and common misspellings. Recognize meanings such as upstream/current/source image, version/tag, FIPS required, Chainguard/CG equivalent, verdict/availability, image/catalog type or tier, candidate count, active tag/version result, notes, CGR availability, and progress/status.
- Combine up to three vertically adjacent header cells per column when group headers are present. Prefer a row containing an image field plus other recognized fields.
- Ignore title/instruction rows, blank rows, totals, repeated headers, and unrelated tables. A row is an image row only when the detected image cell contains a plausible image reference or name.
- Preserve duplicate source rows, but reuse the cached match for identical image/version/FIPS inputs.
- Populate only evidence-supported fields. Leave source-repository URLs, Dockerfile URLs, issue trackers, ETAs, and similar fields unchanged/blank unless authoritative `chainctl` fields directly support them.
- If essential output fields are missing, append: `Chainguard equivalent`, `Verdict / availability`, `Catalog tier`, `Candidate count`, `Active tag result`, and `Match notes`.

## Reference parsing

- Parse registry, repository path, final image component, tag, and digest separately.
- Normalize `index.docker.io` and `registry-1.docker.io` to `docker.io`, and normalize Docker Hub single-component paths to `library/<name>` only for registry comparison. Do not discard meaningful path components.
- Treat a separate version/tag column as the requested tag when the image reference has no tag. Support multiple comma-, semicolon-, newline-, or whitespace-separated versions conservatively.
- Never infer FIPS from the sample sheet or a general notes field. Use only an explicit FIPS column/value.

## Candidate order and decisions

Apply the first non-empty stage:

1. Exact normalized upstream alias from active `chainctl` metadata.
2. Exact Chainguard repository name/path.
3. Exact final path component, only when it identifies an unambiguous repository after FIPS filtering.
4. Conservative fuzzy name candidates.

FIPS filtering precedes the final decision:

- Explicit non-FIPS: exclude repositories marked by `catalogTier/catalog_tier`, bundles, or a `-fips` name as FIPS.
- Explicit FIPS: retain only candidates verified by those same fields, and prefer the exact `-fips` repository.
- Unspecified: do not infer or filter on FIPS.

Check requested tags only against active tags returned by `chainctl`. Distinguish:

- `exact_image_exact_tag`: exact repository/alias match and every requested tag exists.
- `image_available_tag_unavailable`: exact image exists, but one or more requested tags do not.
- `multiple_possible_matches`: multiple exact-final-component or fuzzy candidates remain.
- `possible_match_review`: one conservative fuzzy candidate remains; require review rather than silently treating it as exact.
- `no_match`: no defensible candidate exists.

An active repository with `UNKNOWN`, missing, or internal/unclassified tier is evidence that the repository exists, not that it is customer-ready. State this limitation in verdict/notes. Never invent a customer registry namespace. Use `cgr.dev/ORGANIZATION/<repo>:<tag>` unless the user provides a destination organization.
