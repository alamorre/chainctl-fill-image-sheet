#!/usr/bin/env python3
"""Deterministically locate intake headers and match rows to chainctl catalog JSON."""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


FIELD_SYNONYMS = {
    "image": (
        "upstream image name tag", "upstream image", "source image", "current image",
        "container image", "source container", "container source", "image name", "image repository", "repository image", "image",
    ),
    "version": ("versions", "version", "requested version", "requested tag", "requested release", "release", "tag requirements", "tag"),
    "fips": ("fips required", "fips reqd", "fips requirement", "fips compliance", "requires fips", "fips"),
    "equivalent": (
        "chainguard equivalent", "chaingaurd equivalent", "cg equivalent", "chainguard image",
        "recommended image", "equivalent image", "cg match", "chainguard match",
    ),
    "verdict": ("verdict yes no", "verdict", "availability", "available"),
    "tier": ("catalog tier", "image type", "existing image type", "catalog type"),
    "candidate_count": ("candidate count", "number of candidates", "chainguard image count", "image count"),
    "tag_result": ("active tag result", "active version result", "tag availability", "version availability"),
    "notes": ("match notes", "matching notes", "notes", "comments"),
    "cgr": ("cgr availability", "in cgr dev", "cgr dev", "in cgr"),
    "progress": ("image progress", "progress status", "tracking status", "progress", "status"),
}

OUTPUT_HEADERS = (
    ("equivalent", "Chainguard equivalent"),
    ("verdict", "Verdict / availability"),
    ("tier", "Catalog tier"),
    ("candidate_count", "Candidate count"),
    ("tag_result", "Active tag result"),
    ("notes", "Match notes"),
)

REGISTRY_ALIASES = {
    "index.docker.io": "docker.io",
    "registry-1.docker.io": "docker.io",
}

TRUE_VALUES = {"yes", "y", "true", "required", "require", "1", "fips"}
FALSE_VALUES = {"no", "n", "false", "not required", "non fips", "nonfips", "0"}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = text.lower().replace("chaingaurd", "chainguard")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def field_for_header(value: Any) -> tuple[str | None, float]:
    norm = normalize_text(value)
    if not norm:
        return None, 0.0
    best_field, best_score = None, 0.0
    for field_name, synonyms in FIELD_SYNONYMS.items():
        for synonym in synonyms:
            if norm == synonym:
                return field_name, 1.0
            if len(norm) >= 4 and (norm in synonym or synonym in norm):
                score = 0.94 if min(len(norm), len(synonym)) >= 7 else 0.86
            else:
                score = difflib.SequenceMatcher(None, norm, synonym).ratio()
            if score > best_score:
                best_field, best_score = field_name, score
    return (best_field, best_score) if best_score >= 0.80 else (None, 0.0)


def locate_header(rows: list[list[Any]], scan_limit: int = 60) -> tuple[int, dict[str, int]]:
    best: tuple[float, int, dict[str, int]] | None = None
    width = max((len(row) for row in rows[:scan_limit]), default=0)
    for row_index in range(min(len(rows), scan_limit)):
        mapping: dict[str, int] = {}
        scores: dict[str, float] = {}
        for col in range(width):
            parts = []
            for prior in range(max(0, row_index - 2), row_index + 1):
                if col < len(rows[prior]) and str(rows[prior][col] or "").strip():
                    parts.append(str(rows[prior][col]))
            candidates = [rows[row_index][col] if col < len(rows[row_index]) else "", " ".join(parts)]
            for candidate in candidates:
                field_name, score = field_for_header(candidate)
                if field_name and score > scores.get(field_name, 0):
                    mapping[field_name] = col
                    scores[field_name] = score
        if "image" not in mapping:
            continue
        output_fields = sum(name in mapping for name in ("equivalent", "verdict", "tier", "notes", "progress"))
        score = sum(scores.values()) + 1.5 * min(len(mapping), 5) + 0.25 * output_fields
        candidate_best = (score, row_index, mapping)
        if best is None or candidate_best[0] > best[0]:
            best = candidate_best
    if best is None:
        raise ValueError("Could not locate a semantic image-table header row")
    return best[1], best[2]


