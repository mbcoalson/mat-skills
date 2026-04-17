# Eval Harness Best Practices

Curated from four foundational articles plus lessons learned from production harness implementations. This is the source of truth for harness design decisions. Audit mode checks this file against recent publications and proposes updates.

**Last updated:** 2026-04-01
**Sources:** See `./audit/article-sources.yaml` for full list with URLs.

---

## 1. Separate Generation from Evaluation

Agents are reliably bad at grading their own work. A standalone skeptical evaluator is far easier to tune than making a generator self-critical.

**Implementation:** Three-actor minimum — Generator (produces output), Scorer (deterministic comparison), Analyzer (reads scores, patches pipeline). The Scorer is Python, never an LLM. The Analyzer is an LLM but sees only scores and diffs, not raw output quality judgments.

**Source:** All four articles agree on this. Anthropic calls it the "generator-evaluator loop" (inspired by GANs). OpenAI's Codex team uses dedicated agent reviewers. Huntley's Ralph loop relies on compilation and test suites as backpressure.

**Pattern:** When the gap between "what mismatched" and "why it mismatched" requires domain reasoning, inserting a cheap (haiku-class) Context Reviewer between Scorer and Analyzer can classify root causes before the expensive Analyzer runs. This prevents the Analyzer from spending premium tokens on triage.

---

## 2. Fresh Context Per Session

Every agent invocation gets a fresh context window. One task per session. Structured files bridge the gap between sessions.

**Implementation:** Always `claude -p` (pipe mode, fresh process). Never accumulate context across iterations. The orchestrator (Python or PowerShell) is the persistent memory — it reads STATE.json, git history, and change logs, then passes structured prompts to fresh Claude instances.

**Why it works:** Context anxiety (models degrading as context fills) is real even with modern models. Fresh windows eliminate it entirely. The cost is structured handoff, which you need anyway for auditability.

**Source:** Anthropic recommends context resets over compaction for long-running work. Huntley's Ralph loop is literally `while :; do cat PROMPT.md | claude-code ; done`. OpenAI's Codex runs routinely execute for 6+ hours using fresh instances per task.

---

## 3. Deterministic Scorer, No AI

The scorer is a Python script that parses output files and compares against a reference. It produces structured JSON. No LLM in the scoring path.

**Why Python, not an LLM:** Deterministic scoring means the same input always produces the same score. This makes regression detection reliable. An LLM scorer introduces variance that makes it impossible to distinguish "the pipeline got worse" from "the scorer scored differently."

**Dimension pattern:** Score across 2-5 orthogonal dimensions (data accuracy, content completeness, structural fidelity, etc.) rather than a single composite. Per-dimension scores let the Analyzer target the weakest area and let regression checks catch dimension-level drops that a composite might mask.

**Composite formula:** Weight dimensions by importance. Include hard-fail thresholds for non-negotiable dimensions (e.g., fabrication score < 70% = automatic fail regardless of composite).

**Library choice:** Match the library to the output format. A document-scoring harness might use `python-docx` + `scikit-learn` (TF-IDF for content similarity). A spreadsheet-scoring harness might use `openpyxl` + `sentence-transformers` + `scipy` (Hungarian algorithm for optimal 1:1 matching). Choose based on the structure of your output, not habit.

---

## 4. JSON for State, Not Markdown

Use JSON for all machine-readable state: scores, change logs, run metadata, task specs. Models are less likely to corrupt JSON than markdown.

**Implementation:** `STATE.json` (overnight framework), `score_report.json` (scorer output), `change_log.json` (analyzer history), `run_meta.json` (per-run metadata). Human-readable summaries (diff_summary.md, CHANGELOG.md) are secondary artifacts derived from JSON, not the source of truth.

**Source:** Anthropic's second article explicitly recommends JSON over markdown for feature lists and state. Huntley uses STATE.json as the core persistence mechanism.

---

## 5. Verify Before Building

Every session starts with a baseline check. Run the scorer on the current output before making changes. This catches cases where the environment changed, dependencies broke, or a prior patch introduced a subtle regression.

**Implementation:** The overnight framework's ASSESS step reads STATE.json scores and git log before proposing new work. A production harness should run the scorer before invoking the Analyzer each cycle. If the baseline doesn't match expectations, investigate before proceeding.

**Why it matters:** Compounding bugs across sessions is one of the most common failure modes. A 2% regression per session becomes 20% after 10 runs if nobody catches it early.

---

## 6. Revert with Context

When a change causes regression, don't just undo it — explain what failed and why. The next Analyzer invocation needs to understand what was tried, what broke, and what to do differently.

**Implementation:** On regression, `git revert` the last Analyzer commit. Inject a regression context block into the next Analyzer prompt: what changed, which dimensions regressed, the likely causal chain (e.g., "restructuring sections broke table alignment"), and guidance to pursue the same goal via a different approach.

**Pattern:** Use per-dimension circuit breakers (e.g., >5% drop in any single dimension = auto-revert) alongside composite checks. This catches cases where an improvement in one dimension masks a catastrophic drop in another — the composite passes but real quality regresses.

---

## 7. Deduplicate Changes (3-Strike Rule)

Track every Analyzer patch in a change log with target, approach, and outcome. If the same target fails 3 consecutive times, flag it for manual review and move to the next-highest-impact gap.

