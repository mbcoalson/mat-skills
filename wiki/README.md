# Wiki — Persistent LLM-Maintained Workspace Knowledge

A Claude Code skill that builds and maintains a persistent, cross-linked Obsidian-formatted wiki from your Claude Code workspace. Instead of re-discovering knowledge via RAG on every query, the LLM incrementally catalogs skills, plugins, projects, and concepts into a compounding knowledge artifact that stays current as the workspace evolves.

## Inspiration

This tool is a spin-off of Andrej Karpathy's **LLM Wiki** idea, described in [this gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

Karpathy's central insight:

> *The tedious part of maintaining a knowledge base is not the reading or the thinking — it's the bookkeeping.*

LLMs excel at the bookkeeping work humans avoid, so you can hand them the maintenance burden and keep the knowledge base current as it grows. The wiki becomes a compounding asset rather than a one-time snapshot.

## Three-Layer Architecture (adapted from the gist)

1. **Raw sources** — immutable documents the LLM reads but never modifies: your `.claude/skills/`, `.claude/plugins/`, project folders, design docs.
2. **The wiki** — markdown files the LLM creates and maintains, organized into topic zones (skills, plugins, concepts, projects, harness, etc.) with frontmatter tags and `[[wikilinks]]` for graph navigation.
3. **The schema** — configuration that defines structure, scoring rules, tag vocabulary, and which directories to ingest at which phase.

## Main Operations

- **Ingest** — Process new sources and integrate their content across multiple wiki pages. Handled via a batch manifest that chunks the workspace by directory so each batch fits in context.
- **Query** — Search relevant pages by frontmatter (token-efficient) and synthesize answers. Query outputs can become new wiki pages.
- **Lint / Score** — Periodic health-check: stub detection, orphan detection, backlink validation, tag vocabulary drift.

## What's different from the gist

Karpathy's gist describes the pattern. This implementation:
- Targets **Claude Code workspaces specifically** — skills, plugins, project folders under `User-Files/`.
- Outputs to **Obsidian vault format** (`[[wikilinks]]`, frontmatter, graph view, tag pane).
- Ships with a **batched orchestrator** (`orchestrate_wiki.py`) that runs Claude in pipe mode per batch to stay under context limits.
- Includes **automated scorer** (`wiki/harness/`) for lint-mode health-checks.

## Credit

- **Andrej Karpathy** — the core idea and gist.
- **Obsidian** — the vault format this tool targets.

## License

MIT — see the repo root [LICENSE](../LICENSE).
