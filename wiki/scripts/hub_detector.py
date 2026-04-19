"""
hub_detector.py — Proposal-only hub detection

Walks all .md files in wiki SCAN_DIRS, counts wikilink references to each
target, and emits PROPOSALS for any target with ref_count >= threshold that
lacks both a primary article and a topic hub.

hub_detector does not write wiki articles. It writes proposal files to
wiki/scripts/hub-proposals/<kebab>.md with status "proposed". A human
reviewer changes the status to "approved" or "rejected" before any topic
article is created. This prevents the tool from regenerating duplicate
concept articles on repeat runs.

Usage:
    python wiki/scripts/hub_detector.py

Reads:
    wiki/skills/, wiki/plugins/, wiki/concepts/, wiki/topics/, wiki/projects/,
    wiki/opportunities/, wiki/processes/, wiki/references/
    wiki/scripts/STATE.json   (hub_threshold, existing hub_candidates)

Writes:
    wiki/scripts/STATE.json                        (hub_candidates bookkeeping)
    wiki/scripts/hub-proposals/<kebab>.md          (one file per new candidate)
"""

from pathlib import Path
import json
import re

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent          # wiki/scripts/
WIKI_ROOT  = SCRIPT_DIR.parent                        # wiki/
WORKSPACE  = WIKI_ROOT.parent                         # repo root

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
# Primary (type-specific canonical) directories — a primary article here
# satisfies a hub requirement for that target, regardless of ref_count.
PRIMARY_DIRS = [
    WIKI_ROOT / "skills",
    WIKI_ROOT / "plugins",
    WIKI_ROOT / "projects",
    WIKI_ROOT / "opportunities",
    WIKI_ROOT / "processes",
    WIKI_ROOT / "references",
]
TOPICS_DIR     = WIKI_ROOT / "topics"
CONCEPTS_DIR   = WIKI_ROOT / "concepts"
STATE_PATH     = SCRIPT_DIR / "STATE.json"
PROPOSALS_DIR  = SCRIPT_DIR / "hub-proposals"

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def posix_rel(path: Path) -> str:
    """Return a forward-slash relative path from workspace root."""
    return path.relative_to(WORKSPACE).as_posix()


def target_to_kebab(target: str) -> str:
    """
    Convert a wikilink target string to its expected kebab-case filename.

    Rules applied in order:
      1. Strip leading/trailing whitespace.
      2. Lowercase everything.
      3. Replace one or more whitespace characters or underscores with a hyphen.
      4. Remove any character that is not alphanumeric or a hyphen.
      5. Collapse consecutive hyphens to a single hyphen.
      6. Strip leading/trailing hyphens.

    Examples:
      "ASHRAE 211"   -> "ashrae-211"
      "Test Concept" -> "test-concept"
      "MBCx / RCx"   -> "mbcx-rcx"
    """
    s = target.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s


def collect_md_files() -> list[Path]:
    """Walk SCAN_DIRS and return all .md files, excluding .gitkeep files."""
    files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for md_file in sorted(scan_dir.rglob("*.md")):
            if md_file.name == ".gitkeep":
                continue
            files.append(md_file)
    return files


def extract_wikilinks(text: str) -> set[str]:
    """Return the set of unique wikilink targets found in text."""
    return set(WIKILINK_RE.findall(text))


def build_reference_map(md_files: list[Path]) -> dict[str, list[str]]:
    """Map each wikilink target -> list of distinct article relative paths."""
    ref_map: dict[str, list[str]] = {}
    for md_file in md_files:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            print(f"  [WARN] Could not read {md_file}")
            continue
        targets = extract_wikilinks(text)
        rel_path = posix_rel(md_file)
        for target in targets:
            if target not in ref_map:
                ref_map[target] = []
            ref_map[target].append(rel_path)
    return ref_map


# ---------------------------------------------------------------------------
# Canonicity checks (replace the old concept_file_exists)
# ---------------------------------------------------------------------------

def primary_article_exists(target: str) -> str | None:
    """
    Return a posix relative path if a primary article exists for this target,
    else None. Checks kebab-name match in each primary type directory.
    """
    kebab = target_to_kebab(target)
    if not kebab:
        return None
    for d in PRIMARY_DIRS:
        candidate = d / f"{kebab}.md"
        if candidate.exists():
            return posix_rel(candidate)
    return None


def topic_hub_exists(target: str) -> str | None:
    """
    Return a posix relative path if a topic hub exists for this target under
    the (Topic) naming convention: wiki/topics/<kebab>-topic.md.
    """
    kebab = target_to_kebab(target)
    if not kebab:
        return None
    candidate = TOPICS_DIR / f"{kebab}-topic.md"
    if candidate.exists():
        return posix_rel(candidate)
    return None


def concept_singleton_exists(target: str) -> str | None:
    """
    Return a posix relative path if a singleton concept article exists for
    this target at wiki/concepts/<kebab>.md. Used for bookkeeping only —
    the scorer (Item 3) is the authority on hub satisfaction.
    """
    kebab = target_to_kebab(target)
    if not kebab:
        return None
    candidate = CONCEPTS_DIR / f"{kebab}.md"
    if candidate.exists():
        return posix_rel(candidate)
    return None


# ---------------------------------------------------------------------------
# Proposal emission
# ---------------------------------------------------------------------------

def _proposal_path(target: str) -> Path:
    return PROPOSALS_DIR / f"{target_to_kebab(target)}.md"