**Why:** Without deduplication, the Analyzer will attempt the same fix repeatedly with minor variations, burning runs. The change log gives it memory across sessions and the 3-strike rule prevents spinning.

**Implementation:** Change log is a JSON array. Each entry: `{run, target, gap, change, outcome, score_delta}`. The Analyzer prompt includes the full log with instructions: "Do not re-attempt a target that regressed unless you have a fundamentally different approach. Explain how your approach differs."

---

## 8. Progressive Disclosure for Agent Context

Give agents a map, not a manual. A short entry point (CLAUDE.md, AGENTS.md, or the generator prompt) with pointers to deeper docs. Agents start with a stable overview and navigate to detail on demand.

**Source:** OpenAI learned this the hard way — their initial monolithic AGENTS.md "failed in predictable ways." Too much guidance becomes non-guidance. They moved to ~100 lines of AGENTS.md pointing to a structured `docs/` directory.

**Implementation:** Generator and Analyzer prompts should be under 200 lines. Reference config files, section maps, and tool documentation by path rather than inlining them. The agent reads what it needs when it needs it.

**Anti-pattern:** Inlining the entire section map, scoring rubric, and change log into a single prompt. This crowds out the actual task.

---

## 9. Enforce Invariants Mechanically

Prompt instructions are suggestions. File-modification boundaries, commit format rules, and structural constraints should be enforced by the orchestrator or CI, not by asking the agent nicely.

**Implementation:** After the Analyzer commits, validate with `git diff --name-only` and reject if it touched files outside the allowed set. Check commit message format programmatically. Verify no reference files were modified. These are 10-20 lines of script in the orchestrator.

**Source:** OpenAI's Codex team uses custom linters whose error messages inject remediation instructions into agent context. "Once encoded, they apply everywhere at once." Huntley uses compilation as backpressure — if the code doesn't compile, the loop restarts.

---

## 10. Strip Complexity Per Model Upgrade

Every harness component encodes an assumption about what the model can't do. These assumptions go stale fast. When a new model releases, audit the harness and remove components that are no longer load-bearing.

**Examples:**
- Sprint constructs (breaking work into small pieces) were necessary with Opus 4.5 but unnecessary with Opus 4.6's longer coherence.
- Context compaction workarounds are less necessary as context windows grow.
- Elaborate formatting instructions may become unnecessary as models improve at structured output.

**Source:** Anthropic: "Find the simplest solution possible, and only increase complexity when needed." When they upgraded to Opus 4.6, they stripped sprint contracts and simplified the harness.

**Practical test:** For each harness component, ask: "If I removed this, would the output quality measurably degrade?" If you can't answer yes with evidence, it's a candidate for removal.

---

## 11. Generator Self-Validation

The generator should check its own output for obvious failures before handing off to the scorer. This catches the easy 80% (empty sections, missing tables, malformed structure) without spending a full scoring cycle.

**Implementation:** A lightweight validation script (20-50 lines of Python) that the generator prompt instructs the agent to run after producing output. Checks: file exists, file is not empty, expected sections/tables present, no placeholder text in data cells. If validation fails, the generator fixes and regenerates within the same session.

**Why not just let the scorer catch it:** Scoring cycles are expensive (scorer runtime + Analyzer invocation + cooldown). Self-validation is cheap and prevents wasting a full iteration on output that obviously isn't ready.

---

## 12. Contamination Safeguards

The generator must never see the reference. This prevents the harness from measuring "how well can the agent copy" instead of "how well can the pipeline produce."

**Implementation:**
- Generator prompt receives only source document paths, never reference paths.
- Reference directory is never copied into worktrees.
- Scorer is Python (no AI) — cannot leak reference content into the loop.
- Analyzer sees the reference (by design — it needs to understand the target) but is a separate agent from the generator.
- Each generator is a fresh Claude instance with no memory of prior runs.

**Detection:** Post-hoc, check if generated content contains reference-only text (phrases that appear in the reference but not in any source document). This catches subtle contamination.

---

## 13. Entropy Management

Agent-generated patches accumulate. After 10+ iterations, the codebase may have overlapping or contradictory changes that individually made sense but collectively create confusion.

**Implementation:** After every 5 runs (or at harness completion), run a consolidation step: review the cumulative diff on the evolving branch and simplify. This can be another agent invocation with the prompt: "Review the accumulated changes. Identify redundant, contradictory, or overly complex patterns. Propose simplifications that preserve the improvements."

**Source:** OpenAI calls this "garbage collection" — their team used to spend 20% of each week cleaning up AI slop until they automated it with recurring background tasks. They encode "golden principles" and have background Codex tasks scan for deviations.

---

## Appendix: When NOT to Build a Harness

Not every pipeline needs an automated eval loop. A harness is justified when:
- The pipeline produces complex structured output (documents, spreadsheets, code)
- A known-good reference exists or can be created
- The pipeline will run many times across projects (amortizes harness investment)
- Quality failures are expensive (client-facing deliverables, compliance documents)

Skip the harness when:
- The output is simple enough to validate by inspection
- No reference exists and creating one costs more than manual QA
- The pipeline is a one-off
- The domain changes faster than the harness can be maintained
