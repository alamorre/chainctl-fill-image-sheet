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
    "version": ("versions", "version", "requested version", "requested tag", "requested release", "release", "tag"),
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
    # Treat header plurals like "Version(s)" as "versions" before tokenization.
    text = re.sub(r"\(([a-z0-9]+)\)", r"\1", text)
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
                # Prefer the left-most column on score ties so "Version(s)" wins over
                # later lookalikes such as "Tag requirements".
                if field_name and (
                    score > scores.get(field_name, 0)
                    or (score == scores.get(field_name, 0) and col < mapping.get(field_name, col))
                ):
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


def alias_path_key(ref: ImageRef) -> str:
    """Registry-agnostic project path used to compare catalog aliases."""
    path = ref.path
    if (ref.registry or "docker.io") == "docker.io" and "/" not in path:
        path = f"library/{path}"
    return path


def tokenize_name(value: str) -> list[str]:
    return [token for token in re.split(r"[-/_.]+", value.lower()) if token]


def path_context_tokens(ref: ImageRef) -> list[str]:
    """Org/namespace tokens from the upstream path, excluding the final image name."""
    parts = [part for part in ref.path.split("/") if part]
    if len(parts) <= 1:
        return []
    tokens: list[str] = []
    for part in parts[:-1]:
        tokens.append(part)
        tokens.extend(token for token in tokenize_name(part) if token != part)
    return list(dict.fromkeys(tokens))


def tokens_compatible(left: str, right: str, min_len: int = 4, min_ratio: float = 0.7) -> bool:
    """True when tokens are equal or one is a substantial prefix of the other (org renames)."""
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < min_len:
        return False
    return longer.startswith(shorter) and (len(shorter) / len(longer)) >= min_ratio


def project_alias_match(ref: ImageRef, alias_ref: ImageRef) -> bool:
    """Match the same upstream project across registries or generically compatible org names."""
    if not ref.path or not alias_ref.path:
        return False
    if canonical_alias(ref) == canonical_alias(alias_ref):
        return True
    if alias_path_key(ref) == alias_path_key(alias_ref):
        return True
    if ref.final != alias_ref.final:
        return False
    ref_ns = [part for part in ref.path.split("/") if part][:-1]
    alias_ns = [part for part in alias_ref.path.split("/") if part][:-1]
    if len(ref_ns) != len(alias_ns) or not ref_ns:
        return False
    return all(tokens_compatible(left, right) for left, right in zip(ref_ns, alias_ns))


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
    alias_paths: frozenset[str] = field(init=False)
    alias_refs: tuple[ImageRef, ...] = field(init=False)

    def __post_init__(self) -> None:
        refs = tuple(parsed for alias in self.aliases if (parsed := parse_image_ref(alias)).path)
        object.__setattr__(self, "alias_refs", refs)
        object.__setattr__(self, "alias_keys", frozenset(canonical_alias(ref) for ref in refs))
        object.__setattr__(self, "alias_paths", frozenset(alias_path_key(ref) for ref in refs))

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