@dataclass(frozen=True)
class ImageRef:
    raw: str
    registry: str | None
    path: str
    final: str
    tag: str | None
    digest: str | None


def parse_image_ref(value: Any) -> ImageRef:
    raw = str(value or "").strip()
    text = re.sub(r"^(?:docker|oci)://", "", raw, flags=re.I).strip().strip("`'\"")
    digest = None
    if "@" in text:
        text, digest = text.rsplit("@", 1)
    tag = None
    slash = text.rfind("/")
    colon = text.rfind(":")
    if colon > slash:
        text, tag = text[:colon], text[colon + 1:]
    parts = [part for part in text.strip("/").split("/") if part]
    registry = None
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry_component = parts.pop(0).lower()
        registry = REGISTRY_ALIASES.get(registry_component, registry_component)
    path = "/".join(parts).lower()
    return ImageRef(raw, registry, path, parts[-1].lower() if parts else "", tag or None, digest or None)


def canonical_alias(ref: ImageRef) -> str:
    registry = ref.registry or "docker.io"
    path = ref.path
    if registry == "docker.io" and "/" not in path:
        path = f"library/{path}"
    return f"{registry}/{path}"


def requested_tags(image: ImageRef, version_value: Any) -> list[str]:
    if image.tag:
        return [image.tag]
    text = str(version_value or "").strip()
    if not text:
        return []
    pieces = [piece.strip() for piece in re.split(r"[,;\n]+", text) if piece.strip()]
    if len(pieces) == 1 and " " in pieces[0] and not re.search(r"\s[-/]\s", pieces[0]):
        pieces = [piece for piece in pieces[0].split() if piece]
    return list(dict.fromkeys(piece.removeprefix(":") for piece in pieces))


def parse_fips(value: Any) -> bool | None:
    norm = normalize_text(value)
    if norm in FALSE_VALUES or norm.startswith("no ") or "not required" in norm:
        return False
    if norm in TRUE_VALUES or norm.startswith("yes ") or norm.endswith(" required"):
        return True
    return None


