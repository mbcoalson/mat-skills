# Engineering Eval Harnesses

A Claude Code skill for building and auditing automated eval harnesses for domain plugins.

## What It Does

Your plugin produces structured output (DOCX, XLSX, JSON). This skill helps you set up an automated loop that proves the output is correct by scoring it against a known-good reference and iteratively improving the pipeline.

**Two modes:**

- **Build** — Walk through a structured consultation, then scaffold an orchestrator config, scorer, prompt templates, and state files tailored to your domain.
- **Audit** — Compare an existing harness against curated best practices (sourced from Anthropic, OpenAI, and community research), fetch recent articles, and produce a gap report with prioritized recommendations.

## How It Works

The core pattern every harness follows:

```
Generator agent (produces output)
    ↓
Deterministic scorer (Python, no AI — compares against reference)
    ↓
Analyzer agent (reads scores, produces one targeted patch)
    ↓
Orchestrator (loops until threshold met or max runs reached)
```

The overnight framework (`orchestrate.py`) drives the loop. Each agent gets a fresh context window per invocation. State persists in JSON files and git history.

## File Structure

```
engineering-eval-harnesses/
  SKILL.md                          ← Skill entry point (invoked by Claude)
  best-practices.md                 ← 13 curated principles with sources
  README.md                         ← This file
  templates/
    harness-config.yaml             ← Scoring dimensions, thresholds, paths
    state-initial.json              ← Initial STATE.json for overnight framework
    scorer-skeleton.py              ← Annotated Python scorer template
    generator-prompt.md             ← Generator agent prompt (contamination-safe)
    analyzer-prompt.md              ← Analyzer agent prompt (one-fix-per-commit)
    run-instructions.md             ← CLAUDE.md template for each run
    harness-adapter.yaml            ← Refinery integration bridge
  audit/
    article-sources.yaml            ← Known best-practice article URLs
    gap-report-template.md          ← Structured audit output format
```

## Quick Start

1. Say: "I need an eval harness for my [plugin name]"
2. The skill walks through 7 questions about your domain
3. Review the generated design brief
4. Approve, and the skill scaffolds all harness files with inline comments explaining each decision

## Complexity Classes

| Class | When to Use | Infrastructure |
|-------|-------------|----------------|
| **Light** | Simple output, 2-3 scoring dimensions | Overnight framework + custom scorer |
| **Standard** | Structured documents, 3-5 dimensions, regression checks | Overnight framework + context reviewer + change log |
| **Heavy** | Multiple output types, worktree isolation, contamination safeguards | Standalone orchestrator |

## Key Principles

The full list with sources and implementation notes is in `best-practices.md`. The highlights:

1. **Separate generation from evaluation** — agents grade their own work poorly
2. **Fresh context per session** — one task, one invocation, structured handoff
3. **Deterministic scorer** — Python, not a prompt
4. **JSON for state** — models corrupt markdown; JSON survives
5. **Revert with context** — explain what failed, not just undo it
6. **Strip complexity per model upgrade** — harness components encode assumptions that go stale

## Dependencies

- **Overnight framework** — loop driver (`orchestrate.py`, `STATE.json`, `launch.ps1`)
- **Refinery plugin** (optional) — bridges eval harness to production feedback
- **Python 3.12+** — for scorer scripts (`python-docx`, `openpyxl`, `scikit-learn` as needed)
- **Claude CLI** — `claude -p` with `--allowedTools "Bash,Read,Write,Edit,Glob,Grep"`
