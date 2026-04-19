---
name: wiki
description: Use when searching for information across your Claude Code workspace, when compiling new knowledge into the wiki, or when running wiki maintenance. Provides a token-efficient search protocol (index → frontmatter → full read), an ingest protocol for filing session learnings back into the wiki, and a maintenance protocol for running the scorer and fixing issues. Trigger keywords: wiki, search workspace, find skill, knowledge base, obsidian, second brain.
---

## Search Protocol

The wiki uses three-tier progressive disclosure. Always start at Tier 1. Only escalate when needed. Do not read full articles to answer questions that the index or frontmatter can answer.

**Tier 1: Index scan**
Read `wiki/index.md`. Scan the table by title, tags, and summary. This costs minimal tokens and answers most "do we have X?" questions.

**Tier 2: Frontmatter scan**
Read the target article's first ~15 lines (frontmatter only). The `summary` and `related` fields tell you whether this is what you need without reading the full body.

**Tier 3: Full read**
Read the entire article body for workflow details, key concepts, and connections. Only do this when Tier 1 and Tier 2 confirm the article is relevant and you need the specifics.

**Fallback**
If no match in the index: grep `wiki/` for keywords, then check `.claude/skills/` and `.claude/plugins/` directly.

---

## Ingest Protocol

When a session produces reusable knowledge that should be filed into the wiki:

1. Write the article to the appropriate `wiki/` subdirectory (`skills/`, `plugins/`, or `concepts/`).

2. Follow the frontmatter schema exactly:
   ```yaml
   ---
   title: Article Title
   type: skill | plugin | concept
   tags: [tag1, tag2]  # from wiki/tags.md only
   source: path/to/source
   compiled: YYYY-MM-DD
   summary: >
     20-80 words, specific enough to decide relevance from the index.
   related:
     - "[[Related Article]]"
   ---
   ```

3. Use only tags from `wiki/tags.md`. If a new tag is needed, add it to `tags.md` with a comment description before using it.

4. Run index_builder.py to update the index:
   ```
   python wiki/scripts/index_builder.py
   ```

5. Append an entry to `wiki/log.md`:
   ```
   ## [YYYY-MM-DD] ingest | Article Title
   Created from session work. Source: [describe context].
   ```

---

## Maintenance Protocol

Run on demand when the wiki needs a health check.

1. Run the scorer for full validation:
   ```
   python wiki/scripts/scorer_wiki.py --post-all
   ```

2. Read `wiki/scripts/score_report.json` for failures.

3. Fix interactively or queue for an overnight analyzer run.

4. Run hub_detector to check for new concept candidates:
   ```
   python wiki/scripts/hub_detector.py
   ```
   This emits proposal files to `wiki/scripts/hub-proposals/` rather than
   writing articles directly. Review each proposal and change its `status`
   frontmatter to `approved` or `rejected` before creating the actual
   topic article.

---

## Conventions

- Never modify raw source files in `.claude/skills/` or `.claude/plugins/` — the wiki is the compiled layer.
- Wiki articles summarize and cross-reference; they don't duplicate source content.
- New tags require explicit addition to `wiki/tags.md` before use.
- File naming: kebab-case matching the source directory name (e.g., `diagnosing-energy-models.md`).
- Wikilinks use title case: `[[Diagnosing Energy Models]]`, not `[[diagnosing-energy-models]]`.