def first(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return default


@dataclass
class CatalogRepo:
    name: str
    tier: str
    bundles: tuple[str, ...]
    aliases: tuple[str, ...]
    active_tags: frozenset[str]
    repo_id: str = ""
    alias_keys: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        keys = {canonical_alias(parse_image_ref(alias)) for alias in self.aliases if parse_image_ref(alias).path}
        object.__setattr__(self, "alias_keys", frozenset(keys))

    @property
    def is_fips(self) -> bool:
        return self.name.endswith("-fips") or self.tier.upper() == "FIPS" or "fips" in self.bundles

    @property
    def is_unclassified(self) -> bool:
        return self.tier.upper() in {"", "UNKNOWN", "INTERNAL", "UNCLASSIFIED"}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def flatten_repo_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "repos", "repositories"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def load_catalog(repos_path: Path, images_path: Path) -> list[CatalogRepo]:
    repo_metadata = flatten_repo_records(load_json(repos_path))
    repo_by_id = {str(first(item, "id", default="")): item for item in repo_metadata}
    repo_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in repo_metadata:
        repo_by_name.setdefault(str(first(item, "name", default="")).lower(), []).append(item)

    images_payload = load_json(images_path)
    image_items = images_payload if isinstance(images_payload, list) else flatten_repo_records(images_payload)
    catalog: list[CatalogRepo] = []
    for item in image_items:
        repo = item.get("repo", item) if isinstance(item, dict) else {}
        if not isinstance(repo, dict):
            continue
        repo_id = str(first(repo, "id", default=""))
        name = str(first(repo, "name", default="")).strip().lower()
        if not name:
            continue
        meta = repo_by_id.get(repo_id)
        if meta is None:
            same_names = repo_by_name.get(name, [])
            meta = same_names[0] if len(same_names) == 1 else {}
        aliases = first(repo, "aliases", default=None) or first(meta, "aliases", default=[]) or []
        bundles = first(repo, "bundles", default=None) or first(meta, "bundles", default=[]) or []
        tier = str(first(repo, "catalogTier", "catalog_tier", default=None) or first(meta, "catalogTier", "catalog_tier", default="UNKNOWN"))
        tags = item.get("tags", []) if isinstance(item, dict) else []
        active = {str(tag.get("name")) for tag in tags if isinstance(tag, dict) and tag.get("name")}
        active.update(str(tag) for tag in (first(repo, "activeTags", "active_tags", default=[]) or []))
        catalog.append(CatalogRepo(name, tier, tuple(str(x).lower() for x in bundles), tuple(str(x) for x in aliases), frozenset(active), repo_id))
    return catalog


def fips_filter(candidates: Iterable[CatalogRepo], fips: bool | None) -> list[CatalogRepo]:
    if fips is True:
        return [candidate for candidate in candidates if candidate.is_fips]
    if fips is False:
        return [candidate for candidate in candidates if not candidate.is_fips]
    return list(candidates)


def unique_repos(candidates: Iterable[CatalogRepo]) -> list[CatalogRepo]:
    result: dict[tuple[str, str], CatalogRepo] = {}
    for candidate in candidates:
        result[(candidate.repo_id, candidate.name)] = candidate
    return sorted(result.values(), key=lambda candidate: (candidate.name, candidate.repo_id))


def choose_candidates(ref: ImageRef, catalog: list[CatalogRepo], fips: bool | None) -> tuple[str, list[CatalogRepo]]:
    alias_key = canonical_alias(ref)
    stage = unique_repos(fips_filter((repo for repo in catalog if alias_key in repo.alias_keys), fips))
    if stage:
        return "exact_upstream_alias", stage

    requested_path = ref.path.removeprefix("chainguard/")
    exact_names = {requested_path}
    if fips is True and not requested_path.endswith("-fips"):
        exact_names.add(f"{requested_path}-fips")
    stage = unique_repos(fips_filter((repo for repo in catalog if repo.name in exact_names), fips))
    if stage:
        return "exact_chainguard_repository", stage

    stage = unique_repos(fips_filter((repo for repo in catalog if ref.final == repo.name.rsplit("/", 1)[-1]), fips))
    if stage:
        return "exact_final_component", stage

    fuzzy: list[CatalogRepo] = []
    for repo in fips_filter(catalog, fips):
        score = difflib.SequenceMatcher(None, ref.final, repo.name).ratio()
        token_overlap = set(re.split(r"[-_.]+", ref.final)) & set(re.split(r"[-_.]+", repo.name))
        if score >= 0.86 and (score >= 0.92 or token_overlap):
            fuzzy.append(repo)
    return ("conservative_fuzzy", unique_repos(fuzzy)) if fuzzy else ("none", [])


def equivalent(repo: CatalogRepo, tags: list[str], destination_org: str | None) -> str:
    org = destination_org or "ORGANIZATION"
    tag = tags[0] if tags and tags[0] in repo.active_tags else ("latest" if "latest" in repo.active_tags and not tags else "")
    return f"cgr.dev/{org}/{repo.name}" + (f":{tag}" if tag else "")


def match_one(image_value: Any, version_value: Any, fips_value: Any, catalog: list[CatalogRepo], destination_org: str | None) -> dict[str, Any]:
    ref = parse_image_ref(image_value)
    tags = requested_tags(ref, version_value)
    fips = parse_fips(fips_value)
    method, candidates = choose_candidates(ref, catalog, fips)

    base = {
        "source": ref.raw,
        "requested_tags": tags,
        "fips_required": fips,
        "match_method": method,
        "candidate_count": len(candidates),
        "candidates": [repo.name for repo in candidates],
        "equivalent": "",
        "tier": "",
        "tag_result": "Not checked",
        "cgr": "No",
        "progress": "Unmatched",
    }
    if not candidates:
        return base | {"status": "no_match", "verdict": "No match", "notes": "No defensible active catalog candidate found."}

    exact_method = method in {"exact_upstream_alias", "exact_chainguard_repository", "exact_final_component"}
    if len(candidates) > 1:
        return base | {
            "status": "multiple_possible_matches",
            "verdict": "Review required",
            "tag_result": "Not checked; match ambiguous",
            "progress": "Needs review",
            "notes": f"{len(candidates)} candidates via {method}: " + ", ".join(repo.name for repo in candidates),
        }

    repo = candidates[0]
    tier_note = " Catalog tier is unclassified; existence is not evidence of customer readiness." if repo.is_unclassified else ""
    present = [tag for tag in tags if tag in repo.active_tags]
    missing = [tag for tag in tags if tag not in repo.active_tags]
    if not exact_method:
        return base | {
            "status": "possible_match_review",
            "verdict": "Possible match; review",
            "tier": repo.tier,
            "tag_result": "Not authoritative until match confirmed",
            "progress": "Needs review",
            "notes": f"Unique conservative fuzzy candidate via {method}: {repo.name}. Confirm before use." + tier_note,
        }
    base.update({
        "equivalent": equivalent(repo, tags, destination_org),
        "tier": repo.tier,
        "cgr": "Yes (tier unclassified)" if repo.is_unclassified else "Yes",
    })
    if missing:
        return base | {
            "status": "image_available_tag_unavailable",
            "verdict": "Image available; requested tag unavailable",
            "tag_result": "Missing: " + ", ".join(missing) + (("; available: " + ", ".join(present)) if present else ""),
            "progress": "Tag unavailable",
            "notes": f"Exact image match via {method}; requested active tag(s) missing." + tier_note,
        }
    return base | {
        "status": "exact_image_exact_tag",
        "verdict": "Available" if not repo.is_unclassified else "Exists; readiness unclassified",
        "tag_result": "All requested tags active" if tags else "Image active; no tag requested",
        "progress": "Matched" if not repo.is_unclassified else "Needs tier review",
        "notes": f"Exact image match via {method}." + tier_note,
    }


def plausible_image_row(value: Any) -> bool:
    norm = normalize_text(value)
    if not norm or norm in {"total", "totals", "subtotal", "image", "image name", "upstream image"}:
        return False
    if field_for_header(value)[0] == "image":
        return False
    raw = str(value)
    if len(raw.split()) > 5 and not re.search(r"[/@:.-]", raw):
        return False
    return bool(re.search(r"[a-z0-9]", raw, re.I))


def analyze_rows(rows: list[list[Any]], catalog: list[CatalogRepo], destination_org: str | None) -> dict[str, Any]:
    header_row, fields = locate_header(rows)
    max_width = max((len(row) for row in rows), default=0)
    output_columns: dict[str, dict[str, Any]] = {}
    next_col = max_width
    for field_name, header in OUTPUT_HEADERS:
        if field_name in fields:
            output_columns[field_name] = {"index": fields[field_name], "header": header, "existing": True}
        else:
            output_columns[field_name] = {"index": next_col, "header": header, "existing": False}
            next_col += 1
    for optional in ("cgr", "progress"):
        if optional in fields:
            output_columns[optional] = {"index": fields[optional], "header": optional, "existing": True}

    memo: dict[tuple[str, str, str], dict[str, Any]] = {}
    row_results = []
    counts = {key: 0 for key in ("exact_image_exact_tag", "image_available_tag_unavailable", "multiple_possible_matches", "possible_match_review", "no_match")}
    consecutive_blanks = 0
    for row_index in range(header_row + 1, len(rows)):
        row = rows[row_index]
        image_value = row[fields["image"]] if fields["image"] < len(row) else ""
        if not str(image_value or "").strip():
            consecutive_blanks += 1
            if consecutive_blanks >= 3:
                break
            continue
        consecutive_blanks = 0
        if not plausible_image_row(image_value):
            continue
        version_value = row[fields["version"]] if fields.get("version", -1) < len(row) and "version" in fields else ""
        fips_value = row[fields["fips"]] if fields.get("fips", -1) < len(row) and "fips" in fields else ""
        cache_key = (str(image_value), str(version_value), str(fips_value))
        if cache_key not in memo:
            memo[cache_key] = match_one(image_value, version_value, fips_value, catalog, destination_org)
        result = memo[cache_key]
        counts[result["status"]] += 1
        values = {name: result.get(name, "") for name in output_columns}
        row_results.append({"row": row_index, "values": values, "match": result})
    return {
        "header_row": header_row,
        "input_columns": fields,
        "output_columns": output_columns,
        "row_results": row_results,
        "summary": counts | {"processed_rows": len(row_results), "unique_requests": len(memo)},
    }


def read_csv(path: Path) -> tuple[list[list[str]], csv.Dialect]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:8192])
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect)), dialect


