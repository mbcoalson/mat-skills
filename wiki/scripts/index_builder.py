"""
index_builder.py — Wiki Index Builder

Walks wiki/skills/, wiki/plugins/, wiki/concepts/, wiki/topics/, wiki/projects/,
wiki/opportunities/, wiki/processes/, wiki/references/ for .md files,
parses YAML frontmatter, and writes a sorted markdown table to wiki/index.md.

Usage:
    python wiki/scripts/index_builder.py
"""

from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["skills", "plugins", "concepts", "topics", "projects", "opportunities", "processes", "references"]
OUTPUT_FILE = WIKI_ROOT / "index.md"
SUMMARY_MAX = 100


def parse_frontmatter(text: str) -> dict:
    """
    Parse YAML frontmatter from a markdown file.

    Expects the file to start with '---', followed by key: value lines,
    closed by another '---'. Returns an empty dict if no frontmatter found.

    Handles:
      - Scalar values: key: value
      - Inline YAML lists: key: [a, b, c]
      - Block YAML lists:
          key:
            - item1
            - item2
      - Quoted strings: key: "value"
      - Folded/literal scalars: key: > or key: | (collects indented continuation)
    """
    lines = text.splitlines()

    # Must start with ---
    if not lines or lines[0].strip() != "---":
        return {}

    # Find closing ---
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
        # Blank lines inside frontmatter
        if not line.strip():
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
            key = key.strip()
            value = value.strip()

            if not value:
                # Might be a block list — set up to collect
                current_key = key
                current_list = []
            elif value in (">", "|", ">-", "|-"):
                # YAML folded (>) or literal (|) scalar indicator
                current_key = key
                current_folded = []
            elif value.startswith("[") and value.endswith("]"):
                # Inline list: [a, b, c]
                inner = value[1:-1]
                items = [
                    item.strip().strip('"').strip("'")
                    for item in inner.split(",")
                    if item.strip()
                ]
                result[key] = items
                current_key = None
            else:
                # Scalar
                result[key] = value.strip('"').strip("'")
                current_key = None

    # Flush trailing collections
    if current_folded is not None and current_key is not None:
        result[current_key] = " ".join(current_folded)
    elif current_list is not None and current_key is not None:
        result[current_key] = current_list

    return result


def truncate(text: str, max_len: int = SUMMARY_MAX) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def collect_articles() -> list[dict]:
    articles = []

    for subdir in SCAN_DIRS:
        scan_path = WIKI_ROOT / subdir
        if not scan_path.exists():
            continue

        for md_file in scan_path.glob("*.md"):
            if md_file.name == ".gitkeep":
                continue

            text = md_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)

            title = fm.get("title", md_file.stem)
            article_type = fm.get("type", subdir.rstrip("s"))  # fallback: dir name singular

            tags_raw = fm.get("tags", [])
            if isinstance(tags_raw, list):
                tags = ", ".join(tags_raw)
            else:
                tags = str(tags_raw)

            summary_raw = fm.get("summary", "")
            summary = truncate(summary_raw)

            articles.append(
                {
                    "title": title,
                    "type": article_type,
                    "tags": tags,
                    "summary": summary,
                }
            )

    articles.sort(key=lambda a: a["title"].lower())
    return articles


def _escape_cell(value: str) -> str:
    """
    Escape a value for safe inclusion inside a markdown table cell.
    Replaces literal pipes with ``\\|`` and collapses newlines to a space
    so multi-line strings don't break row rendering.
    """
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def build_index(articles: list[dict]) -> str:
    header = (
        "# Wiki Index\n\n"
        "| Title | Type | Tags | Summary |\n"
        "|-------|------|------|---------|"
    )

    rows = []
    for a in articles:
        title   = _escape_cell(a["title"])
        art_typ = _escape_cell(a["type"])
        tags    = _escape_cell(a["tags"])
        summary = _escape_cell(a["summary"])
        row = f"| [[{title}]] | {art_typ} | {tags} | {summary} |"
        rows.append(row)

    if rows:
        return header + "\n" + "\n".join(rows) + "\n"
    else:
        return header + "\n"


def main():
    articles = collect_articles()
    content = build_index(articles)
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"Indexed {len(articles)} article(s) -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
