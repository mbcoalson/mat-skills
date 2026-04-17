---
name: engineering-eval-harnesses
description: Use this skill when building, auditing, or improving automated eval harnesses for domain plugins. This includes setting up overnight evaluation loops, creating scorers, designing generator/analyzer agent prompts, configuring the overnight framework, bridging to refinery for production feedback, or checking existing harnesses against current best practices. Trigger keywords: eval harness, overnight loop, automated scoring, harness audit, eval framework, quality loop, scorer, generator-evaluator.
---

# Engineering Eval Harnesses

Build and maintain automated evaluation loops that prove domain plugins produce correct output. Two modes: **build** a new harness, or **audit** an existing one against current best practices.

**Core pattern:** Generator agent (produces output) → Deterministic scorer (compares against reference) → Analyzer agent (patches the pipeline). The overnight framework drives the loop. Refinery captures production feedback.

## Before You Start

Read `./best-practices.md` — it distills the principles that every harness decision traces back to. The templates encode these principles, but understanding *why* prevents cargo-culting.

## Mode 1: Build a Harness

### Phase 1 — Consultation (produce a design brief)

Ask these questions **one at a time**. The answers determine harness complexity.

1. **What plugin is this for?** Read the plugin's `plugin.json`, skills, and tools to understand what it produces.

2. **What's the output format?** (DOCX, XLSX, PDF, JSON, other) — determines scorer strategy.

3. **Do you have a known-good reference?** A reference output is the single biggest accelerator. Without one, the scorer must validate against rules rather than diffing against a target.

4. **What dimensions matter?** Help the user identify 2-5 scoring dimensions. Common patterns:
   - Data accuracy (cell-by-cell, numeric tolerance)
   - Content completeness (sections present, no placeholders)
   - Structural fidelity (heading hierarchy, table placement)
   - Formatting quality (styles, colors, merged cells)
   - Domain correctness (signal types, unit conversions, code compliance)

5. **What's excluded from scoring?** Judgment sections, pending config, human-authored content — items the generator can't be expected to produce. Excluding these prevents the scorer from penalizing the generator for things outside its control.

6. **What complexity class?** Based on answers above, recommend:
   - **Light** — Simple output, reference available, 2-3 dimensions. Use overnight framework directly with a custom scorer and STATE.json schema.
   - **Standard** — Structured document, reference available, 3-5 dimensions, regression checks needed. Overnight framework + context reviewer + change log deduplication.
   - **Heavy** — Multiple output types, complex regression logic, worktree isolation, contamination safeguards. Standalone orchestrator (modeled on `launch-mv-eval.ps1`).

7. **Refinery integration?** If the plugin has active users, bridge to refinery so production corrections feed back into the eval loop.

**Output:** Write the design brief to `docs/superpowers/specs/YYYY-MM-DD-<plugin>-harness-design.md`. Get user approval before Phase 2.

### Phase 2 — Scaffold (generate annotated files)

**Detect Python first.** Before generating files, find the user's Python interpreter and populate `{python_path}` in the harness config and agent prompts. Try these in order, stop at the first success:

1. `python --version` — works if Python is on PATH
2. `python3 --version` — common on macOS/Linux
3. `py -3 --version` — Windows py launcher
4. `where python` (Windows) or `which python3` (macOS/Linux) — shows full path

Use the full absolute path (e.g., `C:/Users/alice/AppData/Local/Programs/Python/Python312/python.exe` or `/usr/bin/python3`). Confirm the version is 3.10+ since scorer dependencies need it.

Generate these files, customized from `./templates/`:

| File | Purpose | Template |
|------|---------|----------|
| `harness-config.yaml` | Harness configuration (dimensions, paths, thresholds) | `./templates/harness-config.yaml` |
| `STATE.json` | Initial state for overnight framework | `./templates/state-initial.json` |
| `scorer_<domain>.py` | Deterministic scorer | `./templates/scorer-skeleton.py` |
| `generator_prompt.md` | Generator agent instructions | `./templates/generator-prompt.md` |
| `analyzer_prompt.md` | Analyzer agent instructions | `./templates/analyzer-prompt.md` |
| `harness-adapter.yaml` | Refinery bridge (if applicable) | `./templates/harness-adapter.yaml` |
| `CLAUDE.md` | Run-specific agent instructions | `./templates/run-instructions.md` |

