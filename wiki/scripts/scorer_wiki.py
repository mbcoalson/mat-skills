"""
scorer_wiki.py — Wiki Quality Scorer

Scores wiki articles across multiple quality dimensions using deterministic
checks only (no LLM calls). Two operating modes:

  --batch N    Score all articles in wiki/skills/, wiki/plugins/, wiki/concepts/
               (per-batch dimensions: frontmatter_validity, link_syntax,
                summary_quality, tag_hygiene, index_consistency)

  --post-all   Score after all batches complete (dimensions: link_resolution,
               hub_threshold, cross_ref_symmetry)

Usage:
    python wiki/scripts/scorer_wiki.py --batch 1
    python wiki/scripts/scorer_wiki.py --post-all

Reads:  wiki/scripts/STATE.json   (optional; sensible defaults if absent)
        wiki/tags.md              (optional; tag_hygiene skipped if absent)
        wiki/index.md             (built by index_builder.py)
        wiki/<type>/*.md          (articles across all wiki subdirectories)

Writes: wiki/scripts/score_report.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent   # wiki/scripts/
WIKI_ROOT  = SCRIPT_DIR.parent                 # wiki/
WORKSPACE  = WIKI_ROOT.parent                  # repo root

STATE_PATH  = SCRIPT_DIR / "STATE.json"
TAGS_PATH   = WIKI_ROOT / "tags.md"
INDEX_PATH  = WIKI_ROOT / "index.md"
REPORT_PATH = SCRIPT_DIR / "score_report.json"

SCAN_DIRS = [
    WIKI_ROOT / "skills",
    WIKI_ROOT / "plugins",
    WIKI_ROOT / "concepts",
    WIKI_ROOT / "topics",
    WIKI_ROOT / "projects",
    WIKI_ROOT / "opportunities",
    WIKI_ROOT / "processes",
    WIKI_ROOT / "references",
]

# Required frontmatter fields
REQUIRED_FIELDS = ["title", "type", "tags", "source", "compiled", "summary", "related"]

# Summary word count bounds
SUMMARY_MIN_WORDS = 20
SUMMARY_MAX_WORDS = 80

# Placeholder strings that must not appear in summary
SUMMARY_PLACEHOLDERS = ["TODO", "PLACEHOLDER", "TBD", "Lorem"]

# Wikilink regexes
WIKILINK_RE      = re.compile(r"\[\[([^\]]+)\]\]")   # valid: [[non-empty]]
EMPTY_LINK_RE    = re.compile(r"\[\[\]\]")            # broken: [[]]
NESTED_LINK_RE   = re.compile(r"\[\[\[")              # broken: [[[


# ---------------------------------------------------------------------------
# Frontmatter parser (shared with index_builder.py approach)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    """
    Parse YAML frontmatter from a markdown file.

    Expects the file to start with '---', followed by key: value lines,
    closed by another '---'. Returns an empty dict if no frontmatter found.

    Handles:
      - Scalar values: key: value
      - Inline YAML lists: key: [a, b, c]
      - Block YAML lists with items indented under the key
      - Quoted strings: key: "value" or key: 'value'
      - Folded/literal scalars: key: > or key: | (collects indented continuation)
    """
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        return {}

    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break

    if end is None:
        return {}

    fm_lines = lines[1:end]
    result = {}
    current_key = None
    current_list = None       # collecting block list items
    current_folded = None     # collecting folded/literal scalar lines

    for line in fm_lines:
        if not line.strip():
            # Blank line: flush any pending collection
            if current_folded is not None:
                result[current_key] = " ".join(current_folded)
                current_key = None
                current_folded = None
            if current_list is not None:
                result[current_key] = current_list
                current_key = None
                current_list = None
            continue

        # Indented continuation line (for folded scalars or block lists)
        if line.startswith("  ") and current_key is not None:
            # Block list item: "  - value"
            if (line.startswith("  - ") or line.startswith("    - ")) and current_list is not None:
                item = line.strip().lstrip("- ").strip().strip('"').strip("'")
                current_list.append(item)
                continue
            # Folded scalar continuation
            if current_folded is not None:
                current_folded.append(line.strip())
                continue
            # Block list item when we have a list context
            if current_list is not None and line.strip().startswith("- "):
                item = line.strip().lstrip("- ").strip().strip('"').strip("'")
                current_list.append(item)
                continue
            continue

        # Top-level key: value
        if ":" in line and not line.startswith(" "):
            # Flush pending folded scalar
            if current_folded is not None:
                result[current_key] = " ".join(current_folded)
                current_folded = None
            # Flush pending list
            if current_list is not None:
                result[current_key] = current_list
                current_list = None

            key, _, value = line.partition(":")
            key   = key.strip()
            value = value.strip()

            if not value:
                current_key = key
                current_list = []
            elif value in (">", "|", ">-", "|-"):
                # YAML folded (>) or literal (|) scalar indicator
                current_key = key
                current_folded = []
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                items = [
                    item.strip().strip('"').strip("'")
                    for item in inner.split(",")
                    if item.strip()
                ]
                result[key] = items
                current_key = None
            else:
                result[key] = value.strip('"').strip("'")
                current_key = None

    # Flush trailing collections
    if current_folded is not None and current_key is not None:
        result[current_key] = " ".join(current_folded)
    elif current_list is not None and current_key is not None:
        result[current_key] = current_list

    return result


# ---------------------------------------------------------------------------
# File collection helpers
# ---------------------------------------------------------------------------

def posix_rel(path: Path) -> str:
    """Forward-slash path relative to workspace root."""
    return path.relative_to(WORKSPACE).as_posix()


def collect_md_files() -> list[Path]:
    """Walk SCAN_DIRS and return all .md files (excluding .gitkeep)."""
    files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for md_file in sorted(scan_dir.glob("*.md")):
            if md_file.name == ".gitkeep":
                continue
            files.append(md_file)
    return files


# ---------------------------------------------------------------------------
# STATE.json helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "harness": "wiki-compiler",
        "valid_types": ["skill", "plugin", "concept"],
        "hub_threshold": 3,
        "score_threshold": 90,
        "hub_candidates": {},
        "batches": {},
    }


# ---------------------------------------------------------------------------
# Tag vocabulary loader
# ---------------------------------------------------------------------------

def load_valid_tags() -> set[str]:
    """
    Parse wiki/tags.md for valid tag names.

    Rules:
      - Skip lines starting with '#' (comments) and blank lines.
      - Split on first '#' to strip inline comments.
      - Strip whitespace — the result is the tag name.
    """
    if not TAGS_PATH.exists():
        return set()

    valid: set[str] = set()
    for line in TAGS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tag = stripped.split("#")[0].strip()
        if tag:
            valid.add(tag)
    return valid


# ---------------------------------------------------------------------------
# Index content loader
# ---------------------------------------------------------------------------

def load_index_content() -> str:
    if INDEX_PATH.exists():
        return INDEX_PATH.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def dimension_score(failures: list[dict], total: int) -> float:
    """(passing / total) * 100. Returns 100.0 if total == 0."""
    if total == 0:
        return 100.0
    failing_files = {f["file"] for f in failures}
    passing = total - len(failing_files)
    return round((passing / total) * 100, 2)


def target_to_kebab(target: str) -> str:
    """
    Convert a wikilink target string to its expected kebab-case filename.

    Steps:
      1. Strip whitespace and lowercase.
      2. Replace spaces/underscores with hyphens.
      3. Remove non-alphanumeric, non-hyphen chars.
      4. Collapse consecutive hyphens.
      5. Strip leading/trailing hyphens.
    """
    s = target.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


# ---------------------------------------------------------------------------
# Per-batch dimension checks
# ---------------------------------------------------------------------------

def check_frontmatter_validity(md_file: Path, fm: dict, valid_types: list[str]) -> list[str]:
    """Return list of failure reasons for this file, or empty list if OK."""
    reasons = []

    # Check all required fields present
    for field in REQUIRED_FIELDS:
        if field not in fm:
            reasons.append(f"missing required field '{field}'")

    # Check type value
    if "type" in fm:
        art_type = fm["type"]
        if art_type not in valid_types:
            reasons.append(f"type '{art_type}' not in valid_types {valid_types}")

    # Check compiled date is parseable as YYYY-MM-DD
    if "compiled" in fm:
        compiled = fm["compiled"]
        try:
            datetime.strptime(str(compiled), "%Y-%m-%d")
        except ValueError:
            reasons.append(f"compiled '{compiled}' is not a valid YYYY-MM-DD date")

    return reasons


def check_link_syntax(text: str) -> list[str]:
    """
    Check wikilink syntax in article body + frontmatter text.

    Failures:
      - Empty links [[]]
      - Nested brackets [[[...
    """
    reasons = []

    if EMPTY_LINK_RE.search(text):
        count = len(EMPTY_LINK_RE.findall(text))
        reasons.append(f"contains {count} empty wikilink(s) [[]]")

    if NESTED_LINK_RE.search(text):
        count = len(NESTED_LINK_RE.findall(text))
        reasons.append(f"contains {count} nested bracket(s) [[[")

    return reasons


def check_summary_quality(fm: dict) -> list[str]:
    """Check summary field: existence, word count, no placeholder strings."""
    reasons = []

    if "summary" not in fm:
        reasons.append("summary field missing")
        return reasons  # can't check further

    summary = fm["summary"]
    words = summary.split()
    word_count = len(words)

    if word_count < SUMMARY_MIN_WORDS:
        reasons.append(
            f"summary is {word_count} word(s) (min {SUMMARY_MIN_WORDS})"
        )
    elif word_count > SUMMARY_MAX_WORDS:
        reasons.append(
            f"summary is {word_count} words (max {SUMMARY_MAX_WORDS})"
        )

    for placeholder in SUMMARY_PLACEHOLDERS:
        if placeholder in summary:
            reasons.append(f"summary contains placeholder string '{placeholder}'")

    return reasons


def check_tag_hygiene(fm: dict, valid_tags: set[str]) -> list[str]:
    """
    Check tags:
      - All lowercase
      - No duplicates per article
      - Every tag exists in wiki/tags.md
    """
    reasons = []

    tags_raw = fm.get("tags", [])
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]

    seen = set()
    for tag in tags_raw:
        # Uppercase check
        if tag != tag.lower():
            reasons.append(f"tag '{tag}' is not lowercase")

        # Duplicate check
        lower_tag = tag.lower()
        if lower_tag in seen:
            reasons.append(f"duplicate tag '{lower_tag}'")
        seen.add(lower_tag)

        # Vocabulary check (compare lowercase)
        if tag.lower() not in {t.lower() for t in valid_tags}:
            reasons.append(f"tag '{tag}' not found in wiki/tags.md")

    return reasons


def check_index_consistency(md_file: Path, fm: dict, index_content: str) -> list[str]:
    """
    Check that this article's title appears as [[Title]] in wiki/index.md.
    """
    title = fm.get("title", "")
    if not title:
        return [f"no title in frontmatter, cannot check index"]

    pattern = f"[[{title}]]"
    if pattern not in index_content:
        return [f"[[{title}]] not found in wiki/index.md"]

    return []


# ---------------------------------------------------------------------------
# Post-all dimension checks
# ---------------------------------------------------------------------------

def check_link_resolution(md_files: list[Path]) -> list[dict]:
    """
    Every [[Target]] must map to an existing .md file in any wiki subdirectory.
    Returns list of failure dicts with file + reason.
    """
    # Build set of all existing kebab stems
    existing_stems: set[str] = set()
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for md_file in scan_dir.glob("*.md"):
            if md_file.name != ".gitkeep":
                existing_stems.add(md_file.stem)

    failures: list[dict] = []
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        targets = WIKILINK_RE.findall(text)
        for target in targets:
            if not target.strip():
                continue  # empty links caught by link_syntax
            kebab = target_to_kebab(target)
            if kebab and kebab not in existing_stems:
                failures.append({
                    "file": posix_rel(md_file),
                    "reason": f"[[{target}]] -> '{kebab}.md' not found in any wiki subdirectory",
                })

    return failures


# Two-layer wiki architecture (Phase 0, 2026-04-17):
#   - Primary articles (type-specific) satisfy a hub requirement.
#   - Topic hubs at wiki/topics/<kebab>-topic.md satisfy a hub requirement.
#   - Singleton concept articles at wiki/concepts/<kebab>.md still satisfy
#     for the legitimate KEEP entries (Phase 1 cleans up the 41 dupes).
_HUB_PRIMARY_DIRS = ["skills", "plugins", "projects", "opportunities", "processes", "references"]


def _hub_satisfied(target: str) -> tuple[bool, str | None]:
    """
    Return (True, rel_path) if any canonical location holds a file for this
    target — primary type dir, topic hub (``(Topic)`` convention), or singleton
    concept. Return (False, None) if none exist.
    """
    kebab = target_to_kebab(target)
    if not kebab:
        return False, None
    for d in _HUB_PRIMARY_DIRS:
        candidate = WIKI_ROOT / d / f"{kebab}.md"
        if candidate.exists():
            return True, f"{d}/{kebab}.md"
    topic_candidate = WIKI_ROOT / "topics" / f"{kebab}-topic.md"
    if topic_candidate.exists():
        return True, f"topics/{kebab}-topic.md"
    concept_candidate = WIKI_ROOT / "concepts" / f"{kebab}.md"
    if concept_candidate.exists():
        return True, f"concepts/{kebab}.md"
    return False, None


def check_hub_threshold(state: dict) -> list[dict]:
    """
    Every hub candidate with ref_count >= hub_threshold must be satisfied by
    a canonical article in one of: primary type dirs, wiki/topics/ (with
    (Topic) naming), or wiki/concepts/ (legitimate singletons only).
    """
    threshold = state.get("hub_threshold", 3)
    hub_candidates = state.get("hub_candidates", {})

    failures: list[dict] = []
    for target, info in hub_candidates.items():
        ref_count = info.get("ref_count", 0)
        if ref_count >= threshold:
            satisfied, _location = _hub_satisfied(target)
            if not satisfied:
                kebab = target_to_kebab(target)
                failures.append({
                    "file": f"wiki/topics/{kebab}-topic.md",
                    "reason": (
                        f"hub candidate '[[{target}]]' has {ref_count} refs "
                        f"(>= threshold {threshold}) but no primary article, "
                        f"topic hub, or singleton concept satisfies it"
                    ),
                })

    return failures


def check_cross_ref_symmetry(md_files: list[Path]) -> list[dict]:
    """
    If article A has [[B]] in its 'related' frontmatter field,
    article B should have [[A]] in its 'related' field.

    Returns failure dicts for asymmetric pairs.
    """
    # Build map: title -> (file_path, set of related targets from frontmatter)
    title_map: dict[str, tuple[Path, set[str]]] = {}

    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        fm = parse_frontmatter(text)
        title = fm.get("title", "")
        if not title:
            continue

        related_raw = fm.get("related", [])
        if isinstance(related_raw, str):
            related_raw = [related_raw]

        # Extract wikilink targets from the related list items
        related_targets: set[str] = set()
        for item in related_raw:
            for target in WIKILINK_RE.findall(item):
                related_targets.add(target.strip())

        title_map[title] = (md_file, related_targets)

    failures: list[dict] = []
    for title_a, (file_a, related_a) in title_map.items():
        for target_b in related_a:
            if target_b not in title_map:
                # Can't check symmetry if B doesn't exist (link_resolution handles that)
                continue
            file_b, related_b = title_map[target_b]
            # Check that A's title appears in B's related
            if title_a not in related_b:
                failures.append({
                    "file": posix_rel(file_b),
                    "reason": (
                        f"'[[{title_a}]]' links to '[[{target_b}]]' in related, "
                        f"but '{target_b}' does not link back to '[[{title_a}]]'"
                    ),
                })

    return failures


# ---------------------------------------------------------------------------
# Composite score calculation
# ---------------------------------------------------------------------------

def compute_composite(dimensions: dict, weights: dict[str, float]) -> float:
    total_weight = sum(weights.values())
    weighted_sum = sum(
        dimensions[dim]["score"] * weights[dim]
        for dim in weights
    )
    return round(weighted_sum / total_weight, 2)


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def print_report(result: dict) -> None:
    mode = result["mode"]
    composite = result["composite"]
    passed = result["pass"]

    if mode == "per-batch":
        print(f"\n=== Wiki Scorer — Batch {result['batch']} ===")
    else:
        print("\n=== Wiki Scorer — Post-All ===")

    print(f"Composite score: {composite:.1f}%  ({'PASS' if passed else 'FAIL'})")
    print()

    for dim_name, dim_data in result["dimensions"].items():
        score = dim_data["score"]
        failures = dim_data["failures"]
        status = "OK" if not failures else f"{len(failures)} failure(s)"
        print(f"  {dim_name:<25} {score:>6.1f}%  [{status}]")
        for failure in failures[:5]:  # cap display at 5 per dimension
            print(f"      {failure['file']}")
            print(f"        -> {failure['reason']}")
        if len(failures) > 5:
            print(f"      ... and {len(failures) - 5} more")

    print()
    report_rel = posix_rel(REPORT_PATH)
    print(f"Report written: {report_rel}")


# ---------------------------------------------------------------------------
# Per-batch scoring
# ---------------------------------------------------------------------------

def score_per_batch(batch_num: int) -> dict:
    state = load_state()
    valid_types = state.get("valid_types", ["skill", "plugin", "concept"])
    score_threshold = state.get("score_threshold", 90)

    valid_tags = load_valid_tags()
    index_content = load_index_content()
    md_files = collect_md_files()

    total = len(md_files)

    # Accumulate failures per dimension
    fm_failures: list[dict] = []
    link_failures: list[dict] = []
    summary_failures: list[dict] = []
    tag_failures: list[dict] = []
    index_failures: list[dict] = []

    for md_file in md_files:
        rel = posix_rel(md_file)
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            fm_failures.append({"file": rel, "reason": "could not read file"})
            continue

        fm = parse_frontmatter(text)

        # frontmatter_validity
        for reason in check_frontmatter_validity(md_file, fm, valid_types):
            fm_failures.append({"file": rel, "reason": reason})

        # link_syntax (check full file text)
        for reason in check_link_syntax(text):
            link_failures.append({"file": rel, "reason": reason})

        # summary_quality
        for reason in check_summary_quality(fm):
            summary_failures.append({"file": rel, "reason": reason})

        # tag_hygiene
        for reason in check_tag_hygiene(fm, valid_tags):
            tag_failures.append({"file": rel, "reason": reason})

        # index_consistency
        for reason in check_index_consistency(md_file, fm, index_content):
            index_failures.append({"file": rel, "reason": reason})

    weights = {
        "frontmatter_validity": 0.30,
        "link_syntax":          0.15,
        "summary_quality":      0.20,
        "tag_hygiene":          0.20,
        "index_consistency":    0.15,
    }

    dimensions = {
        "frontmatter_validity": {
            "score":    dimension_score(fm_failures, total),
            "failures": fm_failures,
        },
        "link_syntax": {
            "score":    dimension_score(link_failures, total),
            "failures": link_failures,
        },
        "summary_quality": {
            "score":    dimension_score(summary_failures, total),
            "failures": summary_failures,
        },
        "tag_hygiene": {
            "score":    dimension_score(tag_failures, total),
            "failures": tag_failures,
        },
        "index_consistency": {
            "score":    dimension_score(index_failures, total),
            "failures": index_failures,
        },
    }

    composite = compute_composite(dimensions, weights)

    result = {
        "mode":       "per-batch",
        "batch":      batch_num,
        "composite":  composite,
        "dimensions": dimensions,
        "pass":       composite >= score_threshold,
    }

    return result


# ---------------------------------------------------------------------------
# Post-all scoring
# ---------------------------------------------------------------------------

def score_post_all() -> dict:
    state = load_state()
    score_threshold = state.get("score_threshold", 90)

    md_files = collect_md_files()
    total = len(md_files)

    # link_resolution
    link_res_failures = check_link_resolution(md_files)

    # hub_threshold
    hub_failures = check_hub_threshold(state)
    # Hub dimension total: number of hub candidates meeting threshold
    hub_threshold_val = state.get("hub_threshold", 3)
    hub_candidates = state.get("hub_candidates", {})
    hub_total = sum(
        1 for info in hub_candidates.values()
        if info.get("ref_count", 0) >= hub_threshold_val
    )

    # cross_ref_symmetry
    sym_failures = check_cross_ref_symmetry(md_files)

    weights = {
        "link_resolution":    0.50,
        "hub_threshold":      0.25,
        "cross_ref_symmetry": 0.25,
    }

    dimensions = {
        "link_resolution": {
            "score":    dimension_score(link_res_failures, total),
            "failures": link_res_failures,
        },
        "hub_threshold": {
            "score":    dimension_score(hub_failures, max(hub_total, len(hub_failures))),
            "failures": hub_failures,
        },
        "cross_ref_symmetry": {
            "score":    dimension_score(sym_failures, total),
            "failures": sym_failures,
        },
    }

    composite = compute_composite(dimensions, weights)

    result = {
        "mode":       "post-all",
        "batch":      None,
        "composite":  composite,
        "dimensions": dimensions,
        "pass":       composite >= score_threshold,
    }

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score wiki articles for quality across multiple dimensions."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--batch",
        type=int,
        metavar="N",
        help="Score all wiki articles (per-batch dimensions) after batch N.",
    )
    group.add_argument(
        "--post-all",
        action="store_true",
        help="Score after all batches complete (post-all dimensions).",
    )
    args = parser.parse_args()

    if args.batch is not None:
        result = score_per_batch(args.batch)
    else:
        result = score_post_all()

    # Write JSON report
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    # Print human-readable summary
    print_report(result)

    # Exit non-zero on failure so CI can detect it
    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