def _render_proposal(target: str, ref_count: int, referring: list[str]) -> str:
    """Render a new proposal's markdown body. Status defaults to 'proposed'."""
    kebab = target_to_kebab(target)
    proposed_title = f"{target.strip()} (Topic)"
    proposed_filename = f"{kebab}-topic.md"
    bullets = "\n".join(f"  - {r}" for r in referring)
    return (
        "---\n"
        f'status: proposed\n'
        f'target: "[[{target}]]"\n'
        f"ref_count: {ref_count}\n"
        f'proposed_title: "{proposed_title}"\n'
        f'proposed_filename: "topics/{proposed_filename}"\n'
        "---\n\n"
        f"# Hub proposal: [[{target}]]\n\n"
        f"- **Ref count:** {ref_count}\n"
        f"- **Proposed topic hub title:** `{proposed_title}`\n"
        f"- **Proposed filename:** `wiki/topics/{proposed_filename}`\n\n"
        "## Referring articles\n\n"
        f"{bullets}\n\n"
        "## Review instructions\n\n"
        "Change the `status:` frontmatter value to `approved` to have the\n"
        "topic hub generated on the next run of `generate_approved_hubs.py`,\n"
        "or to `rejected` to suppress this target from future proposals.\n"
    )


def emit_proposal(target: str, ref_count: int, referring: list[str]) -> str:
    """
    Write a proposal file for this target if one does not already exist.
    Returns one of:
      - "created"   : proposal file was created
      - "exists"    : proposal already exists; left untouched (preserves
                      human status changes such as approved/rejected)
    Never overwrites an existing proposal. Never writes to any article dir.
    """
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    path = _proposal_path(target)
    if path.exists():
        return "exists"
    path.write_text(_render_proposal(target, ref_count, sorted(referring)), encoding="utf-8")
    return "created"


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "harness": "wiki-compiler",
        "run_date": None,
        "hub_threshold": 3,
        "hub_candidates": {},
        "last_updated": None,
    }


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    md_files = collect_md_files()
    print(f"Found {len(md_files)} .md files across {len(SCAN_DIRS)} scan directories")

    ref_map = build_reference_map(md_files)

    state = load_state()
    threshold = state.get("hub_threshold", 3)
    print(f"Hub threshold: {threshold} (from STATE.json)")

    sorted_targets = sorted(ref_map.items(), key=lambda kv: len(kv[1]), reverse=True)

    new_candidates: dict[str, dict] = {}
    has_primary: list[tuple[str, str]] = []
    has_topic: list[tuple[str, str]] = []
    has_concept_singleton: list[tuple[str, str]] = []
    needs_proposal: list[tuple[str, int, list[str]]] = []
    above_threshold: list[str] = []

    for target, articles in sorted_targets:
        count = len(articles)
        if count < threshold:
            continue
        above_threshold.append(target)

        new_candidates[target] = {
            "ref_count": count,
            "referencing_articles": sorted(articles),
        }

        primary = primary_article_exists(target)
        topic = topic_hub_exists(target)
        concept = concept_singleton_exists(target)

        if primary:
            has_primary.append((target, primary))
            continue
        if topic:
            has_topic.append((target, topic))
            continue
        if concept:
            # Singleton concept is not auto-elevated to a hub satisfier;
            # scorer (Item 3) decides satisfaction. Record for visibility,
            # still emit a proposal so reviewers see ref_count context.
            has_concept_singleton.append((target, concept))

        needs_proposal.append((target, count, sorted(articles)))

    # Merge hub_candidates into STATE.json (preserve existing entries; refresh counts)
    existing_candidates: dict = state.get("hub_candidates", {})
    for target, info in new_candidates.items():
        existing_candidates[target] = info
    state["hub_candidates"] = existing_candidates
    save_state(state)

    # Emit proposals
    proposal_outcomes: dict[str, str] = {}
    for target, count, articles in needs_proposal:
        proposal_outcomes[target] = emit_proposal(target, count, articles)

    # ---- Report --------------------------------------------------------
    print()
    print("=" * 60)
    print("WIKILINK REFERENCE COUNTS (all targets, descending)")
    print("=" * 60)
    for target, articles in sorted_targets:
        count = len(articles)
        marker = ""
        if count >= threshold:
            if any(t == target for t, _ in has_primary):
                marker = " [primary exists]"
            elif any(t == target for t, _ in has_topic):
                marker = " [topic hub exists]"
            elif any(t == target for t, _ in has_concept_singleton):
                marker = " [singleton concept — proposal emitted]"
            else:
                marker = " [PROPOSAL]"
        print(f"  {count:>4}  {target}{marker}")

    print()
    print("=" * 60)
    print(f"ABOVE THRESHOLD (>= {threshold} distinct articles)")
    print("=" * 60)
    if above_threshold:
        for target in above_threshold:
            print(f"  {target}")
    else:
        print("  (none)")

    print()
    print("=" * 60)
    print("ALREADY CANONICAL (primary article or topic hub exists)")
    print("=" * 60)
    if has_primary or has_topic:
        for target, rel in has_primary:
            print(f"  {target}  ->  {rel}  [primary]")
        for target, rel in has_topic:
            print(f"  {target}  ->  {rel}  [topic]")
    else:
        print("  (none)")

    print()
    print("=" * 60)
    print(f"PROPOSALS (written to {posix_rel(PROPOSALS_DIR)}/)")
    print("=" * 60)
    if proposal_outcomes:
        for target, outcome in sorted(proposal_outcomes.items()):
            info = new_candidates[target]
            tag = "NEW" if outcome == "created" else "existing — preserved"
            print(f"  [{info['ref_count']} refs] [{tag}]  {target}")
    else:
        print("  (none)")

    print()
    print(f"STATE.json updated: {STATE_PATH}")
    print(f"Proposals directory: {PROPOSALS_DIR}")


if __name__ == "__main__":
    main()
