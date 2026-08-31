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

Gather candidates in order, then autofill only when disambiguation yields one confident pick:

1. Exact normalized upstream alias from active `chainctl` metadata, including the same project path under a different registry and generically compatible org/path spellings (substantial prefix match on namespace tokens; no vendor allowlists).
2. Exact Chainguard repository name/path (including preferred FIPS / IAMGuarded variants).
3. Exact final path component, including `-fips` / `-iamguarded` variant forms of that component.
4. Stem/keyword pool: repositories whose name equals the final component, starts with `<final>-`, or matches after stripping `-fips` / `-iamguarded`.
5. Conservative fuzzy name candidates (never auto-filled).

Before deciding, collapse duplicate catalog rows that share the same repository name (prefer classified tier, aliases, richer active tags).

Disambiguation / autofill rules:

- Prefer a repository whose base name (variants stripped) equals the upstream final component — e.g. pick `cert-manager-controller` from a shared cert-manager alias family.
- When that final component is generic or shared (`agent`, `operator`, `controller`, or several `*/widget` repos), rank remaining candidates using other upstream path tokens (org/namespace segments). Prefer a name that is `{namespace}-{final}` for a compatible namespace token. Leave blank when those tokens do not uniquely select a candidate.
- Explicit non-FIPS: exclude FIPS repositories (`catalogTier` / bundles / `-fips` name). Explicit FIPS: retain only FIPS repositories and prefer the exact `-fips` name. Unspecified FIPS: do not filter on FIPS.
- Prefer non-`-iamguarded` repositories unless the upstream image is a Bitnami image (`bitnami/...`, bitnami registry, or bitnami legacy paths). For Bitnami, prefer the `-iamguarded` replacement when present.
- If multiple distinct bases remain after those filters (for example unrelated `*-kubernetes-operator` hits), leave equivalent/verdict blank for review rather than guessing.

Check requested tags only against active tags returned by `chainctl`. Distinguish:

- `exact_image_exact_tag`: confident repository pick and every requested tag exists.
- `image_available_tag_unavailable`: confident repository pick, but one or more requested tags do not.
- `multiple_possible_matches`: candidate pool remains after filtering and no single pick is defensible; leave blank.
- `possible_match_review`: one conservative fuzzy candidate remains; require review rather than autofill.
- `no_match`: no defensible candidate exists.

An active repository with `UNKNOWN`, missing, or internal/unclassified tier is evidence that the repository exists, not that it is customer-ready. State this limitation in verdict/notes. Use `cgr.dev/chainguard-private/<repo>:<tag>` by default. Use a different registry organization only when the user provides one explicitly.