**Note:** `scorer-skeleton.py` is a template for generated harnesses, not a script this skill executes.

**For Light harnesses:** After scaffolding, copy the generated files into the plugin's directory, then create a run with `setup-run.ps1 -Type plugin -Target <plugin-path> -Name <run-name>`. The framework copies the plugin source into the run directory.

**Important:** The overnight framework's `orchestrate.py` must not use `--dangerously-skip-permissions` for Light harness runs. Before running, search that file for `--dangerously-skip-permissions` and verify it has been replaced with `--allowedTools "Bash,Read,Write,Edit,Glob,Grep"`.

**For Standard/Heavy harnesses:** Place files in the plugin's `eval/` directory (or project `eval/` directory if cross-cutting).

After scaffolding, present a **"What this does and why"** summary explaining each generated file's role. Encourage the user to read the inline comments if they have time — understanding the harness pays compound interest when debugging later.

### Scaffold Rules

- **`--allowedTools "Bash,Read,Write,Edit,Glob,Grep"`** in all generated prompts. Never `--dangerously-skip-permissions`.
- **Python for scorers.** Scorers are deterministic — no AI. Python with `python-docx`, `openpyxl`, `scikit-learn` as needed.
- **JSON for state files.** Not markdown. Models are less likely to corrupt JSON.
- **Fresh context per agent invocation.** Always `claude -p`. Never accumulate context across iterations.
- **Contamination safeguards.** Generator prompt receives only source paths. Reference paths go to scorer and analyzer only.
- **One task per session.** Generator produces output. Analyzer produces one patch. Never both in one invocation.

## Mode 2: Audit a Harness

### Step 1 — Read the existing harness

Identify all harness components: orchestrator, scorer, prompts, config, state files. Map them to the four-actor pattern (orchestrator, generator, scorer, analyzer).

### Step 2 — Check best practices

Compare each component against `./best-practices.md`. Flag:
- Missing separation of concerns (generator grading its own work)
- State in markdown instead of JSON
- No regression handling
- No contamination safeguards
- Stale complexity (components that encode old model limitations)
- Missing self-validation in generator
- No mechanical enforcement of file-modification boundaries

### Step 3 — Fetch recent articles

Read `./audit/article-sources.yaml` for known sources. Use WebSearch to check for new harness engineering articles published since the last audit. Compare any new insights against `./best-practices.md`. If WebSearch is unavailable (offline, restricted network), skip this step and note it in the gap report — the curated sources remain the primary reference.

### Step 4 — Produce gap report

Write the gap report to `docs/superpowers/specs/YYYY-MM-DD-<plugin>-harness-audit.md` using the structure in `./audit/gap-report-template.md`. Prioritize gaps by impact. Recommend specific changes.

### Step 5 — Update best practices (if warranted)

If the audit surfaced genuinely new patterns from recent articles, propose updates to `./best-practices.md`. Show the user what changed and why before committing.

## Key Principles (abbreviated)

These are expanded in `./best-practices.md`. The short version:

1. **Separate generation from evaluation.** Agents grade their own work poorly.
2. **Fresh context per session.** One task, one invocation, structured handoff.
3. **Deterministic scorer, no AI.** The scorer is Python, not a prompt.
4. **JSON for state.** Models corrupt markdown; JSON survives.
5. **Verify before building.** Baseline check at session start.
6. **Revert with context.** Don't just undo — explain what failed.
7. **Deduplicate changes.** 3-strike escalation to manual review.
8. **Progressive disclosure.** Give agents a map, not a manual.
9. **Enforce mechanically.** Linters and checks, not just prompt instructions.
10. **Strip complexity per model upgrade.** Every component encodes assumptions that go stale.

## References

- **Best practices:** `./best-practices.md`
- **Templates:** `./templates/` (scorer, prompts, configs)
- **Audit sources:** `./audit/article-sources.yaml`
- **Overnight framework:** See `FRAMEWORK.md` in the overnight-framework directory (outside this repo)