def write_filled_csv(source: Path, output: Path, rows: list[list[Any]], dialect: csv.Dialect, analysis: dict[str, Any], allow_overwrite: bool = False) -> None:
    if source.resolve() == output.resolve() and not allow_overwrite:
        raise ValueError("Refusing to overwrite the source CSV; choose a different --output-csv path")
    header_row = analysis["header_row"]
    columns = analysis["output_columns"]
    required_width = 1 + max(spec["index"] for spec in columns.values())
    for row in rows:
        while len(row) < required_width:
            row.append("")
    for spec in columns.values():
        if not spec["existing"]:
            rows[header_row][spec["index"]] = spec["header"]
    for item in analysis["row_results"]:
        row = rows[item["row"]]
        while len(row) < required_width:
            row.append("")
        for field_name, value in item["values"].items():
            row[columns[field_name]["index"]] = value
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, dialect=dialect)
        writer.writerows(rows)


def summary_report(analysis: dict[str, Any]) -> dict[str, Any]:
    attention = []
    for item in analysis["row_results"]:
        match = item["match"]
        if match["status"] != "exact_image_exact_tag" or str(match.get("tier", "")).upper() in {"", "UNKNOWN", "INTERNAL", "UNCLASSIFIED"}:
            attention.append({
                "row": item["row"] + 1,
                "status": match["status"],
                "candidates": match["candidates"],
                "tier": match.get("tier", ""),
                "requested_tags": match.get("requested_tags", []),
                "tag_result": match.get("tag_result", ""),
            })
    return {"counts": analysis["summary"], "attention_rows": attention}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-json", required=True, type=Path)
    parser.add_argument("--images-json", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-csv", type=Path)
    source.add_argument("--rows-json", type=Path)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--result-json", type=Path)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--destination-org")
    parser.add_argument("--allow-overwrite", action="store_true", help="Allow source replacement only when the user explicitly requested it")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_csv and not args.input_csv:
        raise SystemExit("--output-csv requires --input-csv")
    catalog = load_catalog(args.repos_json, args.images_json)
    if not catalog:
        raise SystemExit("No active catalog repositories found in the supplied chainctl JSON")
    if args.input_csv:
        rows, dialect = read_csv(args.input_csv)
    else:
        rows = load_json(args.rows_json)
        dialect = csv.excel
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise SystemExit("--rows-json must contain an array of row arrays")
    analysis = analyze_rows(rows, catalog, args.destination_org)
    if args.output_csv:
        write_filled_csv(args.input_csv, args.output_csv, rows, dialect, analysis, args.allow_overwrite)
        analysis["output"] = str(args.output_csv)
    if args.result_json:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary_report(analysis), indent=2) + "\n", encoding="utf-8")
    json.dump(analysis["summary"], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