def strip_variant_suffixes(name: str) -> str:
    base = name.lower()
    changed = True
    while changed:
        changed = False
        for suffix in ("-fips", "-iamguarded"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                changed = True
    return base


def repo_final_component(name: str) -> str:
    return strip_variant_suffixes(name.rsplit("/", 1)[-1])


def is_iamguarded(repo: CatalogRepo) -> bool:
    return "-iamguarded" in repo.name


def is_bitnami_ref(ref: ImageRef) -> bool:
    path = ref.path.lower()
    registry = (ref.registry or "").lower()
    return (
        path == "bitnami"
        or path.startswith("bitnami/")
        or "/bitnami/" in f"/{path}/"
        or "bitnamilegacy" in path
        or "bitnami" in registry
    )


def collapse_by_name(candidates: Iterable[CatalogRepo]) -> list[CatalogRepo]:
    """Collapse duplicate catalog rows that share a repository name."""
    best: dict[str, CatalogRepo] = {}
    for candidate in candidates:
        current = best.get(candidate.name)
        if current is None:
            best[candidate.name] = candidate
            continue
        current_score = (
            (0 if current.is_unclassified else 2)
            + (1 if current.aliases else 0)
            + min(len(current.active_tags), 20) / 20.0
        )
        candidate_score = (
            (0 if candidate.is_unclassified else 2)
            + (1 if candidate.aliases else 0)
            + min(len(candidate.active_tags), 20) / 20.0
        )
        if candidate_score > current_score:
            best[candidate.name] = candidate
    return sorted(best.values(), key=lambda repo: repo.name)


def preferred_variant_names(ref: ImageRef, fips: bool | None) -> list[str]:
    """Ordered preferred Chainguard names for the upstream final component."""
    final = ref.final
    bitnami = is_bitnami_ref(ref)
    names: list[str] = []
    if bitnami and fips is True:
        names.extend([f"{final}-iamguarded-fips", f"{final}-fips", f"{final}-iamguarded", final])
    elif bitnami:
        names.extend([f"{final}-iamguarded", final])
        if fips is True:
            names.insert(0, f"{final}-iamguarded-fips")
    elif fips is True:
        names.extend([f"{final}-fips", final])
    else:
        names.append(final)
    # Preserve order while deduplicating.
    return list(dict.fromkeys(names))


def composition_match(ref: ImageRef, repo: CatalogRepo) -> bool:
    """True when the catalog name is `{namespace}-{final}` for a compatible upstream namespace."""
    base = strip_variant_suffixes(repo.name).replace("/", "-")
    final = ref.final
    if not final or not base.endswith(f"-{final}"):
        return False
    prefix = base[: -(len(final) + 1)]
    if not prefix or "-" in prefix:
        return False
    return any(tokens_compatible(token, prefix) for token in path_context_tokens(ref))


def path_context_score(ref: ImageRef, repo: CatalogRepo) -> tuple[int, int, int] | None:
    """Rank a candidate by upstream path tokens. None means the tokens do not support this repo."""
    tokens = path_context_tokens(ref)
    if not tokens:
        return None
    repo_tokens = tokenize_name(strip_variant_suffixes(repo.name))
    used: set[int] = set()
    matched = 0
    for token in tokens:
        for index, repo_token in enumerate(repo_tokens):
            if index in used:
                continue
            if tokens_compatible(token, repo_token):
                matched += 1
                used.add(index)
                break
    if matched == 0:
        return None
    final_accounted = any(repo_token == ref.final or tokens_compatible(repo_token, ref.final) for repo_token in repo_tokens)
    extra = max(0, len(repo_tokens) - len(used) - (1 if final_accounted else 0))
    return (1 if composition_match(ref, repo) else 0, matched, -extra)


def apply_path_context(ref: ImageRef, candidates: list[CatalogRepo]) -> list[CatalogRepo] | None:
    """Filter to the best path-token-supported candidates, or None when there is no path context."""
    if not path_context_tokens(ref):
        return None
    scored = [(score, repo) for repo in candidates if (score := path_context_score(ref, repo)) is not None]
    if not scored:
        return []
    best = max(score for score, _repo in scored)
    return [repo for score, repo in scored if score == best]


def repo_matches_upstream_alias(ref: ImageRef, repo: CatalogRepo) -> bool:
    if canonical_alias(ref) in repo.alias_keys:
        return True
    if alias_path_key(ref) in repo.alias_paths:
        return True
    return any(project_alias_match(ref, alias_ref) for alias_ref in repo.alias_refs)


def pick_best(ref: ImageRef, candidates: Iterable[CatalogRepo], fips: bool | None) -> CatalogRepo | None:
    """Pick a single autofill candidate, or None when judgment would leave the cell blank."""
    cands = collapse_by_name(candidates)
    if not cands:
        return None

    bitnami = is_bitnami_ref(ref)
    if bitnami:
        iam = [repo for repo in cands if is_iamguarded(repo)]
        if iam:
            cands = iam
    else:
        non_iam = [repo for repo in cands if not is_iamguarded(repo)]
        if non_iam:
            cands = non_iam

    # Prefer repositories whose final component base equals the upstream final component.
    exact_base = [repo for repo in cands if repo_final_component(repo.name) == ref.final]
    if exact_base:
        cands = exact_base
        # Ambiguous exact finals (team/widget vs other/widget) can still use path tokens.
        if len(cands) > 1:
            contextual = apply_path_context(ref, cands)
            if contextual is not None:
                if not contextual:
                    return None
                cands = contextual
    else:
        # Generic finals like "agent" / "operator": use org/namespace tokens to rank.
        contextual = apply_path_context(ref, cands)
        if contextual is not None:
            if not contextual:
                return None
            cands = contextual
        elif len({repo_final_component(repo.name) for repo in cands}) > 1:
            # Related family or unrelated keyword hits without a clear component match.
            return None

    for name in preferred_variant_names(ref, fips):
        preferred = [repo for repo in cands if repo.name == name]
        if len(preferred) == 1:
            return preferred[0]
        if preferred:
            cands = preferred
            break

    if len(cands) == 1:
        return cands[0]
    # Same final component under different repository names/paths — leave blank.
    return None


def stem_keyword_candidates(ref: ImageRef, catalog: list[CatalogRepo], fips: bool | None) -> list[CatalogRepo]:
    """Keyword/stem pool: final component exact, prefix, or variant-suffix forms."""
    stem = ref.final
    if len(stem) < 3:
        return []
    hits: list[CatalogRepo] = []
    for repo in fips_filter(catalog, fips):
        name = repo.name
        final = repo_final_component(name)
        if name == stem or name.endswith(f"/{stem}") or name.startswith(f"{stem}-") or final == stem:
            hits.append(repo)
        elif f"-{stem}-" in f"-{final}-":
            hits.append(repo)
    return unique_repos(hits)


def expand_bitnami_variants(
    ref: ImageRef, catalog: list[CatalogRepo], candidates: list[CatalogRepo], fips: bool | None
) -> list[CatalogRepo]:
    if not is_bitnami_ref(ref):
        return candidates
    wanted = set(preferred_variant_names(ref, fips))
    extra = [repo for repo in catalog if repo.name in wanted]
    return unique_repos(list(candidates) + fips_filter(extra, fips))


def choose_candidates(
    ref: ImageRef, catalog: list[CatalogRepo], fips: bool | None
) -> tuple[str, list[CatalogRepo], CatalogRepo | None]:
    """Return match method, candidate pool, and autofill pick (if confident)."""

    def attempt(method: str, pool: list[CatalogRepo]) -> tuple[str, list[CatalogRepo], CatalogRepo | None] | None:
        if not pool:
            return None
        pool = expand_bitnami_variants(ref, catalog, pool, fips)
        picked = pick_best(ref, pool, fips)
        if picked is not None:
            return method, pool, picked
        return None

    alias_pool = unique_repos(fips_filter((repo for repo in catalog if repo_matches_upstream_alias(ref, repo)), fips))
    selected = attempt("exact_upstream_alias", alias_pool)
    if selected:
        return selected

    requested_path = ref.path.removeprefix("chainguard/")
    exact_names = {requested_path, ref.final, *preferred_variant_names(ref, fips)}
    if fips is True and not requested_path.endswith("-fips"):
        exact_names.add(f"{requested_path}-fips")
    name_pool = unique_repos(fips_filter((repo for repo in catalog if repo.name in exact_names), fips))
    selected = attempt("exact_chainguard_repository", name_pool)
    if selected:
        return selected

    final_pool = unique_repos(
        fips_filter((repo for repo in catalog if repo_final_component(repo.name) == ref.final), fips)
    )
    selected = attempt("exact_final_component", final_pool)
    if selected:
        return selected

    stem_pool = stem_keyword_candidates(ref, catalog, fips)
    selected = attempt("stem_keyword", stem_pool)
    if selected:
        return selected

    fuzzy: list[CatalogRepo] = []
    for repo in fips_filter(catalog, fips):
        score = difflib.SequenceMatcher(None, ref.final, repo.name).ratio()
        token_overlap = set(re.split(r"[-_.]+", ref.final)) & set(re.split(r"[-_.]+", repo.name))
        if score >= 0.86 and (score >= 0.92 or token_overlap):
            fuzzy.append(repo)
    fuzzy_pool = unique_repos(fuzzy)
    if fuzzy_pool:
        # Fuzzy matches are never auto-filled; leave blank for human review.
        return "conservative_fuzzy", fuzzy_pool, None

    # Preserve ambiguous alias/name pools for reporting when nothing was pickable.
    for method, pool in (
        ("exact_upstream_alias", alias_pool),
        ("exact_chainguard_repository", name_pool),
        ("exact_final_component", final_pool),
        ("stem_keyword", stem_pool),
    ):
        if collapse_by_name(pool):
            return method, pool, None
    return "none", [], None


def equivalent(repo: CatalogRepo, tags: list[str], destination_org: str | None) -> str:
    org = destination_org or "ORGANIZATION"
    tag = tags[0] if tags and tags[0] in repo.active_tags else ("latest" if "latest" in repo.active_tags and not tags else "")
    return f"cgr.dev/{org}/{repo.name}" + (f":{tag}" if tag else "")


def match_one(image_value: Any, version_value: Any, fips_value: Any, catalog: list[CatalogRepo], destination_org: str | None) -> dict[str, Any]:
    ref = parse_image_ref(image_value)
    tags = requested_tags(ref, version_value)
    fips = parse_fips(fips_value)
    method, candidates, picked = choose_candidates(ref, catalog, fips)
    collapsed = collapse_by_name(candidates)

    base = {
        "source": ref.raw,
        "requested_tags": tags,
        "fips_required": fips,
        "match_method": method,
        "candidate_count": len(collapsed),
        "candidates": [repo.name for repo in collapsed],
        "equivalent": "",
        "tier": "",
        "tag_result": "Not checked",
        "cgr": "No",
        "progress": "Unmatched",
    }
    if not candidates:
        return base | {"status": "no_match", "verdict": "No match", "notes": "No defensible active catalog candidate found."}

    autofill_methods = {
        "exact_upstream_alias",
        "exact_chainguard_repository",
        "exact_final_component",
        "stem_keyword",
    }
    if picked is None:
        if method == "conservative_fuzzy" and len(collapsed) == 1:
            repo = collapsed[0]
            tier_note = " Catalog tier is unclassified; existence is not evidence of customer readiness." if repo.is_unclassified else ""
            return base | {
                "status": "possible_match_review",
                "verdict": "Possible match; review",
                "tier": repo.tier,
                "tag_result": "Not authoritative until match confirmed",
                "progress": "Needs review",
                "notes": f"Unique conservative fuzzy candidate via {method}: {repo.name}. Confirm before use." + tier_note,
            }
        return base | {
            "status": "multiple_possible_matches" if len(collapsed) > 1 else "no_match",
            "verdict": "Review required" if len(collapsed) > 1 else "No match",
            "tag_result": "Not checked; match ambiguous" if len(collapsed) > 1 else "Not checked",
            "progress": "Needs review" if len(collapsed) > 1 else "Unmatched",
            "notes": (
                f"{len(collapsed)} candidates via {method}; left blank for review: "
                + ", ".join(repo.name for repo in collapsed)
                if collapsed
                else "No defensible active catalog candidate found."
            ),
        }

    if method not in autofill_methods:
        return base | {
            "status": "possible_match_review",
            "verdict": "Possible match; review",
            "tag_result": "Not authoritative until match confirmed",
            "progress": "Needs review",
            "notes": f"Refusing to autofill via {method}: {picked.name}.",
        }

    repo = picked
    pool_note = ""
    if len(collapsed) > 1:
        others = [name for name in base["candidates"] if name != repo.name]
        preview = ", ".join(others[:8]) + ("..." if len(others) > 8 else "")
        pool_note = f" Selected from {len(collapsed)} stem/alias candidates"
        if preview:
            pool_note += f" (also: {preview})"
        pool_note += "."
    base.update({
        "candidate_count": 1,
        "candidates": [repo.name],
        "equivalent": equivalent(repo, tags, destination_org),
        "tier": repo.tier,
        "cgr": "Yes (tier unclassified)" if repo.is_unclassified else "Yes",
    })
    tier_note = " Catalog tier is unclassified; existence is not evidence of customer readiness." if repo.is_unclassified else ""
    present = [tag for tag in tags if tag in repo.active_tags]
    missing = [tag for tag in tags if tag not in repo.active_tags]
    if missing:
        return base | {
            "status": "image_available_tag_unavailable",
            "verdict": "Image available; requested tag unavailable",
            "tag_result": "Missing: " + ", ".join(missing) + (("; available: " + ", ".join(present)) if present else ""),
            "progress": "Tag unavailable",
            "notes": f"Image match via {method}; requested active tag(s) missing." + pool_note + tier_note,
        }
    return base | {
        "status": "exact_image_exact_tag",
        "verdict": "Available" if not repo.is_unclassified else "Exists; readiness unclassified",
        "tag_result": "All requested tags active" if tags else "Image active; no tag requested",
        "progress": "Matched" if not repo.is_unclassified else "Needs tier review",
        "notes": f"Image match via {method}." + pool_note + tier_note,
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


def _dialect_column_widths(text: str, dialect: csv.Dialect, sample_rows: int = 20) -> list[int]:
    widths: list[int] = []
    for index, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        widths.append(len(row))
        if index + 1 >= sample_rows:
            break
    return widths


def _looks_like_stable_table(widths: list[int]) -> bool:
    if not widths:
        return False
    # Reject space-delimited sniffing of comma CSVs: wildly varying or tiny widths.
    mode = max(set(widths), key=widths.count)
    if mode < 3:
        return False
    stable = sum(1 for width in widths if width == mode)
    return stable >= max(2, len(widths) // 2)


def read_csv(path: Path) -> tuple[list[list[str]], csv.Dialect]:
    text = path.read_text(encoding="utf-8-sig")
    excel_rows = list(csv.reader(io.StringIO(text), csv.excel))
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
    except csv.Error:
        return excel_rows, csv.excel
    sniffed_widths = _dialect_column_widths(text, dialect)
    excel_widths = [len(row) for row in excel_rows[:20]]
    if not _looks_like_stable_table(sniffed_widths) or (
        _looks_like_stable_table(excel_widths)
        and max(set(excel_widths), key=excel_widths.count) > max(set(sniffed_widths), key=sniffed_widths.count)
    ):
        return excel_rows, csv.excel
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
